"""
지능형 지침 증류기 - API 핸들러 (보안 및 견고성 강화)

수정 로그 수집 및 지침 관리 API

보안 및 견고성 개선사항:
1. JWT 검증의 실구현 및 미들웨어화 (JWKS 기반)
2. 비동기 핸들러의 런타임 정합성 (Lambda 호환)
3. 입력 데이터의 Sanitization 및 예외 처리
"""

import json
import logging
import os
import asyncio
import re
import html
import concurrent.futures
import threading
from typing import Dict, Any, Optional, Callable, Union
from datetime import datetime
from functools import wraps
from pydantic import BaseModel, Field, ValidationError, validator

# JWT 검증을 위한 라이브러리
try:
    import jwt
    from jwt import PyJWKClient
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False
    logging.warning("PyJWT not available - using mock authentication for development")

from .services.correction_service import CorrectionService
from .models.correction_log import TaskCategory, CorrectionLog, CorrectionType

# Pydantic 요청 스키마 정의 (개선사항: 스키마 기반 검증)
class CorrectionLogRequest(BaseModel):
    """수정 로그 요청 스키마 (Pydantic 기반 검증)"""
    
    workflow_id: str = Field(..., min_length=1, max_length=100, description="워크플로우 ID")
    node_id: str = Field(..., min_length=1, max_length=100, description="노드 ID")
    original_input: str = Field(..., min_length=1, max_length=5000, description="원본 입력")
    agent_output: str = Field(..., min_length=1, max_length=5000, description="에이전트 출력")
    user_correction: str = Field(..., min_length=1, max_length=5000, description="사용자 수정")
    task_category: TaskCategory = Field(..., description="태스크 카테고리")
    
    # 선택적 필드들
    node_type: Optional[str] = Field("llm_operator", max_length=50, description="노드 타입")
    workflow_domain: Optional[str] = Field("general", max_length=50, description="워크플로우 도메인")
    correction_time_seconds: Optional[float] = Field(0, ge=0, le=3600, description="수정 소요 시간 (초)")
    user_confirmed_valuable: Optional[bool] = Field(None, description="사용자 가치 확인")
    context: Optional[Dict[str, Any]] = Field(default_factory=dict, description="추가 컨텍스트")
    
    # 메타데이터 (선택적)
    correction_type: Optional[CorrectionType] = Field(None, description="수정 타입")
    context_scope: Optional[str] = Field("global", description="컨텍스트 범위")
    
    class Config:
        # Pydantic 설정
        validate_assignment = True
        str_strip_whitespace = True  # 문자열 앞뒤 공백 자동 제거
        anystr_lower = False  # 대소문자 유지
        
        # 예시 스키마
        schema_extra = {
            "example": {
                "workflow_id": "wf_12345",
                "node_id": "node_67890",
                "original_input": "Write an email to the client",
                "agent_output": "Hey there! Hope you're doing well...",
                "user_correction": "Dear Client, I hope this message finds you well...",
                "task_category": "email",
                "node_type": "llm_operator",
                "workflow_domain": "sales",
                "correction_time_seconds": 45.5,
                "user_confirmed_valuable": True,
                "correction_type": "tone",
                "context_scope": "global"
            }
        }

class RecentCorrectionsRequest(BaseModel):
    """최근 수정 로그 조회 요청 스키마"""
    
    task_category: Optional[TaskCategory] = Field(None, description="필터링할 태스크 카테고리")
    hours: Optional[int] = Field(24, ge=1, le=168, description="조회 시간 범위 (시간)")
    limit: Optional[int] = Field(50, ge=1, le=100, description="최대 결과 수")

class PatternSearchRequest(BaseModel):
    """패턴 검색 요청 스키마"""
    
    metadata_pattern: Dict[str, str] = Field(..., description="검색할 메타데이터 패턴")
    limit: Optional[int] = Field(10, ge=1, le=50, description="최대 결과 수")
    
    @validator('metadata_pattern')
    def validate_pattern_size(cls, v):
        if len(v) > 20:
            raise ValueError("metadata_pattern cannot have more than 20 keys")
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "metadata_pattern": {
                    "tone": "professional",
                    "task_category": "email"
                },
                "limit": 10
            }
        }

def validate_and_sanitize_request(
    request_data: Dict[str, Any], 
    schema_class: BaseModel
) -> tuple[Optional[BaseModel], Optional[Dict[str, Any]]]:
    """
    개선사항: Pydantic을 활용한 통합 검증 및 Sanitization
    
    Returns:
        (validated_data, error_response) - 성공 시 error_response는 None
    """
    try:
        # 1단계: 입력 데이터 Sanitization
        sanitized_data = sanitize_input_data(request_data)
        
        # 2단계: Pydantic 스키마 검증
        validated_request = schema_class(**sanitized_data)
        
        return validated_request, None
        
    except ValidationError as e:
        # Pydantic 검증 에러를 사용자 친화적 메시지로 변환
        error_details = []
        for error in e.errors():
            field = " -> ".join(str(loc) for loc in error['loc'])
            message = error['msg']
            error_details.append(f"{field}: {message}")
        
        error_response = create_error_response(
            400,
            "Validation Error",
            f"Request validation failed: {'; '.join(error_details)}"
        )
        return None, error_response
        
    except Exception as e:
        logger.error(f"Unexpected validation error: {str(e)}", exc_info=True)
        error_response = create_error_response(
            400,
            "Bad Request",
            "Request validation failed"
        )
        return None, error_response

logger = logging.getLogger(__name__)

# 전역 서비스 인스턴스
correction_service = CorrectionService()

# JWT 설정 (환경 변수에서 로드)
JWT_ALGORITHM = os.environ.get('JWT_ALGORITHM', 'RS256')
JWT_ISSUER = os.environ.get('JWT_ISSUER')  # 예: https://cognito-idp.region.amazonaws.com/user_pool_id
JWKS_URL = os.environ.get('JWKS_URL')      # 예: https://cognito-idp.region.amazonaws.com/user_pool_id/.well-known/jwks.json
JWT_AUDIENCE = os.environ.get('JWT_AUDIENCE')  # 클라이언트 ID

# JWKS 클라이언트 (JWT 공개키 검증용)
jwks_client = None
if JWT_AVAILABLE and JWKS_URL:
    try:
        jwks_client = PyJWKClient(JWKS_URL)
        logger.info(f"JWKS client initialized: {JWKS_URL}")
    except Exception as e:
        logger.warning(f"Failed to initialize JWKS client: {e}")

def safe_run_async(coro):
    """
    개선사항: asyncio.run()의 중첩 호출 방지
    
    실행 중인 이벤트 루프가 있는지 확인하고 안전하게 비동기 함수 실행
    """
    try:
        # 현재 실행 중인 이벤트 루프가 있는지 확인
        loop = asyncio.get_running_loop()
        if loop.is_running():
            # 이미 실행 중인 루프가 있으면 create_task 사용
            logger.warning("Event loop already running, using create_task")
            task = loop.create_task(coro)
            # 동기적으로 기다리기 위해 run_until_complete 사용 (주의: 데드락 위험)
            # Lambda 환경에서는 일반적으로 안전하지만, 더 안전한 방법은 별도 스레드 사용
            import concurrent.futures
            import threading
            
            def run_in_thread():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(coro)
                finally:
                    new_loop.close()
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_thread)
                return future.result(timeout=30)  # 30초 타임아웃
        else:
            # 실행 중인 루프가 없으면 새로 생성
            return asyncio.run(coro)
            
    except RuntimeError as e:
        if "no running event loop" in str(e).lower():
            # 루프가 없으면 새로 생성
            return asyncio.run(coro)
        elif "cannot be called from src.a running event loop" in str(e).lower():
            # 중첩 호출 에러 - 스레드에서 실행
            logger.warning(f"Nested event loop detected: {e}")
            
            def run_in_thread():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(coro)
                finally:
                    new_loop.close()
            
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_thread)
                return future.result(timeout=30)
        else:
            raise

def auth_decorator(func: Callable) -> Callable:
    """
    개선사항 1: JWT 검증 미들웨어 데코레이터 (안전한 asyncio 실행)
    
    모든 API 공통으로 사용할 수 있는 인증 데코레이터
    """
    @wraps(func)
    def wrapper(event: Dict[str, Any], context) -> Dict[str, Any]:
        try:
            # JWT 토큰 검증
            user_id = extract_and_verify_user_id(event.get('headers', {}))
            if not user_id:
                return create_error_response(401, "Unauthorized", "Invalid or missing JWT token")
            
            # 검증된 user_id를 event에 추가
            event['authenticated_user_id'] = user_id
            
            # 원본 함수 실행 (개선된 안전한 asyncio 실행)
            if asyncio.iscoroutinefunction(func):
                # 개선사항: 안전한 asyncio.run() 사용
                return safe_run_async(func(event, context))
            else:
                return func(event, context)
                
        except Exception as e:
            logger.error(f"Auth decorator error: {str(e)}", exc_info=True)
            return create_error_response(500, "Internal server error", "Authentication failed")
    
    return wrapper

def extract_and_verify_user_id(headers: Dict[str, str]) -> Optional[str]:
    """
    개선사항 1: JWT 검증의 실구현 (JWKS 기반)
    
    Cognito 또는 Auth0의 공개키로 토큰 서명을 직접 검증
    """
    try:
        # Authorization 헤더에서 JWT 토큰 추출
        auth_header = headers.get('Authorization') or headers.get('authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            logger.warning("Missing or invalid Authorization header")
            return None
        
        token = auth_header.replace('Bearer ', '').strip()
        if not token:
            logger.warning("Empty JWT token")
            return None
        
        # JWT 검증 (실제 구현)
        if JWT_AVAILABLE and jwks_client and JWT_ISSUER:
            try:
                # JWKS에서 공개키 가져오기
                signing_key = jwks_client.get_signing_key_from_jwt(token)
                
                # JWT 토큰 검증 및 디코딩
                decoded_token = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=[JWT_ALGORITHM],
                    issuer=JWT_ISSUER,
                    audience=JWT_AUDIENCE,
                    options={"verify_exp": True, "verify_aud": True}
                )
                
                # 사용자 ID 추출 (Cognito의 경우 'sub', Auth0의 경우 'sub' 또는 'user_id')
                user_id = decoded_token.get('sub') or decoded_token.get('user_id')
                if not user_id:
                    logger.error("No user ID found in JWT token")
                    return None
                
                logger.info(f"JWT verification successful for user: {user_id}")
                return user_id
                
            except jwt.ExpiredSignatureError:
                logger.warning("JWT token has expired")
                return None
            except jwt.InvalidTokenError as e:
                logger.warning(f"Invalid JWT token: {str(e)}")
                return None
            except Exception as e:
                logger.error(f"JWT verification failed: {str(e)}")
                return None
        
        else:
            # 개발 환경용 fallback (JWT 라이브러리 없거나 설정 미완료)
            logger.warning("JWT verification not configured - using development mode")
            
            # 개발용: 헤더에서 직접 user-id 추출
            dev_user_id = headers.get('x-user-id') or headers.get('X-User-Id')
            if dev_user_id:
                logger.info(f"Development mode: using x-user-id header: {dev_user_id}")
                return dev_user_id
            
            # 개발용 기본값
            logger.warning("Development mode: using default user ID")
            return "dev_user_default"
        
    except Exception as e:
        logger.error(f"Error extracting user ID: {str(e)}")
        return None

def sanitize_input_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    개선사항 3: 입력 데이터 Sanitization
    
    XSS 및 프롬프트 인젝션 방어를 위한 문자열 필터링
    """
    sanitized = {}
    
    for key, value in data.items():
        if isinstance(value, str):
            # HTML 이스케이프
            sanitized_value = html.escape(value)
            
            # 악의적인 스크립트 패턴 제거
            dangerous_patterns = [
                r'<script[^>]*>.*?</script>',  # 스크립트 태그
                r'javascript:',               # 자바스크립트 프로토콜
                r'on\w+\s*=',                # 이벤트 핸들러
                r'eval\s*\(',                # eval 함수
                r'expression\s*\(',          # CSS expression
            ]
            
            for pattern in dangerous_patterns:
                sanitized_value = re.sub(pattern, '', sanitized_value, flags=re.IGNORECASE)
            
            # 프롬프트 인젝션 패턴 제거
            prompt_injection_patterns = [
                r'ignore\s+previous\s+instructions',
                r'system\s*:',
                r'assistant\s*:',
                r'human\s*:',
                r'\[INST\]',
                r'\[/INST\]',
            ]
            
            for pattern in prompt_injection_patterns:
                sanitized_value = re.sub(pattern, '[FILTERED]', sanitized_value, flags=re.IGNORECASE)
            
            # 길이 제한 (DoS 방어)
            max_length = 10000  # 10KB
            if len(sanitized_value) > max_length:
                sanitized_value = sanitized_value[:max_length] + "...[TRUNCATED]"
            
            sanitized[key] = sanitized_value
        
        elif isinstance(value, dict):
            # 중첩된 딕셔너리 재귀 처리
            sanitized[key] = sanitize_input_data(value)
        
        elif isinstance(value, list):
            # 리스트 내 문자열 처리
            sanitized[key] = [
                html.escape(item) if isinstance(item, str) else item
                for item in value
            ]
        
        else:
            # 다른 타입은 그대로 유지
            sanitized[key] = value
    
    return sanitized

def create_error_response(status_code: int, error: str, details: str = "") -> Dict[str, Any]:
    """표준화된 에러 응답 생성"""
    response_body = {"error": error}
    if details:
        response_body["details"] = details
    
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
        },
        "body": json.dumps(response_body)
    }

def create_success_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """표준화된 성공 응답 생성"""
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
        },
        "body": json.dumps(data)
    }

@auth_decorator
async def handle_log_correction(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    수정 로그 저장 API (Pydantic 스키마 검증 적용)
    
    개선사항: 
    - Pydantic을 활용한 스키마 검증으로 수동 검증 로직 대체
    - 안전한 asyncio 실행
    """
    
    try:
        # 개선사항 3: JSON 파싱 예외 처리
        try:
            body = json.loads(event.get('body', '{}'))
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in request body: {str(e)}")
            return create_error_response(400, "Bad Request", "Invalid JSON format")
        
        # 인증된 사용자 ID 가져오기
        user_id = event.get('authenticated_user_id')
        if not user_id:
            return create_error_response(401, "Unauthorized", "Authentication required")
        
        # 개선사항: Pydantic 스키마 검증 (수동 검증 로직 대체)
        validated_request, error_response = validate_and_sanitize_request(
            body, CorrectionLogRequest
        )
        
        if error_response:
            return error_response
        
        # CorrectionLog 모델 생성 (Pydantic 기반)
        try:
            correction_log_data = {
                "pk": f"user#{user_id}",
                "user_id": user_id,
                "workflow_id": validated_request.workflow_id,
                "node_id": validated_request.node_id,
                "original_input": validated_request.original_input,
                "agent_output": validated_request.agent_output,
                "user_correction": validated_request.user_correction,
                "task_category": validated_request.task_category,
                "node_type": validated_request.node_type,
                "workflow_domain": validated_request.workflow_domain,
                "correction_time_seconds": int(validated_request.correction_time_seconds or 0),
                "user_confirmed_valuable": validated_request.user_confirmed_valuable,
                "correction_type": validated_request.correction_type,
                "context_scope": validated_request.context_scope or "global"
            }
            
            # 편집 거리 계산 (간단한 구현)
            edit_distance = abs(len(validated_request.user_correction) - len(validated_request.agent_output))
            correction_log_data["edit_distance"] = edit_distance
            
            # CorrectionLog 모델 검증
            correction_log = CorrectionLog(**correction_log_data)
            
        except ValidationError as e:
            logger.error(f"CorrectionLog validation failed: {e}")
            return create_error_response(
                400,
                "Bad Request", 
                "Failed to create correction log: invalid data"
            )
        
        # 수정 로그 저장 (기존 서비스 사용)
        correction_id = await correction_service.log_correction(
            user_id=user_id,
            workflow_id=validated_request.workflow_id,
            node_id=validated_request.node_id,
            original_input=validated_request.original_input,
            agent_output=validated_request.agent_output,
            user_correction=validated_request.user_correction,
            task_category=validated_request.task_category.value,
            node_type=validated_request.node_type,
            workflow_domain=validated_request.workflow_domain,
            correction_time_seconds=validated_request.correction_time_seconds or 0,
            user_confirmed_valuable=validated_request.user_confirmed_valuable,
            context=validated_request.context or {}
        )
        
        return create_success_response({
            "correction_id": correction_id,
            "message": "Correction logged successfully",
            "user_id": user_id,
            "validation": {
                "schema_validated": True,
                "sanitized": True,
                "edit_distance": edit_distance
            }
        })
        
    except Exception as e:
        logger.error(f"Error logging correction: {str(e)}", exc_info=True)
        return create_error_response(500, "Internal Server Error", "Failed to log correction")

@auth_decorator
async def handle_get_recent_corrections(event: Dict[str, Any], context) -> Dict[str, Any]:
    """최근 수정 로그 조회 API (Pydantic 검증 적용)"""
    
    try:
        user_id = event.get('authenticated_user_id')
        query_params = event.get('queryStringParameters') or {}
        
        # Pydantic 스키마 검증
        validated_request, error_response = validate_and_sanitize_request(
            query_params, RecentCorrectionsRequest
        )
        
        if error_response:
            return error_response
        
        # 최근 수정 로그 조회
        corrections = await correction_service.get_recent_corrections(
            user_id=user_id,
            task_category=validated_request.task_category.value if validated_request.task_category else None,
            hours=validated_request.hours,
            limit=validated_request.limit
        )
        
        return create_success_response({
            "corrections": corrections,
            "count": len(corrections),
            "filters": {
                "task_category": validated_request.task_category.value if validated_request.task_category else None,
                "hours": validated_request.hours,
                "limit": validated_request.limit
            },
            "validation": {
                "schema_validated": True,
                "sanitized": True
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting recent corrections: {str(e)}", exc_info=True)
        return create_error_response(500, "Internal Server Error", "Failed to retrieve corrections")

@auth_decorator
async def handle_get_corrections_by_pattern(event: Dict[str, Any], context) -> Dict[str, Any]:
    """메타데이터 패턴으로 수정 로그 검색 API (Pydantic 검증 적용)"""
    
    try:
        user_id = event.get('authenticated_user_id')
        
        # JSON 파싱 예외 처리
        try:
            body = json.loads(event.get('body', '{}'))
        except json.JSONDecodeError as e:
            return create_error_response(400, "Bad Request", "Invalid JSON format")
        
        # Pydantic 스키마 검증
        validated_request, error_response = validate_and_sanitize_request(
            body, PatternSearchRequest
        )
        
        if error_response:
            return error_response
        
        # 패턴으로 수정 로그 검색
        corrections = await correction_service.get_corrections_by_pattern(
            user_id=user_id,
            metadata_pattern=validated_request.metadata_pattern,
            limit=validated_request.limit
        )
        
        return create_success_response({
            "corrections": corrections,
            "pattern": validated_request.metadata_pattern,
            "count": len(corrections),
            "limit": validated_request.limit,
            "validation": {
                "schema_validated": True,
                "sanitized": True,
                "pattern_keys": list(validated_request.metadata_pattern.keys())
            }
        })
        
    except Exception as e:
        logger.error(f"Error searching corrections by pattern: {str(e)}", exc_info=True)
        return create_error_response(500, "Internal Server Error", "Failed to search corrections")

# Lambda 핸들러 함수들 (개선사항 2: 동기식 래퍼)
def lambda_log_correction(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    개선사항 2: Lambda 호환 동기식 핸들러
    
    AWS Lambda의 기본 Python 핸들러는 동기 방식으로 호출되므로
    auth_decorator에서 asyncio.run()을 사용하여 비동기 함수를 실행
    """
    return handle_log_correction(event, context)

def lambda_get_recent_corrections(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Lambda 호환 동기식 핸들러 - 최근 수정 로그 조회"""
    return handle_get_recent_corrections(event, context)

def lambda_get_corrections_by_pattern(event: Dict[str, Any], context) -> Dict[str, Any]:
    """Lambda 호환 동기식 핸들러 - 패턴 검색"""
    return handle_get_corrections_by_pattern(event, context)

def get_jwt_configuration_guide() -> Dict[str, Any]:
    """
    개선사항 1: JWT 설정 가이드 (고급 개선사항 포함)
    
    운영 환경에서 JWT 검증을 위한 환경 변수 설정 가이드
    """
    return {
        "required_environment_variables": {
            "JWT_ALGORITHM": {
                "description": "JWT 서명 알고리즘",
                "example": "RS256",
                "required": True
            },
            "JWT_ISSUER": {
                "description": "JWT 발급자 (Cognito User Pool URL)",
                "example": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX",
                "required": True
            },
            "JWKS_URL": {
                "description": "JWKS 엔드포인트 URL",
                "example": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX/.well-known/jwks.json",
                "required": True
            },
            "JWT_AUDIENCE": {
                "description": "JWT 대상 (클라이언트 ID)",
                "example": "1234567890abcdefghijklmnop",
                "required": True
            }
        },
        "cognito_setup": {
            "user_pool_creation": "AWS Cognito에서 User Pool 생성",
            "app_client_creation": "User Pool에서 App Client 생성",
            "jwks_url_format": "https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
        },
        "auth0_setup": {
            "application_creation": "Auth0에서 Application 생성",
            "jwks_url_format": "https://{domain}/.well-known/jwks.json"
        },
        "security_considerations": [
            "JWT 토큰은 HTTPS를 통해서만 전송",
            "토큰 만료 시간을 적절히 설정 (예: 1시간)",
            "Refresh Token을 사용하여 토큰 갱신",
            "JWKS 캐싱으로 성능 최적화",
            "토큰 검증 실패 시 상세 로그 기록"
        ],
        "advanced_improvements": {
            "safe_asyncio_execution": {
                "description": "중첩 이벤트 루프 방지",
                "implementation": "safe_run_async() 함수 사용",
                "benefits": ["RuntimeError 방지", "Lambda 환경 호환성", "스레드 안전성"]
            },
            "pydantic_validation": {
                "description": "스키마 기반 검증",
                "implementation": "CorrectionLogRequest, RecentCorrectionsRequest 스키마",
                "benefits": ["자동 타입 변환", "상세한 에러 메시지", "코드 간소화"]
            }
        }
    }

def get_advanced_improvements_guide() -> Dict[str, Any]:
    """
    고급 개선사항 가이드
    
    asyncio 중첩 호출 방지 및 Pydantic 스키마 검증 가이드
    """
    return {
        "asyncio_improvements": {
            "problem": "asyncio.run() 중첩 호출로 인한 RuntimeError",
            "solution": "safe_run_async() 함수로 안전한 비동기 실행",
            "implementation": {
                "detection": "asyncio.get_running_loop()로 실행 중인 루프 확인",
                "fallback": "별도 스레드에서 새 이벤트 루프 생성",
                "timeout": "30초 타임아웃으로 데드락 방지"
            },
            "benefits": [
                "Lambda 환경에서 안정적 동작",
                "다른 비동기 프레임워크와 충돌 방지",
                "견고한 에러 처리"
            ]
        },
        "pydantic_validation": {
            "problem": "수동 검증 로직의 복잡성과 유지보수 어려움",
            "solution": "Pydantic 스키마 기반 자동 검증",
            "schemas": {
                "CorrectionLogRequest": "수정 로그 생성 요청",
                "RecentCorrectionsRequest": "최근 수정 로그 조회",
                "PatternSearchRequest": "패턴 기반 검색"
            },
            "features": [
                "자동 타입 변환 및 검증",
                "문자열 앞뒤 공백 제거",
                "상세한 검증 에러 메시지",
                "스키마 문서화 자동 생성"
            ],
            "benefits": [
                "코드 간소화 (수동 검증 로직 제거)",
                "일관된 검증 규칙",
                "개발자 경험 향상",
                "API 문서화 자동화"
            ]
        },
        "security_enhancements": {
            "input_sanitization": {
                "xss_protection": "HTML 이스케이프 및 스크립트 태그 제거",
                "prompt_injection": "프롬프트 인젝션 패턴 필터링",
                "length_limits": "DoS 방어를 위한 길이 제한"
            },
            "jwt_verification": {
                "jwks_validation": "공개키 기반 서명 검증",
                "token_expiry": "만료 시간 자동 확인",
                "audience_validation": "대상 클라이언트 검증"
            }
        },
        "operational_recommendations": [
            "Lambda Layer에 PyJWT 라이브러리 포함",
            "CloudWatch 로그로 보안 이벤트 모니터링",
            "API Gateway에서 요청 크기 제한 설정",
            "DynamoDB 조건부 쓰기로 멱등성 보장",
            "벡터 DB 동기화 상태 모니터링"
        ]
    }

def get_pydantic_schema_examples() -> Dict[str, Any]:
    """Pydantic 스키마 사용 예시"""
    return {
        "request_validation_example": {
            "before": """
# 수동 검증 (기존 방식)
required_fields = ['workflow_id', 'node_id', ...]
for field in required_fields:
    if field not in body:
        return error_response(400, f"Missing {field}")

if len(body['workflow_id']) > 100:
    return error_response(400, "workflow_id too long")
            """,
            "after": """
# Pydantic 스키마 검증 (개선된 방식)
validated_request, error_response = validate_and_sanitize_request(
    body, CorrectionLogRequest
)
if error_response:
    return error_response
            """
        },
        "schema_definition_example": {
            "description": "Pydantic 스키마 정의 예시",
            "code": """
class CorrectionLogRequest(BaseModel):
    workflow_id: str = Field(..., min_length=1, max_length=100)
    node_id: str = Field(..., min_length=1, max_length=100)
    task_category: TaskCategory = Field(...)
    correction_time_seconds: Optional[float] = Field(0, ge=0, le=3600)
    
    class Config:
        str_strip_whitespace = True  # 자동 공백 제거
        validate_assignment = True   # 할당 시 검증
            """
        },
        "validation_benefits": [
            "자동 타입 변환 (문자열 → 숫자)",
            "범위 검증 (ge=0, le=3600)",
            "길이 검증 (min_length, max_length)",
            "필수 필드 검증 (...)",
            "사용자 친화적 에러 메시지"
        ]
    }

# 레거시 함수 (하위 호환성)
def extract_user_id_from_headers(headers: Dict[str, str]) -> Optional[str]:
    """
    레거시 함수 - 하위 호환성을 위해 유지
    새로운 코드는 extract_and_verify_user_id 사용 권장
    """
    logger.warning("Using legacy extract_user_id_from_headers - consider upgrading to extract_and_verify_user_id")
    return extract_and_verify_user_id(headers)