"""
StateManager - Legacy State Management Utilities

⚠️ DEPRECATED: Phase E에서 StateVersioningService로 통합 중

현재 상태:
- ✅ PII 마스킹 → SecurityUtils로 분리 완료
- 🔄 S3 업로드/다운로드 → StateVersioningService로 통합 중
- ✅ Backward Compatibility 유지 (기존 코드 그대로 작동)

마이그레이션 가이드:
    # 기존 코드 (계속 작동)
    from src.services.state.state_manager import StateManager
    manager = StateManager()
    s3_path = manager.upload_state_to_s3(bucket, prefix, state)
    
    # 새 코드 (권장)
    from src.services.state.state_versioning_service import StateVersioningService
    versioning = StateVersioningService(...)
    s3_path = versioning.save_state(state, workflow_id, execution_id)
"""

import json
import logging
import os
import boto3
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ✅ Phase E: PII 마스킹은 SecurityUtils로 분리
try:
    from src.common.security_utils import mask_pii_in_state as _mask_pii_in_state
    _HAS_SECURITY_UTILS = True
except ImportError:
    logger.warning("[StateManager] SecurityUtils not available, using legacy masking")
    _HAS_SECURITY_UTILS = False
    
    # Fallback: 기존 마스킹 로직
    import re
    
    PII_REGEX_PATTERNS = {
        'email': (re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'), '[EMAIL_MASKED]'),
        'phone_kr': (re.compile(r'0\d{1,2}-\d{3,4}-\d{4}'), '[PHONE_MASKED]'),
    }
    
    def _mask_pii_in_state(state: Any) -> Any:
        """Legacy PII 마스킹 (fallback)"""
        if isinstance(state, str):
            for _, (pattern, replacement) in PII_REGEX_PATTERNS.items():
                state = pattern.sub(replacement, state)
            return state
        elif isinstance(state, dict):
            return {k: _mask_pii_in_state(v) for k, v in state.items()}
        elif isinstance(state, list):
            return [_mask_pii_in_state(item) for item in state]
        else:
            return state


class StateManager:
    """
    ✅ Phase E: Wrapper Class (Backward Compatibility)
    
    기존 코드와의 호환성을 위해 유지되는 래퍼 클래스입니다.
    실제 구현은 StateVersioningService와 SecurityUtils에 위임됩니다.
    
    ⚠️ DEPRECATED: 새 코드에서는 직접 StateVersioningService 사용 권장
    """
    
    def __init__(self, s3_client=None):
        self.s3_client = s3_client or boto3.client("s3")
        self._versioning_service = None  # Lazy initialization
    
    @property
    def versioning_service(self):
        """✅ Phase E: Lazy StateVersioningService 초기화"""
        if self._versioning_service is None:
            try:
                from src.services.state.state_versioning_service import StateVersioningService
                
                # 환경 변수에서 설정 읽기
                manifests_table = os.environ.get('MANIFESTS_TABLE', 'WorkflowManifests-v3-dev')
                state_bucket = os.environ.get('SKELETON_S3_BUCKET') or os.environ.get('WORKFLOW_STATE_BUCKET')
                
                self._versioning_service = StateVersioningService(
                    dynamodb_table=manifests_table,
                    s3_bucket=state_bucket
                )
                logger.info("[StateManager] ✅ StateVersioningService initialized (Lazy)")
            except Exception as e:
                logger.error(f"[StateManager] ❌ Failed to initialize StateVersioningService: {e}")
                raise
        
        return self._versioning_service

    def download_state_from_s3(self, s3_path: str) -> Dict[str, Any]:
        """
        ✅ Phase E: Wrapper → StateVersioningService.load_state()
        
        Download state JSON from S3.
        
        ⚠️ DEPRECATED: 새 코드에서는 StateVersioningService.load_state() 직접 사용
        """
        logger.debug("[StateManager] download_state_from_s3() wrapper called")
        return self.versioning_service.load_state(s3_path)

    def upload_state_to_s3(self, bucket: str, prefix: str, state: Dict[str, Any], deterministic_filename: Optional[str] = None) -> str:
        """
        ✅ Phase E: Wrapper → StateVersioningService.save_state()
        
        Upload state JSON to S3.
        
        ⚠️ DEPRECATED: 새 코드에서는 StateVersioningService.save_state() 직접 사용
        """
        logger.debug("[StateManager] upload_state_to_s3() wrapper called")
        
        # bucket과 prefix에서 workflow_id, execution_id 추출
        # prefix 형식: "workflows/{workflow_id}/executions/{execution_id}/segments/{segment_id}"
        try:
            parts = prefix.split('/')
            workflow_id = parts[1] if len(parts) > 1 else 'unknown'
            execution_id = parts[3] if len(parts) > 3 else 'unknown'
            segment_id = int(parts[5]) if len(parts) > 5 and parts[4] == 'segments' else None
        except:
            workflow_id = 'legacy'
            execution_id = 'unknown'
            segment_id = None
        
        return self.versioning_service.save_state(
            state=state,
            workflow_id=workflow_id,
            execution_id=execution_id,
            segment_id=segment_id,
            deterministic_filename=deterministic_filename
        )

    def _upload_raw_bytes_to_s3(self, bucket: str, prefix: str, serialized_bytes: bytes, deterministic_filename: Optional[str] = None) -> str:
        """
        [Perf Optimization] Upload pre-serialized bytes directly to S3.
        Eliminates double serialization overhead.
        """
        try:
            import time
            import uuid
            
            file_name = deterministic_filename if deterministic_filename else f"{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
            key = f"{prefix}/{file_name}"
            s3_path = f"s3://{bucket}/{key}"
            
            logger.info("⬆️ [Optimized] Uploading pre-serialized bytes to: %s (%d bytes)", s3_path, len(serialized_bytes))
            self.s3_client.put_object(
                Bucket=bucket,
                Key=key,
                Body=serialized_bytes,
                ContentType="application/json"
            )
            return s3_path
        except Exception as e:
            logger.error("❌ Failed to upload raw bytes to %s: %s", bucket, e)
            raise RuntimeError(f"Failed to upload raw bytes to S3: {e}")

    def handle_state_storage(self, state: Dict[str, Any], auth_user_id: str, workflow_id: str, segment_id: int, bucket: Optional[str], threshold: Optional[int] = None, loop_counter: Optional[int] = None) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        ✅ Phase E: PII 마스킹은 SecurityUtils 사용
        
        Decide whether to store state inline or in S3 based on size threshold.
        PII data is masked before storage to ensure privacy compliance.
        
        ⚠️ 변경 사항:
        - PII 마스킹: SecurityUtils.mask_pii_in_state() 사용
        - 기존 로직 유지 (Backward Compatibility)
        """
        try:
            # ✅ Phase E: SecurityUtils로 PII 마스킹
            masked_state = _mask_pii_in_state(state)
            logger.debug("🔒 PII masking applied to state before storage")
            
            # [Perf Optimization] Single Serialization - 직렬화 한 번만 수행
            serialized_bytes = json.dumps(masked_state, ensure_ascii=False).encode("utf-8")
            state_size = len(serialized_bytes)
            
            # [Critical Fix] Step Functions hard limit with safety buffer
            # 256KB = 262,144 bytes, but AWS wrapper adds ~10-15KB overhead
            # Using 180KB (180,000 bytes) for safe margin
            SF_HARD_LIMIT = 180000  # ~175KB safe threshold
            
            # [Fix] Handle None threshold - default to 180KB (safe Step Functions limit)
            if threshold is None:
                threshold = 180000
                logger.warning("⚠️ threshold parameter was None, using default 180KB (safe SF limit)")
            
            if state_size > threshold:
                if not bucket:
                    logger.error("🚨 CRITICAL: State size (%d bytes, %.1fKB) exceeds threshold (%d) but no S3 bucket provided!", 
                                state_size, state_size/1024, threshold)
                    
                    # [Critical Fix] Instead of returning the full state (which causes SF failure),
                    # return a truncated state with error information
                    if state_size > SF_HARD_LIMIT:
                        logger.error("🚨 State exceeds Step Functions safe limit (180KB)! Creating safe fallback state.")
                        
                        # Create a minimal safe state that won't exceed limits
                        safe_state = {
                            "__state_truncated": True,
                            "__original_size_bytes": state_size,
                            "__original_size_kb": round(state_size / 1024, 2),
                            "__truncation_reason": "State exceeded 180KB Step Functions safe limit but no S3 bucket available",
                            "__error": "PAYLOAD_TOO_LARGE_NO_S3_BUCKET",
                            # Preserve essential metadata if present
                            "workflowId": masked_state.get("workflowId") if isinstance(masked_state, dict) else None,
                            "ownerId": masked_state.get("ownerId") if isinstance(masked_state, dict) else None,
                            "segment_id": segment_id,
                        }
                        
                        # Try to preserve test result if this is a test workflow
                        if isinstance(masked_state, dict):
                            for key in ['TEST_RESULT', 'VALIDATION_STATUS', '__kernel_actions']:
                                if key in masked_state:
                                    safe_state[key] = masked_state[key]
                        
                        logger.warning("⚠️ Returning truncated safe state (%d bytes) instead of full state (%d bytes)", 
                                      len(json.dumps(safe_state)), state_size)
                        return safe_state, None
                    else:
                        # State is below SF limit but above our threshold - return with warning
                        logger.warning("⚠️ State size (%d) exceeds threshold but below SF safe limit. Returning inline (risky).", state_size)
                        return masked_state, None

                if not auth_user_id:
                    raise PermissionError("Missing authenticated user id for S3 upload")
                
                # [v3.10] Loop-Safe Path Construction
                if loop_counter is not None and isinstance(loop_counter, int) and loop_counter >= 0:
                    # e.g. .../segments/10/5/output.json (Loop #5)
                    prefix = f"workflow-states/{auth_user_id}/{workflow_id}/segments/{segment_id}/{loop_counter}"
                else:
                    prefix = f"workflow-states/{auth_user_id}/{workflow_id}/segments/{segment_id}"
                
                # [Perf Optimization] 이미 직렬화된 바이트를 직접 S3에 업로드 (중복 직렬화 제거)
                s3_path = self._upload_raw_bytes_to_s3(bucket, prefix, serialized_bytes, deterministic_filename="output.json")
                logger.info("📦 State uploaded to S3: %s (%d bytes, %.1fKB)", s3_path, state_size, state_size/1024)
                
                # [Critical Fix] Return S3 metadata instead of None to prevent AttributeError
                # downstream when calling .get() on the result
                s3_metadata = {
                    "__s3_offloaded": True,
                    "__s3_path": s3_path,
                    "__original_size_kb": round(state_size / 1024, 2)
                }
                return s3_metadata, s3_path
            else:
                logger.info("📦 Returning state inline (%d bytes <= %d threshold)", state_size, threshold)
                return masked_state, None
        except Exception as e:
            logger.exception("Failed to handle state storage")
            raise RuntimeError(f"Failed to handle state storage: {e}")
