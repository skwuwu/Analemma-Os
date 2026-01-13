"""
Task Manager API Handler

Task Manager UI를 위한 Lambda 핸들러입니다.
기존 notification 시스템을 활용하여 비즈니스 친화적인 Task 정보를 제공합니다.
"""

import json
import logging
import os
import asyncio
from typing import Dict, Any, Optional

# 서비스 임포트
try:
    from src.services.task_service import TaskService
    from src.common.auth_utils import extract_owner_id_from_event
except ImportError:
    from src.services.task_service import TaskService
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


def _get_query_param(event: Dict, param: str, default: Any = None) -> Any:
    """쿼리 파라미터 안전 추출"""
    query_params = event.get('queryStringParameters') or {}
    return query_params.get(param, default)


def _get_path_param(event: Dict, param: str) -> Optional[str]:
    """경로 파라미터 안전 추출"""
    path_params = event.get('pathParameters') or {}
    return path_params.get(param)


async def lambda_handler(event, context):
    """Task Manager API Lambda 핸들러"""
    
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
    task_id = _get_path_param(event, 'id')
    
    logger.info(f"Task API: {http_method} {path} (owner={owner_id[:8]}...)")
    
    try:
        # TaskService 초기화
        task_service = TaskService()
        
        # 라우팅
        if task_id:
            # GET /tasks/{id} - Task 상세 조회
            if http_method == 'GET':
                return await handle_get_task_detail(task_service, task_id, owner_id, event)
            else:
                return _response(405, {'error': f'Method {http_method} not allowed'})
        else:
            # GET /tasks - Task 목록 조회
            if http_method == 'GET':
                return await handle_list_tasks(task_service, owner_id, event)
            else:
                return _response(405, {'error': f'Method {http_method} not allowed'})
                
    except Exception as e:
        logger.exception(f"Task API error: {e}")
        return _response(500, {'error': 'Internal server error'})


async def handle_list_tasks(
    task_service: TaskService,
    owner_id: str,
    event: Dict
) -> Dict:
    """Task 목록 조회 핸들러"""
    
    # 쿼리 파라미터 파싱
    status_filter = _get_query_param(event, 'status')
    limit = int(_get_query_param(event, 'limit', 50))
    include_completed = _get_query_param(event, 'include_completed', 'true').lower() == 'true'
    
    # 제한값 검증
    limit = max(1, min(limit, 100))
    
    try:
        tasks = await task_service.get_tasks(
            owner_id=owner_id,
            status_filter=status_filter,
            limit=limit,
            include_completed=include_completed
        )
        
        response_body = {
            'tasks': tasks,
            'count': len(tasks),
            'filters': {
                'status': status_filter,
                'include_completed': include_completed
            }
        }
        
        logger.info(f"Retrieved {len(tasks)} tasks for user {owner_id[:8]}...")
        return _response(200, response_body)
        
    except Exception as e:
        logger.error(f"Failed to list tasks: {e}")
        return _response(500, {'error': 'Failed to retrieve tasks'})


async def handle_get_task_detail(
    task_service: TaskService,
    task_id: str,
    owner_id: str,
    event: Dict
) -> Dict:
    """Task 상세 조회 핸들러"""
    
    # 쿼리 파라미터 파싱
    include_technical_logs = _get_query_param(event, 'include_technical_logs', 'false').lower() == 'true'
    
    try:
        task_detail = await task_service.get_task_detail(
            task_id=task_id,
            owner_id=owner_id,
            include_technical_logs=include_technical_logs
        )
        
        if not task_detail:
            return _response(404, {'error': 'Task not found'})
        
        logger.info(f"Retrieved task detail for {task_id[:8]}... (user={owner_id[:8]}...)")
        return _response(200, task_detail)
        
    except Exception as e:
        logger.error(f"Failed to get task detail: {e}")
        return _response(500, {'error': 'Failed to retrieve task detail'})


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