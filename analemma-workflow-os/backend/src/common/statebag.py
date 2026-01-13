"""State-bag normalization helpers.

Provide a small helper to normalize incoming Lambda/Step Functions events so
handlers can accept either legacy top-level fields or the new `state_data`
bag. The normalization is intentionally non-destructive: explicit top-level
keys are not overwritten by values inside `state_data`.
"""
from typing import Any, Dict, Union


def normalize_event(event: Union[Dict[str, Any], Any], remove_state_data: bool = False) -> Union[Dict[str, Any], Any]:
    """Return a normalized event where, if `event` is a dict and contains
    a `state_data` dict, the keys from `state_data` are copied into the
    top-level event only if they don't already exist.

    Args:
        event: The event dict to normalize
        remove_state_data: If True, removes the 'state_data' key after merging
                          to optimize Step Functions payload size (default: False)

    The function is a no-op for non-dict events.
    """
    if not isinstance(event, dict):
        return event

    sd = event.get("state_data")
    if not isinstance(sd, dict):
        return event

    # Shallow copy to avoid mutating caller's dict in unexpected ways.
    out = dict(event)
    for k, v in sd.items():
        if k not in out:
            out[k] = v
    
    # Step Functions Payload 최적화: 병합 후 state_data 제거
    if remove_state_data:
        out.pop("state_data", None)
    
    return out


def normalize_inplace(event: Union[Dict[str, Any], Any], remove_state_data: bool = False) -> Union[Dict[str, Any], Any]:
    """Mutating variant: copies keys from src.state_data into event in-place.
    Use when callers want to preserve the same dict object.

    Args:
        event: The event dict to normalize (modified in-place)
        remove_state_data: If True, removes the 'state_data' key after merging
                          to optimize Step Functions payload size (default: False)
    """
    if not isinstance(event, dict):
        return event
    sd = event.get("state_data")
    if not isinstance(sd, dict):
        return event
    
    # Pythonic setdefault 활용: C 레벨 최적화로 미세하게 더 빠름
    for k, v in sd.items():
        event.setdefault(k, v)
    
    # Step Functions Payload 최적화: 병합 후 state_data 제거
    if remove_state_data:
        event.pop("state_data", None)
    
    return event
