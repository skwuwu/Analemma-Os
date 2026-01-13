"""
JSON serialization utilities for DynamoDB and API responses.

Consolidates DecimalEncoder and related utilities used across Lambda handlers.
"""

import json
from decimal import Decimal
from typing import Any


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
