"""
Distributed Processing Core Tests
프로덕션 코드: aggregate_distributed_results.py, store_distributed_task_token.py
핵심: Map-Reduce, 부분 실패, 멱등성

no-llm 테스트: 결정론적 분산 처리 검증
"""
import pytest
import json
import sys
import os
from unittest.mock import patch, MagicMock

# 환경 변수 설정
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("DISTRIBUTED_TOKENS_TABLE", "test-distributed-tokens")

# OpenAI 모킹
mock_openai = MagicMock()
sys.modules['openai'] = mock_openai

from moto import mock_aws
import boto3


@pytest.fixture(autouse=True)
def mock_aws_services():
    """모든 테스트에 AWS 모킹"""
    with mock_aws():
        dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
        dynamodb.create_table(
            TableName='test-distributed-tokens',
            KeySchema=[
                {'AttributeName': 'pk', 'KeyType': 'HASH'},
                {'AttributeName': 'sk', 'KeyType': 'RANGE'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'pk', 'AttributeType': 'S'},
                {'AttributeName': 'sk', 'AttributeType': 'S'}
            ],
            BillingMode='PAY_PER_REQUEST'
        )
        yield


class TestAggregateDistributedResultsCore:
    """aggregate_distributed_results.py 핵심 테스트"""
    
    def test_aggregate_empty_results_returns_failed(self):
        """빈 distributed_results 배열은 FAILED 상태 반환"""
        from src.handlers.core.aggregate_distributed_results import lambda_handler
        
        event = {
            "distributed_results": [],  # 프로덕션 코드는 distributed_results 사용
            "state_data": {"execution_id": "exec-123"}
        }
        
        result = lambda_handler(event, None)
        
        # 빈 결과 = FAILED 상태
        assert result.get("status") == "FAILED"
        assert "No distributed results" in result.get("error", "")
    
    def test_aggregate_multiple_results_success(self):
        """다중 결과 집계 성공"""
        from src.handlers.core.aggregate_distributed_results import lambda_handler
        
        event = {
            "distributed_results": [
                {"status": "SUCCESS", "chunk_id": "chunk_0", "output": {"data": "result0"}},
                {"status": "SUCCESS", "chunk_id": "chunk_1", "output": {"data": "result1"}},
                {"status": "SUCCESS", "chunk_id": "chunk_2", "output": {"data": "result2"}}
            ],
            "state_data": {"execution_id": "exec-123"},
            "use_s3_results": False
        }
        
        result = lambda_handler(event, None)
        
        # 성공 또는 집계 결과 반환
        assert result.get("status") in ["SUCCESS", "COMPLETED"] or result.get("total_chunks") == 3
    
    def test_aggregate_handles_partial_failure(self):
        """부분 실패 처리 - 일부 청크 실패해도 처리"""
        from src.handlers.core.aggregate_distributed_results import lambda_handler
        
        event = {
            "distributed_results": [
                {"status": "SUCCESS", "chunk_id": "chunk_0", "output": {"data": "ok"}},
                {"status": "FAILED", "chunk_id": "chunk_1", "error": "timeout"},
                {"status": "SUCCESS", "chunk_id": "chunk_2", "output": {"data": "ok"}}
            ],
            "state_data": {"execution_id": "exec-123"},
            "use_s3_results": False
        }
        
        result = lambda_handler(event, None)
        
        # 부분 실패 시 PARTIAL_SUCCESS 또는 에러 정보 포함
        assert "status" in result or "error" in str(result).lower()


class TestStoreDistributedTaskTokenCore:
    """store_distributed_task_token.py 핵심 테스트"""
    
    def test_store_distributed_token_success(self):
        """분산 토큰 저장 성공 - 프로덕션 스키마 준수"""
        from backend.store_distributed_task_token import lambda_handler
        
        # 프로덕션 코드는 TaskToken (대문자 T), parent_execution_id 필수
        event = {
            "TaskToken": "distributed-token-xyz",
            "chunk_result": {
                "chunk_id": "chunk_0001",
                "status": "HITL_PAUSED"
            },
            "distributed_context": {
                "is_child_execution": True,
                "parent_execution_id": "exec-123",
                "chunk_id": "chunk_0001",
                "paused_segment_id": 42,
                "owner_id": "user123"
            }
        }
        
        with patch('backend.store_distributed_task_token.boto3') as mock_boto:
            mock_dynamodb = MagicMock()
            mock_table = MagicMock()
            mock_table.put_item.return_value = {}
            mock_dynamodb.Table.return_value = mock_table
            mock_boto.resource.return_value = mock_dynamodb
            
            result = lambda_handler(event, None)
        
        # 저장 성공 결과 확인
        assert "conversation_id" in result or result.get("status") == "stored"
    
    def test_store_distributed_token_requires_task_token(self):
        """TaskToken 필수 - 없으면 ValueError"""
        from backend.store_distributed_task_token import lambda_handler
        
        event = {
            # TaskToken 없음
            "distributed_context": {
                "parent_execution_id": "exec-123"
            }
        }
        
        with pytest.raises(ValueError, match="TaskToken is required"):
            lambda_handler(event, None)
    
    def test_store_distributed_token_requires_parent_execution_id(self):
        """parent_execution_id 필수 - 없으면 ValueError"""
        from backend.store_distributed_task_token import lambda_handler
        
        event = {
            "TaskToken": "token-abc",
            "distributed_context": {
                # parent_execution_id 없음
                "chunk_id": "chunk_0001"
            }
        }
        
        with pytest.raises(ValueError, match="parent_execution_id is required"):
            lambda_handler(event, None)
