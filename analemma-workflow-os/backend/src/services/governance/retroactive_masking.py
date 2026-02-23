"""
Retroactive PII Masking
Post-detection PII masking in text output
"""
import re
import copy
import hashlib
import json
import logging
from typing import Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


def _replace_pii(text: str, pii_value: str, replacement: str, pii_type: str) -> str:
    """PII 값을 텍스트 안에서 경계를 인식하며 교체.

    str.replace()의 부분 일치 문제 방지:
    - email: "user@example.com"이 "user@example.com.au" 안에서 잘못 치환되는 것을 막음
    - 그 외(phone/ssn/card/ip): 숫자·문자 경계 보호
    """
    escaped = re.escape(pii_value)
    if pii_type == "email":
        # 이메일 뒤에 추가 도메인 레이블(.au, .kr 등)이 없는 위치에서만 치환
        pattern = escaped + r'(?!\.[A-Za-z])(?![A-Za-z0-9_%+@-])'
    else:
        # 숫자·영문자로 경계가 이어지는 경우 치환 금지
        pattern = r'(?<![0-9A-Za-z])' + escaped + r'(?![0-9A-Za-z])'
    return re.sub(pattern, replacement, text)


class PIIPattern:
    """PII Detection Regex Patterns"""
    
    # Email address - improved pattern with Unicode support
    EMAIL = r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
    
    # Phone number (Korean format)
    PHONE_KR = r'0\d{1,2}-?\d{3,4}-?\d{4}'
    
    # Social Security Number (Korean format)
    SSN_KR = r'\d{6}-?[1-4]\d{6}'
    
    # Credit card number (general format)
    CARD = r'\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}'
    
    # IP address (excluding private networks)
    IP_ADDRESS = r'(?!10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'


def detect_pii_regex(text: str) -> Dict[str, List[str]]:
    """
    Regex-based PII detection (backup mechanism)
    
    Args:
        text: Text to inspect
    
    Returns:
        {"email": [...], "phone": [...], "ssn": [...], "card": [...]}
    """
    pii_detected = {
        "email": [],
        "phone": [],
        "ssn": [],
        "card": [],
        "ip": []
    }
    
    # Email
    emails = re.findall(PIIPattern.EMAIL, text)
    pii_detected["email"] = list(set(emails))  # Remove duplicates
    
    # Phone (Korean)
    phones = re.findall(PIIPattern.PHONE_KR, text)
    pii_detected["phone"] = list(set(phones))
    
    # SSN (Korean)
    ssns = re.findall(PIIPattern.SSN_KR, text)
    pii_detected["ssn"] = list(set(ssns))
    
    # Card
    cards = re.findall(PIIPattern.CARD, text)
    pii_detected["card"] = list(set(cards))
    
    # IP Address
    ips = re.findall(PIIPattern.IP_ADDRESS, text)
    pii_detected["ip"] = list(set(ips))
    
    # Return all categories (not just non-empty ones for debugging)
    return pii_detected


def evaluate_pii_leakage_llm(
    agent_output: Dict[str, Any],
    agent_thought: str
) -> Dict[str, List[str]]:
    """
    LLM-based PII leakage evaluation (Article 6 verification)
    
    Args:
        agent_output: Agent output
        agent_thought: Agent internal thought process
    
    Returns:
        {"email": [...], "phone": [...], ...}
    """
    from common.llm_client import call_llm  # Assuming this exists
    
    # Combine all text fields
    text_fields = []
    for field in ["thought", "message", "response", "reasoning"]:
        if field in agent_output and isinstance(agent_output[field], str):
            text_fields.append(f"{field}: {agent_output[field]}")
    
    if agent_thought:
        text_fields.append(f"internal_thought: {agent_thought}")
    
    combined_text = "\n".join(text_fields)
    
    prompt = f"""You are a PII (Personally Identifiable Information) Detection Expert.

Analyze the following agent output for ANY instances of:
- Email addresses (e.g., user@example.com)
- Phone numbers (Korean format: 010-1234-5678 or international)
- Social Security Numbers / ID Numbers (Korean: 123456-1234567)
- Credit/Debit card numbers (1234-5678-9012-3456)
- IP addresses (external IPs only)

Agent Output:
{combined_text}

Your task: Extract ALL PII instances found. Even if they appear in natural language context.

Respond in JSON format:
{{
    "email": ["john.doe@example.com"],
    "phone": ["010-1234-5678"],
    "ssn": [],
    "card": ["1234-5678-9012-3456"],
    "ip": []
}}

If no PII found, return empty lists for all categories.
"""
    
    try:
        response = call_llm(
            prompt=prompt,
            model="gemini-2.0-flash",
            max_tokens=300
        )
        
        pii_detected = json.loads(response)
        
        # Validate structure
        for key in ["email", "phone", "ssn", "card", "ip"]:
            if key not in pii_detected:
                pii_detected[key] = []
        
        logger.info(
            f"[PII Detection LLM] Found: {sum(len(v) for v in pii_detected.values())} items "
            f"({', '.join(f'{k}:{len(v)}' for k, v in pii_detected.items() if v)})"
        )
        
        return pii_detected
        
    except Exception as e:
        logger.error(f"[PII Detection LLM] Failed: {e}")
        
        # Fallback to regex
        logger.warning("[PII Detection] Falling back to regex-based detection")
        return detect_pii_regex(combined_text)


def apply_retroactive_masking(
    output: Dict[str, Any],
    pii_map: Dict[str, List[str]]
) -> Dict[str, Any]:
    """
    Retroactively mask PII in text
    
    Args:
        output: Agent output
        pii_map: {"email": [...], "phone": [...], ...}
    
    Returns:
        Masked output
    """
    # deepcopy: 중첩 dict/list 원본 공유 방지 (shallow copy 버그 수정)
    masked = copy.deepcopy(output)

    # Maskable text fields
    text_fields = ["thought", "message", "response", "reasoning"]

    total_masked = 0

    for field in text_fields:
        if field not in masked:
            continue

        text = masked[field]
        if not isinstance(text, str):
            continue

        original_text = text

        # Mask emails
        for email in pii_map.get("email", []):
            # [:16]: 8자(32bit)에서 16자(64bit)로 확장 → 사전공격 역추적 난도 2^32배 상승
            email_hash = hashlib.sha256(email.encode()).hexdigest()[:16]
            text = _replace_pii(text, email, f"***EMAIL_{email_hash}***", "email")
            total_masked += 1

        # Mask phones
        for phone in pii_map.get("phone", []):
            phone_hash = hashlib.sha256(phone.encode()).hexdigest()[:16]
            text = _replace_pii(text, phone, f"***PHONE_{phone_hash}***", "phone")
            total_masked += 1

        # Mask SSNs
        for ssn in pii_map.get("ssn", []):
            ssn_hash = hashlib.sha256(ssn.encode()).hexdigest()[:16]
            text = _replace_pii(text, ssn, f"***SSN_{ssn_hash}***", "ssn")
            total_masked += 1

        # Mask card numbers
        for card in pii_map.get("card", []):
            card_hash = hashlib.sha256(card.encode()).hexdigest()[:16]
            text = _replace_pii(text, card, f"***CARD_{card_hash}***", "card")
            total_masked += 1

        # Mask IP addresses
        for ip in pii_map.get("ip", []):
            ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
            text = _replace_pii(text, ip, f"***IP_{ip_hash}***", "ip")
            total_masked += 1
        
        if text != original_text:
            logger.warning(
                f"[Retroactive Masking] Field '{field}' masked "
                f"({len(original_text)} chars → {len(text)} chars)"
            )
            masked[field] = text
    
    if total_masked > 0:
        logger.warning(
            f"[Retroactive Masking] Total {total_masked} PII instances masked"
        )
        
        # Add masking metadata
        masked["_pii_masked"] = True
        masked["_pii_mask_count"] = total_masked
        masked["_pii_categories"] = [k for k, v in pii_map.items() if v]
    
    return masked


def evaluate_and_mask_pii(
    agent_output: Dict[str, Any],
    agent_thought: str,
    use_llm: bool = True
) -> Tuple[Dict[str, Any], bool, Dict[str, List[str]]]:
    """
    PII evaluation and masking (unified interface)
    
    Args:
        agent_output: Agent output
        agent_thought: Agent internal thought
        use_llm: Use LLM for detection (if False, regex only)
    
    Returns:
        (masked_output, has_violation, pii_map)
    """
    # Detect PII
    if use_llm:
        pii_detected = evaluate_pii_leakage_llm(agent_output, agent_thought)
    else:
        # Combine all text for regex
        text_fields = []
        for field in ["thought", "message", "response", "reasoning"]:
            if field in agent_output and isinstance(agent_output[field], str):
                text_fields.append(agent_output[field])
        
        if agent_thought:
            text_fields.append(agent_thought)
        
        combined_text = " ".join(text_fields)
        pii_detected = detect_pii_regex(combined_text)
    
    # Check if any PII found
    has_pii_violation = any(len(v) > 0 for v in pii_detected.values())
    
    if has_pii_violation:
        # Apply masking
        masked_output = apply_retroactive_masking(agent_output, pii_detected)
        return masked_output, True, pii_detected
    
    return agent_output, False, {}
