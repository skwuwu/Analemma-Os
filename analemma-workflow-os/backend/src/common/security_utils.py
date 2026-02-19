# -*- coding: utf-8 -*-
"""
Security Utilities - PII 마스킹 및 보안 유틸리티

Phase E에서 StateManager로부터 분리된 보안 관련 유틸리티입니다.
"""

import re
import logging
from typing import Dict, Any, List, Set

logger = logging.getLogger(__name__)

# PII 필드 패턴 (정규식)
PII_FIELD_PATTERNS = [
    re.compile(r'.*email.*', re.IGNORECASE),
    re.compile(r'.*password.*', re.IGNORECASE),
    re.compile(r'.*ssn.*', re.IGNORECASE),
    re.compile(r'.*social.*security.*', re.IGNORECASE),
    re.compile(r'.*credit.*card.*', re.IGNORECASE),
    re.compile(r'.*phone.*', re.IGNORECASE),
    re.compile(r'.*address.*', re.IGNORECASE),
    re.compile(r'.*dob.*', re.IGNORECASE),
    re.compile(r'.*birth.*date.*', re.IGNORECASE),
]

# 명시적 PII 필드 (exact match)
EXPLICIT_PII_FIELDS: Set[str] = {
    'email', 'password', 'ssn', 'social_security_number',
    'credit_card', 'phone', 'phone_number', 'address',
    'date_of_birth', 'dob', 'driver_license'
}


def is_pii_field(field_name: str) -> bool:
    """
    필드명이 PII 필드인지 확인
    
    Args:
        field_name: 필드명
    
    Returns:
        bool: PII 필드 여부
    """
    # Explicit match
    if field_name.lower() in EXPLICIT_PII_FIELDS:
        return True
    
    # Pattern match
    for pattern in PII_FIELD_PATTERNS:
        if pattern.match(field_name):
            return True
    
    return False


def mask_pii_value(value: Any) -> str:
    """
    PII 값을 마스킹
    
    Args:
        value: 마스킹할 값
    
    Returns:
        str: 마스킹된 값
    """
    if value is None:
        return "***MASKED***"
    
    value_str = str(value)
    
    # Email 패턴
    if '@' in value_str:
        parts = value_str.split('@')
        if len(parts) == 2:
            username = parts[0]
            domain = parts[1]
            # 첫 2글자만 표시
            masked_username = username[:2] + '*' * (len(username) - 2) if len(username) > 2 else '**'
            return f"{masked_username}@{domain}"
    
    # 전화번호 패턴 (숫자만)
    if value_str.replace('-', '').replace(' ', '').isdigit():
        digits = value_str.replace('-', '').replace(' ', '')
        if len(digits) >= 10:
            # 마지막 4자리만 표시
            return '*' * (len(digits) - 4) + digits[-4:]
    
    # 기본 마스킹: 첫 3글자만 표시
    if len(value_str) > 3:
        return value_str[:3] + '*' * (len(value_str) - 3)
    else:
        return '***'


def mask_pii_in_state(state: Dict[str, Any], recursive: bool = True) -> Dict[str, Any]:
    """
    ✅ Phase E: StateManager에서 분리된 PII 마스킹 유틸리티
    
    State 딕셔너리에서 PII 필드를 자동 탐지하여 마스킹합니다.
    
    Args:
        state: 마스킹할 State 딕셔너리
        recursive: 중첩된 딕셔너리도 재귀적으로 마스킹
    
    Returns:
        Dict: 마스킹된 State (원본은 변경되지 않음)
    
    Example:
        >>> state = {'user_email': 'john@example.com', 'age': 30}
        >>> masked = mask_pii_in_state(state)
        >>> masked['user_email']
        'jo***@example.com'
    """
    if not isinstance(state, dict):
        return state
    
    masked_state = {}
    
    for key, value in state.items():
        # PII 필드 확인
        if is_pii_field(key):
            masked_state[key] = mask_pii_value(value)
            logger.debug(f"[PII Masking] Masked field: {key}")
        elif recursive and isinstance(value, dict):
            # 중첩된 딕셔너리 재귀 처리
            masked_state[key] = mask_pii_in_state(value, recursive=True)
        elif recursive and isinstance(value, list):
            # 리스트 내 딕셔너리 처리
            masked_list = []
            for item in value:
                if isinstance(item, dict):
                    masked_list.append(mask_pii_in_state(item, recursive=True))
                else:
                    masked_list.append(item)
            masked_state[key] = masked_list
        else:
            masked_state[key] = value
    
    return masked_state


def sanitize_for_logging(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    로깅용 데이터 정제 (PII 마스킹 + 크기 제한)
    
    Args:
        data: 정제할 데이터
    
    Returns:
        Dict: 정제된 데이터
    """
    # PII 마스킹
    sanitized = mask_pii_in_state(data, recursive=True)
    
    # 크기 제한 (큰 필드는 요약)
    MAX_FIELD_SIZE = 1000  # 1KB
    
    for key, value in sanitized.items():
        if isinstance(value, str) and len(value) > MAX_FIELD_SIZE:
            sanitized[key] = f"{value[:MAX_FIELD_SIZE]}... (truncated {len(value) - MAX_FIELD_SIZE} chars)"
        elif isinstance(value, (list, dict)):
            value_str = str(value)
            if len(value_str) > MAX_FIELD_SIZE:
                sanitized[key] = f"<{type(value).__name__} with {len(value)} items>"
    
    return sanitized


def validate_no_pii_in_logs(log_message: str) -> bool:
    """
    로그 메시지에 PII가 포함되어 있는지 검증
    
    Args:
        log_message: 검증할 로그 메시지
    
    Returns:
        bool: PII 없으면 True
    """
    # Email 패턴
    email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    if email_pattern.search(log_message):
        logger.warning("[PII Validation] Email detected in log message!")
        return False
    
    # SSN 패턴 (XXX-XX-XXXX)
    ssn_pattern = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
    if ssn_pattern.search(log_message):
        logger.warning("[PII Validation] SSN detected in log message!")
        return False
    
    # Credit Card 패턴 (16자리 숫자)
    cc_pattern = re.compile(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b')
    if cc_pattern.search(log_message):
        logger.warning("[PII Validation] Credit Card detected in log message!")
        return False
    
    return True


# Backward Compatibility: StateManager에서 import하던 코드 지원
__all__ = [
    'mask_pii_in_state',
    'mask_pii_value',
    'is_pii_field',
    'sanitize_for_logging',
    'validate_no_pii_in_logs'
]
