"""
CoDesign Worker Handler

비동기로 실제 워크플로우 생성을 처리하고 결과를 WebSocket으로 전송합니다.
API Gateway 타임아웃을 회피하기 위한 Worker 패턴 구현.
"""

import json
import logging
import os
import asyncio
import time
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 서비스 임포트
try:
    from src.services.design.codesign_assistant import (
        stream_codesign_response,
        get_or_create_context
    )
    from src.common.websocket_utils import (
        get_connections_for_owner,
        send_to_connection,
        get_websocket_endpoint
    )
    from src.common.aws_clients import get_dynamodb_resource
except ImportError as e:
    logger.error(f"Failed to import dependencies: {e}")


def lambda_handler(event, context):
    """
    CoDesign Worker Lambda Handler
    
    비동기로 호출되어 실제 워크플로우 생성을 처리합니다.
    """
    try:
        logger.info(f"CoDesign Worker started: {json.dumps(event)}")
        
        # 이벤트 데이터 추출
        task_id = event.get('task_id')
        owner_id = event.get('owner_id')
        workflow_data = event.get('workflow_data', {})
        user_message = event.get('user_message', '')
        
        if not task_id or not owner_id:
            logger.error("Missing task_id or owner_id")
            return {'statusCode': 400, 'body': 'Missing required parameters'}
        
        # ExecutionsTable 초기화
        executions_table_name = os.environ.get('EXECUTIONS_TABLE')
        if not executions_table_name:
            raise ValueError("EXECUTIONS_TABLE not configured")
        
        dynamodb = get_dynamodb_resource()
        executions_table = dynamodb.Table(executions_table_name)
        
        # 상태를 RUNNING으로 업데이트
        _update_task_status(executions_table, owner_id, task_id, 'RUNNING', 'CoDesign 처리 중...')
        
        # 실제 CoDesign 처리 (동기 실행)
        try:
            result = _process_codesign(workflow_data, user_message, owner_id)
            
            # 성공 시 ExecutionsTable 업데이트
            _update_task_status(
                executions_table, 
                owner_id, 
                task_id, 
                'SUCCEEDED',
                'CoDesign 완료',
                result
            )
            
            # WebSocket으로 결과 전송
            _send_result_via_websocket(owner_id, task_id, result, 'success')
            
            logger.info(f"CoDesign worker completed successfully for task {task_id}")
            return {'statusCode': 200, 'body': 'Success'}
            
        except Exception as e:
            logger.exception(f"CoDesign processing failed: {e}")
            
            # 실패 시 ExecutionsTable 업데이트
            _update_task_status(
                executions_table,
                owner_id,
                task_id,
                'FAILED',
                f'CoDesign 실패: {str(e)}'
            )
            
            # WebSocket으로 에러 전송
            _send_result_via_websocket(owner_id, task_id, {'error': str(e)}, 'error')
            
            return {'statusCode': 500, 'body': f'Processing failed: {str(e)}'}
            
    except Exception as e:
        logger.exception(f"Worker handler failed: {e}")
        return {'statusCode': 500, 'body': str(e)}


def _process_codesign(workflow_data: Dict[str, Any], user_message: str, owner_id: str) -> Dict[str, Any]:
    """
    실제 CoDesign 처리
    
    Returns:
        완성된 워크플로우 데이터
    """
    logger.info("Starting CoDesign processing...")
    
    # 컨텍스트 생성
    context = get_or_create_context(owner_id)
    
    # 워크플로우 생성 (제너레이터를 리스트로 수집)
    chunks = []
    for chunk in stream_codesign_response(
        workflow_data=workflow_data,
        user_message=user_message,
        context=context,
        owner_id=owner_id
    ):
        chunks.append(chunk)
        logger.debug(f"Generated chunk: {chunk.get('type')}")
    
    # 최종 워크플로우 추출
    workflow_result = None
    for chunk in reversed(chunks):
        if chunk.get('type') == 'workflow':
            workflow_result = chunk.get('data')
            break
    
    if not workflow_result:
        raise ValueError("No workflow generated from CoDesign service")
    
    logger.info(f"CoDesign processing completed, workflow nodes: {len(workflow_result.get('nodes', []))}")
    
    return {
        'workflow': workflow_result,
        'chunks': chunks,  # 전체 청크 포함 (프론트엔드가 재생 가능)
        'total_chunks': len(chunks)
    }


def _update_task_status(
    table,
    owner_id: str,
    task_id: str,
    status: str,
    message: str,
    result_data: Dict[str, Any] = None
):
    """ExecutionsTable에 태스크 상태 업데이트"""
    try:
        execution_arn = f'arn:aws:states:us-east-1:000000000000:execution:codesign:{task_id}'
        
        update_expr = 'SET #status = :status, #msg = :msg, stopDate = :stop_date'
        expr_values = {
            ':status': status,
            ':msg': message,
            ':stop_date': datetime.now().isoformat()
        }
        
        if result_data:
            update_expr += ', output = :output'
            expr_values[':output'] = json.dumps(result_data)
        
        table.update_item(
            Key={
                'ownerId': owner_id,
                'executionArn': execution_arn
            },
            UpdateExpression=update_expr,
            ExpressionAttributeNames={
                '#status': 'status',
                '#msg': 'message'
            },
            ExpressionAttributeValues=expr_values
        )
        
        logger.info(f"Updated task {task_id} status to {status}")
        
    except Exception as e:
        logger.error(f"Failed to update task status: {e}")


def _send_result_via_websocket(
    owner_id: str,
    task_id: str,
    result_data: Dict[str, Any],
    result_type: str
):
    """WebSocket으로 결과 전송"""
    try:
        endpoint = get_websocket_endpoint()
        if not endpoint:
            logger.warning("WebSocket endpoint not configured, skipping notification")
            return
        
        # 사용자의 활성 연결 조회
        connections = get_connections_for_owner(owner_id)
        
        if not connections:
            logger.info(f"No active WebSocket connections for owner {owner_id}")
            return
        
        # 메시지 페이로드
        payload = {
            'type': 'codesign_result',
            'task_id': task_id,
            'result_type': result_type,
            'data': result_data,
            'timestamp': int(time.time())
        }
        
        # 모든 활성 연결에 전송
        for conn_id in connections:
            try:
                send_to_connection(endpoint, conn_id, payload)
                logger.info(f"Sent codesign result to connection {conn_id}")
            except Exception as e:
                logger.warning(f"Failed to send to connection {conn_id}: {e}")
        
    except Exception as e:
        logger.error(f"Failed to send WebSocket notification: {e}")
