import logging
import os
import json
import time
import boto3
from typing import Dict, Any

from src.services.execution.segment_runner_service import SegmentRunnerService

# 🎒 [v3.13] Kernel Protocol - The Great Seal Pattern
try:
    from src.common.kernel_protocol import seal_state_bag, open_state_bag
    KERNEL_PROTOCOL_AVAILABLE = True
except ImportError:
    try:
        from common.kernel_protocol import seal_state_bag, open_state_bag
        KERNEL_PROTOCOL_AVAILABLE = True
    except ImportError:
        KERNEL_PROTOCOL_AVAILABLE = False

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 🚨 [Critical] Validate S3 bucket at module load time (Cold Start)
_S3_BUCKET = os.environ.get("S3_BUCKET") or os.environ.get("SKELETON_S3_BUCKET") or ""
_S3_BUCKET = _S3_BUCKET.strip() if _S3_BUCKET else ""
if not _S3_BUCKET:
    logger.error("🚨 [CRITICAL CONFIG ERROR] S3_BUCKET or SKELETON_S3_BUCKET environment variable is NOT SET! "
                f"S3_BUCKET='{os.environ.get('S3_BUCKET')}', "
                f"SKELETON_S3_BUCKET='{os.environ.get('SKELETON_S3_BUCKET')}'. "
                "Payloads exceeding 256KB will cause Step Functions failures.")
else:
    logger.info(f"✅ S3 bucket configured for state offloading: {_S3_BUCKET}")

# [v3.35] Segment-level idempotency guard
_IDEMPOTENCY_TABLE_NAME = os.environ.get('TASK_TOKENS_TABLE_NAME', os.environ.get('IDEMPOTENCY_TABLE'))
try:
    from src.common.aws_clients import get_dynamodb_resource
    _dynamodb = get_dynamodb_resource()
except ImportError:
    _dynamodb = boto3.resource('dynamodb')
_idempotency_table = _dynamodb.Table(_IDEMPOTENCY_TABLE_NAME) if _IDEMPOTENCY_TABLE_NAME else None

try:
    from src.common.aws_clients import get_s3_client
    _s3_client = get_s3_client()
except ImportError:
    _s3_client = boto3.client('s3')

def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Entry point for Segment Executions.

    Refactored to "Tiny Handler" pattern.
    Logic delegated to:
    - src.services.execution.segment_runner_service.SegmentRunnerService
    """
    # 🛡️ [v3.4 Critical] NULL Event Defense
    # Step Functions may pass null event if ASL ResultPath/Payload mapping is misconfigured
    if event is None:
        logger.error("🚨 [CRITICAL] Received NULL event from Step Functions! Check ASL mapping.")
        error_result = {
            "status": "FAILED",
            "_status": "FAILED",  # 🛡️ [v3.21 Fix] USC reads _status (not status) for next_action
            "error": "Event is None. Check ASL ResultPath/Payload mapping.",
            "error_type": "NullEventError",
            "final_state": {},
            "final_state_s3_path": None,
            "next_segment_to_run": None,
            "new_history_logs": [],
            "branches": None,
            "segment_type": "ERROR",
            "total_segments": 1,
            "segment_id": 0
        }
        # 🎒 [v3.13] Use seal_state_bag for ASL contract compliance
        if KERNEL_PROTOCOL_AVAILABLE:
            return seal_state_bag(
                base_state={},
                result_delta=error_result,
                action='error',
                context={'error_type': 'NullEventError'}
            )
        # Fallback: wrap in state_data.bag for ASL compatibility
        # 🔧 [Fix] Match initialize_state_data error response structure
        return {
            "state_data": {"bag": error_result},
            "next_action": "FAILED"
        }

    try:
        # PII / Logging safety check
        # Limit log size
        event_str = json.dumps(event)
        log_size = len(event_str)
        if log_size > 10000:
             logger.info("🚀 Segment Runner started. Event size: %s (large event truncated)", log_size)
        else:
             logger.info("🚀 Segment Runner started. Event: %s", event_str)

        # [v3.35] Lambda Timeout Watchdog: extract deadline for graceful shutdown
        deadline_ms = None
        if context and hasattr(context, 'get_remaining_time_in_millis'):
            remaining = context.get_remaining_time_in_millis()
            deadline_ms = time.time() * 1000 + remaining - 15000  # 15s buffer for seal

        # [v3.35] Segment idempotency: skip if this exact segment was already processed
        _seg_idem_key = None
        _exec_id = None
        if isinstance(event, dict):
            _exec_id = event.get('execution_id', '')
            _seg_num = event.get('segment_to_run', 0)
            if _exec_id:
                _seg_idem_key = f"{_exec_id}#seg_{_seg_num}"

        if _seg_idem_key and _idempotency_table:
            try:
                _existing = _idempotency_table.get_item(Key={'idempotency_key': _seg_idem_key}).get('Item')
                if _existing and _existing.get('status') == 'COMPLETED':
                    logger.info(f"Segment already processed (idempotent skip): {_seg_idem_key}")
                    if _existing.get('result_s3_path'):
                        _cached = _s3_client.get_object(Bucket=_S3_BUCKET, Key=_existing['result_s3_path'])
                        return json.loads(_cached['Body'].read().decode('utf-8'))
                    elif _existing.get('result_json'):
                        return json.loads(_existing['result_json'])
            except Exception as e:
                logger.warning(f"Segment idempotency check failed (proceeding): {e}")

        # 🛡️ [v2.5] S3 bucket 강제 동기화 - 핸들러에서 서비스로 전달
        service = SegmentRunnerService(s3_bucket=_S3_BUCKET, deadline_ms=deadline_ms)
        result = service.execute_segment(event)

        # 🛡️ [v2.5] TypeError 방어 코드 - total_segments 보장 (강제 캐스팅)
        if result and isinstance(result, dict):
            # 🛡️ [Critical Fix] 어떤 타입이든 int로 강제 변환 시도
            raw_total = result.get('total_segments')
            if raw_total is None:
                raw_total = event.get('total_segments')

            try:
                # Dict, List 등 잘못된 타입이 들어와도 int()로 변환 시도
                if isinstance(raw_total, (int, float)):
                    result['total_segments'] = max(1, int(raw_total))
                elif isinstance(raw_total, str) and raw_total.strip().isdigit():
                    result['total_segments'] = max(1, int(raw_total.strip()))
                elif raw_total is None:
                    # 최후의 보루: partition_map 크기 체크 또는 1로 강제
                    p_map = event.get('partition_map', [])
                    result['total_segments'] = len(p_map) if isinstance(p_map, list) and p_map else 1
                    logger.info(f"🛡️ [v2.5] total_segments forced to {result['total_segments']}")
                else:
                    # Dict, List 등 예상치 못한 타입
                    logger.error(f"🚨 Invalid total_segments type: {type(raw_total).__name__} = {raw_total}")
                    p_map = event.get('partition_map', [])
                    result['total_segments'] = len(p_map) if isinstance(p_map, list) and p_map else 1
            except (TypeError, ValueError) as e:
                # 모든 변환 시도 실패 시
                logger.error(f"🚨 Failed to convert total_segments: {e}, raw_value={raw_total}")
                result['total_segments'] = 1

            # 🛡️ [v3.3] threshold 안전 추출 (AttributeError 방지)
            # service.threshold 속성이 없을 수 있으므로 getattr 사용
            if 'state_size_threshold' not in result:
                result['state_size_threshold'] = getattr(service, 'threshold', 180)

        logger.info("✅ Segment Runner finished successfully.")

        # [v3.35] Record segment completion for idempotency
        if _seg_idem_key and _idempotency_table and result:
            try:
                _result_json = json.dumps(result, default=str)
                if len(_result_json) > 350000:
                    _s3_idem_key = f"idempotency/{_seg_idem_key}.json"
                    _s3_client.put_object(Bucket=_S3_BUCKET, Key=_s3_idem_key, Body=_result_json)
                    _idempotency_table.put_item(Item={
                        'idempotency_key': _seg_idem_key,
                        'status': 'COMPLETED',
                        'result_s3_path': _s3_idem_key,
                        'completed_at': int(time.time()),
                        'ttl': int(time.time()) + 86400
                    })
                else:
                    _idempotency_table.put_item(Item={
                        'idempotency_key': _seg_idem_key,
                        'status': 'COMPLETED',
                        'result_json': _result_json,
                        'completed_at': int(time.time()),
                        'ttl': int(time.time()) + 86400
                    })
            except Exception as e:
                logger.warning(f"Failed to record segment idempotency: {e}")

        # 🎒 [v3.13] ASL contract enforcement: ensure result has next_action
        # service.execute_segment() has multiple return paths:
        #   - kernel protocol path already returns sealed {state_data, next_action}
        #   - all other paths return raw {status, final_state, ...} without next_action
        # If raw, seal now so SFN ResultSelector can find $.Payload.next_action.
        # Without this, EvaluateNextAction always hits Default→IncrementLoopCounter→loop limit.
        if KERNEL_PROTOCOL_AVAILABLE and isinstance(result, dict) and 'next_action' not in result:
            try:
                base_state = open_state_bag(event)
            except Exception:
                base_state = {}
            result = seal_state_bag(
                base_state=base_state,
                result_delta=result,
                action='sync',
                context={}
            )

        return result

    except Exception as e:
        if isinstance(e, TimeoutError):
            logger.warning("Lambda deadline reached — state sealed safely via graceful shutdown")
        logger.exception("❌ Segment Runner failed")
        # Return error state that Step Functions can catch
        # 🎒 [v3.13] Use seal_state_bag for ASL contract compliance
        error_info = {
            "error": str(e),
            "error_type": type(e).__name__
        }

        # 🛡️ [v3.3] total_segments 추출 로직 강화 (S3 포인터 대응)
        # 우선순위: event.total_segments > partition_map 길이 > 기본값 유지
        raw_total = event.get('total_segments') if event else None
        p_map = event.get('partition_map') if event else None

        # 1. 숫자로 변환 가능한 경우
        if raw_total is not None:
            try:
                if isinstance(raw_total, (int, float)):
                    safe_total_segments = max(1, int(raw_total))
                elif isinstance(raw_total, str) and raw_total.strip().isdigit():
                    safe_total_segments = max(1, int(raw_total.strip()))
                else:
                    safe_total_segments = None
            except (TypeError, ValueError):
                safe_total_segments = None
        else:
            safe_total_segments = None

        # 2. partition_map이 실제 리스트인 경우
        if safe_total_segments is None and isinstance(p_map, list) and p_map:
            safe_total_segments = len(p_map)

        # 3. 최후의 보루: S3 포인터만 있거나 완전히 없는 경우
        if safe_total_segments is None:
            safe_total_segments = 1
            if event and event.get('partition_map_s3_path'):
                logger.warning(
                    f"🛡️ [v3.3] total_segments unknown (partition_map offloaded to S3). "
                    f"Using fallback=1. This may cause premature workflow termination."
                )

        # 🎒 [v3.13] Build error result for seal_state_bag
        # 🛡️ [v3.21 Fix] Must include '_status' = 'FAILED' so USC _compute_next_action
        # reads the correct status from delta._status (not delta.status which USC ignores).
        # Without _status, USC defaults to 'CONTINUE' and the SFN loops forever.
        error_result = {
            "status": "FAILED",
            "_status": "FAILED",  # 🛡️ [v3.21] USC reads _status, not status
            "error": str(e),
            "error_type": type(e).__name__,
            "final_state": event.get('current_state', {}) if event else {},
            "final_state_s3_path": None,
            "next_segment_to_run": None,
            "new_history_logs": [],
            "error_info": error_info,
            "branches": None,
            "segment_type": "ERROR",
            "total_segments": safe_total_segments,
            "segment_id": event.get('segment_id', 0) if event else 0
        }

        # 🎒 [v3.13] Use seal_state_bag for ASL contract compliance
        if KERNEL_PROTOCOL_AVAILABLE:
            try:
                base_state = open_state_bag(event) if event else {}
            except Exception:
                base_state = {}
            return seal_state_bag(
                base_state=base_state,
                result_delta=error_result,
                action='error',
                context={'error_type': type(e).__name__}
            )

        # Fallback: wrap in state_data.bag for ASL compatibility
        # 🔧 [Fix] Match initialize_state_data error response structure
        # ASL JSONPath: $.state_data.bag.error_type
        return {
            "state_data": {"bag": error_result},  # Wrap in bag for ASL
            "next_action": "FAILED"
        }

# --- Legacy Helper Imports REMOVED (v3.3) ---
# 🚨 [WARNING] 아래 임포트는 Circular Import 위험으로 제거되었습니다.
# 필요한 경우 함수 내부에서 Local Import를 사용하세요.
#
# REMOVED:
#   from src.services.state.state_manager import StateManager
#   from src.services.workflow.repository import WorkflowRepository
#   from src.handlers.core.main import run_workflow, partition_workflow, _build_segment_config
#
# 대안: 함수 내부에서 로컬 임포트 사용
# def some_function():
#     from src.services.state.state_manager import StateManager
#     ...
