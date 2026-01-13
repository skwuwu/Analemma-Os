"""
WebSocket $default route handler.

Handles all messages that don't match specific routes ($connect, $disconnect).
This is required for WebSocket API to process client messages.
"""
import os
import json
import logging
import boto3
from typing import Dict, Any

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    WebSocket $default handler.
    
    Processes incoming messages from connected WebSocket clients.
    Supported actions:
      - ping: Returns pong (health check)
      - subscribe: Subscribe to execution updates
      - unsubscribe: Unsubscribe from execution updates
      - Any other action: Returns acknowledgement
    """
    try:
        logger.info('WebSocket $default invoked')
        logger.debug('Event: %s', json.dumps(event, default=str)[:2000])
        
        connection_id = event.get('requestContext', {}).get('connectionId')
        if not connection_id:
            logger.error('No connectionId in requestContext')
            return {'statusCode': 400, 'body': 'Missing connectionId'}
        
        # Parse message body
        body = event.get('body', '{}')
        try:
            message = json.loads(body) if body else {}
        except json.JSONDecodeError:
            logger.warning('Invalid JSON in message body: %s', body[:500])
            message = {}
        
        action = message.get('action', 'unknown')
        logger.info('Received action: %s from connection: %s', action, connection_id)
        
        # Handle different actions
        if action == 'ping':
            return _handle_ping(connection_id, event)
        elif action == 'subscribe':
            return _handle_subscribe(connection_id, message)
        elif action == 'unsubscribe':
            return _handle_unsubscribe(connection_id, message)
        else:
            # Default: acknowledge receipt
            return _handle_default(connection_id, action, message)
            
    except Exception as e:
        logger.exception('Error in $default handler: %s', str(e))
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}


def _handle_ping(connection_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Handle ping action - send pong response."""
    try:
        # Get API Gateway Management API endpoint
        domain = event.get('requestContext', {}).get('domainName')
        stage = event.get('requestContext', {}).get('stage')
        
        if domain and stage:
            endpoint_url = f'https://{domain}/{stage}'
            apigw_client = boto3.client(
                'apigatewaymanagementapi',
                endpoint_url=endpoint_url
            )
            
            apigw_client.post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps({'action': 'pong', 'timestamp': _get_timestamp()}).encode('utf-8')
            )
            logger.info('Sent pong to connection: %s', connection_id)
        
        return {'statusCode': 200, 'body': 'pong'}
    except Exception as e:
        logger.warning('Failed to send pong: %s', str(e))
        return {'statusCode': 200, 'body': 'pong'}


def _handle_subscribe(connection_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
    """Handle subscribe action - subscribe to execution updates."""
    execution_id = message.get('execution_id') or message.get('executionId')
    
    if not execution_id:
        logger.warning('Subscribe without execution_id from connection: %s', connection_id)
        return {'statusCode': 400, 'body': json.dumps({'error': 'Missing execution_id'})}
    
    table_name = os.environ.get('WEBSOCKET_CONNECTIONS_TABLE')
    if not table_name:
        logger.error('WEBSOCKET_CONNECTIONS_TABLE not configured')
        return {'statusCode': 500, 'body': 'Server misconfiguration'}
    
    try:
        table = dynamodb.Table(table_name)
        # Update connection record with subscribed execution
        table.update_item(
            Key={'connectionId': connection_id},
            UpdateExpression='SET subscribed_executions = list_append(if_not_exists(subscribed_executions, :empty), :exec_id)',
            ExpressionAttributeValues={
                ':empty': [],
                ':exec_id': [execution_id]
            }
        )
        logger.info('Connection %s subscribed to execution %s', connection_id, execution_id)
        return {'statusCode': 200, 'body': json.dumps({'subscribed': execution_id})}
    except Exception as e:
        logger.exception('Failed to subscribe: %s', str(e))
        return {'statusCode': 500, 'body': json.dumps({'error': str(e)})}


def _handle_unsubscribe(connection_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
    """Handle unsubscribe action - unsubscribe from execution updates."""
    execution_id = message.get('execution_id') or message.get('executionId')
    
    if not execution_id:
        logger.warning('Unsubscribe without execution_id from connection: %s', connection_id)
        return {'statusCode': 400, 'body': json.dumps({'error': 'Missing execution_id'})}
    
    logger.info('Connection %s unsubscribed from execution %s', connection_id, execution_id)
    return {'statusCode': 200, 'body': json.dumps({'unsubscribed': execution_id})}


def _handle_default(connection_id: str, action: str, message: Dict[str, Any]) -> Dict[str, Any]:
    """Handle unknown/default actions - acknowledge receipt."""
    logger.info('Received unknown action "%s" from connection %s', action, connection_id)
    return {
        'statusCode': 200,
        'body': json.dumps({
            'acknowledged': True,
            'action': action,
            'message': 'Action received'
        })
    }


def _get_timestamp() -> str:
    """Get current timestamp in ISO format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
