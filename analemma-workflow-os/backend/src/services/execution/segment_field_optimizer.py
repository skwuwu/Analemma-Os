"""
Segment Field Optimizer - 세그먼트별 필요 필드만 전달

각 세그먼트 타입에 따라 실제 필요한 필드만 추출하여 
payload 크기를 70-80% 감소시킵니다.
"""

from typing import Dict, Any, Set, Optional
import logging
import json

logger = logging.getLogger(__name__)

# [V3 Enhancement] 동적 Hydration 메타데이터 키
DYNAMIC_INTENT_KEY = "__agent_intent__"  # 에이전트가 요청하는 추가 필드

# 노드 타입별 필수 필드 매핑
NODE_REQUIRED_FIELDS = {
    "llm_chat": {
        "control": {"segment_id", "execution_id", "owner_id", "workflow_id"},
        "data": {"current_state"},  # workflow_config 불필요
        "config": {"node.config", "node.id", "node.type"}
    },
    "conditional": {
        "control": {"segment_id", "execution_id"},
        "data": {"current_state"},  # partition_map 불필요
        "config": {"node.condition", "node.id", "node.type"}
    },
    "data_transform": {
        "control": {"segment_id", "execution_id"},
        "data": {"current_state", "query_results"},
        "config": {"node.transform", "node.id", "node.type"}
    },
    "operator": {
        "control": {"segment_id", "execution_id"},
        "data": {"current_state"},
        "config": {"node.params", "node.strategy", "node.id", "node.type"}
    },
    "vision": {
        "control": {"segment_id", "execution_id"},
        "data": {"current_state", "image_inputs", "video_inputs"},
        "config": {"node.config", "node.id", "node.type"}
    },
    "api_call": {
        "control": {"segment_id", "execution_id"},
        "data": {"current_state"},
        "config": {"node.endpoint", "node.headers", "node.params", "node.id"}
    },
    "parallel_group": {
        "control": {"segment_id", "execution_id", "owner_id", "workflow_id"},
        "data": {"current_state", "branches"},
        "config": {"node.id", "node.type"}  # workflow_config는 branches에 포함
    },
    "aggregator": {
        "control": {"segment_id", "execution_id"},
        "data": {"current_state", "branch_results", "parallel_results"},
        "config": {"node.aggregation_strategy", "node.id"}
    },
    "loop": {
        "control": {"segment_id", "execution_id", "loop_counter", "max_loop_iterations"},
        "data": {"current_state"},
        "config": {"node.loop_config", "node.id", "node.type"}
    },
    "trigger": {
        "control": {"execution_id"},
        "data": {"trigger_payload", "request_context"},
        "config": {"node.trigger_type"}
    }
}

# 항상 제외할 대용량 필드 (포인터로만 전달)
ALWAYS_EXCLUDE_FIELDS = {
    "workflow_config",     # 전체 워크플로우 그래프 (수백 KB)
    "partition_map",       # 전체 세그먼트 파티션 (수십 KB)
    "segment_manifest",    # 전체 세그먼트 매니페스트 (수십 KB)
    "state_history",       # 이전 상태 히스토리 (메가바이트 급)
    "all_edges",           # 전체 엣지 정보 (수십 KB)
}

# 특수 케이스: 이 타입들만 예외적으로 필요
SPECIAL_CASE_NEEDS = {
    "parallel_group": {"workflow_config"},  # 브랜치 생성 시 필요
    "aggregator": {},  # 집계만 하므로 불필요
}


class SegmentFieldOptimizer:
    """세그먼트별 필요 필드만 추출하는 최적화 도구"""
    
    @staticmethod
    def get_required_fields(
        segment_type: str,
        agent_intent: Optional[Dict[str, Any]] = None
    ) -> Set[str]:
        """
        세그먼트 타입에 따라 필요한 필드 집합 반환
        
        [V3 Enhancement] 동적 Hydration 지원:
        - agent_intent로 런타임에 추가 필드 요청 가능
        - 예: {"additional_fields": ["state_history"], "range": "recent_10"}
        
        Args:
            segment_type: 노드 타입 (llm_chat, conditional, etc.)
            agent_intent: 에이전트의 동적 요청 메타데이터
        
        Returns:
            필요한 필드명 집합
        """
        required = NODE_REQUIRED_FIELDS.get(segment_type, {})
        
        all_fields = set()
        all_fields.update(required.get("control", set()))
        all_fields.update(required.get("data", set()))
        all_fields.update(required.get("config", set()))
        
        # 특수 케이스 추가
        special = SPECIAL_CASE_NEEDS.get(segment_type, set())
        all_fields.update(special)
        
        # [V3] 동적 Hydration: 에이전트 Intent 반영
        if agent_intent:
            additional = agent_intent.get("additional_fields", [])
            all_fields.update(additional)
            
            # 로그: 어떤 필드가 동적으로 추가되었는지 추적
            if additional:
                logger.info(
                    f"[DynamicHydration] {segment_type} requested: {additional}"
                )
        
        return all_fields
    
    @staticmethod
    def filter_event_payload(
        event: Dict[str, Any],
        segment_type: str,
        preserve_control_plane: bool = True,
        strict_mode: bool = False
    ) -> Dict[str, Any]:
        """
        세그먼트 타입에 따라 불필요한 필드 제거
        
        [V3 Enhancements]:
        - strict_mode: 완전 화이트리스트 모드 (가차없이 제거)
        - 동적 Intent 지원 (__agent_intent__ 키)
        - 조건부 로깅 (직렬화 비용 최소화)
        
        Args:
            event: 원본 이벤트 페이로드
            segment_type: 세그먼트 타입
            preserve_control_plane: Control Plane 필드 보존 여부
            strict_mode: True면 화이트리스트 외 모두 제거
        
        Returns:
            최적화된 이벤트 페이로드
        """
        # [V3] 동적 Intent 추출
        agent_intent = event.get(DYNAMIC_INTENT_KEY)
        
        required_fields = SegmentFieldOptimizer.get_required_fields(
            segment_type,
            agent_intent=agent_intent
        )
        
        # Control Plane 필드 (항상 유지)
        control_plane_fields = {
            "segment_id", "execution_id", "owner_id", "workflow_id",
            "idempotency_key", "loop_counter", "segment_to_run",
            "next_action", "status", "total_segments"
        }
        
        # 필터링된 페이로드 생성
        filtered = {}
        
        # [V3] Strict Mode: 화이트리스트 외 모두 제거
        if strict_mode:
            allowed = required_fields | control_plane_fields if preserve_control_plane else required_fields
            
            for key, value in event.items():
                if key in allowed:
                    filtered[key] = value
                elif key in ALWAYS_EXCLUDE_FIELDS:
                    # 포인터로 변환
                    filtered[key] = SegmentFieldOptimizer._to_pointer(key, value)
            
        else:
            # Legacy Mode: 기존 혼합 방식
            for key, value in event.items():
                # 1. Control Plane 필드는 항상 유지
                if preserve_control_plane and key in control_plane_fields:
                    filtered[key] = value
                    continue
                
                # 2. 필수 필드는 유지
                if key in required_fields:
                    filtered[key] = value
                    continue
                
                # 3. 제외 대상이면 포인터만 유지
                if key in ALWAYS_EXCLUDE_FIELDS:
                    filtered[key] = SegmentFieldOptimizer._to_pointer(key, value)
                    continue
                
                # 4. 나머지는 현재 로직 유지 (당분간)
                # TODO: 추후 화이트리스트 방식으로 전환
                filtered[key] = value
        
        # [Fix #1] 조건부 로깅: INFO 레벨일 때만 크기 계산
        # 피드백: len(str(event))는 직렬화 비용 2번 발생
        if logger.isEnabledFor(logging.INFO):
            # json.dumps 결과를 한 번만 계산
            original_json = json.dumps(event, default=str)
            filtered_json = json.dumps(filtered, default=str)
            
            original_size = len(original_json)
            filtered_size = len(filtered_json)
            reduction = (1 - filtered_size / original_size) * 100 if original_size > 0 else 0
            
            mode_label = "Strict" if strict_mode else "Legacy"
            logger.info(
                f"[FieldOptimizer/{mode_label}] {segment_type}: "
                f"{original_size} → {filtered_size} bytes "
                f"({reduction:.1f}% reduction)"
            )
        
        return filtered
    
    @staticmethod
    def _to_pointer(field_name: str, value: Any) -> Dict[str, Any]:
        """
        필드를 S3 포인터로 변환
        
        Returns:
            S3Pointer 형태 또는 기존 포인터 유지
        """
        if isinstance(value, dict):
            if value.get("__s3_pointer__"):
                # 이미 포인터면 그대로
                return value
            elif value.get("bucket") and value.get("key"):
                # S3Pointer 형태
                return {
                    "__s3_pointer__": True,
                    "bucket": value["bucket"],
                    "key": value["key"],
                    "checksum": value.get("checksum", "")
                }
        
        # 포인터로 변환 불가능한 경우 None 반환 (제외)
        return {"__excluded__": True, "field": field_name}
    
    @staticmethod
    def should_offload_to_s3(field_name: str, segment_type: str) -> bool:
        """
        특정 필드를 S3로 오프로드해야 하는지 판단
        
        Args:
            field_name: 필드 이름
            segment_type: 세그먼트 타입
        
        Returns:
            True면 S3 오프로드 필요
        """
        # 항상 오프로드
        if field_name in ALWAYS_EXCLUDE_FIELDS:
            return True
        
        # 특수 케이스: parallel_group은 workflow_config 필요하므로 오프로드 안 함
        if segment_type in SPECIAL_CASE_NEEDS:
            if field_name in SPECIAL_CASE_NEEDS[segment_type]:
                return False
        
        # 대용량 데이터 필드
        large_data_fields = {
            "final_state", "current_state", "branches",
            "parallel_results", "branch_results"
        }
        
        return field_name in large_data_fields
    
    @staticmethod
    def load_pointer_with_select(
        s3_client,
        pointer: Dict[str, Any],
        fields_to_extract: Optional[Set[str]] = None
    ) -> Dict[str, Any]:
        """
        [Fix #3] S3 Select로 포인터 로드 (부분 필드만 추출)
        
        피드백 반영:
        - 전체 파일 다운로드 대신 SQL-on-JSON 사용
        - Lambda 메모리/네트워크 비용 획기적 감소
        
        Args:
            s3_client: boto3 S3 클라이언트
            pointer: S3Pointer 객체 ({"bucket": ..., "key": ...})
            fields_to_extract: 추출할 필드명 집합 (None이면 전체)
        
        Returns:
            로드된 데이터 (필드 필터링 적용)
        """
        if not pointer.get("__s3_pointer__"):
            # 포인터가 아니면 그대로 반환
            return pointer
        
        bucket = pointer.get("bucket")
        key = pointer.get("key")
        
        if not bucket or not key:
            logger.warning("Invalid S3 pointer: missing bucket or key")
            return {}
        
        try:
            # 필드 추출 없으면 GetObject
            if not fields_to_extract:
                response = s3_client.get_object(Bucket=bucket, Key=key)
                data = json.loads(response['Body'].read().decode('utf-8'))
                logger.info(f"[S3 GetObject] Loaded full data from {key}")
                return data
            
            # S3 Select로 필드별 추출
            # SQL: SELECT s.field1, s.field2 FROM S3Object[*] s
            select_fields = ", ".join([f"s.{field}" for field in fields_to_extract])
            sql_query = f"SELECT {select_fields} FROM S3Object[*] s"
            
            response = s3_client.select_object_content(
                Bucket=bucket,
                Key=key,
                ExpressionType='SQL',
                Expression=sql_query,
                InputSerialization={
                    'JSON': {'Type': 'DOCUMENT'},
                    'CompressionType': 'GZIP'  # 🔄 v3.3 KernelStateManager 호환
                },
                OutputSerialization={'JSON': {'RecordDelimiter': '\n'}}
            )
            
            # 스트림에서 결과 수집
            result_data = {}
            for event in response['Payload']:
                if 'Records' in event:
                    records = event['Records']['Payload'].decode('utf-8')
                    for line in records.strip().split('\n'):
                        if line:
                            result_data.update(json.loads(line))
            
            logger.info(
                f"[S3 Select] Extracted {len(fields_to_extract)} fields from {key} "
                f"(~{len(str(result_data))} bytes)"
            )
            
            return result_data
            
        except Exception as e:
            logger.error(f"Failed to load S3 pointer: {e}", exc_info=True)
            # Fallback: 전체 로드
            try:
                response = s3_client.get_object(Bucket=bucket, Key=key)
                return json.loads(response['Body'].read().decode('utf-8'))
            except Exception as fallback_error:
                logger.error(f"Fallback GetObject also failed: {fallback_error}")
                return {}


# Singleton 인스턴스
_optimizer = SegmentFieldOptimizer()


def optimize_segment_payload(
    event: Dict[str, Any],
    segment_config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    세그먼트 실행 전 페이로드 최적화 (편의 함수)
    
    Args:
        event: 원본 이벤트
        segment_config: 세그먼트 설정 (type 포함)
    
    Returns:
        최적화된 이벤트
    """
    segment_type = segment_config.get("type", "unknown")
    return _optimizer.filter_event_payload(event, segment_type)


def get_offload_fields(segment_type: str) -> Set[str]:
    """
    S3 오프로드가 필요한 필드 목록 반환 (편의 함수)
    
    Args:
        segment_type: 세그먼트 타입
    
    Returns:
        오프로드 대상 필드명 집합
    """
    offload_fields = set()
    
    # 기본 오프로드 필드
    for field in ["final_state", "current_state", "branches", "parallel_results"]:
        if _optimizer.should_offload_to_s3(field, segment_type):
            offload_fields.add(field)
    
    # 항상 제외 필드
    offload_fields.update(ALWAYS_EXCLUDE_FIELDS)
    
    # 특수 케이스 제외
    if segment_type in SPECIAL_CASE_NEEDS:
        offload_fields -= SPECIAL_CASE_NEEDS[segment_type]
    
    return offload_fields
