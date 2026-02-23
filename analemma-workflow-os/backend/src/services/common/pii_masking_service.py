"""
PIIMaskingService - Advanced PII Masking with URL Protection

Provides robust PII (Personally Identifiable Information) masking that:
- Protects URLs from being corrupted by PII masking
- Correctly handles trailing punctuation in URLs
- Uses UUID-based tokens to prevent text collision
- Preserves URL-embedded patterns (e.g., user@domain.com in URLs)

Author: Analemma Team
"""

import re
import uuid
import logging
import threading
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class PIIMaskingService:
    """
    Advanced PII masking service with URL protection.
    
    This service addresses common edge cases in PII masking:
    1. URL-embedded email patterns should NOT be masked
    2. Trailing punctuation should NOT be included in URLs
    3. Parentheses in URLs (e.g., Wikipedia) should be preserved
    4. UUID tokens prevent collision with original text
    """
    
    # PII patterns to mask
    PII_PATTERNS: List[Tuple[re.Pattern, str]] = [
        # API Keys (OpenAI, Anthropic, etc.) - includes hyphens in key
        (re.compile(r"\bsk-[a-zA-Z0-9-]{20,}\b"), "[API_KEY_REDACTED]"),
        # Email addresses (with negative lookbehind to skip URL contexts)
        (re.compile(r"(?<![/=@])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})(?![/a-zA-Z0-9])"), "[EMAIL_REDACTED]"),
        # Korean phone numbers
        (re.compile(r"\d{3}[-.\s]?\d{3,4}[-.\s]?\d{4}"), "[PHONE_REDACTED]"),
        # US Social Security Numbers
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
        # Credit card numbers (simple pattern)
        (re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"), "[CARD_REDACTED]"),
    ]
    
    # Improved URL pattern that excludes trailing punctuation
    # Based on Google/Twitter-style URL detection logic
    URL_PATTERN = re.compile(
        r'(https?://[^\s()<>]+(?:\([\w\d]+\)|[^\s`!()\[\]{};:\'".,<>?«»""'']))',
        re.IGNORECASE
    )
    
    # Fallback simpler pattern for edge cases
    URL_PATTERN_SIMPLE = re.compile(
        r'(https?://[^\s<>]+)',
        re.IGNORECASE
    )
    
    # Trailing punctuation to strip from URLs
    TRAILING_PUNCT = '.,:;!?\'")'
    
    def __init__(self, strict_mode: bool = True):
        """
        Initialize PII masking service.
        
        Args:
            strict_mode: If True, use stricter URL pattern that excludes
                         trailing punctuation. If False, use simpler pattern.
        """
        self.strict_mode = strict_mode
        self._url_pattern = self.URL_PATTERN if strict_mode else self.URL_PATTERN_SIMPLE
    
    def mask(self, text: Any) -> Any:
        """
        Mask PII in text while preserving URLs.
        
        Process:
        1. Extract and stash URLs with UUID-based tokens
        2. Apply PII masking patterns
        3. Restore URLs from stash
        
        Args:
            text: Text to mask (only strings are processed)
            
        Returns:
            Masked text with PII redacted but URLs preserved
        """
        if not isinstance(text, str):
            return text
        
        if not text.strip():
            return text
        
        # Step 1: Stash URLs with UUID tokens
        url_stash = {}
        masked_text = self._stash_urls(text, url_stash)
        
        # Step 2: Apply PII masking
        for pattern, replacement in self.PII_PATTERNS:
            masked_text = pattern.sub(replacement, masked_text)
        
        # Step 3: Restore URLs
        masked_text = self._restore_urls(masked_text, url_stash)
        
        return masked_text
    
    def _stash_urls(self, text: str, url_stash: Dict[str, str]) -> str:
        """
        Replace URLs with UUID-based tokens.
        
        UUID tokens prevent collision with text that might
        accidentally look like tokens (e.g., __URL_TOKEN_0__).
        """
        def replace_url(match):
            url = match.group(0)
            
            # Clean trailing punctuation if in strict mode
            if self.strict_mode:
                cleaned_url, trailing = self._clean_url_trailing(url)
            else:
                cleaned_url, trailing = url, ""
            
            # Validate URL structure
            try:
                parsed = urlparse(cleaned_url)
                if not (parsed.scheme and parsed.netloc):
                    return url  # Invalid URL, don't stash
            except Exception:
                return url
            
            # Generate UUID token using only letters to avoid phone pattern collision
            # Convert hex to alphabetic representation
            token = f"__URL_STASH_{self._uuid_to_alpha()}__"
            url_stash[token] = cleaned_url
            
            return token + trailing
        
        return self._url_pattern.sub(replace_url, text)
    
    def _uuid_to_alpha(self) -> str:
        """
        Generate an alphabetic-only token from UUID.
        
        Converts UUID hex digits to letters to prevent collision
        with phone number patterns (which match digit sequences).
        
        Returns:
            32-character alphabetic string
        """
        hex_uuid = uuid.uuid4().hex
        # Map 0-9 to a-j, a-f to k-p
        mapping = str.maketrans('0123456789abcdef', 'abcdefghijklmnop')
        return hex_uuid.translate(mapping)
    
    @staticmethod
    def _parens_balanced(url: str) -> bool:
        """괄호 순서를 고려한 균형 검사.

        count() 비교만으로는 ')(' 처럼 순서가 뒤집힌 케이스를 잡지 못한다.
        카운터가 음수가 되는 순간(닫힘이 열림보다 먼저) False를 반환한다.
        """
        depth = 0
        for ch in url:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth < 0:   # 닫힘이 열림보다 먼저 → 불균형
                    return False
        return depth == 0

    def _clean_url_trailing(self, url: str) -> Tuple[str, str]:
        """
        Remove trailing punctuation from URL while preserving balanced parentheses.

        Examples:
            "https://ex.com/page." -> ("https://ex.com/page", ".")
            "https://ex.com/wiki/A_(B)" -> ("https://ex.com/wiki/A_(B)", "")
            "https://ex.com/page)." -> ("https://ex.com/page", ").")
            "https://ex.com/url)(" -> ("https://ex.com/url", ")(")   ← 수정 전 버그 케이스

        Returns:
            Tuple of (cleaned_url, trailing_chars)
        """
        trailing_chars = []

        while url:
            last_char = url[-1]

            # 끝이 ')' 이고 괄호가 순서까지 균형 잡혀 있으면 URL의 일부로 판단
            if last_char == ')' and self._parens_balanced(url):
                break

            # 후행 구두점이면 제거
            if last_char in self.TRAILING_PUNCT:
                trailing_chars.append(last_char)
                url = url[:-1]
            else:
                break

        trailing = ''.join(reversed(trailing_chars))
        return url, trailing
    
    def _restore_urls(self, text: str, url_stash: Dict[str, str]) -> str:
        """Restore URLs from UUID token stash."""
        for token, url in url_stash.items():
            text = text.replace(token, url)
        return text
    
    def mask_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively mask PII in dictionary values.
        
        Args:
            data: Dictionary with potentially sensitive values
            
        Returns:
            Dictionary with PII masked
        """
        if not isinstance(data, dict):
            return self.mask(data)
        
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.mask(value)
            elif isinstance(value, dict):
                result[key] = self.mask_dict(value)
            elif isinstance(value, list):
                result[key] = [self.mask_dict(item) if isinstance(item, dict) else self.mask(item) for item in value]
            else:
                result[key] = value
        return result
    
    def get_masked_log_entry(self, message: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Create a log entry with PII masked.
        
        Useful for Glass-Box logging where sensitive data must be redacted.
        
        Args:
            message: Log message
            context: Additional context data
            
        Returns:
            Dict with masked message and context
        """
        entry = {
            "message": self.mask(message),
        }
        
        if context:
            entry["context"] = self.mask_dict(context)
        
        return entry


# Singleton instance
_pii_masking_instance = None
_pii_masking_lock = threading.Lock()


def get_pii_masking_service(strict_mode: bool = True) -> PIIMaskingService:
    """
    Get or create the singleton PIIMaskingService instance (스레드 안전).

    Args:
        strict_mode: If True, use stricter URL handling

    Returns:
        PIIMaskingService instance
    """
    global _pii_masking_instance
    if _pii_masking_instance is None:
        with _pii_masking_lock:
            if _pii_masking_instance is None:  # double-checked locking
                _pii_masking_instance = PIIMaskingService(strict_mode=strict_mode)
    return _pii_masking_instance
