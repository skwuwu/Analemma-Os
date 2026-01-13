import json
import os
import logging
from decimal import Decimal
import boto3
from botocore.exceptions import ClientError
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

# Avoid top-level dependency on boto3.dynamodb.conditions which may not be
# present in test shims; provide fallbacks if necessary.
try:
    from boto3.dynamodb.conditions import Key, Attr
except Exception:
    class _KeyCond:
        def __init__(self, data):
            self.data = data
        def __and__(self, other):
            return self.data

    class Key:
        def __init__(self, name):
            self.name = name
        def eq(self, v):
            return _KeyCond({'name': self.name, 'op': 'eq', 'value': v})

    class Attr:
        def __init__(self, name):
            self.name = name
        def eq(self, v):
            return {'name': self.name, 'op': 'eq', 'value': v}

WORKFLOWS_TABLE = os.environ.get('WORKFLOWS_TABLE', 'Workflows')

table = dynamodb.Table(WORKFLOWS_TABLE)

logger = logging.getLogger(__name__)


def _json_default(value):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def lambda_handler(event, context):
    # Normalize state-bag inputs if present
    event = statebag.normalize_event(event)

    # Handle OPTIONS request for CORS
    if event.get('httpMethod') == 'OPTIONS' or event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {"statusCode": 200, "body": ""}
    
    try:
        params = event.get('queryStringParameters') or {}
        
        # --- [보안 패치 시작] ---
        
        # 1. 오직 JWT 토큰(Cognito 'sub' 클레임)에서만 owner_id를 가져옵니다.
        # query parameter의 ownerId는 명시적으로 무시합니다.
        try:
            jwt_claims = (event.get('requestContext', {})
                         .get('authorizer', {})
                         .get('jwt', {})
                         .get('claims', {}))
            owner_id = jwt_claims.get('sub') if isinstance(jwt_claims, dict) else None
        except Exception:
            owner_id = None
        
        # 2. 인증 실패 시 401 반환
        if not owner_id:
            logger.error("Authentication failed: Could not extract ownerId (sub) from src.JWT claims.")
            return {
                'statusCode': 401,
                'body': json.dumps({'error': 'Unauthorized: Missing or invalid token'})
            }
        
        # --- [보안 패치 종료] ---

        # Pagination support via LastEvaluatedKey token
        try:
            limit = int(params.get('limit', 100))
        except (TypeError, ValueError):
            limit = 100

        exclusive_start = None
        raw_token = params.get('nextToken')
        if raw_token:
            try:
                exclusive_start = json.loads(raw_token)
            except json.JSONDecodeError:
                logger.warning(f"Ignoring invalid nextToken query value: {raw_token}")
                exclusive_start = None

        # Query by authenticated owner partition
        response_kwargs = {
            'KeyConditionExpression': Key('ownerId').eq(owner_id),
            'Limit': limit,
            'ProjectionExpression': '#nm, workflowId',  # Only fetch needed attributes
            'ExpressionAttributeNames': {'#nm': 'name'}
        }
        if exclusive_start:
            response_kwargs['ExclusiveStartKey'] = exclusive_start
        try:
            response = table.query(**response_kwargs)
        except ClientError as e:
            logger.exception('DynamoDB ClientError when querying workflows')
            raise
        except Exception:
            logger.exception('Unexpected error when querying workflows')
            raise

        items = response.get('Items', [])

        # Return both name and workflowId for frontend operations
        workflows = []
        for item in items:
            try:
                name_val = item.get('name')
                workflow_id = item.get('workflowId')
                if name_val and workflow_id:
                    workflows.append({
                        'name': name_val,
                        'workflowId': workflow_id
                    })
            except Exception:
                # non-fatal: skip malformed items
                continue

        next_token = response.get('LastEvaluatedKey')
        if next_token:
            next_token = json.dumps(next_token)
        return build_response(200, {'workflows': workflows, 'nextToken': next_token})
    except ClientError as e:
        logger.exception('DynamoDB ClientError in get_workflow')
        return build_response(500, {'error': 'Database error occurred'})
    except Exception as e:
        logger.exception('Unexpected error in get_workflow')
        return build_response(500, {'error': 'Internal server error'})
