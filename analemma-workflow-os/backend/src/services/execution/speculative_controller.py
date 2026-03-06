# -*- coding: utf-8 -*-
"""
Speculative Execution Controller — CPU branch-prediction for segment governance.

Like a CPU's branch predictor: if Stage 1 (local heuristic, <5ms) returns PASS,
fire merkle hash storage asynchronously and allow the next segment to start
immediately.  If Stage 2 (LLM verifier, ~500ms) or async hash verification
later fails, SIGKILL the speculative segment and rollback to the previous
merkle node.

Safety constraints (NEVER speculate when):
    - Next segment contains side-effectful nodes (external API, DB write, etc.)
    - HITP edge detected (human approval boundary)
    - REACT segment (autonomous agent — already self-contained)
    - Parallel branch (distributed map context)
    - Another speculative handle is already in-flight (max 1)

Kernel Level: RING_0_KERNEL
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, Optional, Set

logger = logging.getLogger(__name__)


# ── Configuration ───────────────────────────────────────────────────────────

# Threshold raised to 0.75 (from 0.65) per feedback:
# Lower values waste Lambda time + LLM tokens as sunk cost on rollback.
SPECULATIVE_THRESHOLD: float = float(
    os.environ.get("SPECULATIVE_THRESHOLD", "0.75")
)

# Feature flag — disable speculation entirely if needed
ENABLE_SPECULATION: bool = (
    os.environ.get("ENABLE_SPECULATION", "true").lower() == "true"
)

# ── [v3.33] Default-Deny Speculation Model ──────────────────────────────
# Previous model (v3.32): Default-Allow — any node NOT in a blocklist was
# assumed pure.  This is unsafe because a new node type (e.g. custom_transform
# that internally calls an external API) would silently be speculated.
#
# New model (v3.33): Default-Deny — ONLY node types in SPECULATABLE_NODE_TYPES
# are eligible for speculation.  All unknown/unclassified types are treated as
# side-effectful.  Workflow authors can opt-in individual nodes via the
# `speculatable: true` marker.
#
# Node types proven to be pure (no external I/O, deterministic output):
SPECULATABLE_NODE_TYPES: FrozenSet[str] = frozenset({
    "transform",        # Pure data transformation
    "conditional",      # Branch logic (if/else)
    "validator",        # Schema / rule validation
    "mapper",           # Data field mapping
    "aggregator",       # Data aggregation (sum, count, merge)
    "filter",           # Data filtering
    "formatter",        # String / data formatting
    "calculator",       # Arithmetic / expression evaluation
    "router",           # Routing decision (no I/O)
    "passthrough",      # Identity / no-op node
})

# Node types that are NEVER speculatable regardless of markers.
# This is a safety net — even if someone sets `speculatable: true`, these are blocked.
NEVER_SPECULATE_NODE_TYPES: FrozenSet[str] = frozenset({
    "webhook",
    "email",
    "slack",
    "database_write",
    "http_post",
    "sns_publish",
    "sqs_send",
    "s3_write",
    "dynamodb_write",
    "aiModel",          # Bedrock / external LLM API call
    "http_get",         # External HTTP read (non-deterministic)
    "lambda_invoke",    # Arbitrary Lambda execution
})

# Per-node marker keys — used for opt-in (`speculatable: true`)
# and forced opt-out (`side_effect: true`, `external_api: true`)
SIDE_EFFECT_MARKERS = ("side_effect", "external_api", "has_side_effects")
SPECULATE_OPT_IN_MARKER = "speculatable"


# ── Data Structures ─────────────────────────────────────────────────────────

class SpeculativeStatus(Enum):
    """Lifecycle states of a speculative handle."""
    PENDING = "pending"          # Background verification in progress
    COMMITTED = "committed"      # Verification passed — state is stable
    ABORTED = "aborted"          # Verification failed — rollback required


@dataclass
class RollbackTarget:
    """Information needed to rollback a failed speculative segment."""
    segment_id: int
    parent_manifest_id: str
    abort_reason: str
    abort_details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SpeculativeHandle:
    """Handle representing one in-flight speculative execution.

    Lifecycle:
        begin_speculative() → PENDING
        verify_background() runs in thread:
            success → commit() → COMMITTED
            failure → rollback() → ABORTED
        check_abort() called at next segment entry:
            ABORTED → return RollbackTarget
            COMMITTED → return None (proceed)
            PENDING → block until resolved (with timeout)
    """
    segment_id: int
    state_snapshot: Dict[str, Any]
    merkle_parent_hash: str
    status: SpeculativeStatus = SpeculativeStatus.PENDING
    abort_reason: str = ""
    abort_details: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    # Threading primitives
    _event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait_for_resolution(self, timeout: float = 30.0) -> bool:
        """Block until verification completes. Returns True if resolved."""
        return self._event.wait(timeout=timeout)

    def resolve(self, status: SpeculativeStatus, reason: str = "",
                details: Optional[Dict[str, Any]] = None) -> None:
        """Set final status and unblock waiters."""
        with self._lock:
            self.status = status
            self.abort_reason = reason
            self.abort_details = details or {}
        self._event.set()


# ── Controller ──────────────────────────────────────────────────────────────

class SpeculativeExecutionController:
    """Controls speculative segment execution with background verification.

    Usage in segment_runner_service.py (post-seal block):

        controller = SpeculativeExecutionController()

        if controller.should_speculate(stage1_result, next_segment_config):
            handle = controller.begin_speculative(
                segment_id=current_segment_id,
                state_snapshot=sealed_state,
                merkle_parent_hash=current_manifest_id,
            )
            controller.verify_background(
                handle,
                stage2_callable=lambda: quality_gate.evaluate(output_text),
                merkle_hash_callable=lambda: versioning_service.save_state_delta(...),
            )
            return sealed_result  # Proceed immediately

    At next segment entry:

        rollback = controller.check_abort(handle)
        if rollback:
            return {"_kernel_rollback_to_manifest": rollback.parent_manifest_id}
    """

    def __init__(self) -> None:
        self._active_handle: Optional[SpeculativeHandle] = None
        self._lock = threading.Lock()
        self._stats = {
            "total_speculations": 0,
            "committed": 0,
            "aborted": 0,
            "skipped_side_effects": 0,
            "skipped_threshold": 0,
        }

    # ── Decision ────────────────────────────────────────────────────────

    def should_speculate(
        self,
        stage1_combined_score: float,
        stage1_verdict: str,
        next_segment_config: Optional[Dict[str, Any]] = None,
        is_hitp_edge: bool = False,
        is_react: bool = False,
        is_parallel_branch: bool = False,
    ) -> bool:
        """Determine if speculative execution is safe and beneficial.

        Returns True only when ALL conditions are met:
        1. Feature enabled
        2. Stage 1 quality score >= SPECULATIVE_THRESHOLD (0.75)
        3. Stage 1 verdict is not FAIL
        4. ALL nodes in next segment are in SPECULATABLE_NODE_TYPES or
           have explicit `speculatable: true` marker (Default-Deny)
        5. Not HITP edge / REACT / parallel branch
        6. No other speculative handle in-flight
        """
        if not ENABLE_SPECULATION:
            return False

        if stage1_verdict == "FAIL":
            self._stats["skipped_threshold"] += 1
            return False

        if stage1_combined_score < SPECULATIVE_THRESHOLD:
            self._stats["skipped_threshold"] += 1
            return False

        if is_hitp_edge or is_react or is_parallel_branch:
            return False

        if next_segment_config and not self._is_speculatable(next_segment_config):
            self._stats["skipped_side_effects"] += 1
            logger.info(
                "[Speculative] Skipped — next segment contains non-speculatable nodes"
            )
            return False

        with self._lock:
            if self._active_handle is not None:
                # Max 1 speculative segment in-flight
                return False

        return True

    # ── Lifecycle ───────────────────────────────────────────────────────

    def begin_speculative(
        self,
        segment_id: int,
        state_snapshot: Dict[str, Any],
        merkle_parent_hash: str,
    ) -> SpeculativeHandle:
        """Create a speculative handle and register it as active."""
        handle = SpeculativeHandle(
            segment_id=segment_id,
            state_snapshot=state_snapshot,
            merkle_parent_hash=merkle_parent_hash,
        )
        with self._lock:
            self._active_handle = handle
            self._stats["total_speculations"] += 1
        logger.info(
            "[Speculative] BEGIN segment=%d parent_manifest=%s threshold=%.2f",
            segment_id, merkle_parent_hash[:12] if merkle_parent_hash else "ROOT",
            SPECULATIVE_THRESHOLD,
        )
        return handle

    def verify_background(
        self,
        handle: SpeculativeHandle,
        stage2_callable: Optional[Callable[[], Any]] = None,
        merkle_hash_callable: Optional[Callable[[], None]] = None,
    ) -> None:
        """Launch background verification thread.

        Runs Stage 2 LLM verification and/or merkle hash computation
        asynchronously.  On failure, sets handle to ABORTED.

        Args:
            handle: The speculative handle to verify.
            stage2_callable: Zero-arg callable returning a QualityGateResult
                (or similar object with .final_verdict attribute).
            merkle_hash_callable: Zero-arg callable that performs merkle hash
                computation as a side-effect (return value ignored).
        """
        def _verify() -> None:
            try:
                # 1. Merkle hash computation (if provided)
                if merkle_hash_callable:
                    merkle_hash_callable()

                # 2. Stage 2 LLM verification (if provided)
                if stage2_callable:
                    result = stage2_callable()
                    # QualityGateResult or similar — check for failure
                    if hasattr(result, 'final_verdict'):
                        verdict = result.final_verdict
                        if hasattr(verdict, 'value'):
                            verdict = verdict.value
                        if verdict in ("FAIL", "REJECT"):
                            handle.resolve(
                                SpeculativeStatus.ABORTED,
                                reason=f"Stage 2 verification failed: {verdict}",
                                details={"verdict": verdict},
                            )
                            self._stats["aborted"] += 1
                            logger.warning(
                                "[Speculative] ABORT segment=%d reason=stage2_fail verdict=%s",
                                handle.segment_id, verdict,
                            )
                            return

                # All checks passed
                handle.resolve(SpeculativeStatus.COMMITTED)
                self._stats["committed"] += 1
                logger.info(
                    "[Speculative] COMMIT segment=%d elapsed=%.0fms",
                    handle.segment_id,
                    (time.time() - handle.created_at) * 1000,
                )

            except Exception as exc:
                handle.resolve(
                    SpeculativeStatus.ABORTED,
                    reason=f"Background verification error: {exc}",
                    details={"exception": str(exc)},
                )
                self._stats["aborted"] += 1
                logger.error(
                    "[Speculative] ABORT segment=%d reason=exception error=%s",
                    handle.segment_id, exc, exc_info=True,
                )

        thread = threading.Thread(
            target=_verify,
            name=f"speculative-verify-seg{handle.segment_id}",
            daemon=True,
        )
        thread.start()

    def check_abort(
        self,
        handle: Optional[SpeculativeHandle] = None,
        timeout: float = 30.0,
    ) -> Optional[RollbackTarget]:
        """Check if the speculative execution should be aborted.

        Called at the entry of the next segment. Blocks until the
        background verification completes (with timeout).

        Returns:
            RollbackTarget if verification failed, None if committed.
        """
        with self._lock:
            if handle is None:
                handle = self._active_handle
            if handle is None:
                return None

        # Wait for background verification to complete
        resolved = handle.wait_for_resolution(timeout=timeout)
        if not resolved:
            # Timeout — treat as abort (conservative)
            logger.error(
                "[Speculative] TIMEOUT segment=%d after %.0fs — treating as abort",
                handle.segment_id, timeout,
            )
            handle.resolve(
                SpeculativeStatus.ABORTED,
                reason="Background verification timed out",
            )
            self._stats["aborted"] += 1

        # Clear active handle
        with self._lock:
            if self._active_handle is handle:
                self._active_handle = None

        if handle.status == SpeculativeStatus.ABORTED:
            return RollbackTarget(
                segment_id=handle.segment_id,
                parent_manifest_id=handle.merkle_parent_hash,
                abort_reason=handle.abort_reason,
                abort_details=handle.abort_details,
            )

        return None

    def commit(self, handle: SpeculativeHandle) -> None:
        """Explicitly commit a speculative handle."""
        handle.resolve(SpeculativeStatus.COMMITTED)
        with self._lock:
            if self._active_handle is handle:
                self._active_handle = None
        self._stats["committed"] += 1

    def get_stats(self) -> Dict[str, Any]:
        """Return speculation statistics for monitoring."""
        return dict(self._stats)

    # ── Speculatability Check (Default-Deny) ────────────────────────────

    @staticmethod
    def _is_speculatable(segment_config: Dict[str, Any]) -> bool:
        """Check if ALL nodes in a segment are safe for speculative execution.

        [v3.33] Default-Deny model: returns True only when EVERY node is
        proven safe.  A single unverified node blocks the entire segment.

        A node is speculatable when:
        - Its type is in SPECULATABLE_NODE_TYPES, OR
        - It has `speculatable: true` marker (explicit opt-in)
        AND it does NOT:
        - Have type in NEVER_SPECULATE_NODE_TYPES (hard block)
        - Have any SIDE_EFFECT_MARKERS set to True (forced opt-out)
        - Have segment-level `has_side_effects: true`
        """
        # Segment-level forced opt-out
        if segment_config.get("has_side_effects") is True:
            return False

        nodes = segment_config.get("nodes", [])
        # Support both list-of-dicts and dict-of-dicts node formats
        if isinstance(nodes, dict):
            node_list = list(nodes.values())
        elif isinstance(nodes, list):
            node_list = nodes
        else:
            return False

        if not node_list:
            return False  # Empty segment — conservative deny

        for node in node_list:
            if not isinstance(node, dict):
                return False  # Unparseable node — deny

            node_type = node.get("type", "").lower()

            # Hard block: never-speculate types override everything
            if node_type in NEVER_SPECULATE_NODE_TYPES:
                return False

            # Forced opt-out: side-effect markers
            for marker in SIDE_EFFECT_MARKERS:
                if node.get(marker) is True:
                    return False

            # Check if node is in the proven-pure allowlist
            if node_type in SPECULATABLE_NODE_TYPES:
                continue

            # Not in allowlist — check explicit opt-in marker
            if node.get(SPECULATE_OPT_IN_MARKER) is True:
                continue

            # Unknown node type without opt-in — DEFAULT DENY
            logger.debug(
                "[Speculative] Node type '%s' is not in SPECULATABLE_NODE_TYPES "
                "and lacks 'speculatable: true' marker — blocking speculation",
                node_type,
            )
            return False

        return True
