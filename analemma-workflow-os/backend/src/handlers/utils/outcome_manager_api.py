# -*- coding: utf-8 -*-
"""
Outcome Manager API Handler

결과물 중심(Outcome-First) UI를 위한 API 엔드포인트입니다.

핵심 원칙: "사용자는 결과에 집중하고, 과정은 필요할 때만 본다"

기능:
1. 결과물 목록 조회 (완성된 아티팩트 우선)
2. 축약된 히스토리 제공
3. 상세 히스토리는 on-demand 로딩

엔드포인트:
- GET /tasks/{taskId}/outcomes - 결과물 목록
- GET /tasks/{taskId}/outcomes/{artifactId}/reasoning - 사고 과정 조회
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 환경 변수
EXECUTIONS_TABLE = os.environ.get("EXECUTIONS_TABLE", "ExecutionsTable")
S3_BUCKET = os.environ.get("WORKFLOW_STATE_BUCKET", "")
PRESIGNED_URL_EXPIRY_SECONDS = int(os.environ.get("PRESIGNED_URL_EXPIRY_SECONDS", "3600"))  # 1시간

# 인메모리 캐시 (Lambda warm start 간 공유)
_reasoning_cache: Dict[str, tuple] = {}  # {cache_key: (data, timestamp)}
CACHE_TTL_SECONDS = 300  # 5분

# AWS 클라이언트
dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))
executions_table = dynamodb.Table(EXECUTIONS_TABLE)
s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))


# =============================================================================
# Pydantic 스키마
# =============================================================================

class OutcomeItem(BaseModel):
    """단일 결과물"""
    artifact_id: str
    artifact_type: str
    title: str
    preview_text: Optional[str] = None
    content_ref: Optional[str] = None
    download_url: Optional[str] = None
    is_final: bool = False
    version: int = 1
    created_at: str
    logic_trace_id: Optional[str] = None
    word_count: Optional[int] = None
    file_size_bytes: Optional[int] = None


class CollapsedHistoryResponse(BaseModel):
    """축약된 히스토리"""
    summary: str
    node_count: int = 0
    llm_call_count: int = 0
    total_duration_seconds: Optional[float] = None
    key_decisions: List[str] = []
    full_trace_available: bool = False


class OutcomesResponse(BaseModel):
    """결과물 목록 응답"""
    task_id: str
    task_title: str
    status: str
    outcomes: List[OutcomeItem]
    collapsed_history: CollapsedHistoryResponse
    correction_applied: bool = False
    last_updated: str


class ReasoningStep(BaseModel):
    """사고 과정 단계"""
    step_id: str
    timestamp: str
    step_type: str  # decision, observation, action, reasoning
    content: str
    node_id: Optional[str] = None
    confidence: Optional[float] = None


class ReasoningPathResponse(BaseModel):
    """상세 사고 과정 응답"""
    artifact_id: str
    artifact_title: str
    reasoning_steps: List[ReasoningStep]
    total_steps: int
    total_duration_seconds: Optional[float] = None


# =============================================================================
# 메인 핸들러
# =============================================================================

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    API Gateway 요청 처리
    """
    try:
        http_method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")
        path = event.get("path") or event.get("rawPath", "")
        path_params = event.get("pathParameters", {}) or {}
        
        task_id = path_params.get("taskId") or path_params.get("task_id")
        artifact_id = path_params.get("artifactId") or path_params.get("artifact_id")
        
        # 소유자 ID 추출 (Cognito JWT claims)
        request_owner_id = event.get("requestContext", {}).get("authorizer", {}).get("claims", {}).get("sub")
        if not request_owner_id:
            return _error_response(401, "Unauthorized: missing owner_id")
        
        if not task_id:
            return _error_response(400, "task_id is required")
        
        # 라우팅
        if artifact_id and "reasoning" in path:
            # GET /tasks/{taskId}/outcomes/{artifactId}/reasoning
            return _get_reasoning_path(task_id, artifact_id, request_owner_id)
        else:
            # GET /tasks/{taskId}/outcomes
            return _get_outcomes(task_id, request_owner_id)
        
    except Exception as e:
        logger.error(f"Error in outcome manager: {e}", exc_info=True)
        return _error_response(500, str(e))


def _get_outcomes(task_id: str, request_owner_id: str) -> Dict[str, Any]:
    """
    결과물 목록 조회 (Outcome-First)
    """
    try:
        # DynamoDB에서 Task 조회
        response = executions_table.get_item(Key={"execution_id": task_id})
        task = response.get("Item")
        
        if not task:
            return _error_response(404, f"Task not found: {task_id}")
        
        # 소유권 검증
        task_owner_id = task.get("ownerId") or task.get("user_id") or task.get("created_by")
        if task_owner_id != request_owner_id:
            logger.warning(f"Unauthorized access attempt: user {request_owner_id} tried to access task {task_id}")
            return _error_response(403, "You do not have permission to access this task")
        
        # 결과물 추출 및 정렬 (최종 결과물 우선)
        artifacts = task.get("artifacts", [])
        outcomes = []
        
        for artifact in artifacts:
            is_final = artifact.get("is_final", False)
            extended = artifact.get("extended_metadata", {})
            
            outcome = OutcomeItem(
                artifact_id=artifact.get("artifact_id", ""),
                artifact_type=artifact.get("artifact_type", "text"),
                title=artifact.get("title", "결과물"),
                preview_text=artifact.get("preview_content", "")[:300] if artifact.get("preview_content") else None,
                content_ref=extended.get("content_ref"),
                download_url=_generate_presigned_url(extended.get("content_ref")),  # Presigned URL 생성
                is_final=is_final,
                version=extended.get("version", 1),
                created_at=artifact.get("created_at", datetime.now(timezone.utc).isoformat()),
                logic_trace_id=artifact.get("logic_trace_id") or extended.get("logic_trace_id"),
                word_count=extended.get("word_count"),
                file_size_bytes=extended.get("file_size_bytes"),
            )
            outcomes.append(outcome)
        
        # 최종 결과물 우선 정렬
        outcomes.sort(key=lambda x: (not x.is_final, x.created_at), reverse=True)
        
        # 축약된 히스토리 생성
        collapsed_history = _build_collapsed_history(task)
        
        # 응답 구성
        response_data = OutcomesResponse(
            task_id=task_id,
            task_title=task.get("execution_alias") or task.get("task_summary") or f"작업 #{task_id[:8]}",
            status=task.get("status", "UNKNOWN"),
            outcomes=[o.model_dump() for o in outcomes],
            collapsed_history=collapsed_history.model_dump(),
            correction_applied=task.get("correction_delta") is not None,
            last_updated=task.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )
        
        return {
            "statusCode": 200,
            "body": json.dumps(response_data.model_dump(), ensure_ascii=False, default=str)
        }
        
    except ClientError as e:
        logger.error(f"DynamoDB error: {e}")
        return _error_response(500, "Database error")


def _build_collapsed_history(task: Dict[str, Any]) -> CollapsedHistoryResponse:
    """
    축약된 히스토리 구축
    """
    # 기존 collapsed_history가 있으면 사용
    existing = task.get("collapsed_history", {})
    if existing:
        return CollapsedHistoryResponse(
            summary=existing.get("summary", ""),
            node_count=existing.get("node_count", 0),
            llm_call_count=existing.get("llm_call_count", 0),
            total_duration_seconds=existing.get("total_duration_seconds"),
            key_decisions=existing.get("key_decisions", [])[:3],
            full_trace_available=bool(existing.get("full_trace_ref")),
        )
    
    # 히스토리에서 동적 생성
    state_history = task.get("state_history", [])
    thought_history = task.get("thought_history", [])
    
    node_count = len(set(s.get("state_name", "") for s in state_history))
    llm_call_count = sum(1 for t in thought_history if "llm" in t.get("thought_type", "").lower())
    
    # 총 소요 시간 계산
    started_at = task.get("started_at")
    completed_at = task.get("completed_at") or task.get("updated_at")
    duration = None
    
    if started_at and completed_at:
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            duration = (end - start).total_seconds()
        except (ValueError, TypeError):
            pass
    
    # 핵심 의사결정 추출 (중요 표시된 thought)
    key_decisions = [
        t.get("message", "")[:100]
        for t in thought_history
        if t.get("is_important") or t.get("thought_type") == "decision"
    ][:3]
    
    # 요약 생성
    status = task.get("status", "UNKNOWN")
    if status in ("COMPLETED", "SUCCEEDED", "COMPLETE"):
        summary = f"{node_count}개의 단계를 거쳐 완료되었습니다."
    elif status in ("FAILED", "ERROR"):
        summary = f"{node_count}개의 단계 중 오류가 발생했습니다."
    elif status == "PAUSED_FOR_HITP":
        summary = f"{node_count}개의 단계를 진행 중, 승인 대기입니다."
    else:
        summary = f"{node_count}개의 단계를 진행 중입니다."
    
    return CollapsedHistoryResponse(
        summary=summary,
        node_count=node_count,
        llm_call_count=llm_call_count,
        total_duration_seconds=duration,
        key_decisions=key_decisions,
        full_trace_available=len(state_history) > 0,
    )


def _get_reasoning_path(task_id: str, artifact_id: str, request_owner_id: str) -> Dict[str, Any]:
    """
    특정 결과물의 상세 사고 과정 조회
    인메모리 캐싱을 적용하여 S3 호출 비용 절감
    """
    try:
        # DynamoDB에서 Task 조회
        response = executions_table.get_item(Key={"execution_id": task_id})
        task = response.get("Item")
        
        if not task:
            return _error_response(404, f"Task not found: {task_id}")
        
        # 소유권 검증
        task_owner_id = task.get("ownerId") or task.get("user_id") or task.get("created_by")
        if task_owner_id != request_owner_id:
            logger.warning(f"Unauthorized access attempt: user {request_owner_id} tried to access task {task_id}")
            return _error_response(403, "You do not have permission to access this task")
        
        # 상태 확인 - Empty State 처리
        status = task.get("status", "UNKNOWN")
        if status in ("QUEUED", "PENDING", "INITIALIZING"):
            return _user_friendly_response(
                task_id=task_id,
                artifact_id=artifact_id,
                status=status,
                message="AI가 작업을 준비하고 있습니다. 잠시 후 다시 확인해주세요."
            )
        elif status in ("RUNNING", "IN_PROGRESS"):
            return _user_friendly_response(
                task_id=task_id,
                artifact_id=artifact_id,
                status=status,
                message="AI가 결과물을 생성 중입니다. 완료되면 자동으로 업데이트됩니다."
            )
        
        # 해당 아티팩트 찾기
        artifacts = task.get("artifacts", [])
        target_artifact = None
        
        for artifact in artifacts:
            if artifact.get("artifact_id") == artifact_id:
                target_artifact = artifact
                break
        
        if not target_artifact:
            return _error_response(404, f"Artifact not found: {artifact_id}")
        
        # 사고 과정 추출
        logic_trace_id = target_artifact.get("logic_trace_id")
        reasoning_steps = []
        
        # thought_history에서 관련 항목 추출
        thought_history = task.get("thought_history", [])
        
        for i, thought in enumerate(thought_history):
            # logic_trace_id가 있으면 해당 시점까지만
            if logic_trace_id and thought.get("thought_id") == logic_trace_id:
                # 이 시점까지의 사고 과정
                break
            
            step = ReasoningStep(
                step_id=thought.get("thought_id", f"step_{i}"),
                timestamp=thought.get("timestamp", datetime.now(timezone.utc).isoformat()),
                step_type=_map_thought_type(thought.get("thought_type", "progress")),
                content=thought.get("message", ""),
                node_id=thought.get("node_id"),
                confidence=thought.get("confidence"),
            )
            reasoning_steps.append(step)
        
        # S3에서 추가 트레이스 로드 (있는 경우)
        extended = target_artifact.get("extended_metadata", {})
        reasoning_path_ref = extended.get("reasoning_path_ref")
        
        if reasoning_path_ref:
            additional_steps = _load_reasoning_from_s3(reasoning_path_ref)
            reasoning_steps.extend(additional_steps)
        
        # 응답 구성
        response_data = ReasoningPathResponse(
            artifact_id=artifact_id,
            artifact_title=target_artifact.get("title", "결과물"),
            reasoning_steps=[s.model_dump() for s in reasoning_steps],
            total_steps=len(reasoning_steps),
            total_duration_seconds=None,  # TODO: 계산
        )
        
        return {
            "statusCode": 200,
            "body": json.dumps(response_data.model_dump(), ensure_ascii=False, default=str)
        }
        
    except ClientError as e:
        logger.error(f"DynamoDB error: {e}")
        return _error_response(500, "Database error")


def _map_thought_type(thought_type: str) -> str:
    """thought_type을 reasoning step type으로 매핑"""
    mapping = {
        "progress": "observation",
        "decision": "decision",
        "question": "reasoning",
        "warning": "observation",
        "success": "action",
        "error": "observation",
    }
    return mapping.get(thought_type, "observation")


def _load_reasoning_from_s3(s3_ref: str) -> List[ReasoningStep]:
    """
    S3에서 상세 사고 과정 로드 (인메모리 캐싱 적용)
    """
    # 캐시 확인
    cache_key = s3_ref
    if cache_key in _reasoning_cache:
        cached_data, cached_time = _reasoning_cache[cache_key]
        if (datetime.now(timezone.utc) - cached_time).total_seconds() < CACHE_TTL_SECONDS:
            logger.info(f"Cache hit for reasoning path: {s3_ref}")
            return cached_data
    
    try:
        if s3_ref.startswith("s3://"):
            parts = s3_ref[5:].split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""
        else:
            bucket = S3_BUCKET
            key = s3_ref
        
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read().decode("utf-8")
        data = json.loads(content)
        
        steps = []
        for item in data.get("steps", []):
            steps.append(ReasoningStep(
                step_id=item.get("id", ""),
                timestamp=item.get("timestamp", ""),
                step_type=item.get("type", "observation"),
                content=item.get("content", ""),
                node_id=item.get("node_id"),
                confidence=item.get("confidence"),
            ))
        
        # 캐시에 저장
        _reasoning_cache[cache_key] = (steps, datetime.now(timezone.utc))
        
        # 캐시 크기 제한 (100개)
        if len(_reasoning_cache) > 100:
            oldest_key = min(_reasoning_cache, key=lambda k: _reasoning_cache[k][1])
            del _reasoning_cache[oldest_key]
        
        return steps
        
    except Exception as e:
        logger.warning(f"Failed to load reasoning from S3: {e}")
        return []


def _generate_presigned_url(content_ref: Optional[str]) -> Optional[str]:
    """
    S3 객체에 대한 Presigned URL 생성
    유효시간이 짧은 URL을 생성하여 보안 강화
    """
    if not content_ref:
        return None
    
    try:
        if content_ref.startswith("s3://"):
            parts = content_ref[5:].split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""
        else:
            bucket = S3_BUCKET
            key = content_ref
        
        if not bucket or not key:
            return None
        
        presigned_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=PRESIGNED_URL_EXPIRY_SECONDS
        )
        
        return presigned_url
        
    except Exception as e:
        logger.warning(f"Failed to generate presigned URL: {e}")
        return None


def _user_friendly_response(
    task_id: str,
    artifact_id: str,
    status: str,
    message: str
) -> Dict[str, Any]:
    """
    작업 진행 중일 때 사용자 친화적인 응답 생성
    에러 대신 상태 정보와 안내 메시지를 제공합니다.
    """
    response_data = {
        "task_id": task_id,
        "artifact_id": artifact_id,
        "status": status,
        "is_ready": False,
        "message": message,
        "reasoning_steps": [],
        "total_steps": 0,
        "retry_after_seconds": 5 if status in ("RUNNING", "IN_PROGRESS") else 10,
    }
    
    return {
        "statusCode": 202,  # Accepted (처리 중)
        "body": json.dumps(response_data, ensure_ascii=False)
    }


def _error_response(status_code: int, message: str) -> Dict[str, Any]:
    """에러 응답"""
    return {
        "statusCode": status_code,
        "body": json.dumps({"error": message}, ensure_ascii=False)
    }
