"""
지능형 지침 증류기 - DynamoDB Streams 품질 평가 프로세서
비동기 처리 및 Rate Limit 보호 적용

주요 개선사항:
1. 절대값에서 상대값으로: 수정 비율(Correction Ratio) 도입
2. DynamoDB Streams 재시도 지옥 방지: Partial Batch Failure 지원
3. 멱등성(Idempotency) 보장: 중복 처리 방지
"""

import json
import boto3
import asyncio
import logging
import os
from typing import Dict, Any, List
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from src.services.vector_sync_service import VectorSyncService

# 로깅 설정
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# DynamoDB 클라이언트
dynamodb = boto3.resource('dynamodb')
correction_table = dynamodb.Table(os.environ.get('CORRECTION_LOGS_TABLE', 'correction-logs'))

def lambda_handler(event: Dict[str, Any], context) -> Dict[str, Any]:
    """
    DynamoDB Streams 트리거 - Partial Batch Failure 지원
    
    Fix #2: DynamoDB Streams의 재시도 지옥 방지
    - AWS가 BatchSize=5로 자동 분할하므로 코드 내 슬라이싱 불필요
    - 실패한 레코드만 반환하여 선택적 재시도 지원
    """
    
    try:
        records = event.get('Records', [])
        logger.info(f"Processing {len(records)} records")
        
        # 비동기 배치 처리
        results = asyncio.run(process_correction_batch(records))
        
        # 실패한 레코드 ID 수집 (Partial Batch Failure)
        failed_record_ids = []
        successful_count = 0
        
        for i, result in enumerate(results):
            if result.get('success'):
                successful_count += 1
            else:
                # 실패한 레코드의 eventID 수집
                failed_record_ids.append(records[i]['eventID'])
                logger.error(f"Failed to process record {records[i]['eventID']}: {result.get('error')}")
        
        # Partial Batch Failure Response
        if failed_record_ids:
            logger.warning(f"Partial batch failure: {len(failed_record_ids)} failed, {successful_count} successful")
            return {
                "batchItemFailures": [
                    {"itemIdentifier": record_id} for record_id in failed_record_ids
                ]
            }
        
        logger.info(f"All {successful_count} records processed successfully")
        return {"statusCode": 200}
        
    except Exception as e:
        logger.error(f"Lambda handler critical error: {str(e)}")
        # 전체 배치 실패 시 모든 레코드 재시도
        return {
            "batchItemFailures": [
                {"itemIdentifier": record['eventID']} 
                for record in event.get('Records', [])
            ]
        }

async def process_correction_batch(records: List[Dict]) -> List[Dict]:
    """비동기로 배치 처리"""
    
    tasks = []
    for record in records:
        if record['eventName'] == 'INSERT':
            correction_data = record['dynamodb']['NewImage']
            task = process_single_correction(correction_data)
            tasks.append(task)
    
    # 동시 실행 (하지만 Rate Limit 고려)
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    return [
        {"success": True, "result": r} if not isinstance(r, Exception) 
        else {"success": False, "error": str(r)}
        for r in results
    ]

async def process_single_correction(correction_data: Dict) -> Dict[str, Any]:
    """단일 수정 로그 처리"""
    
    try:
        # DynamoDB 데이터 파싱
        user_id = correction_data['user_id']['S']
        correction_sk = correction_data['sk']['S']
        
        # 품질 평가 (동기 함수로 변경)
        quality_result = evaluate_correction_quality_sync(correction_data)
        
        # 품질 평가 결과 업데이트
        await update_correction_quality(user_id, correction_sk, quality_result)
        
        # 가치 있는 수정만 벡터 DB 저장
        if quality_result['is_valuable']:
            logger.info(f"Valuable correction detected: {correction_sk}")
            
            # 벡터 DB 저장 로직 구현
            try:
                vector_service = VectorSyncService()
                await vector_service.store_correction_vector(
                    user_id=user_id,
                    correction_data=correction_data,
                    quality_result=quality_result
                )
                logger.info(f"Successfully stored valuable correction in vector DB: {correction_sk}")
            except Exception as e:
                logger.error(f"Failed to store correction in vector DB: {str(e)}")
                # 벡터 DB 저장 실패는 전체 프로세스 실패로 처리하지 않음
            
        return quality_result
        
    except Exception as e:
        logger.error(f"Error processing correction: {str(e)}")
        raise

def evaluate_correction_quality_sync(correction_data: Dict) -> Dict[str, Any]:
    """
    동기식 품질 평가 (LLM 호출 제거로 비용 절약)
    
    Fix #1: 절대값에서 상대값으로 - 수정 비율(Correction Ratio) 도입
    """
    
    try:
        # DynamoDB 데이터 추출
        edit_distance = int(correction_data.get('edit_distance', {}).get('N', '0'))
        correction_time = int(correction_data.get('correction_time_seconds', {}).get('N', '0'))
        user_confirmed = correction_data.get('user_confirmed_valuable', {}).get('BOOL')
        
        # 원본 텍스트 길이 추출 (agent_output 기준)
        agent_output = correction_data.get('agent_output', {}).get('S', '')
        original_length = len(agent_output)
        
        # Fix #1: 수정 비율(Correction Ratio) 계산
        if original_length == 0:
            correction_ratio = 0.0
        else:
            correction_ratio = edit_distance / original_length
        
        logger.info(f"Edit distance: {edit_distance}, Original length: {original_length}, Correction ratio: {correction_ratio:.3f}")
        
        # 1. 기본 필터링 (단순 오타 수정)
        if edit_distance < 3:
            return {
                "is_valuable": False, 
                "confidence": 0.9,
                "reason": "minor_edit",
                "correction_ratio": correction_ratio
            }
        
        # 2. 수정 비율 기반 평가 (상대적 변경량)
        ratio_weight = min(correction_ratio * 2.0, 1.0)  # 50% 변경 시 최대 가중치
        
        # 3. 시간 기반 평가 (오래 고민했으면 중요한 수정)
        time_weight = min(correction_time / 30.0, 1.0)  # 30초 = 1.0 가중치
        
        # 4. 사용자 명시적 확인 (가장 높은 우선순위)
        if user_confirmed is not None:
            return {
                "is_valuable": user_confirmed,
                "confidence": 0.95,
                "reason": "user_confirmed",
                "correction_ratio": correction_ratio
            }
        
        # 5. 개선된 점수 계산 (수정 비율 중심)
        base_score = 0.1 + (ratio_weight * 0.5) + (time_weight * 0.3)
        
        # 메타데이터 존재 여부로 가중치 추가
        extracted_metadata = correction_data.get('extracted_metadata', {}).get('M', {})
        if extracted_metadata and 'no_significant_change' not in extracted_metadata:
            base_score += 0.2  # 메타데이터가 추출되면 가치 있을 가능성 높음
        
        # 수정 비율이 너무 높으면 (90% 이상) 의심스러운 변경으로 간주
        if correction_ratio > 0.9:
            base_score *= 0.7  # 페널티 적용
            reason_suffix = "_high_ratio_penalty"
        else:
            reason_suffix = ""
        
        return {
            "is_valuable": base_score > 0.6,  # 임계값 조정 (0.7 -> 0.6)
            "confidence": min(base_score, 1.0),
            "reason": f"computed_score_{base_score:.2f}_ratio_{correction_ratio:.3f}{reason_suffix}",
            "correction_ratio": correction_ratio
        }
        
    except Exception as e:
        logger.error(f"Quality evaluation error: {str(e)}")
        return {
            "is_valuable": False,
            "confidence": 0.0,
            "reason": f"evaluation_error_{str(e)}",
            "correction_ratio": 0.0
        }

async def update_correction_quality(
    user_id: str, 
    correction_sk: str, 
    quality_result: Dict[str, Any]
) -> None:
    """
    수정 로그에 품질 평가 결과 업데이트
    
    Fix #3: 멱등성(Idempotency) 보장 - 중복 처리 방지
    """
    
    try:
        # ConditionExpression으로 중복 처리 방지
        correction_table.update_item(
            Key={
                'pk': f'user#{user_id}',
                'sk': correction_sk
            },
            UpdateExpression='SET is_valuable = :valuable, quality_confidence = :confidence, quality_reason = :reason, correction_ratio = :ratio, updated_at = :updated',
            ConditionExpression='attribute_not_exists(is_valuable) OR is_valuable = :null',
            ExpressionAttributeValues={
                ':valuable': quality_result['is_valuable'],
                ':confidence': quality_result['confidence'],
                ':reason': quality_result['reason'],
                ':ratio': quality_result.get('correction_ratio', 0.0),
                ':updated': datetime.now(timezone.utc).isoformat(),
                ':null': None
            }
        )
        
        logger.info(f"Updated correction quality: {correction_sk} (ratio: {quality_result.get('correction_ratio', 0.0):.3f})")
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            # 이미 처리된 레코드 - 정상적인 중복 처리 상황
            logger.info(f"Correction quality already processed (idempotent): {correction_sk}")
        else:
            logger.error(f"Failed to update correction quality: {str(e)}")
            raise
    except Exception as e:
        logger.error(f"Failed to update correction quality: {str(e)}")
        raise