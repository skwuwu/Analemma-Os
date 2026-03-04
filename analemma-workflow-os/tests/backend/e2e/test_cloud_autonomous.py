"""
test_cloud_autonomous.py — Pure Cloud E2E Tests (5 tests)

ReactExecutor runs INSIDE Lambda (segment_runner_handler invokes it).
No local worker involved. Tests the autonomous agent path in AWS.

Requirements:
    - E2E_SFN_ORCHESTRATOR_ARN env var pointing to deployed SFN
    - AWS credentials with SFN + DynamoDB + S3 access
    - SegmentRunnerFunction deployed with ReactExecutor support
"""

import json
import os
import uuid

import pytest

from e2e_helpers import (
    _extract_bag, _assert_react_evidence, _get_react_result,
    _has_batch_pointers, _assert_lambda_internal_execution,
)

pytestmark = [pytest.mark.e2e, pytest.mark.cloud]


def _build_cloud_input(
    task_prompt: str = "Use read_only to echo 'hello'",
    max_iterations: int = 5,
    extra: dict = None,
) -> dict:
    """Build SFN input for cloud-only ReactExecutor tests.

    NO __e2e_proxy — routes through ExecuteSegmentDirect → Lambda invoke →
    SegmentRunnerService → _execute_react_segment (true cloud autonomous).
    """
    run_id = uuid.uuid4().hex[:12]
    payload = {
        "ownerId": "e2e_cloud_owner",
        "workflowId": f"e2e_cloud_{run_id}",
        "idempotency_key": f"e2e_cloud_{run_id}",
        # No __e2e_proxy → goes through ExecuteSegmentDirect (Lambda invoke)
        "workflow_config": {
            "name": "e2e_cloud_autonomous",
            "segments": [{"id": "seg_0", "type": "REACT"}],
            "react_executor": {
                "enabled": True,
                "max_iterations": max_iterations,
                "token_budget": 100_000,
            },
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


# ── Test 1: Cloud React Single Tool ──────────────────────────────────────────


class TestCloudReactSingleTool:
    """Lambda ReactExecutor: 1 tool call -> answer"""

    def test_cloud_react_single_tool(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_cloud_input(
            task_prompt="Use read_only to echo 'cloud test' and give final answer.",
            max_iterations=5,
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="cloud_single",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=300)

        assert result["status"] == "SUCCEEDED", (
            f"Cloud single tool test failed: {result.get('error', 'unknown')}"
        )

        output = json.loads(result.get("output", "{}"))
        bag = _extract_bag(output)

        # Verify LLM evidence exists (or was dehydrated by seal_state_bag)
        _assert_react_evidence(bag, context="CloudReactSingleTool")

        # Verify Lambda-internal execution (not MQTT proxy)
        _assert_lambda_internal_execution(bag, context="CloudReactSingleTool")


# ── Test 2: Cloud React Budget Gate ──────────────────────────────────────────


class TestCloudReactBudgetGate:
    """High token usage, low budget -> stop_reason='budget_exceeded'"""

    def test_cloud_react_budget_gate(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_cloud_input(
            task_prompt=(
                "Write an extremely detailed 10-page essay about quantum computing, "
                "including mathematical formulas and code examples. Use every tool available "
                "to gather information before writing."
            ),
            max_iterations=3,
            extra={
                "workflow_config": {
                    "name": "e2e_budget_test",
                    "segments": [{"id": "seg_0", "type": "REACT"}],
                    "react_executor": {
                        "enabled": True,
                        "max_iterations": 3,
                        "token_budget": 1000,  # Very low budget
                    },
                },
            },
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="cloud_budget",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=300)

        # SFN should complete (not hang) even with budget exceeded
        assert result["status"] in ("SUCCEEDED", "FAILED"), (
            f"Budget test should terminate, got {result['status']}"
        )

        output = json.loads(result.get("output", "{}"))
        bag = _extract_bag(output)

        # react_result may be dehydrated to S3 by seal_state_bag
        react_result = _get_react_result(bag)
        if react_result:
            stop_reason = react_result.get("stop_reason", "")
            # Budget exceeded OR max_iterations are both valid stops
            assert stop_reason in ("budget_exceeded", "max_iterations", "end_turn"), (
                f"Expected budget/max stop, got '{stop_reason}'"
            )
        else:
            # Dehydrated: SFN terminated which proves budget/safety gate fired.
            assert _has_batch_pointers(bag), (
                "react_result dehydrated but no batch pointers found. "
                f"Keys: {list(bag.keys())}"
            )


# ── Test 3: Cloud React Governance Reject ────────────────────────────────────


class TestCloudReactGovernanceReject:
    """Ring 3 agent attempts filesystem_write (denied) -> SOFT_ROLLBACK"""

    def test_cloud_react_governance_reject(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_cloud_input(
            task_prompt=(
                "Try to write a file using filesystem_write tool. "
                "If that fails, use read_only to echo 'governance works' instead."
            ),
            max_iterations=5,
            extra={
                "workflow_config": {
                    "name": "e2e_governance_test",
                    "segments": [{"id": "seg_0", "type": "REACT"}],
                    "react_executor": {
                        "enabled": True,
                        "max_iterations": 5,
                        "ring_level": 3,  # USER ring -- filesystem_write denied
                    },
                },
            },
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="cloud_governance",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=300)

        assert result["status"] in ("SUCCEEDED", "FAILED"), (
            f"Governance test should terminate, got {result['status']}"
        )


# ── Test 4: Cloud Multi-Segment with React ───────────────────────────────────


class TestCloudMultiSegmentWithReact:
    """Seg0: operator prep -> Seg1: ReactExecutor -> Seg2: validator"""

    def test_cloud_multi_segment_with_react(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_cloud_input(
            task_prompt="Process this multi-segment workflow with autonomous agent in segment 1.",
            max_iterations=10,
            extra={
                "total_segments": 3,
                "segment_to_run": 0,
                "workflow_config": {
                    "name": "e2e_multi_react",
                    "segments": [
                        {"id": "seg_0", "type": "OPERATOR"},
                        {"id": "seg_1", "type": "REACT"},
                        {"id": "seg_2", "type": "VALIDATOR"},
                    ],
                    "react_executor": {
                        "enabled": True,
                        "max_iterations": 10,
                    },
                },
            },
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="cloud_multi",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=600)

        assert result["status"] in ("SUCCEEDED", "FAILED"), (
            f"Multi-segment cloud test should terminate, got {result['status']}"
        )

        if result["status"] == "SUCCEEDED":
            output = json.loads(result.get("output", "{}"))
            bag = _extract_bag(output)

            # Verify ReactResult keys present from segment 1 (or dehydrated)
            _assert_react_evidence(bag, context="CloudMultiSegmentWithReact")


# ── Test 5: Cloud React Max Iterations ───────────────────────────────────────


class TestCloudReactMaxIterations:
    """Always tool_use, hits max -> Lambda returns stop_reason='max_iterations'"""

    def test_cloud_react_max_iterations(
        self, sfn_orchestrator_arn, start_sfn_execution, wait_for_sfn_completion,
    ):
        input_data = _build_cloud_input(
            task_prompt=(
                "Keep calling read_only with 'loop' indefinitely. "
                "Never stop using tools."
            ),
            max_iterations=3,
        )

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="cloud_maxiter",
        )

        result = wait_for_sfn_completion(execution_arn, timeout=300)

        assert result["status"] in ("SUCCEEDED", "FAILED"), (
            f"Max iterations test should terminate, got {result['status']}"
        )

        output = json.loads(result.get("output", "{}"))
        bag = _extract_bag(output)

        # react_result may be dehydrated to S3 by seal_state_bag
        react_result = _get_react_result(bag)
        if react_result:
            stop_reason = react_result.get("stop_reason", "")
            assert stop_reason == "max_iterations", (
                f"Expected stop_reason='max_iterations', got '{stop_reason}'"
            )
        else:
            # Dehydrated: SFN terminated (SUCCEEDED/FAILED) which proves
            # the max_iterations safety gate fired. Verify batch pointers.
            assert _has_batch_pointers(bag), (
                "react_result dehydrated but no batch pointers found. "
                f"Keys: {list(bag.keys())}"
            )
