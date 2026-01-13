"""
TemplateRenderer - Template Rendering and PII Masking Service

Extracted from `main.py` for maintainability.
Handles {{variable}} substitution and sensitive data masking.
"""

import re
import json
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


class TemplateRenderer:
    """
    Service for rendering Jinja-like {{variable}} templates
    and masking PII in text content.
    """
    
    # PII patterns for Glass-Box logging
    PII_REGEX_PATTERNS: List[Tuple[str, str]] = [
        (r"\bsk-[a-zA-Z0-9]{20,}\b", "[API_KEY_REDACTED]"),
        (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL_REDACTED]"),
        (r"\d{3}-\d{3,4}-\d{4}", "[PHONE_REDACTED]"),
    ]

    def render(self, template: Any, state: Dict[str, Any]) -> Any:
        """
        Render {{variable}} templates against the provided state.
        
        Supports:
        - String templates with {{key}} or {{nested.key}}
        - Dict templates (recursive rendering)
        - List templates (recursive rendering)
        - Special {{__state_json}} for full state serialization
        
        Args:
            template: The template to render (can be str, dict, list, or other)
            state: The state dictionary for variable substitution
            
        Returns:
            Rendered template with variables substituted
        """
        if template is None:
            return None
            
        if isinstance(template, str):
            return self._render_string(template, state)
            
        if isinstance(template, dict):
            return {k: self.render(v, state) for k, v in template.items()}
            
        if isinstance(template, list):
            return [self.render(v, state) for v in template]
            
        return template

    def _render_string(self, template: str, state: Dict[str, Any]) -> str:
        """Render string template with variable substitution."""
        def _repl(match):
            key = match.group(1).strip()
            
            # Special case: full state as JSON
            if key == "__state_json":
                try:
                    return json.dumps(state, ensure_ascii=False)
                except Exception:
                    return str(state)
            
            # Normal variable lookup
            value = self.get_nested_value(state, key, "")
            
            # Serialize complex types
            if isinstance(value, (dict, list)):
                try:
                    return json.dumps(value)
                except Exception:
                    return str(value)
            
            return str(value)
        
        return re.sub(r"\{\{\s*([\w\.]+)\s*\}\}", _repl, template)

    def get_nested_value(
        self, 
        state: Dict[str, Any], 
        path: str, 
        default: Any = ""
    ) -> Any:
        """
        Retrieve nested value from state using dot-separated path.
        
        Args:
            state: The state dictionary
            path: Dot-separated path (e.g., "user.profile.name")
            default: Default value if path not found
            
        Returns:
            Value at path or default
        """
        if not path:
            return default
            
        parts = path.split('.')
        current: Any = state
        
        try:
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return default
            return current
        except Exception:
            return default

    def mask_pii(self, text: Any) -> Any:
        """
        Mask PII (Personally Identifiable Information) in text.
        
        Delegates to PIIMaskingService for improved handling:
        - URL protection (emails inside URLs are preserved)
        - Trailing punctuation handling
        - UUID-based token collision prevention
        
        Fallback to basic patterns if service import fails.
        
        Args:
            text: Text to mask (only strings are processed)
            
        Returns:
            Masked text or original value if not a string
        """
        if not isinstance(text, str):
            return text
        
        try:
            from src.services.common.pii_masking_service import get_pii_masking_service
            pii_service = get_pii_masking_service()
            return pii_service.mask(text)
        except ImportError:
            # Fallback to basic patterns
            logger.warning("PIIMaskingService not available, using basic patterns")
            masked = text
            for pattern, replacement in self.PII_REGEX_PATTERNS:
                masked = re.sub(pattern, replacement, masked)
            return masked


# Singleton instance
_renderer_instance = None

def get_template_renderer() -> TemplateRenderer:
    """Get or create the singleton TemplateRenderer instance."""
    global _renderer_instance
    if _renderer_instance is None:
        _renderer_instance = TemplateRenderer()
    return _renderer_instance
