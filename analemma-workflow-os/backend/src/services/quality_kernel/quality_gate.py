"""
Quality Gate - 2단계 필터링 품질 게이트
======================================

커널 레벨 품질 검증 시스템의 핵심 컴포넌트.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                     INCOMING TEXT                            │
    └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
    ┌─────────────────────────────────────────────────────────────┐
    │  STAGE 1: Local Heuristic Filter (Cost: $0, Latency: <5ms) │
    │  ┌─────────────────┐  ┌─────────────────┐                   │
    │  │ Entropy Check   │  │  Slop Detection │                   │
    │  │ H(X) >= 4.2?    │  │  Score < 0.5?   │                   │
    │  └────────┬────────┘  └────────┬────────┘                   │
    │           └──────────┬─────────┘                            │
    │                      ▼                                       │
    │              ┌──────────────┐                                │
    │              │ PASS / FAIL / │                               │
    │              │   UNCERTAIN   │                               │
    │              └──────────────┘                                │
    └─────────────────────────────────────────────────────────────┘
            │              │              │
           PASS         UNCERTAIN        FAIL
            │              │              │
            ▼              ▼              ▼
        ┌───────┐   ┌───────────┐   ┌───────────┐
        │ OUTPUT│   │  STAGE 2  │   │  REJECT   │
        └───────┘   └─────┬─────┘   └───────────┘
                          ▼
    ┌─────────────────────────────────────────────────────────────┐
    │  STAGE 2: Strong Verifier (Cost: ~$0.001, Latency: ~500ms) │
    │  ┌─────────────────────────────────────────────────────┐    │
    │  │  Gemini 1.5 Flash-8B                                │    │
    │  │  "Rate information density 1-10. REJECT if < 7."   │    │
    │  └─────────────────────────────────────────────────────┘    │
    └─────────────────────────────────────────────────────────────┘
                          │
                          ▼
                   ┌──────────────┐
                   │ FINAL VERDICT│
                   └──────────────┘
"""

import json
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Tuple
from enum import Enum
from datetime import datetime
import logging

from .entropy_analyzer import EntropyAnalyzer, EntropyAnalysisResult, ContentDomain
from .slop_detector import SlopDetector, SlopDetectionResult
from .cost_guardrails import (
    CostGuardrailSystem,
    GuardrailAction,
    GuardrailDecision,
    GuardrailTrigger,
    RetryState,
    BudgetState,
    create_guardrail_for_workflow
)

logger = logging.getLogger(__name__)


# ============================================================
# 적응형 품질 정책 (Adaptive Quality Policy)
# ============================================================

class QualityPolicyMode(Enum):
    """품질 정책 모드"""
    STANDARD = "standard"              # 기본 모드
    BUDGET_SAVER = "budget_saver"      # 예산 절약 모드 (Stage 2 최소화)
    HIGH_PRECISION = "high_precision"  # 고정밀 모드 (법률/금융)
    FAST_PASS = "fast_pass"            # 빠른 통과 모드 (내부 용도)
    STRICT = "strict"                  # 엄격 모드 (외부 발행)


@dataclass
class AdaptiveQualityPolicy:
    """
    적응형 품질 정책
    
    예산, 도메인 중요도, 긴급도에 따라 동적으로 임계값 조절.
    
    Usage:
        policy = AdaptiveQualityPolicy.for_domain('legal', remaining_budget=0.5)
        gate = QualityGate(adaptive_policy=policy)
    """
    mode: QualityPolicyMode = QualityPolicyMode.STANDARD
    
    # 동적 임계값
    stage2_threshold_low: float = 0.35
    stage2_threshold_high: float = 0.65
    
    # 예산 관련
    remaining_budget_ratio: float = 1.0  # 0.0 ~ 1.0
    max_stage2_calls_per_workflow: int = 10
    current_stage2_calls: int = 0
    
    # 도메인 중요도 가중치
    domain_criticality: float = 1.0  # 1.0 = 보통, 2.0 = 높음 (법률/금융)
    
    # 긴급도 (낮을수록 빠른 통과)
    urgency_factor: float = 1.0  # 0.5 = 긴급, 1.0 = 보통, 1.5 = 여유
    
    def get_effective_thresholds(self) -> tuple:
        """
        현재 상황에 맞는 실효 임계값 반환
        
        Returns:
            Tuple[low_threshold, high_threshold]
        """
        low = self.stage2_threshold_low
        high = self.stage2_threshold_high
        
        # 예산 부족 시: Stage 2 호출 줄이기 (high 낮춤)
        if self.remaining_budget_ratio < 0.3:
            high = max(0.5, high - 0.15)  # 더 많이 Stage 1에서 통과
            low = max(0.2, low - 0.1)
        elif self.remaining_budget_ratio < 0.5:
            high = max(0.55, high - 0.1)
        
        # Stage 2 호출 횟수 제한 도달 시
        if self.current_stage2_calls >= self.max_stage2_calls_per_workflow:
            high = 0.4  # 거의 모두 Stage 1에서 처리
            low = 0.2
        
        # 도메인 중요도가 높을 시: 더 엄격하게 (high 높임)
        if self.domain_criticality > 1.5:
            high = min(0.8, high + 0.1)  # 더 많이 Stage 2 검증
            low = min(0.5, low + 0.1)
        
        # 긴급도 반영
        if self.urgency_factor < 0.7:
            high = max(0.5, high - 0.1)  # 긴급하면 빠른 통과
        elif self.urgency_factor > 1.3:
            high = min(0.75, high + 0.05)  # 여유로우면 더 엄격
        
        return low, high
    
    def should_skip_stage2(self) -> bool:
        """예산/횟수 제한으로 Stage 2 건너뛰어야 하는지"""
        if self.remaining_budget_ratio < 0.1:
            return True
        if self.current_stage2_calls >= self.max_stage2_calls_per_workflow:
            return True
        return False
    
    def record_stage2_call(self):
        """호출 횟수 기록"""
        self.current_stage2_calls += 1
    
    @classmethod
    def for_domain(
        cls,
        domain: str,
        remaining_budget: float = 1.0,
        urgency: str = "normal"
    ) -> 'AdaptiveQualityPolicy':
        """
        도메인별 적응형 정책 생성
        
        Args:
            domain: 'legal', 'financial', 'medical', 'creative', 'internal', 'general'
            remaining_budget: 남은 예산 비율 (0.0 ~ 1.0)
            urgency: 'urgent', 'normal', 'relaxed'
        """
        # 도메인별 중요도
        criticality_map = {
            'legal': 2.0,
            'financial': 2.0,
            'medical': 2.0,
            'compliance': 1.8,
            'external': 1.5,
            'creative': 1.0,
            'internal': 0.7,
            'general': 1.0,
        }
        
        # 긴급도 매핑
        urgency_map = {
            'urgent': 0.5,
            'normal': 1.0,
            'relaxed': 1.5,
        }
        
        criticality = criticality_map.get(domain.lower(), 1.0)
        urgency_factor = urgency_map.get(urgency.lower(), 1.0)
        
        # 도메인별 모드 결정
        if criticality >= 2.0:
            mode = QualityPolicyMode.HIGH_PRECISION
            base_low, base_high = 0.45, 0.75
        elif criticality <= 0.7:
            mode = QualityPolicyMode.FAST_PASS
            base_low, base_high = 0.25, 0.55
        else:
            mode = QualityPolicyMode.STANDARD
            base_low, base_high = 0.35, 0.65
        
        # 예산 부족 시 모드 전환
        if remaining_budget < 0.3:
            mode = QualityPolicyMode.BUDGET_SAVER
        
        return cls(
            mode=mode,
            stage2_threshold_low=base_low,
            stage2_threshold_high=base_high,
            remaining_budget_ratio=remaining_budget,
            domain_criticality=criticality,
            urgency_factor=urgency_factor
        )
    
    def to_dict(self) -> Dict:
        low, high = self.get_effective_thresholds()
        return {
            'mode': self.mode.value,
            'effective_thresholds': {
                'low': round(low, 3),
                'high': round(high, 3)
            },
            'budget': {
                'remaining_ratio': round(self.remaining_budget_ratio, 3),
                'stage2_calls': self.current_stage2_calls,
                'max_calls': self.max_stage2_calls_per_workflow
            },
            'factors': {
                'domain_criticality': self.domain_criticality,
                'urgency': self.urgency_factor
            }
        }


class QualityVerdict(Enum):
    """품질 판정 결과"""
    PASS = "PASS"                    # 품질 통과
    FAIL = "FAIL"                    # 품질 실패 (즉시 거부)
    UNCERTAIN = "UNCERTAIN"          # 불확실 (Stage 2 필요)
    PASS_WITH_WARNING = "PASS_WITH_WARNING"  # 통과하나 경고 포함
    REGENERATE = "REGENERATE"        # 재생성 필요


@dataclass
class Stage1Result:
    """Stage 1 (Local Heuristic) 결과"""
    verdict: QualityVerdict
    entropy_result: EntropyAnalysisResult
    slop_result: SlopDetectionResult
    combined_score: float  # 0.0 (worst) ~ 1.0 (best)
    latency_ms: float
    requires_stage2: bool
    
    # 길이 정규화 정보
    raw_entropy_score: float = 0.0
    normalized_entropy_score: float = 0.0
    length_adjustment_applied: float = 1.0
    
    def to_dict(self) -> Dict:
        return {
            'verdict': self.verdict.value,
            'combined_score': round(self.combined_score, 4),
            'entropy_scores': {
                'raw': round(self.raw_entropy_score, 4),
                'normalized': round(self.normalized_entropy_score, 4),
                'length_adjustment': round(self.length_adjustment_applied, 4)
            },
            'latency_ms': round(self.latency_ms, 2),
            'requires_stage2': self.requires_stage2,
            'entropy': self.entropy_result.to_dict(),
            'slop': self.slop_result.to_dict()
        }


@dataclass
class Stage2Result:
    """Stage 2 (LLM Verifier) 결과"""
    verdict: QualityVerdict
    llm_score: int  # 1-10
    llm_feedback: str
    model_used: str
    latency_ms: float
    cost_usd: float
    
    def to_dict(self) -> Dict:
        return {
            'verdict': self.verdict.value,
            'llm_score': self.llm_score,
            'llm_feedback': self.llm_feedback,
            'model_used': self.model_used,
            'latency_ms': round(self.latency_ms, 2),
            'cost_usd': round(self.cost_usd, 6)
        }


@dataclass
class RegenerationResult:
    """재생성 결과"""
    success: bool
    attempt_count: int
    final_quality_score: float
    guardrail_triggered: bool
    guardrail_action: Optional[str] = None
    degraded_threshold_used: float = 0.7
    model_switched: bool = False
    final_model: str = ""
    user_message: str = ""
    
    def to_dict(self) -> Dict:
        return {
            'success': self.success,
            'attempt_count': self.attempt_count,
            'final_quality_score': round(self.final_quality_score, 4),
            'guardrail_triggered': self.guardrail_triggered,
            'guardrail_action': self.guardrail_action,
            'degraded_threshold_used': round(self.degraded_threshold_used, 3),
            'model_switched': self.model_switched,
            'final_model': self.final_model,
            'user_message': self.user_message
        }


@dataclass
class QualityGateResult:
    """최종 품질 게이트 결과"""
    final_verdict: QualityVerdict
    stage1: Stage1Result
    stage2: Optional[Stage2Result] = None
    
    # 메타데이터
    text_hash: str = ""
    text_length: int = 0
    domain: ContentDomain = ContentDomain.GENERAL_TEXT
    timestamp: str = ""
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    
    # 커널 레벨 정보
    kernel_intervention: bool = False
    kernel_action: str = ""
    
    # 가드레일 정보
    guardrail_decision: Optional[GuardrailDecision] = None
    regeneration_result: Optional[RegenerationResult] = None
    
    def to_dict(self) -> Dict:
        result = {
            'final_verdict': self.final_verdict.value,
            'stage1': self.stage1.to_dict(),
            'metadata': {
                'text_hash': self.text_hash,
                'text_length': self.text_length,
                'domain': self.domain.value,
                'timestamp': self.timestamp,
                'total_latency_ms': round(self.total_latency_ms, 2),
                'total_cost_usd': round(self.total_cost_usd, 6)
            },
            'kernel': {
                'intervention': self.kernel_intervention,
                'action': self.kernel_action
            }
        }
        
        if self.stage2:
            result['stage2'] = self.stage2.to_dict()
        
        if self.guardrail_decision:
            result['guardrail'] = self.guardrail_decision.to_dict()
        
        if self.regeneration_result:
            result['regeneration'] = self.regeneration_result.to_dict()
        
        return result


class QualityGate:
    """
    2단계 필터링 품질 게이트
    
    Kernel Level: RING_1_QUALITY
    
    Stage 1: Local Heuristic (비용 $0)
        - Shannon Entropy 분석
        - 슬롭 패턴 탐지
        - N-gram 반복성 검사
        
    Stage 2: Strong Verifier (최소 비용)
        - Gemini 1.5 Flash-8B
        - 정보 밀도 점수화
        - 최종 PASS/REJECT 판정
    
    Usage:
        gate = QualityGate(domain=ContentDomain.TECHNICAL_REPORT)
        result = gate.evaluate("Your generated text here...")
        
        if result.final_verdict == QualityVerdict.FAIL:
            # 재생성 요청
            pass
        elif result.final_verdict == QualityVerdict.PASS:
            # 출력 허용
            pass
    """
    
    # Stage 2 프롬프트 템플릿
    VERIFIER_SYSTEM_PROMPT = """You are a strict quality evaluator for AI-generated content.
Your job is to detect "slop" - low-quality, generic, repetitive, or vapid content.

Evaluate the given text on INFORMATION DENSITY using this scale:
1-3: SLOP - Generic filler, no real information
4-6: MEDIOCRE - Some content but padded with fluff  
7-8: GOOD - Dense, useful information
9-10: EXCELLENT - Highly informative, zero waste

IMPORTANT: Be harsh. Most AI output is slop. Score 7+ only for genuinely useful content."""

    VERIFIER_USER_PROMPT_TEMPLATE = """Rate this text's information density (1-10).
If score < 7, respond with: REJECT: [score] - [one sentence reason]
If score >= 7, respond with: APPROVE: [score] - [one sentence praise]

TEXT TO EVALUATE:
---
{text}
---

Your verdict (REJECT or APPROVE):"""

    def __init__(
        self,
        domain: ContentDomain = ContentDomain.GENERAL_TEXT,
        slop_threshold: float = 0.5,
        entropy_weight: float = 0.5,
        slop_weight: float = 0.5,
        stage2_threshold_low: float = 0.35,
        stage2_threshold_high: float = 0.65,
        llm_verifier: Optional[Callable] = None,
        adaptive_policy: Optional[AdaptiveQualityPolicy] = None
    ):
        """
        Args:
            domain: 콘텐츠 도메인 (임계값 조정용)
            slop_threshold: 슬롭 판정 임계값
            entropy_weight: 엔트로피 점수 가중치
            slop_weight: 슬롭 점수 가중치
            stage2_threshold_low: 이 점수 이하면 즉시 FAIL
            stage2_threshold_high: 이 점수 이상이면 즉시 PASS
            llm_verifier: Stage 2용 LLM 호출 함수 (Optional)
            adaptive_policy: 적응형 품질 정책 (Optional)
        """
        self.domain = domain
        self.entropy_weight = entropy_weight
        self.slop_weight = slop_weight
        
        # 적응형 정책 적용
        self.adaptive_policy = adaptive_policy
        if adaptive_policy:
            # 정책에서 동적 임계값 사용
            effective_low, effective_high = adaptive_policy.get_effective_thresholds()
            self.stage2_threshold_low = effective_low
            self.stage2_threshold_high = effective_high
        else:
            self.stage2_threshold_low = stage2_threshold_low
            self.stage2_threshold_high = stage2_threshold_high
        
        # 분석기 초기화
        self.entropy_analyzer = EntropyAnalyzer(domain=domain)
        self.slop_detector = SlopDetector(slop_threshold=slop_threshold)
        
        # LLM 검증기 (외부 주입 또는 기본값)
        self.llm_verifier = llm_verifier
        
        # 비용 가드레일 시스템
        self.guardrail: Optional[CostGuardrailSystem] = None
    
    def attach_guardrail(
        self,
        guardrail: CostGuardrailSystem
    ) -> 'QualityGate':
        """
        비용 가드레일 시스템 연결
        
        Args:
            guardrail: CostGuardrailSystem 인스턴스
            
        Returns:
            self (체이닝 지원)
        """
        self.guardrail = guardrail
        return self
    
    def evaluate(
        self,
        text: str,
        skip_stage2: bool = False,
        force_stage2: bool = False
    ) -> QualityGateResult:
        """
        텍스트 품질 평가 수행
        
        Args:
            text: 평가할 텍스트
            skip_stage2: Stage 2 건너뛰기 (빠른 검사용)
            force_stage2: Stage 2 강제 실행
            
        Returns:
            QualityGateResult: 종합 평가 결과
        """
        import time
        start_time = time.time()
        
        # 텍스트 해시 (캐싱/로깅용)
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        timestamp = datetime.utcnow().isoformat() + 'Z'
        
        # ==========================================
        # STAGE 1: Local Heuristic Filter
        # ==========================================
        stage1_start = time.time()
        stage1_result = self._run_stage1(text)
        stage1_latency = (time.time() - stage1_start) * 1000
        
        stage1 = Stage1Result(
            verdict=stage1_result['verdict'],
            entropy_result=stage1_result['entropy'],
            slop_result=stage1_result['slop'],
            combined_score=stage1_result['combined_score'],
            latency_ms=stage1_latency,
            requires_stage2=stage1_result['requires_stage2'],
            raw_entropy_score=stage1_result['raw_entropy_score'],
            normalized_entropy_score=stage1_result['normalized_entropy_score'],
            length_adjustment_applied=stage1_result['length_adjustment']
        )
        
        # Stage 1에서 확정된 경우
        if not force_stage2:
            if stage1.verdict == QualityVerdict.FAIL:
                return self._build_result(
                    final_verdict=QualityVerdict.FAIL,
                    stage1=stage1,
                    text_hash=text_hash,
                    text_length=len(text),
                    timestamp=timestamp,
                    start_time=start_time,
                    kernel_action="REJECT_BY_STAGE1"
                )
            
            if stage1.verdict == QualityVerdict.PASS and not stage1.requires_stage2:
                return self._build_result(
                    final_verdict=QualityVerdict.PASS,
                    stage1=stage1,
                    text_hash=text_hash,
                    text_length=len(text),
                    timestamp=timestamp,
                    start_time=start_time,
                    kernel_action="PASS_BY_STAGE1"
                )
        
        # ==========================================
        # STAGE 2: Strong Verifier (LLM)
        # ==========================================
        
        # 적응형 정책에서 Stage 2 건너뛰기 판단
        if self.adaptive_policy and self.adaptive_policy.should_skip_stage2():
            return self._build_result(
                final_verdict=QualityVerdict.PASS_WITH_WARNING,
                stage1=stage1,
                text_hash=text_hash,
                text_length=len(text),
                timestamp=timestamp,
                start_time=start_time,
                kernel_action="PASS_WITH_WARNING_BUDGET_LIMIT"
            )
        
        if skip_stage2 or not self.llm_verifier:
            # Stage 2 없이 불확실 상태로 통과
            return self._build_result(
                final_verdict=QualityVerdict.PASS_WITH_WARNING,
                stage1=stage1,
                text_hash=text_hash,
                text_length=len(text),
                timestamp=timestamp,
                start_time=start_time,
                kernel_action="PASS_WITH_WARNING_NO_STAGE2"
            )
        
        # Stage 2 호출 기록 (적응형 정책)
        if self.adaptive_policy:
            self.adaptive_policy.record_stage2_call()
        
        stage2_start = time.time()
        stage2_result = self._run_stage2(text)
        stage2_latency = (time.time() - stage2_start) * 1000
        
        stage2 = Stage2Result(
            verdict=stage2_result['verdict'],
            llm_score=stage2_result['score'],
            llm_feedback=stage2_result['feedback'],
            model_used=stage2_result['model'],
            latency_ms=stage2_latency,
            cost_usd=stage2_result['cost']
        )
        
        # 최종 판정
        final_verdict = stage2.verdict
        kernel_action = f"{'PASS' if final_verdict == QualityVerdict.PASS else 'REJECT'}_BY_STAGE2"
        
        return self._build_result(
            final_verdict=final_verdict,
            stage1=stage1,
            stage2=stage2,
            text_hash=text_hash,
            text_length=len(text),
            timestamp=timestamp,
            start_time=start_time,
            kernel_action=kernel_action
        )
    
    def _run_stage1(self, text: str) -> Dict:
        """
        Stage 1: Local Heuristic 실행
        
        Length-based Normalization 적용:
        - 짧은 텍스트에서도 말도 높은 정보(예: 핵심 수치)가 억울하게 거부되지 않도록
        - 로그 정규화(Log-normalization) 적용
        
        Shannon Entropy Formula:
        H(X) = -Σ P(x_i) * log₂(P(x_i))
        
        Normalized Formula:
        H_norm(X) = H(X) * (1 + α * log₂(1 + N/N_ref))
        """
        # 엔트로피 분석
        entropy_result = self.entropy_analyzer.analyze(text)
        
        # 슬롭 탐지
        slop_result = self.slop_detector.detect(text)
        
        # ============================================================
        # 길이 정규화된 엔트로피 사용 (Log-normalization)
        # 짧지만 강렬한 문장이 억울하게 UNCERTAIN으로 빠지지 않도록 보정
        # ============================================================
        raw_word_entropy = entropy_result.word_entropy
        normalized_word_entropy = entropy_result.normalized_word_entropy
        length_adjustment = entropy_result.length_adjustment_factor
        
        # 정규화된 엔트로피를 점수 계산에 사용
        raw_entropy_score = min(1.0, raw_word_entropy / 6.0)
        normalized_entropy_score = min(1.0, normalized_word_entropy / 6.0)
        
        # 슬롭: 낮을수록 좋음 (반전)
        slop_score = 1.0 - slop_result.slop_score
        
        # 가중 평균 (정규화된 엔트로피 사용)
        combined_score = (
            self.entropy_weight * normalized_entropy_score +
            self.slop_weight * slop_score
        )
        
        # ============================================================
        # 적응형 정책에 따른 동적 임계값 적용
        # ============================================================
        if self.adaptive_policy:
            threshold_low, threshold_high = self.adaptive_policy.get_effective_thresholds()
        else:
            threshold_low = self.stage2_threshold_low
            threshold_high = self.stage2_threshold_high
        
        # 판정
        if combined_score < threshold_low:
            verdict = QualityVerdict.FAIL
            requires_stage2 = False
        elif combined_score > threshold_high:
            verdict = QualityVerdict.PASS
            requires_stage2 = False
        else:
            verdict = QualityVerdict.UNCERTAIN
            requires_stage2 = True
            
            # 적응형 정책에서 Stage 2 건너뛰기 판단
            if self.adaptive_policy and self.adaptive_policy.should_skip_stage2():
                verdict = QualityVerdict.PASS_WITH_WARNING
                requires_stage2 = False
        
        return {
            'verdict': verdict,
            'entropy': entropy_result,
            'slop': slop_result,
            'combined_score': combined_score,
            'requires_stage2': requires_stage2,
            'raw_entropy_score': raw_entropy_score,
            'normalized_entropy_score': normalized_entropy_score,
            'length_adjustment': length_adjustment
        }
    
    def _run_stage2(self, text: str) -> Dict:
        """Stage 2: LLM Verifier 실행"""
        if not self.llm_verifier:
            return {
                'verdict': QualityVerdict.PASS_WITH_WARNING,
                'score': 0,
                'feedback': 'No LLM verifier configured',
                'model': 'none',
                'cost': 0.0
            }
        
        # 텍스트 길이 제한 (비용 절감)
        truncated_text = text[:2000] if len(text) > 2000 else text
        
        user_prompt = self.VERIFIER_USER_PROMPT_TEMPLATE.format(text=truncated_text)
        
        try:
            # LLM 호출 (외부 주입된 함수 사용)
            response = self.llm_verifier(
                system_prompt=self.VERIFIER_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                model="gemini-1.5-flash-8b",
                max_tokens=100
            )
            
            # 응답 파싱
            verdict, score, feedback = self._parse_verifier_response(response)
            
            # 비용 추정 (Gemini Flash-8B 기준)
            # Input: ~$0.0375/1M tokens, Output: ~$0.15/1M tokens
            input_tokens = len(user_prompt) // 4
            output_tokens = len(response) // 4
            cost = (input_tokens * 0.0000000375) + (output_tokens * 0.00000015)
            
            return {
                'verdict': verdict,
                'score': score,
                'feedback': feedback,
                'model': 'gemini-1.5-flash-8b',
                'cost': cost
            }
            
        except Exception as e:
            return {
                'verdict': QualityVerdict.PASS_WITH_WARNING,
                'score': 0,
                'feedback': f'LLM verification failed: {str(e)}',
                'model': 'gemini-1.5-flash-8b',
                'cost': 0.0
            }
    
    def _parse_verifier_response(self, response: str) -> tuple:
        """LLM 응답 파싱"""
        import re
        
        response = response.strip()
        
        # REJECT: 5 - reason 또는 APPROVE: 8 - reason 형식 파싱
        reject_match = re.match(r'REJECT:\s*(\d+)\s*-\s*(.+)', response, re.IGNORECASE)
        approve_match = re.match(r'APPROVE:\s*(\d+)\s*-\s*(.+)', response, re.IGNORECASE)
        
        if reject_match:
            score = int(reject_match.group(1))
            feedback = reject_match.group(2).strip()
            return QualityVerdict.FAIL, score, feedback
        
        if approve_match:
            score = int(approve_match.group(1))
            feedback = approve_match.group(2).strip()
            return QualityVerdict.PASS, score, feedback
        
        # 파싱 실패 시 숫자만 추출 시도
        numbers = re.findall(r'\d+', response)
        if numbers:
            score = int(numbers[0])
            if score >= 7:
                return QualityVerdict.PASS, score, response
            else:
                return QualityVerdict.FAIL, score, response
        
        # 완전 실패
        return QualityVerdict.UNCERTAIN, 0, response
    
    def _build_result(
        self,
        final_verdict: QualityVerdict,
        stage1: Stage1Result,
        text_hash: str,
        text_length: int,
        timestamp: str,
        start_time: float,
        kernel_action: str,
        stage2: Optional[Stage2Result] = None
    ) -> QualityGateResult:
        """결과 객체 빌드"""
        import time
        
        total_latency = (time.time() - start_time) * 1000
        total_cost = stage2.cost_usd if stage2 else 0.0
        
        return QualityGateResult(
            final_verdict=final_verdict,
            stage1=stage1,
            stage2=stage2,
            text_hash=text_hash,
            text_length=text_length,
            domain=self.domain,
            timestamp=timestamp,
            total_latency_ms=total_latency,
            total_cost_usd=total_cost,
            kernel_intervention=True,
            kernel_action=kernel_action
        )
    
    @staticmethod
    def quick_check(text: str) -> bool:
        """
        빠른 품질 체크 (Stage 1만)
        
        Cost: $0
        Latency: < 5ms
        
        Returns:
            bool: True if likely quality content
        """
        # 빠른 엔트로피 체크
        if not EntropyAnalyzer.quick_entropy_check(text, min_threshold=3.8):
            return False
        
        # 빠른 슬롭 체크
        if SlopDetector.quick_slop_check(text):
            return False
        
        return True


# ============================================================
# 커널 미들웨어 통합용 데코레이터
# ============================================================

def quality_gate_middleware(
    domain: ContentDomain = ContentDomain.GENERAL_TEXT,
    reject_on_fail: bool = True,
    log_results: bool = True
):
    """
    품질 게이트 미들웨어 데코레이터
    
    Usage:
        @quality_gate_middleware(domain=ContentDomain.TECHNICAL_REPORT)
        def my_llm_handler(state):
            response = call_llm(...)
            return response
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            # 함수 실행
            result = func(*args, **kwargs)
            
            # 결과가 문자열인 경우 품질 검사
            if isinstance(result, str):
                gate = QualityGate(domain=domain)
                check_result = gate.evaluate(result, skip_stage2=True)
                
                if log_results:
                    print(f"[QualityGate] Verdict: {check_result.final_verdict.value}, "
                          f"Score: {check_result.stage1.combined_score:.3f}")
                
                if reject_on_fail and check_result.final_verdict == QualityVerdict.FAIL:
                    raise QualityGateError(
                        f"Content rejected by quality gate: {check_result.stage1.slop_result.recommendation}"
                    )
            
            return result
        return wrapper
    return decorator


class QualityGateError(Exception):
    """품질 게이트 거부 예외"""
    pass


class GuardrailTriggeredError(Exception):
    """가드레일 발동 예외"""
    def __init__(self, message: str, decision: GuardrailDecision):
        super().__init__(message)
        self.decision = decision


# ============================================================
# 재생성 루프 with 비용 가드레일
# ============================================================

class QualityGateWithGuardrails:
    """
    비용 가드레일이 통합된 품질 게이트
    
    4단계 가드레일:
        1. Retry Guard - 재생성 쿼터 제한 (MAX 3회)
        2. Budget Watchdog - 비용 서킷 브레이커 ($0.5)
        3. Adaptive Threshold - 단계적 품질 완화 (0.7 → 0.3)
        4. Drift Detector - 시맨틱 드리프트 감지 (95%)
    
    Usage:
        gate = QualityGateWithGuardrails(
            workflow_id="report-generation",
            max_budget_usd=0.5,
            max_retries=3
        )
        
        # 재생성 루프 with 가드레일
        result = gate.evaluate_with_regeneration(
            node_id="generate_summary",
            generator_func=lambda: call_llm(...),
            initial_text=None  # None이면 generator_func 호출
        )
        
        if result.guardrail_triggered:
            print(f"Guardrail: {result.guardrail_action}")
    """
    
    def __init__(
        self,
        workflow_id: str = "default",
        domain: ContentDomain = ContentDomain.GENERAL_TEXT,
        max_budget_usd: float = 0.5,
        max_retries: int = 3,
        llm_verifier: Optional[Callable] = None,
        enable_drift_detection: bool = True
    ):
        self.workflow_id = workflow_id
        self.domain = domain
        self.max_retries = max_retries
        
        # 품질 게이트
        self.quality_gate = QualityGate(
            domain=domain,
            llm_verifier=llm_verifier
        )
        
        # 비용 가드레일
        self.guardrail = CostGuardrailSystem(
            workflow_id=workflow_id,
            max_budget_usd=max_budget_usd,
            max_retries_per_node=max_retries,
            enable_drift_detection=enable_drift_detection
        )
        
        # 가드레일 연결
        self.quality_gate.attach_guardrail(self.guardrail)
    
    def evaluate_with_regeneration(
        self,
        node_id: str,
        generator_func: Callable[[], Tuple[str, int, int]],
        initial_text: Optional[str] = None,
        initial_tokens: Tuple[int, int] = (0, 0),
        model: str = 'gemini-1.5-flash'
    ) -> QualityGateResult:
        """
        재생성 루프 실행 with 가드레일
        
        Args:
            node_id: 노드 식별자
            generator_func: LLM 호출 함수 (반환: (text, input_tokens, output_tokens))
            initial_text: 초기 텍스트 (없으면 generator_func 호출)
            initial_tokens: 초기 토큰 사용량 (input, output)
            model: 사용 모델명
            
        Returns:
            QualityGateResult with regeneration info
        """
        import time
        start_time = time.time()
        
        current_text = initial_text
        current_model = model
        attempt = 0
        best_result: Optional[QualityGateResult] = None
        best_score = -1.0
        
        while True:
            # ============================================================
            # Step 1: 텍스트 생성 (필요시)
            # ============================================================
            if current_text is None:
                try:
                    gen_result = generator_func()
                    if isinstance(gen_result, tuple):
                        current_text, input_tokens, output_tokens = gen_result
                    else:
                        current_text = gen_result
                        input_tokens, output_tokens = 500, 200  # 추정치
                except Exception as e:
                    logger.error(f"[QualityGate] Generator failed: {e}")
                    raise
            else:
                input_tokens, output_tokens = initial_tokens
            
            attempt += 1
            
            # ============================================================
            # Step 2: 품질 평가
            # ============================================================
            quality_result = self.quality_gate.evaluate(current_text, skip_stage2=True)
            quality_score = quality_result.stage1.combined_score
            
            # 최고 점수 추적
            if quality_score > best_score:
                best_score = quality_score
                best_result = quality_result
                best_text = current_text
            
            # ============================================================
            # Step 3: 품질 통과 시 종료
            # ============================================================
            if quality_result.final_verdict == QualityVerdict.PASS:
                regen_result = RegenerationResult(
                    success=True,
                    attempt_count=attempt,
                    final_quality_score=quality_score,
                    guardrail_triggered=False,
                    degraded_threshold_used=self.guardrail.get_retry_state(node_id).current_threshold,
                    model_switched=(current_model != model),
                    final_model=current_model
                )
                quality_result.regeneration_result = regen_result
                return quality_result
            
            # ============================================================
            # Step 4: 가드레일 평가
            # ============================================================
            guardrail_decision = self.guardrail.evaluate_regeneration_request(
                node_id=node_id,
                quality_score=quality_score,
                response_text=current_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=current_model
            )
            
            # ============================================================
            # Step 5: 가드레일 액션 처리
            # ============================================================
            
            # EMERGENCY_STOP: 즉시 중단
            if guardrail_decision.action == GuardrailAction.EMERGENCY_STOP:
                logger.warning(f"[QualityGate] EMERGENCY_STOP triggered for node {node_id}")
                return self._build_guardrail_result(
                    best_result,
                    guardrail_decision,
                    attempt,
                    current_model,
                    model,
                    start_time,
                    user_message=guardrail_decision.user_facing_message or "예산 초과로 중단되었습니다."
                )
            
            # FORCE_BEST_EFFORT: 최선의 결과물 반환
            if guardrail_decision.action == GuardrailAction.FORCE_BEST_EFFORT:
                logger.info(f"[QualityGate] FORCE_BEST_EFFORT for node {node_id}")
                return self._build_guardrail_result(
                    best_result,
                    guardrail_decision,
                    attempt,
                    current_model,
                    model,
                    start_time,
                    user_message=guardrail_decision.user_facing_message or "최대 시도 횟수에 도달하여 최선의 결과물을 반환합니다."
                )
            
            # ESCALATE_TO_HITL: 인간 검토로 전환
            if guardrail_decision.action == GuardrailAction.ESCALATE_TO_HITL:
                logger.info(f"[QualityGate] ESCALATE_TO_HITL for node {node_id}")
                return self._build_guardrail_result(
                    best_result,
                    guardrail_decision,
                    attempt,
                    current_model,
                    model,
                    start_time,
                    user_message=guardrail_decision.user_facing_message or "AI가 품질 개선에 어려움을 겪고 있습니다. 인간 검토가 필요합니다."
                )
            
            # SWITCH_MODEL: 모델 스위칭
            if guardrail_decision.action == GuardrailAction.SWITCH_MODEL:
                if guardrail_decision.recommended_model:
                    current_model = guardrail_decision.recommended_model
                    logger.info(f"[QualityGate] Switching model to {current_model}")
            
            # LOWER_THRESHOLD / ALLOW_REGENERATION: 임계값 조정 후 재시도
            if guardrail_decision.adjusted_threshold:
                retry_state = self.guardrail.get_retry_state(node_id)
                retry_state.current_threshold = guardrail_decision.adjusted_threshold
            
            # 다음 시도를 위해 텍스트 초기화
            current_text = None
    
    def _build_guardrail_result(
        self,
        best_result: Optional[QualityGateResult],
        decision: GuardrailDecision,
        attempt: int,
        current_model: str,
        original_model: str,
        start_time: float,
        user_message: str
    ) -> QualityGateResult:
        """가드레일 발동 시 결과 빌드"""
        import time
        
        if best_result is None:
            # 결과가 없는 경우 (첫 시도에서 실패)
            from .entropy_analyzer import EntropyAnalysisResult
            from .slop_detector import SlopDetectionResult
            
            empty_entropy = EntropyAnalysisResult(
                char_entropy=0, word_entropy=0, normalized_word_entropy=0,
                char_count=0, word_count=0, unique_chars=0, unique_words=0,
                vocabulary_richness=0, bigram_entropy=0, trigram_entropy=0,
                domain=self.domain, quality_level="POOR", analysis_details={}
            )
            empty_slop = SlopDetectionResult(
                is_slop=True, slop_score=1.0, matched_patterns=[],
                total_patterns_checked=0, recommendation="Unable to evaluate"
            )
            empty_stage1 = Stage1Result(
                verdict=QualityVerdict.FAIL,
                entropy_result=empty_entropy,
                slop_result=empty_slop,
                combined_score=0.0,
                latency_ms=0,
                requires_stage2=False
            )
            best_result = QualityGateResult(
                final_verdict=QualityVerdict.FAIL,
                stage1=empty_stage1
            )
        
        # 재생성 결과 설정
        best_result.regeneration_result = RegenerationResult(
            success=False,
            attempt_count=attempt,
            final_quality_score=best_result.stage1.combined_score,
            guardrail_triggered=True,
            guardrail_action=decision.action.value,
            degraded_threshold_used=decision.adjusted_threshold or 0.7,
            model_switched=(current_model != original_model),
            final_model=current_model,
            user_message=user_message
        )
        
        best_result.guardrail_decision = decision
        best_result.final_verdict = QualityVerdict.PASS_WITH_WARNING
        best_result.kernel_action = f"GUARDRAIL_{decision.action.value}"
        best_result.total_latency_ms = (time.time() - start_time) * 1000
        best_result.total_cost_usd = self.guardrail.budget_state.current_cost_usd
        
        return best_result
    
    def get_guardrail_summary(self) -> Dict:
        """가드레일 시스템 상태 요약"""
        return self.guardrail.get_summary()
    
    def reset(self):
        """상태 리셋"""
        self.guardrail.reset_all()
