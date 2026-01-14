# -*- coding: utf-8 -*-
"""
Checkpoint Service

Time Machine 디버깅을 위한 체크포인트 관리 서비스입니다.
기존 execution 데이터를 활용하여 체크포인트 기능을 제공합니다.
"""

import os
import logging
import json
import asyncio
from functools import partial
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

try:
    from src.common.aws_clients import get_dynamodb_resource
except ImportError:
    from src.common.aws_clients import get_dynamodb_resource

logger = logging.getLogger(__name__)


class CheckpointService:
    """
    체크포인트 관리 서비스
    
    기존 execution 및 notification 데이터를 활용하여
    Time Machine 디버깅을 위한 체크포인트 기능을 제공합니다.
    """
    
    def __init__(
        self,
        executions_table: Optional[str] = None,
        notifications_table: Optional[str] = None
    ):
        """
        Args:
            executions_table: 실행 테이블 이름
            notifications_table: 알림 테이블 이름
        """
        self.executions_table_name = executions_table or os.environ.get(
            'EXECUTION_TABLE', 'executions'
        )
        self.notifications_table_name = notifications_table or os.environ.get(
            'NOTIFICATION_TABLE', 'notifications'
        )
        self._executions_table = None
        self._notifications_table = None
    
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

    async def get_execution_timeline(
        self,
        thread_id: str,
        include_state: bool = True
    ) -> List[Dict[str, Any]]:
        """
        실행 타임라인 조회
        
        기존 notification 데이터를 활용하여 타임라인을 생성합니다.
        
        Args:
            thread_id: 실행 스레드 ID (execution_id)
            include_state: 상태 정보 포함 여부
            
        Returns:
            타임라인 항목 목록
        """
        try:
            # notification 테이블에서 해당 실행의 이벤트들을 조회
            # 실제로는 execution_id로 필터링해야 함
            query_func = partial(
                self.notifications_table.scan,  # 임시로 scan 사용
                FilterExpression="contains(notification, :tid)",
                ExpressionAttributeValues={
                    ":tid": thread_id
                },
                Limit=100
            )
            response = await asyncio.get_event_loop().run_in_executor(None, query_func)
            
            items = response.get('Items', [])
            timeline = []
            
            for item in items:
                notification = item.get('notification', {})
                payload = notification.get('payload', {})
                
                if payload.get('execution_id') == thread_id:
                    timeline_item = {
                        "checkpoint_id": f"cp_{item.get('timestamp', '')}",
                        "timestamp": item.get('timestamp'),
                        "event_type": "execution_event",
                        "node_id": payload.get('current_step_label', ''),
                        "status": payload.get('status', ''),
                        "message": payload.get('message', ''),
                    }
                    
                    if include_state and 'step_function_state' in payload:
                        timeline_item['state'] = payload['step_function_state']
                    
                    timeline.append(timeline_item)
            
            # 시간순 정렬
            timeline.sort(key=lambda x: x.get('timestamp', ''))
            
            return timeline
            
        except ClientError as e:
            logger.error(f"Failed to get execution timeline: {e}")
            return []

    async def list_checkpoints(
        self,
        thread_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        체크포인트 목록 조회
        
        Args:
            thread_id: 실행 스레드 ID
            limit: 최대 조회 개수
            
        Returns:
            체크포인트 목록
        """
        try:
            # 타임라인에서 주요 체크포인트만 추출
            timeline = await self.get_execution_timeline(thread_id, include_state=False)
            
            checkpoints = []
            for item in timeline[:limit]:
                checkpoint = {
                    "checkpoint_id": item.get('checkpoint_id'),
                    "thread_id": thread_id,
                    "created_at": item.get('timestamp'),
                    "node_id": item.get('node_id'),
                    "status": item.get('status'),
                    "message": item.get('message', ''),
                    "has_state": 'state' in item,
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
                    checkpoint_detail = {
                        "checkpoint_id": checkpoint_id,
                        "thread_id": thread_id,
                        "created_at": item.get('timestamp'),
                        "node_id": item.get('node_id'),
                        "status": item.get('status'),
                        "message": item.get('message', ''),
                        "state_snapshot": item.get('state', {}),
                        "execution_context": {},
                        "metadata": {},
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
        체크포인트 비교
        
        Args:
            thread_id: 실행 스레드 ID
            checkpoint_id_a: 첫 번째 체크포인트 ID
            checkpoint_id_b: 두 번째 체크포인트 ID
            
        Returns:
            비교 결과
        """
        try:
            # 두 체크포인트 조회
            checkpoint_a = await self.get_checkpoint_detail(thread_id, checkpoint_id_a)
            checkpoint_b = await self.get_checkpoint_detail(thread_id, checkpoint_id_b)
            
            if not checkpoint_a or not checkpoint_b:
                raise ValueError("One or both checkpoints not found")
            
            # 상태 비교
            state_a = checkpoint_a.get('state_snapshot', {})
            state_b = checkpoint_b.get('state_snapshot', {})
            
            # 간단한 diff 계산
            added_keys = set(state_b.keys()) - set(state_a.keys())
            removed_keys = set(state_a.keys()) - set(state_b.keys())
            modified_keys = []
            
            for key in set(state_a.keys()) & set(state_b.keys()):
                if state_a[key] != state_b[key]:
                    modified_keys.append(key)
            
            comparison = {
                "checkpoint_a": checkpoint_id_a,
                "checkpoint_b": checkpoint_id_b,
                "added_keys": list(added_keys),
                "removed_keys": list(removed_keys),
                "modified_keys": modified_keys,
                "state_diff": {
                    "added": {k: state_b[k] for k in added_keys},
                    "removed": {k: state_a[k] for k in removed_keys},
                    "modified": {
                        k: {"from": state_a[k], "to": state_b[k]}
                        for k in modified_keys
                    }
                }
            }
            
            return comparison
            
        except Exception as e:
            logger.error(f"Failed to compare checkpoints: {e}")
            raise