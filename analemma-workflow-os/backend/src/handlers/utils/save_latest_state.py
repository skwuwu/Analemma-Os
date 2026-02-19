"""
[Tiny Handler] Save Latest State

[v3.3] Direct StateVersioningService usage - wrapper removed
This handler is a thin wrapper for Lambda/Step Functions compatibility.
"""

import logging
import os
from typing import Dict, Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Save latest state for distributed workflow chunk.
    
    [v3.3] Direct StateVersioningService.save_state_delta() usage.
    
    Args:
        event: {
            "chunk_data": { "chunk_id": "chunk_0001", "segment_id": 99, ... },
            "execution_id": "exec-123",
            "owner_id": "user-456",
            "workflow_id": "wf-789",
            "state_bucket": "my-bucket",
            "final_state": {...}
        }
    
    Returns:
        {
            "saved": bool,
            "s3_path": str,
            "timestamp": int,
            "segment_id": int
        }
    """
    try:
        chunk_data = event.get('chunk_data', {})
        execution_id = event.get('execution_id')
        owner_id = event.get('owner_id')
        workflow_id = event.get('workflow_id')
        state_bucket = event.get('state_bucket') or os.environ.get('WORKFLOW_STATE_BUCKET')
        
        chunk_id = chunk_data.get('chunk_id', 'unknown')
        segment_id = chunk_data.get('segment_id') or chunk_data.get('latest_segment_id', 0)
        final_state = event.get('final_state', {})
        
        # 🛡️ [P0] 컨텍스트 추출 (total_segments) - 워크플로우 흐름 보존
        total_segments = event.get('total_segments')
        
        # 🛡️ [P0] 데이터 정화 (유령 'code' 타입 박멸)
        # 상태 데이터 내부에 오염된 노드 타입이 저장되지 않도록 방어
        if isinstance(final_state, dict):
            if 'partition_map' in final_state and isinstance(final_state['partition_map'], list):
                for seg in final_state['partition_map']:
                    if isinstance(seg, dict):
                        for node in seg.get('nodes', []):
                            if isinstance(node, dict) and node.get('type') == 'code':
                                logger.warning(f"🛡️ [SaveHandler] Fixing 'code' type to 'operator' in state for node {node.get('id')}")
                                node['type'] = 'operator'
        
        logger.info(f"[v3.3] SaveLatestState: chunk={chunk_id}, segment={segment_id}")
        
        # v3.3: Direct StateVersioningService usage
        from src.services.state.state_versioning_service import StateVersioningService
        
        kernel = StateVersioningService(
            dynamodb_table=os.environ.get('MANIFESTS_TABLE', 'StateManifestsV3'),
            s3_bucket=state_bucket or os.environ.get('WORKFLOW_STATE_BUCKET'),
            use_2pc=True
        )
        
        save_result = kernel.save_state_delta(
            delta=final_state,
            workflow_id=workflow_id,
            execution_id=execution_id,
            owner_id=owner_id,
            segment_id=segment_id,
            previous_manifest_id=final_state.get('current_manifest_id')
        )
        
        # v3.3: Convert to legacy format for Step Functions compatibility
        result = {
            "saved": True,
            "manifest_id": save_result.get('manifest_id'),
            "block_ids": save_result.get('block_ids', []),
            "segment_id": segment_id,
            "chunk_id": chunk_id,
            "committed": save_result.get('committed', False),
            "total_segments": int(total_segments) if total_segments is not None else 1
        }
            
        return result
        
    except Exception as e:
        logger.exception(f"SaveLatestState failed: {e}")
        return {
            "saved": False,
            "error": str(e),
            "phase": "handler_exception"
        }
