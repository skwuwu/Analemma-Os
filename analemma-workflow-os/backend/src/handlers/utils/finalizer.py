import json
import os
import logging
import boto3
import time
from botocore.exceptions import ClientError
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb')
sfn_client = boto3.client('stepfunctions')


def _get_payload(event):
    # normalize EventBridge or Lambda-invoked shapes
    if isinstance(event, dict) and event.get('detail'):
        return event['detail']
    if isinstance(event, list) and len(event) > 0 and isinstance(event[0], dict) and event[0].get('detail'):
        return event[0]['detail']
    return event


from decimal import Decimal

def _convert_floats_to_decimals(obj):
    """Recursively convert float values to Decimal for DynamoDB."""
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: _convert_floats_to_decimals(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_floats_to_decimals(v) for v in obj]
    return obj

def lambda_handler(event, context):
    """Finalizer lambda: update idempotency table when Step Functions execution reaches terminal state.

    Expects Step Functions "Execution Status Change" EventBridge events.
    Looks for `idempotency_key` inside the execution input JSON and updates the
    DynamoDB table specified by environment variable `IDEMPOTENCY_TABLE`.

    Enhanced with:
    - Fallback to describe_execution when EventBridge truncates input/output
    - Stores output for complete idempotency response
    - TTL for automatic cleanup
    """
    try:
        detail = _get_payload(event)
        if not isinstance(detail, dict):
            logger.warning('Finalizer: unexpected event shape')
            return {'statusCode': 400}

        execution_arn = detail.get('executionArn')
        status = detail.get('status')

        # Only terminal states
        if status not in ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED'):
            logger.info('Finalizer: skipping non-terminal status %s for %s', status, execution_arn)
            return {'statusCode': 200}

        # 2. Input 및 Output 확보 (EventBridge Truncation 방어 로직)
        input_raw = detail.get('input')
        output_raw = detail.get('output')

        # input이나 output이 이벤트에 없으면 직접 조회 (DescribeExecution)
        if not input_raw or (status == 'SUCCEEDED' and not output_raw):
            logger.info('Finalizer: input/output missing in event, fetching from src.Step Functions API')
            try:
                desc = sfn_client.describe_execution(executionArn=execution_arn)
                input_raw = desc.get('input')
                output_raw = desc.get('output')
            except ClientError as e:
                logger.error(f"Finalizer: Failed to describe execution {execution_arn}: {e}")
                # 조회가 불가능하면 진행 불가
                return {'statusCode': 500}

        # 3. Input 파싱 및 Idempotency Key 추출
        input_obj = None
        if input_raw:
            try:
                input_obj = json.loads(input_raw)
            except (json.JSONDecodeError, ValueError):
                logger.warning('Finalizer: failed to parse input JSON')

        idemp_table_name = os.environ.get('IDEMPOTENCY_TABLE')
        if not idemp_table_name:
            logger.info('Finalizer: no IDEMPOTENCY_TABLE configured; nothing to do')
            return {'statusCode': 200}

        idemp_key = None
        if isinstance(input_obj, dict):
            # Support both legacy top-level idempotency_key and new state-bag location
            idemp_key = input_obj.get('idempotency_key') or (input_obj.get('state_data') or {}).get('idempotency_key')

        if not idemp_key:
            logger.info('Finalizer: no idempotency_key found in execution input for %s', execution_arn)
            return {'statusCode': 200}

        # 4. DynamoDB 업데이트 준비
        idemp_table = dynamodb.Table(idemp_table_name)
        new_status = 'COMPLETED' if status == 'SUCCEEDED' else 'FAILED'
        stop_date = detail.get('stopDate') or datetime.now(timezone.utc).isoformat()

        # TTL 설정 (환경변수로 설정 가능, 기본값: 24시간)
        ttl_hours = int(os.environ.get('EXECUTION_TTL_HOURS', '24'))
        ttl_seconds = int(time.time()) + (ttl_hours * 60 * 60)

        update_expression = 'SET #status = :s, executionArn = :e, stopDate = :d, #ttl = :t'
        expression_attr_names = {
            '#status': 'status',
            '#ttl': 'ttl'
        }
        expression_attr_values = {
            ':s': new_status,
            ':e': execution_arn,
            ':d': stop_date,
            ':t': ttl_seconds
        }

        # 성공 시 Output도 함께 저장 (재시도 요청에 응답하기 위해)
        if status == 'SUCCEEDED' and output_raw:
            update_expression += ', #output = :o'
            expression_attr_names['#output'] = 'output'
            # output_raw는 JSON 문자열이므로, 그대로 저장하거나 객체로 변환해 저장
            # 여기서는 JSON 문자열 그대로 저장하거나, 필요시 json.loads(output_raw) 사용
            try:
                # [Fix] Convert floats to Decimals for DynamoDB
                parsed_output = json.loads(output_raw)
                expression_attr_values[':o'] = _convert_floats_to_decimals(parsed_output)
            except:
                expression_attr_values[':o'] = output_raw

        # 에러 시 Error/Cause 저장 (선택)
        if status != 'SUCCEEDED':
            if detail.get('error'):
                update_expression += ', #error = :err'
                expression_attr_names['#error'] = 'error'
                expression_attr_values[':err'] = detail.get('error')

        # 5. DB Update
        try:
            idemp_table.update_item(
                Key={'idempotency_key': idemp_key},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attr_names,
                ExpressionAttributeValues=expression_attr_values
            )
            logger.info('Finalizer: updated idempotency key %s -> %s (execution=%s)', idemp_key, new_status, execution_arn)
        except ClientError:
            logger.exception('Finalizer: failed to update idempotency table for key %s', idemp_key)
            return {'statusCode': 500}

        return {'statusCode': 200}

    except Exception:
        logger.exception('Finalizer: unexpected error')
        return {'statusCode': 500}
