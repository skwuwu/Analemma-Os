import os
import boto3
import logging
from botocore.exceptions import ClientError

# 공통 모듈에서 AWS 클라이언트 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource
    dynamodb = get_dynamodb_resource()
except ImportError:
    dynamodb = boto3.resource('dynamodb')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

IDEMPOTENCY_TABLE = os.environ.get('IDEMPOTENCY_TABLE')

def lambda_handler(event, context):
    """
    Checks for existing execution to prevent duplicates.
    """
    idempotency_key = event.get('idempotency_key')
    
    if not idempotency_key:
        return {
            "existing_execution_arn": None,
            "existing_execution_status": None
        }

    if not IDEMPOTENCY_TABLE:
        logger.error("IDEMPOTENCY_TABLE not configured")
        raise RuntimeError("Server configuration error: IDEMPOTENCY_TABLE missing")

    table = dynamodb.Table(IDEMPOTENCY_TABLE)

    try:
        # 멱등성 키로 조회 (파티션 키 기준)
        # 만약 정렬키(segment)를 쓴다면, '워크플로우 전체'를 대표하는 키(예: segment_to_run=-1 또는 meta)가 필요함
        # 여기서는 idempotency_key가 PK라고 가정합니다.
        response = table.get_item(Key={'idempotency_key': idempotency_key})
        item = response.get('Item')

        if item:
            logger.info(f"Duplicate execution found: {idempotency_key}")
            return {
                "existing_execution_arn": item.get('executionArn'),
                "existing_execution_status": item.get('status')
            }
        
        return {
            "existing_execution_arn": None,
            "existing_execution_status": None
        }

    except Exception as e:
        logger.error(f"Idempotency check failed: {e}")
        # DB 에러가 났다고 해서 워크플로우를 죽이지 않고, 중복 체크를 스킵하도록(False) 처리할 수도 있음
        raise e
