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
        from src.services.workflow.partition_service import partition_workflow_advanced
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
    Initializes state data.
    [Priority 1 Optimization] Load pre-compiled partition_map from DB.
    Fallback: Runtime calculation (maintain backward compatibility)
    
    [Subgraph Support] If workflow_config includes subgraphs,
    DynamicWorkflowBuilder will build recursively.
    """
    logger.info("Initializing state data")
    
    # ====================================================================
    # 🛡️ [v2.6 P0 Fix] 최우선 초기화 - 에러 발생 시에도 필수 필드 보존
    # Step Functions ASL이 $.total_segments를 참조할 때 null 방지
    # ====================================================================
    raw_input = event.get('input', event)
    if not isinstance(raw_input, dict):
        raw_input = {}
    
    # 🛡️ [P0] total_segments 안전 초기화 - try 블록 외부에서 먼저 계산
    _early_partition_map = raw_input.get('partition_map', [])
    _safe_total_segments = len(_early_partition_map) if isinstance(_early_partition_map, list) and _early_partition_map else 1
    
    # [FIX] 1. Move initialization to top (prevent NameError in S3 Metadata)
    current_time = int(time.time())
        
    # [FIX] Explicit variable initialization (prevent UnboundLocalError and Missing Field)
    # Keys must always exist to prevent errors when SFN references $.field_name
    partition_map_s3_path = ""
    manifest_s3_path = ""
    state_s3_path = ""
    
    # 2. Refine config determination priority
    workflow_config = raw_input.get('test_workflow_config') or raw_input.get('workflow_config')
    # [Hydration] Check for S3 offloaded config
    config_s3_path = raw_input.get('test_workflow_config_s3_path') or raw_input.get('workflow_config_s3_path')
    
    owner_id = raw_input.get('ownerId', "")
    workflow_id = raw_input.get('workflowId', "")
    idempotency_key = raw_input.get('idempotency_key', "")
    quota_reservation_id = raw_input.get('quota_reservation_id', "")
    
    # [Fix] Extract MOCK_MODE - for simulator E2E testing
    mock_mode = raw_input.get('MOCK_MODE', 'false')

    # [Hydration] Download config from S3 if offloaded
    if not workflow_config and config_s3_path:
        try:
            logger.info(f"⬇️ [Hydration] Downloading workflow_config from S3: {config_s3_path}")
            # Parse s3://bucket/key
            bucket_name = config_s3_path.replace("s3://", "").split("/")[0]
            key_name = "/".join(config_s3_path.replace("s3://", "").split("/")[1:])
            
            s3_client = boto3.client('s3')
            obj = s3_client.get_object(Bucket=bucket_name, Key=key_name)
            workflow_config = json.loads(obj['Body'].read().decode('utf-8'))
            logger.info(f"✅ [Hydration] Config restored ({len(json.dumps(workflow_config))} bytes)")
        except Exception as e:
            logger.error(f"❌ [Hydration] Failed to download config from S3: {e}")
            # If failed, proceed (will try DB or fallback)
    
    # [Robustness Fix] Load from DB if workflow_config missing (when test_config absent)
    if not workflow_config and workflow_id and owner_id:
        logger.info(f"workflow_config missing in input, loading from DB: {workflow_id}")
        db_data = _load_workflow_config(owner_id, workflow_id)
        if db_data and db_data.get('config'):
            workflow_config = db_data.get('config')
            # Also load partition_map from DB if available
            if db_data.get('partition_map'):
                event['_db_partition_map'] = db_data.get('partition_map')
                event['_db_total_segments'] = db_data.get('total_segments')
                event['_db_llm_segments'] = db_data.get('llm_segments_count')
                event['_db_hitp_segments'] = db_data.get('hitp_segments_count')
        else:
            logger.warning(f"Workflow not found in DB: {workflow_id}")

    # Set to empty dict if workflow_config still missing to prevent crash
    if not workflow_config:
        workflow_config = {}
        logger.warning("Proceeding with empty workflow_config (risk of later failure)")

    # [Fix v2] Merge initial_state bidirectionally for test mode
    # When using test_workflow_config:
    # - workflow_config.initial_state may have workflow-defined variables (e.g., input_text)
    # - raw_input.initial_state may have test metadata (e.g., e2e_test_scenario)
    # Both must be merged, with workflow definition taking precedence for variable values
    top_level_initial_state = raw_input.get('initial_state', {})
    workflow_initial_state = workflow_config.get('initial_state', {})
    
    if top_level_initial_state or workflow_initial_state:
        # Merge: workflow definition first, then overlay test metadata (test metadata can't override workflow vars)
        merged_initial_state = {**workflow_initial_state, **top_level_initial_state}
        # But if workflow has explicit variables, those should win (e.g., input_text from workflow definition)
        # Re-overlay workflow vars to ensure they're not overwritten by empty test metadata
        for key, value in workflow_initial_state.items():
            if value is not None and value != "":
                merged_initial_state[key] = value
        
        workflow_config['initial_state'] = merged_initial_state
        logger.info(f"Merged initial_state (workflow + test metadata): {list(merged_initial_state.keys())}")

    # 3. MOCK_MODE: Force partitioning (prepare for no DB data)
    if raw_input.get('test_workflow_config'):
         if not _HAS_PARTITION:
             logger.warning("MOCK_MODE but partition logic unavailable!")
         else:
             logger.info("MOCK_MODE detected: Forcing runtime partition calculation")
             try:
                 partition_result = partition_workflow_advanced(workflow_config)
                 
                 # 🛡️ [v2.6 P0 Fix] 유령 'code' 타입 박멸 로직
                 # 상위 데이터 오염을 런타임에서 교정
                 for seg in partition_result.get('partition_map', []):
                     if seg is None:
                         logger.warning("🛡️ [Self-Healing] Skipping None segment in partition_map")
                         continue
                     for node in (seg.get('nodes', []) if isinstance(seg, dict) else []):
                         if isinstance(node, dict) and node.get('type') == 'code':
                             logger.warning(f"🛡️ [Self-Healing] Aliasing 'code' to 'operator' for node {node.get('id')}")
                             node['type'] = 'operator'
                 
                 # Override raw inputs/event cache with fresh calculation
                 raw_input['partition_map'] = partition_result.get('partition_map', [])
                 raw_input['total_segments'] = partition_result.get('total_segments', 0)
                 raw_input['llm_segments'] = partition_result.get('llm_segments', 0)
                 raw_input['hitp_segments'] = partition_result.get('hitp_segments', 0)
                 
                 # 🛡️ [P0] 조기 계산된 _safe_total_segments 업데이트
                 _safe_total_segments = max(1, partition_result.get('total_segments', 1))
                 
             except Exception as e:
                 logger.error(f"MOCK_MODE partitioning failed: {e}")

    # 2. Partitioning (priority: input > DB pre-compiled > runtime calculation)
    partition_map = None
    total_segments = 0
    llm_segments = 0
    hitp_segments = 0
    
    # 2a. Use partition_map if already in input (retry/Child Workflow)
    if raw_input.get('partition_map'):
        logger.info("Using provided partition_map from input")
        partition_map = raw_input.get('partition_map')
        total_segments = raw_input.get('total_segments', len(partition_map))
        llm_segments = raw_input.get('llm_segments') or 0
        hitp_segments = raw_input.get('hitp_segments') or 0
        _safe_total_segments = max(1, total_segments)  # 🛡️ Update safe counter
    
    # 2a-1. Use partition_map loaded with config
    if not partition_map and event.get('_db_partition_map'):
        logger.info("Using partition_map from DB config load")
        partition_map = event.get('_db_partition_map')
        total_segments = event.get('_db_total_segments', len(partition_map) if partition_map else 0)
        llm_segments = event.get('_db_llm_segments') or 0
        hitp_segments = event.get('_db_hitp_segments') or 0
        _safe_total_segments = max(1, total_segments)  # 🛡️ Update safe counter
    
    # 2b. Load pre-compiled partition_map from DB (priority 1 optimization)
    if not partition_map:
        precompiled = _load_precompiled_partition(owner_id, workflow_id)
        if precompiled:
            partition_map = precompiled.get('partition_map')
            total_segments = precompiled.get('total_segments', len(partition_map) if partition_map else 0)
            llm_segments = precompiled.get('llm_segments_count') or 0
            hitp_segments = precompiled.get('hitp_segments_count') or 0
            _safe_total_segments = max(1, total_segments)  # 🛡️ Update safe counter
    
    # 2c. Fallback: Runtime calculation (existing logic, maintain backward compatibility)
    if not partition_map:
        if not _HAS_PARTITION:
            raise RuntimeError("partition_workflow_advanced not available and no pre-compiled partition_map found")
        
        logger.info("Calculating partition_map at runtime (fallback)...")
        try:
            partition_result = partition_workflow_advanced(workflow_config)
            partition_map = partition_result.get('partition_map', [])
            total_segments = partition_result.get('total_segments', 0)
            llm_segments = partition_result.get('llm_segments', 0)
            hitp_segments = partition_result.get('hitp_segments', 0)
            
            # 🛡️ Update _safe_total_segments after runtime partitioning
            _safe_total_segments = max(1, total_segments)
            
            # 🛡️ [v2.6 P0 Fix] 유령 'code' 타입 박멸 로직
            for seg in partition_map:
                if seg is None:
                    logger.warning("🛡️ [Self-Healing] Skipping None segment in partition_map")
                    continue
                for node in (seg.get('nodes', []) if isinstance(seg, dict) else []):
                    if isinstance(node, dict) and node.get('type') == 'code':
                        logger.warning(f"🛡️ [Self-Healing] Aliasing 'code' to 'operator' for node {node.get('id')}")
                        node['type'] = 'operator'
            
        except Exception as e:
            logger.error(f"Partitioning failed: {e}")
            raise RuntimeError(f"Failed to partition workflow: {str(e)}")

    # 🚨 [Critical Fix] Recalculate metadata from partition_map if it doesn't match
    # This handles cases where DB has stale or incorrect metadata
    # Recalculate if: partition_map exists AND (metadata missing OR doesn't match actual segment types)
    if partition_map:
        # Count actual segment types from partition_map
        actual_llm_count = sum(1 for seg in partition_map if seg and seg.get('type') == 'llm')
        actual_hitp_count = sum(1 for seg in partition_map if seg and seg.get('type') == 'hitp')
        
        # Recalculate if metadata is missing, None, or doesn't match actual counts
        needs_recalc = (
            llm_segments is None or 
            hitp_segments is None or 
            llm_segments != actual_llm_count or 
            hitp_segments != actual_hitp_count
        )
        
        if needs_recalc:
            logger.info(f"Recalculating metadata: stored=(llm:{llm_segments}, hitp:{hitp_segments}), actual=(llm:{actual_llm_count}, hitp:{actual_hitp_count})")
            llm_segments = actual_llm_count
            hitp_segments = actual_hitp_count
            total_segments = len(partition_map)
            _safe_total_segments = max(1, total_segments)
            logger.info(f"✅ Metadata corrected: llm_segments={llm_segments}, hitp_segments={hitp_segments}, total_segments={total_segments}")

    # 🎯 [Unification Strategy] Create segment_manifest (list of segments to execute)
    segment_manifest = []
    for idx, segment in enumerate(partition_map):
        segment_manifest.append({
            "segment_id": idx,
            "segment_config": segment,
            "execution_order": idx,
            "dependencies": segment.get("dependencies", []),
            "type": segment.get("type", "normal")
        })
    
    # � [MOVED UP] Hybrid Distributed Strategy Calculation - BEFORE S3 offload decision
    distributed_strategy = _calculate_distributed_strategy(
        total_segments=total_segments,
        llm_segments=llm_segments,
        hitp_segments=hitp_segments,
        partition_map=partition_map
    )
    
    logger.info(f"[Distributed Strategy] {distributed_strategy['strategy']}: {distributed_strategy['reason']}")
    
    # 🚨 [Critical Fix] Detect distributed mode and offload large data to S3
    # Check if workflow explicitly sets distributed_mode in initial_state
    explicit_distributed_mode = workflow_config.get('initial_state', {}).get('distributed_mode')
    if explicit_distributed_mode is not None:
        is_distributed_mode = bool(explicit_distributed_mode)
        logger.info(f"[Distributed Mode] Using explicit value from initial_state: {is_distributed_mode}")
    else:
        is_distributed_mode = total_segments > 300  # Distributed mode threshold
        logger.info(f"[Distributed Mode] Auto-detected based on segment count: {is_distributed_mode} (total_segments={total_segments})")
    
    partition_map_for_return = partition_map  # Default: return all
    
    # 🚀 [Hybrid Mode Compatibility] MAP_REDUCE/BATCHED 모드에서는 segment_manifest 인라인 유지
    uses_inline_manifest = distributed_strategy["strategy"] in ("MAP_REDUCE", "BATCHED")
    
    # [Payload Safety] Calculate manifest size to prevent inline explosion
    if DecimalEncoder:
        manifest_json = json.dumps(segment_manifest, cls=DecimalEncoder, ensure_ascii=False)
    else:
        # Fallback: try to convert decimals manually
        def convert_decimals(obj):
            if isinstance(obj, dict):
                return {k: convert_decimals(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_decimals(v) for v in obj]
            elif hasattr(obj, '__class__') and 'Decimal' in obj.__class__.__name__:
                return float(obj) if '.' in str(obj) else int(obj)
            else:
                return obj
        
        converted_manifest = convert_decimals(segment_manifest)
        manifest_json = json.dumps(converted_manifest, ensure_ascii=False)
    
    manifest_size_kb = len(manifest_json.encode('utf-8')) / 1024
    
    # If manifest is too large (> 50KB), force offload regardless of strategy
    if manifest_size_kb > 50:
        logger.warning(f"Manifest size {manifest_size_kb:.1f}KB exceeds limit. Forcing offload (Override strategy {distributed_strategy['strategy']})")
        uses_inline_manifest = False

    # AWS Clients
    s3_client = None
    bucket = os.environ.get('WORKFLOW_STATE_BUCKET')
    
    # Initialize boto3 client only when S3 upload needed
    if bucket and owner_id and workflow_id:
        if (len(segment_manifest) > 50 and not uses_inline_manifest) or is_distributed_mode:
            import boto3
            s3_client = boto3.client('s3')

    # 1. Manifest Offloading (when exceeding 50 items OR forced by size)
    if (len(segment_manifest) > 50 or manifest_size_kb > 50) and s3_client and not uses_inline_manifest:
        try:
            manifest_key = f"workflow-manifests/{owner_id}/{workflow_id}/segment_manifest.json"
            # Use pre-calculated json
            s3_client.put_object(
                Bucket=bucket,
                Key=manifest_key,
                Body=manifest_json.encode('utf-8')
            )
            manifest_s3_path = f"s3://{bucket}/{manifest_key}"
            logger.info(f"Segment manifest uploaded to S3: {manifest_s3_path}")
        except Exception as e:
            logger.warning(f"Failed to upload segment manifest to S3: {e}")
            # Keep manifest_s3_path as initial value "" on failure

    # 2. Partition Map Offloading (distributed mode)
    # 🚨 [Critical Fix] Optimize partition_map size in distributed mode
    # Check size FIRST to determine if we need to enter distributed/offloaded mode
    if DecimalEncoder:
        partition_map_json = json.dumps(partition_map, cls=DecimalEncoder, ensure_ascii=False)
    else:
        # Fallback: try to convert decimals manually
        def convert_decimals(obj):
            if isinstance(obj, dict):
                return {k: convert_decimals(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_decimals(v) for v in obj]
            elif hasattr(obj, '__class__') and 'Decimal' in obj.__class__.__name__:
                return float(obj) if '.' in str(obj) else int(obj)
            else:
                return obj
        
        converted_map = convert_decimals(partition_map)
        partition_map_json = json.dumps(converted_map, ensure_ascii=False)
    
    partition_map_size_kb = len(partition_map_json.encode('utf-8')) / 1024
    
    # Offload if segments > 300 OR payload > 100KB (Safety margin for 256KB limit)
    should_offload_map = is_distributed_mode or partition_map_size_kb > 100
    
    if should_offload_map:
        # Force distributed flag if offloading due to size
        is_distributed_mode = True
        
        logger.info(f"Large payload detected: {total_segments} segments, map size: {partition_map_size_kb:.1f}KB. Forcing offload.")
        
        # Offload partition_map larger than 100KB to S3
        if s3_client:
            try:
                partition_map_key = f"workflow-partitions/{owner_id}/{workflow_id}/partition_map.json"
                s3_client.put_object(
                    Bucket=bucket,
                    Key=partition_map_key,
                    Body=partition_map_json.encode('utf-8'),
                    ContentType='application/json',
                    Metadata={
                        'total_segments': str(total_segments),
                        'size_kb': str(int(partition_map_size_kb)),
                        'created_at': str(current_time)
                    }
                )
                
                partition_map_s3_path = f"s3://{bucket}/{partition_map_key}"
                logger.info(f"Large partition_map offloaded to S3: {partition_map_s3_path}")
                
                # 🚨 [Critical] Exclude partition_map in distributed mode
                partition_map_for_return = None
                
            except Exception as e:
                logger.warning(f"Failed to offload partition_map to S3: {e}")
                # Warn and fallback to inline on S3 failure
                logger.warning(f"Proceeding with inline partition_map ({partition_map_size_kb:.1f}KB)")
        else:
            logger.info(f"partition_map size acceptable for inline return: {partition_map_size_kb:.1f}KB")

    # 3. Initial state configuration
    current_time = int(time.time())
    
    # Set current_state to null if S3 path exists, otherwise use initial_state
    initial_state_s3_path = workflow_config.get('initial_state_s3_path')
    if initial_state_s3_path:
        # Large payload: Load from S3 (handled by segment_runner)
        current_state = None
        state_s3_path = initial_state_s3_path
        input_value = initial_state_s3_path
    else:
        # Inline state
        current_state = workflow_config.get('initial_state', {})
        # state_s3_path = "" # Already initialized above
        input_value = current_state
    
    # 🚨 [Strategy 2] Always offload workflow_config and current_state to S3
    # Step Functions will only receive S3 paths, Lambda functions hydrate on demand
    import boto3
    s3_client = boto3.client('s3')
    bucket = os.environ.get('WORKFLOW_STATE_BUCKET')
    
    # Upload workflow_config to S3
    workflow_config_s3_key = f"workflow-states/{owner_id}/{workflow_id}/workflow_config_{current_time}.json"
    workflow_config_json = json.dumps(workflow_config, default=str, ensure_ascii=False)
    s3_client.put_object(
        Bucket=bucket,
        Key=workflow_config_s3_key,
        Body=workflow_config_json.encode('utf-8'),
        ContentType='application/json'
    )
    workflow_config_s3_path_full = f"s3://{bucket}/{workflow_config_s3_key}"
    logger.info(f"✅ Uploaded workflow_config to S3: {workflow_config_s3_path_full} ({len(workflow_config_json)/1024:.1f}KB)")
    
    # Upload current_state to S3 if it exists (skip if None from initial_state_s3_path)
    if current_state is not None:
        state_s3_key = f"workflow-states/{owner_id}/{workflow_id}/current_state_{current_time}.json"
        state_json = json.dumps(current_state, default=str, ensure_ascii=False)
        s3_client.put_object(
            Bucket=bucket,
            Key=state_s3_key,
            Body=state_json.encode('utf-8'),
            ContentType='application/json'
        )
        state_s3_path = f"s3://{bucket}/{state_s3_key}"
        logger.info(f"✅ Uploaded current_state to S3: {state_s3_path} ({len(state_json)/1024:.1f}KB)")
    # else: state_s3_path already set to initial_state_s3_path above
    
    # 4. Dynamic concurrency calculation (Map state optimization)
    max_concurrency = _calculate_dynamic_concurrency(
        total_segments=total_segments,
        llm_segments=llm_segments,
        hitp_segments=hitp_segments,
        partition_map=partition_map,
        owner_id=owner_id
    )
    
    # 🚀 Override max_concurrency with strategy-recommended value (strategy calculated earlier)
    max_concurrency = distributed_strategy.get("max_concurrency", max_concurrency)
    
    # � [Light Config] Extract minimal metadata for Step Functions routing
    light_config = {
        "workflow_id": workflow_id,
        "execution_mode": workflow_config.get("execution_mode", "SEQUENTIAL"),
        "node_count": len(workflow_config.get("nodes", [])),
        "distributed_mode": is_distributed_mode,
        "distributed_strategy": distributed_strategy["strategy"],
        "llm_segments": llm_segments,
        "hitp_segments": hitp_segments,
        "max_concurrency": max_concurrency
    }
    
    # ============================================================
    # 🎯 v3.3 Unified Pipe: Universal Sync Core를 통한 Day-Zero Sync
    # ============================================================
    # 
    # 비즈니스 로직(파티셔닝)은 여기까지, 데이터 저장/최적화는 USC에 위임
    # - P0: Dirty Input 자동 방어 (256KB 초과 시 자동 오프로딩)
    # - 필수 메타데이터 강제 주입 (segment_to_run, loop_counter 등)
    # - 생애 주기 전체에 걸쳐 단일 파이프 통과
    #
    initial_payload = {
        # Light Config: Step Functions 라우팅용 경량 메타데이터
        "light_config": light_config,
        # S3 paths (이미 업로드된 경로)
        "workflow_config_s3_path": workflow_config_s3_path_full,
        "state_s3_path": state_s3_path,
        "input": input_value,
        "ownerId": owner_id,
        "workflowId": workflow_id,
        "idempotency_key": idempotency_key,
        "quota_reservation_id": quota_reservation_id,
        
        # 파티셔닝 결과
        "total_segments": _safe_total_segments,
        "partition_map": partition_map_for_return,
        "partition_map_s3_path": partition_map_s3_path,
        "segment_manifest": segment_manifest if manifest_s3_path == "" else None,
        "segment_manifest_s3_path": manifest_s3_path,
        
        # 통계 및 메타데이터
        "llm_segments": llm_segments,
        "hitp_segments": hitp_segments,
        "state_durations": {},
        "start_time": current_time,
        
        # 분산 실행 설정
        "distributed_mode": is_distributed_mode,
        "distributed_strategy": distributed_strategy["strategy"],
        "max_concurrency": max_concurrency,
        
        # 루프 제어 (Step Functions 비교용 int 강제)
        "max_loop_iterations": int(workflow_config.get("max_loop_iterations", 100)),
        "max_branch_iterations": int(workflow_config.get("max_branch_iterations", 100)),
        
        # 테스트 모드
        "MOCK_MODE": mock_mode,
    }
    
    # 🎯 Universal Sync Core 호출 (action='init')
    # - 빈 상태 {}에서 시작하여 표준 StateBag으로 승격
    # - 필수 메타데이터 자동 주입 (segment_to_run=0, loop_counter=0, state_history=[])
    # - P0~P2 자동 최적화 (256KB 초과 시 자동 오프로딩)
    if _HAS_USC and universal_sync_core:
        logger.info("🎯 [Day-Zero Sync] Routing through Universal Sync Core (action='init')")
        
        usc_result = universal_sync_core(
            base_state={},  # 빈 상태에서 시작
            new_result=initial_payload,
            context={
                'action': 'init',
                'execution_id': idempotency_key,
                'idempotency_key': idempotency_key
            }
        )
        
        response_data = usc_result['state_data']
        next_action = usc_result.get('next_action', 'STARTED')
        
        logger.info(f"✅ [Day-Zero Sync] Complete: next_action={next_action}, size={response_data.get('payload_size_kb', 0)}KB")
    else:
        # USC 미사용 폴백 (기존 로직)
        logger.warning("⚠️ Universal Sync Core not available, using legacy initialization")
        response_data = initial_payload
        response_data['segment_to_run'] = 0
        response_data['loop_counter'] = 0
        response_data['state_history'] = []
        response_data['last_update_time'] = current_time
    
    # 최종 크기 검증 (USC가 이미 처리했지만 로깅용)
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

