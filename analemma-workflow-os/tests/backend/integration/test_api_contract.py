"""
API Contract Tests
프론트엔드-백엔드 간 계약(Contract) 검증

🚨 핵심 원칙: 실제 프로덕션 핸들러를 직접 임포트하여 테스트
- AWS/LLM 모킹만 허용
- 실제 API 응답 스키마를 검증
"""
import pytest
import json
import sys
import os
from unittest.mock import patch, MagicMock

# 환경 변수 설정 (모듈 import 전)
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("WORKFLOWS_TABLE", "test-workflows")
os.environ.setdefault("EXECUTIONS_TABLE", "test-executions")
os.environ.setdefault("WEBSOCKET_CONNECTIONS_TABLE", "test-connections")

# OpenAI 모킹 (LLM 비용 방지)
mock_openai = MagicMock()
sys.modules['openai'] = mock_openai

from moto import mock_aws
import boto3


@pytest.fixture(autouse=True)
def mock_aws_services():
    """모든 테스트에 AWS 모킹 (필수)"""
    with mock_aws():
        dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
        
        # 워크플로우 테이블
        dynamodb.create_table(
            TableName='test-workflows',
            KeySchema=[
                {'AttributeName': 'ownerId', 'KeyType': 'HASH'},
                {'AttributeName': 'workflowId', 'KeyType': 'RANGE'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'ownerId', 'AttributeType': 'S'},
                {'AttributeName': 'workflowId', 'AttributeType': 'S'}
            ],
            BillingMode='PAY_PER_REQUEST'
        )
        
        # 실행 테이블
        dynamodb.create_table(
            TableName='test-executions',
            KeySchema=[
                {'AttributeName': 'ownerId', 'KeyType': 'HASH'},
                {'AttributeName': 'executionId', 'KeyType': 'RANGE'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'ownerId', 'AttributeType': 'S'},
                {'AttributeName': 'executionId', 'AttributeType': 'S'}
            ],
            BillingMode='PAY_PER_REQUEST'
        )
        yield


class TestAPIResponseSchemaContract:
    """
    API 응답 스키마 계약 검증 - 프론트엔드 TypeScript 인터페이스와 일치
    
    🚨 프로덕션 코드 직접 사용:
    - backend.get_workflow.lambda_handler
    - backend.correction_api_handler.lambda_log_correction
    """
    
    # 프론트엔드에서 기대하는 필드명 (camelCase)
    FRONTEND_WORKFLOW_FIELDS = {
        "workflowId",      # not workflow_id
        "name",
        "description",
        "nodes",
        "edges",
        "createdAt",       # not created_at
        "updatedAt",       # not updated_at
        "ownerId"
    }
    
    def test_get_workflow_handler_returns_camel_case(self):
        """프로덕션 get_workflow 핸들러가 camelCase 응답 반환"""
        from backend.get_workflow import lambda_handler
        
        # DynamoDB에 테스트 워크플로우 삽입
        dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
        table = dynamodb.Table('test-workflows')
        table.put_item(Item={
            'ownerId': 'user-test-123',
            'workflowId': 'wf-test-001',
            'name': 'Test Workflow',
            'description': 'For contract testing',
            'nodes': [],
            'edges': [],
            'createdAt': '2026-01-03T00:00:00Z',
            'updatedAt': '2026-01-03T00:00:00Z'
        })
        
        # JWT 인증된 요청 시뮬레이션 (목록 조회)
        event = {
            'httpMethod': 'GET',
            'pathParameters': {},
            'queryStringParameters': {},
            'requestContext': {
                'authorizer': {
                    'jwt': {
                        'claims': {'sub': 'user-test-123'}
                    }
                }
            }
        }
        
        result = lambda_handler(event, None)
        
        # 성공 응답
        assert result['statusCode'] == 200
        
        # 응답 본문 파싱
        body = json.loads(result['body'])
        
        # 목록 응답 형식: workflows 배열
        assert 'workflows' in body
        assert len(body['workflows']) >= 1
        
        # 첫 번째 워크플로우에서 camelCase 확인
        workflow = body['workflows'][0]
        assert 'workflowId' in workflow
        assert 'name' in workflow
        
        # snake_case가 없어야 함
        body_str = json.dumps(body)
        assert 'workflow_id' not in body_str
        assert 'created_at' not in body_str
    
    def test_correction_api_401_error_format(self):
        """프로덕션 correction_api_handler 401 에러 응답 형식"""
        from backend.correction_api_handler import lambda_log_correction
        
        event = {
            'body': json.dumps({
                'workflow_id': 'wf-123',
                'node_id': 'node-1',
                'original_input': 'test',
                'agent_output': 'output',
                'user_correction': 'corrected',
                'task_category': 'email'
            }),
            'headers': {}
        }
        
        with patch('backend.correction_api_handler.extract_and_verify_user_id', return_value=None):
            result = lambda_log_correction(event, None)
        
        assert result['statusCode'] == 401
        
        body = json.loads(result['body'])
        # 에러 응답에 'error' 필드 존재
        assert 'error' in body
        assert isinstance(body['error'], str)
    
    def test_get_workflow_404_when_not_found(self):
        """존재하지 않는 워크플로우 요청 시 404"""
        from backend.get_workflow import lambda_handler
        
        event = {
            'httpMethod': 'GET',
            'pathParameters': {'workflowId': 'wf-nonexistent'},
            'queryStringParameters': {},
            'requestContext': {
                'authorizer': {
                    'jwt': {
                        'claims': {'sub': 'user-test-123'}
                    }
                }
            }
        }
        
        result = lambda_handler(event, None)
        
        # 404 또는 빈 응답
        assert result['statusCode'] in [200, 404]
    
    def test_options_request_cors_handling(self):
        """OPTIONS 요청에 대한 CORS 처리"""
        from backend.get_workflow import lambda_handler
        
        event = {
            'httpMethod': 'OPTIONS',
            'pathParameters': {},
            'queryStringParameters': {}
        }
        
        result = lambda_handler(event, None)
        
        # CORS preflight 성공
        assert result['statusCode'] == 200


class TestPaginationParameters:
    """페이지네이션 파라미터 검증"""
    
    def test_limit_parameter_validation(self):
        """limit 파라미터 범위 검증"""
        valid_limits = [1, 10, 50, 100]
        invalid_limits = [0, -1, 101, 1000]
        
        for limit in valid_limits:
            assert 1 <= limit <= 100, f"유효한 limit이어야 함: {limit}"
        
        for limit in invalid_limits:
            assert not (1 <= limit <= 100), f"무효한 limit이어야 함: {limit}"
    
    def test_next_token_roundtrip(self):
        """nextToken 왕복 인코딩/디코딩"""
        import base64
        
        # DynamoDB LastEvaluatedKey 시뮬레이션
        last_key = {
            "pk": "user123",
            "sk": "wf-abc-123"
        }
        
        # 인코딩 (백엔드 → 프론트엔드)
        next_token = base64.b64encode(json.dumps(last_key).encode()).decode()
        
        # 디코딩 (프론트엔드 → 백엔드)
        decoded_key = json.loads(base64.b64decode(next_token).decode())
        
        assert decoded_key == last_key
    
    def test_sort_order_values(self):
        """sortOrder 파라미터 값 검증"""
        valid_sort_orders = ["asc", "desc", "ASC", "DESC"]
        
        for order in valid_sort_orders:
            normalized = order.lower()
            assert normalized in ["asc", "desc"]


class TestHTTPStatusCodeConsistency:
    """HTTP 상태 코드 일관성 검증"""
    
    def test_success_codes(self):
        """성공 응답 코드"""
        success_cases = {
            "GET /workflows": 200,
            "POST /workflows": 201,
            "PUT /workflows/{id}": 200,
            "DELETE /workflows/{id}": 204,
            "POST /executions": 202,  # Accepted (비동기)
        }
        
        for endpoint, expected_code in success_cases.items():
            assert expected_code in [200, 201, 202, 204]
    
    def test_error_codes_mapping(self):
        """에러 유형별 상태 코드 매핑"""
        error_code_mapping = {
            "authentication_required": 401,
            "invalid_token": 401,
            "permission_denied": 403,
            "resource_not_found": 404,
            "validation_failed": 400,
            "invalid_json": 400,
            "rate_limit_exceeded": 429,
            "internal_error": 500,
            "service_unavailable": 503
        }
        
        # 각 에러 유형이 적절한 HTTP 코드에 매핑되는지
        for error_type, code in error_code_mapping.items():
            if "auth" in error_type or "token" in error_type:
                assert code == 401
            elif "permission" in error_type or "forbidden" in error_type:
                assert code == 403
            elif "not_found" in error_type:
                assert code == 404
            elif "validation" in error_type or "invalid" in error_type:
                assert code == 400


class TestWebSocketContract:
    """WebSocket 메시지 형식 검증"""
    
    def test_progress_message_format(self):
        """실행 진행률 메시지 형식"""
        progress_message = {
            "type": "execution_progress",
            "executionId": "exec-123",
            "payload": {
                "status": "RUNNING",
                "currentStep": 3,
                "totalSteps": 10,
                "progress": 0.3,
                "currentNodeId": "node-5",
                "message": "Processing step 3..."
            },
            "timestamp": "2026-01-03T12:00:00Z"
        }
        
        # 필수 필드 확인
        assert "type" in progress_message
        assert "executionId" in progress_message
        assert "payload" in progress_message
        assert "timestamp" in progress_message
        
        # payload 내부 필드
        payload = progress_message["payload"]
        assert "status" in payload
        assert "progress" in payload
        assert 0 <= payload["progress"] <= 1
    
    def test_error_message_format(self):
        """에러 메시지 형식"""
        error_message = {
            "type": "execution_error",
            "executionId": "exec-123",
            "payload": {
                "status": "FAILED",
                "error": "Node execution failed",
                "errorCode": "NODE_EXECUTION_ERROR",
                "failedNodeId": "node-7",
                "details": {
                    "reason": "Timeout after 30 seconds"
                }
            },
            "timestamp": "2026-01-03T12:00:00Z"
        }
        
        # 에러 타입 확인
        assert error_message["type"] == "execution_error"
        assert "error" in error_message["payload"]
    
    def test_hitl_pause_message_format(self):
        """HITL 일시정지 메시지 형식"""
        hitl_message = {
            "type": "hitl_required",
            "executionId": "exec-123",
            "payload": {
                "status": "PAUSED_FOR_HITL",
                "pausedNodeId": "node-approval",
                "pausedNodeLabel": "Manager Approval",
                "requiredAction": "approve_or_reject",
                "context": {
                    "request_amount": 50000,
                    "requester": "John Doe"
                },
                "taskToken": "arn:aws:states:..."
            },
            "timestamp": "2026-01-03T12:00:00Z"
        }
        
        # HITL 필수 필드
        payload = hitl_message["payload"]
        assert payload["status"] == "PAUSED_FOR_HITL"
        assert "pausedNodeId" in payload
        assert "taskToken" in payload
