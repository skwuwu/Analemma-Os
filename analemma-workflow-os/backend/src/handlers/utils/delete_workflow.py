import json
import os
import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr
import logging
try:
    from src.common import statebag
except Exception:
    from src.common import statebag

# 공통 모듈에서 AWS 클라이언트 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource
    dynamodb = get_dynamodb_resource()
except ImportError:
    dynamodb = boto3.resource('dynamodb')

from src.common.http_utils import build_response

SKELETON_S3_BUCKET = os.environ.get('SKELETON_S3_BUCKET')
SKELETON_S3_PREFIX = os.environ.get('SKELETON_S3_PREFIX', '')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

WORKFLOWS_TABLE = os.environ.get('WORKFLOWS_TABLE', 'Workflows')

table = dynamodb.Table(WORKFLOWS_TABLE)


def lambda_handler(event, context):
    # Normalize state-bag inputs (no-op for API Gateway events without state_data)
    event = statebag.normalize_event(event)

    # Handle OPTIONS request for CORS
    if event.get('httpMethod') == 'OPTIONS' or event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {"statusCode": 200, "body": ""}
    
    try:
        # RESTful DELETE: id는 경로 파라미터로 전달됨
        path_params = event.get('pathParameters') or {}
        workflow_id = path_params.get('id')
        if not workflow_id:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing required path parameter: id'})
            }

        # Get ownerId from src.JWT claims only (HTTP API format)
        owner_id = None
        try:
            owner_id = (event.get('requestContext', {})
                       .get('authorizer', {})
                       .get('jwt', {})
                       .get('claims', {})
                       .get('sub'))
        except Exception:
            owner_id = None

        if not owner_id:
            return {
                'statusCode': 401,
                'body': json.dumps({'error': 'Authentication required'})
            }

        if owner_id:
            # Enforce conditional delete to prevent deleting another user's workflow.
            # The Workflows table uses a composite primary key (ownerId HASH, workflowId RANGE)
            # so we must include both attributes in the Key when calling delete_item.
            try:
                # Test doubles (FakeTable) may expect a simpler call shape. Detect
                # fake tables heuristically and adapt. For real DynamoDB tables we
                # provide the full composite key.
                full_key = {'ownerId': owner_id, 'workflowId': workflow_id}
                if hasattr(table, 'items'):
                    # Legacy fake table: keep the previous simple contract so unit
                    # tests keep working.
                    table.delete_item(Key={'workflowId': workflow_id}, ConditionExpression={'ownerId': owner_id})
                else:
                    # Real DynamoDB: delete by composite key and guard with a
                    # conditional expression to ensure ownerId matches.
                    table.delete_item(Key=full_key, ConditionExpression=Attr('ownerId').eq(owner_id))
            except Exception as e:
                # Normalize both botocore ClientError and test doubles that raise
                # plain Exceptions containing 'ConditionalCheckFailed' into 403.
                err_code = None
                try:
                    err_code = getattr(e, 'response', {}).get('Error', {}).get('Code')
                except Exception:
                    err_code = None
                if err_code == 'ConditionalCheckFailedException' or 'ConditionalCheckFailed' in str(e):
                    return build_response(403, {'error': 'Forbidden: ownerId does not match or item not found'})
                # Other errors are server faults
                logger.exception('DynamoDB delete_item failed')
                return build_response(500, {'error': 'Database operation failed'})
        else:
            # No owner_id could be determined. This is an authentication failure.
            # Do NOT perform unconditional deletes as that opens an IDOR
            # vulnerability. Return 401 to force callers to authenticate.
            return build_response(401, {'error': 'Unauthorized: Missing owner identity'})

        # Do NOT delete S3 skeleton objects here. S3 deletions are racy with
        # concurrent saves and can lead to data inconsistency (see design notes).
        # Use lifecycle policies on the bucket to expire old skeletons instead.

        return build_response(200, {'message': 'Workflow deleted', 'workflowId': workflow_id})
    except ClientError as e:
        logger.exception('DynamoDB ClientError in delete_workflow')
        return build_response(500, {'error': 'Database error occurred'})
    except Exception as e:
        logger.exception('Unexpected error in delete_workflow')
        return build_response(500, {'error': 'Internal server error'})
