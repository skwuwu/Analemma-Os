# -*- coding: utf-8 -*-
"""Unit tests for OptimisticVerifier (Phase 4)."""

import os
import time

import pytest

from src.services.execution.optimistic_verifier import (
    MIN_MEMORY_FOR_PARALLEL_MB,
    OptimisticVerifier,
    TrustChainResult,
    VerificationFailedError,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def verifier():
    """Verifier with parallel enabled (default 2048MB)."""
    os.environ["AWS_LAMBDA_FUNCTION_MEMORY_SIZE"] = "2048"
    v = OptimisticVerifier()
    yield v
    os.environ.pop("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", None)


@pytest.fixture
def sequential_verifier():
    """Verifier forced into sequential mode (low memory)."""
    os.environ["AWS_LAMBDA_FUNCTION_MEMORY_SIZE"] = "256"
    v = OptimisticVerifier()
    yield v
    os.environ.pop("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", None)


def _trust_pass():
    return TrustChainResult(is_valid=True, manifest_hash="aaa", expected_hash="aaa")


def _trust_fail():
    return TrustChainResult(
        is_valid=False,
        manifest_hash="aaa",
        expected_hash="bbb",
        elapsed_ms=2.0,
    )


def _execute_ok():
    return {"result": "success", "tokens": 100}


def _execute_slow():
    time.sleep(0.1)
    return {"result": "slow_but_ok"}


# ── Parallel Mode ────────────────────────────────────────────────────────────

class TestParallelVerification:

    def test_pass_returns_execution_result(self, verifier):
        result = verifier.verify_and_execute(_trust_pass, _execute_ok)
        assert result == {"result": "success", "tokens": 100}

    def test_fail_raises_verification_error(self, verifier):
        with pytest.raises(VerificationFailedError) as exc_info:
            verifier.verify_and_execute(_trust_fail, _execute_ok)
        assert exc_info.value.result.is_valid is False

    def test_pass_with_slow_execution(self, verifier):
        result = verifier.verify_and_execute(_trust_pass, _execute_slow)
        assert result["result"] == "slow_but_ok"

    def test_verify_exception_propagates(self, verifier):
        def _verify_crash():
            raise RuntimeError("hash computation error")

        with pytest.raises(RuntimeError, match="hash computation"):
            verifier.verify_and_execute(_verify_crash, _execute_ok)


# ── Sequential Mode (Low Memory) ────────────────────────────────────────────

class TestSequentialFallback:

    def test_sequential_pass(self, sequential_verifier):
        result = sequential_verifier.verify_and_execute(_trust_pass, _execute_ok)
        assert result == {"result": "success", "tokens": 100}

    def test_sequential_fail(self, sequential_verifier):
        with pytest.raises(VerificationFailedError):
            sequential_verifier.verify_and_execute(_trust_fail, _execute_ok)

    def test_sequential_skips_execution_on_fail(self, sequential_verifier):
        """In sequential mode, execution is never called if verify fails."""
        call_log = []

        def _tracked_execute():
            call_log.append("executed")
            return {}

        with pytest.raises(VerificationFailedError):
            sequential_verifier.verify_and_execute(_trust_fail, _tracked_execute)

        assert len(call_log) == 0, "Execution should not run after verification failure"


# ── Memory Guard ─────────────────────────────────────────────────────────────

class TestMemoryGuard:

    def test_high_memory_enables_parallel(self):
        os.environ["AWS_LAMBDA_FUNCTION_MEMORY_SIZE"] = "2048"
        v = OptimisticVerifier()
        assert v._can_parallelize is True
        os.environ.pop("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", None)

    def test_low_memory_disables_parallel(self):
        os.environ["AWS_LAMBDA_FUNCTION_MEMORY_SIZE"] = "512"
        v = OptimisticVerifier()
        assert v._can_parallelize is False
        os.environ.pop("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", None)

    def test_threshold_boundary(self):
        os.environ["AWS_LAMBDA_FUNCTION_MEMORY_SIZE"] = str(MIN_MEMORY_FOR_PARALLEL_MB)
        v = OptimisticVerifier()
        assert v._can_parallelize is True
        os.environ.pop("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", None)

    def test_below_threshold_boundary(self):
        os.environ["AWS_LAMBDA_FUNCTION_MEMORY_SIZE"] = str(MIN_MEMORY_FOR_PARALLEL_MB - 1)
        v = OptimisticVerifier()
        assert v._can_parallelize is False
        os.environ.pop("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", None)

    def test_missing_env_var_assumes_sufficient(self):
        os.environ.pop("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", None)
        v = OptimisticVerifier()
        assert v._can_parallelize is True


# ── Stats Tracking ───────────────────────────────────────────────────────────

class TestStats:

    def test_stats_tracking(self, verifier):
        verifier.verify_and_execute(_trust_pass, _execute_ok)
        stats = verifier.get_stats()
        assert stats["total_calls"] == 1
        assert stats["parallel_executions"] == 1

    def test_failure_tracked(self, verifier):
        with pytest.raises(VerificationFailedError):
            verifier.verify_and_execute(_trust_fail, _execute_ok)
        stats = verifier.get_stats()
        assert stats["verification_failures"] == 1
        assert stats["early_exits"] == 1


# ── Feature Flag ─────────────────────────────────────────────────────────────

class TestFeatureFlag:

    def test_disabled_flag_forces_sequential(self):
        os.environ["ENABLE_OPTIMISTIC_VERIFICATION"] = "false"
        os.environ["AWS_LAMBDA_FUNCTION_MEMORY_SIZE"] = "2048"

        # Need to reimport to pick up the env var at module level
        import importlib
        import src.services.execution.optimistic_verifier as mod
        importlib.reload(mod)

        v = mod.OptimisticVerifier()
        result = v.verify_and_execute(_trust_pass, _execute_ok)
        assert result == {"result": "success", "tokens": 100}
        stats = v.get_stats()
        assert stats["sequential_fallbacks"] == 1

        # Restore
        os.environ["ENABLE_OPTIMISTIC_VERIFICATION"] = "true"
        importlib.reload(mod)
        os.environ.pop("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", None)


# ── TrustChainResult ─────────────────────────────────────────────────────────

class TestTrustChainResult:

    def test_default_details_not_none(self):
        r = TrustChainResult(is_valid=True)
        assert r.details is not None
        assert isinstance(r.details, dict)

    def test_verification_failed_error_message(self):
        r = TrustChainResult(
            is_valid=False,
            manifest_hash="a" * 32,
            expected_hash="b" * 32,
        )
        err = VerificationFailedError(r)
        assert "aaaaaaaaaaaaaaaa" in str(err)
        assert "bbbbbbbbbbbbbbbb" in str(err)
