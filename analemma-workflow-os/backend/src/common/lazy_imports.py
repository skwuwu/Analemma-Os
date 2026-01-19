"""
Lazy Import Utility Module

Lambda Cold Start 최적화를 위한 지연 로딩(Lazy Loading) 유틸리티.

문제점:
    - Python은 import 시점에 모듈 전체를 로드하고 초기화합니다.
    - 무거운 패키지(google-cloud, boto3, langchain 등)는 수 초의 로딩 시간이 필요합니다.
    - 모든 Lambda에서 모든 패키지가 필요한 것은 아닙니다.

해결책:
    - 필요한 시점에만 import하는 Lazy Loading 패턴을 적용합니다.
    - 한번 로드된 모듈은 캐싱하여 재사용합니다.

사용법:
    # 일반적인 import 대신:
    # from aws_lambda_powertools import Logger, Tracer  # ❌ 즉시 로드
    
    # Lazy import 사용:
    from src.common.lazy_imports import get_powertools_logger, get_tracer  # ✅ 지연 로드

v2.0 Cold Start Optimization
"""

import importlib
import functools
from typing import Any, Optional, Dict, TypeVar, Callable
import threading

T = TypeVar('T')

# 모듈 캐시 (Lambda warm start에서 재사용)
_module_cache: Dict[str, Any] = {}
_instance_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()


def lazy_import(module_path: str, attribute: Optional[str] = None) -> Any:
    """
    모듈을 지연 로딩합니다.
    
    Args:
        module_path: 모듈 경로 (예: 'aws_lambda_powertools')
        attribute: 가져올 속성 (예: 'Logger')
    
    Returns:
        로드된 모듈 또는 속성
    
    Example:
        Logger = lazy_import('aws_lambda_powertools', 'Logger')
        boto3 = lazy_import('boto3')
    """
    cache_key = f"{module_path}:{attribute or '__module__'}"
    
    with _cache_lock:
        if cache_key in _module_cache:
            return _module_cache[cache_key]
        
        try:
            module = importlib.import_module(module_path)
            result = getattr(module, attribute) if attribute else module
            _module_cache[cache_key] = result
            return result
        except ImportError as e:
            raise ImportError(f"Failed to lazy import {module_path}.{attribute}: {e}")


def lazy_singleton(factory: Callable[[], T]) -> Callable[[], T]:
    """
    싱글톤 인스턴스를 지연 생성하는 데코레이터
    
    Example:
        @lazy_singleton
        def get_s3_client():
            import boto3
            return boto3.client('s3')
    """
    cache_key = factory.__name__
    
    @functools.wraps(factory)
    def wrapper() -> T:
        with _cache_lock:
            if cache_key not in _instance_cache:
                _instance_cache[cache_key] = factory()
            return _instance_cache[cache_key]
    
    return wrapper


# ============================================================
# 🔧 AWS Lambda Powertools (Lazy)
# ============================================================

@lazy_singleton
def get_powertools_logger():
    """AWS Lambda Powertools Logger를 지연 로드합니다."""
    import os
    Logger = lazy_import('aws_lambda_powertools', 'Logger')
    return Logger(
        service=os.getenv("AWS_LAMBDA_FUNCTION_NAME", "analemma-backend"),
        level=os.getenv("LOG_LEVEL", "INFO")
    )


@lazy_singleton
def get_tracer():
    """AWS Lambda Powertools Tracer를 지연 로드합니다."""
    Tracer = lazy_import('aws_lambda_powertools', 'Tracer')
    return Tracer()


@lazy_singleton
def get_metrics():
    """AWS Lambda Powertools Metrics를 지연 로드합니다."""
    Metrics = lazy_import('aws_lambda_powertools', 'Metrics')
    return Metrics()


# ============================================================
# 🔧 Boto3 Clients (Lazy)
# ============================================================

@lazy_singleton
def get_dynamodb_resource():
    """DynamoDB Resource를 지연 로드합니다."""
    boto3 = lazy_import('boto3')
    return boto3.resource('dynamodb')


@lazy_singleton
def get_s3_client():
    """S3 Client를 지연 로드합니다."""
    boto3 = lazy_import('boto3')
    return boto3.client('s3')


@lazy_singleton
def get_stepfunctions_client():
    """Step Functions Client를 지연 로드합니다."""
    boto3 = lazy_import('boto3')
    return boto3.client('stepfunctions')


@lazy_singleton
def get_ssm_client():
    """SSM Client를 지연 로드합니다."""
    boto3 = lazy_import('boto3')
    return boto3.client('ssm')


@lazy_singleton
def get_lambda_client():
    """Lambda Client를 지연 로드합니다."""
    boto3 = lazy_import('boto3')
    return boto3.client('lambda')


@lazy_singleton
def get_ecs_client():
    """ECS Client를 지연 로드합니다."""
    boto3 = lazy_import('boto3')
    return boto3.client('ecs')


@lazy_singleton
def get_kinesis_client():
    """Kinesis Client를 지연 로드합니다."""
    boto3 = lazy_import('boto3')
    return boto3.client('kinesis')


def get_dynamodb_table(table_name: str):
    """DynamoDB Table을 지연 로드합니다."""
    resource = get_dynamodb_resource()
    return resource.Table(table_name)


# ============================================================
# 🔧 Heavy ML/AI Packages (Lazy)
# ============================================================

def get_google_genai():
    """Google Generative AI를 지연 로드합니다."""
    return lazy_import('google.generativeai')


def get_vertexai():
    """Vertex AI를 지연 로드합니다."""
    return lazy_import('vertexai')


def get_aiplatform():
    """Google Cloud AI Platform을 지연 로드합니다."""
    return lazy_import('google.cloud.aiplatform')


def get_langgraph():
    """LangGraph를 지연 로드합니다."""
    return lazy_import('langgraph')


def get_openai():
    """OpenAI를 지연 로드합니다."""
    return lazy_import('openai')


def get_anthropic():
    """Anthropic를 지연 로드합니다."""
    return lazy_import('anthropic')


# ============================================================
# 🔧 Conditional Import Helpers
# ============================================================

def import_if_available(module_path: str, fallback: Any = None) -> Any:
    """
    모듈이 있으면 로드하고, 없으면 fallback을 반환합니다.
    
    Example:
        dlp = import_if_available('google.cloud.dlp_v2', fallback=None)
        if dlp:
            client = dlp.DlpServiceClient()
    """
    try:
        return lazy_import(module_path)
    except ImportError:
        return fallback


def require_import(module_path: str, package_name: str) -> Any:
    """
    모듈이 없으면 설치 안내와 함께 에러를 발생시킵니다.
    
    Example:
        genai = require_import('google.generativeai', 'google-generativeai')
    """
    try:
        return lazy_import(module_path)
    except ImportError:
        raise ImportError(
            f"Required package '{package_name}' is not installed. "
            f"Please add it to requirements.txt or the appropriate Lambda layer."
        )


# ============================================================
# 🔧 Import Timing Diagnostics
# ============================================================

_import_timings: Dict[str, float] = {}


def timed_import(module_path: str, attribute: Optional[str] = None) -> Any:
    """
    Import 시간을 측정하며 모듈을 로드합니다.
    디버깅/프로파일링 용도.
    """
    import time
    
    cache_key = f"{module_path}:{attribute or '__module__'}"
    
    if cache_key in _module_cache:
        return _module_cache[cache_key]
    
    start = time.perf_counter()
    result = lazy_import(module_path, attribute)
    elapsed_ms = (time.perf_counter() - start) * 1000
    
    _import_timings[cache_key] = elapsed_ms
    
    # 느린 import 경고 (100ms 이상)
    if elapsed_ms > 100:
        import logging
        logging.getLogger(__name__).warning(
            f"[SlowImport] {cache_key} took {elapsed_ms:.1f}ms"
        )
    
    return result


def get_import_timings() -> Dict[str, float]:
    """Import 타이밍 정보를 반환합니다."""
    return _import_timings.copy()


def clear_caches():
    """
    모든 캐시를 초기화합니다. 테스트 용도.
    """
    global _module_cache, _instance_cache, _import_timings
    with _cache_lock:
        _module_cache.clear()
        _instance_cache.clear()
        _import_timings.clear()
