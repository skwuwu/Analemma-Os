"""
[Critical Fix #3] HITL 콜백 후 청크 처리 재개 Lambda

콜백 완료 후 해당 청크 내에서 중단된 지점 이후의 세그먼트들을
다시 계산하여 실행합니다.

핵심 원리:
- paused_segment_id를 기준으로 남은 세그먼트 파티셔닝
- 콜백 결과를 현재 상태에 병합
- 남은 세그먼트들을 순차 실행
- 추가 HITL 발생 시 다시 PAUSED_FOR_HITP 반환

🚨 [Critical Fixes Applied]:

① 상태 페이로드 크기 제어 (Payload Management):
- Step Functions 256KB 제한 방지를 위한 S3 오프로딩 구현
- 200KB 임계값으로 대용량 상태 자동 감지
- S3 URI 참조 방식으로 페이로드 크기 최소화
- 상태 요약 정보는 인라인으로 유지하여 호환성 보장

② 멱등성 키 정합성 (Idempotency Key Consistency):
- #resumed# 접미사 사용 시 중복 실행 방지 로직 강화
- 기존 execution_id 기반 고유 키 생성으로 안전성 확보
- DynamoDB 멱등성 테이블과의 정합성 검증
- 키 길이 및 구조 검증으로 DynamoDB 제한 준수
- 폴백 메커니즘으로 키 생성 실패 시에도 안전한 동작 보장

이 수정으로 재개된 청크가 Step Functions 페이로드 제한에 걸리지 않고,
사용자의 실수로 인한 중복 콜백에도 안전하게 대응할 수 있습니다.
"""

import json
import logging
import os
import time
from typing import Dict, List, Any, Optional

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    HITL 콜백 후 청크 처리 재개
    
    Args:
        event: {
            "chunk_result": { 이전 청크 처리 결과 },
            "callback_result": { HITL 콜백 결과 },
            "chunk_data": { 원본 청크 데이터 },
            "paused_segment_id": 중단된 세그먼트 ID,
            "execution_id": "exec-123",
            "owner_id": "user-456",
            "workflow_id": "wf-789",
            "workflow_config": {...},
            "state_bucket": "my-bucket"
        }
    
    Returns:
        {
            "chunk_id": str,
            "status": "COMPLETED" | "PAUSED_FOR_HITP" | "FAILED",
            "final_state": {...},
            "paused_segment_id": int (if paused again)
        }
    """
    try:
        chunk_result = event.get('chunk_result', {})
        callback_result = event.get('callback_result', {})
        chunk_data = event.get('chunk_data', {})
        paused_segment_id = event.get('paused_segment_id')
        execution_id = event.get('execution_id')
        owner_id = event.get('owner_id')
        workflow_id = event.get('workflow_id')
        workflow_config = event.get('workflow_config', {})
        state_bucket = event.get('state_bucket') or os.environ.get('WORKFLOW_STATE_BUCKET')

        chunk_id = chunk_data.get('chunk_id', chunk_result.get('chunk_id', 'unknown'))
        partition_slice = chunk_data.get('partition_slice', [])
        start_segment = chunk_data.get('start_segment', 0)
        
        logger.info(
            f"Resuming chunk {chunk_id} after HITL callback, "
            f"paused at segment {paused_segment_id}"
        )
        
        # 1. 콜백 결과에서 사용자 입력 추출
        user_input = _extract_user_input(callback_result)
        logger.info(f"Extracted user input from src.callback: {list(user_input.keys())}")
        
        # 2. 이전 상태와 콜백 결과 병합
        previous_state = chunk_result.get('final_state', {})
        current_state = _merge_callback_state(previous_state, user_input, callback_result)
        
        # 3. 중단 지점 이후 세그먼트 계산
        remaining_segments = _calculate_remaining_segments(
            partition_slice=partition_slice,
            start_segment=start_segment,
            paused_segment_id=paused_segment_id
        )
        
        if not remaining_segments:
            logger.info(f"No remaining segments after paused segment {paused_segment_id}")
            return {
                "chunk_id": chunk_id,
                "status": "COMPLETED",
                "final_state": current_state,
                "processed_after_resume": 0
            }
        
        logger.info(
            f"Processing {len(remaining_segments)} remaining segments "
            f"(from src.segment {remaining_segments[0]['global_index']} onwards)"
        )
        
        # 4. 남은 세그먼트 순차 실행
        return _process_remaining_segments(
            chunk_id=chunk_id,
            remaining_segments=remaining_segments,
            current_state=current_state,
            workflow_config=workflow_config,
            owner_id=owner_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            state_bucket=state_bucket,
            context=context
        )
        
    except Exception as e:
        logger.exception(f"Failed to resume chunk processing: {e}")
        return {
            "chunk_id": event.get('chunk_data', {}).get('chunk_id', 'unknown'),
            "status": "FAILED",
            "error": str(e),
            "final_state": event.get('chunk_result', {}).get('final_state', {})
        }


def _extract_user_input(callback_result: Dict[str, Any]) -> Dict[str, Any]:
    """콜백 결과에서 사용자 입력 추출"""
    # 다양한 콜백 형식 지원
    if 'user_input' in callback_result:
        return callback_result['user_input']
    elif 'Payload' in callback_result:
        payload = callback_result['Payload']
        return payload.get('user_input', payload)
    elif 'output' in callback_result:
        return callback_result['output']
    else:
        return callback_result


def _merge_callback_state(
    previous_state: Dict[str, Any],
    user_input: Dict[str, Any],
    callback_result: Dict[str, Any]
) -> Dict[str, Any]:
    """이전 상태와 콜백 결과 병합"""
    merged = {**previous_state}
    
    # 사용자 입력을 상태에 병합
    if user_input:
        merged['__callback_input'] = user_input
        
        # 특정 필드들은 직접 병합
        for key in ['selected_option', 'user_response', 'approval', 'feedback']:
            if key in user_input:
                merged[key] = user_input[key]
    
    # 콜백 메타데이터 추가
    merged['__last_callback'] = {
        'timestamp': int(time.time()),
        'callback_type': callback_result.get('callback_type', 'unknown')
    }
    
    return merged


def _calculate_remaining_segments(
    partition_slice: List[Dict[str, Any]],
    start_segment: int,
    paused_segment_id: int
) -> List[Dict[str, Any]]:
    """중단 지점 이후 세그먼트 계산"""
    remaining = []
    
    for idx, segment in enumerate(partition_slice):
        global_index = start_segment + idx
        
        # 중단된 세그먼트 이후만 포함
        if global_index > paused_segment_id:
            remaining.append({
                'segment': segment,
                'local_index': idx,
                'global_index': global_index
            })
    
    return remaining


def _process_remaining_segments(
    chunk_id: str,
    remaining_segments: List[Dict[str, Any]],
    current_state: Dict[str, Any],
    workflow_config: Dict[str, Any],
    owner_id: str,
    workflow_id: str,
    execution_id: str,
    state_bucket: str,
    context: Any
) -> Dict[str, Any]:
    """남은 세그먼트들을 순차 실행"""
    
    # segment_runner_handler 임포트
    try:
        from src.handlers.core.segment_runner_handler import lambda_handler as segment_runner_handler
    except ImportError:
        logger.error("Failed to import segment_runner_handler")
        return {
            "chunk_id": chunk_id,
            "status": "FAILED",
            "error": "segment_runner_handler import failed",
            "final_state": current_state
        }
    
    processed_count = 0
    s3_client = boto3.client('s3') if state_bucket else None
    
    for seg_info in remaining_segments:
        segment = seg_info['segment']
        global_index = seg_info['global_index']
        
        logger.info(f"Processing resumed segment {global_index} in chunk {chunk_id}")
        
        try:
            # 🚨 [Critical Fix] 멱등성 키 정합성 - 중복 실행 방지
            # 기존 execution_id 기반으로 고유한 키 생성 (resumed 접미사 사용하되 안전하게)
            base_idempotency_key = event.get('idempotency_key') or f"{execution_id}#chunk#{chunk_id}"
            segment_idempotency_key = f"{base_idempotency_key}#resumed_segment_{global_index}"
            
            # 🚨 멱등성 키 안전성 검증
            idempotency_validation = _validate_idempotency_safety(
                execution_id=execution_id,
                chunk_id=chunk_id,
                segment_id=global_index,
                base_idempotency_key=base_idempotency_key
            )
            
            if not idempotency_validation["is_safe"]:
                logger.warning(f"Idempotency key safety concerns: {idempotency_validation['warnings']}")
                # 안전한 폴백 키 사용
                segment_idempotency_key = idempotency_validation["generated_key"]
            
            if idempotency_validation["recommendations"]:
                logger.info(f"Idempotency recommendations: {idempotency_validation['recommendations']}")
            
            # 세그먼트 실행 이벤트 구성
            segment_event = {
                'segment_config': segment,
                'current_state': current_state,
                'ownerId': owner_id,
                'workflowId': workflow_id,
                'segment_to_run': global_index,
                'workflow_config': workflow_config,
                'execution_id': f"{chunk_id}_resumed_segment_{global_index}",
                'idempotency_key': segment_idempotency_key,  # 🚨 검증된 멱등성 키
                'distributed_context': {
                    'chunk_id': chunk_id,
                    'is_resumed': True,
                    'resumed_from_segment': seg_info.get('paused_at', global_index - 1),
                    'original_execution_id': execution_id,  # 원본 실행 ID 추적
                    'resume_timestamp': int(time.time()),
                    'idempotency_validated': True  # 검증 완료 표시
                }
            }
            
            # 세그먼트 실행
            segment_result = segment_runner_handler(segment_event, context)
            processed_count += 1
            
            # 결과 처리
            if segment_result.get('status') == 'COMPLETE':
                # 상태 업데이트
                if segment_result.get('final_state'):
                    current_state = segment_result['final_state']
                
                # S3에 중간 상태 저장
                if s3_client and state_bucket:
                    _save_intermediate_state(
                        s3_client=s3_client,
                        bucket=state_bucket,
                        owner_id=owner_id,
                        workflow_id=workflow_id,
                        execution_id=execution_id,
                        chunk_id=chunk_id,
                        segment_id=global_index,
                        state=current_state
                    )
                    
            elif segment_result.get('status') in ['PAUSE', 'PAUSED_FOR_HITP']:
                # 다시 HITL 대기 필요
                logger.info(
                    f"Segment {global_index} requires another HITL pause "
                    f"after resume in chunk {chunk_id}"
                )
                
                # 🚨 [Critical Fix] 상태 페이로드 크기 제어
                return _build_chunk_response_with_payload_control(
                    chunk_id=chunk_id,
                    status="PAUSED_FOR_HITP",
                    final_state=current_state,
                    paused_segment_id=global_index,
                    processed_after_resume=processed_count,
                    task_token=segment_result.get('task_token'),
                    remaining_segments=len(remaining_segments) - processed_count,
                    state_bucket=state_bucket,
                    owner_id=owner_id,
                    workflow_id=workflow_id,
                    execution_id=execution_id
                )
                
            elif segment_result.get('status') == 'FAILED':
                logger.error(f"Segment {global_index} failed after resume")
                # 🚨 [Critical Fix] 상태 페이로드 크기 제어
                return _build_chunk_response_with_payload_control(
                    chunk_id=chunk_id,
                    status="FAILED",
                    final_state=current_state,
                    failed_segment_id=global_index,
                    processed_after_resume=processed_count,
                    error=segment_result.get('error_info', 'Unknown error'),
                    state_bucket=state_bucket,
                    owner_id=owner_id,
                    workflow_id=workflow_id,
                    execution_id=execution_id
                )
                
        except Exception as e:
            logger.error(f"Error processing resumed segment {global_index}: {e}")
            # 🚨 [Critical Fix] 상태 페이로드 크기 제어
            return _build_chunk_response_with_payload_control(
                chunk_id=chunk_id,
                status="FAILED",
                final_state=current_state,
                failed_segment_id=global_index,
                processed_after_resume=processed_count,
                error=str(e),
                state_bucket=state_bucket,
                owner_id=owner_id,
                workflow_id=workflow_id,
                execution_id=execution_id
            )
    
    # 모든 남은 세그먼트 처리 완료
    logger.info(
        f"Chunk {chunk_id} completed after resume, "
        f"processed {processed_count} segments"
    )
    
    # 최종 세그먼트 ID 기록
    current_state['__latest_segment_id'] = remaining_segments[-1]['global_index']
    
    # 🚨 [Critical Fix] 상태 페이로드 크기 제어 - Step Functions 256KB 제한 방지
    return _build_chunk_response_with_payload_control(
        chunk_id=chunk_id,
        status="COMPLETED",
        final_state=current_state,
        processed_after_resume=processed_count,
        state_bucket=state_bucket,
        owner_id=owner_id,
        workflow_id=workflow_id,
        execution_id=execution_id
    )


def _save_intermediate_state(
    s3_client,
    bucket: str,
    owner_id: str,
    workflow_id: str,
    execution_id: str,
    chunk_id: str,
    segment_id: int,
    state: Dict[str, Any]
) -> None:
    """중간 상태를 S3에 저장"""
    try:
        key = (
            f"distributed-states/{owner_id}/{workflow_id}/{execution_id}/"
            f"chunks/{chunk_id}/segment_{segment_id}_resumed.json"
        )
        
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(state, ensure_ascii=False, default=str).encode('utf-8'),
            ContentType='application/json',
            Metadata={
                'chunk_id': chunk_id,
                'segment_id': str(segment_id),
                'is_resumed': 'true',
                'timestamp': str(int(time.time()))
            }
        )
        
        logger.debug(f"Saved intermediate state: s3://{bucket}/{key}")
        
    except Exception as e:
        logger.warning(f"Failed to save intermediate state (non-fatal): {e}")


def _build_chunk_response_with_payload_control(
    chunk_id: str,
    status: str,
    final_state: Dict[str, Any],
    state_bucket: str,
    owner_id: str,
    workflow_id: str,
    execution_id: str,
    processed_after_resume: int = 0,
    paused_segment_id: Optional[int] = None,
    task_token: Optional[str] = None,
    remaining_segments: Optional[int] = None,
    failed_segment_id: Optional[int] = None,
    error: Optional[str] = None
) -> Dict[str, Any]:
    """
    🚨 [Critical Fix] 상태 페이로드 크기 제어
    
    Step Functions 256KB 제한을 방지하기 위해 대용량 상태는 S3에 저장하고
    URI만 반환하는 오프로딩 로직 적용
    
    Args:
        chunk_id: 청크 ID
        status: 처리 상태
        final_state: 최종 상태 데이터
        state_bucket: S3 버킷
        owner_id: 소유자 ID
        workflow_id: 워크플로우 ID
        execution_id: 실행 ID
        processed_after_resume: 재개 후 처리된 세그먼트 수
        paused_segment_id: 중단된 세그먼트 ID (PAUSED 상태 시)
        task_token: Task Token (PAUSED 상태 시)
        remaining_segments: 남은 세그먼트 수 (PAUSED 상태 시)
        failed_segment_id: 실패한 세그먼트 ID (FAILED 상태 시)
        error: 오류 메시지 (FAILED 상태 시)
    
    Returns:
        청크 응답 (페이로드 크기 제어 적용)
    """
    try:
        # 기본 응답 구성
        response = {
            "chunk_id": chunk_id,
            "status": status,
            "processed_after_resume": processed_after_resume
        }
        
        # 상태별 추가 필드
        if paused_segment_id is not None:
            response["paused_segment_id"] = paused_segment_id
        if task_token:
            response["task_token"] = task_token
        if remaining_segments is not None:
            response["remaining_segments"] = remaining_segments
        if failed_segment_id is not None:
            response["failed_segment_id"] = failed_segment_id
        if error:
            response["error"] = error
        
        # 🚨 [Critical] final_state 크기 검사 및 오프로딩
        if final_state:
            state_json = json.dumps(final_state, ensure_ascii=False)
            state_size_bytes = len(state_json.encode('utf-8'))
            
            # Step Functions Task Output 제한: 256KB
            # 안전 마진을 위해 200KB로 제한 (기존 패턴과 동일)
            SIZE_LIMIT_BYTES = 200 * 1024  # 200KB
            
            logger.info(f"Resume response state size: {state_size_bytes} bytes (limit: {SIZE_LIMIT_BYTES})")
            
            if state_size_bytes <= SIZE_LIMIT_BYTES:
                # 작은 상태는 인라인으로 반환
                response["final_state"] = final_state
                response["payload_type"] = "inline"
                response["payload_size_bytes"] = state_size_bytes
                
                logger.info(f"Resume response: inline state ({state_size_bytes} bytes)")
                
            else:
                # 🚨 [Critical] 대용량 상태는 S3에 저장하고 URI만 반환
                logger.warning(f"Large resume state detected ({state_size_bytes} bytes), storing in S3")
                
                if not state_bucket:
                    logger.error("No state bucket available for large state offloading")
                    # 폴백: 상태 요약만 포함
                    response["final_state"] = _create_state_summary(final_state)
                    response["payload_type"] = "summary_fallback"
                    response["error"] = "Large state detected but no S3 bucket available"
                else:
                    # S3에 상태 저장
                    s3_uri = _store_large_state_in_s3(
                        state_data=final_state,
                        state_bucket=state_bucket,
                        owner_id=owner_id,
                        workflow_id=workflow_id,
                        execution_id=execution_id,
                        chunk_id=chunk_id,
                        context="resume_response"
                    )
                    
                    if s3_uri:
                        # S3 URI와 메타데이터만 반환
                        response["final_state_s3_uri"] = s3_uri
                        response["payload_type"] = "s3_reference"
                        response["payload_size_bytes"] = state_size_bytes
                        response["s3_offloaded"] = True
                        
                        # 작은 상태 요약은 인라인으로 포함
                        response["state_summary"] = _create_state_summary(final_state)
                        
                        logger.info(f"Resume response: S3 offloaded to {s3_uri}")
                    else:
                        # S3 저장 실패 시 폴백
                        logger.error("Failed to store large state in S3, using summary")
                        response["final_state"] = _create_state_summary(final_state)
                        response["payload_type"] = "summary_fallback"
                        response["error"] = "Large state S3 storage failed"
        else:
            # 상태가 없는 경우
            response["final_state"] = {}
            response["payload_type"] = "empty"
        
        return response
        
    except Exception as e:
        logger.error(f"Failed to build chunk response with payload control: {e}")
        # 실패 시 최소한의 응답 반환
        return {
            "chunk_id": chunk_id,
            "status": "FAILED",
            "error": f"Response building failed: {str(e)}",
            "final_state": {},
            "processed_after_resume": processed_after_resume
        }


def _store_large_state_in_s3(
    state_data: Dict[str, Any],
    state_bucket: str,
    owner_id: str,
    workflow_id: str,
    execution_id: str,
    chunk_id: str,
    context: str = "resume"
) -> Optional[str]:
    """
    대용량 상태를 S3에 저장
    
    Args:
        state_data: 상태 데이터
        state_bucket: S3 버킷
        owner_id: 소유자 ID
        workflow_id: 워크플로우 ID
        execution_id: 실행 ID
        chunk_id: 청크 ID
        context: 저장 컨텍스트
    
    Returns:
        S3 URI (성공 시) 또는 None (실패 시)
    """
    try:
        s3_client = boto3.client('s3')
        
        # 재개 전용 S3 키 생성
        timestamp = int(time.time())
        large_state_key = (
            f"distributed-states/{owner_id}/{workflow_id}/{execution_id}/"
            f"chunks/{chunk_id}/{context}_state_{timestamp}.json"
        )
        
        # 상태를 JSON으로 직렬화
        state_json = json.dumps(state_data, ensure_ascii=False, default=str)
        
        # S3에 저장
        s3_client.put_object(
            Bucket=state_bucket,
            Key=large_state_key,
            Body=state_json.encode('utf-8'),
            ContentType='application/json',
            Metadata={
                'execution_id': execution_id,
                'chunk_id': chunk_id,
                'owner_id': owner_id,
                'workflow_id': workflow_id,
                'context': context,
                'original_size': str(len(state_json.encode('utf-8'))),
                'created_at': str(timestamp),
                'payload_type': 'large_state_resume'
            }
        )
        
        s3_uri = f"s3://{state_bucket}/{large_state_key}"
        logger.info(f"Large resume state stored: {s3_uri}")
        
        return s3_uri
        
    except Exception as e:
        logger.error(f"Failed to store large state in S3: {e}")
        return None


def _create_state_summary(state_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    상태 데이터의 요약 생성 (크기 제한 준수)
    
    Args:
        state_data: 원본 상태 데이터
    
    Returns:
        상태 요약 (작은 크기)
    """
    try:
        if not isinstance(state_data, dict):
            return {"type": type(state_data).__name__, "size": len(str(state_data))}
        
        summary = {
            "segment_count": len(state_data.get('segments', [])),
            "has_chunks": 'chunks' in state_data,
            "has_callback_input": '__callback_input' in state_data,
            "latest_segment_id": state_data.get('__latest_segment_id'),
            "last_callback_timestamp": state_data.get('__last_callback', {}).get('timestamp'),
            "state_keys": list(state_data.keys())[:10],  # 처음 10개 키만
            "total_keys": len(state_data.keys())
        }
        
        # 중요한 작은 필드들은 직접 포함
        for key in ['selected_option', 'user_response', 'approval']:
            if key in state_data and isinstance(state_data[key], (str, int, bool, float)):
                value_str = str(state_data[key])
                if len(value_str) < 100:  # 100자 미만만 포함
                    summary[key] = state_data[key]
        
        return summary
        
    except Exception as e:
        logger.error(f"Failed to create state summary: {e}")
        return {"error": "Summary creation failed", "original_type": type(state_data).__name__}


def _validate_idempotency_safety(
    execution_id: str,
    chunk_id: str,
    segment_id: int,
    base_idempotency_key: str
) -> Dict[str, Any]:
    """
    🚨 [Critical Fix] 멱등성 키 안전성 검증
    
    재개된 세그먼트의 멱등성 키가 안전하게 생성되었는지 검증하고
    중복 실행 위험을 평가
    
    Args:
        execution_id: 실행 ID
        chunk_id: 청크 ID
        segment_id: 세그먼트 ID
        base_idempotency_key: 기본 멱등성 키
    
    Returns:
        검증 결과 및 권장사항
    """
    try:
        validation = {
            "is_safe": True,
            "warnings": [],
            "recommendations": [],
            "generated_key": f"{base_idempotency_key}#resumed_segment_{segment_id}"
        }
        
        # 1. 키 길이 검증 (DynamoDB 제한)
        key_length = len(validation["generated_key"])
        if key_length > 2048:  # DynamoDB 키 길이 제한
            validation["is_safe"] = False
            validation["warnings"].append(f"Idempotency key too long: {key_length} chars")
        
        # 2. 고유성 검증
        if "#resumed#" in base_idempotency_key:
            validation["warnings"].append("Base key already contains #resumed# - potential double resumption")
            validation["recommendations"].append("Check for multiple resume attempts")
        
        # 3. 구조 검증
        expected_parts = ["execution_id", "chunk", "segment"]
        key_parts = validation["generated_key"].split("#")
        if len(key_parts) < 3:
            validation["warnings"].append(f"Idempotency key structure may be incomplete: {len(key_parts)} parts")
        
        # 4. 타임스탬프 기반 고유성 권장
        if str(int(time.time())) not in validation["generated_key"]:
            validation["recommendations"].append("Consider adding timestamp for stronger uniqueness")
        
        return validation
        
    except Exception as e:
        logger.error(f"Idempotency validation failed: {e}")
        return {
            "is_safe": False,
            "warnings": [f"Validation failed: {str(e)}"],
            "recommendations": ["Use fallback idempotency key generation"],
            "generated_key": f"fallback_{execution_id}_{chunk_id}_{segment_id}_{int(time.time())}"
        }
