"""
Resume System Core Tests
프로덕션 코드: resume_handler.py, resume_chunk_processing.py, store_task_token.py
핵심: HITP 토큰 저장, 상태 복원, 청크 재개

no-llm 테스트: 결정론적 재개 로직 검증
"""
import pytest
import json
import sys
import os
from unittest.mock import patch, MagicMock

# 환경 변수 설정
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("TASK_TOKENS_TABLE", "test-task-tokens")

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
            TableName='test-task-tokens',
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


class TestResumeHandlerCore:
    """resume_handler.py 핵심 테스트"""
    
    def test_resume_requires_auth(self):
        """인증 없으면 401"""
        from backend.resume_handler import lambda_handler
        
        event = {
            "body": json.dumps({"taskToken": "token123"}),
            "headers": {},
            "requestContext": {}
        }
        
        result = lambda_handler(event, None)
        assert result['statusCode'] == 401
    
    def test_resume_requires_task_token(self):
        """taskToken 필수"""
        from backend.resume_handler import lambda_handler
        
        event = {
            "body": json.dumps({}),  # taskToken 없음
            "headers": {"Authorization": "Bearer test"},
            "requestContext": {
                "authorizer": {"jwt": {"claims": {"sub": "user123"}}}
            }
        }
        
        result = lambda_handler(event, None)
        # taskToken 누락 시 400
        assert result['statusCode'] in [400, 422]


class TestStoreTaskTokenCore:
    """store_task_token.py 핵심 테스트"""
    
    def test_store_task_token_success(self):
        """토큰 저장 성공 - 프로덕션 스키마 준수 (taskToken, conversation_id, ownerId 필수)"""
        from backend.store_task_token import lambda_handler
        
        # 프로덕션 코드 필수 필드: taskToken, conversation_id, ownerId
        event = {
            "TaskToken": "test-token-abc",
            "taskToken": "test-token-abc",  # 둘 다 지원
            "conversation_id": "conv-12345",
            "ownerId": "user123",
            "execution_id": "exec-001",
            "segment_to_run": 0,
            "workflow_config": {"name": "test-workflow"}
        }
        
        with patch('backend.store_task_token.table') as mock_table:
            mock_table.put_item.return_value = {}
            
            result = lambda_handler(event, None)
        
        # 저장 성공 - put_item 호출됨
        mock_table.put_item.assert_called_once()
    
    def test_store_task_token_requires_all_fields(self):
        """taskToken, conversation_id, ownerId 모두 필수"""
        from backend.store_task_token import lambda_handler
        
        # ownerId 누락
        event = {
            "taskToken": "test-token-abc",
            "conversation_id": "conv-12345"
            # ownerId 없음
        }
        
        with pytest.raises(ValueError, match="Missing TaskToken, conversation_id or ownerId"):
            lambda_handler(event, None)


class TestResumeChunkProcessingCore:
    """resume_chunk_processing.py 핵심 테스트"""
    
    def test_idempotency_key_generation(self):
        """멱등성 키 생성 로직"""
        from backend.resume_chunk_processing import _validate_idempotency_safety
        
        result = _validate_idempotency_safety(
            execution_id="exec-123",
            chunk_id="chunk-001",
            segment_id=5,
            base_idempotency_key="exec-123#chunk#chunk-001"
        )
        
        assert result["is_safe"] == True
        assert "resumed_segment_5" in result["generated_key"]
    
    def test_idempotency_key_too_long_unsafe(self):
        """너무 긴 키는 unsafe (DynamoDB 2048자 제한)"""
        from backend.resume_chunk_processing import _validate_idempotency_safety
        
        # generated_key = base_key + "#resumed_segment_5" (약 18자)
        # 2048자를 넘으려면 base_key가 2030자 이상이어야 함
        long_base = "x" * 2050  # 2048 + 여유분
        result = _validate_idempotency_safety(
            execution_id="exec-123",
            chunk_id="chunk-001",
            segment_id=5,
            base_idempotency_key=long_base
        )
        
        # 2048자 초과 시 unsafe
        assert result["is_safe"] == False
        assert any("too long" in w.lower() for w in result["warnings"])
    
    def test_double_resumption_warning(self):
        """이미 #resumed# 포함된 키에 경고"""
        from backend.resume_chunk_processing import _validate_idempotency_safety
        
        result = _validate_idempotency_safety(
            execution_id="exec-456",
            chunk_id="chunk-002",
            segment_id=10,
            base_idempotency_key="exec-456#chunk#chunk-002#resumed#previous"
        )
        
        # double resumption 경고
        assert any("resumed" in w.lower() for w in result["warnings"])
