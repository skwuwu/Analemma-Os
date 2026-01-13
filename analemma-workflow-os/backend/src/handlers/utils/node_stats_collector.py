# -*- coding: utf-8 -*-
"""
NodeStats Collector Lambda

Step Functions 상태 변화 이벤트를 수신하여 노드별 실행 시간 통계를 업데이트합니다.

트리거: EventBridge (Step Functions Execution Status Change)
출력: DynamoDB NodeStats 테이블 업데이트

가중 이동 평균(WMA) 공식:
    New_Avg = (Old_Avg × 0.9) + (Current_Duration × 0.1)
"""

import os
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 환경 변수
NODE_STATS_TABLE = os.environ.get("NODE_STATS_TABLE", "NodeStats")
DECAY_FACTOR = Decimal("0.9")  # 기존 평균 가중치
CURRENT_FACTOR = Decimal("0.1")  # 현재 값 가중치
OUTLIER_THRESHOLD_MULTIPLIER = Decimal("3.0")  # 아웃라이어 임계값 (평균의 3배)
TTL_DAYS = 90  # 통계 데이터 TTL (일)

# DynamoDB 클라이언트
dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
table = dynamodb.Table(NODE_STATS_TABLE)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    EventBridge에서 Step Functions 상태 변화 이벤트를 처리합니다.
    
    이벤트 구조:
    {
        "source": "aws.states",
        "detail-type": "Step Functions Execution Status Change",
        "detail": {
            "executionArn": "arn:aws:states:...",
            "stateMachineArn": "arn:aws:states:...",
            "status": "SUCCEEDED",
            "startDate": 1704326400000,
            "stopDate": 1704326450000,
            "input": "{...}",
            "output": "{...}"
        }
    }
    """
    try:
        logger.info(f"Received event: {json.dumps(event, default=str)[:500]}")
        
        # EventBridge 이벤트 파싱
        detail = event.get("detail", {})
        detail_type = event.get("detail-type", "")
        
        # Step Functions 상태 변화 이벤트 처리
        if detail_type == "Step Functions Execution Status Change":
            return _process_execution_status_change(detail)
        
        # 개별 상태 전환 이벤트 처리 (상세 추적 활성화 시)
        if detail_type == "Step Functions State Machine Execution Status Change":
            return _process_state_transition(detail)
        
        logger.info(f"Ignoring event type: {detail_type}")
        return {"statusCode": 200, "body": "Ignored"}
        
    except Exception as e:
        logger.error(f"Error processing event: {e}", exc_info=True)
        return {"statusCode": 500, "body": str(e)}


def _process_execution_status_change(detail: Dict[str, Any]) -> Dict[str, Any]:
    """
    전체 실행 상태 변화 처리 (SUCCEEDED, FAILED 등)
    """
    status = detail.get("status", "")
    
    # 완료된 실행만 처리
    if status not in ("SUCCEEDED", "FAILED", "TIMED_OUT"):
        return {"statusCode": 200, "body": f"Skipping status: {status}"}
    
    # 실행 시간 계산
    start_date = detail.get("startDate")  # milliseconds
    stop_date = detail.get("stopDate")    # milliseconds
    
    if not start_date or not stop_date:
        logger.warning("Missing start/stop date")
        return {"statusCode": 200, "body": "Missing timestamps"}
    
    duration_seconds = (stop_date - start_date) / 1000.0
    
    # State Machine ARN에서 워크플로우 이름 추출
    state_machine_arn = detail.get("stateMachineArn", "")
    workflow_name = state_machine_arn.split(":")[-1] if state_machine_arn else "unknown"
    
    # 노드 타입 추론 (전체 실행의 경우 워크플로우 레벨)
    node_type = f"workflow:{workflow_name}"
    
    # 통계 업데이트
    _update_node_stats(node_type, duration_seconds, status == "SUCCEEDED")
    
    logger.info(f"Updated stats for {node_type}: {duration_seconds:.2f}s")
    return {"statusCode": 200, "body": f"Updated {node_type}"}


def _process_state_transition(detail: Dict[str, Any]) -> Dict[str, Any]:
    """
    개별 상태 전환 처리 (Express Workflow 상세 추적)
    
    상세 이벤트 구조:
    {
        "name": "ClassifyIntent",
        "type": "TaskStateExited",
        "timestamp": "2024-01-04T12:00:00Z",
        "previousEventId": 5,
        "input": "{...}",
        "output": "{...}",
        "inputDetails": {...},
        "outputDetails": {...}
    }
    """
    state_name = detail.get("name", "unknown")
    event_type = detail.get("type", "")
    
    # 상태 완료 이벤트만 처리
    if not event_type.endswith("Exited"):
        return {"statusCode": 200, "body": f"Skipping event type: {event_type}"}
    
    # 실행 시간 추출 (detail에 duration이 있는 경우)
    duration_ms = detail.get("durationMs") or detail.get("duration")
    
    if duration_ms:
        duration_seconds = float(duration_ms) / 1000.0
    else:
        # 타임스탬프에서 계산 (이전 이벤트 참조 필요)
        logger.warning(f"No duration for state {state_name}")
        return {"statusCode": 200, "body": "No duration available"}
    
    # 노드 타입 추론
    node_type = _infer_node_type(state_name, detail)
    
    # 통계 업데이트
    _update_node_stats(node_type, duration_seconds, True)
    
    logger.info(f"Updated state stats: {state_name} ({node_type}): {duration_seconds:.2f}s")
    return {"statusCode": 200, "body": f"Updated {node_type}"}


def _infer_node_type(state_name: str, detail: Dict[str, Any]) -> str:
    """
    상태 이름에서 노드 타입 추론
    """
    state_name_lower = state_name.lower()
    
    # LLM 관련 노드
    if any(kw in state_name_lower for kw in ["llm", "ai", "generate", "reason", "classify", "write", "analyze"]):
        if "classify" in state_name_lower:
            return "classification"
        elif "generate" in state_name_lower or "write" in state_name_lower:
            return "generation"
        elif "reason" in state_name_lower or "analyze" in state_name_lower:
            return "reasoning"
        return "llm_default"
    
    # 데이터 처리 노드
    if any(kw in state_name_lower for kw in ["extract", "parse"]):
        return "extraction"
    if any(kw in state_name_lower for kw in ["transform", "convert"]):
        return "transformation"
    if any(kw in state_name_lower for kw in ["validate", "check", "verify"]):
        return "validation"
    
    # 외부 연동 노드
    if any(kw in state_name_lower for kw in ["api", "http", "request", "call"]):
        return "api_call"
    if any(kw in state_name_lower for kw in ["db", "dynamo", "database", "query"]):
        return "database"
    
    return "default"


def _update_node_stats(
    node_type: str, 
    duration_seconds: float, 
    is_success: bool
) -> None:
    """
    DynamoDB NodeStats 테이블 업데이트 (Atomic Update + 아웃라이어 필터링 + TTL)
    
    테이블 스키마:
    - PK: node_type (String)
    - avg_duration_seconds: 평균 실행 시간 (Number)
    - sample_count: 샘플 수 (Number)
    - success_count: 성공 샘플 수 (Number)
    - success_rate: 성공률 (Number)
    - last_duration_seconds: 마지막 실행 시간 (Number)
    - last_updated: 마지막 업데이트 시간 (String)
    - ttl: TTL 타임스탬프 (Number)
    """
    current_duration = Decimal(str(duration_seconds))
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    
    # TTL 계산 (현재 시간 + 90일)
    ttl_timestamp = int((now.timestamp() + (TTL_DAYS * 24 * 60 * 60)))
    
    try:
        # 기존 데이터 조회
        response = table.get_item(Key={"node_type": node_type})
        item = response.get("Item", {})
        
        old_avg = item.get("avg_duration_seconds", current_duration)
        old_sample_count = int(item.get("sample_count", 0))
        old_success_count = int(item.get("success_count", 0))
        
        # 아웃라이어 판별: 현재 평균의 3배 초과 시 제외
        if old_sample_count > 0:
            outlier_threshold = old_avg * OUTLIER_THRESHOLD_MULTIPLIER
            if current_duration > outlier_threshold:
                logger.warning(
                    f"Outlier detected for {node_type}: {float(current_duration)}s > {float(outlier_threshold)}s (threshold). Skipping update."
                )
                return
        
        # 새로운 통계 계산
        new_sample_count = old_sample_count + 1
        new_success_count = old_success_count + (1 if is_success else 0)
        new_success_rate = Decimal(str(new_success_count / new_sample_count))
        
        # 이동 평균 계산: old_avg * decay + current * current_factor
        if old_sample_count == 0:
            new_avg = current_duration
        else:
            new_avg = old_avg * DECAY_FACTOR + current_duration * CURRENT_FACTOR
        
        # Atomic Update 실행
        table.update_item(
            Key={"node_type": node_type},
            UpdateExpression="""
            SET avg_duration_seconds = :new_avg,
                sample_count = :new_sample_count,
                success_count = :new_success_count,
                success_rate = :new_success_rate,
                last_duration_seconds = :current,
                last_updated = :now_iso,
                ttl = :ttl
            """,
            ExpressionAttributeValues={
                ":new_avg": new_avg,
                ":new_sample_count": new_sample_count,
                ":new_success_count": new_success_count,
                ":new_success_rate": new_success_rate,
                ":current": current_duration,
                ":now_iso": now_iso,
                ":ttl": ttl_timestamp
            },
            ReturnValues="NONE"
        )
        
        logger.info(f"Updated stats for {node_type}: duration={float(current_duration)}s, new_avg={float(new_avg)}s")
        
    except ClientError as e:
        logger.error(f"DynamoDB error updating {node_type}: {e}")
        raise


def get_node_stats_for_eta(node_types: list) -> Dict[str, float]:
    """
    ETA 계산을 위한 노드별 평균 실행 시간 조회
    
    Args:
        node_types: 조회할 노드 타입 목록
        
    Returns:
        {node_type: avg_duration_seconds}
    """
    result = {}
    
    for node_type in node_types:
        try:
            response = table.get_item(Key={"node_type": node_type})
            item = response.get("Item", {})
            
            if item:
                result[node_type] = float(item.get("avg_duration_seconds", 5.0))
            else:
                # 기본값 사용
                result[node_type] = 5.0
                
        except ClientError as e:
            logger.warning(f"Failed to get stats for {node_type}: {e}")
            result[node_type] = 5.0
    
    return result
