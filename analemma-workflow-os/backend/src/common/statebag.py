from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class StateBag(dict):
    """
    🛡️ [v3.7] Recursive Data Ownership Defense
    
    Ensures that all nested dictionaries are automatically upgraded to StateBag,
    preventing 'NoneType' errors deep in the state structure.
    """
    def __init__(self, initial_data: Optional[Dict[str, Any]] = None):
        # 🛡️ Recursive wrap on init
        processed_data = {}
        if initial_data:
            for k, v in initial_data.items():
                processed_data[k] = self._wrap(v)
        super().__init__(processed_data)

    def _wrap(self, value: Any) -> Any:
        """Recursively upgrade dicts to StateBag"""
        if isinstance(value, dict) and not isinstance(value, StateBag):
            return StateBag(value)
        return value

    def __setitem__(self, key: str, value: Any):
        # 🛡️ Wrap on set
        super().__setitem__(key, self._wrap(value))

    def get(self, key: str, default: Any = None) -> Any:
        """
        Safe get with default promotion.
        If value is found as None, and default is provided, return wrapped default.
        """
        val = super().get(key, default)
        
        # 🛡️ Core Defense: Promote default if value is None
        if val is None and default is not None:
             # Note: logic requires wrapping the default if it's returned
            return self._wrap(default)
        return val

    def __getitem__(self, key: str) -> Any:
        """
        Safe item access using get semantics.
        Returns None if key missing, or the value (wrapped) if present.
        """
        # Note: The user provided snippet uses super().get(key), which returns None on missing.
        # It relies on the value being already wrapped by __setitem__ / __init__.
        val = super().get(key)
        return val

    def copy(self) -> 'StateBag':
        return StateBag(super().copy())

def ensure_state_bag(state: Any) -> StateBag:
    """Helper to upgrade a dict to StateBag if needed"""
    if isinstance(state, StateBag):
        return state
    # Recursion happens inside StateBag constructor
    return StateBag(state if isinstance(state, dict) else {})

def normalize_inplace(event: Dict[str, Any], remove_state_data: bool = False):
    """
    🛡️ [v3.6] Legacy Support & Event Normalization
    REQUIRED for import compatibility
    """
    if not isinstance(event, dict): return
    
    if 'current_state' in event:
        event['current_state'] = ensure_state_bag(event['current_state'])
    
    if remove_state_data and 'state_data' in event:
        del event['state_data']
