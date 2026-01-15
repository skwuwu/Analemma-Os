# -*- coding: utf-8 -*-
"""
Clone Instructions Lambda Handler

기존 워크플로우에서 학습된 지침을 새 워크플로우로 복제합니다.
원본 가중치를 유지하여 검증된 지침의 신뢰도를 상속합니다.

엔드포인트: POST /instructions/clone
요청 바디:
{
    "source_workflow_id": "wf-source-123",
    "target_workflow_id": "wf-target-456"
}

응답:
{
    "message": "5개의 노드 지침이 성공적으로 복제되었습니다.",
    "cloned_count": 5,
    "source_workflow_id": "wf-source-123",
    "target_workflow_id": "wf-target-456"
}

성능 최적화:
- BatchGetItem을 사용하여 N+1 쿼리 문제 해결
- TypeSerializer를 모듈 레벨에서 재사용
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from decimal import Decimal

import boto3
from boto3.dynamodb.types import TypeSerializer, TypeDeserializer
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 환경 변수 - 🚨 [Critical Fix] 기본값을 template.yaml과 일치시킴
DISTILLED_INSTRUCTIONS_TABLE = os.environ.get("DISTILLED_INSTRUCTIONS_TABLE", "DistilledInstructionsTable")

# AWS 클라이언트 (지연 초기화 - 테스트 시 모킹 가능)
_dynamodb = None
_dynamodb_client = None
_instructions_table = None

def get_dynamodb():
    """DynamoDB resource 지연 초기화"""
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb

def get_dynamodb_client():
    """DynamoDB client 지연 초기화"""
    global _dynamodb_client
    if _dynamodb_client is None:
        _dynamodb_client = boto3.client("dynamodb")
    return _dynamodb_client

def get_instructions_table():
    """Instructions table 지연 초기화"""
    global _instructions_table
    if _instructions_table is None:
        _instructions_table = get_dynamodb().Table(DISTILLED_INSTRUCTIONS_TABLE)
    return _instructions_table

# TypeSerializer/Deserializer 모듈 레벨에서 재사용 (성능 최적화 - AWS 연결 불필요)
type_serializer = TypeSerializer()
type_deserializer = TypeDeserializer()

# Batch 작업 한계
BATCH_WRITE_LIMIT = 25
BATCH_GET_LIMIT = 100



class DecimalEncoder(json.JSONEncoder):
    """DynamoDB Decimal 타입을 JSON으로 직렬화"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    지침 복제 API 핸들러
    """
    try:
        logger.info(f"Received clone request: {json.dumps(event, default=str)[:500]}")
        
        # 요청 파싱
        body = json.loads(event.get("body", "{}"))
        
        # Cognito 인증에서 owner_id 추출
        claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
        owner_id = claims.get("sub")
        
        if not owner_id:
            return _response(401, {"error": "인증 정보가 없습니다."})
        
        source_wf = body.get("source_workflow_id")
        target_wf = body.get("target_workflow_id")
        
        if not source_wf or not target_wf:
            return _response(400, {"error": "source_workflow_id와 target_workflow_id가 필요합니다."})
        
        if source_wf == target_wf:
            return _response(400, {"error": "소스와 대상 워크플로우가 동일합니다."})
        
        # 지침 복제 실행
        result = clone_instructions(
            owner_id=owner_id,
            source_workflow_id=source_wf,
            target_workflow_id=target_wf
        )
        
        return _response(200, result)
        
    except json.JSONDecodeError:
        return _response(400, {"error": "잘못된 JSON 형식입니다."})
    except Exception as e:
        logger.error(f"Clone instructions failed: {e}", exc_info=True)
        return _response(500, {"error": str(e)})


def clone_instructions(
    owner_id: str,
    source_workflow_id: str,
    target_workflow_id: str
) -> Dict[str, Any]:
    """
    원본 워크플로우의 모든 활성 지침을 대상 워크플로우로 복제합니다.
    
    성능 최적화:
    - BatchGetItem을 사용하여 N+1 쿼리 문제 해결 (최대 100개 항목을 1회 호출로 조회)
    - 원본 가중치를 유지하여 검증된 지침의 신뢰도를 상속
    - usage_count는 0으로 초기화하여 새 환경에서의 사용량을 별도 추적
    
    Args:
        owner_id: 사용자 ID (IDOR 방지를 위해 인증된 사용자만 허용)
        source_workflow_id: 원본 워크플로우 ID
        target_workflow_id: 대상 워크플로우 ID
    
    Returns:
        복제 결과 정보
    """
    source_pk = f"{owner_id}#{source_workflow_id}"
    target_pk = f"{owner_id}#{target_workflow_id}"
    
    # 1. 원본 워크플로우의 LATEST# 인덱스 조회
    latest_pointers = _get_latest_pointers(source_pk)
    
    if not latest_pointers:
        return {
            "message": "복제할 지침이 없습니다.",
            "cloned_count": 0,
            "source_workflow_id": source_workflow_id,
            "target_workflow_id": target_workflow_id
        }
    
    # 2. BatchGetItem을 위한 키 목록 생성
    keys_to_fetch = [
        {"pk": source_pk, "sk": p["latest_instruction_sk"]}
        for p in latest_pointers
        if "latest_instruction_sk" in p
    ]
    
    if not keys_to_fetch:
        return {
            "message": "복제할 지침이 없습니다.",
            "cloned_count": 0,
            "source_workflow_id": source_workflow_id,
            "target_workflow_id": target_workflow_id
        }
    
    # 3. BatchGetItem으로 실제 지침 데이터 일괄 조회 (N+1 문제 해결)
    source_items = _batch_get_instructions(keys_to_fetch)
    
    if not source_items:
        return {
            "message": "복제할 활성 지침이 없습니다.",
            "cloned_count": 0,
            "source_workflow_id": source_workflow_id,
            "target_workflow_id": target_workflow_id
        }
    
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    new_timestamp = now.strftime("%Y%m%d%H%M%S")
    
    items_to_write: List[Dict[str, Any]] = []
    
    # 4. 복제 데이터 생성
    for item in source_items:
        node_id = item["sk"].split("#")[0]
        target_sk = f"{node_id}#{new_timestamp}"
        
        # 복제 데이터 생성 (원본 가중치 유지, usage_count 초기화)
        cloned_item = _create_cloned_item(
            original=item,
            target_pk=target_pk,
            target_sk=target_sk,
            target_workflow_id=target_workflow_id,
            source_workflow_id=source_workflow_id,
            now_iso=now_iso
        )
        
        # LATEST# 인덱스 항목 생성
        latest_index_item = {
            "pk": target_pk,
            "sk": f"LATEST#{node_id}",
            "latest_instruction_sk": target_sk,
            "updated_at": now_iso,
            "is_cloned": True,
            "cloned_from": source_workflow_id
        }
        
        items_to_write.append(cloned_item)
        items_to_write.append(latest_index_item)
    
    # 5. BatchWriteItem으로 일괄 저장 (25개 단위로 분할)
    if items_to_write:
        _batch_write_items(items_to_write)
    
    cloned_count = len(source_items)
    logger.info(f"Cloned {cloned_count} instructions from {source_workflow_id} to {target_workflow_id}")
    
    return {
        "message": f"{cloned_count}개의 노드 지침이 성공적으로 복제되었습니다.",
        "cloned_count": cloned_count,
        "source_workflow_id": source_workflow_id,
        "target_workflow_id": target_workflow_id
    }


def _get_latest_pointers(pk: str) -> List[Dict[str, Any]]:
    """LATEST# 인덱스 항목들 조회"""
    try:
        response = get_instructions_table().query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={
                ":pk": pk,
                ":sk": "LATEST#"
            }
        )
        return response.get("Items", [])
    except ClientError as e:
        logger.error(f"Failed to query LATEST pointers: {e}")
        return []


def _batch_get_instructions(keys: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """
    BatchGetItem으로 여러 지침을 일괄 조회 (N+1 쿼리 문제 해결)
    
    최대 100개의 항목을 단 한 번의 네트워크 호출로 가져옵니다.
    비활성화된 지침(is_active=False)은 결과에서 제외됩니다.
    
    Args:
        keys: {"pk": ..., "sk": ...} 형태의 키 목록
    
    Returns:
        활성화된 지침 항목 목록
    """
    if not keys:
        return []
    
    all_items: List[Dict[str, Any]] = []
    
    # 100개 단위로 분할 (BatchGetItem 한계)
    for i in range(0, len(keys), BATCH_GET_LIMIT):
        batch_keys = keys[i:i + BATCH_GET_LIMIT]
        
        # DynamoDB 형식으로 키 변환
        formatted_keys = [
            {
                "pk": type_serializer.serialize(k["pk"]),
                "sk": type_serializer.serialize(k["sk"])
            }
            for k in batch_keys
        ]
        
        request_items = {
            DISTILLED_INSTRUCTIONS_TABLE: {
                "Keys": formatted_keys
            }
        }
        
        # 재시도 로직 (UnprocessedKeys 처리)
        while request_items:
            try:
                response = get_dynamodb_client().batch_get_item(RequestItems=request_items)
                
                # 결과 처리 (DynamoDB 형식 -> Python dict)
                raw_items = response.get("Responses", {}).get(DISTILLED_INSTRUCTIONS_TABLE, [])
                for raw_item in raw_items:
                    item = {k: type_deserializer.deserialize(v) for k, v in raw_item.items()}
                    
                    # 비활성화된 지침은 제외
                    if item.get("is_active", True):
                        all_items.append(item)
                
                # UnprocessedKeys가 있으면 재시도
                unprocessed = response.get("UnprocessedKeys", {})
                if unprocessed:
                    logger.warning(f"Retry: {len(unprocessed.get(DISTILLED_INSTRUCTIONS_TABLE, {}).get('Keys', []))} unprocessed keys")
                    request_items = unprocessed
                else:
                    break
                    
            except ClientError as e:
                logger.error(f"BatchGetItem failed: {e}")
                break
    
    return all_items


def _create_cloned_item(
    original: Dict[str, Any],
    target_pk: str,
    target_sk: str,
    target_workflow_id: str,
    source_workflow_id: str,
    now_iso: str
) -> Dict[str, Any]:
    """
    복제된 지침 항목 생성
    
    원본 가중치(weight)는 유지하여 검증된 지침의 신뢰도를 상속합니다.
    usage_count는 0으로 초기화하여 새 환경에서의 사용량을 별도 추적합니다.
    """
    cloned = dict(original)
    
    # PK/SK 변경
    cloned["pk"] = target_pk
    cloned["sk"] = target_sk
    
    # 워크플로우 ID 변경
    cloned["workflow_id"] = target_workflow_id
    
    # 메타데이터 업데이트
    cloned["created_at"] = now_iso
    cloned["cloned_from"] = source_workflow_id
    cloned["cloned_at"] = now_iso
    
    # 사용량 초기화 (새 환경에서 별도 추적)
    cloned["usage_count"] = 0
    cloned["total_applications"] = 0
    
    # 원본 가중치, success_rate, weighted_instructions 유지
    # (이미 original에서 복사됨)
    
    # 활성 상태 유지
    cloned["is_active"] = True
    
    return cloned


def _to_dynamodb_format(item: Dict[str, Any]) -> Dict[str, Any]:
    """Python dict를 DynamoDB 형식으로 변환 (모듈 레벨 serializer 재사용)"""
    return {k: type_serializer.serialize(v) for k, v in item.items()}


def _batch_write_items(items: List[Dict[str, Any]]) -> None:
    """
    BatchWriteItem으로 항목들을 25개 단위로 저장
    
    UnprocessedItems가 있으면 재시도합니다.
    """
    # 25개 단위로 분할
    for i in range(0, len(items), BATCH_WRITE_LIMIT):
        batch = items[i:i + BATCH_WRITE_LIMIT]
        
        request_items = {
            DISTILLED_INSTRUCTIONS_TABLE: [
                {"PutRequest": {"Item": _to_dynamodb_format(item)}}
                for item in batch
            ]
        }
        
        # 재시도 로직 (최대 3회)
        for attempt in range(3):
            try:
                response = get_dynamodb_client().batch_write_item(RequestItems=request_items)
                
                # UnprocessedItems 처리
                unprocessed = response.get("UnprocessedItems", {})
                if not unprocessed:
                    break
                
                logger.warning(f"Retry {attempt + 1}: {len(unprocessed.get(DISTILLED_INSTRUCTIONS_TABLE, []))} unprocessed items")
                request_items = unprocessed
                
            except ClientError as e:
                logger.error(f"Batch write failed (attempt {attempt + 1}): {e}")
                if attempt == 2:
                    raise


def _response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """API Gateway 응답 형식"""
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "POST,OPTIONS"
        },
        "body": json.dumps(body, ensure_ascii=False, cls=DecimalEncoder)
    }
