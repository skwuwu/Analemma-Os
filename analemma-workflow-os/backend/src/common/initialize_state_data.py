"""
Initialize State Data - 워크플로우 상태 초기화

v3.3 - Unified Pipe 통합
    "탄생(Init)부터 소멸까지 단일 파이프"
    
    - Universal Sync Core(action='init')를 통한 표준화된 상태 생성
    - T=0 가드레일: Dirty Input 자동 방어 (256KB 초과 시 자동 오프로딩)
    - 필수 메타데이터 강제 주입 (Semantic Integrity)
    - 파티셔닝 로직은 비즈니스 로직에 집중, 저장은 USC에 위임
"""

import os
import json
import time
import logging
import boto3

# Import DecimalEncoder for JSON serialization
try:
    from src.common.json_utils import DecimalEncoder
except ImportError:
    DecimalEncoder = None

# v3.3: Universal Sync Core import (Unified Pipe)
try:
    from src.handlers.utils.universal_sync_core import universal_sync_core
    _HAS_USC = True
except ImportError:
    try:
        # Lambda 환경 대체 경로
        from handlers.utils.universal_sync_core import universal_sync_core
        _HAS_USC = True
    except ImportError:
        _HAS_USC = False
        universal_sync_core = None

# [Priority 1 Optimization] Pre-compilation: Load partition_map from DB
# Runtime partitioning used only as fallback
try:
    from src.services.workflow.partition_service import partition_workflow_advanced
    _HAS_PARTITION = True
except ImportError:
    try:
        from services.workflow.partition_service import partition_workflow_advanced
        _HAS_PARTITION = True
    except ImportError:
        _HAS_PARTITION = False
        partition_workflow_advanced = None

# DynamoDB client (warm start optimization)
try:
    from src.common.aws_clients import get_dynamodb_resource
    _dynamodb = get_dynamodb_resource()
except ImportError:
    _dynamodb = boto3.resource('dynamodb')

# 🚨 [Critical Fix] Match default values with template.yaml
WORKFLOWS_TABLE = os.environ.get('WORKFLOWS_TABLE', 'WorkflowsTableV3')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# [v3.11] Unified State Hydration Strategy
from src.common.state_hydrator import StateHydrator, SmartStateBag

# [Phase 1] Merkle DAG State Versioning
try:
    from src.services.state.state_versioning_service import StateVersioningService
    _HAS_VERSIONING = True
except ImportError as _versioning_import_err:
    _HAS_VERSIONING = False
    StateVersioningService = None
    logger.error(
        f"[initialize_state_data] StateVersioningService import FAILED: {_versioning_import_err}"
    )

# Startup diagnostics — all _HAS_* flags resolved, visible in CloudWatch cold-start logs
logger.info(
    f"[initialize_state_data] startup flags: "
    f"_HAS_USC={_HAS_USC}, _HAS_PARTITION={_HAS_PARTITION}, _HAS_VERSIONING={_HAS_VERSIONING}"
)

# Environment variables for Merkle DAG
MANIFESTS_TABLE = os.environ.get('MANIFESTS_TABLE', 'WorkflowManifests-v3-dev')
STATE_BUCKET = os.environ.get('WORKFLOW_STATE_BUCKET', 'analemma-workflow-state-dev')

def _calculate_distributed_strategy(
    total_segments: int,
    llm_segments: int,
    hitp_segments: int,
    partition_map: list
) -> dict:
    """
    🚀 하이브리드 분산 실행 전략 결정
    
    Returns:
        dict: {
            "strategy": "SAFE" | "BATCHED" | "MAP_REDUCE" | "RECURSIVE",
            "max_concurrency": int,
            "batch_size": int (for BATCHED mode),
            "reason": str
        }
    """
    # 🔍 워크플로우 특성 분석
    llm_ratio = llm_segments / max(total_segments, 1)
    hitp_ratio = hitp_segments / max(total_segments, 1)
    
    # 🔗 의존성 분석: 독립 실행 가능한 세그먼트 그룹 계산
    independent_segments = 0
    max_dependency_depth = 0
    
    for segment in partition_map:
        deps = segment.get("dependencies", [])
        if not deps:
            independent_segments += 1
        max_dependency_depth = max(max_dependency_depth, len(deps))
    
    independence_ratio = independent_segments / max(total_segments, 1)
    
    logger.info(f"[Strategy Analysis] segments={total_segments}, llm_ratio={llm_ratio:.2f}, "
                f"hitp_ratio={hitp_ratio:.2f}, independence_ratio={independence_ratio:.2f}, "
                f"max_dep_depth={max_dependency_depth}")
    
    # 📊 전략 결정 로직
    # 1. HITP가 포함된 경우: 반드시 SAFE 모드 (인간 승인 필요)
    if hitp_segments > 0:
        return {
            "strategy": "SAFE",
            "max_concurrency": 1,
            "batch_size": 1,
            "reason": f"HITP segments detected ({hitp_segments}), requires sequential human approval"
        }
    
    # 2. 소규모 워크플로우: SAFE 모드
    if total_segments <= 10:
        return {
            "strategy": "SAFE",
            "max_concurrency": min(total_segments, 5),
            "batch_size": 1,
            "reason": f"Small workflow ({total_segments} segments), SAFE mode sufficient"
        }
    
    # 3. 대규모 + 높은 독립성 + LLM 비율 높음: MAP_REDUCE 모드
    if total_segments > 100 and independence_ratio > 0.5 and llm_ratio > 0.3:
        return {
            "strategy": "MAP_REDUCE",
            "max_concurrency": 100,  # 높은 병렬성
            "batch_size": 25,
            "reason": f"High independence ({independence_ratio:.1%}), LLM-heavy ({llm_ratio:.1%}), optimal for Map-Reduce"
        }
    
    # 4. 중간 규모 또는 혼합 워크플로우: BATCHED 모드
    if total_segments > 10:
        # 배치 크기 동적 결정: LLM 비율 높으면 작은 배치
        if llm_ratio > 0.5:
            batch_size = 10
            max_concurrency = 10
        else:
            batch_size = 20
            max_concurrency = 20
            
        return {
            "strategy": "BATCHED",
            "max_concurrency": max_concurrency,
            "batch_size": batch_size,
            "reason": f"Medium workflow ({total_segments} segments), batched processing optimal"
        }
    
    # 5. 기본: SAFE 모드
    return {
        "strategy": "SAFE",
        "max_concurrency": 2,
        "batch_size": 1,
        "reason": "Default fallback to SAFE mode"
    }

def _calculate_dynamic_concurrency(
    total_segments: int,
    llm_segments: int,
    hitp_segments: int,
    partition_map: list,
    owner_id: str
) -> int:
    """
    Calculate dynamic concurrency based on workflow complexity and user tier
    
    🚨 [Critical] Specification clarification:
    The max_concurrency returned by this function determines **parallel processing capacity within chunks**.
    
    - Distributed Map's MaxConcurrency is fixed at 1 in ASL (ensures state continuity)
    - This value is used for parallel branch execution within each chunk
    - Applied to parallel group processing within ProcessSegmentChunk
    
    Args:
        total_segments: Total number of segments
        llm_segments: Number of LLM segments
        hitp_segments: Number of HITP segments
        partition_map: Partition map
        owner_id: User ID
        
    Returns:
        int: Calculated chunk-internal MaxConcurrency value (range 5-50)
             ※ Separate from Distributed Map's own MaxConcurrency
    """
    # Default value
    base_concurrency = 15
    
    # 1. Calculate number of parallel branches
    max_parallel_branches = 0
    if partition_map:
        for segment in partition_map:
            if segment.get('type') == 'parallel_group':
                branches = segment.get('branches', [])
                max_parallel_branches = max(max_parallel_branches, len(branches))
    
    # 2. Adjust based on workflow complexity
    calculated_concurrency = base_concurrency # [Safety] Initialize default
    
    if max_parallel_branches == 0:
        # Use default value if no parallel branches
        calculated_concurrency = base_concurrency
    elif max_parallel_branches <= 5:

        # Small-scale parallel (5 or fewer): Execute all concurrently
        calculated_concurrency = max_parallel_branches
    elif max_parallel_branches <= 10:
        # Medium-scale parallel (6-10): Execute 80% concurrently
        calculated_concurrency = int(max_parallel_branches * 0.8)
    elif max_parallel_branches <= 20:
        # Large-scale parallel (11-20): Execute 60% concurrently
        calculated_concurrency = int(max_parallel_branches * 0.6)
    else:
        # Ultra-large-scale parallel (21+): Limit to maximum 30
        calculated_concurrency = min(30, int(max_parallel_branches * 0.5))
    
    # 3. Adjust based on user tier (optional)
    try:
        user_table = _dynamodb.Table(os.environ.get('USERS_TABLE', 'UsersTableV3'))
        user_response = user_table.get_item(Key={'userId': owner_id})
        user_item = user_response.get('Item')
        
        if user_item:
            subscription_plan = user_item.get('subscription_plan', 'free')
            
            # Apply tier multipliers
            tier_multipliers = {
                'free': 0.5,      # 50% limit
                'basic': 0.75,    # 75%
                'pro': 1.0,       # 100%
                'enterprise': 1.5 # 150% (up to 50 max)
            }
            
            multiplier = tier_multipliers.get(subscription_plan, 1.0)
            calculated_concurrency = int(calculated_concurrency * multiplier)
            
            logger.info(f"User tier '{subscription_plan}' applied multiplier {multiplier}")
    except Exception as e:
        logger.warning(f"Failed to load user tier for concurrency calculation: {e}")
    
    # 4. 🛡️ [Concurrency Protection] OS-level upper limit clamping
    # Ensure overall system stability according to account concurrency limit
    # Increased from 2 to 5 to enable proper testing of parallel scheduling strategies
    MAX_OS_LIMIT = 5  # Allows testing of batch splitting and speed guardrails
    clamped_concurrency = min(calculated_concurrency, MAX_OS_LIMIT)
    
    if calculated_concurrency > MAX_OS_LIMIT:
        logger.warning(
            f"[Concurrency Protection] Clamping requested concurrency {calculated_concurrency} "
            f"to OS limit {MAX_OS_LIMIT}"
        )
    
    # 5. Final range limit (1 ~ MAX_OS_LIMIT)
    final_concurrency = max(1, clamped_concurrency)
    
    logger.info(
        f"Chunk-internal concurrency calculated: {final_concurrency} "
        f"(branches: {max_parallel_branches}, segments: {total_segments}, "
        f"llm: {llm_segments}, hitp: {hitp_segments}, OS_LIMIT: {MAX_OS_LIMIT}) "
        f"※ Distributed Map MaxConcurrency remains 1 for state continuity"
    )
    
    return final_concurrency


def _load_workflow_config(owner_id: str, workflow_id: str) -> dict:
    """
    Load workflow config from Workflows table.
    Retrieve full config including subgraphs.
    """
    if not owner_id or not workflow_id:
        return None
    
    try:
        table = _dynamodb.Table(WORKFLOWS_TABLE)
        response = table.get_item(
            Key={'ownerId': owner_id, 'workflowId': workflow_id},
            ProjectionExpression='config, partition_map, total_segments, llm_segments_count, hitp_segments_count'
        )
        item = response.get('Item')
        if item and item.get('config'):
            config = item.get('config')
            # Parse if JSON string
            if isinstance(config, str):
                import json
                config = json.loads(config)
            logger.info(f"Loaded workflow config from DB: {workflow_id}")
            return {
                'config': config,
                'partition_map': item.get('partition_map'),
                'total_segments': item.get('total_segments'),
                'llm_segments_count': item.get('llm_segments_count'),
                'hitp_segments_count': item.get('hitp_segments_count')
            }
        return None
    except Exception as e:
        logger.warning(f"Failed to load workflow config: {e}")
        return None


def _load_precompiled_partition(owner_id: str, workflow_id: str) -> dict:
    """
    Load pre-compiled partition_map from Workflows table.
    Retrieve partition_map calculated at save time.
    """
    if not owner_id or not workflow_id:
        return None
    
    try:
        table = _dynamodb.Table(WORKFLOWS_TABLE)
        response = table.get_item(
            Key={'ownerId': owner_id, 'workflowId': workflow_id},
            ProjectionExpression='partition_map, total_segments, llm_segments_count, hitp_segments_count'
        )
        item = response.get('Item')
        if item and item.get('partition_map'):
            logger.info(f"Loaded pre-compiled partition_map from DB: {item.get('total_segments', 0)} segments")
            return item
        return None
    except Exception as e:
        logger.warning(f"Failed to load pre-compiled partition_map: {e}")
        return None


def lambda_handler(event, context):
    """
    Initializes state data using StateHydrator for strict "SFN Only Sees Pointers" strategy.
    
    Processing Steps:
    1. Resolve inputs (workflow_config, partition_map).
    2. Calculate strategy (distributed vs sequential).
    3. Initialize SmartStateBag.
    4. Dehydrate state (Offload large data to S3).
    5. Return safe payload to Step Functions.
    """
    logger.info("Initializing state data with StateHydrator strategy")
    
    # 1. State Hydrator Initialization
    bucket = os.environ.get('WORKFLOW_STATE_BUCKET')
    # Validate bucket early
    if not bucket:
        raw_bucket = os.environ.get('S3_BUCKET') or os.environ.get('SKELETON_S3_BUCKET')
        bucket = raw_bucket.strip() if raw_bucket else None
    
    hydrator = StateHydrator(bucket_name=bucket)
    
    # 2. Input Resolution
    # SFN 입력이 {'input': {...}} 형태이면 하위 dict 사용.
    # 'initial_state' 키도 인식 (v3+ SFN 입력 스키마).
    # 둘 다 없으면 event 전체를 폴백 (ownerId/workflowId가 top-level에 있는 레거시 호출 호환).
    # [M-03 Note] event 전체 폴백 시 orchestrator_selection 등 SFN 내부 필드가 포함될 수 있음.
    #   해당 필드들은 force_offload={'input'} 경로를 통해 S3로 오프로드되어 SFN 페이로드에 잔류하지 않음.
    raw_input = event.get('input') or event.get('initial_state') or event
    if not isinstance(raw_input, dict):
        raw_input = {}
        
    # [FIX] When raw_input resolves to event['initial_state'], top-level fields
    # (workflowId, ownerId, test_workflow_config) are in event, not in raw_input.
    # Always fall back to event for fields that callers place at the top level.
    owner_id = raw_input.get('ownerId', "") or event.get('ownerId', "")
    workflow_id = raw_input.get('workflowId', "") or event.get('workflowId', "")
    current_time = int(time.time())

    # Generate execution_id for state tracking
    # Use idempotency_key if provided, otherwise generate unique ID
    execution_id = (raw_input.get('idempotency_key') or event.get('idempotency_key')
                    or raw_input.get('execution_id') or event.get('execution_id'))
    if not execution_id:
        import uuid
        execution_id = f"init-{workflow_id}-{int(time.time())}-{str(uuid.uuid4())[:8]}"

    # 2.1 Load Config & Partition Map
    # Priority: Input > DB (Precompiled) > Runtime Calc

    workflow_config = (raw_input.get('test_workflow_config') or raw_input.get('workflow_config')
                       or event.get('test_workflow_config') or event.get('workflow_config'))
    partition_map = raw_input.get('partition_map') or event.get('partition_map')
    
    # DB Loader Fallback
    if not workflow_config and workflow_id and owner_id:
        db_data = _load_workflow_config(owner_id, workflow_id)
        if db_data:
            workflow_config = db_data.get('config')
            # Only use DB partition map if not provided in input
            if not partition_map: 
                partition_map = db_data.get('partition_map')
    
    # Robustness: Ensure workflow_config is dict
    if not workflow_config:
        workflow_config = {}
        logger.warning("Proceeding with empty workflow_config")
    
    # Runtime Partitioning Fallback
    partition_result = None  # Initialize to prevent UnboundLocalError
    if not partition_map and _HAS_PARTITION:
        logger.info("Calculating partition_map at runtime...")
        try:
            partition_result = partition_workflow_advanced(workflow_config)
            partition_map = partition_result.get('partition_map', [])
        except Exception as e:
            logger.error(f"Partitioning failed: {e}")
            partition_map = []
            partition_result = {}  # Set empty dict on failure
            
    # Metadata Calculation
    total_segments = len(partition_map) if partition_map else 0
    llm_segments = sum(1 for seg in partition_map if seg.get('type') == 'llm') if partition_map else 0
    hitp_segments = sum(1 for seg in partition_map if seg.get('type') == 'hitp') if partition_map else 0
    
    # 3. Strategy Calculation
    try:
        distributed_strategy = _calculate_distributed_strategy(
            total_segments=total_segments,
            llm_segments=llm_segments,
            hitp_segments=hitp_segments,
            partition_map=partition_map or []
        )
    except Exception as e:
        logger.error(f"Strategy calculation failed: {e}")
        distributed_strategy = {
            "strategy": "SAFE",
            "max_concurrency": 1,
            "reason": "Strategy calculation failed"
        }
    
    # Concurrency Calculation
    max_concurrency = distributed_strategy.get('max_concurrency', 1)
    is_distributed_mode = distributed_strategy['strategy'] in ["MAP_REDUCE", "BATCHED"]
    
    # 4. [Phase 1] Merkle DAG Manifest Creation
    # 이전: workflow_config + partition_map 저장 (전체 StateBag의 67%)
    # 현재: Merkle manifest_id + hash만 저장 (참조 포인터)
    
    manifest_id = None
    manifest_hash = None
    config_hash = None
    
    if _HAS_VERSIONING:
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Merkle DAG 생성: 최초 1회 시도 + 실패 시 1회 재시도
        # 무결성 원칙: 재생성도 실패하면 워크플로우를 명시적으로 종료.
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        _manifest_last_error = None
        for _attempt in range(1, 3):  # 1회차, 2회차
            try:
                # [BUG-INIT-01 FIX] gc_dlq_url 및 use_2pc 누락 → create_manifest()가
                # "GC DLQ URL is required for 2-Phase Commit" RuntimeError를 항상 발생시킴.
                # segment_runner_service.py와 동일한 설정을 사용해야 함.
                versioning_service = StateVersioningService(
                    dynamodb_table=MANIFESTS_TABLE,
                    s3_bucket=STATE_BUCKET,
                    use_2pc=True,
                    gc_dlq_url=os.environ.get('GC_DLQ_URL')
                )

                # segment_manifest 생성 (먼저 계산)
                segment_manifest = []
                for idx, segment in enumerate(partition_map or []):
                    segment_manifest.append({
                        "segment_id": idx,
                        "segment_config": segment,
                        "execution_order": idx,
                        "dependencies": segment.get("dependencies", []),
                        "type": segment.get("type", "normal")
                    })

                # Merkle Manifest 생성
                manifest_pointer = versioning_service.create_manifest(
                    workflow_id=workflow_id,
                    workflow_config=workflow_config,
                    segment_manifest=segment_manifest,
                    parent_manifest_id=None  # 최초 버전
                )

                manifest_id = manifest_pointer.manifest_id
                manifest_hash = manifest_pointer.manifest_hash
                config_hash = manifest_pointer.config_hash

                logger.info(
                    f"[Merkle DAG] Created manifest {manifest_id[:8]}... "
                    f"(attempt={_attempt}/2, hash={manifest_hash[:8]}..., {total_segments} segments)"
                )

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # [Phase 8.1] Pre-flight Check: S3 Strong Consistency 검증
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # "데이터가 준비되지 않았다면 실행조차 하지 않는다"
                # - S3 Propagation Delay 방어
                # - Fail-fast: 700ms 내 검증 완료 (3회 재시도)
                # - Trust Chain의 첫 번째 검증 지점
                try:
                    from src.services.state.async_commit_service import get_async_commit_service

                    async_commit = get_async_commit_service()
                    manifest_s3_key = f"manifests/{manifest_id}.json"

                    logger.info(
                        f"[Pre-flight Check] Verifying manifest availability: "
                        f"{manifest_id[:8]}..."
                    )

                    commit_status = async_commit.verify_commit_with_retry(
                        execution_id=execution_id,
                        s3_bucket=STATE_BUCKET,
                        s3_key=manifest_s3_key,
                        redis_key=None  # Redis 없이 S3만 검증
                    )

                    if not commit_status.s3_available:
                        raise RuntimeError(
                            f"[System Fault] Manifest S3 availability verification failed "
                            f"after {commit_status.retry_count} attempts "
                            f"(total_wait={commit_status.total_wait_ms:.1f}ms). "
                            f"S3 Strong Consistency violation! "
                            f"Manifest: {manifest_id[:8]}..."
                        )

                    logger.info(
                        f"[Pre-flight Check] ✅ Manifest verified: {manifest_id[:8]}... "
                        f"(retries={commit_status.retry_count}, "
                        f"wait={commit_status.total_wait_ms:.1f}ms)"
                    )

                except ImportError:
                    logger.warning(
                        "[Pre-flight Check] AsyncCommitService not available, "
                        "skipping S3 verification (development mode)"
                    )
                except Exception as verify_error:
                    logger.error(
                        f"[Pre-flight Check] Manifest verification failed: {verify_error}",
                        exc_info=True
                    )
                    raise RuntimeError(
                        f"Pre-flight Check failed for manifest {manifest_id[:8]}...: "
                        f"{str(verify_error)}"
                    ) from verify_error

                break  # ✅ 생성 + 검증 모두 성공 → 루프 탈출

            except Exception as e:
                _manifest_last_error = e
                manifest_id = None
                if _attempt < 2:
                    logger.warning(
                        f"[Merkle DAG] Attempt {_attempt}/2 failed: {e}. "
                        f"Retrying manifest creation once more..."
                    )
                else:
                    logger.error(
                        f"[Merkle DAG] Attempt {_attempt}/2 failed: {e}",
                        exc_info=True
                    )

        if not manifest_id:
            raise RuntimeError(
                f"[System Fault] Merkle DAG manifest creation failed after 2 attempts. "
                f"Workflow state integrity cannot be guaranteed — aborting execution. "
                f"Last error: {_manifest_last_error}"
            ) from _manifest_last_error
    
    # State Bag Construction
    bag = SmartStateBag({}, hydrator=hydrator)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [Phase 2] Merkle DAG Content-Addressable Storage
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if not manifest_id:
        raise RuntimeError(
            f"Merkle DAG manifest is required for workflow state integrity. "
            f"Diagnostics: _HAS_VERSIONING={_HAS_VERSIONING}, "
            f"workflow_config={'present' if workflow_config else 'missing'}, "
            f"partition_map={'present({} segs)'.format(len(partition_map)) if partition_map else 'empty/None'}. "
            f"Check StateVersioningService import and dependencies."
        )

    # ✅ Merkle DAG Mode: Content-Addressable Storage
    # - workflow_config/partition_map → S3 블록으로 저장됨
    # - StateBag에는 manifest_id 포인터만 저장 (93% 크기 감소)
    # - segment_runner는 manifest에서 segment_config 로드
    bag['manifest_id'] = manifest_id
    bag['manifest_hash'] = manifest_hash
    bag['config_hash'] = config_hash

    logger.info(
        f"[Merkle DAG] State storage optimized: "
        f"manifest_id={manifest_id[:8]}..., "
        f"hash={manifest_hash[:8]}..., "
        f"StateBag reduction: ~93%"
    )
    
    bag['current_state'] = workflow_config.get('initial_state', {})
    
    # Metadata
    bag['ownerId'] = owner_id
    bag['workflowId'] = workflow_id
    bag['idempotency_key'] = (raw_input.get('idempotency_key', "")
                              or event.get('idempotency_key', ""))
    bag['quota_reservation_id'] = (raw_input.get('quota_reservation_id', "")
                                   or event.get('quota_reservation_id', ""))
    bag['segment_to_run'] = 0
    bag['total_segments'] = max(1, total_segments)
    bag['distributed_mode'] = is_distributed_mode
    bag['max_concurrency'] = int(max_concurrency)
    bag['llm_segments'] = llm_segments
    bag['hitp_segments'] = hitp_segments
    # [M-02 FIX] execution_id를 bag에 명시적으로 저장.
    # segment_runner_service의 save_state_delta 가 event.get('execution_id', 'unknown')으로
    # 조회하므로, bag에 없으면 항상 'unknown'이 기록됨.
    bag['execution_id'] = execution_id
    
    # [State Bag] Enforce Input Encapsulation
    # Embed raw input into the bag so it can be offloaded if large
    bag['input'] = raw_input
    bag['loop_counter'] = 0
    
    # 🛡️ [Dynamic Loop Limit] Segment-based weighted execution counting
    # Formula: (TotalSegments + LoopWeightedSegments) × 1.2 + 20
    # where LoopWeightedSegments = Σ(SegCount × (MaxIter-1)) + 2×ForEachCount
    # 
    # Example 1 - for_each with 30 iterations:
    #   - 3 base segments (prep, for_each, validator)
    #   - for_each adds 2 (parallel_group + aggregator)
    #   - Estimated: (3 + 2) × 1.2 + 20 = 26
    #
    # Example 2 - Sequential loop with 5 iterations, 2 internal segments:
    #   - 4 base segments (prep, loop, seg1, seg2, validator)
    #   - loop adds 2 × (5-1) = 8
    #   - Estimated: (4 + 8) × 1.2 + 20 = 34.4 ≈ 35
    #
    # This prevents LoopLimitExceeded errors while maintaining safety bounds.
    
    # Extract loop analysis from partition_result (if available)
    if partition_result:
        estimated_executions = partition_result.get("estimated_executions", total_segments)
        loop_analysis = partition_result.get("loop_analysis", {})
    else:
        # Fallback when partitioning failed
        estimated_executions = total_segments
        loop_analysis = {}
    
    # Apply safety margin: 20% of estimate or minimum 20
    safety_margin = max(int(estimated_executions * 0.2), 20)
    default_loop_limit = estimated_executions + safety_margin
    
    # Branch loop limit: 50% of main limit or minimum 50
    default_branch_limit = max(int(default_loop_limit * 0.5), 50)
    
    # User can override via workflow_config, otherwise use dynamic calculation
    bag['max_loop_iterations'] = workflow_config.get('max_loop_iterations', default_loop_limit)
    bag['max_branch_iterations'] = workflow_config.get('max_branch_iterations', default_branch_limit)
    
    logger.info(
        f"[Dynamic Loop Limit] "
        f"estimated_executions={estimated_executions}, "
        f"safety_margin={safety_margin}, "
        f"loop_limit={bag['max_loop_iterations']}, "
        f"branch_limit={bag['max_branch_iterations']}, "
        f"loop_nodes={loop_analysis.get('loop_count', 0)}"
    )
    
    bag['start_time'] = current_time
    bag['last_update_time'] = current_time
    bag['state_durations'] = {}
    
    # [v3.23] 시뮬레이터 플래그를 bag 최상위로 복사
    # store_task_token.py가 bag.get('AUTO_RESUME_HITP') 조회
    auto_resume = raw_input.get('AUTO_RESUME_HITP') or event.get('AUTO_RESUME_HITP')
    if auto_resume:
        bag['AUTO_RESUME_HITP'] = auto_resume
    mock_mode = raw_input.get('MOCK_MODE') or event.get('MOCK_MODE')
    if mock_mode:
        bag['MOCK_MODE'] = mock_mode
    
    # 5. [Phase 1/2] Segment Manifest Strategy
    # Merkle DAG 모드: segment_manifest는 이미 S3에 저장됨 (StateVersioningService)
    # Legacy 모드: 기존 방식으로 S3에 업로드
    
    manifest_s3_path = ""
    
    if manifest_id:
        # Merkle DAG: Manifest는 이미 Content-Addressable 블록으로 저장됨
        # manifest_id로 segment_manifest 접근 가능
        manifest_s3_path = f"s3://{STATE_BUCKET}/manifests/{manifest_id}.json"
        logger.info(f"[Merkle DAG] Using manifest: {manifest_s3_path}")
        
    elif partition_map and hydrator.s3_client and bucket:
        # Legacy 모드: 기존 방식으로 segment_manifest 생성/저장
        segment_manifest = []
        for idx, segment in enumerate(partition_map or []):
            segment_manifest.append({
                "segment_id": idx,
                "segment_config": segment,
                "execution_order": idx,
                "dependencies": segment.get("dependencies", []),
                "type": segment.get("type", "normal")
            })
        
        # Legacy manifest upload
        try:
            manifest_key = f"workflow-manifests/{owner_id}/{workflow_id}/segment_manifest.json"
            hydrator.s3_client.put_object(
                Bucket=bucket,
                Key=manifest_key,
                Body=json.dumps(segment_manifest, default=str),
                ContentType='application/json'
            )
            manifest_s3_path = f"s3://{bucket}/{manifest_key}"
            logger.info(f"[Legacy] Segment manifest uploaded: {manifest_s3_path}")
        except Exception as e:
            logger.warning(f"Failed to upload manifest: {e}")
            
    # Create Pointers List for ItemProcessor (Map state에서 사용)
    segment_manifest_pointers = []
    if manifest_s3_path:
        # S3 경로만 참조하는 경량 포인터
        # Merkle DAG 모드에서는 segment_index로 S3 Select 쿼리
        if manifest_id:
            # Merkle DAG: manifest_id + segment_index로 블록 접근
            for idx in range(total_segments):
                segment_manifest_pointers.append({
                    'segment_index': idx,
                    'segment_id': idx,
                    'segment_type': 'computed',  # Merkle DAG에서 타입 추론
                    'manifest_id': manifest_id,
                    'manifest_s3_path': manifest_s3_path,
                    'total_segments': total_segments
                })
        else:
            # Legacy: 기존 방식
            segment_manifest = []
            if partition_map:
                for idx, segment in enumerate(partition_map or []):
                    segment_manifest.append({
                        "segment_id": idx,
                        "segment_config": segment,
                        "execution_order": idx,
                        "dependencies": segment.get("dependencies", []),
                        "type": segment.get("type", "normal")
                    })
            
            for idx, seg in enumerate(segment_manifest):
                segment_manifest_pointers.append({
                    'segment_index': idx,
                    'segment_id': seg.get('segment_id', idx),
                    'segment_type': seg.get('type', 'normal'),
                    'manifest_s3_path': manifest_s3_path,
                    'total_segments': len(segment_manifest)
                })
    else:
        # Fallback to inline (only if S3 failed/missing)
        if partition_map:
            segment_manifest_pointers = []
            for idx, segment in enumerate(partition_map or []):
                segment_manifest_pointers.append({
                    "segment_id": idx,
                    "segment_config": segment,
                    "execution_order": idx,
                    "dependencies": segment.get("dependencies", []),
                    "type": segment.get("type", "normal")
                })

    # Add to bag
    # [C-01 FIX] segment_manifest_pointers는 경량 포인터 배열 (~200B × n 세그먼트).
    # ASL v3 Map state의 ItemsPath: "$.state_data.bag.segment_manifest" 가 이 배열을 참조.
    # 포인터가 없으면 BATCHED/MAP_REDUCE 모드에서 Map state가 0회 실행되어 전면 불능.
    # 전체 segment_config가 아닌 포인터만 담으므로 256KB SFN 한도 내에 유지됨.
    bag['segment_manifest'] = segment_manifest_pointers
    bag['segment_manifest_s3_path'] = manifest_s3_path

    # 6. Dehydrate Final Payload
    # Force offload large fields to Ensure "SFN Only Sees Pointers"
    # Added 'input' to forced offload list to prevent Zombie Data
    # Note: 'segment_manifest' is intentionally NOT force-offloaded — it is a
    #   lightweight pointer array required inline by ASL Map state ItemsPath.
    force_offload = {'workflow_config', 'partition_map', 'current_state', 'input'}
    
    payload = hydrator.dehydrate(
        state=bag,
        owner_id=owner_id,
        workflow_id=workflow_id,
        execution_id=execution_id,
        return_delta=False,
        force_offload_fields=force_offload
    )
    
    # 7. Post-Processing & Compatibility
    # [C-01] segment_manifest_pointers는 bag['segment_manifest']에 이미 저장됨.
    # dehydrate() 이후에도 포인터 배열이 payload에 유지되어야 ASL Map state가 동작함.
    
    # Ensure compatibility aliases exist if fields were offloaded
    # (ASL might reference _s3_path fields directly)
    for field in ['workflow_config', 'partition_map', 'current_state', 'input']:
        val = payload.get(field)
        if isinstance(val, dict) and val.get('__s3_offloaded'):
            payload[f"{field}_s3_path"] = val.get('__s3_path')
            
    # Explicitly set state_s3_path alias for current_state
    if payload.get('current_state_s3_path'):
        payload['state_s3_path'] = payload['current_state_s3_path']
        
    # [No Data at Root] REMOVED inline input copy to prevent payload explosion
    # payload['input'] = raw_input - DELETED
    
    logger.info(f"✅ State Initialization Complete. Keys: {list(payload.keys())}")
    
    # � [v3.13] Kernel Protocol - The Great Seal Pattern
    # seal_state_bag을 사용하여 표준 응답 포맷 생성
    try:
        from src.common.kernel_protocol import seal_state_bag
        _HAS_KERNEL_PROTOCOL = True
    except ImportError:
        try:
            from common.kernel_protocol import seal_state_bag
            _HAS_KERNEL_PROTOCOL = True
        except ImportError:
            _HAS_KERNEL_PROTOCOL = False
    
    if _HAS_KERNEL_PROTOCOL:
        logger.info("🎒 [v3.13] Using Kernel Protocol seal_state_bag")
        
        # Ensure idempotency_key is available
        idempotency_key = bag.get('idempotency_key') or raw_input.get('idempotency_key') or "unknown"
        
        # seal_state_bag: USC 호출 + 표준 포맷 반환
        response_data = seal_state_bag(
            base_state={},  # 빈 상태에서 시작
            result_delta=payload,
            action='init',
            context={
                'execution_id': idempotency_key,
                'idempotency_key': idempotency_key
            }
        )
        
        logger.info(f"✅ [Kernel Protocol] Init complete: next_action={response_data.get('next_action')}")
    elif _HAS_USC and universal_sync_core:
        # Fallback to direct USC (if kernel_protocol not available)
        logger.warning("⚠️ Kernel Protocol not available, using direct USC")
        
        idempotency_key = bag.get('idempotency_key') or raw_input.get('idempotency_key') or "unknown"
        
        usc_result = universal_sync_core(
            base_state={},
            new_result=payload,
            context={
                'action': 'init',
                'execution_id': idempotency_key,
                'idempotency_key': idempotency_key
            }
        )
        
        # 표준 포맷으로 반환
        response_data = {
            'state_data': usc_result.get('state_data', {}),
            'next_action': usc_result.get('next_action', 'STARTED')
        }
    else:
        # USC도 없는 경우 폴백
        logger.warning("⚠️ Universal Sync Core not available, using legacy initialization")
        
        payload['segment_to_run'] = 0
        payload['loop_counter'] = 0
        payload['state_history'] = []
        payload['last_update_time'] = current_time
        
        response_data = {
            'state_data': payload,
            'next_action': 'STARTED'
        }
    
    # 최종 크기 검증
    response_json = json.dumps(response_data, default=str, ensure_ascii=False)
    response_size_kb = len(response_json.encode('utf-8')) / 1024
    
    logger.info(f"✅ InitializeStateData response: {response_size_kb:.1f}KB")
    
    if response_size_kb > 250:
        logger.error(
            f"🚨 CRITICAL: Response exceeds 250KB ({response_size_kb:.1f}KB)! "
            f"Step Functions will reject with DataLimitExceeded."
        )
    elif response_size_kb > 200:
        logger.warning(f"⚠️ Response is {response_size_kb:.1f}KB (>200KB). Close to limit.")
    
    return response_data

