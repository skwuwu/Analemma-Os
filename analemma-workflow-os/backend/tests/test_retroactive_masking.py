"""
Retroactive PII Masking - Unit Tests
"""
import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from services.governance.retroactive_masking import (
    detect_pii_regex,
    apply_retroactive_masking,
    evaluate_and_mask_pii,
    PIIPattern
)


class TestPIIMasking:
    """PII 마스킹 테스트"""
    
    def test_email_detection(self):
        """이메일 주소 탐지 테스트"""
        text = "사용자 john.doe@example.com에게 알림 발송 완료"
        
        pii = detect_pii_regex(text)
        
        assert "email" in pii
        assert "john.doe@example.com" in pii["email"]
    
    def test_phone_detection(self):
        """전화번호 탐지 테스트"""
        test_cases = [
            "010-1234-5678로 연락 드리겠습니다",
            "전화번호: 01012345678",
            "02-123-4567 내선 123"
        ]
        
        for text in test_cases:
            pii = detect_pii_regex(text)
            
            if "phone" in pii:
                assert len(pii["phone"]) > 0
                print(f"Detected: {pii['phone']} in '{text}'")
    
    def test_card_detection(self):
        """카드번호 탐지 테스트"""
        text = "Card ending in 1234-5678-9012-3456..."
        
        pii = detect_pii_regex(text)
        
        assert "card" in pii
        assert len(pii["card"]) > 0
    
    def test_apply_masking(self):
        """마스킹 적용 테스트"""
        output = {
            "thought": "사용자 john.doe@example.com에게 알림 발송",
            "message": "010-1234-5678로 연락 드리겠습니다",
            "response": "처리 완료"
        }
        
        pii_map = {
            "email": ["john.doe@example.com"],
            "phone": ["010-1234-5678"]
        }
        
        masked = apply_retroactive_masking(output, pii_map)
        
        # Email should be masked
        assert "john.doe@example.com" not in masked["thought"]
        assert "***EMAIL_" in masked["thought"]
        
        # Phone should be masked
        assert "010-1234-5678" not in masked["message"]
        assert "***PHONE_" in masked["message"]
        
        # Metadata added
        assert masked["_pii_masked"] is True
        assert masked["_pii_mask_count"] >= 2  # At least 2 PII instances masked
    
    def test_no_false_positives(self):
        """허위 양성 방지 테스트"""
        text = "내부 IP 192.168.1.1은 허용됩니다"
        
        pii = detect_pii_regex(text)
        
        # Internal IPs should NOT be detected
        if "ip" in pii:
            assert "192.168.1.1" not in pii["ip"]
    
    def test_evaluate_and_mask_integration(self):
        """통합 평가 및 마스킹 테스트"""
        output = {
            "thought": "사용자 test@example.com 조회 중",
            "message": "결과를 전달합니다"
        }
        
        # Use regex-only (no LLM)
        masked_output, has_violation, pii_map = evaluate_and_mask_pii(
            agent_output=output,
            agent_thought="",
            use_llm=False
        )
        
        assert has_violation is True
        assert "email" in pii_map
        assert "test@example.com" not in masked_output["thought"]
    
    def test_multiple_pii_types(self):
        """복합 PII 탐지 테스트"""
        text = """
        사용자 정보:
        - 이메일: admin@company.com
        - 연락처: 010-9876-5432
        - 카드: 4111-1111-1111-1111
        """
        
        pii = detect_pii_regex(text)
        
        assert "email" in pii
        assert "phone" in pii
        assert "card" in pii
        
        # Verify all detected
        assert len(pii["email"]) >= 1
        assert len(pii["phone"]) >= 1
        assert len(pii["card"]) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
