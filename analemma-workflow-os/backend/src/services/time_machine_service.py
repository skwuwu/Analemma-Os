# -*- coding: utf-8 -*-
"""
Time Machine Service

워크플로우 실행의 롤백 및 분기 관리를 위한 서비스입니다.
기존 execution 데이터를 활용하여 Time Machine 기능을 제공합니다.
"""

import os
import logging
import json
import asyncio
import uuid
from functools import partial
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

try:
    from src.services.checkpoint_service import CheckpointService
    from src.common.aws_clients import get_dynamodb_resource
except ImportError:
    from src.checkpoint_service import CheckpointService
    from src.common.aws_clients import get_dynamodb_resource

logger = logging.getLogger(__name__)


class TimeMachineService:
    """
    Time Machine 서비스
    
    기존 execution 데이터를 활용하여 롤백 및 분기 관리 기능을 제공합니다.
    """
    
    def __init__(
        self,
        checkpoint_service: Optional[CheckpointService] = None,
        executions_table: Optional[str] = None
    ):
        """
        Args:
            checkpoint_service: CheckpointService 인스턴스
            executions_table: 실행 테이블 이름
        """
        self.checkpoint_service = checkpoint_service or CheckpointService()
        self.executions_table_name = executions_table or os.environ.get(
            'EXECUTION_TABLE', 'executions'
        )
        self._executions_table = None
    
    @property
    def executions_table(self):
        """지연 초기화된 실행 테이블"""
        if self._executions_table is None:
            dynamodb_resource = get_dynamodb_resource()
            self._executions_table = dynamodb_resource.Table(self.executions_table_name)
        return self._executions_table

    async def preview_rollback(
        self,
        rollback_request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        롤백 미리보기
        
        Args:
            rollback_request: 롤백 요청 정보
            
        Returns:
            롤백 미리보기 결과
        """
        try:
            thread_id = rollback_request.get('thread_id')
            target_checkpoint_id = rollback_request.get('target_checkpoint_id')
            
            if not thread_id or not target_checkpoint_id:
                raise ValueError("thread_id and target_checkpoint_id are required")
            
            # 대상 체크포인트 조회
            target_checkpoint = await self.checkpoint_service.get_checkpoint_detail(
                thread_id, target_checkpoint_id
            )
            
            if not target_checkpoint:
                raise ValueError("Target checkpoint not found")
            
            # 현재 상태와 비교
            current_checkpoints = await self.checkpoint_service.list_checkpoints(
                thread_id, limit=1
            )
            
            preview = {
                "rollback_id": str(uuid.uuid4()),
                "thread_id": thread_id,
                "target_checkpoint_id": target_checkpoint_id,
                "target_timestamp": target_checkpoint.get('created_at'),
                "target_node_id": target_checkpoint.get('node_id'),
                "estimated_impact": self._estimate_rollback_impact(
                    target_checkpoint, current_checkpoints[0] if current_checkpoints else None
                ),
                "affected_nodes": self._get_affected_nodes(thread_id, target_checkpoint_id),
                "data_changes": self._preview_data_changes(target_checkpoint),
                "warnings": self._generate_rollback_warnings(rollback_request),
                "requires_confirmation": True,
                "estimated_duration_seconds": 30,
            }
            
            return preview
            
        except Exception as e:
            logger.error(f"Failed to preview rollback: {e}")
            raise

    async def execute_rollback(
        self,
        rollback_request: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        롤백 실행 및 분기 생성
        
        현재는 미리보기만 제공하고, 실제 롤백은 향후 구현 예정입니다.
        
        Args:
            rollback_request: 롤백 요청 정보
            
        Returns:
            생성된 분기 정보
        """
        try:
            thread_id = rollback_request.get('thread_id')
            target_checkpoint_id = rollback_request.get('target_checkpoint_id')
            branch_name = rollback_request.get('branch_name', f"rollback-{int(datetime.now().timestamp())}")
            
            if not thread_id or not target_checkpoint_id:
                raise ValueError("thread_id and target_checkpoint_id are required")
            
            # 대상 체크포인트 조회
            target_checkpoint = await self.checkpoint_service.get_checkpoint_detail(
                thread_id, target_checkpoint_id
            )
            
            if not target_checkpoint:
                raise ValueError("Target checkpoint not found")
            
            # 새 분기 ID 생성 (실제 롤백은 향후 구현)
            branch_id = str(uuid.uuid4())
            new_thread_id = f"{thread_id}_branch_{branch_id[:8]}"
            
            # 분기 정보 (Mock)
            branch_info = {
                "branch_id": branch_id,
                "parent_thread_id": thread_id,
                "new_thread_id": new_thread_id,
                "branch_name": branch_name,
                "rollback_checkpoint_id": target_checkpoint_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "status": "simulated",  # 실제 롤백은 향후 구현
                "initial_state": target_checkpoint.get('state_snapshot', {}),
                "note": "Rollback simulation - actual execution requires Step Functions integration"
            }
            
            logger.info(f"Simulated rollback branch {branch_id} from src.checkpoint {target_checkpoint_id}")
            return branch_info
            
        except Exception as e:
            logger.error(f"Failed to execute rollback: {e}")
            raise

    async def get_branch_history(
        self,
        thread_id: str
    ) -> List[Dict[str, Any]]:
        """
        분기 히스토리 조회
        
        현재는 Mock 데이터를 반환합니다.
        
        Args:
            thread_id: 실행 스레드 ID
            
        Returns:
            분기 목록
        """
        try:
            # 실제로는 별도 분기 테이블에서 조회해야 함
            # 현재는 Mock 데이터 반환
            branches = [
                {
                    "branch_id": "main",
                    "branch_name": "main",
                    "created_at": "2024-01-01T00:00:00Z",
                    "status": "active",
                    "is_main": True,
                    "thread_id": thread_id,
                }
            ]
            
            return branches
            
        except Exception as e:
            logger.error(f"Failed to get branch history: {e}")
            return []

    async def get_rollback_suggestions(
        self,
        thread_id: str
    ) -> List[Dict[str, Any]]:
        """
        롤백 추천 지점 분석
        
        Args:
            thread_id: 실행 스레드 ID
            
        Returns:
            롤백 추천 목록
        """
        try:
            # 체크포인트 목록 조회
            checkpoints = await self.checkpoint_service.list_checkpoints(thread_id, limit=20)
            
            suggestions = []
            
            for checkpoint in checkpoints:
                # 추천 점수 계산 (간단한 휴리스틱)
                score = self._calculate_rollback_score(checkpoint)
                
                if score > 0.5:  # 임계값 이상만 추천
                    suggestion = {
                        "checkpoint_id": checkpoint.get('checkpoint_id'),
                        "timestamp": checkpoint.get('created_at'),
                        "node_id": checkpoint.get('node_id'),
                        "reason": self._generate_rollback_reason(checkpoint),
                        "confidence": score,
                        "estimated_impact": "low" if score < 0.7 else "medium",
                        "tags": self._generate_rollback_tags(checkpoint),
                    }
                    suggestions.append(suggestion)
            
            # 점수순 정렬
            suggestions.sort(key=lambda x: x['confidence'], reverse=True)
            
            return suggestions[:5]  # 상위 5개만 반환
            
        except Exception as e:
            logger.error(f"Failed to get rollback suggestions: {e}")
            return []

    def _estimate_rollback_impact(
        self,
        target_checkpoint: Dict[str, Any],
        current_checkpoint: Optional[Dict[str, Any]]
    ) -> str:
        """롤백 영향도 추정"""
        if not current_checkpoint:
            return "unknown"
        
        # 시간 차이 계산
        target_time = target_checkpoint.get('created_at', '')
        current_time = current_checkpoint.get('created_at', '')
        
        try:
            if target_time and current_time:
                # 간단한 시간 비교 (실제로는 더 정교한 분석 필요)
                return "medium"
            else:
                return "unknown"
        except Exception:
            return "unknown"

    def _get_affected_nodes(
        self,
        thread_id: str,
        target_checkpoint_id: str
    ) -> List[str]:
        """영향받는 노드 목록 계산"""
        # 실제로는 체크포인트 이후 실행된 노드들을 분석해야 함
        return ["node_after_checkpoint"]

    def _preview_data_changes(
        self,
        target_checkpoint: Dict[str, Any]
    ) -> Dict[str, Any]:
        """데이터 변경 미리보기"""
        state_snapshot = target_checkpoint.get('state_snapshot', {})
        
        return {
            "restored_variables": list(state_snapshot.keys())[:5],
            "variable_count": len(state_snapshot),
            "estimated_size_kb": len(json.dumps(state_snapshot)) / 1024 if state_snapshot else 0,
        }

    def _generate_rollback_warnings(
        self,
        rollback_request: Dict[str, Any]
    ) -> List[str]:
        """롤백 경고 생성"""
        warnings = []
        
        # 기본 경고
        warnings.append("롤백은 시뮬레이션 모드입니다. 실제 실행은 향후 지원 예정입니다.")
        
        # 조건부 경고
        if rollback_request.get('force', False):
            warnings.append("강제 롤백이 활성화되었습니다.")
        
        return warnings

    def _calculate_rollback_score(
        self,
        checkpoint: Dict[str, Any]
    ) -> float:
        """롤백 추천 점수 계산"""
        score = 0.5  # 기본 점수
        
        # 상태 기반 점수 조정
        status = checkpoint.get('status', '')
        if status in ['COMPLETED', 'SUCCEEDED']:
            score += 0.3
        elif status in ['FAILED', 'ERROR']:
            score += 0.4
        
        # 노드 ID 기반 점수 조정
        node_id = checkpoint.get('node_id', '')
        if node_id and len(node_id) > 0:
            score += 0.1
        
        return min(score, 1.0)

    def _generate_rollback_reason(
        self,
        checkpoint: Dict[str, Any]
    ) -> str:
        """롤백 추천 이유 생성"""
        status = checkpoint.get('status', '')
        node_id = checkpoint.get('node_id', 'Unknown')
        
        if status in ['COMPLETED', 'SUCCEEDED']:
            return f"'{node_id}' 단계가 성공적으로 완료된 안정적인 지점입니다."
        elif status in ['FAILED', 'ERROR']:
            return f"'{node_id}' 단계에서 오류가 발생하기 직전의 지점입니다."
        else:
            return f"'{node_id}' 단계 실행 지점입니다."

    def _generate_rollback_tags(
        self,
        checkpoint: Dict[str, Any]
    ) -> List[str]:
        """롤백 태그 생성"""
        tags = []
        
        status = checkpoint.get('status', '')
        if status in ['COMPLETED', 'SUCCEEDED']:
            tags.append('stable')
        elif status in ['FAILED', 'ERROR']:
            tags.append('error-point')
        
        node_id = checkpoint.get('node_id', '')
        if 'llm' in node_id.lower():
            tags.append('llm-node')
        elif 'api' in node_id.lower():
            tags.append('api-node')
        
        return tags