import os
import json
import logging
import boto3

# 공통 모듈에서 AWS 클라이언트 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource
    dynamodb = get_dynamodb_resource()
except ImportError:
    dynamodb = boto3.resource('dynamodb')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    WebSocket $disconnect handler.

    Removes the connectionId entry from src.the DynamoDB connections table.
    """
    table_name = os.environ.get('WEBSOCKET_CONNECTIONS_TABLE')
    if not table_name:
        logger.error('WEBSOCKET_CONNECTIONS_TABLE not configured')
        return {'statusCode': 500, 'body': 'Server misconfiguration'}

    connection_id = event.get('requestContext', {}).get('connectionId')
    if not connection_id:
        logger.error('No connectionId in requestContext; requestContext=%s', event.get('requestContext'))
        return {'statusCode': 400, 'body': 'Missing connectionId'}

    try:
        logger.info('Removing websocket connection from src.DDB table=%s connectionId=%s', table_name, connection_id)
        table = dynamodb.Table(table_name)
        resp = table.delete_item(Key={'connectionId': connection_id})
        logger.debug('DynamoDB delete_item response: %s', resp)
    except Exception as e:
        # Deletion failures are non-fatal for API Gateway disconnects; log details for troubleshooting
        logger.exception('Failed to remove connection %s from src.table %s: %s', connection_id, table_name, e)

    return {
        'statusCode': 200,
        'body': 'Disconnected.'
    }
