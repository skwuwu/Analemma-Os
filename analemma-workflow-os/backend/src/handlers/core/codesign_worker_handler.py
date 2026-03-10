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

# Critical imports (must succeed for handler to work)
from src.common.aws_clients import get_dynamodb_resource

# Optional service imports
try:
    from src.services.design.codesign_assistant import stream_codesign_response
    from src.common.websocket_utils import (
        get_connections_for_owner,
        send_to_connection,
        get_websocket_endpoint
    )
except ImportError as e:
    logger.error(f"Failed to import optional dependencies: {e}")


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
        _update_task_status(executions_table, owner_id, task_id, 'RUNNING', 'CoDesign processing...')
        
        # 실제 CoDesign 처리 (동기 실행)
        try:
            result = _process_codesign(workflow_data, user_message, owner_id)
            
            # 성공 시 ExecutionsTable 업데이트
            _update_task_status(
                executions_table, 
                owner_id, 
                task_id, 
                'SUCCEEDED',
                'CoDesign completed',
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
                f'CoDesign failed: {str(e)}'
            )
            
            # WebSocket으로 에러 전송
            _send_result_via_websocket(owner_id, task_id, {'error': str(e)}, 'error')
            
            return {'statusCode': 500, 'body': f'Processing failed: {str(e)}'}
            
    except Exception as e:
        logger.exception(f"Worker handler failed: {e}")
        return {'statusCode': 500, 'body': str(e)}


def _process_codesign(workflow_data: Dict[str, Any], user_message: str, owner_id: str) -> Dict[str, Any]:
    """
    Process CoDesign request by invoking the async streaming generator.

    Returns:
        Completed workflow data with all chunks.
    """
    logger.info("Starting CoDesign processing...")

    async def _run_async():
        chunks = []
        async for chunk in stream_codesign_response(
            user_request=user_message,
            current_workflow=workflow_data,
        ):
            chunks.append(chunk)
            logger.debug(f"Generated chunk type: {type(chunk)}")
        return chunks

    raw_chunks = asyncio.run(_run_async())

    # Parse JSONL strings into dicts and assemble workflow from node/edge chunks.
    # stream_codesign_response yields JSONL strings, not dicts.
    nodes = []
    edges = []
    parsed_chunks = []

    for raw in raw_chunks:
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
        elif isinstance(raw, dict):
            obj = raw
        else:
            continue

        if not isinstance(obj, dict):
            continue

        parsed_chunks.append(obj)
        chunk_type = obj.get('type')

        if chunk_type == 'node':
            nodes.append(obj.get('data', {}))
        elif chunk_type == 'edge':
            edges.append(obj.get('data', {}))
        elif chunk_type == 'workflow':
            # If the model ever emits a complete workflow chunk, use it directly
            wf = obj.get('data', {})
            nodes.extend(wf.get('nodes', []))
            edges.extend(wf.get('edges', []))

    logger.info(f"Parsed {len(parsed_chunks)} chunks: {len(nodes)} nodes, {len(edges)} edges")

    if not nodes:
        raise ValueError("No workflow generated from CoDesign service")

    workflow_result = {'nodes': nodes, 'edges': edges}

    return {
        'workflow': workflow_result,
        'chunks': parsed_chunks,
        'total_chunks': len(parsed_chunks)
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
                send_to_connection(conn_id, payload, endpoint)
                logger.info(f"Sent codesign result to connection {conn_id}")
            except Exception as e:
                logger.warning(f"Failed to send to connection {conn_id}: {e}")
        
    except Exception as e:
        logger.error(f"Failed to send WebSocket notification: {e}")
