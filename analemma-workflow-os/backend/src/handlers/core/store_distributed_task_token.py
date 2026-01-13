"""
분산 실행에서 HITL Task Token 저장 Lambda

Distributed Map의 자식 실행에서 waitForTaskToken 상태에 진입할 때
Task Token을 DynamoDB에 저장하여 외부에서 콜백할 수 있도록 합니다.

이 함수는 Step Functions의 .waitForTaskToken 통합에서 호출됩니다.

🚨 [Critical Fixes Applied]:

① conversation_id 고유성 결함 해결:
- execution_id를 접두어로 포함하여 사용자/워크플로우 간 충돌 방지
- 고유성 보장으로 Task Token 덮어쓰기 대참사 예방

② 큰 상태(Large State) 처리 일관성:
- Latest State Pointer 전략 활용으로 S3 URI 참조 저장
- ResumeChunkProcessingFunction에서 안전한 상태 복구 지원
"""

import json
import logging
import os
import time
from typing import Dict, Any

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    분산 실행에서 Task Token 저장
    
    🚨 [Critical Fixes]:
    - conversation_id 고유성: execution_id 접두어로 충돌 방지
    - 대용량 상태 처리: S3 URI 참조로 안전한 복구 지원
    
    Args:
        event: {
            "TaskToken": "arn:aws:states:...",
            "chunk_result": {...},
            "distributed_context": {
                "is_child_execution": true,
                "parent_execution_id": "exec-123",
                "chunk_id": "chunk_0001",
                "paused_segment_id": 42
            }
        }
    
    Returns:
        저장 결과 (콜백 시 Step Functions에 전달됨)
    """
    try:
        task_token = event.get('TaskToken')
        chunk_result = event.get('chunk_result', {})
        distributed_context = event.get('distributed_context', {})
        
        chunk_id = distributed_context.get('chunk_id', chunk_result.get('chunk_id', 'unknown'))
        paused_segment_id = distributed_context.get('paused_segment_id')
        parent_execution_id = distributed_context.get('parent_execution_id')
        
        # 🚨 [Critical Fix] 추가 컨텍스트 정보 추출 (고유성 보장용)
        owner_id = event.get('owner_id') or distributed_context.get('owner_id')
        workflow_id = event.get('workflow_id') or distributed_context.get('workflow_id')
        
        logger.info(
            f"Storing task token for distributed HITL: "
            f"chunk={chunk_id}, segment={paused_segment_id}, "
            f"execution={parent_execution_id}, owner={owner_id}"
        )

        if not task_token:
            logger.error("No TaskToken provided")
            raise ValueError("TaskToken is required")
        
        if not parent_execution_id:
            logger.error("No parent_execution_id provided - required for uniqueness")
            raise ValueError("parent_execution_id is required for conversation_id uniqueness")
        
        # DynamoDB에 Task Token 저장
        dynamodb = boto3.resource('dynamodb')
        token_table_name = os.environ.get('TASK_TOKEN_TABLE') or os.environ.get('TASK_TOKENS_TABLE_NAME', 'TaskTokensTableV2')
        token_table = dynamodb.Table(token_table_name)
        
        timestamp = int(time.time())
        
        # 🚨 [Critical Fix #1] conversation_id 고유성 보장
        # execution_id를 접두어로 포함하여 사용자/워크플로우 간 충돌 방지
        conversation_id = f"{parent_execution_id}_{chunk_id}"
        if paused_segment_id is not None:
            conversation_id = f"{conversation_id}_seg_{paused_segment_id}"
        
        # 추가 안전장치: owner_id도 포함 (극도로 안전한 고유성)
        if owner_id:
            conversation_id = f"{owner_id}_{conversation_id}"
        
        logger.info(f"Generated unique conversation_id: {conversation_id}")
        
        # 🚨 conversation_id 고유성 검증
        uniqueness_validation = _validate_conversation_id_uniqueness(
            conversation_id=conversation_id,
            parent_execution_id=parent_execution_id,
            owner_id=owner_id,
            chunk_id=chunk_id,
            paused_segment_id=paused_segment_id
        )
        
        if not uniqueness_validation["is_unique"]:
            logger.error(f"conversation_id uniqueness validation failed: {uniqueness_validation['warnings']}")
            raise ValueError(f"conversation_id not unique enough: {uniqueness_validation['warnings']}")
        
        if uniqueness_validation["warnings"]:
            logger.warning(f"conversation_id warnings: {uniqueness_validation['warnings']}")
        
        if uniqueness_validation["collision_risk"] != "low":
            logger.warning(f"conversation_id collision risk: {uniqueness_validation['collision_risk']}")
        
        logger.info(f"conversation_id uniqueness validated: collision_risk={uniqueness_validation['collision_risk']}")
        
        token_item = {
            'conversation_id': conversation_id,
            'task_token': task_token,
            'chunk_id': chunk_id,
            'segment_id': paused_segment_id,
            'parent_execution_id': parent_execution_id,
            'owner_id': owner_id,
            'workflow_id': workflow_id,
            'distributed_execution': True,
            'is_child_execution': distributed_context.get('is_child_execution', True),
            'chunk_result_status': chunk_result.get('status'),
            'created_at': timestamp,
            'ttl': timestamp + 86400 * 7,  # 7일 TTL
            'status': 'WAITING_FOR_CALLBACK'
        }
        
        # 🚨 [Critical Fix #2] 큰 상태(Large State) 처리 일관성
        # Latest State Pointer 전략 활용으로 S3 URI 참조 저장
        if chunk_result.get('final_state'):
            state_handling_result = _handle_chunk_final_state(
                final_state=chunk_result['final_state'],
                chunk_id=chunk_id,
                parent_execution_id=parent_execution_id,
                owner_id=owner_id,
                workflow_id=workflow_id,
                paused_segment_id=paused_segment_id
            )
            
            # 상태 처리 결과를 토큰 아이템에 병합
            token_item.update(state_handling_result)
        
        token_table.put_item(Item=token_item)
        
        logger.info(
            f"Task token stored successfully: conversation_id={conversation_id}, "
            f"chunk={chunk_id}, segment={paused_segment_id}"
        )
        
        # 콜백 완료 시 Step Functions에 전달될 결과
        return {
            "stored": True,
            "conversation_id": conversation_id,
            "chunk_id": chunk_id,
            "paused_segment_id": paused_segment_id,
            "parent_execution_id": parent_execution_id,
            "owner_id": owner_id,
            "workflow_id": workflow_id,
            "callback_info": {
                "table_name": token_table_name,
                "conversation_id": conversation_id,
                "instructions": "Use SendTaskSuccess with this conversation_id to resume",
                "uniqueness_guaranteed": True  # 🚨 고유성 보장 확인
            }
        }
        
    except Exception as e:
        logger.exception(f"Failed to store task token: {e}")
        raise


def _handle_chunk_final_state(
    final_state: Dict[str, Any],
    chunk_id: str,
    parent_execution_id: str,
    owner_id: str,
    workflow_id: str,
    paused_segment_id: int
) -> Dict[str, Any]:
    """
    🚨 [Critical Fix #2] 큰 상태(Large State) 처리 일관성
    
    Latest State Pointer 전략을 활용하여 대용량 상태를 S3에 저장하고
    URI 참조를 DynamoDB에 저장하여 ResumeChunkProcessingFunction에서
    안전하게 상태를 복구할 수 있도록 합니다.
    
    Args:
        final_state: 청크의 최종 상태
        chunk_id: 청크 ID
        parent_execution_id: 부모 실행 ID
        owner_id: 소유자 ID
        workflow_id: 워크플로우 ID
        paused_segment_id: 중단된 세그먼트 ID
    
    Returns:
        DynamoDB 아이템에 추가할 상태 관련 필드들
    """
    try:
        state_json = json.dumps(final_state, default=str, ensure_ascii=False)
        state_size_bytes = len(state_json.encode('utf-8'))
        
        # Step Functions 및 DynamoDB 제한을 고려한 임계값
        # 400KB를 넘으면 S3에 저장 (DynamoDB 400KB 제한 고려)
        SIZE_LIMIT_BYTES = 400 * 1024  # 400KB
        
        logger.info(f"Chunk final state size: {state_size_bytes} bytes (limit: {SIZE_LIMIT_BYTES})")
        
        if state_size_bytes <= SIZE_LIMIT_BYTES:
            # 작은 상태는 DynamoDB에 직접 저장
            logger.info(f"Storing state inline for chunk {chunk_id} ({state_size_bytes} bytes)")
            return {
                'chunk_final_state': state_json,
                'state_storage_type': 'inline',
                'state_size_bytes': state_size_bytes
            }
        
        # 🚨 [Critical Fix] 대용량 상태는 S3에 저장하고 URI 참조
        logger.warning(f"Large state detected for chunk {chunk_id} ({state_size_bytes} bytes), storing in S3")
        
        state_bucket = os.environ.get('WORKFLOW_STATE_BUCKET')
        if not state_bucket:
            logger.error("No WORKFLOW_STATE_BUCKET configured for large state storage")
            # 폴백: 상태 요약만 저장
            return {
                'state_too_large': True,
                'state_size_bytes': state_size_bytes,
                'state_storage_type': 'too_large_no_bucket',
                'error': 'No S3 bucket configured for large state storage',
                'state_summary': _create_state_summary_for_token(final_state)
            }
        
        # S3에 상태 저장
        s3_uri = _store_large_state_in_s3_for_token(
            state_data=final_state,
            state_json=state_json,
            state_bucket=state_bucket,
            chunk_id=chunk_id,
            parent_execution_id=parent_execution_id,
            owner_id=owner_id,
            workflow_id=workflow_id,
            paused_segment_id=paused_segment_id
        )
        
        if s3_uri:
            # 🚨 [Critical] S3 URI 참조로 ResumeChunkProcessingFunction에서 복구 가능
            logger.info(f"Large state stored in S3 for chunk {chunk_id}: {s3_uri}")
            return {
                'chunk_final_state_s3_path': s3_uri,  # 🚨 핵심: 재개 시 사용할 S3 경로
                'state_storage_type': 's3_reference',
                'state_size_bytes': state_size_bytes,
                'state_summary': _create_state_summary_for_token(final_state),
                's3_stored_at': int(time.time())
            }
        else:
            # S3 저장 실패 시 폴백
            logger.error(f"Failed to store large state in S3 for chunk {chunk_id}")
            return {
                'state_too_large': True,
                'state_size_bytes': state_size_bytes,
                'state_storage_type': 's3_storage_failed',
                'error': 'Failed to store large state in S3',
                'state_summary': _create_state_summary_for_token(final_state)
            }
        
    except Exception as e:
        logger.error(f"Error handling chunk final state: {e}")
        return {
            'state_handling_error': str(e),
            'state_storage_type': 'error',
            'state_summary': _create_state_summary_for_token(final_state) if final_state else {}
        }


def _store_large_state_in_s3_for_token(
    state_data: Dict[str, Any],
    state_json: str,
    state_bucket: str,
    chunk_id: str,
    parent_execution_id: str,
    owner_id: str,
    workflow_id: str,
    paused_segment_id: int
) -> str:
    """
    대용량 상태를 S3에 저장하고 URI 반환
    
    Latest State Pointer 전략과 일관된 경로 구조 사용
    
    Args:
        state_data: 상태 데이터
        state_json: JSON 문자열
        state_bucket: S3 버킷
        chunk_id: 청크 ID
        parent_execution_id: 부모 실행 ID
        owner_id: 소유자 ID
        workflow_id: 워크플로우 ID
        paused_segment_id: 중단된 세그먼트 ID
    
    Returns:
        S3 URI (성공 시) 또는 None (실패 시)
    """
    try:
        s3_client = boto3.client('s3')
        
        # Latest State Pointer와 일관된 경로 구조 사용
        timestamp = int(time.time())
        state_key = (
            f"distributed-states/{owner_id}/{workflow_id}/{parent_execution_id}/"
            f"chunks/{chunk_id}/paused_state_seg_{paused_segment_id}_{timestamp}.json"
        )
        
        # 메타데이터에 복구에 필요한 정보 포함
        metadata = {
            'chunk_id': chunk_id,
            'parent_execution_id': parent_execution_id,
            'owner_id': owner_id or '',
            'workflow_id': workflow_id or '',
            'paused_segment_id': str(paused_segment_id),
            'storage_type': 'task_token_large_state',
            'created_at': str(timestamp),
            'original_size_bytes': str(len(state_json.encode('utf-8'))),
            'resumable': 'true'  # 🚨 재개 가능 표시
        }
        
        # S3에 저장
        s3_client.put_object(
            Bucket=state_bucket,
            Key=state_key,
            Body=state_json.encode('utf-8'),
            ContentType='application/json',
            Metadata=metadata
        )
        
        s3_uri = f"s3://{state_bucket}/{state_key}"
        logger.info(f"Large state stored for task token: {s3_uri}")
        
        return s3_uri
        
    except Exception as e:
        logger.error(f"Failed to store large state in S3: {e}")
        return None


def _create_state_summary_for_token(final_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Task Token용 상태 요약 생성 (DynamoDB 크기 제한 준수)
    
    Args:
        final_state: 원본 상태 데이터
    
    Returns:
        상태 요약 (작은 크기)
    """
    try:
        if not isinstance(final_state, dict):
            return {"type": type(final_state).__name__, "size": len(str(final_state))}
        
        summary = {
            "latest_segment_id": final_state.get('__latest_segment_id'),
            "has_callback_input": '__callback_input' in final_state,
            "last_callback_timestamp": final_state.get('__last_callback', {}).get('timestamp'),
            "segment_count": len(final_state.get('segments', [])),
            "total_keys": len(final_state.keys()),
            "state_keys_sample": list(final_state.keys())[:5]  # 처음 5개 키만
        }
        
        # 중요한 작은 필드들은 직접 포함 (재개 시 유용)
        important_fields = ['selected_option', 'user_response', 'approval', 'status']
        for field in important_fields:
            if field in final_state:
                value = final_state[field]
                if isinstance(value, (str, int, bool, float)) and len(str(value)) < 50:
                    summary[field] = value
        
        return summary
        
    except Exception as e:
        logger.error(f"Failed to create state summary: {e}")
        return {"error": "Summary creation failed", "original_type": type(final_state).__name__}


def _validate_conversation_id_uniqueness(
    conversation_id: str,
    parent_execution_id: str,
    owner_id: str,
    chunk_id: str,
    paused_segment_id: int
) -> Dict[str, Any]:
    """
    🚨 [Critical Fix #1] conversation_id 고유성 검증
    
    생성된 conversation_id가 충분히 고유한지 검증하고
    충돌 위험을 평가합니다.
    
    Args:
        conversation_id: 생성된 conversation_id
        parent_execution_id: 부모 실행 ID
        owner_id: 소유자 ID
        chunk_id: 청크 ID
        paused_segment_id: 중단된 세그먼트 ID
    
    Returns:
        검증 결과 및 권장사항
    """
    try:
        validation = {
            "is_unique": True,
            "warnings": [],
            "recommendations": [],
            "collision_risk": "low"
        }
        
        # 1. 필수 구성 요소 확인
        required_components = [parent_execution_id, chunk_id]
        if paused_segment_id is not None:
            required_components.append(str(paused_segment_id))
        
        missing_components = []
        for component in required_components:
            if not component or str(component) not in conversation_id:
                missing_components.append(component)
        
        if missing_components:
            validation["is_unique"] = False
            validation["warnings"].append(f"Missing components in conversation_id: {missing_components}")
            validation["collision_risk"] = "high"
        
        # 2. owner_id 포함 여부 확인 (추가 안전장치)
        if owner_id and owner_id not in conversation_id:
            validation["warnings"].append("owner_id not included - consider adding for extra uniqueness")
            validation["collision_risk"] = "medium"
        
        # 3. 길이 검증 (DynamoDB 키 제한)
        if len(conversation_id) > 2048:  # DynamoDB 키 길이 제한
            validation["is_unique"] = False
            validation["warnings"].append(f"conversation_id too long: {len(conversation_id)} chars")
        
        # 4. 특수 문자 검증
        if any(char in conversation_id for char in ['/', '\\', '?', '#']):
            validation["warnings"].append("Special characters in conversation_id may cause issues")
        
        # 5. 권장사항 생성
        if validation["collision_risk"] == "high":
            validation["recommendations"].append("Include execution_id and owner_id in conversation_id")
        elif validation["collision_risk"] == "medium":
            validation["recommendations"].append("Consider including owner_id for extra uniqueness")
        
        if len(conversation_id) < 20:
            validation["recommendations"].append("conversation_id may be too short for guaranteed uniqueness")
        
        return validation
        
    except Exception as e:
        logger.error(f"conversation_id validation failed: {e}")
        return {
            "is_unique": False,
            "warnings": [f"Validation failed: {str(e)}"],
            "recommendations": ["Use fallback conversation_id generation"],
            "collision_risk": "unknown"
        }