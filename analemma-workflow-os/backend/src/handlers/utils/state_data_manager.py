"""
State Data Manager Lambda Function
Lambda function responsible for payload size management and S3 offloading

v3.3 - Unified Pipe: 탄생부터 소멸까지 단일 파이프
    
    데이터 생애 주기 (Unified Pipe):
        - 탄생 (Init): {} → Universal Sync → StateBag v0
        - 성장 (Sync): StateBag vN + Result → Universal Sync → StateBag vN+1
        - 협업 (Aggregate): StateBag vN + Branches → Universal Sync → StateBag vFinal
    
    - 모든 액션 함수는 "3줄짜리 래퍼"로 변환됨
    - 실제 로직은 universal_sync_core 단일 엔진에서 처리
    - Copy-on-Write + Shallow Merge로 성능 최적화
    - StateHydrator로 S3 복구 시 재시도 + 체크섬 검증
    - P0~P2 이슈 자동 해결 (어떤 경로로 들어왔든 크면 S3로)
"""

import json
import boto3
import gzip
import base64
import hashlib
import time
from typing import Dict, Any, Tuple, Optional, List, Callable
from datetime import datetime, timezone
import os
import sys

# Add the common directory to the path
sys.path.append('/opt/python')
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'common'))

from src.common.logging_utils import get_logger

# v3.2: Universal Sync Core import
try:
    from .universal_sync_core import universal_sync_core, get_default_hydrator
except ImportError:
    # Lambda 환경에서 상대 import 실패 시
    from universal_sync_core import universal_sync_core, get_default_hydrator

# 직접 Logger 생성 (lazy import 회피)
from aws_lambda_powertools import Logger
import os
log_level = os.getenv("LOG_LEVEL", "INFO")
logger = Logger(
    service=os.getenv("AWS_LAMBDA_FUNCTION_NAME", "analemma-backend"),
    level=log_level,
    child=True
)

# AWS clients
s3_client = boto3.client('s3')
cloudwatch_client = boto3.client('cloudwatch')

# Environment variables
# Environment variables
S3_BUCKET = os.environ.get('STATE_STORAGE_BUCKET')
if not S3_BUCKET:
    logger.error("[CRITICAL] STATE_STORAGE_BUCKET env var is NOT set! Offloading will fail.")

MAX_PAYLOAD_SIZE_KB = int(os.environ.get('MAX_PAYLOAD_SIZE_KB', '200'))
MAX_PAYLOAD_SIZE_KB = int(os.environ.get('MAX_PAYLOAD_SIZE_KB', '200'))


def calculate_payload_size(data: Dict[str, Any]) -> int:
    """Calculate payload size in KB"""
    try:
        json_str = json.dumps(data, separators=(',', ':'))
        size_bytes = len(json_str.encode('utf-8'))
        size_kb = size_bytes / 1024
        return int(size_kb)
    except Exception as e:
        logger.warning(f"Failed to calculate payload size: {e}")
        return 0


def compress_data(data: Any) -> str:
    """Compress data using gzip and return base64 encoded string"""
    try:
        json_str = json.dumps(data, separators=(',', ':'))
        compressed = gzip.compress(json_str.encode('utf-8'))
        return base64.b64encode(compressed).decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to compress data: {e}")
        raise


def decompress_data(compressed_str: str) -> Any:
    """Decompress base64 encoded gzip data"""
    try:
        compressed = base64.b64decode(compressed_str.encode('utf-8'))
        decompressed = gzip.decompress(compressed)
        return json.loads(decompressed.decode('utf-8'))
    except Exception as e:
        logger.error(f"Failed to decompress data: {e}")
        raise


def store_to_s3(data: Any, key: str) -> str:
    """Store data to S3 and return the S3 path"""
    try:
        json_str = json.dumps(data, separators=(',', ':'))
        
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=json_str,
            ContentType='application/json',
            ServerSideEncryption='AES256'
        )
        
        s3_path = f"s3://{S3_BUCKET}/{key}"
        logger.info(f"Stored data to S3: {s3_path}")
        return s3_path
        
    except Exception as e:
        logger.error(f"Failed to store data to S3: {e}")
        # Re-raise to allow caller to handle fallback
        raise


def load_from_s3(s3_path: str, expected_checksum: Optional[str] = None, max_retries: int = 3) -> Any:
    """
    📥 [P0 + P1] S3에서 데이터 로드 (재시도 + 체크섬 검증)
    
    포인터 기반 아키텍처에서 실제 데이터를 불러올 때 사용.
    
    v3.1 개선사항:
        - Exponential Backoff 재시도 (최대 3회)
        - 체크섬 검증 (데이터 무결성 보장)
        - ③ 커널의 자존심: 데이터가 깨졌다면 에러를 내고 재시도
    
    Args:
        s3_path: S3 경로 (s3://bucket/key)
        expected_checksum: 예상 MD5 해시 (선택적)
        max_retries: 최대 재시도 횟수
    
    Returns:
        로드된 데이터 또는 None (실패 시)
    """
    if not s3_path or not s3_path.startswith('s3://'):
        return None
    
    last_error = None
    base_delay = 0.5  # 초기 대기 시간
    
    for attempt in range(max_retries):
        try:
            # Parse s3://bucket/key format
            path_parts = s3_path.replace('s3://', '').split('/', 1)
            bucket = path_parts[0]
            key = path_parts[1] if len(path_parts) > 1 else ''
            
            response = s3_client.get_object(Bucket=bucket, Key=key)
            content = response['Body'].read()
            content_str = content.decode('utf-8')
            
            # ③ Checksum 검증 - 데이터가 깨졌다면 에러를 내고 재시도
            if expected_checksum:
                actual_checksum = hashlib.md5(content).hexdigest()
                if actual_checksum != expected_checksum:
                    raise ValueError(
                        f"Checksum mismatch! Expected {expected_checksum}, got {actual_checksum}. "
                        "Data corrupted - triggering retry."
                    )
            
            return json.loads(content_str)
            
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = min(base_delay * (2 ** attempt), 8.0)  # 최대 8초
                logger.warning(f"Retry {attempt+1}/{max_retries} for {s3_path} after {delay:.2f}s: {e}")
                time.sleep(delay)
            else:
                logger.error(f"Failed to load from S3 {s3_path} after {max_retries} attempts: {last_error}")
    
    return None


# ============================================
# [P0] 중복 로그 방지 헬퍼
# ============================================
def deduplicate_history_logs(existing_logs: List[Dict], new_logs: List[Dict]) -> List[Dict]:
    """
    🛡️ [P0] 히스토리 로그 중복 제거
    
    Lambda 재시도 등으로 동일한 로그가 중복 발생할 수 있음.
    로그의 고유 ID(node_id + timestamp)를 기준으로 중복 필터링.
    
    Args:
        existing_logs: 기존 히스토리 로그
        new_logs: 새로 추가할 로그
    
    Returns:
        중복 제거된 병합 로그 리스트 (시간순 정렬)
    """
    if not new_logs:
        return existing_logs
    
    # 고유 키 생성 함수
    def get_log_key(log: Dict) -> str:
        """node_id + timestamp 기반 고유 키 생성"""
        if not isinstance(log, dict):
            return str(hash(str(log)))
        
        node_id = log.get('node_id', log.get('id', ''))
        timestamp = log.get('timestamp', log.get('created_at', ''))
        
        # 둘 다 있으면 조합, 아니면 개별 필드 사용
        if node_id and timestamp:
            return f"{node_id}:{timestamp}"
        elif node_id:
            return f"node:{node_id}"
        elif timestamp:
            return f"ts:{timestamp}"
        else:
            # 둘 다 없으면 전체 콘텐츠 해시
            return str(hash(json.dumps(log, sort_keys=True, default=str)))
    
    # 기존 로그의 키 세트 구성
    seen_keys = set()
    for log in existing_logs:
        seen_keys.add(get_log_key(log))
    
    # 중복되지 않은 새 로그만 추가
    deduplicated_new = []
    for log in new_logs:
        key = get_log_key(log)
        if key not in seen_keys:
            deduplicated_new.append(log)
            seen_keys.add(key)
        else:
            logger.debug(f"Duplicate log filtered: {key}")
    
    if len(deduplicated_new) < len(new_logs):
        logger.info(f"Filtered {len(new_logs) - len(deduplicated_new)} duplicate logs")
    
    return existing_logs + deduplicated_new


# ============================================
# [최적화] S3 역직렬화 비용 감소
# ============================================
# 자주 사용되는 제어 필드 - 오프로딩 제외 대상
CONTROL_FIELDS_NEVER_OFFLOAD = {
    'execution_id',
    'segment_to_run',
    'loop_counter',
    'next_action',
    'status',
    'idempotency_key',
    'state_s3_path',
    'pre_snapshot_s3_path',
    'post_snapshot_s3_path',
    'last_update_time',
    'payload_size_kb'
}

# Lambda 내부 캐시 (Cold Start 동안 유지)
_s3_cache: Dict[str, Any] = {}
_cache_timestamps: Dict[str, float] = {}
CACHE_TTL_SECONDS = 300  # 5분


def cached_load_from_s3(s3_path: str) -> Any:
    """
    📥 [Optimized] TTL 기반 캐시를 사용한 S3 로드
    
    동일한 S3 경로를 반복 요청할 때 네트워크 비용 감소.
    Lambda 콜드 스타트 동안 캐시 유지 (5분 TTL)
    """
    import time
    
    if not s3_path:
        return None
    
    current_time = time.time()
    
    # 캐시 확인
    if s3_path in _s3_cache:
        cache_time = _cache_timestamps.get(s3_path, 0)
        if current_time - cache_time < CACHE_TTL_SECONDS:
            logger.debug(f"Cache hit for {s3_path}")
            return _s3_cache[s3_path]
        else:
            # TTL 만료 - 캐시 제거
            del _s3_cache[s3_path]
            del _cache_timestamps[s3_path]
    
    # S3에서 로드
    data = load_from_s3(s3_path)
    
    if data is not None:
        # 캐시에 저장 (최대 20개 항목만 유지)
        if len(_s3_cache) >= 20:
            # 가장 오래된 항목 제거
            oldest_key = min(_cache_timestamps, key=_cache_timestamps.get)
            del _s3_cache[oldest_key]
            del _cache_timestamps[oldest_key]
        
        _s3_cache[s3_path] = data
        _cache_timestamps[s3_path] = current_time
    
    return data


def generate_s3_key(idempotency_key: str, data_type: str) -> str:
    """Generate S3 key for storing data"""
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    return f"workflow-state/{idempotency_key}/{data_type}_{timestamp}.json"


def optimize_state_history(state_history: list, idempotency_key: str, max_entries: int = 50) -> Tuple[list, Optional[str]]:
    """Optimize state history by keeping recent entries and storing old ones to S3"""
    if not state_history or len(state_history) <= max_entries:
        return state_history, None
    
    # Keep recent entries
    recent_history = state_history[-max_entries:]
    
    # Store old entries to S3 if there are many
    old_history = state_history[:-max_entries]
    if len(old_history) > 10:  # Only store if significant amount
        try:
            # idempotency_key used for S3 path generation
            s3_key = generate_s3_key(idempotency_key, "history_archive")
            s3_path = store_to_s3(old_history, s3_key)

            
            # Add reference to archived history
            archive_ref = {
                "type": "history_archive",
                "s3_path": s3_path,
                "entry_count": len(old_history),
                "archived_at": datetime.now(timezone.utc).isoformat()
            }
            recent_history.insert(0, archive_ref)
            
            return recent_history, s3_path
        except Exception as e:
            logger.warning(f"Failed to archive old history: {e}")
            return state_history, None
    
    return recent_history, None


def optimize_current_state(current_state: Dict[str, Any], idempotency_key: str) -> Tuple[Dict[str, Any], bool]:
    """Optimize current state by moving large fields to S3"""
    if not current_state:
        return current_state, False
    
    # 🛡️ [P0 Critical] Calculate total state size first
    try:
        total_size_kb = calculate_payload_size(current_state)
    except:
        total_size_kb = 0
        
    optimized_state = current_state.copy()
    s3_offloaded = False
    
    # Strategy 1: Individual Field Offloading
    # Iterate over ALL fields, not just a hardcoded list
    for field, field_data in list(optimized_state.items()):
        # Skip small primitive types to save calculation time
        if field_data is None or isinstance(field_data, (bool, int, float)):
            continue
            
        # Skip already offloaded fields
        if isinstance(field_data, dict) and field_data.get('__s3_offloaded'):
            continue
            
        # Helper to check field size
        try:
            field_size = calculate_payload_size({field: field_data})
        except:
            continue
            
        # Move to S3 if field is larger than 30KB (Lowered from 50KB for safety)
        if field_size > 30:
            try:
                s3_key = generate_s3_key(idempotency_key, f"state_{field}")
                s3_path = store_to_s3(field_data, s3_key)
                
                # Replace with S3 reference
                optimized_state[field] = {
                    "type": "s3_reference",
                    "s3_path": s3_path,
                    "size_kb": field_size,
                    "stored_at": datetime.now(timezone.utc).isoformat()
                }
                
                s3_offloaded = True
                logger.info(f"Moved {field} ({field_size}KB) to S3: {s3_path}")
                
            except Exception as e:
                logger.error(f"[CRITICAL] Failed to move {field} to S3: {e}. Truncating field to prevent catastrophic failure.")
                # 🛡️ [Fail-Safe] If offload fails, WE MUST NOT return the huge field.
                # Returning it guarantees a crash (States.DataLimitExceeded).
                # Truncating it causes data loss but keeps the workflow alive for debugging.
                optimized_state[field] = {
                    "type": "error_truncated",
                    "error": f"S3 Offload Failed: {str(e)}",
                    "original_size_kb": field_size
                }

    # Strategy 2: Full State Offloading (Fallback)
    # If state is still too large (> 100KB) after individual field optimization,
    # offload the ENTIRE state object.
    final_size_kb = calculate_payload_size(optimized_state)
    
    if final_size_kb > 100:
        logger.info(f"State still too large ({final_size_kb}KB > 100KB) after field optimization. Offloading ENTIRE state.")
        try:
            s3_key = generate_s3_key(idempotency_key, "full_state")
            s3_path = store_to_s3(optimized_state, s3_key)
            
            # Return a pointer to the full state
            # Preserve minimal metadata for Step Functions routing if needed
            wrapper = {
                "__s3_offloaded": True,
                "__s3_path": s3_path,
                "__original_size_kb": final_size_kb,
                # Preserve critical scheduling/guardrail metadata for router
                "guardrail_verified": optimized_state.get('guardrail_verified', False),
                "batch_count_actual": optimized_state.get('batch_count_actual', 1),
                "scheduling_metadata": optimized_state.get('scheduling_metadata', {}),
                "__scheduling_metadata": optimized_state.get('scheduling_metadata', {}),
                "__guardrail_verified": optimized_state.get('guardrail_verified', False),
                "__batch_count_actual": optimized_state.get('batch_count_actual', 1),
            }
            return wrapper, True
            
        except Exception as e:
            logger.error(f"Failed to offload full state: {e}")
            # Return partially optimized state as best effort
            return optimized_state, s3_offloaded

    return optimized_state, s3_offloaded


def update_and_compress_state_data(event: Dict[str, Any]) -> Dict[str, Any]:
    """Main function to update and compress state data"""
    state_data = event.get('state_data', {})
    execution_result = event.get('execution_result', {})
    max_payload_size_kb = event.get('max_payload_size_kb', MAX_PAYLOAD_SIZE_KB)
    
    # Extract key information
    idempotency_key = state_data.get('idempotency_key', 'unknown')
    
    # Update state data with execution result
    updated_state_data = {
        'light_config': state_data.get('light_config'),
        'workflow_config_s3_path': state_data.get('workflow_config_s3_path'),
        'state_s3_path': execution_result.get('final_state_s3_path', state_data.get('state_s3_path')),
        'state_history': execution_result.get('new_history_logs', state_data.get('state_history', [])),
        'ownerId': state_data.get('ownerId'),
        'workflowId': state_data.get('workflowId'),
        'segment_to_run': state_data.get('segment_to_run'),
        'idempotency_key': idempotency_key,
        'quota_reservation_id': state_data.get('quota_reservation_id'),
        'total_segments': state_data.get('total_segments'),
        'partition_map': state_data.get('partition_map'),
        # [FIX] Add missing fields for Step Functions loop and Distributed Map
        'partition_map_s3_path': state_data.get('partition_map_s3_path'),
        # 🚨 [Critical] Distributed Map Manifest Fields
        'segment_manifest': state_data.get('segment_manifest'),
        'segment_manifest_s3_path': state_data.get('segment_manifest_s3_path'),
        
        'distributed_mode': state_data.get('distributed_mode'),
        'distributed_strategy': state_data.get('distributed_strategy'),
        'max_concurrency': state_data.get('max_concurrency'),
        
        # 🚨 [Critical] Statistics Fields for Scenario J
        'llm_segments': state_data.get('llm_segments'),
        'hitp_segments': state_data.get('hitp_segments'),
        
        # [Fix] Preserve input & pointers during sync to prevent Data Loss
        'input': state_data.get('input'),
        'input_s3_path': state_data.get('input_s3_path'),
        
        'state_durations': state_data.get('state_durations'),
        'last_update_time': state_data.get('last_update_time'),
        'start_time': state_data.get('start_time'),
        'max_loop_iterations': int(state_data.get('max_loop_iterations', 100)),
        'max_branch_iterations': int(state_data.get('max_branch_iterations', 100)),
        'loop_counter': int(state_data.get('loop_counter', 0))
    }

    
    # Calculate initial payload size
    initial_size_kb = calculate_payload_size(updated_state_data)
    logger.info(f"Initial payload size: {initial_size_kb}KB")
    
    compression_applied = False
    s3_offloaded = False
    
    # If payload is too large, apply optimizations
    # [Fix] ALWAYS optimize current state to catch large individual fields early
    # regardless of total size. This keeps state lean.
    if updated_state_data.get('current_state'):
        optimized_state, state_s3_offloaded = optimize_current_state(
            updated_state_data['current_state'], 
            idempotency_key
        )
        updated_state_data['current_state'] = optimized_state
        if state_s3_offloaded:
            s3_offloaded = True

    if initial_size_kb > max_payload_size_kb:
        logger.info(f"Payload size ({initial_size_kb}KB) exceeds limit ({max_payload_size_kb}KB), applying further optimizations")
        
        # 1. Optimize state history
        if updated_state_data.get('state_history'):
            optimized_history, history_s3_path = optimize_state_history(
                updated_state_data['state_history'], 
                idempotency_key=idempotency_key,
                max_entries=30
            )
            updated_state_data['state_history'] = optimized_history
            if history_s3_path:
                compression_applied = True
        
        # 2. [Already done above] Optimize current state
        # (Moved outside this block to run unconditionally)
        
        # 3. If still too large, compress workflow_config
        final_size_kb = calculate_payload_size(updated_state_data)
        if final_size_kb > max_payload_size_kb and updated_state_data.get('workflow_config'):
            try:
                compressed_config = compress_data(updated_state_data['workflow_config'])
                updated_state_data['workflow_config'] = {
                    "type": "compressed",
                    "data": compressed_config,
                    "compressed_at": datetime.now(timezone.utc).isoformat()
                }
                compression_applied = True
                logger.info("Applied compression to workflow_config")
            except Exception as e:
                logger.warning(f"Failed to compress workflow_config: {e}")
    
    # Final size calculation
    final_size_kb = calculate_payload_size(updated_state_data)
    
    # Add metadata
    updated_state_data['payload_size_kb'] = final_size_kb
    updated_state_data['compression_applied'] = compression_applied
    updated_state_data['s3_offloaded'] = s3_offloaded
    updated_state_data['last_optimization'] = datetime.now(timezone.utc).isoformat()
    
    logger.info(f"Final payload size: {final_size_kb}KB (compression: {compression_applied}, s3_offload: {s3_offloaded})")
    
    # Send CloudWatch metrics
    _send_cloudwatch_metrics(
        initial_size_kb=initial_size_kb,
        final_size_kb=final_size_kb,
        compression_applied=compression_applied,
        s3_offloaded=s3_offloaded,
        idempotency_key=idempotency_key
    )
    
    return updated_state_data


def _send_cloudwatch_metrics(
    initial_size_kb: int,
    final_size_kb: int,
    compression_applied: bool,
    s3_offloaded: bool,
    idempotency_key: str
) -> None:
    """CloudWatch 메트릭 발송"""
    try:
        metric_data = [
            {
                'MetricName': 'PayloadSizeKB',
                'Value': final_size_kb,
                'Unit': 'Kilobytes',
                'Dimensions': [
                    {
                        'Name': 'OptimizationType',
                        'Value': 'Final'
                    }
                ]
            },
            {
                'MetricName': 'PayloadSizeKB',
                'Value': initial_size_kb,
                'Unit': 'Kilobytes',
                'Dimensions': [
                    {
                        'Name': 'OptimizationType',
                        'Value': 'Initial'
                    }
                ]
            },
            {
                'MetricName': 'PayloadOptimization',
                'Value': 1 if compression_applied or s3_offloaded else 0,
                'Unit': 'Count',
                'Dimensions': [
                    {
                        'Name': 'OptimizationType',
                        'Value': 'Applied'
                    }
                ]
            }
        ]
        
        if initial_size_kb > 0:
            compression_ratio = (initial_size_kb - final_size_kb) / initial_size_kb * 100
            metric_data.append({
                'MetricName': 'CompressionRatio',
                'Value': compression_ratio,
                'Unit': 'Percent'
            })
        
        cloudwatch_client.put_metric_data(
            Namespace='Workflow/StateDataManager',
            MetricData=metric_data
        )
        
        logger.info(f"Sent CloudWatch metrics for {idempotency_key}")
        
    except Exception as e:
        logger.warning(f"Failed to send CloudWatch metrics: {e}")


def sync_state_data(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    🔄 [v3.2 Wrapper] 중앙 집중형 상태 동기화
    
    모든 Lambda 실행 결과를 state_data에 머지하고 S3 오프로딩 수행.
    ASL의 SyncStateData 상태에서 호출됩니다.
    
    v3.2: 3줄짜리 래퍼 - 실제 로직은 universal_sync_core에서 처리
    
    Returns:
        {"state_data": {...}, "next_action": "CONTINUE" | "COMPLETE" | ...}
    """
    # v3.2: 3줄짜리 래퍼 패턴
    state_data = event.get('state_data', {})
    execution_result = event.get('execution_result', {})
    
    return universal_sync_core(
        base_state=state_data,
        new_result={'execution_result': execution_result},
        context={'action': 'sync'}
    )


def hydrate_branch_config(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    🌿 [Pointer Strategy] S3에서 브랜치 설정 Hydrate
    
    Map 내부에서 호출되어 경량 포인터를 사용해 S3에서 실제 브랜치 설정을 로드합니다.
    
    Input:
        - branch_pointer: {branch_index, branch_id, branches_s3_path, total_branches}
        - branch_index: Map Iterator 인덱스
        - state_data: 부모의 State Bag
    
    Output:
        - branch_config: S3에서 로드된 실제 브랜치 설정 (partition_map, nodes 포함)
        - branch_index: 원본 인덱스
        - state_data: 원본 State Bag (변경 없음)
    
    이 패턴을 통해:
    1. pending_branches에는 경량 포인터만 전달 (256KB 제한 회피)
    2. Map 내부 각 브랜치가 개별적으로 S3에서 자신의 데이터 Hydrate
    3. 개별 Lambda 반환값은 작음 (256KB 제한 회피)
    """
    branch_pointer = event.get('branch_pointer', {})
    branch_index = event.get('branch_index', 0)
    state_data = event.get('state_data', {})
    
    # 포인터에서 S3 경로 추출
    branches_s3_path = branch_pointer.get('branches_s3_path')
    pointer_branch_index = branch_pointer.get('branch_index', branch_index)
    
    if not branches_s3_path:
        # 폴백: 포인터가 아닌 전체 브랜치 데이터가 직접 전달된 경우
        # (S3 오프로딩 실패 시 또는 작은 브랜치의 경우)
        logger.warning(f"[Hydrate Branch] No branches_s3_path in pointer. Using pointer as branch_config directly.")
        return {
            'branch_config': branch_pointer,
            'branch_index': branch_index,
            'state_data': state_data
        }
    
    try:
        # S3에서 전체 branches 배열 로드 (캐싱 활용)
        all_branches = cached_load_from_s3(branches_s3_path)
        
        if not all_branches:
            logger.error(f"[Hydrate Branch] Failed to load branches from S3: {branches_s3_path}")
            raise ValueError(f"Failed to load branches from S3: {branches_s3_path}")
        
        if not isinstance(all_branches, list):
            logger.error(f"[Hydrate Branch] Loaded data is not a list: {type(all_branches)}")
            raise ValueError(f"Loaded branches data is not a list")
        
        # 해당 인덱스의 브랜치 추출
        if pointer_branch_index >= len(all_branches):
            logger.error(f"[Hydrate Branch] Branch index {pointer_branch_index} out of range (total: {len(all_branches)})")
            raise ValueError(f"Branch index {pointer_branch_index} out of range")
        
        branch_config = all_branches[pointer_branch_index]
        
        logger.info(f"[Hydrate Branch] ✅ Loaded branch {pointer_branch_index}/{len(all_branches)} from S3 "
                   f"(nodes: {len(branch_config.get('nodes', []))}, "
                   f"partition_map: {len(branch_config.get('partition_map', []))})")
        
        return {
            'branch_config': branch_config,
            'branch_index': branch_index,
            'state_data': state_data
        }
        
    except Exception as e:
        logger.error(f"[Hydrate Branch] ❌ Error hydrating branch {pointer_branch_index}: {e}")
        raise


def aggregate_branches(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    🔀 [v3.2 Wrapper] 병렬 브랜치 결과 집계 (Fork-Join)
    
    ProcessParallelBranches의 Map 상태 결과를 머지합니다.
    
    v3.2: 3줄짜리 래퍼 - P0/P1 자동 해결은 universal_sync_core에서 처리
    """
    state_data = event.get('state_data', {})
    load_from_s3_flag = event.get('load_from_s3', False)
    map_error = event.get('map_error')
    
    # 에러 처리 (래퍼에서 빠른 반환)
    if map_error:
        logger.error(f"Map state error: {map_error}")
        return {'state_data': state_data, 'next_action': 'FAILED'}
    
    # P0: 포인터 기반 vs 전체 결과 입력 정규화
    if load_from_s3_flag:
        branch_pointers = event.get('branch_pointers', [])
        # 포인터 모드: S3에서 로그 로드
        all_logs = []
        for pointer in branch_pointers:
            if pointer.get('status') == 'COMPLETE':
                s3_path = pointer.get('s3_path') or pointer.get('final_state_s3_path')
                if s3_path:
                    branch_data = cached_load_from_s3(s3_path)
                    if branch_data:
                        all_logs.extend(branch_data.get('new_history_logs', []))
        normalized_result = {'parallel_results': branch_pointers, 'new_history_logs': all_logs}
    else:
        normalized_result = {'parallel_results': event.get('parallel_results', [])}
    
    return universal_sync_core(
        base_state=state_data,
        new_result=normalized_result,
        context={'action': 'aggregate_branches'}
    )


def merge_callback_result(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    📞 [v3.2 Wrapper] 콜백 결과 머지 (HITP)
    
    WaitForHITPCallback 후 콜백 결과를 state_data에 머지합니다.
    
    v3.2: 3줄짜리 래퍼 - P2 자동 해결은 universal_sync_core에서 처리
    """
    state_data = event.get('state_data', {})
    callback_result = event.get('callback_result', {})
    
    return universal_sync_core(
        base_state=state_data,
        new_result={'callback_result': callback_result},
        context={'action': 'merge_callback'}
    )


def merge_async_result(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    🤖 [v3.2 Wrapper] 비동기 LLM 결과 머지
    
    v3.2: 3줄짜리 래퍼 - P2 자동 해결은 universal_sync_core에서 처리
    """
    state_data = event.get('state_data', {})
    async_result = event.get('async_result', {})
    
    return universal_sync_core(
        base_state=state_data,
        new_result={'async_result': async_result},
        context={'action': 'merge_async'}
    )


def aggregate_distributed_results(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    🚀 [v3.2 Wrapper] MAP_REDUCE/BATCHED 결과 집계 (Distributed Mode)
    
    v3.2: 3줄짜리 래퍼 - P0/P1 자동 해결은 universal_sync_core에서 처리
        - P0: 자동 오프로딩으로 256KB 제한 방지
        - P1: execution_order 기준 정렬로 논리적 순서 보장
    """
    state_data = event.get('state_data', {})
    execution_result = event.get('execution_result', [])
    pre_snapshot = event.get('pre_snapshot')
    create_post_snapshot = event.get('create_post_snapshot', False)
    
    # Pre-snapshot 경로 추가 (래퍼에서 처리)
    if pre_snapshot:
        state_data = state_data.copy()
        state_data['pre_distributed_snapshot'] = pre_snapshot
        logger.info(f"Using pre-snapshot: {pre_snapshot}")
    
    # universal_sync_core 호출
    result = universal_sync_core(
        base_state=state_data,
        new_result=execution_result,  # 리스트 직접 전달
        context={'action': 'aggregate_distributed'}
    )
    
    updated_state = result['state_data']
    
    # 전체 실패 여부 확인
    chunk_summary = updated_state.get('distributed_chunk_summary', {})
    final_status = 'FAILED' if chunk_summary.get('failed', 0) == chunk_summary.get('total', 1) else 'COMPLETE'
    
    # Post-snapshot 생성 (요청시)
    post_snapshot_path = None
    if create_post_snapshot and final_status == 'COMPLETE':
        try:
            snapshot_result = create_snapshot({
                'state_data': updated_state,
                'snapshot_type': 'post'
            })
            post_snapshot_path = snapshot_result.get('snapshot_s3_path')
            updated_state['post_distributed_snapshot'] = post_snapshot_path
        except Exception as e:
            logger.error(f"Failed to create post-snapshot: {e}")
    
    logger.info(f"Aggregated distributed results: {chunk_summary.get('succeeded', 0)} success, {chunk_summary.get('failed', 0)} failed")
    
    return {
        'state_data': updated_state,
        'final_status': final_status,
        'post_snapshot': post_snapshot_path
    }


def sync_branch_state(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    🌿 [v3.2 Wrapper] 브랜치 내 상태 동기화
    
    v3.2: 3줄짜리 래퍼 - 실제 로직은 universal_sync_core에서 처리
    
    Note: branch_state는 state_data와 구조가 다르므로 
    래퍼에서 적절히 변환하여 코어에 전달합니다.
    """
    branch_state = event.get('branch_state', {})
    execution_result = event.get('execution_result', {})
    
    # branch_state에서 state_data 추출 또는 branch_state 자체를 사용
    state_data = branch_state.get('state_data', branch_state)
    
    result = universal_sync_core(
        base_state=state_data,
        new_result={'execution_result': execution_result},
        context={'action': 'sync_branch'}
    )
    
    # 결과를 branch_state 형식으로 재구성
    updated_branch = branch_state.copy()
    if 'state_data' in branch_state:
        updated_branch['state_data'] = result['state_data']
    else:
        updated_branch.update(result['state_data'])
    
    updated_branch['segment_to_run'] = result['state_data'].get('segment_to_run', 0)
    updated_branch['loop_counter'] = result['state_data'].get('loop_counter', 0)
    
    return {
        'branch_state': updated_branch,
        'next_action': result['next_action']
    }


def create_snapshot(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    📸 [v3.2] 상태 스냅샷 생성
    
    Distributed Map 실행 전/후 상태를 S3에 스냅샷으로 저장합니다.
    - Pre-Snapshot: MAP 실행 전 상태 보존 (재실행/복구용)
    - Post-Snapshot: MAP 실행 후 최종 상태 기록
    
    v3.2: 포인터 모드 자동 감지 - state_s3_path 존재 시 경량 스냅샷 생성
    
    Returns:
        state_data: 스냅샷 경로가 추가된 상태
        snapshot_s3_path: 스냅샷 S3 경로
    """
    state_data = event.get('state_data', {})
    snapshot_type = event.get('snapshot_type', 'pre')  # 'pre' or 'post'
    execution_id = state_data.get('execution_id', 'unknown')
    idempotency_key = state_data.get('idempotency_key', 'unknown')
    
    updated_state = state_data.copy()
    
    try:
        # 스냅샷 ID 생성
        timestamp = datetime.now().isoformat().replace(':', '-').replace('.', '-')
        snapshot_id = f"{snapshot_type}-{timestamp}"
        
        # P2: state_data가 이미 S3에 있으면 경로만 참조
        state_s3_path = state_data.get('state_s3_path')
        
        # 스냅샷 데이터 구성 (경량화)
        if state_s3_path:
            # 포인터 참조 모드 (P2 최적화)
            snapshot_data = {
                'snapshot_id': snapshot_id,
                'snapshot_type': snapshot_type,
                'execution_id': execution_id,
                'created_at': datetime.now().isoformat(),
                'state_s3_path': state_s3_path,  # 포인터만
                'segment_to_run': state_data.get('segment_to_run', 0),
                'loop_counter': state_data.get('loop_counter', 0),
                'idempotency_key': idempotency_key,
                'is_pointer_only': True
            }
        else:
            # 전체 복제 모드 (state_s3_path 없는 경우)
            # P2: 스냅샷 전에 최적화 적용
            optimized_state_data = state_data.copy()
            if optimized_state_data.get('state_history'):
                optimized_history, _ = optimize_state_history(
                    optimized_state_data['state_history'],
                    idempotency_key=idempotency_key,
                    max_entries=30
                )
                optimized_state_data['state_history'] = optimized_history
            
            snapshot_data = {
                'snapshot_id': snapshot_id,
                'snapshot_type': snapshot_type,
                'execution_id': execution_id,
                'created_at': datetime.now().isoformat(),
                'state_data': optimized_state_data,  # 최적화된 상태
                'segment_to_run': state_data.get('segment_to_run', 0),
                'loop_counter': state_data.get('loop_counter', 0),
                'is_pointer_only': False
            }
        
        # S3에 스냅샷 저장
        bucket = os.environ.get('STATE_BUCKET', 'analemma-state')
        snapshot_key = f"snapshots/{execution_id}/{snapshot_type}_{timestamp}.json"
        
        s3_client.put_object(
            Bucket=bucket,
            Key=snapshot_key,
            Body=json.dumps(snapshot_data, ensure_ascii=False, default=str),
            ContentType='application/json'
        )
        
        snapshot_s3_path = f"s3://{bucket}/{snapshot_key}"
        
        # 상태에 스냅샷 경로 추가
        if snapshot_type == 'pre':
            updated_state['pre_snapshot_s3_path'] = snapshot_s3_path
        else:
            updated_state['post_snapshot_s3_path'] = snapshot_s3_path
        
        logger.info(f"Created {snapshot_type}-snapshot: {snapshot_s3_path} (pointer_only={state_s3_path is not None})")
        
        return {
            'state_data': updated_state,
            'snapshot_s3_path': snapshot_s3_path,
            'snapshot_id': snapshot_id
        }
        
    except Exception as e:
        logger.error(f"Failed to create {snapshot_type}-snapshot: {e}")
        # 스냅샷 실패해도 워크플로는 계속 진행
        return {
            'state_data': updated_state,
            'snapshot_s3_path': None,
            'error': str(e)
        }


def lambda_handler(event, context):
    """
    Lambda handler for state data management
    
    v3.0 Smart StateBag: 모든 상태 변경의 중앙 집중 처리점
    
    Supported actions:
        - update_and_compress: 기존 압축/오프로드 (backward compatible)
        - sync: 실행 결과를 state_data에 머지
        - sync_branch: 브랜치 내 상태 동기화
        - aggregate_branches: 병렬 브랜치 결과 집계 (Fork-Join)
        - merge_callback: HITP 콜백 결과 머지
        - merge_async: 비동기 LLM 결과 머지
        - aggregate_distributed: MAP_REDUCE/BATCHED 결과 집계
        - create_snapshot: P1 상태 스냅샷 생성
        - decompress: 압축 데이터 해제
    """
    try:
        action = event.get('action', 'update_and_compress')
        logger.info(f"StateDataManager action: {action}")
        
        if action == 'update_and_compress':
            result = update_and_compress_state_data(event)
            return result
        
        elif action == 'sync':
            return sync_state_data(event)
        
        elif action == 'sync_branch':
            return sync_branch_state(event)
        
        elif action == 'aggregate_branches':
            return aggregate_branches(event)
        
        elif action == 'hydrate_branch':
            # 🌿 [Pointer Strategy] S3에서 브랜치 설정 Hydrate
            return hydrate_branch_config(event)
        
        elif action == 'merge_callback':
            return merge_callback_result(event)
        
        elif action == 'merge_async':
            return merge_async_result(event)
        
        elif action == 'aggregate_distributed':
            return aggregate_distributed_results(event)
        
        elif action == 'create_snapshot':
            return create_snapshot(event)
        
        elif action == 'decompress':
            compressed_data = event.get('compressed_data')
            if not compressed_data:
                raise ValueError("compressed_data is required for decompress action")
            return decompress_data(compressed_data)
        
        else:
            raise ValueError(f"Unknown action: {action}")
    
    except Exception as e:
        logger.error(f"Error in state_data_manager: {e}", exc_info=True)
        raise