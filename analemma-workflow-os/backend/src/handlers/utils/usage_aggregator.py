import json
import logging
import os
import boto3
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional

# 공통 모듈에서 AWS 클라이언트 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource, get_ssm_client
    dynamodb = get_dynamodb_resource()
    ssm_client = get_ssm_client()
except ImportError:
    dynamodb = boto3.resource('dynamodb')
    ssm_client = boto3.client('ssm')

# 구조화된 로깅 설정
try:
    logger = get_logger(__name__)
except:
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

# 환경 변수
EXECUTIONS_TABLE = os.environ.get('EXECUTIONS_TABLE')
USER_USAGE_TABLE = os.environ.get('USER_USAGE_TABLE', 'UserUsageTable')
PRICING_CONFIG_PARAM = os.environ.get('PRICING_CONFIG_PARAM', '/analemma/pricing')

executions_table = dynamodb.Table(EXECUTIONS_TABLE) if EXECUTIONS_TABLE else None
usage_table = dynamodb.Table(USER_USAGE_TABLE) if USER_USAGE_TABLE else None

# 공통 모듈 임포트
try:
    from src.common.constants import TTLConfig, ModelPricing, QuotaLimits
    from src.common.logging_utils import get_logger, log_business_event
    from src.common.error_handlers import handle_dynamodb_error
except ImportError:
    # Fallback for backward compatibility
    class TTLConfig:
        PRICING_CACHE = 3600
    class ModelPricing:
        DEFAULT_MODELS = {}
        DEFAULT_MODEL = "gpt-3.5-turbo"
        TOKENS_PER_THOUSAND = Decimal("1000")
        COST_PRECISION = Decimal("0.000001")
    class QuotaLimits:
        USAGE_COLLECTION_SAMPLE_SIZE = 50
        USAGE_COLLECTION_MAX_DEPTH = 10
        MAX_OUTPUT_SIZE_BYTES = 1024 * 1024

# 가격 정책 캐싱 (Cold Start 최적화)
_pricing_cache = None
_pricing_cache_time = None
CACHE_DURATION = TTLConfig.PRICING_CACHE


def get_pricing_config() -> Dict[str, Any]:
    """Parameter Store에서 가격 정책을 가져와 캐싱"""
    global _pricing_cache, _pricing_cache_time

    current_time = datetime.now(timezone.utc).timestamp()

    # 캐시가 유효하면 재사용
    if _pricing_cache and _pricing_cache_time and (current_time - _pricing_cache_time) < CACHE_DURATION:
        return _pricing_cache

    try:
        response = ssm_client.get_parameter(Name=PRICING_CONFIG_PARAM)
        pricing_json = response['Parameter']['Value']
        _pricing_cache = json.loads(pricing_json)
        _pricing_cache_time = current_time

        logger.info("Loaded pricing configuration from src.Parameter Store")
        return _pricing_cache

    except Exception as e:
        logger.warning(f"Failed to load pricing config, using defaults: {e}")

        # 기본 가격 정책 (fallback)
        _pricing_cache = {
            model: {
                "input_per_1k": str(pricing["input_per_1k"]),
                "output_per_1k": str(pricing["output_per_1k"])
            }
            for model, pricing in ModelPricing.DEFAULT_MODELS.items()
        }
        _pricing_cache_time = current_time
        return _pricing_cache


def calculate_cost(usage: Dict[str, Any], model: str) -> Decimal:
    """
    사용량 데이터를 기반으로 비용 계산 (Decimal 사용으로 정밀도 보장)
    Parameter Store에서 가격 정책을 동적으로 로드
    """
    try:
        pricing_config = get_pricing_config()

        # 모델 이름 정규화 (대소문자 무시, 버전 차이 무시)
        model_key = None
        model_lower = model.lower()

        for key in pricing_config:
            if key.lower() in model_lower:
                model_key = key
                break

        if not model_key:
            logger.warning(f"Unknown model '{model}', using default pricing")
            model_key = ModelPricing.DEFAULT_MODEL

        model_pricing = pricing_config[model_key]

        # Decimal로 변환하여 정밀도 보장
        prompt_tokens = Decimal(str(usage.get('prompt_tokens', 0)))
        completion_tokens = Decimal(str(usage.get('completion_tokens', 0)))
        input_price = Decimal(model_pricing['input_per_1k'])
        output_price = Decimal(model_pricing['output_per_1k'])

        # 비용 계산: (토큰 / 1000) * 가격
        input_cost = (prompt_tokens / ModelPricing.TOKENS_PER_THOUSAND) * input_price
        output_cost = (completion_tokens / ModelPricing.TOKENS_PER_THOUSAND) * output_price

        total_cost = input_cost + output_cost

        # 소수점 6자리로 반올림 (마이크로센트 단위)
        return total_cost.quantize(ModelPricing.COST_PRECISION, rounding=ROUND_HALF_UP)

    except Exception as e:
        logger.exception(f"Error calculating cost for model {model}: {e}")
        return Decimal('0')


# 구조화된 로깅 데코레이터 적용 (fallback 패턴)
try:
    from src.common.logging_utils import log_execution_context
    
    def lambda_handler(event, context):
        """
        Step Functions 워크플로우 완료 이벤트를 처리하여 사용량을 집계하고 비용을 계산
        EventBridge에서 호출됨
        """
        return _lambda_handler_impl(event, context)
        
except ImportError:
    def lambda_handler(event, context):
        """
        Step Functions 워크플로우 완료 이벤트를 처리하여 사용량을 집계하고 비용을 계산
        EventBridge에서 호출됨
        """
        return _lambda_handler_impl(event, context)


def _lambda_handler_impl(event, context):
    """실제 Lambda 핸들러 구현 로직"""
    try:
        logger.info("Usage aggregator started")

        # EventBridge 이벤트에서 실행 정보 추출
        detail = event.get('detail', {})
        execution_arn = detail.get('executionArn')
        state = detail.get('stateEnteredEventDetails', {}).get('name', '')

        if not execution_arn:
            logger.warning("No execution ARN in event")
            return {"status": "skipped", "reason": "no_execution_arn"}

        # 완료된 실행만 처리
        if state != 'SUCCEEDED':
            logger.info(f"Skipping non-successful execution: {state}")
            return {"status": "skipped", "reason": f"state_{state}"}

        # 실행 세부 정보 조회
        execution_details = get_execution_details(execution_arn)
        if not execution_details:
            logger.error(f"Could not retrieve execution details for {execution_arn}")
            return {"status": "error", "reason": "execution_not_found"}

        # 사용량 데이터 추출 및 집계
        usage_data = extract_usage_from_execution(execution_details)
        if not usage_data:
            logger.info("No usage data found in execution")
            return {"status": "skipped", "reason": "no_usage_data"}

        # 사용자별 사용량 업데이트
        owner_id = extract_owner_id_from_execution(execution_arn)
        if not owner_id:
            logger.error("No ownerId found in execution")
            return {"status": "error", "reason": "no_owner_id"}

        update_user_usage(owner_id, usage_data)

        logger.info(f"Successfully processed usage for execution {execution_arn}")
        return {
            "status": "success",
            "execution_arn": execution_arn,
            "owner_id": owner_id,
            "total_tokens": usage_data.get('total_tokens', 0),
            "total_cost": usage_data.get('total_cost', 0)
        }

    except Exception as e:
        logger.exception(f"Error in usage aggregator: {e}")
        return {"status": "error", "reason": str(e)}


def get_execution_details(execution_arn: str) -> Optional[Dict[str, Any]]:
    """Step Functions에서 실행 세부 정보 조회"""
    try:
        sf_client = boto3.client('stepfunctions')
        response = sf_client.describe_execution(executionArn=execution_arn)
        return response
    except Exception as e:
        logger.error(f"Failed to get execution details: {e}")
        return None


def extract_usage_from_execution(execution_details: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """실행 결과에서 사용량 데이터 추출 (최적화된 버전)"""
    try:
        output = execution_details.get('output', '{}')
        if isinstance(output, str):
            output = json.loads(output)

        # 출력 크기 체크 (Lambda 메모리 제한 고려)
        output_size = len(json.dumps(output))
        if output_size > QuotaLimits.MAX_OUTPUT_SIZE_BYTES:
            logger.warning(
                "Large execution output detected",
                extra={
                    "output_size_bytes": output_size,
                    "threshold_bytes": QuotaLimits.MAX_OUTPUT_SIZE_BYTES,
                    "execution_arn": execution_arn
                }
            )

        # 워크플로우 실행 결과에서 사용량 집계
        total_tokens = 0
        total_cost = Decimal('0')
        models_used = []
        usage_found = False

        # 최적화된 사용량 데이터 수집 (깊이 제한)
        def collect_usage(data, depth=0, max_depth=QuotaLimits.USAGE_COLLECTION_MAX_DEPTH):
            nonlocal total_tokens, total_cost, models_used, usage_found

            # 깊이 제한으로 무한 재귀 방지
            if depth > max_depth:
                return

            if isinstance(data, dict):
                # 노드별 사용량 데이터
                if 'usage' in data:
                    usage = data['usage']
                    if isinstance(usage, dict):
                        tokens = usage.get('total_tokens', 0)
                        model = data.get('model', 'unknown')

                        if tokens > 0:
                            usage_found = True
                            total_tokens += tokens
                            cost = calculate_cost(usage, model)
                            total_cost += cost

                            if model not in models_used:
                                models_used.append(model)

                # 재귀적으로 모든 하위 객체 검사 (최적화)
                # usage 키가 있는 객체 위주로 탐색
                for key, value in data.items():
                    if key == 'usage':
                        continue  # 이미 위에서 처리함
                    elif isinstance(value, (dict, list)):
                        collect_usage(value, depth + 1, max_depth)

            elif isinstance(data, list):
                # 리스트는 샘플링하여 탐색 (성능 최적화)
                sample_size = min(len(data), QuotaLimits.USAGE_COLLECTION_SAMPLE_SIZE)
                for item in data[:sample_size]:
                    collect_usage(item, depth + 1, max_depth)

        collect_usage(output)

        if not usage_found or total_tokens == 0:
            return None

        return {
            'total_tokens': total_tokens,
            'total_cost': float(total_cost),  # JSON 직렬화를 위해 float 변환
            'models_used': models_used,
            'execution_count': 1
        }

    except Exception as e:
        logger.exception(f"Failed to extract usage: {e}")
        return None


def extract_owner_id_from_execution(execution_arn: str) -> Optional[str]:
    """실행 ARN에서 owner ID 추출"""
    try:
        # execution ARN에서 execution ID 추출 후 DynamoDB에서 조회
        execution_id = execution_arn.split(':')[-1]

        if executions_table:
            response = executions_table.get_item(Key={'executionId': execution_id})
            item = response.get('Item')
            if item:
                return item.get('ownerId')

        return None
    except Exception as e:
        logger.error(f"Failed to extract owner ID: {e}")
        return None


def update_user_usage(owner_id: str, usage_data: Dict[str, Any]):
    """사용자별 사용량 테이블 업데이트 (Atomic Update로 Race Condition 방지)"""
    if not usage_table:
        logger.warning("User usage table not configured - skipping update")
        return

    try:
        current_month = datetime.now(timezone.utc).strftime('%Y-%m')

        # modelsUsed가 있다면 set으로 변환 (DynamoDB SS 타입 대응)
        current_models = usage_data.get('models_used', [])

        # Atomic Update를 위한 UpdateExpression 구성
        add_actions = [
            "totalTokens :tokens",
            "totalCost :cost",
            "executionCount :count"
        ]
        
        expression_attribute_values = {
            ':tokens': usage_data['total_tokens'],
            ':cost': Decimal(str(usage_data['total_cost'])),  # Float 오차 방지
            ':count': usage_data['execution_count'],
            ':time': datetime.now(timezone.utc).isoformat(),
            ':ts': int(datetime.now(timezone.utc).timestamp())
        }

        # 모델 리스트도 ADD 절에 포함 (String Set Union)
        if current_models:
            add_actions.append("modelsUsed :models")
            # set으로 변환하여 전달하면 DynamoDB가 알아서 SS(String Set)으로 저장 및 병합
            expression_attribute_values[':models'] = set(current_models)

        # ADD 절 완성
        update_expression = "ADD " + ", ".join(add_actions)
        
        # SET 절 추가
        update_expression += " SET lastUpdated = :time, updatedAt = :ts"

        # Atomic Update 실행
        usage_table.update_item(
            Key={
                'ownerId': owner_id,
                'period': current_month
            },
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues='ALL_NEW'  # 업데이트된 전체 아이템 반환 (디버깅용)
        )

        logger.info(f"Atomically updated usage for user {owner_id}: +{usage_data['total_tokens']} tokens, +${usage_data['total_cost']:.6f}")

    except Exception as e:
        logger.exception(f"Failed to update user usage: {e}")
        # DLQ로 메시지 전송하거나 다른 복구 로직을 추가할 수 있음