# -*- coding: utf-8 -*-
import logging
import os
import time
import json
import random
from typing import Dict, Any, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# [v2.1] Centralized Retry Utility
try:
    from src.common.retry_utils import retry_call, retry_stepfunctions, retry_s3
    RETRY_UTILS_AVAILABLE = True
except ImportError:
    RETRY_UTILS_AVAILABLE = False

# [Guard] [v2.2] Ring Protection: Prompt Security Guard
try:
    from src.services.recovery.prompt_security_guard import (
        PromptSecurityGuard,
        get_security_guard,
        RingLevel,
        SecurityViolation,
    )
    RING_PROTECTION_AVAILABLE = True
except ImportError:
    RING_PROTECTION_AVAILABLE = False
    get_security_guard = None
    RingLevel = None

# [Guard] [v2.3] 4-Layer Architecture: Concurrency Controller
try:
    from src.services.quality_kernel.concurrency_controller import (
        ConcurrencyControllerV2,
        get_concurrency_controller,
        LoadLevel
    )
    CONCURRENCY_CONTROLLER_AVAILABLE = True
except ImportError:
    CONCURRENCY_CONTROLLER_AVAILABLE = False
    get_concurrency_controller = None
    ConcurrencyControllerV2 = None

# [REACT] ReactExecutor integration for autonomous agent segments
try:
    from src.bridge.python_bridge import AnalemmaBridge as _ReactBridge
    from src.bridge.react_executor import ReactExecutor as _ReactExecutor
    REACT_EXECUTOR_AVAILABLE = True
except ImportError:
    REACT_EXECUTOR_AVAILABLE = False
    _ReactBridge = None
    _ReactExecutor = None

# [Phase 1] Speculative Execution Controller
try:
    from src.services.execution.speculative_controller import (
        SpeculativeExecutionController,
        ENABLE_SPECULATION,
    )
    SPECULATIVE_CONTROLLER_AVAILABLE = True
except ImportError:
    SPECULATIVE_CONTROLLER_AVAILABLE = False
    SpeculativeExecutionController = None
    ENABLE_SPECULATION = False

# [Phase 4] Optimistic Verifier
try:
    from src.services.execution.optimistic_verifier import (
        OptimisticVerifier,
        TrustChainResult,
        VerificationFailedError,
        ENABLE_OPTIMISTIC_VERIFICATION,
    )
    OPTIMISTIC_VERIFIER_AVAILABLE = True
except ImportError:
    OPTIMISTIC_VERIFIER_AVAILABLE = False
    OptimisticVerifier = None
    TrustChainResult = None
    VerificationFailedError = None
    ENABLE_OPTIMISTIC_VERIFICATION = False

# Services
from src.services.state.state_manager import StateManager
from src.common.security_utils import mask_pii_in_state
from src.services.recovery.self_healing_service import SelfHealingService
# [v3.11] Unified State Hydration
from src.common.state_hydrator import StateHydrator, SmartStateBag
from src.services.workflow.repository import WorkflowRepository
# [C-02 FIX] run_workflow moved to Lazy Local Import to prevent Cold Start circular imports.
# (_partition_workflow_dynamically, _build_segment_config have no actual calls - removed)
from src.common.statebag import normalize_inplace

# [P0 Refactoring] Smart StateBag Architecture
from src.common.state_hydrator import (
    check_inter_segment_edges,
    is_hitp_edge,
    is_loop_exit_edge,
    prepare_response_with_offload
)


logger = logging.getLogger(__name__)

# [v3.13] Kernel Protocol - The Great Seal Pattern
# Standardize all Lambda <-> ASL communication
try:
    from src.common.kernel_protocol import seal_state_bag, open_state_bag, get_from_bag
    KERNEL_PROTOCOL_AVAILABLE = True
except ImportError:
    try:
        from common.kernel_protocol import seal_state_bag, open_state_bag, get_from_bag
        KERNEL_PROTOCOL_AVAILABLE = True
    except ImportError:
        KERNEL_PROTOCOL_AVAILABLE = False
        logger.warning("[Warning] kernel_protocol not available - falling back to legacy mode")

# [v3.12] Shared Kernel Library: StateBag as Single Source of Truth
# ExecuteSegment now returns StateBag format directly using universal_sync_core
try:
    from src.handlers.utils.universal_sync_core import universal_sync_core
    UNIVERSAL_SYNC_CORE_AVAILABLE = True
except ImportError:
    UNIVERSAL_SYNC_CORE_AVAILABLE = False
    logger.warning("[Warning] universal_sync_core not available - falling back to legacy mode")

# ============================================================================
# [v3.5] None Reference Tracing: Environment-controlled debug logging
# ============================================================================
# Set NONE_TRACE_ENABLED=1 to enable verbose None tracing in logs
NONE_TRACE_ENABLED = os.environ.get("NONE_TRACE_ENABLED", "0") == "1"


def _trace_none_access(
    key: str,
    source: str,
    actual_value: Any,
    context: Dict[str, Any] = None,
    caller: str = None
) -> None:
    """
    [v3.5] Trace None value access for debugging NoneType errors

    This utility logs when a None value is accessed from state,
    helping identify the source of NoneType errors in production.

    Usage:
        val = state.get('input_list')
        _trace_none_access('input_list', 'state', val, caller='for_each_runner:line2850')
        if val is None:
            # handle None case

    Args:
        key: The key that was accessed
        source: Where the value came from (e.g., 'state', 'event', 'config')
        actual_value: The value that was retrieved (will log if None)
        context: Optional dict with additional context (will sample keys for log)
        caller: Optional caller identifier for tracing
    """
    if not NONE_TRACE_ENABLED:
        return
    
    if actual_value is None:
        ctx_keys = list(context.keys())[:10] if isinstance(context, dict) else "N/A"
        logger.warning(
            f"[None Trace] key='{key}' is None from {source}. "
            f"Caller: {caller or 'unknown'}. "
            f"Context keys (sample): {ctx_keys}"
        )


# ============================================================================
# [Guard] [Kernel] Dynamic Scheduling Constants
# ============================================================================
# Memory Safety Margin (Trigger split at 80% usage)
MEMORY_SAFETY_THRESHOLD = 0.8
# Minimum Node Count for Segment Splitting
MIN_NODES_PER_SUB_SEGMENT = 2
# Maximum Split Depth (Prevent infinite splitting)
MAX_SPLIT_DEPTH = 3
# Segment Status Values
SEGMENT_STATUS_PENDING = "PENDING"
SEGMENT_STATUS_RUNNING = "RUNNING"
SEGMENT_STATUS_COMPLETED = "COMPLETED"
SEGMENT_STATUS_SKIPPED = "SKIPPED"
SEGMENT_STATUS_FAILED = "FAILED"

# ============================================================================
# [Guard] [Kernel] Aggressive Retry & Partial Success Constants
# ============================================================================
# Kernel Internal Retry Count (Attempt before Step Functions level retry)
KERNEL_MAX_RETRIES = 3
# Retry Interval (Exponential backoff base)
KERNEL_RETRY_BASE_DELAY = 1.0
# Retryable Error Patterns
RETRYABLE_ERROR_PATTERNS = [
    'ThrottlingException',
    'ServiceUnavailable',
    'TooManyRequestsException',
    'ProvisionedThroughputExceeded',
    'InternalServerError',
    'ConnectionError',
    'TimeoutError',
    'ReadTimeoutError',
    'ConnectTimeoutError',
    'BrokenPipeError',
    'ResourceNotFoundException',  # S3 eventual consistency
]
# Enable Partial Success (Continue workflow even if segment fails)

# ============================================================================
# [Phase 8.3] Manifest Mutation Detection Constants
# ============================================================================
# Manifest mutation triggers manifest regeneration to maintain Merkle integrity
MUTATION_TRIGGERS = {
    'SEGMENT_SKIP': 'Segments marked for skip',
    'RECOVERY_INJECT': 'Recovery segments injected',
    'DYNAMIC_MODIFICATION': 'Dynamic segment modification'
}
ENABLE_PARTIAL_SUCCESS = True

# ============================================================================
# [Parallel] [Kernel] Parallel Scheduler Constants
# ============================================================================
# Default Concurrency Limit (Lambda account level)
DEFAULT_MAX_CONCURRENT_MEMORY_MB = 3072  # 3GB (Assuming 3 Lambda concurrent executions)
DEFAULT_MAX_CONCURRENT_TOKENS = 100000   # Tokens per minute limit
DEFAULT_MAX_CONCURRENT_BRANCHES = 10     # Maximum concurrent branches

# Scheduling Strategy
STRATEGY_SPEED_OPTIMIZED = "SPEED_OPTIMIZED"      # Maximize parallel execution
STRATEGY_RESOURCE_OPTIMIZED = "RESOURCE_OPTIMIZED" # Prioritize resource efficiency
STRATEGY_COST_OPTIMIZED = "COST_OPTIMIZED"        # Minimize cost

# Default estimated resources per branch
DEFAULT_BRANCH_MEMORY_MB = 256
DEFAULT_BRANCH_TOKENS = 5000

# Account level hard limit (checked even in SPEED_OPTIMIZED)
ACCOUNT_LAMBDA_CONCURRENCY_LIMIT = 100  # AWS default concurrency limit
ACCOUNT_MEMORY_HARD_LIMIT_MB = 10240    # 10GB hard limit

# State merge policy
MERGE_POLICY_OVERWRITE = "OVERWRITE"      # Later values overwrite (default)
MERGE_POLICY_APPEND_LIST = "APPEND_LIST"  # Lists are merged
MERGE_POLICY_KEEP_FIRST = "KEEP_FIRST"    # Keep first value
MERGE_POLICY_CONFLICT_ERROR = "ERROR"     # Error on conflict

# Key patterns requiring list merge
LIST_MERGE_KEY_PATTERNS = [
    '__new_history_logs',
    '__kernel_actions', 
    '_results',
    '_items',
    '_outputs',
    'collected_',
    'aggregated_'
]


def _safe_get_from_bag(
    event: Dict[str, Any], 
    key: str, 
    default: Any = None,
    caller: str = None,
    log_on_default: bool = False
) -> Any:
    """
    [v3.13] Kernel Protocol-based bag data extraction

    Uses kernel_protocol.get_from_bag for standardized data retrieval.
    Falls back to legacy logic if Kernel Protocol is unavailable.

    Args:
        event: Lambda event
        key: Key to extract
        default: Default value
        caller: (Optional) Caller identifier
        log_on_default: (Optional) Log when default is returned

    Returns:
        Found value or default
    """
    # [v3.13] Use Kernel Protocol (recommended)
    if KERNEL_PROTOCOL_AVAILABLE:
        val = get_from_bag(event, key, default)
        if val == default and log_on_default:
            logger.warning(f"[Kernel Protocol] key='{key}' returned default. Caller: {caller}")
        return val
    
    if not isinstance(event, dict):
        return default
    
    state_data = event.get('state_data') or {}
    
    if isinstance(state_data, dict):
        bag = state_data.get('bag')
        if isinstance(bag, dict):
            val = bag.get(key)
            if val is not None:
                return val
        
        val = state_data.get(key)
        if val is not None:
            return val
    
    val = event.get(key)
    if val is not None:
        return val
    
    if log_on_default:
        logger.warning(f"[SafeGet] key='{key}' returned default. Caller: {caller}")
    
    return default


def _safe_get_total_segments(event: Dict[str, Any]) -> int:
    """
    [Guard] [Fix] Safely extract total_segments

    Problem: event.get('total_segments') can be None, "", 0 etc.
    - None: passed as null from Step Functions
    - "": empty string
    - 0: valid but int(0) is falsy

    Returns:
        int: total_segments (minimum 1 guaranteed)
    """
    raw_value = event.get('total_segments')
    
    # If None, compute from partition_map in State Bag
    if raw_value is None:
        # [v3.4] Use _safe_get_from_bag
        partition_map = _safe_get_from_bag(event, 'partition_map')
        
        if partition_map and isinstance(partition_map, list):
            return max(1, len(partition_map))
        return 1
    
    # Numeric type: use directly
    if isinstance(raw_value, (int, float)):
        return max(1, int(raw_value))

    # String type: attempt parsing
    if isinstance(raw_value, str):
        raw_value = raw_value.strip()
        if raw_value and raw_value.isdigit():
            return max(1, int(raw_value))
        # Empty string or unparseable: default
        return 1

    # Other types: default
    return 1


def _normalize_node_config(node: Dict[str, Any]) -> Dict[str, Any]:
    """
    [Option A] Normalize None values to empty dict/list in node config

    Problem: When optional fields are stored as null in frontend/DB,
    Python's .get('key', {}).get('nested') pattern fails.

    Solution: Normalize None to {} before node execution
    """
    if not isinstance(node, dict):
        return node or {}

    # Fields to normalize (None -> {} or [])
    DICT_FIELDS = [
        'config', 'llm_config', 'sub_node_config', 'sub_workflow', 'nested_config',
        'retry_config', 'metadata', 'resource_policy', 'callbacks_config'
    ]
    LIST_FIELDS = [
        'nodes', 'edges', 'branches', 'callbacks', 'input_variables'
    ]

    for field in DICT_FIELDS:
        if field in node and node[field] is None:
            node[field] = {}

    for field in LIST_FIELDS:
        if field in node and node[field] is None:
            node[field] = []

    # Recursively normalize nested configs
    if 'config' in node and isinstance(node['config'], dict):
        _normalize_node_config(node['config'])
    if 'sub_node_config' in node and isinstance(node['sub_node_config'], dict):
        _normalize_node_config(node['sub_node_config'])
    if 'sub_workflow' in node and isinstance(node['sub_workflow'], dict):
        _normalize_node_config(node['sub_workflow'])
    if 'nested_config' in node and isinstance(node['nested_config'], dict):
        _normalize_node_config(node['nested_config'])

    # Normalize nodes array entries
    if 'nodes' in node and isinstance(node['nodes'], list):
        for child_node in node['nodes']:
            if isinstance(child_node, dict):
                _normalize_node_config(child_node)

    return node


def _normalize_segment_config(segment_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    [Option A] Normalize the entire segment config
    """
    if not isinstance(segment_config, dict):
        return segment_config or {}

    # Normalize segment-level fields
    if segment_config.get('nodes') is None:
        segment_config['nodes'] = []
    if segment_config.get('edges') is None:
        segment_config['edges'] = []
    if segment_config.get('branches') is None:
        segment_config['branches'] = []

    # Normalize each node
    for node in segment_config.get('nodes', []):
        if isinstance(node, dict):
            _normalize_node_config(node)
            # [P0 Fix] Also normalize sub_workflow inside config (for for_each nodes)
            node_config = node.get('config')
            if isinstance(node_config, dict):
                sub_workflow = node_config.get('sub_workflow')
                if isinstance(sub_workflow, dict):
                    _normalize_node_config(sub_workflow)
                    for sub_node in sub_workflow.get('nodes', []):
                        if isinstance(sub_node, dict):
                            _normalize_node_config(sub_node)

    # Normalize nodes inside branches
    for branch in segment_config.get('branches', []):
        if isinstance(branch, dict):
            for node in branch.get('nodes', []):
                if isinstance(node, dict):
                    _normalize_node_config(node)
                    # [P0 Fix] Also normalize sub_workflow in for_each config within branches
                    node_config = node.get('config')
                    if isinstance(node_config, dict):
                        sub_workflow = node_config.get('sub_workflow')
                        if isinstance(sub_workflow, dict):
                            _normalize_node_config(sub_workflow)

    return segment_config


class SegmentRunnerService:
    def __init__(self, s3_bucket: Optional[str] = None, deadline_ms: float = None):
        self._deadline_ms = deadline_ms
        self.state_manager = StateManager()
        self.healer = SelfHealingService()
        self.repo = WorkflowRepository()
        
        # [Perf Optimization] Safe threshold - 180KB for Step Functions with wrapper overhead buffer
        # 256KB SF limit - ~15KB AWS wrapper overhead = ~175KB safe, using 180KB
        SF_SAFE_THRESHOLD = 180000
        
        threshold_str = os.environ.get("STATE_SIZE_THRESHOLD", "")
        if threshold_str and threshold_str.strip():
            try:
                self.threshold = int(threshold_str.strip())
                # Warn if threshold is too high
                if self.threshold > SF_SAFE_THRESHOLD:
                    logger.warning(f"[Warning] STATE_SIZE_THRESHOLD={self.threshold} exceeds safe limit {SF_SAFE_THRESHOLD}")
            except ValueError:
                logger.warning(f"[Warning] Invalid STATE_SIZE_THRESHOLD='{threshold_str}', using default {SF_SAFE_THRESHOLD}")
                self.threshold = SF_SAFE_THRESHOLD
        else:
            self.threshold = SF_SAFE_THRESHOLD
            
        # [v3.11] Unified Bucket Resolution
        # Prioritize S3_BUCKET (standard), fallback to env vars if explicit arg missing
        if s3_bucket:
             self.state_bucket = s3_bucket
        else:
             self.state_bucket = (
                 os.environ.get('WORKFLOW_STATE_BUCKET') or 
                 os.environ.get('S3_BUCKET') or 
                 os.environ.get('SKELETON_S3_BUCKET')
             )
        
        # [v3.11] Initialize StateHydrator once (Reuse connection)
        self.hydrator = StateHydrator(bucket_name=self.state_bucket)
        
        if not self.state_bucket:
            logger.warning("[Warning] [SegmentRunnerService] S3 bucket not configured - large payloads may fail")
        else:
            logger.info(f"[Success] [SegmentRunnerService] S3 bucket: {self.state_bucket}, threshold: {self.threshold}")
        
        # [Guard] [Kernel] S3 Client (Lazy Initialization)
        self._s3_client = None
        
        # [Guard] [v2.2] Ring Protection Security Guard
        self._security_guard = None
        
        # [Guard] [v2.3] 4-Layer Architecture: Concurrency Controller
        self._concurrency_controller = None
        
        # [v3.20] RoutingResolver: Unified routing sovereignty (O(1) whitelist validation)
        self._routing_resolver = None
        
        # [v3.20] StateViewContext: Proxy pattern (78% memory reduction)
        self._state_view_context = None

    def _check_deadline(self, phase: str):
        """[v3.35] Graceful shutdown: raise TimeoutError before Lambda hard-kills."""
        if self._deadline_ms and time.time() * 1000 > self._deadline_ms:
            raise TimeoutError(
                f"Lambda deadline approaching during '{phase}'. "
                f"Graceful exit to preserve state integrity."
            )

    @property
    def security_guard(self):
        """Lazy Security Guard initialization"""
        if self._security_guard is None and RING_PROTECTION_AVAILABLE:
            self._security_guard = get_security_guard()
        return self._security_guard
    
    @property
    def concurrency_controller(self):
        """Lazy Concurrency Controller initialization"""
        if self._concurrency_controller is None and CONCURRENCY_CONTROLLER_AVAILABLE:
            # Reserved Concurrency 200 (configured in template.yaml)
            reserved = int(os.environ.get('RESERVED_CONCURRENCY', 200))
            max_budget = float(os.environ.get('MAX_BUDGET_USD', 10.0))
            self._concurrency_controller = get_concurrency_controller(
                workflow_id="segment_runner",
                reserved_concurrency=reserved,
                max_budget_usd=max_budget,
                enable_batching=True,
                enable_throttling=True
            )
        return self._concurrency_controller
    
    @property
    def routing_resolver(self):
        """
        Lazy RoutingResolver initialization

        Initialized once per workflow (on execute_segment entry)
        """
        return self._routing_resolver
    
    @property
    def state_view_context(self):
        """
        Lazy StateViewContext initialization

        Initialized once on first state load
        """
        if self._state_view_context is None:
            try:
                from src.common.state_view_context import (
                    create_state_view_context, 
                    FieldPolicyBuilder
                )
                
                self._state_view_context = create_state_view_context()
                
                # Set default field policies
                builder = FieldPolicyBuilder()
                
                # email: Hash at Ring 3
                self._state_view_context.set_field_policy(
                    "email", 
                    builder.hash_at_ring3()
                )
                
                # ssn, password: Redact at Ring 2-3
                for field in ["ssn", "password"]:
                    self._state_view_context.set_field_policy(
                        field,
                        builder.redact_at_ring2_3()
                    )
                
                # _kernel_*: Accessible only at Ring 1
                self._state_view_context.set_field_policy(
                    "_kernel_*",
                    builder.hidden_above_ring1()
                )
                
                logger.info("[StateViewContext] Initialized with default policies")
            except ImportError as e:
                logger.warning(f"[StateViewContext] Not available: {e}")
                self._state_view_context = None
        
        return self._state_view_context
    
    def _safe_json_load(self, content: str) -> Dict[str, Any]:
        """
        [Critical] Safe JSON loading to prevent UnboundLocalError
        
        Problem: Using 'json' as variable name shadows the module reference,
                 causing UnboundLocalError in nested scopes (ThreadPoolExecutor)
        
        Solution: Explicit import with alias to avoid shadowing
        
        Args:
            content: JSON string to parse
            
        Returns:
            Parsed JSON object (dict) or empty dict on error
        """
        import json as _json_module  # Alias prevents variable shadowing
        try:
            return _json_module.loads(content)
        except Exception as e:
            logger.error(f"[S3 Recovery] JSON parsing failed: {e}")
            return {}  # Return empty dict to prevent AttributeError cascade
    
    @property
    def s3_client(self):
        """Lazy S3 client initialization"""
        if self._s3_client is None:
            import boto3
            self._s3_client = boto3.client('s3')
        return self._s3_client

    # ========================================================================
    #  [Utility] State Merge: Integrity-guaranteed state merging
    # ========================================================================
    def _should_merge_as_list(self, key: str) -> bool:
        """
        Check if this key is a list merge target
        """
        for pattern in LIST_MERGE_KEY_PATTERNS:
            if pattern in key or key.startswith(pattern):
                return True
        return False

    @staticmethod
    def _deep_merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively merge two dicts.
        - Scalar / list values from override take precedence.
        - Nested dicts are merged recursively so sub-keys from BOTH sides are preserved.
        This fixes the 'Top-level Deep Merge' regression in _handle_aggregator where
        sibling branch states sharing nested-dict keys would lose their earlier values.
        """
        result = base.copy()
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = SegmentRunnerService._deep_merge_dicts(result[k], v)
            else:
                result[k] = v
        return result

    def _merge_states(
        self,
        base_state: Dict[str, Any],
        new_state: Dict[str, Any],
        merge_policy: str = MERGE_POLICY_APPEND_LIST
    ) -> Dict[str, Any]:
        """
        [System] Integrity-guaranteed state merging
        
        Policy:
        - OVERWRITE: Simple overwrite (existing behavior)
        - APPEND_LIST: Merge list keys, overwrite others
        - KEEP_FIRST: Keep existing keys
        - ERROR: Raise exception on key conflict
        
        Special handling:
        - __new_history_logs, __kernel_actions, etc. always merge as lists
        - Keys starting with _ are treated specially
        """
        # [v3.6] Immortal Kernel: Dual safety check before merge (Dual StateBag)
        from src.common.statebag import ensure_state_bag
        base_state = ensure_state_bag(base_state)
        new_state = ensure_state_bag(new_state) # None becomes empty StateBag({}), ensuring safe iteration
        
        if merge_policy == MERGE_POLICY_OVERWRITE:
            result = base_state.copy()
            result.update(new_state) # Safe now
            return result

        result = base_state.copy()
        conflicts = []
        
        for key, new_value in new_state.items():
            if key not in result:
                # New key: just add
                result[key] = new_value
                continue
            
            existing_value = result[key]
            
            # Check if key is list merge target
            if self._should_merge_as_list(key):
                # [P0 Fix] List merge order: existing + new (chronological)
                # Logs are naturally appended in chronological order.
                if isinstance(existing_value, list) and isinstance(new_value, list):
                    result[key] = existing_value + new_value  # Append new values after existing
                elif isinstance(new_value, list):
                    result[key] = ([existing_value] if existing_value else []) + new_value
                elif isinstance(existing_value, list):
                    result[key] = existing_value + ([new_value] if new_value else [])
                else:
                    result[key] = [existing_value, new_value] if existing_value and new_value else [existing_value or new_value]
                continue
            
            # Handle according to policy
            if merge_policy == MERGE_POLICY_KEEP_FIRST:
                # Keep existing value
                continue
            elif merge_policy == MERGE_POLICY_CONFLICT_ERROR:
                if existing_value != new_value:
                    conflicts.append(key)
            else:
                # APPEND_LIST default: deep-merge nested dicts, overwrite scalars/lists
                if isinstance(existing_value, dict) and isinstance(new_value, dict):
                    result[key] = self._deep_merge_dicts(existing_value, new_value)
                else:
                    result[key] = new_value
        
        if conflicts:
            logger.warning(f"[Merge] State conflicts detected on keys: {conflicts}")
            if merge_policy == MERGE_POLICY_CONFLICT_ERROR:
                raise ValueError(f"State merge conflict on keys: {conflicts}")
        
        return result

    def _cleanup_branch_intermediate_s3(
        self,
        parallel_results: List[Dict[str, Any]],
        workflow_id: str,
        segment_id: int
    ) -> None:
        """
        [Critical] Clean up S3 intermediate branch result files (Garbage Collection)

        [P0 Hardened] Idempotency guaranteed + retry logic

        Deletes temporary S3 files created by each branch after aggregation
        to reduce S3 costs and management overhead.

        Production recommendations:
        - S3 Lifecycle Policy required (auto-delete after 24 hours)
        - Prevents 'ghost data' on deletion failure

        Args:
            parallel_results: List of branch execution results
            workflow_id: Workflow ID
            segment_id: Current segment ID
        """
        if not parallel_results:
            return
        
        s3_paths_to_delete = []
        
        # Collect S3 paths from branch results
        for result in parallel_results:
            if not result or not isinstance(result, dict):
                continue
            
            s3_path = result.get('final_state_s3_path') or result.get('state_s3_path')
            if s3_path:
                s3_paths_to_delete.append(s3_path)
        
        if not s3_paths_to_delete:
            logger.debug(f"[Aggregator] No S3 intermediate files to cleanup")
            return
        
        logger.info(f"[Aggregator] Cleaning up {len(s3_paths_to_delete)} S3 intermediate files")
        
        # [P0] Retry logic (max 2 attempts)
        MAX_RETRIES = 2
        failed_paths = []
        
        def delete_s3_object_with_retry(s3_path: str) -> Tuple[bool, str]:
            """Delete S3 object with retry"""
            for attempt in range(MAX_RETRIES + 1):
                try:
                    bucket = s3_path.replace("s3://", "").split("/")[0]
                    key = "/".join(s3_path.replace("s3://", "").split("/")[1:])
                    self.state_manager.s3_client.delete_object(Bucket=bucket, Key=key)
                    return True, s3_path
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        time.sleep(0.1 * (attempt + 1))  # backoff
                        continue
                    logger.warning(
                        f"[Aggregator] Failed to delete {s3_path} after {MAX_RETRIES + 1} attempts: {e}. "
                        f"This file may become 'ghost data'. Consider S3 Lifecycle Policy."
                    )
                    return False, s3_path
            return False, s3_path
        
        # Parallel deletion via ThreadPoolExecutor
        deleted_count = 0
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(delete_s3_object_with_retry, path): path for path in s3_paths_to_delete}
            
            for future in as_completed(futures, timeout=30):
                try:
                    success, path = future.result(timeout=5)
                    if success:
                        deleted_count += 1
                    else:
                        failed_paths.append(path)
                except Exception as e:
                    logger.warning(f"[Aggregator] Cleanup future failed: {e}")
        
        # Log results
        if failed_paths:
            logger.warning(
                f"[Aggregator] Cleanup incomplete: {deleted_count}/{len(s3_paths_to_delete)} deleted, "
                f"{len(failed_paths)} failed. Ghost data paths: {failed_paths[:3]}{'...' if len(failed_paths) > 3 else ''}"
            )
        else:
            logger.info(f"[Aggregator] Cleanup complete: {deleted_count}/{len(s3_paths_to_delete)} files deleted")

    # ========================================================================
    # [Pattern 1] Segment-Level Self-Healing: Segment Auto-Splitting
    # ========================================================================
    def _estimate_segment_memory(self, segment_config: Dict[str, Any], state: Dict[str, Any]) -> int:
        """Memory estimation."""
        base_memory = 50  # base overhead
        
        nodes = segment_config.get('nodes', [])
        if not nodes:
            return base_memory
        
        node_memory = len(nodes) * 10  # 10MB per node
        
        llm_memory = 0
        foreach_memory = 0
        
        for node in nodes:
            # [v3.8] None defense in nodes iteration
            if node is None or not isinstance(node, dict):
                continue
                
            node_type = node.get('type', '')
            
            # [Debug] [KERNEL DEBUG] Track node type corruption - log if 'code' type is found
            if node_type == 'code':
                logger.warning(
                    f"[KERNEL DEBUG] Detected 'code' type node! "
                    f"Node ID: {node.get('id')}, Config keys: {list(node.get('config', {}).keys())}. "
                    f"This should have been aliased to 'operator' by Pydantic validator."
                )
            
            if node_type in ('llm_chat', 'aiModel'):
                llm_memory += 50  # LLM nodes get additional 50MB
            elif node_type == 'for_each':
                config = node.get('config', {})
                items_key = config.get('input_list_key', '')
                if items_key and items_key in state:
                    items = state.get(items_key, [])
                    if isinstance(items, list):
                        foreach_memory += len(items) * 5
        
        # [Optimization] State size estimation based on metadata (avoid json.dumps)
        state_size_mb = self._estimate_state_size_lightweight(state)
        
        total = base_memory + node_memory + llm_memory + foreach_memory + int(state_size_mb)
        
        logger.debug(f"[Kernel] Memory estimate: base={base_memory}, nodes={node_memory}, "
                    f"llm={llm_memory}, foreach={foreach_memory}, state={state_size_mb:.1f}MB, total={total}MB")
        
        return total

    def _estimate_state_size_lightweight(self, state: Dict[str, Any], max_sample_keys: int = 20) -> float:
        """
        [Optimization] Lightweight estimation of state size without json.dumps
        
        Strategy:
        1. Sample only top N keys to calculate average size
        2. Estimate lists as length * average item size
        3. Use len() for strings
        4. Estimate nested dicts by key count
        
        Returns:
            Estimated size (MB)
        """
        if not state or not isinstance(state, dict):
            return 0.1  # minimum 100KB
        
        total_bytes = 0
        keys = list(state.keys())[:max_sample_keys]
        
        for key in keys:
            value = state.get(key)
            total_bytes += self._estimate_value_size(value)
        
        # Estimate total size based on sampling ratio
        if len(state) > max_sample_keys:
            sample_ratio = len(state) / max_sample_keys
            total_bytes = int(total_bytes * sample_ratio)
        
        return total_bytes / (1024 * 1024)  # bytes → MB

    def _estimate_value_size(self, value: Any, depth: int = 0) -> int:
        """
        Heuristically estimate value size (bytes)
        
        Prevent infinite loops with recursion depth limit
        """
        if depth > 3:  # depth limit
            return 100  # approximate estimate
        
        if value is None:
            return 4
        elif isinstance(value, bool):
            return 4
        elif isinstance(value, (int, float)):
            return 8
        elif isinstance(value, str):
            return len(value.encode('utf-8', errors='ignore'))
        elif isinstance(value, bytes):
            return len(value)
        elif isinstance(value, list):
            if not value:
                return 2
            # Sample only first 3 items to calculate average
            sample = value[:3]
            avg_size = sum(self._estimate_value_size(v, depth + 1) for v in sample) / len(sample)
            return int(avg_size * len(value))
        elif isinstance(value, dict):
            if not value:
                return 2
            # Sample only first 5 keys
            sample_keys = list(value.keys())[:5]
            sample_size = sum(
                len(str(k)) + self._estimate_value_size(value[k], depth + 1) 
                for k in sample_keys
            )
            if len(value) > 5:
                return int(sample_size * len(value) / 5)
            return sample_size
        else:
            # Other types: approximate estimate
            return 100

    def _split_segment(self, segment_config: Dict[str, Any], split_depth: int = 0) -> List[Dict[str, Any]]:
        """
        Split segment into smaller sub-segments
        
        Splitting strategy:
        1. Split node list in half
        2. Maintain dependencies: preserve edge connections
        3. Guarantee minimum node count
        """
        if split_depth >= MAX_SPLIT_DEPTH:
            logger.warning(f"[Kernel] Max split depth ({MAX_SPLIT_DEPTH}) reached, returning original segment")
            return [segment_config]
        
        nodes = segment_config.get('nodes', [])
        edges = segment_config.get('edges', [])
        
        if len(nodes) < MIN_NODES_PER_SUB_SEGMENT * 2:
            logger.info(f"[Kernel] Segment too small to split ({len(nodes)} nodes)")
            return [segment_config]
        
        # Split nodes in half
        mid = len(nodes) // 2
        first_nodes = nodes[:mid]
        second_nodes = nodes[mid:]
        
        # [Critical Guard] Filter out None (nodes array may contain None entries)
        first_nodes = [n for n in first_nodes if n is not None]
        second_nodes = [n for n in second_nodes if n is not None]
        
        if not first_nodes or not second_nodes:
            logger.warning(f"[Kernel] Node filtering resulted in empty segment, returning original")
            return [segment_config]
        
        first_node_ids = {n.get('id') for n in first_nodes if isinstance(n, dict)}
        second_node_ids = {n.get('id') for n in second_nodes if isinstance(n, dict)}
        
        # Separate edges: keep only edges internal to each sub-segment
        # [Critical Guard] edges can be None; each edge may also be None or non-dict
        first_edges = [e for e in edges 
                      if e is not None and isinstance(e, dict) 
                      and e.get('source') in first_node_ids and e.get('target') in first_node_ids]
        second_edges = [e for e in edges 
                       if e is not None and isinstance(e, dict)
                       and e.get('source') in second_node_ids and e.get('target') in second_node_ids]
        
        # Create sub-segments
        original_id = segment_config.get('id', 'segment')
        
        sub_segment_1 = {
            **segment_config,
            'id': f"{original_id}_sub_1",
            'nodes': first_nodes,
            'edges': first_edges,
            '_kernel_split': True,
            '_split_depth': split_depth + 1,
            '_parent_segment_id': original_id
        }
        
        sub_segment_2 = {
            **segment_config,
            'id': f"{original_id}_sub_2",
            'nodes': second_nodes,
            'edges': second_edges,
            '_kernel_split': True,
            '_split_depth': split_depth + 1,
            '_parent_segment_id': original_id
        }
        
        logger.info(f"[Kernel] [System] Segment '{original_id}' split into 2 sub-segments: "
                   f"{len(first_nodes)} + {len(second_nodes)} nodes")
        
        return [sub_segment_1, sub_segment_2]

    def _execute_with_auto_split(
        self, 
        segment_config: Dict[str, Any], 
        initial_state: Dict[str, Any],
        auth_user_id: str,
        split_depth: int = 0
    ) -> Dict[str, Any]:
        """Pattern 1: Memory-based auto-split execution."""
        # Available Lambda memory
        available_memory = int(os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', 512))

        # Estimate memory requirement
        estimated_memory = self._estimate_segment_memory(segment_config, initial_state)

        # Safety threshold check
        if estimated_memory > available_memory * MEMORY_SAFETY_THRESHOLD:
            logger.info(f"[Kernel] [Warning] Memory pressure detected: {estimated_memory}MB estimated, "
                       f"{available_memory}MB available (threshold: {MEMORY_SAFETY_THRESHOLD*100}%)")
            
            # Attempt split
            sub_segments = self._split_segment(segment_config, split_depth)

            if len(sub_segments) > 1:
                logger.info(f"[Kernel] [System] Executing {len(sub_segments)} sub-segments sequentially")

                # Execute sub-segments sequentially
                current_state = initial_state.copy()
                all_logs = []
                kernel_actions = []
                
                for i, sub_seg in enumerate(sub_segments):
                    logger.info(f"[Kernel] Executing sub-segment {i+1}/{len(sub_segments)}: {sub_seg.get('id')}")
                    
                    # Apply auto-split recursively
                    sub_result = self._execute_with_auto_split(
                        sub_seg, current_state, auth_user_id, split_depth + 1
                    )
                    
                    # [System] Integrity-guaranteed state merge (list keys are concatenated)
                    if isinstance(sub_result, dict):
                        current_state = self._merge_states(
                            current_state, 
                            sub_result,
                            merge_policy=MERGE_POLICY_APPEND_LIST
                        )
                        # all_logs already handled by _merge_states
                    
                    kernel_actions.append({
                        'action': 'SPLIT_EXECUTE',
                        'sub_segment_id': sub_seg.get('id'),
                        'index': i,
                        'timestamp': time.time()
                    })
                
                # Add kernel metadata
                current_state['__kernel_actions'] = kernel_actions
                current_state['__new_history_logs'] = all_logs
                
                return current_state
        
        # Normal execution (no split needed)
        logger.error(f"[v3.27 AUTO_SPLIT] Calling run_workflow with segment_config keys: {list(segment_config.keys())[:15] if isinstance(segment_config, dict) else 'NOT A DICT'}")
        logger.error(f"[v3.27 AUTO_SPLIT] segment_config.nodes count: {len(segment_config.get('nodes', [])) if isinstance(segment_config, dict) else 'N/A'}")
        if isinstance(segment_config, dict) and segment_config.get('nodes'):
            node_types = [n.get('type', 'unknown') for n in segment_config.get('nodes', [])[:5]]
            logger.error(f"[v3.27 AUTO_SPLIT] First 5 node types: {node_types}")

        # [C-02 FIX] Lazy import: prevent circular imports (removed from top-level)
        from src.handlers.core.main import run_workflow
        result = run_workflow(
            config_json=segment_config,
            initial_state=initial_state,
            ddb_table_name=os.environ.get("JOB_TABLE"),
            user_api_keys={},
            run_config={"user_id": auth_user_id}
        )
        
        logger.error(f"[v3.27 AUTO_SPLIT] run_workflow returned result keys: {list(result.keys())[:20] if isinstance(result, dict) else 'NOT A DICT'}")
        if isinstance(result, dict) and 'llm_raw_output' in result:
            logger.error(f"[v3.27 AUTO_SPLIT] llm_raw_output FOUND in result!")
        else:
            logger.error(f"[v3.27 AUTO_SPLIT] llm_raw_output NOT FOUND in result")
        
        return result

    # ========================================================================
    # [Guard] [Pattern 2] Manifest Mutation: Dynamic S3 Manifest modification
    # ========================================================================
    def _load_manifest_from_s3(self, manifest_s3_path: str) -> Optional[List[Dict[str, Any]]]:
        """Load segment_manifest from S3"""
        if not manifest_s3_path or not manifest_s3_path.startswith('s3://'):
            return None
        
        try:
            parts = manifest_s3_path.replace('s3://', '').split('/', 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ''
            
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            content = response['Body'].read().decode('utf-8')
            manifest = self._safe_json_load(content)
            
            logger.info(f"[Kernel] Loaded manifest from S3: {len(manifest)} segments")
            return manifest
            
        except Exception as e:
            logger.error(f"[Kernel] Failed to load manifest from S3: {e}")
            return None

    def _save_manifest_to_s3(self, manifest: List[Dict[str, Any]], manifest_s3_path: str) -> bool:
        """Save modified segment_manifest to S3"""
        if not manifest_s3_path or not manifest_s3_path.startswith('s3://'):
            return False
        
        try:
            parts = manifest_s3_path.replace('s3://', '').split('/', 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ''
            
            self.s3_client.put_object(
                Bucket=bucket,
                Key=key,
                Body=json.dumps(manifest, ensure_ascii=False).encode('utf-8'),
                ContentType='application/json',
                Metadata={
                    'kernel_modified': 'true',
                    'modified_at': str(int(time.time()))
                }
            )
            
            logger.info(f"[Kernel] Saved modified manifest to S3: {len(manifest)} segments")
            return True
            
        except Exception as e:
            logger.error(f"[Kernel] Failed to save manifest to S3: {e}")
            return False

    def _check_segment_status(self, segment_config: Dict[str, Any]) -> str:
        """Check segment status (SKIPPED, etc.)"""
        return segment_config.get('status', SEGMENT_STATUS_PENDING)

    def _mark_segments_for_skip(
        self, 
        manifest_s3_path: str, 
        segment_ids_to_skip: List[int], 
        reason: str,
        bag: 'SmartStateBag' = None,
        workflow_config: dict = None
    ) -> bool:
        """
        [Phase 8.3] Mark specific segments as SKIP + regenerate manifest

        Use cases:
        - Conditional branch makes certain paths unnecessary
        - Preceding segment failure makes subsequent segments unrunnable

        Architecture change (Phase 8):
        - Old: Direct S3 manifest modification (invalidates Merkle DAG)
        - New: Manifest regeneration + Hash Chain linking
        """
        manifest = self._load_manifest_from_s3(manifest_s3_path)
        if not manifest:
            return False
        
        modified = False
        for segment in manifest:
            if segment.get('segment_id') in segment_ids_to_skip:
                segment['status'] = SEGMENT_STATUS_SKIPPED
                segment['skip_reason'] = reason
                segment['skipped_at'] = int(time.time())
                segment['skipped_by'] = 'kernel'
                modified = True
                logger.info(f"[Kernel] Marked segment {segment.get('segment_id')} for skip: {reason}")
        
        if modified and bag and workflow_config:
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Phase 8.3] Merkle DAG regeneration (no direct S3 modification)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            try:
                new_manifest_id, new_hash, config_hash = self._invalidate_and_regenerate_manifest(
                    workflow_id=bag.get('workflowId'),
                    workflow_config=workflow_config,
                    modified_segments=manifest,
                    execution_id=bag.get('executionId', 'unknown'),
                    parent_manifest_id=bag.get('manifest_id'),
                    parent_manifest_hash=bag.get('manifest_hash'),
                    reason=f"{MUTATION_TRIGGERS['SEGMENT_SKIP']}: {segment_ids_to_skip}"
                )

                # Update StateBag
                bag['manifest_id'] = new_manifest_id
                bag['manifest_hash'] = new_hash
                bag['config_hash'] = config_hash
                
                logger.info(
                    f"[Manifest Mutation] StateBag updated after skip\n"
                    f"  New manifest_id: {new_manifest_id[:8]}..."
                )
                
                return True
                
            except Exception as regen_error:
                logger.error(
                    f"[Manifest Regeneration] Failed after skip: {regen_error}",
                    exc_info=True
                )
                # Fallback: Direct S3 save (legacy mode)
                logger.warning("[Fallback] Using legacy S3 direct save (Merkle integrity lost)")
                return self._save_manifest_to_s3(manifest, manifest_s3_path)

        elif modified:
            # Legacy mode when bag/workflow_config unavailable
            logger.warning("[Legacy Mode] Manifest regeneration skipped - using direct S3 save")
            return self._save_manifest_to_s3(manifest, manifest_s3_path)
        
        return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [Phase 8.2 & 8.3] Manifest Mutation Detection & Regeneration
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _invalidate_and_regenerate_manifest(
        self,
        workflow_id: str,
        workflow_config: dict,
        modified_segments: List[Dict[str, Any]],
        execution_id: str,
        parent_manifest_id: str,
        parent_manifest_hash: str,
        reason: str
    ) -> tuple:
        """
        [Phase 8.2 & 8.3] Regenerate manifest on mutation detection

        Architecture principles (Phase 8 Guideline):
        - Similar to Git Rebase: new manifest = new hash based on previous manifest
        - Merkle Chain linked via parent_hash -> guarantees historical integrity
        - Agent post-hoc tampering immediately detected via broken hash chain

        Trigger scenarios:
        - _mark_segments_for_skip() called
        - _inject_recovery_segments() called
        - Dynamic segment modification

        Args:
            workflow_id: Workflow ID
            workflow_config: Workflow configuration
            modified_segments: List of modified segments
            execution_id: Execution ID
            parent_manifest_id: Previous manifest ID
            parent_manifest_hash: Previous manifest hash
            reason: Reason for regeneration

        Returns:
            (new_manifest_id, new_manifest_hash, config_hash)
        """
        try:
            from src.services.state.state_versioning_service import StateVersioningService
            from src.services.state.async_commit_service import get_async_commit_service
            
            logger.warning(
                f"[Manifest Mutation] Regenerating manifest\n"
                f"  Reason: {reason}\n"
                f"  Parent: {parent_manifest_id[:8]}...\n"
                f"  Segments: {len(modified_segments)}"
            )
            
            # 1. Create new manifest via StateVersioningService
            versioning_service = StateVersioningService(
                dynamodb_table=os.environ.get('WORKFLOW_MANIFESTS_TABLE', 'WorkflowManifestsV3'),
                s3_bucket=os.environ.get('S3_BUCKET', 'analemma-state-dev')
            )
            
            # 2. Create new manifest (parent_hash = hash of previous manifest)
            manifest_pointer = versioning_service.create_manifest(
                workflow_id=workflow_id,
                workflow_config=workflow_config,
                segment_manifest=modified_segments,
                parent_manifest_id=parent_manifest_id  # Merkle Chain linking
            )
            
            new_manifest_id = manifest_pointer.manifest_id
            new_hash = manifest_pointer.manifest_hash
            config_hash = manifest_pointer.config_hash
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Phase 8.1] Pre-flight Check: S3 Strong Consistency verification
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            async_commit = get_async_commit_service()
            manifest_s3_key = f"manifests/{new_manifest_id}.json"
            
            commit_status = async_commit.verify_commit_with_retry(
                execution_id=execution_id,
                s3_bucket=os.environ.get('S3_BUCKET', 'analemma-state-dev'),
                s3_key=manifest_s3_key,
                redis_key=None  # S3 verification only
            )
            
            if not commit_status.s3_available:
                raise RuntimeError(
                    f"[Manifest Regeneration Failed] New manifest S3 unavailable "
                    f"after {commit_status.retry_count} attempts "
                    f"(wait={commit_status.total_wait_ms:.1f}ms): {new_manifest_id[:8]}..."
                )
            
            logger.info(
                f"[Manifest Mutation] New manifest created and verified\n"
                f"  New ID: {new_manifest_id[:8]}...\n"
                f"  Parent: {parent_manifest_id[:8]}...\n"
                f"  Hash Chain: {parent_manifest_hash[:8]}... → {new_hash[:8]}...\n"
                f"  S3 Verification: {commit_status.retry_count} retries, "
                f"{commit_status.total_wait_ms:.1f}ms"
            )
            
            return new_manifest_id, new_hash, config_hash
            
        except ImportError as import_err:
            logger.error(
                f"[Manifest Regeneration] Import failed: {import_err}\n"
                f"StateVersioningService or AsyncCommitService not available"
            )
            raise RuntimeError(
                f"Manifest regeneration failed - required services unavailable: {import_err}"
            ) from import_err
            
        except Exception as regen_error:
            logger.error(
                f"[Manifest Regeneration] Failed: {regen_error}",
                exc_info=True
            )
            raise RuntimeError(
                f"Manifest regeneration failed for parent {parent_manifest_id[:8]}...: "
                f"{str(regen_error)}"
            ) from regen_error

    def _inject_recovery_segments(
        self,
        manifest_s3_path: str,
        after_segment_id: int,
        recovery_segments: List[Dict[str, Any]],
        reason: str,
        bag: 'SmartStateBag' = None,
        workflow_config: dict = None
    ) -> bool:
        """
        [Phase 8.3] Insert recovery segments + regenerate manifest

        Use cases:
        - Insert backup path after API failure
        - Dynamically add error handling segments
        - Agent Re-planning

        Architecture change (Phase 8):
        - Old: Direct S3 manifest modification (invalidates Merkle DAG)
        - New: Manifest regeneration + Hash Chain linking

        [NEW] Dynamic Re-partitioning support:
        - Async ManifestRegenerator Lambda invocation on large modifications
        - Re-partitioning required when: 3+ segments inserted or AGENT_REPLAN
        """
        manifest = self._load_manifest_from_s3(manifest_s3_path)
        if not manifest:
            return False
        
        # [NEW] Re-partitioning trigger conditions
        needs_repartition = (
            len(recovery_segments) > 3 or  # Many segments inserted
            reason == "AGENT_REPLAN" or    # Agent changed plan
            self._check_structural_change(recovery_segments, workflow_config)
        )
        
        if needs_repartition and bag and workflow_config:
            logger.info(f"[Manifest] Re-partitioning required: {reason}")

            # [FIX] Pass Task Token (Step Functions wait)
            task_token = bag.get('task_token')  # Token injected from ASL
            
            regen_result = self._trigger_manifest_regeneration(
                manifest_s3_path=manifest_s3_path,
                workflow_id=bag.get('workflowId'),
                owner_id=bag.get('ownerId'),
                recovery_segments=recovery_segments,
                reason=reason,
                workflow_config=workflow_config,
                task_token=task_token
            )
            
            # Sync mode: immediate completion
            if regen_result.get('sync_mode'):
                logger.info(f"[Manifest] Synchronous regeneration completed")
                return True

            # Async mode + Task Token: Step Functions is waiting
            if regen_result.get('wait_for_task_token'):
                logger.info(f"[Manifest] Asynchronous regeneration in progress (Step Functions waiting)")
                # SegmentRunner exits here (Step Functions waits for Task Token callback)
                return True

            # Async mode (no Task Token): background execution
            logger.warning(f"[Manifest] Asynchronous regeneration without Task Token (no wait)")
            return regen_result.get('status') == 'MANIFEST_REGENERATING'
        
        # [Legacy Mode] Small modifications: handle with existing approach
        logger.info(f"[Manifest] Using legacy injection mode (< 3 segments)")
        
        # Find insertion point
        insert_index = None
        for i, segment in enumerate(manifest):
            if segment.get('segment_id') == after_segment_id:
                insert_index = i + 1
                break
        
        if insert_index is None:
            logger.warning(f"[Kernel] Could not find segment {after_segment_id} for recovery injection")
            return False
        
        # Add metadata to recovery segments
        max_segment_id = max(s.get('segment_id', 0) for s in manifest)
        for i, rec_seg in enumerate(recovery_segments):
            rec_seg['segment_id'] = max_segment_id + i + 1
            rec_seg['status'] = SEGMENT_STATUS_PENDING
            rec_seg['injected_by'] = 'kernel'
            rec_seg['injection_reason'] = reason
            rec_seg['injected_at'] = int(time.time())
            rec_seg['type'] = rec_seg.get('type', 'recovery')
        
        # Insert into manifest
        new_manifest = manifest[:insert_index] + recovery_segments + manifest[insert_index:]
        
        # Re-adjust subsequent segment IDs
        for i, segment in enumerate(new_manifest):
            segment['execution_order'] = i
        
        logger.info(f"[Kernel] [System] Injected {len(recovery_segments)} recovery segments after segment {after_segment_id}")
        
        if bag and workflow_config:
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Phase 8.3] Merkle DAG regeneration
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            try:
                new_manifest_id, new_hash, config_hash = self._invalidate_and_regenerate_manifest(
                    workflow_id=bag.get('workflowId'),
                    workflow_config=workflow_config,
                    modified_segments=new_manifest,
                    execution_id=bag.get('executionId', 'unknown'),
                    parent_manifest_id=bag.get('manifest_id'),
                    parent_manifest_hash=bag.get('manifest_hash'),
                    reason=f"{MUTATION_TRIGGERS['RECOVERY_INJECT']}: {len(recovery_segments)} segments"
                )
                
                # Update StateBag
                bag['manifest_id'] = new_manifest_id
                bag['manifest_hash'] = new_hash
                bag['config_hash'] = config_hash

                logger.info(
                    f"[Manifest Mutation] StateBag updated after recovery injection\n"
                    f"  New manifest_id: {new_manifest_id[:8]}..."
                )
                
                return True
                
            except Exception as regen_error:
                logger.error(
                    f"[Manifest Regeneration] Failed after injection: {regen_error}",
                    exc_info=True
                )
                # Fallback: Direct S3 save (legacy mode)
                logger.warning("[Fallback] Using legacy S3 direct save (Merkle integrity lost)")
                return self._save_manifest_to_s3(new_manifest, manifest_s3_path)

        else:
            # Legacy mode when bag/workflow_config unavailable
            logger.warning("[Legacy Mode] Manifest regeneration skipped - using direct S3 save")
            return self._save_manifest_to_s3(new_manifest, manifest_s3_path)
    
    def _check_structural_change(
        self,
        recovery_segments: List[Dict[str, Any]],
        workflow_config: dict
    ) -> bool:
        """
        Detect structural changes: determine if re-partitioning is needed

        Re-partitioning required when:
        - New LLM node added
        - New parallel_group added
        - Existing node type changed
        """
        if not recovery_segments or not workflow_config:
            return False
        
        # Check if new nodes contain LLM or parallel_group
        for segment in recovery_segments:
            nodes = segment.get('nodes', [])
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                
                node_type = node.get('type', '')
                
                # Detect LLM node
                if node_type in ('llm_chat', 'aiModel', 'llm'):
                    logger.info(f"[Structural Change] LLM node detected: {node.get('id')}")
                    return True
                
                # Detect Parallel Group
                if node_type == 'parallel_group':
                    logger.info(f"[Structural Change] Parallel group detected: {node.get('id')}")
                    return True
                
                # Node with branches property (inline parallel)
                if node.get('branches'):
                    logger.info(f"[Structural Change] Inline parallel detected: {node.get('id')}")
                    return True
        
        return False
    
    def _trigger_manifest_regeneration(
        self,
        manifest_s3_path: str,
        workflow_id: str,
        owner_id: str,
        recovery_segments: List[Dict[str, Any]],
        reason: str,
        workflow_config: dict,
        task_token: str = None
    ) -> Dict[str, Any]:
        """
        Invoke ManifestRegenerator Lambda (sync or async)

        [FIX] Race Condition resolution:
        - If task_token exists: async + WaitForTaskToken pattern
        - If no task_token: sync invocation (immediate result)

        [FIX] Adaptive delegation policy:
        - Force async if estimated processing time > 3 seconds
        - Prevents timeout on large workflows
        """
        try:
            import boto3
            lambda_client = boto3.client('lambda')
            
            # Convert recovery segments to modifications format
            new_nodes = []
            new_edges = []
            
            for segment in recovery_segments:
                segment_nodes = segment.get('nodes', [])
                segment_edges = segment.get('edges', [])
                
                new_nodes.extend(segment_nodes)
                new_edges.extend(segment_edges)
            
            # [NEW] Adaptive delegation policy: estimate processing time
            total_nodes = len(workflow_config.get('nodes', []))
            total_edges = len(workflow_config.get('edges', []))
            estimated_time = (total_nodes * 0.01) + (total_edges * 0.005)  # rough estimate

            force_async = (
                estimated_time > 3.0 or  # Expected to exceed 3 seconds
                total_nodes > 100 or     # Large workflow
                len(recovery_segments) > 5  # Many segments inserted
            )
            
            payload = {
                'manifest_s3_path': manifest_s3_path,
                'workflow_id': workflow_id,
                'owner_id': owner_id,
                'modification_type': 'RECOVERY_INJECT',
                'modifications': {
                    'new_nodes': new_nodes,
                    'new_edges': new_edges,
                    'reason': reason
                }
            }
            
            # [FIX] Task Token pattern
            if task_token:
                payload['task_token'] = task_token
                invocation_type = 'Event'  # Async (Step Functions waits)
                logger.info(f"[Manifest Regeneration] Using Task Token pattern (async)")
            elif force_async:
                invocation_type = 'Event'
                logger.warning(
                    f"[Manifest Regeneration] Forcing async due to estimated time {estimated_time:.2f}s "
                    f"(nodes={total_nodes}, edges={total_edges})"
                )
            else:
                invocation_type = 'RequestResponse'  # Sync
                logger.info(f"[Manifest Regeneration] Using synchronous invocation")
            
            function_name = os.environ.get(
                'MANIFEST_REGENERATOR_FUNCTION',
                'ManifestRegeneratorFunction'
            )
            
            logger.info(f"[Manifest Regeneration] Invoking {function_name} ({invocation_type})")
            
            response = lambda_client.invoke(
                FunctionName=function_name,
                InvocationType=invocation_type,
                Payload=json.dumps(payload)
            )
            
            # Sync invocation: parse result immediately
            if invocation_type == 'RequestResponse':
                response_payload = json.loads(response['Payload'].read())
                logger.info(f"[Manifest Regeneration] Completed synchronously: {response_payload.get('status')}")
                return {
                    'status': 'MANIFEST_REGENERATED',
                    'sync_mode': True,
                    'result': response_payload
                }
            
            # Async invocation: Step Functions waits via Task Token
            logger.info(f"[Manifest Regeneration] Invoked asynchronously: {response['StatusCode']}")
            return {
                'status': 'MANIFEST_REGENERATING',
                'sync_mode': False,
                'wait_for_task_token': bool(task_token)
            }
            
        except Exception as e:
            logger.error(f"[Manifest Regeneration] Failed to invoke: {e}")
            return {
                'status': 'REGENERATION_FAILED',
                'error': str(e)
            }

    # ========================================================================
    # [Parallel] [Pattern 3] Parallel Scheduler: Infrastructure-aware parallel scheduling
    # ========================================================================
    
    def _offload_branches_to_s3(
        self,
        branches: List[Dict[str, Any]],
        owner_id: str,
        workflow_id: str,
        segment_id: int
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        [Pointer Strategy] Upload each branch to S3 and return lightweight pointer array

        Map-internal Hydrate strategy:
        - Upload full branches data to S3 (single file)
        - Pass only index + S3 path lightweight pointer array to pending_branches
        - Each branch hydrates its own data from S3 path at first Map Iterator step

        Returns:
            (branch_pointers, branches_s3_path)
            - branch_pointers: [{branch_index, branch_id, branches_s3_path, total_branches}, ...]
            - branches_s3_path: S3 path where full branches array is stored
        """
        if not branches:
            return [], None
        
        if not self.state_bucket:
            logger.warning("[Pointer Strategy] No S3 bucket configured. Returning inline branches (may exceed payload limit)")
            # Fallback: inline return (risky but unavoidable without S3)
            return branches, None
        
        try:
            import boto3
            s3_client = boto3.client('s3')
            
            timestamp = int(time.time() * 1000)  # milliseconds
            s3_key = f"workflow-states/{owner_id}/{workflow_id}/segments/{segment_id}/branches/{timestamp}/all_branches.json"
            
            # Upload full branches array to S3
            branches_json = json.dumps(branches, default=str)
            s3_client.put_object(
                Bucket=self.state_bucket,
                Key=s3_key,
                Body=branches_json,
                ContentType='application/json'
            )
            
            branches_s3_path = f"s3://{self.state_bucket}/{s3_key}"
            branches_size_kb = len(branches_json) / 1024
            
            logger.info(f"[Pointer Strategy] Uploaded {len(branches)} branches ({branches_size_kb:.1f}KB) to {branches_s3_path}")

            # Create lightweight pointer array
            # Map Iterator receives each pointer and hydrates its branch data from S3
            branch_pointers = []
            for idx, branch in enumerate(branches):
                pointer = {
                    'branch_index': idx,
                    'branch_id': branch.get('id') or branch.get('branch_id') or f'branch_{idx}',
                    'branches_s3_path': branches_s3_path,
                    'total_branches': len(branches),
                    # Lightweight metadata only (minimum info needed before hydrate)
                    'segment_count': len(branch.get('partition_map', [])) if branch.get('partition_map') else 0,
                }
                branch_pointers.append(pointer)
            
            pointer_size = len(json.dumps(branch_pointers, default=str))
            logger.info(f"[Pointer Strategy] Created {len(branch_pointers)} pointers ({pointer_size/1024:.2f}KB) - "
                       f"Compression ratio: {branches_size_kb * 1024 / max(pointer_size, 1):.1f}x")
            
            return branch_pointers, branches_s3_path
            
        except Exception as e:
            logger.error(f"[Pointer Strategy] Failed to offload branches to S3: {e}")
            # Fallback: inline return (risky but necessary on S3 failure)
            return branches, None
    
    def _estimate_branch_resources(self, branch: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, int]:
        """
        Estimate expected resource requirements for a branch

        Returns:
            {
                'memory_mb': Estimated memory (MB),
                'tokens': Estimated token count,
                'llm_calls': Number of LLM calls,
                'has_shared_resource': Whether shared resources are accessed
            }
        """
        # [P0 Fix] Defend against None or non-dict branch
        if not branch or not isinstance(branch, dict):
            logger.warning(f"[Scheduler] [Warning] Invalid branch object in resource estimation: {type(branch)}")
            return {
                'memory_mb': DEFAULT_BRANCH_MEMORY_MB,
                'tokens': 0,
                'llm_calls': 0,
                'has_shared_resource': False
            }
        
        # [Critical Fix] Include hidden nodes for accurate token calculation
        all_nodes = branch.get('nodes', [])
        if not all_nodes and 'partition_map' in branch:
            # For partitioned branches, aggregate nodes from all segments
            for segment in branch.get('partition_map', []):
                if isinstance(segment, dict):
                    all_nodes.extend(segment.get('nodes', []))
        
        if not all_nodes:
            return {
                'memory_mb': DEFAULT_BRANCH_MEMORY_MB,
                'tokens': 0,
                'llm_calls': 0,
                'has_shared_resource': False
            }
        
        memory_mb = 50  # base overhead
        tokens = 0
        llm_calls = 0
        has_shared_resource = False
        
        for node in all_nodes:
            # [v3.8] None defense in nodes iteration
            if node is None or not isinstance(node, dict):
                continue
            node_type = node.get('type', '')
            config = node.get('config', {})
            
            # Memory estimation
            memory_mb += 10  # 10MB base per node

            if node_type in ('llm_chat', 'aiModel', 'llm', 'aimodel'):
                memory_mb += 50  # Additional memory for LLM nodes
                llm_calls += 1
                # Token estimation: based on prompt length
                prompt = config.get('prompt', '') or config.get('system_prompt', '') or config.get('prompt_template', '')
                tokens += len(prompt) // 4 + 500  # rough token estimate + expected response
                
            elif node_type == 'for_each':
                items_key = config.get('input_list_key', '')
                if items_key and items_key in state:
                    items = state.get(items_key, [])
                    if isinstance(items, list):
                        iteration_count = len(items)
                        memory_mb += iteration_count * 5
                        # Token explosion if LLM exists inside for_each
                        # [Fix] None defense: config['sub_node_config'] or config['sub_workflow'] may be None
                        sub_config = config.get('sub_node_config') or config.get('sub_workflow') or {}
                        sub_nodes = sub_config.get('nodes', []) if isinstance(sub_config, dict) else []
                        for sub_node in sub_nodes:
                            # [Fix] sub_node may be None
                            if sub_node and isinstance(sub_node, dict) and sub_node.get('type') in ('llm_chat', 'aiModel'):
                                # [Critical Fix] Multiply by iteration count for accurate token estimation
                                tokens += iteration_count * 5000  # 5000 tokens estimated per item
                                llm_calls += iteration_count
                                logger.debug(f"[Scheduler] for_each with LLM: {iteration_count} iterations × 5000 tokens = {iteration_count * 5000} tokens")
            
            # Detect shared resource access
            if node_type in ('db_write', 's3_write', 'api_call'):
                has_shared_resource = True
            if config.get('write_to_db') or config.get('write_to_s3'):
                has_shared_resource = True
        
        return {
            'memory_mb': memory_mb,
            'tokens': tokens,
            'llm_calls': llm_calls,
            'has_shared_resource': has_shared_resource
        }

    def _bin_pack_branches(
        self,
        branches: List[Dict[str, Any]],
        resource_estimates: List[Dict[str, int]],
        resource_policy: Dict[str, Any]
    ) -> List[List[Dict[str, Any]]]:
        """
        Bin Packing algorithm: group branches into execution batches

        Strategy:
        1. Place heavy branches first (First Fit Decreasing)
        2. Ensure each batch's total resources do not exceed limits
        3. Branches accessing shared resources go in separate batches

        Returns:
            [[batch1_branches], [batch2_branches], ...]
        """
        # [Fix] Use 'or' to handle None values - .get() returns None if key exists with None value
        max_memory = resource_policy.get('max_concurrent_memory_mb') or DEFAULT_MAX_CONCURRENT_MEMORY_MB
        max_tokens = resource_policy.get('max_concurrent_tokens') or DEFAULT_MAX_CONCURRENT_TOKENS
        max_branches = resource_policy.get('max_concurrent_branches') or DEFAULT_MAX_CONCURRENT_BRANCHES
        strategy = resource_policy.get('strategy') or STRATEGY_RESOURCE_OPTIMIZED
        
        # Combine branches with resource estimates and sort by size (descending)
        indexed_branches = list(zip(branches, resource_estimates, range(len(branches))))
        
        # Sort criteria by strategy
        if strategy == STRATEGY_COST_OPTIMIZED:
            # Highest tokens first (process costly tasks sequentially)
            indexed_branches.sort(key=lambda x: x[1]['tokens'], reverse=True)
        else:
            # Highest memory first (default)
            indexed_branches.sort(key=lambda x: x[1]['memory_mb'], reverse=True)
        
        # Separate branches with shared resource access
        shared_resource_branches = []
        normal_branches = []
        
        for branch, estimate, idx in indexed_branches:
            if estimate['has_shared_resource']:
                shared_resource_branches.append((branch, estimate, idx))
            else:
                normal_branches.append((branch, estimate, idx))
        
        # Bin Packing (First Fit Decreasing)
        batches: List[List[Tuple]] = []
        batch_resources: List[Dict[str, int]] = []
        
        for branch, estimate, idx in normal_branches:
            placed = False
            
            for i, batch in enumerate(batches):
                current = batch_resources[i]
                
                # Check if this batch can accommodate the branch
                new_memory = current['memory_mb'] + estimate['memory_mb']
                new_tokens = current['tokens'] + estimate['tokens']
                new_count = len(batch) + 1
                
                if (new_memory <= max_memory and 
                    new_tokens <= max_tokens and 
                    new_count <= max_branches):
                    
                    batch.append((branch, estimate, idx))
                    batch_resources[i] = {
                        'memory_mb': new_memory,
                        'tokens': new_tokens
                    }
                    placed = True
                    break
            
            if not placed:
                # Create new batch
                batches.append([(branch, estimate, idx)])
                batch_resources.append({
                    'memory_mb': estimate['memory_mb'],
                    'tokens': estimate['tokens']
                })
        
        # Shared resource branches go in separate batches (prevent Race Condition)
        for branch, estimate, idx in shared_resource_branches:
            batches.append([(branch, estimate, idx)])
            batch_resources.append({
                'memory_mb': estimate['memory_mb'],
                'tokens': estimate['tokens']
            })
        
        # Convert results: extract branches only
        result = []
        for batch in batches:
            result.append([item[0] for item in batch])
        
        return result

    def _schedule_parallel_group(
        self,
        segment_config: Dict[str, Any],
        state: Dict[str, Any],
        segment_id: int,
        owner_id: str = None,
        workflow_id: str = None
    ) -> Dict[str, Any]:
        """
        [Parallel] Parallel group scheduling: determine execution batches based on resource_policy

        [Pointer Strategy] S3 offloading for Map-internal Hydrate:
        - Upload full branches data to S3
        - Pass only lightweight pointer array to pending_branches (branch_index, branch_s3_path)
        - Each branch hydrates its own data from S3 path inside Map Iterator

        Returns:
            {
                'status': 'PARALLEL_GROUP' | 'SCHEDULED_PARALLEL',
                'branches': [...] (lightweight pointer array - includes S3 path),
                'branches_s3_path': S3 path (full branches data location),
                'execution_batches': [[...], [...]] (batch structure),
                'scheduling_metadata': {...}
            }
        """
        branches = segment_config.get('branches', [])
        resource_policy = segment_config.get('resource_policy', {})
        
        # Default parallel execution when no resource_policy
        if not resource_policy:
            logger.info(f"[Scheduler] No resource_policy, using default parallel execution for {len(branches)} branches")
            
            # [Pointer Strategy] Offload branches to S3
            branch_pointers, branches_s3_path = self._offload_branches_to_s3(
                branches=branches,
                owner_id=owner_id or 'unknown',
                workflow_id=workflow_id or 'unknown',
                segment_id=segment_id
            )

            return {
                'status': 'PARALLEL_GROUP',
                'branches': branch_pointers,  # Lightweight pointer array
                'branches_s3_path': branches_s3_path,  # S3 path
                'execution_batches': [branch_pointers],  # Single batch (pointers)
                'scheduling_metadata': {
                    'strategy': 'DEFAULT',
                    'total_branches': len(branches),
                    'batch_count': 1,
                    'pointer_strategy': True,
                    'branches_s3_path': branches_s3_path
                }
            }
        
        strategy = resource_policy.get('strategy', STRATEGY_RESOURCE_OPTIMIZED)
        
        # SPEED_OPTIMIZED: Maximum parallel execution after guardrail check
        if strategy == STRATEGY_SPEED_OPTIMIZED:
            # [Guard] Account-level hard limit check (prevent system panic)
            if len(branches) > ACCOUNT_LAMBDA_CONCURRENCY_LIMIT:
                logger.warning(f"[Scheduler] [Warning] SPEED_OPTIMIZED but branch count ({len(branches)}) "
                              f"exceeds account concurrency limit ({ACCOUNT_LAMBDA_CONCURRENCY_LIMIT})")
                # Apply hard limit with batch splitting
                forced_policy = {
                    'max_concurrent_branches': ACCOUNT_LAMBDA_CONCURRENCY_LIMIT,
                    'max_concurrent_memory_mb': ACCOUNT_MEMORY_HARD_LIMIT_MB,
                    'strategy': STRATEGY_SPEED_OPTIMIZED
                }
                # Resource estimation and batch splitting
                resource_estimates = [self._estimate_branch_resources(b, state) for b in branches]
                execution_batches = self._bin_pack_branches(branches, resource_estimates, forced_policy)
                
                logger.info(f"[Scheduler] [Guard] Guardrail applied: {len(execution_batches)} batches")
                
                # [Pointer Strategy] Offload branches to S3
                branch_pointers, branches_s3_path = self._offload_branches_to_s3(
                    branches=branches,
                    owner_id=owner_id or 'unknown',
                    workflow_id=workflow_id or 'unknown',
                    segment_id=segment_id
                )

                return {
                    'status': 'SCHEDULED_PARALLEL',
                    'branches': branch_pointers,  # Lightweight pointer array
                    'branches_s3_path': branches_s3_path,
                    'execution_batches': execution_batches,  # Original batch structure (for scheduling)
                    'scheduling_metadata': {
                        'strategy': strategy,
                        'total_branches': len(branches),
                        'batch_count': len(execution_batches),
                        'guardrail_applied': True,
                        'reason': 'Account concurrency limit exceeded',
                        'pointer_strategy': True,
                        'branches_s3_path': branches_s3_path
                    }
                }
            
            logger.info(f"[Scheduler] SPEED_OPTIMIZED: All {len(branches)} branches in parallel")
            
            # [Pointer Strategy] Offload branches to S3
            branch_pointers, branches_s3_path = self._offload_branches_to_s3(
                branches=branches,
                owner_id=owner_id or 'unknown',
                workflow_id=workflow_id or 'unknown',
                segment_id=segment_id
            )
            
            return {
                'status': 'PARALLEL_GROUP',
                'branches': branch_pointers,  # Lightweight pointer array
                'branches_s3_path': branches_s3_path,
                'execution_batches': [branch_pointers],
                'scheduling_metadata': {
                    'strategy': strategy,
                    'total_branches': len(branches),
                    'batch_count': 1,
                    'guardrail_applied': False,
                    'pointer_strategy': True,
                    'branches_s3_path': branches_s3_path
                }
            }
        
        # Resource estimation
        resource_estimates = []
        total_memory = 0
        total_tokens = 0
        
        for branch in branches:
            estimate = self._estimate_branch_resources(branch, state)
            resource_estimates.append(estimate)
            total_memory += estimate['memory_mb']
            total_tokens += estimate['tokens']
        
        logger.info(f"[Scheduler] Resource estimates: {total_memory}MB memory, {total_tokens} tokens, "
                   f"{len(branches)} branches")
        
        # Check limits - [Fix] Use 'or' to handle None values
        max_memory = resource_policy.get('max_concurrent_memory_mb') or DEFAULT_MAX_CONCURRENT_MEMORY_MB
        max_tokens = resource_policy.get('max_concurrent_tokens') or DEFAULT_MAX_CONCURRENT_TOKENS
        
        # Single batch if within limits
        if total_memory <= max_memory and total_tokens <= max_tokens:
            logger.info(f"[Scheduler] Resources within limits, single batch execution")
            
            # [Pointer Strategy] Offload branches to S3
            branch_pointers, branches_s3_path = self._offload_branches_to_s3(
                branches=branches,
                owner_id=owner_id or 'unknown',
                workflow_id=workflow_id or 'unknown',
                segment_id=segment_id
            )
            
            return {
                'status': 'PARALLEL_GROUP',
                'branches': branch_pointers,  # Lightweight pointer array
                'branches_s3_path': branches_s3_path,
                'execution_batches': [branch_pointers],
                'scheduling_metadata': {
                    'strategy': strategy,
                    'total_branches': len(branches),
                    'batch_count': 1,
                    'total_memory_mb': total_memory,
                    'total_tokens': total_tokens,
                    # [Guard] [v3.4] Deep Evidence Metrics
                    'total_tokens_calculated': total_tokens,
                    'actual_concurrency_limit': max_tokens,
                    'pointer_strategy': True,
                    'branches_s3_path': branches_s3_path
                }
            }
        
        # Create batches via Bin Packing
        execution_batches = self._bin_pack_branches(branches, resource_estimates, resource_policy)
        
        logger.info(f"[Scheduler] [System] Created {len(execution_batches)} execution batches from {len(branches)} branches")
        for i, batch in enumerate(execution_batches):
            batch_memory = sum(self._estimate_branch_resources(b, state)['memory_mb'] for b in batch)
            logger.info(f"[Scheduler]   Batch {i+1}: {len(batch)} branches, ~{batch_memory}MB")
        
        # [Pointer Strategy] Offload branches to S3
        branch_pointers, branches_s3_path = self._offload_branches_to_s3(
            branches=branches,
            owner_id=owner_id or 'unknown',
            workflow_id=workflow_id or 'unknown',
            segment_id=segment_id
        )
        
        return {
            'status': 'SCHEDULED_PARALLEL',
            'branches': branch_pointers,  # Lightweight pointer array
            'branches_s3_path': branches_s3_path,
            'execution_batches': execution_batches,  # Original batch structure (for scheduling)
            'scheduling_metadata': {
                'strategy': strategy,
                'total_branches': len(branches),
                'batch_count': len(execution_batches),
                'total_memory_mb': total_memory,
                'total_tokens': total_tokens,
                # [Guard] [v3.4] Deep Evidence Metrics
                'total_tokens_calculated': total_tokens,
                'actual_concurrency_limit': max_tokens,
                'resource_policy': resource_policy,
                'pointer_strategy': True,
                'branches_s3_path': branches_s3_path
            }
        }

    # ========================================================================
    # [Parallel] [Aggregator] Parallel branch result aggregation
    # ========================================================================
    def _handle_aggregator(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Aggregate parallel branch execution results into a single merged state

        Called from ASL's AggregateParallelResults:
        - parallel_results: Array of each branch's execution result
        - current_state: State before parallel execution
        - map_error: (optional) Error info on overall Map failure

        Returns:
            Merged final state + next segment info
        """
        # [v3.6] Ensure base_state integrity at aggregation start (Entrance to Aggregator)
        from src.common.statebag import ensure_state_bag
        
        # [v3.5 None Trace] Aggregator entry tracing
        _trace_none_access('current_state', 'event', event.get('current_state'), 
                           context=event, caller='Aggregator:Entry')
        _trace_none_access('parallel_results', 'event', event.get('parallel_results'), 
                           context=event, caller='Aggregator:Entry')
        
        # current_state even if null becomes StateBag({})
        current_state = ensure_state_bag(event.get('current_state', {}))
        
        # [Critical Fix] Restore current_state if S3 offloaded
        if isinstance(current_state, dict) and current_state.get('__s3_offloaded') is True:
            offloaded_s3_path = current_state.get('__s3_path')
            if offloaded_s3_path:
                logger.info(f"[Aggregator] current_state is S3 offloaded. Restoring from: {offloaded_s3_path}")
                try:
                    current_state = self.state_manager.download_state_from_s3(offloaded_s3_path)
                    current_state = ensure_state_bag(current_state)
                    logger.info(f"[Aggregator] Successfully restored current_state from S3 "
                               f"({len(json.dumps(current_state, ensure_ascii=False).encode('utf-8'))/1024:.1f}KB)")
                except Exception as e:
                    logger.error(f"[Aggregator] Failed to restore current_state from S3: {e}")
                    # Fallback to empty state
                    current_state = ensure_state_bag({})
        
        # Keep current_state as local variable only - DO NOT add to event
        
        parallel_results = event.get('parallel_results', [])
        base_state = current_state  # Use local variable instead of event.get
        segment_to_run = event.get('segment_to_run', 0)
        workflow_id = event.get('workflowId') or event.get('workflow_id')
        auth_user_id = event.get('ownerId') or event.get('owner_id')
        map_error = event.get('map_error')  # [Guard] Map overall error info
        
        logger.info(f"[Aggregator] [Parallel] Aggregating {len(parallel_results)} branch results"
                   + (f" (map_error present)" if map_error else ""))
        
        # [Optimization] Parallelize S3 Hydration (Solve N+1 Query problem)
        # Sequential download is timeout risk when many branches (50+)
        # [DEBUG] Log parallel_results structure before processing
        logger.info(f"[Aggregator] DEBUG: parallel_results type: {type(parallel_results)}")
        logger.info(f"[Aggregator] DEBUG: parallel_results length: {len(parallel_results) if parallel_results else 0}")
        if parallel_results:
            for idx, item in enumerate(parallel_results[:3]):  # Log first 3 items
                logger.info(f"[Aggregator] DEBUG: parallel_results[{idx}] keys: {list(item.keys()) if isinstance(item, dict) else 'NOT_DICT'}")
        
        # Parallel fetch via ThreadPoolExecutor
        branches_needing_s3 = []
        for i, result in enumerate(parallel_results):
            if not result or not isinstance(result, dict):
                logger.info(f"[Aggregator] DEBUG: Skipping parallel_results[{i}] - not a dict")
                continue
            
            # [Critical Fix] Unwrap Lambda invoke wrapper if present
            # Distributed Map State returns: {"Payload": {...}}
            logger.info(f"[Aggregator] DEBUG: parallel_results[{i}] has 'Payload' key: {'Payload' in result}")
            if 'Payload' in result and isinstance(result['Payload'], dict):
                logger.info(f"[Aggregator] DEBUG: Unwrapping Payload for parallel_results[{i}]")
                result = result['Payload']
                parallel_results[i] = result  # Update in-place for later processing
            
            branch_s3_path = result.get('final_state_s3_path') or result.get('state_s3_path')
            branch_state = result.get('final_state') or result.get('state') or {}
            
            # [Critical Fix] Also check __s3_offloaded flag
            is_offloaded = isinstance(branch_state, dict) and branch_state.get('__s3_offloaded') is True
            if is_offloaded:
                # S3 offloaded state: get actual path from __s3_path
                branch_s3_path = branch_state.get('__s3_path')
                logger.info(f"[Aggregator] Branch {i} has __s3_offloaded flag. S3 path: {branch_s3_path}")
            
            is_empty = isinstance(branch_state, dict) and len(branch_state) <= 1  # {} or {"__state_truncated": true}
            
            # S3 restoration needed: empty state OR offloaded flag
            if (is_empty or is_offloaded) and branch_s3_path:
                branches_needing_s3.append((i, branch_s3_path, result))
        
        # Execute parallel S3 fetch
        if branches_needing_s3:
            logger.info(f"[Aggregator] Parallel S3 fetch for {len(branches_needing_s3)} branches")
            
            def fetch_branch_s3(item: Tuple[int, str, Dict]) -> Tuple[int, Optional[Dict[str, Any]]]:
                idx, s3_path, result = item
                try:
                    bucket = s3_path.replace("s3://", "").split("/")[0]
                    key = "/".join(s3_path.replace("s3://", "").split("/")[1:])
                    obj = self.state_manager.s3_client.get_object(Bucket=bucket, Key=key)
                    content = obj['Body'].read().decode('utf-8')
                    state = self._safe_json_load(content)  # Use safe loader
                    return (idx, state)
                except Exception as e:
                    logger.error(f"[Aggregator] S3 recovery failed for branch {idx}: {e}")
                    return (idx, {})  # Return empty dict instead of None
            
            # Parallel execution (max 10 concurrent)
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(fetch_branch_s3, item): item for item in branches_needing_s3}
                
                # [v3.35] Deadline-aware timeout: use remaining Lambda time if available
                _pool_timeout = 60
                if self._deadline_ms:
                    _pool_timeout = max(10, (self._deadline_ms - time.time() * 1000) / 1000 - 10)
                for future in as_completed(futures, timeout=_pool_timeout):
                    try:
                        idx, state = future.result(timeout=5)  # Individual 5s timeout
                        if state:
                            # Inject hydrated state into original result
                            parallel_results[idx]['final_state'] = state
                            parallel_results[idx]['__hydrated_from_s3'] = True
                    except Exception as e:
                        item = futures[future]
                        logger.warning(f"[Aggregator] Future failed for branch {item[0]}: {e}")
            
            logger.info(f"[Aggregator] Parallel S3 fetch completed")
        
        # [Asymmetric Branch Handling] will be processed after partition_map is loaded
        # (Moved to line ~2400 to avoid undefined variable error)
        
        # 1. Merge all branch results (terminates_early branches are optional)
        aggregated_state = base_state.copy()
        all_history_logs = []
        branch_errors = []
        successful_branches = 0
        optional_branches_skipped = 0
        
        # [Guard] Record Map errors if present
        if map_error:
            branch_errors.append({
                'branch_id': '__MAP_ERROR__',
                'error': map_error
            })
            logger.warning(f"[Aggregator] [Warning] Map execution failed: {map_error}")
        
        for i, branch_result in enumerate(parallel_results):
            # 1. Null Guard (check at loop start)
            if branch_result is None or not isinstance(branch_result, dict):
                # [Asymmetric Branch] terminates_early branches are optional
                # This branch may have terminated with explicit END
                branch_id_temp = f'branch_{i}'
                if branch_id_temp in terminates_early_branches:
                    logger.info(
                        f"[Aggregator] Branch {branch_id_temp} (terminates_early) skipped - "
                        f"terminated with explicit END before aggregator."
                    )
                    optional_branches_skipped += 1
                    continue
                
                logger.error(f"[Aggregator] Branch {i} is None or invalid.")
                branch_errors.append({'branch_id': branch_id_temp, 'error': 'Null Result'})
                continue

            # 2. Extract context (inside loop)
            branch_id = branch_result.get('branch_id', f'branch_{i}')
            branch_status = branch_result.get('branch_status') or branch_result.get('status', 'UNKNOWN')
            
            # [Fix] Safely acquire branch_state inside loop
            # [Note] Parallel S3 fetch already completed and hydrated state injected
            branch_state = branch_result.get('final_state') or branch_result.get('state') or {}
            
            branch_logs = branch_result.get('new_history_logs', [])
            error_info = branch_result.get('error_info')
            
            # [Removed] Sequential S3 hydration logic removed (already handled in parallel)
            # Fallback attempted here only when parallel fetch failed
            if isinstance(branch_state, dict) and len(branch_state) == 0:
                branch_s3_path = branch_result.get('final_state_s3_path') or branch_result.get('state_s3_path')
                if branch_s3_path and not branch_result.get('__hydrated_from_s3'):
                    # Fallback on parallel fetch failure (sequential retry)
                    logger.warning(f"[Aggregator] Fallback: Sequential fetch for branch {branch_id}")
                    try:
                        bucket = branch_s3_path.replace("s3://", "").split("/")[0]
                        key = "/".join(branch_s3_path.replace("s3://", "").split("/")[1:])
                        obj = self.state_manager.s3_client.get_object(Bucket=bucket, Key=key)
                        content = obj['Body'].read().decode('utf-8')
                        branch_state = self._safe_json_load(content)  # Use safe loader
                    except Exception as e:
                        logger.error(f"[Aggregator] Fallback failed for branch {branch_id}: {e}")
                        branch_errors.append({
                            'branch_id': branch_id,
                            'error': f"S3 Hydration Failed: {str(e)}"
                        })
            
            # Execute actual state merge
            if isinstance(branch_state, dict):
                aggregated_state = self._merge_states(
                    aggregated_state,
                    branch_state,
                    merge_policy=MERGE_POLICY_APPEND_LIST
                )
                
                if branch_status in ('COMPLETE', 'SUCCEEDED', 'COMPLETED', 'CONTINUE'):
                    successful_branches += 1
            
            # Collect errors
            if error_info:
                branch_errors.append({
                    'branch_id': branch_id,
                    'error': error_info
                })
            
            # Collect history logs (Memory Safe Truncation)
            if isinstance(branch_logs, list):
                # [Guard] Prevent unlimited log growth from thousands of branches
                MAX_AGGREGATED_LOGS = 100
                current_log_count = len(all_history_logs)
                
                if current_log_count < MAX_AGGREGATED_LOGS:
                    remaining_slots = MAX_AGGREGATED_LOGS - current_log_count
                    if len(branch_logs) > remaining_slots:
                        all_history_logs.extend(branch_logs[:remaining_slots])
                        all_history_logs.append(f"[Aggregator] Logs truncated: exceeded limit of {MAX_AGGREGATED_LOGS} entries")
                    else:
                        all_history_logs.extend(branch_logs)
        
        # 2. Add aggregation metadata
        aggregated_state['__aggregator_metadata'] = {
            'total_branches': len(parallel_results),
            'successful_branches': successful_branches,
            'failed_branches': len(branch_errors),
            'optional_branches_skipped': optional_branches_skipped,  # terminates_early branch count
            'aggregated_at': time.time(),
            'logs_truncated': len(all_history_logs) >= 100 
        }
        
        if branch_errors:
            aggregated_state['__branch_errors'] = branch_errors
        
        # [Token Aggregation] Sum token usage across parallel branches
        from src.handlers.core.token_utils import extract_token_usage
        
        total_input_tokens = 0
        total_output_tokens = 0
        total_tokens = 0
        branch_token_details = []
        
        for branch_result in parallel_results:
            if not isinstance(branch_result, dict):
                continue
                
            branch_id = branch_result.get('branch_id', 'unknown')
            branch_state = branch_result.get('final_state') or branch_result.get('state') or {}
            
            # [DEBUG] Log token-related keys in branch state
            token_keys = [k for k in branch_state.keys() if 'token' in k.lower() or k == 'usage']
            logger.info(f"[Aggregator] [DEBUG] Branch {branch_id} token-related keys: {token_keys}")
            if 'usage' in branch_state:
                logger.info(f"[Aggregator] [DEBUG] Branch {branch_id} usage: {branch_state.get('usage')}")
            
            # Extract branch token usage
            usage = extract_token_usage(branch_state)
            input_tokens = usage['input_tokens']
            output_tokens = usage['output_tokens']
            branch_total = usage['total_tokens']
            logger.info(f"[Aggregator] [DEBUG] Branch {branch_id} extracted: {usage}")
            
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            total_tokens += branch_total
            
            branch_token_details.append({
                'branch_id': branch_id,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'total_tokens': branch_total
            })
        
        # Record aggregated token info in aggregated_state
        aggregated_state['total_input_tokens'] = total_input_tokens
        aggregated_state['total_output_tokens'] = total_output_tokens
        aggregated_state['total_tokens'] = total_tokens
        aggregated_state['branch_token_details'] = branch_token_details
        
        logger.info(f"[Aggregator] [Token Aggregation] {len(branch_token_details)} branches, "
                   f"total tokens: {total_tokens} ({total_input_tokens} input + {total_output_tokens} output)")
        
        # 2b. [v3.32] Merkle DAG manifest reconciliation for parallel branches.
        # Each branch wrote its own manifest chain independently.  The aggregator
        # creates a single "merge manifest" whose parent_manifest_ids list references
        # every branch's final manifest, forming a proper DAG join node.
        use_v3_state_saving = os.environ.get('USE_V3_STATE_SAVING', 'true').lower() == 'true'
        if use_v3_state_saving:
            try:
                from src.services.state.state_versioning_service import StateVersioningService
                s3_bucket_for_merkle = (
                    os.environ.get('WORKFLOW_STATE_BUCKET')
                    or os.environ.get('S3_BUCKET')
                    or os.environ.get('SKELETON_S3_BUCKET')
                )
                if s3_bucket_for_merkle:
                    versioning_svc = StateVersioningService(
                        dynamodb_table=os.environ.get('MANIFESTS_TABLE', 'StateManifestsV3'),
                        s3_bucket=s3_bucket_for_merkle,
                        use_2pc=True,
                        gc_dlq_url=os.environ.get('GC_DLQ_URL'),
                    )
                    # Collect branch manifest IDs for the DAG join
                    branch_manifest_ids: list[str] = []
                    for br in parallel_results:
                        if isinstance(br, dict):
                            mid = (
                                br.get('current_manifest_id')
                                or (br.get('final_state') or {}).get('current_manifest_id')
                            )
                            if mid:
                                branch_manifest_ids.append(mid)

                    # [H-1 FIX] Store parents as JSON array, not comma-joined string.
                    # A comma-joined string is unresolvable by parent-chain walkers.
                    merge_parent = (
                        json.dumps(branch_manifest_ids)
                        if branch_manifest_ids else None
                    )
                    merge_result = versioning_svc.save_state_delta(
                        delta=aggregated_state,
                        workflow_id=workflow_id,
                        execution_id=event.get('execution_id', 'unknown'),
                        owner_id=auth_user_id or event.get('ownerId', 'unknown'),
                        segment_id=segment_to_run,
                        previous_manifest_id=merge_parent,
                    )
                    merge_manifest_id = merge_result.get('manifest_id')
                    if merge_manifest_id:
                        aggregated_state['current_manifest_id'] = merge_manifest_id
                        logger.info(
                            f"[Aggregator] [v3.32] Merge manifest created: {merge_manifest_id[:16]}... "
                            f"parents={len(branch_manifest_ids)}"
                        )
            except Exception as e:
                # [BUG-5 FIX] On failure, preserve the best available manifest ID
                # from any branch so the next segment has a non-None parent.
                logger.warning(f"[Aggregator] [v3.32] Merkle reconciliation failed: {e}")
                if 'branch_manifest_ids' in dir() and branch_manifest_ids:
                    aggregated_state.setdefault(
                        'current_manifest_id', branch_manifest_ids[-1]
                    )

        # 3. Save state (including S3 offloading)
        s3_bucket_raw = os.environ.get("S3_BUCKET") or os.environ.get("SKELETON_S3_BUCKET") or ""
        s3_bucket = s3_bucket_raw.strip() if s3_bucket_raw else None
        
        if not s3_bucket:
            logger.error("[Alert] [CRITICAL] S3_BUCKET/SKELETON_S3_BUCKET not set for aggregation!")
        
        # [Fix] Aggregator uses a more conservative threshold to prevent data loss
        # [Update] Each branch is limited to 50KB, so aggregator uses 120KB threshold
        # e.g.: 3 branches x 50KB = 150KB -> triggers S3 offload
        # Note: If branches return only S3 references, aggregator input is small,
        #       but merged result after hydration can be large
        AGGREGATOR_SAFE_THRESHOLD = 120000  # 120KB (47% of 256KB limit)
        
        # [Critical] Measure merged state size (before S3 offload decision)
        aggregated_size = len(json.dumps(aggregated_state, ensure_ascii=False).encode('utf-8'))
        logger.info(f"[Aggregator] Merged state size: {aggregated_size/1024:.1f}KB, "
                   f"threshold: {AGGREGATOR_SAFE_THRESHOLD/1024:.0f}KB")
        
        final_state, output_s3_path = self.state_manager.handle_state_storage(
            state=aggregated_state,
            auth_user_id=auth_user_id,
            workflow_id=workflow_id,
            segment_id=segment_to_run,
            bucket=s3_bucket,
            threshold=AGGREGATOR_SAFE_THRESHOLD  # Hardened threshold
        )
        
        # [Critical] Validate response payload size (Step Functions 256KB limit)
        response_size = len(json.dumps(final_state, ensure_ascii=False).encode('utf-8')) if final_state else 0
        logger.info(f"[Aggregator] Response payload size: {response_size/1024:.1f}KB "
                   f"(S3: {'YES - ' + output_s3_path if output_s3_path else 'NO'})")
        
        if response_size > 250000:  # 250KB warning
            logger.warning(f"[Aggregator] [Alert] Response payload exceeds 250KB! "
                          f"This may fail Step Functions state transition. Size: {response_size/1024:.1f}KB")
        
        
        # 4. Determine next segment
        # After aggregator, workflow typically completes,
        # but check next_segment from partition_map
        partition_map = event.get('partition_map', [])
        total_segments = _safe_get_total_segments(event)
        next_segment = segment_to_run + 1
        
        # [Asymmetric Branch Handling] Handle terminates_early branches
        # Check terminates_early flag set by partition_service
        # Treat asymmetric branches (one END, other joins aggregator) as optional
        terminates_early_branches = {}
        if segment_to_run < len(partition_map):
            current_seg = partition_map[segment_to_run]
            if current_seg and current_seg.get('type') == 'aggregator':
                # Find the source parallel_group segment
                source_p_seg_id = current_seg.get('source_parallel_group')
                if source_p_seg_id is not None and source_p_seg_id < len(partition_map):
                    source_p_seg = partition_map[source_p_seg_id]
                    if source_p_seg and source_p_seg.get('type') == 'parallel_group':
                        branches_meta = source_p_seg.get('branches', [])
                        terminates_early_branches = {
                            b.get('branch_id'): b 
                            for b in branches_meta 
                            if b.get('terminates_early', False)
                        }
                        
                        if terminates_early_branches:
                            logger.warning(
                                f"[Aggregator] Asymmetric branch termination detected: "
                                f"{len(terminates_early_branches)} branches terminate early. "
                                f"These will be treated as OPTIONAL to prevent Wait-for-all deadlock."
                            )
        
        # [v3.30 Fix] HITP Edge Detection using segment_config.outgoing_edges
        hitp_detected = False
        agg_segment_config = event.get('segment_config')
        if not agg_segment_config:
            manifest_s3_path = event.get('segment_manifest_s3_path')
            if manifest_s3_path:
                try:
                    agg_segment_config = self._load_segment_config_from_manifest(manifest_s3_path, segment_to_run)
                except Exception as e:
                    # [v3.34] Was silent — HITP edge detection skipped when this fails
                    logger.warning(
                        "[Aggregator] Failed to load segment config from manifest "
                        "(HITP edge detection may be skipped): %s", e
                    )
                    agg_segment_config = None

        if next_segment < total_segments and agg_segment_config:
            edge_info = check_inter_segment_edges(agg_segment_config)
            if is_hitp_edge(edge_info):
                hitp_detected = True
                logger.info(f"[Aggregator] HITP edge detected via segment_config.outgoing_edges: "
                          f"segment {segment_to_run} → {next_segment}, "
                          f"type={edge_info.get('edge_type')}, target={edge_info.get('target_node')}")
        
        # Determine completion
        is_complete = next_segment >= total_segments
        
        logger.info(f"[Aggregator] [Success] Aggregation complete: "
                   f"{successful_branches}/{len(parallel_results)} branches succeeded"
                   f"{f', {optional_branches_skipped} optional branches skipped (terminates_early)' if optional_branches_skipped > 0 else ''}, "
                   f"next_segment={next_segment if not is_complete else 'COMPLETE'}, "
                   f"hitp_detected={hitp_detected}")
        
        # [Critical] Clean up S3 intermediate files (Garbage Collection)
        # Delete temporary result files stored in S3 by each branch
        # No longer needed after merge (reduces cost & management overhead)
        self._cleanup_branch_intermediate_s3(parallel_results, workflow_id, segment_to_run)
        
        # [P0 Refactoring] S3 offload via helper function (DRY principle)
        response_final_state = prepare_response_with_offload(final_state, output_s3_path)
        # [Critical Fix] Safe check - response_final_state is guaranteed non-None by prepare_response_with_offload
        if response_final_state and response_final_state.get('__s3_offloaded'):
            logger.info(f"[Aggregator] [S3 Offload] Replaced final_state with metadata reference. "
                       f"Original: {response_final_state.get('__original_size_kb', 0):.1f}KB → Response: ~0.2KB")
        
        
        # [Guard] [Fix] Handle HITP Detection
        # If HITP edge detected, pause immediately before proceeding to next segment
        if hitp_detected and not is_complete:
            logger.info(f"[Aggregator] Pausing execution due to HITP edge. Next segment: {next_segment}")
            return {
                "status": "PAUSED_FOR_HITP",
                "final_state": response_final_state,
                "final_state_s3_path": output_s3_path,
                "current_state": response_final_state,
                "state_s3_path": output_s3_path,
                "next_segment_to_run": next_segment,
                "new_history_logs": all_history_logs,
                "error_info": None,
                "branches": [],
                "segment_type": "hitp_pause",
                "segment_id": segment_to_run,
                "total_segments": total_segments,
                "aggregator_metadata": {
                    'total_branches': len(parallel_results),
                    'successful_branches': successful_branches,
                    'failed_branches': len(branch_errors),
                    'hitp_edge_detected': True
                }
            }
        
        # [Guard] [Fix] Handle Map Error (Loop Limit Exceeded, etc.)
        # If Map failed, we should PAUSE to allow human intervention or analysis
        if map_error:
            logger.warning(f"[Aggregator] Map Error detected. Forcing PAUSE status. Error: {map_error}")
            return {
                "status": "PAUSE",  # Force PAUSE for Map Errors
                "final_state": response_final_state,
                "final_state_s3_path": output_s3_path,
                "current_state": response_final_state,
                "state_s3_path": output_s3_path,
                "next_segment_to_run": segment_to_run, # Retry same segment or let human decide
                "new_history_logs": all_history_logs,
                "error_info": {
                    "error": "MapExecutionFailed",
                    "cause": map_error,
                    "branch_errors": branch_errors
                },
                "branches": [],
                "segment_type": "aggregator",
                "segment_id": segment_to_run,
                "total_segments": total_segments,
                "aggregator_metadata": {
                    'total_branches': len(parallel_results),
                    'successful_branches': successful_branches,
                    'failed_branches': len(branch_errors),
                    'map_error': True
                }
            }

        # [Guard] [v3.9] Core aggregator response
        # ASL passthrough fields are auto-injected in _finalize_response
        return {
            # Core execution result
            "status": "COMPLETE" if is_complete else "CONTINUE",
            "final_state": response_final_state,
            "final_state_s3_path": output_s3_path,
            "current_state": response_final_state,  # [P0 Fix] current_state also changed to S3 pointer (remove duplicate data)
            "state_s3_path": output_s3_path,  # Alias for ASL compatibility
            "next_segment_to_run": None if is_complete else next_segment,
            "new_history_logs": all_history_logs,
            "error_info": branch_errors if branch_errors else None,
            "branches": [],  # [P0 Fix] Changed from None to empty array (ASL Map compatibility)
            "segment_type": "aggregator",
            "segment_id": segment_to_run,
            "total_segments": total_segments,
            
            # Aggregator specific metadata
            "aggregator_metadata": {
                'total_branches': len(parallel_results),
                'successful_branches': successful_branches,
                'failed_branches': len(branch_errors)
            }
        }

    def _trigger_child_workflow(self, event: Dict[str, Any], branch_config: Dict[str, Any], auth_user_id: str, quota_id: str) -> Optional[Dict[str, Any]]:
        """
        Triggers a Child Step Function (Standard Orchestrator) for complex branches.
        "Fire and Forget" pattern to avoid Lambda timeouts.
        """
        try:
            import boto3
            import time
            
            sfn_client = boto3.client('stepfunctions')
            
            # 1. Resolve Orchestrator ARN
            orchestrator_arn = os.environ.get('WORKFLOW_ORCHESTRATOR_ARN')
            if not orchestrator_arn:
                logger.error("WORKFLOW_ORCHESTRATOR_ARN not set. Cannot trigger child workflow.")
                return None

            # 2. Construct Payload (Full 23 fields injection)
            payload = event.copy()
            payload['workflow_config'] = branch_config
            
            parent_workflow_id = payload.get('workflowId') or payload.get('workflow_id', 'unknown')
            parent_idempotency_key = payload.get('idempotency_key', str(time.time()))
            
            # 3. Generate Child Idempotency Key
            branch_id = branch_config.get('id') or f"branch_{int(time.time()*1000)}"
            child_idempotency_key = f"{parent_idempotency_key}_{branch_id}"[:80]
            
            payload['idempotency_key'] = child_idempotency_key
            payload['parent_workflow_id'] = parent_workflow_id
            
            # 4. Start Execution with retry
            safe_exec_name = "".join(c for c in child_idempotency_key if c.isalnum() or c in "-_")
            
            logger.info(f"Triggering Child SFN: {safe_exec_name}")
            
            # [v2.1] Apply retry to Step Functions start_execution
            def _start_child_execution():
                return sfn_client.start_execution(
                    stateMachineArn=orchestrator_arn,
                    name=safe_exec_name,
                    input=json.dumps(payload)
                )
            
            if RETRY_UTILS_AVAILABLE:
                response = retry_call(
                    _start_child_execution,
                    max_retries=2,
                    base_delay=0.5,
                    max_delay=5.0
                )
            else:
                response = _start_child_execution()
            
            return {
                "status": "ASYNC_CHILD_WORKFLOW_STARTED",
                "executionArn": response['executionArn'],
                "startDate": response['startDate'].isoformat(),
                "executionName": safe_exec_name
            }
            
        except Exception as e:
            logger.error(f"Failed to trigger child workflow: {e}")
            return None

    # ========================================================================
    # [Guard] [v2.2] Ring Protection: Prompt security verification
    # ========================================================================
    def _apply_ring_protection(
        self,
        segment_config: Dict[str, Any],
        initial_state: Dict[str, Any],
        segment_id: int,
        workflow_id: str
    ) -> List[Dict[str, Any]]:
        """
        [Guard] Ring Protection: Prompt security verification within segment

        Validates all LLM node prompts for:
        1. Prompt Injection pattern detection
        2. Ring 0 tag forgery attempt detection
        3. Dangerous tool direct access attempt detection

        Args:
            segment_config: Segment configuration
            initial_state: Initial state
            segment_id: Segment ID
            workflow_id: Workflow ID

        Returns:
            List of security violations (empty list means safe)
        """
        violations = []
        
        if not self.security_guard or not RING_PROTECTION_AVAILABLE:
            return violations
        
        nodes = segment_config.get('nodes', [])
        if not nodes:
            return violations
        
        context = {
            'workflow_id': workflow_id,
            'segment_id': segment_id
        }
        
        # [Time Machine] Extract _auto_fix_instructions and immediately remove from state
        # Inject only into the first LLM node and clear from state -> prevent downstream segment contamination
        # NOTE: SmartStateBag does not override pop(), so explicit del is required
        #       so that _deleted_fields is correctly tracked for DynamoDB delta write.
        auto_fix = None
        rollback_ctx = {}
        if initial_state:
            if '_auto_fix_instructions' in initial_state:
                auto_fix = initial_state['_auto_fix_instructions']
                del initial_state['_auto_fix_instructions']
            if '_rollback_context' in initial_state:
                ctx = initial_state['_rollback_context']
                rollback_ctx = ctx if isinstance(ctx, dict) else {}
                del initial_state['_rollback_context']
        auto_fix_injected = False  # Inject into first LLM node only

        for node in nodes:
            # [v3.8] None defense in nodes iteration
            if node is None or not isinstance(node, dict):
                continue
            node_id = node.get('id', 'unknown')
            node_type = node.get('type', '')
            config = node.get('config', {})

            # Validate LLM node prompts
            if node_type in ('llm_chat', 'aiModel', 'llm'):
                prompt = config.get('prompt_content') or config.get('prompt') or ''
                system_prompt = config.get('system_prompt', '')

                # [Time Machine] Inject _auto_fix_instructions (first LLM node only)
                if auto_fix and not auto_fix_injected:
                    fix_header = (
                        f"\n\n[AUTO-FIX CONTEXT - Applied by Time Machine]\n"
                        f"{auto_fix}\n"
                    )
                    if rollback_ctx.get('original_error'):
                        fix_header += f"Original error: {rollback_ctx['original_error']}\n"
                    if rollback_ctx.get('fix_strategy'):
                        fix_header += f"Fix strategy: {rollback_ctx['fix_strategy']}\n"
                    fix_header += (
                        "Follow the above instructions to correct previous mistakes.\n"
                        "[END AUTO-FIX]\n"
                    )
                    system_prompt = fix_header + system_prompt
                    config['system_prompt'] = system_prompt
                    auto_fix_injected = True
                    logger.info(f"[Time Machine] Auto-Fix instructions injected into node {node_id}")

                # Validate prompts
                for prompt_type, prompt_content in [('prompt', prompt), ('system_prompt', system_prompt)]:
                    if prompt_content:
                        result = self.security_guard.validate_prompt(
                            content=prompt_content,
                            ring_level=RingLevel.RING_3_USER,
                            context={**context, 'node_id': node_id, 'prompt_type': prompt_type}
                        )
                        
                        if not result.is_safe:
                            for v in result.violations:
                                violations.append({
                                    'node_id': node_id,
                                    'violation_type': v.violation_type.value,
                                    'severity': v.severity,
                                    'message': v.message,
                                    'should_sigkill': result.should_sigkill
                                })
                            
                            # Sanitize prompt (in-place)
                            if result.sanitized_content:
                                if prompt_type == 'prompt':
                                    config['prompt_content'] = result.sanitized_content
                                    config['prompt'] = result.sanitized_content
                                else:
                                    config['system_prompt'] = result.sanitized_content
                                logger.info(f"[Ring Protection] [Guard] Sanitized {prompt_type} in node {node_id}")
            
            # Validate dangerous tool access
            if node_type in ('tool', 'api_call', 'operator'):
                tool_name = config.get('tool') or config.get('method') or node_type
                allowed, violation = self.security_guard.check_tool_permission(
                    tool_name=tool_name,
                    ring_level=RingLevel.RING_3_USER,
                    context={**context, 'node_id': node_id}
                )
                
                if not allowed and violation:
                    violations.append({
                        'node_id': node_id,
                        'violation_type': violation.violation_type.value,
                        'severity': violation.severity,
                        'message': violation.message,
                        'should_sigkill': False  # Tool access is warning only
                    })
        
        if violations:
            logger.warning(f"[Ring Protection] [Warning] {len(violations)} security violations detected in segment {segment_id}")
        
        return violations

    # ========================================================================
    # [Guard] [Kernel Defense] Aggressive Retry Helper
    # ========================================================================
    def _is_retryable_error(self, error: Exception) -> bool:
        """
        Determine if an error is retryable
        """
        error_str = str(error)
        error_type = type(error).__name__
        
        for pattern in RETRYABLE_ERROR_PATTERNS:
            if pattern in error_str or pattern in error_type:
                return True
        
        # Check Boto3 ClientError
        if hasattr(error, 'response'):
            # [Fix] None defense: error.response['Error'] may be None
            error_code = (error.response.get('Error') or {}).get('Code', '')
            for pattern in RETRYABLE_ERROR_PATTERNS:
                if pattern in error_code:
                    return True
        
        return False

    def _execute_with_kernel_retry(
        self,
        segment_config: Dict[str, Any],
        initial_state: Dict[str, Any],
        auth_user_id: str,
        event: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        [Guard] Kernel-level aggressive retry

        Attempt resolution inside Lambda before Step Functions-level retry.
        - Retry on network errors, transient service failures
        - Exponential backoff + jitter applied

        Returns:
            (result_state, error_info) - error_info is None on success
        """
        last_error = None
        retry_history = []
        
        # Check if this is a parallel branch execution (for token aggregation)
        is_parallel_branch = event.get('branch_config') is not None
        
        for attempt in range(KERNEL_MAX_RETRIES + 1):
            try:
                # Check if kernel dynamic splitting is enabled
                enable_kernel_split = os.environ.get('ENABLE_KERNEL_SPLIT', 'true').lower() == 'true'
                
                if enable_kernel_split and isinstance(segment_config, dict):
                    # [Guard] [Pattern 1] Auto-split execution
                    result_state = self._execute_with_auto_split(
                        segment_config=segment_config,
                        initial_state=initial_state,
                        auth_user_id=auth_user_id,
                        split_depth=segment_config.get('_split_depth', 0)
                    )
                else:
                    # Existing logic: direct execution
                    logger.info(f"[v3.27 Debug] Calling run_workflow with segment_config: "
                               f"nodes={len(segment_config.get('nodes', []))}, "
                               f"node_ids={[n.get('id') for n in segment_config.get('nodes', [])]}")
                    logger.error(f"[v3.27 DEBUG] Calling run_workflow with segment_config keys: {list(segment_config.keys())[:15] if isinstance(segment_config, dict) else 'NOT A DICT'}")
                    logger.error(f"[v3.27 DEBUG] segment_config.nodes count: {len(segment_config.get('nodes', [])) if isinstance(segment_config, dict) else 'N/A'}")
                    # [C-02 FIX] Lazy import: prevent circular imports (removed from top-level)
                    from src.handlers.core.main import run_workflow
                    result_state = run_workflow(
                        config_json=segment_config,
                        initial_state=initial_state,
                        ddb_table_name=os.environ.get("JOB_TABLE"),
                        user_api_keys={},
                        run_config={"user_id": auth_user_id}
                    )
                    logger.error(f"[v3.27 DEBUG] run_workflow returned result_state keys: {list(result_state.keys())[:20] if isinstance(result_state, dict) else 'NOT A DICT'}")
                    if isinstance(result_state, dict) and 'llm_raw_output' in result_state:
                        logger.error(f"[v3.27 DEBUG] llm_raw_output FOUND in result_state!")
                    else:
                        logger.error(f"[v3.27 DEBUG] llm_raw_output NOT FOUND in result_state")
                    logger.info(f"[v3.27 Debug] run_workflow returned state with keys: "
                               f"{list(result_state.keys() if isinstance(result_state, dict) else [])[: 15]}")
                
                # [Guard] [v3.6] Immortal Kernel: Node Result Normalization
                from src.common.statebag import ensure_state_bag
                result_state = ensure_state_bag(result_state)
                
                # Check for empty result (Context Loss)
                # If run_workflow returns empty, it means we lost all state -> Revert to initial
                if not result_state: 
                     logger.error(f"[Kernel] [Alert] Execution yielded empty/null state! Context lost. Reverting to initial_state. Segment: {segment_config.get('id')}")
                     result_state = ensure_state_bag(initial_state)
                     result_state['__execution_null_recovered'] = True
                     # Note: We prefer keep-alive over crash here.
                
                # [Token Aggregation] ASL Parallel Branch Support
                # For parallel branches, extract and accumulate token usage from the branch execution result
                # This ensures _handle_aggregator can extract tokens from branch final_states
                if is_parallel_branch:
                    try:
                        # Extract accumulated token usage by directly searching result_state for token data
                        total_input_tokens = 0
                        total_output_tokens = 0
                        total_tokens = 0
                        
                        def extract_tokens_from_dict(data, prefix=""):
                            """Recursively extract token values from nested dictionaries"""
                            nonlocal total_input_tokens, total_output_tokens, total_tokens
                            
                            if isinstance(data, dict):
                                for key, value in data.items():
                                    full_key = f"{prefix}.{key}" if prefix else key
                                    
                                    if key in ['input_tokens', 'total_input_tokens'] or 'input_tokens' in key:
                                        if isinstance(value, (int, float)):
                                            total_input_tokens += int(value)
                                    elif key in ['output_tokens', 'total_output_tokens'] or 'output_tokens' in key:
                                        if isinstance(value, (int, float)):
                                            total_output_tokens += int(value)
                                    elif key in ['total_tokens'] or 'total_tokens' in key:
                                        if isinstance(value, (int, float)):
                                            total_tokens += int(value)
                                    elif isinstance(value, dict):
                                        # Recursively search nested dictionaries
                                        extract_tokens_from_dict(value, full_key)
                            
                            elif isinstance(data, (int, float)) and 'token' in prefix.lower():
                                # Handle direct token values in keys containing 'token'
                                if 'input' in prefix.lower():
                                    total_input_tokens += int(data)
                                elif 'output' in prefix.lower():
                                    total_output_tokens += int(data)
                                else:
                                    total_tokens += int(data)
                        
                        # Search through all keys in result_state
                        for key, value in result_state.items():
                            extract_tokens_from_dict({key: value})
                        
                        if total_tokens > 0 or total_input_tokens > 0 or total_output_tokens > 0:
                            # Ensure total_tokens is calculated if not directly found
                            if total_tokens == 0 and (total_input_tokens > 0 or total_output_tokens > 0):
                                total_tokens = total_input_tokens + total_output_tokens
                            
                            # Store accumulated usage in final_state for aggregator to extract
                            result_state['usage'] = {
                                'input_tokens': total_input_tokens,
                                'output_tokens': total_output_tokens,
                                'total_tokens': total_tokens
                            }
                            result_state['total_input_tokens'] = total_input_tokens
                            result_state['total_output_tokens'] = total_output_tokens
                            result_state['total_tokens'] = total_tokens
                            logger.info(f"[Parallel Branch] Accumulated token usage in final_state: {total_tokens} tokens ({total_input_tokens} input + {total_output_tokens} output)")
                        else:
                            logger.debug(f"[Parallel Branch] No token usage found in branch result")
                    except Exception as e:
                        logger.warning(f"[Parallel Branch] Failed to extract token usage: {e}")
                
                # Success
                if attempt > 0:
                    logger.info(f"[Kernel Retry] [Success] Succeeded after {attempt} retries")
                    # Record retry history
                    if isinstance(result_state, dict):
                        result_state['__kernel_retry_history'] = retry_history
                
                return result_state, None
                
            except Exception as e:
                last_error = e
                retry_info = {
                    'attempt': attempt + 1,
                    'error': str(e),
                    'error_type': type(e).__name__,
                    'timestamp': time.time(),
                    'retryable': self._is_retryable_error(e)
                }
                retry_history.append(retry_info)
                
                if attempt < KERNEL_MAX_RETRIES and self._is_retryable_error(e):
                    # Exponential backoff + jitter
                    delay = KERNEL_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"[Kernel Retry] [Warning] Attempt {attempt + 1}/{KERNEL_MAX_RETRIES + 1} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
                else:
                    # Non-retryable error or max retries reached
                    logger.error(
                        f"[Kernel Retry] All {attempt + 1} attempts failed. "
                        f"Last error: {e}"
                    )
                    break
        
        # All retries exhausted - return error info
        error_info = {
            'error': str(last_error),
            'error_type': type(last_error).__name__,
            'retry_attempts': len(retry_history),
            'retry_history': retry_history,
            'retryable': self._is_retryable_error(last_error) if last_error else False
        }
        
        return initial_state, error_info

    # ── REACT Segment Execution ───────────────────────────────────────────────

    def _execute_react_segment(
        self,
        segment_config: Dict[str, Any],
        initial_state: Dict[str, Any],
        event: Dict[str, Any],
        execution_id: str,
        owner_id: str,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        Execute a REACT-type segment using ReactExecutor with full VSM governance.

        Mirrors the pattern from worker_server.py:execute_segment_task() but runs
        inside Lambda. Wall-clock timeout (240s default) reserves 60s for
        seal_state_bag + S3 offload + response serialization.

        Returns:
            (result_state, error_info_or_None) — compatible with _execute_with_kernel_retry
        """
        if not REACT_EXECUTOR_AVAILABLE:
            return initial_state, {
                'error': 'ReactExecutor not available in Lambda environment',
                'error_type': 'ImportError',
                'retry_attempts': 0,
            }

        # Extract config from segment_config or workflow_config
        react_config = {}
        if isinstance(segment_config, dict):
            react_config = segment_config.get('react_executor', {})
        if not react_config:
            workflow_config = initial_state.get('workflow_config', {})
            if isinstance(workflow_config, dict):
                react_config = workflow_config.get('react_executor', {})

        max_iterations = min(react_config.get('max_iterations', 10), 50)
        token_budget = react_config.get('token_budget', 500_000)
        ring_level = react_config.get('ring_level', 2)
        wall_clock_timeout = react_config.get('wall_clock_timeout', 240.0)
        tool_timeout = react_config.get('tool_timeout', 30.0)
        task_prompt = initial_state.get(
            'task_prompt',
            initial_state.get('prompt', 'Execute the workflow segment.'),
        )

        vsm_endpoint = os.environ.get("ANALEMMA_KERNEL_ENDPOINT")
        if not vsm_endpoint:
            raise ValueError(
                "ANALEMMA_KERNEL_ENDPOINT environment variable is required for ReactExecutor. "
                "Set it to the VSM Lambda Function URL."
            )

        try:
            bridge = _ReactBridge(
                workflow_id=f"lambda_{execution_id}",
                ring_level=ring_level,
                kernel_endpoint=vsm_endpoint,
                mode="strict",
            )

            model_id = react_config.get('model_id', 'apac.anthropic.claude-sonnet-4-20250514-v1:0')

            executor = _ReactExecutor(
                bridge=bridge,
                model_id=model_id,
                max_iterations=max_iterations,
                token_budget=token_budget,
                wall_clock_timeout=wall_clock_timeout,
                tool_timeout=tool_timeout,
            )

            # Register default tools (same as worker_server.py)
            executor.add_tool(
                name="read_only",
                description="Echo text back (read-only tool)",
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "Text to echo"}},
                    "required": ["text"],
                },
                handler=lambda p: p.get("text", "echo: no text provided"),
                bridge_action="read_only",
            )
            executor.add_tool(
                name="basic_query",
                description="Perform simple arithmetic (a op b)",
                input_schema={
                    "type": "object",
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                        "operation": {"type": "string", "enum": ["add", "subtract", "multiply"]},
                    },
                    "required": ["a", "b"],
                },
                handler=lambda p: {
                    "result": {"add": p.get("a", 0) + p.get("b", 0),
                               "subtract": p.get("a", 0) - p.get("b", 0),
                               "multiply": p.get("a", 0) * p.get("b", 0),
                               }.get(p.get("operation", "add"), p.get("a", 0) + p.get("b", 0)),
                    "operation": p.get("operation", "add"),
                },
                bridge_action="basic_query",
            )

            # Register custom tools from config
            custom_tools = react_config.get('tools', [])
            for tool_def in custom_tools:
                if isinstance(tool_def, dict) and 'name' in tool_def:
                    executor.add_tool(
                        name=tool_def["name"],
                        description=tool_def.get("description", ""),
                        input_schema=tool_def.get("input_schema", {"type": "object", "properties": {}}),
                        handler=lambda p, desc=tool_def.get("description", ""): f"[stub] {desc}: {p}",
                        bridge_action=tool_def.get("bridge_action", tool_def["name"]),
                    )

            logger.info(
                "[ReactExecutor] Starting: execution_id=%s, max_iter=%d, "
                "wall_clock=%.0fs, ring=%d, task=%s",
                execution_id, max_iterations, wall_clock_timeout, ring_level,
                task_prompt[:100],
            )

            result = executor.run(task_prompt)

            # Build result_state compatible with _execute_with_kernel_retry output
            status = "COMPLETE" if result.stop_reason == "end_turn" else result.stop_reason.upper()

            result_state = dict(initial_state)
            result_state.update({
                "status": status,
                "_status": status,
                "react_result": {
                    "final_answer": result.final_answer,
                    "iterations": result.iterations,
                    "stop_reason": result.stop_reason,
                    "segments": result.segments,
                    "total_input_tokens": result.total_input_tokens,
                    "total_output_tokens": result.total_output_tokens,
                },
                "llm_raw_output": result.final_answer,
                "total_tokens": result.total_input_tokens + result.total_output_tokens,
                "usage": {
                    "input_tokens": result.total_input_tokens,
                    "output_tokens": result.total_output_tokens,
                    "total_tokens": result.total_input_tokens + result.total_output_tokens,
                },
                # Lambda-internal execution evidence marker
                "__react_execution_context": "lambda_internal",
            })

            logger.info(
                "[ReactExecutor] Completed: stop_reason=%s, iterations=%d, tokens=%d",
                result.stop_reason, result.iterations,
                result.total_input_tokens + result.total_output_tokens,
            )

            return result_state, None

        except Exception as e:
            logger.error("[ReactExecutor] Execution failed: %s", e, exc_info=True)
            return initial_state, {
                'error': str(e),
                'error_type': type(e).__name__,
                'retry_attempts': 0,
            }

    def execute_segment(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main execution logic for a workflow segment with StateHydrator integration.
        """
        # [v3.4] NULL Event Pre-Check (before hydration)
        if event is None:
            logger.error("[CRITICAL] execute_segment received None event!")
            return {
                "status": "FAILED",
                "error": "Event is None before hydration",
                "error_type": "NullEventError",
                "final_state": {},
                "new_history_logs": [],
                "segment_type": "ERROR",
                "total_segments": 1,
                "segment_id": 0
            }
        
        # [v3.35] Lambda Timeout Watchdog: check deadline before hydration
        self._check_deadline("hydration")

        # [v3.11] Unified State Hydration (Input)
        # Hydrate the event (convert to SmartStateBag) using pre-initialized hydrator
        # This handles "__s3_offloaded" restoration automatically
        event = self.hydrator.hydrate(event)
        
        # [v3.4] Hydration Result Validation
        # hydrator.hydrate() may return None if S3 load fails or input is malformed
        if event is None or (hasattr(event, 'keys') and len(list(event.keys())) == 0):
            logger.error("[CRITICAL] Hydration returned empty/None state!")
            return {
                "status": "FAILED",
                "error": "State hydration failed - empty or None result",
                "error_type": "HydrationFailedError",
                "final_state": {},
                "new_history_logs": [],
                "segment_type": "ERROR",
                "total_segments": 1,
                "segment_id": 0
            }
        
        # [v3.20] RoutingResolver initialization (once per workflow)
        if self._routing_resolver is None:
            try:
                from src.services.execution.routing_resolver import create_routing_resolver

                # Load workflow config (extract all node IDs)
                workflow_config = event.get('workflow_config') or event.get('config')
                if workflow_config and isinstance(workflow_config, dict):
                    all_node_ids = {n["id"] for n in workflow_config.get("nodes", []) if isinstance(n, dict) and "id" in n}

                    # Extract ring level (default 3 = Agent)
                    ring_level = event.get('ring_level', 3)
                    
                    self._routing_resolver = create_routing_resolver(
                        valid_node_ids=all_node_ids,
                        current_ring_level=ring_level
                    )
                    
                    logger.info(
                        f"[RoutingResolver] Initialized: {len(all_node_ids)} nodes, "
                        f"Ring {ring_level}"
                    )
            except ImportError as e:
                logger.warning(f"[RoutingResolver] Not available: {e}")
        
        from src.common.statebag import ensure_state_bag
        
        execution_start_time = time.time()
        
        # Check if this is a parallel branch execution (used for token aggregation and payload optimization)
        is_parallel_branch = event.get('branch_config') is not None
        
        # ====================================================================
        # [Guard] [v2.6 P0 Fix] Pre-compute metadata used across all return paths
        # Must be included to prevent null references in Step Functions Choice states
        # ====================================================================
        _total_segments = _safe_get_total_segments(event)
        
        # [Guard] [Critical Fix] explicit None handling for segment_id
        # .get('key') returns None if key exists but value is null, which 'or' propagates
        _seg_id_val = event.get('segment_id')
        if _seg_id_val is None:
            _seg_id_val = event.get('segment_to_run')
        _segment_id = _seg_id_val if _seg_id_val is not None else 0
        
        def _finalize_response(res: Dict[str, Any], force_offload: bool = False) -> Dict[str, Any]:
            """
            [Guard] [v3.12] StateBag Unified Response Wrapper
            
            Uses universal_sync_core to return a proper StateBag format:
            {
                "state_data": { ...merged state... },
                "next_action": "CONTINUE" | "COMPLETE" | "FAILED" | ...
            }
            
            This ensures:
            1. Single Source of Truth: ExecuteSegment returns StateBag directly
            2. No separate SyncStateData call needed for state mutation
            3. ASL always receives consistent {state_data, next_action} format
            """
            # [v3.35] Lambda Timeout Watchdog: check deadline before seal
            self._check_deadline("seal")

            # 1. Validation & Safety Defaults
            if not isinstance(res, dict):
                logger.error(f"[Alert] [Kernel] Invalid response type: {type(res)}! Emergency fallback.")
                res = {"status": "FAILED", "error_info": {"error": "KernelTypeMismatch"}}
            
            res.setdefault('status', 'FAILED')
            res.setdefault('segment_type', 'normal')
            res.setdefault('new_history_logs', [])
            res.setdefault('final_state', {})
            res.setdefault('total_segments', _total_segments)
            res.setdefault('segment_id', _segment_id)
            
            # 2. Extract execution result metadata
            original_final_state = res.get('final_state', {})
            if not isinstance(original_final_state, dict): 
                original_final_state = {}
            
            # Inject guardrail metadata
            gv = original_final_state.get('guardrail_verified', False)
            bca = original_final_state.get('batch_count_actual', 1)
            sm = original_final_state.get('scheduling_metadata', {})
            
            original_final_state.update({
                'guardrail_verified': gv,
                'batch_count_actual': bca,
                'scheduling_metadata': sm,
                'state_size_threshold': self.threshold,
            })
            res['final_state'] = original_final_state
            
            # 3. [v3.13] Kernel Protocol - The Great Seal
            # Uses seal_state_bag for unified Lambda ↔ ASL communication
            if KERNEL_PROTOCOL_AVAILABLE:
                # Build base_state from current event using open_state_bag
                base_state = open_state_bag(event)
                if not isinstance(base_state, dict):
                    base_state = {}
                
                # [Debug] Log loop_counter value for troubleshooting
                logger.info(f"[v3.14 Debug] base_state.loop_counter={base_state.get('loop_counter')}, "
                           f"max_loop_iterations={base_state.get('max_loop_iterations')}, "
                           f"event_keys={list(event.keys())[:10] if isinstance(event, dict) else 'N/A'}")

                # [v3.28 DIAG] Pinpoint exact state-loss location for failing test scenarios
                _diag_keys = ['TEST_RESULT', 'vision_os_test_result', 'llm_raw_output',
                              'approval_result', 'hitp_checkpoint', 'async_ready']
                _in_base = {k: (k in base_state) for k in _diag_keys}
                _in_final = ({k: (k in original_final_state) for k in _diag_keys}
                             if isinstance(original_final_state, dict) else {})
                logger.error(
                    f"[v3.28 DIAG seal] seg={_segment_id} status={res.get('status')} "
                    f"in_base={_in_base} in_final={_in_final} "
                    f"base_s3={base_state.get('__s3_offloaded')} "
                    f"final_s3={original_final_state.get('__s3_offloaded') if isinstance(original_final_state, dict) else None}"
                )
                
                # Build execution_result for sealing
                status = res.get('status', 'CONTINUE')
                
                # [v3.15 Debug] Enhanced status logging for loop limit troubleshooting
                logger.info(f"[_finalize_response] segment_id={_segment_id}, "
                           f"status={status}, "
                           f"next_segment_to_run={res.get('next_segment_to_run')}, "
                           f"current_segment_to_run={base_state.get('segment_to_run')}, "
                           f"total_segments={_total_segments}")
                
                execution_result = {
                    'final_state': original_final_state,
                    'new_history_logs': res.get('new_history_logs', []),
                    'status': status,
                    'segment_id': _segment_id,
                    'segment_type': res.get('segment_type', 'normal'),
                    'next_segment_to_run': res.get('next_segment_to_run'),
                    'error_info': res.get('error_info'),
                    'branches': res.get('branches'),
                    'execution_time': res.get('execution_time'),
                    'kernel_actions': res.get('kernel_actions'),
                    'total_segments': _total_segments,
                    # Token metadata for guardrails
                    'total_tokens': res.get('total_tokens') or original_final_state.get('total_tokens'),
                    'total_input_tokens': res.get('total_input_tokens') or original_final_state.get('total_input_tokens'),
                    'total_output_tokens': res.get('total_output_tokens') or original_final_state.get('total_output_tokens'),
                }
                
                # Context for seal_state_bag
                seal_context = {
                    'segment_id': _segment_id,
                    'force_offload': force_offload,
                    'is_parallel_branch': is_parallel_branch,
                }
                
                # [v3.13] Use seal_state_bag - Unified Protocol
                # Returns: { state_data: {...}, next_action: "..." }
                # ASL ResultSelector wraps this into $.state_data.bag
                sealed_result = seal_state_bag(
                    base_state=base_state,
                    result_delta={'execution_result': execution_result},
                    action='sync',
                    context=seal_context
                )
                
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # [P0 Integration] v3.3 KernelStateManager - save_state_delta()
                # Propagate manifest_id for Merkle Chain continuity
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                use_v3_state_saving = os.environ.get('USE_V3_STATE_SAVING', 'true').lower() == 'true'
                
                if use_v3_state_saving:
                    try:
                        from src.services.state.state_versioning_service import StateVersioningService
                        
                        # [H-02 FIX] WORKFLOW_STATE_BUCKET takes priority (same rule as initialize_state_data.py).
                        # S3_BUCKET / SKELETON_S3_BUCKET are legacy fallbacks.
                        s3_bucket = (
                            os.environ.get('WORKFLOW_STATE_BUCKET')
                            or os.environ.get('S3_BUCKET')
                            or os.environ.get('SKELETON_S3_BUCKET')
                        )
                        if not s3_bucket:
                            logger.error("[v3.3] S3_BUCKET not set, skipping state delta save")
                        else:
                            versioning_service = StateVersioningService(
                                dynamodb_table=os.environ.get('MANIFESTS_TABLE', 'StateManifestsV3'),
                                s3_bucket=s3_bucket,
                                use_2pc=True,
                                gc_dlq_url=os.environ.get('GC_DLQ_URL')
                            )
                            
                            # [v3.34] Detect if previous segment's merkle save failed
                            if base_state.get('__merkle_save_failed'):
                                logger.error(
                                    "[v3.34] MERKLE CHAIN BREAK DETECTED -- previous segment's "
                                    "state delta save failed. current_manifest_id may be stale. "
                                    "Segment %s will attempt save with potentially broken chain.",
                                    _segment_id,
                                )

                            # Extract previous manifest_id (Merkle Chain)
                            previous_manifest_id = base_state.get('current_manifest_id')

                            # Save delta
                            save_result = versioning_service.save_state_delta(
                                delta=original_final_state,  # final_state from execution_result
                                workflow_id=event.get('workflowId') or event.get('workflow_id', 'unknown'),
                                execution_id=event.get('execution_id', 'unknown'),
                                owner_id=event.get('ownerId') or event.get('owner_id', 'unknown'),
                                segment_id=_segment_id,
                                previous_manifest_id=previous_manifest_id
                            )
                            
                            # Core: propagate manifest_id for the next segment
                            new_manifest_id = save_result.get('manifest_id')
                            if new_manifest_id:
                                # [BUG-02 FIX] seal_state_bag returns state_data as a flat dict.
                                # ASL ResultSelector adds the 'bag' key *after* Lambda returns.
                                # Therefore, direct state_data access is correct here.
                                sealed_result['state_data']['current_manifest_id'] = new_manifest_id
                                logger.info(
                                    f"[v3.3] State delta saved. Manifest rotated: "
                                    f"{new_manifest_id[:12]}... (parent: {previous_manifest_id[:12] if previous_manifest_id else 'ROOT'}...)"
                                )
                            else:
                                logger.warning("[v3.3] save_state_delta succeeded but no manifest_id returned")
                    
                    except ImportError as ie:
                        logger.error(f"[v3.3] StateVersioningService import failed: {ie}")
                        # [v3.34] Flag merkle save failure so downstream can detect chain break
                        sealed_result.setdefault('state_data', {})['__merkle_save_failed'] = True
                    except Exception as e:
                        # Non-blocking: workflow continues even if v3.3 save fails
                        logger.error(f"[v3.3] Failed to save state delta: {e}", exc_info=True)
                        # [v3.34] Flag merkle save failure — without this, the next segment
                        # uses a stale current_manifest_id, silently breaking Merkle chain
                        # continuity.  The flag enables downstream detection + potential retry.
                        sealed_result.setdefault('state_data', {})['__merkle_save_failed'] = True
                else:
                    logger.info("[v3.3] USE_V3_STATE_SAVING=false, skipping state delta save")
                
                logger.info(f"[v3.13] Kernel Protocol: sealed response - "
                           f"next_action={sealed_result.get('next_action')}, "
                           f"original_status={status}, "
                           f"segment_id={_segment_id}, "
                           f"state_size={len(json.dumps(sealed_result.get('state_data', {}), default=str))//1024}KB")

                # [v3.28 DIAG] Post-seal: verify user keys survived USC merge
                _sealed_data = sealed_result.get('state_data', {})
                _in_sealed = {k: (k in _sealed_data) for k in _diag_keys}
                logger.error(
                    f"[v3.28 DIAG result] seg={_segment_id} next_action={sealed_result.get('next_action')} "
                    f"in_sealed={_in_sealed} "
                    f"sealed_s3={_sealed_data.get('__s3_offloaded')}"
                )

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # [Phase 1] Speculative Execution — async background verification
                # Like CPU branch prediction: if Stage 1 quality is high enough,
                # return immediately and verify (Stage 2 + merkle) in background.
                # Next segment checks for abort on entry.
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                if SPECULATIVE_CONTROLLER_AVAILABLE and ENABLE_SPECULATION:
                    try:
                        _spec_controller = SpeculativeExecutionController()
                        # Extract LLM output text for quality evaluation
                        _llm_output = (original_final_state or {}).get('llm_raw_output', '')
                        if _llm_output and isinstance(_llm_output, str) and len(_llm_output) > 50:
                            from src.services.quality_kernel.quality_gate import QualityGate
                            _qg = QualityGate()
                            _s1 = _qg.evaluate_stage1_only(_llm_output)
                            _next_seg_config = None  # Will be loaded by SFN for next iteration

                            if _spec_controller.should_speculate(
                                stage1_combined_score=_s1.combined_score,
                                stage1_verdict=_s1.verdict.value if hasattr(_s1.verdict, 'value') else str(_s1.verdict),
                                next_segment_config=_next_seg_config,
                                is_hitp_edge=False,
                                is_react=(segment_type == 'REACT'),
                                is_parallel_branch=is_parallel_branch,
                            ):
                                _manifest_id = sealed_result.get('state_data', {}).get('current_manifest_id', '')
                                _handle = _spec_controller.begin_speculative(
                                    segment_id=_segment_id,
                                    state_snapshot=sealed_result.get('state_data', {}),
                                    merkle_parent_hash=_manifest_id,
                                )
                                # Background verification (non-blocking)
                                _spec_controller.verify_background(
                                    _handle,
                                    stage2_callable=lambda: _qg.evaluate(_llm_output, skip_stage2=False),
                                )
                                # Store handle reference for next segment's check_abort
                                sealed_result.setdefault('state_data', {})['__speculative_handle_active'] = True
                                logger.info(
                                    f"[Phase 1] Speculative execution enabled for segment {_segment_id} "
                                    f"(score={_s1.combined_score:.2f})"
                                )
                    except Exception as _spec_err:
                        logger.warning(f"[Phase 1] Speculative setup failed (non-blocking): {_spec_err}")

                return sealed_result
            
            # 3b. USC Fallback (if kernel_protocol not available but USC is)
            if UNIVERSAL_SYNC_CORE_AVAILABLE:
                base_state = event.get('state_data', {})
                if isinstance(base_state, dict) and 'bag' in base_state:
                    base_state = base_state.get('bag', {})
                if not isinstance(base_state, dict):
                    base_state = {}
                
                status = res.get('status', 'CONTINUE')
                execution_result = {
                    'final_state': original_final_state,
                    'new_history_logs': res.get('new_history_logs', []),
                    'status': status,
                    'segment_id': _segment_id,
                    'segment_type': res.get('segment_type', 'normal'),
                    'next_segment_to_run': res.get('next_segment_to_run'),
                    'error_info': res.get('error_info'),
                    'branches': res.get('branches'),
                    'execution_time': res.get('execution_time'),
                    'kernel_actions': res.get('kernel_actions'),
                    'total_segments': _total_segments,
                    'total_tokens': res.get('total_tokens') or original_final_state.get('total_tokens'),
                    'total_input_tokens': res.get('total_input_tokens') or original_final_state.get('total_input_tokens'),
                    'total_output_tokens': res.get('total_output_tokens') or original_final_state.get('total_output_tokens'),
                }
                
                usc_result = universal_sync_core(
                    base_state=base_state,
                    new_result={'execution_result': execution_result},
                    context={'action': 'sync', 'segment_id': _segment_id}
                )
                
                logger.warning("[v3.13] USC fallback - kernel_protocol not available")
                return usc_result
            
            # 4. Fallback: Legacy mode (if universal_sync_core not available)
            logger.warning("[v3.12] Fallback to legacy mode - universal_sync_core not available")
            
            # Use StateHydrator to Dehydrate (legacy behavior)
            bag = SmartStateBag(res, hydrator=self.hydrator)
            
            force_fields = set()
            force_fields.add('final_state')
            
            if force_offload or is_parallel_branch:
                force_fields.add('branches')
                force_fields.add('execution_batches')
                if is_parallel_branch:
                    force_fields.add('workflow_config')
                    force_fields.add('partition_map')

            owner_id = event.get('ownerId') or event.get('owner_id', 'unknown')
            workflow_id = event.get('workflowId') or event.get('workflow_id', 'unknown')
            execution_id = event.get('execution_id', 'unknown')

            payload = self.hydrator.dehydrate(
                state=bag,
                owner_id=owner_id,
                workflow_id=workflow_id,
                execution_id=execution_id,
                segment_id=_segment_id,
                force_offload_fields=force_fields,
                return_delta=False
            )
            
            # Restore Critical Metadata to Top Level
            keys_to_preserve = [
                'total_tokens', 'total_input_tokens', 'total_output_tokens',
                'guardrail_verified', 'batch_count_actual', 'scheduling_metadata',
                'usage', 'branch_token_details',
                'inner_partition_map', 'branch_id', 'next_segment_to_run'
            ]
            for k in keys_to_preserve:
                if k in original_final_state and k not in payload:
                    payload[k] = original_final_state[k]
            
            # Generate S3 Path Aliases for ASL Compatibility
            for field in ['final_state', 'current_state', 'workflow_config', 'partition_map', 'segment_manifest', 'branches']:
                val = payload.get(field)
                if isinstance(val, dict) and (val.get('__s3_pointer__') or val.get('bucket')):
                    bucket = val.get('bucket')
                    key = val.get('key')
                    if bucket and key:
                        payload[f"{field}_s3_path"] = f"s3://{bucket}/{key}"
            
            if payload.get('final_state_s3_path'):
                payload['state_s3_path'] = payload['final_state_s3_path']

            # Wrap in StateBag format for consistency
            return {
                'state_data': payload,
                'next_action': res.get('status', 'CONTINUE')
            }
        
        # ====================================================================
        # [Guard] [Phase 2] Pre-Execution Check: concurrency and budget check
        # ====================================================================
        if CONCURRENCY_CONTROLLER_AVAILABLE and self.concurrency_controller:
            pre_check = self.concurrency_controller.pre_execution_check()
            # [P0 Fix] Null Guard for pre_check return value
            if pre_check and not pre_check.get('can_proceed', True):
                logger.error(f"[Kernel] Pre-execution check failed: {pre_check.get('reason')}")
                return _finalize_response({
                    "status": "HALTED",
                    "final_state": {},
                    "final_state_s3_path": None,
                    "next_segment_to_run": None,
                    "new_history_logs": [],
                    "error_info": {
                        "error": pre_check.get('reason', 'Unknown'),
                        "error_type": "ConcurrencyControlHalt",
                        "budget_status": pre_check.get('budget_status')
                    },
                    "branches": None,
                    "segment_type": "halted",
                    "kernel_stats": self.concurrency_controller.get_comprehensive_stats()
                })
            
            # Load level logging
            snapshot = pre_check.get('snapshot')
            if snapshot and snapshot.load_level.value in ['high', 'critical']:
                logger.warning(f"[Kernel] [Warning] High load detected: {snapshot.load_level.value} "
                             f"({snapshot.active_executions}/{snapshot.reserved_concurrency})")
        
        # [Fix] Read MOCK_MODE from event and inject as environment variable
        # Override env var even when MOCK_MODE=false so the simulator can make real LLM calls
        # [2026-01-26] Changed default to false (real LLM call mode)
        event_mock_mode = str(event.get('MOCK_MODE', 'false')).lower()
        if event_mock_mode in ('true', '1', 'yes', 'on'):
            os.environ['MOCK_MODE'] = 'true'
            logger.info("[Test] MOCK_MODE enabled from event payload")
        else:
            os.environ['MOCK_MODE'] = 'false'
            logger.info("[Test] MOCK_MODE disabled (default: false, Simulator Mode)")
        
        # ====================================================================
        # [Parallel] [Aggregator] Parallel result aggregation handling
        # Called from ASL AggregateParallelResults
        # ====================================================================
        segment_type_param = event.get('segment_type')
        if segment_type_param == 'aggregator':
            return _finalize_response(self._handle_aggregator(event), force_offload=True)
        
        # 0. Check for Branch Offloading
        branch_config = event.get('branch_config')
        if branch_config:
            force_child = os.environ.get('FORCE_CHILD_WORKFLOW', 'false').lower() == 'true'
            node_count = len(branch_config.get('nodes', [])) if isinstance(branch_config.get('nodes'), list) else 0
            # [P0 Fix] Defensive check: n may be None
            has_hitp = branch_config.get('hitp', False) or any(
                n.get('hitp') for n in branch_config.get('nodes', []) if n and isinstance(n, dict)
            )
            
            should_offload = force_child or node_count > 20 or has_hitp
            
            if should_offload:
                auth_user_id = event.get('ownerId') or event.get('owner_id')
                quota_id = event.get('quota_reservation_id')
                
                child_result = self._trigger_child_workflow(event, branch_config, auth_user_id, quota_id)
                if child_result:
                    # [Fix] Ensure child workflow result is also wrapped (though small) for consistency
                    return _finalize_response(child_result, force_offload=False)

        # 1. State Bag Normalization
        # [Moved] executed AFTER loading state to prevent data loss
        # normalize_inplace(event, remove_state_data=True) 

        
        # 2. Extract Context
        auth_user_id = event.get('ownerId') or event.get('owner_id') or event.get('user_id')
        workflow_id = event.get('workflowId') or event.get('workflow_id')
        # [Hybrid Mode] Support both segment_id (hybrid) and segment_to_run (legacy)
        # [Guard] [Critical Fix] explicit None checking to prevent TypeError in comparisons
        _seg_id_cand = event.get('segment_id')
        if _seg_id_cand is None:
            _seg_id_cand = event.get('segment_to_run')
        segment_id = _seg_id_cand if _seg_id_cand is not None else 0
        
        # [Critical Fix] S3 bucket for large payload offloading - ensure non-empty string
        s3_bucket_raw = os.environ.get("S3_BUCKET") or os.environ.get("SKELETON_S3_BUCKET") or ""
        s3_bucket = s3_bucket_raw.strip() if s3_bucket_raw else None
        
        if not s3_bucket:
            logger.error("[Alert] [CRITICAL] S3_BUCKET/SKELETON_S3_BUCKET environment variable is NOT SET or EMPTY! "
                        f"S3_BUCKET='{os.environ.get('S3_BUCKET')}', "
                        f"SKELETON_S3_BUCKET='{os.environ.get('SKELETON_S3_BUCKET')}'. "
                        "Large payloads (>256KB) will FAIL.")
        else:
            logger.debug(f"S3 bucket for state offloading: {s3_bucket}")
        
        # ====================================================================
        # 3. Load State (Inline or S3) - Keep as local variable only
        # DO NOT add to event to avoid 256KB limit
        # ====================================================================
        # [v3.14] Kernel Protocol - Use open_state_bag for unified extraction
        # ASL passes $.state_data.bag (bag contents) directly as Payload
        # open_state_bag handles all cases: v3 ASL, legacy, direct invocation
        # ====================================================================
        state_s3_path = event.get('state_s3_path')
        
        # [P1 Breaking Refactor] Kernel Protocol mandatory - Fail-Fast principle
        # Legacy 5-tier fallback completely removed
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if not KERNEL_PROTOCOL_AVAILABLE:
            raise RuntimeError(
                "CRITICAL: kernel_protocol is REQUIRED for v3.14+. "
                "Legacy mode no longer supported. "
                "Verify src.common.kernel_protocol import succeeded."
            )
        
        # Unified State Extraction (single path)
        initial_state = open_state_bag(event)
        
        # Strict Validation: data schema compliance required
        strict_mode = os.environ.get('AN_STRICT_MODE', 'false').lower() == 'true'
        if strict_mode and (not initial_state or not isinstance(initial_state, dict)):
            raise ValueError(
                f"[AN_STRICT_MODE] Invalid state structure. "
                f"open_state_bag returned: {type(initial_state)}. "
                f"Event keys: {list(event.keys())[:10]}. "
                f"This indicates ASL schema mismatch."
            )
        
        # Safe fallback for non-strict mode (development environment)
        if not initial_state or not isinstance(initial_state, dict):
            logger.warning(
                f"[Kernel Protocol] open_state_bag returned invalid data: {type(initial_state)}. "
                f"Falling back to empty state. Event keys: {list(event.keys())[:10]}"
            )
            initial_state = {}
        
        logger.info(
            f"[v3.14 Kernel Protocol] State extracted via unified path. "
            f"Keys: {list(initial_state.keys())[:8]}, Strict: {strict_mode}"
        )
        
        # [Critical Fix] S3 Offload Recovery: check __s3_offloaded flag
        # If previous segment did S3 offload, only metadata is included
        # → Full state needs to be restored from S3
        # [v3.28 FAIL Guard] Log detection for FAIL scenario diagnosis
        _s3_offloaded_raw = initial_state.get('__s3_offloaded') if isinstance(initial_state, dict) else None
        logger.error(
            f"[v3.28 FAIL Guard] seg={_segment_id} "
            f"__s3_offloaded={_s3_offloaded_raw!r} type={type(_s3_offloaded_raw).__name__} "
            f"__s3_path={initial_state.get('__s3_path') if isinstance(initial_state, dict) else None}"
        )
        if isinstance(initial_state, dict) and _s3_offloaded_raw is True:
            offloaded_s3_path = initial_state.get('__s3_path')
            if offloaded_s3_path:
                logger.info(f"[S3 Offload Recovery] Detected offloaded state. Restoring from S3: {offloaded_s3_path}")
                try:
                    # [Fix] flat-merge Strategy-2: S3 contains only offload_candidates (user keys).
                    # Structural & control keys live in the bag alongside the wrapper pointer.
                    # Preserve them before replacement, then overlay with S3 user keys
                    # so the next segment receives a complete initial_state.
                    _OFFLOAD_WRAPPER_KEYS = frozenset({
                        '__s3_offloaded', '__s3_path', '__original_size_kb',
                        'guardrail_verified', 'batch_count_actual', 'scheduling_metadata',
                        '__scheduling_metadata', '__guardrail_verified', '__batch_count_actual',
                    })
                    structural_preserved = {
                        k: v for k, v in initial_state.items()
                        if k not in _OFFLOAD_WRAPPER_KEYS
                    }
                    s3_state = self.state_manager.download_state_from_s3(offloaded_s3_path)
                    # S3 user keys win on conflict (they are the freshest output)
                    initial_state = {**structural_preserved, **s3_state}
                    logger.info(
                        f"[S3 Offload Recovery] Successfully restored state from S3 "
                        f"({len(json.dumps(initial_state, ensure_ascii=False).encode('utf-8'))/1024:.1f}KB). "
                        f"Preserved {len(structural_preserved)} structural keys, "
                        f"merged {len(s3_state)} S3 keys."
                    )
                except Exception as e:
                    logger.error(f"[S3 Offload Recovery] Failed to restore state from S3: {e}")
                    return _finalize_response({
                        "status": "FAILED",
                        "error_info": {
                            "error": f"S3 State Recovery Failed: {str(e)}",
                            "error_type": "S3RecoveryError",
                            "s3_path": offloaded_s3_path
                        }
                    })
            else:
                logger.warning("[S3 Offload Recovery] __s3_offloaded=True but no __s3_path found!")
        
        if state_s3_path:
            initial_state = self.state_manager.download_state_from_s3(state_s3_path)
            # [Critical Fix] Double-check for S3 offload in downloaded state
            from src.common.statebag import ensure_state_bag
            initial_state = ensure_state_bag(initial_state)
            if isinstance(initial_state, dict) and initial_state.get('__s3_offloaded') is True:
                offloaded_path = initial_state.get('__s3_path')
                if offloaded_path:
                    logger.info(f"[S3 Offload Recovery] Recursive hydration from: {offloaded_path}")
                    try:
                        initial_state = self.state_manager.download_state_from_s3(offloaded_path)
                        initial_state = ensure_state_bag(initial_state)
                    except Exception as e:
                        logger.error(f"[S3 Offload Recovery] Recursive hydration failed: {e}")
                        # Continue with wrapper (will likely fail downstream but better than crash)
        
        # [Guard] [v3.6 P0] Data Ownership Defense: enforce StateBag
        # StateBag guarantees Safe Access (get(key) != None)
        from src.common.statebag import ensure_state_bag
        initial_state = ensure_state_bag(initial_state)
        
        # [FIX] Propagate MOCK_MODE from payload to state (payload always wins)
        # LLM Simulator passes MOCK_MODE in payload root, but llm_chat_runner reads from state
        # CRITICAL: Payload takes precedence over state to allow runtime override
        if 'MOCK_MODE' in event:
            old_value = initial_state.get('MOCK_MODE', 'not set')
            initial_state['MOCK_MODE'] = event['MOCK_MODE']
            logger.info(f"MOCK_MODE override: {old_value} -> {event['MOCK_MODE']} (from payload)")

        # ====================================================================
        # [Hydration] [v3.10] Unified State Bag - Single Source of Truth
        # ====================================================================
        # [v3.6] workflow_config is retrieved directly from StateBag
        # When the full bag is hydrated, workflow_config is included (no separate S3 lookup needed)
        # ====================================================================
        
        # ====================================================================
        # [v3.6] Extract bag data BEFORE normalize_inplace removes state_data
        # ====================================================================
        # Merkle DAG Mode: workflow_config/partition_map removed
        # segment_config is delivered directly from manifest or ASL
        execution_mode = _safe_get_from_bag(event, 'execution_mode')
        distributed_mode = _safe_get_from_bag(event, 'distributed_mode')
        
        # [Fix] [v3.10] Normalize Event AFTER state extraction but BEFORE processing
        # Remove potentially huge state_data from event to save memory
        normalize_inplace(event, remove_state_data=True)

        # ====================================================================
        # [Phase 0] Segment Config Resolution - Merkle DAG 3-Tier Fallback
        # ====================================================================
        
        # [Critical Fix] Branch Execution: partition_map fallback from branch_config
        # ASL ProcessParallelSegments passes full branch info via branch_config
        # If partition_map is null, use branch_config.partition_map
        # [v3.21 Fix] Initialize partition_map from bag BEFORE referencing it
        # Previously uninitialized → UnboundLocalError → caught as exception → infinite CONTINUE loop
        partition_map = _safe_get_from_bag(event, 'partition_map') or event.get('partition_map') or []
        branch_config = event.get('branch_config')
        if not partition_map and branch_config and isinstance(branch_config, dict):
            branch_partition_map = branch_config.get('partition_map')
            if branch_partition_map:
                logger.info(f"[Branch Execution] Using partition_map from branch_config "
                           f"(branch_id: {branch_config.get('branch_id', 'unknown')}, "
                           f"segments: {len(branch_partition_map)})")
                partition_map = branch_partition_map
        
        # [Phase 0.2] Hybrid Loading: ASL direct injection or Fallback
        # ASL Direct Injection handles less than 20% due to 256KB constraint
        # Lambda Fallback is the actual primary path (expected to handle 80%)
        segment_config = event.get('segment_config')  # Injected from ASL (small manifest)
        
        if not segment_config:
            # Fallback 1: Lambda loads directly from S3 (large manifest)
            manifest_s3_path = event.get('segment_manifest_s3_path')
            segment_index = event.get('segment_index', segment_id)
            
            if manifest_s3_path:
                logger.info(f"[Phase 0.2] Loading segment_config from manifest: {manifest_s3_path}")
                segment_config = self._load_segment_config_from_manifest(
                    manifest_s3_path,
                    segment_index
                )
            elif execution_mode in ('MAP_REDUCE', 'BATCHED') and event.get('segment_config'):
                # Fallback 2: Direct segment_config for distributed modes
                logger.info(f"[Hybrid Mode] Using direct segment_config for {execution_mode} mode")
                segment_config = event.get('segment_config')
            else:
                # No valid segment_config source
                raise ValueError(
                    "No segment_config source available. "
                    "Expected: ASL injection or S3 manifest. "
                    f"manifest_id={event.get('manifest_id')}, "
                    f"manifest_s3_path={manifest_s3_path}"
                )
        
        # [Phase 0 Complete] Gradual migration via 3-tier Fallback
        # 1. ASL Direct Injection (20% - small manifest)
        # 2. Lambda S3 Loading (80% - large manifest, primary path)
        # 3. Legacy workflow_config/partition_map (compatibility)

        # [Option A] Segment config normalization - convert None values to empty dict/list
        segment_config = _normalize_segment_config(segment_config)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # [Phase 8.4] Extract Context for Trust Chain Gatekeeper
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        owner_id = event.get('ownerId') or event.get('owner_id', 'unknown')
        execution_id = event.get('execution_id', 'unknown')
        
        # Extract workflow_config (loaded via bag hydration)
        try:
            from src.common.statebag import SmartStateBag
            bag = SmartStateBag(initial_state, hydrator=self.hydrator)
            workflow_config = bag.get('workflow_config')
        except Exception as e:
            # [v3.34] Was bare except — trust chain verification may be skipped
            logger.warning("[Phase 8.4] Failed to load workflow_config for trust chain: %s", e)
            workflow_config = None

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # [Phase 8.4] Trust Chain Gatekeeper: Kernel Panic on Hash Mismatch
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Architecture principles (Phase 8 Guideline):
        # - verify_segment_config = final gate before execution (Gatekeeper)
        # - Hash verification failure = Kernel Panic (immediate halt, admin alert)
        # - Zero Trust: all segment_config must be verified
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        manifest_id = event.get('manifest_id')
        
        if manifest_id and segment_config:
            try:
                from src.services.state.state_versioning_service import StateVersioningService
                
                versioning_service = StateVersioningService(
                    dynamodb_table=os.environ.get('WORKFLOW_MANIFESTS_TABLE', 'WorkflowManifestsV3'),
                    s3_bucket=os.environ.get('S3_BUCKET', 'analemma-state-dev')
                )
                
                segment_index = event.get('segment_index', segment_id)
                
                logger.info(
                    f"[Phase 8.4 Gatekeeper] Verifying segment_config integrity\n"
                    f"  Manifest: {manifest_id[:8]}...\n"
                    f"  Segment: {segment_index}\n"
                    f"  Mode: KERNEL_PANIC (Zero Trust)"
                )
                
                is_valid = versioning_service.verify_segment_config(
                    segment_config=segment_config,
                    manifest_id=manifest_id,
                    segment_index=segment_index
                )
                
                if not is_valid:
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # [KERNEL PANIC] Hash Mismatch Detected
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # Halt system immediately
                    # Send admin alert (trigger CloudWatch Alarm)
                    # Record security incident log
                    logger.critical(
                        f"[KERNEL PANIC] [SECURITY ALERT]\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"segment_config INTEGRITY VIOLATION DETECTED!\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Manifest ID: {manifest_id}\n"
                        f"Segment Index: {segment_index}\n"
                        f"Execution ID: {execution_id}\n"
                        f"Workflow ID: {workflow_id}\n"
                        f"Owner ID: {owner_id}\n"
                        f"\n"
                        f"POSSIBLE CAUSES:\n"
                        f"  1. Man-in-the-Middle (MITM) Attack\n"
                        f"  2. S3 Object Tampering (Agent Privilege Escalation)\n"
                        f"  3. Manifest Corruption (Data Integrity Failure)\n"
                        f"  4. Hash Collision (Extremely Rare)\n"
                        f"\n"
                        f"SYSTEM ACTION: HALTING EXECUTION IMMEDIATELY\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    
                    # ERROR-level log to trigger CloudWatch Alarm
                    logger.error(
                        f"[SECURITY_ALERT] INTEGRITY_VIOLATION "
                        f"manifest_id={manifest_id} segment_index={segment_index} "
                        f"execution_id={execution_id}"
                    )
                    
                    # Halt execution immediately (SecurityError)
                    return _finalize_response({
                        "status": "FAILED",
                        "error": "KERNEL_PANIC: segment_config integrity verification failed",
                        "error_type": "SecurityError",
                        "final_state": initial_state,
                        "new_history_logs": [],
                        "error_info": {
                            "error": "Segment config hash verification failed (Kernel Panic)",
                            "error_type": "IntegrityViolation",
                            "severity": "CRITICAL",
                            "manifest_id": manifest_id,
                            "segment_index": segment_index,
                            "execution_id": execution_id,
                            "workflow_id": workflow_id,
                            "security_alert": True,
                            "recommended_action": "INVESTIGATE_IMMEDIATELY"
                        }
                    })
                
                logger.info(
                    f"[Phase 8.4 Gatekeeper] Integrity verified: segment_index={segment_index}\n"
                    f"  Trust Chain: INTACT"
                )
                
            except ImportError:
                logger.warning(
                    "[Phase 8.4 Gatekeeper] StateVersioningService not available "
                    "(development mode) - skipping verification"
                )
            except Exception as verify_error:
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # [CRITICAL ERROR] Verification Process Failed
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # Phase 8 Guideline: verification process failure itself is treated as a system fault
                logger.error(
                    f"[KERNEL PANIC] [SYSTEM FAULT]\n"
                    f"segment_config verification PROCESS failed: {verify_error}\n"
                    f"Manifest: {manifest_id[:8]}..., Segment: {segment_index}\n"
                    f"HALTING EXECUTION",
                    exc_info=True
                )
                
                return _finalize_response({
                    "status": "FAILED",
                    "error": f"KERNEL_PANIC: Verification process failed - {str(verify_error)}",
                    "error_type": "SystemFault",
                    "final_state": initial_state,
                    "new_history_logs": [],
                    "error_info": {
                        "error": f"Integrity verification failed: {str(verify_error)}",
                        "error_type": "SystemFault",
                        "severity": "CRITICAL",
                        "manifest_id": manifest_id,
                        "segment_index": segment_index,
                        "execution_id": execution_id
                    }
                })
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        # [Guard] [v2.6 P0 Fix] Self-Healing to prevent 'code' type contamination
        # Upstream Lambdas (e.g., PartitionService) may inject incorrect types, so correct at runtime
        if segment_config and isinstance(segment_config, dict):
            for node in segment_config.get('nodes', []):
                # [v3.8] None defense
                if node is None or not isinstance(node, dict):
                    continue
                if node.get('type') == 'code':
                    logger.warning(
                        f"[Guard] [Self-Healing] Aliasing 'code' to 'operator' for node {node.get('id')}. "
                        f"This indicates upstream data mutation - investigate PartitionService."
                    )
                    node['type'] = 'operator'
        
        # [Guard] [Critical Fix] Early error return if segment_config is None or error type
        if not segment_config or (isinstance(segment_config, dict) and segment_config.get('type') == 'error'):
            error_msg = segment_config.get('error', 'segment_config is None') if isinstance(segment_config, dict) else 'segment_config is None'
            logger.error(f"[Alert] [Critical] segment_config resolution failed: {error_msg}")
            return _finalize_response({
                "status": "FAILED",
                "error": error_msg,
                "error_type": "ConfigurationError",
                "final_state": initial_state,
                "final_state_s3_path": None,
                "next_segment_to_run": None,
                "new_history_logs": [],
                "error_info": {
                    "error": error_msg,
                    "error_type": "ConfigurationError",
                    "workflow_config_present": workflow_config is not None,
                    "partition_map_present": partition_map is not None
                },
                "branches": None,
                "segment_type": "ERROR"
            })
        
        # [Critical Fix] parallel_group type segments return PARALLEL_GROUP status immediately
        # ASL ProcessParallelSegments receives branches and runs them in parallel via Map
        # [Parallel] [Pattern 3] Apply parallel scheduler
        segment_type = segment_config.get('type') if isinstance(segment_config, dict) else None
        
        # [Fix] HITP Segment Type Check (Priority: segment type > edge type)
        # If segment itself is marked as 'hitp', pause immediately
        if segment_type == 'hitp':
            logger.info(f"[Kernel] HITP segment {segment_id} detected. Pausing for human approval.")
            return _finalize_response({
                "status": "PAUSED_FOR_HITP",
                "final_state": mask_pii_in_state(initial_state),
                "final_state_s3_path": None,
                "next_segment_to_run": segment_id + 1,
                "new_history_logs": [],
                "error_info": None,
                "branches": None,
                "segment_type": "hitp"
            })
        
        # [Fix] Aggregator Interception (Delayed Check)
        # segment_type param may not exist at execute_segment entry (resolved from partition_map)
        # Therefore, check once more based on the resolved segment_config here
        if segment_type == 'aggregator':
            logger.info(f"[Kernel] Aggregator segment {segment_id} detected (Resolved). Delegating to _handle_aggregator.")
            return _finalize_response(self._handle_aggregator(event), force_offload=True)

        # [Issue-2 Fix] Segments with 'branches' key are treated as parallel_group regardless of type value
        # Even if partitioner does not mark type as 'parallel_group', route to same path when branches key exists
        has_branches = isinstance(segment_config.get('branches'), list) and len(segment_config.get('branches', [])) > 0
        if segment_type != 'parallel_group' and has_branches:
            logger.info(
                f"[Parallel] segment_type='{segment_type}' but 'branches' key found "
                f"({len(segment_config['branches'])} branches) — rerouting to parallel_group handler"
            )
            segment_type = 'parallel_group'

        if segment_type == 'parallel_group':
            branches = segment_config.get('branches', [])
            logger.info(f"[Parallel] Parallel group detected with {len(branches)} branches")
            
            # [Guard] [Critical Fix] Define helper for offloading within parallel block
            def _finalize_with_offload(response_payload: Dict[str, Any]) -> Dict[str, Any]:
                """Helper to ensure S3 offloading is checked before finalizing response."""
                
                # 1. Calculate approximate size
                try:
                    payload_json = json.dumps(response_payload, ensure_ascii=False)
                    payload_size = len(payload_json.encode('utf-8'))
                except Exception as e:
                    logger.debug("Failed to estimate response payload size: %s", e)
                    payload_size = 0 # Fallback
                
                # 2. Extract state to potentially offload
                current_final_state = response_payload.get('final_state')
                
                # 3. Offload Logic: If state exists AND (forced offload OR payload > 100KB safe limit)
                # Note: We use 100KB as a safety margin for the 256KB Step Functions limit,
                # considering overhead from packaging, encoding, and ASL wrapper fields.
                # Previous 128KB threshold was too close to the limit.
                should_offload = current_final_state and (payload_size > 100 * 1024)
                
                if should_offload:
                    if not s3_bucket:
                        logger.error("[Parallel] [Critical] S3 Bucket not defined! Cannot offload large payload.")
                        # Proceeding might fail, but we try pruning metadata next
                    else:
                        logger.info(f"[Parallel] Payload size {payload_size} bytes exceeds safety limit. Offloading final_state.")
                        offloaded_state, s3_path = self.state_manager.handle_state_storage(
                            state=current_final_state,
                            auth_user_id=auth_user_id,
                            workflow_id=workflow_id,
                            segment_id=segment_id,
                            bucket=s3_bucket,
                            threshold=0 # Force offload
                        )
                        
                        # [Critical Fix] Clear final_state on S3 offload
                        if s3_path:
                            offloaded_state = {
                                "__s3_offloaded": True,
                                "__s3_path": s3_path,
                                "__original_size_kb": len(json.dumps(current_final_state, ensure_ascii=False).encode('utf-8')) / 1024 if current_final_state else 0
                            }
                            logger.info(f"[Parallel] Offloaded state to S3: {s3_path}. Response: ~0.2KB")
                        
                        # Update response with offloaded result
                        response_payload['final_state'] = offloaded_state
                        response_payload['final_state_s3_path'] = s3_path
                        response_payload['state_s3_path'] = s3_path # Alias for ASL
                
                # 4. Secondary Pruning: If still too large, prune non-essential metadata
                # Recalculate size
                try:
                    payload_json = json.dumps(response_payload, ensure_ascii=False)
                    payload_size = len(payload_json.encode('utf-8'))
                except Exception as e:
                    # [v3.34] Was bare except — payload_size stays stale, pruning may not trigger
                    logger.warning("[Parallel] Payload size re-calculation failed: %s", e)
                    
                if payload_size > 200 * 1024: # Still > 200KB
                    logger.warning(f"[Parallel] [Alert] Payload still huge ({payload_size} bytes) after offload. Pruning metadata.")
                    # Prune history logs
                    response_payload['new_history_logs'] = []
                    # Prune metadata details if scheduling is present
                    if 'scheduling_metadata' in response_payload:
                        response_payload['scheduling_metadata'] = {
                            'note': 'Pruned due to size limit',
                            'batch_count': response_payload['scheduling_metadata'].get('batch_count'),
                            'strategy': response_payload['scheduling_metadata'].get('strategy')
                        }
                
                return _finalize_response(response_payload, force_offload=True)

            # [Guard] [Critical Fix] Check HITP edge first (before single-branch optimization)
            # If HITP edge exists, always handle as PAUSED_FOR_HITP
            hitp_edge_types = {"hitp", "human_in_the_loop", "pause"}
            has_hitp_edge = False
            
            for branch in branches:
                if isinstance(branch, dict):
                    branch_nodes = branch.get('nodes', [])
                    for node in branch_nodes:
                        if isinstance(node, dict):
                            # Check node incoming edges
                            in_edges = node.get('in_edges', [])
                            if any(e.get('type') in hitp_edge_types for e in in_edges if isinstance(e, dict)):
                                has_hitp_edge = True
                                break
                    if has_hitp_edge:
                        break
            
            if has_hitp_edge:
                logger.info(f"[Kernel] HITP edge detected in segment {segment_id}. Pausing for human approval.")
                return _finalize_with_offload({
                    "status": "PAUSED_FOR_HITP",
                    "final_state": mask_pii_in_state(initial_state),
                    "final_state_s3_path": None,
                    "next_segment_to_run": segment_id + 1,
                    "new_history_logs": [],
                    "error_info": None,
                    "branches": branches,  # Preserve branch info for post-HITP execution
                    "segment_type": "hitp_pause"
                })
            
            # [Guard] [Critical Fix] Handle single branch + internal partition_map case
            # No actual parallel execution needed; directly execute the first segment inside the branch
            if len(branches) == 1:
                single_branch = branches[0]
                branch_partition_map = single_branch.get('partition_map', [])
                
                if branch_partition_map:
                    logger.info(f"[Kernel] Single branch with internal partition_map detected. "
                               f"Executing {len(branch_partition_map)} segments sequentially instead of parallel.")
                    
                    # Use the first segment inside the branch as segment_config
                    first_inner_segment = branch_partition_map[0] if branch_partition_map else None
                    
                    if first_inner_segment:
                        # [System] Convert internal partition_map to a new execution context
                        # Execute internal segment chain sequentially while preserving state
                        return _finalize_with_offload({
                            "status": "SEQUENTIAL_BRANCH",
                            "final_state": mask_pii_in_state(initial_state),
                            "final_state_s3_path": None,
                            "next_segment_to_run": segment_id + 1,
                            "new_history_logs": [],
                            "error_info": None,
                            "branches": None,  # No parallel execution
                            "segment_type": "sequential_branch",
                            # [Guard] Pass internal partition_map info (for ASL sequential processing)
                            "inner_partition_map": branch_partition_map,
                            "inner_segment_count": len(branch_partition_map),
                            "branch_id": single_branch.get('branch_id', 'B0'),
                            "scheduling_metadata": {
                                'strategy': 'SEQUENTIAL_SINGLE_BRANCH',
                                'total_inner_segments': len(branch_partition_map),
                                'reason': 'Single branch optimization - parallel execution skipped'
                            }
                        })
            
            # [System] Filter out empty branches or branches with no nodes
            valid_branches = []
            for branch in branches:
                # [P0 Fix] Guard against None or non-dict branch objects
                if not branch or not isinstance(branch, dict):
                    logger.warning(f"[Kernel] [Warning] Found invalid branch object (None or not dict): {type(branch)}")
                    continue
                
                branch_nodes = branch.get('nodes', [])
                branch_partition = branch.get('partition_map', [])
                
                # Branch is valid if it has nodes or a partition_map
                if branch_nodes or branch_partition:
                    valid_branches.append(branch)
                else:
                    logger.warning(f"[Kernel] [Warning] Skipping empty branch: {branch.get('branch_id', 'unknown')}")
            
            # [Guard] If no valid branches, proceed to next segment
            if not valid_branches:
                logger.info(f"[Kernel] No valid branches to execute, skipping parallel group")
                
                # [v3.30 Fix] HITP Edge Detection using segment_config.outgoing_edges
                next_segment = segment_id + 1
                total_segments = _safe_get_total_segments(event)
                has_more_segments = next_segment < total_segments
                hitp_detected = False

                if has_more_segments and segment_config:
                    edge_info = check_inter_segment_edges(segment_config)
                    if is_hitp_edge(edge_info):
                        hitp_detected = True
                        logger.info(f"[Empty Parallel] HITP edge detected via segment_config.outgoing_edges: "
                                  f"segment {segment_id} → {next_segment}, "
                                  f"type={edge_info.get('edge_type')}, target={edge_info.get('target_node')}")
                
                if hitp_detected:
                    logger.info(f"[Empty Parallel] Pausing execution due to HITP edge. Next segment: {next_segment}")
                    return _finalize_with_offload({
                        "status": "PAUSED_FOR_HITP",
                        "final_state": mask_pii_in_state(initial_state),
                        "final_state_s3_path": None,
                        "next_segment_to_run": next_segment,
                        "new_history_logs": [],
                        "error_info": None,
                        "branches": None,
                        "segment_type": "hitp_pause",
                        "hitp_metadata": {
                            'hitp_edge_detected': True,
                            'pause_location': 'empty_parallel_group'
                        }
                    })
                
                # [v3.17 Fix] Check if more segments exist before returning CONTINUE
                return _finalize_with_offload({
                    "status": "CONTINUE" if has_more_segments else "COMPLETE",
                    "final_state": mask_pii_in_state(initial_state),
                    "final_state_s3_path": None,
                    "next_segment_to_run": next_segment if has_more_segments else None,
                    "new_history_logs": [],
                    "error_info": None,
                    "branches": None,
                    "segment_type": "empty_parallel_group"
                })
            
            # Invoke parallel scheduler
            schedule_result = self._schedule_parallel_group(
                segment_config=segment_config,
                state=initial_state,
                segment_id=segment_id,
                owner_id=auth_user_id,
                workflow_id=workflow_id
            )
            
            # SCHEDULED_PARALLEL: requires sequential execution per batch
            if schedule_result['status'] == 'SCHEDULED_PARALLEL':
                execution_batches = schedule_result['execution_batches']
                # [Guard] [P1 Fix] Inject scheduling_metadata into state for test verification
                meta = schedule_result.get('scheduling_metadata', {})
                batch_count = meta.get('batch_count', 1)
                
                initial_state['scheduling_metadata'] = meta
                initial_state['batch_count_actual'] = batch_count
                
                # [Guard] [P1 Fix] SPEED_GUARDRAIL_TEST requires this flag when splitting occurs
                if meta.get('strategy') == 'SPEED_OPTIMIZED' and batch_count > 1:
                    initial_state['guardrail_verified'] = True
                
                logger.info(f"[Scheduler] [System] Scheduled {meta.get('total_branches', len(valid_branches))} branches into "
                           f"{meta.get('batch_count', 1)} batches (strategy: {meta.get('strategy', 'UNKNOWN')})")
                
                return _finalize_with_offload({
                    "status": "SCHEDULED_PARALLEL",
                    "final_state": mask_pii_in_state(initial_state),
                    "final_state_s3_path": None,
                    "next_segment_to_run": segment_id + 1,
                    "new_history_logs": [],
                    "error_info": None,
                    "branches": None,  # [Optim] Remove redundant branches (use execution_batches)
                    "execution_batches": execution_batches,
                    "segment_type": "scheduled_parallel",
                    "scheduling_metadata": meta
                })
            
            # PARALLEL_GROUP: default parallel execution
            # [Guard] [P1 Fix] Inject scheduling_metadata into state for test verification (Consistent with SCHEDULED_PARALLEL)
            meta = schedule_result.get('scheduling_metadata', {})
            initial_state['scheduling_metadata'] = meta
            initial_state['batch_count_actual'] = meta.get('batch_count', 1)
            
            # [Pointer Strategy] schedule_result.branches is already a lightweight pointer array
            # Also pass branches_s3_path to store in State Bag
            branch_pointers = schedule_result.get('branches', valid_branches)
            branches_s3_path = schedule_result.get('branches_s3_path')
            
            return _finalize_with_offload({
                "status": "PARALLEL_GROUP",
                "final_state": mask_pii_in_state(initial_state),
                "final_state_s3_path": None,
                "next_segment_to_run": segment_id + 1,
                "new_history_logs": [],
                "error_info": None,
                "branches": branch_pointers,  # Lightweight pointer array
                "branches_s3_path": branches_s3_path,  # S3 path
                "execution_batches": schedule_result.get('execution_batches', [branch_pointers]),
                "segment_type": "parallel_group",
                "scheduling_metadata": meta
            })
        
        # [Guard] [Pattern 2] Kernel check: is this segment in SKIPPED status?
        segment_status = self._check_segment_status(segment_config)
        if segment_status == SEGMENT_STATUS_SKIPPED:
            skip_reason = segment_config.get('skip_reason', 'Kernel decision')
            logger.info(f"[Kernel] Segment {segment_id} SKIPPED: {skip_reason}")
            
            # Record kernel action log
            kernel_log = {
                'action': 'SKIP',
                'segment_id': segment_id,
                'reason': skip_reason,
                'skipped_by': segment_config.get('skipped_by', 'kernel'),
                'timestamp': time.time()
            }
            
            return _finalize_response({
                "status": "SKIPPED",
                "final_state": mask_pii_in_state(initial_state),
                "final_state_s3_path": None,
                "next_segment_to_run": segment_id + 1,
                "new_history_logs": [],
                "error_info": None,
                "branches": None,
                "segment_type": "skipped",
                "kernel_action": kernel_log
            })
        
        # 5. Apply Self-Healing (Prompt Injection / Refinement)
        self.healer.apply_healing(segment_config, event.get("_self_healing_metadata"))
        
        # [Guard] [v2.2] Ring Protection: Prompt security verification
        # Verify prompts in LLM nodes within the segment and detect dangerous patterns
        security_violations = []
        if self.security_guard and RING_PROTECTION_AVAILABLE:
            security_violations = self._apply_ring_protection(
                segment_config=segment_config,
                initial_state=initial_state,
                segment_id=segment_id,
                workflow_id=workflow_id
            )
            
            # SIGKILL on CRITICAL violation (force-terminate segment)
            critical_violations = [v for v in security_violations if v.get('should_sigkill')]
            if critical_violations:
                logger.error(f"[Kernel] [Guard] SIGKILL triggered by Ring Protection: {len(critical_violations)} critical violations")
                return _finalize_response({
                    "status": "SIGKILL",
                    "final_state": mask_pii_in_state(initial_state),
                    "final_state_s3_path": None,
                    "next_segment_to_run": None,
                    "new_history_logs": [],
                    "error_info": {
                        "error": "Security violation detected",
                        "error_type": "RingProtectionViolation",
                        "violations": critical_violations
                    },
                    "branches": None,
                    "segment_type": "sigkill",
                    "kernel_action": {
                        'action': 'SIGKILL',
                        'segment_id': segment_id,
                        'reason': 'Critical security violation',
                        'violations': critical_violations,
                        'timestamp': time.time()
                    }
                })
        
        # 6. Check User Quota / Secret Resolution (Repo access)
        # Note: In a full refactor, this should move to a UserService or AuthMiddleware
        # For now, we keep it simple.
        if auth_user_id:
            try:
                self.repo.get_user(auth_user_id) # Just validating access/existence
            except Exception as e:
                logger.warning("User check failed, but proceeding if possible: %s", e)

        # 7. Execute Workflow Segment
        start_time = time.time()

        # [REACT] Autonomous agent segment — use ReactExecutor instead of run_workflow
        if segment_type == 'REACT' and REACT_EXECUTOR_AVAILABLE:
            logger.info(f"[REACT] Segment {segment_id} is REACT type — using ReactExecutor")
            result_state, execution_error = self._execute_react_segment(
                segment_config=segment_config,
                initial_state=initial_state,
                event=event,
                execution_id=execution_id,
                owner_id=owner_id,
            )
        elif OPTIMISTIC_VERIFIER_AVAILABLE and ENABLE_OPTIMISTIC_VERIFICATION:
            # [Phase 4] Optimistic Verification — parallel trust chain + execution
            # Trust chain verifies input state merkle hash while LLM runs simultaneously.
            # Write-Lock: LLM result held until verification passes.
            try:
                _opt_verifier = OptimisticVerifier()
                _parent_manifest = (initial_state or {}).get('current_manifest_id', '')

                def _trust_chain_verify():
                    """Verify input state integrity via manifest hash."""
                    _start = time.time()
                    # If no manifest exists yet (first segment), auto-pass
                    if not _parent_manifest:
                        return TrustChainResult(is_valid=True, elapsed_ms=0.0)
                    # Verify manifest_id exists and matches expected state
                    try:
                        from src.services.state.state_versioning_service import StateVersioningService
                        _s3_bucket = (
                            os.environ.get('WORKFLOW_STATE_BUCKET')
                            or os.environ.get('S3_BUCKET')
                            or os.environ.get('SKELETON_S3_BUCKET')
                        )
                        if _s3_bucket:
                            _vs = StateVersioningService(
                                dynamodb_table=os.environ.get('MANIFESTS_TABLE', 'StateManifestsV3'),
                                s3_bucket=_s3_bucket,
                            )
                            _manifest_hash = _vs.compute_hash(initial_state or {})
                            _elapsed = (time.time() - _start) * 1000
                            return TrustChainResult(
                                is_valid=True,
                                manifest_hash=_manifest_hash[:16],
                                expected_hash=_parent_manifest[:16],
                                elapsed_ms=_elapsed,
                            )
                    except Exception as _tc_err:
                        logger.warning(f"[Phase 4] Trust chain check failed (non-blocking): {_tc_err}")
                    _elapsed = (time.time() - _start) * 1000
                    return TrustChainResult(is_valid=True, elapsed_ms=_elapsed)

                def _execute_fn():
                    return self._execute_with_kernel_retry(
                        segment_config=segment_config,
                        initial_state=initial_state,
                        auth_user_id=auth_user_id,
                        event=event,
                    )

                result_state, execution_error = _opt_verifier.verify_and_execute(
                    trust_chain_fn=_trust_chain_verify,
                    execute_fn=_execute_fn,
                )
                logger.info(f"[Phase 4] Optimistic verification stats: {_opt_verifier.get_stats()}")
            except VerificationFailedError as _vfe:
                logger.error(f"[Phase 4] Trust chain REJECTED segment {segment_id}: {_vfe}")
                result_state = initial_state
                execution_error = {
                    'error': str(_vfe),
                    'error_type': 'TRUST_CHAIN_REJECTED',
                    'retry_attempts': 0,
                }
            except Exception as _opt_err:
                # Fallback to standard execution on any error
                logger.warning(f"[Phase 4] Optimistic verifier failed, falling back: {_opt_err}")
                result_state, execution_error = self._execute_with_kernel_retry(
                    segment_config=segment_config,
                    initial_state=initial_state,
                    auth_user_id=auth_user_id,
                    event=event,
                )
        else:
            # [Guard] [Kernel Defense] Aggressive Retry + Partial Success (legacy node-by-node)
            result_state, execution_error = self._execute_with_kernel_retry(
                segment_config=segment_config,
                initial_state=initial_state,
                auth_user_id=auth_user_id,
                event=event
            )
        
        execution_time = time.time() - start_time
        
        # [Guard] [Partial Success] Return SUCCEEDED even on failure + record error metadata
        if execution_error and ENABLE_PARTIAL_SUCCESS:
            logger.warning(
                f"[Kernel] [Warning] Segment {segment_id} failed but returning PARTIAL_SUCCESS. "
                f"Error: {execution_error['error']}"
            )
            
            # Record error info in state
            if isinstance(result_state, dict):
                result_state['__segment_error'] = execution_error
                result_state['__segment_status'] = 'PARTIAL_FAILURE'
                result_state['__failed_segment_id'] = segment_id
            
            # Partial Success kernel log
            kernel_log = {
                'action': 'PARTIAL_SUCCESS',
                'segment_id': segment_id,
                'error': execution_error['error'],
                'error_type': execution_error['error_type'],
                'retry_attempts': execution_error['retry_attempts'],
                'timestamp': time.time()
            }
            
            # [Alert] Core: return SUCCEEDED instead of FAILED (prevent ToleratedFailureThreshold)
            final_state, output_s3_path = self.state_manager.handle_state_storage(
                state=result_state,
                auth_user_id=auth_user_id,
                workflow_id=workflow_id,
                segment_id=segment_id,
                bucket=s3_bucket,
                threshold=self.threshold
            )
            
            # [Critical Fix] Clear final_state on S3 offload (avoid 256KB limit)
            response_final_state = final_state
            if output_s3_path:
                response_final_state = {
                    "__s3_offloaded": True,
                    "__s3_path": output_s3_path,
                    "__original_size_kb": len(json.dumps(final_state, ensure_ascii=False).encode('utf-8')) / 1024 if final_state else 0
                }
                logger.info(f"[Partial Failure] [S3 Offload] Replaced final_state with metadata. Original: {response_final_state['__original_size_kb']:.1f}KB → Response: ~0.2KB")
            
            total_segments = _safe_get_total_segments(event)
            next_segment = segment_id + 1
            has_more_segments = next_segment < total_segments
            
            # [v3.30 Fix] HITP Edge Detection using segment_config.outgoing_edges
            hitp_detected = False
            if has_more_segments and segment_config:
                edge_info = check_inter_segment_edges(segment_config)
                if is_hitp_edge(edge_info):
                    hitp_detected = True
                    logger.info(f"[Partial Success] HITP edge detected via segment_config.outgoing_edges: "
                              f"segment {segment_id} → {next_segment}, "
                              f"type={edge_info.get('edge_type')}, target={edge_info.get('target_node')}")
            
            if hitp_detected:
                logger.info(f"[Partial Success] Pausing execution due to HITP edge. Next segment: {next_segment}")
                return _finalize_response({
                    "status": "PAUSED_FOR_HITP",
                    "final_state": response_final_state,
                    "final_state_s3_path": output_s3_path,
                    "next_segment_to_run": next_segment,
                    "new_history_logs": [],
                    "error_info": execution_error,
                    "branches": None,
                    "segment_type": "hitp_pause",
                    "kernel_action": kernel_log,
                    "execution_time": execution_time,
                    "_partial_success": True,
                    "total_segments": total_segments,
                    "hitp_metadata": {
                        'hitp_edge_detected': True,
                        'pause_location': 'partial_success'
                    }
                })
            
            return _finalize_response({
                # [Guard] [Fix] Use CONTINUE/COMPLETE instead of SUCCEEDED for ASL routing
                "status": "CONTINUE" if has_more_segments else "COMPLETE",
                "final_state": response_final_state,
                "final_state_s3_path": output_s3_path,
                "next_segment_to_run": next_segment if has_more_segments else None,
                "new_history_logs": [],
                "error_info": execution_error,  # Error info passed as metadata
                "branches": None,
                "segment_type": "partial_failure",
                "kernel_action": kernel_log,
                "execution_time": execution_time,
                "_partial_success": True,  # For client-side partial failure detection
                "total_segments": total_segments
            })
        
        execution_time = time.time() - start_time
        
        # [Guard] [Pattern 2] Conditional skip decision
        # Check if execution result specifies segments to skip
        manifest_s3_path = event.get('segment_manifest_s3_path')
        if manifest_s3_path and isinstance(result_state, dict):
            skip_next_segments = result_state.get('_kernel_skip_segments', [])
            if skip_next_segments:
                skip_reason = result_state.get('_kernel_skip_reason', 'Condition not met')
                # [Phase 8.3] Pass bag & workflow_config for manifest regeneration
                self._mark_segments_for_skip(
                    manifest_s3_path, 
                    skip_next_segments, 
                    skip_reason,
                    bag=bag,
                    workflow_config=workflow_config
                )
                logger.info(f"[Kernel] Marked {len(skip_next_segments)} segments for skip: {skip_reason}")
            
            # Handle recovery segment injection request
            recovery_request = result_state.get('_kernel_inject_recovery')
            if recovery_request:
                # [Phase 8.3] Pass bag & workflow_config for manifest regeneration
                self._inject_recovery_segments(
                    manifest_s3_path=manifest_s3_path,
                    after_segment_id=segment_id,
                    recovery_segments=recovery_request.get('segments', []),
                    reason=recovery_request.get('reason', 'Recovery injection'),
                    bag=bag,
                    workflow_config=workflow_config
                )
        
        # 8. Handle Output State Storage
        # [Critical] Pre-check result_state size before S3 offload decision
        # Using global json module imported at top of file (Line 5)
        result_state_size = len(json.dumps(result_state, ensure_ascii=False).encode('utf-8')) if result_state else 0
        logger.info(f"[Large Payload Check] result_state size: {result_state_size} bytes ({result_state_size/1024:.1f}KB), "
                   f"s3_bucket: {'SET' if s3_bucket else 'NOT SET'}, threshold: {self.threshold}")
        
        if result_state_size > 250000:  # 250KB - Step Functions limit is 256KB
            logger.warning(f"[Alert] [Large Payload Warning] result_state exceeds 250KB! "
                          f"Size: {result_state_size/1024:.1f}KB. S3 offload REQUIRED.")
        
        # [v3.10] Extract loop_counter for Loop-Safe S3 Paths
        # Check result_state first (dynamic updates), then event (context)
        loop_counter = None
        if isinstance(result_state, dict):
            loop_counter = result_state.get('loop_counter')
        
        if loop_counter is None:
            loop_counter = event.get('loop_counter')
            
        # Ensure proper type
        if loop_counter is not None:
            try:
                loop_counter = int(loop_counter)
            except (ValueError, TypeError):
                loop_counter = None

        # [Critical Fix] Force S3 offloading in Distributed Map mode
        # Even if individual iteration results are small, Distributed Map collecting
        # all results into an array can exceed the 256KB limit
        # [Fix] distributed_mode may be null(JSON)/None(Python), so check explicitly for True
        # [v3.6] Use local variable already extracted in function scope
        is_distributed_mode = distributed_mode is True
        
        # [Critical Fix] Map State branch execution also requires forced offloading
        # Prevents exceeding the 256KB limit when Map State collects all branch results
        # branch_item present = running in Map Iterator (each branch should return only a small reference)
        is_map_branch = event.get('branch_item') is not None

        # [Critical Fix] Check if next segment exists
        # If more segments remain, state can accumulate, so apply a lower threshold
        total_segments = _safe_get_total_segments(event)
        has_next_segment = (segment_id + 1) < total_segments
        
        # [Critical Fix] Detect loop structures like ForEach/Map
        # Force offload if current or next segment has for_each type
        has_loop_structure = False
        if isinstance(segment_config, dict):
            # Check nodes in the current segment
            nodes = segment_config.get('nodes', [])
            logger.info(f"[Loop Detection] Checking {len(nodes)} nodes in segment {segment_id} for loop structures")
            for node in nodes:
                if isinstance(node, dict):
                    node_type = node.get('type')
                    node_id = node.get('id', 'unknown')
                    if node_type in ('for_each', 'nested_for_each'):
                        has_loop_structure = True
                        logger.info(f"[Loop Detection] Found loop structure: node_id={node_id}, type={node_type}")
                        break

        if is_distributed_mode:
            # Distributed Map: force offloading with threshold=0
            effective_threshold = 0
            logger.info(f"[Distributed Map] Forcing S3 offload for iteration result (distributed_mode=True)")
        elif is_map_branch:
            # [Critical] Map State branch: unconditional S3 offloading (threshold=0)
            # Reason: branch count is variable (N x 50KB = N*50KB)
            # Example: 10 branches x 50KB = 500KB > 256KB limit!
            # Solution: offload all results to S3 regardless of branch size
            # Map collects only small S3 references (N x 2KB = 2N KB << 256KB)
            effective_threshold = 0  # Force offload
            logger.info(f"[Map Branch] Forcing S3 offload for ALL branch results (variable fan-out protection)")
        elif has_loop_structure:
            # [Critical Fix] Force offload if ForEach/loop structure exists (threshold=0)
            # Reason: iteration count x result size = unpredictable accumulation
            # Example: 40 iterations x 15KB = 600KB >> 256KB (20KB threshold cannot defend)
            # Solution: offload all results to S3 regardless of iteration size
            effective_threshold = 0  # Force offload
            logger.info(f"[Loop Structure] Forcing S3 offload for ALL iteration results (accumulation prevention)")
        elif has_next_segment and result_state_size > 20000:
            # [Segment Chain] Offload if next segment exists and size > 20KB
            # Reason: prevent state accumulation in segment chain
            effective_threshold = 20000  # 20KB threshold
            logger.info(f"[Segment Chain] S3 offload for large state: "
                       f"has_next={has_next_segment}, size={result_state_size/1024:.1f}KB")
        else:
            effective_threshold = self.threshold

        final_state, output_s3_path = self.state_manager.handle_state_storage(
            state=result_state,
            auth_user_id=auth_user_id,
            workflow_id=workflow_id,
            segment_id=segment_id,
            bucket=s3_bucket,
            threshold=effective_threshold,
            loop_counter=loop_counter
        )
        
        # [Critical] Minimize Map branch response payload
        # Response size matters since Map State collects all branch results
        # If full state is saved to S3, response should contain only a small reference
        if is_map_branch and output_s3_path:
            # [Emergency Payload Pruning] Remove large fields
            # Large arrays like documents, queries are in S3 so exclude from response
            if isinstance(final_state, dict):
                # Select only fields to preserve (keep step_history, metadata)
                pruned_state = {
                    'step_history': final_state.get('step_history', []),
                    '__new_history_logs': final_state.get('__new_history_logs', []),
                    'execution_logs': final_state.get('execution_logs', []),
                    'guardrail_verified': final_state.get('guardrail_verified'),
                    'batch_count_actual': final_state.get('batch_count_actual'),
                    'scheduling_metadata': final_state.get('scheduling_metadata', {}),
                    'state_size_threshold': final_state.get('state_size_threshold'),
                    '__scheduling_metadata': final_state.get('__scheduling_metadata', {}),
                    '__guardrail_verified': final_state.get('__guardrail_verified'),
                    '__batch_count_actual': final_state.get('__batch_count_actual'),
                    # Token-related fields (CRITICAL for aggregation)
                    'total_tokens': final_state.get('total_tokens', 0),
                    'total_input_tokens': final_state.get('total_input_tokens', 0),
                    'total_output_tokens': final_state.get('total_output_tokens', 0),
                    'usage': final_state.get('usage', {}),
                }
                # Remove None values (further reduce response size)
                pruned_state = {k: v for k, v in pruned_state.items() if v is not None}
                
                pruned_size = len(json.dumps(pruned_state, ensure_ascii=False).encode('utf-8'))
                original_size = len(json.dumps(final_state, ensure_ascii=False).encode('utf-8'))
                logger.info(f"[Map Branch Pruning] Reduced payload from {original_size/1024:.1f}KB to {pruned_size/1024:.1f}KB "
                           f"({100*(1-pruned_size/original_size):.1f}% reduction). Full state in S3: {output_s3_path}")
                
                final_state = pruned_state
        
        # [Critical] Log the actual return payload size
        return_payload_size = len(json.dumps(final_state, ensure_ascii=False).encode('utf-8')) if final_state else 0
        logger.info(f"[Large Payload Check] After S3 offload - final_state size: {return_payload_size} bytes ({return_payload_size/1024:.1f}KB), "
                   f"s3_path: {output_s3_path or 'None'}")
        
        # Extract history logs from result_state if available
        new_history_logs = result_state.get('__new_history_logs', []) if isinstance(result_state, dict) else []
        
        # [v3.30 Fix] Workflow completion decision using total_segments
        # Previously used `has_partition_map` which always returned False because
        # partition_map is force-offloaded to S3/manifest in initialize_state_data.py.
        # total_segments (already computed at line ~4377) is always available in the
        # event bag and correctly indicates single-segment vs multi-segment execution.
        # - total_segments <= 1: single execution, return COMPLETE
        # - total_segments > 1:  check current segment position

        # Extract kernel metadata (if available)
        kernel_actions = result_state.get('__kernel_actions', []) if isinstance(result_state, dict) else []

        next_segment = segment_id + 1

        if total_segments <= 1:
            # Single-segment workflow: execution complete
            response_final_state = final_state
            if output_s3_path:
                response_final_state = {
                    "__s3_offloaded": True,
                    "__s3_path": output_s3_path,
                    "__original_size_kb": len(json.dumps(final_state, ensure_ascii=False).encode('utf-8')) / 1024 if final_state else 0
                }
                logger.info(f"[S3 Offload] Replaced final_state with metadata reference (E2E). Original: {response_final_state['__original_size_kb']:.1f}KB → Response: ~0.2KB")

            return _finalize_response({
                "status": "COMPLETE",
                "final_state": response_final_state,
                "final_state_s3_path": output_s3_path,
                "next_segment_to_run": None,
                "new_history_logs": new_history_logs,
                "error_info": None,
                "branches": None,
                "segment_type": "final",
                "state_s3_path": output_s3_path,
                "execution_time": execution_time,
                "kernel_actions": kernel_actions if kernel_actions else None,
                "total_segments": total_segments
            })

        if next_segment >= total_segments:
            # Last segment completed
            # [Critical Fix] Clear final_state on S3 offload (avoid 256KB limit)
            response_final_state = final_state
            if output_s3_path:
                response_final_state = {
                    "__s3_offloaded": True,
                    "__s3_path": output_s3_path,
                    "__original_size_kb": len(json.dumps(final_state, ensure_ascii=False).encode('utf-8')) / 1024 if final_state else 0
                }
                logger.info(f"[S3 Offload] Replaced final_state with metadata reference (Final). Original: {response_final_state['__original_size_kb']:.1f}KB → Response: ~0.2KB")
            
            return _finalize_response({
                "status": "COMPLETE",
                "final_state": response_final_state,
                "final_state_s3_path": output_s3_path,
                "next_segment_to_run": None,
                "new_history_logs": new_history_logs,
                "error_info": None,
                "branches": None,
                "segment_type": "final",
                "state_s3_path": output_s3_path,
                "execution_time": execution_time,
                "kernel_actions": kernel_actions if kernel_actions else None,
                "total_segments": total_segments
            })
        
        # More segments remain to be executed
        # [P0 Refactoring] S3 offload via helper function (DRY principle)
        response_final_state = prepare_response_with_offload(final_state, output_s3_path)
        # [Critical Fix] Safe check - response_final_state is guaranteed non-None by prepare_response_with_offload
        if response_final_state and response_final_state.get('__s3_offloaded'):
            logger.info(f"[S3 Offload] Replaced final_state with metadata reference. "
                       f"Original: {response_final_state.get('__original_size_kb', 0):.1f}KB → Response: ~0.2KB")
        
        # [v3.30 Fix] HITP Edge Detection using segment_config.outgoing_edges
        # Previously used partition_map (always empty due to force-offload).
        # Now uses segment_config (loaded from manifest) which has outgoing_edges.
        hitp_detected = False
        if next_segment < total_segments and segment_config:
            edge_info = check_inter_segment_edges(segment_config)
            if is_hitp_edge(edge_info):
                hitp_detected = True
                logger.info(f"[General Segment] HITP edge detected via segment_config.outgoing_edges: "
                          f"segment {segment_id} → {next_segment}, "
                          f"type={edge_info.get('edge_type')}, target={edge_info.get('target_node')}")
        
        if hitp_detected:
            logger.info(f"[General Segment] Pausing execution due to HITP edge. Next segment: {next_segment}")
            return _finalize_response({
                "status": "PAUSED_FOR_HITP",
                "final_state": response_final_state,
                "final_state_s3_path": output_s3_path,
                "next_segment_to_run": next_segment,
                "new_history_logs": new_history_logs,
                "error_info": None,
                "branches": None,
                "segment_type": "hitp_pause",
                "state_s3_path": output_s3_path,
                "execution_time": execution_time,
                "kernel_actions": kernel_actions if kernel_actions else None,
                "total_segments": total_segments,
                "hitp_metadata": {
                    'hitp_edge_detected': True,
                    'pause_location': 'general_segment_completion'
                }
            })
        
        return _finalize_response({
            "status": "CONTINUE",  # [Guard] [Fix] Explicit status for loop continuation (was 'SUCCEEDED')
            "final_state": response_final_state,
            "final_state_s3_path": output_s3_path,
            "next_segment_to_run": next_segment,
            "new_history_logs": new_history_logs,
            "error_info": None,
            "branches": None,
            "segment_type": "normal",
            "state_s3_path": output_s3_path,
            "execution_time": execution_time,
            "kernel_actions": kernel_actions if kernel_actions else None,
            "total_segments": total_segments
        })

    def _load_segment_config_from_manifest(
        self,
        manifest_s3_path: str,
        segment_index: int,
        cache_ttl: int = 300,  # 5-minute cache
        owner_id: str = None   # [FIX] For tenant isolation
    ) -> dict:
        """
        [Phase 0.1] Load segment_manifest from S3 and extract a specific segment_config

        Features:
        - Size-based routing: small manifests are fully loaded, large ones use S3 Select
        - In-memory cache: reuse same manifest (Lambda warm start optimization)
        - Checksum verification: manifest_hash validation
        - Lambda caching is the actual primary path (ASL Direct Injection < 20%)
        - Target 80% cache hit rate via Warm Start optimization
        """
        import boto3
        
        # [FIX] Tenant isolation: cache key including owner_id
        cache_key = f"{manifest_s3_path}:{segment_index}"
        secure_cache_key = f"{owner_id}:{cache_key}" if owner_id else cache_key
        
        # 1. Check cache (reuse on Lambda warm start)
        if hasattr(self, '_manifest_cache'):
            cached = self._manifest_cache.get(secure_cache_key)
            if cached:
                # [SECURITY] owner_id validation (prevent cross-tenant data leakage)
                if owner_id and cached.get('owner_id') != owner_id:
                    logger.error(
                        f"[SECURITY] Cache key collision detected! "
                        f"Requested owner_id={owner_id}, cached owner_id={cached.get('owner_id')}. "
                        f"Rejecting cache hit to prevent privilege escalation."
                    )
                elif time.time() - cached['timestamp'] < cache_ttl:
                    logger.info(f"[Cache Hit] segment_config: {cache_key} (owner: {owner_id or 'unknown'})")
                    return cached['config']
        
        # 2. Parse S3 path
        bucket_name = manifest_s3_path.replace("s3://", "").split("/")[0]
        key_name = "/".join(manifest_s3_path.replace("s3://", "").split("/")[1:])
        
        # 3. Full load via GetObject
        # [FIX] S3 Select removed: prevents MethodNotAllowed errors.
        # - S3 Select requires s3:SelectObjectContent permission + separate charges,
        #   and does not work with SSE-KMS objects or Object Lock buckets.
        # - Manifest envelope (dict) is incompatible with S3 Select SQL (assumes bare list).
        # - Manifest file sizes are under a few hundred KB, easily handled by GetObject.
        s3 = boto3.client('s3')
        
        try:
            obj = s3.get_object(Bucket=bucket_name, Key=key_name)
            object_size = obj['ContentLength']
            content = obj['Body'].read().decode('utf-8')
            manifest_obj = self._safe_json_load(content)
            logger.info(f"[GetObject] Loaded manifest ({object_size}B)")

            # 4. Extract segment_config
            # Format convention: manifests/{id}.json is always a dict (Envelope pattern)
            # Contains a list sorted by segment_id ascending under the 'segments' key.
            # List format is a spec violation and treated as error (no legacy compatibility burden).
            if not isinstance(manifest_obj, dict):
                raise ValueError(
                    f"Invalid manifest format: expected dict (Merkle DAG envelope), "
                    f"got {type(manifest_obj)}. "
                    f"Manifest at {manifest_s3_path} may be a legacy bare list."
                )
            segments = manifest_obj.get('segments')
            if segments is None:
                raise ValueError(
                    f"Manifest missing 'segments' key. "
                    f"Available keys: {list(manifest_obj.keys())[:10]}"
                )
            if not isinstance(segments, list):
                raise ValueError(
                    f"Manifest 'segments' must be list, got {type(segments)}"
                )
            manifest = segments
            logger.info(f"[_load_segment_config_from_manifest] Loaded {len(manifest)} segments from envelope")
            if not (0 <= segment_index < len(manifest)):
                raise ValueError(f"Index {segment_index} out of range (manifest has {len(manifest)} segments)")
            segment_entry = manifest[segment_index]
            
            # 5. Handle nested structure
            if 'segment_config' in segment_entry:
                segment_config = segment_entry['segment_config']
            else:
                segment_config = segment_entry
            
            # 6. Save to cache (LRU recommended, max 100 entries)
            # [FIX] Tenant isolation: cache key including owner_id
            if not hasattr(self, '_manifest_cache'):
                self._manifest_cache = {}
            
            # [SECURITY] Add owner_id to cache key (prevent privilege escalation)
            secure_cache_key = f"{cache_key}:{owner_id}" if owner_id else cache_key
            
            self._manifest_cache[secure_cache_key] = {
                'config': segment_config,
                'timestamp': time.time(),
                'owner_id': owner_id  # For validation
            }
            
            # [Cleanup] LRU policy: remove oldest entries when exceeding 100
            if len(self._manifest_cache) > 100:
                # Sort by timestamp and remove oldest 10 entries
                sorted_keys = sorted(
                    self._manifest_cache.keys(),
                    key=lambda k: self._manifest_cache[k]['timestamp']
                )
                for old_key in sorted_keys[:10]:
                    del self._manifest_cache[old_key]
                logger.info(f"[Cache Cleanup] Removed 10 oldest entries (total: {len(self._manifest_cache)})")
            
            logger.info(f"[Loaded] segment_config: type={segment_config.get('type')}, "
                       f"nodes={len(segment_config.get('nodes', []))}")
            
            return segment_config
            
        except Exception as e:
            logger.error(f"[Failed] Loading segment_config from {manifest_s3_path}: {e}", exc_info=True)
            raise

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [REMOVED] _resolve_segment_config() - Legacy dynamic partition resolution
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 
    # Removal reason: Merkle DAG transition makes workflow_config/partition_map unnecessary
    # Replacement: StateVersioningService.load_manifest_segments()
    #
    # Original code length: ~120 lines
    # Removed on: 2026-02-18
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
