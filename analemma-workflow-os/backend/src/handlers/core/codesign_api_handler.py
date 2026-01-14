"""
Co-design Assistant API Handler

양방향 협업 워크플로우 설계를 위한 스트리밍 API 핸들러입니다.
Canvas가 비어있을 때는 Agentic Designer 모드로 자동 전환됩니다.
"""

import json
import logging
import os
import asyncio
from typing import Dict, Any, Optional, Generator

# 서비스 임포트
try:
    from src.services.design.codesign_assistant import (
        stream_codesign_response,
        explain_workflow,
        generate_suggestions,
        apply_suggestion,
        get_or_create_context
    )
    from src.handlers.core.agentic_designer_handler import (
        invoke_bedrock_model_stream,
        SYSTEM_PROMPT,
        MODEL_SONNET,
        _is_mock_mode,
        _mock_workflow_json
    )
    from src.common.model_router import get_model_for_canvas_mode
    from src.common.auth_utils import extract_owner_id_from_event
except ImportError:
    from src.services.design.codesign_assistant import (
        stream_codesign_response,
        explain_workflow,
        generate_suggestions,
        apply_suggestion,
        get_or_create_context
    )
    from src.handlers.core.agentic_designer_handler import (
        invoke_bedrock_model_stream,
        SYSTEM_PROMPT,
        MODEL_SONNET,
        _is_mock_mode,
        _mock_workflow_json
    )
    from src.common.model_router import get_model_for_canvas_mode
    from src.common.auth_utils import extract_owner_id_from_event

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def _response(status_code: int, body: Any, headers: Dict = None) -> Dict:
    """Lambda proxy response 생성"""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
            **(headers or {})
        },
        'body': json.dumps(body, ensure_ascii=False, default=str) if body else ''
    }


def _streaming_response(generator: Generator[str, None, None]) -> Dict:
    """스트리밍 응답 생성"""
    # Lambda에서는 실제 스트리밍이 제한적이므로 
    # 모든 청크를 수집해서 한 번에 반환
    chunks = []
    try:
        for chunk in generator:
            chunks.append(chunk)
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        chunks.append(json.dumps({"type": "error", "data": str(e)}) + "\n")
    
    # JSONL 형식으로 결합
    response_body = ''.join(chunks)
    
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/x-ndjson',  # JSONL MIME type
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
        },
        'body': response_body
    }


def _parse_body(event: Dict) -> tuple[Optional[Dict], Optional[str]]:
    """요청 바디 파싱"""
    raw_body = event.get('body')
    if not raw_body:
        return {}, None
    
    try:
        parsed = json.loads(raw_body)
        if not isinstance(parsed, dict):
            return None, 'Request body must be a JSON object'
        return parsed, None
    except json.JSONDecodeError as e:
        return None, f'Invalid JSON: {str(e)}'


def _get_query_param(event: Dict, param: str, default: Any = None) -> Any:
    """쿼리 파라미터 안전 추출"""
    query_params = event.get('queryStringParameters') or {}
    return query_params.get(param, default)


async def lambda_handler(event, context):
    """Co-design Assistant API Lambda 핸들러"""
    
    # CORS preflight 처리
    http_method = event.get('httpMethod') or event.get('requestContext', {}).get('http', {}).get('method')
    if http_method == 'OPTIONS':
        return _response(200, None)
    
    # 인증 확인
    owner_id = extract_owner_id_from_event(event)
    if not owner_id:
        return _response(401, {'error': 'Authentication required'})
    
    # 경로 및 메소드 추출
    path = event.get('path') or event.get('rawPath', '')
    
    logger.info(f"Co-design API: {http_method} {path} (owner={owner_id[:8]}...)")
    
    try:
        # 라우팅
        if path.endswith('/codesign') or path.endswith('/codesign/'):
            # POST /codesign - 협업 워크플로우 설계
            if http_method == 'POST':
                return await handle_codesign_stream(owner_id, event)
            else:
                return _response(405, {'error': f'Method {http_method} not allowed'})
                
        elif path.endswith('/explain'):
            # POST /codesign/explain - 워크플로우 설명
            if http_method == 'POST':
                return await handle_explain_workflow(owner_id, event)
            else:
                return _response(405, {'error': f'Method {http_method} not allowed'})
                
        elif path.endswith('/suggestions'):
            # POST /codesign/suggestions - 제안 생성
            if http_method == 'POST':
                return await handle_generate_suggestions(owner_id, event)
            else:
                return _response(405, {'error': f'Method {http_method} not allowed'})
                
        elif '/apply-suggestion' in path:
            # POST /codesign/apply-suggestion - 제안 적용
            if http_method == 'POST':
                return await handle_apply_suggestion(owner_id, event)
            else:
                return _response(405, {'error': f'Method {http_method} not allowed'})
        else:
            return _response(404, {'error': 'Endpoint not found'})
                
    except Exception as e:
        logger.exception(f"Co-design API error: {e}")
        return _response(500, {'error': 'Internal server error'})


def _generate_initial_workflow_stream(user_request: str, owner_id: str, session_id: str) -> Generator[str, None, None]:
    """
    빈 Canvas에서 초기 워크플로우를 생성하는 스트리밍 함수
    Agentic Designer 로직을 사용하여 완전한 워크플로우를 생성합니다.
    """
    try:
        # Mock 모드 처리
        if _is_mock_mode():
            logger.info("Mock mode: generating sample workflow")
            mock_workflow = _mock_workflow_json()
            
            # 노드들을 순차적으로 스트리밍
            for node in mock_workflow.get("nodes", []):
                yield json.dumps({"type": "node", "data": node}) + "\n"
            
            # 엣지들을 순차적으로 스트리밍
            for edge in mock_workflow.get("edges", []):
                yield json.dumps({"type": "edge", "data": edge}) + "\n"
            
            # 완료 신호
            yield json.dumps({"type": "status", "data": "done"}) + "\n"
            return
        
        # 동적 모델 선택 (Agentic Designer 모드)
        selected_model_id = get_model_for_canvas_mode(
            canvas_mode="agentic-designer",
            current_workflow={"nodes": [], "edges": []},
            user_request=user_request
        )
        
        logger.info(f"Using model for Agentic Designer: {selected_model_id}")
        
        # 실제 Bedrock 스트리밍 호출
        system_prompt = SYSTEM_PROMPT
        enhanced_prompt = f"""사용자 요청: {user_request}

위 요청을 분석하여 완전한 워크플로우를 생성해주세요. 
각 노드와 엣지를 JSONL 형식으로 순차적으로 출력하고, 
마지막에 {{"type": "status", "data": "done"}}으로 완료를 알려주세요.

레이아웃 규칙을 준수하여 X=150, Y는 50부터 시작해서 100씩 증가시켜주세요."""
        
        # 선택된 모델로 Bedrock 스트리밍 호출
        for chunk in invoke_bedrock_model_stream(system_prompt, enhanced_prompt):
            yield chunk
            
    except Exception as e:
        logger.error(f"Initial workflow generation failed: {e}")
        # 에러 발생 시 기본 워크플로우 반환
        error_response = {
            "type": "error", 
            "data": f"워크플로우 생성 중 오류가 발생했습니다: {str(e)}"
        }
        yield json.dumps(error_response) + "\n"


async def handle_codesign_stream(owner_id: str, event: Dict) -> Dict:
    """협업 워크플로우 설계 스트리밍 (Canvas 상태에 따른 자동 모드 전환)"""
    body, error = _parse_body(event)
    if error:
        return _response(400, {'error': error})
    
    # 필수 필드 검증
    user_request = body.get('user_request')
    current_workflow = body.get('current_workflow', {"nodes": [], "edges": []})
    
    if not user_request:
        return _response(400, {'error': 'user_request is required'})
    
    try:
        # Canvas 상태 확인 - 비어있는지 판단 (개선된 로직)
        nodes = current_workflow.get('nodes', [])
        edges = current_workflow.get('edges', [])
        recent_changes = body.get('recent_changes', [])
        session_id = body.get('session_id') or f"session_{owner_id}_{int(asyncio.get_event_loop().time())}"
        
        # 세션 컨텍스트 확인 (대화 기록이 있는지)
        has_conversation_history = len(recent_changes) > 0
        is_physically_empty = len(nodes) == 0 and len(edges) == 0
        
        # 정교한 모드 결정 로직
        if is_physically_empty and not has_conversation_history:
            # 진짜 빈 Canvas - Agentic Designer 모드
            canvas_mode = "agentic-designer"
            logger.info("Empty canvas with no history - using Agentic Designer mode")
        elif is_physically_empty and has_conversation_history:
            # Canvas는 비어있지만 대화 기록 존재 - Co-design 모드 유지
            canvas_mode = "co-design"
            logger.info("Empty canvas but has conversation history - maintaining Co-design mode")
        else:
            # 기존 워크플로우 존재 - Co-design 모드
            canvas_mode = "co-design"
            logger.info("Existing workflow detected - using Co-design mode")
        
        logger.info(f"Canvas analysis: nodes={len(nodes)}, edges={len(edges)}, "
                   f"changes={len(recent_changes)}, mode={canvas_mode}")
        
        if canvas_mode == "agentic-designer":
            # Canvas가 비어있고 대화 기록도 없음 - Agentic Designer 모드로 전환
            logger.info(f"Empty canvas detected - switching to Agentic Designer mode")
            generator = _generate_initial_workflow_stream(
                user_request=user_request,
                owner_id=owner_id,
                session_id=session_id
            )
        else:
            # Canvas에 내용이 있거나 대화 기록이 있음 - Co-design 모드 사용
            logger.info(f"Using Co-design mode (canvas_mode={canvas_mode})")
            
            # Co-design 모드용 모델 선택
            selected_model_id = get_model_for_canvas_mode(
                canvas_mode="co-design",
                current_workflow=current_workflow,
                user_request=user_request,
                recent_changes=recent_changes
            )
            logger.info(f"Using model for Co-design: {selected_model_id}")
            
            generator = stream_codesign_response(
                user_request=user_request,
                current_workflow=current_workflow,
                recent_changes=recent_changes,
                session_id=session_id,
                connection_ids=None  # WebSocket 연결 ID는 별도 처리
            )
        
        logger.info(f"Started workflow streaming for session {session_id[:8]}...")
        return _streaming_response(generator)
        
    except Exception as e:
        logger.error(f"Failed to start workflow streaming: {e}")
        return _response(500, {'error': 'Failed to start workflow streaming'})


async def handle_explain_workflow(owner_id: str, event: Dict) -> Dict:
    """워크플로우 설명 생성"""
    body, error = _parse_body(event)
    if error:
        return _response(400, {'error': error})
    
    # 필수 필드 검증
    workflow = body.get('workflow')
    if not workflow:
        return _response(400, {'error': 'workflow is required'})
    
    try:
        explanation = explain_workflow(workflow)
        
        logger.info(f"Generated workflow explanation")
        return _response(200, explanation)
        
    except Exception as e:
        logger.error(f"Failed to explain workflow: {e}")
        return _response(500, {'error': 'Failed to explain workflow'})


async def handle_generate_suggestions(owner_id: str, event: Dict) -> Dict:
    """워크플로우 최적화 제안 생성"""
    body, error = _parse_body(event)
    if error:
        return _response(400, {'error': error})
    
    # 필수 필드 검증
    workflow = body.get('workflow')
    if not workflow:
        return _response(400, {'error': 'workflow is required'})
    
    try:
        max_suggestions = body.get('max_suggestions', 5)
        suggestions = []
        
        # 제안 생성 (Generator를 리스트로 변환)
        for suggestion in generate_suggestions(workflow, max_suggestions):
            suggestions.append(suggestion)
        
        response = {
            'suggestions': suggestions,
            'count': len(suggestions)
        }
        
        logger.info(f"Generated {len(suggestions)} suggestions")
        return _response(200, response)
        
    except Exception as e:
        logger.error(f"Failed to generate suggestions: {e}")
        return _response(500, {'error': 'Failed to generate suggestions'})


async def handle_apply_suggestion(owner_id: str, event: Dict) -> Dict:
    """제안 적용"""
    body, error = _parse_body(event)
    if error:
        return _response(400, {'error': error})
    
    # 필수 필드 검증
    workflow = body.get('workflow')
    suggestion = body.get('suggestion')
    
    if not workflow or not suggestion:
        return _response(400, {'error': 'workflow and suggestion are required'})
    
    try:
        modified_workflow = apply_suggestion(workflow, suggestion)
        
        response = {
            'modified_workflow': modified_workflow,
            'applied_suggestion': suggestion
        }
        
        logger.info(f"Applied suggestion {suggestion.get('id', 'unknown')}")
        return _response(200, response)
        
    except Exception as e:
        logger.error(f"Failed to apply suggestion: {e}")
        return _response(500, {'error': 'Failed to apply suggestion'})


# 동기 래퍼 (Lambda는 비동기 핸들러를 직접 지원하지 않음)
def lambda_handler_sync(event, context):
    """동기 Lambda 핸들러 래퍼"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(lambda_handler(event, context))
    finally:
        loop.close()


# Lambda 엔트리포인트
lambda_handler = lambda_handler_sync
