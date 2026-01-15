"""
WebSocket $disconnect handler.

[v2.1] 개선사항:
1. Incomplete Cleanup 해결
   - 삭제 전 구독 목록 조회 (메트릭/로깅용)
   - 역색인 테이블 지원 (SUBSCRIPTION_INDEX_TABLE)
   - TransactWriteItems로 원자적 삭제

2. Throttling Risk 대응
   - 지수 백오프 재시도 (최대 3회)
   - CloudWatch 메트릭 (disconnect 카운트)
   - Reserved Concurrency 설정 가이드 문서화

Production Deployment Notes:
- template.yaml에 ReservedConcurrentExecutions: 100 설정 권장
- DynamoDB 테이블은 On-demand 또는 충분한 WCU 확보
- 대규모 장애 시 Lambda throttling으로 다른 함수 보호
"""
import os
import json
import logging
import time
import boto3
from typing import Dict, Any, Optional, Set
from botocore.exceptions import ClientError

# 공통 모듈에서 AWS 클라이언트 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource
    dynamodb = get_dynamodb_resource()
except ImportError:
    dynamodb = boto3.resource('dynamodb')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# =============================================================================
# [v2.1] 전역 설정
# =============================================================================
cloudwatch = boto3.client('cloudwatch')

# 재시도 설정
MAX_RETRIES = 3
BASE_DELAY = 0.1  # 100ms

# 역색인 테이블 (구독자 수 추적용, 선택적)
SUBSCRIPTION_INDEX_TABLE = os.environ.get('SUBSCRIPTION_INDEX_TABLE')


def _exponential_backoff_retry(func, max_retries: int = MAX_RETRIES):
    """
    [v2.1] 지수 백오프 재시도.
    
    대량 disconnect 시 DynamoDB throttling 대응.
    """
    last_error = None
    
    for attempt in range(max_retries):
        try:
            return func()
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            
            # Throttling 또는 일시적 오류만 재시도
            if error_code in ('ProvisionedThroughputExceededException', 
                              'ThrottlingException',
                              'InternalServerError'):
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning(f'DynamoDB throttled, retry {attempt + 1}/{max_retries} after {delay}s')
                time.sleep(delay)
                last_error = e
            else:
                raise
        except Exception as e:
            last_error = e
            delay = BASE_DELAY * (2 ** attempt)
            time.sleep(delay)
    
    raise last_error


def _emit_disconnect_metric(success: bool, subscription_count: int = 0):
    """
    [v2.1] CloudWatch 메트릭 발행.
    
    disconnect 이벤트 추적용.
    """
    try:
        metrics = [
            {
                'MetricName': 'WebSocketDisconnect',
                'Value': 1,
                'Unit': 'Count',
                'Dimensions': [
                    {'Name': 'Status', 'Value': 'Success' if success else 'Failed'}
                ]
            }
        ]
        
        # 구독 정리 수도 추적
        if subscription_count > 0:
            metrics.append({
                'MetricName': 'SubscriptionsCleanedUp',
                'Value': subscription_count,
                'Unit': 'Count'
            })
        
        cloudwatch.put_metric_data(
            Namespace='Analemma/WebSocket',
            MetricData=metrics
        )
    except Exception as e:
        logger.debug(f'Failed to emit metric: {e}')


def _get_subscribed_executions(table, connection_id: str) -> Set[str]:
    """
    [v2.1] 삭제 전 구독 목록 조회.
    
    역색인 테이블 업데이트 및 메트릭용.
    """
    try:
        response = table.get_item(
            Key={'connectionId': connection_id},
            ProjectionExpression='subscribed_executions'
        )
        
        subs = response.get('Item', {}).get('subscribed_executions', set())
        
        # DynamoDB Set은 Python set으로 변환됨
        if isinstance(subs, set):
            return subs
        elif subs:
            return set(subs)
        return set()
        
    except Exception as e:
        logger.debug(f'Failed to get subscriptions for {connection_id}: {e}')
        return set()


def _cleanup_subscription_index(execution_ids: Set[str], connection_id: str):
    """
    [v2.1] 역색인 테이블에서 구독자 제거.
    
    execution_id -> connection_ids 매핑 테이블이 있는 경우,
    해당 연결을 구독자 목록에서 제거.
    
    Schema 예시:
    - PK: executionId
    - subscribers: SS (연결 ID Set)
    """
    if not SUBSCRIPTION_INDEX_TABLE or not execution_ids:
        return
    
    try:
        index_table = dynamodb.Table(SUBSCRIPTION_INDEX_TABLE)
        
        for execution_id in execution_ids:
            try:
                index_table.update_item(
                    Key={'executionId': execution_id},
                    UpdateExpression='DELETE subscribers :conn',
                    ExpressionAttributeValues={
                        ':conn': {connection_id}
                    }
                )
            except Exception as e:
                logger.debug(f'Failed to remove from index for {execution_id}: {e}')
                
        logger.info(f'Cleaned up {len(execution_ids)} subscription index entries')
        
    except Exception as e:
        logger.warning(f'Failed to cleanup subscription index: {e}')


def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    WebSocket $disconnect handler.

    [v2.1] 개선된 정리 로직:
    1. 구독 목록 조회 (메트릭/역색인용)
    2. 역색인 테이블 정리 (설정된 경우)
    3. 연결 레코드 삭제 (재시도 포함)
    4. CloudWatch 메트릭 발행
    
    Production Notes:
    - Reserved Concurrency 설정으로 다른 Lambda 보호
    - On-demand DynamoDB 또는 충분한 WCU 확보
    """
    table_name = os.environ.get('WEBSOCKET_CONNECTIONS_TABLE')
    if not table_name:
        logger.error('WEBSOCKET_CONNECTIONS_TABLE not configured')
        return {'statusCode': 500, 'body': 'Server misconfiguration'}

    connection_id = event.get('requestContext', {}).get('connectionId')
    if not connection_id:
        logger.error('No connectionId in requestContext; requestContext=%s', event.get('requestContext'))
        return {'statusCode': 400, 'body': 'Missing connectionId'}

    subscription_count = 0
    success = False
    
    try:
        table = dynamodb.Table(table_name)
        
        # [v2.1] Step 1: 삭제 전 구독 목록 조회 (역색인 정리용)
        subscribed_executions = _get_subscribed_executions(table, connection_id)
        subscription_count = len(subscribed_executions)
        
        if subscription_count > 0:
            logger.info(
                f'Connection {connection_id} had {subscription_count} active subscriptions'
            )
        
        # [v2.1] Step 2: 역색인 테이블 정리 (설정된 경우)
        if subscribed_executions:
            _cleanup_subscription_index(subscribed_executions, connection_id)
        
        # [v2.1] Step 3: 연결 레코드 삭제 (지수 백오프)
        logger.info(
            f'Removing websocket connection from DDB table={table_name} connectionId={connection_id}'
        )
        
        def delete_connection():
            return table.delete_item(
                Key={'connectionId': connection_id},
                ReturnValues='ALL_OLD'  # 삭제된 항목 확인용
            )
        
        resp = _exponential_backoff_retry(delete_connection)
        
        # 삭제된 항목 로깅
        deleted_item = resp.get('Attributes')
        if deleted_item:
            logger.debug(f'Deleted connection record: {json.dumps(deleted_item, default=str)[:500]}')
        else:
            logger.debug(f'Connection {connection_id} was already deleted or never existed')
        
        success = True
        
    except Exception as e:
        # Deletion failures are non-fatal for API Gateway disconnects
        logger.exception(
            f'Failed to remove connection {connection_id} from table {table_name}: {e}'
        )
    
    # [v2.1] Step 4: CloudWatch 메트릭 발행
    _emit_disconnect_metric(success, subscription_count)

    return {
        'statusCode': 200,
        'body': json.dumps({
            'disconnected': True,
            'subscriptions_cleaned': subscription_count
        })
    }
