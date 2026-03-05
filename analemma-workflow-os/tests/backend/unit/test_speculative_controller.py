# -*- coding: utf-8 -*-
"""Unit tests for SpeculativeExecutionController (Phase 1)."""

import time

import pytest

from src.services.execution.speculative_controller import (
    SIDE_EFFECTFUL_NODE_TYPES,
    SPECULATIVE_THRESHOLD,
    SpeculativeExecutionController,
    SpeculativeHandle,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def controller():
    return SpeculativeExecutionController()


def _segment_config(node_types=None, side_effect_nodes=None,
                     has_hitp=False, has_react=False, is_parallel=False):
    """Minimal segment_config dict for side-effect testing."""
    nodes = {}
    for i, nt in enumerate(node_types or []):
        nodes[f"node_{i}"] = {"type": nt}
    for i, _ in enumerate(side_effect_nodes or []):
        nodes[f"se_{i}"] = {"type": "custom", "side_effect": True}
    return {"nodes": nodes}


# ── should_speculate ─────────────────────────────────────────────────────────

class TestShouldSpeculate:

    def test_high_score_clean_segment_passes(self, controller):
        cfg = _segment_config(node_types=["aiModel", "transform"])
        assert controller.should_speculate(0.85, "PASS", cfg) is True

    def test_score_below_threshold_rejected(self, controller):
        assert controller.should_speculate(0.60, "PASS") is False

    def test_score_at_threshold_passes(self, controller):
        assert controller.should_speculate(SPECULATIVE_THRESHOLD, "PASS") is True

    def test_fail_verdict_rejected(self, controller):
        assert controller.should_speculate(0.90, "FAIL") is False

    def test_hitp_edge_rejected(self, controller):
        assert controller.should_speculate(
            0.90, "PASS", is_hitp_edge=True,
        ) is False

    def test_react_mode_rejected(self, controller):
        assert controller.should_speculate(
            0.90, "PASS", is_react=True,
        ) is False

    def test_parallel_branch_rejected(self, controller):
        assert controller.should_speculate(
            0.90, "PASS", is_parallel_branch=True,
        ) is False

    def test_side_effect_node_rejected(self, controller):
        cfg = _segment_config(side_effect_nodes=["webhook_sender"])
        assert controller.should_speculate(0.90, "PASS", cfg) is False

    def test_side_effectful_node_type_rejected(self, controller):
        for node_type in list(SIDE_EFFECTFUL_NODE_TYPES)[:3]:
            cfg = _segment_config(node_types=[node_type])
            assert controller.should_speculate(0.90, "PASS", cfg) is False, (
                f"Node type {node_type} should block speculation"
            )


# ── Lifecycle: begin -> commit / rollback ────────────────────────────────────

class TestSpeculativeLifecycle:

    def test_begin_and_commit(self, controller):
        handle = controller.begin_speculative(
            segment_id=1,
            state_snapshot={"key": "val"},
            merkle_parent_hash="abc123",
        )
        assert isinstance(handle, SpeculativeHandle)
        assert handle.segment_id == 1

        # Commit directly (no background verify needed for this test)
        controller.commit(handle)

    def test_begin_blocks_second_speculative(self, controller):
        controller.begin_speculative(1, {}, "h1")
        # Max 1 in-flight: should_speculate returns False
        assert controller.should_speculate(0.90, "PASS") is False
        # Cleanup by accessing the handle
        controller.commit(controller._active_handle)

    def test_state_snapshot_preserved_in_handle(self, controller):
        snapshot = {"restore_me": True}
        handle = controller.begin_speculative(2, snapshot, "h2")
        assert handle.state_snapshot == snapshot
        controller.commit(handle)

    def test_verify_background_pass(self, controller):
        handle = controller.begin_speculative(3, {}, "h3")

        def _verify_pass():
            """Return a result with final_verdict=PASS."""
            return type("R", (), {"final_verdict": "PASS"})()

        controller.verify_background(handle, _verify_pass, lambda: "hash_ok")
        time.sleep(0.15)
        abort_info = controller.check_abort(handle, timeout=1.0)
        assert abort_info is None

    def test_verify_background_fail_triggers_abort(self, controller):
        handle = controller.begin_speculative(4, {"snap": 1}, "h4")

        def _verify_fail():
            """Return a result with final_verdict=FAIL."""
            return type("R", (), {"final_verdict": "FAIL"})()

        controller.verify_background(handle, _verify_fail, lambda: "x")
        time.sleep(0.15)
        abort_info = controller.check_abort(handle, timeout=1.0)
        assert abort_info is not None


# ── Side-Effect Detection ────────────────────────────────────────────────────

class TestSideEffectDetection:

    def test_all_side_effectful_types_detected(self):
        ctrl = SpeculativeExecutionController()
        for node_type in SIDE_EFFECTFUL_NODE_TYPES:
            cfg = _segment_config(node_types=[node_type])
            assert ctrl._has_side_effects(cfg) is True, node_type

    def test_safe_nodes_not_flagged(self):
        ctrl = SpeculativeExecutionController()
        cfg = _segment_config(node_types=["aiModel", "transform", "validator"])
        assert ctrl._has_side_effects(cfg) is False

    def test_side_effect_flag_on_node(self):
        ctrl = SpeculativeExecutionController()
        cfg = {"nodes": {"n1": {"type": "custom", "side_effect": True}}}
        assert ctrl._has_side_effects(cfg) is True

    def test_external_api_flag_on_node(self):
        ctrl = SpeculativeExecutionController()
        cfg = {"nodes": {"n1": {"type": "custom", "external_api": True}}}
        assert ctrl._has_side_effects(cfg) is True
