import json
import logging
import os
import boto3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from decimal import Decimal

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
EXECUTIONS_TABLE = os.environ.get('EXECUTIONS_TABLE')
RETENTION_DAYS = int(os.environ.get('RETENTION_DAYS', '90'))

executions_table = dynamodb.Table(EXECUTIONS_TABLE) if EXECUTIONS_TABLE else None


def lambda_handler(event, context):
    """
    기존 ExecutionsTable 데이터에 expiration_timestamp 필드 추가
    TTL 마이그레이션용 일회성 스크립트
    """
    try:
        logger.info("TTL migration started")

        # 모든 기존 레코드에 expiration_timestamp 추가
        migrated_count = migrate_existing_records()

        logger.info(f"TTL migration completed: {migrated_count} records migrated")

        return {
            "status": "success",
            "migrated_count": migrated_count
        }

    except Exception as e:
        logger.exception(f"Error in TTL migration: {e}")
        return {"status": "error", "reason": str(e)}


def migrate_existing_records() -> int:
    """기존 레코드에 expiration_timestamp 필드 추가"""
    if not executions_table:
        logger.error("Executions table not configured")
        return 0

    try:
        migrated_count = 0

        # Scan으로 모든 레코드 조회
        response = executions_table.scan()
        items = response.get('Items', [])

        # 페이징 처리
        while 'LastEvaluatedKey' in response:
            response = executions_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))

        logger.info(f"Found {len(items)} total records to migrate")

        # 배치 업데이트를 위해 25개씩 그룹화
        batch_size = 25

        for i in range(0, len(items), batch_size):
            batch = items[i:i + batch_size]

            # 배치 업데이트 요청 생성
            update_requests = []
            for item in batch:
                # 이미 expiration_timestamp가 있는 경우 스킵
                if 'expiration_timestamp' in item:
                    continue

                # createdAt 또는 startDate를 기반으로 expiration_timestamp 계산
                expiration_ts = calculate_expiration_timestamp(item)

                if expiration_ts:
                    update_requests.append({
                        'PutRequest': {
                            'Item': {
                                'ownerId': item['ownerId'],
                                'executionArn': item['executionArn'],
                                'expiration_timestamp': expiration_ts,
                                # 기존 필드들 유지 (업데이트가 아닌 추가이므로)
                                **{k: v for k, v in item.items() if k not in ['ownerId', 'executionArn']}
                            }
                        }
                    })

            # 배치 쓰기 실행
            if update_requests:
                try:
                    response = dynamodb.batch_write_item(
                        RequestItems={
                            executions_table.table_name: update_requests
                        }
                    )

                    # 처리되지 않은 항목 재처리
                    unprocessed_items = response.get('UnprocessedItems', {}).get(executions_table.table_name, [])
                    retry_count = 0
                    while unprocessed_items and retry_count < 3:
                        logger.warning(f"Retrying {len(unprocessed_items)} unprocessed migration requests")
                        response = dynamodb.batch_write_item(
                            RequestItems={
                                executions_table.table_name: unprocessed_items
                            }
                        )
                        unprocessed_items = response.get('UnprocessedItems', {}).get(executions_table.table_name, [])
                        retry_count += 1

                    successful_updates = len(update_requests) - len(unprocessed_items)
                    migrated_count += successful_updates

                    logger.info(f"Batch migration: {successful_updates}/{len(update_requests)} records updated")

                    if unprocessed_items:
                        logger.error(f"Failed to migrate {len(unprocessed_items)} items after retries")

                except Exception as e:
                    logger.exception(f"Failed to execute batch migration: {e}")

        return migrated_count

    except Exception as e:
        logger.exception(f"Failed to migrate records: {e}")
        return 0


def calculate_expiration_timestamp(item: Dict[str, Any]) -> Optional[int]:
    """레코드의 생성 시간을 기반으로 expiration_timestamp 계산"""
    try:
        # createdAt 필드 우선 사용
        if 'createdAt' in item:
            created_at = item['createdAt']
            if isinstance(created_at, (int, float)):
                # Unix timestamp인 경우
                created_dt = datetime.fromtimestamp(created_at)
            elif isinstance(created_at, str):
                # ISO string인 경우
                created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            else:
                # datetime 객체인 경우
                created_dt = created_at

        # createdAt이 없으면 startDate 사용
        elif 'startDate' in item:
            start_date = item['startDate']
            if isinstance(start_date, str):
                created_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            else:
                created_dt = start_date
        else:
            logger.warning(f"No timestamp found in item: {item.get('executionArn', 'unknown')}")
            return None

        # 90일 후의 Unix timestamp 계산
        expiration_dt = created_dt + timedelta(days=RETENTION_DAYS)
        return int(expiration_dt.timestamp())

    except Exception as e:
        logger.exception(f"Failed to calculate expiration timestamp for item: {item.get('executionArn', 'unknown')}")
        return None


# 로컬 테스트용
if __name__ == "__main__":
    # 환경 변수 설정 (실제 환경에서는 Lambda 환경변수로 설정)
    os.environ['EXECUTIONS_TABLE'] = 'test-executions-table'

    # 테스트 실행
    result = lambda_handler({}, None)
    print(f"Migration result: {result}")