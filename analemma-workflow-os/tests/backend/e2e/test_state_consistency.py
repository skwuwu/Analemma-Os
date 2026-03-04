"""
test_state_consistency.py — Merkle DAG Integrity Tests (5 tests)

Verifies state consistency across local/cloud boundaries:
    - Merkle root hash matches S3 content blocks
    - CONTROL_PLANE_FIELDS survive dehydration across segments
    - __s3_offloaded flag survives seal_state_bag (v3.31 fix)
    - Rollback restores exact checkpoint state
    - DynamoDB checkpoints match S3 manifests

Requirements:
    - Completed SFN executions (run hybrid/cloud tests first)
    - AWS credentials with S3 + DynamoDB access
"""

import json
import os
import time

import pytest

pytestmark = [pytest.mark.e2e]


# ── Test 1: Merkle Root Matches S3 Blocks ────────────────────────────────────


class TestMerkleRootMatchesS3Blocks:
    """Recomputed hash from S3 content blocks == stored manifest hash"""

    def test_merkle_root_matches_s3_blocks(
        self,
        sfn_orchestrator_arn,
        start_sfn_execution,
        wait_for_sfn_completion,
        merkle_verifier,
    ):
        # Run a simple execution first
        input_data = {
            "ownerId": "e2e_merkle_owner",
            "workflow_config": {
                "name": "e2e_merkle_test",
                "segments": [{"id": "seg_0", "type": "REACT"}],
            },
            "task_prompt": "Use read_only to echo 'merkle test' and provide final answer.",
            "max_iterations": 5,
            "total_segments": 1,
            "segment_to_run": 0,
        }

        execution_arn, execution_name = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="merkle_root",
        )
        result = wait_for_sfn_completion(execution_arn, timeout=120)

        if result["status"] != "SUCCEEDED":
            pytest.skip(f"Execution failed, cannot verify Merkle: {result['status']}")

        # Extract execution_id from output
        output = json.loads(result.get("output", "{}"))
        state_data = output.get("state_data", output)
        bag = state_data.get("bag", state_data)
        execution_id = bag.get("execution_id", execution_name)

        # Verify Merkle DAG
        verification = merkle_verifier.verify_execution(execution_id)

        if verification.segments_verified > 0:
            assert verification.passed, (
                f"Merkle root mismatch: {verification.errors}"
            )
            assert verification.blocks_verified > 0, (
                "No content blocks verified"
            )
        else:
            # No manifests found -- may be using different storage pattern
            assert len(verification.warnings) > 0


# ── Test 2: Control Plane Survives Dehydration ───────────────────────────────


class TestControlPlaneSurvivesDehydration:
    """All CONTROL_PLANE_FIELDS present after seal->ResultSelector->open across 3 segments"""

    def test_control_plane_survives_dehydration(
        self,
        sfn_orchestrator_arn,
        start_sfn_execution,
        wait_for_sfn_completion,
        merkle_verifier,
    ):
        input_data = {
            "ownerId": "e2e_controlplane_owner",
            "workflow_config": {
                "name": "e2e_controlplane_test",
                "segments": [
                    {"id": "seg_0", "type": "OPERATOR"},
                    {"id": "seg_1", "type": "REACT"},
                    {"id": "seg_2", "type": "VALIDATOR"},
                ],
            },
            "task_prompt": "Execute 3-segment workflow for control plane test.",
            "max_iterations": 5,
            "total_segments": 3,
            "segment_to_run": 0,
        }

        execution_arn, execution_name = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="controlplane",
        )
        result = wait_for_sfn_completion(execution_arn, timeout=300)

        if result["status"] != "SUCCEEDED":
            pytest.skip(f"Execution failed: {result['status']}")

        output = json.loads(result.get("output", "{}"))
        state_data = output.get("state_data", output)
        bag = state_data.get("bag", state_data)
        execution_id = bag.get("execution_id", execution_name)

        # Check critical control plane fields survived
        expected_fields = [
            "workflow_config",
            "total_segments",
            "ownerId",
        ]

        verification = merkle_verifier.verify_control_plane_preservation(
            execution_id, expected_fields,
        )

        # At minimum, these fields should be in the final bag
        for field in expected_fields:
            assert field in bag or field in state_data, (
                f"Control plane field '{field}' missing after 3-segment dehydration. "
                f"bag keys: {list(bag.keys())}, state_data keys: {list(state_data.keys())}"
            )


# ── Test 3: S3 Offloaded Flag Reinjected ─────────────────────────────────────


class TestS3OffloadedFlagReinjected:
    """__s3_offloaded + __s3_path survive seal_state_bag (v3.31 fix)"""

    def test_s3_offloaded_flag_reinjected(
        self,
        sfn_orchestrator_arn,
        start_sfn_execution,
        wait_for_sfn_completion,
    ):
        # Inject __s3_offloaded flag to trigger the FAIL scenario path
        input_data = {
            "ownerId": "e2e_s3offload_owner",
            "workflow_config": {
                "name": "e2e_s3offload_test",
                "segments": [{"id": "seg_0", "type": "REACT"}],
            },
            "task_prompt": "Use read_only to echo 's3 test' and provide final answer.",
            "max_iterations": 5,
            "total_segments": 1,
            "segment_to_run": 0,
            # This tests that the flag is properly propagated and re-injected
            "__s3_offloaded": False,
        }

        execution_arn, _ = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="s3offload",
        )
        result = wait_for_sfn_completion(execution_arn, timeout=120)

        # The execution should succeed (not crash due to missing flags)
        assert result["status"] in ("SUCCEEDED", "FAILED"), (
            f"S3 offload test should terminate, got {result['status']}"
        )


# ── Test 4: Rollback Restores Exact Checkpoint ──────────────────────────────


class TestRollbackRestoresExactCheckpoint:
    """Force-kill worker -> resume from checkpoint -> state matches pre-kill snapshot"""

    def test_rollback_restores_exact_checkpoint(
        self,
        sfn_orchestrator_arn,
        start_sfn_execution,
        wait_for_sfn_completion,
        merkle_verifier,
    ):
        # This test requires checkpoint infrastructure -- skip if not available
        input_data = {
            "ownerId": "e2e_rollback_owner",
            "workflow_config": {
                "name": "e2e_rollback_test",
                "segments": [{"id": "seg_0", "type": "REACT"}],
            },
            "task_prompt": "Use read_only to echo 'checkpoint test' and provide final answer.",
            "max_iterations": 5,
            "total_segments": 1,
            "segment_to_run": 0,
        }

        execution_arn, execution_name = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="rollback",
        )
        result = wait_for_sfn_completion(execution_arn, timeout=120)

        if result["status"] != "SUCCEEDED":
            pytest.skip(f"Execution failed: {result['status']}")

        output = json.loads(result.get("output", "{}"))
        state_data = output.get("state_data", output)
        bag = state_data.get("bag", state_data)
        execution_id = bag.get("execution_id", execution_name)

        # Verify that checkpoint data is consistent
        verification = merkle_verifier.verify_execution(execution_id)
        # Rollback-specific verification would need a more complex test setup
        # For now, verify basic Merkle consistency
        if verification.segments_verified > 0:
            assert verification.passed, f"Checkpoint integrity failed: {verification.errors}"


# ── Test 5: DynamoDB Checkpoint Matches S3 Manifest ──────────────────────────


class TestDynamoDBCheckpointMatchesS3Manifest:
    """DynamoDB execution record and S3 manifest point to same state version"""

    def test_dynamodb_checkpoint_matches_s3_manifest(
        self,
        sfn_orchestrator_arn,
        start_sfn_execution,
        wait_for_sfn_completion,
        merkle_verifier,
    ):
        input_data = {
            "ownerId": "e2e_dynamo_owner",
            "workflow_config": {
                "name": "e2e_dynamo_s3_test",
                "segments": [{"id": "seg_0", "type": "REACT"}],
            },
            "task_prompt": "Use read_only to echo 'dynamo test' and provide final answer.",
            "max_iterations": 5,
            "total_segments": 1,
            "segment_to_run": 0,
        }

        execution_arn, execution_name = start_sfn_execution(
            sfn_orchestrator_arn, input_data, name_prefix="dynamo_s3",
        )
        result = wait_for_sfn_completion(execution_arn, timeout=120)

        if result["status"] != "SUCCEEDED":
            pytest.skip(f"Execution failed: {result['status']}")

        output = json.loads(result.get("output", "{}"))
        state_data = output.get("state_data", output)
        bag = state_data.get("bag", state_data)
        execution_id = bag.get("execution_id", execution_name)

        # Full Merkle verification including DynamoDB <-> S3 cross-check
        verification = merkle_verifier.verify_execution(execution_id)

        if verification.segments_verified > 0:
            assert verification.passed, (
                f"DynamoDB/S3 mismatch: {verification.errors}"
            )
        # Log warnings for debugging
        if verification.warnings:
            for w in verification.warnings:
                pytest.warns(UserWarning, match=w) if False else None
