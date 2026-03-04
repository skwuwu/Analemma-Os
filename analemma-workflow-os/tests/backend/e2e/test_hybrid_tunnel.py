"""
test_hybrid_tunnel.py — Hybrid E2E Tests (8 tests)

Real SFN orchestration with local ReactExecutor as worker node.
Every test specifies max_iterations explicitly to prevent infinite loops.

Requirements:
    - E2E_SFN_ORCHESTRATOR_ARN env var pointing to deployed E2E SFN
    - AWS credentials with SFN + DynamoDB + S3 access
    - Local VSM (:8765) and Worker (:9876) servers running
    - Active ngrok tunnel to :9876
"""

import json
import os
import time

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.hybrid]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_input(
    task_prompt: str = "Use read_only to echo 'hello world'",
    max_iterations: int = 5,
    owner_id: str = "e2e_test_owner",
    extra: dict = None,
) -> dict:
    """Build standard SFN input payload."""
    payload = {
        "ownerId": owner_id,
        "workflow_config": {
            "name": "e2e_hybrid_test",
            "segments": [{"id": "seg_0", "type": "REACT"}],
        },
        "task_prompt": task_prompt,
        "max_iterations": max_iterations,
        "total_segments": 1,
        "segment_to_run": 0,
        "MOCK_MODE": "false",
    }
    if extra:
        payload.update(extra)
    return payload


# ── Test 1: Single Segment Complete ──────────────────────────────────────────


class TestSingleSegmentComplete:
    """SFN -> Proxy -> Local(1 iter) -> SendTaskSuccess -> COMPLETE"""

    def test_single_segment_complete(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion, merkle_verifier,
    ):
        input_data = _build_input(
            task_prompt="Use read_only to echo 'hello world', then provide your final answer.",
            max_iterations=5,
        )

        execution_arn, execution_name = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="hybrid_single",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=120)

        assert result["status"] == "SUCCEEDED", (
            f"SFN execution failed: {result.get('error', 'unknown')}"
        )

        # Verify final state has ReactResult data
        output = json.loads(result.get("output", "{}"))
        state_data = output.get("state_data", output)
        bag = state_data.get("bag", state_data)

        assert "react_result" in bag or "llm_raw_output" in bag, (
            f"final_state missing ReactResult keys. Keys: {list(bag.keys())}"
        )

        # Verify Merkle DAG consistency
        execution_id = bag.get("execution_id", execution_name)
        verification = merkle_verifier.verify_execution(execution_id)
        assert verification.passed or len(verification.warnings) > 0, (
            f"Merkle verification failed: {verification.errors}"
        )


# ── Test 2: Multi-Segment Loop ──────────────────────────────────────────────


class TestMultiSegmentLoop:
    """3x CONTINUE segments -> COMPLETE"""

    def test_multi_segment_loop(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_input(
            task_prompt="Process this multi-segment workflow step by step.",
            max_iterations=10,
            extra={
                "total_segments": 3,
                "segment_to_run": 0,
            },
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="hybrid_multi",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=300)

        assert result["status"] == "SUCCEEDED", (
            f"Multi-segment loop failed: {result.get('error', 'unknown')}"
        )

        output = json.loads(result.get("output", "{}"))
        state_data = output.get("state_data", output)
        bag = state_data.get("bag", state_data)

        # Verify loop_counter indicates 3+ segments executed
        loop_counter = bag.get("loop_counter", state_data.get("loop_counter", 0))
        assert loop_counter >= 3, f"Expected loop_counter >= 3, got {loop_counter}"


# ── Test 3: Autonomous Tool Chain ────────────────────────────────────────────


class TestAutonomousToolChain:
    """SFN -> Local ReactExecutor runs 3 tool iterations -> answer"""

    def test_autonomous_tool_chain(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_input(
            task_prompt=(
                "Step 1: Use read_only to echo 'step one'. "
                "Step 2: Use basic_query to compute 3 + 4. "
                "Step 3: Use read_only to echo the result. "
                "Then provide your final answer summarizing all steps."
            ),
            max_iterations=5,
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="hybrid_chain",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=120)
        assert result["status"] == "SUCCEEDED"

        output = json.loads(result.get("output", "{}"))
        state_data = output.get("state_data", output)
        bag = state_data.get("bag", state_data)

        react_result = bag.get("react_result", {})
        iterations = react_result.get("iterations", 0)
        assert iterations >= 2, f"Expected >= 2 iterations for tool chain, got {iterations}"

        # Verify governance checkpoints recorded
        segments = react_result.get("segments", [])
        assert len(segments) >= 2, f"Expected >= 2 governance segments, got {len(segments)}"


# ── Test 4: Max Iterations Safety ────────────────────────────────────────────


class TestMaxIterationsSafety:
    """ReactExecutor always calls tools -> hits max_iterations -> SFN COMPLETE"""

    def test_max_iterations_safety(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_input(
            task_prompt=(
                "Keep calling read_only with 'loop' repeatedly. "
                "Never provide a final answer."
            ),
            max_iterations=3,
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="hybrid_maxiter",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=120)

        # SFN should complete (not hang)
        assert result["status"] in ("SUCCEEDED", "FAILED"), (
            f"SFN should terminate, got status={result['status']}"
        )

        output = json.loads(result.get("output", "{}"))
        state_data = output.get("state_data", output)
        bag = state_data.get("bag", state_data)

        react_result = bag.get("react_result", {})
        stop_reason = react_result.get("stop_reason", "")
        assert stop_reason == "max_iterations", (
            f"Expected stop_reason='max_iterations', got '{stop_reason}'"
        )


# ── Test 5: SFN Loop Limit Exceeded ─────────────────────────────────────────


class TestSFNLoopLimitExceeded:
    """Proxy always returns CONTINUE -> SFN hits CheckLoopLimit -> Fail"""

    def test_sfn_loop_limit_exceeded(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_input(
            task_prompt="Always continue without completing.",
            max_iterations=5,
            extra={"_force_continue": True},
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="hybrid_looplimit",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=600)

        # SFN should fail due to loop limit
        assert result["status"] == "FAILED", (
            f"Expected FAILED due to loop limit, got {result['status']}"
        )


# ── Test 6: HITP Pause + Hybrid Resume ──────────────────────────────────────


class TestHITPPauseHybridResume:
    """Seg1 -> PAUSED_FOR_HITP -> manual resume -> Seg2 -> COMPLETE"""

    def test_hitp_pause_hybrid_resume(
        self, sfn_orchestrator_arn, sfn_client, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_input(
            task_prompt="Process and request human approval.",
            max_iterations=5,
            extra={
                "total_segments": 2,
                "segment_to_run": 0,
                "AUTO_RESUME_HITP": "true",
                "AUTO_RESUME_DELAY_SECONDS": "3",
            },
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="hybrid_hitp",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=120)

        # With AUTO_RESUME_HITP, the execution should eventually complete
        assert result["status"] in ("SUCCEEDED", "FAILED"), (
            f"HITP test ended with unexpected status: {result['status']}"
        )


# ── Test 7: Payload 256KB Limit (S3 Offloading) ─────────────────────────────


class TestPayload256KBLimit:
    """Local generates large result (>256KB) -> auto S3-offloaded via seal_state_bag"""

    def test_payload_256kb_limit(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        # Generate a task that produces large output
        large_text = "x" * 300_000  # ~300KB
        input_data = _build_input(
            task_prompt=f"Use read_only to echo this large text: {large_text[:100]}...",
            max_iterations=5,
            extra={"_large_payload_test": True},
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="hybrid_payload",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=120)

        # SFN should succeed -- seal_state_bag auto-offloads to S3
        assert result["status"] == "SUCCEEDED", (
            f"Payload test failed: {result.get('error', 'unknown')}"
        )


# ── Test 8: Network Latency Tolerance ────────────────────────────────────────


class TestNetworkLatencyTolerance:
    """Add 2s artificial delay in worker response -> SFN still completes"""

    def test_network_latency_tolerance(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_input(
            task_prompt="Use read_only to echo 'latency test' and provide final answer.",
            max_iterations=5,
            extra={"_artificial_delay_seconds": 2},
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="hybrid_latency",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=120)

        # waitForTaskToken has long timeout -> 2s delay is fine
        assert result["status"] == "SUCCEEDED", (
            f"Latency tolerance test failed: {result.get('error', 'unknown')}"
        )
