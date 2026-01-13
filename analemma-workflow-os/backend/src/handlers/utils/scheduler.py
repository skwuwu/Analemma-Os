import os
import json
import boto3
import logging
import time
from datetime import datetime
from src.croniter import croniter
from src.common.constants import DynamoDBConfig
try:
    # Normal import when boto3 is fully available
    from boto3.dynamodb.conditions import Key
except Exception:
    # Lightweight fallback used during tests or trimmed boto3 installs.
    # The scheduler only needs Key objects to build a KeyConditionExpression
    # which in tests is ignored by the dummy table. Provide minimal methods
    # used by the code (eq, lte) and return Condition objects that support
    # chaining with &. The returned objects are only used as opaque values
    # passed to test dummies and do not implement full boto3 behavior.
    class Key:
        def __init__(self, name):
            self.name = name

        class Condition:
            def __init__(self, op, name, val):
                self.op = op
                self.name = name
                self.val = val

            def __and__(self, other):
                return ("and", self, other)

            def __repr__(self):
                return f"Condition({self.op},{self.name},{self.val})"

        def eq(self, value):
            return Key.Condition("eq", self.name, value)

        def lte(self, value):
            return Key.Condition("lte", self.name, value)

# 공통 모듈에서 AWS 클라이언트 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource, get_stepfunctions_client
    dynamodb = get_dynamodb_resource()
except ImportError:
    dynamodb = boto3.resource('dynamodb')

# --- 로깅 설정 ---
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# --- 환경 변수 (Lambda 함수 설정에서 주입) ---
# 워크플로우 설계가 저장된 DynamoDB 테이블 정보
# 기존 핸들러들과 일관된 환경변수 이름 사용
# WORKFLOWS_TABLE: DynamoDB 테이블 이름 (기본: Workflows)
WORKFLOWS_TABLE = DynamoDBConfig.WORKFLOWS_TABLE
# SCHEDULED_INDEX_NAME: GSI 이름 (기본: ScheduledWorkflowsIndex)
SCHEDULED_INDEX_NAME = DynamoDBConfig.SCHEDULED_WORKFLOWS_INDEX
# Orchestrator State Machine ARN (다른 핸들러들과 동일한 환경변수 이름 사용)
WORKFLOW_ORCHESTRATOR_ARN = os.environ.get("WORKFLOW_ORCHESTRATOR_ARN")

# --- AWS 클라이언트 초기화 ---
# Lazy Step Functions client: create on demand to avoid import-time errors in test environments
_sfn_client = None
# Allow tests to monkeypatch scheduler.sfn directly
sfn = None

def get_sfn():
    global _sfn_client
    if _sfn_client is None:
        try:
            _sfn_client = boto3.client('stepfunctions')
        except Exception:
            # In constrained test environments boto3.client might not be available
            # Tests typically monkeypatch the scheduler.sfn attribute directly, so
            # fall back to a simple object that will raise if used unexpectedly.
            class _Dummy:
                def start_execution(self, *args, **kwargs):
                    raise RuntimeError('StepFunctions client unavailable in this environment')
            _sfn_client = _Dummy()
    return _sfn_client

# 항상 기본 테이블 이름으로 초기화하여 다른 핸들러들과 일관되게 사용
table = dynamodb.Table(WORKFLOWS_TABLE)


def calculate_next_run(cron_expression: str) -> int:
    """Cron 표현식을 기반으로 다음 실행 시간을 Unix 타임스탬프로 계산합니다."""
    now = datetime.now()
    # croniter를 사용하여 다음 실행 시각을 계산
    cron = croniter(cron_expression, now)
    next_run_datetime = cron.get_next(datetime)
    return int(next_run_datetime.timestamp())


def lambda_handler(event, context):
    """
    EventBridge 스케줄러에 의해 주기적으로 호출되어, 실행 시간이 된 워크플로우를 찾아
    Step Functions 실행을 시작시키는 메인 핸들러 함수입니다.
    """
    # 필수 환경변수 검사: 테이블은 항상 초기화되므로 주로 오케스트레이터 ARN 확인
    if not all([WORKFLOWS_TABLE, SCHEDULED_INDEX_NAME, WORKFLOW_ORCHESTRATOR_ARN]):
        logger.error("환경 변수가 올바르게 설정되지 않았습니다.")
        return {"status": "error", "message": "Missing environment variables"}
    
    current_timestamp = int(time.time())
    logger.info(f"스케줄러 실행 시작. 현재 시각: {current_timestamp}")

    try:
        # --- 1. 실행 시간이 된 워크플로우 조회 ---
        # GSI를 사용하여 효율적으로 쿼리합니다.
        # 페이지 처리: GSI에서 조건에 맞는 모든 아이템을 반복적으로 조회
        due_workflows = []
        exclusive_start_key = None
        while True:
            kwargs = {
                'IndexName': SCHEDULED_INDEX_NAME,
                'KeyConditionExpression': Key('is_scheduled').eq('true') & Key('next_run_time').lte(current_timestamp),
            }
            if exclusive_start_key:
                kwargs['ExclusiveStartKey'] = exclusive_start_key

            response = table.query(**kwargs)
            items = response.get('Items', [])
            due_workflows.extend(items)

            exclusive_start_key = response.get('LastEvaluatedKey')
            if not exclusive_start_key:
                break

        logger.info(f"실행 대상 워크플로우 {len(due_workflows)}개 발견.")

        # --- 2. 각 워크플로우에 대해 Step Functions 실행 ---
        for workflow in due_workflows:
            workflow_id = workflow.get('workflowId')
            raw_config = workflow.get('config')

            # DynamoDB에 config가 문자열로 저장된 경우를 처리
            if isinstance(raw_config, str):
                try:
                    config = json.loads(raw_config)
                except (json.JSONDecodeError, ValueError):
                    logger.warning(f"워크플로우 {workflow_id}의 config를 JSON으로 파싱하지 못했습니다. 원시값 사용.")
                    config = {}
            else:
                config = raw_config or {}

            schedule_expression = config.get('trigger', {}).get('schedule_expression')

            if not workflow_id or not schedule_expression:
                logger.warning(f"잘못된 워크플로우 항목 발견 (ID 또는 스케줄 정보 누락): {workflow}")
                continue

            try:
                # --- 2a. Step Functions 실행 시작 ---
                execution_name = f"{workflow_id}-{int(time.time() * 1000)}"
                # allow tests to monkeypatch scheduler.sfn (or replace get_sfn)
                sfn_client = globals().get('sfn') or get_sfn()
                sfn_client.start_execution(
                    stateMachineArn=WORKFLOW_ORCHESTRATOR_ARN,
                    name=execution_name,
                    input=json.dumps({
                        # Step Functions의 첫 상태가 이 workflowId를 사용하여 설계를 가져옵니다.
                        "workflowId": workflow_id 
                    })
                )
                logger.info(f"워크플로우 {workflow_id}에 대한 Step Functions 실행 시작. (Execution: {execution_name})")

                # --- 2b. 다음 실행 시간 계산 및 DB 업데이트 ---
                next_run_timestamp = calculate_next_run(schedule_expression)
                # The Workflows table uses a composite primary key (ownerId, workflowId).
                # The GSI query above returns the ownerId as well, so use both keys
                # to update the item safely. If ownerId is missing for an item,
                # log and skip the update to avoid malformed Key errors.
                owner_id = workflow.get('ownerId') or workflow.get('ownerId')
                if not owner_id:
                    logger.warning(f"ownerId missing for workflow {workflow_id}; skipping next_run_time update")
                else:
                    table.update_item(
                        Key={'ownerId': owner_id, 'workflowId': workflow_id},
                        UpdateExpression="SET next_run_time = :next_run",
                        ExpressionAttributeValues={":next_run": next_run_timestamp}
                    )
                logger.info(f"워크플로우 {workflow_id}의 다음 실행 시간 업데이트: {next_run_timestamp}")

            except Exception as e:
                logger.error(f"워크플로우 {workflow_id} 처리 중 오류 발생: {str(e)}")
                # 한 워크플로우의 실패가 다른 워크플로우에 영향을 주지 않도록 루프 계속
                continue
                
        return {"status": "success", "triggered_workflows": len(due_workflows)}

    except Exception as e:
        logger.error(f"스케줄러 람다 실행 중 심각한 오류 발생: {str(e)}")
        raise e
