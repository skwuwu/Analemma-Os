"""
🎯 Universal Sync Core - Function-Agnostic 데이터 파이프라인

v3.3 - "Unified Pipe: 탄생부터 소멸까지"

핵심 원칙:
    "함수가 무엇이든 상관없이, 데이터가 흐르는 파이프 자체를 표준화"
    
    모든 액션 함수는 이제 "3줄짜리 래퍼"입니다:
        1. 입력 전처리 (액션별 특수 필드 추출)
        2. universal_sync_core() 호출
        3. 응답 포맷팅

모든 StateDataManager 액션은 이 코어를 통과합니다:
    1. flatten_result() - 입력 정규화 (액션별 스마트 추출)
    2. merge_logic() - 상태 병합 (Shallow Merge + Copy-on-Write)
    3. optimize_and_offload() - 자동 최적화 (P0~P2 자동 해결)

데이터 생애 주기 (Unified Pipe):
    - 탄생 (Init): {} → Universal Sync → StateBag v0
    - 성장 (Sync): StateBag vN + Result → Universal Sync → StateBag vN+1
    - 협업 (Aggregate): StateBag vN + Branches → Universal Sync → StateBag vFinal

성능 최적화:
    - ① Copy-on-Write: 전체 deepcopy 대신 변경된 서브트리만 복사
    - ② Shallow Merge: 불필요한 중첩 복사 방지
    - ③ Checksum 검증: S3 로드 시 데이터 무결성 확인

P0~P2 자동 해결:
    - P0: 어떤 경로로 들어왔든 크면 S3로 간다 (T=0 가드레일 포함)
    - P1: 포인터 비대화 방지 (모든 액션에 자동 적용)
    - P2: 스냅샷도 코어 통과 → 중복 저장 방지

v3.3 - 2026-01-29 (Unified Pipe: Day-Zero Sync)
"""

import json
import hashlib
import time
from typing import Dict, Any, Optional, List, Callable, TypedDict, Literal
from datetime import datetime, timezone
from abc import ABC, abstractmethod

# Lazy imports to avoid circular dependencies
_logger = None
_s3_client = None
_S3_BUCKET = None

def _get_logger():
    global _logger
    if _logger is None:
        from aws_lambda_powertools import Logger
        import os
        _logger = Logger(
            service=os.getenv("AWS_LAMBDA_FUNCTION_NAME", "universal-sync-core"),
            level=os.getenv("LOG_LEVEL", "INFO"),
            child=True
        )
    return _logger

def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client('s3')
    return _s3_client

def _get_s3_bucket():
    global _S3_BUCKET
    if _S3_BUCKET is None:
        import os
        # 🛡️ [Constraint] Bucket Name Consistency
        # Prioritize unified WORKFLOW_STATE_BUCKET, then legacy fallbacks
        _S3_BUCKET = (
            os.environ.get('WORKFLOW_STATE_BUCKET') or 
            os.environ.get('S3_BUCKET') or 
            os.environ.get('STATE_STORAGE_BUCKET') or
            ''
        )
        # 🛡️ [Guard] Fail-fast if no bucket configured
        if not _S3_BUCKET:
            _get_logger().error(
                "[CRITICAL] No S3 bucket configured! "
                "Set WORKFLOW_STATE_BUCKET, S3_BUCKET, or STATE_STORAGE_BUCKET env var."
            )
    return _S3_BUCKET


# ============================================
# Type Definitions
# ============================================

class MergeStrategy(TypedDict, total=False):
    """필드별 병합 전략"""
    list_strategy: Literal['append', 'replace', 'dedupe_append', 'set_union']
    conflict_resolution: Literal['latest', 'base', 'delta']
    deep_merge_fields: List[str]


class SyncContext(TypedDict, total=False):
    """동기화 컨텍스트"""
    execution_id: str
    action: str
    merge_strategy: MergeStrategy
    idempotency_key: str


# ============================================
# Constants
# ============================================

# 오프로딩 제외 제어 필드
CONTROL_FIELDS_NEVER_OFFLOAD = frozenset({
    'execution_id',
    'segment_to_run', 
    'segment_id',  # 🛡️ [Fix] Routing safety
    'loop_counter',
    'next_action',
    'status',
    'idempotency_key',
    'state_s3_path',
    'pre_snapshot_s3_path',
    'post_snapshot_s3_path',
    'last_update_time',
    'payload_size_kb',
    'AUTO_RESUME_HITP',  # 시뮬레이터 HITP 자동 승인 플래그 (USC 경로 보존)
    'MOCK_MODE',         # 모의 실행 모드 플래그 (USC 경로 보존)
})

# 리스트 필드 기본 병합 전략
LIST_FIELD_STRATEGIES: Dict[str, str] = {
    'state_history': 'dedupe_append',      # 중복 제거 후 추가
    'new_history_logs': 'dedupe_append',
    'failed_branches': 'append',           # 그냥 추가
    'distributed_outputs': 'append',
    'branches': 'replace',                 # 교체 (최신 브랜치 정보)
    'chunk_results': 'replace',
    '_failed_segments': 'replace',         # 매 aggregate마다 최신 값으로 교체 (누적 방지)
}

# [v3.22] Bag 구조 키 — flat-merge 시 루트에 올리지 않을 커널 전용 키
# run_workflow() 결과를 delta 루트에 직접 플랫 머지할 때 이 키들은 제외함
_BAG_STRUCTURAL_SKIP: frozenset = frozenset({
    # Routing / identity keys — kernel owns these, never merge from workflow output
    'workflow_config', 'partition_map', 'segment_manifest_s3_path',
    'execution_id', 'idempotency_key', 'ownerId', 'workflowId',
    'segment_to_run', 'loop_counter', 'segment_id', 'total_segments',
    'state_s3_path', 'payload_size_kb', 'last_update_time',
    'AUTO_RESUME_HITP', 'MOCK_MODE', 'manifest_id', 'branches_s3_path',
    'pre_snapshot_s3_path', 'post_snapshot_s3_path',
    # [v3.22 SFN-size fix] Volatile runtime-internal keys — flat-merging these to bag root
    # causes unbounded payload growth and SFN 256 KB limit violations:
    #   • step_history  — LangGraph per-node trace list; merge_logic uses 'append' strategy
    #                     so it grows O(segments × nodes). Handled via new_history_logs instead.
    #   • execution_logs — similar ephemeral log list; not consumed by verifiers
    #   • _metadata      — per-LLM-call internal dict written by llm_chat_runner
    #   • _kernel_execution_summary — accumulating kernel stats dict
    'step_history',
    'execution_logs',
    '_metadata',
    '_kernel_execution_summary',
})

# 크기 임계값 (KB)
FIELD_OFFLOAD_THRESHOLD_KB = 30
FULL_STATE_OFFLOAD_THRESHOLD_KB = 100
MAX_PAYLOAD_SIZE_KB = 200
POINTER_BLOAT_WARNING_THRESHOLD_KB = 10


# ============================================
# Retry Strategy (Abstract + Concrete)
# ============================================

class RetryStrategy(ABC):
    """재시도 전략 추상 클래스"""
    
    @abstractmethod
    def execute(self, func: Callable, fallback: Any = None) -> Any:
        """재시도를 적용하여 함수 실행"""
        pass


class ExponentialBackoffRetry(RetryStrategy):
    """Exponential Backoff 재시도 전략"""
    
    def __init__(self, max_retries: int = 3, base_delay: float = 0.5, max_delay: float = 8.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
    
    def execute(self, func: Callable, fallback: Any = None) -> Any:
        logger = _get_logger()
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                    logger.warning(f"Retry {attempt+1}/{self.max_retries} after {delay:.2f}s: {e}")
                    time.sleep(delay)
        
        logger.error(f"Failed after {self.max_retries} attempts: {last_exception}")
        return fallback


class NoRetry(RetryStrategy):
    """재시도 없는 전략 (테스트용)"""
    
    def execute(self, func: Callable, fallback: Any = None) -> Any:
        try:
            return func()
        except Exception:
            return fallback


# ============================================
# StateHydrator - S3 복구 전담 클래스
# ============================================

class StateHydrator:
    """
    상태 복구 전담 클래스 (Control Plane)
    
    v3.1 개선사항:
        - Retry Strategy 주입 가능
        - Checksum 검증 지원
        - 캐시 통합
    """
    
    def __init__(
        self, 
        retry_strategy: Optional[RetryStrategy] = None,
        validate_checksum: bool = True
    ):
        self.retry_strategy = retry_strategy or ExponentialBackoffRetry()
        self.validate_checksum = validate_checksum
        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, float] = {}
        self._cache_ttl = 300  # 5분
        self._max_cache_size = 20
    
    def load_from_s3(
        self, 
        s3_path: str, 
        expected_checksum: Optional[str] = None,
        use_cache: bool = True
    ) -> Any:
        """
        재시도 + 체크섬 검증이 통합된 S3 로딩
        
        Args:
            s3_path: S3 경로 (s3://bucket/key)
            expected_checksum: 예상 MD5 해시 (검증용)
            use_cache: 캐시 사용 여부
        
        Returns:
            로드된 데이터 또는 None (실패 시)
        """
        if not s3_path or not s3_path.startswith('s3://'):
            return None
        
        # 캐시 확인
        if use_cache:
            cached = self._get_from_cache(s3_path)
            if cached is not None:
                return cached
        
        # 재시도 전략 적용
        result = self.retry_strategy.execute(
            func=lambda: self._load_and_validate(s3_path, expected_checksum),
            fallback=None
        )
        
        # 캐시 저장
        if result is not None and use_cache:
            self._put_to_cache(s3_path, result)
        
        return result
    
    def _load_and_validate(self, s3_path: str, expected_checksum: Optional[str]) -> Any:
        """단일 시도: S3 로드 + 체크섬 검증"""
        logger = _get_logger()
        s3_client = _get_s3_client()
        
        # Parse s3://bucket/key
        path_parts = s3_path.replace('s3://', '').split('/', 1)
        bucket = path_parts[0]
        key = path_parts[1] if len(path_parts) > 1 else ''
        
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read()
        content_str = content.decode('utf-8')
        
        # ③ Checksum 검증 - 데이터가 깨졌다면 에러를 내고 재시도
        if self.validate_checksum and expected_checksum:
            actual_checksum = hashlib.md5(content).hexdigest()
            if actual_checksum != expected_checksum:
                raise ValueError(
                    f"Checksum mismatch! Expected {expected_checksum}, got {actual_checksum}. "
                    "Data corrupted - triggering retry."
                )
        
        return json.loads(content_str)
    
    def _get_from_cache(self, s3_path: str) -> Optional[Any]:
        """캐시에서 데이터 조회 (TTL 체크)"""
        if s3_path not in self._cache:
            return None
        
        cache_time = self._cache_timestamps.get(s3_path, 0)
        if time.time() - cache_time >= self._cache_ttl:
            # TTL 만료
            del self._cache[s3_path]
            del self._cache_timestamps[s3_path]
            return None
        
        _get_logger().debug(f"Cache hit: {s3_path}")
        return self._cache[s3_path]
    
    def _put_to_cache(self, s3_path: str, data: Any) -> None:
        """캐시에 데이터 저장 (LRU 정책)"""
        if len(self._cache) >= self._max_cache_size:
            # 가장 오래된 항목 제거
            oldest = min(self._cache_timestamps, key=self._cache_timestamps.get)
            del self._cache[oldest]
            del self._cache_timestamps[oldest]
        
        self._cache[s3_path] = data
        self._cache_timestamps[s3_path] = time.time()
    
    def clear_cache(self) -> None:
        """캐시 초기화"""
        self._cache.clear()
        self._cache_timestamps.clear()


# 모듈 레벨 싱글턴
_default_hydrator: Optional[StateHydrator] = None

def get_default_hydrator() -> StateHydrator:
    """기본 StateHydrator 인스턴스 반환"""
    global _default_hydrator
    if _default_hydrator is None:
        _default_hydrator = StateHydrator()
    return _default_hydrator


# ============================================
# Universal Sync Core - 핵심 함수
# ============================================

def calculate_checksum(data: Any) -> str:
    """데이터의 MD5 체크섬 계산"""
    json_str = json.dumps(data, separators=(',', ':'), sort_keys=True, default=str)
    return hashlib.md5(json_str.encode('utf-8')).hexdigest()


def flatten_result(result: Any, context: Optional[SyncContext] = None) -> Dict[str, Any]:
    """
    📥 입력 정규화 (Normalize) - v3.2 스마트 추출
    
    액션 타입에 따라 적절한 필드를 추출합니다.
    리스트든 단일 객체든 동일한 Delta 형태로 평탄화합니다.
    
    액션별 추출 규칙:
        - sync: execution_result에서 상태 추출
        - aggregate_branches: 병렬 결과 배열에서 로그/상태 집계
        - aggregate_distributed: Map 결과에서 정렬 후 마지막 상태 선택
        - merge_callback: callback_result에서 사용자 응답 추출
        - merge_async: async_result에서 LLM 응답 추출
        - create_snapshot: 포인터 모드 결정
    
    🛡️ [v3.4] NEVER returns None - always returns dict
    """
    # 🛡️ [v3.4 Deep Guard] None 방지
    if result is None:
        _get_logger().debug("[Deep Guard] flatten_result received None, returning empty dict")
        return {}
    
    # 🛡️ context도 None일 수 있음
    if context is None:
        context = {'action': 'sync'}
    
    action = context.get('action', 'sync') if context else 'sync'
    
    # ============================================
    # Distributed Map ResultWriter 처리 (Manifest Pointer)
    # ============================================
    if isinstance(result, dict) and 'ResultWriterDetails' in result:
        rw_details = result['ResultWriterDetails']
        bucket = rw_details.get('Bucket')
        key = rw_details.get('Key')
        
        if bucket and key:
            try:
                # 1. Load Manifest Summary (Lightweight)
                s3 = _get_s3_client()
                obj = s3.get_object(Bucket=bucket, Key=key)
                manifest_data = json.loads(obj['Body'].read().decode('utf-8'))
                
                # 2. Extract Stats
                # Manifest structure varies, but typically contains stats or pointers
                # Assuming standard SFN Distributed Map output or custom aggregator format
                succeeded_count = 0
                failed_count = 0
                
                # Standard SFN Manifest typically separates Success/Failure file shards
                # We won't iterate all shards here (too heavy).
                # Instead, we rely on the manifest path itself as the "result".
                
                # If the manifest contains direct stats (some versions do):
                # otherwise we might need to assume success or check execution output?
                # Actually, for huge maps, we just store the pointer.
                
                return {
                    'segment_manifest_s3_path': f"s3://{bucket}/{key}",
                    'distributed_chunk_summary': {
                         'status': 'MANIFEST_ONLY',
                         'manifest_bucket': bucket,
                         'manifest_key': key
                    },
                    '_aggregation_complete': True
                }
            except Exception as e:
                _get_logger().error(f"Failed to process ResultWriter manifest: {e}")
                return {
                    'error': f"Failed to load manifest: {str(e)}",
                    '_aggregation_complete': False
                }

    # ============================================
    # Distributed Map 결과 (리스트 입력 - 인라인 모드)
    # ============================================
    if isinstance(result, list):
        # P1: execution_order 기준 정렬 → 논리적 마지막 세그먼트 보장
        sorted_results = sorted(
            [r for r in result if isinstance(r, dict)],
            key=lambda x: (str(x.get('execution_order', x.get('chunk_id', ''))), str(x.get('chunk_id', '')))
        )
        
        # 성공/실패 분리
        successful = [r for r in sorted_results if r.get('status') in ('COMPLETE', 'SUCCESS')]
        failed = [r for r in sorted_results if r.get('status') not in ('COMPLETE', 'SUCCESS', None)]
        
        # 마지막 성공 결과에서 상태 추출
        last_s3_path = None
        if successful:
            last_result = successful[-1]
            last_s3_path = last_result.get('output_s3_path') or last_result.get('final_state_s3_path')
        
        return {
            'state_s3_path': last_s3_path,
            'distributed_chunk_summary': {
                'total': len(result),
                'succeeded': len(successful),
                'failed': len(failed),
                'chunk_results': sorted_results[:10]  # 256KB 방지
            },
            '_failed_segments': failed,  # 내부 처리용
            '_aggregation_complete': True,
            # 🌿 [Pointer Strategy] Manifest extraction
            'segment_manifest_s3_path': successful[-1].get('segment_manifest_s3_path') if successful else None
        }
    
    # ============================================
    # 단일 객체 (딕셔너리 입력)
    # ============================================
    if isinstance(result, dict):
        delta = {}
        
        # 래퍼 패턴 제거 및 액션별 추출
        if action == 'merge_callback':
            payload = result.get('Payload', result.get('callback_result', result))
            if payload.get('user_response'):
                delta['last_hitp_response'] = payload['user_response']
            if payload.get('new_state_s3_path'):
                delta['state_s3_path'] = payload['new_state_s3_path']
            if payload.get('new_history_logs'):
                delta['new_history_logs'] = payload['new_history_logs']
            # [v3.29 HITP Fix] The HITP pause already advanced segment_to_run to the NEXT
            # segment (forward pointer). Do NOT increment again via _increment_segment —
            # that would push segment_to_run past the next segment and cause premature COMPLETE.
            # Instead, set _hitp_resume so _compute_next_action uses the forward pointer directly.
            delta['_hitp_resume'] = True
                
        elif action == 'merge_async':
            payload = result.get('async_result', result)
            if payload.get('final_state_s3_path'):
                delta['state_s3_path'] = payload['final_state_s3_path']
            if payload.get('new_history_logs'):
                delta['new_history_logs'] = payload['new_history_logs']
            delta['_increment_segment'] = payload.get('segment_to_run') is None
            
        elif action == 'sync':
            payload = result.get('execution_result', result)
            if payload.get('final_state_s3_path'):
                delta['state_s3_path'] = payload['final_state_s3_path']
            if payload.get('next_segment_to_run') is not None:
                delta['segment_to_run'] = payload['next_segment_to_run']
            if payload.get('new_history_logs'):
                delta['new_history_logs'] = payload['new_history_logs']
            if payload.get('branches'):
                delta['pending_branches'] = payload['branches']
            # 🌿 [Pointer Strategy] branches_s3_path도 State Bag에 저장
            if payload.get('branches_s3_path'):
                delta['branches_s3_path'] = payload['branches_s3_path']
            # 🌿 [Pointer Strategy] Manifest extraction
            if payload.get('segment_manifest_s3_path'):
                 delta['segment_manifest_s3_path'] = payload['segment_manifest_s3_path']
            if payload.get('inner_partition_map'):
                delta['partition_map'] = payload['inner_partition_map']
                delta['segment_to_run'] = 0  # restart from inner partition segment 0
            delta['_status'] = payload.get('status', 'CONTINUE')
            
            # �️ [v3.16 Fix] CONTINUE 상태일 때 next_segment_to_run 필수 검증
            if delta['_status'] == 'CONTINUE' and payload.get('next_segment_to_run') is None:
                _get_logger().error(
                    f"[flatten_result] CRITICAL: status=CONTINUE but next_segment_to_run is None! "
                    f"This will cause infinite loop. Payload keys: {list(payload.keys())[:20]}"
                )
                # 강제로 segment_to_run 유지 (무한 루프 방지)
                if 'segment_to_run' not in delta or delta['segment_to_run'] is None:
                    _get_logger().error(f"[flatten_result] EMERGENCY: Forcing status to FAILED to prevent infinite loop")
                    delta['_status'] = 'FAILED'
                    delta['_error'] = 'CONTINUE status without next_segment_to_run'
            
            # �🔍 [v3.15 Debug] Log status extraction for troubleshooting
            _get_logger().info(f"[flatten_result sync] Extracted status={delta['_status']} from payload, next_segment={payload.get('next_segment_to_run')}")
            
            # [v3.22] Flat-merge final_state directly into delta root
            # Replaces v3.20 burial (delta['current_state'] = final_state) which hid
            # all user keys one level deep and broke cross-segment state propagation.
            # Kernel structural keys (workflowId, execution_id, …) are skipped via
            # _BAG_STRUCTURAL_SKIP to prevent user data from overwriting kernel config.
            final_state = payload.get('final_state')
            # [v3.23 S3 Hydration] When segment_runner has offloaded final_state to S3
            # (e.g., has_next_segment=True + size>20KB), USC receives a pointer dict:
            #   {"__s3_offloaded": True, "__s3_path": "s3://bucket/key", ...}
            # Flat-merging the pointer alone would write __s3_offloaded/__s3_path to
            # the bag root and lose all user keys (TEST_RESULT etc).
            # Solution: detect the pointer and hydrate the full state from S3 before merging.
            if isinstance(final_state, dict) and final_state.get('__s3_offloaded'):
                s3_path = (
                    final_state.get('__s3_path')
                    or final_state.get('s3_path')
                    or payload.get('final_state_s3_path')
                    or payload.get('state_s3_path')
                )
                if s3_path:
                    _get_logger().info(
                        f"[v3.23] final_state is S3 pointer, hydrating from {s3_path}"
                    )
                    _hydration_succeeded = False
                    try:
                        hydrated = get_default_hydrator().load_from_s3(s3_path)
                        if isinstance(hydrated, dict):
                            final_state = hydrated
                            _hydration_succeeded = True
                            _get_logger().info(
                                f"[v3.23] Hydration OK — {len(final_state)} keys from S3"
                            )
                        else:
                            _get_logger().warning(
                                f"[v3.23] Hydrated value is not dict: {type(hydrated)}. "
                                "Falling back to pointer merge."
                            )
                    except Exception as _hydrate_err:
                        _get_logger().error(
                            f"[v3.23] S3 hydration failed ({s3_path}): {_hydrate_err}. "
                            "Falling back to pointer merge."
                        )
                    # [v3.28 DIAG] Log hydration result for diagnosing state loss
                    _diag_keys_usc = ['TEST_RESULT', 'vision_os_test_result', 'llm_raw_output',
                                      'approval_result', 'hitp_checkpoint']
                    _in_hydrated = {k: (k in final_state) for k in _diag_keys_usc} if isinstance(final_state, dict) else {}
                    _get_logger().error(
                        f"[v3.28 DIAG hydrate] s3_path={s3_path} "
                        f"hydrated={_hydration_succeeded} "
                        f"in_hydrated={_in_hydrated}"
                    )
            if isinstance(final_state, dict):
                # [v3.24] Root keys WIN over current_state burial (v3.20-era compat).
                # Step 1: seed delta with any buried current_state keys (lower priority)
                inner = final_state.get('current_state')
                if isinstance(inner, dict):
                    for k, v in inner.items():
                        if k not in _BAG_STRUCTURAL_SKIP:
                            delta[k] = v
                # Step 2: overlay root keys — these are the CURRENT segment's outputs
                # and must override any stale current_state data.
                merged_keys = []
                for k, v in final_state.items():
                    if k not in _BAG_STRUCTURAL_SKIP and k != 'current_state':
                        delta[k] = v
                        merged_keys.append(k)
                _get_logger().info(
                    f"[v3.24] flat-merged final_state keys to delta root "
                    f"({len(merged_keys)} keys, root-wins over current_state burial): "
                    f"{merged_keys[:10]}"
                )

        elif action == 'sync_branch':
            # [C-1/C-2 Fix] sync_branch를 sync와 동일한 1급 시민으로 격상
            # 브랜치 전용 next_segment_to_run → segment_to_run 명시적 매핑
            payload = result.get('execution_result', result)
            if payload.get('final_state_s3_path'):
                delta['state_s3_path'] = payload['final_state_s3_path']
            if payload.get('next_segment_to_run') is not None:
                delta['segment_to_run'] = payload['next_segment_to_run']
            if payload.get('new_history_logs'):
                delta['new_history_logs'] = payload['new_history_logs']
            delta['_status'] = payload.get('status', 'CONTINUE')
            # [v3.22] Flat-merge (sync_branch) — mirrors sync path above
            # [v3.23] S3 pointer hydration identical to action='sync'
            final_state = payload.get('final_state')
            if isinstance(final_state, dict) and final_state.get('__s3_offloaded'):
                s3_path = (
                    final_state.get('__s3_path')
                    or final_state.get('s3_path')
                    or payload.get('final_state_s3_path')
                    or payload.get('state_s3_path')
                )
                if s3_path:
                    try:
                        hydrated = get_default_hydrator().load_from_s3(s3_path)
                        if isinstance(hydrated, dict):
                            final_state = hydrated
                            _get_logger().info(
                                f"[v3.23 sync_branch] Hydrated from S3: {len(final_state)} keys"
                            )
                    except Exception as _hydrate_err:
                        _get_logger().error(
                            f"[v3.23 sync_branch] S3 hydration failed: {_hydrate_err}"
                        )
            if isinstance(final_state, dict):
                # [v3.24] Root keys WIN — mirrors sync path burial fix
                inner = final_state.get('current_state')
                if isinstance(inner, dict):
                    for k, v in inner.items():
                        if k not in _BAG_STRUCTURAL_SKIP:
                            delta[k] = v
                for k, v in final_state.items():
                    if k not in _BAG_STRUCTURAL_SKIP and k != 'current_state':
                        delta[k] = v
            _get_logger().info(
                f"[flatten_result sync_branch v3.24] status={delta['_status']}, "
                f"next_segment={payload.get('next_segment_to_run')}, "
                f"state_s3_path={'set' if delta.get('state_s3_path') else 'unset'}"
            )

        elif action == 'aggregate_branches':
            # 병렬 브랜치 결과 (포인터 배열)
            pointers = result.get('parallel_results', result.get('branch_pointers', []))
            if isinstance(pointers, list):
                list_delta = flatten_result(pointers, context)  # 리스트 처리로 위임
                # [Soft-fail] _soft_fail_branches를 리스트 결과에 병합
                # state_data_manager.aggregate_branches()가 채워서 넘긴 값 보존
                soft_fail = result.get('_soft_fail_branches')
                if soft_fail:
                    existing = list_delta.get('_failed_segments', [])
                    # _soft_fail_branches가 _failed_segments와 중복될 수 있으므로
                    # branch_id 기준 deduplicate (soft_fail 정보가 더 풍부함)
                    existing_ids = {s.get('branch_id') for s in existing if isinstance(s, dict)}
                    extra = [s for s in soft_fail if s.get('branch_id') not in existing_ids]
                    list_delta['_failed_segments'] = existing + extra
                    _get_logger().warning(
                        f"[flatten_result aggregate_branches] "
                        f"{len(soft_fail)} soft-fail branch(es) added to _failed_segments: "
                        f"{[s['branch_id'] for s in soft_fail]}"
                    )
                # new_history_logs도 전달 (S3에서 로드한 partial 로그)
                if result.get('new_history_logs'):
                    list_delta.setdefault('new_history_logs', [])
                    list_delta['new_history_logs'] = (
                        list_delta['new_history_logs'] + result['new_history_logs']
                    )
                return list_delta
            delta = result
            
        elif action == 'create_snapshot':
            # 스냅샷: state_s3_path 존재 여부만 확인
            delta['_is_pointer_mode'] = bool(result.get('state_s3_path'))
        
        elif action == 'init':
            # 탄생 (Day-Zero Sync): 파티셔닝 결과 + 초기 상태를 그대로 전달
            # required metadata는 merge_logic에서 강제 주입됨
            
            # 🔑 [Critical] Extract bag contents and merge into delta
            # InitializeStateData passes {'bag': payload}, we need to extract payload
            # 🛡️ [Guard] bag이 None이거나 없는 경우 result 자체를 사용
            bag_contents = result.get('bag') if isinstance(result, dict) else None
            if bag_contents is None:
                # bag 키가 없거나 값이 None인 경우 result 자체 사용
                bag_contents = result if isinstance(result, dict) else {}
            
            if isinstance(bag_contents, dict):
                delta.update(bag_contents)
            else:
                _get_logger().warning(f"[Init] bag_contents is not dict: {type(bag_contents)}")
            
            delta['_is_init'] = True
            delta['_status'] = 'STARTED'
            # 🌿 [Pointer Strategy] Manifest extraction for Init
            if isinstance(result, dict) and result.get('segment_manifest_s3_path'):
                 delta['segment_manifest_s3_path'] = result['segment_manifest_s3_path']
            
        else:
            # 기본: 래퍼 제거
            if 'callback_result' in result and len(result) <= 2:
                delta = result['callback_result']
            elif 'async_result' in result and len(result) <= 2:
                delta = result['async_result']
            elif 'execution_result' in result and len(result) <= 2:
                delta = result['execution_result']
            else:
                delta = result
            
            # 🛡️ [v3.21 Fix] else 브랜치에서 _status가 없으면 status 값을 승격
            # action='error' 등 비표준 액션이 여기 떨어질 때 _status 미설정 시
            # _compute_next_action이 CONTINUE를 기본값으로 사용해 무한루프 유발
            # 해결: delta에 _status가 없으면 delta.status → 'FAILED' 순으로 폴백
            if isinstance(delta, dict) and '_status' not in delta:
                fallback_status = delta.get('status')
                if isinstance(fallback_status, str) and fallback_status.upper() in (
                    'FAILED', 'COMPLETE', 'SUCCESS', 'SUCCEEDED', 'PAUSED_FOR_HITP', 'CONTINUE'
                ):
                    delta['_status'] = fallback_status.upper()
                elif action == 'error':
                    # action='error'는 항상 FAILED여야 함 — 루프 방지
                    delta['_status'] = 'FAILED'
                    _get_logger().warning(
                        f"[flatten_result] action='error' had no _status. "
                        f"Forcing _status=FAILED to prevent infinite loop. "
                        f"delta keys: {list(delta.keys())[:10]}"
                    )
        
        return delta
    
    # 기타 타입 (문자열, 숫자 등)
    return {'raw_result': result}


def _shallow_copy_with_cow(base_state: Dict[str, Any], fields_to_modify: set) -> Dict[str, Any]:
    """
    ① Copy-on-Write 방식의 얕은 복사
    
    전체 deepcopy 대신 변경될 필드만 복사합니다.
    14만 줄 커널 상태의 CPU/GC 부하를 방지합니다.
    """
    # 기본은 얕은 복사 (참조 유지)
    result = base_state.copy()
    
    # 변경될 필드만 깊은 복사
    for field in fields_to_modify:
        if field in result:
            value = result[field]
            if isinstance(value, dict):
                result[field] = value.copy()
            elif isinstance(value, list):
                result[field] = value.copy()
    
    return result


def _get_log_key(log: Dict) -> str:
    """히스토리 로그의 고유 키 생성 (중복 제거용)"""
    if not isinstance(log, dict):
        return str(hash(str(log)))
    
    node_id = log.get('node_id', log.get('id', ''))
    timestamp = log.get('timestamp', log.get('created_at', ''))
    
    if node_id and timestamp:
        return f"{node_id}:{timestamp}"
    elif node_id:
        return f"node:{node_id}"
    elif timestamp:
        return f"ts:{timestamp}"
    else:
        return hashlib.md5(json.dumps(log, sort_keys=True, default=str).encode()).hexdigest()


def _merge_list_field(
    base_list: List,
    delta_list: List,
    strategy: str
) -> List:
    """
    ② 리스트 필드 병합 (원자성 보장)
    
    전략:
        - 'append': 단순 추가
        - 'replace': 교체
        - 'dedupe_append': 중복 제거 후 추가
        - 'set_union': 집합 합집합
    """
    if strategy == 'replace':
        return delta_list.copy()
    
    if strategy == 'append':
        return base_list + delta_list
    
    if strategy == 'dedupe_append':
        # 로그 중복 제거 (node_id + timestamp 기반)
        seen_keys = {_get_log_key(item) for item in base_list}
        unique_delta = [
            item for item in delta_list 
            if _get_log_key(item) not in seen_keys
        ]
        return base_list + unique_delta
    
    if strategy == 'set_union':
        # 문자열/숫자 집합
        result_set = set(base_list) | set(delta_list)
        return list(result_set)
    
    # 기본: append
    return base_list + delta_list


def _deep_merge_dicts(base: Dict[str, Any], delta: Dict[str, Any]) -> Dict[str, Any]:
    """
    🔀 재귀적 딕셔너리 딥 머지

    [M-1 Fix] dict.update()는 2단계 아래 키를 삭제합니다.
    current_state처럼 중첩 구조가 깊은 필드는 서브키를 보존해야 합니다:
        - 두 값이 모두 dict이면 → 재귀 딥 머지 (서브키 보존)
        - 그 외 → delta 값이 base 값을 대체
    """
    merged = base.copy()
    for k, v in delta.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_dicts(merged[k], v)
        else:
            merged[k] = v
    return merged


# 필수 메타데이터 기본값 (action='init' 전용)
INIT_REQUIRED_METADATA = {
    'segment_to_run': 0,
    'loop_counter': 0,
    'state_history': [],
    'max_loop_iterations': 100,
    'max_branch_iterations': 100,
    'distributed_mode': False,
    'distributed_strategy': 'SAFE',
    'max_concurrency': 1,
}


def merge_logic(
    base_state: Dict[str, Any],
    delta: Dict[str, Any],
    context: Optional[SyncContext] = None
) -> Dict[str, Any]:
    """
    🔀 상태 병합 (Shallow Merge + Copy-on-Write)
    
    규칙:
        1. 제어 필드는 delta 우선
        2. 히스토리는 dedupe_append (중복 제거 후 추가)
        3. 딕셔너리 필드는 shallow merge
        4. 리스트 필드는 context.merge_strategy에 따름
    
    성능:
        - deepcopy 대신 Copy-on-Write 사용
        - 변경되는 서브트리만 복사
    
    Special:
        - action='init': 빈 base_state에 필수 메타데이터 강제 주입
    
    🛡️ [v3.4] NEVER returns None - always returns dict
    """
    logger = _get_logger()
    
    # 🛡️ [v3.4 Deep Guard] None 방지 - Immutable Empty Dict
    if base_state is None:
        logger.warning("🚨 [Deep Guard] merge_logic received None base_state!")
        base_state = {}
    
    if delta is None:
        logger.debug("[Deep Guard] merge_logic received None delta, returning base_state")
        return base_state if base_state else {}
    
    if context is None:
        context = {'action': 'sync'}
    
    action = context.get('action', 'sync') if context else 'sync'
    
    # 탄생 (init): 필수 메타데이터 강제 주입
    if action == 'init':
        # 기본값 먼저 적용, 그 위에 delta 덩어쓰기
        base_with_defaults = INIT_REQUIRED_METADATA.copy()
        base_with_defaults.update(base_state)
        base_state = base_with_defaults
        logger.info(f"[Init] Injected required metadata: {list(INIT_REQUIRED_METADATA.keys())}")
    
    if not delta:
        return base_state
    
    # 변경될 필드 식별 (CoW용)
    fields_to_modify = set(delta.keys())
    if 'state_history' in delta or 'new_history_logs' in delta:
        fields_to_modify.add('state_history')
    
    # ① Copy-on-Write 방식 복사
    updated_state = _shallow_copy_with_cow(base_state, fields_to_modify)
    
    # merge_strategy 추출
    merge_strategy = (context.get('merge_strategy', {}) if context else {})
    
    for key, value in delta.items():
        # 제어 필드: 무조건 delta 우선
        if key in CONTROL_FIELDS_NEVER_OFFLOAD:
            updated_state[key] = value
            continue
        
        # new_history_logs → state_history로 병합
        if key == 'new_history_logs':
            existing = updated_state.get('state_history', [])
            strategy = LIST_FIELD_STRATEGIES.get('state_history', 'dedupe_append')
            updated_state['state_history'] = _merge_list_field(existing, value, strategy)
            continue
        
        # 기존 값 확인
        base_value = updated_state.get(key)
        
        # 리스트 필드
        if isinstance(value, list):
            if isinstance(base_value, list):
                strategy = LIST_FIELD_STRATEGIES.get(key, 'append')
                updated_state[key] = _merge_list_field(base_value, value, strategy)
            else:
                updated_state[key] = value.copy()
        
        # 딕셔너리 필드: current_state는 딥 머지, 나머지는 Shallow Merge
        elif isinstance(value, dict):
            if isinstance(base_value, dict):
                if key == 'current_state':
                    # [M-1 Fix] current_state 딥 머지로 서브키 보존
                    updated_state[key] = _deep_merge_dicts(base_value, value)
                else:
                    # Shallow merge: delta 키가 base 키를 덮어씀
                    merged = base_value.copy()
                    merged.update(value)
                    updated_state[key] = merged
            else:
                updated_state[key] = value.copy() if isinstance(value, dict) else value
        
        # 기타 타입 (문자열, 숫자 등)
        else:
            updated_state[key] = value
    
    return updated_state


def prevent_pointer_bloat(
    state: Dict[str, Any],
    idempotency_key: str
) -> Dict[str, Any]:
    """
    🔒 포인터 비대화 방지
    
    scheduling_metadata, failed_segments 등 포인터가 커질 수 있는
    필드를 간소화합니다.
    """
    logger = _get_logger()
    
    # failed_segments 오프로딩
    if 'failed_segments' in state:
        failed = state['failed_segments']
        if isinstance(failed, list) and len(failed) > 5:
            from .state_data_manager import store_to_s3, generate_s3_key
            try:
                s3_key = generate_s3_key(idempotency_key, 'failed_segments')
                s3_path = store_to_s3(failed, s3_key)
                state['failed_segments_s3_path'] = s3_path
                state['failed_segments'] = failed[:5]  # 샘플만
                logger.info(f"Offloaded {len(failed)} failed_segments to S3")
            except Exception as e:
                logger.warning(f"Failed to offload failed_segments: {e}")
    
    # current_state 내 scheduling_metadata 간소화
    if isinstance(state.get('current_state'), dict):
        current = state['current_state']
        if isinstance(current.get('scheduling_metadata'), dict):
            metadata = current['scheduling_metadata']
            batch_details = metadata.get('batch_details', [])
            if len(batch_details) > 5:
                current['scheduling_summary'] = {
                    'total_batches': len(batch_details),
                    'priority': metadata.get('priority', 1),
                    'total_items': sum(b.get('size', 0) for b in batch_details if isinstance(b, dict))
                }
                del current['scheduling_metadata']
                logger.info("Simplified scheduling_metadata to scheduling_summary")
    
    return state


def emergency_offload_large_arrays(
    state: Dict[str, Any],
    idempotency_key: str
) -> Dict[str, Any]:
    """
    🚨 응급 대용량 배열 오프로딩
    
    페이로드가 200KB의 75%를 초과할 때 호출됩니다.
    """
    logger = _get_logger()
    
    from .state_data_manager import store_to_s3, generate_s3_key
    
    # distributed_outputs 오프로딩
    if 'distributed_outputs' in state:
        outputs = state['distributed_outputs']
        if isinstance(outputs, list) and len(outputs) > 10:
            try:
                s3_key = generate_s3_key(idempotency_key, 'distributed_outputs')
                s3_path = store_to_s3(outputs, s3_key)
                state['distributed_outputs_s3_path'] = s3_path
                state['distributed_outputs'] = outputs[:10]  # 샘플 10개만
                logger.warning(f"Emergency offload: distributed_outputs ({len(outputs)} items)")
            except Exception as e:
                logger.error(f"Emergency offload failed: {e}")
    
    return state


def optimize_and_offload(
    state: Dict[str, Any],
    context: Optional[SyncContext] = None
) -> Dict[str, Any]:
    """
    🚀 통합 최적화 파이프라인 - P0~P2 자동 해결
    
    처리 순서:
        1. 히스토리 아카이빙 (>50 entries)
        2. 개별 필드 오프로딩 (>30KB)
        3. 전체 상태 오프로딩 (>100KB)
        4. 포인터 비대화 방지
        5. 최종 크기 체크 (>200KB 경고)
    
    🛡️ [v3.4] NEVER returns None - always returns dict
    """
    logger = _get_logger()
    
    # 🛡️ [v3.4 Deep Guard] None 방지
    if state is None:
        logger.warning("🚨 [Deep Guard] optimize_and_offload received None state!")
        state = {}
    
    if context is None:
        context = {'action': 'sync'}
    
    # state_data_manager의 기존 함수들 재사용
    from .state_data_manager import (
        optimize_state_history,
        optimize_current_state,
        calculate_payload_size as calc_size
    )
    
    idempotency_key = (
        context.get('idempotency_key') if context 
        else state.get('idempotency_key', 'unknown')
    )
    
    # 1. 히스토리 최적화
    if state.get('state_history'):
        optimized_history, _ = optimize_state_history(
            state['state_history'],
            idempotency_key=idempotency_key,
            max_entries=50
        )
        state['state_history'] = optimized_history
    
    # 2. current_state 최적화 (개별 필드 + 전체 상태)
    if state.get('current_state'):
        optimized_current, _ = optimize_current_state(
            state['current_state'],
            idempotency_key
        )
        state['current_state'] = optimized_current
    else:
        # [v3.22 SFN-size fix] Fix 1 flat-merge mode: all workflow output keys are at bag
        # root (no current_state sub-dict).  optimize_current_state would be a no-op above,
        # so apply the same field-level S3 offloading at root level — but only for
        # non-critical user fields (skip CONTROL_FIELDS_NEVER_OFFLOAD and _BAG_STRUCTURAL_SKIP).
        offload_candidates = {
            k: v for k, v in state.items()
            if k not in CONTROL_FIELDS_NEVER_OFFLOAD and k not in _BAG_STRUCTURAL_SKIP
        }
        if offload_candidates:
            optimized_root, any_offloaded = optimize_current_state(
                offload_candidates, idempotency_key
            )
            if any_offloaded:
                if optimized_root.get('__s3_offloaded') is True:
                    # [Fix] Strategy-2 full-offload: S3 contains only offload_candidates.
                    # Evict original candidate keys from bag so the bag carries only the
                    # wrapper pointer — otherwise the bag stays bloated (data + pointer)
                    # and the next segment's S3 recovery would discard structural keys.
                    _diag_watch = ['TEST_RESULT', 'hitp_checkpoint', 'vision_os_test_result',
                                   'llm_raw_output', 'approval_result']
                    _watched_evicted = [k for k in _diag_watch if k in offload_candidates]
                    logger.error(
                        "[v3.28 Strategy-2] Full-offload triggered: evicting %d candidate keys. "
                        "S3 path=%s. Watched keys evicted=%s. Total candidate keys=%s",
                        len(offload_candidates),
                        optimized_root.get('__s3_path', 'N/A'),
                        _watched_evicted,
                        list(offload_candidates.keys())[:20],
                    )
                    for k in list(offload_candidates.keys()):
                        state.pop(k, None)
                    logger.info(
                        "[v3.22] Strategy-2 full offload: evicted %d candidate keys from bag root",
                        len(offload_candidates)
                    )
                state.update(optimized_root)
                logger.info("[v3.22] Root-level field offload applied (Fix 1 flat-merge mode)")

    # 3. 포인터 비대화 방지
    state = prevent_pointer_bloat(state, idempotency_key)
    
    # 4. 최종 크기 체크
    final_size_kb = calc_size(state)
    warning_threshold = MAX_PAYLOAD_SIZE_KB * 0.75  # 150KB
    
    if final_size_kb > warning_threshold:
        logger.warning(f"Payload approaching limit: {final_size_kb}KB / {MAX_PAYLOAD_SIZE_KB}KB")
        state = emergency_offload_large_arrays(state, idempotency_key)
    
    # 메타데이터 업데이트
    state['payload_size_kb'] = calc_size(state)
    state['last_update_time'] = datetime.now(timezone.utc).isoformat()
    
    return state


def universal_sync_core(
    base_state: Dict[str, Any],
    new_result: Any,
    context: Optional[SyncContext] = None
) -> Dict[str, Any]:
    """
    🎯 Function-Agnostic 동기화 코어 (v3.2 Engine)
    
    모든 StateDataManager 액션이 이 함수를 통과합니다.
    9개의 액션 함수는 이제 "3줄짜리 래퍼"입니다.
    
    파이프라인:
        1. flatten_result() - 입력 정규화 (액션별 스마트 추출)
        2. merge_logic() - 상태 병합 (Shallow Merge + CoW)
        3. optimize_and_offload() - 자동 최적화 (P0~P2 해결)
        4. _compute_next_action() - next_action 결정
    
    Args:
        base_state: 기존 state_data
        new_result: 새로운 실행 결과 (단일 객체 or 리스트)
        context: 동기화 컨텍스트 (action, execution_id, merge_strategy 등)
    
    Returns:
        {
            'state_data': 최적화된 상태,
            'next_action': 'CONTINUE' | 'COMPLETE' | 'FAILED' | ...
        }
    """
    logger = _get_logger()
    
    # 🛡️ [v3.4 Deep Guard] None 방지 - Immutable Empty Dict 전략
    # 절대로 None이 파이프라인을 통과하지 못하게 함
    if base_state is None:
        logger.warning("🚨 [Deep Guard] base_state is None! Using empty dict.")
        base_state = {}
    
    if new_result is None:
        logger.warning("🚨 [Deep Guard] new_result is None! Using empty dict.")
        new_result = {}
    
    if context is None:
        context = {'action': 'sync'}
    
    action = context.get('action', 'sync') if context else 'sync'
    idempotency_key = base_state.get('idempotency_key', 'unknown') if isinstance(base_state, dict) else 'unknown'
    
    # 컨텍스트에 idempotency_key 추가
    if context:
        context['idempotency_key'] = idempotency_key
    else:
        context = {'action': action, 'idempotency_key': idempotency_key}
    
    logger.info(f"UniversalSyncCore v3.2: action={action}")
    
    # Step 1: 입력 정규화 (액션별 스마트 추출)
    normalized_delta = flatten_result(new_result, context)
    
    # Step 2: 상태 병합 (Shallow Merge + CoW)
    updated_state = merge_logic(base_state, normalized_delta, context)
    
    # 🔍 [Debug] Log loop_counter after merge for troubleshooting
    logger.info(f"[v3.14 Debug] After merge_logic: loop_counter={updated_state.get('loop_counter')}, "
               f"base_state.loop_counter={base_state.get('loop_counter') if isinstance(base_state, dict) else 'N/A'}")
    
    # Step 3: 공통 필드 업데이트 (루프 카운터, 세그먼트)
    # 🛡️ [v3.14 Fix] loop_counter 증가는 ASL IncrementLoopCounter에서만 수행
    # USC에서 중복 증가하면 무한 루프 방지 로직이 깨짐
    # should_increment_loop 로직 제거 - ASL이 loop_counter 증가 담당
    # 
    # REMOVED:
    # should_increment_loop = (action == 'sync' or normalized_delta.get('_increment_loop', False))
    # if should_increment_loop and action != 'init':
    #     updated_state['loop_counter'] = int(updated_state.get('loop_counter', 0)) + 1
    
    # 세그먼트 증가 (플래그가 있는 경우)
    if normalized_delta.get('_increment_segment', False):
        updated_state['segment_to_run'] = int(updated_state.get('segment_to_run', 0)) + 1
    
    # Step 4: 자동 최적화 (P0~P2 해결)
    optimized_state = optimize_and_offload(updated_state, context)
    
    # Step 5: next_action 결정
    next_action = _compute_next_action(optimized_state, normalized_delta, action)
    
    # pending_branches 정리 (aggregate_branches 완료 시)
    if action == 'aggregate_branches' and normalized_delta.get('_aggregation_complete'):
        optimized_state.pop('pending_branches', None)
        optimized_state['segment_to_run'] = int(optimized_state.get('segment_to_run', 0)) + 1

    # [H-1 Delayed Deletion] 파이프라인 내부 제어 신호를 Persist 직전에 제거
    # _compute_next_action이 normalized_delta에서 읽으므로 state에서 제거해도 안전
    # _failed_segments/_error는 추적 목적으로 보존
    _PIPELINE_INTERNAL_KEYS = frozenset({
        '_status', '_is_init', '_increment_segment', '_increment_loop',
        '_aggregation_complete', '_is_pointer_mode', '_soft_fail_branches',
        '_hitp_resume',
    })
    for _k in _PIPELINE_INTERNAL_KEYS:
        optimized_state.pop(_k, None)

    logger.info(f"UniversalSyncCore complete: action={action}, next={next_action}, size={optimized_state.get('payload_size_kb', 0)}KB")

    return {
        'state_data': optimized_state,
        'next_action': next_action
    }


def _compute_next_action(
    state: Dict[str, Any],
    delta: Dict[str, Any],
    action: str
) -> str:
    """
    🎯 next_action 결정 로직 (중앙화)
    
    모든 액션의 next_action을 단일 로직으로 결정합니다.
    
    탄생 (init): 'STARTED' 반환
    
    🛡️ [v3.3] 타입 안전성 강화 - TypeError 방지
    """
    # 탄생 (init) - 시작 상태
    if action == 'init' or delta.get('_is_init'):
        return 'STARTED'
    
    # delta에서 상태 추출 (문자열 정규화)
    raw_status = delta.get('_status', 'CONTINUE')
    status = str(raw_status).upper() if raw_status is not None else 'CONTINUE'
    
    logger = _get_logger()
    logger.info(f"[_compute_next_action] action={action}, raw_status={raw_status}, normalized_status={status}")

    # [C-3 Fix] 브랜치 전용 탈출 조건: _status만으로 결정
    # main workflow의 total_segments/segment_to_run 오염 방지
    if action == 'sync_branch':
        if status in ('COMPLETE', 'SUCCESS', 'SUCCEEDED'):
            logger.info(f"[_compute_next_action sync_branch] Branch completed: status={status}")
            return 'COMPLETE'
        if status in ('FAILED', 'HALTED', 'SIGKILL', 'LOOP_LIMIT_EXCEEDED', 'PARTIAL_FAILURE'):
            logger.warning(f"[_compute_next_action sync_branch] Branch failed: status={status}")
            return 'FAILED'
        if status in ('PAUSED_FOR_HITP', 'PAUSE'):
            logger.info(f"[_compute_next_action sync_branch] Branch paused for HITP")
            return 'PAUSED_FOR_HITP'
        # CONTINUE 또는 기타 → 브랜치 루프 계속
        logger.info(f"[_compute_next_action sync_branch] Branch continues: status={status}")
        return 'CONTINUE'

    # 명시적 실패/중단 상태 (🛡️ [v3.16] HALTED/SIGKILL → FAILED 정규화)
    if status in ('FAILED', 'HALTED', 'SIGKILL'):
        # ASL에는 HALTED/SIGKILL case가 없으므로 FAILED로 통일
        if status in ('HALTED', 'SIGKILL'):
            logger.warning(f"[_compute_next_action] Normalizing {status} to FAILED for ASL compatibility")
            return 'FAILED'
        logger.info(f"[_compute_next_action] Returning failure status: {status}")
        return status
    
    # 명시적 완료 (SUCCESS, SUCCEEDED도 COMPLETE로 처리)
    if status in ('COMPLETE', 'SUCCESS', 'SUCCEEDED'):
        # 🛡️ [v3.16 Fix] next_segment가 있으면 COMPLETE 무시 (조기 종료 방지)
        if delta.get('segment_to_run') is not None:
            next_seg = delta.get('segment_to_run')
            logger.warning(
                f"[_compute_next_action] Status is {status} but next_segment={next_seg} exists. "
                f"This may indicate incorrect status. Treating as CONTINUE."
            )
            return 'CONTINUE'
        
        logger.info(f"[_compute_next_action] Workflow completed with status={status}, returning COMPLETE")
        return 'COMPLETE'
    
    # HITP 대기
    if status in ('PAUSED_FOR_HITP', 'PAUSE'):
        return 'PAUSED_FOR_HITP'

    # [v3.29 HITP Resume] merge_callback sets _hitp_resume=True to signal that
    # segment_to_run in state is a FORWARD pointer (set by HITP pause to next segment).
    # Use >= total_segments (not total_segments - 1) since the value is already incremented.
    if delta.get('_hitp_resume', False):
        try:
            current_segment = int(state.get('segment_to_run', 0) or 0)
            total_segments_raw = state.get('total_segments')
            if total_segments_raw is not None:
                total_segments = int(total_segments_raw)
                if current_segment >= total_segments:
                    logger.info(
                        f"[_compute_next_action HITP resume] All segments done: "
                        f"segment_to_run={current_segment} >= total={total_segments}, returning COMPLETE"
                    )
                    return 'COMPLETE'
                else:
                    logger.info(
                        f"[_compute_next_action HITP resume] Next segment {current_segment} pending "
                        f"(total={total_segments}), returning CONTINUE"
                    )
                    return 'CONTINUE'
        except (ValueError, TypeError) as e:
            logger.warning(f"[_compute_next_action HITP resume] Invalid segment numbers: {e}. Defaulting to CONTINUE.")
        return 'CONTINUE'

    # Distributed 전체 실패
    if delta.get('_aggregation_complete'):
        failed = delta.get('_failed_segments', [])
        chunk_summary = delta.get('distributed_chunk_summary')
        total = chunk_summary.get('total', 0) if isinstance(chunk_summary, dict) else 0
        if failed and len(failed) == total:
            return 'FAILED'
    
    # 다음 세그먼트 없으면 완료
    if delta.get('segment_to_run') is None and status == 'CONTINUE':
        # 🛡️ [Guard] 안전한 숫자 비교 - TypeError 방지
        try:
            current_segment = int(state.get('segment_to_run', 0) or 0)
            total_segments_raw = state.get('total_segments')
            
            if total_segments_raw is not None:
                total_segments = int(total_segments_raw)

                # [v3.25 Fix] _increment_segment=True 케이스:
                # USC Step 3에서 segment_to_run이 이미 +1 증가된 상태로 이 함수에 진입.
                # 따라서 state['segment_to_run']은 '마지막 실행 세그먼트'가 아닌
                # '다음에 실행할 세그먼트(forward pointer)'를 가리킴.
                #
                # 의미 변화:
                #   _increment_segment=False: segment_to_run = 마지막 실행 세그먼트
                #     → COMPLETE 조건: current_segment >= total_segments - 1
                #   _increment_segment=True:  segment_to_run = 다음 실행 세그먼트
                #     → COMPLETE 조건: current_segment >= total_segments
                #       (아직 실행 안 된 세그먼트가 마지막 세그먼트 위치에 있으면 CONTINUE)
                #
                # 예시 (total_segments=2):
                #   HITP 후 _increment_segment=True, segment_to_run=1 (Seg1 아직 미실행)
                #   old: 1 >= 2-1=1 → COMPLETE (버그! Seg1 건너뜀)
                #   new: 1 >= 2=2 → False → CONTINUE (올바름)
                if delta.get('_increment_segment', False):
                    if current_segment >= total_segments:
                        logger.info(
                            f"[_compute_next_action] All segments done after HITP/async increment: "
                            f"segment_to_run={current_segment} >= total={total_segments}, returning COMPLETE"
                        )
                        return 'COMPLETE'
                    else:
                        logger.info(
                            f"[_compute_next_action] Next segment {current_segment} pending after increment "
                            f"(total={total_segments}), returning CONTINUE"
                        )
                        return 'CONTINUE'

                # 일반 케이스: segment_to_run = 마지막으로 실행된 세그먼트 인덱스
                if current_segment >= total_segments - 1:
                    logger.info(f"[_compute_next_action] Last segment reached: {current_segment + 1}/{total_segments}, returning COMPLETE")
                    return 'COMPLETE'
        except (ValueError, TypeError) as e:
            logger.warning(
                f"[_compute_next_action] Invalid segment numbers: "
                f"segment_to_run={state.get('segment_to_run')}, "
                f"total_segments={state.get('total_segments')}. Error: {e}. Defaulting to CONTINUE."
            )
    
    # pending_branches가 있으면 병렬 처리
    pending = state.get('pending_branches') or delta.get('pending_branches')
    if pending:
        # 🛡️ [v3.16 Fix] 빈 배열 체크 (무한 루프 방지)
        if isinstance(pending, list) and len(pending) > 0:
            logger.info(f"[_compute_next_action] Pending branches detected ({len(pending)}), returning PARALLEL_GROUP")
            return 'PARALLEL_GROUP'
        else:
            logger.warning(f"[_compute_next_action] pending_branches is empty or invalid: {type(pending).__name__}, treating as CONTINUE")
    
    logger.info(f"[_compute_next_action] No special conditions matched, returning CONTINUE")
    return 'CONTINUE'


# ============================================
# Backward Compatibility - 기존 함수 래퍼
# ============================================

def load_from_s3_with_retry(
    s3_path: str,
    expected_checksum: Optional[str] = None
) -> Any:
    """
    기존 load_from_s3의 재시도 + 체크섬 검증 버전
    
    backward compatible 래퍼로, 기존 코드에서 drop-in replacement로 사용 가능
    """
    return get_default_hydrator().load_from_s3(s3_path, expected_checksum)
