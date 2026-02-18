"""
Trust Score Manager - Unit Tests
"""
import pytest
import sys
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from services.governance.trust_score_manager import (
    TrustScoreManager,
    TrustScoreState,
    GovernanceMode
)


class TestTrustScoreManager:
    """Trust Score Manager 테스트"""
    
    def setup_method(self):
        """각 테스트 전 초기화"""
        self.manager = TrustScoreManager()
    
    def test_initial_score(self):
        """초기 점수 테스트"""
        result = {
            "decision": "APPROVED",
            "audit_log": {}
        }
        
        score = self.manager.update_score("test_agent", "mf_001", result)
        
        # Initial: 0.8, Success: +0.01 (no streak yet)
        assert score > 0.8
        assert score <= 0.81
    
    def test_ema_acceleration(self):
        """EMA 가속 복구 테스트"""
        agent_id = "test_agent"
        
        # Initial score: 0.8
        # Drop to 0.4 (Strict mode threshold)
        violation_result = {
            "decision": "REJECTED",
            "audit_log": {"anomaly_score": 0.8}
        }
        
        score = self.manager.update_score(agent_id, "mf_001", violation_result)
        assert score <= 0.4  # 0.8 - (0.8 * 0.5) = 0.4 or less
        
        # Only enter STRICT mode if actually below threshold
        if score < self.manager.STRICT_MODE_THRESHOLD:
            assert self.manager.get_governance_mode(agent_id) == GovernanceMode.STRICT
        
        # Now recover with 10 consecutive successes
        success_result = {
            "decision": "APPROVED",
            "audit_log": {}
        }
        
        scores = []
        for i in range(10):
            score = self.manager.update_score(agent_id, f"mf_{i+2}", success_result)
            scores.append(score)
        
        # Check EMA acceleration
        # With acceleration, should recover faster than linear +0.01
        # Expected: ~14 successes needed instead of 40
        
        # After 10 successes with EMA, should be near 0.6+
        assert score > 0.6
        
        print(f"\nRecovery progress: {scores}")
        print(f"Final score after 10 successes: {score:.4f}")
    
    def test_asymmetric_penalty(self):
        """비대칭적 페널티 테스트"""
        agent_id = "test_agent"
        
        # Start at 0.8
        violation_result = {
            "decision": "ROLLBACK",
            "audit_log": {"anomaly_score": 0.6}
        }
        
        score = self.manager.update_score(agent_id, "mf_001", violation_result)
        
        # Penalty: 0.6 * 0.5 = 0.3
        # New score: 0.8 - 0.3 = 0.5
        assert score == 0.5
    
    def test_trend_analysis(self):
        """추세 분석 테스트"""
        agent_id = "test_agent"
        
        # Initial trend (not enough data)
        assert self.manager.get_trend(agent_id) == "STABLE"
        
        # Add 10 successes
        success_result = {
            "decision": "APPROVED",
            "audit_log": {}
        }
        
        for i in range(10):
            self.manager.update_score(agent_id, f"mf_{i}", success_result)
        
        # Should be IMPROVING or STABLE
        trend = self.manager.get_trend(agent_id)
        assert trend in ["IMPROVING", "STABLE"]
        
        # Add violations
        violation_result = {
            "decision": "REJECTED",
            "audit_log": {"anomaly_score": 0.5}
        }
        
        for i in range(5):
            self.manager.update_score(agent_id, f"mf_v_{i}", violation_result)
        
        # Should be DEGRADING
        trend = self.manager.get_trend(agent_id)
        assert trend == "DEGRADING"
    
    def test_strict_mode_threshold(self):
        """Strict Mode 임계값 테스트"""
        agent_id = "test_agent"
        
        # Initially OPTIMISTIC
        assert self.manager.get_governance_mode(agent_id) == GovernanceMode.OPTIMISTIC
        
        # Drop below 0.4
        violation_result = {
            "decision": "REJECTED",
            "audit_log": {"anomaly_score": 1.0}
        }
        
        score = self.manager.update_score(agent_id, "mf_001", violation_result)
        
        # 0.8 - (1.0 * 0.5) = 0.3 (below threshold)
        assert abs(score - 0.3) < 0.01  # Floating point tolerance
        assert self.manager.get_governance_mode(agent_id) == GovernanceMode.STRICT


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
