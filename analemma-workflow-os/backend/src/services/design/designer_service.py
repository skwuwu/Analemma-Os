"""
DesignerService - Workflow Design and LLM Streaming Service

Extracted from `agentic_designer.py` to separate business logic from handler.
Handles:
- Request intent analysis
- Workflow generation via LLM
- Streaming response processing
"""

import json
import codecs
import logging
import os
import time
from typing import Dict, Any, Iterator, List, Optional

# 공통 유틸리티 import
try:
    from src.common.constants import is_mock_mode as _common_is_mock_mode, LLMModels
    MODEL_HAIKU = LLMModels.CLAUDE_3_HAIKU
    MODEL_SONNET = LLMModels.CLAUDE_3_SONNET
    MODEL_GEMINI = LLMModels.GEMINI_1_5_PRO
except ImportError:
    _common_is_mock_mode = None
    MODEL_HAIKU = os.getenv("HAIKU_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
    MODEL_SONNET = os.getenv("SONNET_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
    MODEL_GEMINI = os.getenv("GEMINI_MODEL_ID", "gemini-1.5-pro-latest")

logger = logging.getLogger(__name__)

# Prompts
ANALYSIS_PROMPT = """사용자 요청을 분석하여 두 가지를 판단해.
1. intent: 요청이 구조화된 워크플로우 JSON 생성을 요구하는 'workflow'인지, 단순 정보 요청인 'text'인지 판단.
2. complexity: 요청의 복잡도를 '단순', '보통', '복잡' 중 하나로 분류.

반드시 아래와 같은 JSON 형식으로만 답변해야 해. 다른 설명은 절대 추가하지 마.
예시 응답: {{"intent": "workflow", "complexity": "보통"}}

사용자 요청:
{user_request}
"""


class DesignerService:
    """
    Service for AI-powered workflow design.
    
    Responsibilities:
    - Analyze user requests to determine intent (workflow vs text)
    - Generate workflow components via LLM streaming
    - Provide mock responses for testing
    """
    
    def __init__(self):
        self._bedrock_client = None
        
    @property
    def bedrock_client(self):
        """Lazy Bedrock client initialization."""
        if self._bedrock_client is None:
            import boto3
            self._bedrock_client = boto3.client(
                'bedrock-runtime',
                region_name=os.getenv('AWS_REGION', 'us-east-1')
            )
        return self._bedrock_client
    
    def is_mock_mode(self) -> bool:
        """Check if mock mode is enabled."""
        if _common_is_mock_mode is not None:
            return _common_is_mock_mode()
        return os.getenv("MOCK_MODE", "true").strip().lower() in {"true", "1", "yes", "on"}

    def analyze_request(self, user_request: str) -> Dict[str, str]:
        """
        Analyze user request to determine intent and complexity.
        
        Returns:
            {"intent": "workflow" | "text", "complexity": "단순" | "보통" | "복잡"}
        """
        if self.is_mock_mode():
            logger.info("MOCK_MODE: Forcing intent=workflow")
            return {"intent": "workflow", "complexity": "단순"}
        
        try:
            prompt = ANALYSIS_PROMPT.format(user_request=user_request)
            response = self.invoke_model(MODEL_HAIKU, prompt, "")
            
            # Extract text from response
            text = self._extract_text(response)
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
            
            return {"intent": "text", "complexity": "단순"}
            
        except Exception as e:
            logger.exception(f"analyze_request failed: {e}")
            return {"intent": "text", "complexity": "단순"}

    def invoke_model(
        self,
        model_id: str,
        user_prompt: str,
        system_prompt: str = "",
        max_tokens: int = 1024
    ) -> Dict[str, Any]:
        """
        Invoke a Bedrock model (Claude or Gemini).
        """
        if self.is_mock_mode():
            return self._mock_text_response()
        
        is_gemini = "gemini" in model_id.lower()
        
        if is_gemini:
            payload = {
                "text_generation_config": {"max_output_tokens": max_tokens},
                "contents": [{"role": "user", "parts": [{"text": user_prompt or " "}]}]
            }
            if system_prompt:
                payload["system_instruction"] = {"parts": [{"text": system_prompt}]}
        else:
            payload = {
                "max_tokens": max_tokens,
                "anthropic_version": "bedrock-2023-05-31",
                "messages": [{"role": "user", "content": user_prompt or " "}]
            }
            if system_prompt:
                payload["system"] = system_prompt
        
        response = self.bedrock_client.invoke_model(
            body=json.dumps(payload),
            modelId=model_id
        )
        return json.loads(response.get("body").read())

    def stream_workflow_generation(
        self,
        system_prompt: str,
        user_request: str,
        broadcast_fn: Optional[callable] = None
    ) -> Iterator[Dict[str, Any]]:
        """
        Stream workflow generation from LLM.
        
        Args:
            system_prompt: System prompt for the LLM
            user_request: User's workflow request
            broadcast_fn: Optional function to broadcast chunks (for WebSocket)
            
        Yields:
            Workflow components as dictionaries
        """
        if self.is_mock_mode():
            yield from self._mock_workflow_stream(broadcast_fn)
            return
        
        try:
            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": int(os.getenv("STREAM_MAX_TOKENS", "4096")),
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_request}]
            }
            
            response = self.bedrock_client.invoke_model_with_response_stream(
                modelId=MODEL_SONNET,
                body=json.dumps(payload)
            )
            
            stream = response.get("body")
            if not stream:
                return
            
            decoder = codecs.getincrementaldecoder("utf-8")()
            buffer = ""
            ui_delay = float(os.environ.get("STREAMING_UI_DELAY", "0.1"))
            
            for event in stream:
                chunk = event.get("chunk")
                if not chunk:
                    continue
                chunk_bytes = chunk.get("bytes")
                if not chunk_bytes:
                    continue
                
                try:
                    text = decoder.decode(chunk_bytes)
                except Exception:
                    continue
                
                buffer += text
                
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    
                    try:
                        json_obj = json.loads(line)
                        if broadcast_fn:
                            broadcast_fn(json_obj)
                        yield json_obj
                        
                        if ui_delay > 0:
                            time.sleep(ui_delay)
                    except json.JSONDecodeError:
                        pass
            
            # Process remaining buffer
            if buffer.strip():
                try:
                    json_obj = json.loads(buffer)
                    if broadcast_fn:
                        broadcast_fn(json_obj)
                    yield json_obj
                except json.JSONDecodeError:
                    pass
            
            # Emit done status
            done_obj = {"type": "status", "data": "done"}
            if broadcast_fn:
                broadcast_fn(done_obj)
            yield done_obj
            
        except Exception as e:
            logger.exception(f"stream_workflow_generation failed: {e}")
            error_obj = {"type": "error", "data": str(e)}
            if broadcast_fn:
                broadcast_fn(error_obj)
            yield error_obj

    # =========================================================================
    # Mock Helpers
    # =========================================================================
    
    def _mock_workflow_json(self) -> Dict[str, Any]:
        """Generate a mock workflow for testing."""
        return {
            "name": "Mock Workflow",
            "nodes": [
                {"id": "start", "type": "operator", "position": {"x": 150, "y": 50}, "data": {"label": "Start"}},
                {"id": "mock_llm", "type": "aiModel", "prompt_content": "목업 응답", "position": {"x": 150, "y": 150}},
                {"id": "end", "type": "operator", "position": {"x": 150, "y": 250}, "data": {"label": "End"}}
            ],
            "edges": [
                {"id": "e-start-mock_llm", "source": "start", "target": "mock_llm"},
                {"id": "e-mock_llm-end", "source": "mock_llm", "target": "end"}
            ]
        }
    
    def _mock_text_response(self) -> Dict[str, Any]:
        return {"content": [{"text": "목업 응답입니다."}]}
    
    def _mock_workflow_stream(self, broadcast_fn: Optional[callable]) -> Iterator[Dict[str, Any]]:
        """Stream mock workflow components."""
        mock_wf = self._mock_workflow_json()
        ui_delay = float(os.environ.get("STREAMING_UI_DELAY", "0.1"))
        
        for node in mock_wf.get("nodes", []):
            obj = {"type": "node", "data": node}
            if broadcast_fn:
                broadcast_fn(obj)
            yield obj
            if ui_delay > 0:
                time.sleep(ui_delay)
        
        for edge in mock_wf.get("edges", []):
            obj = {"type": "edge", "data": edge}
            if broadcast_fn:
                broadcast_fn(obj)
            yield obj
            if ui_delay > 0:
                time.sleep(ui_delay)
        
        done_obj = {"type": "status", "data": "done"}
        if broadcast_fn:
            broadcast_fn(done_obj)
        yield done_obj
    
    def _extract_text(self, response: Dict[str, Any]) -> Optional[str]:
        """Extract text from Bedrock response."""
        if isinstance(response, dict) and "content" in response:
            blocks = response.get("content", [])
            if blocks and isinstance(blocks[0], dict):
                return blocks[0].get("text")
        return None


# Singleton
_designer_service_instance = None

def get_designer_service() -> DesignerService:
    global _designer_service_instance
    if _designer_service_instance is None:
        _designer_service_instance = DesignerService()
    return _designer_service_instance


# =========================================================================
# 고수준 API 함수들 (핸들러에서 사용)
# =========================================================================

def analyze_request(user_request: str) -> Dict[str, str]:
    """
    사용자 요청의 intent와 complexity 분석
    
    핸들러에서 직접 호출 가능한 편의 함수
    """
    return get_designer_service().analyze_request(user_request)


def stream_workflow_jsonl(
    user_request: str,
    current_workflow: Optional[Dict[str, Any]] = None,
    canvas_mode: str = "agentic-designer"
) -> Iterator[str]:
    """
    워크플로우 생성 JSONL 스트리밍
    
    Args:
        user_request: 사용자 요청
        current_workflow: 현재 워크플로우 상태
        canvas_mode: Canvas 모드
        
    Yields:
        JSONL 형식의 응답 문자열
    """
    from services.design.prompts import SYSTEM_PROMPT, get_gemini_system_prompt
    
    service = get_designer_service()
    
    # 프롬프트 구성
    enhanced_prompt = f"""사용자 요청: {user_request}

현재 워크플로우:
{json.dumps(current_workflow or {}, ensure_ascii=False, indent=2)}

위 요청을 분석하여 완전한 워크플로우를 생성해주세요.
각 노드와 엣지를 JSONL 형식으로 순차적으로 출력하고,
마지막에 {{"type": "status", "data": "done"}}으로 완료를 알려주세요.
"""
    
    try:
        for obj in service.stream_workflow_generation(
            system_prompt=SYSTEM_PROMPT,
            user_request=enhanced_prompt,
            broadcast_fn=None
        ):
            yield json.dumps(obj, ensure_ascii=False) + "\n"
    except Exception as e:
        logger.error(f"Workflow streaming failed: {e}")
        yield json.dumps({"type": "error", "data": str(e)}) + "\n"


def build_text_response(user_request: str) -> Dict[str, Any]:
    """
    텍스트 응답 생성 (워크플로우가 아닌 일반 질문)
    
    Returns:
        Lambda 응답 형식의 딕셔너리
    """
    service = get_designer_service()
    
    try:
        response = service.invoke_model(
            model_id=MODEL_HAIKU,
            user_prompt=user_request,
            system_prompt="",
            max_tokens=1024
        )
        
        text = service._extract_text(response)
        if not text:
            text = "모델로부터 응답을 받지 못했습니다."
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "complexity": "단순",
                "response": {"text": text}
            })
        }
    except Exception as e:
        logger.exception("텍스트 응답 생성 중 오류")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
