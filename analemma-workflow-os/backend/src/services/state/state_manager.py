"""
StateManager - Pure S3 Client Wrapper

[v3.3] Simplified to pure S3 operations only.
No StateVersioningService delegation - use StateVersioningService directly for state management.

This class only handles:
- Raw S3 get/put operations
- PII masking before storage
- Size-based offloading logic
"""

import json
import logging
import time
import uuid
import boto3
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# PII masking utility
try:
    from src.common.security_utils import mask_pii_in_state as _mask_pii_in_state
    _HAS_SECURITY_UTILS = True
except ImportError:
    logger.warning("[StateManager] SecurityUtils not available, using legacy masking")
    _HAS_SECURITY_UTILS = False
    
    import re
    
    PII_REGEX_PATTERNS = {
        'email': (re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'), '[EMAIL_MASKED]'),
        'phone_kr': (re.compile(r'0\d{1,2}-\d{3,4}-\d{4}'), '[PHONE_MASKED]'),
    }
    
    def _mask_pii_in_state(state: Any) -> Any:
        """Legacy PII masking (fallback)"""
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
    [v3.3] Pure S3 Client Wrapper
    
    Simplified to handle only S3 operations. For state versioning and 
    manifest management, use StateVersioningService directly.
    """
    
    def __init__(self, s3_client=None):
        self.s3_client = s3_client or boto3.client("s3")

    def download_state_from_s3(self, s3_path: str) -> Dict[str, Any]:
        """
        Download and parse JSON from S3.
        
        Args:
            s3_path: S3 URI (s3://bucket/key)
            
        Returns:
            Parsed JSON dict
        """
        try:
            # Parse s3://bucket/key
            if not s3_path.startswith("s3://"):
                raise ValueError(f"Invalid S3 path: {s3_path}")
            
            parts = s3_path[5:].split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""
            
            logger.debug(f"[StateManager] Downloading from s3://{bucket}/{key}")
            
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read().decode("utf-8")
            
            return json.loads(content)
            
        except Exception as e:
            logger.error(f"Failed to download from {s3_path}: {e}")
            raise RuntimeError(f"S3 download failed: {e}")

    def upload_state_to_s3(self, bucket: str, prefix: str, state: Dict[str, Any], deterministic_filename: Optional[str] = None) -> str:
        """
        Upload JSON to S3.
        
        Args:
            bucket: S3 bucket name
            prefix: S3 key prefix
            state: Data to upload
            deterministic_filename: Optional fixed filename
            
        Returns:
            S3 URI (s3://bucket/key)
        """
        try:
            file_name = deterministic_filename or f"{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
            key = f"{prefix}/{file_name}"
            s3_path = f"s3://{bucket}/{key}"
            
            serialized = json.dumps(state, ensure_ascii=False).encode("utf-8")
            
            logger.debug(f"[StateManager] Uploading to {s3_path} ({len(serialized)} bytes)")
            
            self.s3_client.put_object(
                Bucket=bucket,
                Key=key,
                Body=serialized,
                ContentType="application/json"
            )
            
            return s3_path
            
        except Exception as e:
            logger.error(f"Failed to upload to s3://{bucket}/{prefix}: {e}")
            raise RuntimeError(f"S3 upload failed: {e}")


    def _upload_raw_bytes_to_s3(self, bucket: str, prefix: str, serialized_bytes: bytes, deterministic_filename: Optional[str] = None) -> str:
        """
        Upload pre-serialized bytes directly to S3.
        Eliminates double serialization overhead.
        """
        try:
            file_name = deterministic_filename or f"{int(time.time())}_{uuid.uuid4().hex[:8]}.json"
            key = f"{prefix}/{file_name}"
            s3_path = f"s3://{bucket}/{key}"
            
            logger.debug(f"[StateManager] Uploading raw bytes to {s3_path} ({len(serialized_bytes)} bytes)")
            
            self.s3_client.put_object(
                Bucket=bucket,
                Key=key,
                Body=serialized_bytes,
                ContentType="application/json"
            )
            
            return s3_path
            
        except Exception as e:
            logger.error(f"Failed to upload raw bytes to s3://{bucket}/{prefix}: {e}")
            raise RuntimeError(f"S3 upload failed: {e}")

    def handle_state_storage(self, state: Dict[str, Any], auth_user_id: str, workflow_id: str, segment_id: int, bucket: Optional[str], threshold: Optional[int] = None, loop_counter: Optional[int] = None) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Decide whether to store state inline or in S3 based on size threshold.
        PII data is masked before storage.
        
        Args:
            state: State data to store
            auth_user_id: User ID for S3 path
            workflow_id: Workflow ID for S3 path
            segment_id: Segment ID for S3 path
            bucket: S3 bucket (required if size exceeds threshold)
            threshold: Size threshold in bytes (default 180KB)
            loop_counter: Optional loop iteration counter
            
        Returns:
            (state_or_metadata, s3_path_or_none)
        """
        try:
            # Apply PII masking
            masked_state = _mask_pii_in_state(state)
            
            # Single serialization
            serialized_bytes = json.dumps(masked_state, ensure_ascii=False).encode("utf-8")
            state_size = len(serialized_bytes)
            
            # Default to 180KB (safe Step Functions limit with buffer)
            threshold = threshold or 180000
            SF_HARD_LIMIT = 180000
            
            if state_size > threshold:
                if not bucket:
                    logger.error(f"State size ({state_size} bytes) exceeds threshold but no S3 bucket provided!")
                    
                    if state_size > SF_HARD_LIMIT:
                        # Return truncated safe state
                        safe_state = {
                            "__state_truncated": True,
                            "__original_size_bytes": state_size,
                            "__original_size_kb": round(state_size / 1024, 2),
                            "__error": "PAYLOAD_TOO_LARGE_NO_S3_BUCKET",
                            "segment_id": segment_id,
                        }
                        
                        if isinstance(masked_state, dict):
                            for key in ['workflowId', 'ownerId', 'TEST_RESULT', 'VALIDATION_STATUS']:
                                if key in masked_state:
                                    safe_state[key] = masked_state[key]
                        
                        logger.warning(f"Returning truncated state ({len(json.dumps(safe_state))} bytes)")
                        return safe_state, None
                    else:
                        logger.warning(f"State size ({state_size}) exceeds threshold but below SF limit")
                        return masked_state, None

                if not auth_user_id:
                    raise PermissionError("Missing authenticated user id for S3 upload")
                
                # Construct S3 path
                if loop_counter is not None and isinstance(loop_counter, int) and loop_counter >= 0:
                    prefix = f"workflow-states/{auth_user_id}/{workflow_id}/segments/{segment_id}/{loop_counter}"
                else:
                    prefix = f"workflow-states/{auth_user_id}/{workflow_id}/segments/{segment_id}"
                
                # Upload to S3
                s3_path = self._upload_raw_bytes_to_s3(bucket, prefix, serialized_bytes, deterministic_filename="output.json")
                logger.info(f"State uploaded to S3: {s3_path} ({state_size} bytes, {state_size/1024:.1f}KB)")
                
                # Return S3 metadata
                s3_metadata = {
                    "__s3_offloaded": True,
                    "__s3_path": s3_path,
                    "__original_size_kb": round(state_size / 1024, 2)
                }
                return s3_metadata, s3_path
            else:
                logger.info(f"Returning state inline ({state_size} bytes)")
                return masked_state, None
                
        except Exception as e:
            logger.exception("Failed to handle state storage")
            raise RuntimeError(f"Failed to handle state storage: {e}")
