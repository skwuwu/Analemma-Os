"""
JSON serialization utilities for DynamoDB and API responses.

Consolidates DecimalEncoder, LLM response parsing, and related utilities
used across Lambda handlers.
"""

import json
import logging
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


class DecimalEncoder(json.JSONEncoder):
    """
    JSON Encoder that handles DynamoDB Decimal types and other special types.
    
    Converts:
    - Decimal with no fractional part -> int
    - Decimal with fractional part -> float
    - set -> list
    - Other non-serializable types -> str (fallback)
    
    Usage:
        json.dumps(data, cls=DecimalEncoder)
    """
    
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            # Integer if no fractional part, otherwise float
            return int(obj) if obj % 1 == 0 else float(obj)
        if isinstance(obj, set):
            return list(obj)
        # Try str() as fallback for unknown types
        try:
            return str(obj)
        except Exception:
            return super().default(obj)


def convert_decimals(obj: Any) -> Any:
    """
    Recursively convert Decimal objects returned by DynamoDB into native Python types.
    
    Args:
        obj: Any object that may contain Decimal values
        
    Returns:
        Object with all Decimals converted to int or float
    """
    if isinstance(obj, dict):
        return {k: convert_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_decimals(item) for item in obj]
    elif isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    return obj


def convert_to_dynamodb_format(obj: Any) -> Any:
    """
    Convert Python objects to DynamoDB-compatible format.
    
    Converts:
    - float -> Decimal (via string to preserve precision)
    - int -> kept as-is
    - dict/list -> recursively converted
    
    Args:
        obj: Any object to convert
        
    Returns:
        Object with floats converted to Decimal
    """
    if isinstance(obj, dict):
        return {k: convert_to_dynamodb_format(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_dynamodb_format(item) for item in obj]
    elif isinstance(obj, float):
        return Decimal(str(obj))
    return obj


def dumps_decimal(obj: Any, **kwargs) -> str:
    """
    Convenience wrapper for json.dumps with DecimalEncoder.
    
    Args:
        obj: Object to serialize
        **kwargs: Additional arguments passed to json.dumps
        
    Returns:
        JSON string
    """
    return json.dumps(obj, cls=DecimalEncoder, **kwargs)


def clean_llm_json_response(text: str) -> str:
    """
    Clean LLM response to extract valid JSON.

    LLMs often wrap JSON in markdown code blocks or add explanatory text.
    This function strips common artifacts:
    - Markdown code fences: ```json ... ```
    - Leading/trailing whitespace
    - Text before first { or [
    - Text after last } or ]

    Returns:
        Cleaned JSON string ready for parsing
    """
    if not isinstance(text, str):
        return str(text)

    # Remove markdown code fences
    text = text.strip()

    # Pattern 1: ```json\n{...}\n```
    if text.startswith("```json"):
        text = text[7:]  # Remove ```json
        if text.endswith("```"):
            text = text[:-3]  # Remove closing ```
    elif text.startswith("```"):
        text = text[3:]  # Remove generic ```
        if text.endswith("```"):
            text = text[:-3]

    text = text.strip()

    # Pattern 2: Text before/after JSON object or array
    # Find first { or [ and last } or ]
    start_obj = text.find('{')
    start_arr = text.find('[')

    # Determine actual start (whichever comes first, or -1 if neither found)
    if start_obj == -1 and start_arr == -1:
        return text  # No JSON structure found, return as-is
    elif start_obj == -1:
        start = start_arr
    elif start_arr == -1:
        start = start_obj
    else:
        start = min(start_obj, start_arr)

    # Find corresponding end
    if text[start] == '{':
        end = text.rfind('}')
    else:
        end = text.rfind(']')

    if end != -1 and end > start:
        text = text[start:end+1]

    return text.strip()


def parse_llm_json_response(text: str, fallback_value: Any = None) -> Any:
    """
    Attempt to parse LLM response as JSON with robust error handling.

    Args:
        text: Raw LLM response text
        fallback_value: Value to return if parsing fails (default: original text)

    Returns:
        Parsed JSON object, or fallback_value if parsing fails
    """
    if not isinstance(text, str):
        return fallback_value if fallback_value is not None else text

    try:
        # First attempt: direct parsing
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        # Second attempt: clean markdown artifacts
        cleaned = clean_llm_json_response(text)
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM response as JSON: {e}. Returning raw text.")
        return fallback_value if fallback_value is not None else text
    except Exception as e:
        logger.error(f"Unexpected error parsing LLM JSON: {e}")
        return fallback_value if fallback_value is not None else text


def get_ms_timestamp(value: Any) -> int:
    """
    Convert any timestamp format to milliseconds (defensive).

    Supported formats:
    - int/float: direct integer conversion
    - ISO 8601 string: "2026-01-26T08:12:53.732Z"
    - Unix timestamp string: "1769415175"
    - Invalid format: returns 0

    Args:
        value: Timestamp value to convert

    Returns:
        Integer timestamp in milliseconds
    """
    from datetime import datetime

    if value is None:
        return 0

    if isinstance(value, (int, float)):
        ts = int(value)
        # Convert seconds to milliseconds if needed
        if ts < 10_000_000_000:  # Less than 10 digits = seconds
            ts *= 1000
        return ts

    if isinstance(value, str):
        # Empty string or placeholder handling
        if not value or value in ('epoch_ms', 'unix_ms', 'iso', 'N/A', 'null', 'None'):
            return 0

        try:
            # ISO format string (contains T or date separator)
            if 'T' in value or (len(value) > 10 and '-' in value[:10]):
                normalized = value.replace('Z', '+00:00')
                dt = datetime.fromisoformat(normalized)
                return int(dt.timestamp() * 1000)

            # Numeric string
            ts = int(float(value))
            if ts < 10_000_000_000:
                ts *= 1000
            return ts
        except (ValueError, TypeError, OSError):
            return 0

    return 0
