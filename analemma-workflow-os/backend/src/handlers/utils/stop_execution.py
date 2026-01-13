import json
import os
import time
import logging
import boto3
from urllib.parse import unquote  # [추가] URL 디코딩용
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from src.common.workflow_utils import get_owner_id, get_current_timestamp

# 공통 모듈에서 AWS 클라이언트 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource, get_stepfunctions_client
    dynamodb = get_dynamodb_resource()
    stepfunctions = get_stepfunctions_client()
except ImportError:
    dynamodb = boto3.resource('dynamodb')
    stepfunctions = boto3.client('stepfunctions')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

executions_table = dynamodb.Table(os.environ['EXECUTIONS_TABLE'])

def lambda_handler(event, context):
    try:
        # JWT 토큰에서 owner_id 추출
        owner_id = get_owner_id(event)
        if not owner_id:
            return {
                'statusCode': 401,
                'body': json.dumps({'error': 'Unauthorized'})
            }

        # 1. Try to get from src.body first (preferred)
        try:
            body = json.loads(event.get('body') or '{}')
            execution_arn = body.get('executionArn') or body.get('execution_id')
        except Exception:
            execution_arn = None

        # 2. Fallback to path (legacy)
        if not execution_arn:
            path_parameters = event.get('pathParameters', {})
            execution_id = path_parameters.get('id')
            if execution_id:
                execution_arn = unquote(execution_id)

        if not execution_arn:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing executionArn'})
            }

        # DynamoDB에서 실행 정보 조회 (PK: ownerId + executionArn)
        response = executions_table.get_item(Key={'ownerId': owner_id, 'executionArn': execution_arn})
        if 'Item' not in response:
            return {
                'statusCode': 404,
                'body': json.dumps({'error': 'Execution not found'})
            }

        execution = response['Item']

        # 소유자 확인은 PK에 의해 이미 보장됨 (필요 없음)

        # 이미 종료된 상태인지 확인 (Idempotency)
        current_status = execution.get('status')
        if current_status in ['SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED']:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': f'Execution is already finished with status: {current_status}'})
            }

        # Step Functions 실행 중지
        try:
            stepfunctions.stop_execution(
                executionArn=execution['executionArn'],  # [수정] 속성명 일치 (executionArn)
                error='UserAborted',      # 명시적 에러 코드
                cause='User initiated stop from src.UI' # 상세 사유
            )
        except stepfunctions.exceptions.ExecutionDoesNotExist:
            # SFN에는 없지만 DB에는 있는 경우 (드문 케이스), DB 업데이트만 진행
            print(f"Execution ARN not found in Step Functions: {execution['executionArn']}")
        except ClientError as e:
            print(f"Error stopping Step Functions: {e}")
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Failed to stop execution'})
            }

        # DynamoDB 업데이트 (상태 ABORTED, 표준 타임스탬프, 종료 시간 기록)
        # 프론트엔드가 초(sec) 단위를 사용하므로 int(time.time()) 사용
        now_timestamp = get_current_timestamp() 
        
        try:
            executions_table.update_item(
                Key={'ownerId': owner_id, 'executionArn': execution_arn},
                UpdateExpression='SET #status = :status, updated_at = :date, stopDate = :date',  # [수정] stopDate로 통일
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':status': 'ABORTED',       # 표준 상태값 사용
                    ':date': now_timestamp,     # 표준 Unix Timestamp 사용
                    ':running': 'RUNNING'       # Race condition 방지를 위한 condition
                },
                ConditionExpression='#status = :running'  # RUNNING 상태일 때만 업데이트
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                # 이미 RUNNING 상태가 아닌 경우 (Race condition 발생)
                return {
                    'statusCode': 409,  # Conflict
                    'body': json.dumps({
                        'error': 'Execution was not in RUNNING state',
                        'executionArn': execution_arn,
                        'current_status': current_status
                    })
                }
            else:
                # 다른 DynamoDB 에러
                print(f"Error updating DynamoDB: {e}")
                raise

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Execution stopped successfully',
                'executionArn': execution_arn,  # [수정] execution_id -> executionArn
                'status': 'ABORTED'
            })
        }

    except Exception as e:
        print(f"Error in stop_execution: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': 'Internal server error'})
        }
