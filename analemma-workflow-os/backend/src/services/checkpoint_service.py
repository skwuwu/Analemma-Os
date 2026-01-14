# -*- coding: utf-8 -*-
"""
Checkpoint Service

Time Machine 디버깅을 위한 체크포인트 관리 서비스입니다.
기존 execution 데이터를 활용하여 체크포인트 기능을 제공합니다.

Features:
- 실행 타임라인 조회 (GSI 기반 query 최적화)
- 체크포인트 비교 (State Diff)
- S3 오프로딩 대응 (대용량 상태 데이터)
- Semantic Checkpoint Filtering (중요 의사결정 지점 필터링)
- Snapshot Recovery (체크포인트 기반 재실행)
"""

import os
import logging
import json
import asyncio
import uuid
import hashlib
from functools import partial
from typing import Dict, Any, List, Optional, Set
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

try:
    from src.common.aws_clients import get_dynamodb_resource, get_s3_client
except ImportError:
    try:
        from common.aws_clients import get_dynamodb_resource, get_s3_client
    except ImportError:
        def get_dynamodb_resource():
            return boto3.resource('dynamodb')
        def get_s3_client():
            return boto3.client('s3')

logger = logging.getLogger(__name__)

# ============================================================================
# 상수 정의
# ============================================================================

# GSI 이름 (notifications 테이블에 execution_id로 조회하기 위한 인덱스)
EXECUTION_ID_GSI = os.environ.get('NOTIFICATION_EXECUTION_GSI', 'execution-id-index')

# S3 버킷 (대용량 상태 데이터 오프로딩용)
STATE_BUCKET = os.environ.get('STATE_BUCKET', os.environ.get('SKELETON_S3_BUCKET', ''))

# 중요 이벤트 타입 (Semantic Filtering용)
IMPORTANT_EVENT_TYPES: Set[str] = {
    'workflow_started',
    'workflow_completed',
    'workflow_failed',
    'llm_response_received',
    'human_input_received',
    'condition_evaluated',
    'loop_iteration_start',
    'error_caught',
    'retry_attempted',
    'state_modified',
}


class CheckpointService:
    """
    체크포인트 관리 서비스
    
    기존 execution 및 notification 데이터를 활용하여
    Time Machine 디버깅을 위한 체크포인트 기능을 제공합니다.
    
    Features:
    - GSI 기반 query로 성능 최적화 (scan 사용 안 함)
    - S3 오프로딩된 대용량 상태 데이터 자동 로드
    - Semantic Checkpoint Filtering (중요 이벤트만 필터링)
    - Snapshot Recovery (체크포인트 기반 재실행)
    """
    
    def __init__(
        self,
        executions_table: Optional[str] = None,
        notifications_table: Optional[str] = None,
        state_bucket: Optional[str] = None
    ):
        """
        Args:
            executions_table: 실행 테이블 이름
            notifications_table: 알림 테이블 이름
            state_bucket: 상태 데이터 S3 버킷
        """
        self.executions_table_name = executions_table or os.environ.get(
            'EXECUTION_TABLE', 'executions'
        )
        self.notifications_table_name = notifications_table or os.environ.get(
            'NOTIFICATION_TABLE', 'notifications'
        )
        self.state_bucket = state_bucket or STATE_BUCKET
        self._executions_table = None
        self._notifications_table = None
        self._s3_client = None
    
    @property
    def executions_table(self):
        """지연 초기화된 실행 테이블"""
        if self._executions_table is None:
            dynamodb_resource = get_dynamodb_resource()
            self._executions_table = dynamodb_resource.Table(self.executions_table_name)
        return self._executions_table

    @property
    def notifications_table(self):
        """지연 초기화된 알림 테이블"""
        if self._notifications_table is None:
            dynamodb_resource = get_dynamodb_resource()
            self._notifications_table = dynamodb_resource.Table(self.notifications_table_name)
        return self._notifications_table

    @property
    def s3_client(self):
        """지연 초기화된 S3 클라이언트"""
        if self._s3_client is None:
            self._s3_client = get_s3_client()
        return self._s3_client

    # =========================================================================
    # 유틸리티 메서드
    # =========================================================================

    def _generate_checkpoint_id(
        self,
        timestamp: str,
        notification_id: Optional[str] = None
    ) -> str:
        """
        고유한 체크포인트 ID 생성
        
        동일 밀리초 내 여러 이벤트 발생 시 충돌 방지를 위해
        notification_id 또는 UUID를 결합합니다.
        
        Args:
            timestamp: 이벤트 타임스탬프
            notification_id: 알림 고유 ID (있는 경우)
            
        Returns:
            고유한 체크포인트 ID
        """
        if notification_id:
            # notification_id가 있으면 해시하여 사용
            hash_suffix = hashlib.sha256(notification_id.encode()).hexdigest()[:8]
            return f"cp_{timestamp}_{hash_suffix}"
        else:
            # 없으면 UUID 사용
            return f"cp_{timestamp}_{uuid.uuid4().hex[:8]}"

    async def _load_state_from_s3(self, s3_path: str) -> Dict[str, Any]:
        """
        S3에서 오프로딩된 상태 데이터 로드
        
        Args:
            s3_path: S3 경로 (s3://bucket/key 또는 key만)
            
        Returns:
            상태 데이터 딕셔너리
        """
        try:
            # s3:// 접두사 처리
            if s3_path.startswith('s3://'):
                parts = s3_path[5:].split('/', 1)
                bucket = parts[0]
                key = parts[1] if len(parts) > 1 else ''
            else:
                bucket = self.state_bucket
                key = s3_path
            
            if not bucket:
                logger.warning(f"No S3 bucket configured for state loading")
                return {}
            
            get_func = partial(
                self.s3_client.get_object,
                Bucket=bucket,
                Key=key
            )
            response = await asyncio.get_event_loop().run_in_executor(None, get_func)
            body = response['Body'].read().decode('utf-8')
            return json.loads(body)
            
        except ClientError as e:
            logger.error(f"Failed to load state from S3: {e}")
            return {}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in S3 state data: {e}")
            return {}

    def _is_important_checkpoint(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """
        중요한 체크포인트인지 판단 (Semantic Filtering)
        
        Args:
            event_type: 이벤트 타입
            payload: 이벤트 페이로드
            
        Returns:
            중요 체크포인트 여부
        """
        # 명시적으로 중요한 이벤트 타입
        if event_type in IMPORTANT_EVENT_TYPES:
            return True
        
        # 상태 변경이 있는 경우
        if payload.get('state_modified') or payload.get('step_function_state'):
            return True
        
        # 에러 또는 경고가 있는 경우
        status = payload.get('status', '').lower()
        if status in {'error', 'failed', 'warning', 'timeout'}:
            return True
        
        # LLM 관련 이벤트
        if 'llm' in event_type.lower() or 'model' in event_type.lower():
            return True
        
        return False

    async def get_execution_timeline(
        self,
        thread_id: str,
        include_state: bool = True,
        only_important: bool = False
    ) -> List[Dict[str, Any]]:
        """
        실행 타임라인 조회
        
        GSI를 활용한 query로 성능 최적화.
        기존 notification 데이터를 활용하여 타임라인을 생성합니다.
        
        Args:
            thread_id: 실행 스레드 ID (execution_id)
            include_state: 상태 정보 포함 여부
            only_important: 중요 체크포인트만 필터링 (Semantic Filtering)
            
        Returns:
            타임라인 항목 목록
        """
        try:
            from boto3.dynamodb.conditions import Key
            
            # GSI를 사용한 query (scan 대신)
            # execution_id를 파티션 키로 하는 GSI 활용
            query_func = partial(
                self.notifications_table.query,
                IndexName=EXECUTION_ID_GSI,
                KeyConditionExpression=Key('execution_id').eq(thread_id),
                ScanIndexForward=True,  # 시간순 정렬
                Limit=500
            )
            
            try:
                response = await asyncio.get_event_loop().run_in_executor(None, query_func)
            except ClientError as e:
                # GSI가 없는 경우 fallback (개발 환경용)
                if e.response['Error']['Code'] == 'ValidationException':
                    logger.warning(f"GSI '{EXECUTION_ID_GSI}' not found, falling back to scan")
                    response = await self._fallback_scan(thread_id)
                else:
                    raise
            
            items = response.get('Items', [])
            timeline = []
            
            for item in items:
                notification = item.get('notification', {})
                if isinstance(notification, str):
                    try:
                        notification = json.loads(notification)
                    except json.JSONDecodeError:
                        notification = {}
                
                payload = notification.get('payload', {})
                event_type = notification.get('type', 'execution_event')
                notification_id = item.get('notification_id') or item.get('id', '')
                timestamp = item.get('timestamp', '')
                
                # Semantic Filtering
                if only_important and not self._is_important_checkpoint(event_type, payload):
                    continue
                
                timeline_item = {
                    "checkpoint_id": self._generate_checkpoint_id(timestamp, notification_id),
                    "notification_id": notification_id,
                    "timestamp": timestamp,
                    "event_type": event_type,
                    "node_id": payload.get('current_step_label', payload.get('node_id', '')),
                    "status": payload.get('status', ''),
                    "message": payload.get('message', ''),
                    "is_important": self._is_important_checkpoint(event_type, payload),
                }
                
                # 상태 데이터 로드
                if include_state:
                    state_data = payload.get('step_function_state', {})
                    s3_path = payload.get('state_s3_path')
                    
                    # S3 오프로딩된 경우 로드
                    if s3_path and not state_data:
                        state_data = await self._load_state_from_s3(s3_path)
                    
                    timeline_item['state'] = state_data
                    timeline_item['state_s3_path'] = s3_path
                
                timeline.append(timeline_item)
            
            # 시간순 정렬 (이미 정렬되어 있지만 안전을 위해)
            timeline.sort(key=lambda x: x.get('timestamp', ''))
            
            return timeline
            
        except ClientError as e:
            logger.error(f"Failed to get execution timeline: {e}")
            return []

    async def _fallback_scan(self, thread_id: str) -> Dict[str, Any]:
        """
        GSI가 없는 경우 fallback scan (개발 환경용)
        
        WARNING: 프로덕션에서는 GSI 사용 필수
        """
        from boto3.dynamodb.conditions import Attr
        
        logger.warning("Using scan fallback - configure GSI for production!")
        
        scan_func = partial(
            self.notifications_table.scan,
            FilterExpression=Attr('execution_id').eq(thread_id),
            Limit=100
        )
        return await asyncio.get_event_loop().run_in_executor(None, scan_func)

    async def list_checkpoints(
        self,
        thread_id: str,
        limit: int = 50,
        only_important: bool = False
    ) -> List[Dict[str, Any]]:
        """
        체크포인트 목록 조회
        
        Args:
            thread_id: 실행 스레드 ID
            limit: 최대 조회 개수
            only_important: 중요 체크포인트만 필터링
            
        Returns:
            체크포인트 목록
        """
        try:
            # 타임라인에서 체크포인트 추출
            timeline = await self.get_execution_timeline(
                thread_id,
                include_state=False,
                only_important=only_important
            )
            
            checkpoints = []
            for item in timeline[:limit]:
                checkpoint = {
                    "checkpoint_id": item.get('checkpoint_id'),
                    "thread_id": thread_id,
                    "notification_id": item.get('notification_id'),
                    "created_at": item.get('timestamp'),
                    "node_id": item.get('node_id'),
                    "event_type": item.get('event_type'),
                    "status": item.get('status'),
                    "message": item.get('message', ''),
                    "is_important": item.get('is_important', False),
                    "has_state": item.get('state_s3_path') is not None or 'state' in item,
                }
                checkpoints.append(checkpoint)
            
            return checkpoints
            
        except Exception as e:
            logger.error(f"Failed to list checkpoints: {e}")
            return []

    async def get_checkpoint_detail(
        self,
        thread_id: str,
        checkpoint_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        체크포인트 상세 조회
        
        S3 오프로딩된 상태 데이터도 자동으로 로드합니다.
        
        Args:
            thread_id: 실행 스레드 ID
            checkpoint_id: 체크포인트 ID
            
        Returns:
            체크포인트 상세 정보
        """
        try:
            # 타임라인에서 해당 체크포인트 찾기
            timeline = await self.get_execution_timeline(thread_id, include_state=True)
            
            for item in timeline:
                if item.get('checkpoint_id') == checkpoint_id:
                    # 상태 데이터 확보
                    state_snapshot = item.get('state', {})
                    s3_path = item.get('state_s3_path')
                    
                    # S3에서 상태가 오프로딩된 경우 로드
                    if s3_path and not state_snapshot:
                        state_snapshot = await self._load_state_from_s3(s3_path)
                    
                    checkpoint_detail = {
                        "checkpoint_id": checkpoint_id,
                        "thread_id": thread_id,
                        "notification_id": item.get('notification_id'),
                        "created_at": item.get('timestamp'),
                        "node_id": item.get('node_id'),
                        "event_type": item.get('event_type'),
                        "status": item.get('status'),
                        "message": item.get('message', ''),
                        "is_important": item.get('is_important', False),
                        "state_snapshot": state_snapshot,
                        "state_s3_path": s3_path,
                        "execution_context": {
                            "workflow_id": state_snapshot.get('workflow_id'),
                            "owner_id": state_snapshot.get('owner_id'),
                            "started_at": state_snapshot.get('started_at'),
                        },
                        "metadata": {
                            "state_size_bytes": len(json.dumps(state_snapshot)) if state_snapshot else 0,
                            "is_s3_offloaded": bool(s3_path),
                        },
                    }
                    return checkpoint_detail
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get checkpoint detail: {e}")
            return None

    async def compare_checkpoints(
        self,
        thread_id: str,
        checkpoint_id_a: str,
        checkpoint_id_b: str
    ) -> Dict[str, Any]:
        """
        체크포인트 비교 (State Diff)
        
        두 체크포인트 간의 상태 변화를 분석하여
        UI에서 시각화하기 적합한 diff 형식으로 반환합니다.
        
        Args:
            thread_id: 실행 스레드 ID
            checkpoint_id_a: 첫 번째 체크포인트 ID (이전)
            checkpoint_id_b: 두 번째 체크포인트 ID (이후)
            
        Returns:
            비교 결과 (added, removed, modified 구분)
        """
        try:
            # 두 체크포인트 조회 (S3 오프로딩 자동 처리)
            checkpoint_a = await self.get_checkpoint_detail(thread_id, checkpoint_id_a)
            checkpoint_b = await self.get_checkpoint_detail(thread_id, checkpoint_id_b)
            
            if not checkpoint_a or not checkpoint_b:
                raise ValueError("One or both checkpoints not found")
            
            # 상태 비교
            state_a = checkpoint_a.get('state_snapshot', {})
            state_b = checkpoint_b.get('state_snapshot', {})
            
            # 재귀적 diff 계산
            diff_result = self._compute_deep_diff(state_a, state_b)
            
            comparison = {
                "checkpoint_a": {
                    "id": checkpoint_id_a,
                    "timestamp": checkpoint_a.get('created_at'),
                    "node_id": checkpoint_a.get('node_id'),
                },
                "checkpoint_b": {
                    "id": checkpoint_id_b,
                    "timestamp": checkpoint_b.get('created_at'),
                    "node_id": checkpoint_b.get('node_id'),
                },
                "summary": {
                    "added_count": len(diff_result['added']),
                    "removed_count": len(diff_result['removed']),
                    "modified_count": len(diff_result['modified']),
                },
                "state_diff": diff_result,
            }
            
            return comparison
            
        except Exception as e:
            logger.error(f"Failed to compare checkpoints: {e}")
            raise

    def _compute_deep_diff(
        self,
        state_a: Dict[str, Any],
        state_b: Dict[str, Any],
        path: str = ""
    ) -> Dict[str, Any]:
        """
        재귀적 상태 diff 계산
        
        Args:
            state_a: 이전 상태
            state_b: 이후 상태
            path: 현재 경로 (중첩된 키 표시용)
            
        Returns:
            {added: {...}, removed: {...}, modified: {...}}
        """
        added = {}
        removed = {}
        modified = {}
        
        keys_a = set(state_a.keys()) if isinstance(state_a, dict) else set()
        keys_b = set(state_b.keys()) if isinstance(state_b, dict) else set()
        
        # 추가된 키
        for key in keys_b - keys_a:
            full_path = f"{path}.{key}" if path else key
            added[full_path] = state_b[key]
        
        # 제거된 키
        for key in keys_a - keys_b:
            full_path = f"{path}.{key}" if path else key
            removed[full_path] = state_a[key]
        
        # 수정된 키 (재귀적 비교)
        for key in keys_a & keys_b:
            full_path = f"{path}.{key}" if path else key
            val_a = state_a[key]
            val_b = state_b[key]
            
            if val_a != val_b:
                # 둘 다 딕셔너리면 재귀적으로 비교
                if isinstance(val_a, dict) and isinstance(val_b, dict):
                    nested_diff = self._compute_deep_diff(val_a, val_b, full_path)
                    added.update(nested_diff['added'])
                    removed.update(nested_diff['removed'])
                    modified.update(nested_diff['modified'])
                else:
                    modified[full_path] = {
                        "from": val_a,
                        "to": val_b,
                        "type_changed": type(val_a).__name__ != type(val_b).__name__
                    }
        
        return {
            "added": added,
            "removed": removed,
            "modified": modified,
        }

    # =========================================================================
    # Snapshot Recovery (체크포인트 기반 재실행)
    # =========================================================================

    async def restore_from_checkpoint(
        self,
        thread_id: str,
        checkpoint_id: str,
        workflow_id: Optional[str] = None,
        owner_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        체크포인트에서 워크플로우 재실행
        
        특정 체크포인트의 상태를 기반으로 새로운 워크플로우 실행을 트리거합니다.
        
        Args:
            thread_id: 원본 실행 스레드 ID
            checkpoint_id: 복원할 체크포인트 ID
            workflow_id: 워크플로우 ID (없으면 체크포인트에서 추출)
            owner_id: 소유자 ID (없으면 체크포인트에서 추출)
            
        Returns:
            새 실행 정보 {new_execution_id, status, restored_from}
        """
        try:
            # 체크포인트 상세 조회
            checkpoint = await self.get_checkpoint_detail(thread_id, checkpoint_id)
            if not checkpoint:
                raise ValueError(f"Checkpoint not found: {checkpoint_id}")
            
            state_snapshot = checkpoint.get('state_snapshot', {})
            if not state_snapshot:
                raise ValueError(f"No state snapshot available for checkpoint: {checkpoint_id}")
            
            # 워크플로우/소유자 정보 추출
            exec_context = checkpoint.get('execution_context', {})
            wf_id = workflow_id or exec_context.get('workflow_id') or state_snapshot.get('workflow_id')
            o_id = owner_id or exec_context.get('owner_id') or state_snapshot.get('owner_id')
            
            if not wf_id:
                raise ValueError("Cannot determine workflow_id for restoration")
            
            # 새 실행 ID 생성
            new_execution_id = f"restored_{uuid.uuid4().hex[:12]}"
            
            # 복원된 상태 준비
            restored_state = {
                **state_snapshot,
                "execution_id": new_execution_id,
                "restored_from": {
                    "original_execution_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                    "checkpoint_timestamp": checkpoint.get('created_at'),
                    "restored_at": datetime.now(timezone.utc).isoformat(),
                },
                "is_restored": True,
            }
            
            # Step Functions 실행 트리거
            new_execution_arn = await self._trigger_step_function_execution(
                workflow_id=wf_id,
                execution_id=new_execution_id,
                initial_state=restored_state,
                owner_id=o_id
            )
            
            return {
                "status": "success",
                "new_execution_id": new_execution_id,
                "new_execution_arn": new_execution_arn,
                "workflow_id": wf_id,
                "owner_id": o_id,
                "restored_from": {
                    "original_execution_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                    "checkpoint_timestamp": checkpoint.get('created_at'),
                },
                "message": f"Workflow restored from checkpoint {checkpoint_id}",
            }
            
        except Exception as e:
            logger.error(f"Failed to restore from checkpoint: {e}")
            return {
                "status": "error",
                "error": str(e),
                "restored_from": {
                    "original_execution_id": thread_id,
                    "checkpoint_id": checkpoint_id,
                },
            }

    async def _trigger_step_function_execution(
        self,
        workflow_id: str,
        execution_id: str,
        initial_state: Dict[str, Any],
        owner_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Step Functions 실행 트리거
        
        Args:
            workflow_id: 워크플로우 ID
            execution_id: 새 실행 ID
            initial_state: 초기 상태
            owner_id: 소유자 ID
            
        Returns:
            새 실행 ARN
        """
        try:
            sfn_client = boto3.client('stepfunctions')
            
            # State Machine ARN 조회 (workflow_id로 매핑)
            state_machine_arn = os.environ.get(
                'STATE_MACHINE_ARN',
                f"arn:aws:states:{os.environ.get('AWS_REGION', 'us-east-1')}:"
                f"{os.environ.get('AWS_ACCOUNT_ID', '')}:stateMachine:{workflow_id}"
            )
            
            start_func = partial(
                sfn_client.start_execution,
                stateMachineArn=state_machine_arn,
                name=execution_id,
                input=json.dumps(initial_state, ensure_ascii=False, default=str)
            )
            
            response = await asyncio.get_event_loop().run_in_executor(None, start_func)
            
            return response.get('executionArn')
            
        except ClientError as e:
            logger.error(f"Failed to trigger Step Functions execution: {e}")
            raise


# ============================================================================
# Singleton 인스턴스
# ============================================================================

_checkpoint_service_instance: Optional[CheckpointService] = None


def get_checkpoint_service() -> CheckpointService:
    """CheckpointService 싱글톤 인스턴스 반환"""
    global _checkpoint_service_instance
    if _checkpoint_service_instance is None:
        _checkpoint_service_instance = CheckpointService()
    return _checkpoint_service_instance