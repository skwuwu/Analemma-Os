"""
Error Classifier Service (v3.9)
===============================

에러를 두 가지 경로로 분류:
1. DETERMINISTIC (자동 복구 가능): JSON 구조 오류, 스키마 불일치, 단순 문법 에러
2. SEMANTIC (수동 개입 필요): 가드레일 위반, 논리적 모순, 3회 이상 자동 복구 실패
"""

import re
import logging
from enum import Enum
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """에러 분류"""
    DETERMINISTIC = "deterministic"  # 자동 복구 가능
    SEMANTIC = "semantic"            # 수동 개입 필요
    UNKNOWN = "unknown"              # 분류 불가


class ErrorClassifier:
    """
    에러 분류기: Deterministic vs Semantic 에러 판별
    
    Deterministic Errors (자동 복구 대상):
    - JSONDecodeError, SyntaxError
    - 스키마 불일치 (ValidationError)
    - 단순 파이썬 문법 에러
    - 일시적 API 오류 (Rate Limit, Timeout)
    
    Semantic Errors (수동 개입 대상):
    - 가드레일 위반 (SecurityViolation, SIGKILL)
    - 논리적 모순 (Infinite loop detected)
    - 3회 이상 자동 복구 실패
    - 권한/인증 오류
    """
    
    # Deterministic 에러 패턴 (자동 복구 가능)
    DETERMINISTIC_PATTERNS = [
        # JSON/Schema 에러
        r"JSONDecodeError",
        r"Invalid JSON",
        r"Unexpected token",
        r"json\.loads",
        r"ValidationError",
        r"schema.*mismatch",
        r"missing.*required.*field",
        
        # Python 문법 에러
        r"SyntaxError",
        r"IndentationError",
        r"NameError: name .* is not defined",
        r"TypeError: .* takes .* arguments",
        r"KeyError:",
        r"IndexError:",
        r"AttributeError:",
        
        # 일시적 API 에러
        r"Rate limit",
        r"RateLimitError",
        r"429",
        r"Too Many Requests",
        r"Timeout",
        r"TimeoutError",
        r"Connection.*reset",
        r"ETIMEDOUT",
        r"ECONNREFUSED",
        
        # Bedrock/LLM 일시적 에러
        r"ThrottlingException",
        r"ServiceUnavailable",
        r"ModelStreamErrorException",
        r"InternalServerError",
    ]
    
    # Semantic 에러 패턴 (수동 개입 필요)
    SEMANTIC_PATTERNS = [
        # 보안/가드레일 위반
        r"SIGKILL",
        r"SecurityViolation",
        r"PromptInjection",
        r"Ring.*Protection",
        r"Guardrail.*violated",
        r"forbidden",
        r"AccessDenied",
        r"UnauthorizedAccess",
        
        # 무한 루프/재귀 한계
        r"LoopLimitExceeded",
        r"BranchLoopLimitExceeded",
        r"RecursionError",
        r"maximum recursion depth",
        r"Infinite loop",
        
        # 논리적 모순
        r"Logical.*contradiction",
        r"Circular.*dependency",
        r"Deadlock",
        
        # 리소스 고갈
        r"Resource.*Exhaustion",
        r"MemoryError",
        r"OutOfMemory",
        r"MAX_SPLIT_DEPTH",
        
        # 인증/권한 에러
        r"AuthenticationError",
        r"CredentialsError",
        r"InvalidToken",
        r"403",
        r"401",
    ]
    
    MAX_AUTO_HEALING_COUNT = 3
    
    def __init__(self):
        # 패턴 컴파일 (성능 최적화)
        self._deterministic_compiled = [
            re.compile(p, re.IGNORECASE) for p in self.DETERMINISTIC_PATTERNS
        ]
        self._semantic_compiled = [
            re.compile(p, re.IGNORECASE) for p in self.SEMANTIC_PATTERNS
        ]
    
    def classify(
        self, 
        error_type: str, 
        error_message: str, 
        healing_count: int = 0,
        context: Optional[Dict[str, Any]] = None
    ) -> Tuple[ErrorCategory, str]:
        """
        에러를 분류하고 카테고리와 이유를 반환합니다.
        
        Args:
            error_type: 에러 타입 (e.g., "JSONDecodeError")
            error_message: 에러 메시지
            healing_count: 현재까지의 자동 복구 시도 횟수
            context: 추가 컨텍스트 (optional)
        
        Returns:
            Tuple[ErrorCategory, str]: (분류, 이유)
        """
        full_error_text = f"{error_type}: {error_message}"
        
        # 1. Circuit Breaker 확인 (3회 초과 시 무조건 SEMANTIC)
        if healing_count >= self.MAX_AUTO_HEALING_COUNT:
            return (
                ErrorCategory.SEMANTIC,
                f"Circuit breaker triggered: {healing_count} auto-healing attempts exceeded limit ({self.MAX_AUTO_HEALING_COUNT})"
            )
        
        # 2. Semantic 패턴 우선 확인 (더 위험한 에러)
        for pattern in self._semantic_compiled:
            if pattern.search(full_error_text):
                return (
                    ErrorCategory.SEMANTIC,
                    f"Semantic error detected: {pattern.pattern}"
                )
        
        # 3. Deterministic 패턴 확인
        for pattern in self._deterministic_compiled:
            if pattern.search(full_error_text):
                return (
                    ErrorCategory.DETERMINISTIC,
                    f"Deterministic error detected: {pattern.pattern}"
                )
        
        # 4. Context 기반 추가 분류 (optional)
        if context:
            # 가드레일 관련 메타데이터 확인
            if context.get("guardrail_violated") or context.get("security_violation"):
                return (
                    ErrorCategory.SEMANTIC,
                    "Security context flag detected"
                )
            
            # 이전 복구 실패 이력 확인
            if context.get("previous_healing_failed"):
                return (
                    ErrorCategory.SEMANTIC,
                    "Previous healing attempt failed"
                )
        
        # 5. 분류 불가 → 기본적으로 SEMANTIC (안전한 경로)
        logger.warning(f"Unknown error type, defaulting to SEMANTIC: {error_type}")
        return (
            ErrorCategory.SEMANTIC,
            f"Unknown error type, defaulting to manual intervention: {error_type}"
        )
    
    def should_auto_heal(
        self, 
        error_type: str, 
        error_message: str, 
        healing_count: int = 0,
        context: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        """
        자동 복구 가능 여부를 판단합니다.
        
        Returns:
            Tuple[bool, str]: (자동 복구 가능 여부, 이유)
        """
        category, reason = self.classify(error_type, error_message, healing_count, context)
        
        if category == ErrorCategory.DETERMINISTIC:
            return True, reason
        else:
            return False, reason
    
    def get_healing_advice(
        self, 
        error_type: str, 
        error_message: str
    ) -> Optional[str]:
        """
        에러 유형에 따른 기본 수정 제안을 반환합니다.
        LLM 기반 분석 전에 사용할 수 있는 휴리스틱 힌트입니다.
        """
        full_text = f"{error_type}: {error_message}"
        
        # JSON 관련 에러
        if re.search(r"JSON|json\.loads|Unexpected token", full_text, re.IGNORECASE):
            return "Escape special characters in JSON strings. Check for unquoted keys or trailing commas."
        
        # Python 문법 에러
        if re.search(r"SyntaxError|IndentationError", full_text, re.IGNORECASE):
            return "Check Python syntax: indentation, colons, parentheses matching."
        
        # KeyError
        if re.search(r"KeyError", full_text, re.IGNORECASE):
            return "Use .get() method instead of direct key access, or check if key exists before accessing."
        
        # TypeError (arguments)
        if re.search(r"TypeError.*arguments", full_text, re.IGNORECASE):
            return "Check function signature and number of arguments passed."
        
        # Rate limit
        if re.search(r"Rate limit|429|ThrottlingException", full_text, re.IGNORECASE):
            return "Apply exponential backoff and retry after a short delay."
        
        # Timeout
        if re.search(r"Timeout|ETIMEDOUT", full_text, re.IGNORECASE):
            return "Increase timeout or reduce payload size. Consider chunking large requests."
        
        return None


# Singleton instance
_error_classifier = None


def get_error_classifier() -> ErrorClassifier:
    """싱글톤 ErrorClassifier 인스턴스 반환"""
    global _error_classifier
    if _error_classifier is None:
        _error_classifier = ErrorClassifier()
    return _error_classifier
