import json
import logging
import os
import uuid
import boto3
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from botocore.exceptions import ClientError

# 공통 모듈에서 AWS 클라이언트 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource
    dynamodb = get_dynamodb_resource()
except ImportError:
    dynamodb = boto3.resource('dynamodb')

# 로깅 설정
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 환경 변수
TASK_TOKENS_TABLE = os.environ.get('TASK_TOKENS_TABLE')
EXECUTIONS_TABLE = os.environ.get('EXECUTIONS_TABLE')
WEBSOCKET_CONNECTIONS_TABLE = os.environ.get('WEBSOCKET_CONNECTIONS_TABLE')
WEBSOCKET_ENDPOINT = os.environ.get('WEBSOCKET_ENDPOINT')

# AWS 클라이언트 초기화
apigateway = boto3.client('apigatewaymanagementapi', endpoint_url=WEBSOCKET_ENDPOINT) if WEBSOCKET_ENDPOINT else None

task_tokens_table = dynamodb.Table(TASK_TOKENS_TABLE) if TASK_TOKENS_TABLE else None
executions_table = dynamodb.Table(EXECUTIONS_TABLE) if EXECUTIONS_TABLE else None
connections_table = dynamodb.Table(WEBSOCKET_CONNECTIONS_TABLE) if WEBSOCKET_CONNECTIONS_TABLE else None


def lambda_handler(event, context):
    """
    DLQ에서 메시지를 재처리하여 장애 복구 수행
    SQS DLQ 트리거로 호출됨
    """
    try:
        logger.info("DLQ redrive handler started")

        batch_item_failures = []  # 실패한 메시지 ID들을 저장
        records = event.get('Records', [])

        for record in records:
            try:
                # SQS 메시지에서 원본 이벤트 추출
                body = json.loads(record['body'])
                original_event = body.get('originalEvent', body)  # DLQ에 저장된 원본 이벤트

                # 메시지 타입에 따라 처리
                message_type = determine_message_type(original_event)

                success = False
                if message_type == 'websocket_notification':
                    success = retry_websocket_notification(original_event)
                elif message_type == 'task_token_callback':
                    success = retry_task_token_callback(original_event)
                elif message_type == 'execution_update':
                    success = retry_execution_update(original_event)
                else:
                    logger.warning(f"Unknown message type: {message_type}")
                    # 알 수 없는 메시지는 재시도해도 소용없으므로 성공 처리(삭제)
                    success = True

                if not success:
                    # 로직 실패 시 실패 리스트에 추가
                    batch_item_failures.append({"itemIdentifier": record['messageId']})

            except Exception as e:
                logger.exception(f"Failed to process message {record.get('messageId')}: {e}")
                # 예외 발생 시 실패 리스트에 추가
                batch_item_failures.append({"itemIdentifier": record['messageId']})

        logger.info(f"DLQ processing complete: {len(records) - len(batch_item_failures)} processed, {len(batch_item_failures)} failed")

        # SQS에게 실패한 메시지만 다시 보내달라고 요청
        return {
            "batchItemFailures": batch_item_failures
        }

    except Exception as e:
        logger.exception(f"Error in DLQ redrive handler: {e}")
        # 전체 배치 실패 시 모든 메시지를 재시도하도록 설정
        message_ids = [record['messageId'] for record in event.get('Records', [])]
        return {
            "batchItemFailures": [{"itemIdentifier": msg_id} for msg_id in message_ids]
        }


def determine_message_type(event: Dict[str, Any]) -> str:
    """이벤트 타입 판별"""
    try:
        # WebSocket 알림 이벤트
        if 'source' in event and event['source'] == 'aws.states':
            detail = event.get('detail', {})
            if 'executionArn' in detail:
                return 'websocket_notification'

        # Task Token 콜백 이벤트
        if 'taskToken' in event:
            return 'task_token_callback'

        # 실행 업데이트 이벤트
        if 'executionId' in event and 'status' in event:
            return 'execution_update'

        # 기본값
        return 'unknown'

    except Exception as e:
        logger.error(f"Failed to determine message type: {e}")
        return 'unknown'


def retry_websocket_notification(event: Dict[str, Any]) -> bool:
    """WebSocket 알림 재시도"""
    try:
        detail = event.get('detail', {})
        execution_arn = detail.get('executionArn')

        if not execution_arn:
            logger.warning("No execution ARN in websocket notification event")
            return False

        # 실행 ID 추출
        execution_id = execution_arn.split(':')[-1]

        # 실행 정보 조회
        execution_info = get_execution_info(execution_id)
        if not execution_info:
            logger.warning(f"Execution not found: {execution_id}")
            return False

        owner_id = execution_info.get('ownerId')
        if not owner_id:
            logger.warning(f"No owner ID in execution: {execution_id}")
            return False

        # 사용자 연결 조회
        connection_ids = get_user_connection_ids(owner_id)
        if not connection_ids:
            logger.info(f"No active connections for user: {owner_id}")
            return True  # 연결이 없어도 성공으로 처리

        # WebSocket 메시지 전송 (멱등성 보장을 위해 executionId 포함)
        payload = create_websocket_payload(event, execution_info)

        success_count = 0
        for connection_id in connection_ids:
            try:
                apigateway.post_to_connection(
                    ConnectionId=connection_id,
                    Data=json.dumps(payload)
                )
                success_count += 1
            except ClientError as e:
                error_code = e.response['Error']['Code']
                if error_code == 'GoneException':
                    # 연결이 끊어진 경우 - 연결 삭제
                    remove_stale_connection(connection_id)
                    logger.info(f"Removed stale connection: {connection_id}")
                else:
                    logger.error(f"Failed to send to connection {connection_id}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error sending to connection {connection_id}: {e}")

        logger.info(f"Successfully sent notification to {success_count}/{len(connection_ids)} connections for execution {execution_id}")
        return success_count > 0

    except Exception as e:
        logger.exception(f"Failed to retry websocket notification: {e}")
        return False


def retry_task_token_callback(event: Dict[str, Any]) -> bool:
    """Task Token 콜백 재시도"""
    try:
        task_token = event.get('taskToken')
        result = event.get('result', {})
        error = event.get('error')

        if not task_token:
            logger.warning("No task token in callback event")
            return False

        # Step Functions에 결과 전송
        sf_client = boto3.client('stepfunctions')

        try:
            if error:
                # 에러인 경우
                sf_client.send_task_failure(
                    taskToken=task_token,
                    error=error.get('error', 'UnknownError'),
                    cause=error.get('cause', 'Task failed in DLQ retry')
                )
            else:
                # 성공인 경우
                sf_client.send_task_success(
                    taskToken=task_token,
                    output=json.dumps(result)
                )

            logger.info(f"Successfully retried task token callback: {task_token}")
            return True

        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code in ['TaskTimedOut', 'InvalidToken']:
                # 토큰이 만료되었거나 유효하지 않은 경우
                logger.warning(f"Task token expired or invalid: {task_token}, error: {error_code}")
                # 토큰이 만료된 경우 재시도해도 소용없으므로 성공으로 처리하여 DLQ에서 제거
                return True
            else:
                # 다른 클라이언트 에러는 재시도
                logger.error(f"Step Functions client error: {error_code}")
                raise

    except Exception as e:
        logger.exception(f"Failed to retry task token callback: {e}")
        return False


def retry_execution_update(event: Dict[str, Any]) -> bool:
    """실행 상태 업데이트 재시도"""
    try:
        execution_id = event.get('executionId')
        status = event.get('status')
        updates = event.get('updates', {})

        if not execution_id or not status:
            logger.warning("Missing execution ID or status in update event")
            return False

        # DynamoDB 업데이트
        if executions_table:
            update_expression = "SET #status = :status, updatedAt = :updatedAt"
            expression_attribute_names = {'#status': 'status'}
            expression_attribute_values = {
                ':status': status,
                ':updatedAt': int(datetime.now(timezone.utc).timestamp())
            }

            # 추가 업데이트 필드
            for key, value in updates.items():
                update_expression += f", {key} = :{key}"
                expression_attribute_values[f':{key}'] = value

            executions_table.update_item(
                Key={'executionId': execution_id},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values
            )

        logger.info(f"Successfully retried execution update: {execution_id}")
        return True

    except Exception as e:
        logger.exception(f"Failed to retry execution update: {e}")
        return False


def get_execution_info(execution_id: str) -> Optional[Dict[str, Any]]:
    """실행 정보 조회"""
    try:
        if executions_table:
            response = executions_table.get_item(Key={'executionId': execution_id})
            return response.get('Item')
        return None
    except Exception as e:
        logger.error(f"Failed to get execution info: {e}")
        return None


def get_user_connection_ids(owner_id: str) -> List[str]:
    """사용자의 활성 WebSocket 연결 ID 조회"""
    try:
        if not connections_table:
            logger.warning("WebSocket connections table not configured")
            return []

        # GSI를 사용한 ownerId 기반 쿼리
        response = connections_table.query(
            IndexName='ownerId-index',  # GSI 이름 (실제 환경에 맞게 조정)
            KeyConditionExpression=boto3.dynamodb.conditions.Key('ownerId').eq(owner_id)
        )

        connection_ids = []
        for item in response.get('Items', []):
            connection_id = item.get('connectionId')
            if connection_id:
                connection_ids.append(connection_id)

        logger.info(f"Found {len(connection_ids)} connections for user {owner_id}")
        return connection_ids

    except Exception as e:
        logger.exception(f"Failed to get user connections: {e}")
        return []


def create_websocket_payload(event: Dict[str, Any], execution_info: Dict[str, Any]) -> Dict[str, Any]:
    """WebSocket 메시지 페이로드 생성 (멱등성 보장을 위한 executionId 포함)"""
    try:
        detail = event.get('detail', {})
        state = detail.get('stateEnteredEventDetails', {}).get('name', 'UNKNOWN')

        return {
            'type': 'execution_progress',
            'executionId': execution_info.get('executionId'),
            'workflowId': execution_info.get('workflowId'),
            'status': state,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'messageId': str(uuid.uuid4()),  # 중복 메시지 필터링을 위한 고유 ID
            'progress': detail.get('stateEnteredEventDetails', {}).get('output', {})
        }

    except Exception as e:
        logger.exception(f"Failed to create websocket payload: {e}")
        return {
            'type': 'execution_progress',
            'status': 'ERROR',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'messageId': str(uuid.uuid4()),
            'error': 'Failed to create payload'
        }


def remove_stale_connection(connection_id: str):
    """끊어진 WebSocket 연결 삭제"""
    try:
        if connections_table:
            connections_table.delete_item(Key={'connectionId': connection_id})
            logger.info(f"Removed stale connection: {connection_id}")
    except Exception as e:
        logger.error(f"Failed to remove stale connection {connection_id}: {e}")