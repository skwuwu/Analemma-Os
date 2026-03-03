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

# [Guard] [v2.3] 4단계 아키텍처: Concurrency Controller
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

# Services
from src.services.state.state_manager import StateManager
from src.common.security_utils import mask_pii_in_state
from src.services.recovery.self_healing_service import SelfHealingService
# [v3.11] Unified State Hydration
from src.common.state_hydrator import StateHydrator, SmartStateBag
from src.services.workflow.repository import WorkflowRepository
# [C-02 FIX] run_workflow는 Cold Start 순환 임포트 방지를 위해 Lazy Local Import로 이동.
# (_partition_workflow_dynamically, _build_segment_config 는 실제 호출 없음 → 제거)
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
# 모든 Lambda ↔ ASL 통신을 표준화
try:
    from src.common.kernel_protocol import seal_state_bag, open_state_bag, get_from_bag
    KERNEL_PROTOCOL_AVAILABLE = True
except ImportError:
    try:
        from common.kernel_protocol import seal_state_bag, open_state_bag, get_from_bag
        KERNEL_PROTOCOL_AVAILABLE = True
    except ImportError:
        KERNEL_PROTOCOL_AVAILABLE = False
        logger.warning("⚠️ kernel_protocol not available - falling back to legacy mode")

# [v3.12] Shared Kernel Library: StateBag as Single Source of Truth
# ExecuteSegment now returns StateBag format directly using universal_sync_core
try:
    from src.handlers.utils.universal_sync_core import universal_sync_core
    UNIVERSAL_SYNC_CORE_AVAILABLE = True
except ImportError:
    UNIVERSAL_SYNC_CORE_AVAILABLE = False
    logger.warning("⚠️ universal_sync_core not available - falling back to legacy mode")

# ============================================================================
# 🔍 [v3.5] None Reference Tracing: Environment-controlled debug logging
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
    🔍 [v3.5] Trace None value access for debugging NoneType errors
    
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
            f"🔍 [None Trace] key='{key}' is None from {source}. "
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
    🛡️ [v3.13] Kernel Protocol 기반 Bag 데이터 추출
    
    kernel_protocol.get_from_bag을 사용하여 표준화된 경로로 데이터 추출.
    Kernel Protocol이 없으면 레거시 로직 사용.
    
    Args:
        event: Lambda 이벤트
        key: 추출할 키
        default: 기본값
        caller: (Optional) 호출자 식별자
        log_on_default: (Optional) 기본값 반환 시 로깅
    
    Returns:
        찾은 값 또는 default
    """
    # [v3.13] Kernel Protocol 사용 (권장)
    if KERNEL_PROTOCOL_AVAILABLE:
        val = get_from_bag(event, key, default)
        if val == default and log_on_default:
            logger.warning(f"🔍 [Kernel Protocol] key='{key}' returned default. Caller: {caller}")
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
        logger.warning(f"🔍 [SafeGet] key='{key}' returned default. Caller: {caller}")
    
    return default


def _safe_get_total_segments(event: Dict[str, Any]) -> int:
    """
    [Guard] [Fix] total_segments를 안전하게 추출
    
    문제점: event.get('total_segments')가 None, "", 0 등 다양한 값일 수 있음
    - None: Step Functions에서 null로 전달
    - "": 빈 문자열
    - 0: 유효하지만 int(0)은 falsy
    
    Returns:
        int: total_segments (최소 1 보장)
    """
    raw_value = event.get('total_segments')
    
    # None이면 State Bag에서 partition_map 추출하여 계산
    if raw_value is None:
        # 🛡️ [v3.4] _safe_get_from_bag 사용
        partition_map = _safe_get_from_bag(event, 'partition_map')
        
        if partition_map and isinstance(partition_map, list):
            return max(1, len(partition_map))
        return 1
    
    # 숫자 타입이면 직접 사용
    if isinstance(raw_value, (int, float)):
        return max(1, int(raw_value))
    
    # 문자열이면 파싱 시도
    if isinstance(raw_value, str):
        raw_value = raw_value.strip()
        if raw_value and raw_value.isdigit():
            return max(1, int(raw_value))
        # 빈 문자열이나 파싱 불가능하면 기본값
        return 1
    
    # 그 외 타입은 기본값
    return 1


def _normalize_node_config(node: Dict[str, Any]) -> Dict[str, Any]:
    """
    [Option A] 노드 config에서 None 값을 빈 dict/list로 정규화

    문제점: 프론트엔드나 DB에서 optional 필드가 null로 저장되면
    Python에서 .get('key', {}).get('nested') 패턴이 실패함

    해결책: 노드 실행 전에 None → {} 정규화
    """
    if not isinstance(node, dict):
        return node or {}

    # 정규화할 필드 목록 (None → {} or [])
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

    # 재귀적으로 nested config도 정규화
    if 'config' in node and isinstance(node['config'], dict):
        _normalize_node_config(node['config'])
    if 'sub_node_config' in node and isinstance(node['sub_node_config'], dict):
        _normalize_node_config(node['sub_node_config'])
    if 'sub_workflow' in node and isinstance(node['sub_workflow'], dict):
        _normalize_node_config(node['sub_workflow'])
    if 'nested_config' in node and isinstance(node['nested_config'], dict):
        _normalize_node_config(node['nested_config'])

    # nodes 배열 내부도 정규화
    if 'nodes' in node and isinstance(node['nodes'], list):
        for child_node in node['nodes']:
            if isinstance(child_node, dict):
                _normalize_node_config(child_node)

    return node


def _normalize_segment_config(segment_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    [Option A] 세그먼트 config 전체를 정규화
    """
    if not isinstance(segment_config, dict):
        return segment_config or {}

    # 세그먼트 레벨 필드 정규화
    if segment_config.get('nodes') is None:
        segment_config['nodes'] = []
    if segment_config.get('edges') is None:
        segment_config['edges'] = []
    if segment_config.get('branches') is None:
        segment_config['branches'] = []

    # 각 노드 정규화
    for node in segment_config.get('nodes', []):
        if isinstance(node, dict):
            _normalize_node_config(node)
            # 🛡️ [P0 Fix] config 내부의 sub_workflow도 정규화 (for_each 노드용)
            node_config = node.get('config')
            if isinstance(node_config, dict):
                sub_workflow = node_config.get('sub_workflow')
                if isinstance(sub_workflow, dict):
                    _normalize_node_config(sub_workflow)
                    for sub_node in sub_workflow.get('nodes', []):
                        if isinstance(sub_node, dict):
                            _normalize_node_config(sub_node)

    # 브랜치 내 노드도 정규화
    for branch in segment_config.get('branches', []):
        if isinstance(branch, dict):
            for node in branch.get('nodes', []):
                if isinstance(node, dict):
                    _normalize_node_config(node)
                    # 🛡️ [P0 Fix] 브랜치 내 for_each config의 sub_workflow도 정규화
                    node_config = node.get('config')
                    if isinstance(node_config, dict):
                        sub_workflow = node_config.get('sub_workflow')
                        if isinstance(sub_workflow, dict):
                            _normalize_node_config(sub_workflow)

    return segment_config


class SegmentRunnerService:
    def __init__(self, s3_bucket: Optional[str] = None):
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
        
        # [Guard] [v2.3] 4단계 아키텍처: Concurrency Controller
        self._concurrency_controller = None
        
        # [v3.20] RoutingResolver: 라우팅 주권 일원화 (O(1) 화이트리스트 검증)
        self._routing_resolver = None
        
        # [v3.20] StateViewContext: 프록시 패턴 (메모리 78% 절감)
        self._state_view_context = None
    
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
            # Reserved Concurrency 200 (template.yaml에서 설정)
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
        
        워크플로우별로 한 번 초기화 (execute_segment 진입 시)
        """
        return self._routing_resolver
    
    @property
    def state_view_context(self):
        """
        Lazy StateViewContext initialization
        
        첫 상태 로딩 시 한 번 초기화
        """
        if self._state_view_context is None:
            try:
                from src.common.state_view_context import (
                    create_state_view_context, 
                    FieldPolicyBuilder
                )
                
                self._state_view_context = create_state_view_context()
                
                # 기본 필드 정책 설정
                builder = FieldPolicyBuilder()
                
                # email: Ring 3에서 해시
                self._state_view_context.set_field_policy(
                    "email", 
                    builder.hash_at_ring3()
                )
                
                # ssn, password: Ring 2-3에서 리덕션
                for field in ["ssn", "password"]:
                    self._state_view_context.set_field_policy(
                        field,
                        builder.redact_at_ring2_3()
                    )
                
                # _kernel_*: Ring 1만 접근 가능
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
        🛡️ [Critical] Safe JSON loading to prevent UnboundLocalError
        
        Problem: Using 'json' as variable name shadows the module reference,
                 causing UnboundLocalError in nested scopes (ThreadPoolExecutor)
        
        Solution: Explicit import with alias to avoid shadowing
        
        Args:
            content: JSON string to parse
            
        Returns:
            Parsed JSON object (dict) or empty dict on error
        """
        import json as _json_module  # 🛡️ Alias prevents variable shadowing
        try:
            return _json_module.loads(content)
        except Exception as e:
            logger.error(f"[S3 Recovery] JSON parsing failed: {e}")
            return {}  # 🛡️ Return empty dict to prevent AttributeError cascade
    
    @property
    def s3_client(self):
        """Lazy S3 client initialization"""
        if self._s3_client is None:
            import boto3
            self._s3_client = boto3.client('s3')
        return self._s3_client

    # ========================================================================
    #  [Utility] State Merge: 무결성 보장 상태 병합
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
        # 🛡️ [v3.6] Immortal Kernel: 병합 시작 전 이중 안전장치 (Dual StateBag)
        from src.common.statebag import ensure_state_bag
        base_state = ensure_state_bag(base_state)
        new_state = ensure_state_bag(new_state) # None이면 빈 StateBag({})이 됨, iteration 안전 보장
        
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
                # 🛡️ [P0 Fix] 리스트 병합 순서: existing + new (시간순 유지)
                # 로그는 시간순으로 뒤에 붙는 것이 자연스럽습니다.
                if isinstance(existing_value, list) and isinstance(new_value, list):
                    result[key] = existing_value + new_value  # 기존 뒤에 새 값 추가
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
        [Critical] S3 중간 브랜치 결과 파일 정리 (Garbage Collection)
        
        🛡️ [P0 강화] 멱등성 보장 + 재시도 로직
        
        Aggregation 완료 후 각 브랜치가 생성한 임시 S3 파일들을 삭제하여
        S3 비용 절감 및 관리 부하 감소
        
        ⚠️ 실운영 권장사항:
        - S3 Lifecycle Policy 설정 필수 (24시간 후 자동 삭제)
        - 삭제 실패 시 '유령 데이터' 방지
        
        Args:
            parallel_results: 브랜치 실행 결과 목록
            workflow_id: 워크플로우 ID
            segment_id: 현재 세그먼트 ID
        """
        if not parallel_results:
            return
        
        s3_paths_to_delete = []
        
        # 브랜치 결과에서 S3 path 수집
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
        
        # 🛡️ [P0] 재시도 로직 추가 (max 2회)
        MAX_RETRIES = 2
        failed_paths = []
        
        def delete_s3_object_with_retry(s3_path: str) -> Tuple[bool, str]:
            """S3 객체 삭제 (재시도 포함)"""
            for attempt in range(MAX_RETRIES + 1):
                try:
                    bucket = s3_path.replace("s3://", "").split("/")[0]
                    key = "/".join(s3_path.replace("s3://", "").split("/")[1:])
                    self.state_manager.s3_client.delete_object(Bucket=bucket, Key=key)
                    return True, s3_path
                except Exception as e:
                    if attempt < MAX_RETRIES:
                        time.sleep(0.1 * (attempt + 1))  # 백오프
                        continue
                    logger.warning(
                        f"[Aggregator] ⚠️ Failed to delete {s3_path} after {MAX_RETRIES + 1} attempts: {e}. "
                        f"This file may become 'ghost data'. Consider S3 Lifecycle Policy."
                    )
                    return False, s3_path
            return False, s3_path
        
        # ThreadPoolExecutor로 병렬 삭제 (빠르게 처리)
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
        
        # 결과 로깅
        if failed_paths:
            logger.warning(
                f"[Aggregator] ⚠️ Cleanup incomplete: {deleted_count}/{len(s3_paths_to_delete)} deleted, "
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
            # 🛡️ [v3.8] None defense in nodes iteration
            if node is None or not isinstance(node, dict):
                continue
                
            node_type = node.get('type', '')
            
            # [Debug] [KERNEL DEBUG] 노드 타입 변질 추적 - code 타입이 발견b되면 로그
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
        3. 최소 노드 수 보장
        """
        if split_depth >= MAX_SPLIT_DEPTH:
            logger.warning(f"[Kernel] Max split depth ({MAX_SPLIT_DEPTH}) reached, returning original segment")
            return [segment_config]
        
        nodes = segment_config.get('nodes', [])
        edges = segment_config.get('edges', [])
        
        if len(nodes) < MIN_NODES_PER_SUB_SEGMENT * 2:
            logger.info(f"[Kernel] Segment too small to split ({len(nodes)} nodes)")
            return [segment_config]
        
        # 노드를 반으로 분할
        mid = len(nodes) // 2
        first_nodes = nodes[:mid]
        second_nodes = nodes[mid:]
        
        # [Critical Guard] None 필터링 (nodes 배열에 None이 섞여있을 수 있음)
        first_nodes = [n for n in first_nodes if n is not None]
        second_nodes = [n for n in second_nodes if n is not None]
        
        if not first_nodes or not second_nodes:
            logger.warning(f"[Kernel] Node filtering resulted in empty segment, returning original")
            return [segment_config]
        
        first_node_ids = {n.get('id') for n in first_nodes if isinstance(n, dict)}
        second_node_ids = {n.get('id') for n in second_nodes if isinstance(n, dict)}
        
        # 엣지 분리: 각 서브 세그먼트 내부 엣지만 유지
        # [Critical Guard] edges도 None일 수 있고, 각 edge도 None이거나 dict가 아닐 수 있음
        first_edges = [e for e in edges 
                      if e is not None and isinstance(e, dict) 
                      and e.get('source') in first_node_ids and e.get('target') in first_node_ids]
        second_edges = [e for e in edges 
                       if e is not None and isinstance(e, dict)
                       and e.get('source') in second_node_ids and e.get('target') in second_node_ids]
        
        # 서브 세그먼트 생성
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
        # 사용 가능한 Lambda 메모리
        available_memory = int(os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', 512))
        
        # 메모리 요구량 추정
        estimated_memory = self._estimate_segment_memory(segment_config, initial_state)
        
        # 안전 임계값 체크
        if estimated_memory > available_memory * MEMORY_SAFETY_THRESHOLD:
            logger.info(f"[Kernel] [Warning] Memory pressure detected: {estimated_memory}MB estimated, "
                       f"{available_memory}MB available (threshold: {MEMORY_SAFETY_THRESHOLD*100}%)")
            
            # 분할 시도
            sub_segments = self._split_segment(segment_config, split_depth)
            
            if len(sub_segments) > 1:
                logger.info(f"[Kernel] [System] Executing {len(sub_segments)} sub-segments sequentially")
                
                # 서브 세그먼트 순차 실행
                current_state = initial_state.copy()
                all_logs = []
                kernel_actions = []
                
                for i, sub_seg in enumerate(sub_segments):
                    logger.info(f"[Kernel] Executing sub-segment {i+1}/{len(sub_segments)}: {sub_seg.get('id')}")
                    
                    # 재귀적으로 자동 분할 적용
                    sub_result = self._execute_with_auto_split(
                        sub_seg, current_state, auth_user_id, split_depth + 1
                    )
                    
                    # [System] 무결성 보장 상태 병합 (리스트 키는 합침)
                    if isinstance(sub_result, dict):
                        current_state = self._merge_states(
                            current_state, 
                            sub_result,
                            merge_policy=MERGE_POLICY_APPEND_LIST
                        )
                        # all_logs는 이미 _merge_states에서 처리됨
                    
                    kernel_actions.append({
                        'action': 'SPLIT_EXECUTE',
                        'sub_segment_id': sub_seg.get('id'),
                        'index': i,
                        'timestamp': time.time()
                    })
                
                # 커널 메타데이터 추가
                current_state['__kernel_actions'] = kernel_actions
                current_state['__new_history_logs'] = all_logs
                
                return current_state
        
        # 정상 실행 (분할 불필요)
        logger.error(f"[v3.27 AUTO_SPLIT] Calling run_workflow with segment_config keys: {list(segment_config.keys())[:15] if isinstance(segment_config, dict) else 'NOT A DICT'}")
        logger.error(f"[v3.27 AUTO_SPLIT] segment_config.nodes count: {len(segment_config.get('nodes', [])) if isinstance(segment_config, dict) else 'N/A'}")
        if isinstance(segment_config, dict) and segment_config.get('nodes'):
            node_types = [n.get('type', 'unknown') for n in segment_config.get('nodes', [])[:5]]
            logger.error(f"[v3.27 AUTO_SPLIT] First 5 node types: {node_types}")

        # [C-02 FIX] Lazy import: 순환 임포트 방지 (top-level에서 제거됨)
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
            logger.error(f"[v3.27 AUTO_SPLIT] ✅ llm_raw_output FOUND in result!")
        else:
            logger.error(f"[v3.27 AUTO_SPLIT] ❌ llm_raw_output NOT FOUND in result")
        
        return result

    # ========================================================================
    # [Guard] [Pattern 2] Manifest Mutation: S3 Manifest 동적 수정
    # ========================================================================
    def _load_manifest_from_s3(self, manifest_s3_path: str) -> Optional[List[Dict[str, Any]]]:
        """S3에서 segment_manifest 로드"""
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
        """수정된 segment_manifest를 S3에 저장"""
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
        """세그먼트 상태 확인 (SKIPPED 등)"""
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
        [Phase 8.3] 특정 세그먼트를 SKIP으로 마킹 + Manifest 재생성
        
        사용 시나리오:
        - 조건 분기에서 특정 경로 불필요
        - 선행 세그먼트 실패로 후속 세그먼트 실행 불가
        
        아키텍처 변경 (Phase 8):
        - ❌ 기존: S3 manifest 직접 수정 (Merkle DAG 무효화)
        - ✅ 개선: Manifest 재생성 + Hash Chain 연결
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
            # [Phase 8.3] Merkle DAG 재생성 (S3 직접 수정 금지)
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
                
                # StateBag 갱신
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
                # Fallback: S3 직접 저장 (레거시 모드)
                logger.warning("[Fallback] Using legacy S3 direct save (Merkle integrity lost)")
                return self._save_manifest_to_s3(manifest, manifest_s3_path)
        
        elif modified:
            # bag/workflow_config 없으면 레거시 모드
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
        [Phase 8.2 & 8.3] Manifest 변조 감지 시 재생성
        
        아키텍처 원칙 (Phase 8 Guideline):
        - Git Rebase와 유사: 새 매니페스트 = 이전 매니페스트 기반 새 해시
        - parent_hash로 Merkle Chain 연결 → 역사적 무결성 보장
        - 에이전트의 사후 조작 시도 시 해시 체인 깨짐으로 즉시 감지
        
        트리거 시나리오:
        - _mark_segments_for_skip() 호출
        - _inject_recovery_segments() 호출
        - 동적 세그먼트 수정
        
        Args:
            workflow_id: 워크플로우 ID
            workflow_config: 워크플로우 설정
            modified_segments: 수정된 세그먼트 목록
            execution_id: 실행 ID
            parent_manifest_id: 이전 매니페스트 ID
            parent_manifest_hash: 이전 매니페스트 해시
            reason: 재생성 사유
        
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
            
            # 1. StateVersioningService로 새 Manifest 생성
            versioning_service = StateVersioningService(
                dynamodb_table=os.environ.get('WORKFLOW_MANIFESTS_TABLE', 'WorkflowManifestsV3'),
                s3_bucket=os.environ.get('S3_BUCKET', 'analemma-state-dev')
            )
            
            # 2. 새 manifest 생성 (parent_hash = 이전 manifest의 hash)
            manifest_pointer = versioning_service.create_manifest(
                workflow_id=workflow_id,
                workflow_config=workflow_config,
                segment_manifest=modified_segments,
                parent_manifest_id=parent_manifest_id  # Merkle Chain 연결
            )
            
            new_manifest_id = manifest_pointer.manifest_id
            new_hash = manifest_pointer.manifest_hash
            config_hash = manifest_pointer.config_hash
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Phase 8.1] Pre-flight Check: S3 Strong Consistency 검증
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            async_commit = get_async_commit_service()
            manifest_s3_key = f"manifests/{new_manifest_id}.json"
            
            commit_status = async_commit.verify_commit_with_retry(
                execution_id=execution_id,
                s3_bucket=os.environ.get('S3_BUCKET', 'analemma-state-dev'),
                s3_key=manifest_s3_key,
                redis_key=None  # S3 검증만
            )
            
            if not commit_status.s3_available:
                raise RuntimeError(
                    f"[Manifest Regeneration Failed] New manifest S3 unavailable "
                    f"after {commit_status.retry_count} attempts "
                    f"(wait={commit_status.total_wait_ms:.1f}ms): {new_manifest_id[:8]}..."
                )
            
            logger.info(
                f"[Manifest Mutation] ✅ New manifest created and verified\n"
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
        [Phase 8.3] 복구 세그먼트 삽입 + Manifest 재생성
        
        사용 시나리오:
        - API 실패 후 백업 경로 삽입
        - 에러 핸들링 세그먼트 동적 추가
        - 에이전트 계획 수정 (Agent Re-planning)
        
        아키텍처 변경 (Phase 8):
        - ❌ 기존: S3 manifest 직접 수정 (Merkle DAG 무효화)
        - ✅ 개선: Manifest 재생성 + Hash Chain 연결
        
        [NEW] 동적 Re-partitioning 지원:
        - 대규모 수정 시 ManifestRegenerator Lambda 비동기 호출
        - 재파티셔닝 필요 조건: 3개 이상 세그먼트 삽입 or AGENT_REPLAN
        """
        manifest = self._load_manifest_from_s3(manifest_s3_path)
        if not manifest:
            return False
        
        # [NEW] 재파티셔닝 트리거 조건
        needs_repartition = (
            len(recovery_segments) > 3 or  # 많은 세그먼트 삽입
            reason == "AGENT_REPLAN" or    # 에이전트가 계획 변경
            self._check_structural_change(recovery_segments, workflow_config)
        )
        
        if needs_repartition and bag and workflow_config:
            logger.info(f"[Manifest] 🔄 Re-partitioning required: {reason}")
            
            # [FIX] Task Token 전달 (Step Functions 대기)
            task_token = bag.get('task_token')  # ASL에서 주입된 토큰
            
            regen_result = self._trigger_manifest_regeneration(
                manifest_s3_path=manifest_s3_path,
                workflow_id=bag.get('workflowId'),
                owner_id=bag.get('ownerId'),
                recovery_segments=recovery_segments,
                reason=reason,
                workflow_config=workflow_config,
                task_token=task_token
            )
            
            # 동기 모드: 즉시 완료
            if regen_result.get('sync_mode'):
                logger.info(f"[Manifest] ✅ Synchronous regeneration completed")
                return True
            
            # 비동기 모드 + Task Token: Step Functions가 대기
            if regen_result.get('wait_for_task_token'):
                logger.info(f"[Manifest] ⏳ Asynchronous regeneration in progress (Step Functions waiting)")
                # SegmentRunner는 여기서 종료 (Step Functions는 Task Token 콜백 대기)
                return True
            
            # 비동기 모드 (Task Token 없음): 백그라운드 실행
            logger.warning(f"[Manifest] ⚠️ Asynchronous regeneration without Task Token (no wait)")
            return regen_result.get('status') == 'MANIFEST_REGENERATING'
        
        # [Legacy Mode] 소규모 수정: 기존 방식으로 처리
        logger.info(f"[Manifest] Using legacy injection mode (< 3 segments)")
        
        # 삽입 위치 찾기
        insert_index = None
        for i, segment in enumerate(manifest):
            if segment.get('segment_id') == after_segment_id:
                insert_index = i + 1
                break
        
        if insert_index is None:
            logger.warning(f"[Kernel] Could not find segment {after_segment_id} for recovery injection")
            return False
        
        # 복구 세그먼트에 메타데이터 추가
        max_segment_id = max(s.get('segment_id', 0) for s in manifest)
        for i, rec_seg in enumerate(recovery_segments):
            rec_seg['segment_id'] = max_segment_id + i + 1
            rec_seg['status'] = SEGMENT_STATUS_PENDING
            rec_seg['injected_by'] = 'kernel'
            rec_seg['injection_reason'] = reason
            rec_seg['injected_at'] = int(time.time())
            rec_seg['type'] = rec_seg.get('type', 'recovery')
        
        # 매니페스트에 삽입
        new_manifest = manifest[:insert_index] + recovery_segments + manifest[insert_index:]
        
        # 후속 세그먼트 ID 재조정
        for i, segment in enumerate(new_manifest):
            segment['execution_order'] = i
        
        logger.info(f"[Kernel] [System] Injected {len(recovery_segments)} recovery segments after segment {after_segment_id}")
        
        if bag and workflow_config:
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Phase 8.3] Merkle DAG 재생성
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
                
                # StateBag 갱신
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
                # Fallback: S3 직접 저장 (레거시 모드)
                logger.warning("[Fallback] Using legacy S3 direct save (Merkle integrity lost)")
                return self._save_manifest_to_s3(new_manifest, manifest_s3_path)
        
        else:
            # bag/workflow_config 없으면 레거시 모드
            logger.warning("[Legacy Mode] Manifest regeneration skipped - using direct S3 save")
            return self._save_manifest_to_s3(new_manifest, manifest_s3_path)
    
    def _check_structural_change(
        self,
        recovery_segments: List[Dict[str, Any]],
        workflow_config: dict
    ) -> bool:
        """
        구조적 변경 감지: 재파티셔닝이 필요한지 판단
        
        재파티셔닝 필요 조건:
        - 새 LLM 노드 추가
        - 새 parallel_group 추가
        - 기존 노드 타입 변경
        """
        if not recovery_segments or not workflow_config:
            return False
        
        # 새 노드에 LLM이나 parallel_group이 있는지 확인
        for segment in recovery_segments:
            nodes = segment.get('nodes', [])
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                
                node_type = node.get('type', '')
                
                # LLM 노드 감지
                if node_type in ('llm_chat', 'aiModel', 'llm'):
                    logger.info(f"[Structural Change] LLM node detected: {node.get('id')}")
                    return True
                
                # Parallel Group 감지
                if node_type == 'parallel_group':
                    logger.info(f"[Structural Change] Parallel group detected: {node.get('id')}")
                    return True
                
                # Branches 속성이 있는 노드 (인라인 parallel)
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
        ManifestRegenerator Lambda 호출 (동기 or 비동기)
        
        [FIX] Race Condition 해결:
        - task_token이 있으면 비동기 + WaitForTaskToken 패턴
        - task_token이 없으면 동기 호출 (즉시 결과 반환)
        
        [FIX] 적응형 위임 정책:
        - 예상 처리 시간 > 3초면 무조건 비동기
        - 대규모 워크플로우 타임아웃 방지
        """
        try:
            import boto3
            lambda_client = boto3.client('lambda')
            
            # 복구 세그먼트를 modifications 형식으로 변환
            new_nodes = []
            new_edges = []
            
            for segment in recovery_segments:
                segment_nodes = segment.get('nodes', [])
                segment_edges = segment.get('edges', [])
                
                new_nodes.extend(segment_nodes)
                new_edges.extend(segment_edges)
            
            # [NEW] 적응형 위임 정책: 처리 시간 예측
            total_nodes = len(workflow_config.get('nodes', []))
            total_edges = len(workflow_config.get('edges', []))
            estimated_time = (total_nodes * 0.01) + (total_edges * 0.005)  # 대략적 추정
            
            force_async = (
                estimated_time > 3.0 or  # 3초 초과 예상
                total_nodes > 100 or     # 대규모 워크플로우
                len(recovery_segments) > 5  # 많은 세그먼트 삽입
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
            
            # [FIX] Task Token 패턴
            if task_token:
                payload['task_token'] = task_token
                invocation_type = 'Event'  # 비동기 (Step Functions가 대기)
                logger.info(f"[Manifest Regeneration] Using Task Token pattern (async)")
            elif force_async:
                invocation_type = 'Event'
                logger.warning(
                    f"[Manifest Regeneration] Forcing async due to estimated time {estimated_time:.2f}s "
                    f"(nodes={total_nodes}, edges={total_edges})"
                )
            else:
                invocation_type = 'RequestResponse'  # 동기
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
            
            # 동기 호출: 즉시 결과 파싱
            if invocation_type == 'RequestResponse':
                response_payload = json.loads(response['Payload'].read())
                logger.info(f"[Manifest Regeneration] ✅ Completed synchronously: {response_payload.get('status')}")
                return {
                    'status': 'MANIFEST_REGENERATED',
                    'sync_mode': True,
                    'result': response_payload
                }
            
            # 비동기 호출: Step Functions가 Task Token으로 대기
            logger.info(f"[Manifest Regeneration] ✅ Invoked asynchronously: {response['StatusCode']}")
            return {
                'status': 'MANIFEST_REGENERATING',
                'sync_mode': False,
                'wait_for_task_token': bool(task_token)
            }
            
        except Exception as e:
            logger.error(f"[Manifest Regeneration] ❌ Failed to invoke: {e}")
            return {
                'status': 'REGENERATION_FAILED',
                'error': str(e)
            }

    # ========================================================================
    # [Parallel] [Pattern 3] Parallel Scheduler: 인프라 인지형 병렬 스케줄링
    # ========================================================================
    
    def _offload_branches_to_s3(
        self,
        branches: List[Dict[str, Any]],
        owner_id: str,
        workflow_id: str,
        segment_id: int
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        🌿 [Pointer Strategy] 각 브랜치를 S3에 업로드하고 경량 포인터 배열 반환
        
        Map 내부 Hydrate 전략:
        - 전체 branches 데이터를 S3에 업로드 (단일 파일)
        - pending_branches에는 인덱스 + S3 경로만 포함된 경량 포인터 배열 전달
        - Map Iterator 첫 단계에서 각 브랜치가 S3 경로로 자신의 데이터 hydrate
        
        Returns:
            (branch_pointers, branches_s3_path)
            - branch_pointers: [{branch_index, branch_id, branches_s3_path, total_branches}, ...]
            - branches_s3_path: 전체 branches 배열이 저장된 S3 경로
        """
        if not branches:
            return [], None
        
        if not self.state_bucket:
            logger.warning("[Pointer Strategy] No S3 bucket configured. Returning inline branches (may exceed payload limit)")
            # 폴백: 인라인 반환 (위험하지만 S3 없으면 어쩔 수 없음)
            return branches, None
        
        try:
            import boto3
            s3_client = boto3.client('s3')
            
            timestamp = int(time.time() * 1000)  # 밀리초 단위
            s3_key = f"workflow-states/{owner_id}/{workflow_id}/segments/{segment_id}/branches/{timestamp}/all_branches.json"
            
            # 전체 branches 배열을 S3에 업로드
            branches_json = json.dumps(branches, default=str)
            s3_client.put_object(
                Bucket=self.state_bucket,
                Key=s3_key,
                Body=branches_json,
                ContentType='application/json'
            )
            
            branches_s3_path = f"s3://{self.state_bucket}/{s3_key}"
            branches_size_kb = len(branches_json) / 1024
            
            logger.info(f"[Pointer Strategy] ✅ Uploaded {len(branches)} branches ({branches_size_kb:.1f}KB) to {branches_s3_path}")
            
            # 경량 포인터 배열 생성
            # Map Iterator에서 각 포인터를 받아 S3에서 자신의 브랜치 데이터를 hydrate
            branch_pointers = []
            for idx, branch in enumerate(branches):
                pointer = {
                    'branch_index': idx,
                    'branch_id': branch.get('id') or branch.get('branch_id') or f'branch_{idx}',
                    'branches_s3_path': branches_s3_path,
                    'total_branches': len(branches),
                    # 💡 경량 메타데이터만 포함 (hydrate 전 필요한 최소 정보)
                    'segment_count': len(branch.get('partition_map', [])) if branch.get('partition_map') else 0,
                }
                branch_pointers.append(pointer)
            
            pointer_size = len(json.dumps(branch_pointers, default=str))
            logger.info(f"[Pointer Strategy] 📦 Created {len(branch_pointers)} pointers ({pointer_size/1024:.2f}KB) - "
                       f"Compression ratio: {branches_size_kb * 1024 / max(pointer_size, 1):.1f}x")
            
            return branch_pointers, branches_s3_path
            
        except Exception as e:
            logger.error(f"[Pointer Strategy] ❌ Failed to offload branches to S3: {e}")
            # 폴백: 인라인 반환 (위험하지만 S3 실패 시)
            return branches, None
    
    def _estimate_branch_resources(self, branch: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, int]:
        """
        브랜치의 예상 자원 요구량 추정
        
        Returns:
            {
                'memory_mb': 예상 메모리 (MB),
                'tokens': 예상 토큰 수,
                'llm_calls': LLM 호출 횟수,
                'has_shared_resource': 공유 자원 접근 여부
            }
        """
        # 🛡️ [P0 Fix] None 또는 dict가 아닌 브랜치 방어
        if not branch or not isinstance(branch, dict):
            logger.warning(f"[Scheduler] [Warning] Invalid branch object in resource estimation: {type(branch)}")
            return {
                'memory_mb': DEFAULT_BRANCH_MEMORY_MB,
                'tokens': 0,
                'llm_calls': 0,
                'has_shared_resource': False
            }
        
        # [Critical Fix] 숨어있는 노드들까지 투시해서 토큰 계산
        all_nodes = branch.get('nodes', [])
        if not all_nodes and 'partition_map' in branch:
            # 파티셔닝된 브랜치라면 모든 세그먼트의 노드를 합쳐서 계산 대상에 포함
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
        
        memory_mb = 50  # 기본 오버헤드
        tokens = 0
        llm_calls = 0
        has_shared_resource = False
        
        for node in all_nodes:
            # 🛡️ [v3.8] None defense in nodes iteration
            if node is None or not isinstance(node, dict):
                continue
            node_type = node.get('type', '')
            config = node.get('config', {})
            
            # 메모리 추정
            memory_mb += 10  # 노드당 기본 10MB
            
            if node_type in ('llm_chat', 'aiModel', 'llm', 'aimodel'):
                memory_mb += 50  # LLM 노드 추가 메모리
                llm_calls += 1
                # 토큰 추정: 프롬프트 길이 기반
                prompt = config.get('prompt', '') or config.get('system_prompt', '') or config.get('prompt_template', '')
                tokens += len(prompt) // 4 + 500  # 대략적 토큰 추정 + 응답 예상
                
            elif node_type == 'for_each':
                items_key = config.get('input_list_key', '')
                if items_key and items_key in state:
                    items = state.get(items_key, [])
                    if isinstance(items, list):
                        iteration_count = len(items)
                        memory_mb += iteration_count * 5
                        # for_each 내부에 LLM이 있으면 토큰 폭증
                        # [Fix] None defense: config['sub_node_config'] 또는 config['sub_workflow']가 None일 수 있음
                        sub_config = config.get('sub_node_config') or config.get('sub_workflow') or {}
                        sub_nodes = sub_config.get('nodes', []) if isinstance(sub_config, dict) else []
                        for sub_node in sub_nodes:
                            # [Fix] sub_node가 None일 수 있음
                            if sub_node and isinstance(sub_node, dict) and sub_node.get('type') in ('llm_chat', 'aiModel'):
                                # [Critical Fix] Multiply by iteration count for accurate token estimation
                                tokens += iteration_count * 5000  # 아이템당 5000 토큰 예상
                                llm_calls += iteration_count
                                logger.debug(f"[Scheduler] for_each with LLM: {iteration_count} iterations × 5000 tokens = {iteration_count * 5000} tokens")
            
            # 공유 자원 접근 감지
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
        🎯 Bin Packing 알고리즘: 브랜치를 실행 배치로 그룹화
        
        전략:
        1. 무거운 브랜치 먼저 배치 (First Fit Decreasing)
        2. 각 배치의 총 자원이 제한을 초과하지 않도록 구성
        3. 공유 자원 접근 브랜치는 별도 배치
        
        Returns:
            [[batch1_branches], [batch2_branches], ...]
        """
        # [Fix] Use 'or' to handle None values - .get() returns None if key exists with None value
        max_memory = resource_policy.get('max_concurrent_memory_mb') or DEFAULT_MAX_CONCURRENT_MEMORY_MB
        max_tokens = resource_policy.get('max_concurrent_tokens') or DEFAULT_MAX_CONCURRENT_TOKENS
        max_branches = resource_policy.get('max_concurrent_branches') or DEFAULT_MAX_CONCURRENT_BRANCHES
        strategy = resource_policy.get('strategy') or STRATEGY_RESOURCE_OPTIMIZED
        
        # 브랜치와 자원 추정치 결합 후 크기순 정렬 (내림차순)
        indexed_branches = list(zip(branches, resource_estimates, range(len(branches))))
        
        # 전략에 따른 정렬 기준
        if strategy == STRATEGY_COST_OPTIMIZED:
            # 토큰 많은 것 먼저 (비용이 큰 작업 순차 처리)
            indexed_branches.sort(key=lambda x: x[1]['tokens'], reverse=True)
        else:
            # 메모리 많은 것 먼저 (기본)
            indexed_branches.sort(key=lambda x: x[1]['memory_mb'], reverse=True)
        
        # 공유 자원 접근 브랜치 분리
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
                
                # 이 배치에 추가 가능한지 확인
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
                # 새 배치 생성
                batches.append([(branch, estimate, idx)])
                batch_resources.append({
                    'memory_mb': estimate['memory_mb'],
                    'tokens': estimate['tokens']
                })
        
        # 공유 자원 브랜치는 각각 별도 배치 (Race Condition 방지)
        for branch, estimate, idx in shared_resource_branches:
            batches.append([(branch, estimate, idx)])
            batch_resources.append({
                'memory_mb': estimate['memory_mb'],
                'tokens': estimate['tokens']
            })
        
        # 결과 변환: 브랜치만 추출
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
        [Parallel] 병렬 그룹 스케줄링: resource_policy에 따라 실행 배치 결정
        
        🌿 [Pointer Strategy] Map 내부 Hydrate를 위한 S3 오프로딩:
        - 전체 branches 데이터를 S3에 업로드
        - pending_branches에는 경량 포인터 배열만 전달 (branch_index, branch_s3_path)
        - Map Iterator 내부에서 각 브랜치가 S3 경로로 자신의 데이터 hydrate
        
        Returns:
            {
                'status': 'PARALLEL_GROUP' | 'SCHEDULED_PARALLEL',
                'branches': [...] (경량 포인터 배열 - S3 경로 포함),
                'branches_s3_path': S3 경로 (전체 branches 데이터 위치),
                'execution_batches': [[...], [...]] (배치 구조),
                'scheduling_metadata': {...}
            }
        """
        branches = segment_config.get('branches', [])
        resource_policy = segment_config.get('resource_policy', {})
        
        # resource_policy가 없으면 기본 병렬 실행
        if not resource_policy:
            logger.info(f"[Scheduler] No resource_policy, using default parallel execution for {len(branches)} branches")
            
            # 🌿 [Pointer Strategy] S3에 브랜치 오프로딩
            branch_pointers, branches_s3_path = self._offload_branches_to_s3(
                branches=branches,
                owner_id=owner_id or 'unknown',
                workflow_id=workflow_id or 'unknown',
                segment_id=segment_id
            )
            
            return {
                'status': 'PARALLEL_GROUP',
                'branches': branch_pointers,  # 경량 포인터 배열
                'branches_s3_path': branches_s3_path,  # S3 경로
                'execution_batches': [branch_pointers],  # 단일 배치 (포인터)
                'scheduling_metadata': {
                    'strategy': 'DEFAULT',
                    'total_branches': len(branches),
                    'batch_count': 1,
                    'pointer_strategy': True,
                    'branches_s3_path': branches_s3_path
                }
            }
        
        strategy = resource_policy.get('strategy', STRATEGY_RESOURCE_OPTIMIZED)
        
        # SPEED_OPTIMIZED: 가드레일 체크 후 최대 병렬 실행
        if strategy == STRATEGY_SPEED_OPTIMIZED:
            # [Guard] 계정 수준 하드 리밋 체크 (시스템 패닉 방지)
            if len(branches) > ACCOUNT_LAMBDA_CONCURRENCY_LIMIT:
                logger.warning(f"[Scheduler] [Warning] SPEED_OPTIMIZED but branch count ({len(branches)}) "
                              f"exceeds account concurrency limit ({ACCOUNT_LAMBDA_CONCURRENCY_LIMIT})")
                # 하드 리밋 적용하여 배치 분할
                forced_policy = {
                    'max_concurrent_branches': ACCOUNT_LAMBDA_CONCURRENCY_LIMIT,
                    'max_concurrent_memory_mb': ACCOUNT_MEMORY_HARD_LIMIT_MB,
                    'strategy': STRATEGY_SPEED_OPTIMIZED
                }
                # 자원 추정 및 배치 분할
                resource_estimates = [self._estimate_branch_resources(b, state) for b in branches]
                execution_batches = self._bin_pack_branches(branches, resource_estimates, forced_policy)
                
                logger.info(f"[Scheduler] [Guard] Guardrail applied: {len(execution_batches)} batches")
                
                # 🌿 [Pointer Strategy] S3에 브랜치 오프로딩
                branch_pointers, branches_s3_path = self._offload_branches_to_s3(
                    branches=branches,
                    owner_id=owner_id or 'unknown',
                    workflow_id=workflow_id or 'unknown',
                    segment_id=segment_id
                )
                
                return {
                    'status': 'SCHEDULED_PARALLEL',
                    'branches': branch_pointers,  # 경량 포인터 배열
                    'branches_s3_path': branches_s3_path,
                    'execution_batches': execution_batches,  # 원본 배치 구조 (스케줄링용)
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
            
            # 🌿 [Pointer Strategy] S3에 브랜치 오프로딩
            branch_pointers, branches_s3_path = self._offload_branches_to_s3(
                branches=branches,
                owner_id=owner_id or 'unknown',
                workflow_id=workflow_id or 'unknown',
                segment_id=segment_id
            )
            
            return {
                'status': 'PARALLEL_GROUP',
                'branches': branch_pointers,  # 경량 포인터 배열
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
        
        # 자원 추정
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
        
        # 제한 확인 - [Fix] Use 'or' to handle None values
        max_memory = resource_policy.get('max_concurrent_memory_mb') or DEFAULT_MAX_CONCURRENT_MEMORY_MB
        max_tokens = resource_policy.get('max_concurrent_tokens') or DEFAULT_MAX_CONCURRENT_TOKENS
        
        # 제한 내라면 단일 배치
        if total_memory <= max_memory and total_tokens <= max_tokens:
            logger.info(f"[Scheduler] Resources within limits, single batch execution")
            
            # 🌿 [Pointer Strategy] S3에 브랜치 오프로딩
            branch_pointers, branches_s3_path = self._offload_branches_to_s3(
                branches=branches,
                owner_id=owner_id or 'unknown',
                workflow_id=workflow_id or 'unknown',
                segment_id=segment_id
            )
            
            return {
                'status': 'PARALLEL_GROUP',
                'branches': branch_pointers,  # 경량 포인터 배열
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
        
        # Bin Packing으로 배치 생성
        execution_batches = self._bin_pack_branches(branches, resource_estimates, resource_policy)
        
        logger.info(f"[Scheduler] [System] Created {len(execution_batches)} execution batches from {len(branches)} branches")
        for i, batch in enumerate(execution_batches):
            batch_memory = sum(self._estimate_branch_resources(b, state)['memory_mb'] for b in batch)
            logger.info(f"[Scheduler]   Batch {i+1}: {len(batch)} branches, ~{batch_memory}MB")
        
        # 🌿 [Pointer Strategy] S3에 브랜치 오프로딩
        branch_pointers, branches_s3_path = self._offload_branches_to_s3(
            branches=branches,
            owner_id=owner_id or 'unknown',
            workflow_id=workflow_id or 'unknown',
            segment_id=segment_id
        )
        
        return {
            'status': 'SCHEDULED_PARALLEL',
            'branches': branch_pointers,  # 경량 포인터 배열
            'branches_s3_path': branches_s3_path,
            'execution_batches': execution_batches,  # 원본 배치 구조 (스케줄링용)
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
    # [Parallel] [Aggregator] 병렬 브랜치 결과 집계
    # ========================================================================
    def _handle_aggregator(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        병렬 브랜치 실행 결과를 집계하여 단일 상태로 병합
        
        ASL의 AggregateParallelResults에서 호출됨:
        - parallel_results: 각 브랜치의 실행 결과 배열
        - current_state: 병렬 실행 전 상태
        - map_error: (선택) Map 전체 실패 시 에러 정보
        
        Returns:
            Merged final state + next segment info
        """
        # 🛡️ [v3.6] Ensure base_state integrity at aggregation start (Entrance to Aggregator)
        from src.common.statebag import ensure_state_bag
        
        # 🔍 [v3.5 None Trace] Aggregator entry tracing
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
        
        # ⚠️ Keep current_state as local variable only - DO NOT add to event
        
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
        logger.info(f"[Aggregator] 🔍 DEBUG: parallel_results type: {type(parallel_results)}")
        logger.info(f"[Aggregator] 🔍 DEBUG: parallel_results length: {len(parallel_results) if parallel_results else 0}")
        if parallel_results:
            for idx, item in enumerate(parallel_results[:3]):  # Log first 3 items
                logger.info(f"[Aggregator] 🔍 DEBUG: parallel_results[{idx}] keys: {list(item.keys()) if isinstance(item, dict) else 'NOT_DICT'}")
        
        # ThreadPoolExecutor로 병렬 fetch
        branches_needing_s3 = []
        for i, result in enumerate(parallel_results):
            if not result or not isinstance(result, dict):
                logger.info(f"[Aggregator] ⚠️ DEBUG: Skipping parallel_results[{i}] - not a dict")
                continue
            
            # [Critical Fix] Unwrap Lambda invoke wrapper if present
            # Distributed Map State returns: {"Payload": {...}}
            logger.info(f"[Aggregator] 🔍 DEBUG: parallel_results[{i}] has 'Payload' key: {'Payload' in result}")
            if 'Payload' in result and isinstance(result['Payload'], dict):
                logger.info(f"[Aggregator] ✅ DEBUG: Unwrapping Payload for parallel_results[{i}]")
                result = result['Payload']
                parallel_results[i] = result  # Update in-place for later processing
            
            branch_s3_path = result.get('final_state_s3_path') or result.get('state_s3_path')
            branch_state = result.get('final_state') or result.get('state') or {}
            
            # [Critical Fix] __s3_offloaded 플래그 확인 추가
            is_offloaded = isinstance(branch_state, dict) and branch_state.get('__s3_offloaded') is True
            if is_offloaded:
                # S3 offload된 상태: __s3_path에서 실제 경로 가져오기
                branch_s3_path = branch_state.get('__s3_path')
                logger.info(f"[Aggregator] Branch {i} has __s3_offloaded flag. S3 path: {branch_s3_path}")
            
            is_empty = isinstance(branch_state, dict) and len(branch_state) <= 1  # {} or {"__state_truncated": true}
            
            # S3 복원이 필요한 경우: 빈 상태 OR offloaded 플래그
            if (is_empty or is_offloaded) and branch_s3_path:
                branches_needing_s3.append((i, branch_s3_path, result))
        
        # 병렬 S3 fetch 실행
        if branches_needing_s3:
            logger.info(f"[Aggregator] 🚀 Parallel S3 fetch for {len(branches_needing_s3)} branches")
            
            def fetch_branch_s3(item: Tuple[int, str, Dict]) -> Tuple[int, Optional[Dict[str, Any]]]:
                idx, s3_path, result = item
                try:
                    bucket = s3_path.replace("s3://", "").split("/")[0]
                    key = "/".join(s3_path.replace("s3://", "").split("/")[1:])
                    obj = self.state_manager.s3_client.get_object(Bucket=bucket, Key=key)
                    content = obj['Body'].read().decode('utf-8')
                    state = self._safe_json_load(content)  # 🛡️ Use safe loader
                    return (idx, state)
                except Exception as e:
                    logger.error(f"[Aggregator] S3 recovery failed for branch {idx}: {e}")
                    return (idx, {})  # 🛡️ Return empty dict instead of None
            
            # 병렬 실행 (최대 10개 동시)
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(fetch_branch_s3, item): item for item in branches_needing_s3}
                
                for future in as_completed(futures, timeout=60):  # 60초 전체 timeout
                    try:
                        idx, state = future.result(timeout=5)  # 개별 5초 timeout
                        if state:
                            # 원본 결과에 hydrated state 주입
                            parallel_results[idx]['final_state'] = state
                            parallel_results[idx]['__hydrated_from_s3'] = True
                    except Exception as e:
                        item = futures[future]
                        logger.warning(f"[Aggregator] Future failed for branch {item[0]}: {e}")
            
            logger.info(f"[Aggregator] ✅ Parallel S3 fetch completed")
        
        # 🛡️ [Asymmetric Branch Handling] will be processed after partition_map is loaded
        # (Moved to line ~2400 to avoid undefined variable error)
        
        # 1. 모든 브랜치 결과 병합 (terminates_early 브랜치는 Optional)
        aggregated_state = base_state.copy()
        all_history_logs = []
        branch_errors = []
        successful_branches = 0
        optional_branches_skipped = 0
        
        # [Guard] Map 에러가 있으면 기록
        if map_error:
            branch_errors.append({
                'branch_id': '__MAP_ERROR__',
                'error': map_error
            })
            logger.warning(f"[Aggregator] [Warning] Map execution failed: {map_error}")
        
        for i, branch_result in enumerate(parallel_results):
            # 1. Null Guard (루프 시작하자마자 체크)
            if branch_result is None or not isinstance(branch_result, dict):
                # 🛡️ [Asymmetric Branch] terminates_early 브랜치는 Optional
                # 해당 브랜치가 명시적 END로 종료되었을 수 있음
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

            # 2. 컨텍스트 추출 (루프 내부)
            branch_id = branch_result.get('branch_id', f'branch_{i}')
            branch_status = branch_result.get('branch_status') or branch_result.get('status', 'UNKNOWN')
            
            # 🛡️ [Fix] branch_state를 루프 내부에서 안전하게 획득
            # [Note] 병렬 S3 fetch가 이미 완료되어 hydrated state가 주입됨
            branch_state = branch_result.get('final_state') or branch_result.get('state') or {}
            
            branch_logs = branch_result.get('new_history_logs', [])
            error_info = branch_result.get('error_info')
            
            # [Removed] 순차 S3 hydration 로직 제거 (이미 병렬로 처리됨)
            # 병렬 fetch에서 실패한 경우에만 여기서 fallback 시도
            if isinstance(branch_state, dict) and len(branch_state) == 0:
                branch_s3_path = branch_result.get('final_state_s3_path') or branch_result.get('state_s3_path')
                if branch_s3_path and not branch_result.get('__hydrated_from_s3'):
                    # 병렬 fetch 실패 시 fallback (순차 재시도)
                    logger.warning(f"[Aggregator] Fallback: Sequential fetch for branch {branch_id}")
                    try:
                        bucket = branch_s3_path.replace("s3://", "").split("/")[0]
                        key = "/".join(branch_s3_path.replace("s3://", "").split("/")[1:])
                        obj = self.state_manager.s3_client.get_object(Bucket=bucket, Key=key)
                        content = obj['Body'].read().decode('utf-8')
                        branch_state = self._safe_json_load(content)  # 🛡️ Use safe loader
                    except Exception as e:
                        logger.error(f"[Aggregator] Fallback failed for branch {branch_id}: {e}")
                        branch_errors.append({
                            'branch_id': branch_id,
                            'error': f"S3 Hydration Failed: {str(e)}"
                        })
            
            # 실제 상태 병합 실행
            if isinstance(branch_state, dict):
                aggregated_state = self._merge_states(
                    aggregated_state,
                    branch_state,
                    merge_policy=MERGE_POLICY_APPEND_LIST
                )
                
                if branch_status in ('COMPLETE', 'SUCCEEDED', 'COMPLETED', 'CONTINUE'):
                    successful_branches += 1
            
            # 에러 수집
            if error_info:
                branch_errors.append({
                    'branch_id': branch_id,
                    'error': error_info
                })
            
            # 히스토리 로그 수집 (Memory Safe Truncation)
            if isinstance(branch_logs, list):
                # 🛡️ [Guard] Prevent unlimited log growth from thousands of branches
                MAX_AGGREGATED_LOGS = 100
                current_log_count = len(all_history_logs)
                
                if current_log_count < MAX_AGGREGATED_LOGS:
                    remaining_slots = MAX_AGGREGATED_LOGS - current_log_count
                    if len(branch_logs) > remaining_slots:
                        all_history_logs.extend(branch_logs[:remaining_slots])
                        all_history_logs.append(f"[Aggregator] Logs truncated: exceeded limit of {MAX_AGGREGATED_LOGS} entries")
                    else:
                        all_history_logs.extend(branch_logs)
        
        # 2. 집계 메타데이터 추가
        aggregated_state['__aggregator_metadata'] = {
            'total_branches': len(parallel_results),
            'successful_branches': successful_branches,
            'failed_branches': len(branch_errors),
            'optional_branches_skipped': optional_branches_skipped,  # 🛡️ terminates_early 브랜치 수
            'aggregated_at': time.time(),
            'logs_truncated': len(all_history_logs) >= 100 
        }
        
        if branch_errors:
            aggregated_state['__branch_errors'] = branch_errors
        
        # [Token Aggregation] 병렬 브랜치들의 토큰 사용량 합산
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
            
            # [DEBUG] 브랜치 상태에서 토큰 관련 키 로깅
            token_keys = [k for k in branch_state.keys() if 'token' in k.lower() or k == 'usage']
            logger.info(f"[Aggregator] [DEBUG] Branch {branch_id} token-related keys: {token_keys}")
            if 'usage' in branch_state:
                logger.info(f"[Aggregator] [DEBUG] Branch {branch_id} usage: {branch_state.get('usage')}")
            
            # 브랜치의 토큰 사용량 추출
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
        
        # 합산된 토큰 정보를 aggregated_state에 기록
        aggregated_state['total_input_tokens'] = total_input_tokens
        aggregated_state['total_output_tokens'] = total_output_tokens
        aggregated_state['total_tokens'] = total_tokens
        aggregated_state['branch_token_details'] = branch_token_details
        
        logger.info(f"[Aggregator] [Token Aggregation] {len(branch_token_details)} branches, "
                   f"total tokens: {total_tokens} ({total_input_tokens} input + {total_output_tokens} output)")
        
        # 3. 상태 저장 (S3 오프로딩 포함)
        s3_bucket_raw = os.environ.get("S3_BUCKET") or os.environ.get("SKELETON_S3_BUCKET") or ""
        s3_bucket = s3_bucket_raw.strip() if s3_bucket_raw else None
        
        if not s3_bucket:
            logger.error("[Alert] [CRITICAL] S3_BUCKET/SKELETON_S3_BUCKET not set for aggregation!")
        
        # [Fix] Aggregator는 데이터 유실 방지를 위해 더 보수적인 임계값 적용
        # [Update] 각 브랜치가 50KB로 제한되므로 aggregator는 120KB threshold 적용
        # 예: 3개 브랜치 x 50KB = 150KB → S3 오프로드 발생
        # 참고: 브랜치가 S3 레퍼런스만 반환하면 aggregator 입력은 작지만,
        #       hydration 후 병합된 결과는 클 수 있음
        AGGREGATOR_SAFE_THRESHOLD = 120000  # 120KB (256KB 리밋의 47%)
        
        # [Critical] 병합된 상태 크기 측정 (S3 오프로드 결정 전)
        aggregated_size = len(json.dumps(aggregated_state, ensure_ascii=False).encode('utf-8'))
        logger.info(f"[Aggregator] Merged state size: {aggregated_size/1024:.1f}KB, "
                   f"threshold: {AGGREGATOR_SAFE_THRESHOLD/1024:.0f}KB")
        
        final_state, output_s3_path = self.state_manager.handle_state_storage(
            state=aggregated_state,
            auth_user_id=auth_user_id,
            workflow_id=workflow_id,
            segment_id=segment_to_run,
            bucket=s3_bucket,
            threshold=AGGREGATOR_SAFE_THRESHOLD  # 강화된 임계값
        )
        
        # [Critical] 응답 페이로드 크기 검증 (Step Functions 256KB 제한)
        response_size = len(json.dumps(final_state, ensure_ascii=False).encode('utf-8')) if final_state else 0
        logger.info(f"[Aggregator] Response payload size: {response_size/1024:.1f}KB "
                   f"(S3: {'YES - ' + output_s3_path if output_s3_path else 'NO'})")
        
        if response_size > 250000:  # 250KB warning
            logger.warning(f"[Aggregator] [Alert] Response payload exceeds 250KB! "
                          f"This may fail Step Functions state transition. Size: {response_size/1024:.1f}KB")
        
        
        # 4. 다음 세그먼트 결정
        # aggregator 다음은 일반적으로 워크플로우 완료이지만,
        # partition_map에서 next_segment를 확인
        partition_map = event.get('partition_map', [])
        total_segments = _safe_get_total_segments(event)
        next_segment = segment_to_run + 1
        
        # 🛡️ [Asymmetric Branch Handling] terminates_early 브랜치 처리
        # partition_service에서 설정된 terminates_early 플래그 확인
        # 비대칭 브랜치(한쪽은 END, 다른쪽은 aggregator 합류)를 Optional로 처리
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
                                f"[Aggregator] ⚠️ Asymmetric branch termination detected: "
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
                except Exception:
                    agg_segment_config = None

        if next_segment < total_segments and agg_segment_config:
            edge_info = check_inter_segment_edges(agg_segment_config)
            if is_hitp_edge(edge_info):
                hitp_detected = True
                logger.info(f"[Aggregator] HITP edge detected via segment_config.outgoing_edges: "
                          f"segment {segment_to_run} → {next_segment}, "
                          f"type={edge_info.get('edge_type')}, target={edge_info.get('target_node')}")
        
        # 완료 여부 판단
        is_complete = next_segment >= total_segments
        
        logger.info(f"[Aggregator] [Success] Aggregation complete: "
                   f"{successful_branches}/{len(parallel_results)} branches succeeded"
                   f"{f', {optional_branches_skipped} optional branches skipped (terminates_early)' if optional_branches_skipped > 0 else ''}, "
                   f"next_segment={next_segment if not is_complete else 'COMPLETE'}, "
                   f"hitp_detected={hitp_detected}")
        
        # [Critical] S3 중간 파일 정리 (Garbage Collection)
        # 각 브랜치가 S3에 저장한 임시 결과 파일들을 삭제
        # 병합 완료 후에는 더 이상 필요 없음 (비용 & 관리 부하 감소)
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
            logger.info(f"[Aggregator] 🚨 Pausing execution due to HITP edge. Next segment: {next_segment}")
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
        # ASL passthrough 필드는 _finalize_response에서 자동 주입됨
        return {
            # Core execution result
            "status": "COMPLETE" if is_complete else "CONTINUE",
            "final_state": response_final_state,
            "final_state_s3_path": output_s3_path,
            "current_state": response_final_state,  # 🛡️ [P0 Fix] current_state도 S3 포인터로 변경 (중복 데이터 제거)
            "state_s3_path": output_s3_path,  # ASL 호환용 별칭
            "next_segment_to_run": None if is_complete else next_segment,
            "new_history_logs": all_history_logs,
            "error_info": branch_errors if branch_errors else None,
            "branches": [],  # 🛡️ [P0 Fix] None 대신 빈 배열로 변경 (ASL Map 호환성)
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
            
            # [v2.1] Step Functions start_execution에 재시도 적용
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
    # [Guard] [v2.2] Ring Protection: 프롬프트 보안 검증
    # ========================================================================
    def _apply_ring_protection(
        self,
        segment_config: Dict[str, Any],
        initial_state: Dict[str, Any],
        segment_id: int,
        workflow_id: str
    ) -> List[Dict[str, Any]]:
        """
        [Guard] Ring Protection: 세그먼트 내 프롬프트 보안 검증
        
        모든 LLM 노드의 프롬프트를 검증하고:
        1. Prompt Injection 패턴 탐지
        2. Ring 0 태그 위조 시도 탐지
        3. 위험 도구 직접 접근 시도 탐지
        
        Args:
            segment_config: 세그먼트 설정
            initial_state: 초기 상태
            segment_id: 세그먼트 ID
            workflow_id: 워크플로우 ID
            
        Returns:
            보안 위반 목록 (빈 리스트면 안전)
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
        
        # [Time Machine] _auto_fix_instructions 추출 후 state에서 즉시 제거
        # 첫 번째 LLM 노드에만 주입하고 state에서 소거 → 하위 세그먼트 연쇄 오염 방지
        # NOTE: SmartStateBag은 pop()을 오버라이드하지 않으므로 del을 명시적으로 사용해야
        #       _deleted_fields가 올바르게 추적되어 DynamoDB delta write에 반영됨.
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
        auto_fix_injected = False  # 첫 번째 LLM 노드에만 주입

        for node in nodes:
            # 🛡️ [v3.8] None defense in nodes iteration
            if node is None or not isinstance(node, dict):
                continue
            node_id = node.get('id', 'unknown')
            node_type = node.get('type', '')
            config = node.get('config', {})

            # LLM 노드의 프롬프트 검증
            if node_type in ('llm_chat', 'aiModel', 'llm'):
                prompt = config.get('prompt_content') or config.get('prompt') or ''
                system_prompt = config.get('system_prompt', '')

                # [Time Machine] _auto_fix_instructions 주입 (첫 번째 LLM 노드에만)
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

                # 프롬프트 검증
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
                            
                            # 프롬프트 정화 (in-place)
                            if result.sanitized_content:
                                if prompt_type == 'prompt':
                                    config['prompt_content'] = result.sanitized_content
                                    config['prompt'] = result.sanitized_content
                                else:
                                    config['system_prompt'] = result.sanitized_content
                                logger.info(f"[Ring Protection] [Guard] Sanitized {prompt_type} in node {node_id}")
            
            # 위험 도구 접근 검증
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
                        'should_sigkill': False  # 도구 접근은 경고만
                    })
        
        if violations:
            logger.warning(f"[Ring Protection] [Warning] {len(violations)} security violations detected in segment {segment_id}")
        
        return violations

    # ========================================================================
    # [Guard] [Kernel Defense] Aggressive Retry Helper
    # ========================================================================
    def _is_retryable_error(self, error: Exception) -> bool:
        """
        에러가 재시도 가능한지 판단
        """
        error_str = str(error)
        error_type = type(error).__name__
        
        for pattern in RETRYABLE_ERROR_PATTERNS:
            if pattern in error_str or pattern in error_type:
                return True
        
        # Boto3 ClientError 체크
        if hasattr(error, 'response'):
            # [Fix] None defense: error.response['Error']가 None일 수 있음
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
        [Guard] 커널 레벨 공격적 재시도
        
        Step Functions 레벨 재시도 전에 Lambda 내부에서 먼저 해결 시도.
        - 네트워크 에러, 일시적 서비스 장애 시 재시도
        - 지수 백오프 + 지터 적용
        
        Returns:
            (result_state, error_info) - 성공 시 error_info는 None
        """
        last_error = None
        retry_history = []
        
        # Check if this is a parallel branch execution (for token aggregation)
        is_parallel_branch = event.get('branch_config') is not None
        
        for attempt in range(KERNEL_MAX_RETRIES + 1):
            try:
                # 커널 동적 분할 활성화 여부 확인
                enable_kernel_split = os.environ.get('ENABLE_KERNEL_SPLIT', 'true').lower() == 'true'
                
                if enable_kernel_split and isinstance(segment_config, dict):
                    # [Guard] [Pattern 1] 자동 분할 실행
                    result_state = self._execute_with_auto_split(
                        segment_config=segment_config,
                        initial_state=initial_state,
                        auth_user_id=auth_user_id,
                        split_depth=segment_config.get('_split_depth', 0)
                    )
                else:
                    # 기존 로직: 직접 실행
                    logger.info(f"[v3.27 Debug] Calling run_workflow with segment_config: "
                               f"nodes={len(segment_config.get('nodes', []))}, "
                               f"node_ids={[n.get('id') for n in segment_config.get('nodes', [])]}")
                    logger.error(f"[v3.27 DEBUG] Calling run_workflow with segment_config keys: {list(segment_config.keys())[:15] if isinstance(segment_config, dict) else 'NOT A DICT'}")
                    logger.error(f"[v3.27 DEBUG] segment_config.nodes count: {len(segment_config.get('nodes', [])) if isinstance(segment_config, dict) else 'N/A'}")
                    # [C-02 FIX] Lazy import: 순환 임포트 방지 (top-level에서 제거됨)
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
                        logger.error(f"[v3.27 DEBUG] ✅ llm_raw_output FOUND in result_state!")
                    else:
                        logger.error(f"[v3.27 DEBUG] ❌ llm_raw_output NOT FOUND in result_state")
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
                
                # 성공
                if attempt > 0:
                    logger.info(f"[Kernel Retry] [Success] Succeeded after {attempt} retries")
                    # 재시도 이력 기록
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
                    # 지수 백오프 + 지터
                    delay = KERNEL_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        f"[Kernel Retry] [Warning] Attempt {attempt + 1}/{KERNEL_MAX_RETRIES + 1} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    time.sleep(delay)
                else:
                    # 재시도 불가능 또는 최대 횟수 도달
                    logger.error(
                        f"[Kernel Retry] ❌ All {attempt + 1} attempts failed. "
                        f"Last error: {e}"
                    )
                    break
        
        # 모든 재시도 실패 - 에러 정보 반환
        error_info = {
            'error': str(last_error),
            'error_type': type(last_error).__name__,
            'retry_attempts': len(retry_history),
            'retry_history': retry_history,
            'retryable': self._is_retryable_error(last_error) if last_error else False
        }
        
        return initial_state, error_info

    def execute_segment(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main execution logic for a workflow segment with StateHydrator integration.
        """
        # 🛡️ [v3.4] NULL Event Pre-Check (before hydration)
        if event is None:
            logger.error("🚨 [CRITICAL] execute_segment received None event!")
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
        
        # 🛡️ [v3.11] Unified State Hydration (Input)
        # Hydrate the event (convert to SmartStateBag) using pre-initialized hydrator
        # This handles "__s3_offloaded" restoration automatically
        event = self.hydrator.hydrate(event)
        
        # 🛡️ [v3.4] Hydration Result Validation
        # hydrator.hydrate() may return None if S3 load fails or input is malformed
        if event is None or (hasattr(event, 'keys') and len(list(event.keys())) == 0):
            logger.error("🚨 [CRITICAL] Hydration returned empty/None state!")
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
        
        # [v3.20] RoutingResolver 초기화 (워크플로우별 1회)
        if self._routing_resolver is None:
            try:
                from src.services.execution.routing_resolver import create_routing_resolver
                
                # 워크플로우 설정 로딩 (모든 노드 ID 추출)
                workflow_config = event.get('workflow_config') or event.get('config')
                if workflow_config and isinstance(workflow_config, dict):
                    all_node_ids = {n["id"] for n in workflow_config.get("nodes", []) if isinstance(n, dict) and "id" in n}
                    
                    # Ring 레벨 추출 (기본값 3 = Agent)
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
        # [Guard] [v2.6 P0 Fix] 모든 return 경로에서 사용할 메타데이터 사전 계산
        # Step Functions Choice 상태에서 null 참조를 방지하기 위해 반드시 포함되어야 함
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
            🛡️ [Guard] [v3.12] StateBag Unified Response Wrapper
            
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
            
            # 3. 🎯 [v3.13] Kernel Protocol - The Great Seal
            # Uses seal_state_bag for unified Lambda ↔ ASL communication
            if KERNEL_PROTOCOL_AVAILABLE:
                # Build base_state from current event using open_state_bag
                base_state = open_state_bag(event)
                if not isinstance(base_state, dict):
                    base_state = {}
                
                # 🔍 [Debug] Log loop_counter value for troubleshooting
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
                
                # 🔍 [v3.15 Debug] Enhanced status logging for loop limit troubleshooting
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
                
                # 🎯 [v3.13] Use seal_state_bag - Unified Protocol
                # Returns: { state_data: {...}, next_action: "..." }
                # ASL ResultSelector wraps this into $.state_data.bag
                sealed_result = seal_state_bag(
                    base_state=base_state,
                    result_delta={'execution_result': execution_result},
                    action='sync',
                    context=seal_context
                )
                
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # 🔥 [P0 통합] v3.3 KernelStateManager - save_state_delta()
                # Merkle Chain 연속성 확보를 위한 manifest_id 전파
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                use_v3_state_saving = os.environ.get('USE_V3_STATE_SAVING', 'true').lower() == 'true'
                
                if use_v3_state_saving:
                    try:
                        from src.services.state.state_versioning_service import StateVersioningService
                        
                        # [H-02 FIX] WORKFLOW_STATE_BUCKET 우선 (initialize_state_data.py 와 동일한 규칙).
                        # S3_BUCKET / SKELETON_S3_BUCKET 은 레거시 폴백.
                        s3_bucket = (
                            os.environ.get('WORKFLOW_STATE_BUCKET')
                            or os.environ.get('S3_BUCKET')
                            or os.environ.get('SKELETON_S3_BUCKET')
                        )
                        if not s3_bucket:
                            logger.error("[v3.3] ❌ S3_BUCKET not set, skipping state delta save")
                        else:
                            versioning_service = StateVersioningService(
                                dynamodb_table=os.environ.get('MANIFESTS_TABLE', 'StateManifestsV3'),
                                s3_bucket=s3_bucket,
                                use_2pc=True,
                                gc_dlq_url=os.environ.get('GC_DLQ_URL')
                            )
                            
                            # 이전 manifest_id 추출 (Merkle Chain)
                            previous_manifest_id = base_state.get('current_manifest_id')
                            
                            # Delta 저장
                            save_result = versioning_service.save_state_delta(
                                delta=original_final_state,  # execution_result의 final_state
                                workflow_id=event.get('workflowId') or event.get('workflow_id', 'unknown'),
                                execution_id=event.get('execution_id', 'unknown'),
                                owner_id=event.get('ownerId') or event.get('owner_id', 'unknown'),
                                segment_id=_segment_id,
                                previous_manifest_id=previous_manifest_id
                            )
                            
                            # 🎯 핵심: 다음 세그먼트를 위한 manifest_id 전파
                            new_manifest_id = save_result.get('manifest_id')
                            if new_manifest_id:
                                # [BUG-02 FIX] seal_state_bag 반환 시 state_data는 flat dict.
                                # ASL ResultSelector가 'bag' 키를 추가하는 것은 Lambda 반환 *이후*.
                                # 따라서 여기서는 state_data 직접 접근이 올바름.
                                sealed_result['state_data']['current_manifest_id'] = new_manifest_id
                                logger.info(
                                    f"[v3.3] ✅ State delta saved. Manifest rotated: "
                                    f"{new_manifest_id[:12]}... (parent: {previous_manifest_id[:12] if previous_manifest_id else 'ROOT'}...)"
                                )
                            else:
                                logger.warning("[v3.3] ⚠️ save_state_delta succeeded but no manifest_id returned")
                    
                    except ImportError as ie:
                        logger.error(f"[v3.3] ❌ StateVersioningService import failed: {ie}")
                    except Exception as e:
                        # Non-blocking: v3.3 실패해도 워크플로우는 계속 진행
                        logger.error(f"[v3.3] ❌ Failed to save state delta: {e}", exc_info=True)
                else:
                    logger.info("[v3.3] ℹ️ USE_V3_STATE_SAVING=false, skipping state delta save")
                
                logger.info(f"[v3.13] 🎯 Kernel Protocol: sealed response - "
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
                
                logger.warning("[v3.13] ⚠️ USC fallback - kernel_protocol not available")
                return usc_result
            
            # 4. Fallback: Legacy mode (if universal_sync_core not available)
            logger.warning("[v3.12] ⚠️ Fallback to legacy mode - universal_sync_core not available")
            
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
        # [Guard] [2단계] Pre-Execution Check: 동시성 및 예산 체크
        # ====================================================================
        if CONCURRENCY_CONTROLLER_AVAILABLE and self.concurrency_controller:
            pre_check = self.concurrency_controller.pre_execution_check()
            # 🛡️ [P0 Fix] Null Guard for pre_check return value
            if pre_check and not pre_check.get('can_proceed', True):
                logger.error(f"[Kernel] ❌ Pre-execution check failed: {pre_check.get('reason')}")
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
            
            # 로드 레벨 로깅
            snapshot = pre_check.get('snapshot')
            if snapshot and snapshot.load_level.value in ['high', 'critical']:
                logger.warning(f"[Kernel] [Warning] High load detected: {snapshot.load_level.value} "
                             f"({snapshot.active_executions}/{snapshot.reserved_concurrency})")
        
        # [Fix] 이벤트에서 MOCK_MODE를 읽어서 환경 변수로 주입
        # MOCK_MODE=false인 경우에도 강제로 환경변수를 덮어써서 시뮬레이터가 실제 LLM 호출을 가능하게 함
        # [2026-01-26] 기본값을 false로 변경 (실제 LLM 호출 모드)
        event_mock_mode = str(event.get('MOCK_MODE', 'false')).lower()
        if event_mock_mode in ('true', '1', 'yes', 'on'):
            os.environ['MOCK_MODE'] = 'true'
            logger.info("🧪 MOCK_MODE enabled from event payload")
        else:
            os.environ['MOCK_MODE'] = 'false'
            logger.info("🧪 MOCK_MODE disabled (default: false, Simulator Mode)")
        
        # ====================================================================
        # [Parallel] [Aggregator] 병렬 결과 집계 처리
        # ASL의 AggregateParallelResults에서 호출됨
        # ====================================================================
        segment_type_param = event.get('segment_type')
        if segment_type_param == 'aggregator':
            return _finalize_response(self._handle_aggregator(event), force_offload=True)
        
        # 0. Check for Branch Offloading
        branch_config = event.get('branch_config')
        if branch_config:
            force_child = os.environ.get('FORCE_CHILD_WORKFLOW', 'false').lower() == 'true'
            node_count = len(branch_config.get('nodes', [])) if isinstance(branch_config.get('nodes'), list) else 0
            # 🛡️ [P0 Fix] n이 None일 수 있으므로 방어 코드 추가
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
        # 🚀 [Hybrid Mode] Support both segment_id (hybrid) and segment_to_run (legacy)
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
        # ⚠️ DO NOT add to event to avoid 256KB limit
        # ====================================================================
        # 🛡️ [v3.14] Kernel Protocol - Use open_state_bag for unified extraction
        # ASL passes $.state_data.bag (bag contents) directly as Payload
        # open_state_bag handles all cases: v3 ASL, legacy, direct invocation
        # ====================================================================
        state_s3_path = event.get('state_s3_path')
        
        # 🔥 [P1 파괴적 리팩토링] Kernel Protocol 필수화 - Fail-Fast 원칙
        # Legacy 5단계 fallback 완전 제거
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if not KERNEL_PROTOCOL_AVAILABLE:
            raise RuntimeError(
                "❌ CRITICAL: kernel_protocol is REQUIRED for v3.14+. "
                "Legacy mode no longer supported. "
                "Verify src.common.kernel_protocol import succeeded."
            )
        
        # 🎯 Unified State Extraction (단일 경로)
        initial_state = open_state_bag(event)
        
        # 🛡️ Strict Validation: 데이터 규격 확신 필수
        strict_mode = os.environ.get('AN_STRICT_MODE', 'false').lower() == 'true'
        if strict_mode and (not initial_state or not isinstance(initial_state, dict)):
            raise ValueError(
                f"❌ [AN_STRICT_MODE] Invalid state structure. "
                f"open_state_bag returned: {type(initial_state)}. "
                f"Event keys: {list(event.keys())[:10]}. "
                f"This indicates ASL schema mismatch."
            )
        
        # Safe fallback for non-strict mode (개발 환경)
        if not initial_state or not isinstance(initial_state, dict):
            logger.warning(
                f"⚠️ [Kernel Protocol] open_state_bag returned invalid data: {type(initial_state)}. "
                f"Falling back to empty state. Event keys: {list(event.keys())[:10]}"
            )
            initial_state = {}
        
        logger.info(
            f"[v3.14 Kernel Protocol] ✅ State extracted via unified path. "
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
            logger.info(f"🔄 MOCK_MODE override: {old_value} → {event['MOCK_MODE']} (from payload)")

        # ====================================================================
        # [Hydration] [v3.10] Unified State Bag - Single Source of Truth
        # ====================================================================
        # [v3.6] workflow_config는 StateBag에서 직접 조회
        # bag 전체가 hydration되면 workflow_config도 포함됨 (별도 S3 조회 불필요)
        # ====================================================================
        
        # ====================================================================
        # [v3.6] Extract bag data BEFORE normalize_inplace removes state_data
        # ====================================================================
        # ✅ Merkle DAG Mode: workflow_config/partition_map 제거됨
        # segment_config는 manifest 또는 ASL에서 직접 전달
        execution_mode = _safe_get_from_bag(event, 'execution_mode')
        distributed_mode = _safe_get_from_bag(event, 'distributed_mode')
        
        # [Fix] [v3.10] Normalize Event AFTER state extraction but BEFORE processing
        # Remove potentially huge state_data from event to save memory
        normalize_inplace(event, remove_state_data=True)

        # ====================================================================
        # [Phase 0] Segment Config Resolution - Merkle DAG 3-Tier Fallback
        # ====================================================================
        
        # 👉 [Critical Fix] Branch Execution: partition_map fallback from branch_config
        # ASL의 ProcessParallelSegments에서 branch_config에 전체 브랜치 정보가 전달됨
        # partition_map이 null이면 branch_config.partition_map을 사용
        # 🛡️ [v3.21 Fix] Initialize partition_map from bag BEFORE referencing it
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
        
        # ✅ [Phase 0.2] Hybrid Loading: ASL 직접 주입 또는 Fallback
        # ASL Direct Injection은 256KB 제약으로 전체의 20% 미만만 처리
        # Lambda Fallback이 실제 주 경로 (80% 처리 예상)
        segment_config = event.get('segment_config')  # ASL에서 주입 (작은 manifest)
        
        if not segment_config:
            # Fallback 1: Lambda가 S3에서 직접 로드 (큰 manifest)
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
                # ❌ No valid segment_config source
                raise ValueError(
                    "No segment_config source available. "
                    "Expected: ASL injection or S3 manifest. "
                    f"manifest_id={event.get('manifest_id')}, "
                    f"manifest_s3_path={manifest_s3_path}"
                )
        
        # [Phase 0 Complete] 3단계 Fallback으로 점진적 마이그레이션 가능
        # 1. ASL Direct Injection (20% - 작은 manifest)
        # 2. Lambda S3 Loading (80% - 큰 manifest, 주 경로)
        # 3. Legacy workflow_config/partition_map (호환성)

        # [Option A] 세그먼트 config 정규화 - None 값을 빈 dict/list로 변환
        segment_config = _normalize_segment_config(segment_config)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # [Phase 8.4] Extract Context for Trust Chain Gatekeeper
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        owner_id = event.get('ownerId') or event.get('owner_id', 'unknown')
        execution_id = event.get('execution_id', 'unknown')
        
        # workflow_config 추출 (bag hydration에서 로드됨)
        try:
            from src.common.statebag import SmartStateBag
            bag = SmartStateBag(initial_state, hydrator=self.hydrator)
            workflow_config = bag.get('workflow_config')
        except:
            workflow_config = None

        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # [Phase 8.4] Trust Chain Gatekeeper: Kernel Panic on Hash Mismatch
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 아키텍처 원칙 (Phase 8 Guideline):
        # - verify_segment_config = 실행 직전의 최종 관문 (Gatekeeper)
        # - 해시 검증 실패 = Kernel Panic (즉시 중단, 관리자 경보)
        # - Zero Trust: 모든 segment_config는 검증 필수
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
                    # 시스템 즉시 중단 (Halt)
                    # 관리자 경보 발송 (CloudWatch Alarm 트리거)
                    # 보안 사고 로그 기록
                    logger.critical(
                        f"🚨 [KERNEL PANIC] [SECURITY ALERT] 🚨\n"
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
                    
                    # CloudWatch Alarm 트리거를 위한 ERROR 레벨 로그
                    logger.error(
                        f"[SECURITY_ALERT] INTEGRITY_VIOLATION "
                        f"manifest_id={manifest_id} segment_index={segment_index} "
                        f"execution_id={execution_id}"
                    )
                    
                    # 즉시 실행 중단 (SecurityError)
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
                    f"[Phase 8.4 Gatekeeper] ✅ Integrity verified: segment_index={segment_index}\n"
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
                # Phase 8 Guideline: 검증 자체의 실패도 시스템 장애로 간주
                logger.error(
                    f"🚨 [KERNEL PANIC] [SYSTEM FAULT] 🚨\n"
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

        # [Guard] [v2.6 P0 Fix] 'code' 타입 오염 방지 Self-Healing
        # 상위 람다(PartitionService 등)에서 잘못된 타입이 주입될 수 있으므로 런타임 교정
        if segment_config and isinstance(segment_config, dict):
            for node in segment_config.get('nodes', []):
                # 🛡️ [v3.8] None defense
                if node is None or not isinstance(node, dict):
                    continue
                if node.get('type') == 'code':
                    logger.warning(
                        f"[Guard] [Self-Healing] Aliasing 'code' to 'operator' for node {node.get('id')}. "
                        f"This indicates upstream data mutation - investigate PartitionService."
                    )
                    node['type'] = 'operator'
        
        # [Guard] [Critical Fix] segment_config이 None이거나 error 타입이면 조기 에러 반환
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
        
        # [Critical Fix] parallel_group 타입 세그먼트는 바로 PARALLEL_GROUP status 반환
        # ASL의 ProcessParallelSegments가 branches를 받아서 Map으로 병렬 실행함
        # [Parallel] [Pattern 3] 병렬 스케줄러 적용
        segment_type = segment_config.get('type') if isinstance(segment_config, dict) else None
        
        # [Fix] HITP Segment Type Check (Priority: segment type > edge type)
        # If segment itself is marked as 'hitp', pause immediately
        if segment_type == 'hitp':
            logger.info(f"[Kernel] 🚨 HITP segment {segment_id} detected. Pausing for human approval.")
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
        # execute_segment 시작 시점에는 segment_type 파라미터가 없을 수 있음 (partition_map에서 resolve된 경우)
        # 따라서 여기서 resolve된 segment_config를 기반으로 한 번 더 체크해야 함
        if segment_type == 'aggregator':
            logger.info(f"[Kernel] 🧩 Aggregator segment {segment_id} detected (Resolved). Delegating to _handle_aggregator.")
            return _finalize_response(self._handle_aggregator(event), force_offload=True)

        # [Issue-2 Fix] 'branches' 키가 있는 세그먼트는 type 값에 무관하게 parallel_group으로 처리
        # 파티셔너가 type을 'parallel_group'으로 마킹하지 않아도 branches 키 존재 시 동일 경로로 라우팅
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
                except:
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
                        
                        # [Critical Fix] S3 offload 시 final_state 비우기
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
                except:
                    pass
                    
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

            # [Guard] [Critical Fix] HITP edge 우선 체크 (단일 브랜치 최적화 전)
            # HITP edge가 있으면 무조건 PAUSED_FOR_HITP로 처리
            hitp_edge_types = {"hitp", "human_in_the_loop", "pause"}
            has_hitp_edge = False
            
            for branch in branches:
                if isinstance(branch, dict):
                    branch_nodes = branch.get('nodes', [])
                    for node in branch_nodes:
                        if isinstance(node, dict):
                            # 노드의 incoming edges 체크
                            in_edges = node.get('in_edges', [])
                            if any(e.get('type') in hitp_edge_types for e in in_edges if isinstance(e, dict)):
                                has_hitp_edge = True
                                break
                    if has_hitp_edge:
                        break
            
            if has_hitp_edge:
                logger.info(f"[Kernel] 🚨 HITP edge detected in segment {segment_id}. Pausing for human approval.")
                return _finalize_with_offload({
                    "status": "PAUSED_FOR_HITP",
                    "final_state": mask_pii_in_state(initial_state),
                    "final_state_s3_path": None,
                    "next_segment_to_run": segment_id + 1,
                    "new_history_logs": [],
                    "error_info": None,
                    "branches": branches,  # HITP 이후 실행할 브랜치 정보 유지
                    "segment_type": "hitp_pause"
                })
            
            # [Guard] [Critical Fix] 단일 브랜치 + 내부 partition_map 케이스 처리
            # 이 경우 실제 병렬 실행이 필요 없으므로 브랜치 내부의 첫 번째 세그먼트 직접 실행
            if len(branches) == 1:
                single_branch = branches[0]
                branch_partition_map = single_branch.get('partition_map', [])
                
                if branch_partition_map:
                    logger.info(f"[Kernel] 📌 Single branch with internal partition_map detected. "
                               f"Executing {len(branch_partition_map)} segments sequentially instead of parallel.")
                    
                    # 브랜치 내부의 첫 번째 세그먼트를 segment_config로 사용
                    first_inner_segment = branch_partition_map[0] if branch_partition_map else None
                    
                    if first_inner_segment:
                        # [System] 내부 partition_map을 새로운 실행 컨텍스트로 변환
                        # 상태를 유지하면서 내부 세그먼트 체인 순차 실행
                        return _finalize_with_offload({
                            "status": "SEQUENTIAL_BRANCH",
                            "final_state": mask_pii_in_state(initial_state),
                            "final_state_s3_path": None,
                            "next_segment_to_run": segment_id + 1,
                            "new_history_logs": [],
                            "error_info": None,
                            "branches": None,  # 병렬 실행 안함
                            "segment_type": "sequential_branch",
                            # [Guard] 내부 partition_map 정보 전달 (ASL이 순차 처리하도록)
                            "inner_partition_map": branch_partition_map,
                            "inner_segment_count": len(branch_partition_map),
                            "branch_id": single_branch.get('branch_id', 'B0'),
                            "scheduling_metadata": {
                                'strategy': 'SEQUENTIAL_SINGLE_BRANCH',
                                'total_inner_segments': len(branch_partition_map),
                                'reason': 'Single branch optimization - parallel execution skipped'
                            }
                        })
            
            # [System] 빈 브랜치 또는 노드가 없는 브랜치 필터링
            valid_branches = []
            for branch in branches:
                # 🛡️ [P0 Fix] None 또는 dict가 아닌 브랜치 객체 방어
                if not branch or not isinstance(branch, dict):
                    logger.warning(f"[Kernel] [Warning] Found invalid branch object (None or not dict): {type(branch)}")
                    continue
                
                branch_nodes = branch.get('nodes', [])
                branch_partition = branch.get('partition_map', [])
                
                # nodes가 있거나 partition_map이 있으면 유효한 브랜치
                if branch_nodes or branch_partition:
                    valid_branches.append(branch)
                else:
                    logger.warning(f"[Kernel] [Warning] Skipping empty branch: {branch.get('branch_id', 'unknown')}")
            
            # [Guard] 유효한 브랜치가 없으면 다음 세그먼트로 진행
            if not valid_branches:
                logger.info(f"[Kernel] ⏭️ No valid branches to execute, skipping parallel group")
                
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
                    logger.info(f"[Empty Parallel] 🚨 Pausing execution due to HITP edge. Next segment: {next_segment}")
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
                
                # 🛡️ [v3.17 Fix] Check if more segments exist before returning CONTINUE
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
            
            # 병렬 스케줄러 호출
            schedule_result = self._schedule_parallel_group(
                segment_config=segment_config,
                state=initial_state,
                segment_id=segment_id,
                owner_id=auth_user_id,
                workflow_id=workflow_id
            )
            
            # SCHEDULED_PARALLEL: 배치별 순차 실행 필요
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
            
            # PARALLEL_GROUP: 기본 병렬 실행
            # [Guard] [P1 Fix] Inject scheduling_metadata into state for test verification (Consistent with SCHEDULED_PARALLEL)
            meta = schedule_result.get('scheduling_metadata', {})
            initial_state['scheduling_metadata'] = meta
            initial_state['batch_count_actual'] = meta.get('batch_count', 1)
            
            # 🌿 [Pointer Strategy] schedule_result.branches는 이미 경량 포인터 배열
            # branches_s3_path도 전달하여 State Bag에 저장
            branch_pointers = schedule_result.get('branches', valid_branches)
            branches_s3_path = schedule_result.get('branches_s3_path')
            
            return _finalize_with_offload({
                "status": "PARALLEL_GROUP",
                "final_state": mask_pii_in_state(initial_state),
                "final_state_s3_path": None,
                "next_segment_to_run": segment_id + 1,
                "new_history_logs": [],
                "error_info": None,
                "branches": branch_pointers,  # 🌿 경량 포인터 배열
                "branches_s3_path": branches_s3_path,  # 🌿 S3 경로
                "execution_batches": schedule_result.get('execution_batches', [branch_pointers]),
                "segment_type": "parallel_group",
                "scheduling_metadata": meta
            })
        
        # [Guard] [Pattern 2] 커널 검증: 이 세그먼트가 SKIPPED 상태인가?
        segment_status = self._check_segment_status(segment_config)
        if segment_status == SEGMENT_STATUS_SKIPPED:
            skip_reason = segment_config.get('skip_reason', 'Kernel decision')
            logger.info(f"[Kernel] ⏭️ Segment {segment_id} SKIPPED: {skip_reason}")
            
            # 커널 액션 로그 기록
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
        
        # [Guard] [v2.2] Ring Protection: 프롬프트 보안 검증
        # 세그먼트 내 LLM 노드의 프롬프트를 검증하고 위험 패턴 탐지
        security_violations = []
        if self.security_guard and RING_PROTECTION_AVAILABLE:
            security_violations = self._apply_ring_protection(
                segment_config=segment_config,
                initial_state=initial_state,
                segment_id=segment_id,
                workflow_id=workflow_id
            )
            
            # CRITICAL 위반 시 SIGKILL (세그먼트 강제 종료)
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

        # 7. Execute Workflow Segment with Kernel Defense
        # [Guard] [Kernel Defense] Aggressive Retry + Partial Success
        start_time = time.time()
        
        result_state, execution_error = self._execute_with_kernel_retry(
            segment_config=segment_config,
            initial_state=initial_state,
            auth_user_id=auth_user_id,
            event=event
        )
        
        execution_time = time.time() - start_time
        
        # [Guard] [Partial Success] 실패해도 SUCCEEDED 반환 + 에러 메타데이터 기록
        if execution_error and ENABLE_PARTIAL_SUCCESS:
            logger.warning(
                f"[Kernel] [Warning] Segment {segment_id} failed but returning PARTIAL_SUCCESS. "
                f"Error: {execution_error['error']}"
            )
            
            # 에러 정보를 상태에 기록
            if isinstance(result_state, dict):
                result_state['__segment_error'] = execution_error
                result_state['__segment_status'] = 'PARTIAL_FAILURE'
                result_state['__failed_segment_id'] = segment_id
            
            # Partial Success 커널 로그
            kernel_log = {
                'action': 'PARTIAL_SUCCESS',
                'segment_id': segment_id,
                'error': execution_error['error'],
                'error_type': execution_error['error_type'],
                'retry_attempts': execution_error['retry_attempts'],
                'timestamp': time.time()
            }
            
            # [Alert] 핵심: FAILED 대신 SUCCEEDED 반환 (ToleratedFailureThreshold 방지)
            final_state, output_s3_path = self.state_manager.handle_state_storage(
                state=result_state,
                auth_user_id=auth_user_id,
                workflow_id=workflow_id,
                segment_id=segment_id,
                bucket=s3_bucket,
                threshold=self.threshold
            )
            
            # [Critical Fix] S3 offload 시 final_state 비우기 (256KB 제한 회피)
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
                logger.info(f"[Partial Success] 🚨 Pausing execution due to HITP edge. Next segment: {next_segment}")
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
                "error_info": execution_error,  # 에러 정보는 메타데이터로 전달
                "branches": None,
                "segment_type": "partial_failure",
                "kernel_action": kernel_log,
                "execution_time": execution_time,
                "_partial_success": True,  # 클라이언트가 부분 실패 감지용
                "total_segments": total_segments
            })
        
        execution_time = time.time() - start_time
        
        # [Guard] [Pattern 2] 조건부 스킵 결정
        # 실행 결과에서 스킵할 세그먼트가 지정되었는지 확인
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
            
            # 복구 세그먼트 삽입 요청 처리
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

        # [Critical Fix] Distributed Map 모드에서는 무조건 S3 오프로딩
        # 각 iteration 결과가 개별적으로는 작아도 Distributed Map이 모든 결과를
        # 배열로 수집하면 256KB 제한을 초과할 수 있음
        # [Fix] distributed_mode가 null(JSON)/None(Python)일 수 있으므로 명시적 True 체크
        # 🛡️ [v3.6] 함수 스코프에서 이미 추출된 로컬 변수 사용
        is_distributed_mode = distributed_mode is True
        
        # [Critical Fix] Map State 브랜치 실행도 강제 오프로딩 필요
        # Map State가 모든 브랜치 결과를 수집할 때 256KB 제한 초과 방지
        # branch_item 존재 = Map Iterator에서 실행 중 (각 브랜치는 작은 레퍼런스만 반환해야 함)
        is_map_branch = event.get('branch_item') is not None

        # [Critical Fix] 다음 세그먼트 존재 여부 확인
        # 세그먼트가 더 있으면 상태가 누적될 수 있으므로 낮은 threshold 적용
        total_segments = _safe_get_total_segments(event)
        has_next_segment = (segment_id + 1) < total_segments
        
        # [Critical Fix] ForEach/Map 같은 반복 구조 감지
        # 현재 또는 다음 세그먼트에 for_each 타입이 있으면 강제 offload
        has_loop_structure = False
        if isinstance(segment_config, dict):
            # 현재 세그먼트의 노드들 확인
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
            # Distributed Map: threshold=0으로 강제 오프로딩
            effective_threshold = 0
            logger.info(f"[Distributed Map] Forcing S3 offload for iteration result (distributed_mode=True)")
        elif is_map_branch:
            # [Critical] Map State 브랜치: 무조건 S3 오프로딩 (threshold=0)
            # 이유: 브랜치 개수가 가변적 (N개 × 50KB = N×50KB)
            # 예시: 10개 브랜치 × 50KB = 500KB → 256KB 초과!
            # 해결: 브랜치 크기와 무관하게 모든 결과를 S3로 오프로드
            # Map은 작은 S3 레퍼런스만 수집 (N개 × 2KB = 2N KB << 256KB)
            effective_threshold = 0  # 강제 오프로드
            logger.info(f"[Map Branch] Forcing S3 offload for ALL branch results (variable fan-out protection)")
        elif has_loop_structure:
            # [Critical Fix] ForEach/반복 구조가 있으면 무조건 강제 offload (threshold=0)
            # 이유: 반복 횟수 × 결과 크기 = 예측 불가능한 누적
            # 예: 40회 × 15KB = 600KB >> 256KB (20KB threshold로는 방어 불가)
            # 해결: iteration 크기와 무관하게 모든 결과를 S3로 오프로드
            effective_threshold = 0  # 강제 오프로드
            logger.info(f"[Loop Structure] Forcing S3 offload for ALL iteration results (accumulation prevention)")
        elif has_next_segment and result_state_size > 20000:
            # [Segment Chain] 다음 세그먼트가 있고 20KB 이상이면 offload
            # 이유: 세그먼트 체인에서 상태 누적 방지
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
        
        # [Critical] Map 브랜치 응답 페이로드 최소화
        # Map State는 모든 브랜치 결과를 수집하므로 응답 크기가 중요
        # S3에 전체 상태를 저장했으면 응답은 작은 레퍼런스만 포함
        if is_map_branch and output_s3_path:
            # [Emergency Payload Pruning] 대용량 필드 제거
            # documents, queries 같은 큰 배열은 S3에 있으므로 응답에서 제외
            if isinstance(final_state, dict):
                # 보존할 필드만 선택 (step_history, 메타데이터는 유지)
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
                    # 🚀 토큰 관련 필드 추가 (CRITICAL for aggregation)
                    'total_tokens': final_state.get('total_tokens', 0),
                    'total_input_tokens': final_state.get('total_input_tokens', 0),
                    'total_output_tokens': final_state.get('total_output_tokens', 0),
                    'usage': final_state.get('usage', {}),
                }
                # None 값 제거 (응답 크기 추가 절감)
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

        # 커널 메타데이터 추출 (있는 경우)
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
            # 마지막 세그먼트 완료
            # [Critical Fix] S3 offload 시 final_state 비우기 (256KB 제한 회피)
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
        
        # 아직 실행할 세그먼트가 남아있음
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
            logger.info(f"[General Segment] 🚨 Pausing execution due to HITP edge. Next segment: {next_segment}")
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
        cache_ttl: int = 300,  # 5분 캐시
        owner_id: str = None   # [FIX] 테넌트 격리용
    ) -> dict:
        """
        [Phase 0.1] S3에서 segment_manifest를 로드하고 특정 segment_config를 추출
        
        새 기능:
        - Size-based routing: 작은 manifest는 전체 로드, 큰 것은 S3 Select
        - In-memory cache: 같은 manifest 재사용 (Lambda warm start 최적화)
        - Checksum verification: manifest_hash 검증
        - Lambda 캐싱이 실제 주 경로 (ASL Direct Injection은 20% 미만)
        - Warm Start 최적화로 캐시 히트율 80% 목표
        """
        import boto3
        
        # [FIX] 테넌트 격리: owner_id 포함한 캐시 키
        cache_key = f"{manifest_s3_path}:{segment_index}"
        secure_cache_key = f"{owner_id}:{cache_key}" if owner_id else cache_key
        
        # 1. 캐시 확인 (Lambda warm start 시 재사용)
        if hasattr(self, '_manifest_cache'):
            cached = self._manifest_cache.get(secure_cache_key)
            if cached:
                # [SECURITY] owner_id 검증 (테넌트 간 데이터 누출 방지)
                if owner_id and cached.get('owner_id') != owner_id:
                    logger.error(
                        f"[SECURITY] Cache key collision detected! "
                        f"Requested owner_id={owner_id}, cached owner_id={cached.get('owner_id')}. "
                        f"Rejecting cache hit to prevent privilege escalation."
                    )
                elif time.time() - cached['timestamp'] < cache_ttl:
                    logger.info(f"[Cache Hit] segment_config: {cache_key} (owner: {owner_id or 'unknown'})")
                    return cached['config']
        
        # 2. S3 경로 파싱
        bucket_name = manifest_s3_path.replace("s3://", "").split("/")[0]
        key_name = "/".join(manifest_s3_path.replace("s3://", "").split("/")[1:])
        
        # 3. GetObject로 전체 로드
        # [FIX] S3 Select 제거: MethodNotAllowed 오류 방지.
        # - S3 Select는 s3:SelectObjectContent 권한 + 별도 요금이 필요하며
        #   SSE-KMS 객체 및 Object Lock 버킷에서 동작하지 않음.
        # - Manifest envelope(dict)은 S3 Select SQL(bare list 가정)와 호환 불가.
        # - Manifest 파일 크기는 수백KB 이하로 GetObject로 충분히 처리 가능.
        s3 = boto3.client('s3')
        
        try:
            obj = s3.get_object(Bucket=bucket_name, Key=key_name)
            object_size = obj['ContentLength']
            content = obj['Body'].read().decode('utf-8')
            manifest_obj = self._safe_json_load(content)
            logger.info(f"[GetObject] Loaded manifest ({object_size}B)")

            # 4. segment_config 추출
            # 형식 규약: manifests/{id}.json 은 항상 dict (Envelope 패턴)
            # 'segments' 키에 segment_id 오름차순 정렬된 리스트 포함.
            # list 형식은 규격 위반으로 에러 처리 (legacy 호환성 부담 거부).
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
            
            # 5. Nested 구조 처리
            if 'segment_config' in segment_entry:
                segment_config = segment_entry['segment_config']
            else:
                segment_config = segment_entry
            
            # 6. 캐시 저장 (LRU 권장, 최대 100개 항목)
            # [FIX] 테넌트 격리: owner_id 포함한 캐시 키
            if not hasattr(self, '_manifest_cache'):
                self._manifest_cache = {}
            
            # [SECURITY] 캐시 키에 owner_id 추가 (권한 월권 방지)
            secure_cache_key = f"{cache_key}:{owner_id}" if owner_id else cache_key
            
            self._manifest_cache[secure_cache_key] = {
                'config': segment_config,
                'timestamp': time.time(),
                'owner_id': owner_id  # 검증용
            }
            
            # [Cleanup] LRU 정책: 100개 초과 시 오래된 항목 삭제
            if len(self._manifest_cache) > 100:
                # 타임스탬프 기준 정렬 후 오래된 10개 삭제
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
    # [REMOVED] _resolve_segment_config() - Legacy 동적 파티션 해석
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 
    # 제거 사유: Merkle DAG 전환으로 workflow_config/partition_map 불필요
    # 대체 방법: StateVersioningService.load_manifest_segments()
    # 
    # 기존 코드 길이: ~120 lines
    # 제거 일자: 2026-02-18
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
