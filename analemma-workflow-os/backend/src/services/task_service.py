# -*- coding: utf-8 -*-
"""
Task Service

Task Manager UI를 위한 비즈니스 로직 서비스입니다.
기술적인 실행 로그를 비즈니스 친화적인 Task 정보로 변환합니다.
"""

import os
import logging
import json
import asyncio
import time
from functools import partial
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

try:
    from src.common.dynamodb_utils import get_dynamodb_resource
    from src.models.task_context import (
        TaskContext,
        TaskStatus,
        ArtifactType,
        ArtifactPreview,
        AgentThought,
        convert_technical_status,
        get_friendly_error_message,
    )
except ImportError:
    from src.common.dynamodb_utils import get_dynamodb_resource
    from src.models.task_context import (
        TaskContext,
        TaskStatus,
        ArtifactType,
        ArtifactPreview,
        AgentThought,
        convert_technical_status,
        get_friendly_error_message,
    )

# 비즈니스 메트릭스 계산 모듈 임포트
try:
    from backend.services.business_metrics_calculator import (
        calculate_all_business_metrics,
    )
except ImportError:
    try:
        from src.services.business_metrics_calculator import (
            calculate_all_business_metrics,
        )
    except ImportError:
        # 폴백: 모듈 없으면 빈 딕셔너리 반환
        def calculate_all_business_metrics(*args, **kwargs):
            return {}

logger = logging.getLogger(__name__)


class TaskService:
    """
    Task Manager 서비스
    
    - 기술적 실행 로그를 Task 컨텍스트로 변환
    - Task 목록 조회 (필터링 지원)
    - Task 상세 정보 조회
    - 실시간 Task 컨텍스트 업데이트
    """
    
    def __init__(
        self,
        execution_table: Optional[str] = None,
        notification_table: Optional[str] = None,
    ):
        """
        Args:
            execution_table: 실행 상태 테이블 이름
            notification_table: 알림 테이블 이름
        """
        self.execution_table_name = execution_table or os.environ.get(
            'EXECUTION_TABLE', 'executions'
        )
        self.notification_table_name = notification_table or os.environ.get(
            'NOTIFICATION_TABLE', 'notifications'
        )
        self._execution_table = None
        self._notification_table = None
    
    @property
    def execution_table(self):
        """지연 초기화된 실행 테이블"""
        if self._execution_table is None:
            dynamodb_resource = get_dynamodb_resource()
            self._execution_table = dynamodb_resource.Table(self.execution_table_name)
        return self._execution_table

    @property
    def notification_table(self):
        """지연 초기화된 알림 테이블"""
        if self._notification_table is None:
            dynamodb_resource = get_dynamodb_resource()
            self._notification_table = dynamodb_resource.Table(self.notification_table_name)
        return self._notification_table

    async def get_tasks(
        self,
        owner_id: str,
        status_filter: Optional[str] = None,
        limit: int = 50,
        include_completed: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Task 목록 조회
        
        Args:
            owner_id: 사용자 ID
            status_filter: 상태 필터 (pending_approval, in_progress, completed 등)
            limit: 최대 조회 개수
            include_completed: 완료된 Task 포함 여부
            
        Returns:
            Task 요약 정보 목록
        """
        try:
            # 기존 notification 테이블에서 조회
            query_func = partial(
                self.notification_table.query,
                KeyConditionExpression="ownerId = :oid",
                ExpressionAttributeValues={
                    ":oid": owner_id
                },
                ScanIndexForward=False,  # 최신순
                Limit=limit
            )
            response = await asyncio.get_event_loop().run_in_executor(None, query_func)
            
            items = response.get('Items', [])
            tasks = []
            
            for item in items:
                task = self._convert_notification_to_task(item)
                if task:
                    # 필터 적용
                    if status_filter:
                        if task.get('status') != status_filter:
                            continue
                    if not include_completed and task.get('status') == 'completed':
                        continue
                    
                    tasks.append(task)
            
            return tasks[:limit]
            
        except ClientError as e:
            logger.error(f"Failed to get tasks: {e}")
            return []

    async def get_task_detail(
        self,
        task_id: str,
        owner_id: str,
        include_technical_logs: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Task 상세 정보 조회
        
        Args:
            task_id: Task ID (execution_id)
            owner_id: 사용자 ID
            include_technical_logs: 기술 로그 포함 여부 (권한에 따라)
            
        Returns:
            Task 상세 정보
        """
        try:
            # notification에서 해당 task 찾기
            query_func = partial(
                self.notification_table.query,
                KeyConditionExpression="ownerId = :oid",
                FilterExpression="contains(notification, :tid)",
                ExpressionAttributeValues={
                    ":oid": owner_id,
                    ":tid": task_id
                },
                ScanIndexForward=False,
                Limit=10
            )
            response = await asyncio.get_event_loop().run_in_executor(None, query_func)
            
            items = response.get('Items', [])
            if not items:
                return None
            
            # 가장 최신 항목 사용
            latest = items[0]
            task = self._convert_notification_to_task(latest, detailed=True)
            
            if include_technical_logs and task:
                task['technical_logs'] = self._extract_technical_logs(latest)
            
            return task
            
        except ClientError as e:
            logger.error(f"Failed to get task detail: {e}")
            return None

    def _convert_notification_to_task(
        self,
        notification: Dict[str, Any],
        detailed: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Notification 데이터를 Task 형식으로 변환
        
        Args:
            notification: DynamoDB notification 항목
            detailed: 상세 정보 포함 여부
            
        Returns:
            Task 정보 딕셔너리
        """
        try:
            payload = notification.get('notification', {}).get('payload', {})
            if not payload:
                return None
            
            execution_id = payload.get('execution_id', '')
            technical_status = payload.get('status', 'UNKNOWN')
            task_status = convert_technical_status(technical_status)
            
            # 진행률 계산
            current_segment = payload.get('current_segment', 0)
            total_segments = payload.get('total_segments', 1)
            progress = int((current_segment / max(total_segments, 1)) * 100)
            
            # 기본 Task 정보
            task = {
                "task_id": execution_id,
                "task_summary": self._generate_task_summary(payload),
                "agent_name": "AI Assistant",
                "status": task_status.value,
                "progress_percentage": progress,
                "current_step_name": payload.get('current_step_label', ''),
                "current_thought": self._generate_current_thought(payload),
                "is_interruption": technical_status == 'PAUSED_FOR_HITP',
                "started_at": self._format_timestamp(payload.get('start_time')),
                "updated_at": self._format_timestamp(notification.get('timestamp')),
                "workflow_name": payload.get('workflow_name', ''),
                "workflow_id": payload.get('workflowId', ''),
            }
            
            # 에러 정보 (사용자 친화적으로 변환)
            if task_status == TaskStatus.FAILED:
                error = payload.get('error', '')
                if error:
                    message, suggestion = get_friendly_error_message(str(error))
                    task['error_message'] = message
                    task['error_suggestion'] = suggestion
            
            # 비즈니스 가치 지표 추가 (새로운 계산 로직)
            business_metrics = calculate_all_business_metrics(
                payload=payload,
                execution_id=execution_id,
                progress=progress,
                status=technical_status
            )
            task.update(business_metrics)
            
            # 상세 정보
            if detailed:
                task['pending_decision'] = None
                if technical_status == 'PAUSED_FOR_HITP':
                    task['pending_decision'] = {
                        "question": "계속 진행하시겠습니까?",
                        "context": payload.get('message', ''),
                        "pre_hitp_output": payload.get('pre_hitp_output', {}),
                    }
                
                task['artifacts'] = self._extract_artifacts(payload)
                task['thought_history'] = self._extract_thought_history(payload)
                
                # 비용 정보
                step_function_state = payload.get('step_function_state', {})
                if step_function_state:
                    task['token_usage'] = step_function_state.get('token_usage', {})
            
            return task
            
        except Exception as e:
            logger.error(f"Failed to convert notification to task: {e}")
            return None

    def _generate_task_summary(self, payload: Dict[str, Any]) -> str:
        """워크플로우 정보로부터 Task 요약 생성"""
        workflow_name = payload.get('workflow_name', '')
        if workflow_name:
            return f"{workflow_name} 실행"
        
        workflow_id = payload.get('workflowId', '')
        if workflow_id:
            return f"워크플로우 {workflow_id[:8]}... 실행"
        
        return "AI 작업 진행 중"

    def _generate_current_thought(self, payload: Dict[str, Any]) -> str:
        """현재 상태에 따른 에이전트 사고 메시지 생성"""
        status = payload.get('status', '')
        current_step = payload.get('current_step_label', '')
        
        if status == 'PAUSED_FOR_HITP':
            return "사용자의 승인을 기다리고 있습니다."
        
        if status == 'RUNNING':
            if current_step:
                return f"{current_step} 단계를 처리하고 있습니다..."
            return "작업을 처리하고 있습니다..."
        
        if status == 'COMPLETE' or status == 'COMPLETED':
            return "작업이 완료되었습니다."
        
        if status == 'FAILED':
            return "작업 중 문제가 발생했습니다."
        
        return "작업을 준비하고 있습니다..."

    def _format_timestamp(self, ts: Any) -> Optional[str]:
        """타임스탬프를 ISO 형식으로 변환"""
        if not ts:
            return None
        try:
            if isinstance(ts, (int, float)):
                # 현재 시각 기준으로 밀리초/초 판단 (더 안정적인 휴리스틱)
                # 밀리초는 13자리, 초는 10자리
                if ts > 1e12:  # 13자리 이상이면 밀리초로 간주
                    return datetime.fromtimestamp(ts / 1000).isoformat()
                else:  # 10자리면 초 단위로 간주
                    return datetime.fromtimestamp(ts).isoformat()
            return str(ts)
        except Exception:
            return None

    def _extract_artifacts(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """실행 결과물 추출"""
        artifacts = []
        
        # step_function_state에서 output 추출
        step_state = payload.get('step_function_state', {})
        final_output = step_state.get('final_state', {}) or step_state.get('output', {})
        
        if isinstance(final_output, dict):
            for key, value in final_output.items():
                if key.startswith('_'):  # 내부 필드 제외
                    continue
                
                artifact = {
                    "artifact_id": f"output_{key}",
                    "artifact_type": "data",
                    "title": key,
                    "preview_content": str(value)[:200] if value else None,
                }
                artifacts.append(artifact)
        
        return artifacts

    def _extract_thought_history(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """사고 과정 히스토리 추출"""
        from src.models.task_context import THOUGHT_HISTORY_MAX_LENGTH
        
        thoughts = []
        
        # state_history에서 추출
        state_history = payload.get('state_history', [])
        if not state_history:
            step_state = payload.get('step_function_state', {})
            state_history = step_state.get('state_history', [])
        
        for entry in state_history[-THOUGHT_HISTORY_MAX_LENGTH:]:  # 상수 사용
            thought = {
                "thought_id": entry.get('id', str(hash(str(entry)))),
                "timestamp": entry.get('entered_at', ''),
                "thought_type": "progress",
                "message": self._state_to_thought_message(entry),
                "node_id": entry.get('node_id', entry.get('state_name', '')),
            }
            thoughts.append(thought)
        
        return thoughts

    def _state_to_thought_message(self, state_entry: Dict[str, Any]) -> str:
        """상태 엔트리를 사고 메시지로 변환"""
        state_name = state_entry.get('state_name', state_entry.get('name', ''))
        status = state_entry.get('status', '')
        
        if status == 'COMPLETED':
            return f"'{state_name}' 단계를 완료했습니다."
        elif status == 'RUNNING':
            return f"'{state_name}' 단계를 처리하고 있습니다..."
        elif status == 'FAILED':
            return f"'{state_name}' 단계에서 문제가 발생했습니다."
        
        return f"'{state_name}' 단계 진행 중"

    def _extract_technical_logs(self, notification: Dict[str, Any]) -> List[Dict[str, Any]]:
        """기술 로그 추출 (개발자 모드용)"""
        logs = []
        
        payload = notification.get('notification', {}).get('payload', {})
        step_state = payload.get('step_function_state', {})
        state_history = step_state.get('state_history', [])
        
        for entry in state_history:
            log = {
                "timestamp": entry.get('entered_at'),
                "node_id": entry.get('state_name', entry.get('node_id', '')),
                "input": entry.get('input'),
                "output": entry.get('output'),
                "duration": entry.get('duration', 0),
                "error": entry.get('error'),
            }
            logs.append(log)
        
        return logs

    def update_task_context(
        self,
        execution_id: str,
        thought: str,
        progress: Optional[int] = None,
        current_step: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Task 컨텍스트 업데이트 (노드 실행 중 호출)
        
        실시간으로 Task의 상태를 업데이트합니다.
        WebSocket과 DynamoDB에 동시에 반영됩니다.
        
        Args:
            execution_id: 실행 ID
            thought: 에이전트 사고 메시지
            progress: 진행률 (0-100)
            current_step: 현재 단계 이름
            
        Returns:
            WebSocket 페이로드
        """
        ws_payload = {
            "type": "task_update",
            "task_id": execution_id,
            "thought": thought,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        if progress is not None:
            ws_payload["progress"] = progress
        if current_step:
            ws_payload["current_step"] = current_step
        
        return ws_payload


class ContextAwareLogger:
    """
    컨텍스트 인식 로거
    
    기존 logging.Logger를 확장하여 Task 컨텍스트 업데이트를 함께 수행합니다.
    """
    
    def __init__(
        self,
        execution_id: str,
        task_service: Optional[TaskService] = None,
        websocket_callback: Optional[callable] = None,
    ):
        """
        Args:
            execution_id: 실행 ID
            task_service: TaskService 인스턴스
            websocket_callback: WebSocket 전송 콜백 함수
        """
        self.execution_id = execution_id
        self.task_service = task_service or TaskService()
        self.websocket_callback = websocket_callback
        self._logger = logging.getLogger(f"task.{execution_id[:8]}")
        self._progress = 0
        self._current_step = ""

    def report_thought(
        self,
        message: str,
        thought_type: str = "progress",
        progress: Optional[int] = None,
        current_step: Optional[str] = None,
    ) -> None:
        """
        에이전트 사고 보고
        
        사용자에게 보여줄 메시지를 기록하고 WebSocket으로 전송합니다.
        
        Args:
            message: 사용자에게 보여줄 메시지
            thought_type: 사고 유형 (progress, decision, warning, success, error)
            progress: 진행률 업데이트
            current_step: 현재 단계 업데이트
        """
        if progress is not None:
            self._progress = progress
        if current_step:
            self._current_step = current_step
        
        # 기술 로그도 기록
        self._logger.info(f"[{thought_type.upper()}] {message}")
        
        # WebSocket 페이로드 생성
        payload = self.task_service.update_task_context(
            self.execution_id,
            thought=message,
            progress=self._progress,
            current_step=self._current_step,
        )
        payload["thought_type"] = thought_type
        
        # WebSocket 전송
        if self.websocket_callback:
            try:
                self.websocket_callback(payload)
            except Exception as e:
                self._logger.error(f"Failed to send WebSocket message: {e}")

    def report_progress(self, percentage: int, step_name: str = "") -> None:
        """진행률 업데이트"""
        self.report_thought(
            f"{step_name} 처리 중..." if step_name else "작업 진행 중...",
            thought_type="progress",
            progress=percentage,
            current_step=step_name,
        )

    def report_decision(self, question: str, context: str = "") -> None:
        """의사결정 요청 보고"""
        message = f"결정이 필요합니다: {question}"
        if context:
            message += f" ({context})"
        self.report_thought(message, thought_type="decision")

    def report_success(self, message: str) -> None:
        """성공 보고"""
        self.report_thought(message, thought_type="success", progress=100)

    def report_error(self, error: str) -> None:
        """에러 보고 (사용자 친화적으로 변환)"""
        from src.models.task_context import get_friendly_error_message
        friendly_message, _ = get_friendly_error_message(error)
        self.report_thought(friendly_message, thought_type="error")
        self._logger.error(f"Original error: {error}")

    def report_warning(self, message: str) -> None:
        """경고 보고"""
        self.report_thought(message, thought_type="warning")
