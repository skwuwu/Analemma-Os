# -*- coding: utf-8 -*-
"""
Unit tests for kernel_protocol — seal_state_bag / open_state_bag
Lambda <-> ASL communication contract.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'src'))

import pytest
from unittest.mock import patch
from common.kernel_protocol import seal_state_bag, open_state_bag


class TestSealStateBag:
    def test_output_format(self):
        """seal_state_bag returns {state_data: {bag: ...}, next_action: ...}.

        When USC is unavailable, the fallback merges base_state + result_delta
        and wraps them in the standard {state_data, next_action} envelope.
        """
        result = seal_state_bag(
            base_state={"key": "value"},
            result_delta={"status": "SUCCESS"},
            action="sync"
        )
        assert "state_data" in result
        assert "next_action" in result
        assert isinstance(result["state_data"], dict)


class TestOpenStateBag:
    def test_nested_extraction(self):
        """Extracts from event.state_data.bag pattern."""
        event = {"state_data": {"bag": {"key": "value"}}}
        result = open_state_bag(event)
        assert result.get("key") == "value"

    def test_flat_extraction(self):
        """Extracts from event.state_data when no bag wrapper."""
        event = {"state_data": {"key": "value"}}
        result = open_state_bag(event)
        assert "key" in result


class TestSealOpenRoundtrip:
    @patch("common.kernel_protocol._get_universal_sync_core", return_value=None)
    def test_roundtrip(self, mock_usc):
        """Seal then open returns the original data.

        With USC mocked away, seal_state_bag uses the simple-merge fallback:
        merges base_state + result_delta into state_data.
        open_state_bag then extracts state_data (flat path).
        """
        original = {"test_key": "test_value", "number": 42}
        sealed = seal_state_bag(base_state={}, result_delta=original, action="sync")
        opened = open_state_bag(sealed)
        assert opened.get("test_key") == "test_value"
        assert opened.get("number") == 42
