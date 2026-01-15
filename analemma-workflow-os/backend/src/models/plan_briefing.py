# -*- coding: utf-8 -*-
"""
Plan Briefing models for workflow preview feature.

[v2.1] 개선사항:
1. Pydantic v2 @model_validator 사용 (__init__ 오버라이딩 제거)
2. ImpactScope Enum 추가 (Gemini 위험도 판단 가이드)
3. DraftResult 확장성 개선 (is_truncated, full_content_s3_key)

이 모듈은 워크플로우 실행 전 미리보기를 생성하기 위한 
데이터 모델을 정의합니다.
"""

from pydantic import BaseModel, Field, model_validator
from typing import List, Optional, Dict, Any, Set
from datetime import datetime, timezone, timedelta
from enum import Enum
import uuid


class RiskLevel(str, Enum):
    """위험 수준 열거형"""
    LOW = "low"         # 낮은 위험 - 읽기 전용 작업, 외부 영향 없음
    MEDIUM = "medium"   # 중간 위험 - 제한된 외부 영향, 되돌릴 수 있음
    HIGH = "high"       # 높은 위험 - 되돌릴 수 없는 외부 영향 (이메일 발송, 결제 등)


class ImpactScope(str, Enum):
    """
    [v2.1] 영향 범위 열거형
    
    Gemini가 RiskLevel을 결정할 때 참고하는 객관적 기준.
    
    Risk Mapping Guide:
    - READ_ONLY: LOW
    - DATABASE_WRITE, FILESYSTEM: MEDIUM (rollback 가능)
    - EMAIL, NOTIFICATION: MEDIUM/HIGH (발송 후 취소 불가)
    - PAYMENT, EXTERNAL_API: HIGH (되돌릴 수 없음)
    """
    READ_ONLY = "read_only"           # 읽기 전용 (DB 조회, 파일 읽기)
    DATABASE_WRITE = "database_write" # DB 쓰기 (롤백 가능)
    FILESYSTEM = "filesystem"         # 파일 시스템 변경
    EMAIL = "email"                   # 이메일 발송 (발송 후 취소 불가)
    NOTIFICATION = "notification"     # 알림 발송 (Slack, SMS 등)
    EXTERNAL_API = "external_api"     # 외부 API 호출 (부작용 가능)
    PAYMENT = "payment"               # 결제/금융 트랜잭션
    AUTHENTICATION = "authentication" # 인증/권한 변경
    SCHEDULED_TASK = "scheduled_task" # 예약 작업 등록


# [v2.1] 자동 위험도 매핑 (Gemini 참조용)
IMPACT_TO_RISK_MAPPING: Dict[ImpactScope, RiskLevel] = {
    ImpactScope.READ_ONLY: RiskLevel.LOW,
    ImpactScope.DATABASE_WRITE: RiskLevel.MEDIUM,
    ImpactScope.FILESYSTEM: RiskLevel.MEDIUM,
    ImpactScope.EMAIL: RiskLevel.HIGH,
    ImpactScope.NOTIFICATION: RiskLevel.MEDIUM,
    ImpactScope.EXTERNAL_API: RiskLevel.HIGH,
    ImpactScope.PAYMENT: RiskLevel.HIGH,
    ImpactScope.AUTHENTICATION: RiskLevel.HIGH,
    ImpactScope.SCHEDULED_TASK: RiskLevel.MEDIUM,
}


def _calculate_risk_from_impacts(impact_scopes: Set[ImpactScope]) -> RiskLevel:
    """
    [v2.1] 영향 범위에서 최대 위험도 계산.
    
    여러 ImpactScope 중 가장 높은 위험도를 반환.
    """
    if not impact_scopes:
        return RiskLevel.LOW
    
    risk_priority = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
    max_risk = RiskLevel.LOW
    
    for scope in impact_scopes:
        risk = IMPACT_TO_RISK_MAPPING.get(scope, RiskLevel.LOW)
        if risk_priority[risk] > risk_priority[max_risk]:
            max_risk = risk
    
    return max_risk


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
    
    # [v2.1] 영향 범위 (Gemini 위험도 판단 가이드)
    impact_scopes: Set[ImpactScope] = Field(
        default_factory=set,
        description="이 단계의 영향 범위 (DB, Email 등)"
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
    
    @model_validator(mode='after')
    def auto_calculate_risk(self) -> 'PlanStep':
        """
        [v2.1] impact_scopes에서 risk_level 자동 계산.
        
        명시적으로 risk_level을 설정하지 않았다면 impact_scopes에서 유추.
        """
        if self.impact_scopes and self.risk_level == RiskLevel.LOW:
            calculated_risk = _calculate_risk_from_impacts(self.impact_scopes)
            if calculated_risk != RiskLevel.LOW:
                object.__setattr__(self, 'risk_level', calculated_risk)
        
        # has_external_side_effect 자동 설정
        external_scopes = {
            ImpactScope.EMAIL, ImpactScope.NOTIFICATION, 
            ImpactScope.EXTERNAL_API, ImpactScope.PAYMENT
        }
        if self.impact_scopes & external_scopes:
            object.__setattr__(self, 'has_external_side_effect', True)
        
        return self


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
    
    # [v2.1] 확장성: 긴 콘텐츠 처리
    is_truncated: bool = Field(
        default=False,
        description="미리보기가 잘렸는지 여부 (UI '더 보기' 버튼용)"
    )
    full_content_s3_key: Optional[str] = Field(
        None,
        description="전체 콘텐츠가 저장된 S3 키 (is_truncated=True일 때)"
    )
    original_length: Optional[int] = Field(
        None,
        ge=0,
        description="원본 콘텐츠 길이 (바이트)"
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
    
    @model_validator(mode='after')
    def validate_truncation(self) -> 'DraftResult':
        """
        [v2.1] 잘림 상태 검증.
        
        content_preview가 1000자에 도달하면 is_truncated 자동 설정.
        """
        if len(self.content_preview) >= 1000 and not self.is_truncated:
            object.__setattr__(self, 'is_truncated', True)
        
        return self


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
    
    @model_validator(mode='after')
    def set_token_expiry_and_risk(self) -> 'PlanBriefing':
        """
        [v2.1] 토큰 만료 시간 및 전체 위험도 자동 설정.
        
        - token_expires_at: 생성 후 30분
        - overall_risk_level: 모든 step 중 최대 위험도
        - requires_confirmation: HIGH 위험 시 자동 활성화
        """
        # 토큰 만료 시간 설정
        if self.token_expires_at is None:
            object.__setattr__(
                self, 
                'token_expires_at', 
                datetime.now(timezone.utc) + timedelta(minutes=30)
            )
        
        # 전체 위험도 계산 (모든 step 중 최대)
        if self.steps:
            risk_priority = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
            max_risk = max(self.steps, key=lambda s: risk_priority[s.risk_level]).risk_level
            
            if risk_priority[max_risk] > risk_priority[self.overall_risk_level]:
                object.__setattr__(self, 'overall_risk_level', max_risk)
        
        # HIGH 위험 시 확인 필요
        if self.overall_risk_level == RiskLevel.HIGH and not self.requires_confirmation:
            object.__setattr__(self, 'requires_confirmation', True)
            if not self.confirmation_message:
                object.__setattr__(
                    self,
                    'confirmation_message',
                    "이 워크플로우는 되돌릴 수 없는 작업을 포함합니다. 계속하시겠습니까?"
                )
        
        return self
