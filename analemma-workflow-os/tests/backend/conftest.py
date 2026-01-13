import os
import pytest
from unittest.mock import MagicMock
import sys

# 🚨 이 파일은 모든 백엔드 테스트(unit, integration, security)의 전역 설정을 담당합니다.

@pytest.fixture(scope="session", autouse=True)
def setup_global_test_environment():
    """테스트 세션 시작 시 전역 환경 변수 및 모킹 설정"""
    
    # 0. 기존 AWS_PROFILE 제거 (SSO 세션 충돌 방지 핵심)
    # AWS_PROFILE이 설정되어 있으면 Boto3가 dummy credentials를 무시하고 SSO 갱신을 시도할 수 있음
    if "AWS_PROFILE" in os.environ:
        del os.environ["AWS_PROFILE"]
    
    # 1. AWS SSO 세션 충돌 및 실제 AWS 호출 방지를 위한 더미 자격 증명 강제 설정
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["AWS_REGION"] = "us-east-1"
    
    # 2. MOCK_MODE 활성화 (프로덕션 코드 내 분기 처리용)
    os.environ["MOCK_MODE"] = "true"
    
    # 3. 필수 테이블명 등 기본값 설정 (이미 설정되어 있으면 유지)
    os.environ.setdefault("WORKFLOWS_TABLE", "test-workflows")
    os.environ.setdefault("EXECUTIONS_TABLE", "test-executions")
    os.environ.setdefault("IDEMPOTENCY_TABLE", "test-idempotency")
    os.environ.setdefault("NODE_STATS_TABLE", "test-node-stats")
    
    # 4. OpenAI 모킹 (모든 테스트에 공통 적용)
    if 'openai' not in sys.modules:
        sys.modules['openai'] = MagicMock()
    
    yield
