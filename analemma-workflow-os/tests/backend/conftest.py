import os
import pytest
from unittest.mock import MagicMock
import sys

# 🚨 This file handles global configuration for all backend tests (unit, integration, security).

@pytest.fixture(scope="session", autouse=True)
def setup_global_test_environment(request):
    """Set up global environment variables and mocking when test session starts.

    Skipped when E2E tests are collected (they need real AWS credentials).
    """
    # Detect if we're running E2E tests — they need real AWS credentials
    e2e_dirs = {"e2e"}
    collected_dirs = set()
    for item in request.session.items:
        parts = item.nodeid.split("/")
        for part in parts:
            collected_dirs.add(part)

    if e2e_dirs & collected_dirs:
        # E2E tests present — skip dummy credential injection
        yield
        return

    # 0. Remove existing AWS_PROFILE (key to prevent SSO session conflicts)
    # If AWS_PROFILE is set, Boto3 may ignore dummy credentials and attempt SSO renewal
    if "AWS_PROFILE" in os.environ:
        del os.environ["AWS_PROFILE"]

    # 1. Force set dummy credentials to prevent AWS SSO session conflicts and actual AWS calls
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
    os.environ["AWS_REGION"] = "us-east-1"

    # 2. Enable MOCK_MODE (for branching in production code)
    os.environ["MOCK_MODE"] = "true"

    # 3. Set default values for required table names (maintain if already set)
    os.environ.setdefault("WORKFLOWS_TABLE", "test-workflows")
    os.environ.setdefault("EXECUTIONS_TABLE", "test-executions")
    os.environ.setdefault("IDEMPOTENCY_TABLE", "test-idempotency")
    os.environ.setdefault("NODE_STATS_TABLE", "test-node-stats")

    # 4. OpenAI mocking (applied to all tests)
    if 'openai' not in sys.modules:
        sys.modules['openai'] = MagicMock()

    yield
