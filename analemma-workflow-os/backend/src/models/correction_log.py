"""
지능형 지침 증류기 - 수정 로그 데이터 모델

주요 개선사항:
1. 멱등성 보장을 위한 SK 생성 로직 강화
2. metadata_signature를 활용한 충돌 방지 로직
3. 벡터 검색 인덱싱 필드 추가
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from enum import Enum
import uuid
import hashlib
import json

class ToneEnum(str, Enum):
    """톤 메타데이터 열거형"""
    FORMAL = "formal"
    CASUAL = "casual"
    FRIENDLY = "friendly"
    PROFESSIONAL = "professional"
    URGENT = "urgent"

class LengthEnum(str, Enum):
    """길이 메타데이터 열거형"""
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"
    DETAILED = "detailed"

class FormalityEnum(str, Enum):
    """격식 메타데이터 열거형"""
    FORMAL = "formal"
    INFORMAL = "informal"
    NEUTRAL = "neutral"

class StyleEnum(str, Enum):
    """스타일 메타데이터 열거형"""
    DIRECT = "direct"
    DIPLOMATIC = "diplomatic"
    TECHNICAL = "technical"
    CONVERSATIONAL = "conversational"

class VectorSyncStatus(str, Enum):
    """벡터 동기화 상태"""
    PENDING = "pending"
    SUCCESS = "success" 
    FAILED = "failed"
    RETRY = "retry"

class CorrectionType(str, Enum):
    """수정 타입 분류"""
    TONE = "tone"
    FORMAT = "format"
    CONTENT = "content"
    LOGIC = "logic"
    STYLE = "style"

class TaskCategory(str, Enum):
    """태스크 카테고리"""
    EMAIL = "email"
    SQL = "sql"
    DOCUMENT = "document"
    API = "api"
    WORKFLOW = "workflow"
    ANALYSIS = "analysis"

class CorrectionLog(BaseModel):
    """사용자 수정 로그 스키마"""
    
    # Primary Key
    pk: str = Field(..., description="user#{user_id}")
    sk: Optional[str] = Field(None, description="correction#{timestamp}#{uuid}")
    
    # 핵심 식별자
    user_id: str
    workflow_id: str
    node_id: str
    task_category: TaskCategory
    
    # 수정 전후 데이터
    original_input: str = Field(..., description="원본 입력")
    agent_output: str = Field(..., description="에이전트의 출력")
    user_correction: str = Field(..., description="사용자가 수정한 출력")
    
    # 객관적 메트릭 (mood 대신)
    edit_distance: int = Field(..., ge=0, description="편집 거리")
    correction_time_seconds: int = Field(..., ge=0, description="수정 소요 시간")
    user_confirmed_valuable: Optional[bool] = Field(None, description="사용자 명시적 확인")
    
    # 수정 분류
    correction_type: Optional[CorrectionType] = None
    
    # 구조화된 메타데이터 (충돌 감지용)
    extracted_metadata: Dict[str, str] = Field(default_factory=dict)
    # 예: {"tone": "casual", "length": "short", "formality": "informal"}
    
    # 컨텍스트 격리
    node_type: str = Field(..., description="llm_operator|api_call|data_transform")
    workflow_domain: str = Field(..., description="sales|marketing|support|finance")
    context_scope: str = Field(..., description="global|domain|task|node")
    applicable_contexts: List[str] = Field(default_factory=list)
    
    # 품질 평가 결과
    is_valuable: Optional[bool] = None
    quality_confidence: Optional[float] = Field(None, ge=0, le=1)
    quality_reason: Optional[str] = None
    correction_ratio: Optional[float] = Field(None, ge=0, le=1, description="수정 비율 (edit_distance/original_length)")
    
    # 벡터 검색용 (개선사항 #3)
    embedding_id: Optional[str] = None
    vector_sync_status: VectorSyncStatus = Field(default=VectorSyncStatus.PENDING, description="벡터 DB 동기화 상태")
    vector_sync_error: Optional[str] = Field(None, description="벡터 동기화 실패 시 에러 메시지")
    vector_sync_attempts: int = Field(default=0, description="벡터 동기화 시도 횟수")
    last_vector_sync_attempt: Optional[datetime] = Field(None, description="마지막 벡터 동기화 시도 시간")
    
    # 증류 상태
    distilled: bool = False
    distilled_instruction_id: Optional[str] = None
    
    # GSI용 키들
    gsi1_pk: Optional[str] = Field(None, description="task#{task_category}")
    gsi1_sk: Optional[str] = Field(None, description="user#{user_id}#{timestamp}")
    gsi2_pk: Optional[str] = Field(None, description="user#{user_id}")
    gsi2_sk: Optional[str] = Field(None, description="timestamp#{task_category}")
    
    # 타임스탬프
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # TTL (90일 보관)
    ttl: Optional[int] = None
    
    def __init__(self, **data):
        super().__init__(**data)
        
        # 개선사항 #1: 멱등성 보장을 위한 SK 생성 로직 강화
        if not self.sk:
            self.sk = self._generate_idempotent_sk()
        
        if not self.gsi1_pk:
            self.gsi1_pk = f"task#{self.task_category.value}"
        
        if not self.gsi1_sk:
            timestamp = self.created_at.isoformat()
            self.gsi1_sk = f"user#{self.user_id}#{timestamp}"
        
        if not self.gsi2_pk:
            self.gsi2_pk = f"user#{self.user_id}"
        
        if not self.gsi2_sk:
            timestamp = self.created_at.isoformat()
            self.gsi2_sk = f"timestamp#{timestamp}#{self.task_category.value}"
        
        # TTL 설정 (90일)
        if not self.ttl:
            import time
            self.ttl = int(time.time()) + (90 * 24 * 3600)
    
    def _generate_idempotent_sk(self) -> str:
        """
        멱등성 보장을 위한 SK 생성
        
        동일한 수정 내용에 대해서는 동일한 SK를 생성하여
        네트워크 오류로 인한 재시도 시 중복 데이터 방지
        """
        # 수정 내용의 핵심 요소들로 해시 생성
        content_hash_input = {
            "user_id": self.user_id,
            "workflow_id": self.workflow_id,
            "node_id": self.node_id,
            "original_input": self.original_input.strip(),
            "agent_output": self.agent_output.strip(),
            "user_correction": self.user_correction.strip(),
            "task_category": self.task_category.value
        }
        
        # JSON 직렬화 후 해시 생성 (키 순서 보장)
        content_json = json.dumps(content_hash_input, sort_keys=True, ensure_ascii=False)
        content_hash = hashlib.sha256(content_json.encode('utf-8')).hexdigest()[:16]
        
        # 타임스탬프는 일자 단위로만 포함 (같은 날 같은 수정은 동일 SK)
        date_str = self.created_at.strftime('%Y-%m-%d')
        
        return f"correction#{date_str}#{content_hash}"
    
    def get_content_fingerprint(self) -> str:
        """수정 내용의 지문 생성 (중복 감지용)"""
        return self._generate_idempotent_sk().split('#')[-1]  # 해시 부분만 반환

class DistilledInstruction(BaseModel):
    """증류된 시스템 지침 스키마"""
    
    pk: str = Field(..., description="user#{user_id}")
    sk: Optional[str] = Field(None, description="instruction#{category}#{uuid}")
    
    user_id: str
    category: CorrectionType
    context_scope: str = Field(..., description="global|domain|task|node")
    
    # 증류된 지침
    instruction: str = Field(..., description="한 줄 요약된 지침")
    confidence: float = Field(..., ge=0, le=1)
    
    # 근거
    source_correction_ids: List[str]
    pattern_description: str
    
    # 개선사항 #2: metadata_signature를 활용한 충돌 방지 로직
    metadata_signature: Dict[str, str] = Field(default_factory=dict)
    signature_hash: Optional[str] = Field(None, description="메타데이터 시그니처 해시")
    
    # 활성화 상태
    is_active: bool = True
    version: int = 1
    superseded_by: Optional[str] = Field(None, description="이 지침을 대체한 새 지침의 ID")
    
    # 적용 범위
    applicable_task_categories: List[TaskCategory] = Field(default_factory=list)
    applicable_node_types: List[str] = Field(default_factory=list)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    def __init__(self, **data):
        super().__init__(**data)
        
        # SK 자동 생성
        if not self.sk:
            import uuid
            self.sk = f"instruction#{self.category.value}#{uuid.uuid4().hex[:8]}"
        
        # 메타데이터 시그니처 해시 자동 생성
        if not self.signature_hash and self.metadata_signature:
            self.signature_hash = self._generate_signature_hash()
    
    def _generate_signature_hash(self) -> str:
        """메타데이터 시그니처 해시 생성"""
        if not self.metadata_signature:
            return ""
        
        # 키-값 쌍을 정렬하여 일관된 해시 생성
        signature_json = json.dumps(self.metadata_signature, sort_keys=True)
        return hashlib.sha256(signature_json.encode('utf-8')).hexdigest()[:12]
    
    def has_conflicting_signature(self, other_signature: Dict[str, str]) -> bool:
        """
        다른 지침과 메타데이터 시그니처 충돌 여부 확인
        
        예: 기존 {"tone": "formal"} vs 새로운 {"tone": "casual"}
        """
        if not self.metadata_signature or not other_signature:
            return False
        
        # 공통 키에서 값이 다른 경우 충돌
        common_keys = set(self.metadata_signature.keys()) & set(other_signature.keys())
        
        for key in common_keys:
            if self.metadata_signature[key] != other_signature[key]:
                return True
        
        return False
    
    def get_conflict_details(self, other_signature: Dict[str, str]) -> Dict[str, Dict[str, str]]:
        """충돌 상세 정보 반환"""
        conflicts = {}
        
        if not self.metadata_signature or not other_signature:
            return conflicts
        
        common_keys = set(self.metadata_signature.keys()) & set(other_signature.keys())
        
        for key in common_keys:
            if self.metadata_signature[key] != other_signature[key]:
                conflicts[key] = {
                    "existing": self.metadata_signature[key],
                    "new": other_signature[key]
                }
        
        return conflicts
    
    def create_override_version(self, new_instruction: str, new_signature: Dict[str, str]) -> 'DistilledInstruction':
        """충돌 해결을 위한 새 버전 생성"""
        new_version = DistilledInstruction(
            pk=self.pk,
            sk=f"instruction#{self.category.value}#{uuid.uuid4().hex[:8]}",
            user_id=self.user_id,
            category=self.category,
            context_scope=self.context_scope,
            instruction=new_instruction,
            confidence=0.8,  # 새 지침은 낮은 신뢰도로 시작
            source_correction_ids=[],
            pattern_description=f"Override of {self.sk}",
            metadata_signature=new_signature,
            applicable_task_categories=self.applicable_task_categories,
            applicable_node_types=self.applicable_node_types,
            version=self.version + 1
        )
        
        # 기존 지침을 비활성화하고 대체 관계 설정
        self.is_active = False
        self.superseded_by = new_version.sk
        
        return new_version

class CorrectionQualityMetrics(BaseModel):
    """수정 품질 평가 메트릭"""
    
    correction_id: str
    
    # 객관적 메트릭
    edit_distance_score: float = Field(..., ge=0, le=1)
    time_investment_score: float = Field(..., ge=0, le=1)
    user_confirmation_score: float = Field(..., ge=0, le=1)
    
    # 종합 점수
    overall_score: float = Field(..., ge=0, le=1)
    is_valuable: bool
    confidence: float = Field(..., ge=0, le=1)
    
    # 평가 근거
    evaluation_reason: str
    evaluation_timestamp: datetime = Field(default_factory=datetime.utcnow)

class VectorSyncManager:
    """
    개선사항 #3: 벡터 검색 인덱싱 필드 관리
    
    DynamoDB와 벡터 DB 간의 동기화 상태를 추적하고 관리
    """
    
    @staticmethod
    def mark_sync_pending(correction_log: CorrectionLog) -> None:
        """벡터 동기화 대기 상태로 설정"""
        correction_log.vector_sync_status = VectorSyncStatus.PENDING
        correction_log.vector_sync_attempts = 0
        correction_log.vector_sync_error = None
        correction_log.last_vector_sync_attempt = None
    
    @staticmethod
    def mark_sync_success(correction_log: CorrectionLog, embedding_id: str) -> None:
        """벡터 동기화 성공 처리"""
        correction_log.vector_sync_status = VectorSyncStatus.SUCCESS
        correction_log.embedding_id = embedding_id
        correction_log.vector_sync_error = None
        correction_log.last_vector_sync_attempt = datetime.now(timezone.utc)
    
    @staticmethod
    def mark_sync_failed(correction_log: CorrectionLog, error_message: str) -> None:
        """벡터 동기화 실패 처리"""
        correction_log.vector_sync_status = VectorSyncStatus.FAILED
        correction_log.vector_sync_attempts += 1
        correction_log.vector_sync_error = error_message
        correction_log.last_vector_sync_attempt = datetime.now(timezone.utc)
        
        # 3회 실패 후에는 재시도 상태로 변경
        if correction_log.vector_sync_attempts >= 3:
            correction_log.vector_sync_status = VectorSyncStatus.RETRY
    
    @staticmethod
    def should_retry_sync(correction_log: CorrectionLog) -> bool:
        """벡터 동기화 재시도 여부 판단"""
        if correction_log.vector_sync_status == VectorSyncStatus.SUCCESS:
            return False
        
        if correction_log.vector_sync_status == VectorSyncStatus.PENDING:
            return True
        
        if correction_log.vector_sync_status == VectorSyncStatus.FAILED:
            return correction_log.vector_sync_attempts < 3
        
        if correction_log.vector_sync_status == VectorSyncStatus.RETRY:
            # 마지막 시도로부터 1시간 경과 시 재시도
            if correction_log.last_vector_sync_attempt:
                time_diff = datetime.now(timezone.utc) - correction_log.last_vector_sync_attempt
                return time_diff.total_seconds() > 3600  # 1시간
        
        return False
    
    @staticmethod
    def get_retry_corrections(corrections: List[CorrectionLog]) -> List[CorrectionLog]:
        """재시도가 필요한 수정 로그들 필터링"""
        return [
            correction for correction in corrections
            if VectorSyncManager.should_retry_sync(correction)
        ]

class ConflictResolver:
    """
    개선사항 #2: 지침 충돌 해결 도우미
    
    metadata_signature를 활용한 지침 간 충돌 감지 및 해결
    """
    
    @staticmethod
    def detect_conflicts(
        existing_instructions: List[DistilledInstruction],
        new_signature: Dict[str, str],
        category: CorrectionType,
        context_scope: str
    ) -> List[DistilledInstruction]:
        """새 지침과 충돌하는 기존 지침들 찾기"""
        conflicts = []
        
        for instruction in existing_instructions:
            # 같은 카테고리와 컨텍스트 스코프에서만 충돌 검사
            if (instruction.category == category and 
                instruction.context_scope == context_scope and
                instruction.is_active and
                instruction.has_conflicting_signature(new_signature)):
                conflicts.append(instruction)
        
        return conflicts
    
    @staticmethod
    def resolve_conflict_strategy(
        conflicting_instruction: DistilledInstruction,
        new_signature: Dict[str, str],
        user_preference: str = "override"  # "override" | "merge" | "ask_user"
    ) -> Dict[str, Any]:
        """충돌 해결 전략 결정"""
        conflict_details = conflicting_instruction.get_conflict_details(new_signature)
        
        if user_preference == "override":
            return {
                "action": "override",
                "message": f"새 지침이 기존 지침을 대체합니다",
                "conflicts": conflict_details
            }
        elif user_preference == "merge":
            # 메타데이터 병합 시도
            merged_signature = {**conflicting_instruction.metadata_signature, **new_signature}
            return {
                "action": "merge",
                "message": f"메타데이터를 병합합니다",
                "merged_signature": merged_signature,
                "conflicts": conflict_details
            }
        else:  # ask_user
            return {
                "action": "ask_user",
                "message": f"사용자 확인이 필요한 충돌이 감지되었습니다",
                "conflicts": conflict_details,
                "options": ["override", "keep_existing", "merge"]
            }
    
    @staticmethod
    def apply_resolution(
        conflicting_instruction: DistilledInstruction,
        resolution: Dict[str, Any],
        new_instruction_text: str
    ) -> Optional[DistilledInstruction]:
        """충돌 해결 적용"""
        if resolution["action"] == "override":
            return conflicting_instruction.create_override_version(
                new_instruction_text,
                resolution.get("new_signature", {})
            )
        elif resolution["action"] == "merge":
            return conflicting_instruction.create_override_version(
                new_instruction_text,
                resolution["merged_signature"]
            )
        
        return None