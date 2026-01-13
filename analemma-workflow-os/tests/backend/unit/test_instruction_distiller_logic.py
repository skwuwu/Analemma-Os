
import pytest
import json
import os
from unittest.mock import MagicMock, patch, ANY
from decimal import Decimal
from botocore.exceptions import ClientError

# Mock environment variables before importing the module
@pytest.fixture(autouse=True)
def mock_env():
    with patch.dict(os.environ, {
        "DISTILLED_INSTRUCTIONS_TABLE": "test-instructions-table",
        "WORKFLOW_STATE_BUCKET": "test-bucket",
        "AWS_REGION": "us-east-1"
    }):
        yield

# Import the module under test
# Assuming backend/src is in sys.path due to conftest.py
try:
    from src.handlers.core import instruction_distiller
except ImportError:
    # Fallback for different path structures
    from backend.src.handlers import instruction_distiller

@pytest.fixture
def mock_aws_clients():
    with patch('src.handlers.core.instruction_distiller.s3_client') as mock_s3, \
         patch('src.handlers.core.instruction_distiller.bedrock_client') as mock_bedrock, \
         patch('src.handlers.core.instruction_distiller.instructions_table') as mock_table:
        yield mock_s3, mock_bedrock, mock_table


class TestInstructionDistillerLogic:

    def test_lambda_handler_missing_fields(self, mock_aws_clients):
        """Test 400 Bad Request when required fields are missing."""
        event = {"detail": {"execution_id": "exec-1"}} # Missing node_id, etc.
        response = instruction_distiller.lambda_handler(event, None)
        assert response["statusCode"] == 400
        assert "Missing" in response["body"]

    def test_lambda_handler_s3_load_failure(self, mock_aws_clients):
        """Test handling when S3 load fails."""
        mock_s3, _, _ = mock_aws_clients
        mock_s3.get_object.side_effect = Exception("S3 Error")
        
        event = {
            "detail": {
                "execution_id": "exec-1",
                "node_id": "node-1",
                "original_output_ref": "s3://bucket/orig",
                "corrected_output_ref": "s3://bucket/corr"
            }
        }
        
        response = instruction_distiller.lambda_handler(event, None)
        assert response["statusCode"] == 400
        assert "Failed to load outputs" in response["body"]

    def test_extract_new_instructions_bedrock_success(self, mock_aws_clients):
        """Test successful instruction extraction via Bedrock."""
        _, mock_bedrock, _ = mock_aws_clients
        
        # Mock Bedrock response
        mock_response_body = json.dumps({
            "content": [{"text": '["Use formal tone", "Check grammar"]'}]
        })
        mock_bedrock.invoke_model.return_value = {"body": MagicMock(read=lambda: mock_response_body)}

        instructions = instruction_distiller._extract_new_instructions(
            "Original text", "Corrected text", "node-1", "wf-1"
        )
        
        assert len(instructions) == 2
        assert "Use formal tone" in instructions
        assert "Check grammar" in instructions

    def test_extract_new_instructions_malformed_json(self, mock_aws_clients):
        """Test resilience against malformed JSON from Bedrock."""
        _, mock_bedrock, _ = mock_aws_clients
        
        # Bedrock returns non-JSON text
        mock_response_body = json.dumps({
            "content": [{"text": 'Sure, here are instructions: - Use formal tone'}]
        })
        mock_bedrock.invoke_model.return_value = {"body": MagicMock(read=lambda: mock_response_body)}

        instructions = instruction_distiller._extract_new_instructions(
            "Orig", "Corr", "node-1", "wf-1"
        )
        # Should return empty list, not crash
        assert instructions == []

    def test_extract_new_instructions_client_error(self, mock_aws_clients):
        """Test resilience against Bedrock ClientError."""
        _, mock_bedrock, _ = mock_aws_clients
        mock_bedrock.invoke_model.side_effect = ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel")
        
        instructions = instruction_distiller._extract_new_instructions(
            "Orig", "Corr", "node-1", "wf-1"
        )
        assert instructions == []

    def test_save_distilled_instructions_weight_decay(self, mock_aws_clients):
        """Test that existing instructions key weights are decayed."""
        _, _, mock_table = mock_aws_clients
        
        # Mock getting existing instructions (Sequential calls)
        # Call 1: _get_weighted_instructions -> instructions_table.get_item (LATEST index)
        # Call 2: _get_weighted_instructions -> instructions_table.get_item (Actual Item)
        # Call 3: _update_latest_instruction_index -> instructions_table.put_item (Wait, update uses put_item)
        
        mock_table.get_item.side_effect = [
            {"Item": {"latest_instruction_sk": "sk-old"}}, # 1. Index lookup
            {"Item": {
                "weighted_instructions": [
                    {"text": "Old Rule", "weight": Decimal("1.0"), "is_active": True},
                    {"text": "Weak Rule", "weight": Decimal("0.2"), "is_active": True}
                ]
            }} # 2. Data lookup
        ]
        
        new_instructions = ["New Rule"]
        
        instruction_distiller._save_distilled_instructions(
            "wf-1", "node-1", "user-1", new_instructions, "exec-1"
        )
        
        # Verify put_item was called for saving instructions
        # We need to find the call that saved the instructions (sk != LATEST)
        # mock_table.put_item is called twice: once for data, once for index update
        
        calls = mock_table.put_item.call_args_list
        # Filter for the call that saves the instructions (has 'instructions' key)
        save_call = next(c for c in calls if "instructions" in c[1]["Item"])
        item = save_call[1]["Item"]
        
        saved_instructions = item["weighted_instructions"]
        
        # "Old Rule" should be decayed: 1.0 - 0.3 = 0.7
        old_rule = next(i for i in saved_instructions if i["text"] == "Old Rule")
        assert old_rule["weight"] == Decimal("0.7")
        
        # "Weak Rule" should be gone (0.2 - 0.3 = -0.1 < 0.1)
        assert not any(i["text"] == "Weak Rule" for i in saved_instructions)
        
        # "New Rule" should have default weight 1.0
        new_rule = next(i for i in saved_instructions if i["text"] == "New Rule")
        assert new_rule["weight"] == Decimal("1.0")

    def test_record_instruction_feedback_positive(self, mock_aws_clients):
        """Test positive feedback increases weight."""
        _, _, mock_table = mock_aws_clients
        
        # Mock getting latest index and then the item
        mock_table.get_item.side_effect = [
            {"Item": {"latest_instruction_sk": "sk-1"}}, # LATEST index
            {"Item": {
                "weighted_instructions": [{"text": "Good Rule", "weight": Decimal("1.0")}],
                "total_applications": Decimal("10"),
                "success_rate": Decimal("0.5")
            }} # Actual Item
        ]
        
        instruction_distiller.record_instruction_feedback(
            "user-1", "wf-1", "node-1", is_positive=True
        )
        
        # Check update_item
        args, kwargs = mock_table.update_item.call_args
        expression_values = kwargs["ExpressionAttributeValues"]
        
        updated_list = expression_values[":wi"]
        updated_rule = updated_list[0]
        
        # 1.0 + 0.1 = 1.1
        assert updated_rule["weight"] == Decimal("1.1")
        
        # Success rate should increase
        # ((0.5 * 9) + 1) / 10 = 5.5 / 10 = 0.55
        new_rate = expression_values[":sr"]
        assert new_rate == Decimal("0.55")

    def test_deduplicate_instructions(self):
        """Test deduplication logic."""
        existing = [{"text": "Rule A", "weight": 1.0}]
        new_insts = ["rule a", "Rule B"] # "rule a" is duplicate (case-insensitive check)
        
        merged = instruction_distiller._merge_and_deduplicate_instructions(existing, new_insts)
        
        assert len(merged) == 2
        assert "Rule A" in merged # Keeps existing casing
        assert "Rule B" in merged

