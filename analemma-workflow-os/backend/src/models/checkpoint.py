# -*- coding: utf-8 -*-
"""
Checkpoint models for Time Machine Debugging feature.

이 모듈은 워크플로우 실행 중 상태를 저장하고 복원하기 위한 
체크포인트 관련 데이터 모델을 정의합니다.
"""

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timezone
from enum import Enum
import uuid
import os


class CheckpointStatus(str, Enum):
    """체크포인트 상태 열거형"""
    ACTIVE = "active"       # 현재 활성 체크포인트
    BRANCHED = "branched"   # 분기 시작점으로 사용됨
    ARCHIVED = "archived"   # 아카이브됨 (TTL 만료 대기)


class ExecutionCheckpoint(BaseModel):
    """
    실행 체크포인트 스키마 (LangGraph Checkpointer 확장)
    
    각 노드 실행 후 상태 스냅샷을 저장하여 이후 롤백/분기 가능하게 합니다.
    DynamoDB에 저장되며, TTL로 자동 정리됩니다.
    """
    
    # Primary Key
    pk: str = Field(..., description="thread#{thread_id}")
    sk: str = Field(..., description="checkpoint#{timestamp}#{checkpoint_id}")
    
    # Core identifiers
    thread_id: str = Field(..., description="실행 스레드 ID")
    checkpoint_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="체크포인트 고유 ID"
    )
    
    # 체크포인트 위치 정보
    node_id: str = Field(..., description="현재 노드 ID")
    node_name: str = Field(default="", description="노드 표시 이름")
    step_number: int = Field(..., ge=0, description="실행 단계 번호 (0-indexed)")
    
    # 상태 스냅샷
    state_snapshot: Union[Dict[str, Any], str] = Field(
        default_factory=dict,
        description="전체 statebag 스냅샷 (JSON 직렬화 가능해야 함, 압축 시 문자열)"
    )
    state_hash: str = Field(
        default="",
        description="상태 해시 (변경 감지용, SHA256 첫 16자)"
    )
    
    # 데이터 압축 여부
    is_compressed: bool = Field(
        default=False,
        description="state_snapshot 압축 여부"
    )
    
    # 실행 메타데이터
    execution_id: str = Field(..., description="실행 ID")
    workflow_id: str = Field(..., description="워크플로우 ID")
    user_id: str = Field(..., description="사용자 ID")
    
    # 분기 정보
    parent_checkpoint_id: Optional[str] = Field(
        None,
        description="분기 시 부모 체크포인트 ID"
    )
    branch_depth: int = Field(
        default=0,
        ge=0,
        description="분기 깊이 (원본=0, 분기=1, 분기의 분기=2, ...)"
    )
    status: CheckpointStatus = Field(
        default=CheckpointStatus.ACTIVE,
        description="체크포인트 상태"
    )
    
    # 타임스탬프
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="체크포인트 생성 시각"
    )
    
    # TTL (DynamoDB TTL - 환경변수에서 설정, 기본 30일)
    ttl: int = Field(
        default_factory=lambda: int(datetime.now(timezone.utc).timestamp()) + int(os.environ.get("CHECKPOINT_TTL_DAYS", "30")) * 24 * 3600,
        description="DynamoDB TTL 타임스탬프 (epoch seconds)"
    )

    class Config:
        """Pydantic 설정"""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

    def to_dynamodb_item(self) -> Dict[str, Any]:
        """DynamoDB 저장용 딕셔너리로 변환"""
        import json
        
        # state_snapshot 처리
        state_data = self.state_snapshot
        if not self.is_compressed and isinstance(state_data, dict):
            state_data = json.dumps(state_data)
            
        return {
            "pk": self.pk,
            "sk": self.sk,
            "thread_id": self.thread_id,
            "checkpoint_id": self.checkpoint_id,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "step_number": self.step_number,
            "state_snapshot": state_data,
            "state_hash": self.state_hash,
            "is_compressed": self.is_compressed,
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "user_id": self.user_id,
            "parent_checkpoint_id": self.parent_checkpoint_id,
            "branch_depth": self.branch_depth,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "ttl": self.ttl,
        }


class RollbackRequest(BaseModel):
    """
    롤백 요청 스키마
    
    사용자가 특정 체크포인트로 롤백하고 상태를 수정하여 
    새로운 분기 실행을 시작할 때 사용합니다.
    """
    thread_id: str = Field(..., description="원본 실행 스레드 ID")
    target_checkpoint_id: str = Field(..., description="롤백 대상 체크포인트 ID")
    state_modifications: Dict[str, Any] = Field(
        default_factory=dict,
        description="수정할 상태 값 (key-value 쌍). 지정된 키만 덮어씁니다."
    )
    reason: Optional[str] = Field(
        None,
        max_length=500,
        description="롤백 사유 (감사 로그용)"
    )
    
    # 미리보기 모드
    preview_only: bool = Field(
        default=False,
        description="True이면 실제 분기 생성 없이 미리보기만 반환"
    )


class BranchInfo(BaseModel):
    """
    분기 실행 정보
    
    롤백 및 분기 생성 후 반환되는 메타데이터입니다.
    """
    original_thread_id: str = Field(..., description="원본 스레드 ID")
    branched_thread_id: str = Field(..., description="새로 생성된 분기 스레드 ID")
    branch_point_checkpoint_id: str = Field(..., description="분기 시작점 체크포인트 ID")
    state_modifications: Dict[str, Any] = Field(
        default_factory=dict,
        description="적용된 상태 수정 사항"
    )
    branch_depth: int = Field(..., description="분기 깊이")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="분기 생성 시각"
    )
    
    # 실행 상태
    ready_to_resume: bool = Field(
        default=True,
        description="분기가 재개 준비 완료 상태인지"
    )
    resume_from_node: Optional[str] = Field(
        None,
        description="재개 시작 노드 ID"
    )
