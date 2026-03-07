# -*- coding: utf-8 -*-
"""
Unit tests for SpeculativeExecutionController — Default-Deny speculation model,
lifecycle transitions (PENDING -> COMMITTED / ABORTED).
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

import pytest
from services.execution.speculative_controller import (
    SpeculativeExecutionController,
    SpeculativeStatus,
    SPECULATABLE_NODE_TYPES,
    NEVER_SPECULATE_NODE_TYPES,
)


@pytest.fixture
def controller():
    """Create a SpeculativeExecutionController (no constructor arguments)."""
    return SpeculativeExecutionController()


class TestShouldSpeculate:
    def test_safe_type_allowed(self, controller):
        """Transform node type is in SPECULATABLE set -> can speculate."""
        config = {"nodes": [{"type": "transform"}]}
        result = controller.should_speculate(
            stage1_combined_score=0.9,
            stage1_verdict="PASS",
            next_segment_config=config,
        )
        assert result is True

    def test_never_type_blocked(self, controller):
        """aiModel node type is in NEVER_SPECULATE set -> blocked."""
        config = {"nodes": [{"type": "aiModel"}]}
        result = controller.should_speculate(
            stage1_combined_score=0.9,
            stage1_verdict="PASS",
            next_segment_config=config,
        )
        assert result is False

    def test_unknown_type_default_deny(self, controller):
        """Unknown node type -> Default-Deny returns False."""
        config = {"nodes": [{"type": "totally_unknown_type_xyz"}]}
        result = controller.should_speculate(
            stage1_combined_score=0.9,
            stage1_verdict="PASS",
            next_segment_config=config,
        )
        assert result is False

    def test_side_effect_marker_blocked(self, controller):
        """Node with side_effect marker -> blocked."""
        config = {"nodes": [{"type": "transform", "side_effect": True}]}
        result = controller.should_speculate(
            stage1_combined_score=0.9,
            stage1_verdict="PASS",
            next_segment_config=config,
        )
        assert result is False


class TestSpeculativeLifecycle:
    def test_begin_and_commit(self, controller):
        """Handle transitions from PENDING to COMMITTED."""
        handle = controller.begin_speculative(
            segment_id=1,
            state_snapshot={"key": "value"},
            merkle_parent_hash="abc123",
        )
        assert handle.status == SpeculativeStatus.PENDING
        controller.commit(handle)
        assert handle.status == SpeculativeStatus.COMMITTED

    def test_begin_and_abort(self, controller):
        """Handle transitions from PENDING to ABORTED via handle.resolve()."""
        handle = controller.begin_speculative(
            segment_id=2,
            state_snapshot={"key": "value"},
            merkle_parent_hash="def456",
        )
        assert handle.status == SpeculativeStatus.PENDING
        handle.resolve(SpeculativeStatus.ABORTED, reason="Test abort")
        assert handle.status == SpeculativeStatus.ABORTED
