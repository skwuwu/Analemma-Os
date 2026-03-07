# -*- coding: utf-8 -*-
"""
Unit tests for hash_utils — canonical serialization, streaming hashes,
and SubBlockHashRegistry incremental merkle root computation.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

import pytest
from decimal import Decimal
from common.hash_utils import (
    canonical_bytes,
    canonical_json,
    content_hash,
    streaming_content_hash,
    SubBlockHashRegistry,
    HOT_FIELDS,
    WARM_FIELDS,
    COLD_FIELDS,
)
from services.state.state_versioning_service import StateVersioningService


class TestCanonicalJson:
    def test_deterministic(self):
        """Same dict -> same JSON bytes regardless of insertion order."""
        d1 = {"b": 2, "a": 1, "c": 3}
        d2 = {"c": 3, "a": 1, "b": 2}
        assert canonical_json(d1) == canonical_json(d2)

    def test_sorted_keys(self):
        """Keys are alphabetically sorted."""
        result = canonical_json({"z": 1, "a": 2, "m": 3})
        assert result.index('"a"') < result.index('"m"') < result.index('"z"')

    def test_decimal_precision(self):
        """Decimal values use str() for exact precision."""
        result = canonical_json({"val": Decimal("0.1")})
        assert "0.1" in result
        # Should NOT contain float representation artifacts
        assert "0.10000000" not in result

    def test_non_serializable_falls_back_to_repr(self):
        """Objects with __dict__ serialize via filtered __dict__ fallback.

        _default checks hasattr(obj, '__dict__') before raising TypeError,
        so plain objects serialize as their public-attribute dict.
        An object with no public attrs becomes {}.
        """
        class SimpleObj:
            pass

        obj = SimpleObj()
        obj.name = "test"
        obj._private = "hidden"
        result = canonical_json({"obj": obj})
        assert isinstance(result, str)
        # Public attribute included
        assert "test" in result
        # Private attribute filtered out
        assert "_private" not in result
        assert "hidden" not in result


class TestStreamingContentHash:
    def test_deterministic(self):
        """Same dict -> same hash."""
        d = {"key1": "value1", "key2": [1, 2, 3]}
        assert streaming_content_hash(d) == streaming_content_hash(d)

    def test_different_input_different_hash(self):
        """Different dicts produce different hashes."""
        d1 = {"key": "value1"}
        d2 = {"key": "value2"}
        assert streaming_content_hash(d1) != streaming_content_hash(d2)


class TestSubBlockHashRegistry:
    def test_incremental_only_dirty(self):
        """Only dirty blocks are re-hashed; unchanged blocks reuse cached hash."""
        registry = SubBlockHashRegistry()
        state = {
            "llm_response": "hello",            # HOT
            "step_history": [1, 2],              # WARM
            "workflow_config": {"id": "test"},   # COLD
        }
        root1, blocks1 = registry.compute_incremental_root(state, set(state.keys()))

        # Second call with only HOT field dirty
        state["llm_response"] = "changed"
        root2, blocks2 = registry.compute_incremental_root(state, {"llm_response"})

        assert root1 != root2  # Root changed because HOT block changed
        # WARM and COLD block hashes should be reused (unchanged)
        assert blocks1.get("warm") == blocks2.get("warm")
        assert blocks1.get("cold") == blocks2.get("cold")

    def test_field_classification(self):
        """Fields are correctly classified into temperature zones."""
        assert "llm_response" in HOT_FIELDS
        assert "step_history" in WARM_FIELDS
        assert "workflow_config" in COLD_FIELDS


class TestCanonicalBytes:
    def test_public_alias_matches_internal(self):
        """canonical_bytes() is a public alias for _canonical_bytes()."""
        data = {"workflow_id": "wf-123", "version": 5}
        assert canonical_bytes(data) == canonical_json(data).encode("utf-8")


class TestHashMigrationCompatibility:
    """Verify hash_utils and StateVersioningService produce identical output
    for manifest-level data (primitive dicts).

    If these tests FAIL, do NOT unify the implementations — the serialization
    divergence would break existing Merkle DAG roots stored in DynamoDB/S3.
    """

    MANIFEST_SAMPLES = [
        {
            "workflow_id": "wf-test-001",
            "version": 3,
            "config_hash": "abc123def456",
            "segment_hashes": {"seg_0": "hash_a", "seg_1": "hash_b"},
            "parent_hash": "parent_xyz",
        },
        {
            "workflow_id": "wf-test-002",
            "version": 1,
            "config_hash": "deadbeef",
            "segment_hashes": {},
            "parent_hash": "",
        },
        {
            "workflow_id": "wf-unicode-한글",
            "version": 99,
            "config_hash": "utf8test",
            "segment_hashes": {"s0": "h0"},
        },
    ]

    @pytest.mark.parametrize("sample", MANIFEST_SAMPLES)
    def test_canonical_bytes_identical(self, sample):
        """hash_utils.canonical_bytes() == SVS.get_canonical_json() for manifest data."""
        hu_bytes = canonical_bytes(sample)
        svs_bytes = StateVersioningService.get_canonical_json(sample)
        assert hu_bytes == svs_bytes, (
            f"Serialization divergence detected!\n"
            f"hash_utils:  {hu_bytes!r}\n"
            f"SVS:         {svs_bytes!r}"
        )

    @pytest.mark.parametrize("sample", MANIFEST_SAMPLES)
    def test_hash_identical(self, sample):
        """hash_utils.content_hash() == SVS.compute_hash() for manifest data."""
        hu_hash = content_hash(sample)
        svs_hash = StateVersioningService.compute_hash(sample)
        assert hu_hash == svs_hash, (
            f"Hash divergence: hash_utils={hu_hash}, SVS={svs_hash}"
        )
