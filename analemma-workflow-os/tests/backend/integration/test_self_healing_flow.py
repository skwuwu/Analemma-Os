
import pytest
import json
import os
import sys
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

# Add backend to path
sys.path.append(os.path.abspath("backend/src"))

try:
    # Import the handler from core/ after migration
    from src.handlers.core.quick_fix_executor import lambda_handler, QuickFixType
except ImportError:
    # Fallback
    sys.path.append(os.path.abspath("backend/apps/backend"))
    from src.handlers.core.quick_fix_executor import lambda_handler, QuickFixType

class TestSelfHealingFlow:
    
    @patch("src.handlers.core.quick_fix_executor.sfn_client")
    @patch("src.handlers.core.quick_fix_executor.dynamodb")
    @patch("src.handlers.core.quick_fix_executor.sns_client")
    def test_self_healing_trigger_and_restart(self, mock_sns, mock_dynamodb, mock_sfn):

        """
        Scenario: Error Recovery (Self-Healing).
        
        1. An execution fails.
        2. Frontend/System requests a 'SELF_HEALING' fix.
        3. Executor retrieves failure context.
        4. Executor starts a NEW execution with 'suggested_fix' and 'error_context'.
        5. Verify the new execution is linked to the old one.
        """
        # 1. Setup Data
        task_id = "exec-fail-123"
        owner_id = "user-test"
        execution_arn = f"arn:aws:states:us-east-1:123:execution:AnalemmaFlow:{task_id}"
        
        # 2. Mock DynamoDB Execution Record
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        
        # _get_execution -> Returns current failed task
        mock_table.get_item.return_value = {
            "Item": {
                "ownerId": owner_id,
                "executionArn": execution_arn,
                "task_id": task_id,
                "input": json.dumps({"query": "analyze data"}),
                "self_healing_count": 0,
                "workflow_config": json.dumps({"orchestrator_type": "standard"})
            }
        }
        
        # _increment_retry_count -> Returns updated count (1)
        mock_table.update_item.return_value = {"Attributes": {"self_healing_count": 1}}
        
        # 3. Mock Step Functions History (Failure Context)
        mock_sfn.get_execution_history.return_value = {
            "events": [
                {
                    "type": "TaskFailed",
                    "id": 5,
                    "previousEventId": 4,
                    "taskFailedEventDetails": {
                        "error": "ValueError",
                        "cause": "Invalid JSON format in prompt"
                    },
                    "timestamp": datetime.now(timezone.utc)
                },
                {
                    "type": "TaskStateEntered",
                    "id": 4,
                    "stateEnteredEventDetails": {
                        "name": "GeneratePromptNode"
                    }
                }
            ]
        }
        
        # Mock Start Execution (The 'Recovery')
        new_exec_arn = "arn:aws:states:us-east-1:123:execution:AnalemmaFlow:exec-recovery-456"
        mock_sfn.start_execution.return_value = {
            "executionArn": new_exec_arn,
            "startDate": datetime.now(timezone.utc)
        }
        
        # 4. Trigger QuickFix Event
        event = {
            "body": json.dumps({
                "task_id": task_id,
                "fix_type": "self_healing",
                "owner_id": owner_id,
                "payload": {
                    "suggested_fix": "Escape special characters in JSON prompt"
                }
            })
        }
        
        # Act
        response = lambda_handler(event, None)
        
        # 5. Assertions
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["success"] is True
        assert body["action_taken"] == "self_healing_initiated"
        assert body["new_execution_arn"] == new_exec_arn
        
        # Verify Context Retrieval
        mock_sfn.get_execution_history.assert_called_with(
            executionArn=execution_arn, maxResults=20, reverseOrder=True
        )
        
        # Verify Restart with Fix Metadata
        call_args = mock_sfn.start_execution.call_args
        _, kwargs = call_args
        input_payload = json.loads(kwargs["input"])
        
        assert "_self_healing_metadata" in input_payload
        healing_meta = input_payload["_self_healing_metadata"]
        
        assert healing_meta["original_execution_arn"] == execution_arn
        assert healing_meta["suggested_fix"] == "Escape special characters in JSON prompt"
        assert healing_meta["error_context"]["error_type"] == "TaskFailed"
        assert healing_meta["error_context"]["error_message"] == "Invalid JSON format in prompt"
        assert healing_meta["enable_auto_correction"] is True
        
        # Verify DB Update (Linking new execution)
        # Check that update_item was called to set 'healing_execution_arn'
        # The code calls update_item twice: once for incrementing count, once for status update.
        # We check the LAST call or scan calls.
        
        calls = mock_table.update_item.call_args_list
        # Find the status update call
        status_update = next((c for c in calls if "healing_execution_arn" in str(c)), None)
        assert status_update is not None, "Failed to link new execution ARN in DynamoDB"


class TestSelfHealingEdgeCases:
    
    @patch("src.handlers.core.quick_fix_executor.sfn_client")
    @patch("src.handlers.core.quick_fix_executor.dynamodb")
    def test_fallback_empty_history(self, mock_dynamodb, mock_sfn):
        """Scenario 1: History is empty or unavailable. System should not crash."""
        owner_id = "user-test"
        task_id = "exec-no-history"
        
        # Mock Execution
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        mock_table.get_item.return_value = {
            "Item": {
                "ownerId": owner_id,
                "executionArn": "arn:abc",
                "task_id": task_id,
                "self_healing_count": 0
            }
        }
        mock_table.update_item.return_value = {"Attributes": {"self_healing_count": 1}}
        
        # Mock Start Execution (Success)
        mock_sfn.start_execution.return_value = {"executionArn": "arn:new", "startDate": datetime.now()}
        
        # Mock History -> Empty!
        mock_sfn.get_execution_history.return_value = {"events": []}
        
        event = {
            "body": json.dumps({
                "task_id": task_id, "fix_type": "self_healing", "owner_id": owner_id,
                "payload": {"suggested_fix": "fix it"}
            })
        }
        
        response = lambda_handler(event, None)
        
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["success"] is True
        
        # Verify metadata has fallback error context
        call_args = mock_sfn.start_execution.call_args
        _, kwargs = call_args
        input_payload = json.loads(kwargs["input"])
        error_ctx = input_payload["_self_healing_metadata"]["error_context"]
        
        assert error_ctx["error_type"] == "Unknown"
        assert error_ctx["error_name"] == "NoFailureFound"

    @patch("src.handlers.core.quick_fix_executor.sfn_client")
    @patch("src.handlers.core.quick_fix_executor.dynamodb")
    def test_circuit_breaker_max_attempts(self, mock_dynamodb, mock_sfn):
        """Scenario 2: Max attempts exceeded. Should return 403."""
        owner_id = "user-test"
        task_id = "exec-infinite-loop"
        
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        mock_table.get_item.return_value = {
            "Item": {
                "ownerId": owner_id,
                "executionArn": "arn:abc",
                "task_id": task_id,
                "self_healing_count": 3 # MAX is 3
            }
        }
        
        event = {
            "body": json.dumps({
                "task_id": task_id, "fix_type": "self_healing", "owner_id": owner_id
            })
        }
        
        response = lambda_handler(event, None)
        
        assert response["statusCode"] == 403
        body = json.loads(response["body"])
        assert "자가 치유 횟수가 한도" in body["error"]
        # Ensure we did NOT start a new execution
        mock_sfn.start_execution.assert_not_called()

    @patch("src.handlers.core.quick_fix_executor.sfn_client")
    @patch("src.handlers.core.quick_fix_executor.dynamodb")
    def test_security_owner_mismatch(self, mock_dynamodb, mock_sfn):
        """Scenario 3: Owner ID mismatch (IDOR). Should return 403."""
        attacker = "user-attacker"
        victim = "user-victim"
        task_id = "exec-victim"
        
        mock_table = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        mock_table.get_item.return_value = {
            "Item": {
                "ownerId": victim, # Resource owned by victim
                "executionArn": "arn:abc",
                "task_id": task_id
            }
        }
        
        event = {
            "body": json.dumps({
                "task_id": task_id, "fix_type": "self_healing", "owner_id": attacker # Request by attacker
            })
        }
        
        response = lambda_handler(event, None)
        
        assert response["statusCode"] == 403
        body = json.loads(response["body"])
        assert "접근 권한이 없습니다" in body["error"]

