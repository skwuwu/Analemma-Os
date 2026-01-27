# -*- coding: utf-8 -*-
"""
Gemini Client - Gemini Native API 클라이언트

Google Gemini 모델 호출을 위한 전용 클라이언트입니다.
Response Schema (Structured Output) 지원.

Features:
- Gemini Native API 스트리밍 호출
- Response Schema를 통한 구조화된 출력
- Mock 모드 지원
"""

import json
import logging
import os
import time
from typing import Any, Dict, Generator, List, Optional

from src.services.llm.bedrock_client import is_mock_mode, get_mock_workflow, MOCK_UI_DELAY_MS

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


# ============================================================================
# Gemini 클라이언트 Lazy Loading
# ============================================================================

_genai_client = None


def get_genai_client():
    """google.genai 클라이언트 lazy loading"""
    global _genai_client
    if _genai_client is not None:
        return _genai_client
    
    try:
        from google import genai
        from google.genai import types
        
        api_key = os.getenv("GEMINI_API_KEY") or _get_gemini_api_key_from_secrets()
        if not api_key:
            raise ValueError("GEMINI_API_KEY not configured")
        
        _genai_client = genai.Client(api_key=api_key)
        return _genai_client
        
    except ImportError:
        logger.error("google-genai package not installed")
        raise


def _get_gemini_api_key_from_secrets() -> Optional[str]:
    """SecretsManager에서 Gemini API Key 조회"""
    try:
        import boto3
        client = boto3.client('secretsmanager', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        secret_name = os.getenv("GEMINI_API_KEY_SECRET_NAME", "analemma/gemini-api-key")
        response = client.get_secret_value(SecretId=secret_name)
        secret = json.loads(response['SecretString'])
        return secret.get('api_key')
    except Exception as e:
        logger.debug(f"Failed to get Gemini API key from secrets: {e}")
        return None


# ============================================================================
# Response Schema 생성
# ============================================================================

def create_workflow_schema():
    """워크플로우용 Gemini Response Schema 생성"""
    try:
        from google.genai import types
    except ImportError:
        return None
    
    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "name": types.Schema(type=types.Type.STRING, description="워크플로우 이름"),
            "nodes": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "id": types.Schema(type=types.Type.STRING),
                        "type": types.Schema(type=types.Type.STRING),
                        "position": types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "x": types.Schema(type=types.Type.NUMBER),
                                "y": types.Schema(type=types.Type.NUMBER)
                            }
                        ),
                        "data": types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "label": types.Schema(type=types.Type.STRING),
                                "blockId": types.Schema(type=types.Type.STRING),
                                "model": types.Schema(type=types.Type.STRING),
                                "prompt_content": types.Schema(type=types.Type.STRING)
                            }
                        )
                    },
                    required=["id", "type", "position", "data"]
                )
            ),
            "edges": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "id": types.Schema(type=types.Type.STRING),
                        "source": types.Schema(type=types.Type.STRING),
                        "target": types.Schema(type=types.Type.STRING),
                        "type": types.Schema(type=types.Type.STRING)
                    },
                    required=["id", "source", "target"]
                )
            )
        },
        required=["name", "nodes", "edges"]
    )


# ============================================================================
# Gemini 스트리밍 호출
# ============================================================================

def invoke_gemini_stream(
    system_prompt: str,
    user_request: str,
    model_id: str = None,
    use_response_schema: bool = True
) -> Generator[str, None, None]:
    """
    Gemini Native API 스트리밍 호출
    
    Args:
        system_prompt: 시스템 프롬프트
        user_request: 사용자 요청
        model_id: 모델 ID (기본: gemini-2.0-flash)
        use_response_schema: Response Schema 사용 여부
        
    Yields:
        JSONL 형식의 응답 청크
    """
    model_to_use = model_id or os.getenv("GEMINI_MODEL_ID", "gemini-2.0-flash")
    
    # Mock 모드 처리
    if is_mock_mode():
        logger.info("MOCK_MODE: Gemini streaming synthetic response")
        mock_wf = get_mock_workflow()
        ui_delay = MOCK_UI_DELAY_MS / 1000.0
        
        for node in mock_wf.get("nodes", []):
            yield json.dumps({"type": "node", "data": node}) + "\n"
            if ui_delay > 0:
                time.sleep(ui_delay)
        
        for edge in mock_wf.get("edges", []):
            yield json.dumps({"type": "edge", "data": edge}) + "\n"
            if ui_delay > 0:
                time.sleep(ui_delay)
        
        yield json.dumps({"type": "status", "data": "done"}) + "\n"
        return
    
    try:
        from google.genai import types
        
        client = get_genai_client()
        
        # Config 생성
        config_params = {
            "temperature": 0.1,
            "max_output_tokens": 4096
        }
        
        if use_response_schema:
            schema = create_workflow_schema()
            if schema:
                config_params["response_mime_type"] = "application/json"
                config_params["response_schema"] = schema
        
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            **config_params
        )
        
        # 스트리밍 호출
        response_stream = client.models.generate_content_stream(
            model=model_to_use,
            contents=user_request,
            config=config
        )
        
        buffer = ""
        for chunk in response_stream:
            if hasattr(chunk, 'text') and chunk.text:
                buffer += chunk.text
                
                # JSONL 라인 파싱
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        yield line + "\n"
        
        # 남은 버퍼 처리
        if buffer.strip():
            yield buffer + "\n"
        
        yield json.dumps({"type": "status", "data": "done"}) + "\n"
        
    except Exception as e:
        logger.exception(f"Gemini stream failed: {e}")
        yield json.dumps({"type": "error", "message": str(e)}) + "\n"


# ============================================================================
# 통합 LLM 스트리밍 (모델 선택)
# ============================================================================

def select_and_invoke_stream(
    system_prompt: str,
    user_request: str,
    model_id: str = None
) -> Generator[str, None, None]:
    """
    모델 ID에 따라 적절한 클라이언트 선택하여 스트리밍 호출
    
    Args:
        system_prompt: 시스템 프롬프트
        user_request: 사용자 요청
        model_id: 모델 ID
        
    Yields:
        JSONL 형식의 응답 청크
    """
    from src.services.llm.bedrock_client import invoke_bedrock_stream
    
    model = model_id or os.getenv("DEFAULT_MODEL_ID", "gemini-2.0-flash")
    
    if "gemini" in model.lower():
        yield from invoke_gemini_stream(system_prompt, user_request, model)
    else:
        yield from invoke_bedrock_stream(system_prompt, user_request, model)
