"""
Common utility module
Common functions to eliminate duplication in backend code and increase reusability

v2.0 Cold Start Optimization:
    - Lazy Import 패턴 적용으로 모듈 레벨 초기화 최소화
    - 실제 사용 시점에 import되도록 __getattr__ 활용
    - 호환성을 위해 기존 import 스타일도 지원
"""

import importlib
from typing import TYPE_CHECKING

# TYPE_CHECKING 블록: 타입 힌트용으로만 import (런타임에는 실행 안 됨)
if TYPE_CHECKING:
    from src.common.websocket_utils import (
        get_connections_for_owner,
        send_to_connection,
        broadcast_to_connections,
        notify_user,
        cleanup_stale_connection
    )
    from src.common.auth_utils import (
        validate_token,
        extract_owner_id_from_token,
        extract_owner_id_from_event,
        require_authentication
    )
    from src.common.aws_clients import (
        get_dynamodb_resource,
        get_dynamodb_table,
        get_s3_client,
        get_stepfunctions_client,
        get_ssm_client,
        get_ecs_client,
        get_lambda_client,
        get_kinesis_client
    )
    from src.common.exceptions import (
        BaseAnalemmaError,
        ExecutionNotFound,
        ExecutionForbidden,
        ExecutionAlreadyExists,
        WorkflowNotFound,
        WorkflowValidationError,
        AuthenticationError,
        AuthorizationError,
        TokenExpiredError,
        InvalidTokenError,
        ValidationError,
        MissingRequiredFieldError,
        ExternalServiceError,
        LLMServiceError,
        S3OperationError,
        RateLimitExceededError,
        QuotaExceededError
    )
    from src.common.json_utils import (
        DecimalEncoder,
        convert_decimals,
        convert_to_dynamodb_format,
        dumps_decimal
    )
    from src.common.http_utils import (
        get_cors_headers,
        get_json_headers,
        JSON_HEADERS,
        CORS_HEADERS,
        build_response,
        success_response,
        created_response,
        bad_request_response,
        unauthorized_response,
        forbidden_response,
        not_found_response,
        internal_error_response
    )
    from src.common.logging_utils import (
        get_logger,
        get_tracer,
        get_metrics,
        log_execution_context,
        log_external_service_call,
        log_business_event,
        log_security_event,
        log_workflow_event,
        log_execution_event
    )
    from src.common.error_handlers import (
        handle_dynamodb_error,
        handle_s3_error,
        handle_stepfunctions_error,
        handle_bedrock_error,
        handle_llm_api_error,
        handle_network_error,
        safe_external_call,
        handle_lambda_error
    )
    from src.common.constants import (
        TTLConfig,
        QuotaLimits,
        ModelPricing,
        HTTPStatusCodes,
        RetryConfig,
        WorkflowConfig,
        LoggingConfig,
        SecurityConfig,
        EnvironmentVariables,
        DynamoDBConfig,
        get_env_var,
        get_table_name,
        is_mock_mode,
        get_stage_name,
        get_log_level,
        get_inline_threshold,
        get_messages_window
    )


# ============================================================
# 🚀 Lazy Import 매핑 (Cold Start 최적화)
# ============================================================

# 속성명 -> (모듈 경로, 실제 속성명) 매핑
_LAZY_IMPORT_MAP = {
    # WebSocket utilities
    'get_connections_for_owner': ('src.common.websocket_utils', 'get_connections_for_owner'),
    'send_to_connection': ('src.common.websocket_utils', 'send_to_connection'),
    'broadcast_to_connections': ('src.common.websocket_utils', 'broadcast_to_connections'),
    'notify_user': ('src.common.websocket_utils', 'notify_user'),
    'cleanup_stale_connection': ('src.common.websocket_utils', 'cleanup_stale_connection'),

    # Authentication utilities
    'validate_token': ('src.common.auth_utils', 'validate_token'),
    'extract_owner_id_from_token': ('src.common.auth_utils', 'extract_owner_id_from_token'),
    'extract_owner_id_from_event': ('src.common.auth_utils', 'extract_owner_id_from_event'),
    'require_authentication': ('src.common.auth_utils', 'require_authentication'),
    
    # AWS Client utilities
    'get_dynamodb_resource': ('src.common.aws_clients', 'get_dynamodb_resource'),
    'get_dynamodb_table': ('src.common.aws_clients', 'get_dynamodb_table'),
    'get_s3_client': ('src.common.aws_clients', 'get_s3_client'),
    'get_stepfunctions_client': ('src.common.aws_clients', 'get_stepfunctions_client'),
    'get_ssm_client': ('src.common.aws_clients', 'get_ssm_client'),
    'get_ecs_client': ('src.common.aws_clients', 'get_ecs_client'),
    'get_lambda_client': ('src.common.aws_clients', 'get_lambda_client'),
    'get_kinesis_client': ('src.common.aws_clients', 'get_kinesis_client'),
    
    # Exception classes
    'BaseAnalemmaError': ('src.common.exceptions', 'BaseAnalemmaError'),
    'ExecutionNotFound': ('src.common.exceptions', 'ExecutionNotFound'),
    'ExecutionForbidden': ('src.common.exceptions', 'ExecutionForbidden'),
    'ExecutionAlreadyExists': ('src.common.exceptions', 'ExecutionAlreadyExists'),
    'WorkflowNotFound': ('src.common.exceptions', 'WorkflowNotFound'),
    'WorkflowValidationError': ('src.common.exceptions', 'WorkflowValidationError'),
    'AuthenticationError': ('src.common.exceptions', 'AuthenticationError'),
    'AuthorizationError': ('src.common.exceptions', 'AuthorizationError'),
    'TokenExpiredError': ('src.common.exceptions', 'TokenExpiredError'),
    'InvalidTokenError': ('src.common.exceptions', 'InvalidTokenError'),
    'ValidationError': ('src.common.exceptions', 'ValidationError'),
    'MissingRequiredFieldError': ('src.common.exceptions', 'MissingRequiredFieldError'),
    'ExternalServiceError': ('src.common.exceptions', 'ExternalServiceError'),
    'LLMServiceError': ('src.common.exceptions', 'LLMServiceError'),
    'S3OperationError': ('src.common.exceptions', 'S3OperationError'),
    'RateLimitExceededError': ('src.common.exceptions', 'RateLimitExceededError'),
    'QuotaExceededError': ('src.common.exceptions', 'QuotaExceededError'),
    
    # JSON utilities
    'DecimalEncoder': ('src.common.json_utils', 'DecimalEncoder'),
    'convert_decimals': ('src.common.json_utils', 'convert_decimals'),
    'convert_to_dynamodb_format': ('src.common.json_utils', 'convert_to_dynamodb_format'),
    'dumps_decimal': ('src.common.json_utils', 'dumps_decimal'),
    
    # HTTP utilities
    'get_cors_headers': ('src.common.http_utils', 'get_cors_headers'),
    'get_json_headers': ('src.common.http_utils', 'get_json_headers'),
    'JSON_HEADERS': ('src.common.http_utils', 'JSON_HEADERS'),
    'CORS_HEADERS': ('src.common.http_utils', 'CORS_HEADERS'),
    'build_response': ('src.common.http_utils', 'build_response'),
    'success_response': ('src.common.http_utils', 'success_response'),
    'created_response': ('src.common.http_utils', 'created_response'),
    'bad_request_response': ('src.common.http_utils', 'bad_request_response'),
    'unauthorized_response': ('src.common.http_utils', 'unauthorized_response'),
    'forbidden_response': ('src.common.http_utils', 'forbidden_response'),
    'not_found_response': ('src.common.http_utils', 'not_found_response'),
    'internal_error_response': ('src.common.http_utils', 'internal_error_response'),
    
    # Logging utilities
    'get_logger': ('src.common.logging_utils', 'get_logger'),
    'get_tracer': ('src.common.logging_utils', 'get_tracer'),
    'get_metrics': ('src.common.logging_utils', 'get_metrics'),
    'log_execution_context': ('src.common.logging_utils', 'log_execution_context'),
    'log_external_service_call': ('src.common.logging_utils', 'log_external_service_call'),
    'log_business_event': ('src.common.logging_utils', 'log_business_event'),
    'log_security_event': ('src.common.logging_utils', 'log_security_event'),
    'log_workflow_event': ('src.common.logging_utils', 'log_workflow_event'),
    'log_execution_event': ('src.common.logging_utils', 'log_execution_event'),
    
    # Error handling utilities
    'handle_dynamodb_error': ('src.common.error_handlers', 'handle_dynamodb_error'),
    'handle_s3_error': ('src.common.error_handlers', 'handle_s3_error'),
    'handle_stepfunctions_error': ('src.common.error_handlers', 'handle_stepfunctions_error'),
    'handle_bedrock_error': ('src.common.error_handlers', 'handle_bedrock_error'),
    'handle_llm_api_error': ('src.common.error_handlers', 'handle_llm_api_error'),
    'handle_network_error': ('src.common.error_handlers', 'handle_network_error'),
    'safe_external_call': ('src.common.error_handlers', 'safe_external_call'),
    'handle_lambda_error': ('src.common.error_handlers', 'handle_lambda_error'),
    
    # Constants and configuration
    'TTLConfig': ('src.common.constants', 'TTLConfig'),
    'QuotaLimits': ('src.common.constants', 'QuotaLimits'),
    'ModelPricing': ('src.common.constants', 'ModelPricing'),
    'HTTPStatusCodes': ('src.common.constants', 'HTTPStatusCodes'),
    'RetryConfig': ('src.common.constants', 'RetryConfig'),
    'WorkflowConfig': ('src.common.constants', 'WorkflowConfig'),
    'LoggingConfig': ('src.common.constants', 'LoggingConfig'),
    'SecurityConfig': ('src.common.constants', 'SecurityConfig'),
    'EnvironmentVariables': ('src.common.constants', 'EnvironmentVariables'),
    'DynamoDBConfig': ('src.common.constants', 'DynamoDBConfig'),
    'get_env_var': ('src.common.constants', 'get_env_var'),
    'get_table_name': ('src.common.constants', 'get_table_name'),
    'is_mock_mode': ('src.common.constants', 'is_mock_mode'),
    'get_stage_name': ('src.common.constants', 'get_stage_name'),
    'get_log_level': ('src.common.constants', 'get_log_level'),
    'get_inline_threshold': ('src.common.constants', 'get_inline_threshold'),
    'get_messages_window': ('src.common.constants', 'get_messages_window'),
}

# 로드된 속성 캐시
_loaded_attrs = {}


def __getattr__(name: str):
    """
    Lazy Import 구현: 속성 접근 시점에 실제 import 수행
    
    이 방식은 Python 3.7+의 모듈 레벨 __getattr__을 활용하여
    `from src.common import get_logger` 형태의 import를 지원하면서도
    실제 사용 시점까지 로딩을 지연시킵니다.
    """
    if name in _loaded_attrs:
        return _loaded_attrs[name]
    
    if name in _LAZY_IMPORT_MAP:
        module_path, attr_name = _LAZY_IMPORT_MAP[name]
        try:
            module = importlib.import_module(module_path)
            attr = getattr(module, attr_name)
            _loaded_attrs[name] = attr
            return attr
        except (ImportError, AttributeError) as e:
            raise AttributeError(f"Cannot import '{name}' from common module: {e}")
    
    raise AttributeError(f"module 'src.common' has no attribute '{name}'")


def __dir__():
    """dir() 지원: 사용 가능한 모든 속성 반환"""
    return list(_LAZY_IMPORT_MAP.keys()) + ['__getattr__', '__dir__']


__all__ = list(_LAZY_IMPORT_MAP.keys())
