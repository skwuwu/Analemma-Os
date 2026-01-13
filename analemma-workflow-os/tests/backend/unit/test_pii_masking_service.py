"""
Unit Tests for PIIMaskingService

Tests the production-grade PII masking service for:
- URL protection with trailing punctuation handling
- Email masking while preserving URL-embedded emails
- UUID-based token collision prevention
- Balanced parentheses handling
"""

import pytest
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.abspath("backend"))

from src.services.common.pii_masking_service import PIIMaskingService, get_pii_masking_service


class TestPIIMaskingServiceURLHandling:
    """Test URL trailing punctuation handling"""
    
    @pytest.fixture
    def masker(self):
        return PIIMaskingService(strict_mode=True)
    
    def test_url_with_trailing_period_excluded(self, masker):
        """
        URL 끝의 마침표가 제외되는지 확인
        Go to https://analemma.ai. -> https://analemma.ai (not https://analemma.ai.)
        """
        text = "Go to https://analemma.ai."
        result = masker.mask(text)
        
        # URL should NOT include trailing period
        assert "https://analemma.ai" in result
        # The period should still be in the text, just not part of URL
        assert result.endswith(".")
    
    def test_url_with_trailing_comma_excluded(self, masker):
        """쉼표가 URL에 포함되지 않음"""
        text = "Check https://example.com/page, it's useful."
        result = masker.mask(text)
        
        assert "https://example.com/page" in result
        assert ", it's useful" in result
    
    def test_url_with_internal_period_preserved(self, masker):
        """URL 내부의 마침표는 보존됨"""
        text = "Download from https://example.com/file.php."
        result = masker.mask(text)
        
        # Internal period preserved
        assert "https://example.com/file.php" in result
        # Trailing period excluded
        assert result.endswith(".")
    
    def test_url_with_balanced_parentheses_preserved(self, masker):
        """Wikipedia 스타일 URL의 균형 잡힌 괄호 보존"""
        text = "See https://wikipedia.org/wiki/Python_(programming_language) for details."
        result = masker.mask(text)
        
        # Full URL with parentheses preserved
        assert "https://wikipedia.org/wiki/Python_(programming_language)" in result
    
    def test_url_with_unbalanced_closing_paren_excluded(self, masker):
        """불균형한 닫는 괄호는 제외"""
        text = "(Visit https://example.com/page) for more."
        result = masker.mask(text)
        
        # Closing paren from markdown should not be in URL
        assert "https://example.com/page" in result
    
    def test_url_with_multiple_trailing_punct_excluded(self, masker):
        """여러 trailing 문자가 모두 제외됨"""
        text = "Is this https://example.com?!"
        result = masker.mask(text)
        
        assert "https://example.com" in result
        assert result.endswith("?!")


class TestPIIMaskingServiceEmailHandling:
    """Test email masking with URL protection"""
    
    @pytest.fixture
    def masker(self):
        return PIIMaskingService(strict_mode=True)
    
    def test_standalone_email_masked(self, masker):
        """독립적인 이메일은 마스킹됨"""
        text = "Contact us at support@example.com for help."
        result = masker.mask(text)
        
        assert "support@example.com" not in result
        assert "[EMAIL_REDACTED]" in result
    
    def test_url_embedded_email_pattern_preserved(self, masker):
        """URL 내 이메일 패턴은 보존됨"""
        text = "See https://cdn.example.com/user@tenant.io/image.png for the logo."
        result = masker.mask(text)
        
        # Email-like pattern inside URL should be preserved
        assert "user@tenant.io" in result
        assert "https://cdn.example.com/user@tenant.io/image.png" in result
    
    def test_mixed_email_and_url(self, masker):
        """이메일과 URL이 혼재된 경우"""
        text = "Email admin@company.com or visit https://company.com/contact."
        result = masker.mask(text)
        
        # Standalone email masked
        assert "admin@company.com" not in result
        assert "[EMAIL_REDACTED]" in result
        # URL preserved
        assert "https://company.com/contact" in result


class TestPIIMaskingServiceAPIKeys:
    """Test API key masking"""
    
    @pytest.fixture
    def masker(self):
        return PIIMaskingService()
    
    def test_openai_key_masked(self, masker):
        """OpenAI API 키가 마스킹됨"""
        text = "Use key: sk-proj-abc123def456ghi789jkl012mno345pqr678"
        result = masker.mask(text)
        
        assert "sk-proj" not in result
        assert "[API_KEY_REDACTED]" in result
    
    def test_anthropic_key_masked(self, masker):
        """Anthropic API 키가 마스킹됨"""
        text = "API: sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        result = masker.mask(text)
        
        assert "sk-ant" not in result
        assert "[API_KEY_REDACTED]" in result


class TestPIIMaskingServicePhoneNumbers:
    """Test phone number masking"""
    
    @pytest.fixture
    def masker(self):
        return PIIMaskingService()
    
    def test_korean_phone_masked(self, masker):
        """한국 전화번호 마스킹"""
        text = "Call 010-1234-5678 for support."
        result = masker.mask(text)
        
        assert "010-1234-5678" not in result
        assert "[PHONE_REDACTED]" in result
    
    def test_phone_with_dots_masked(self, masker):
        """점 구분자 전화번호 마스킹"""
        text = "Contact: 010.1234.5678"
        result = masker.mask(text)
        
        assert "010.1234.5678" not in result


class TestPIIMaskingServiceUUIDTokens:
    """Test UUID-based token collision prevention"""
    
    @pytest.fixture
    def masker(self):
        return PIIMaskingService()
    
    def test_token_like_text_preserved(self, masker):
        """토큰처럼 보이는 텍스트가 보존됨"""
        text = "Old code used __URL_TOKEN_0__. Visit https://example.com."
        result = masker.mask(text)
        
        # Original token-like text preserved
        assert "__URL_TOKEN_0__" in result
        # Real URL also preserved
        assert "https://example.com" in result
    
    def test_no_token_collision(self, masker):
        """UUID 토큰은 충돌하지 않음"""
        # Multiple URLs should each get unique tokens
        text = "Visit https://a.com and https://b.com and https://c.com"
        result = masker.mask(text)
        
        assert "https://a.com" in result
        assert "https://b.com" in result
        assert "https://c.com" in result


class TestPIIMaskingServiceDictMasking:
    """Test dictionary masking"""
    
    @pytest.fixture
    def masker(self):
        return PIIMaskingService()
    
    def test_mask_dict_recursive(self, masker):
        """딕셔너리 재귀 마스킹"""
        data = {
            "user": {
                "email": "test@example.com",
                "profile_url": "https://example.com/user@tenant.io/profile"
            },
            "api_key": "sk-test123456789012345678901234567890"
        }
        
        result = masker.mask_dict(data)
        
        # Email masked
        assert "[EMAIL_REDACTED]" in result["user"]["email"]
        # URL-embedded email preserved
        assert "user@tenant.io" in result["user"]["profile_url"]
        # API key masked
        assert "[API_KEY_REDACTED]" in result["api_key"]


class TestPIIMaskingServiceSingleton:
    """Test singleton pattern"""
    
    def test_singleton_returns_same_instance(self):
        """싱글톤이 동일 인스턴스를 반환함"""
        service1 = get_pii_masking_service()
        service2 = get_pii_masking_service()
        
        assert service1 is service2


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
