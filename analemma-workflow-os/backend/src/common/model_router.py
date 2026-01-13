"""
Model Router: Canvas 모드에 따른 동적 모델 선택

비용 최적화를 위해 작업 복잡도에 따라 적절한 모델을 선택합니다.
- Agentic Designer (전체 생성): 고성능 모델 (Claude 3.5 Sonnet, GPT-4o)
- Co-design (부분 수정): 경량 모델 (Claude Haiku, Llama 3.1 70B)
"""

import os
import logging
from typing import Dict, Any, Literal
from enum import Enum

logger = logging.getLogger(__name__)

class ModelTier(Enum):
    """모델 성능 티어"""
    PREMIUM = "premium"      # 최고 성능, 높은 비용 (전체 워크플로우 생성)
    STANDARD = "standard"    # 중간 성능, 중간 비용 (복잡한 Co-design)
    ECONOMY = "economy"      # 기본 성능, 낮은 비용 (단순 Co-design)

class ModelConfig:
    """모델 설정"""
    def __init__(self, model_id: str, max_tokens: int, cost_per_1k_tokens: float, tier: ModelTier):
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.cost_per_1k_tokens = cost_per_1k_tokens
        self.tier = tier

# 사용 가능한 모델들 (비용 순서대로)
AVAILABLE_MODELS = {
    # Premium Tier - 전체 워크플로우 생성용
    "claude-3.5-sonnet": ModelConfig(
        model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        max_tokens=8192,
        cost_per_1k_tokens=3.0,  # $3.00 per 1K tokens (input)
        tier=ModelTier.PREMIUM
    ),
    "gpt-4o": ModelConfig(
        model_id="gpt-4o-2024-08-06",
        max_tokens=4096,
        cost_per_1k_tokens=2.5,  # $2.50 per 1K tokens (input)
        tier=ModelTier.PREMIUM
    ),
    
    # Standard Tier - 복잡한 Co-design용
    "claude-3-sonnet": ModelConfig(
        model_id="anthropic.claude-3-sonnet-20240229-v1:0",
        max_tokens=4096,
        cost_per_1k_tokens=3.0,  # $3.00 per 1K tokens (input)
        tier=ModelTier.STANDARD
    ),
    
    # Economy Tier - 단순 Co-design용
    "claude-3-haiku": ModelConfig(
        model_id="anthropic.claude-3-haiku-20240307-v1:0",
        max_tokens=4096,
        cost_per_1k_tokens=0.25,  # $0.25 per 1K tokens (input)
        tier=ModelTier.ECONOMY
    ),
    "llama-3.1-70b": ModelConfig(
        model_id="meta.llama3-1-70b-instruct-v1:0",
        max_tokens=2048,
        cost_per_1k_tokens=0.99,  # $0.99 per 1K tokens (input)
        tier=ModelTier.ECONOMY
    )
}

def estimate_request_complexity(
    canvas_mode: Literal["agentic-designer", "co-design"],
    current_workflow: Dict[str, Any],
    user_request: str,
    recent_changes: list = None
) -> ModelTier:
    """
    요청 복잡도를 분석하여 적절한 모델 티어를 결정합니다.
    
    Args:
        canvas_mode: Canvas 모드 ("agentic-designer" 또는 "co-design")
        current_workflow: 현재 워크플로우 상태
        user_request: 사용자 요청
        recent_changes: 최근 변경사항
    
    Returns:
        ModelTier: 권장 모델 티어
    """
    
    # Agentic Designer 모드는 항상 Premium 모델 사용
    if canvas_mode == "agentic-designer":
        logger.info("Agentic Designer mode detected - using PREMIUM tier")
        return ModelTier.PREMIUM
    
    # Co-design 모드에서는 복잡도 분석
    nodes = current_workflow.get("nodes", [])
    edges = current_workflow.get("edges", [])
    node_count = len(nodes)
    edge_count = len(edges)
    
    # 복잡도 점수 계산
    complexity_score = 0
    
    # 워크플로우 크기 기반 점수
    if node_count > 20:
        complexity_score += 3
    elif node_count > 10:
        complexity_score += 2
    elif node_count > 5:
        complexity_score += 1
    
    # 연결 복잡도
    if edge_count > node_count * 1.5:  # 복잡한 연결 구조
        complexity_score += 2
    
    # 사용자 요청 복잡도 분석
    request_lower = user_request.lower()
    complex_keywords = [
        "전체", "완전히", "처음부터", "새로", "리팩토링", "재설계",
        "최적화", "성능", "보안", "에러처리", "예외처리", "로깅",
        "병렬", "분산", "스케일링", "아키텍처"
    ]
    
    for keyword in complex_keywords:
        if keyword in request_lower:
            complexity_score += 1
    
    # 최근 변경사항이 많으면 복잡도 증가
    if recent_changes and len(recent_changes) > 5:
        complexity_score += 1
    
    # 점수에 따른 티어 결정
    if complexity_score >= 5:
        tier = ModelTier.PREMIUM
    elif complexity_score >= 3:
        tier = ModelTier.STANDARD
    else:
        tier = ModelTier.ECONOMY
    
    logger.info(f"Complexity analysis: score={complexity_score}, tier={tier.value}")
    return tier

def select_optimal_model(
    canvas_mode: Literal["agentic-designer", "co-design"],
    current_workflow: Dict[str, Any],
    user_request: str,
    recent_changes: list = None,
    budget_constraint: ModelTier = None
) -> ModelConfig:
    """
    최적의 모델을 선택합니다.
    
    Args:
        canvas_mode: Canvas 모드
        current_workflow: 현재 워크플로우
        user_request: 사용자 요청
        recent_changes: 최근 변경사항
        budget_constraint: 예산 제약 (최대 허용 티어)
    
    Returns:
        ModelConfig: 선택된 모델 설정
    """
    
    # 복잡도 분석
    recommended_tier = estimate_request_complexity(
        canvas_mode, current_workflow, user_request, recent_changes
    )
    
    # 예산 제약 적용
    if budget_constraint and budget_constraint.value < recommended_tier.value:
        logger.warning(f"Budget constraint applied: {recommended_tier.value} -> {budget_constraint.value}")
        recommended_tier = budget_constraint
    
    # 환경 변수로 모델 강제 지정 가능
    forced_model = os.getenv("FORCE_MODEL_ID")
    if forced_model and forced_model in AVAILABLE_MODELS:
        logger.info(f"Using forced model: {forced_model}")
        return AVAILABLE_MODELS[forced_model]
    
    # 티어별 모델 선택
    tier_models = {
        model_name: config 
        for model_name, config in AVAILABLE_MODELS.items() 
        if config.tier == recommended_tier
    }
    
    if not tier_models:
        # Fallback to economy tier
        logger.warning(f"No models found for tier {recommended_tier.value}, falling back to economy")
        tier_models = {
            model_name: config 
            for model_name, config in AVAILABLE_MODELS.items() 
            if config.tier == ModelTier.ECONOMY
        }
    
    # 가장 비용 효율적인 모델 선택 (같은 티어 내에서)
    selected_model_name = min(tier_models.keys(), 
                             key=lambda x: tier_models[x].cost_per_1k_tokens)
    selected_model = tier_models[selected_model_name]
    
    logger.info(f"Selected model: {selected_model_name} (tier: {selected_model.tier.value}, "
               f"cost: ${selected_model.cost_per_1k_tokens}/1K tokens)")
    
    return selected_model

def get_model_for_canvas_mode(
    canvas_mode: Literal["agentic-designer", "co-design"],
    current_workflow: Dict[str, Any] = None,
    user_request: str = "",
    recent_changes: list = None
) -> str:
    """
    Canvas 모드에 따른 모델 ID를 반환합니다.
    
    Returns:
        str: Bedrock 모델 ID
    """
    
    if current_workflow is None:
        current_workflow = {"nodes": [], "edges": []}
    
    # 예산 제약 설정 (환경 변수로 제어 가능)
    budget_tier_name = os.getenv("MAX_MODEL_TIER", "premium").lower()
    budget_constraint = None
    
    if budget_tier_name == "economy":
        budget_constraint = ModelTier.ECONOMY
    elif budget_tier_name == "standard":
        budget_constraint = ModelTier.STANDARD
    # premium이면 제약 없음
    
    selected_model = select_optimal_model(
        canvas_mode=canvas_mode,
        current_workflow=current_workflow,
        user_request=user_request,
        recent_changes=recent_changes,
        budget_constraint=budget_constraint
    )
    
    return selected_model.model_id

# 편의 함수들
def get_agentic_designer_model() -> str:
    """Agentic Designer용 모델 ID 반환"""
    return get_model_for_canvas_mode("agentic-designer")

def get_codesign_model(workflow: Dict[str, Any], request: str, changes: list = None) -> str:
    """Co-design용 모델 ID 반환"""
    return get_model_for_canvas_mode("co-design", workflow, request, changes)