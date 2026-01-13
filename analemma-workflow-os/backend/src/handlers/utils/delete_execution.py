import json
import os
import logging
import boto3
from botocore.exceptions import ClientError

# 공통 모듈에서 AWS 클라이언트 및 유틸리티 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource
    from src.common.http_utils import get_cors_headers, build_response
    dynamodb = get_dynamodb_resource()
    _USE_COMMON_UTILS = True
except ImportError:
    dynamodb = boto3.resource('dynamodb')
    _USE_COMMON_UTILS = False

# statebag may be provided in the runtime; if not available, set to None and
# handlers should tolerate a missing statebag (existing code often calls
# statebag.normalize_event if present).
try:
    from src.common import statebag
except ImportError:
    statebag = None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

EXEC_TABLE = os.environ.get('EXECUTIONS_TABLE')

# Fail-fast warning at cold-start if misconfigured
if not EXEC_TABLE:
    logging.getLogger(__name__).warning('EXECUTIONS_TABLE environment variable is not set')

# Create table resource once (cold-start) if configured
exec_table = dynamodb.Table(EXEC_TABLE) if EXEC_TABLE else None

# Use common CORS headers or fallback
if _USE_COMMON_UTILS:
    JSON_HEADERS = get_cors_headers()
else:
    JSON_HEADERS = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": os.environ.get("CLOUDFRONT_DOMAIN", "*"),
        "Access-Control-Allow-Headers": "Authorization, Content-Type",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Credentials": "true"
    }


def lambda_handler(event, context):
    """DELETE /executions/{id}

    Deletes an execution record from src.the executions table for the authenticated owner.
    """
    # Normalize event if statebag provided
    if statebag and hasattr(statebag, 'normalize_event'):
        try:
            event = statebag.normalize_event(event)
        except Exception:
            # non-fatal: continue with raw event
            pass

    # Handle CORS preflight
    if event.get('httpMethod') == 'OPTIONS' or event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {"statusCode": 200, "body": ""}

    try:
        owner_id = (event.get('requestContext', {})
                    .get('authorizer', {})
                    .get('jwt', {})
                    .get('claims', {})
                    .get('sub'))
    except Exception:
        owner_id = None

    if not owner_id:
        return {'statusCode': 401, 'headers': JSON_HEADERS, 'body': json.dumps({'error': 'Unauthorized'})}

    # 1. Try to get from src.body first (preferred for DELETE with ARN)
    try:
        body = json.loads(event.get('body') or '{}')
        execution_arn = body.get('executionArn') or body.get('execution_arn')
    except Exception:
        execution_arn = None

    # 2. Fallback to path/query
    if not execution_arn:
        execution_arn = (event.get('pathParameters') or {}).get('id')
    if not execution_arn:
        qs = event.get('queryStringParameters') or {}
        execution_arn = qs.get('executionArn') or qs.get('execution_arn')

    if not execution_arn:
        return {'statusCode': 400, 'headers': JSON_HEADERS, 'body': json.dumps({'error': 'Missing executionArn'})}

    # Server configuration check
    if not exec_table:
        logger.error('No EXECUTIONS_TABLE configured')
        return {'statusCode': 500, 'headers': JSON_HEADERS, 'body': json.dumps({'error': 'Server misconfigured'})}

    try:
        # [수정됨] 변경된 테이블 스키마(PK+SK)에 맞춰 삭제
        # 조건부 삭제를 사용하여 없는 데이터 삭제 시 에러 유도
        exec_table.delete_item(
            Key={
                'ownerId': owner_id,        # Partition Key
                'executionArn': execution_arn # Sort Key
            },
            ConditionExpression="attribute_exists(executionArn)"
        )

        # 예외가 발생하지 않았다면 정상 삭제된 것
        return build_response(200, {'message': 'Deleted'}, JSON_HEADERS)

    except ClientError as e:
        code = e.response.get('Error', {}).get('Code')
        
        # 아이템이 없음 (조건 불만족)
        if code == 'ConditionalCheckFailedException':
            # 보안상 "없음(404)"으로 통일하여 정보 노출 방지
            return build_response(404, {'error': 'Execution not found or access denied'}, JSON_HEADERS)
            
        logger.exception(f'DynamoDB ClientError during delete for ARN: {execution_arn}')
        return build_response(500, {'error': 'Internal error'}, JSON_HEADERS)
    except Exception:
        logger.exception(f'Unhandled error during delete for ARN: {execution_arn}')
        return build_response(500, {'error': 'Internal error'}, JSON_HEADERS)

