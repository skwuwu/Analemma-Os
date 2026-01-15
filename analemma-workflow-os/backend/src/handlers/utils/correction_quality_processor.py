"""
지능형 지침 증류기 - DynamoDB Streams 품질 평가 프로세서
비동기 처리 및 Rate Limit 보호 적용

주요 개선사항:
1. 절대값에서 상대값으로: 수정 비율(Correction Ratio) 도입
2. DynamoDB Streams 재시도 지옥 방지: Partial Batch Failure 지원
3. 멱등성(Idempotency) 보장: 중복 처리 방지
4. [v2.1] Gemini 1.5 Flash 세만틱 분석 통합 (애매한 점수일 때)
5. [v2.1] 전역 이벤트 루프 재사용 (Warm Start 최적화)
6. [v2.1] 벡터 DB 실패 시 재시도 유도
7. [v2.1] 짧은 텍스트 페널티 추가
"""

import json
import boto3
import asyncio
import logging
import os
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from src.services.vector_sync_service import VectorSyncService

# 로깅 설정
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# DynamoDB 클라이언트
dynamodb = boto3.resource('dynamodb')
# 🚨 [Critical Fix] 기본값을 template.yaml과 일치시킴
correction_table = dynamodb.Table(os.environ.get('CORRECTION_LOGS_TABLE', 'CorrectionLogsTable'))

# =============================================================================
# [v2.1] 전역 이벤트 루프 관리 (Warm Start 최적화)
# =============================================================================
_global_loop: Optional[asyncio.AbstractEventLoop] = None
_executor: Optional[ThreadPoolExecutor] = None

def get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    """
    [v2.1] 전역 이벤트 루프 재사용.
    Lambda Warm Start 시 asyncio.run() 오버헤드 제거.
    """
    global _global_loop
    
    if _global_loop is not None:
        try:
            if not _global_loop.is_closed():
                return _global_loop
        except Exception:
            pass
    
    try:
        _global_loop = asyncio.get_running_loop()
    except RuntimeError:
        _global_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_global_loop)
    
    return _global_loop

def safe_run_async(coro):
    """
    [v2.1] 안전한 비동기 실행 래퍼.
    이미 실행 중인 루프가 있으면 ThreadPoolExecutor 사용.
    """
    global _executor
    
    try:
        loop = asyncio.get_running_loop()
        # 이미 루프가 실행 중 - 별도 스레드에서 실행
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="async_runner")
        
        import concurrent.futures
        future = _executor.submit(lambda: asyncio.run(coro))
        return future.result(timeout=60)
    except RuntimeError:
        # 실행 중인 루프 없음 - 전역 루프 사용
        loop = get_or_create_event_loop()
        return loop.run_until_complete(coro)

# =============================================================================
# [v2.1] Gemini 1.5 Flash 통합 (세만틱 분석)
# =============================================================================
_gemini_model = None

def _get_gemini_model():
    """Lazy initialization of Gemini model."""
    global _gemini_model
    if _gemini_model is None:
        try:
            import google.generativeai as genai
            api_key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
            if api_key:
                genai.configure(api_key=api_key)
                _gemini_model = genai.GenerativeModel('gemini-1.5-flash')
                logger.info("Gemini 1.5 Flash model initialized")
            else:
                logger.warning("No Gemini API key found, semantic analysis disabled")
        except Exception as e:
            logger.warning(f"Failed to initialize Gemini model: {e}")
    return _gemini_model

async def _analyze_with_gemini(original: str, corrected: str) -> Optional[Dict[str, Any]]:
    """
    [v2.1] Gemini 1.5 Flash로 세만틱 분석 수행.
    
    점수가 애매할 때만 호출되어 비용 최적화.
    
    Returns:
        {"is_valuable": bool, "confidence": float, "reason": str} or None
    """
    model = _get_gemini_model()
    if not model:
        return None
    
    prompt = f"""You are a quality evaluator for AI-generated text corrections.

Analyze if this correction is semantically valuable (not just typo fixes):

**Original text:**
{original[:500]}

**Corrected text:**
{corrected[:500]}

Respond in JSON format only:
{{
  "is_valuable": true/false,
  "confidence": 0.0-1.0,
  "reason": "brief explanation in English"
}}

Criteria for valuable corrections:
- Improves clarity or accuracy
- Fixes factual errors
- Enhances professional tone
- Adds missing important information

NOT valuable:
- Simple typo fixes
- Minor punctuation changes
- Stylistic preferences without substance"""

    try:
        response = await asyncio.to_thread(
            lambda: model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.1,
                    "max_output_tokens": 200,
                    "response_mime_type": "application/json"
                }
            )
        )
        
        result = json.loads(response.text)
        result['reason'] = f"gemini_semantic: {result.get('reason', 'analyzed')}"
        logger.info(f"Gemini semantic analysis: {result}")
        return result
        
    except Exception as e:
        logger.warning(f"Gemini analysis failed: {e}")
        return None

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
        
        # [v2.1] 비동기 배치 처리 (전역 루프 재사용)
        results = safe_run_async(process_correction_batch(records))
        
        # [v2.1] 실패한 레코드 ID 수집 (Partial Batch Failure + Vector DB 실패 포함)
        failed_record_ids = []
        successful_count = 0
        vector_db_retry_count = 0
        
        for i, result in enumerate(results):
            if result.get('success'):
                # [v2.1] 벡터 DB 저장 실패도 재시도 대상
                inner_result = result.get('result', {})
                if inner_result.get('vector_db_failed'):
                    failed_record_ids.append(records[i]['eventID'])
                    vector_db_retry_count += 1
                    logger.warning(f"Vector DB failed for record {records[i]['eventID']}, will retry")
                else:
                    successful_count += 1
            else:
                # 실패한 레코드의 eventID 수집
                failed_record_ids.append(records[i]['eventID'])
                logger.error(f"Failed to process record {records[i]['eventID']}: {result.get('error')}")
        
        # [v2.1] Partial Batch Failure Response (Vector DB 실패 포함)
        if failed_record_ids:
            logger.warning(
                f"Partial batch failure: {len(failed_record_ids)} failed "
                f"({vector_db_retry_count} vector_db), {successful_count} successful"
            )
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
    """
    단일 수정 로그 처리.
    
    [v2.1] 벡터 DB 저장 실패 시 vector_db_failed 플래그 반환.
    """
    
    try:
        # DynamoDB 데이터 파싱
        user_id = correction_data['user_id']['S']
        correction_sk = correction_data['sk']['S']
        
        # 원본/수정본 추출 (Gemini 분석용)
        agent_output = correction_data.get('agent_output', {}).get('S', '')
        user_corrected = correction_data.get('user_corrected', {}).get('S', '')
        
        # 품질 평가 (Gemini 통합 버전)
        quality_result = await evaluate_correction_quality_async(
            correction_data, 
            agent_output, 
            user_corrected
        )
        
        # 품질 평가 결과 업데이트
        await update_correction_quality(user_id, correction_sk, quality_result)
        
        # 가치 있는 수정만 벡터 DB 저장
        vector_db_failed = False
        if quality_result['is_valuable']:
            logger.info(f"Valuable correction detected: {correction_sk}")
            
            # [v2.1] 벡터 DB 저장 실패 시 재시도 유도
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
                vector_db_failed = True  # [v2.1] 실패 플래그 설정
        
        quality_result['vector_db_failed'] = vector_db_failed
        return quality_result
        
    except Exception as e:
        logger.error(f"Error processing correction: {str(e)}")
        raise

# [v2.1] 짧은 텍스트 임계값
SHORT_TEXT_THRESHOLD = 10  # 10자 미만은 짧은 텍스트
AMBIGUOUS_SCORE_MIN = 0.4  # 애매한 점수 하한
AMBIGUOUS_SCORE_MAX = 0.7  # 애매한 점수 상한


async def evaluate_correction_quality_async(
    correction_data: Dict,
    original_text: str,
    corrected_text: str
) -> Dict[str, Any]:
    """
    [v2.1] 비동기 품질 평가 (Gemini 세만틱 분석 통합).
    
    개선사항:
    1. 수정 비율(Correction Ratio) 기반 평가
    2. 짧은 텍스트 페널티 (10자 미만)
    3. 애매한 점수(0.4~0.7)일 때 Gemini 1.5 Flash 세만틱 분석
    """
    
    try:
        # DynamoDB 데이터 추출
        edit_distance = int(correction_data.get('edit_distance', {}).get('N', '0'))
        correction_time = int(correction_data.get('correction_time_seconds', {}).get('N', '0'))
        user_confirmed = correction_data.get('user_confirmed_valuable', {}).get('BOOL')
        
        # 원본 텍스트 길이
        original_length = len(original_text)
        
        # 수정 비율 계산
        if original_length == 0:
            correction_ratio = 0.0
        else:
            correction_ratio = edit_distance / original_length
        
        logger.info(
            f"Edit distance: {edit_distance}, Original length: {original_length}, "
            f"Correction ratio: {correction_ratio:.3f}"
        )
        
        # 1. 기본 필터링 (단순 오타 수정)
        if edit_distance < 3:
            return {
                "is_valuable": False, 
                "confidence": 0.9,
                "reason": "minor_edit",
                "correction_ratio": correction_ratio
            }
        
        # 2. 수정 비율 기반 평가
        ratio_weight = min(correction_ratio * 2.0, 1.0)
        
        # [v2.1] 짧은 텍스트 페널티
        short_text_penalty = 1.0
        if original_length < SHORT_TEXT_THRESHOLD:
            # 짧은 텍스트는 비율 가중치 50% 감소
            short_text_penalty = 0.5
            ratio_weight *= short_text_penalty
            logger.info(f"Short text penalty applied: original_length={original_length}")
        
        # 3. 시간 기반 평가
        time_weight = min(correction_time / 30.0, 1.0)
        
        # 4. 사용자 명시적 확인 (최우선)
        if user_confirmed is not None:
            return {
                "is_valuable": user_confirmed,
                "confidence": 0.95,
                "reason": "user_confirmed",
                "correction_ratio": correction_ratio
            }
        
        # 5. 기본 점수 계산
        base_score = 0.1 + (ratio_weight * 0.5) + (time_weight * 0.3)
        
        # 메타데이터 가중치
        extracted_metadata = correction_data.get('extracted_metadata', {}).get('M', {})
        if extracted_metadata and 'no_significant_change' not in extracted_metadata:
            base_score += 0.2
        
        # 높은 비율 페널티 (90% 이상)
        reason_suffix = ""
        if correction_ratio > 0.9:
            base_score *= 0.7
            reason_suffix = "_high_ratio_penalty"
        
        # 짧은 텍스트 접미사
        if short_text_penalty < 1.0:
            reason_suffix += "_short_text_penalty"
        
        # [v2.1] 애매한 점수일 때 Gemini 세만틱 분석
        if AMBIGUOUS_SCORE_MIN <= base_score <= AMBIGUOUS_SCORE_MAX:
            logger.info(f"Ambiguous score {base_score:.2f}, invoking Gemini semantic analysis")
            
            gemini_result = await _analyze_with_gemini(original_text, corrected_text)
            
            if gemini_result:
                # Gemini 결과 우선 사용
                gemini_result['correction_ratio'] = correction_ratio
                gemini_result['heuristic_score'] = base_score  # 참고용
                return gemini_result
            else:
                # Gemini 실패 시 기본 로직 사용
                reason_suffix += "_gemini_fallback"
        
        return {
            "is_valuable": base_score > 0.6,
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


def evaluate_correction_quality_sync(correction_data: Dict) -> Dict[str, Any]:
    """
    [Legacy] 동기식 품질 평가 (하위 호환성).
    새 코드는 evaluate_correction_quality_async 사용.
    """
    agent_output = correction_data.get('agent_output', {}).get('S', '')
    user_corrected = correction_data.get('user_corrected', {}).get('S', '')
    return safe_run_async(
        evaluate_correction_quality_async(correction_data, agent_output, user_corrected)
    )

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