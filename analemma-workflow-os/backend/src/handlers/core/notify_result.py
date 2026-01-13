import json
import os
import logging
import urllib.request
import urllib.error
import boto3
import copy

# 공통 모듈에서 AWS 클라이언트 및 유틸리티 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource, get_stepfunctions_client
    from src.common.http_utils import JSON_HEADERS
    from src.common.constants import DynamoDBConfig
    dynamodb = get_dynamodb_resource()
    stepfunctions = get_stepfunctions_client()
    _USE_COMMON_UTILS = True
except ImportError:
    dynamodb = boto3.resource('dynamodb')
    stepfunctions = boto3.client('stepfunctions')
    _USE_COMMON_UTILS = False

# get_connections_for_owner import (Lambda 환경에서는 상대 경로 import 불가)
try:
    from src.common.websocket_utils import get_connections_for_owner
except ImportError:
    # fallback for get_connections_for_owner if common module not available
    def get_connections_for_owner(owner_id):
        return []


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 환경 변수 로드
EXECUTIONS_TABLE = os.environ.get('EXECUTIONS_TABLE')
executions_table = None
if EXECUTIONS_TABLE:
    try:
        executions_table = dynamodb.Table(EXECUTIONS_TABLE)
    except Exception:
        logger.warning(f"Failed to load table resource: {EXECUTIONS_TABLE}")

# Optional: SES client for fallback email notifications
ses = boto3.client('ses') if os.environ.get('USE_SES', 'false').lower() in ('1','true','yes') else None

# Use common JSON_HEADERS or fallback
if not _USE_COMMON_UTILS:
    JSON_HEADERS = {'Content-Type': 'application/json'}


def _post_json(url, payload, timeout=30, headers=None):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return getattr(resp, 'status', None)


def _describe_execution(execution_arn):
    try:
        return stepfunctions.describe_execution(executionArn=execution_arn)
    except Exception:
        logger.exception('Failed to describe execution %s', execution_arn)
        return None


def _update_db_status(owner_id, execution_arn, status, error=None):
    """
    ExecutionsTable에 최종 상태를 업데이트합니다.
    """
    if not executions_table or not owner_id or not execution_arn: 
        return
    try:
        import time
        update_expr_parts = ["#st = :st", "#ua = :ua"]
        expr_names = {'#st': 'status', '#ua': 'updatedAt'}
        expr_vals = {':st': status, ':ua': int(time.time())}

        if error:
            update_expr_parts.append("#err = :err")
            expr_names['#err'] = 'error_info'
            expr_vals[':err'] = error
            
        update_expr = "SET " + ", ".join(update_expr_parts)
            
        executions_table.update_item(
            Key={'ownerId': owner_id, 'executionArn': execution_arn},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_vals
        )
        logger.info(f"Updated DB status for {execution_arn} to {status}")
    except Exception as e:
        logger.error(f"Failed to update execution status in DB: {e}")


def lambda_handler(event, context):
    """
    EventBridge -> Lambda handler for Step Functions execution state change events.

    Expected EventBridge event shape (example):
    {
      "version": "0",
      "id": "...",
      "source": "aws.states",
      "account": "123456789012",
      "time": "...",
      "region": "us-east-1",
      "resources": ["arn:aws:states:...:execution:..."],
      "detail": {
        "executionArn": "arn:aws:states:...",
        "stateMachineArn": "arn:aws:states:...",
        "name": "...",
        "status": "SUCCEEDED",
        "startDate": "...",
        "stopDate": "...",
        "input": "{...}",
        "output": "{...}"
      }
    }

    This function will try to locate a callback URL in the execution input or output
    and POST a JSON payload with executionArn, status, and parsed output.
    """
    logger.info('notify_result invoked with event keys: %s', list(event.keys()) if isinstance(event, dict) else 'not-dict')

    # Support both single EventBridge event and the usual wrapper
    records = []
    if isinstance(event, dict) and event.get('detail'):
        # single event
        records = [event]
    elif isinstance(event, dict) and event.get('Records'):
        records = event['Records']
    elif isinstance(event, list):
        records = event
    else:
        logger.warning('Unrecognized event shape')
        return {'statusCode': 400}

    results = []
    for rec in records:
        # Initialize result variables at the start of each loop iteration
        push_result = {'pushed_webhook': False}
        push_result_ws = {'pushed_websocket': False, 'connections': 0}
        
        detail = rec.get('detail') if isinstance(rec, dict) else None
        if not detail:
            logger.debug('Skipping record without detail')
            continue

        source = rec.get('source')
        status = detail.get('status')
        execution_arn = detail.get('executionArn')
        state_machine_arn = detail.get('stateMachineArn')

        # --- [보안 강화] 오직 우리 WorkflowOrchestrator 실행만 처리 ---
        allowed_state_machine = os.environ.get('WORKFLOW_ORCHESTRATOR_ARN', 'WorkflowOrchestrator')
        # execution_arn 형식: arn:aws:states:region:account:execution:StateMachineName:execution-id
        # state_machine_arn 형식: arn:aws:states:region:account:stateMachine:StateMachineName
        # allowed_state_machine이 전체 ARN인 경우와 이름만 있는 경우 모두 처리
        if state_machine_arn and allowed_state_machine:
            # allowed_state_machine이 전체 ARN이면 정확히 비교
            if allowed_state_machine.startswith('arn:'):
                if state_machine_arn != allowed_state_machine:
                    logger.warning('Ignoring execution from src.different state machine: %s (expected: %s)', state_machine_arn, allowed_state_machine)
                    continue
            # allowed_state_machine이 이름만 있으면 포함 여부로 확인
            else:
                if allowed_state_machine not in state_machine_arn:
                    logger.warning('Ignoring execution from src.different state machine: %s (expected name: %s)', state_machine_arn, allowed_state_machine)
                    continue

        # Only handle terminal states
        if status not in ('SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED'):
            logger.info('Skipping non-terminal status %s for %s', status, execution_arn)
            continue

        # Try to parse output if present
        output_raw = detail.get('output')
        output = None
        if output_raw:
            try:
                output = json.loads(output_raw)
            except (json.JSONDecodeError, ValueError):
                output = output_raw

        # Try to locate callback_url in the input or output. Prefer input.
        callback_url = None
        input_raw = detail.get('input')
        input_obj = None
        if input_raw:
            try:
                input_obj = json.loads(input_raw)
            except (json.JSONDecodeError, ValueError):
                input_obj = None

        # --- WebSocket 관련: ownerId 추출을 위해 input에서 ownerId 확인 ---
        owner_id = None
        if isinstance(input_obj, dict):
            owner_id = input_obj.get('ownerId')

        # Common places clients may include callback_url
        candidates = []
        if isinstance(input_obj, dict):
            candidates.append(input_obj.get('callback_url'))
            candidates.append(input_obj.get('input_data', {}).get('callback_url') if isinstance(input_obj.get('input_data'), dict) else None)
            candidates.append(input_obj.get('callback'))
        if isinstance(output, dict):
            candidates.append(output.get('callback_url'))
            candidates.append(output.get('callback'))

        for c in candidates:
            if c:
                callback_url = c
                break

        # If no callback_url, optionally fetch execution to inspect stored state
        if not callback_url and execution_arn:
            # Note: EventBridge event already contains input in detail.input, so no need to call DescribeExecution
            # which requires additional IAM permissions. Use input_raw directly.
            if input_raw:
                try:
                    inp_obj = json.loads(input_raw)
                    callback_url = inp_obj.get('callback_url') or inp_obj.get('input_data', {}).get('callback_url')
                except Exception:
                    pass

        # For webhook callbacks we keep executionArn in the outbound payload,
        # but for WebSocket notifications sent to frontend we avoid exposing
        # execution identifiers. Build both variants.
        webhook_payload = {
            'executionArn': execution_arn,
            'status': status,
            'output': output
        }
        # WebSocket payload wrapped in standard format for frontend compatibility
        # Frontend expects: { type: 'workflow_status', payload: { ... } }
        websocket_payload = {
            'type': 'workflow_status',
            'payload': {
                'action': 'workflow_completed',
                'status': status,
                'output_present': output is not None,
                'execution_id': execution_arn,
                'message': f'Workflow {status.lower()}',
                'timestamp': int(__import__('time').time() * 1000)
            }
        }

        # redact any internal `state_history` from src.payloads sent externally
        def _strip_state_history(obj):
            if not isinstance(obj, dict):
                return obj
            o = copy.deepcopy(obj)
            o.pop('state_history', None)
            # If output contains nested state_data, strip it too
            if 'output' in o and isinstance(o['output'], dict):
                sd = o['output'].get('state_data')
                if isinstance(sd, dict):
                    sd2 = sd.copy()
                    sd2.pop('state_history', None)
                    o['output']['state_data'] = sd2
            if 'state_data' in o and isinstance(o['state_data'], dict):
                sd = o['state_data'].copy()
                sd.pop('state_history', None)
                o['state_data'] = sd
            return o

        webhook_payload = _strip_state_history(webhook_payload)

        # idempotency finalization handled by separate Finalizer Lambda

        if callback_url:
            try:
                status_code = _post_json(callback_url, webhook_payload)
                push_result = {'pushed_webhook': True, 'status_code': status_code, 'callback_url': callback_url}
                logger.info('Posted execution result for %s to %s (status=%s)', execution_arn, callback_url, status_code)
            except Exception:
                logger.exception('Failed to POST execution result for %s to %s', execution_arn, callback_url)
        else:
            logger.info('No callback_url found for execution %s. Consider configuring EventBridge rule targets or adding callback info to workflow input.', execution_arn)

        # --- WebSocket Push: apigatewaymanagementapi를 사용해 연결별로 메시지 전송 ---
        # Use environment variable for WebSocket endpoint (keep existing runtime behavior)
        # Prefer the standardized `WEBSOCKET_ENDPOINT_URL` (https://... used by apigatewaymanagementapi)
        # For backward compatibility, fall back to `WEBSOCKET_API_ENDPOINT` (wss://... used by frontend)
        websocket_endpoint = os.environ.get('WEBSOCKET_ENDPOINT_URL') or os.environ.get('WEBSOCKET_API_ENDPOINT')
        connections_table_name = os.environ.get('WEBSOCKET_CONNECTIONS_TABLE')
        websocket_gsi = os.environ.get('WEBSOCKET_OWNER_ID_GSI')

        apigw_management = None
        connections_table = None
        if websocket_endpoint:
            try:
                apigw_management = boto3.client('apigatewaymanagementapi', endpoint_url=websocket_endpoint)
            except Exception:
                logger.exception('Failed to create apigatewaymanagementapi client for %s', websocket_endpoint)

        if connections_table_name:
            try:
                connections_table = dynamodb.Table(connections_table_name)
            except Exception:
                logger.exception('Failed to get DynamoDB table %s', connections_table_name)

        if apigw_management and connections_table and websocket_gsi and owner_id:
            connection_ids = get_connections_for_owner(owner_id)
            push_result_ws['connections'] = len(connection_ids)
            if connection_ids:
                payload_bytes = json.dumps(websocket_payload, ensure_ascii=False).encode('utf-8')
                for conn_id in connection_ids:
                    try:
                        apigw_management.post_to_connection(ConnectionId=conn_id, Data=payload_bytes)
                        logger.info("Pushed WebSocket notification to %s for owner %s", conn_id, owner_id)
                        push_result_ws['pushed_websocket'] = True
                    except apigw_management.exceptions.GoneException:
                        logger.info("Stale connection %s found, deleting", conn_id)
                        try:
                            connections_table.delete_item(Key={'connectionId': conn_id})
                        except Exception:
                            logger.exception("Failed to delete stale connection %s", conn_id)
                    except Exception:
                        logger.exception("Failed to push to WebSocket connection %s", conn_id)
        else:
            if not owner_id:
                logger.debug('No ownerId available in SFN input; skipping WebSocket push')
            elif not apigw_management or not connections_table or not websocket_gsi:
                logger.debug('WebSocket push disabled/config incomplete (endpoint/table/gsi missing)')

        # --- Pending Table 알림 정리: 완료된 워크플로우의 HITP 알림 자동 정리 ---
        # 완료된 실행의 execution_id 추출 (execution_arn에서 파싱)
        execution_id = None
        if execution_arn:
            # execution_arn 형식: arn:aws:states:region:account:execution:StateMachineName:execution-id
            try:
                arn_parts = execution_arn.split(':')
                if len(arn_parts) >= 7:
                    execution_id = arn_parts[7]  # execution-id 부분
            except Exception:
                logger.debug('Failed to parse execution_id from src.ARN: %s', execution_arn)

        # Pending table에서 해당 execution_id의 알림 조회 및 정리
        if execution_id and owner_id:
            try:
                pending_table_name = os.environ.get('PENDING_NOTIFICATIONS_TABLE')
                if pending_table_name:
                    pending_table = dynamodb.Table(pending_table_name)

                    # execution_id로 pending 알림 조회 (ExecutionIdIndex GSI 사용)
                    execution_id_index = DynamoDBConfig.EXECUTION_ID_INDEX
                    try:
                        from boto3.dynamodb.conditions import Key
                        response = pending_table.query(
                            IndexName=execution_id_index,
                            KeyConditionExpression=Key('execution_id').eq(execution_id)
                        )

                        # pending 상태의 알림들을 completed로 업데이트
                        cleanup_count = 0
                        for item in response.get('Items', []):
                            if item.get('status') == 'pending' and item.get('ownerId') == owner_id:
                                try:
                                    pending_table.update_item(
                                        Key={
                                            'ownerId': item['ownerId'],
                                            'notificationId': item['notificationId']
                                        },
                                        UpdateExpression='SET #status = :completed, completedAt = :completedAt',
                                        ExpressionAttributeNames={'#status': 'status'},
                                        ExpressionAttributeValues={
                                            ':completed': 'completed',
                                            ':completedAt': int(__import__('time').time())
                                        }
                                    )
                                    cleanup_count += 1
                                    logger.info('Cleaned up pending notification %s for completed execution %s',
                                              item['notificationId'], execution_id)
                                except Exception as e:
                                    logger.warning('Failed to update pending notification %s: %s',
                                                 item.get('notificationId'), e)

                        if cleanup_count > 0:
                            logger.info('Cleaned up %d pending notifications for completed execution %s',
                                      cleanup_count, execution_id)

                    except Exception as e:
                        logger.warning('Failed to query pending notifications for execution %s: %s', execution_id, e)

            except Exception as e:
                logger.warning('Failed to cleanup pending notifications for execution %s: %s', execution_id, e)

        # [ISSUE] SES 이메일 Fallback 미구현 - backend_issues.md 참조
        # SES 사용 시 verified identity와 IAM 권한 필요

        # [FIX] Update DynamoDB status to ensure persistence of terminal states
        if owner_id and execution_arn and status:
            _update_db_status(owner_id, execution_arn, status, error=detail.get('error') if detail else None)

        results.append({'executionArn': execution_arn, 'status': status, 'notify_webhook': push_result, 'notify_websocket': push_result_ws})

    return {'statusCode': 200, 'results': results}

