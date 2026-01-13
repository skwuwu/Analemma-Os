
import pytest
import json
import os
import sys
from unittest.mock import MagicMock, patch

# Add backend/src to path so 'from src.handlers' and 'from main' works
sys.path.append(os.path.abspath("backend/src"))
sys.path.append(os.path.abspath("backend"))

from src.handlers.core import segment_runner_handler
@pytest.fixture(autouse=True)
def mock_aws_env(monkeypatch):
    """Set dummy AWS credentials to avoid botocore looking for real ones."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

class TestInstructionRefinement:
    
    @patch("src.services.execution.segment_runner_service.WorkflowRepository")
    @patch("src.services.execution.segment_runner_service.run_workflow")
    @patch("src.services.execution.segment_runner_service.StateManager")
    @patch("src.services.execution.segment_runner_service._partition_workflow_dynamically")
    def test_instruction_refinement_injection(self, mock_partition, mock_state_manager_cls, mock_run, mock_repo_cls):
        """
        Verify that _self_healing_metadata['suggested_fix'] is appended to prompts.
        """
        # Mock Repo
        mock_repo = MagicMock()
        mock_repo_cls.return_value = mock_repo
        mock_repo.get_user.return_value = {"userId": "test-user", "subscription_plan": "pro"}
        mock_repo.consume_run.return_value = (True, None)

        mock_run.return_value = {"status": "SUCCEEDED"}
        
        mock_state_manager = mock_state_manager_cls.return_value
        mock_state_manager.handle_state_storage.return_value = ({"result": "ok"}, "s3://bucket/key")
        mock_state_manager.download_state_from_s3.return_value = {}
        
        # Define a mock workflow config with one LLM node
        nodes = [
            {
                "id": "llm_node_1",
                "type": "llm",
                "prompt": "Summarize this text."
            },
            {
                "id": "llm_node_2",
                "type": "llm",
                "config": {
                    "prompt": "Translate this."
                }
            }
        ]
        workflow_config = {"nodes": nodes, "edges": []}

        # Mock partitioner to return distinct segment containing these nodes
        mock_partition.return_value = [workflow_config]
        
        # Self-Healing Metadata
        healing_metadata = {
            "suggested_fix": "Please output valid JSON only.",
            "error_context": {}
        }
        
        event = {
            "workflow_config": workflow_config,
            "segment_to_run": 0,
            "_self_healing_metadata": healing_metadata,
            "ownerId": "test-user",
            "workflowId": "wf-1",
            "partition_map": None
        }
        
        # Act
        segment_runner_handler.lambda_handler(event, None)
        
        # Assert - check run_workflow call arguments
        call_args = mock_run.call_args
        _, kwargs = call_args
        config_json = kwargs.get("config_json") or call_args[0]
        
        nodes_out = config_json["nodes"]
        
        # Check node 1 (top-level prompt)
        node1 = next(n for n in nodes_out if n["id"] == "llm_node_1")
        assert "🚨 [SELF-HEALING ADVICE]:" in node1["prompt"]
        assert "Please output valid JSON only." in node1["prompt"]
        
        # Check node 2 (config level prompt)
        node2 = next(n for n in nodes_out if n["id"] == "llm_node_2")
        assert "🚨 [SELF-HEALING ADVICE]:" in node2["config"]["prompt"]
        assert "Please output valid JSON only." in node2["config"]["prompt"]
        
    @patch("src.services.execution.segment_runner_service.WorkflowRepository")
    @patch("src.services.execution.segment_runner_service.run_workflow")
    @patch("src.services.execution.segment_runner_service.StateManager")
    @patch("src.services.execution.segment_runner_service._partition_workflow_dynamically")
    def test_idempotent_injection(self, mock_partition, mock_state_manager_cls, mock_run, mock_repo_cls):
        """
        Verify that we don't duplicate advice if it's already there (replace instead).
        """
        mock_repo_cls.return_value.get_user.return_value = {"userId": "test-user", "subscription_plan": "pro"}
        mock_repo_cls.return_value.consume_run.return_value = (True, None)
        mock_run.return_value = {"status": "SUCCEEDED"}
        
        mock_state_manager = mock_state_manager_cls.return_value
        mock_state_manager.handle_state_storage.return_value = ({"result": "ok"}, "s3://bucket/key")

        # Node already has old advice
        nodes = [
            {
                "id": "llm_node_dirty",
                "type": "llm",
                "prompt": "Do X.\n\n🚨 [SELF-HEALING ADVICE]:\n<user_advice>\nSYSTEM WARNING: ...\nOld advice.\n</user_advice>"
            }
        ]
        workflow_config = {"nodes": nodes, "edges": []}
        mock_partition.return_value = [workflow_config]
        
        # New advice
        healing_metadata = {"suggested_fix": "New improved advice."}
        
        event = {
            "workflow_config": workflow_config,
            "segment_to_run": 0,
            "_self_healing_metadata": healing_metadata,
            "ownerId": "test-user",
            "workflowId": "wf-2",
            "partition_map": None
        }
        
        # Act
        segment_runner_handler.lambda_handler(event, None)
        
        call_args = mock_run.call_args
        _, kwargs = call_args
        config_json = kwargs.get("config_json") or call_args[0]
        node = config_json["nodes"][0]
        
        # Check that it contains new advice AND check that it DOES NOT contain old advice OR duplicate tags
        assert "New improved advice" in node["prompt"]
        assert "Old advice" not in node["prompt"]
        assert node["prompt"].count("🚨 [SELF-HEALING ADVICE]:") == 1
        assert node["prompt"].count("<user_advice>") == 1

    @patch("src.services.execution.segment_runner_service.WorkflowRepository")
    @patch("src.services.execution.segment_runner_service.run_workflow")
    @patch("src.services.execution.segment_runner_service.StateManager")
    @patch("src.services.execution.segment_runner_service._partition_workflow_dynamically")
    def test_ignore_non_llm_nodes(self, mock_partition, mock_state_manager_cls, mock_run, mock_repo_cls):
        """
        Verify that nodes without 'prompt' fields are untouched.
        """
        mock_repo_cls.return_value.get_user.return_value = {"userId": "test-user", "subscription_plan": "pro"}
        mock_repo_cls.return_value.consume_run.return_value = (True, None)
        mock_run.return_value = {"status": "SUCCEEDED"}
        
        mock_state_manager = mock_state_manager_cls.return_value
        mock_state_manager.handle_state_storage.return_value = ({"result": "ok"}, "s3://bucket/key")

        nodes = [
            {
                "id": "python_node",
                "type": "operator",
                "config": {
                    "code": "print('hello')"
                }
            }
        ]
        workflow_config = {"nodes": nodes, "edges": []}
        mock_partition.return_value = [workflow_config]
        
        healing_metadata = {"suggested_fix": "Fix invalid JSON."}
        
        event = {
            "workflow_config": workflow_config,
            "segment_to_run": 0,
            "_self_healing_metadata": healing_metadata,
            "ownerId": "test-user",
            "workflowId": "wf-3",
            "partition_map": None
        }
        
        segment_runner_handler.lambda_handler(event, None)
        
        call_args = mock_run.call_args
        _, kwargs = call_args
        config_json = kwargs.get("config_json") or call_args[0]
        node = config_json["nodes"][0]
        
        # Ensure nothing was added
        assert "prompt" not in node
        assert "prompt" not in node["config"]
        # Just to be safe, check code wasn't mangled
        assert node["config"]["code"] == "print('hello')"
