# -*- coding: utf-8 -*-
"""
Task Context Models for Task Manager UI.

이 모듈은 기술적인 워크플로우 로그를 비즈니스 친화적인 
"Task" 개념으로 추상화하기 위한 데이터 모델을 정의합니다.
"""

from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from enum import Enum


class TaskStatus(str, Enum):
    """비즈니스 관점의 태스크 상태"""
    QUEUED = "queued"              # 대기 중
    IN_PROGRESS = "in_progress"    # 진행 중
    PENDING_APPROVAL = "pending_approval"  # 승인 대기
    COMPLETED = "completed"        # 완료
    FAILED = "failed"              # 실패
    CANCELLED = "cancelled"        # 취소됨


# 상수 정의
THOUGHT_HISTORY_MAX_LENGTH = 10


class ArtifactType(str, Enum):
    """생성된 결과물 유형"""
    TEXT = "text"           # 텍스트 (이메일 초안, 보고서 등)
    FILE = "file"           # 파일 (PDF, Excel 등)
    IMAGE = "image"         # 이미지
    DATA = "data"           # 데이터 (JSON, 테이블 등)
    LINK = "link"           # 외부 링크


class QuickFixType(str, Enum):
    """Quick Fix 액션 유형"""
    RETRY = "RETRY"               # 단순 재시도
    REDIRECT = "REDIRECT"         # 설정/인증 페이지로 이동
    SELF_HEALING = "SELF_HEALING" # AI가 자동 수정 후 재실행
    INPUT = "INPUT"               # 사용자 입력 보완 필요
    ESCALATE = "ESCALATE"         # 관리자 에스컬레이션


class QuickFix(BaseModel):
    """
    Quick Fix: 장애 유형별 동적 액션 매핑
    
    에러 발생 시 프론트엔드에 '해결 버튼의 기능'을 동적으로 제공합니다.
    """
    fix_type: QuickFixType = Field(..., description="액션 유형")
    label: str = Field(..., description="버튼 라벨 (예: '재시도하기')")
    action_id: str = Field(..., description="실행할 액션 ID")
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="액션 실행에 필요한 컨텍스트 (missing_fields, error_code 등)"
    )
    secondary_action: Optional[Dict[str, str]] = Field(
        None,
        description="보조 액션 (예: {'label': '자세히 보기', 'url': '...'})"
    )


class CorrectionDelta(BaseModel):
    """
    HITL 기반 지침 증류를 위한 수정 차이 데이터
    
    원문과 수정본의 차이를 저장하여 암묵적 학습에 활용합니다.
    """
    original_output_ref: str = Field(..., description="원본 출력 S3 참조")
    corrected_output_ref: str = Field(..., description="수정된 출력 S3 참조")
    diff_summary: Optional[str] = Field(None, description="차이점 요약")
    distilled_instructions: Optional[List[str]] = Field(
        None, description="추출된 지침 목록"
    )
    correction_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    node_id: Optional[str] = Field(None, description="수정된 노드 ID")


class ArtifactMetadata(BaseModel):
    """
    결과물 확장 메타데이터 (Outcome-First UI용)
    """
    content_type: str = Field(default="text/plain", description="MIME 타입")
    preview_text: Optional[str] = Field(None, max_length=300, description="미리보기 텍스트")
    word_count: Optional[int] = Field(None, description="단어 수 (텍스트용)")
    file_size_bytes: Optional[int] = Field(None, description="파일 크기")
    reasoning_path_ref: Optional[str] = Field(
        None, description="결과물에 매칭된 사고 과정 S3 참조"
    )
    logic_trace_id: Optional[str] = Field(
        None, description="이 결과물을 생성한 특정 시점의 히스토리 링크"
    )
    is_final: bool = Field(default=False, description="최종 결과물 여부")
    version: int = Field(default=1, description="결과물 버전")


class CollapsedHistory(BaseModel):
    """
    축약된 히스토리 (결과물 매니저용)
    
    상세 히스토리는 필요할 때만 로드합니다.
    """
    summary: str = Field(..., description="간략 요약 (예: '3개의 노드를 거쳐 완료')")
    node_count: int = Field(default=0, description="거친 노드 수")
    llm_call_count: int = Field(default=0, description="LLM 호출 횟수")
    total_duration_seconds: Optional[float] = Field(None, description="총 소요 시간")
    full_trace_ref: Optional[str] = Field(None, description="전체 히스토리 S3 참조")
    key_decisions: List[str] = Field(
        default_factory=list, description="핵심 의사결정 포인트 (최대 3개)"
    )


class ArtifactPreview(BaseModel):
    """
    생성된 결과물 미리보기
    
    Task가 생성한 중간/최종 결과물의 요약 정보입니다.
    """
    artifact_id: str = Field(..., description="결과물 고유 ID")
    artifact_type: ArtifactType = Field(..., description="결과물 유형")
    title: str = Field(..., description="결과물 제목")
    preview_content: Optional[str] = Field(
        None, 
        max_length=500,
        description="미리보기 내용 (텍스트의 경우 앞부분, 파일의 경우 설명)"
    )
    download_url: Optional[str] = Field(None, description="다운로드 URL (파일의 경우)")
    thumbnail_url: Optional[str] = Field(None, description="썸네일 URL (이미지의 경우)")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict, description="추가 메타데이터")
    # 확장 필드: Outcome-First UI
    extended_metadata: Optional[ArtifactMetadata] = Field(
        None, description="확장 메타데이터 (결과물 매니저용)"
    )
    logic_trace_id: Optional[str] = Field(
        None, description="이 결과물을 만든 특정 시점의 히스토리 링크"
    )


class AgentThought(BaseModel):
    """
    에이전트의 사고 과정 기록
    
    사용자에게 "AI가 무엇을 하고 있는지" 설명하는 로그입니다.
    """
    thought_id: str = Field(..., description="사고 기록 ID")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    thought_type: str = Field(
        default="progress",
        description="사고 유형: progress, decision, question, warning, success, error"
    )
    message: str = Field(..., description="사용자에게 보여줄 메시지 (자연어)")
    technical_detail: Optional[str] = Field(
        None,
        description="기술적 세부 정보 (개발자 모드에서만 표시)"
    )
    node_id: Optional[str] = Field(None, description="관련 노드 ID")
    is_important: bool = Field(default=False, description="중요 알림 여부")


class PendingDecision(BaseModel):
    """
    사용자 의사결정 대기 정보
    
    HITP(Human-in-the-loop) 상황에서 사용자에게 필요한 정보입니다.
    """
    decision_id: str = Field(..., description="의사결정 ID")
    question: str = Field(..., description="사용자에게 묻는 질문")
    context: str = Field(..., description="의사결정에 필요한 배경 정보")
    options: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="선택 가능한 옵션들"
    )
    default_option: Optional[str] = Field(None, description="기본 선택 옵션")
    timeout_seconds: Optional[int] = Field(None, description="응답 대기 시간 (초)")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskContext(BaseModel):
    """
    비즈니스 관점의 Task 컨텍스트
    
    기술적인 실행 로그를 사용자 친화적인 형태로 추상화합니다.
    UI의 Task Manager에서 이 정보를 기반으로 렌더링합니다.
    """
    # 기본 식별 정보
    task_id: str = Field(..., description="Task 고유 ID (execution_id와 동일)")
    
    # 비즈니스 메타데이터
    task_summary: str = Field(
        default="",
        max_length=200,
        description="업무 한 줄 요약 (예: '11월 미수금 정산 보고서 작성')"
    )
    agent_name: str = Field(
        default="AI Assistant",
        description="담당 에이전트 이름"
    )
    agent_avatar: Optional[str] = Field(
        None,
        description="에이전트 아바타 URL"
    )
    
    # 진행 상태
    status: TaskStatus = Field(
        default=TaskStatus.QUEUED,
        description="현재 태스크 상태"
    )
    progress_percentage: int = Field(
        default=0,
        ge=0,
        le=100,
        description="진행률 (0-100)"
    )
    current_step_name: str = Field(
        default="",
        description="현재 진행 중인 단계 이름"
    )
    
    # 실시간 사고 과정
    current_thought: str = Field(
        default="",
        max_length=500,
        description="에이전트의 현재 상태/생각 (실시간 업데이트)"
    )
    thought_history: List[AgentThought] = Field(
        default_factory=list,
        description="사고 과정 히스토리 (최신 10개만 유지)"
    )
    
    # 의사결정 대기
    pending_decision: Optional[PendingDecision] = Field(
        None,
        description="사용자 의사결정 대기 정보 (HITP 상태일 때)"
    )
    
    # 결과물
    artifacts: List[ArtifactPreview] = Field(
        default_factory=list,
        description="생성된 결과물 목록"
    )
    
    # 비용 및 리소스
    estimated_cost: Optional[float] = Field(
        None,
        ge=0,
        description="예상 비용 (USD)"
    )
    actual_cost: Optional[float] = Field(
        None,
        ge=0,
        description="실제 사용 비용 (USD)"
    )
    token_usage: Optional[Dict[str, int]] = Field(
        None,
        description="토큰 사용량 {'input': N, 'output': M}"
    )
    
    # 타임스탬프
    started_at: Optional[datetime] = Field(None, description="시작 시각")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="마지막 업데이트")
    completed_at: Optional[datetime] = Field(None, description="완료 시각")
    
    # 에러 정보 (사용자 친화적)
    error_message: Optional[str] = Field(
        None,
        description="에러 발생 시 사용자 친화적 메시지"
    )
    error_suggestion: Optional[str] = Field(
        None,
        description="에러 해결을 위한 제안"
    )
    
    # Quick Fix: 동적 복구 액션
    quick_fix: Optional[QuickFix] = Field(
        None,
        description="장애 복구를 위한 동적 액션 정보"
    )
    
    # HITL 지침 증류용 수정 데이터
    correction_delta: Optional[CorrectionDelta] = Field(
        None,
        description="원문과 수정본의 차이 데이터 (HITL 학습용)"
    )
    
    # 결과물 매니저: 축약된 히스토리
    collapsed_history: Optional[CollapsedHistory] = Field(
        None,
        description="결과물 중심 뷰를 위한 축약된 히스토리"
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

    def add_thought(self, message: str, thought_type: str = "progress", **kwargs) -> None:
        """사고 기록 추가 (최대 10개 유지)"""
        import uuid
        thought = AgentThought(
            thought_id=str(uuid.uuid4()),
            message=message,
            thought_type=thought_type,
            **kwargs
        )
        self.thought_history.append(thought)
        self.current_thought = message
        self.updated_at = datetime.now(timezone.utc)
        
        # 최대 10개 유지
        if len(self.thought_history) > THOUGHT_HISTORY_MAX_LENGTH:
            self.thought_history = self.thought_history[-THOUGHT_HISTORY_MAX_LENGTH:]

    def add_artifact(self, artifact: ArtifactPreview) -> None:
        """결과물 추가"""
        self.artifacts.append(artifact)
        self.updated_at = datetime.now(timezone.utc)

    def set_pending_decision(self, question: str, context: str, options: List[Dict] = None) -> None:
        """의사결정 대기 상태 설정"""
        import uuid
        self.pending_decision = PendingDecision(
            decision_id=str(uuid.uuid4()),
            question=question,
            context=context,
            options=options or []
        )
        self.status = TaskStatus.PENDING_APPROVAL
        self.updated_at = datetime.now(timezone.utc)

    def clear_pending_decision(self) -> None:
        """의사결정 대기 상태 해제"""
        self.pending_decision = None
        if self.status == TaskStatus.PENDING_APPROVAL:
            self.status = TaskStatus.IN_PROGRESS
        self.updated_at = datetime.now(timezone.utc)

    def to_websocket_payload(self) -> Dict[str, Any]:
        """WebSocket 전송용 간소화된 페이로드 생성"""
        return {
            "task_id": self.task_id,
            "display_status": self._get_display_status(),
            "thought": self.current_thought,
            "progress": self.progress_percentage,
            "current_step": self.current_step_name,
            "is_interruption": self.pending_decision is not None,
            "artifacts_count": len(self.artifacts),
            "agent_name": self.agent_name,
            "updated_at": self.updated_at.isoformat(),
        }

    def _get_display_status(self) -> str:
        """사용자 친화적 상태 문자열 반환"""
        status_map = {
            TaskStatus.QUEUED: "대기 중",
            TaskStatus.IN_PROGRESS: "진행 중",
            TaskStatus.PENDING_APPROVAL: "승인 대기",
            TaskStatus.COMPLETED: "완료",
            TaskStatus.FAILED: "실패",
            TaskStatus.CANCELLED: "취소됨",
        }
        return status_map.get(self.status, str(self.status.value))


# 기술적 상태를 비즈니스 상태로 매핑
TECHNICAL_TO_TASK_STATUS = {
    "STARTED": TaskStatus.IN_PROGRESS,
    "RUNNING": TaskStatus.IN_PROGRESS,
    "IN_PROGRESS": TaskStatus.IN_PROGRESS,
    "PAUSED_FOR_HITP": TaskStatus.PENDING_APPROVAL,
    "WAITING_FOR_INPUT": TaskStatus.PENDING_APPROVAL,
    "COMPLETE": TaskStatus.COMPLETED,
    "COMPLETED": TaskStatus.COMPLETED,
    "SUCCEEDED": TaskStatus.COMPLETED,
    "FAILED": TaskStatus.FAILED,
    "ERROR": TaskStatus.FAILED,
    "TIMED_OUT": TaskStatus.FAILED,
    "CANCELLED": TaskStatus.CANCELLED,
    "ABORTED": TaskStatus.CANCELLED,
}


def convert_technical_status(technical_status: str) -> TaskStatus:
    """기술적 상태 문자열을 TaskStatus로 변환"""
    return TECHNICAL_TO_TASK_STATUS.get(
        technical_status.upper(),
        TaskStatus.IN_PROGRESS
    )


# 에러 메시지 매핑 (Error-to-Speech)
ERROR_MESSAGE_MAP = {
    "500": ("서버에 일시적인 문제가 발생했습니다.", "잠시 후 다시 시도해주세요."),
    "503": ("서비스가 일시적으로 이용 불가합니다.", "몇 분 후 다시 시도해주세요."),
    "504": ("요청 처리 시간이 초과되었습니다.", "작업을 분할하거나 잠시 후 재시도해주세요."),
    "401": ("인증이 만료되었습니다.", "다시 로그인해주세요."),
    "403": ("접근 권한이 없습니다.", "관리자에게 문의해주세요."),
    "429": ("요청이 너무 많습니다.", "잠시 후 다시 시도해주세요."),
    "timeout": ("연결 시간이 초과되었습니다.", "네트워크 상태를 확인해주세요."),
    "connection": ("연결할 수 없습니다.", "네트워크 연결을 확인해주세요."),
}

# Quick Fix 액션 매핑: 에러 코드별 동적 액션 정의
QUICK_FIX_MAP: Dict[str, Dict[str, Any]] = {
    "500": {
        "fix_type": QuickFixType.RETRY,
        "label": "재시도하기",
        "action_id": "lambda_retry",
    },
    "503": {
        "fix_type": QuickFixType.RETRY,
        "label": "재시도하기",
        "action_id": "lambda_retry",
    },
    "504": {
        "fix_type": QuickFixType.SELF_HEALING,
        "label": "작업 분할 후 재실행",
        "action_id": "split_and_retry",
    },
    "401": {
        "fix_type": QuickFixType.REDIRECT,
        "label": "다시 로그인",
        "action_id": "auth_redirect",
        "context": {"redirect_url": "/login"},
    },
    "403": {
        "fix_type": QuickFixType.ESCALATE,
        "label": "관리자에게 문의",
        "action_id": "escalate_to_admin",
    },
    "429": {
        "fix_type": QuickFixType.RETRY,
        "label": "1분 후 재시도",
        "action_id": "delayed_retry",
        "context": {"delay_seconds": 60},
    },
    "validation": {
        "fix_type": QuickFixType.INPUT,
        "label": "데이터 보완하기",
        "action_id": "request_input",
    },
    "timeout": {
        "fix_type": QuickFixType.RETRY,
        "label": "재시도하기",
        "action_id": "lambda_retry",
    },
    "llm_error": {
        "fix_type": QuickFixType.SELF_HEALING,
        "label": "AI가 다시 작성",
        "action_id": "node_retry_with_error_context",
    },
    "schema_error": {
        "fix_type": QuickFixType.SELF_HEALING,
        "label": "자동 수정 후 재실행",
        "action_id": "auto_fix_schema",
    },
}


def get_friendly_error_message(error: str, execution_id: str = None, node_id: str = None) -> tuple[str, str, Optional[QuickFix]]:
    """
    기술적 에러를 사용자 친화적 메시지로 변환하고 Quick Fix 액션 생성
    
    Args:
        error: 에러 메시지 또는 코드
        execution_id: 실행 ID (Quick Fix 컨텍스트용)
        node_id: 노드 ID (Quick Fix 컨텍스트용)
    
    Returns:
        (error_message, error_suggestion, quick_fix)
    """
    error_lower = error.lower()
    quick_fix = None
    matched_key = None
    
    # 에러 유형 매칭
    for key in ERROR_MESSAGE_MAP:
        if key in error_lower:
            matched_key = key
            break
    
    # 추가 에러 유형 체크
    if not matched_key:
        if "validation" in error_lower or "schema" in error_lower:
            matched_key = "validation"
        elif "llm" in error_lower or "bedrock" in error_lower or "anthropic" in error_lower:
            matched_key = "llm_error"
        elif "pydantic" in error_lower or "json" in error_lower:
            matched_key = "schema_error"
    
    # 메시지 및 제안 가져오기
    if matched_key and matched_key in ERROR_MESSAGE_MAP:
        message, suggestion = ERROR_MESSAGE_MAP[matched_key]
    else:
        message = "작업 중 문제가 발생했습니다."
        suggestion = "잠시 후 다시 시도하거나 설정을 확인해주세요."
    
    # Quick Fix 생성
    if matched_key and matched_key in QUICK_FIX_MAP:
        fix_config = QUICK_FIX_MAP[matched_key]
        context = fix_config.get("context", {}).copy()
        
        # 컨텍스트에 실행 정보 추가
        if execution_id:
            context["execution_id"] = execution_id
        if node_id:
            context["node_id"] = node_id
        context["error_code"] = matched_key
        context["original_error"] = error[:200]  # 원본 에러 (최대 200자)
        
        quick_fix = QuickFix(
            fix_type=fix_config["fix_type"],
            label=fix_config["label"],
            action_id=fix_config["action_id"],
            context=context,
        )
    else:
        # 기본 Quick Fix: 재시도
        quick_fix = QuickFix(
            fix_type=QuickFixType.RETRY,
            label="재시도하기",
            action_id="lambda_retry",
            context={
                "execution_id": execution_id,
                "node_id": node_id,
                "error_code": "unknown",
            }
        )
    
    return (message, suggestion, quick_fix)
