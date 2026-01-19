"""
Analemma OS Quality Kernel
==========================

커널 레벨 저품질 데이터 방지 시스템 (2단계 필터링 아키텍처)

Stage 1: Local Heuristic Filter (비용 $0)
    - Shannon Entropy 계산
    - N-gram 반복성 체크
    - 워크슬롭 패턴 탐지

Stage 2: Strong Verifier (최소 비용)
    - Gemini 1.5 Flash-8B 기반 검증
    - 정보 밀도 점수화 (1-10)
    - REJECT/APPROVE 판정

Mathematical Foundation:
    H(X) = -Σ P(x_i) * log₂(P(x_i))
    
    Where:
    - P(x_i): Probability of word x_i appearing
    - H(X): Text entropy value
    - Low H(X) → High predictability → Likely slop
    - High H(X) → Rich information → Quality content
"""

from .entropy_analyzer import EntropyAnalyzer, EntropyAnalysisResult, ContentDomain, EntropyThresholds
from .slop_detector import (
    SlopDetector,
    SlopDetectionResult,
    SlopCategory,
    SlopPattern,
    EmojiAnalysisResult
)
from .quality_gate import (
    QualityGate,
    QualityVerdict,
    QualityGateResult,
    AdaptiveQualityPolicy,
    QualityPolicyMode,
    Stage1Result,
    Stage2Result,
    RegenerationResult,
    QualityGateWithGuardrails,
    QualityGateError,
    GuardrailTriggeredError,
    quality_gate_middleware
)
from .kernel_middleware import (
    KernelMiddlewareInterceptor,
    InterceptorAction,
    InterceptorResult,
    DistillationTarget,
    BackgroundDistillationTask,
    DistillationBudgetConfig,
    create_kernel_interceptor,
    register_node_interceptor
)
from .cost_guardrails import (
    CostGuardrailSystem,
    GuardrailAction,
    GuardrailDecision,
    GuardrailTrigger,
    RetryState,
    BudgetState,
    DriftDetectionResult,
    ModelPricing,
    create_guardrail_for_workflow
)
from .concurrency_controller import (
    # v2.0: 분산 상태 관리자
    DistributedStateConfig,
    DistributedStateManager,
    get_distributed_state_manager,
    # v2.0: 작업 우선순위 (Fast Track)
    TaskPriority,
    # 2단계: 커널 스케줄링
    LoadLevel,
    ConcurrencySnapshot,
    TaskBatch,
    KernelTaskScheduler,
    # 3단계: 지능형 재시도
    AdaptiveThresholdConfig,
    IntelligentRetryController,
    # 4단계: 비용/드리프트 가드레일
    BudgetWatchdogConfig,
    BudgetWatchdog,
    SemanticDriftResult,
    SemanticDriftDetector,
    # 통합 컨트롤러
    ConcurrencyControllerV2,
    get_concurrency_controller
)

__all__ = [
    # Core Analyzers
    'EntropyAnalyzer',
    'EntropyAnalysisResult',
    'EntropyThresholds',
    'ContentDomain',
    
    # Slop Detection
    'SlopDetector',
    'SlopDetectionResult',
    'SlopCategory',
    'SlopPattern',
    'EmojiAnalysisResult',
    
    # Quality Gate
    'QualityGate',
    'QualityVerdict',
    'QualityGateResult',
    'Stage1Result',
    'Stage2Result',
    
    # Adaptive Policy (동적 임계값 튜닝)
    'AdaptiveQualityPolicy',
    'QualityPolicyMode',
    
    # Regeneration & Guardrails Integration
    'RegenerationResult',
    'QualityGateWithGuardrails',
    'QualityGateError',
    'GuardrailTriggeredError',
    'quality_gate_middleware',
    
    # Cost Guardrail System (4단계 비용 가드레일)
    'CostGuardrailSystem',
    'GuardrailAction',
    'GuardrailDecision',
    'GuardrailTrigger',
    'RetryState',
    'BudgetState',
    'DriftDetectionResult',
    'ModelPricing',  # 동적 가격 계산 + Context Caching
    'create_guardrail_for_workflow',
    
    # Kernel Middleware
    'KernelMiddlewareInterceptor',
    'InterceptorAction',
    'InterceptorResult',
    'DistillationTarget',
    'BackgroundDistillationTask',
    'DistillationBudgetConfig',
    'create_kernel_interceptor',
    'register_node_interceptor',
    
    # Concurrency Controller (4단계 아키텍처)
    # v2.0: 분산 상태 관리자 (Lambda Scale-out 대응)
    'DistributedStateConfig',
    'DistributedStateManager',
    'get_distributed_state_manager',
    # v2.0: 작업 우선순위 (Fast Track)
    'TaskPriority',
    # 2단계: 커널 스케줄링 및 부하 평탄화
    'LoadLevel',
    'ConcurrencySnapshot',
    'TaskBatch',
    'KernelTaskScheduler',
    # 3단계: 지능형 품질 및 재시도 제어
    'AdaptiveThresholdConfig',
    'IntelligentRetryController',
    # 4단계: 비용 및 드리프트 모니터링
    'BudgetWatchdogConfig',
    'BudgetWatchdog',
    'SemanticDriftResult',
    'SemanticDriftDetector',
    # 통합 컨트롤러
    'ConcurrencyControllerV2',
    'get_concurrency_controller',
]

__version__ = '2.0.0'
__kernel_level__ = 'RING_1_QUALITY'
