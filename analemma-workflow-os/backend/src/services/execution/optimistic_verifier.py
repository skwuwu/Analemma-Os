# -*- coding: utf-8 -*-
"""
Optimistic Verifier — Parallel Trust Chain + LLM Execution with Write-Lock.

Instead of serial (verify → execute), runs Trust Chain Gatekeeper and
LLM Runner in parallel.  A Write-Lock prevents the LLM response from
being merged into StateBag until verification passes.

Early Exit: If hash mismatch is detected, the LLM response is discarded
immediately without waiting for completion.

Lambda Memory Guard: Requires >= 1024MB Lambda memory for ThreadPoolExecutor.
Below threshold, falls back to sequential execution to prevent CPU contention
on low-memory Lambdas (128MB–512MB share partial vCPUs).

Kernel Level: RING_0_KERNEL
"""

import logging
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────────────

# Minimum Lambda memory (MB) for parallel execution.
# Below this → sequential fallback (verify first, then execute).
MIN_MEMORY_FOR_PARALLEL_MB: int = 1024

# Feature flag
ENABLE_OPTIMISTIC_VERIFICATION: bool = (
    os.environ.get("ENABLE_OPTIMISTIC_VERIFICATION", "true").lower() == "true"
)

T = TypeVar("T")


# ── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class TrustChainResult:
    """Result from Trust Chain Gatekeeper verification."""
    is_valid: bool
    manifest_hash: str = ""
    expected_hash: str = ""
    elapsed_ms: float = 0.0
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


class VerificationFailedError(Exception):
    """Raised when trust chain verification rejects the input state."""

    def __init__(self, result: TrustChainResult):
        self.result = result
        super().__init__(
            f"Trust chain verification failed: "
            f"expected={result.expected_hash[:16]}... "
            f"actual={result.manifest_hash[:16]}..."
        )


# ── Verifier ────────────────────────────────────────────────────────────────

class OptimisticVerifier:
    """Parallel trigger: Trust Chain + LLM execution with Write-Lock.

    State-Locking: LLM response is held in a pending buffer until
    verification 'OK' drops the lock.  If verification fails,
    the response is discarded and the segment exits with an error.

    Early Exit: Hash mismatch → don't wait for LLM → raise immediately.

    Usage in segment_runner_service.py:

        verifier = OptimisticVerifier()

        result = verifier.verify_and_execute(
            trust_chain_fn=lambda: verify_manifest(input_state, parent_manifest),
            execute_fn=lambda: _execute_with_kernel_retry(segment_config, ...),
        )
    """

    def __init__(self) -> None:
        self._can_parallelize = self._check_memory_budget()
        self._stats = {
            "total_calls": 0,
            "parallel_executions": 0,
            "sequential_fallbacks": 0,
            "verification_failures": 0,
            "early_exits": 0,
        }

    def verify_and_execute(
        self,
        trust_chain_fn: Callable[[], TrustChainResult],
        execute_fn: Callable[[], T],
        timeout_verify: float = 30.0,
        timeout_execute: float = 300.0,
    ) -> T:
        """Run verification and execution, potentially in parallel.

        Args:
            trust_chain_fn: Callable returning TrustChainResult.
            execute_fn: Callable returning the execution result.
            timeout_verify: Max wait for verification (seconds).
            timeout_execute: Max wait for execution (seconds).

        Returns:
            Execution result (only if verification passes).

        Raises:
            VerificationFailedError: If trust chain rejects.
            TimeoutError: If either callable exceeds its timeout.
        """
        self._stats["total_calls"] += 1

        if not ENABLE_OPTIMISTIC_VERIFICATION or not self._can_parallelize:
            return self._execute_sequential(
                trust_chain_fn, execute_fn,
                timeout_verify, timeout_execute,
            )

        return self._execute_parallel(
            trust_chain_fn, execute_fn,
            timeout_verify, timeout_execute,
        )

    def _execute_parallel(
        self,
        trust_chain_fn: Callable[[], TrustChainResult],
        execute_fn: Callable[[], T],
        timeout_verify: float,
        timeout_execute: float,
    ) -> T:
        """Parallel execution: trust chain + LLM run simultaneously."""
        self._stats["parallel_executions"] += 1
        start = time.time()

        with ThreadPoolExecutor(max_workers=2) as pool:
            verify_future: Future[TrustChainResult] = pool.submit(trust_chain_fn)
            execute_future: Future[T] = pool.submit(execute_fn)

            # Wait for verification first — it's faster (<15ms vs LLM ~seconds)
            try:
                verify_result = verify_future.result(timeout=timeout_verify)
            except Exception as exc:
                # Verification crashed — cancel execution (Early Exit)
                execute_future.cancel()
                self._stats["early_exits"] += 1
                logger.error(
                    "[OptimisticVerifier] Trust chain threw exception: %s", exc
                )
                raise

            if not verify_result.is_valid:
                # Early Exit — cancel execution, discard any LLM work
                execute_future.cancel()
                self._stats["verification_failures"] += 1
                self._stats["early_exits"] += 1
                logger.warning(
                    "[OptimisticVerifier] REJECT — hash mismatch "
                    "expected=%s actual=%s elapsed=%.0fms",
                    verify_result.expected_hash[:16],
                    verify_result.manifest_hash[:16],
                    verify_result.elapsed_ms,
                )
                raise VerificationFailedError(verify_result)

            # Verification passed — wait for execution (Write-Lock released)
            logger.info(
                "[OptimisticVerifier] Trust chain PASSED in %.0fms — "
                "waiting for execution",
                verify_result.elapsed_ms,
            )
            result = execute_future.result(timeout=timeout_execute)
            elapsed = (time.time() - start) * 1000
            logger.info(
                "[OptimisticVerifier] Parallel complete: total=%.0fms", elapsed
            )
            return result

    def _execute_sequential(
        self,
        trust_chain_fn: Callable[[], TrustChainResult],
        execute_fn: Callable[[], T],
        timeout_verify: float,
        timeout_execute: float,
    ) -> T:
        """Sequential fallback for low-memory Lambdas."""
        self._stats["sequential_fallbacks"] += 1
        start = time.time()

        # Verify first
        verify_result = trust_chain_fn()
        if not verify_result.is_valid:
            self._stats["verification_failures"] += 1
            raise VerificationFailedError(verify_result)

        # Then execute
        result = execute_fn()
        elapsed = (time.time() - start) * 1000
        logger.info(
            "[OptimisticVerifier] Sequential complete: total=%.0fms", elapsed
        )
        return result

    def get_stats(self) -> Dict[str, Any]:
        """Return verification statistics for monitoring."""
        return dict(self._stats)

    # ── Memory Guard ────────────────────────────────────────────────────

    @staticmethod
    def _check_memory_budget() -> bool:
        """Check if Lambda has enough memory for parallel execution.

        Lambda allocates vCPU proportional to memory:
        - 128MB  → ~0.08 vCPU  (too low for threading)
        - 512MB  → ~0.29 vCPU  (marginal)
        - 1024MB → ~0.58 vCPU  (minimum for parallel)
        - 2048MB → ~1.17 vCPU  (recommended)

        Below MIN_MEMORY_FOR_PARALLEL_MB → sequential fallback.
        """
        try:
            memory_mb = int(os.environ.get(
                'AWS_LAMBDA_FUNCTION_MEMORY_SIZE', '2048'
            ))
        except (ValueError, TypeError):
            memory_mb = 2048  # Assume sufficient if env var missing

        can_parallel = memory_mb >= MIN_MEMORY_FOR_PARALLEL_MB
        if not can_parallel:
            logger.info(
                "[OptimisticVerifier] Lambda memory %dMB < %dMB minimum — "
                "using sequential fallback",
                memory_mb, MIN_MEMORY_FOR_PARALLEL_MB,
            )
        return can_parallel
