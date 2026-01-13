import os
import json
import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Initialize Express Workflow by chunking large workflows into manageable pieces.
    
    This function splits extremely long workflows (>100 segments) into chunks
    to avoid Step Functions Event History limits (25,000 events).
    
    Args:
        event: Contains workflow input and chunking parameters
        context: Lambda context (unused)
        
    Returns:
        Dict containing chunks configuration for Express Workflow
    """
    logger.info("Initializing Express Workflow execution")
    
    # Extract input parameters
    workflow_input = event.get('input', {})
    chunk_size = event.get('chunk_size', 50)  # Default 50 segments per chunk
    max_chunks = event.get('max_chunks', 10)  # Maximum 10 chunks (500 segments total)
    
    # Extract workflow data
    workflow_config = workflow_input.get('workflow_config', {})
    partition_map = workflow_input.get('partition_map', [])
    total_segments = workflow_input.get('total_segments', len(partition_map))
    
    # Validate input
    if total_segments <= 100:
        raise ValueError(f"Workflow with {total_segments} segments does not require Express mode")
    
    if not partition_map:
        raise ValueError("partition_map is required for Express Workflow chunking")
    
    # Calculate optimal chunk configuration
    actual_chunk_size = min(chunk_size, max(10, total_segments // max_chunks))
    total_chunks = (total_segments + actual_chunk_size - 1) // actual_chunk_size
    
    logger.info(
        f"Chunking workflow: {total_segments} segments → {total_chunks} chunks "
        f"of ~{actual_chunk_size} segments each"
    )
    
    # Create chunks
    chunks = []
    for chunk_idx in range(total_chunks):
        start_segment = chunk_idx * actual_chunk_size
        end_segment = min(start_segment + actual_chunk_size, total_segments)
        
        # Extract partition map slice for this chunk
        chunk_partition_map = partition_map[start_segment:end_segment]
        
        # Determine initial state for this chunk
        if chunk_idx == 0:
            # First chunk uses original initial state
            initial_state = workflow_input.get('current_state')
            initial_state_s3_path = workflow_input.get('state_s3_path')
        else:
            # Subsequent chunks will receive state from src.previous chunk
            # This will be populated during execution
            initial_state = None
            initial_state_s3_path = None
        
        chunk_config = {
            "chunk_index": chunk_idx,
            "start_segment": start_segment,
            "end_segment": end_segment - 1,  # Inclusive end
            "segment_count": end_segment - start_segment,
            "partition_map": chunk_partition_map,
            "initial_state": initial_state,
            "initial_state_s3_path": initial_state_s3_path,
            "is_first_chunk": chunk_idx == 0,
            "is_last_chunk": chunk_idx == total_chunks - 1
        }
        
        chunks.append(chunk_config)
    
    # Prepare state data for chunks
    state_data = {
        "ownerId": workflow_input.get('ownerId'),
        "workflowId": workflow_input.get('workflowId'),
        "idempotency_key": workflow_input.get('idempotency_key'),
        "quota_reservation_id": workflow_input.get('quota_reservation_id'),
        "total_segments": total_segments,
        "original_partition_map": partition_map
    }
    
    # Validate chunk configuration
    total_chunk_segments = sum(chunk['segment_count'] for chunk in chunks)
    if total_chunk_segments != total_segments:
        logger.error(
            f"Chunk validation failed: {total_chunk_segments} != {total_segments}"
        )
        raise ValueError("Chunk segment count mismatch")
    
    logger.info(f"Successfully created {len(chunks)} chunks for Express Workflow")
    
    return {
        "chunks": chunks,
        "total_chunks": total_chunks,
        "chunk_size": actual_chunk_size,
        "workflow_config": workflow_config,
        "state_data": state_data,
        "chunking_metadata": {
            "original_segments": total_segments,
            "chunks_created": total_chunks,
            "avg_chunk_size": total_segments / total_chunks,
            "express_mode": True
        }
    }