"""
Cost Guardrails - 비용 가드레일 시스템
======================================

품질 게이트의 "무한 재귀적 비용 폭증(Recursive Cost Explosion)" 방지.

4단계 가드레일:
    1. Retry Guard - 재생성 쿼터 제한 (MAX_REGENERATION_ATTEMPTS)
    2. Budget Watchdog - 비용 인지형 서킷 브레이커 ($Budget_max)
    3. Adaptive Threshold - 단계적 품질 완화 (Graceful Degradation)
    4. Drift Detector - 시맨틱 드리프트 감지 (임베딩 유사도)

                    ┌─────────────────────────────────────┐
                    │         QUALITY GATE REJECT         │
                    └─────────────────────────────────────┘
                                      │
                                      ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │                     COST GUARDRAIL SYSTEM                        │
    │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐ │
    │  │ Retry      │  │ Budget     │  │ Adaptive   │  │ Drift      │ │
    │  │ Guard      │  │ Watchdog   │  │ Threshold  │  │ Detector   │ │
    │  │ (3 max)    │  │ ($0.5 max) │  │ (0.7→0.3)  │  │ (95% sim)  │ │
    │  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘ │
    │        └────────────────┼──────────────┼───────────────┘        │
    │                         ▼              ▼                         │
    │              ┌──────────────────────────────────┐               │
    │              │     GUARDRAIL DECISION ENGINE    │               │
    │              └──────────────────────────────────┘               │
    └─────────────────────────────────────────────────────────────────┘
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            ▼                         ▼                         ▼
    ┌───────────────┐       ┌───────────────┐       ┌───────────────┐
    │   REGENERATE  │       │  BEST-EFFORT  │       │   EMERGENCY   │
    │   (quota ok)  │       │    OUTPUT     │       │     STOP      │
    └───────────────┘       └───────────────┘       └───────────────┘
"""

import hashlib
import time
import os
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any, Callable
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class GuardrailAction(Enum):
    """가드레일 액션"""
    ALLOW_REGENERATION = "ALLOW_REGENERATION"      # 재생성 허용
    FORCE_BEST_EFFORT = "FORCE_BEST_EFFORT"        # 최선의 결과물 강제 반환
    ESCALATE_TO_HITL = "ESCALATE_TO_HITL"          # 인간 승인 단계로 전환
    EMERGENCY_STOP = "EMERGENCY_STOP"              # 비상 정지
    LOWER_THRESHOLD = "LOWER_THRESHOLD"            # 임계값 하향 후 재시도
    SWITCH_MODEL = "SWITCH_MODEL"                  # 모델 스위칭


class GuardrailTrigger(Enum):
    """가드레일 트리거 원인"""
    NONE = "none"
    RETRY_QUOTA_EXCEEDED = "retry_quota_exceeded"
    BUDGET_LIMIT_REACHED = "budget_limit_reached"
    SEMANTIC_DRIFT_DETECTED = "semantic_drift_detected"
    QUALITY_THRESHOLD_FLOOR = "quality_threshold_floor"
    EMERGENCY_BUDGET_BREACH = "emergency_budget_breach"


@dataclass
class RetryState:
    """노드별 재시도 상태 추적"""
    node_id: str
    attempt_count: int = 0
    max_attempts: int = 3
    
    # 각 시도의 품질 점수 기록
    quality_scores: List[float] = field(default_factory=list)
    
    # 이전 답변 해시 (드리프트 감지용)
    previous_response_hashes: List[str] = field(default_factory=list)
    previous_response_snippets: List[str] = field(default_factory=list)
    
    # 적응형 임계값
    current_threshold: float = 0.7
    threshold_history: List[float] = field(default_factory=list)
    
    def record_attempt(self, quality_score: float, response_text: str):
        """시도 기록"""
        self.attempt_count += 1
        self.quality_scores.append(quality_score)
        self.threshold_history.append(self.current_threshold)
        
        # 응답 해시 저장 (드리프트 감지용)
        response_hash = hashlib.sha256(response_text.encode()).hexdigest()[:32]
        self.previous_response_hashes.append(response_hash)
        self.previous_response_snippets.append(response_text[:500])
    
    def is_quota_exceeded(self) -> bool:
        """재생성 쿼터 초과 여부"""
        return self.attempt_count >= self.max_attempts
    
    def get_degraded_threshold(self) -> float:
        """단계적 완화된 임계값 반환"""
        # 시도 횟수에 따라 임계값 하향
        degradation_schedule = {
            0: 0.7,   # 1차 시도: Strict
            1: 0.5,   # 2차 시도: Balanced
            2: 0.3,   # 3차 시도: Pass-through
            3: 0.2,   # 최종: Minimal
        }
        return degradation_schedule.get(self.attempt_count, 0.2)
    
    def is_improving(self) -> bool:
        """품질 개선 중인지 확인"""
        if len(self.quality_scores) < 2:
            return True  # 데이터 부족, 개선 중으로 가정
        
        # 최근 2개 비교
        recent = self.quality_scores[-1]
        previous = self.quality_scores[-2]
        
        return recent > previous + 0.05  # 5% 이상 개선
    
    def to_dict(self) -> Dict:
        return {
            'node_id': self.node_id,
            'attempt_count': self.attempt_count,
            'max_attempts': self.max_attempts,
            'quality_scores': [round(s, 4) for s in self.quality_scores],
            'current_threshold': round(self.current_threshold, 3),
            'is_quota_exceeded': self.is_quota_exceeded()
        }


@dataclass
class ModelPricing:
    """
    동적 모델 가격 관리
    
    환경변수 또는 런타임 설정에서 가격 로드.
    Context Caching 할인 반영.
    """
    # 기본 가격 ($/1M tokens) - 2026년 1월 기준
    DEFAULT_PRICING = {
        'gemini-2.0-flash': {'input': 0.10, 'output': 0.40, 'cached_input': 0.025},
        'gemini-2.0-flash-lite': {'input': 0.075, 'output': 0.30, 'cached_input': 0.01875},
        'gemini-1.5-pro': {'input': 1.25, 'output': 5.0, 'cached_input': 0.3125},
        'gemini-1.5-flash': {'input': 0.075, 'output': 0.30, 'cached_input': 0.01875},
        'gemini-1.5-flash-8b': {'input': 0.0375, 'output': 0.15, 'cached_input': 0.01},
    }
    
    # Context Caching 활성화 여부
    context_caching_enabled: bool = True
    
    # 캐시 히트율 (0.0 ~ 1.0, 실제 캐시 적중률)
    cache_hit_ratio: float = 0.3  # 기본 30% 캐시 히트 가정
    
    # 환경변수 오버라이드 접두사
    ENV_PREFIX: str = "ANALEMMA_PRICING_"
    
    @classmethod
    def get_pricing(cls, model: str) -> Dict[str, float]:
        """
        모델별 가격 조회 (with 환경변수 오버라이드)
        
        환경변수 형식:
        - ANALEMMA_PRICING_GEMINI_1_5_FLASH_INPUT=0.08
        - ANALEMMA_PRICING_GEMINI_1_5_FLASH_OUTPUT=0.32
        """
        base_pricing = cls.DEFAULT_PRICING.get(
            model,
            cls.DEFAULT_PRICING['gemini-1.5-flash']
        ).copy()
        
        # 환경변수에서 오버라이드 체크
        env_key = model.upper().replace('-', '_').replace('.', '_')
        
        for price_type in ['input', 'output', 'cached_input']:
            env_var = f"{cls.ENV_PREFIX}{env_key}_{price_type.upper()}"
            env_value = os.environ.get(env_var)
            if env_value:
                try:
                    base_pricing[price_type] = float(env_value)
                    logger.debug(f"Pricing override: {env_var}={env_value}")
                except ValueError:
                    pass
        
        return base_pricing
    
    @classmethod
    def calculate_cost(
        cls,
        input_tokens: int,
        output_tokens: int,
        model: str,
        cached_tokens: int = 0
    ) -> Tuple[float, Dict]:
        """
        비용 계산 (Context Caching 반영)
        
        Expected Cost = Σ(Input_i × P_in + Output_i × P_out)
                      - (Cached_i × (P_in - P_cached))
        
        Returns:
            Tuple[total_cost, cost_breakdown]
        """
        pricing = cls.get_pricing(model)
        
        # 일반 입력 토큰 비용
        regular_input_tokens = input_tokens - cached_tokens
        regular_input_cost = (regular_input_tokens / 1_000_000) * pricing['input']
        
        # 캐시된 토큰 비용 (할인 적용)
        cached_input_cost = (cached_tokens / 1_000_000) * pricing.get('cached_input', pricing['input'] * 0.25)
        
        # 출력 토큰 비용
        output_cost = (output_tokens / 1_000_000) * pricing['output']
        
        total_cost = regular_input_cost + cached_input_cost + output_cost
        
        # 절감액 계산
        full_input_cost = (input_tokens / 1_000_000) * pricing['input']
        savings = full_input_cost - (regular_input_cost + cached_input_cost)
        
        breakdown = {
            'regular_input_cost': round(regular_input_cost, 8),
            'cached_input_cost': round(cached_input_cost, 8),
            'output_cost': round(output_cost, 8),
            'total_cost': round(total_cost, 8),
            'cache_savings': round(savings, 8),
            'model': model,
            'pricing_used': pricing
        }
        
        return total_cost, breakdown
    
    @classmethod
    def estimate_cached_tokens(
        cls,
        input_tokens: int,
        workflow_context: Optional[Dict] = None
    ) -> int:
        """
        캐시 토큰 추정
        
        워크플로우 컨텍스트가 있으면 캐시 히트율 조정
        """
        base_cache_ratio = cls().cache_hit_ratio
        
        if workflow_context:
            # 워크플로우에서 캐시 히트율 힌트 추출
            cache_hint = workflow_context.get('cache_hit_ratio', base_cache_ratio)
            # 누적 노드 수가 많을수록 캐시 확률 증가
            node_count = workflow_context.get('executed_node_count', 0)
            if node_count > 3:
                cache_hint = min(0.6, cache_hint + 0.1)
        else:
            cache_hint = base_cache_ratio
        
        return int(input_tokens * cache_hint)


@dataclass
class BudgetState:
    """워크플로우 예산 상태 추적"""
    workflow_id: str
    max_budget_usd: float = 0.5  # 기본 $0.5
    
    # 비용 추적
    current_cost_usd: float = 0.0
    cost_breakdown: Dict[str, float] = field(default_factory=dict)
    cost_details: List[Dict] = field(default_factory=list)  # 상세 비용 기록
    
    # 토큰 추적
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0  # 캐시 히트 토큰
    total_cache_savings: float = 0.0  # 캐시로 인한 절감액
    
    # 임계값
    warning_threshold: float = 0.8   # 80%에서 경고
    emergency_threshold: float = 0.95  # 95%에서 비상
    
    # 워크플로우 컨텍스트 (캐시 추정용)
    workflow_context: Optional[Dict] = None
    
    def add_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str = 'gemini-1.5-flash',
        node_id: str = 'unknown',
        cached_tokens: Optional[int] = None
    ) -> float:
        """
        비용 추가 (Context Caching 반영)
        
        Args:
            input_tokens: 입력 토큰 수
            output_tokens: 출력 토큰 수
            model: 모델명
            node_id: 노드 ID
            cached_tokens: 캐시 히트 토큰 (없으면 자동 추정)
        """
        # 캐시 토큰 추정 (명시적으로 제공되지 않으면)
        if cached_tokens is None:
            cached_tokens = ModelPricing.estimate_cached_tokens(
                input_tokens,
                self.workflow_context
            )
        
        # 비용 계산
        total_cost, breakdown = ModelPricing.calculate_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            cached_tokens=cached_tokens
        )
        
        # 상태 업데이트
        self.current_cost_usd += total_cost
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cached_tokens += cached_tokens
        self.total_cache_savings += breakdown['cache_savings']
        
        # 노드별 비용 기록
        if node_id not in self.cost_breakdown:
            self.cost_breakdown[node_id] = 0.0
        self.cost_breakdown[node_id] += total_cost
        
        # 상세 로그
        self.cost_details.append({
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'node_id': node_id,
            'model': model,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cached_tokens': cached_tokens,
            **breakdown
        })
        
        return self.current_cost_usd
    
    def get_budget_ratio(self) -> float:
        """예산 사용 비율 (0.0 ~ 1.0+)"""
        if self.max_budget_usd <= 0:
            return 1.0
        return self.current_cost_usd / self.max_budget_usd
    
    def is_warning_zone(self) -> bool:
        """경고 구간 진입 여부"""
        return self.get_budget_ratio() >= self.warning_threshold
    
    def is_emergency_zone(self) -> bool:
        """비상 구간 진입 여부"""
        return self.get_budget_ratio() >= self.emergency_threshold
    
    def is_exceeded(self) -> bool:
        """예산 초과 여부"""
        return self.current_cost_usd >= self.max_budget_usd
    
    def get_remaining_budget(self) -> float:
        """남은 예산"""
        return max(0, self.max_budget_usd - self.current_cost_usd)
    
    def to_dict(self) -> Dict:
        return {
            'workflow_id': self.workflow_id,
            'budget': {
                'max_usd': round(self.max_budget_usd, 4),
                'current_usd': round(self.current_cost_usd, 6),
                'remaining_usd': round(self.get_remaining_budget(), 6),
                'usage_ratio': round(self.get_budget_ratio(), 4)
            },
            'tokens': {
                'input': self.total_input_tokens,
                'output': self.total_output_tokens,
                'cached': self.total_cached_tokens,
                'cache_hit_ratio': round(
                    self.total_cached_tokens / self.total_input_tokens, 4
                ) if self.total_input_tokens > 0 else 0.0
            },
            'cache_savings': {
                'total_savings_usd': round(self.total_cache_savings, 6),
                'effective_discount_pct': round(
                    (self.total_cache_savings / self.current_cost_usd) * 100, 2
                ) if self.current_cost_usd > 0 else 0.0
            },
            'status': {
                'is_warning': self.is_warning_zone(),
                'is_emergency': self.is_emergency_zone(),
                'is_exceeded': self.is_exceeded()
            },
            'cost_breakdown': {k: round(v, 6) for k, v in self.cost_breakdown.items()},
            'cost_details': self.cost_details[-10:]  # 최근 10개만
        }


@dataclass
class DriftDetectionResult:
    """시맨틱 드리프트 감지 결과"""
    is_drifting: bool = False
    similarity_score: float = 0.0
    quality_improvement: float = 0.0
    is_stuck_in_loop: bool = False
    recommendation: str = ""
    
    # LLM 시맨틱 검증 결과 (n-gram 유사도 0.7~0.95 구간에서 사용)
    llm_verified: bool = False
    llm_semantic_same: Optional[bool] = None  # True: 본질적으로 동일, False: 의미있는 차이 있음
    llm_verification_reason: str = ""
    
    def to_dict(self) -> Dict:
        result = {
            'is_drifting': self.is_drifting,
            'similarity_score': round(self.similarity_score, 4),
            'quality_improvement': round(self.quality_improvement, 4),
            'is_stuck_in_loop': self.is_stuck_in_loop,
            'recommendation': self.recommendation
        }
        
        # LLM 검증이 수행된 경우에만 포함
        if self.llm_verified:
            result['llm_verification'] = {
                'semantic_same': self.llm_semantic_same,
                'reason': self.llm_verification_reason
            }
        
        return result


@dataclass
class GuardrailDecision:
    """가드레일 결정 결과"""
    action: GuardrailAction
    trigger: GuardrailTrigger
    
    # 상세 정보
    retry_state: Optional[RetryState] = None
    budget_state: Optional[BudgetState] = None
    drift_result: Optional[DriftDetectionResult] = None
    
    # 조정된 파라미터
    adjusted_threshold: Optional[float] = None
    recommended_model: Optional[str] = None
    
    # 메시지
    message: str = ""
    user_facing_message: str = ""
    
    def to_dict(self) -> Dict:
        return {
            'action': self.action.value,
            'trigger': self.trigger.value,
            'adjusted_threshold': self.adjusted_threshold,
            'recommended_model': self.recommended_model,
            'message': self.message,
            'user_facing_message': self.user_facing_message,
            'retry_state': self.retry_state.to_dict() if self.retry_state else None,
            'budget_state': self.budget_state.to_dict() if self.budget_state else None,
            'drift_result': self.drift_result.to_dict() if self.drift_result else None
        }


class CostGuardrailSystem:
    """
    비용 가드레일 통합 시스템
    
    4단계 가드레일:
        1. Retry Guard - 재생성 쿼터 제한
        2. Budget Watchdog - 비용 서킷 브레이커
        3. Adaptive Threshold - 단계적 품질 완화
        4. Drift Detector - 시맨틱 드리프트 감지
    
    Usage:
        guardrail = CostGuardrailSystem(
            max_budget_usd=0.5,
            max_retries_per_node=3
        )
        
        # 재생성 요청 시
        decision = guardrail.evaluate_regeneration_request(
            node_id="generate_report",
            quality_score=0.4,
            response_text="...",
            input_tokens=500,
            output_tokens=200
        )
        
        if decision.action == GuardrailAction.ALLOW_REGENERATION:
            # 재생성 진행, 조정된 임계값 사용
            new_threshold = decision.adjusted_threshold
        elif decision.action == GuardrailAction.FORCE_BEST_EFFORT:
            # 현재 결과물 강제 반환
            pass
        elif decision.action == GuardrailAction.EMERGENCY_STOP:
            # 워크플로우 중단
            pass
    """
    
    def __init__(
        self,
        workflow_id: str = "default",
        max_budget_usd: float = 0.5,
        max_retries_per_node: int = 3,
        similarity_threshold: float = 0.95,
        enable_drift_detection: bool = True
    ):
        self.workflow_id = workflow_id
        self.max_retries_per_node = max_retries_per_node
        self.similarity_threshold = similarity_threshold
        self.enable_drift_detection = enable_drift_detection
        
        # 상태 저장소
        self.budget_state = BudgetState(
            workflow_id=workflow_id,
            max_budget_usd=max_budget_usd
        )
        self.retry_states: Dict[str, RetryState] = {}
        
        # 이벤트 로그
        self.event_log: List[Dict] = []
    
    def get_retry_state(self, node_id: str) -> RetryState:
        """노드별 재시도 상태 조회/생성"""
        if node_id not in self.retry_states:
            self.retry_states[node_id] = RetryState(
                node_id=node_id,
                max_attempts=self.max_retries_per_node
            )
        return self.retry_states[node_id]
    
    def evaluate_regeneration_request(
        self,
        node_id: str,
        quality_score: float,
        response_text: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = 'gemini-1.5-flash'
    ) -> GuardrailDecision:
        """
        재생성 요청 평가 및 가드레일 결정
        
        Args:
            node_id: 노드 ID
            quality_score: 현재 품질 점수 (0.0 ~ 1.0)
            response_text: 현재 응답 텍스트
            input_tokens: 사용된 입력 토큰
            output_tokens: 사용된 출력 토큰
            model: 사용된 모델
            
        Returns:
            GuardrailDecision: 가드레일 결정
        """
        timestamp = datetime.utcnow().isoformat() + 'Z'
        
        # 상태 업데이트
        retry_state = self.get_retry_state(node_id)
        retry_state.record_attempt(quality_score, response_text)
        
        # 비용 업데이트
        self.budget_state.add_cost(input_tokens, output_tokens, model, node_id)
        
        # ============================================================
        # GUARDRAIL 1: 비상 예산 초과 체크 (최우선)
        # ============================================================
        if self.budget_state.is_exceeded():
            decision = GuardrailDecision(
                action=GuardrailAction.EMERGENCY_STOP,
                trigger=GuardrailTrigger.EMERGENCY_BUDGET_BREACH,
                retry_state=retry_state,
                budget_state=self.budget_state,
                message=f"Budget exceeded: ${self.budget_state.current_cost_usd:.4f} / ${self.budget_state.max_budget_usd:.4f}",
                user_facing_message="예산이 초과되어 워크플로우가 중단되었습니다. 현재까지의 결과물을 반환합니다."
            )
            self._log_event('EMERGENCY_STOP', node_id, decision)
            return decision
        
        # ============================================================
        # GUARDRAIL 2: 재생성 쿼터 체크
        # ============================================================
        if retry_state.is_quota_exceeded():
            decision = GuardrailDecision(
                action=GuardrailAction.FORCE_BEST_EFFORT,
                trigger=GuardrailTrigger.RETRY_QUOTA_EXCEEDED,
                retry_state=retry_state,
                budget_state=self.budget_state,
                message=f"Retry quota exceeded: {retry_state.attempt_count}/{retry_state.max_attempts}",
                user_facing_message="최대 재시도 횟수에 도달했습니다. 최선의 결과물을 반환합니다."
            )
            self._log_event('RETRY_QUOTA_EXCEEDED', node_id, decision)
            return decision
        
        # ============================================================
        # GUARDRAIL 3: 시맨틱 드리프트 감지
        # ============================================================
        if self.enable_drift_detection and len(retry_state.quality_scores) >= 2:
            drift_result = self._detect_semantic_drift(retry_state, response_text)
            
            if drift_result.is_stuck_in_loop:
                decision = GuardrailDecision(
                    action=GuardrailAction.ESCALATE_TO_HITL,
                    trigger=GuardrailTrigger.SEMANTIC_DRIFT_DETECTED,
                    retry_state=retry_state,
                    budget_state=self.budget_state,
                    drift_result=drift_result,
                    message="Semantic drift detected - AI is stuck in a loop",
                    user_facing_message="AI가 해당 요청에 대해 유의미한 품질 개선을 하지 못하고 있습니다. 인간 검토가 필요합니다."
                )
                self._log_event('SEMANTIC_DRIFT_DETECTED', node_id, decision)
                return decision
        
        # ============================================================
        # GUARDRAIL 4: 예산 경고 구간 → 임계값 하향
        # ============================================================
        if self.budget_state.is_warning_zone():
            degraded_threshold = retry_state.get_degraded_threshold()
            
            # 이미 바닥 임계값이면 Best-Effort
            if degraded_threshold <= 0.2:
                decision = GuardrailDecision(
                    action=GuardrailAction.FORCE_BEST_EFFORT,
                    trigger=GuardrailTrigger.QUALITY_THRESHOLD_FLOOR,
                    retry_state=retry_state,
                    budget_state=self.budget_state,
                    adjusted_threshold=degraded_threshold,
                    message="Quality threshold floor reached with budget warning",
                    user_facing_message="예산 제한으로 인해 현재 품질 수준의 결과물을 반환합니다."
                )
                self._log_event('THRESHOLD_FLOOR_BUDGET_WARNING', node_id, decision)
                return decision
            
            # 임계값 하향 후 재시도
            retry_state.current_threshold = degraded_threshold
            decision = GuardrailDecision(
                action=GuardrailAction.LOWER_THRESHOLD,
                trigger=GuardrailTrigger.BUDGET_LIMIT_REACHED,
                retry_state=retry_state,
                budget_state=self.budget_state,
                adjusted_threshold=degraded_threshold,
                message=f"Budget warning zone - lowering threshold to {degraded_threshold}",
                user_facing_message=None
            )
            self._log_event('LOWER_THRESHOLD_BUDGET', node_id, decision)
            return decision
        
        # ============================================================
        # GUARDRAIL 5: 단계적 품질 완화 (정상 경로)
        # ============================================================
        degraded_threshold = retry_state.get_degraded_threshold()
        retry_state.current_threshold = degraded_threshold
        
        # 품질 개선 여부 체크
        if not retry_state.is_improving() and retry_state.attempt_count >= 2:
            # 개선되지 않음 → 모델 스위칭 권장
            recommended_model = self._recommend_model_switch(retry_state)
            
            decision = GuardrailDecision(
                action=GuardrailAction.SWITCH_MODEL,
                trigger=GuardrailTrigger.NONE,
                retry_state=retry_state,
                budget_state=self.budget_state,
                adjusted_threshold=degraded_threshold,
                recommended_model=recommended_model,
                message=f"No quality improvement - recommending model switch to {recommended_model}",
                user_facing_message=None
            )
            self._log_event('SWITCH_MODEL', node_id, decision)
            return decision
        
        # 재생성 허용
        decision = GuardrailDecision(
            action=GuardrailAction.ALLOW_REGENERATION,
            trigger=GuardrailTrigger.NONE,
            retry_state=retry_state,
            budget_state=self.budget_state,
            adjusted_threshold=degraded_threshold,
            message=f"Regeneration allowed with threshold {degraded_threshold}",
            user_facing_message=None
        )
        self._log_event('ALLOW_REGENERATION', node_id, decision)
        return decision
    
    def _detect_semantic_drift(
        self,
        retry_state: RetryState,
        current_response: str
    ) -> DriftDetectionResult:
        """
        시맨틱 드리프트 감지 (2-Stage: N-gram + LLM)
        
        Stage 1: N-gram 유사도 계산 (저비용)
        Stage 2: 유사도 0.7~0.95 구간에서 LLM 시맨틱 검증 (경량 모델)
        
        이전 답변과 현재 답변의 유사도가 높으면서 품질이 개선되지 않으면
        "개선 불가능한 루프"로 판단
        """
        if len(retry_state.previous_response_snippets) < 1:
            return DriftDetectionResult(is_drifting=False)
        
        # Stage 1: 간단한 유사도 계산 (n-gram 기반)
        current_snippet = current_response[:500]
        previous_snippet = retry_state.previous_response_snippets[-1]
        
        similarity = self._calculate_similarity(current_snippet, previous_snippet)
        
        # 품질 개선량 계산
        quality_improvement = 0.0
        if len(retry_state.quality_scores) >= 2:
            quality_improvement = retry_state.quality_scores[-1] - retry_state.quality_scores[-2]
        
        # Stage 2: 애매한 구간 (0.7~0.95)에서 LLM 시맨틱 검증
        llm_verified = False
        llm_semantic_same = None
        llm_reason = ""
        
        GREY_ZONE_LOW = 0.70
        GREY_ZONE_HIGH = 0.95
        
        if GREY_ZONE_LOW <= similarity < GREY_ZONE_HIGH and quality_improvement < 0.05:
            # LLM 시맨틱 검증 수행 (비용 vs 정확도 트레이드오프)
            try:
                llm_result = self._verify_semantic_similarity_with_llm(
                    previous_snippet,
                    current_snippet
                )
                llm_verified = True
                llm_semantic_same = llm_result.get('is_same', False)
                llm_reason = llm_result.get('reason', '')
                
                # LLM이 "다르다"고 판단하면 드리프트 아님
                if not llm_semantic_same:
                    similarity = min(similarity, GREY_ZONE_LOW - 0.01)  # 강제 보정
                    
            except Exception as e:
                logger.warning(f"LLM drift verification failed: {e}")
                # 실패 시 n-gram 결과 유지
        
        # 드리프트 판정: 유사도 높고 품질 개선 없음
        is_stuck = similarity >= self.similarity_threshold and quality_improvement < 0.05
        
        return DriftDetectionResult(
            is_drifting=similarity >= 0.8,
            similarity_score=similarity,
            quality_improvement=quality_improvement,
            is_stuck_in_loop=is_stuck,
            recommendation="HITL_ESCALATION" if is_stuck else "CONTINUE",
            llm_verified=llm_verified,
            llm_semantic_same=llm_semantic_same,
            llm_verification_reason=llm_reason
        )
    
    def _verify_semantic_similarity_with_llm(
        self,
        previous_text: str,
        current_text: str
    ) -> Dict[str, Any]:
        """
        경량 LLM으로 시맨틱 유사도 검증
        
        Gemini Flash-8B 사용 (가장 저렴한 옵션)
        약 100 토큰 응답 기대 → ~$0.00001 비용
        
        Returns:
            {
                'is_same': bool,  # 본질적으로 같은 내용인가
                'reason': str,    # 판단 근거
                'confidence': float  # 신뢰도 (0.0~1.0)
            }
        """
        prompt = f'''You are a semantic similarity judge. Compare these two AI responses and determine if they are SUBSTANTIVELY THE SAME or MEANINGFULLY DIFFERENT.

PREVIOUS RESPONSE (snippet):
---
{previous_text[:300]}
---

CURRENT RESPONSE (snippet):
---
{current_text[:300]}
---

Consider:
1. Core meaning and key points
2. Factual content changes
3. Structural/organizational changes
4. Ignore: minor wording differences, punctuation, formatting

Respond in JSON format ONLY:
{{"is_same": true/false, "reason": "<one sentence explanation>", "confidence": 0.0-1.0}}'''

        try:
            # genai 모듈 동적 임포트 (순환 의존성 방지)
            import google.generativeai as genai
            
            # 가장 저렴한 모델 사용
            model = genai.GenerativeModel('gemini-1.5-flash-8b')
            
            response = model.generate_content(
                prompt,
                generation_config={
                    'temperature': 0.1,  # 일관된 판단
                    'max_output_tokens': 100,
                    'response_mime_type': 'application/json'
                }
            )
            
            # 응답 파싱
            result = json.loads(response.text)
            
            # 비용 추적 (예상 토큰)
            estimated_input = len(prompt) // 4
            estimated_output = 50
            self.budget_state.add_cost(
                input_tokens=estimated_input,
                output_tokens=estimated_output,
                model='gemini-1.5-flash-8b',
                node_id='_drift_verification'
            )
            
            return {
                'is_same': result.get('is_same', False),
                'reason': result.get('reason', ''),
                'confidence': result.get('confidence', 0.5)
            }
            
        except json.JSONDecodeError as e:
            logger.warning(f"LLM drift response parse error: {e}")
            return {'is_same': True, 'reason': 'Parse error - assuming same', 'confidence': 0.3}
        except Exception as e:
            logger.warning(f"LLM drift verification error: {e}")
            raise
    
    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """간단한 n-gram 기반 유사도 계산"""
        def get_ngrams(text: str, n: int = 3) -> set:
            text = text.lower()
            return set(text[i:i+n] for i in range(len(text) - n + 1))
        
        ngrams1 = get_ngrams(text1)
        ngrams2 = get_ngrams(text2)
        
        if not ngrams1 or not ngrams2:
            return 0.0
        
        intersection = len(ngrams1 & ngrams2)
        union = len(ngrams1 | ngrams2)
        
        return intersection / union if union > 0 else 0.0
    
    def _recommend_model_switch(self, retry_state: RetryState) -> str:
        """
        모델 스위칭 권장
        
        전략:
        - 1차: Pro에서 실패 → Flash로 (비용 절감 + 다른 스타일)
        - 2차: Flash에서 실패 → Flash-8B로 (팩트 위주)
        """
        attempt = retry_state.attempt_count
        
        if attempt <= 1:
            return 'gemini-1.5-flash'  # Pro에서 Flash로
        elif attempt == 2:
            return 'gemini-1.5-flash-8b'  # 경량 모델로
        else:
            return 'gemini-1.5-flash-8b'  # 최종
    
    def _log_event(self, event_type: str, node_id: str, decision: GuardrailDecision):
        """이벤트 로깅"""
        self.event_log.append({
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'event_type': event_type,
            'node_id': node_id,
            'action': decision.action.value,
            'trigger': decision.trigger.value,
            'budget_ratio': self.budget_state.get_budget_ratio(),
            'message': decision.message
        })
        
        logger.info(f"[CostGuardrail] {event_type} - Node: {node_id}, Action: {decision.action.value}")
    
    def get_summary(self) -> Dict:
        """시스템 상태 요약"""
        return {
            'workflow_id': self.workflow_id,
            'budget': self.budget_state.to_dict(),
            'retry_states': {k: v.to_dict() for k, v in self.retry_states.items()},
            'total_events': len(self.event_log),
            'recent_events': self.event_log[-5:] if self.event_log else []
        }
    
    def reset_node(self, node_id: str):
        """노드 상태 리셋"""
        if node_id in self.retry_states:
            del self.retry_states[node_id]
    
    def reset_all(self):
        """전체 상태 리셋"""
        self.retry_states.clear()
        self.budget_state = BudgetState(
            workflow_id=self.workflow_id,
            max_budget_usd=self.budget_state.max_budget_usd
        )
        self.event_log.clear()


# ============================================================
# 편의 함수
# ============================================================

def create_guardrail_for_workflow(
    workflow_id: str,
    budget_usd: float = 0.5,
    max_retries: int = 3,
    domain: str = 'general'
) -> CostGuardrailSystem:
    """
    워크플로우용 가드레일 생성
    
    도메인별 기본 설정:
    - legal/financial: 낮은 예산, 낮은 재시도 (비용 민감)
    - creative: 높은 예산, 높은 재시도 (품질 중시)
    - internal: 중간 설정
    """
    domain_settings = {
        'legal': {'budget': 0.3, 'retries': 2},
        'financial': {'budget': 0.3, 'retries': 2},
        'medical': {'budget': 0.4, 'retries': 2},
        'creative': {'budget': 1.0, 'retries': 5},
        'internal': {'budget': 0.5, 'retries': 3},
        'general': {'budget': 0.5, 'retries': 3},
    }
    
    settings = domain_settings.get(domain.lower(), domain_settings['general'])
    
    return CostGuardrailSystem(
        workflow_id=workflow_id,
        max_budget_usd=budget_usd or settings['budget'],
        max_retries_per_node=max_retries or settings['retries']
    )
