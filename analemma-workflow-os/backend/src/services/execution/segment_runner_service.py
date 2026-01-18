import logging
import os
import time
import json
from typing import Dict, Any, Optional, List, Tuple

# [v2.1] 중앙 집중식 재시도 유틸리티
try:
    from src.common.retry_utils import retry_call, retry_stepfunctions, retry_s3
    RETRY_UTILS_AVAILABLE = True
except ImportError:
    RETRY_UTILS_AVAILABLE = False

# Services
from src.services.state.state_manager import StateManager
from src.services.recovery.self_healing_service import SelfHealingService
# Legacy Imports (for now, until further refactoring)
from src.services.workflow.repository import WorkflowRepository
# Using generic imports from main handler file as source of truth
from src.handlers.core.main import run_workflow, partition_workflow as _partition_workflow_dynamically, _build_segment_config
from src.common.statebag import normalize_inplace


logger = logging.getLogger(__name__)

# ============================================================================
# 🛡️ [Kernel] Dynamic Scheduling Constants
# ============================================================================
# 메모리 안전 마진 (80% 사용 시 분할 트리거)
MEMORY_SAFETY_THRESHOLD = 0.8
# 세그먼트 분할 시 최소 노드 수
MIN_NODES_PER_SUB_SEGMENT = 2
# 최대 분할 깊이 (무한 분할 방지)
MAX_SPLIT_DEPTH = 3
# 세그먼트 상태 값
SEGMENT_STATUS_PENDING = "PENDING"
SEGMENT_STATUS_RUNNING = "RUNNING"
SEGMENT_STATUS_COMPLETED = "COMPLETED"
SEGMENT_STATUS_SKIPPED = "SKIPPED"
SEGMENT_STATUS_FAILED = "FAILED"


class SegmentRunnerService:
    def __init__(self):
        self.state_manager = StateManager()
        self.healer = SelfHealingService()
        self.repo = WorkflowRepository()
        self.threshold = int(os.environ.get("STATE_SIZE_THRESHOLD", 256000))
        
        # 🛡️ [Kernel] S3 클라이언트 (지연 초기화)
        self._s3_client = None
    
    @property
    def s3_client(self):
        """Lazy S3 client initialization"""
        if self._s3_client is None:
            import boto3
            self._s3_client = boto3.client('s3')
        return self._s3_client

    # ========================================================================
    # 🛡️ [Pattern 1] Segment-Level Self-Healing: 세그먼트 내부 동적 분할
    # ========================================================================
    def _estimate_segment_memory(self, segment_config: Dict[str, Any], state: Dict[str, Any]) -> int:
        """
        세그먼트 실행에 필요한 메모리 추정 (MB 단위)
        
        [최적화] json.dumps 대신 메타데이터 기반 휴리스틱 사용
        - 대용량 데이터에서 json.dumps는 그 자체로 메모리 부담
        - 리스트 길이, 문자열 키 존재 여부 등으로 경량 추정
        
        추정 기준:
        - 노드 수 × 기본 메모리 (10MB)
        - LLM 노드: 추가 50MB
        - for_each 노드: 아이템 수 × 5MB
        - 상태 크기: 메타데이터 기반 추정
        """
        base_memory = 50  # 기본 오버헤드
        
        nodes = segment_config.get('nodes', [])
        if not nodes:
            return base_memory
        
        node_memory = len(nodes) * 10  # 노드당 10MB
        
        llm_memory = 0
        foreach_memory = 0
        
        for node in nodes:
            node_type = node.get('type', '')
            if node_type in ('llm_chat', 'aiModel'):
                llm_memory += 50  # LLM 노드는 추가 50MB
            elif node_type == 'for_each':
                config = node.get('config', {})
                items_key = config.get('input_list_key', '')
                if items_key and items_key in state:
                    items = state.get(items_key, [])
                    if isinstance(items, list):
                        foreach_memory += len(items) * 5
        
        # [최적화] 상태 크기 메타데이터 기반 추정 (json.dumps 회피)
        state_size_mb = self._estimate_state_size_lightweight(state)
        
        total = base_memory + node_memory + llm_memory + foreach_memory + int(state_size_mb)
        
        logger.debug(f"[Kernel] Memory estimate: base={base_memory}, nodes={node_memory}, "
                    f"llm={llm_memory}, foreach={foreach_memory}, state={state_size_mb:.1f}MB, total={total}MB")
        
        return total

    def _estimate_state_size_lightweight(self, state: Dict[str, Any], max_sample_keys: int = 20) -> float:
        """
        [최적화] json.dumps 없이 상태 크기를 경량 추정
        
        전략:
        1. 상위 N개 키만 샘플링하여 평균 크기 계산
        2. 리스트는 길이 × 평균 아이템 크기로 추정
        3. 문자열은 len() 사용
        4. 중첩 dict는 키 수로 추정
        
        Returns:
            추정 크기 (MB)
        """
        if not state or not isinstance(state, dict):
            return 0.1  # 최소 100KB
        
        total_bytes = 0
        keys = list(state.keys())[:max_sample_keys]
        
        for key in keys:
            value = state.get(key)
            total_bytes += self._estimate_value_size(value)
        
        # 샘플링 비율로 전체 크기 추정
        if len(state) > max_sample_keys:
            sample_ratio = len(state) / max_sample_keys
            total_bytes = int(total_bytes * sample_ratio)
        
        return total_bytes / (1024 * 1024)  # bytes → MB

    def _estimate_value_size(self, value: Any, depth: int = 0) -> int:
        """
        값의 크기를 휴리스틱으로 추정 (bytes)
        
        재귀 깊이 제한으로 무한 루프 방지
        """
        if depth > 3:  # 깊이 제한
            return 100  # 대략적 추정
        
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
            # 첫 3개 아이템만 샘플링하여 평균 계산
            sample = value[:3]
            avg_size = sum(self._estimate_value_size(v, depth + 1) for v in sample) / len(sample)
            return int(avg_size * len(value))
        elif isinstance(value, dict):
            if not value:
                return 2
            # 첫 5개 키만 샘플링
            sample_keys = list(value.keys())[:5]
            sample_size = sum(
                len(str(k)) + self._estimate_value_size(value[k], depth + 1) 
                for k in sample_keys
            )
            if len(value) > 5:
                return int(sample_size * len(value) / 5)
            return sample_size
        else:
            # 기타 타입: 대략적 추정
            return 100

    def _split_segment(self, segment_config: Dict[str, Any], split_depth: int = 0) -> List[Dict[str, Any]]:
        """
        세그먼트를 더 작은 서브 세그먼트로 분할
        
        분할 전략:
        1. 노드 리스트를 반으로 나눔
        2. 의존성 유지: 엣지 연결 보존
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
        
        first_node_ids = {n.get('id') for n in first_nodes}
        second_node_ids = {n.get('id') for n in second_nodes}
        
        # 엣지 분리: 각 서브 세그먼트 내부 엣지만 유지
        first_edges = [e for e in edges 
                      if e.get('source') in first_node_ids and e.get('target') in first_node_ids]
        second_edges = [e for e in edges 
                       if e.get('source') in second_node_ids and e.get('target') in second_node_ids]
        
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
        
        logger.info(f"[Kernel] 🔧 Segment '{original_id}' split into 2 sub-segments: "
                   f"{len(first_nodes)} + {len(second_nodes)} nodes")
        
        return [sub_segment_1, sub_segment_2]

    def _execute_with_auto_split(
        self, 
        segment_config: Dict[str, Any], 
        initial_state: Dict[str, Any],
        auth_user_id: str,
        split_depth: int = 0
    ) -> Dict[str, Any]:
        """
        🛡️ [Pattern 1] 메모리 기반 자동 분할 실행
        
        메모리 부족이 예상되면 세그먼트를 분할하여 순차 실행
        """
        # 사용 가능한 Lambda 메모리
        available_memory = int(os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', 512))
        
        # 메모리 요구량 추정
        estimated_memory = self._estimate_segment_memory(segment_config, initial_state)
        
        # 안전 임계값 체크
        if estimated_memory > available_memory * MEMORY_SAFETY_THRESHOLD:
            logger.info(f"[Kernel] ⚠️ Memory pressure detected: {estimated_memory}MB estimated, "
                       f"{available_memory}MB available (threshold: {MEMORY_SAFETY_THRESHOLD*100}%)")
            
            # 분할 시도
            sub_segments = self._split_segment(segment_config, split_depth)
            
            if len(sub_segments) > 1:
                logger.info(f"[Kernel] 🔧 Executing {len(sub_segments)} sub-segments sequentially")
                
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
                    
                    # 상태 병합
                    if isinstance(sub_result, dict):
                        current_state.update(sub_result)
                        all_logs.extend(sub_result.get('__new_history_logs', []))
                    
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
        return run_workflow(
            config_json=segment_config,
            initial_state=initial_state,
            ddb_table_name=os.environ.get("JOB_TABLE"),
            user_api_keys={},
            run_config={"user_id": auth_user_id}
        )

    # ========================================================================
    # 🛡️ [Pattern 2] Manifest Mutation: S3 Manifest 동적 수정
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
            manifest = json.loads(response['Body'].read().decode('utf-8'))
            
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
        reason: str
    ) -> bool:
        """
        🛡️ [Pattern 2] 특정 세그먼트를 SKIP으로 마킹
        
        사용 시나리오:
        - 조건 분기에서 특정 경로 불필요
        - 선행 세그먼트 실패로 후속 세그먼트 실행 불가
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
        
        if modified:
            return self._save_manifest_to_s3(manifest, manifest_s3_path)
        
        return False

    def _inject_recovery_segments(
        self,
        manifest_s3_path: str,
        after_segment_id: int,
        recovery_segments: List[Dict[str, Any]],
        reason: str
    ) -> bool:
        """
        🛡️ [Pattern 2] 복구 세그먼트 삽입
        
        사용 시나리오:
        - API 실패 후 백업 경로 삽입
        - 에러 핸들링 세그먼트 동적 추가
        """
        manifest = self._load_manifest_from_s3(manifest_s3_path)
        if not manifest:
            return False
        
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
        
        logger.info(f"[Kernel] 🔧 Injected {len(recovery_segments)} recovery segments after segment {after_segment_id}")
        
        return self._save_manifest_to_s3(new_manifest, manifest_s3_path)

    def _trigger_child_workflow(self, event: Dict[str, Any], branch_config: Dict[str, Any], auth_user_id: str, quota_id: str) -> Optional[Dict[str, Any]]:
        """
        Triggers a Child Step Function (Standard Orchestrator) for complex branches.
        "Fire and Forget" pattern to avoid Lambda timeouts.
        """
        try:
            import boto3
            import json
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

    def execute_segment(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main execution logic for a workflow segment.
        """
        # [Fix] 이벤트에서 MOCK_MODE를 읽어서 환경 변수로 주입
        # 이렇게 하면 모든 하위 함수들(invoke_bedrock_model 등)이 MOCK_MODE를 인식함
        event_mock_mode = event.get('MOCK_MODE', '').lower()
        if event_mock_mode in ('true', '1', 'yes', 'on'):
            os.environ['MOCK_MODE'] = 'true'
            logger.info("🧪 MOCK_MODE enabled from event payload")
        
        # 0. Check for Branch Offloading
        branch_config = event.get('branch_config')
        if branch_config:
            force_child = os.environ.get('FORCE_CHILD_WORKFLOW', 'false').lower() == 'true'
            node_count = len(branch_config.get('nodes', [])) if isinstance(branch_config.get('nodes'), list) else 0
            has_hitp = branch_config.get('hitp', False) or any(n.get('hitp') for n in branch_config.get('nodes', []))
            
            should_offload = force_child or node_count > 20 or has_hitp
            
            if should_offload:
                auth_user_id = event.get('ownerId') or event.get('owner_id')
                quota_id = event.get('quota_reservation_id')
                
                child_result = self._trigger_child_workflow(event, branch_config, auth_user_id, quota_id)
                if child_result:
                    return child_result

        # 1. State Bag Normalization
        normalize_inplace(event, remove_state_data=True)
        
        # 2. Extract Context
        auth_user_id = event.get('ownerId') or event.get('owner_id') or event.get('user_id')
        workflow_id = event.get('workflowId') or event.get('workflow_id')
        # 🚀 [Hybrid Mode] Support both segment_id (hybrid) and segment_to_run (legacy)
        segment_id = event.get('segment_id') or event.get('segment_to_run', 0)
        s3_bucket = os.environ.get("S3_BUCKET") or os.environ.get("SKELETON_S3_BUCKET")
        
        # 3. Load State (Inline or S3)
        # [Critical Fix] Step Functions passes state as 'current_state', not 'state'
        state_s3_path = event.get('state_s3_path')
        initial_state = event.get('current_state') or event.get('state', {})
        
        if state_s3_path:
            initial_state = self.state_manager.download_state_from_s3(state_s3_path)
            
        # 4. Resolve Segment Config
        # [Critical Fix] Support both test_workflow_config (E2E tests) and workflow_config
        workflow_config = event.get('test_workflow_config') or event.get('workflow_config')
        partition_map = event.get('partition_map')
        partition_map_s3_path = event.get('partition_map_s3_path')
        
        # 🚀 [Hybrid Mode] Direct segment_config support for MAP_REDUCE/BATCHED modes
        direct_segment_config = event.get('segment_config')
        execution_mode = event.get('execution_mode')
        
        if direct_segment_config and execution_mode in ('MAP_REDUCE', 'BATCHED'):
            logger.info(f"[Hybrid Mode] Using direct segment_config for {execution_mode} mode")
            segment_config = direct_segment_config
        else:
            # [Critical Fix] Support S3 Offloaded Partition Map with retry
            if not partition_map and partition_map_s3_path:
                try:
                    import boto3
                    import json
                    s3 = boto3.client('s3')
                    bucket_name = partition_map_s3_path.replace("s3://", "").split("/")[0]
                    key_name = "/".join(partition_map_s3_path.replace("s3://", "").split("/")[1:])
                    
                    logger.info(f"Loading partition_map from S3: {partition_map_s3_path}")
                    
                    # [v2.1] S3 get_object에 재시도 적용
                    def _get_partition_map():
                        obj = s3.get_object(Bucket=bucket_name, Key=key_name)
                        return json.loads(obj['Body'].read().decode('utf-8'))
                    
                    if RETRY_UTILS_AVAILABLE:
                        partition_map = retry_call(
                            _get_partition_map,
                            max_retries=2,
                            base_delay=0.5,
                            max_delay=5.0
                        )
                    else:
                        partition_map = _get_partition_map()
                        
                except Exception as e:
                    logger.error(f"Failed to load partition_map from S3 after retries: {e}")
                    # Fallback to dynamic partitioning (handled in _resolve_segment_config)
            
            segment_config = self._resolve_segment_config(workflow_config, partition_map, segment_id)
        
        # [Critical Fix] parallel_group 타입 세그먼트는 바로 PARALLEL_GROUP status 반환
        # ASL의 ProcessParallelSegments가 branches를 받아서 Map으로 병렬 실행함
        segment_type = segment_config.get('type') if isinstance(segment_config, dict) else None
        if segment_type == 'parallel_group':
            branches = segment_config.get('branches', [])
            logger.info(f"🔀 Parallel group detected with {len(branches)} branches")
            return {
                "status": "PARALLEL_GROUP",
                "final_state": initial_state,
                "final_state_s3_path": None,
                "next_segment_to_run": segment_id + 1,  # aggregator로 이동
                "new_history_logs": [],
                "error_info": None,
                "branches": branches,
                "segment_type": "parallel_group"
            }
        
        # 🛡️ [Pattern 2] 커널 검증: 이 세그먼트가 SKIPPED 상태인가?
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
            
            return {
                "status": "SKIPPED",
                "final_state": initial_state,
                "final_state_s3_path": None,
                "next_segment_to_run": segment_id + 1,
                "new_history_logs": [],
                "error_info": None,
                "branches": None,
                "segment_type": "skipped",
                "kernel_action": kernel_log,
                "segment_id": segment_id
            }
        
        # 5. Apply Self-Healing (Prompt Injection / Refinement)
        self.healer.apply_healing(segment_config, event.get("_self_healing_metadata"))
        
        # 6. Check User Quota / Secret Resolution (Repo access)
        # Note: In a full refactor, this should move to a UserService or AuthMiddleware
        # For now, we keep it simple.
        if auth_user_id:
            try:
                self.repo.get_user(auth_user_id) # Just validating access/existence
            except Exception as e:
                logger.warning("User check failed, but proceeding if possible: %s", e)

        # 7. Execute Workflow Segment
        # 🛡️ [Pattern 1] 메모리 기반 자동 분할 적용
        user_api_keys = {} # Should be resolved from Secrets Manager or Repo
        
        start_time = time.time()
        
        # 커널 동적 분할 활성화 여부 확인
        enable_kernel_split = os.environ.get('ENABLE_KERNEL_SPLIT', 'true').lower() == 'true'
        
        if enable_kernel_split and isinstance(segment_config, dict):
            # 🛡️ [Pattern 1] 자동 분할 실행
            result_state = self._execute_with_auto_split(
                segment_config=segment_config,
                initial_state=initial_state,
                auth_user_id=auth_user_id,
                split_depth=segment_config.get('_split_depth', 0)
            )
        else:
            # 기존 로직: 직접 실행
            result_state = run_workflow(
                config_json=segment_config,
                initial_state=initial_state,
                ddb_table_name=os.environ.get("JOB_TABLE"),
                user_api_keys=user_api_keys,
                run_config={"user_id": auth_user_id}
            )
        
        execution_time = time.time() - start_time
        
        # 🛡️ [Pattern 2] 조건부 스킵 결정
        # 실행 결과에서 스킵할 세그먼트가 지정되었는지 확인
        manifest_s3_path = event.get('segment_manifest_s3_path')
        if manifest_s3_path and isinstance(result_state, dict):
            skip_next_segments = result_state.get('_kernel_skip_segments', [])
            if skip_next_segments:
                skip_reason = result_state.get('_kernel_skip_reason', 'Condition not met')
                self._mark_segments_for_skip(manifest_s3_path, skip_next_segments, skip_reason)
                logger.info(f"[Kernel] Marked {len(skip_next_segments)} segments for skip: {skip_reason}")
            
            # 복구 세그먼트 삽입 요청 처리
            recovery_request = result_state.get('_kernel_inject_recovery')
            if recovery_request:
                self._inject_recovery_segments(
                    manifest_s3_path=manifest_s3_path,
                    after_segment_id=segment_id,
                    recovery_segments=recovery_request.get('segments', []),
                    reason=recovery_request.get('reason', 'Recovery injection')
                )
        
        # 8. Handle Output State Storage
        final_state, output_s3_path = self.state_manager.handle_state_storage(
            state=result_state,
            auth_user_id=auth_user_id,
            workflow_id=workflow_id,
            segment_id=segment_id,
            bucket=s3_bucket,
            threshold=self.threshold
        )
        
        # Extract history logs from result_state if available
        new_history_logs = result_state.get('__new_history_logs', []) if isinstance(result_state, dict) else []
        
        # [Critical Fix] 워크플로우 완료 여부 결정
        # 1. test_workflow_config가 주입된 경우 (E2E 테스트): 한 번에 전체 실행 후 완료
        # 2. partition_map이 없는 경우: 전체 워크플로우를 한 번에 실행했으므로 완료
        # 3. partition_map이 있는 경우: 다음 세그먼트가 있는지 확인
        is_e2e_test = event.get('test_workflow_config') is not None
        has_partition_map = partition_map is not None and len(partition_map) > 0
        
        # 🛡️ 커널 메타데이터 추출 (있는 경우)
        kernel_actions = result_state.get('__kernel_actions', []) if isinstance(result_state, dict) else []
        
        if is_e2e_test or not has_partition_map:
            # E2E 테스트 또는 파티션 없는 단일 실행: 워크플로우 완료
            return {
                "status": "COMPLETE",  # ASL이 기대하는 상태값
                "final_state": final_state,
                "final_state_s3_path": output_s3_path,
                "next_segment_to_run": None,  # 다음 세그먼트 없음
                "new_history_logs": new_history_logs,
                "error_info": None,
                "branches": None,
                "segment_type": "final",
                "state_s3_path": output_s3_path,
                "segment_id": segment_id,
                "execution_time": execution_time,
                "kernel_actions": kernel_actions if kernel_actions else None
            }
        
        # 파티션 맵이 있는 경우: 다음 세그먼트 존재 여부 확인
        total_segments = event.get('total_segments', len(partition_map))
        next_segment = segment_id + 1
        
        if next_segment >= total_segments:
            # 마지막 세그먼트 완료
            return {
                "status": "COMPLETE",
                "final_state": final_state,
                "final_state_s3_path": output_s3_path,
                "next_segment_to_run": None,
                "new_history_logs": new_history_logs,
                "error_info": None,
                "branches": None,
                "segment_type": "final",
                "state_s3_path": output_s3_path,
                "segment_id": segment_id,
                "execution_time": execution_time,
                "kernel_actions": kernel_actions if kernel_actions else None
            }
        
        # 아직 실행할 세그먼트가 남아있음
        return {
            "status": "SUCCEEDED",
            "final_state": final_state,
            "final_state_s3_path": output_s3_path,
            "next_segment_to_run": next_segment,
            "new_history_logs": new_history_logs,
            "error_info": None,
            "branches": None,
            "segment_type": "normal",
            "state_s3_path": output_s3_path,
            "segment_id": segment_id,
            "execution_time": execution_time,
            "kernel_actions": kernel_actions if kernel_actions else None
        }

    def _resolve_segment_config(self, workflow_config, partition_map, segment_id):
        """
        Identical logic to original handler for partitioning.
        """
        if workflow_config:
             # Basic full workflow or pre-chunked
             # If we are strictly running a segment, we might need to simulate partitioning if map is missing
             # For simplicity, we assume workflow_config IS the segment config if partition_map is missing
             # OR we call the dynamic partitioner.
             if not partition_map:
                 # Fallback to dynamic partitioning logic
                 parts = _partition_workflow_dynamically(workflow_config) # arbitrary chunks removed
                 if 0 <= segment_id < len(parts):
                     return parts[segment_id]
                 return workflow_config # Fallback

        # 🚨 [Critical Fix] partition_map이 list 또는 dict일 수 있음
        if partition_map:
            if isinstance(partition_map, list):
                # list인 경우: 인덱스로 접근
                if 0 <= segment_id < len(partition_map):
                    return partition_map[segment_id]
            elif isinstance(partition_map, dict):
                # dict인 경우: 문자열 키로 접근
                if str(segment_id) in partition_map:
                    return partition_map[str(segment_id)]
            
        # Simplified fallback for readability in pilot
        return workflow_config
