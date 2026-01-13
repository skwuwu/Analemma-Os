"""
[Tiny Handler] Save Latest State

Delegates all logic to StatePersistenceService.
This handler is a thin wrapper for Lambda/Step Functions compatibility.
"""

import logging
import os
from typing import Dict, Any

from src.services.state.state_persistence_service import get_state_persistence_service

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Save latest state for distributed workflow chunk.
    
    Delegates to StatePersistenceService.save_state().
    
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
        
        logger.info(f"SaveLatestState: chunk={chunk_id}, segment={segment_id}")
        
        # Delegate to service
        service = get_state_persistence_service()
        
        # Override bucket if provided in event
        if state_bucket:
            service._state_bucket = state_bucket
        
        return service.save_state(
            execution_id=execution_id,
            owner_id=owner_id,
            workflow_id=workflow_id,
            chunk_id=chunk_id,
            segment_id=segment_id,
            state_data=final_state
        )
        
    except Exception as e:
        logger.exception(f"SaveLatestState failed: {e}")
        return {
            "saved": False,
            "error": str(e),
            "phase": "handler_exception"
        }
