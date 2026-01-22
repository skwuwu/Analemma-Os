from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class StateBag(dict):
    """
    🛡️ [v3.6] Data Ownership Defense: Null-Safe State Container
    
    Prevents inadvertent 'None' returns when accessing state keys.
    Acts exactly like a dict but overrides accessors to guarantee safety.
    """
    def __init__(self, initial_data: Optional[Dict[str, Any]] = None):
        super().__init__(initial_data or {})

    def get(self, key: str, default: Any = None) -> Any:
        """
        Safe get: If value is None, return default (or None if default is None).
        
        CRITICAL: Standard dict.get(key, default) returns None if the key exists but value is None.
        StateBag.get(key, default) returns default if the value is found to be None.
        """
        val = super().get(key, default)
        
        # 🛡️ Core Defense: If we got a value (val) but it is None,
        # AND the user provided a specific default (not None),
        # prefer the default to avoid NoneType crashes downstream.
        if val is None and default is not None:
            return default
            
        return val

    def __getitem__(self, key: str) -> Any:
        """
        Safe item access:
        1. Suppress KeyError (return None)
        2. If key exists but value is None, return None (Standard behavior, but safe)
        """
        try:
            return super().__getitem__(key)
        except KeyError:
            return None

    def copy(self) -> 'StateBag':
        return StateBag(super().copy())

def ensure_state_bag(state: Any) -> StateBag:
    """Helper to upgrade a dict to StateBag if needed"""
    if isinstance(state, StateBag):
        return state
    if state is None:
        return StateBag({})
    if isinstance(state, dict):
        return StateBag(state)
    
    # Fallback for unexpected types
    logger.warning(f"StateBag received non-dict type: {type(state)}. Returning empty StateBag.")
    return StateBag({})

def normalize_inplace(event: Dict[str, Any], remove_state_data: bool = False):
    """
    🛡️ [v3.6] 외부 이벤트를 커널 규격으로 정규화
    ImportModuleError를 해결하기 위해 이 함수가 반드시 이 파일에 정의되어야 함
    """
    if not isinstance(event, dict): return
    
    # StateBag 주입
    if 'current_state' in event:
        event['current_state'] = ensure_state_bag(event['current_state'])
    
    if remove_state_data and 'state_data' in event:
        del event['state_data']
