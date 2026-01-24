
import json
import io
from unittest.mock import MagicMock, patch
import sys
import os

# Mock boto3 before importing the module
with patch.dict(sys.modules, {'boto3': MagicMock()}):
    # Set internal env var so it doesn't fail on import if it checks env
    os.environ['STATE_STORAGE_BUCKET'] = 'mock-bucket'
    import src.handlers.utils.state_data_manager as sdm

# Mock S3 client
mock_s3 = MagicMock()
sdm.s3_client = mock_s3

def test_large_payload():
    # Create a huge state (approx 300KB)
    huge_docs = ["x" * 1000 for _ in range(350)] # 350KB
    
    state_data = {
        "idempotency_key": "test_exec_id",
        "current_state": {
            "some_key": "some_value"
        },
        "workflow_config": {"id": "test_config"}
    }
    
    execution_result = {
        "final_state": {
            "documents": huge_docs, # The huge field
            "other_stuff": "normal"
        }
    }
    
    event = {
        "action": "update_and_compress",
        "state_data": state_data,
        "execution_result": execution_result,
        "max_payload_size_kb": 200
    }
    
    print("--- Running update_and_compress_state_data ---")
    result = sdm.update_and_compress_state_data(event)
    
    print(f"\n[Result Keys]: {list(result.keys())}")
    
    # Check current_state content
    curr_state = result.get('current_state', {})
    print(f"[Current State Keys]: {list(curr_state.keys())}")
    
    if result.get('s3_offloaded'):
        print("[SUCCESS] s3_offloaded is True")
    else:
        print("[FAILURE] s3_offloaded is False")

    # Check if 'documents' is offloaded
    docs = curr_state.get('documents')
    if isinstance(docs, dict) and docs.get('type') == 's3_reference':
        print("[SUCCESS] 'documents' was offloaded to S3 reference")
    elif isinstance(result, dict) and result.get('__s3_offloaded'):
        print("[SUCCESS] Full state was offloaded")
    else:
        print(f"[FAILURE] 'documents' is type: {type(docs)} (Length: {len(docs) if isinstance(docs, list) else 'N/A'})")
        
    # Check final size
    final_json = json.dumps(result)
    print(f"[Final Payload Size]: {len(final_json)} bytes")

if __name__ == "__main__":
    test_large_payload()
