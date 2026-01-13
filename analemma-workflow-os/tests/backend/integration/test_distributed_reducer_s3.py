import pytest
import json
import os
import boto3
from moto import mock_aws
from unittest.mock import MagicMock, patch

# Import the lambda handler
from src.handlers.core.aggregate_distributed_results import lambda_handler

@pytest.fixture
def mock_s3_env():
    """Setup S3 and DynamoDB environment variables and mocks"""
    with mock_aws():
        # Setup S3
        s3 = boto3.client('s3', region_name='us-east-1')
        bucket_name = 'test-results-bucket'
        s3.create_bucket(Bucket=bucket_name)
        
        # Setup DynamoDB (required by _load_latest_state and _save_final_state)
        dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
        dynamodb.create_table(
            TableName='test-workflows-table',
            KeySchema=[{'AttributeName': 'execution_id', 'KeyType': 'HASH'}, {'AttributeName': 'state_type', 'KeyType': 'RANGE'}],
            AttributeDefinitions=[{'AttributeName': 'execution_id', 'AttributeType': 'S'}, {'AttributeName': 'state_type', 'AttributeType': 'S'}],
            BillingMode='PAY_PER_REQUEST'
        )
        
        # Override Environment Variables
        with patch.dict(os.environ, {
            'WORKFLOWS_TABLE': 'test-workflows-table',
            'AWS_DEFAULT_REGION': 'us-east-1'
        }):
            # Mock DynamoDBConfig
            with patch('backend.aggregate_distributed_results.DynamoDBConfig') as MockConfig:
                MockConfig.WORKFLOWS_TABLE = 'test-workflows-table'
                
                # Seed initial state
                table = dynamodb.Table('test-workflows-table')
                table.put_item(Item={
                    'execution_id': 'exec-123',
                    'state_type': 'LATEST',
                    'definition': {},
                    'execution_start_time': 0,
                    'status': 'RUNNING'
                })
                table.put_item(Item={
                    'execution_id': 'exec-456',
                    'state_type': 'LATEST',
                    'definition': {},
                    'execution_start_time': 0,
                    'status': 'RUNNING'
                })
                table.put_item(Item={
                    'execution_id': 'exec-789',
                    'state_type': 'LATEST',
                    'definition': {},
                    'execution_start_time': 0,
                    'status': 'RUNNING'
                })
                
                yield s3, bucket_name

def test_large_scale_aggregation_from_s3(mock_s3_env):
    """
    Realistic Test: Aggregate 100+ results stored in S3.
    Verifies that the reducer can download, parse, and aggregate a large result set.
    """
    s3, bucket = mock_s3_env
    key = 'large_results.json'
    
    # Generate 105 successful chunks
    results = []
    
    # 105 Success
    for i in range(105):
        chunk_data = {
            'chunk_id': f'chunk_{i}',
            'status': 'COMPLETED',
            'processed_segments': 10,
            # Fix data structure: Reducer expects 'chunk_results' list of segment outputs
            'chunk_results': [
                {
                    'status': 'COMPLETED',
                    'result': {'new_history_logs': [{'timestamp': 1000 + i, 'msg': f'log_{i}'}]}
                }
            ],
            'execution_time': 100
        }
        results.append(chunk_data)
        
    # Results complete (no failures)
        
    # Upload to S3
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(results))
    
    event = {
        'use_s3_results': True,
        'distributed_results_s3_path': f's3://{bucket}/{key}',
        'state_data': {'workflowId': 'exec-123'}
    }
    
    # Execute
    response = lambda_handler(event, None)
    
    # Assertions
    if response['status'] != 'COMPLETED':
        print(f"Handler returned FAILED. Error: {response.get('error')}")
        
    assert response['status'] == 'COMPLETED'
    
    assert response['total_chunks'] == 105
    assert response['successful_chunks'] == 105
    
    # Verify execution summary
    summary = response['execution_summary']
    assert summary['total_segments_processed'] == 1050 # 105 chunks * 10 

def test_missing_s3_file(mock_s3_env):
    """
    Error Injection: S3 file does not exist.
    """
    s3, bucket = mock_s3_env
    
    event = {
        'use_s3_results': True,
        'distributed_results_s3_path': f's3://{bucket}/non_existent.json',
        'state_data': {'workflowId': 'exec-456'}
    }
    
    response = lambda_handler(event, None)
    
    # Should fail gracefully
    assert response['status'] == 'FAILED'
    # Current implementation returns error if no results found or S3 fail
    # _load_results_from_s3 catches exception and returns []
    # Then checks "if not distributed_results: return FAILED"
    assert "No distributed results" in response['error']

def test_malformed_s3_content(mock_s3_env):
    """
    Error Injection: S3 file contains invalid JSON.
    """
    s3, bucket = mock_s3_env
    key = 'bad.json'
    s3.put_object(Bucket=bucket, Key=key, Body='{ invalid json ...')
    
    event = {
        'use_s3_results': True,
        'distributed_results_s3_path': f's3://{bucket}/{key}',
        'state_data': {'workflowId': 'exec-789'}
    }
    
    response = lambda_handler(event, None)
    
    assert response['status'] == 'FAILED'
    assert "No distributed results" in response['error']
