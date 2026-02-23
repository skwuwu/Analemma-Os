"""
🏛️ Governance Engine — Constitutional Article Enforcement

constitution.py의 선언적 Article 정의를 실제 집행 파이프라인에 연결.

흐름:
  constitution.py (정의) → GovernanceEngine.register() → GovernanceEngine.verify()
                                                           ↓
                                                  governor_runner.py (판단)

설계 원칙:
  - Open-Closed: ArticleValidator Protocol 구현체만 추가하면 신규 조항 확장 가능
  - Parallel Execution: asyncio.gather()로 6개 Validator 병렬 실행 (지연 최소화)
  - Severity Accumulation: LOW 위반 누적 시 MEDIUM으로 상향 (향후 확장 예정)

Author: Analemma OS Team
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional, Protocol, runtime_checkable

from src.services.governance.constitution import (
    ConstitutionalClause,
    ClauseSeverity,
    get_constitution,
)
from src.common.constants import SecurityConfig

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 모델
# ─────────────────────────────────────────────────────────────────────────────

class RecommendedAction(str, Enum):
    APPROVE      = "APPROVE"
    WARN         = "WARN"
    SOFT_ROLLBACK = "SOFT_ROLLBACK"
    HARD_ROLLBACK = "HARD_ROLLBACK"
    TERMINAL_HALT = "TERMINAL_HALT"


@dataclass
class ArticleViolation:
    clause_id:   str
    article_num: int
    severity:    ClauseSeverity
    description: str
    evidence:    str          # 위반 증거 (매칭된 텍스트 또는 요약)


@dataclass
class GovernanceVerdict:
    violations:         List[ArticleViolation]
    max_severity:       Optional[ClauseSeverity]   # None = 위반 없음
    recommended_action: RecommendedAction
    elapsed_ms:         float
    low_violation_count: int = 0  # 누적 LOW 카운터 (향후 업그레이드 정책용)

    @property
    def is_compliant(self) -> bool:
        return len(self.violations) == 0


# ─────────────────────────────────────────────────────────────────────────────
# ArticleValidator Protocol
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class ArticleValidator(Protocol):
    """
    각 Article에 대응하는 검증기 인터페이스.
    GovernanceEngine.register()에 전달하는 모든 검증기가 이 Protocol을 구현해야 함.
    """

    async def validate(
        self,
        output_text: str,
        context: Dict[str, Any],
    ) -> Optional[ArticleViolation]:
        """
        Args:
            output_text: 검증할 에이전트 출력 텍스트
            context:     ring_level, agent_id 등 추가 컨텍스트

        Returns:
            위반 발견 시 ArticleViolation, 정상 시 None
        """
        ...


# ─────────────────────────────────────────────────────────────────────────────
# 내장 Article Validators (Articles 1–6)
# ─────────────────────────────────────────────────────────────────────────────

class Article1ToneValidator:
    """Article 1: Professional Business Tone (MEDIUM)"""

    _PROFANITY_PATTERNS = [
        re.compile(r'\b(stupid|idiot|dumb|crap|wtf|hell\s+of)\b', re.IGNORECASE),
        re.compile(r'\b(damn|shit|fuck|ass)\b', re.IGNORECASE),
        re.compile(r'this\s+(stupid|terrible|awful)\s+\w+', re.IGNORECASE),
    ]

    async def validate(self, output_text: str, context: Dict[str, Any]) -> Optional[ArticleViolation]:
        for pattern in self._PROFANITY_PATTERNS:
            m = pattern.search(output_text)
            if m:
                return ArticleViolation(
                    clause_id="article_1_professional_tone",
                    article_num=1,
                    severity=ClauseSeverity.MEDIUM,
                    description="Unprofessional or aggressive language detected",
                    evidence=m.group(0)[:80],
                )
        return None


class Article2HarmfulContentValidator:
    """Article 2: No Harmful Content Generation (CRITICAL)"""

    _COMPILED_PATTERNS = [
        re.compile(p) for p in SecurityConfig.INJECTION_PATTERNS
    ]
    _HARMFUL_PATTERNS = [
        re.compile(r'\b(sql\s+injection|xss\s+attack|buffer\s+overflow)\b', re.IGNORECASE),
        re.compile(r'bypass\s+(authentication|authorization|security)', re.IGNORECASE),
        re.compile(r'(violence|harm|kill|murder)\s+(instructions?|guide|how)', re.IGNORECASE),
        re.compile(r'how\s+to\s+(make|create|build)\s+(bomb|weapon|malware)', re.IGNORECASE),
    ]

    async def validate(self, output_text: str, context: Dict[str, Any]) -> Optional[ArticleViolation]:
        for pattern in self._COMPILED_PATTERNS + self._HARMFUL_PATTERNS:
            m = pattern.search(output_text)
            if m:
                return ArticleViolation(
                    clause_id="article_2_no_harmful_content",
                    article_num=2,
                    severity=ClauseSeverity.CRITICAL,
                    description="Harmful content or security bypass instruction detected",
                    evidence=m.group(0)[:80],
                )
        return None


class Article3UserProtectionValidator:
    """Article 3: User Protection Principle (CRITICAL)"""

    _SOLICITATION_PATTERNS = [
        re.compile(r'(please\s+)?(enter|provide|give\s+me|share)\s+your\s+(password|card\s+number|ssn|credit\s+card)', re.IGNORECASE),
        re.compile(r'(please\s+)?(enter|provide|input)\s+your\s+(pin|cvv|security\s+code)', re.IGNORECASE),
        re.compile(r'what\s+is\s+your\s+(password|account\s+number|social\s+security)', re.IGNORECASE),
        re.compile(r'(send|transfer|wire)\s+money\s+(to|via)', re.IGNORECASE),
    ]

    async def validate(self, output_text: str, context: Dict[str, Any]) -> Optional[ArticleViolation]:
        for pattern in self._SOLICITATION_PATTERNS:
            m = pattern.search(output_text)
            if m:
                return ArticleViolation(
                    clause_id="article_3_user_protection",
                    article_num=3,
                    severity=ClauseSeverity.CRITICAL,
                    description="Solicitation of sensitive user information detected",
                    evidence=m.group(0)[:80],
                )
        return None


class Article4TransparencyValidator:
    """Article 4: Transparency Principle (LOW)"""

    _OVERCONFIDENCE_PATTERNS = [
        re.compile(r'\b(100\s*%\s*(accurate|correct|certain)|absolutely\s+guaranteed)\b', re.IGNORECASE),
        re.compile(r'\b(this\s+is\s+definitely|I\s+am\s+completely\s+sure|I\s+guarantee)\b', re.IGNORECASE),
        re.compile(r'\b(no\s+doubt|without\s+question|infallibly)\b', re.IGNORECASE),
    ]

    async def validate(self, output_text: str, context: Dict[str, Any]) -> Optional[ArticleViolation]:
        for pattern in self._OVERCONFIDENCE_PATTERNS:
            m = pattern.search(output_text)
            if m:
                return ArticleViolation(
                    clause_id="article_4_transparency",
                    article_num=4,
                    severity=ClauseSeverity.LOW,
                    description="Overconfident statement without uncertainty acknowledgment",
                    evidence=m.group(0)[:80],
                )
        return None


class Article5SecurityPolicyValidator:
    """Article 5: Security Policy Compliance (CRITICAL)"""

    _BYPASS_PATTERNS = [
        re.compile(r'scan(ning)?\s+(all|entire)\s+(database|dynamodb|table)', re.IGNORECASE),
        re.compile(r'(access|read|query)\s+(all\s+)?user\s+data\s+directly', re.IGNORECASE),
        re.compile(r'(disable|bypass|skip|ignore)\s+(auth(entication)?|access\s+control|audit)', re.IGNORECASE),
        re.compile(r'(drop|delete|truncate)\s+table', re.IGNORECASE),
        re.compile(r'grant\s+all\s+privileges', re.IGNORECASE),
    ]

    async def validate(self, output_text: str, context: Dict[str, Any]) -> Optional[ArticleViolation]:
        for pattern in self._BYPASS_PATTERNS:
            m = pattern.search(output_text)
            if m:
                return ArticleViolation(
                    clause_id="article_5_no_security_bypass",
                    article_num=5,
                    severity=ClauseSeverity.CRITICAL,
                    description="Security policy bypass or unauthorized data access detected",
                    evidence=m.group(0)[:80],
                )
        return None


class Article6PIILeakageValidator:
    """Article 6: No PII Leakage in Text (CRITICAL)"""

    _PII_PATTERNS = [
        re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),              # email
        re.compile(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b'),                        # credit card
        re.compile(r'\b\d{3}[-.]?\d{2}[-.]?\d{4}\b'),                                      # SSN
        re.compile(r'\b(\+?82|0)[-.\s]?\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}\b'),             # KR phone
        re.compile(r'\b(\+1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),              # US phone
    ]

    # RetroactiveMaskingService 재사용 시도 (PII 감지 정밀도 향상)
    _retroactive_masker = None

    @classmethod
    def _get_masker(cls):
        if cls._retroactive_masker is None:
            try:
                from src.services.governance.retroactive_masking import RetroactiveMaskingService
                cls._retroactive_masker = RetroactiveMaskingService()
            except ImportError:
                pass
        return cls._retroactive_masker

    async def validate(self, output_text: str, context: Dict[str, Any]) -> Optional[ArticleViolation]:
        # RetroactiveMaskingService 우선 사용
        masker = self._get_masker()
        if masker is not None:
            try:
                result = masker.scan(output_text)
                if result and result.get('pii_detected'):
                    return ArticleViolation(
                        clause_id="article_6_pii_text_leakage",
                        article_num=6,
                        severity=ClauseSeverity.CRITICAL,
                        description="PII detected in output text (RetroactiveMasker)",
                        evidence=str(result.get('detected_types', []))[:80],
                    )
            except Exception as e:
                logger.debug(f"[Article6] RetroactiveMaskingService scan failed: {e}")

        # 폴백: 정규식 직접 검사
        for pattern in self._PII_PATTERNS:
            m = pattern.search(output_text)
            if m:
                return ArticleViolation(
                    clause_id="article_6_pii_text_leakage",
                    article_num=6,
                    severity=ClauseSeverity.CRITICAL,
                    description="PII (email/phone/card/SSN) found in output text",
                    evidence=m.group(0)[:80],
                )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GovernanceEngine
# ─────────────────────────────────────────────────────────────────────────────

# Severity → RecommendedAction 매핑
_SEVERITY_ACTION_MAP: Dict[ClauseSeverity, RecommendedAction] = {
    ClauseSeverity.LOW:      RecommendedAction.WARN,
    ClauseSeverity.MEDIUM:   RecommendedAction.SOFT_ROLLBACK,
    ClauseSeverity.HIGH:     RecommendedAction.HARD_ROLLBACK,
    ClauseSeverity.CRITICAL: RecommendedAction.TERMINAL_HALT,
}

# LOW 누적 임계치: LOW 위반 N개 이상 → MEDIUM 업그레이드
_LOW_ACCUMULATION_THRESHOLD = 10


class GovernanceEngine:
    """
    Article Registry + Enforcement Service.

    governor_runner.py의 단일 진입점:
        engine = GovernanceEngine.get_instance()
        verdict = asyncio.run(engine.verify(output_text, context))

    기본 Validators(Articles 1–6)는 자동 등록됨.
    추가 Article은 register()로 확장 가능 (Open-Closed Principle).
    """

    _instance: Optional['GovernanceEngine'] = None

    def __init__(self):
        # article_id → validator
        self._registry: Dict[str, ArticleValidator] = {}
        self._register_defaults()

    @classmethod
    def get_instance(cls) -> 'GovernanceEngine':
        """싱글톤 (Double-checked locking)."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, clause: ConstitutionalClause, validator: ArticleValidator) -> None:
        """
        Article과 Validator를 레지스트리에 등록.
        커스텀 Article(article_number > 6) 확장 시 사용.
        """
        self._registry[clause.clause_id] = validator
        logger.debug(
            "[GovernanceEngine] Registered validator for article %d (%s)",
            clause.article_number, clause.clause_id,
        )

    async def verify(
        self,
        output_text: str,
        context: Dict[str, Any],
    ) -> GovernanceVerdict:
        """
        등록된 모든 Article Validator를 asyncio.gather()로 병렬 실행.

        Args:
            output_text: 검증 대상 텍스트 (에이전트 출력)
            context:     ring_level, agent_id 등 추가 컨텍스트

        Returns:
            GovernanceVerdict (violations, max_severity, recommended_action)
        """
        start = time.time()

        # 병렬 실행 — 예외는 None으로 처리 (개별 Validator 장애가 전체를 막지 않음)
        tasks = [
            self._safe_validate(clause_id, validator, output_text, context)
            for clause_id, validator in self._registry.items()
        ]
        results = await asyncio.gather(*tasks)

        violations: List[ArticleViolation] = [r for r in results if r is not None]

        # max_severity 계산
        max_severity = None
        if violations:
            severity_order = [
                ClauseSeverity.CRITICAL,
                ClauseSeverity.HIGH,
                ClauseSeverity.MEDIUM,
                ClauseSeverity.LOW,
            ]
            for sev in severity_order:
                if any(v.severity == sev for v in violations):
                    max_severity = sev
                    break

        # LOW 누적 카운터
        low_count = sum(1 for v in violations if v.severity == ClauseSeverity.LOW)

        # RecommendedAction 결정
        effective_severity = max_severity
        if effective_severity == ClauseSeverity.LOW and low_count >= _LOW_ACCUMULATION_THRESHOLD:
            effective_severity = ClauseSeverity.MEDIUM  # 누적 업그레이드

        recommended_action = (
            _SEVERITY_ACTION_MAP.get(effective_severity, RecommendedAction.APPROVE)
            if effective_severity is not None
            else RecommendedAction.APPROVE
        )

        elapsed_ms = (time.time() - start) * 1000

        if violations:
            logger.warning(
                "[GovernanceEngine] %d violation(s) detected | max_severity=%s action=%s elapsed=%.1fms",
                len(violations),
                max_severity.value if max_severity else "NONE",
                recommended_action.value,
                elapsed_ms,
            )

        return GovernanceVerdict(
            violations=violations,
            max_severity=max_severity,
            recommended_action=recommended_action,
            elapsed_ms=elapsed_ms,
            low_violation_count=low_count,
        )

    # ── Internals ─────────────────────────────────────────────────────────────

    def _register_defaults(self) -> None:
        """Articles 1–6 내장 Validator 자동 등록."""
        constitution = get_constitution()

        default_validators: Dict[str, ArticleValidator] = {
            "article_1_professional_tone":  Article1ToneValidator(),
            "article_2_no_harmful_content": Article2HarmfulContentValidator(),
            "article_3_user_protection":    Article3UserProtectionValidator(),
            "article_4_transparency":       Article4TransparencyValidator(),
            "article_5_no_security_bypass": Article5SecurityPolicyValidator(),
            "article_6_pii_text_leakage":   Article6PIILeakageValidator(),
        }

        for clause in constitution:
            validator = default_validators.get(clause.clause_id)
            if validator:
                self._registry[clause.clause_id] = validator

        logger.info(
            "[GovernanceEngine] Initialized with %d article validators",
            len(self._registry),
        )

    async def _safe_validate(
        self,
        clause_id: str,
        validator: ArticleValidator,
        output_text: str,
        context: Dict[str, Any],
    ) -> Optional[ArticleViolation]:
        """Validator 예외를 캐치하여 None 반환."""
        try:
            return await validator.validate(output_text, context)
        except Exception as e:
            logger.warning(
                "[GovernanceEngine] Validator '%s' raised exception: %s",
                clause_id, e,
            )
            return None
