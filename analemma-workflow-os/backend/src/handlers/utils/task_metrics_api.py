# -*- coding: utf-8 -*-
"""
Task Metrics API Handler

Bento Grid UI 전용 메트릭스 API 엔드포인트입니다.
프론트엔드가 별도의 계산 로직 없이 grid_items를 1:1로 매핑할 수 있는 형태로 제공합니다.

엔드포인트: GET /tasks/{task_id}/metrics

응답 스키마:
{
  "display": {
    "title": "execution_alias",
    "status_color": "green | yellow | red",
    "eta_text": "약 3분 남음"
  },
  "grid_items": {
    "progress": { "value": 45, "label": "진행률", "sub_text": "지침 분석 중" },
    "confidence": { "value": 88.5, "level": "High", "breakdown": { "reflection": 90, "schema": 100 } },
    "autonomy": { "value": 95, "display": "자율도 95% (우수)" },
    "intervention": { "count": 2, "summary": "승인 2회", "history": [...] }
  }
}
"""

import os
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field
from typing import List

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 환경 변수
TASK_TABLE = os.environ.get("TASK_TABLE", "TaskTable")
EXECUTIONS_TABLE = os.environ.get("EXECUTIONS_TABLE", "ExecutionsTable")
NODE_STATS_TABLE = os.environ.get("NODE_STATS_TABLE", "NodeStatsTable")

# DynamoDB 클라이언트
dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
task_table = dynamodb.Table(TASK_TABLE)
executions_table = dynamodb.Table(EXECUTIONS_TABLE)
node_stats_table = dynamodb.Table(NODE_STATS_TABLE)


# =============================================================================
# Pydantic 스키마 정의
# =============================================================================

class DisplayInfo(BaseModel):
    """상단 표시 정보"""
    title: str = Field(description="작업 별칭 또는 제목")
    status_color: str = Field(description="상태 색상 (green, yellow, red)")
    eta_text: str = Field(description="예상 완료 시간 텍스트")
    status: str = Field(description="현재 상태")
    status_label: str = Field(description="상태 라벨 (한국어)")


class ProgressItem(BaseModel):
    """진행률 그리드 아이템"""
    value: int = Field(description="진행률 (0-100)")
    label: str = Field(default="진행률", description="라벨")
    sub_text: str = Field(description="현재 단계 설명")


class ConfidenceBreakdown(BaseModel):
    """신뢰도 세부 구성"""
    reflection: float = Field(description="자기 평가 점수")
    schema_match: float = Field(description="스키마 일치율", alias="schema")
    alignment: float = Field(description="지침 정합도")
    
    class Config:
        populate_by_name = True


class ConfidenceItem(BaseModel):
    """신뢰도 그리드 아이템"""
    value: float = Field(description="종합 신뢰도 (0-100)")
    level: str = Field(description="수준 (High, Medium, Low)")
    breakdown: ConfidenceBreakdown = Field(description="세부 구성")


class AutonomyItem(BaseModel):
    """자율도 그리드 아이템"""
    value: float = Field(description="자율도 (0-100)")
    display: str = Field(description="표시 문자열")


class InterventionHistoryEntry(BaseModel):
    """개입 이력 항목"""
    timestamp: Optional[str] = Field(description="발생 시간")
    type: str = Field(description="유형 (positive, negative, neutral)")
    reason: str = Field(description="사유")
    node_id: Optional[str] = Field(description="노드 ID")


class InterventionItem(BaseModel):
    """개입 이력 그리드 아이템"""
    count: int = Field(description="총 개입 횟수")
    summary: str = Field(description="요약 텍스트")
    positive_count: int = Field(description="긍정 개입 수")
    negative_count: int = Field(description="부정 개입 수")
    history: List[InterventionHistoryEntry] = Field(description="최근 이력")


class ResourcesItem(BaseModel):
    """리소스 사용량 그리드 아이템"""
    tokens: int = Field(description="사용된 토큰 수")
    cost_usd: float = Field(description="비용 (USD)")
    compute_time: str = Field(description="컴퓨팅 시간")


class GridItems(BaseModel):
    """벤토 그리드 아이템 컬렉션"""
    progress: ProgressItem
    confidence: ConfidenceItem
    autonomy: AutonomyItem
    intervention: InterventionItem
    resources: ResourcesItem


class TaskMetricsResponse(BaseModel):
    """전체 응답 스키마"""
    display: DisplayInfo
    grid_items: GridItems
    last_updated: str = Field(description="마지막 업데이트 시간")


# =============================================================================
# 메인 핸들러
# =============================================================================

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    API Gateway에서 GET /tasks/{task_id}/metrics 요청 처리
    """
    try:
        # Path Parameter에서 task_id 추출
        path_params = event.get("pathParameters", {}) or {}
        task_id = path_params.get("task_id") or path_params.get("taskId")
        
        if not task_id:
            return _error_response(400, "task_id is required")
        
        # 소유자 ID 추출 (Cognito JWT claims)
        request_owner_id = event.get("requestContext", {}).get("authorizer", {}).get("claims", {}).get("sub")
        if not request_owner_id:
            return _error_response(401, "Unauthorized: missing owner_id")
        
        # Query Parameter에서 tenant_id 추출 (multi-tenant 지원)
        query_params = event.get("queryStringParameters", {}) or {}
        tenant_id = query_params.get("tenant_id")
        
        # 메트릭스 조회 및 구성
        metrics = _get_task_metrics(task_id, request_owner_id, tenant_id)
        
        if not metrics:
            return _error_response(404, f"Task not found: {task_id}")
        
        return {
            "statusCode": 200,
            "body": json.dumps(metrics, ensure_ascii=False, default=str)
        }
        
    except Exception as e:
        logger.error(f"Error getting task metrics: {e}", exc_info=True)
        return _error_response(500, str(e))


def _get_task_metrics(task_id: str, request_owner_id: str, tenant_id: Optional[str] = None) -> Optional[Dict]:
    """
    DynamoDB에서 Task 정보를 조회하고 Bento Grid 형식으로 변환
    소유권 검증 포함
    """
    try:
        # Task 테이블에서 조회
        response = task_table.get_item(Key={"execution_id": task_id})
        task = response.get("Item")
        
        if not task:
            # Executions 테이블에서 시도
            response = executions_table.get_item(Key={"execution_id": task_id})
            task = response.get("Item")
        
        if not task:
            return None
        
        # 소유권 검증
        task_owner_id = task.get("ownerId") or task.get("user_id") or task.get("created_by")
        if task_owner_id != request_owner_id:
            logger.warning(f"Unauthorized access attempt: user {request_owner_id} tried to access task {task_id}")
            return None
        
        # Bento Grid 형식으로 변환
        return _format_bento_grid_response(task)
        
    except ClientError as e:
        logger.error(f"DynamoDB error: {e}")
        return None


def _format_bento_grid_response(task: Dict[str, Any]) -> Dict[str, Any]:
    """
    Task 데이터를 Bento Grid 응답 형식으로 변환
    모든 Decimal을 float로 변환하여 JSON 직렬화 이슈 해결
    """
    # 상태 정보
    status = task.get("status", "UNKNOWN")
    progress = float(task.get("progress_percentage", 0) or 0)
    
    # 1. Display 정보 구성
    display = {
        "title": task.get("execution_alias") or task.get("task_summary") or f"작업 #{task.get('execution_id', '')[:8]}",
        "status_color": _get_status_color(status, progress),
        "eta_text": task.get("estimated_completion_time") or _calculate_eta_text(progress, status, task),
        "status": status,
        "status_label": _get_status_label(status)
    }
    
    # 2. Progress 아이템
    progress_item = {
        "value": progress,
        "label": "진행률",
        "sub_text": task.get("current_step_name") or task.get("current_thought") or _get_progress_text(progress)
    }
    
    # 3. Confidence 아이템
    confidence_score = float(task.get("confidence_score", 70) or 70)
    confidence_components = task.get("confidence_components", {}) or {}
    
    confidence_item = {
        "value": confidence_score,
        "level": _get_confidence_level(confidence_score),
        "breakdown": {
            "reflection": float(confidence_components.get("self_reflection", 70)),
            "schema": float(confidence_components.get("schema_match", 90)),
            "alignment": float(confidence_components.get("instruction_alignment", 70))
        }
    }
    
    # 4. Autonomy 아이템
    autonomy_rate = float(task.get("autonomy_rate", 100) or 100)
    autonomy_item = {
        "value": autonomy_rate,
        "display": task.get("autonomy_display") or _get_autonomy_display(autonomy_rate)
    }
    
    # 5. Intervention 아이템
    intervention = task.get("intervention_history", {}) or {}
    if isinstance(intervention, str):
        try:
            intervention = json.loads(intervention)
        except json.JSONDecodeError:
            intervention = {}
    
    intervention_item = {
        "count": int(intervention.get("total_count", 0) or 0),
        "summary": intervention.get("summary") or "개입 없음",
        "positive_count": int(intervention.get("positive_count", 0) or 0),
        "negative_count": int(intervention.get("negative_count", 0) or 0),
        "history": intervention.get("history", [])[:5]  # 최근 5개만
    }
    
    # 6. Resource Usage 아이템 (추가)
    resource_usage = task.get("resource_usage", {}) or {}
    resources_item = {
        "tokens": int(resource_usage.get("tokens", 0) or 0),
        "cost_usd": float(resource_usage.get("cost_usd", 0) or 0),
        "compute_time": resource_usage.get("compute_time", "0s") or "0s"
    }
    
    return {
        "display": display,
        "grid_items": {
            "progress": progress_item,
            "confidence": confidence_item,
            "autonomy": autonomy_item,
            "intervention": intervention_item,
            "resources": resources_item  # 추가
        },
        "last_updated": task.get("updated_at") or datetime.now(timezone.utc).isoformat()
    }


def _get_status_color(status: str, progress: int) -> str:
    """상태와 진행률에 따른 색상 결정"""
    status_upper = status.upper()
    
    if status_upper in ("COMPLETED", "SUCCEEDED", "COMPLETE"):
        return "green"
    elif status_upper in ("FAILED", "TIMED_OUT", "ABORTED"):
        return "red"
    elif status_upper in ("PAUSED", "PAUSED_FOR_HITP", "PENDING_APPROVAL"):
        return "yellow"
    elif status_upper == "RUNNING":
        if progress >= 80:
            return "green"
        elif progress >= 40:
            return "yellow"
        else:
            return "blue"
    else:
        return "gray"


def _get_status_label(status: str) -> str:
    """상태 라벨 (한국어)"""
    labels = {
        "RUNNING": "진행 중",
        "COMPLETED": "완료",
        "SUCCEEDED": "완료",
        "COMPLETE": "완료",
        "FAILED": "실패",
        "TIMED_OUT": "시간 초과",
        "ABORTED": "중단됨",
        "PAUSED": "일시 정지",
        "PAUSED_FOR_HITP": "승인 대기",
        "PENDING_APPROVAL": "승인 대기",
        "QUEUED": "대기 중",
        "CANCELLED": "취소됨"
    }
    return labels.get(status.upper(), status)


def _calculate_eta_text(progress: float, status: str, task: Dict[str, Any]) -> str:
    """진행률과 상태 기반 ETA 텍스트 생성 (NodeStats 연동)"""
    if status.upper() in ("COMPLETED", "SUCCEEDED", "COMPLETE"):
        return "완료됨"
    elif status.upper() in ("FAILED", "TIMED_OUT", "ABORTED"):
        return "-"
    elif progress >= 95:
        return "곧 완료"
    
    try:
        # NodeStats에서 전체 평균 실행 시간 조회
        workflow_name = task.get("workflow_name") or "default"
        node_type = f"workflow:{workflow_name}"
        
        response = node_stats_table.get_item(Key={"node_type": node_type})
        node_stats = response.get("Item", {})
        avg_duration = float(node_stats.get("avg_duration_seconds", 300))  # 기본 5분
        
        # 남은 진행률에 따른 예상 시간 계산
        remaining_progress = (100 - progress) / 100
        estimated_seconds = remaining_progress * avg_duration
        
        if estimated_seconds < 60:
            return f"약 {int(estimated_seconds)}초"
        elif estimated_seconds < 3600:
            minutes = int(estimated_seconds / 60)
            return f"약 {minutes}분"
        else:
            hours = int(estimated_seconds / 3600)
            return f"약 {hours}시간"
            
    except Exception as e:
        logger.warning(f"Failed to calculate ETA from NodeStats: {e}")
        # 폴백: 기존 하드코딩 방식
        pass
    
    # 기존 하드코딩 방식 (폴백)
    if progress >= 80:
        return "약 1분"
    elif progress >= 50:
        return "약 2-3분"
    elif progress >= 20:
        return "약 5분"
    else:
        return "계산 중"


def _get_progress_text(progress: int) -> str:
    """진행률 기반 상태 텍스트"""
    if progress >= 90:
        return "마무리 중..."
    elif progress >= 70:
        return "결과 검증 중..."
    elif progress >= 50:
        return "데이터 처리 중..."
    elif progress >= 30:
        return "분석 진행 중..."
    elif progress >= 10:
        return "초기화 완료..."
    else:
        return "준비 중..."


def _get_confidence_level(score: float) -> str:
    """신뢰도 점수에 따른 수준"""
    if score >= 85:
        return "High"
    elif score >= 60:
        return "Medium"
    else:
        return "Low"


def _get_autonomy_display(rate: float) -> str:
    """자율도 표시 문자열"""
    if rate >= 90:
        return f"자율도 {rate:.0f}% (우수)"
    elif rate >= 70:
        return f"자율도 {rate:.0f}% (양호)"
    else:
        return f"자율도 {rate:.0f}% (개선 필요)"


def _error_response(status_code: int, message: str) -> Dict[str, Any]:
    """에러 응답 생성"""
    return {
        "statusCode": status_code,
        "body": json.dumps({"error": message}, ensure_ascii=False)
    }
