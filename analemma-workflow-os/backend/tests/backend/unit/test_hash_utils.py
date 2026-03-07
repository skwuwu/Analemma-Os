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
    canonical_json,
    streaming_content_hash,
    SubBlockHashRegistry,
    HOT_FIELDS,
    WARM_FIELDS,
    COLD_FIELDS,
)


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
