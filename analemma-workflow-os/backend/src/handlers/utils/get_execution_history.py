import json
import os
import logging
import boto3
from decimal import Decimal
from botocore.exceptions import ClientError
try:
    from src.common import statebag
except ImportError:
    statebag = None

# 공통 모듈에서 AWS 클라이언트 및 유틸리티 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource, get_s3_client
    from src.common.json_utils import DecimalEncoder
    from src.common.http_utils import get_cors_headers, build_response
    dynamodb = get_dynamodb_resource()
    s3_client = get_s3_client()
    _USE_COMMON_UTILS = True
except ImportError:
    dynamodb = boto3.resource('dynamodb')
    s3_client = boto3.client('s3')
    _USE_COMMON_UTILS = False

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

EXEC_TABLE = os.environ.get('EXECUTIONS_TABLE')
SKELETON_S3_BUCKET = os.environ.get('SKELETON_S3_BUCKET')

# Initialize table resource at cold-start for performance (reuse on warm starts)
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

# Fallback DecimalEncoder if common module not available
if not _USE_COMMON_UTILS:
    class DecimalEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return float(obj) if obj % 1 else int(obj)
            return super(DecimalEncoder, self).default(obj)


def lambda_handler(event, context):
    """GET /executions/{id}/history

    Returns the stored `step_function_state` (including `state_history`) for
    the specified executionArn. Requires JWT authorizer; only the owner (JWT.sub)
    may access their executions.
    """
    # Normalize event if statebag is available (defensive to avoid runtime errors)
    if statebag and hasattr(statebag, 'normalize_event'):
        try:
            event = statebag.normalize_event(event)
        except Exception:
            # Non-fatal: fall back to raw event
            logger.debug('statebag.normalize_event failed; proceeding with raw event')

    # Handle CORS preflight
    if event.get('httpMethod') == 'OPTIONS' or event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {"statusCode": 200, "body": ""}

    # Auth
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

    # Execution identifier
    execution_arn = None
    execution_arn = (event.get('pathParameters') or {}).get('id')
    if not execution_arn:
        qs = event.get('queryStringParameters') or {}
        execution_arn = qs.get('executionArn') or qs.get('execution_arn')

    if not execution_arn:
        return {'statusCode': 400, 'headers': JSON_HEADERS, 'body': json.dumps({'error': 'Missing executionArn'})}

    if not exec_table:
        logger.error('No EXECUTIONS_TABLE configured')
        return {'statusCode': 500, 'headers': JSON_HEADERS, 'body': json.dumps({'error': 'Server misconfigured'})}

    try:
        # [수정됨] 변경된 테이블 스키마(PK+SK)에 맞춰 조회
        # 이제 ownerId가 키에 포함되므로, 별도의 소유권 검증 로직이 필요 없어짐 (DB가 알아서 거름)
        resp = exec_table.get_item(
            Key={
                'ownerId': owner_id,        # Partition Key
                'executionArn': execution_arn # Sort Key
            },
            ProjectionExpression='step_function_state, history_s3_key, #st, startDate',
            ExpressionAttributeNames={'#st': 'status'}
        )
        item = resp.get('Item')

        if not item:
            # 내 ownerId와 일치하는 데이터가 없으면 404
            return {'statusCode': 404, 'headers': JSON_HEADERS, 'body': json.dumps({'error': 'Not found'})}

        sfs = item.get('step_function_state')
        history_s3_key = item.get('history_s3_key')
        
        # [DEBUG] Log what we have from src.DB
        logger.info(f"[DEBUG] history_s3_key: {history_s3_key}, SKELETON_S3_BUCKET: {SKELETON_S3_BUCKET}, sfs type: {type(sfs)}")
        if sfs and isinstance(sfs, dict):
            logger.info(f"[DEBUG] sfs keys: {list(sfs.keys())}, state_history length: {len(sfs.get('state_history', []))}")

        # Check for S3 offloading (Claim Check Pattern)
        if history_s3_key and SKELETON_S3_BUCKET:
            try:
                s3_response = s3_client.get_object(Bucket=SKELETON_S3_BUCKET, Key=history_s3_key)
                s3_content = s3_response['Body'].read().decode('utf-8')
                full_state = json.loads(s3_content)
                
                # [DEBUG] Log S3 data
                logger.info(f"[DEBUG] S3 fetch successful, keys: {list(full_state.keys()) if isinstance(full_state, dict) else 'not dict'}")
                if isinstance(full_state, dict):
                    logger.info(f"[DEBUG] S3 state_history length: {len(full_state.get('state_history', []))}")
                
                # Merge full state into response, prioritizing S3 data
                # If step_function_state exists in S3 data, use it.
                sfs = full_state
            except Exception as e:
                logger.error(f"Failed to fetch history from src.S3: {e}")
                # Fallback to DynamoDB data if S3 fetch fails, but warn
                if not sfs: sfs = {}
                sfs['error'] = 'Failed to load full history from src.storage'

        # For history endpoint, return step_function_state (including state_history).
        # Ensure input is present by falling back to initial_input if available
        if not sfs.get('input') and item.get('initial_input'):
            sfs['input'] = item.get('initial_input')

        body = {
            'execution_id': execution_arn,
            'status': item.get('status'),
            'start_date': str(item.get('startDate')),
            'step_function_state': sfs
        }

        return build_response(200, body, JSON_HEADERS)

    except ClientError:
        logger.exception('DynamoDB ClientError')
        return build_response(500, {'error': 'Internal error'}, JSON_HEADERS)
    except Exception:
        # Log details but do not return internal error text to client (avoid information leakage)
        logger.exception('Unhandled error in get_execution_history')
        return build_response(500, {'error': 'Internal error'}, JSON_HEADERS)
