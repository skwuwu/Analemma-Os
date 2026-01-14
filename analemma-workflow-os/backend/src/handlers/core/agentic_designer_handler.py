# -*- coding: utf-8 -*-
"""
Agentic Designer Handler - HTTP API 핸들러

워크플로우 생성/수정을 위한 Lambda 핸들러입니다.
비즈니스 로직은 services.design 패키지로 위임합니다.

Clean Architecture:
- 이 파일: HTTP 라우팅, 인증, 요청/응답 변환
- services.design.designer_service: 워크플로우 생성 비즈니스 로직
- services.design.prompts: LLM 시스템 프롬프트
- services.llm: LLM 클라이언트 (Bedrock/Gemini)

Usage:
    POST /design - 워크플로우 생성/수정 요청
    GET /design - 상태 및 기능 정보 조회
"""

import json
import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# ============================================================================
# 공통 유틸리티 import
# ============================================================================

try:
    from src.common.auth_utils import extract_owner_id_from_event
except ImportError:
    def extract_owner_id_from_event(*args, **kwargs):
        raise Exception("Unauthorized: auth_utils not available")

# ============================================================================
# 서비스 계층 import
# ============================================================================

try:
    from src.services.design.designer_service import (
        analyze_request,
        stream_workflow_jsonl,
        build_text_response,
    )
except ImportError:
    # Fallback for Lambda layer resolution
    try:
        from services.design.designer_service import (
            analyze_request,
            stream_workflow_jsonl,
            build_text_response,
        )
    except ImportError:
        logger.warning("designer_service not available, using inline fallback")
        
        def analyze_request(user_request: str) -> Dict[str, str]:
            return {"intent": "workflow", "complexity": "단순"}
        
        def stream_workflow_jsonl(user_request: str, current_workflow=None, canvas_mode=None):
            yield json.dumps({"type": "error", "data": "Service not available"}) + "\n"
        
        def build_text_response(user_request: str) -> Dict[str, Any]:
            return {"statusCode": 500, "body": json.dumps({"error": "Service not available"})}


# ============================================================================
# Lambda Handler
# ============================================================================

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Agentic Designer Lambda 핸들러
    
    Args:
        event: API Gateway 이벤트
        context: Lambda 컨텍스트
        
    Returns:
        HTTP 응답 (statusCode, headers, body)
    """
    try:
        # HTTP 메소드/경로 추출
        http_method = (
            event.get("requestContext", {}).get("http", {}).get("method")
            or event.get("httpMethod")
        )
        
        # =========================================================
        # GET: 상태/기능 정보 반환
        # =========================================================
        if http_method == "GET":
            return _handle_get_status()
        
        # =========================================================
        # POST: 워크플로우 생성/수정 요청
        # =========================================================
        
        # 요청 바디 파싱
        raw_body = event.get("body", "{}")
        if isinstance(raw_body, dict):
            body = raw_body
        else:
            try:
                body = json.loads(raw_body or "{}")
            except (json.JSONDecodeError, ValueError):
                return _error_response(400, "Invalid JSON body")
        
        # 인증 처리
        try:
            owner_id = extract_owner_id_from_event(event)
            logger.info(f"Authenticated request for user: {owner_id}")
        except RuntimeError as e:
            logger.error(f"Authentication configuration error: {e}")
            return _error_response(500, "Internal server configuration error")
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return _error_response(403, f"Forbidden: {e}")
        
        # 요청 필드 추출
        user_request = body.get("request")
        if not user_request:
            return _error_response(400, "No 'request' field provided")
        
        current_workflow = body.get("current_workflow", {"nodes": [], "edges": []})
        
        # 요청 의도 분석
        analysis = analyze_request(user_request)
        intent = analysis.get("intent", "text")
        complexity = analysis.get("complexity", "단순")
        
        logger.info(f"Request analysis: intent={intent}, complexity={complexity}, owner={owner_id}")
        
        # =========================================================
        # 텍스트 응답 처리
        # =========================================================
        if intent == "text":
            return build_text_response(user_request)
        
        # =========================================================
        # 워크플로우 생성 스트리밍
        # =========================================================
        if intent == "workflow":
            return _handle_workflow_stream(user_request, current_workflow)
        
        # 기본 응답
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Request processed",
                "intent": intent,
                "complexity": complexity
            })
        }
    
    except Exception as e:
        logger.exception(f"Unexpected error in lambda_handler: {e}")
        return _error_response(500, "Internal server error")


# ============================================================================
# 핸들러 헬퍼 함수들
# ============================================================================

def _handle_get_status() -> Dict[str, Any]:
    """GET 요청: 상태 및 기능 정보 반환"""
    return {
        "statusCode": 200,
        "headers": _cors_headers(),
        "body": json.dumps({
            "status": "available",
            "version": "2.0",
            "capabilities": {
                "workflow_design": True,
                "workflow_patch": True,
                "streaming_response": True,
                "websocket_integration": True,
                "gemini_native": True
            },
            "models": {
                "primary": "gemini-2.0-flash",
                "fallback": "anthropic.claude-3-sonnet-20240229-v1:0"
            },
            "features": [
                "Natural language to workflow conversion",
                "Existing workflow modification",
                "Real-time streaming responses",
                "WebSocket notifications",
                "Gemini Native with Response Schema"
            ]
        })
    }


def _handle_workflow_stream(
    user_request: str,
    current_workflow: Dict[str, Any]
) -> Dict[str, Any]:
    """워크플로우 생성 스트리밍 처리"""
    chunks = []
    
    try:
        for chunk in stream_workflow_jsonl(
            user_request=user_request,
            current_workflow=current_workflow,
            canvas_mode="agentic-designer"
        ):
            chunks.append(chunk)
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        chunks.append(json.dumps({"type": "error", "data": str(e)}) + "\n")
    
    response_body = ''.join(chunks)
    
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/x-ndjson",
            **_cors_headers()
        },
        "body": response_body
    }


def _error_response(status_code: int, message: str) -> Dict[str, Any]:
    """에러 응답 생성"""
    return {
        "statusCode": status_code,
        "headers": _cors_headers(),
        "body": json.dumps({"error": message})
    }


def _cors_headers() -> Dict[str, str]:
    """CORS 헤더 반환"""
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS"
    }

