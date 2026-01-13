import os
import json
import logging
import boto3
import time as _time
from typing import Dict, Any
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 공통 유틸리티 모듈 import (Lambda 환경에서는 상대 경로 import 불가)
try:
    from src.common.auth_utils import validate_token
    from src.common.constants import DynamoDBConfig
except ImportError:
    try:
        from src.common.auth_utils import validate_token
        from src.common.constants import DynamoDBConfig
    except ImportError:
        # Fallback: validate_token이 없으면 더미 함수 (개발/테스트용)
        def validate_token(*args, **kwargs):
            return None


dynamodb = boto3.resource('dynamodb')

# Import exec_status_helper with fallback pattern used by other Lambda functions
try:
    from src.common.exec_status_helper import (
        build_status_payload,
        ExecutionForbidden,
        ExecutionNotFound,
    )
except ImportError:
    try:
        from src.common.exec_status_helper import (
            build_status_payload,
            ExecutionForbidden,
            ExecutionNotFound,
        )
    except ImportError:
        # Last resort: define minimal fallbacks
        def build_status_payload(*args, **kwargs):
            return {"error": "exec_status_helper not available"}
        class ExecutionForbidden(Exception):
            pass
        class ExecutionNotFound(Exception):
            pass


def lambda_handler(event, context):
    """
    WebSocket $connect handler.

    Expects either:
      - ownerId provided as a query string parameter ?ownerId=..., or
      - API Gateway JWT authorizer injecting requestContext.authorizer.jwt.claims.sub

    Stores a mapping { connectionId -> ownerId } in the DynamoDB table specified
    by the WEBSOCKET_CONNECTIONS_TABLE env var.
    """
    # Helpful debug: log basic event keys so we can trace connect attempts
    try:
        logger.info('WebSocket $connect invoked; requestContext keys: %s', list(event.get('requestContext', {}).keys()))
        logger.debug('Full $connect event: %s', json.dumps(event, default=str)[:2000])
    except Exception:
        # defensive: ensure logging never raises
        logger.exception('Failed to log incoming connect event')

    table_name = os.environ.get('WEBSOCKET_CONNECTIONS_TABLE')
    if not table_name:
        logger.error('WEBSOCKET_CONNECTIONS_TABLE not configured')
        return {'statusCode': 500, 'body': 'Server misconfiguration'}

    connection_id = event.get('requestContext', {}).get('connectionId')
    if not connection_id:
        logger.error('No connectionId in requestContext; event.requestContext=%s', event.get('requestContext'))
        return {'statusCode': 400, 'body': 'Missing connectionId'}

    # --- [Trust Model] Authorizer가 검증한 Identity 사용 ---
    # WebsocketAuthorizerFunction이 검증 후 principalId에 ownerId를 담아 전달함
    try:
        qs = event.get('queryStringParameters') or {}
        # executionArn is optional, used for connecting to a specific run
        execution_arn = qs.get('executionArn') or qs.get('execution_arn')

        authorizer_ctx = event.get('requestContext', {}).get('authorizer', {})
        # [FIX] Lambda Authorizer의 context 객체가 authorizer로 전달됨
        # principalId는 최상위에 있지만 API Gateway는 context만 전달함
        owner_id = authorizer_ctx.get('ownerId') or authorizer_ctx.get('principalId')
        
        # Fallback for local testing or misconfiguration
        if not owner_id:
            logger.warning("No principalId in requestContext.authorizer - check Authorizer configuration")
            # For strict security, we could reject here. 
            # But if Authorizer is disabled (e.g. dev), we might allow anonymous or fail naturally later.
            # Given we rely on Authorizer, if it's missing, it's an error.
            if not os.getenv('MOCK_MODE'):
                return {'statusCode': 401, 'body': 'Unauthorized: Missing identity'}
            
    except Exception as e:
        logger.error(f"Failed to retrieve identity from src.Authorizer context: {e}")
        return {'statusCode': 401, 'body': 'Unauthorized'}


    try:
        table = dynamodb.Table(table_name)
        item = {'connectionId': connection_id}
        if owner_id:
            item['ownerId'] = owner_id
        if execution_arn:
            item['executionArn'] = execution_arn

        # TTL: 2 hours from src.now (using constant)
        try:
            from src.common.constants import TTLConfig
            item['ttl'] = int(_time.time()) + TTLConfig.WEBSOCKET_CONNECTION
        except ImportError:
            item['ttl'] = int(_time.time()) + 7200  # Fallback

        logger.info('Persisting websocket connection to DDB table=%s connectionId=%s ownerId=%s', table_name, connection_id, owner_id)
        table.put_item(Item=item)
        
        # 연결 성공 후 미전송 알림 전송 시도
        if owner_id:
            _send_pending_notifications(owner_id, connection_id, event)
            if execution_arn:
                _send_status_snapshot(owner_id, connection_id, execution_arn, event)
            
    except Exception as e:
        logger.exception('Failed to persist connection %s to table %s: %s', connection_id, table_name, e)
        return {'statusCode': 500, 'body': 'Failed to register connection'}

    return {
        'statusCode': 200,
        'body': 'Connected.'
    }


def _send_pending_notifications(owner_id: str, connection_id: str, event: dict):
    """
    새로운 WebSocket 연결이 성공했을 때, 해당 사용자의 미전송 알림을 전송합니다.
    """
    pending_table_name = os.environ.get('PENDING_NOTIFICATIONS_TABLE', 'PendingNotifications')
    
    try:
        pending_table = dynamodb.Table(pending_table_name)
        
        # ownerId/status GSI로 'pending' 항목만 조회
        from boto3.dynamodb.conditions import Key
        index_name = DynamoDBConfig.OWNER_ID_STATUS_INDEX
        response = pending_table.query(
            IndexName=index_name,
            KeyConditionExpression=(
                Key('ownerId').eq(owner_id) & Key('status').eq('pending')
            )
        )
        
        items = response.get('Items', [])
        if not items:
            logger.info(f'No pending notifications for owner={owner_id}')
            return
        
        logger.info(f'Found {len(items)} pending notifications for owner={owner_id}')
        
        apigw_client = _create_apigw_client_from_event(event)
        if not apigw_client:
            logger.warning('Cannot send pending notifications: missing domainName')
            return
        
        # 각 미전송 알림 전송
        sent_count = 0
        for item in items:
            notification_id = item.get('notificationId')
            notification_payload = item.get('notification', {})
            
            try:
                # WebSocket으로 전송
                apigw_client.post_to_connection(
                    ConnectionId=connection_id,
                    Data=json.dumps(notification_payload)
                )
                
                # 전송 성공하면 상태 업데이트
                pending_table.update_item(
                    Key={
                        'ownerId': owner_id,
                        'notificationId': notification_id
                    },
                    UpdateExpression='SET #status = :sent, sentAt = :sentAt',
                    ExpressionAttributeNames={'#status': 'status'},
                    ExpressionAttributeValues={
                        ':sent': 'sent',
                        ':sentAt': int(_time.time())
                    }
                )
                sent_count += 1
                logger.info(f'Sent pending notification {notification_id} to connection {connection_id}')
                
            except Exception as e:
                logger.warning(f'Failed to send pending notification {notification_id}: {e}')
        
        logger.info(f'Successfully sent {sent_count}/{len(items)} pending notifications for owner={owner_id}')
        
    except Exception as e:
        logger.error(f'Error processing pending notifications for owner={owner_id}: {e}')


def _create_apigw_client_from_event(event: dict):
    request_context = event.get('requestContext', {})
    domain_name = request_context.get('domainName')
    stage = request_context.get('stage', 'prod')
    if not domain_name:
        return None
    endpoint_url = f"https://{domain_name}/{stage}"
    return boto3.client('apigatewaymanagementapi', endpoint_url=endpoint_url)


def _send_status_snapshot(owner_id: str, connection_id: str, execution_arn: str, event: dict):
    apigw_client = _create_apigw_client_from_event(event)
    if not apigw_client:
        logger.warning('Cannot send status snapshot: missing domainName')
        return

    try:
        payload = build_status_payload(execution_arn, owner_id)
    except ExecutionNotFound:
        logger.info('Execution %s not found while sending status snapshot', execution_arn)
        return
    except ExecutionForbidden:
        logger.info('Execution %s not accessible for owner %s', execution_arn, owner_id)
        return
    except ClientError as exc:
        logger.exception('Failed to describe execution %s: %s', execution_arn, exc)
        return

    try:
        apigw_client.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(payload, ensure_ascii=False)
        )
        logger.info('Sent status snapshot for execution %s to connection %s', execution_arn, connection_id)
    except apigw_client.exceptions.GoneException:
        logger.info('Connection %s gone while sending status snapshot', connection_id)
    except Exception:
        logger.exception('Failed to send status snapshot to connection %s', connection_id)
