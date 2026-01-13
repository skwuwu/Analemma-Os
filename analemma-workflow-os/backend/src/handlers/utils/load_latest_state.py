"""
[Tiny Handler] Load Latest State

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
    Load latest state for distributed workflow chunk.
    
    Delegates to StatePersistenceService.load_state().
    
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
            "state_loaded": bool
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
        
        # Delegate to service
        service = get_state_persistence_service()
        
        # Override bucket if provided in event
        if state_bucket:
            service._state_bucket = state_bucket
        
        return service.load_state(
            execution_id=execution_id,
            owner_id=owner_id,
            workflow_id=workflow_id,
            chunk_index=chunk_index,
            chunk_data=chunk_data
        )
        
    except Exception as e:
        logger.exception(f"LoadLatestState failed: {e}")
        return {
            "previous_state": {},
            "latest_segment_id": None,
            "state_loaded": False,
            "error": str(e),
            "reason": "handler_exception"
        }