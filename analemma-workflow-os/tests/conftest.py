"""
pytest configuration for backend tests.
Located outside backend/ to avoid triggering deployments.

🚨 Import 경로 우선순위:
1. backend/src - common 패키지의 Source of Truth
2. backend/apps/backend - backend 패키지 (Lambda 메인 코드)
3. backend/apps/backend/backend - Lambda 핸들러 직접 import
"""
import sys
import os
from pathlib import Path

# 프로젝트 루트 경로
PROJECT_ROOT = Path(__file__).parent.parent

# 백엔드 소스 경로들
BACKEND_SRC = PROJECT_ROOT / "backend" / "src"
BACKEND_APPS = PROJECT_ROOT / "backend" / "apps" / "backend"
BACKEND_HANDLERS = PROJECT_ROOT / "backend" / "apps" / "backend" / "backend"

def pytest_configure(config):
    # 1. backend/src 최우선 등록 (common 패키지)
    if str(BACKEND_SRC) not in sys.path:
        sys.path.insert(0, str(BACKEND_SRC))
    
    # 2. backend/apps/backend 등록 (backend 패키지)
    if str(BACKEND_APPS) not in sys.path:
        sys.path.insert(1, str(BACKEND_APPS))
    
    # 3. 핸들러 경로 등록
    if str(BACKEND_HANDLERS) not in sys.path:
        sys.path.insert(2, str(BACKEND_HANDLERS))

# MOCK_MODE 기본 활성화
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import pytest
import json

@pytest.fixture(scope="session")
def large_json_payload():
    """
    Generates a 50MB+ dummy JSON payload for performance testing.
    Scoped to session to avoid overhead.
    """
    size_mb = 50
    # ~1KB per item approx
    item = {"id": 1, "data": "x" * 1000}
    count = size_mb * 1024  # 50 * 1024 items * 1KB ~= 50MB
    
    return json.dumps([item for _ in range(count)])
