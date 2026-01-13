# -*- coding: utf-8 -*-
"""
Async Alias Generator Lambda

워크플로우 시작 시 LLM(Haiku)을 호출하여 비즈니스 별칭을 생성하고
DynamoDB 업데이트 + WebSocket 푸시로 프론트엔드에 실시간 반영합니다.

트리거: EventBridge (Step Functions Execution Started)
출력: 
  - DynamoDB Task 테이블 업데이트
  - WebSocket API를 통한 실시간 푸시

Optimistic UI 패턴:
  1. 사용자는 처음에 "작업 #A1B2" 표시
  2. 1~2초 후 자동으로 "OOO 고객 건"으로 업데이트
"""

import os
import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 환경 변수
TASK_TABLE = os.environ.get("TASK_TABLE", "TaskTable")
WEBSOCKET_API_ENDPOINT = os.environ.get("WEBSOCKET_API_ENDPOINT", "")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")
HAIKU_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"

# AWS 클라이언트
dynamodb = boto3.resource("dynamodb")
task_table = dynamodb.Table(TASK_TABLE)

# Bedrock 클라이언트 (재시도 및 타임아웃 설정)
bedrock_config = Config(
    retries={"max_attempts": 2, "mode": "standard"},
    read_timeout=10,
    connect_timeout=5,
)
bedrock_client = boto3.client(
    "bedrock-runtime",
    region_name=BEDROCK_REGION,
    config=bedrock_config
)

# WebSocket API Gateway 클라이언트 (동적 생성)
_apigw_client = None


def get_apigw_client():
    """API Gateway Management API 클라이언트 (지연 초기화)"""
    global _apigw_client
    if _apigw_client is None and WEBSOCKET_API_ENDPOINT:
        # endpoint에서 도메인 추출: wss://xxx.execute-api.region.amazonaws.com/stage
        endpoint = WEBSOCKET_API_ENDPOINT.replace("wss://", "https://")
        _apigw_client = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=endpoint
        )
    return _apigw_client


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    EventBridge에서 Step Functions 시작 이벤트를 처리합니다.
    
    이벤트 구조:
    {
        "source": "aws.states",
        "detail-type": "Step Functions Execution Status Change",
        "detail": {
            "executionArn": "arn:aws:states:...",
            "stateMachineArn": "arn:aws:states:...",
            "status": "RUNNING",
            "startDate": 1704326400000,
            "input": "{\"customer_name\": \"홍길동\", ...}"
        }
    }
    """
    try:
        logger.info(f"Received event: {json.dumps(event, default=str)[:500]}")
        
        detail = event.get("detail", {})
        status = detail.get("status", "")
        
        # RUNNING 상태만 처리 (실행 시작)
        if status != "RUNNING":
            return {"statusCode": 200, "body": f"Skipping status: {status}"}
        
        execution_arn = detail.get("executionArn", "")
        execution_id = _extract_execution_id(execution_arn)
        
        # 입력 데이터 파싱
        input_str = detail.get("input", "{}")
        try:
            input_data = json.loads(input_str)
        except json.JSONDecodeError:
            input_data = {}
        
        # 워크플로우 이름 추출
        state_machine_arn = detail.get("stateMachineArn", "")
        workflow_name = state_machine_arn.split(":")[-1] if state_machine_arn else ""
        
        # LLM으로 별칭 생성
        alias = _generate_alias_with_llm(workflow_name, input_data, execution_id)
        
        if alias:
            # DynamoDB 업데이트
            _update_task_alias(execution_id, alias)
            
            # WebSocket 푸시
            _push_alias_update(execution_id, alias)
            
            logger.info(f"Generated alias for {execution_id[:8]}: {alias}")
            return {"statusCode": 200, "body": f"Generated: {alias}"}
        
        return {"statusCode": 200, "body": "No alias generated"}
        
    except Exception as e:
        logger.error(f"Error generating alias: {e}", exc_info=True)
        return {"statusCode": 500, "body": str(e)}


def _extract_execution_id(execution_arn: str) -> str:
    """실행 ARN에서 실행 ID 추출"""
    if not execution_arn:
        return "unknown"
    # arn:aws:states:region:account:execution:state-machine:execution-name
    parts = execution_arn.split(":")
    return parts[-1] if parts else "unknown"


def _generate_alias_with_llm(
    workflow_name: str,
    input_data: Dict[str, Any],
    execution_id: str
) -> Optional[str]:
    """
    Bedrock Haiku를 사용하여 비즈니스 별칭 생성
    """
    # 입력에서 핵심 정보 추출
    input_summary = _extract_input_summary(input_data)
    
    if not input_summary and not workflow_name:
        return _generate_fallback_alias(execution_id, workflow_name)
    
    # 프롬프트 구성
    prompt = f"""다음 정보를 바탕으로 이 작업을 5~10자 내외의 한국어로 요약해주세요.
단순 명사형으로, 문장이 아닌 키워드 형태로 답변하세요.

워크플로우: {workflow_name or '알 수 없음'}
입력 데이터: {input_summary or '없음'}

예시 형식:
- "홍길동 환불 문의"
- "11월 매출 보고서"
- "신규 고객 온보딩"

답변:"""
    
    try:
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 20,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        
        response = bedrock_client.invoke_model(
            body=json.dumps(payload),
            modelId=HAIKU_MODEL_ID
        )
        
        result = json.loads(response['body'].read())
        if 'content' in result and result['content']:
            alias = result['content'][0].get('text', '').strip().strip('"\'').strip()
            if 3 <= len(alias) <= 30:
                return alias
        
    except ClientError as e:
        logger.warning(f"Bedrock Haiku invocation failed: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error in Haiku invocation: {e}")
    
    return _generate_fallback_alias(execution_id, workflow_name)


def _extract_input_summary(input_data: Dict[str, Any], max_length: int = 200) -> str:
    """입력 데이터에서 핵심 정보 추출"""
    if not input_data:
        return ""
    
    # 우선순위 키
    priority_keys = ['customer_name', 'name', 'title', 'subject', 'query', 
                     'request', 'message', 'content', 'description', 'email']
    
    summary_parts = []
    
    for key in priority_keys:
        if key in input_data:
            value = str(input_data[key])[:50]
            summary_parts.append(f"{key}: {value}")
            if len(' '.join(summary_parts)) > max_length:
                break
    
    if not summary_parts:
        for key, value in list(input_data.items())[:3]:
            if isinstance(value, (str, int, float)):
                summary_parts.append(f"{key}: {str(value)[:30]}")
    
    return ' | '.join(summary_parts)[:max_length]


def _generate_fallback_alias(execution_id: str, workflow_name: str = "") -> str:
    """폴백 별칭 생성"""
    if workflow_name:
        keywords = workflow_name.replace('_', ' ').replace('-', ' ').split()
        if keywords:
            return ' '.join(keywords[:2])[:15]
    
    short_hash = hashlib.md5(execution_id.encode()).hexdigest()[:6].upper()
    return f"작업 #{short_hash}"


def _update_task_alias(execution_id: str, alias: str) -> None:
    """DynamoDB Task 테이블에 별칭 업데이트"""
    now_iso = datetime.now(timezone.utc).isoformat()
    
    try:
        task_table.update_item(
            Key={"execution_id": execution_id},
            UpdateExpression="SET execution_alias = :alias, alias_updated_at = :ts",
            ExpressionAttributeValues={
                ":alias": alias,
                ":ts": now_iso
            },
            ConditionExpression="attribute_exists(execution_id)"
        )
        logger.info(f"Updated alias in DynamoDB: {execution_id[:8]} -> {alias}")
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            # 아이템이 아직 없으면 생성
            task_table.put_item(
                Item={
                    "execution_id": execution_id,
                    "execution_alias": alias,
                    "alias_updated_at": now_iso,
                    "created_at": now_iso
                }
            )
            logger.info(f"Created new task with alias: {execution_id[:8]} -> {alias}")
        else:
            logger.error(f"DynamoDB update failed: {e}")
            raise


def _push_alias_update(execution_id: str, alias: str) -> None:
    """WebSocket을 통해 별칭 업데이트 푸시"""
    client = get_apigw_client()
    if not client:
        logger.info("WebSocket endpoint not configured, skipping push")
        return
    
    # TODO: 연결된 클라이언트 조회 및 푸시
    # 이 부분은 실제 WebSocket 연결 관리 로직에 맞게 구현 필요
    message = {
        "type": "ALIAS_UPDATE",
        "payload": {
            "execution_id": execution_id,
            "execution_alias": alias,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    }
    
    logger.info(f"Would push to WebSocket: {json.dumps(message, default=str)}")
    # 실제 구현:
    # connection_ids = _get_connections_for_execution(execution_id)
    # for conn_id in connection_ids:
    #     client.post_to_connection(
    #         ConnectionId=conn_id,
    #         Data=json.dumps(message).encode()
    #     )
