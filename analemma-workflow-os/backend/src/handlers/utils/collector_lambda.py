"""
News Collector Lambda (v2.0)

뉴스 기사를 수집하여 EventBridge로 발행하는 Lambda.

Improvements (v2.0):
- [Fix #1] put_events 부분 실패(Partial Failure) 처리
- [Fix #2] Pydantic 기반 엄격한 스키마 검증
- [Fix #3] 256KB 페이로드 제한 대응 (Claim-Check 패턴)

EventBridge 제약:
- 이벤트 크기 제한: 256KB
- FailedEntryCount > 0 시 부분 실패 (예외 미발생)
"""
import os
import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from enum import Enum

import boto3
try:
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    class BotoCoreError(Exception):
        pass
    class ClientError(Exception):
        pass

# [Fix #2] Pydantic 기반 스키마 검증
try:
    from pydantic import BaseModel, Field, HttpUrl, field_validator, ValidationError
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    ValidationError = ValueError

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# Configuration
EVENT_BUS_NAME = os.getenv("EVENT_BUS_NAME", "my-app-event-bus")
EVENT_SOURCE = os.getenv("EVENT_SOURCE", "com.my-app.news-collector")
DETAIL_TYPE = os.getenv("DETAIL_TYPE", "New Article Found")

# [Fix #3] 페이로드 크기 제한 관련 설정
EVENTBRIDGE_MAX_SIZE_BYTES = 256 * 1024  # 256KB
CONTENT_TRUNCATE_THRESHOLD = 200 * 1024  # 200KB (여유 마진)
S3_CLAIM_CHECK_BUCKET = os.getenv("CLAIM_CHECK_BUCKET", "")  # S3 Claim-Check용 버킷
ENABLE_CLAIM_CHECK = os.getenv("ENABLE_CLAIM_CHECK", "false").lower() == "true"

# Module-level clients (Cold Start 최적화)
events_client: Optional[Any] = None
s3_client: Optional[Any] = None


# =============================================================================
# [Fix #2] Pydantic 스키마 정의
# =============================================================================

if PYDANTIC_AVAILABLE:
    class ArticlePayload(BaseModel):
        """
        뉴스 기사 페이로드 스키마.
        
        엄격한 검증을 통해 하위 시스템(AI 분석 노드 등)으로
        mal-formed 데이터가 흘러가는 것을 방지합니다.
        """
        title: str = Field(..., min_length=1, max_length=500, description="기사 제목")
        url: Optional[str] = Field(None, description="기사 URL")
        content: Optional[str] = Field(None, description="기사 본문")
        summary: Optional[str] = Field(None, max_length=2000, description="기사 요약")
        author: Optional[str] = Field(None, max_length=200, description="저자")
        published_at: Optional[str] = Field(None, description="발행 일시 (ISO 8601)")
        source: Optional[str] = Field(None, max_length=100, description="출처")
        categories: Optional[List[str]] = Field(default_factory=list, description="카테고리")
        tags: Optional[List[str]] = Field(default_factory=list, description="태그")
        language: Optional[str] = Field("ko", max_length=10, description="언어 코드")
        
        @field_validator('url')
        @classmethod
        def validate_url(cls, v):
            if v is not None and not (v.startswith('http://') or v.startswith('https://')):
                raise ValueError('URL must start with http:// or https://')
            return v
        
        @field_validator('published_at')
        @classmethod
        def validate_published_at(cls, v):
            if v is not None:
                try:
                    # ISO 8601 형식 검증
                    datetime.fromisoformat(v.replace('Z', '+00:00'))
                except ValueError:
                    raise ValueError('published_at must be ISO 8601 format')
            return v
        
        class Config:
            extra = "allow"  # 추가 필드 허용 (유연성)
else:
    # Pydantic 없을 때 fallback
    ArticlePayload = None


# =============================================================================
# AWS 클라이언트 초기화
# =============================================================================

def get_events_client():
    global events_client
    if events_client is None:
        events_client = boto3.client("events")
    return events_client


def get_s3_client():
    global s3_client
    if s3_client is None:
        s3_client = boto3.client("s3")
    return s3_client


# =============================================================================
# [Fix #3] Claim-Check 패턴 - 대용량 페이로드 S3 저장
# =============================================================================

def _calculate_payload_size(detail: Dict[str, Any]) -> int:
    """JSON 직렬화 후 바이트 크기 계산"""
    return len(json.dumps(detail, ensure_ascii=False).encode('utf-8'))


def _store_content_to_s3(content: str, article_id: str) -> str:
    """
    대용량 콘텐츠를 S3에 저장하고 키를 반환.
    
    Claim-Check 패턴: 본문 대신 S3 포인터만 이벤트에 포함.
    """
    if not S3_CLAIM_CHECK_BUCKET:
        raise ValueError("CLAIM_CHECK_BUCKET environment variable not set")
    
    client = get_s3_client()
    timestamp = datetime.now(timezone.utc).strftime('%Y/%m/%d')
    s3_key = f"claim-check/{timestamp}/{article_id}.txt"
    
    client.put_object(
        Bucket=S3_CLAIM_CHECK_BUCKET,
        Key=s3_key,
        Body=content.encode('utf-8'),
        ContentType='text/plain; charset=utf-8'
    )
    
    logger.info(f"Large content stored to S3: s3://{S3_CLAIM_CHECK_BUCKET}/{s3_key}")
    return f"s3://{S3_CLAIM_CHECK_BUCKET}/{s3_key}"


def _apply_claim_check_pattern(detail: Dict[str, Any]) -> Dict[str, Any]:
    """
    페이로드가 256KB를 초과할 경우 Claim-Check 패턴 적용.
    
    1. content 필드를 S3에 저장
    2. content 대신 content_ref (S3 URI) 포함
    3. 크기 제한 내로 축소된 페이로드 반환
    """
    payload_size = _calculate_payload_size(detail)
    
    if payload_size <= CONTENT_TRUNCATE_THRESHOLD:
        return detail  # 크기 문제 없음
    
    # content 필드가 있고 S3 Claim-Check가 활성화된 경우
    content = detail.get('content', '')
    if content and ENABLE_CLAIM_CHECK and S3_CLAIM_CHECK_BUCKET:
        # 고유 ID 생성
        article_id = hashlib.sha256(
            f"{detail.get('url', '')}{detail.get('title', '')}".encode()
        ).hexdigest()[:16]
        
        # S3에 저장하고 포인터로 대체
        content_ref = _store_content_to_s3(content, article_id)
        modified_detail = detail.copy()
        modified_detail['content'] = None  # 본문 제거
        modified_detail['content_ref'] = content_ref  # S3 포인터
        modified_detail['content_truncated'] = True
        modified_detail['original_content_size'] = len(content)
        
        logger.info(f"Applied Claim-Check pattern: {payload_size} bytes -> {_calculate_payload_size(modified_detail)} bytes")
        return modified_detail
    
    # Claim-Check 비활성화 시: 본문 절삭
    if content:
        # 안전한 크기로 본문 절삭
        max_content_len = 50000  # ~50KB for content
        modified_detail = detail.copy()
        modified_detail['content'] = content[:max_content_len] + "...[TRUNCATED]"
        modified_detail['content_truncated'] = True
        modified_detail['original_content_size'] = len(content)
        
        logger.warning(f"Content truncated from {len(content)} to {max_content_len} chars")
        return modified_detail
    
    return detail


# =============================================================================
# 이벤트 발행
# =============================================================================

def build_event(detail: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "EventBusName": EVENT_BUS_NAME,
        "Source": EVENT_SOURCE,
        "DetailType": DETAIL_TYPE,
        "Detail": json.dumps(detail, ensure_ascii=False),
    }


class PublishResult(Enum):
    """발행 결과 상태"""
    SUCCESS = "success"
    PARTIAL_FAILURE = "partial_failure"
    TOTAL_FAILURE = "total_failure"


def publish(detail: Dict[str, Any]) -> Dict[str, Any]:
    """
    EventBridge에 이벤트 발행.
    
    [Fix #1] 부분 실패 처리:
    - put_events는 일부 실패 시 예외를 던지지 않음
    - FailedEntryCount > 0 이면 부분 실패
    - Entries[i].ErrorCode, ErrorMessage로 상세 확인
    """
    client = get_events_client()
    
    # [Fix #3] 페이로드 크기 체크 및 Claim-Check 적용
    detail = _apply_claim_check_pattern(detail)
    
    event = build_event(detail)
    logger.debug("Putting event to EventBridge: %s", event)
    
    try:
        resp = client.put_events(Entries=[event])
        
        # ============================================================
        # [Fix #1] 부분 실패 처리
        # EventBridge는 Throttling, 권한 문제 등으로 일부 실패 시
        # 예외를 던지지 않고 FailedEntryCount에 기록만 함
        # ============================================================
        failed_count = resp.get('FailedEntryCount', 0)
        
        if failed_count > 0:
            # 실패한 항목의 상세 정보 추출
            entries = resp.get('Entries', [])
            failed_entries = [
                {
                    'index': i,
                    'error_code': e.get('ErrorCode'),
                    'error_message': e.get('ErrorMessage')
                }
                for i, e in enumerate(entries)
                if e.get('ErrorCode')
            ]
            
            logger.error(
                f"EventBridge partial failure: {failed_count}/{len(entries)} failed. "
                f"Details: {json.dumps(failed_entries)}"
            )
            
            # 재시도 가능한 에러인지 확인
            retryable_errors = {'ThrottlingException', 'InternalFailure'}
            is_retryable = any(
                e['error_code'] in retryable_errors 
                for e in failed_entries
            )
            
            if is_retryable:
                # 재시도 가능한 에러면 예외 발생 (상위에서 재시도 로직 처리)
                raise EventBridgePartialFailureError(
                    f"Retryable partial failure: {failed_entries}",
                    failed_entries=failed_entries,
                    is_retryable=True
                )
            else:
                # 재시도 불가능한 에러 (권한 문제 등)
                raise EventBridgePartialFailureError(
                    f"Non-retryable partial failure: {failed_entries}",
                    failed_entries=failed_entries,
                    is_retryable=False
                )
        
        logger.info("put_events success: %s", resp)
        return {
            'result': PublishResult.SUCCESS.value,
            'response': resp,
            'event_id': resp.get('Entries', [{}])[0].get('EventId')
        }
        
    except (BotoCoreError, ClientError) as e:
        logger.exception("Failed to publish event to EventBridge")
        raise


class EventBridgePartialFailureError(Exception):
    """EventBridge 부분 실패 커스텀 예외"""
    def __init__(self, message: str, failed_entries: List[Dict], is_retryable: bool):
        super().__init__(message)
        self.failed_entries = failed_entries
        self.is_retryable = is_retryable


# =============================================================================
# [Fix #2] 스키마 검증 함수
# =============================================================================

def validate_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pydantic을 사용한 엄격한 스키마 검증.
    
    검증 실패 시 ValidationError 발생.
    """
    if PYDANTIC_AVAILABLE and ArticlePayload:
        validated = ArticlePayload(**payload)
        return validated.model_dump(exclude_none=True)
    else:
        # Fallback: 기본 검증
        if not payload.get("title") and not payload.get("url"):
            raise ValueError("Missing required field: title or url")
        return payload


# =============================================================================
# Lambda Handler
# =============================================================================

def lambda_handler(event, context):
    """
    Lambda handler to accept an article payload and publish it to EventBridge.

    Expected input examples:
    - API Gateway proxy: { "body": "{...article json...}" }
    - Direct invoke / test: { ...article json... }
    
    Response codes:
    - 200: 발행 성공
    - 400: 페이로드 검증 실패
    - 422: 스키마 검증 실패 (Pydantic ValidationError)
    - 502: EventBridge 발행 실패 (재시도 가능)
    - 500: 내부 오류
    """
    logger.debug("collector_lambda received event: %s", event)

    # Support API Gateway proxy event where payload is in `body`
    payload = event.get("body") if isinstance(event, dict) and event.get("body") else event

    if isinstance(payload, str):
        try:
            detail = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.exception("Invalid JSON payload")
            return {
                "statusCode": 400, 
                "body": json.dumps({
                    "error": "Invalid JSON payload",
                    "detail": str(e)
                })
            }
    elif isinstance(payload, dict):
        detail = payload
    else:
        logger.error("Unsupported payload type: %s", type(payload))
        return {"statusCode": 400, "body": json.dumps({"error": "Unsupported payload type"})}

    # ============================================================
    # [Fix #2] Pydantic 기반 엄격한 스키마 검증
    # ============================================================
    try:
        validated_detail = validate_payload(detail)
    except ValidationError as e:
        logger.error(f"Schema validation failed: {e}")
        return {
            "statusCode": 422,  # Unprocessable Entity
            "body": json.dumps({
                "error": "Schema validation failed",
                "validation_errors": e.errors() if hasattr(e, 'errors') else str(e)
            })
        }
    except ValueError as e:
        logger.error(f"Payload validation failed: {e}")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": str(e)})
        }

    # 발행
    try:
        result = publish(validated_detail)
        return {
            "statusCode": 200, 
            "body": json.dumps({
                "result": result['result'],
                "event_id": result.get('event_id')
            })
        }
    except EventBridgePartialFailureError as e:
        # [Fix #1] 부분 실패 처리
        logger.error(f"EventBridge partial failure: {e}")
        status_code = 502 if e.is_retryable else 400
        return {
            "statusCode": status_code,
            "body": json.dumps({
                "error": "EventBridge publish partial failure",
                "failed_entries": e.failed_entries,
                "is_retryable": e.is_retryable
            })
        }
    except (BotoCoreError, ClientError) as e:
        logger.exception("Failed to publish event to EventBridge")
        return {"statusCode": 502, "body": json.dumps({"error": "EventBridge publish failed"})}
    except Exception:
        logger.exception("Unexpected error in collector_lambda")
        return {"statusCode": 500, "body": json.dumps({"error": "Internal server error"})}

