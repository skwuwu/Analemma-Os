
import json
import boto3
import logging
import os
from decimal import Decimal

# 공통 모듈에서 AWS 클라이언트 및 유틸리티 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource, get_stepfunctions_client
    from src.common.http_utils import get_cors_headers
    from src.common.json_utils import convert_decimals
    from src.common.constants import DynamoDBConfig
    dynamodb = get_dynamodb_resource()
    sfn = get_stepfunctions_client()
    _USE_COMMON_UTILS = True
except ImportError:
    dynamodb = boto3.resource('dynamodb')
    sfn = boto3.client('stepfunctions')
    _USE_COMMON_UTILS = False

# module logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 환경 변수에서 테이블명 읽기
TABLE_NAME = os.environ.get("TASK_TOKENS_TABLE_NAME", "TaskTokens")
table = dynamodb.Table(TABLE_NAME)

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

# Use common convert_decimals or define fallback
if not _USE_COMMON_UTILS:
    def _convert_decimals(obj):
        """Recursively convert Decimal objects returned by DynamoDB into native Python types

        - Integers (no fractional part) -> int
        - Otherwise -> float
        - Preserve dicts/lists recursively
        """
        if isinstance(obj, Decimal):
            if obj == obj.to_integral_value():
                return int(obj)
            return float(obj)
        if isinstance(obj, dict):
            return {k: _convert_decimals(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_convert_decimals(v) for v in obj]
        return obj
else:
    _convert_decimals = convert_decimals

def lambda_handler(event, context):
    # Handle OPTIONS request for CORS
    if event.get('httpMethod') == 'OPTIONS' or event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {"statusCode": 200, "headers": JSON_HEADERS, "body": ""}
    
    # API Gateway 프록시 통합을 위한 body 파싱
    body = event.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return {"statusCode": 400, "headers": JSON_HEADERS, "body": json.dumps({"message": "Invalid JSON in body"})}
    elif not body:
        # Step Functions 직접 테스트 등을 위한 event 파싱
        body = event

    # Accept either an execution-specific id (preferred) or the legacy conversation_id
    execution_id = body.get('execution_id') or body.get('executionId')
    conversation_id = body.get('conversation_id')
    user_response = body.get('response')

    # --- [보안 패치 시작] ---
    
    # 1. 오직 JWT 토큰(Cognito 'sub' 클레임)에서만 owner_id를 가져옵니다.
    try:
        owner_id = (event.get('requestContext', {})
                          .get('authorizer', {})
                          .get('jwt', {})
                          .get('claims', {})
                          .get('sub'))
    except Exception:
        owner_id = None

    # 2. body에서 온 ownerId는 명시적으로 무시합니다.
    if not owner_id:
        logger.error("Authentication failed: Could not extract ownerId (sub) from src.JWT claims.")
        return {
            'statusCode': 401, # 401 Unauthorized
            'headers': JSON_HEADERS,
            'body': json.dumps({'message': 'Unauthorized: Missing or invalid token'})
        }
    
    # 3. 토큰에서 가져온 owner_id로 필수 파라미터 검증
    # For security, if this request appears to originate from src.an external
    # frontend (presence of API Gateway requestContext or conversation/execution
    # identifiers), do NOT accept any uploaded state fields. Only accept a
    # natural-language response string. Strip/ignore `current_state`,
    # `state_s3_path`, `previous_final_state`, etc.
    def _is_frontend_event(ev):
        if not isinstance(ev, dict):
            return False
        if ev.get('requestContext') or ev.get('request_context'):
            return True
        if ev.get('conversation_id') or ev.get('execution_id'):
            return True
        return False

    # If frontend-origin, sanitize body to remove any state upload attempts
    if _is_frontend_event(event) or _is_frontend_event(body):
        # Remove potentially harmful fields if present
        for forbidden in ('current_state', 'state_s3_path', 'previous_final_state', 'previous_final_state_s3_path', 'final_state', 'final_state_s3_path'):
            if forbidden in body:
                logger.info("resume_handler: stripping forbidden field '%s' from src.frontend payload", forbidden)
                body.pop(forbidden, None)

        # Accept several common textual fields as the user's response
        if not isinstance(user_response, str):
            # try alternate keys
            alt = body.get('feedback') or body.get('user_response') or body.get('text') or body.get('response_text')
            if isinstance(alt, str):
                user_response = alt

    if not (execution_id or conversation_id) or user_response is None:
        logging.error("필수 정보 누락: (execution_id 또는 conversation_id) 및 response가 필요합니다")
        return {"statusCode": 400,  "body": json.dumps({"message": "Missing execution_id/conversation_id or response"})}
    
    # --- [보안 패치 종료] ---

    # DynamoDB에서 우선 execution_id로 조회하고, 없으면 legacy conversation_id로 폴백
    try:
        item = None
        # If execution_id is provided, query the ExecutionIdIndex only. Do NOT
        # fall back to a table scan — a query failure usually indicates a
        # configuration/permission issue that should surface as a 500 error.
        if execution_id:
            try:
                # Query against GSI 'ExecutionIdIndex' scoped to ownerId
                # Import dynamically to handle test environments
                import importlib
                conditions_module = importlib.import_module('boto3.dynamodb.conditions')
                Key = conditions_module.Key
                
                resp = table.query(
                    IndexName=DynamoDBConfig.EXECUTION_ID_INDEX,
                    KeyConditionExpression=Key('ownerId').eq(owner_id) & Key('execution_id').eq(execution_id)
                )
            except Exception as e:
                # Surface GSI/query failures instead of silently scanning the table.
                logger.error("ExecutionIdIndex query failed for owner=%s execution_id=%s: %s", owner_id, execution_id, e)
                return {"statusCode": 500,  "body": json.dumps({"message": "Failed to query ExecutionIdIndex"})}

            items = resp.get('Items', [])
            if items:
                item = items[0]
                logger.info("Found TaskToken via execution_id for owner=%s execution_id=%s", owner_id, execution_id)
        # Fallback to conversation_id lookup for backward compatibility (requires owner_id)
        if not item and conversation_id:
            try:
                resp = table.get_item(Key={'ownerId': owner_id, 'conversation_id': conversation_id})
                item = resp.get('Item')
            except Exception as e:
                logger.error("Failed to get item by conversation_id for owner=%s conversation_id=%s: %s", owner_id, conversation_id, e)
                return {"statusCode": 500, "headers": JSON_HEADERS, "body": json.dumps({"message": "Failed to query by conversation_id"})}

        if not item or 'taskToken' not in item:
            logging.error(f"TaskToken을 찾을 수 없음: execution_id={execution_id} conversation_id={conversation_id}")
            return {"statusCode": 404, "headers": JSON_HEADERS, "body": json.dumps({"message": "TaskToken not found or already used"})}
        task_token = item['taskToken']
        # Ensure conversation_id variable reflects the item value when available
        try:
            conversation_id = conversation_id or item.get('conversation_id')
        except Exception:
            pass
    except Exception as e:
        logging.error(f"DynamoDB 조회 실패: {str(e)}")
        return {"statusCode": 500, "headers": JSON_HEADERS, "body": json.dumps({"message": "Failed to query DynamoDB"})}

    try:
        # 사용자 응답을 포함한 완전한 상태 생성
        # Resume Handler는 WaitForCallback에서 온 상태를 업데이트해서 반환해야 함
        resume_output = {
            "userResponse": user_response,
            # 사용자 응답이 포함된 업데이트된 상태 반환 (JsonMerge에서 병합됨)
            "human_response": user_response,
            # canonical user callback field expected by merge_callback
            "user_callback_result": user_response,
            "resumed_at": context.aws_request_id if context else "unknown",
            # Step Functions state-bag 호환을 위한 필수 필드들
            # ProcessAsyncResult에서 States.JsonMerge로 current_state와 병합됨
        }
        
        # TaskToken 항목에서 추가 컨텍스트 정보 복구 (사용 가능한 경우)
        if item and isinstance(item, dict):
            # stored context may be under different keys depending on the writer
            stored_context = item.get('context') or item.get('state_data') or {}

            # If stored_context was serialized as a JSON string, try to parse it
            if isinstance(stored_context, str):
                try:
                    stored_context = json.loads(stored_context)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("resume_handler: stored_context is a string but failed to parse JSON; treating as empty")
                    stored_context = {}

            # If still not a dict, normalize to empty dict
            if not isinstance(stored_context, dict):
                logger.warning("resume_handler: stored_context is not a dict, found type=%s", type(stored_context))
                stored_context = {}

            # Restore commonly used context fields for logging and convenience
            if stored_context.get('workflowId'):
                resume_output['workflowId'] = stored_context['workflowId']
            if stored_context.get('segment_to_run') is not None:
                resume_output['current_segment'] = stored_context['segment_to_run']
            if stored_context.get('execution_name'):
                resume_output['execution_name'] = stored_context['execution_name']

            # Build canonical state_data for downstream merge
            state_data = {
                'workflow_config': stored_context.get('workflow_config') if isinstance(stored_context.get('workflow_config'), dict) else None,
                'partition_map': stored_context.get('partition_map') if isinstance(stored_context.get('partition_map'), dict) else None,
                'total_segments': stored_context.get('total_segments') or body.get('total_segments'),
                'ownerId': item.get('ownerId') or owner_id,
                'workflowId': stored_context.get('workflowId') or body.get('workflowId') or None,
                'segment_to_run': stored_context.get('segment_to_run') if stored_context.get('segment_to_run') is not None else body.get('segment_to_run'),
                'current_state': stored_context.get('current_state') if stored_context.get('current_state') is not None else None,
                'state_s3_path': stored_context.get('state_s3_path') if stored_context.get('state_s3_path') is not None else None,
                'idempotency_key': stored_context.get('idempotency_key') if stored_context.get('idempotency_key') is not None else None,
                'state_history': stored_context.get('state_history') or (body.get('state_history') or [])
            }

            # Hard validation: workflow_config is required for safe resumption
            if not state_data.get('workflow_config'):
                logger.error("CRITICAL: Missing workflow_config in stored TaskToken item for conversation_id=%s", conversation_id)
                logger.error("DynamoDB item keys: %s", list(item.keys()))
                return {
                    "statusCode": 500,
                    
                    "body": json.dumps({"message": "Internal Error: Stored workflow context is corrupted (missing workflow_config)."})
                }

            resume_output['state_data'] = state_data
            # also include ownerId at top-level for convenience
            resume_output['ownerId'] = state_data.get('ownerId')
        # Ensure conversation_id is present in the callback_result so Step Functions
        # states that read `$.callback_result.conversation_id` can find it.
        # Prefer the explicit conversation_id from src.the request, fall back to the
        # stored item value when available.
        try:
            if conversation_id:
                resume_output['conversation_id'] = conversation_id
            elif item and isinstance(item, dict) and item.get('conversation_id'):
                resume_output['conversation_id'] = item.get('conversation_id')
        except Exception:
            # best-effort — do not fail resume on this non-critical step
            pass
        
        # Step Functions 작업 재개
        # DynamoDB returns numbers as Decimal; ensure we convert them to native types
        output_for_sfn = _convert_decimals(resume_output)
        sfn.send_task_success(
            taskToken=task_token,
            output=json.dumps(output_for_sfn, ensure_ascii=False)
        )
        # 성공적으로 재개한 후 TaskToken 삭제 (재사용 방지)
        # Delete using the exact composite key (ownerId, conversation_id).
        delete_key = {'ownerId': item.get('ownerId') or owner_id, 'conversation_id': item.get('conversation_id')}
        try:
            # Only delete if the stored taskToken matches the one we used — avoids deleting a newly
            # stored token for a concurrent execution.
            table.delete_item(
                Key=delete_key,
                ConditionExpression='taskToken = :tt',
                ExpressionAttributeValues={':tt': task_token}
            )
        except Exception:
            # If conditional delete fails (another writer replaced the item), just log and continue.
            logging.warning(f"Conditional delete failed for key={delete_key}; it may have been rotated.")

        logging.info(f"conversation_id {conversation_id}의 응답으로 Step Functions 재개 완료")
        return {"statusCode": 200, "headers": JSON_HEADERS, "body": json.dumps({"message": "Step Functions resumed successfully"})}

    except sfn.exceptions.TaskTimedOut:
        logging.error(f"Task가 이미 타임아웃됨: {conversation_id}")
        return {"statusCode": 410, "headers": JSON_HEADERS, "body": json.dumps({"message": "Task timed out and can no longer be resumed"})}
    except sfn.exceptions.TaskDoesNotExist:
        logging.error(f"존재하지 않는 Task: {conversation_id}")
        return {"statusCode": 404, "headers": JSON_HEADERS, "body": json.dumps({"message": "Task does not exist. It may have been resumed or canceled already."})}
    except Exception as e:
        logging.error(f"Step Functions 재개 실패: {str(e)}")
        return {"statusCode": 500, "headers": JSON_HEADERS, "body": json.dumps({"message": "Failed to resume Step Functions execution"})}
