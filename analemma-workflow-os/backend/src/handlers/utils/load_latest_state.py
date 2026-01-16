"""
[Tiny Handler] Load Latest State

Delegates all logic to StatePersistenceService.
This handler is a thin wrapper for Lambda/Step Functions compatibility.

[v2.3] 개선사항:
1. 상태 로드 실패 시 Step Functions Choice State용 명확한 플래그 제공
2. 프라이빗 멤버 접근 대신 set_bucket() 메서드 사용
3. Cold Start 최적화 - Global Scope에서 서비스 초기화
"""

import logging
import os
from typing import Dict, Any

from src.services.state.state_persistence_service import get_state_persistence_service

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# =============================================================================
# [v2.3] Cold Start 최적화: Global Scope에서 서비스 인스턴스 초기화
# Lambda Warm Start 시 인스턴스를 재사용하여 초기화 오버헤드 제거
# =============================================================================
_service_instance = None


def _get_service():
    """Lazy singleton service initialization."""
    global _service_instance
    if _service_instance is None:
        _service_instance = get_state_persistence_service()
    return _service_instance


# =============================================================================
# 상태 로드 실패 유형 (Step Functions Choice State에서 분기 결정용)
# =============================================================================
class LoadFailureReason:
    """상태 로드 실패 사유 상수 (ASL Choice State 조건 매칭용)"""
    HANDLER_EXCEPTION = "handler_exception"      # 핸들러 레벨 예외
    SERVICE_ERROR = "service_error"              # 서비스 레벨 에러
    BUCKET_NOT_CONFIGURED = "bucket_not_configured"  # 버킷 미설정
    STATE_NOT_FOUND = "state_not_found"          # 상태 데이터 없음
    FIRST_CHUNK = "first_chunk"                  # 첫 번째 청크 (정상)


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Load latest state for distributed workflow chunk.
    
    Delegates to StatePersistenceService.load_state().
    
    [v2.3] Step Functions 분기 전략:
    - state_loaded: True → 정상 진행
    - state_loaded: False + reason: "first_chunk" → 정상 진행 (첫 청크)
    - state_loaded: False + is_critical_failure: True → Fail 상태로 전이 권장
    
    ASL Choice State 예시:
    ```json
    {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.is_critical_failure",
          "BooleanEquals": true,
          "Next": "HandleLoadFailure"
        }
      ],
      "Default": "ProcessChunk"
    }
    ```
    
    Args:
        event: {
            "chunk_data": { "chunk_id": "chunk_0001", "chunk_index": 1, ... },
            "execution_id": "exec-123",
            "owner_id": "user-456",
            "workflow_id": "wf-789",
            "state_bucket": "my-bucket"
        }
    
    Returns:
        {
            "previous_state": {...} or {},
            "latest_segment_id": int or null,
            "state_loaded": bool,
            "is_critical_failure": bool,  # [v2.3] Step Functions 분기용
            "reason": str,                 # [v2.3] 실패 사유
            "should_retry": bool           # [v2.3] 재시도 권장 여부
        }
    """
    try:
        chunk_data = event.get('chunk_data', {})
        execution_id = event.get('execution_id')
        owner_id = event.get('owner_id')
        workflow_id = event.get('workflow_id')
        state_bucket = event.get('state_bucket') or os.environ.get('WORKFLOW_STATE_BUCKET')
        
        chunk_index = chunk_data.get('chunk_index', 0)
        chunk_id = chunk_data.get('chunk_id', 'unknown')
        
        logger.info(f"LoadLatestState: chunk={chunk_id}, index={chunk_index}")
        
        # [v2.3] Global Scope의 싱글톤 서비스 사용 (Cold Start 최적화)
        service = _get_service()
        
        # [v2.3] 프라이빗 멤버 접근 대신 set_bucket() 메서드 사용
        if state_bucket:
            service.set_bucket(state_bucket)
        
        result = service.load_state(
            execution_id=execution_id,
            owner_id=owner_id,
            workflow_id=workflow_id,
            chunk_index=chunk_index,
            chunk_data=chunk_data
        )
        
        # [v2.3] Step Functions 분기 전략용 플래그 추가
        result = _enrich_result_with_branch_flags(result)
        
        return result
        
    except Exception as e:
        logger.exception(f"LoadLatestState failed: {e}")
        return {
            "previous_state": {},
            "latest_segment_id": None,
            "state_loaded": False,
            "error": str(e),
            "reason": LoadFailureReason.HANDLER_EXCEPTION,
            # [v2.3] Step Functions 분기용 플래그
            "is_critical_failure": True,  # 핸들러 예외는 치명적 실패
            "should_retry": True          # 일시적 오류일 수 있으므로 재시도 권장
        }


def _enrich_result_with_branch_flags(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    [v2.3] Step Functions Choice State 분기를 위한 플래그 추가.
    
    is_critical_failure 판단 기준:
    - True: 데이터 정합성 에러 발생 가능, Fail 상태 전이 또는 재시도 필요
    - False: 정상 진행 가능 (첫 청크이거나 상태 로드 성공)
    """
    state_loaded = result.get("state_loaded", False)
    reason = result.get("reason", "")
    
    # 첫 청크는 이전 상태가 없는 것이 정상
    if reason == "first_chunk":
        result["is_critical_failure"] = False
        result["should_retry"] = False
    elif state_loaded:
        result["is_critical_failure"] = False
        result["should_retry"] = False
    else:
        # 상태 로드 실패 - 치명적 실패로 간주
        result["is_critical_failure"] = True
        # 버킷 미설정은 재시도해도 해결 안됨
        result["should_retry"] = reason not in ("no_bucket_configured", "first_chunk")
    
    return result