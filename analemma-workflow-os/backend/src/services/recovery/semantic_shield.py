"""
🛡️ Semantic Shield — 3단계 주입 탐지 파이프라인

블록리스트(정규식) 기반 방어의 한계를 극복하는 다층 방어:
  Stage 1: 텍스트 정규화 (Zero-Width Space·RTL·Base64·Homoglyph)
  Stage 2: 정규식 패턴 매칭 (정규화 후 적용 → 인코딩 우회 불가)
  Stage 3: Semantic 분류 (ShieldGemma / Bedrock Guardrails, Ring 2+ 전용)

가변적 방어 (비용 최적화):
  Ring 0/1 (KERNEL/DRIVER): Stage 1+2만 실행 (고신뢰, 모델 추론 생략)
  Ring 2/3 (SERVICE/USER) : Stage 1+2+3 전체 (ShieldGemma 포함)

Author: Analemma OS Team
"""

import base64
import logging
import re
import unicodedata
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple, Dict, Any

from src.common.constants import SecurityConfig

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 모델
# ─────────────────────────────────────────────────────────────────────────────

class DetectionType(Enum):
    ZERO_WIDTH_CHAR  = "zero_width_char"
    RTL_OVERRIDE     = "rtl_override"
    BASE64_INJECTION = "base64_injection"
    HOMOGLYPH        = "homoglyph"
    INJECTION_PATTERN = "injection_pattern"
    SEMANTIC_INJECTION = "semantic_injection"
    SEMANTIC_JAILBREAK = "semantic_jailbreak"


@dataclass
class Detection:
    detection_type: DetectionType
    description:    str
    stage:          int   # 1, 2, 3


@dataclass
class ShieldResult:
    allowed:         bool
    normalized_text: str
    detections:      List[Detection]
    risk_score:      float   # 0.0 – 1.0
    stages_run:      int     # 실행된 단계 수
    elapsed_ms:      float


# ─────────────────────────────────────────────────────────────────────────────
# Homoglyph 매핑 (Cyrillic · Greek · Full-width → Latin)
# ─────────────────────────────────────────────────────────────────────────────

_HOMOGLYPH_MAP: Dict[str, str] = {
    # Cyrillic
    'а': 'a', 'е': 'e', 'і': 'i', 'о': 'o', 'р': 'p', 'с': 'c',
    'у': 'y', 'х': 'x', 'ѕ': 's', 'ԁ': 'd', 'ɡ': 'g',
    # Greek
    'α': 'a', 'β': 'b', 'γ': 'y', 'δ': 'd', 'ε': 'e', 'ι': 'i',
    'κ': 'k', 'μ': 'm', 'ο': 'o', 'ρ': 'p', 'σ': 's', 'τ': 't',
    'υ': 'u', 'χ': 'x', 'ω': 'w',
    # Full-width Latin (U+FF21–FF5A)
    'Ａ': 'A', 'Ｂ': 'B', 'Ｃ': 'C', 'Ｄ': 'D', 'Ｅ': 'E',
    'Ｆ': 'F', 'Ｇ': 'G', 'Ｈ': 'H', 'Ｉ': 'I', 'Ｊ': 'J',
    'ａ': 'a', 'ｂ': 'b', 'ｃ': 'c', 'ｄ': 'd', 'ｅ': 'e',
    'ｆ': 'f', 'ｇ': 'g', 'ｈ': 'h', 'ｉ': 'i', 'ｊ': 'j',
}


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Normalization Pipeline
# ─────────────────────────────────────────────────────────────────────────────

class NormalizationPipeline:
    """
    텍스트 정규화 — 인코딩·유니코드 우회 기법 제거.
    정규화 결과(탐지 내용 포함)를 Stage 2 패턴 매칭에 전달.
    """

    ZERO_WIDTH = frozenset({'\u200b', '\u200c', '\u200d', '\ufeff', '\u2060'})
    RTL_OVERRIDE = frozenset({'\u202e', '\u202d', '\u200f', '\u200e'})

    # Base64 후보: 20자 이상의 유효한 Base64 문자열
    _BASE64_PATTERN = re.compile(r'[A-Za-z0-9+/]{20,}={0,2}', re.ASCII)

    # Base64 디코딩 후 주입 키워드 (소문자)
    _INJECTION_KEYWORDS = frozenset({
        'ignore', 'disregard', 'forget', 'override', 'jailbreak',
        'system:', 'bypass', 'escape', 'you are now',
        '이전 지시', '무시', '시스템 프롬프트',
    })

    def normalize(self, text: str) -> Tuple[str, List[Detection]]:
        """
        정규화 수행.

        Returns:
            (정규화된 텍스트, 탐지 목록)
        """
        detections: List[Detection] = []
        result = text

        # 1-a. Zero-Width Space / BOM 제거
        if any(c in self.ZERO_WIDTH for c in result):
            result = ''.join(c for c in result if c not in self.ZERO_WIDTH)
            detections.append(Detection(
                detection_type=DetectionType.ZERO_WIDTH_CHAR,
                description="Zero-Width Space / BOM characters stripped",
                stage=1,
            ))

        # 1-b. RTL Override 제거
        if any(c in self.RTL_OVERRIDE for c in result):
            result = ''.join(c for c in result if c not in self.RTL_OVERRIDE)
            detections.append(Detection(
                detection_type=DetectionType.RTL_OVERRIDE,
                description="RTL override characters stripped",
                stage=1,
            ))

        # 1-c. Base64 디코드 시도
        b64_detections = self._check_base64(result)
        detections.extend(b64_detections)

        # 1-d. Unicode NFC 정규화
        result = unicodedata.normalize('NFC', result)

        # 1-e. Homoglyph 정규화
        result, hg_detections = self._normalize_homoglyphs(result)
        detections.extend(hg_detections)

        return result, detections

    def _check_base64(self, text: str) -> List[Detection]:
        """Base64 인코딩된 주입 시도 탐지."""
        detections = []
        for match in self._BASE64_PATTERN.finditer(text):
            b64_str = match.group(0)
            try:
                decoded = base64.b64decode(b64_str + '==').decode('utf-8', errors='ignore')
                decoded_lower = decoded.lower()
                if any(kw in decoded_lower for kw in self._INJECTION_KEYWORDS):
                    detections.append(Detection(
                        detection_type=DetectionType.BASE64_INJECTION,
                        description=f"Base64-encoded injection: {decoded[:80]}",
                        stage=1,
                    ))
            except Exception:
                pass
        return detections

    def _normalize_homoglyphs(self, text: str) -> Tuple[str, List[Detection]]:
        """Homoglyph(동형이의자) 정규화."""
        detections = []
        result = []
        replaced = False

        for char in text:
            rep = _HOMOGLYPH_MAP.get(char)
            if rep:
                result.append(rep)
                replaced = True
            else:
                result.append(char)

        if replaced:
            detections.append(Detection(
                detection_type=DetectionType.HOMOGLYPH,
                description="Homoglyph chars normalized (Cyrillic/Greek/Full-width → Latin)",
                stage=1,
            ))

        return ''.join(result), detections


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: 한국어 주입 패턴
# ─────────────────────────────────────────────────────────────────────────────

_KOREAN_INJECTION_PATTERNS: List[re.Pattern] = [
    re.compile(r'이전\s*(지시사항|명령|인스트럭션)\s*(무시|삭제|잊어)', re.IGNORECASE),
    re.compile(r'시스템\s*프롬프트\s*(공개|출력|누설)', re.IGNORECASE),
    re.compile(r'역할\s*(변경|교체|무시)', re.IGNORECASE),
    re.compile(r'새로운\s*(역할|페르소나|인격)', re.IGNORECASE),
    re.compile(r'보안\s*(우회|무력화|비활성화)', re.IGNORECASE),
    re.compile(r'(관리자|admin|root)\s*(권한|모드|접근)', re.IGNORECASE),
    re.compile(r'지금부터\s*(다른|새로운)\s*(역할|모드)', re.IGNORECASE),
    re.compile(r'(이전|위)\s*(모든)?\s*(명령|지시)\s*(삭제|무시)', re.IGNORECASE),
]


# ─────────────────────────────────────────────────────────────────────────────
# SemanticShield — 통합 파이프라인
# ─────────────────────────────────────────────────────────────────────────────

class SemanticShield:
    """
    3단계 Semantic Shield.

    ┌──────────────────────────────────────────────┐
    │ Stage 1  NormalizationPipeline               │  (모든 Ring)
    │ Stage 2  Pattern Matching (EN + KO)          │  (모든 Ring)
    │ Stage 3  Semantic Classifier (LLM)           │  (Ring 2/3만)
    └──────────────────────────────────────────────┘

    차단 판정:
      BASE64_INJECTION, INJECTION_PATTERN, SEMANTIC_* → 차단
      ZERO_WIDTH_CHAR, RTL_OVERRIDE, HOMOGLYPH       → 정규화 후 허용
    """

    _instance: Optional['SemanticShield'] = None

    # 차단 유발 DetectionType
    _BLOCKING_TYPES = frozenset({
        DetectionType.BASE64_INJECTION,
        DetectionType.INJECTION_PATTERN,
        DetectionType.SEMANTIC_INJECTION,
        DetectionType.SEMANTIC_JAILBREAK,
    })

    # 위험도 가중치
    _TYPE_RISK = {
        DetectionType.ZERO_WIDTH_CHAR:    0.2,
        DetectionType.RTL_OVERRIDE:       0.2,
        DetectionType.HOMOGLYPH:          0.3,
        DetectionType.BASE64_INJECTION:   0.9,
        DetectionType.INJECTION_PATTERN:  0.7,
        DetectionType.SEMANTIC_INJECTION: 0.95,
        DetectionType.SEMANTIC_JAILBREAK: 1.0,
    }
    _STAGE_WEIGHT = {1: 0.3, 2: 0.5, 3: 0.8}

    def __init__(self, llm_client=None):
        self.normalizer = NormalizationPipeline()
        self.llm_client = llm_client
        self._compiled_english = [
            re.compile(p) for p in SecurityConfig.INJECTION_PATTERNS
        ]

    @classmethod
    def get_instance(cls, llm_client=None) -> 'SemanticShield':
        """싱글톤 (Double-checked locking)."""
        if cls._instance is None:
            cls._instance = cls(llm_client=llm_client)
        return cls._instance

    def inspect(self, text: str, ring_level: int) -> ShieldResult:
        """
        3단계 보안 검사.

        Args:
            text:       검사할 원본 텍스트
            ring_level: RingLevel.value (정수: 0–3)

        Returns:
            ShieldResult
        """
        start = time.time()
        all_detections: List[Detection] = []

        # Stage 1: 정규화
        normalized, stage1 = self.normalizer.normalize(text)
        all_detections.extend(stage1)

        # Stage 2: 패턴 매칭 (정규화 후 텍스트에 적용)
        stage2 = self._pattern_match(normalized)
        all_detections.extend(stage2)

        stages_run = 2

        # Stage 3: Semantic 분류 (Ring 2/3만, LLM 클라이언트 존재 시)
        if ring_level >= 2 and self.llm_client:
            try:
                stage3 = self._semantic_classify(normalized)
                all_detections.extend(stage3)
                stages_run = 3
            except Exception as e:
                logger.warning(
                    f"[SemanticShield] Stage 3 failed, degraded to Stage 2: {e}"
                )

        risk_score = self._calculate_risk(all_detections)
        allowed = not any(d.detection_type in self._BLOCKING_TYPES for d in all_detections)
        elapsed_ms = (time.time() - start) * 1000

        if all_detections:
            logger.warning(
                "[SemanticShield] ring=%d stages=%d allowed=%s risk=%.2f detections=%s",
                ring_level, stages_run, allowed, risk_score,
                [d.detection_type.value for d in all_detections],
            )

        return ShieldResult(
            allowed=allowed,
            normalized_text=normalized,
            detections=all_detections,
            risk_score=risk_score,
            stages_run=stages_run,
            elapsed_ms=elapsed_ms,
        )

    # ── Stage 2 ──────────────────────────────────────────────────────────────

    def _pattern_match(self, normalized_text: str) -> List[Detection]:
        """영어 + 한국어 패턴 매칭 (정규화 텍스트 기준)."""
        detections = []

        for pattern in self._compiled_english:
            if pattern.search(normalized_text):
                detections.append(Detection(
                    detection_type=DetectionType.INJECTION_PATTERN,
                    description=f"EN injection pattern: {pattern.pattern[:60]}",
                    stage=2,
                ))

        for pattern in _KOREAN_INJECTION_PATTERNS:
            if pattern.search(normalized_text):
                detections.append(Detection(
                    detection_type=DetectionType.INJECTION_PATTERN,
                    description=f"KO injection pattern: {pattern.pattern[:60]}",
                    stage=2,
                ))

        return detections

    # ── Stage 3 ──────────────────────────────────────────────────────────────

    def _semantic_classify(self, text: str) -> List[Detection]:
        """
        LLM 기반 Semantic 분류.
        Bedrock Guardrails API 또는 Gemini ShieldGemma 호출.
        """
        detections = []

        try:
            # Bedrock Guardrails ApplyGuardrail API
            if hasattr(self.llm_client, 'apply_guardrail'):
                result = self.llm_client.apply_guardrail(
                    guardrailIdentifier='analemma-injection-guard',
                    guardrailVersion='DRAFT',
                    source='INPUT',
                    content=[{'text': {'text': text}}],
                )
                if result.get('action') == 'GUARDRAIL_INTERVENED':
                    for assessment in result.get('assessments', []):
                        if assessment.get('promptAttack', {}).get('attackDetected'):
                            detections.append(Detection(
                                detection_type=DetectionType.SEMANTIC_INJECTION,
                                description="Bedrock Guardrails: prompt attack detected",
                                stage=3,
                            ))

            # Gemini ShieldGemma (classify_safety 인터페이스)
            elif hasattr(self.llm_client, 'classify_safety'):
                result = self.llm_client.classify_safety(text)
                for category, score in result.items():
                    if 'INJECTION' in category.upper() and score > 0.7:
                        detections.append(Detection(
                            detection_type=DetectionType.SEMANTIC_INJECTION,
                            description=f"ShieldGemma: {category} score={score:.2f}",
                            stage=3,
                        ))
                    elif 'JAILBREAK' in category.upper() and score > 0.7:
                        detections.append(Detection(
                            detection_type=DetectionType.SEMANTIC_JAILBREAK,
                            description=f"ShieldGemma: {category} score={score:.2f}",
                            stage=3,
                        ))

        except Exception as e:
            logger.debug(f"[SemanticShield] Stage 3 classify error: {e}")

        return detections

    # ── 위험도 계산 ───────────────────────────────────────────────────────────

    def _calculate_risk(self, detections: List[Detection]) -> float:
        """탐지 결과 기반 위험도 점수 (0.0–1.0)."""
        if not detections:
            return 0.0

        max_risk = 0.0
        for d in detections:
            type_w = self._TYPE_RISK.get(d.detection_type, 0.5)
            stage_w = self._STAGE_WEIGHT.get(d.stage, 0.5)
            max_risk = max(max_risk, type_w * stage_w)

        return min(1.0, max_risk)
