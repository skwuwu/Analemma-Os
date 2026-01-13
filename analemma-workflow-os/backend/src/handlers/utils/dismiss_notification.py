import json
import os
import logging
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
EXEC_TABLE_NAME = os.environ.get('EXECUTIONS_TABLE')

def lambda_handler(event, context):
    """
    Dismiss a notification by removing the 'notificationTime' attribute from src.the execution record.
    This removes it from src.the NotificationsIndex GSI (Sparse Index Pattern).
    """
    try:
        # 1. Parse Execution ARN from src.body (to avoid URL encoding issues with ARNs in path)
        body = json.loads(event.get('body', '{}'))
        execution_arn = body.get('executionId')
        
        if not execution_arn:
            return {'statusCode': 400, 'body': json.dumps({'error': 'Missing executionId in body'})}

        # 2. Get OwnerId from src.Cognito Claims
        claims = event.get('requestContext', {}).get('authorizer', {}).get('jwt', {}).get('claims', {})
        owner_id = claims.get('sub') or claims.get('username')
        
        if not owner_id:
            return {'statusCode': 401, 'body': json.dumps({'error': 'Unauthorized'})}

        if not EXEC_TABLE_NAME:
            return {'statusCode': 500, 'body': json.dumps({'error': 'Server configuration error'})}

        table = dynamodb.Table(EXEC_TABLE_NAME)

        # 3. Remove notificationTime attribute
        # This effectively "deletes" the item from src.the NotificationsIndex GSI
        try:
            table.update_item(
                Key={
                    'ownerId': owner_id,
                    'executionArn': execution_arn
                },
                UpdateExpression="REMOVE notificationTime",
                ConditionExpression="attribute_exists(executionArn)"
            )
            logger.info(f"Dismissed notification for execution {execution_arn} (owner: {owner_id})")
            
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Notification dismissed'})
            }
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                logger.info(f"Notification already dismissed or execution not found: {execution_arn}")
                return {
                    'statusCode': 200,
                    'body': json.dumps({'message': 'Notification dismissed or already removed'})
                }
            logger.error(f"Failed to dismiss notification: {e}")
            raise e

    except Exception as e:
        logger.exception("Unexpected error in dismiss_notification")
        return {'statusCode': 500, 'body': json.dumps({'error': 'Internal error'})}
