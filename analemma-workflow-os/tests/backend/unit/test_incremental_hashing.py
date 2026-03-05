# -*- coding: utf-8 -*-
"""Unit tests for incremental dirty-key hashing (Phase 3)."""

import pytest

from src.common.hash_utils import (
    HOT_FIELDS,
    WARM_FIELDS,
    COLD_FIELDS,
    SubBlockHashRegistry,
    classify_field_block,
    content_hash,
    streaming_content_hash,
)


# ── classify_field_block ─────────────────────────────────────────────────────

class TestClassifyFieldBlock:

    def test_hot_fields(self):
        for f in HOT_FIELDS:
            assert classify_field_block(f) == "hot", f

    def test_warm_fields(self):
        for f in WARM_FIELDS:
            assert classify_field_block(f) == "warm", f

    def test_cold_fields(self):
        for f in COLD_FIELDS:
            assert classify_field_block(f) == "cold", f

    def test_unknown_field_is_unclassified(self):
        assert classify_field_block("random_user_key_xyz") == "unclassified"


# ── streaming_content_hash ───────────────────────────────────────────────────

class TestStreamingContentHash:

    def test_deterministic(self):
        data = {"b": 2, "a": 1, "c": [3, 4]}
        h1 = streaming_content_hash(data)
        h2 = streaming_content_hash(data)
        assert h1 == h2

    def test_different_data_different_hash(self):
        h1 = streaming_content_hash({"a": 1})
        h2 = streaming_content_hash({"a": 2})
        assert h1 != h2

    def test_key_order_irrelevant(self):
        """Keys are sorted, so insertion order should not matter."""
        h1 = streaming_content_hash({"z": 1, "a": 2})
        h2 = streaming_content_hash({"a": 2, "z": 1})
        assert h1 == h2

    def test_empty_dict(self):
        h = streaming_content_hash({})
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex length


# ── SubBlockHashRegistry ─────────────────────────────────────────────────────

class TestSubBlockHashRegistry:

    @pytest.fixture
    def sample_state(self):
        return {
            # HOT
            "llm_response": "Hello world",
            "total_tokens": 42,
            # WARM
            "step_history": [{"step": 1}],
            "messages": ["msg1"],
            # COLD
            "workflow_config": {"name": "test"},
            # Unclassified → treated as WARM
            "custom_user_key": "value",
        }

    def test_first_run_hashes_all_blocks(self, sample_state):
        registry = SubBlockHashRegistry()
        root, block_hashes = registry.compute_incremental_root(
            sample_state, set(sample_state.keys()),
        )
        assert isinstance(root, str)
        assert len(root) == 64
        # Should have hashed at least hot and warm blocks
        assert "hot" in block_hashes
        assert "warm" in block_hashes

    def test_unchanged_blocks_reuse_hash(self, sample_state):
        registry = SubBlockHashRegistry()

        # First run: hash everything
        root1, hashes1 = registry.compute_incremental_root(
            sample_state, set(sample_state.keys()),
        )

        # Second run: only hot field changed
        sample_state["llm_response"] = "Updated response"
        root2, hashes2 = registry.compute_incremental_root(
            sample_state, {"llm_response"},
        )

        # Hot block hash should change
        assert hashes1["hot"] != hashes2["hot"]
        # Warm block hash should be reused (unchanged)
        assert hashes1.get("warm") == hashes2.get("warm")
        # Overall root should differ
        assert root1 != root2

    def test_cold_block_skip(self, sample_state):
        registry = SubBlockHashRegistry()

        # First run
        _, hashes1 = registry.compute_incremental_root(
            sample_state, set(sample_state.keys()),
        )
        cold_hash_1 = hashes1.get("cold")

        # Modify cold field and mark dirty — but cold skip should apply
        sample_state["workflow_config"] = {"name": "modified"}
        _, hashes2 = registry.compute_incremental_root(
            sample_state, {"workflow_config"},
        )

        # Cold block is skipped once initialized (immutable after workflow init)
        assert hashes2.get("cold") == cold_hash_1

    def test_no_dirty_keys_reuses_all(self, sample_state):
        registry = SubBlockHashRegistry()
        root1, _ = registry.compute_incremental_root(
            sample_state, set(sample_state.keys()),
        )
        root2, _ = registry.compute_incremental_root(sample_state, set())
        # No changes → same root
        assert root1 == root2

    def test_parallel_variant_matches_serial(self, sample_state):
        """compute_incremental_root_parallel should produce the same root."""
        serial_reg = SubBlockHashRegistry()
        parallel_reg = SubBlockHashRegistry()

        dirty = set(sample_state.keys())
        root_serial, _ = serial_reg.compute_incremental_root(
            sample_state, dirty,
        )
        root_parallel, _ = parallel_reg.compute_incremental_root_parallel(
            sample_state, dirty, max_workers=2,
        )
        assert root_serial == root_parallel

    def test_unclassified_fields_hashed_with_warm(self, sample_state):
        registry = SubBlockHashRegistry()
        _, hashes = registry.compute_incremental_root(
            sample_state, {"custom_user_key"},
        )
        # Unclassified fields get merged into the warm block
        assert "warm" in hashes


# ── content_hash vs streaming_content_hash ───────────────────────────────────

class TestHashConsistency:
    """
    streaming_content_hash uses a different wire format than content_hash,
    so their digests are intentionally incompatible. Verify they are both
    deterministic but produce different values for the same input.
    """

    def test_different_wire_format(self):
        data = {"key": "value"}
        ch = content_hash(data)
        sh = streaming_content_hash(data)
        assert ch != sh  # Intentionally different wire formats
        assert len(ch) == 64
        assert len(sh) == 64
