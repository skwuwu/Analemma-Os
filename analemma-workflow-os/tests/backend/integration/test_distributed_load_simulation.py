
import pytest
import json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

# Assume backend/src in path
import sys
import os

# Add 'backend' to sys.path so 'src.common' is resolvable
sys.path.append(os.path.abspath("backend"))
# Add 'backend/src' to sys.path so we can import src.handlers directly if needed, 
# though 'from src.handlers' is preferred if 'backend' is in path.
sys.path.append(os.path.abspath("backend/src"))

try:
    # Try importing using the package structure implied by the production code
    from src.handlers.core.aggregate_distributed_results import lambda_handler, _load_results_from_s3
except ImportError:
    # Fallback/Debug: explicitly import from file location if package resolution fails
    # This often happens in pytest if __init__.py is missing in root 'backend'
    try:
        from backend.src.handlers.core.aggregate_distributed_results import lambda_handler, _load_results_from_s3
    except ImportError:
         sys.path.append(os.path.abspath("."))
         from backend.src.handlers.core.aggregate_distributed_results import lambda_handler, _load_results_from_s3

class TestDistributedReducerPessimisticLoad:
    
    @patch("boto3.resource")
    @patch("boto3.client")
    @patch("src.handlers.core.aggregate_distributed_results._load_results_from_s3") # Patch the loader directly
    def test_reducer_massive_segment_load(self, mock_load_s3, mock_boto3_client, mock_boto3_resource):
        """
        [Pessimistic] Massive Distributed Load Test.
        Simulate a Reducer receiving 5,000 results (simulated from S3 manifest).
        Verify that the Aggregation Logic handles the volume efficiently.
        """
        # Mock 5,000 successful results
        massive_results = [
            {
                "status": "COMPLETED", 
                "chunk_id": f"chunk-{i}",
                "processed_segments": 1,
                "execution_time": 0.1,
                "chunk_results": [{"result": {"output": "ok"}}]
            }
            for i in range(5000)
        ]
        mock_load_s3.return_value = massive_results
        
        # Mock Context
        mock_context = MagicMock()
        mock_context.get_remaining_time_in_millis.return_value = 300000 
        
        # Mock DynamoDB Table for _load_latest_state and _save_final_state
        mock_table = MagicMock()
        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        mock_boto3_resource.return_value = mock_dynamodb
        
        # Mock latest state retrieval
        mock_table.get_item.return_value = {"Item": {"state": {}}}

        # Event
        event = {
            "execution_id": "exec-load-test",
            "workflow_id": "wf-pessimistic",
            "distributed_results_s3_path": "s3://bucket/massive-manifest.json",
            "use_s3_results": True,
            "state_data": {"workflowId": "wf-pessimistic"}
        }
        
        # Act
        import time
        start = time.time()
        response = lambda_handler(event, mock_context)
        duration = time.time() - start
        
        # Assertions
        assert response["status"] == "COMPLETED"
        assert response["execution_summary"]["total_chunks"] == 5000
        assert response["execution_summary"]["successful_chunks"] == 5000
        assert response["execution_summary"]["total_segments_processed"] == 5000
        
        # Pessimistic Performance Check: 5000 items should aggregate in < 2.0s
        assert duration < 2.0, f"Aggregation too slow: {duration}s"

