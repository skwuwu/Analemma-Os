
import pytest
import json
import os
import sys
from unittest.mock import MagicMock, patch

# Add backend to path (Handled by pytest.ini, but keeping safeguards if run standalone, but removing explicit src add to avoid double import)
# sys.path.append(os.path.abspath("backend/src")) -> REMOVED
if os.path.abspath("backend") not in sys.path:
    sys.path.append(os.path.abspath("backend"))

# Pre-set environment to avoid IJSON critical error during import
os.environ["IJSON_REQUIRED"] = "false"

# Import the handler (will be refactored, but this test should stay valid at the I/O level)
from src.handlers.core import process_segment_chunk

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("IJSON_REQUIRED", "false") # Allow running without strict ijson if local environment lacks it, or mock it

class TestChunkRunnerSnapshot:
    """
    Pessimistic Snapshot Test to ensure refactoring doesn't break external contract.
    Validates:
    - Input Event Structure -> Output Response Structure
    - Error Handling format
    """

    @patch("src.handlers.core.process_segment_chunk.segment_runner_handler")
    @patch("src.handlers.core.process_segment_chunk.boto3.client")
    def test_chunk_processing_success_snapshot(self, mock_boto_client, mock_segment_runner):
        """
        Verify that a standard chunk event produces the expected 'COMPLETED' response structure.
        """
        # Mock S3 for partition map loading (if it tries to load)
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        
        # Scenario: Inline partition slice (simplest case)
        chunk_data = {
            "chunk_id": "chunk-123",
            "start_segment": 0,
            "total_chunks": 1,
            "chunk_index": 0,
            "partition_slice": [
                {"id": "seg-0", "nodes": [], "edges": []},
                {"id": "seg-1", "nodes": [], "edges": []}
            ]
        }
        
        event = {
            "chunk_data": chunk_data,
            "workflow_config": {},
            "owner_id": "test-user",
            "workflowId": "wf-snapshot",
            "execution_id": "exec-1"
        }
        
        # Mock segment runner success
        mock_segment_runner.return_value = {"status": "COMPLETE", "final_state": {"done": True}}
        
        # Act
        response = process_segment_chunk.lambda_handler(event, MagicMock())
        
        # Assert (Snapshot of expected fields)
        assert response["chunk_id"] == "chunk-123"
        assert response["status"] == "COMPLETED"
        assert response["processed_segments"] == 2
        assert response["failed_segments"] == 0
        assert "chunk_results" in response
        assert len(response["chunk_results"]) == 2
        assert response["chunk_results"][0]["status"] == "COMPLETED"
        
        print(f"\n[Snapshot] Success Response: {json.dumps(response, default=str)}")

    @patch("src.handlers.core.process_segment_chunk.segment_runner_handler")
    def test_chunk_processing_failure_snapshot(self, mock_segment_runner):
        """
        Verify that segment failure is correctly aggregated into chunk response.
        """
        chunk_data = {
            "chunk_id": "chunk-fail",
            "chunk_index": 0,
            "partition_slice": [{"id": "seg-0"}]
        }
        event = {"chunk_data": chunk_data, "owner_id": "test-user"}
        
        # Mock failure
        mock_segment_runner.side_effect = Exception("Simulated Crash")
        
        # Act
        response = process_segment_chunk.lambda_handler(event, MagicMock())
        
        # Assert
        assert response["status"] == "FAILED"
        assert response["failed_segments"] == 1
        assert response["processed_segments"] == 0
        assert "error" not in response # The top level error is for handler crash, segment crash is in results
        # Wait, the current implementation might set top level status but not top level error if it caught the exception inside the loop
        
        print(f"\n[Snapshot] Failure Response: {json.dumps(response, default=str)}")

