"""
🛡️ Governance Module - Agent Control & Validation

This module provides autonomous agent governance capabilities:
- Governor Node: Validates agent outputs and generates _kernel commands
- Optimistic Governance: Ring-based async/sync validation
- Optimistic Rollback: Automatic recovery from violations (v2.1)
- Agent Feedback Loop: Self-correction guidance (v2.1)
"""

from .governor_runner import governor_node_runner

__all__ = ["governor_node_runner"]
