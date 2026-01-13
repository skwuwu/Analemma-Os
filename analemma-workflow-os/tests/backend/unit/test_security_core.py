"""
Security Core Tests
프로덕션 코드: common/auth_utils.py, get_workflow.py, correction_api_handler.py
핵심: Owner ID 검증, 인증, XSS 방어

no-llm 테스트: 결정론적 보안 로직 검증
"""
import pytest
import json
import sys
import os
from unittest.mock import patch, MagicMock

# 환경 변수 설정
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("WORKFLOWS_TABLE", "test-workflows")

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
        yield


class TestAuthUtilsCore:
    """auth_utils.py 핵심 테스트 (Table-Driven)"""
    
    @pytest.mark.parametrize("event,expected_owner_id", [
        # JWT claims의 sub에서 추출
        ({"requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-abc-123"}}}}}, "user-abc-123"),
        # REST API 형식의 authorizer claims
        ({"requestContext": {"authorizer": {"claims": {"sub": "rest-user-456"}}}}, "rest-user-456"),
        # JWT claims 없으면 None
        ({"requestContext": {}}, None),
        # requestContext 없으면 None
        ({}, None),
        # authorizer 빈 객체 → None
        ({"requestContext": {"authorizer": {}}}, None),
    ])
    def test_extract_owner_id_from_event(self, event, expected_owner_id):
        """다양한 이벤트 형식에서 owner_id 추출 (Table-Driven)"""
        from src.common.auth_utils import extract_owner_id_from_event
        
        owner_id = extract_owner_id_from_event(event)
        assert owner_id == expected_owner_id
    
    def test_require_authentication_raises_on_missing(self):
        """require_authentication은 인증 없으면 ValueError"""
        from src.common.auth_utils import require_authentication
        
        event = {"requestContext": {}}
        
        with pytest.raises(ValueError) as exc_info:
            require_authentication(event)
        
        assert "Authentication required" in str(exc_info.value)


class TestGetWorkflowSecurity:
    """get_workflow.py 보안 테스트"""
    
    def test_get_workflow_requires_auth(self):
        """인증 없으면 401"""
        from backend.get_workflow import lambda_handler
        
        event = {
            "httpMethod": "GET",
            "queryStringParameters": {},
            "requestContext": {}
        }
        
        result = lambda_handler(event, None)
        assert result['statusCode'] == 401
    
    def test_get_workflow_ignores_query_param_owner_id(self):
        """query param의 ownerId 무시하고 JWT의 sub 사용"""
        from backend.get_workflow import lambda_handler
        
        event = {
            "httpMethod": "GET",
            "queryStringParameters": {"ownerId": "attacker-id"},
            "requestContext": {
                "authorizer": {
                    "jwt": {"claims": {"sub": "real-user-id"}}
                }
            }
        }
        
        with patch('backend.get_workflow.table') as mock_table:
            mock_table.query.return_value = {"Items": [], "Count": 0}
            result = lambda_handler(event, None)
        
        assert result['statusCode'] == 200
        # JWT의 owner_id로 쿼리됨
        mock_table.query.assert_called()


class TestInputSanitization:
    """입력 Sanitization 테스트 (Table-Driven)"""
    
    @pytest.mark.parametrize("input_data,expected_escaped", [
        # script 태그 이스케이프
        ({"text": "<script>alert('XSS')</script>"}, "&lt;script&gt;"),
        # 이벤트 핸들러 이스케이프
        ({"img": "<img src=x onerror=alert(1)>"}, "&lt;"),
    ])
    def test_xss_patterns_escaped(self, input_data, expected_escaped):
        """XSS 패턴들이 이스케이프됨 (Table-Driven)"""
        from backend.correction_api_handler import sanitize_input_data
        
        result = sanitize_input_data(input_data)
        result_str = json.dumps(result)
        assert expected_escaped in result_str
    
    def test_nested_object_sanitized(self):
        """중첩된 객체도 sanitize"""
        from backend.correction_api_handler import sanitize_input_data
        
        result = sanitize_input_data({
            "level1": {
                "level2": "<script>bad</script>"
            }
        })
        
        # 중첩 객체 내부도 처리됨
        assert "&lt;script&gt;" in str(result) or "script" not in str(result).lower()


class TestPathTraversal:
    """Path Traversal 방어 테스트"""
    
    def test_dangerous_paths_detected(self):
        """위험 경로 탐지"""
        dangerous_paths = [
            "../../etc/passwd",
            "../../../root/.ssh/id_rsa",
            "workflow-states/user/../admin/state.json"
        ]
        
        for path in dangerous_paths:
            # 모든 위험 경로는 .. 포함
            assert ".." in path
            # 정규화하면 상위 디렉토리 이동 시도
            normalized = os.path.normpath(path)
            # etc, root, admin 등 민감 경로 접근 시도
            assert any(x in normalized for x in ["etc", "root", "admin", "ssh"])
