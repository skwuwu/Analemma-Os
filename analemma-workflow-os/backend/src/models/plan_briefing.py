# -*- coding: utf-8 -*-
"""
Plan Briefing models for workflow preview feature.

이 모듈은 워크플로우 실행 전 미리보기를 생성하기 위한 
데이터 모델을 정의합니다.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import uuid


class RiskLevel(str, Enum):
    """위험 수준 열거형"""
    LOW = "low"         # 낮은 위험 - 읽기 전용 작업, 외부 영향 없음
    MEDIUM = "medium"   # 중간 위험 - 제한된 외부 영향, 되돌릴 수 있음
    HIGH = "high"       # 높은 위험 - 되돌릴 수 없는 외부 영향 (이메일 발송, 결제 등)


class PlanStep(BaseModel):
    """
    실행 계획의 각 단계
    
    워크플로우의 각 노드가 수행할 작업을 설명합니다.
    """
    step_number: int = Field(..., ge=1, description="단계 번호 (1부터 시작)")
    node_id: str = Field(..., description="노드 ID")
    node_name: str = Field(..., description="노드 표시 이름")
    node_type: str = Field(default="generic", description="노드 타입 (llm, hitp, etc.)")
    action_description: str = Field(
        ...,
        max_length=500,
        description="이 단계에서 수행할 작업 설명 (사용자 친화적)"
    )
    estimated_duration_seconds: int = Field(
        ...,
        ge=0,
        description="예상 소요 시간 (초)"
    )
    risk_level: RiskLevel = Field(
        default=RiskLevel.LOW,
        description="이 단계의 위험 수준"
    )
    risk_description: Optional[str] = Field(
        None,
        max_length=300,
        description="위험 사유 (MEDIUM/HIGH일 때만)"
    )
    
    # 예상 입출력
    expected_input_summary: Optional[str] = Field(
        None,
        max_length=200,
        description="예상 입력 데이터 요약"
    )
    expected_output_summary: Optional[str] = Field(
        None,
        max_length=200,
        description="예상 출력 데이터 요약"
    )
    
    # 외부 시스템 연동 여부
    has_external_side_effect: bool = Field(
        default=False,
        description="외부 시스템에 영향을 미치는지 (이메일, API 호출 등)"
    )
    external_systems: List[str] = Field(
        default_factory=list,
        description="연동되는 외부 시스템 목록"
    )
    
    # 선택적 실행 여부
    is_conditional: bool = Field(
        default=False,
        description="조건부 실행 여부 (분기 노드)"
    )
    condition_description: Optional[str] = Field(
        None,
        description="실행 조건 설명"
    )


class DraftResult(BaseModel):
    """
    예상 결과물 초안
    
    워크플로우 실행 결과로 생성될 것으로 예상되는 산출물입니다.
    """
    result_id: str = Field(
        default_factory=lambda: str(uuid.uuid4())[:8],
        description="결과 ID"
    )
    result_type: str = Field(
        ...,
        description="결과 유형 (email, document, data, notification, api_call)"
    )
    title: str = Field(..., max_length=200, description="결과물 제목/요약")
    content_preview: str = Field(
        ...,
        max_length=1000,
        description="내용 미리보기 (최대 1000자)"
    )
    recipients: Optional[List[str]] = Field(
        None,
        description="수신자 목록 (이메일, 알림 등)"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="추가 메타데이터"
    )
    
    # 위험 관련
    warnings: List[str] = Field(
        default_factory=list,
        description="이 결과물에 대한 경고 메시지"
    )
    requires_review: bool = Field(
        default=False,
        description="발송/실행 전 사용자 검토 필요 여부"
    )


class PlanBriefing(BaseModel):
    """
    전체 실행 계획 브리핑
    
    워크플로우 실행 전에 사용자에게 보여줄 미리보기 정보입니다.
    """
    # 식별자
    briefing_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="브리핑 ID"
    )
    workflow_id: str = Field(..., description="워크플로우 ID")
    workflow_name: str = Field(..., description="워크플로우 이름")
    
    # 계획 요약
    summary: str = Field(
        ...,
        max_length=500,
        description="1-2문장 요약"
    )
    total_steps: int = Field(..., ge=0, description="총 단계 수")
    estimated_total_duration_seconds: int = Field(
        ...,
        ge=0,
        description="예상 총 소요시간 (초)"
    )
    
    # 상세 계획
    steps: List[PlanStep] = Field(
        default_factory=list,
        description="실행 단계 목록"
    )
    
    # 예상 결과물
    draft_results: List[DraftResult] = Field(
        default_factory=list,
        description="예상 결과물 목록"
    )
    
    # 위험 분석
    overall_risk_level: RiskLevel = Field(
        default=RiskLevel.LOW,
        description="전체 위험 수준"
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="주의사항 목록"
    )
    requires_confirmation: bool = Field(
        default=False,
        description="사용자 명시적 승인 필요 여부"
    )
    confirmation_message: Optional[str] = Field(
        None,
        description="승인 요청 시 표시할 메시지"
    )
    
    # 메타데이터
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="브리핑 생성 시각"
    )
    confidence_score: float = Field(
        default=0.8,
        ge=0,
        le=1,
        description="예측 신뢰도 (0~1)"
    )
    
    # 확인 토큰 (실행 승인용)
    confirmation_token: Optional[str] = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="실행 승인 시 사용할 토큰"
    )
    token_expires_at: Optional[datetime] = Field(
        default=None,
        description="토큰 만료 시각 (기본 30분)"
    )

    class Config:
        """Pydantic 설정"""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

    def __init__(self, **data):
        super().__init__(**data)
        # 토큰 만료 시간 설정 (생성 후 30분)
        if self.token_expires_at is None:
            from datetime import timedelta
            self.token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
