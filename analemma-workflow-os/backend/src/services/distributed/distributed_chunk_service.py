
import logging
import time
import os
import os
from typing import Dict, List, Any, Optional
import boto3
import json

from src.services.infrastructure.partition_cache_service import PartitionCacheService

logger = logging.getLogger(__name__)

class DistributedChunkService:
    """
    Domain Service for processing Distributed Map chunks.
    Handles:
    - Chunk data validation & normalization
    - Segment execution loop
    - Result aggregation
    - HITL interruption handling
    """
    
    def __init__(self, partition_cache_service: PartitionCacheService):
        self.cache_service = partition_cache_service

    def process_chunk(
        self, 
        chunk_data: Dict[str, Any], 
        context: Any,
        segment_runner_callable: callable, # Function to run a single segment (dependency injection)
        token_storage_callable: callable = None # Function to store task tokens
    ) -> Dict[str, Any]:
        """
        Main orchestration logic for a chunk.
        """
        # 1. Validate
        validation = self._validate_chunk_data(chunk_data)
        if not validation['is_valid']:
            return self._build_response(
                chunk_id=chunk_data.get('chunk_id', 'unknown'),
                status="FAILED",
                error=f"Invalid chunk data: {'; '.join(validation['errors'])}"
            )
        
        chunk_data = validation['normalized_data']
        chunk_id = chunk_data.get('chunk_id')
        start_segment = chunk_data.get('start_segment', 0)
        chunk_index = chunk_data.get('chunk_index', 0)
        total_chunks = chunk_data.get('total_chunks', 1)
        partition_slice = chunk_data.get('partition_slice', [])

        execution_start_time = time.time()
        
        # 2. Resolve Partition Data (Inline or S3)
        if not partition_slice and 'partition_map_s3_path' in chunk_data:
            # Load from S3 via Cache Service
            # We strictly need the slice for this chunk. 
            # If start/end are defined, we might need full load or streaming.
            # For simplicity in this service, if slice is missing, we try to load the relevant part.
            pass # TODO: Logic handled by caller (handler) or here? 
            # Recommendation: Handler passes populated partition_slice OR 
            # if handler passed s3_path in event, we load it here.
            # Let's assume the handler prepares the data source, 
            # BUT if we want true "service" logic, we should be able to fetch it.
            # Let's rely on the arguments passed. 
            # The handler refactoring plan said: "encapsulate S3 I/O". 
            
            # Let's allow fetching if slice is missing
            s3_path = chunk_data.get('partition_map_s3_path')
            if s3_path:
                 # We need to know WHICH segments. 
                 # If start/end is not in chunk_data, we might need full map to calc it?
                 # Assume start/end is provided or we load full map.
                 logger.info(f"Loading partition slice from {s3_path}")
                 load_result = self.cache_service.load_partition_map(s3_path)
                 full_map = load_result['partition_map']
                 
                 end_segment = chunk_data.get('end_segment', start_segment + len(full_map) - 1)
                 # Slice it
                 # Careful with indices.
                 # If chunking was dynamic, start_segment is reliable.
                 # If full map loaded, we slice.
                 idx_start = start_segment
                 # If start_segment is global index, and map is global map.
                 if idx_start < len(full_map):
                     idx_end = chunk_data.get('end_segment', idx_start) # Inclusive
                     partition_slice = full_map[idx_start : idx_end + 1]
                     chunk_data['partition_slice'] = partition_slice
        
        if not partition_slice:
             return self._build_response(
                chunk_id=chunk_id,
                status="COMPLETED",
                processed_segments=0
            )

        # 3. Execution Loop
        chunk_results = []
        # Current state management should be handled by the runner or passed in?
        # The 'context' contains 'previous_state' which is passed to the loop.
        current_state = context.get('current_state', {})
        owner_id = context.get('owner_id')
        workflow_id = context.get('workflow_id')
        
        for i, segment in enumerate(partition_slice):
            global_segment_idx = start_segment + i
            
            # Prepare Segment Event
            segment_event = {
                'segment_config': segment,
                'current_state': current_state,
                'ownerId': owner_id,
                'workflowId': workflow_id,
                'segment_to_run': global_segment_idx,
                'workflow_config': context.get('workflow_config', {}),
                'partition_map': partition_slice, # Passing slice as map context
                'max_concurrency': context.get('max_concurrency'),
                'execution_id': f"{chunk_id}_segment_{global_segment_idx}",
                'conversation_id': f"{chunk_id}_segment_{global_segment_idx}",
                'distributed_context': {
                    'chunk_id': chunk_id,
                    'chunk_index': chunk_index,
                    'total_chunks': total_chunks,
                    'is_distributed_execution': True
                }
            }
            
            try:
                # RUN SEGMENT
                result = segment_runner_callable(segment_event)
                
                status = result.get('status')
                
                if status in ['COMPLETE', 'SUCCEEDED']:  # [FIX] Accept both values for compatibility
                    if result.get('final_state'):
                        current_state = result['final_state']
                    chunk_results.append({
                        'segment_index': global_segment_idx,
                        'status': 'COMPLETED',
                        'result': result
                    })
                    
                elif status in ['PAUSE', 'PAUSED_FOR_HITP']:
                    # HITL Handling
                    task_token = result.get('task_token')
                    if task_token and token_storage_callable:
                        token_storage_callable(
                             chunk_id=chunk_id,
                             segment_id=global_segment_idx,
                             task_token=task_token,
                             owner_id=owner_id,
                             workflow_id=workflow_id,
                             execution_id=context.get('execution_id')
                        )
                    
                    return self._build_response(
                        chunk_id=chunk_id,
                        status="PAUSED_FOR_HITP",
                        processed_segments=len(chunk_results),
                        paused_segment_id=global_segment_idx,
                        final_state=current_state,
                        chunk_results=chunk_results,
                        execution_time=time.time() - execution_start_time
                    )
                
                elif status == 'PARALLEL_GROUP':
                     chunk_results.append({'segment_index': global_segment_idx, 'status': 'PARALLEL_GROUP', 'result': result})
                     # Parallel groups might finish immediately or branch out. 
                     # Original logic had `break`? 
                     # "elif segment_result.get('status') == 'PARALLEL_GROUP': break"
                     # Replicating original logic:
                     break
                
                else:
                    chunk_results.append({'segment_index': global_segment_idx, 'status': status, 'result': result})
            
            except Exception as e:
                logger.error(f"Segment {global_segment_idx} failed: {e}")
                chunk_results.append({'segment_index': global_segment_idx, 'status': 'FAILED', 'error': str(e)})
                if os.environ.get('DISTRIBUTED_FAIL_FAST', 'true').lower() == 'true':
                    break

        # 4. Final Aggregation
        failed_count = len([r for r in chunk_results if r.get('status') == 'FAILED'])
        success_count = len([r for r in chunk_results if r.get('status') == 'COMPLETED'])
        
        final_status = "COMPLETED"
        if failed_count > 0:
             final_status = "FAILED" if success_count == 0 else "PARTIAL_FAILURE"
             
        if chunk_results:
             last_completed = [r for r in chunk_results if r.get('status') == 'COMPLETED']
             if last_completed:
                 current_state['__latest_segment_id'] = last_completed[-1]['segment_index']

        # 🚨 [Distributed Map Optimization] S3 Offloading
        # Prevent "Payload Size Exceeded" by offloading huge chunk results
        state_bucket = context.get('state_bucket')
        s3_prefix = f"distributed-results/{owner_id}/{workflow_id}/{context.get('execution_id')}"
        
        final_state_ref = current_state
        chunk_results_ref = chunk_results
        
        # Check size & Offload if needed (Threshold: 32KB to be safe)
        if state_bucket:
            s3_client = boto3.client('s3')
            
            # 1. Offload Chunk Results
            results_json = json.dumps(chunk_results, default=str)
            if len(results_json.encode('utf-8')) > 32 * 1024:
                results_key = f"{s3_prefix}/chunk_{chunk_id}_results.json"
                try:
                    s3_client.put_object(
                        Bucket=state_bucket,
                        Key=results_key,
                        Body=results_json,
                        ContentType='application/json'
                    )
                    chunk_results_ref = None # Remove inline data
                    logger.info(f"Offloaded chunk results to s3://{state_bucket}/{results_key}")
                except Exception as e:
                    logger.error(f"Failed to offload chunk results: {e}")

            # 2. Offload Final State
            state_json = json.dumps(current_state, default=str)
            if len(state_json.encode('utf-8')) > 32 * 1024:
                state_key = f"{s3_prefix}/chunk_{chunk_id}_final_state.json"
                try:
                    s3_client.put_object(
                        Bucket=state_bucket,
                        Key=state_key,
                        Body=state_json,
                        ContentType='application/json'
                    )
                    final_state_ref = None # Remove inline data
                    # Pointer is sufficient? No, we need explicit path field.
                    # _build_response handles generic kwargs, so we'll pass s3 path there.
                    logger.info(f"Offloaded final state to s3://{state_bucket}/{state_key}")
                except Exception as e:
                    logger.error(f"Failed to offload final state: {e}")

        response = self._build_response(
            chunk_id=chunk_id,
            status=final_status,
            processed_segments=success_count,
            failed_segments=failed_count,
            final_state=final_state_ref,
            chunk_results=chunk_results_ref,
            execution_time=time.time() - execution_start_time,
            execution_summary={
                'total_segments': len(partition_slice),
                'successful_segments': success_count,
                'failed_segments': failed_count,
                'chunk_index': chunk_index
            }
        )
        
        # Inject S3 paths if offloaded
        if state_bucket:
            if final_state_ref is None:
                response['final_state_s3_path'] = f"s3://{state_bucket}/{s3_prefix}/chunk_{chunk_id}_final_state.json"
            if chunk_results_ref is None:
                response['chunk_results_s3_path'] = f"s3://{state_bucket}/{s3_prefix}/chunk_{chunk_id}_results.json"
                
        return response

    def _validate_chunk_data(self, chunk_data: Dict) -> Dict:
        """Mirroring original validation logic."""
        validation = {'is_valid': True, 'errors': [], 'normalized_data': chunk_data.copy() if isinstance(chunk_data, dict) else {}}
        if not isinstance(chunk_data, dict):
            return {'is_valid': False, 'errors': ['Not a dict']}
            
        required = ['chunk_id', 'chunk_index']
        for r in required:
            if r not in chunk_data:
                validation['is_valid'] = False
                validation['errors'].append(f"Missing {r}")
                
        return validation

    def _build_response(self, chunk_id, status, **kwargs):
        """Standard response builder."""
        res = {
            "chunk_id": chunk_id,
            "status": status,
            **kwargs
        }
        return res
