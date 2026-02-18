"""
Trust Score Manager with EMA-based Asymmetric Recovery
Agent Trust Score Management (v3.0 Enhanced)
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class TrustScoreState:
    """Agent Trust Score State"""
    agent_id: str
    current_score: float  # 0.0 ~ 1.0
    score_history: List[Tuple[str, float]]  # (manifest_id, score)
    violation_count: int = 0
    success_count: int = 0
    last_updated: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class GovernanceMode:
    """거버넌스 검증 모드"""
    OPTIMISTIC = "OPTIMISTIC"
    STRICT = "STRICT"


class TrustScoreManager:
    """
    Agent Trust Score Manager (v3.0 Enhanced with EMA)
    
    Mathematical Model:
    T_new = max(0, min(1, T_old + delta_S - (alpha * A)))
    
    Where:
    - T_old: Previous trust score
    - delta_S: Success increment (variable, EMA-based)
    - A: Anomaly Score (0.0-1.0)
    - alpha: Violation multiplier (0.5)
    
    Asymmetric Recovery Problem Solution:
    - Before: Fixed +0.01 -> 40 successes needed (0.4 -> 0.8 recovery)
    - After: Exponential Moving Average (EMA) -> Recent success weighted acceleration
    
    EMA Formula:
    delta_S = delta_S_base * (1 + beta * streak_ratio)
    
    Where:
    - streak_ratio = recent_successes / total_recent
    - beta = 2.0 (acceleration coefficient)
    
    Example:
    - 5 consecutive successes: delta_S = 0.01 * (1 + 2.0 * 1.0) = 0.03
    - Recovery time: 40 -> 14 iterations (65% reduction)
    """
    
    INITIAL_SCORE = 0.8
    BASE_SUCCESS_INCREMENT = 0.01
    VIOLATION_MULTIPLIER = 0.5
    STRICT_MODE_THRESHOLD = 0.4
    EMA_ACCELERATION = 2.0
    RECENT_WINDOW = 10  # Last 10 decisions
    
    def __init__(self):
        self.agent_scores: Dict[str, TrustScoreState] = {}
    
    def update_score(
        self,
        agent_id: str,
        manifest_id: str,
        governance_result: Dict[str, any]
    ) -> float:
        """
        Update trust score based on governance decision
        
        Args:
            agent_id: Agent ID
            manifest_id: Current Manifest ID
            governance_result: GovernanceDecision dictionary
        
        Returns:
            New trust_score
        """
        # Get or create trust state
        if agent_id not in self.agent_scores:
            self.agent_scores[agent_id] = TrustScoreState(
                agent_id=agent_id,
                current_score=self.INITIAL_SCORE,
                score_history=[],
                violation_count=0,
                success_count=0,
                last_updated=datetime.utcnow().isoformat() + "Z"
            )
        
        trust_state = self.agent_scores[agent_id]
        old_score = trust_state.current_score
        
        decision = governance_result.get("decision", "APPROVED")
        
        # Update based on decision
        if decision == "APPROVED":
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [v3.0 Enhanced] EMA-based Asymmetric Recovery
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Calculate success streak ratio
            recent_decisions = (
                trust_state.score_history[-self.RECENT_WINDOW:] 
                if len(trust_state.score_history) >= self.RECENT_WINDOW 
                else trust_state.score_history
            )
            
            if recent_decisions:
                # Count recent successes (score increased or stable)
                recent_successes = sum(
                    1 for i in range(1, len(recent_decisions))
                    if recent_decisions[i][1] >= recent_decisions[i-1][1]
                )
                streak_ratio = recent_successes / max(len(recent_decisions) - 1, 1)
            else:
                streak_ratio = 0.0
            
            # Accelerated recovery for consistent success
            delta_s = self.BASE_SUCCESS_INCREMENT * (1 + self.EMA_ACCELERATION * streak_ratio)
            new_score = min(old_score + delta_s, 1.0)
            trust_state.success_count += 1
            
            logger.info(
                f"[TrustScore EMA] {agent_id}: streak_ratio={streak_ratio:.2f}, "
                f"delta_s={delta_s:.4f} (base={self.BASE_SUCCESS_INCREMENT})"
            )
            
        elif decision in ["REJECTED", "ESCALATED", "ROLLBACK"]:
            # Violation detected (asymmetric penalty)
            anomaly_score = governance_result.get("audit_log", {}).get("anomaly_score", 0.5)
            penalty = anomaly_score * self.VIOLATION_MULTIPLIER
            new_score = max(old_score - penalty, 0.0)
            trust_state.violation_count += 1
        
        else:
            new_score = old_score
        
        # Update state
        trust_state.current_score = new_score
        trust_state.score_history.append((manifest_id, new_score))
        trust_state.last_updated = datetime.utcnow().isoformat() + "Z"
        
        # Keep only last 20 scores
        if len(trust_state.score_history) > 20:
            trust_state.score_history = trust_state.score_history[-20:]
        
        logger.info(
            f"[TrustScore] {agent_id}: {old_score:.3f} → {new_score:.3f} "
            f"(Decision: {decision}, Success: {trust_state.success_count}, "
            f"Violations: {trust_state.violation_count})"
        )
        
        return new_score
    
    def get_governance_mode(self, agent_id: str) -> str:
        """
        Determine governance mode based on trust score
        
        Args:
            agent_id: Agent ID
        
        Returns:
            STRICT (low trust) or OPTIMISTIC (high trust)
        """
        if agent_id not in self.agent_scores:
            return GovernanceMode.OPTIMISTIC  # Default
        
        score = self.agent_scores[agent_id].current_score
        
        if score < self.STRICT_MODE_THRESHOLD:
            logger.warning(
                f"[TrustScore] {agent_id} trust score too low "
                f"({score:.2f} < {self.STRICT_MODE_THRESHOLD}). "
                f"Forcing STRICT mode."
            )
            return GovernanceMode.STRICT
        
        return GovernanceMode.OPTIMISTIC
    
    def get_trend(self, agent_id: str) -> str:
        """
        Analyze trust score trend
        
        Args:
            agent_id: Agent ID
        
        Returns:
            "IMPROVING" | "STABLE" | "DEGRADING"
        """
        if agent_id not in self.agent_scores:
            return "STABLE"
        
        history = self.agent_scores[agent_id].score_history
        if len(history) < 3:
            return "STABLE"
        
        recent_5 = [score for _, score in history[-5:]]
        avg_recent = sum(recent_5) / len(recent_5)
        
        older_5 = (
            [score for _, score in history[-10:-5]] 
            if len(history) >= 10 
            else recent_5
        )
        avg_older = sum(older_5) / len(older_5)
        
        diff = avg_recent - avg_older
        
        if diff > 0.05:
            return "IMPROVING"
        elif diff < -0.05:
            return "DEGRADING"
        else:
            return "STABLE"
    
    def get_score(self, agent_id: str) -> Optional[float]:
        """
        Get current trust score
        
        Args:
            agent_id: Agent ID
        
        Returns:
            Current trust score or None
        """
        if agent_id not in self.agent_scores:
            return None
        
        return self.agent_scores[agent_id].current_score
    
    def get_state(self, agent_id: str) -> Optional[TrustScoreState]:
        """
        Get complete trust score state
        
        Args:
            agent_id: Agent ID
        
        Returns:
            TrustScoreState or None
        """
        return self.agent_scores.get(agent_id)
