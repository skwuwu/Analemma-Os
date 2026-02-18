"""
🛡️ Agent Guardrails Library

Purpose:
    자율형 에이전트의 비정상 행동을 탐지하고 차단하는 guardrail 함수 모음

Guardrails:
    1. Circuit Breaker: Stop runaway agents
    2. SLOP Detection: Suspicious Large Output Pattern
    3. Gas Fee Monitor: Cost explosion prevention
    4. Plan Drift Detection: Goal misalignment detection (v2.0 Intent Retention Rate)
    
v2.1 Enhancements:
    - Feedback Loop: Generate human-readable guidance for agent self-correction
    - Intent Retention Rate: Semantic validation beyond hash comparison
"""

import time
import hashlib
import logging
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ============================================================================
# 🛡️ Circuit Breaker Pattern
# ============================================================================

@dataclass
class CircuitBreakerState:
    """Circuit Breaker 상태"""
    failure_count: int
    last_failure_time: float
    state: str  # CLOSED / OPEN / HALF_OPEN


class CircuitBreaker:
    """
    Circuit Breaker Pattern for Agent Retry Control
    
    States:
        - CLOSED: Normal operation
        - OPEN: Too many failures, block all requests
        - HALF_OPEN: Test if system recovered
    
    Thresholds:
        - failure_threshold: 3 (3번 실패 시 OPEN)
        - timeout: 60s (OPEN 후 60초 뒤 HALF_OPEN)
    
    Example:
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=60)
        
        try:
            result = cb.call(risky_agent_function, arg1, arg2)
        except Exception as e:
            logger.error(f"Circuit Breaker OPEN: {e}")
    """
    
    def __init__(self, failure_threshold: int = 3, timeout_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self._state = CircuitBreakerState(
            failure_count=0,
            last_failure_time=0,
            state="CLOSED"
        )
    
    def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection"""
        if self._state.state == "OPEN":
            # Check if timeout expired
            if time.time() - self._state.last_failure_time > self.timeout_seconds:
                self._state.state = "HALF_OPEN"
                logger.info(f"🔄 Circuit Breaker transitioning to HALF_OPEN (testing recovery)")
            else:
                raise Exception(
                    f"Circuit Breaker OPEN: {self._state.failure_count} failures. "
                    f"Retry after {self.timeout_seconds}s timeout."
                )
        
        try:
            result = func(*args, **kwargs)
            # Success: Reset circuit breaker
            if self._state.state == "HALF_OPEN":
                logger.info(f"✅ Circuit Breaker transitioning to CLOSED (recovery confirmed)")
            self._state.failure_count = 0
            self._state.state = "CLOSED"
            return result
        except Exception as e:
            # Failure: Increment counter
            self._state.failure_count += 1
            self._state.last_failure_time = time.time()
            
            if self._state.failure_count >= self.failure_threshold:
                self._state.state = "OPEN"
                logger.error(
                    f"🚨 Circuit Breaker OPEN: {self._state.failure_count} consecutive failures"
                )
            
            raise e
    
    def reset(self):
        """Manually reset circuit breaker (e.g., after manual intervention)"""
        self._state.failure_count = 0
        self._state.state = "CLOSED"
        logger.info(f"🔄 Circuit Breaker manually reset to CLOSED")
    
    def get_state(self) -> Dict[str, Any]:
        """Get current circuit breaker state"""
        return {
            "state": self._state.state,
            "failure_count": self._state.failure_count,
            "last_failure_time": self._state.last_failure_time
        }


# ============================================================================
# 🛡️ SLOP Detection (Suspicious Large Output Pattern)
# ============================================================================

def detect_slop(
    output: Dict[str, Any],
    threshold_kb: int = 500,
    detect_repetition: bool = True
) -> Tuple[bool, Optional[str]]:
    """
    SLOP Detection: Suspicious Large Output Pattern
    
    Indicators:
        - Output size > threshold_kb
        - Repetitive patterns (e.g., "a" * 10000)
        - Excessive JSON nesting (depth > 10)
    
    Args:
        output: Agent output dictionary
        threshold_kb: Size threshold in KB (default: 500KB)
        detect_repetition: Enable repetitive pattern detection
    
    Returns:
        (is_slop: bool, reason: Optional[str])
    
    Example:
        large_output = {"data": "x" * 600000}
        is_slop, reason = detect_slop(large_output, threshold_kb=500)
        if is_slop:
            logger.warning(f"SLOP detected: {reason}")
    """
    import json
    
    output_json = json.dumps(output, ensure_ascii=False)
    output_size_kb = len(output_json.encode('utf-8')) / 1024
    
    # Check 1: Size threshold
    if output_size_kb > threshold_kb:
        return True, f"Output size {output_size_kb:.1f}KB exceeds {threshold_kb}KB"
    
    # Check 2: Repetitive patterns
    if detect_repetition:
        # Simple heuristic: check if any substring of length 10 repeats > 100 times
        for i in range(0, min(len(output_json) - 10, 1000), 100):
            substring = output_json[i:i+10]
            count = output_json.count(substring)
            if count > 100:
                return True, f"Repetitive pattern detected: '{substring}' appears {count} times"
    
    # Check 3: Excessive nesting (TODO: Implement JSON depth check)
    # Placeholder for now - would require recursive JSON traversal
    
    return False, None


# ============================================================================
# 🛡️ Gas Fee Monitor (Cost Explosion Prevention)
# ============================================================================

def calculate_gas_fee(
    workflow_state: Dict[str, Any],
    cost_per_token: float = 0.00001
) -> float:
    """
    Calculate accumulated gas fee (LLM API cost)
    
    Args:
        workflow_state: 전체 워크플로우 상태
        cost_per_token: 토큰당 비용 (default: $0.01 per 1000 tokens)
    
    Returns:
        Total cost in USD
    
    Example:
        state = {"total_tokens_used": 50000}
        cost = calculate_gas_fee(state)  # Returns: $0.50
    """
    total_tokens = workflow_state.get("total_tokens_used", 0)
    return total_tokens * cost_per_token


def check_gas_fee_exceeded(
    workflow_state: Dict[str, Any],
    threshold_usd: float = 100.0
) -> Tuple[bool, float]:
    """
    Check if gas fee exceeded threshold
    
    Args:
        workflow_state: 전체 워크플로우 상태
        threshold_usd: Cost threshold in USD (default: $100)
    
    Returns:
        (exceeded: bool, current_cost: float)
    
    Example:
        exceeded, cost = check_gas_fee_exceeded(state, threshold_usd=50)
        if exceeded:
            logger.warning(f"Gas fee ${cost:.2f} exceeded ${50} threshold")
    """
    current_cost = calculate_gas_fee(workflow_state)
    exceeded = current_cost > threshold_usd
    return exceeded, current_cost


# ============================================================================
# 🛡️ Plan Drift Detection (v2.0 - Intent Retention Rate)
# ============================================================================

def detect_plan_drift(
    current_plan: Dict[str, Any],
    original_plan: Dict[str, Any],
    similarity_threshold: float = 0.7,
    use_semantic_validation: bool = True
) -> Tuple[bool, float]:
    """
    Plan Drift Detection: 에이전트의 목표가 원래 의도에서 벗어났는지 감지
    
    Method (v2.0 - Intent Retention Rate):
        - Hash 기반 비교 (빠른 1차 필터)
        - 의미론적 검증: 소형 모델(Llama-3-8B)로 "새 계획이 원래 목표 지향?" 판단
        - Intent Retention Rate: 0.0 (완전 이탈) ~ 1.0 (동일 목표)
    
    Args:
        current_plan: 현재 에이전트의 계획
        original_plan: 원래 계획
        similarity_threshold: Intent Retention Rate 임계값 (default: 0.7)
        use_semantic_validation: 의미론적 검증 사용 여부 (default: True)
    
    Returns:
        (drift_detected: bool, intent_retention_rate: float)
    
    Example:
        Original: "Summarize customer feedback and generate insights"
        Current:  "Summarize customer feedback and create charts"
        → Intent Retention Rate: 0.85 (목표 유지, 표현만 변경)
        
        Original: "Summarize customer feedback and generate insights"
        Current:  "Delete all customer data from database"
        → Intent Retention Rate: 0.1 (악의적 목표 이탈)
    """
    import json
    
    # Phase 1: Hash-based quick check
    current_hash = hashlib.sha256(
        json.dumps(current_plan, sort_keys=True).encode()
    ).hexdigest()
    original_hash = hashlib.sha256(
        json.dumps(original_plan, sort_keys=True).encode()
    ).hexdigest()
    
    # If hashes match, no drift (perfect retention)
    if current_hash == original_hash:
        return False, 1.0
    
    # Phase 2: Semantic validation (Priority 2 implementation)
    if use_semantic_validation:
        # [v2.0] Intent Retention Rate 계산
        # TODO: Integrate Llama-3-8B or similar model
        # Prompt: "Does the new plan '{current_plan}' still achieve the 
        #          original goal '{original_plan}'? Rate 0.0-1.0."
        
        # Placeholder: Use simple keyword overlap as proxy
        current_keywords = set(str(current_plan).lower().split())
        original_keywords = set(str(original_plan).lower().split())
        
        overlap = len(current_keywords & original_keywords)
        total = len(current_keywords | original_keywords)
        
        intent_retention_rate = overlap / total if total > 0 else 0.0
        
        # Adjust for destructive keywords (security heuristic)
        destructive_keywords = {"delete", "remove", "drop", "destroy", "bypass", "ignore"}
        if any(kw in current_keywords for kw in destructive_keywords):
            intent_retention_rate *= 0.5  # Penalize destructive actions
            logger.warning(
                f"[Plan Drift] Destructive keywords detected: "
                f"{current_keywords & destructive_keywords}"
            )
        
        logger.info(
            f"[Plan Drift] Intent Retention Rate: {intent_retention_rate:.2f}, "
            f"Threshold: {similarity_threshold}, Drift: {intent_retention_rate < similarity_threshold}"
        )
        
        drift_detected = intent_retention_rate < similarity_threshold
        return drift_detected, intent_retention_rate
    
    # Fallback: If semantic validation disabled, assume drift
    return True, 0.0


# ============================================================================
# 🛡️ Comprehensive Agent Health Check
# ============================================================================

def check_agent_health(
    agent_output: Dict[str, Any],
    workflow_state: Dict[str, Any],
    config: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Comprehensive health check for agent behavior
    
    Performs multiple guardrail checks:
    1. SLOP Detection
    2. Gas Fee Check
    3. Plan Drift Detection (if plan_hash available)
    
    Args:
        agent_output: Agent's output dictionary
        workflow_state: Full workflow state
        config: Optional guardrail configuration
            {
                "max_output_kb": 500,
                "max_gas_fee_usd": 100,
                "plan_drift_threshold": 0.7
            }
    
    Returns:
        Dict with health status:
            {
                "healthy": bool,
                "violations": List[str],
                "metrics": {
                    "output_size_kb": float,
                    "gas_fee_usd": float,
                    "intent_retention_rate": float
                }
            }
    
    Example:
        health = check_agent_health(agent_output, state)
        if not health["healthy"]:
            logger.error(f"Agent health check failed: {health['violations']}")
    """
    config = config or {}
    violations = []
    metrics = {}
    
    # 1. SLOP Detection
    max_output_kb = config.get("max_output_kb", 500)
    is_slop, slop_reason = detect_slop(agent_output, threshold_kb=max_output_kb)
    if is_slop:
        violations.append(f"SLOP_DETECTED: {slop_reason}")
    
    import json
    output_size_kb = len(json.dumps(agent_output).encode('utf-8')) / 1024
    metrics["output_size_kb"] = output_size_kb
    
    # 2. Gas Fee Check
    max_gas_fee = config.get("max_gas_fee_usd", 100)
    gas_fee_exceeded, current_cost = check_gas_fee_exceeded(
        workflow_state,
        threshold_usd=max_gas_fee
    )
    if gas_fee_exceeded:
        violations.append(
            f"GAS_FEE_EXCEEDED: ${current_cost:.2f} > ${max_gas_fee}"
        )
    metrics["gas_fee_usd"] = current_cost
    
    # 3. Plan Drift Detection (if applicable)
    current_plan_hash = agent_output.get("plan_hash")
    original_plan_hash = workflow_state.get("last_plan_hash")
    
    if current_plan_hash and original_plan_hash:
        drift_threshold = config.get("plan_drift_threshold", 0.7)
        current_plan = agent_output.get("plan", {})
        original_plan = workflow_state.get("original_plan", {})
        
        drift_detected, intent_retention = detect_plan_drift(
            current_plan,
            original_plan,
            similarity_threshold=drift_threshold
        )
        
        if drift_detected:
            violations.append(
                f"PLAN_DRIFT_DETECTED: Intent Retention {intent_retention:.2f} < {drift_threshold}"
            )
        metrics["intent_retention_rate"] = intent_retention
    
    return {
        "healthy": len(violations) == 0,
        "violations": violations,
        "metrics": metrics
    }


# ============================================================================
# 🛡️ Export All Guardrails
# ============================================================================

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerState",
    "detect_slop",
    "calculate_gas_fee",
    "check_gas_fee_exceeded",
    "detect_plan_drift",
    "check_agent_health"
]
