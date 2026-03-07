"""
State Data Manager Lambda Function
Lambda function responsible for payload size management and S3 offloading

v3.3 - Unified Pipe: From Birth to Death in a Single Pipe
    
    Data Lifecycle (Unified Pipe):
        - Birth (Init): {} → Universal Sync → StateBag v0
        - Growth (Sync): StateBag vN + Result → Universal Sync → StateBag vN+1
        - Collaboration (Aggregate): StateBag vN + Branches → Universal Sync → StateBag vFinal
    
    - All action functions are converted to "3-line wrappers"
    - Actual logic is handled by the universal_sync_core single engine
    - Performance optimized with Copy-on-Write + Shallow Merge
    - StateHydrator for S3 recovery with retry + checksum verification
    - P0~P2 issues auto-resolved (large data goes to S3 regardless of entry path)
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
    # Fallback when relative import fails in Lambda environment
    from universal_sync_core import universal_sync_core, get_default_hydrator

# Direct Logger creation (avoiding lazy import)
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

# Environment variables (STATE_STORAGE_BUCKET → WORKFLOW_STATE_BUCKET → SKELETON_S3_BUCKET fallback)
S3_BUCKET = (
    os.environ.get('STATE_STORAGE_BUCKET') or 
    os.environ.get('WORKFLOW_STATE_BUCKET') or 
    os.environ.get('SKELETON_S3_BUCKET')
)
if not S3_BUCKET:
    logger.warning("[WARN] No S3 bucket env var set (STATE_STORAGE_BUCKET/WORKFLOW_STATE_BUCKET/SKELETON_S3_BUCKET). S3 offloading disabled.")

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


def quick_size_check(data: Any) -> int:
    """
    🚀 [Perf] Approximate size measurement without JSON serialization (Bytes)
    
    Prevents O(N^2) problem: Calculate approximation first instead of calling json.dumps() every time.
    Perform precise calculation only when threshold is expected to be exceeded.
    
    Returns:
        Approximate size (KB)
    """
    if data is None:
        return 0
    if isinstance(data, (str, bytes)):
        return len(data) // 1024
    if isinstance(data, (int, float, bool)):
        return 0  # Negligible size
    if isinstance(data, (list, dict)):
        # String conversion approximation (not precise)
        try:
            return len(str(data)) // 1024
        except Exception as e:
            logger.warning("Failed to estimate payload size: %s", e)
            return 0
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
    📥 [P0 + P1] Load data from S3 (with retry + checksum verification)
    
    Used to load actual data in pointer-based architecture.
    
    v3.1 Improvements:
        - Exponential Backoff retry (max 3 attempts)
        - Checksum verification (data integrity guarantee)
        - Kernel integrity: If data is corrupted, raise error and retry
    
    Args:
        s3_path: S3 path (s3://bucket/key)
        expected_checksum: Expected MD5 hash (optional)
        max_retries: Maximum retry attempts
    
    Returns:
        Loaded data or None (on failure)
    """
    if not s3_path or not s3_path.startswith('s3://'):
        return None
    
    last_error = None
    base_delay = 0.5  # Initial delay
    
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
                delay = min(base_delay * (2 ** attempt), 8.0)  # Max 8 seconds
                logger.warning(f"Retry {attempt+1}/{max_retries} for {s3_path} after {delay:.2f}s: {e}")
                time.sleep(delay)
            else:
                logger.error(f"Failed to load from S3 {s3_path} after {max_retries} attempts: {last_error}")
    
    return None


# ============================================
# [P0] Duplicate Log Prevention Helper
# ============================================
def deduplicate_history_logs(existing_logs: List[Dict], new_logs: List[Dict]) -> List[Dict]:
    """
    🛡️ [P0] History log deduplication
    
    Duplicate logs can occur due to Lambda retries, etc.
    Filter duplicates based on log unique ID (node_id + timestamp).
    
    Args:
        existing_logs: Existing history logs
        new_logs: New logs to add
    
    Returns:
        Deduplicated merged log list (sorted by time)
    """
    if not new_logs:
        return existing_logs
    
    # Unique key generation function
    def get_log_key(log: Dict) -> str:
        """Generate unique key based on node_id + timestamp"""
        if not isinstance(log, dict):
            return str(hash(str(log)))
        
        node_id = log.get('node_id', log.get('id', ''))
        timestamp = log.get('timestamp', log.get('created_at', ''))
        
        # Combine if both exist, otherwise use individual field
        if node_id and timestamp:
            return f"{node_id}:{timestamp}"
        elif node_id:
            return f"node:{node_id}"
        elif timestamp:
            return f"ts:{timestamp}"
        else:
            # If neither exists, hash entire content
            return str(hash(json.dumps(log, sort_keys=True, default=str)))
    
    # Build key set from existing logs
    seen_keys = set()
    for log in existing_logs:
        seen_keys.add(get_log_key(log))
    
    # Add only non-duplicate new logs
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
# [Optimization] Reduce S3 Deserialization Cost
# ============================================
# Frequently used control fields - excluded from offloading
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

# Lambda internal cache (maintained during Cold Start)
_s3_cache: Dict[str, Any] = {}
_cache_timestamps: Dict[str, float] = {}
_cache_sizes: Dict[str, int] = {}  # 🛡️ [OOM Guard] Track size of each item
CACHE_TTL_SECONDS = 300  # 5 minutes
CACHE_MAX_TOTAL_MB = 50  # 🛡️ [OOM Guard] Total cache capacity limit
CACHE_MAX_ITEM_MB = 5    # 🛡️ [OOM Guard] Individual item size limit


def cached_load_from_s3(s3_path: str) -> Any:
    """
    📥 [Optimized] S3 load with TTL-based cache
    
    Reduces network cost when repeatedly requesting the same S3 path.
    Cache maintained during Lambda cold start (5-minute TTL)
    
    🛡️ [OOM Guard] Total cache capacity 50MB, individual 5MB limit
    """
    import time
    
    if not s3_path:
        return None
    
    current_time = time.time()
    
    # Check cache
    if s3_path in _s3_cache:
        cache_time = _cache_timestamps.get(s3_path, 0)
        if current_time - cache_time < CACHE_TTL_SECONDS:
            logger.debug(f"Cache hit for {s3_path}")
            return _s3_cache[s3_path]
        else:
            # TTL expired - remove cache
            del _s3_cache[s3_path]
            del _cache_timestamps[s3_path]
            if s3_path in _cache_sizes:
                del _cache_sizes[s3_path]
    
    # S3에서 로드
    data = load_from_s3(s3_path)
    
    if data is not None:
        # 🛡️ [OOM Guard] 크기 체크
        try:
            data_size_mb = len(json.dumps(data, default=str).encode('utf-8')) / (1024 * 1024)
        except Exception as e:
            logger.warning("Failed to calculate S3 cache item size: %s", e)
            data_size_mb = 0
        
        # 개별 항목 크기 제한
        if data_size_mb > CACHE_MAX_ITEM_MB:
            logger.warning(f"[OOM Guard] Skipping cache for {s3_path}: {data_size_mb:.2f}MB > {CACHE_MAX_ITEM_MB}MB limit")
            return data
        
        # Total cache capacity calculation
        total_cache_mb = sum(_cache_sizes.values()) / (1024 * 1024)
        
        # Remove oldest item when capacity exceeded
        while total_cache_mb + data_size_mb > CACHE_MAX_TOTAL_MB and _s3_cache:
            oldest_key = min(_cache_timestamps, key=_cache_timestamps.get)
            removed_size = _cache_sizes.get(oldest_key, 0)
            del _s3_cache[oldest_key]
            del _cache_timestamps[oldest_key]
            if oldest_key in _cache_sizes:
                del _cache_sizes[oldest_key]
            total_cache_mb -= removed_size / (1024 * 1024)
            logger.debug(f"[OOM Guard] Evicted {oldest_key} to free cache space")
        
        # Store in cache (max 20 items)
        if len(_s3_cache) >= 20:
            oldest_key = min(_cache_timestamps, key=_cache_timestamps.get)
            del _s3_cache[oldest_key]
            del _cache_timestamps[oldest_key]
            if oldest_key in _cache_sizes:
                del _cache_sizes[oldest_key]
        
        _s3_cache[s3_path] = data
        _cache_timestamps[s3_path] = current_time
        _cache_sizes[s3_path] = int(data_size_mb * 1024 * 1024)
    
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
    """
    Optimize current state by moving large fields to S3
    
    🚀 [v3.3 Perf] Prevent O(N^2): Calculate approximation first with quick_size_check
    🛡️ [v3.3 Guard] Prevent reprocessing of already offloaded pointers
    """
    if not current_state:
        return current_state, False
    
    # 🛡️ [P0 Critical] Calculate total state size first
    try:
        total_size_kb = calculate_payload_size(current_state)
    except Exception as e:
        logger.warning("Failed to calculate total state size: %s", e)
        total_size_kb = 0
        
    optimized_state = current_state.copy()
    s3_offloaded = False
    
    # 🚀 [Perf] Skip optimization if total size is small
    if total_size_kb < 30:
        return optimized_state, False
    
    # Strategy 1: Individual Field Offloading
    # Iterate over ALL fields, not just a hardcoded list
    for field, field_data in list(optimized_state.items()):
        # Skip small primitive types to save calculation time
        if field_data is None or isinstance(field_data, (bool, int, float)):
            continue
            
        # 🛡️ [v3.3] Skip already offloaded fields (pointer reprocessing prevention)
        if isinstance(field_data, dict):
            # Check __s3_offloaded flag
            if field_data.get('__s3_offloaded'):
                continue
            # Check s3_reference/history_archive type (prevent pointer re-offloading)
            if field_data.get('type') in ('s3_reference', 'history_archive', 'compressed', 'error_truncated'):
                continue
        
        # 🚀 [Perf] Calculate approximation first with quick_size_check (avoid JSON serialization)
        approx_size_kb = quick_size_check(field_data)
        
        # Skip if approximation is less than 20KB (safety margin)
        if approx_size_kb < 20:
            continue
        
        # Precise calculation only when approximation is near threshold
        try:
            field_size = calculate_payload_size({field: field_data})
        except Exception as e:
            logger.warning("Failed to calculate field size for '%s': %s", field, e)
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
    """
    Main function to update and compress state data
    
    🛡️ [v3.3] Legacy compatibility wrapper - delegates to USC
    
    Problem: Previous versions hard-coded field reconstruction
    - Risk of field omission when adding new fields (Logic Drift)
    - Violates "Unified Pipe" principle
    
    Solution: Delegate to universal_sync_core to maintain single pipe
    """
    state_data = event.get('state_data', {})
    execution_result = event.get('execution_result', {})
    
    # 🎯 Delegate to USC - Single pipe principle
    result = universal_sync_core(
        base_state=state_data,
        new_result={'execution_result': execution_result},
        context={'action': 'sync'}
    )
    
    updated_state_data = result.get('state_data', {})
    
    # Legacy compatibility: Send CloudWatch metrics
    initial_size_kb = calculate_payload_size(state_data)
    final_size_kb = updated_state_data.get('payload_size_kb', calculate_payload_size(updated_state_data))
    
    _send_cloudwatch_metrics(
        initial_size_kb=initial_size_kb,
        final_size_kb=final_size_kb,
        compression_applied=updated_state_data.get('compression_applied', False),
        s3_offloaded=updated_state_data.get('s3_offloaded', False),
        idempotency_key=state_data.get('idempotency_key', 'unknown')
    )
    
    return updated_state_data


def _send_cloudwatch_metrics(
    initial_size_kb: int,
    final_size_kb: int,
    compression_applied: bool,
    s3_offloaded: bool,
    idempotency_key: str
) -> None:
    """Send CloudWatch metrics"""
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
    🔄 [v3.2 Wrapper] Centralized state synchronization
    
    Merges all Lambda execution results into state_data and performs S3 offloading.
    Called from SyncStateData state in ASL.
    
    v3.2: 3-line wrapper - actual logic handled by universal_sync_core
    
    Returns:
        {"state_data": {...}, "next_action": "CONTINUE" | "COMPLETE" | ...}
    """
    # v3.2: 3-line wrapper pattern
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
        # 🔧 [Fix] Match ASL JSONPath structure: $.state_data.bag.error_type
        error_bag = {
            'status': 'FAILED',
            'error_type': 'MapStateError',
            'error_message': str(map_error),
            'is_retryable': False
        }
        return {
            'state_data': {'bag': error_bag},
            'next_action': 'FAILED'
        }
    
    # P0: 포인터 기반 vs 전체 결과 입력 정규화
    if load_from_s3_flag:
        branch_pointers = event.get('branch_pointers', [])
        # 포인터 모드: S3에서 로그 로드
        all_logs = []
        soft_fail_branches = []  # LOOP_LIMIT_EXCEEDED / PARTIAL_FAILURE 추적

        for pointer in branch_pointers:
            status = pointer.get('status')

            if status == 'COMPLETE':
                s3_path = pointer.get('s3_path') or pointer.get('final_state_s3_path')
                if s3_path:
                    branch_data = cached_load_from_s3(s3_path)
                    if branch_data:
                        all_logs.extend(branch_data.get('new_history_logs', []))

            elif status == 'LOOP_LIMIT_EXCEEDED':
                # [Soft-fail] 브랜치가 루프 한계 초과로 graceful 종료됨
                # final_state_s3_path에서 마지막 정상 상태 로그 로드 시도
                branch_id = pointer.get('branch_id', 'unknown')
                error_info = pointer.get('error_info', {})
                logger.warning(
                    f"[aggregate_branches] Soft-fail: branch={branch_id} "
                    f"status=LOOP_LIMIT_EXCEEDED cause={error_info.get('Cause', 'unknown')}"
                )
                s3_path = pointer.get('s3_path') or pointer.get('final_state_s3_path')
                if s3_path:
                    branch_data = cached_load_from_s3(s3_path)
                    if branch_data:
                        all_logs.extend(branch_data.get('new_history_logs', []))
                soft_fail_branches.append({
                    'branch_id': branch_id,
                    'reason': 'LOOP_LIMIT_EXCEEDED',
                    'error_info': error_info,
                    'final_state_s3_path': s3_path,
                })

            elif status == 'PARTIAL_FAILURE':
                # [Soft-fail] 브랜치 내부 오류로 실패했지만 Map 전체는 계속 진행됨
                branch_id = pointer.get('branch_id', 'unknown')
                error_info = pointer.get('error_info', {})
                logger.warning(
                    f"[aggregate_branches] Soft-fail: branch={branch_id} "
                    f"status=PARTIAL_FAILURE error={error_info}"
                )
                soft_fail_branches.append({
                    'branch_id': branch_id,
                    'reason': 'PARTIAL_FAILURE',
                    'error_info': error_info,
                    'final_state_s3_path': pointer.get('final_state_s3_path'),
                })

            else:
                # 알 수 없는 상태 — 경고만 기록
                logger.warning(
                    f"[aggregate_branches] Unknown branch status: "
                    f"branch_id={pointer.get('branch_id', 'unknown')} status={status}"
                )

        if soft_fail_branches:
            logger.error(
                f"[aggregate_branches] {len(soft_fail_branches)} branch(es) ended as soft-fail: "
                f"{[b['branch_id'] for b in soft_fail_branches]}"
            )

        normalized_result = {
            'parallel_results': branch_pointers,
            'new_history_logs': all_logs,
            '_soft_fail_branches': soft_fail_branches,  # universal_sync_core에 전달
        }
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
        'next_action': result['next_action'],  # [M-2/L-1 Fix] USC의 실제 결정 위임 (PAUSED_FOR_HITP 등 지원)
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
    v3.3: 🛡️ S3_BUCKET 변수 통일 (버킷 불일치 방지)
    
    Returns:
        state_data: 스냅샷 경로가 추가된 상태
        snapshot_s3_path: 스냅샷 S3 경로
    """
    state_data = event.get('state_data', {})
    snapshot_type = event.get('snapshot_type', 'pre')  # 'pre' or 'post'
    execution_id = state_data.get('execution_id', 'unknown')
    idempotency_key = state_data.get('idempotency_key', 'unknown')
    
    updated_state = state_data.copy()
    
    # 🛡️ [v3.3] S3_BUCKET 통일 - 버킷 불일치 방지
    snapshot_bucket = S3_BUCKET
    if not snapshot_bucket:
        logger.error("[CRITICAL] S3_BUCKET not configured for snapshots!")
        return {
            'state_data': updated_state,
            'snapshot_s3_path': None,
            'error': 'S3_BUCKET not configured'
        }
    
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
        
        # S3에 스냅샷 저장 - 통일된 버킷 사용
        snapshot_key = f"snapshots/{execution_id}/{snapshot_type}_{timestamp}.json"
        
        s3_client.put_object(
            Bucket=snapshot_bucket,
            Key=snapshot_key,
            Body=json.dumps(snapshot_data, ensure_ascii=False, default=str),
            ContentType='application/json'
        )
        
        snapshot_s3_path = f"s3://{snapshot_bucket}/{snapshot_key}"
        
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