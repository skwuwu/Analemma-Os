# Ensure boto3.client attribute exists for tests that monkeypatch it.
# This file is automatically imported by pytest during collection.
import sys
import os
from pathlib import Path

# 프로젝트 루트 및 백엔드 소스 경로 추가
# tests/backend/unit/conftest.py -> 3레벨 상위가 프로젝트 루트
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent  # analemma-fullstack
BACKEND_APPS = PROJECT_ROOT / "backend" / "apps" / "backend"
BACKEND_SRC = PROJECT_ROOT / "backend" / "src"

# 🚨 BACKEND_APPS만 추가 (backend/src/common과 충돌 방지)
# backend/apps/backend/common이 우선 로드되어야 함
path_str = str(BACKEND_APPS)
if path_str not in sys.path:
    sys.path.insert(0, path_str)

# backend/src 추가 (lambda 함수들)
src_path_str = str(BACKEND_SRC)
if src_path_str not in sys.path:
    sys.path.insert(0, src_path_str)

# 프로젝트 루트 추가
root_path_str = str(PROJECT_ROOT)
if root_path_str not in sys.path:
    sys.path.insert(0, root_path_str)

# MOCK_MODE 기본 활성화
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

# PYTHONPATH 설정
current_pythonpath = os.environ.get('PYTHONPATH', '')
new_paths = [str(BACKEND_SRC), str(PROJECT_ROOT)]
for path in new_paths:
    if path not in current_pythonpath:
        current_pythonpath = f"{path}:{current_pythonpath}"
os.environ['PYTHONPATH'] = current_pythonpath

import boto3
if not hasattr(boto3, 'client'):
    def _fake_client(*args, **kwargs):
        raise RuntimeError('boto3.client is unavailable in this environment')
    boto3.client = _fake_client
if not hasattr(boto3, 'resource'):
    def _fake_resource(*args, **kwargs):
        raise RuntimeError('boto3.resource is unavailable in this environment')
    boto3.resource = _fake_resource

# Provide minimal stand-ins for boto3.dynamodb.conditions.Key/Attr in test envs
try:
    from boto3.dynamodb.conditions import Key, Attr  # type: ignore
except Exception:
    class Key:
        def __init__(self, name):
            self.name = name
        def eq(self, v):
            return ("eq", self.name, v)

    class Attr:
        def __init__(self, name):
            self.name = name
        def eq(self, v):
            return ("attr_eq", self.name, v)
