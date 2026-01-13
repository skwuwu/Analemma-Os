"""
Distributed Map 실행 결과들을 집계하여 최종 워크플로우 상태 생성

모든 청크의 실행 결과를 수집하고 병합하여 
단일 워크플로우 실행 결과로 통합합니다.
"""

import json
import logging
import os
import time
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from src.common.constants import DynamoDBConfig

logger = logging.getLogger(__name__)

def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Distributed Map 실행 결과들을 집계하여 최종 워크플로우 상태 생성
    
    🚨 [Critical Fix] S3에서 결과를 읽어서 페이로드 제한 해결
    
    Args:
        event: {
            "distributed_results_s3_path": "s3://bucket/key" (S3 사용 시),
            "distributed_results": [...] (인라인 사용 시),
            "state_data": {...},
            "use_s3_results": boolean
        }
        
    Returns:
        집계된 최종 결과
    """
    try:
        use_s3_results = event.get('use_s3_results', False)
        state_data = event.get('state_data', {})
        
        # 🚨 [Critical Fix] S3에서 결과 로드 또는 인라인 결과 사용
        if use_s3_results and event.get('distributed_results_s3_path'):
            distributed_results = _load_results_from_s3(event['distributed_results_s3_path'])
            logger.info(f"Loaded distributed results from src.S3: {event['distributed_results_s3_path']}")
        else:
            distributed_results = event.get('distributed_results', [])
            logger.info(f"Using inline distributed results: {len(distributed_results)} items")
        
        logger.info(f"Aggregating results from {len(distributed_results)} distributed chunks")
        
        if not distributed_results:
            logger.warning("No distributed results to aggregate")
            return _build_aggregation_response(
                status="FAILED",
                error="No distributed results provided",
                total_chunks=0
            )
        
        # 결과 분류 및 검증
        successful_chunks = []
        failed_chunks = []
        partial_chunks = []
        paused_chunks = []  # 🎯 HITP 대기 청크 추가
        
        for result in distributed_results:
            if not isinstance(result, dict):
                logger.warning(f"Invalid result format: {type(result)}")
                continue
                
            chunk_status = result.get('status', 'UNKNOWN')
            if chunk_status == 'COMPLETED':
                successful_chunks.append(result)
            elif chunk_status == 'FAILED':
                failed_chunks.append(result)
            elif chunk_status == 'PARTIAL_FAILURE':
                partial_chunks.append(result)
            elif chunk_status == 'PAUSED_FOR_HITP':
                paused_chunks.append(result)
            elif chunk_status == 'ASYNC_CHILD_WORKFLOW_STARTED':
                # 🎯 [Critical] Fire and Forget: Treat async launch as success
                # But track it separately for logging
                successful_chunks.append(result)
                logger.info(f"Async child workflow launched: {result.get('executionName')}")
            else:
                logger.warning(f"Unknown chunk status: {chunk_status}")
                failed_chunks.append(result)
        
        total_chunks = len(distributed_results)
        successful_count = len(successful_chunks)
        failed_count = len(failed_chunks)
        partial_count = len(partial_chunks)
        paused_count = len(paused_chunks)
        
        logger.info(f"Chunk results: {successful_count} successful (inc. async), {failed_count} failed, {partial_count} partial, {paused_count} paused")
        
        # 🎯 [Critical] HITP 대기 상태 처리
        if paused_count > 0:
            return _build_aggregation_response(
                status="PAUSED_FOR_HITP",
                successful_chunks=successful_count,
                failed_chunks=failed_count,
                paused_chunks=paused_count,
                total_chunks=total_chunks,
                paused_chunk_details=paused_chunks,
                message=f"Workflow paused: {paused_count} chunks waiting for human input"
            )
        
        # 실패 처리 정책 결정
        failure_policy = os.environ.get('DISTRIBUTED_FAILURE_POLICY', 'fail_on_any_failure')
        
        if failure_policy == 'fail_on_any_failure' and (failed_count > 0 or partial_count > 0):
            return _build_aggregation_response(
                status="FAILED",
                successful_chunks=successful_count,
                failed_chunks=failed_count + partial_count,
                total_chunks=total_chunks,
                failed_chunk_details=failed_chunks + partial_chunks,
                error=f"Distributed execution failed: {failed_count} failed chunks, {partial_count} partial failures"
            )
        elif failure_policy == 'fail_on_majority_failure' and failed_count > successful_count:
            return _build_aggregation_response(
                status="FAILED",
                successful_chunks=successful_count,
                failed_chunks=failed_count,
                total_chunks=total_chunks,
                failed_chunk_details=failed_chunks,
                error=f"Majority of chunks failed: {failed_count}/{total_chunks}"
            )
        
        # 🎯 [Critical] 최신 상태를 DynamoDB/S3에서 로드
        final_state = _load_latest_state(state_data)
        
        # 성공한 청크들의 결과 병합
        aggregated_logs = []
        execution_summary = {
            'distributed_mode': True,
            'total_chunks': total_chunks,
            'successful_chunks': successful_count,
            'failed_chunks': failed_count,
            'partial_chunks': partial_count,
            'paused_chunks': paused_count,
            'chunk_details': [],
            'total_segments_processed': 0,
            'total_execution_time': 0,
            'aggregation_timestamp': datetime.now(timezone.utc).isoformat(),
            'state_continuity_method': 'latest_pointer'
        }
        
        # 청크별 결과 병합
        for chunk_result in successful_chunks + partial_chunks:
            chunk_id = chunk_result.get('chunk_id', 'unknown')
            chunk_results = chunk_result.get('chunk_results', [])
            
            # 로그 병합
            chunk_logs = []
            for segment_result in chunk_results:
                if isinstance(segment_result, dict) and segment_result.get('result'):
                    segment_logs = segment_result['result'].get('new_history_logs', [])
                    if isinstance(segment_logs, list):
                        chunk_logs.extend(segment_logs)
            
            if chunk_logs:
                aggregated_logs.extend(chunk_logs)
            
            # 실행 통계 수집
            processed_segments = chunk_result.get('processed_segments', 0)
            execution_time = chunk_result.get('execution_time', 0)
            
            execution_summary['total_segments_processed'] += processed_segments
            execution_summary['total_execution_time'] = max(
                execution_summary['total_execution_time'], 
                execution_time
            )  # 병렬 실행이므로 최대값 사용
            
            execution_summary['chunk_details'].append({
                'chunk_id': chunk_id,
                'status': chunk_result.get('status'),
                'processed_segments': processed_segments,
                'execution_time': execution_time,
                'start_segment': chunk_result.get('start_segment'),
                'end_segment': chunk_result.get('end_segment')
            })
        
        # 최종 상태 결정
        if successful_count == total_chunks:
            final_status = "COMPLETED"
        elif successful_count > 0:
            final_status = "PARTIAL_SUCCESS"
        else:
            final_status = "FAILED"
        
        # 로그 정렬 (시간순) - 대용량 로그 처리 최적화
        if _should_defer_sorting_to_client(len(aggregated_logs)):
            # 클라이언트 측 정렬 권장
            execution_summary['client_side_sorting_recommended'] = True
            execution_summary['unsorted_log_count'] = len(aggregated_logs)
            logger.info(f"Deferring log sorting to client: {len(aggregated_logs)} logs")
        else:
            aggregated_logs = _sort_logs_by_timestamp(aggregated_logs)
        
        # 🎯 [Critical Fix] 집계 완료 후 최종 상태를 DynamoDB에 저장
        final_execution_state = {
            **final_state,
            'execution_summary': execution_summary,
            'aggregated_logs': aggregated_logs if len(aggregated_logs) < 1000 else [],  # 대용량 로그는 S3에 저장
            'aggregation_completed': True,
            'final_status': final_status,
            'completion_timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # 최종 상태 저장 - [Fix] S3 경로 반환값 사용
        final_state_s3_path = _save_final_state(state_data, final_execution_state, execution_summary)
        
        # 🎯 [Optimization] 중간 상태 정리 (환경 변수로 제어)
        cleanup_enabled = os.environ.get('CLEANUP_INTERMEDIATE_STATES', 'true').lower() == 'true'
        if cleanup_enabled:
            try:
                execution_id = state_data.get('workflowId', 'unknown')
                _cleanup_intermediate_states(execution_id)
            except Exception as cleanup_error:
                logger.warning(f"Cleanup failed but continuing: {cleanup_error}")
        else:
            logger.info("Intermediate state cleanup disabled by configuration")
        
        logger.info(f"Aggregation completed: {final_status}, {execution_summary['total_segments_processed']} segments processed")
        
        return _build_aggregation_response(
            status=final_status,
            final_state=final_state,
            final_state_s3_path=final_state_s3_path,  # [Fix] S3 경로 전달
            total_segments_processed=execution_summary['total_segments_processed'],
            total_chunks=total_chunks,
            successful_chunks=successful_count,
            failed_chunks=failed_count,
            execution_summary=execution_summary,
            all_results=aggregated_logs,
            aggregation_metadata={
                'aggregation_time': time.time(),
                'distributed_execution': True,
                'failure_policy': failure_policy,
                'state_continuity_ensured': True
            }
        )
        
    except Exception as e:
        logger.exception("Failed to aggregate distributed results")
        return _build_aggregation_response(
            status="FAILED",
            error=f"Aggregation failed: {str(e)}",
            total_chunks=len(event.get('distributed_results', []))
        )


def _merge_states(base_state: Dict[str, Any], chunk_state: Dict[str, Any], chunk_id: str) -> Dict[str, Any]:
    """
    두 상태를 안전하게 병합
    
    Args:
        base_state: 기본 상태
        chunk_state: 청크에서 생성된 상태
        chunk_id: 청크 식별자
        
    Returns:
        병합된 상태
    """
    if not isinstance(chunk_state, dict):
        return base_state
    
    merged = base_state.copy()
    
    # 청크별 네임스페이스 격리
    if 'chunks' not in merged:
        merged['chunks'] = {}
    
    # 청크 결과를 네임스페이스에 저장
    merged['chunks'][chunk_id] = chunk_state
    
    # 글로벌 상태 병합 (키 충돌 방지)
    for key, value in chunk_state.items():
        if key.startswith('__'):
            # 시스템 키는 무시
            continue
        elif key in merged and key != 'chunks':
            # 기존 키가 있으면 배열로 변환하여 보존
            if not isinstance(merged[key], list):
                merged[key] = [merged[key]]
            if isinstance(merged[key], list):
                merged[key].append(value)
        else:
            # 새로운 키는 직접 추가
            merged[key] = value
    
    return merged


def _sort_logs_by_timestamp(logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    로그를 타임스탬프 순으로 효율적으로 정렬
    
    🚨 [Performance Fix] 대용량 로그 처리 최적화
    """
    if not logs:
        return []
    
    # 🚨 [Critical] 대용량 로그 감지 및 처리 방식 결정
    log_count = len(logs)
    memory_threshold = 10000  # 10K 로그 이상 시 최적화 적용
    
    if log_count > memory_threshold:
        logger.warning(f"Large log dataset detected: {log_count} entries. Using optimized sorting.")
        return _sort_logs_optimized(logs)
    
    # 일반 크기는 기존 방식 유지
    def get_timestamp(log_entry):
        if isinstance(log_entry, dict):
            # 다양한 타임스탬프 필드 지원
            for ts_field in ['timestamp', 'created_at', 'time', 'date']:
                if ts_field in log_entry:
                    try:
                        if isinstance(log_entry[ts_field], (int, float)):
                            return log_entry[ts_field]
                        elif isinstance(log_entry[ts_field], str):
                            # ISO 형식 파싱 시도
                            from datetime import datetime
                            return datetime.fromisoformat(log_entry[ts_field].replace('Z', '+00:00')).timestamp()
                    except:
                        continue
        return 0  # 타임스탬프가 없으면 0으로 처리
    
    try:
        return sorted(logs, key=get_timestamp)
    except Exception as e:
        logger.warning(f"Failed to sort logs by timestamp: {e}")
        return logs


def _sort_logs_optimized(logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    대용량 로그를 위한 최적화된 정렬
    
    🚨 [Performance Fix] 메모리 효율적인 대용량 로그 정렬
    
    전략:
    1. 청크별로 이미 정렬된 로그들을 Merge Sort 방식으로 병합
    2. 메모리 사용량을 제한하면서 스트리밍 정렬
    3. 필요시 임시 파일 사용
    """
    try:
        import heapq
        from collections import defaultdict
        
        # 🎯 [Optimization] 청크별 로그 그룹핑 (이미 정렬되어 있다고 가정)
        chunk_logs = defaultdict(list)
        
        for log_entry in logs:
            if isinstance(log_entry, dict):
                # 청크 ID 추출 (로그에 청크 정보가 있다고 가정)
                chunk_id = log_entry.get('chunk_id', 'default')
                chunk_logs[chunk_id].append(log_entry)
        
        # 각 청크 내에서 정렬 (이미 정렬되어 있을 가능성 높음)
        sorted_chunks = []
        for chunk_id, chunk_log_list in chunk_logs.items():
            sorted_chunk = sorted(chunk_log_list, key=_extract_timestamp)
            sorted_chunks.append((chunk_id, sorted_chunk))
        
        # 🎯 [Critical] K-way merge using heap
        result = []
        heap = []
        chunk_iterators = {}
        
        # 각 청크의 첫 번째 로그를 힙에 추가
        for chunk_id, sorted_chunk in sorted_chunks:
            if sorted_chunk:
                chunk_iter = iter(sorted_chunk)
                first_log = next(chunk_iter)
                timestamp = _extract_timestamp(first_log)
                heapq.heappush(heap, (timestamp, chunk_id, first_log))
                chunk_iterators[chunk_id] = chunk_iter
        
        # K-way merge 수행
        while heap:
            timestamp, chunk_id, log_entry = heapq.heappop(heap)
            result.append(log_entry)
            
            # 해당 청크에서 다음 로그 가져오기
            try:
                next_log = next(chunk_iterators[chunk_id])
                next_timestamp = _extract_timestamp(next_log)
                heapq.heappush(heap, (next_timestamp, chunk_id, next_log))
            except StopIteration:
                # 해당 청크의 로그가 모두 소진됨
                pass
        
        logger.info(f"Optimized sorting completed: {len(result)} logs processed")
        return result
        
    except Exception as e:
        logger.error(f"Optimized sorting failed, falling back to standard sort: {e}")
        # 실패 시 표준 정렬로 폴백
        return sorted(logs, key=_extract_timestamp)


def _extract_timestamp(log_entry: Dict[str, Any]) -> float:
    """
    로그 엔트리에서 타임스탬프 추출 (최적화된 버전)
    """
    if not isinstance(log_entry, dict):
        return 0.0
    
    # 성능을 위해 가장 일반적인 필드부터 확인
    timestamp_fields = ['timestamp', 'created_at', 'time', 'date']
    
    for ts_field in timestamp_fields:
        if ts_field in log_entry:
            ts_value = log_entry[ts_field]
            
            # 숫자형 타임스탬프 (가장 빠름)
            if isinstance(ts_value, (int, float)):
                return float(ts_value)
            
            # 문자열 타임스탬프
            elif isinstance(ts_value, str):
                try:
                    # ISO 형식 최적화된 파싱
                    if 'T' in ts_value:  # ISO 8601 형식
                        from datetime import datetime
                        # Z를 +00:00으로 변환
                        normalized = ts_value.replace('Z', '+00:00')
                        return datetime.fromisoformat(normalized).timestamp()
                    else:
                        # 다른 형식 시도
                        return float(ts_value)
                except (ValueError, TypeError):
                    continue
    
    return 0.0


def _should_defer_sorting_to_client(log_count: int) -> bool:
    """
    클라이언트 측 정렬을 권장할지 결정
    
    🎯 [Strategy] 대용량 로그는 클라이언트에서 정렬하도록 권장
    """
    # 환경 변수로 임계값 설정 가능
    client_sort_threshold = int(os.environ.get('CLIENT_SORT_THRESHOLD', '50000'))
    
    if log_count > client_sort_threshold:
        logger.info(f"Recommending client-side sorting for {log_count} logs (threshold: {client_sort_threshold})")
        return True
    
    return False


def _load_results_from_s3(s3_path: str) -> List[Dict[str, Any]]:
    """
    S3에서 분산 실행 결과를 스트리밍 방식으로 로드
    
    🚨 [Performance Fix] 메모리 효율적인 청크 단위 로딩
    
    Args:
        s3_path: S3 경로 (s3://bucket/key)
        
    Returns:
        분산 실행 결과 리스트
    """
    try:
        import boto3
        s3_client = boto3.client('s3')
        
        # S3 경로 파싱
        if not s3_path.startswith('s3://'):
            raise ValueError(f"Invalid S3 path format: {s3_path}")
        
        bucket, key = s3_path[5:].split('/', 1)
        
        # 🚨 [Critical Fix] 파일 크기 먼저 확인
        head_response = s3_client.head_object(Bucket=bucket, Key=key)
        file_size = head_response['ContentLength']
        
        # 메모리 제한 확인 (람다 메모리의 80% 이하로 제한)
        lambda_memory_mb = int(os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', '512'))
        max_file_size = (lambda_memory_mb * 1024 * 1024) * 0.8  # 80% 제한
        
        if file_size > max_file_size:
            logger.warning(f"Large S3 file detected: {file_size} bytes (limit: {max_file_size})")
            # 대용량 파일은 스트리밍 처리
            return _load_large_results_streaming(s3_client, bucket, key, file_size)
        
        # 일반 크기 파일은 기존 방식
        response = s3_client.get_object(Bucket=bucket, Key=key)
        results_json = response['Body'].read().decode('utf-8')
        
        # 🚨 [Memory Optimization] JSON 파싱 전 메모리 사용량 로깅
        import sys
        memory_before = sys.getsizeof(results_json)
        logger.info(f"JSON string size: {memory_before} bytes")
        
        results = json.loads(results_json)
        
        # 메모리 정리
        del results_json
        
        if not isinstance(results, list):
            logger.warning(f"Expected list from src.S3, got {type(results)}")
            return []
        
        logger.info(f"Loaded {len(results)} results from src.S3: {s3_path}")
        return results
        
    except Exception as e:
        logger.error(f"Failed to load results from src.S3 {s3_path}: {e}")
        return []


def _load_large_results_streaming(s3_client, bucket: str, key: str, file_size: int) -> List[Dict[str, Any]]:
    """
    대용량 S3 파일을 스트리밍 방식으로 처리
    
    🚨 [Critical Fix] ijson 의존성 강화 및 폴백 로직 개선
    """
    try:
        # 🚨 [Dependency Check] ijson 가용성 확인
        try:
            import ijson
            logger.info(f"Using ijson streaming parser for large file: {file_size} bytes")
            return _load_with_ijson_streaming(s3_client, bucket, key)
        except ImportError as ijson_error:
            logger.warning(f"ijson not available ({ijson_error}), using enhanced fallback")
            return _load_results_enhanced_fallback(s3_client, bucket, key, file_size)
        
    except Exception as e:
        logger.error(f"Streaming load failed: {e}")
        return []


def _load_with_ijson_streaming(s3_client, bucket: str, key: str) -> List[Dict[str, Any]]:
    """
    ijson을 사용한 실제 스트리밍 파싱
    """
    import ijson
    
    # S3 스트리밍 객체 생성
    response = s3_client.get_object(Bucket=bucket, Key=key)
    stream = response['Body']
    
    # ijson을 사용한 스트리밍 파싱
    results = []
    
    try:
        # JSON 배열의 각 항목을 스트리밍으로 파싱
        parser = ijson.items(stream, 'item')
        
        chunk_count = 0
        for chunk_result in parser:
            results.append(chunk_result)
            chunk_count += 1
            
            # 메모리 사용량 모니터링 (1000개마다)
            if chunk_count % 1000 == 0:
                logger.info(f"Processed {chunk_count} chunks via ijson streaming")
                
                # 메모리 사용량 체크
                import sys
                current_memory = sys.getsizeof(results)
                if current_memory > 500 * 1024 * 1024:  # 500MB 제한
                    logger.warning(f"High memory usage detected: {current_memory} bytes")
        
        logger.info(f"ijson streaming completed: {len(results)} results")
        return results
        
    except Exception as ijson_parse_error:
        logger.error(f"ijson parsing failed: {ijson_parse_error}")
        # ijson 파싱 실패 시 향상된 폴백으로 전환
        stream.close()
        return _load_results_enhanced_fallback(s3_client, bucket, key, None)


def _load_results_enhanced_fallback(s3_client, bucket: str, key: str, file_size: Optional[int]) -> List[Dict[str, Any]]:
    """
    🚨 [Enhanced Fallback] ijson이 없거나 실패할 때의 향상된 대안
    
    기존 청크 단위 읽기보다 정교한 JSON 파싱 구현
    """
    try:
        logger.info("Using enhanced fallback JSON parsing")
        
        # 파일 크기가 너무 크면 부분 처리
        if file_size and file_size > 100 * 1024 * 1024:  # 100MB 이상
            return _load_results_partial_processing(s3_client, bucket, key, file_size)
        
        # 전체 파일 읽기 (메모리 모니터링 포함)
        response = s3_client.get_object(Bucket=bucket, Key=key)
        
        # 🚨 [Memory Management] 스트리밍 읽기로 메모리 절약
        content_chunks = []
        chunk_size = 1024 * 1024  # 1MB씩 읽기
        
        while True:
            chunk = response['Body'].read(chunk_size)
            if not chunk:
                break
            content_chunks.append(chunk)
            
            # 메모리 사용량 체크
            total_size = sum(len(c) for c in content_chunks)
            if total_size > 200 * 1024 * 1024:  # 200MB 제한
                logger.warning(f"Large content detected: {total_size} bytes, switching to partial processing")
                return _load_results_partial_processing(s3_client, bucket, key, total_size)
        
        # 전체 내용 조합
        full_content = b''.join(content_chunks).decode('utf-8')
        del content_chunks  # 메모리 정리
        
        # JSON 파싱
        results = json.loads(full_content)
        del full_content  # 메모리 정리
        
        if not isinstance(results, list):
            logger.warning(f"Expected list, got {type(results)}")
            return []
        
        logger.info(f"Enhanced fallback completed: {len(results)} results")
        return results
        
    except Exception as e:
        logger.error(f"Enhanced fallback failed: {e}")
        return []


def _load_results_partial_processing(s3_client, bucket: str, key: str, file_size: int) -> List[Dict[str, Any]]:
    """
    🚨 [Partial Processing] 초대용량 파일을 위한 부분 처리
    
    전체 파일을 로드할 수 없을 때 중요한 부분만 추출
    """
    try:
        logger.warning(f"Using partial processing for large file: {file_size} bytes")
        
        # 파일의 시작과 끝 부분만 읽어서 구조 파악
        head_size = min(1024 * 1024, file_size // 10)  # 1MB 또는 파일의 10%
        tail_size = min(1024 * 1024, file_size // 10)
        
        # 헤드 부분 읽기
        head_response = s3_client.get_object(
            Bucket=bucket, 
            Key=key,
            Range=f'bytes=0-{head_size-1}'
        )
        head_content = head_response['Body'].read().decode('utf-8')
        
        # 테일 부분 읽기
        tail_start = max(0, file_size - tail_size)
        tail_response = s3_client.get_object(
            Bucket=bucket,
            Key=key,
            Range=f'bytes={tail_start}-{file_size-1}'
        )
        tail_content = tail_response['Body'].read().decode('utf-8')
        
        # 🎯 [Strategy] 부분 데이터에서 완전한 JSON 객체 추출
        results = []
        
        # 헤드에서 완전한 객체들 추출
        head_objects = _extract_complete_json_objects(head_content, from_start=True)
        results.extend(head_objects)
        
        # 테일에서 완전한 객체들 추출 (중복 제거)
        tail_objects = _extract_complete_json_objects(tail_content, from_start=False)
        
        # 중복 제거 (chunk_id 기준)
        seen_chunk_ids = {obj.get('chunk_id') for obj in results if isinstance(obj, dict)}
        for obj in tail_objects:
            if isinstance(obj, dict) and obj.get('chunk_id') not in seen_chunk_ids:
                results.append(obj)
        
        logger.warning(f"Partial processing extracted {len(results)} objects from {file_size} byte file")
        return results
        
    except Exception as e:
        logger.error(f"Partial processing failed: {e}")
        return []


def _extract_complete_json_objects(content: str, from_start: bool = True) -> List[Dict[str, Any]]:
    """
    부분 JSON 내용에서 완전한 객체들을 추출
    """
    try:
        objects = []
        
        # 간단한 JSON 객체 경계 찾기
        if from_start:
            # 시작부터 완전한 객체들 찾기
            brace_count = 0
            current_obj = ""
            in_string = False
            escape_next = False
            
            for char in content:
                current_obj += char
                
                if escape_next:
                    escape_next = False
                    continue
                    
                if char == '\\':
                    escape_next = True
                    continue
                    
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                    
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        
                        if brace_count == 0 and current_obj.strip():
                            # 완전한 객체 발견
                            try:
                                obj = json.loads(current_obj.strip().rstrip(','))
                                if isinstance(obj, dict):
                                    objects.append(obj)
                            except:
                                pass
                            current_obj = ""
        
        return objects
        
    except Exception as e:
        logger.warning(f"JSON object extraction failed: {e}")
        return []


def _load_results_chunked_fallback(s3_client, bucket: str, key: str) -> List[Dict[str, Any]]:
    """
    ijson이 없을 때의 대안: 청크 단위로 읽기
    """
    try:
        # Range 요청으로 청크 단위 읽기 (1MB씩)
        chunk_size = 1024 * 1024  # 1MB
        results = []
        offset = 0
        buffer = ""
        
        while True:
            try:
                response = s3_client.get_object(
                    Bucket=bucket, 
                    Key=key,
                    Range=f'bytes={offset}-{offset + chunk_size - 1}'
                )
                chunk_data = response['Body'].read().decode('utf-8')
                buffer += chunk_data
                
                # JSON 객체 경계 찾기 (간단한 구현)
                # 실제로는 더 정교한 파싱이 필요
                if chunk_data == "":
                    break
                    
                offset += chunk_size
                
            except Exception as range_error:
                # Range 요청 실패 시 전체 파일 읽기로 폴백
                logger.warning(f"Range request failed, using full read: {range_error}")
                response = s3_client.get_object(Bucket=bucket, Key=key)
                buffer = response['Body'].read().decode('utf-8')
                break
        
        # 최종 JSON 파싱
        results = json.loads(buffer)
        return results if isinstance(results, list) else []
        
    except Exception as e:
        logger.error(f"Chunked fallback failed: {e}")
        return []


def _load_latest_state(state_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    🎯 [Critical] DynamoDB/S3에서 최신 상태 로드
    
    Args:
        state_data: 워크플로우 상태 데이터
        
    Returns:
        최신 워크플로우 상태
    """
    try:
        import boto3
        
        # DynamoDB에서 최신 상태 포인터 조회
        dynamodb = boto3.resource('dynamodb')
        state_table_name = DynamoDBConfig.WORKFLOWS_TABLE
        state_table = dynamodb.Table(state_table_name)
        
        execution_id = state_data.get('workflowId', 'unknown')
        
        response = state_table.get_item(
            Key={
                'execution_id': execution_id,
                'state_type': 'LATEST'
            }
        )
        
        if 'Item' in response:
            item = response['Item']
            state_s3_path = item.get('state_s3_path')
            
            if state_s3_path:
                # S3에서 최신 상태 로드
                s3_client = boto3.client('s3')
                bucket, key = state_s3_path.replace('s3://', '').split('/', 1)
                
                response = s3_client.get_object(Bucket=bucket, Key=key)
                latest_state = json.loads(response['Body'].read().decode('utf-8'))
                
                logger.info(f"Loaded latest state from src.S3: {state_s3_path}")
                return latest_state
            else:
                # 인라인 상태 반환
                inline_state = item.get('state_data', {})
                logger.info("Loaded latest state from src.DynamoDB (inline)")
                return inline_state
        else:
            logger.warning(f"No latest state found for execution {execution_id}")
            return {}
            
    except Exception as e:
        logger.error(f"Failed to load latest state: {e}")
        return {}


def _build_aggregation_response(
    status: str,
    final_state: Optional[Dict] = None,
    final_state_s3_path: Optional[str] = None,  # [Fix] S3 경로 파라미터 추가
    total_segments_processed: int = 0,
    total_chunks: int = 0,
    successful_chunks: int = 0,
    failed_chunks: int = 0,
    paused_chunks: int = 0,  # 🎯 HITP 지원
    execution_summary: Optional[Dict] = None,
    all_results: Optional[List] = None,
    failed_chunk_details: Optional[List] = None,
    paused_chunk_details: Optional[List] = None,  # 🎯 HITP 지원
    error: Optional[str] = None,
    message: Optional[str] = None,  # 🎯 상태 메시지
    aggregation_metadata: Optional[Dict] = None
) -> Dict[str, Any]:
    """
    집계 결과를 표준화된 형태로 구성
    """
    response = {
        "status": status,
        "final_state": final_state,
        "final_state_s3_path": final_state_s3_path,  # [Fix] S3 경로 포함
        "total_segments_processed": total_segments_processed,
        "total_chunks": total_chunks,
        "successful_chunks": successful_chunks,
        "failed_chunks": failed_chunks,
        "paused_chunks": paused_chunks,
        "execution_summary": execution_summary or {},
        "all_results": all_results or [],
        "aggregation_metadata": aggregation_metadata or {},
        "new_history_logs": all_results or []  # [Fix] ASL ResultSelector 호환성
    }
    
    if failed_chunk_details:
        response["failed_chunk_details"] = failed_chunk_details
    
    if paused_chunk_details:
        response["paused_chunk_details"] = paused_chunk_details
    
    if error:
        response["error"] = error
        
    if message:
        response["message"] = message
    
    return response


def calculate_total_time(results: List[Dict]) -> float:
    """
    분산 실행의 총 시간 계산 (병렬 실행이므로 최대값 사용)
    """
    execution_times = []
    
    for result in results:
        if isinstance(result, dict):
            exec_time = result.get('execution_time')
            if isinstance(exec_time, (int, float)) and exec_time > 0:
                execution_times.append(exec_time)
    
    return max(execution_times) if execution_times else 0


def _save_final_state(state_data: Dict[str, Any], final_state: Dict[str, Any], execution_summary: Dict[str, Any]) -> Optional[str]:
    """
    🎯 [Critical Fix] 집계 완료 후 최종 상태를 DynamoDB에 FINAL로 저장
    
    Args:
        state_data: 워크플로우 상태 데이터
        final_state: 최종 집계된 상태
        execution_summary: 실행 요약 정보
        
    Returns:
        S3 경로 (대용량 상태의 경우) 또는 None (인라인 저장의 경우)
    """
    try:
        import boto3
        
        execution_id = state_data.get('workflowId', 'unknown')
        
        # DynamoDB 테이블 설정
        dynamodb = boto3.resource('dynamodb')
        state_table_name = DynamoDBConfig.WORKFLOWS_TABLE
        state_table = dynamodb.Table(state_table_name)
        
        # S3 설정 (대용량 상태용)
        s3_client = boto3.client('s3')
        state_bucket = os.environ.get('WORKFLOW_STATE_BUCKET', 'workflow-states')
        
        # 상태 크기 확인
        state_json = json.dumps(final_state, ensure_ascii=False)
        state_size = len(state_json.encode('utf-8'))
        
        # 🚨 [Performance] 대용량 상태는 S3에 저장
        use_s3_storage = state_size > 100000  # 100KB 이상
        
        final_record = {
            'execution_id': execution_id,
            'state_type': 'FINAL',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'status': final_state.get('final_status', 'COMPLETED'),
            'total_segments_processed': execution_summary.get('total_segments_processed', 0),
            'total_chunks': execution_summary.get('total_chunks', 0),
            'successful_chunks': execution_summary.get('successful_chunks', 0),
            'failed_chunks': execution_summary.get('failed_chunks', 0),
            'execution_time': execution_summary.get('total_execution_time', 0),
            'aggregation_completed': True,
            'state_size_bytes': state_size,
            'uses_s3_storage': use_s3_storage
        }
        
        if use_s3_storage:
            # 대용량 상태는 S3에 저장
            s3_key = f"final-states/{execution_id}/final-state.json"
            s3_path = f"s3://{state_bucket}/{s3_key}"
            
            s3_client.put_object(
                Bucket=state_bucket,
                Key=s3_key,
                Body=state_json,
                ContentType='application/json',
                Metadata={
                    'execution_id': execution_id,
                    'state_type': 'FINAL',
                    'aggregation_timestamp': datetime.now(timezone.utc).isoformat()
                }
            )
            
            final_record['state_s3_path'] = s3_path
            final_record['execution_summary'] = execution_summary  # 요약만 DynamoDB에
            
            logger.info(f"Final state saved to S3: {s3_path} ({state_size} bytes)")
            final_state_s3_path = s3_path  # [Fix] S3 경로 저장
            
        else:
            # 소용량 상태는 DynamoDB에 인라인 저장
            final_record['state_data'] = final_state
            final_record['execution_summary'] = execution_summary
            final_state_s3_path = None  # [Fix] 인라인 저장의 경우 None
            
            logger.info(f"Final state saved to DynamoDB inline ({state_size} bytes)")
        
        # DynamoDB에 최종 레코드 저장
        state_table.put_item(Item=final_record)
        
        # 🎯 [Performance] LATEST 포인터도 FINAL로 업데이트
        latest_record = {
            'execution_id': execution_id,
            'state_type': 'LATEST',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'points_to_final': True,
            'final_status': final_state.get('final_status', 'COMPLETED'),
            'aggregation_completed': True
        }
        
        if use_s3_storage:
            latest_record['state_s3_path'] = final_record['state_s3_path']
        else:
            latest_record['state_data'] = final_state
        
        state_table.put_item(Item=latest_record)
        
        logger.info(f"Final state successfully saved for execution {execution_id}")
        return final_state_s3_path  # [Fix] S3 경로 반환
        
    except Exception as e:
        logger.error(f"Failed to save final state: {e}")
        # 저장 실패해도 집계 결과는 반환 (비즈니스 로직 우선)
        return None


def _cleanup_intermediate_states(execution_id: str) -> None:
    """
    🎯 [Critical Fix] 중간 상태들을 정리하여 스토리지 비용 절약
    
    DynamoDB 레코드와 S3 객체를 모두 정리합니다.
    
    Args:
        execution_id: 워크플로우 실행 ID
    """
    try:
        import boto3
        
        # DynamoDB 정리
        _cleanup_dynamodb_states(execution_id)
        
        # 🚨 [Critical Fix] S3 중간 객체 정리
        _cleanup_s3_intermediate_objects(execution_id)
        
    except Exception as e:
        logger.warning(f"Failed to cleanup intermediate states: {e}")
        # 정리 실패는 치명적이지 않음


def _cleanup_dynamodb_states(execution_id: str) -> None:
    """
    DynamoDB에서 중간 상태 레코드 정리
    """
    try:
        dynamodb = boto3.resource('dynamodb')
        state_table_name = DynamoDBConfig.WORKFLOWS_TABLE
        state_table = dynamodb.Table(state_table_name)
        
        # 중간 상태들 조회 (INTERMEDIATE, CHUNK_* 등)
        response = state_table.query(
            KeyConditionExpression='execution_id = :exec_id',
            FilterExpression='begins_with(state_type, :intermediate) OR begins_with(state_type, :chunk)',
            ExpressionAttributeValues={
                ':exec_id': execution_id,
                ':intermediate': 'INTERMEDIATE',
                ':chunk': 'CHUNK_'
            }
        )
        
        # 배치 삭제 (최대 25개씩)
        items_to_delete = response.get('Items', [])
        
        if items_to_delete:
            with state_table.batch_writer() as batch:
                for item in items_to_delete:
                    batch.delete_item(
                        Key={
                            'execution_id': item['execution_id'],
                            'state_type': item['state_type']
                        }
                    )
            
            logger.info(f"Cleaned up {len(items_to_delete)} DynamoDB intermediate states for {execution_id}")
        
    except Exception as e:
        logger.warning(f"Failed to cleanup DynamoDB states: {e}")


def _cleanup_s3_intermediate_objects(execution_id: str) -> None:
    """
    🚨 [Critical Fix] S3에서 중간 상태 객체들 정리
    
    정리 대상:
    - distributed-states/{owner_id}/{workflow_id}/{execution_id}/chunks/
    - distributed-states/{owner_id}/{workflow_id}/{execution_id}/segments/
    - distributed-chunks/{owner_id}/{workflow_id}/{execution_id}/
    """
    try:
        import boto3
        
        s3_client = boto3.client('s3')
        state_bucket = os.environ.get('WORKFLOW_STATE_BUCKET')
        
        if not state_bucket:
            logger.warning("No WORKFLOW_STATE_BUCKET configured, skipping S3 cleanup")
            return
        
        # 🎯 [Strategy] execution_id 기반으로 중간 객체 패턴 매칭
        cleanup_prefixes = [
            f"distributed-states/",  # 모든 분산 상태
            f"distributed-chunks/",  # 청크 데이터
            f"workflow-states/"      # 기존 워크플로우 상태
        ]
        
        total_deleted = 0
        
        for prefix in cleanup_prefixes:
            try:
                # execution_id가 포함된 객체들 조회
                paginator = s3_client.get_paginator('list_objects_v2')
                pages = paginator.paginate(
                    Bucket=state_bucket,
                    Prefix=prefix
                )
                
                objects_to_delete = []
                
                for page in pages:
                    if 'Contents' not in page:
                        continue
                    
                    for obj in page['Contents']:
                        key = obj['Key']
                        
                        # execution_id가 경로에 포함되어 있는지 확인
                        if execution_id in key:
                            # 최종 상태는 보존 (final-states 제외)
                            if 'final-states' not in key and 'latest_state.json' not in key:
                                objects_to_delete.append({'Key': key})
                        
                        # 배치 삭제 (최대 1000개씩)
                        if len(objects_to_delete) >= 1000:
                            _batch_delete_s3_objects(s3_client, state_bucket, objects_to_delete)
                            total_deleted += len(objects_to_delete)
                            objects_to_delete = []
                
                # 남은 객체들 삭제
                if objects_to_delete:
                    _batch_delete_s3_objects(s3_client, state_bucket, objects_to_delete)
                    total_deleted += len(objects_to_delete)
                    
            except Exception as prefix_error:
                logger.warning(f"Failed to cleanup prefix {prefix}: {prefix_error}")
        
        if total_deleted > 0:
            logger.info(f"Cleaned up {total_deleted} S3 intermediate objects for {execution_id}")
        else:
            logger.info(f"No S3 intermediate objects found for cleanup: {execution_id}")
            
    except Exception as e:
        logger.warning(f"Failed to cleanup S3 intermediate objects: {e}")


def _batch_delete_s3_objects(s3_client, bucket: str, objects: List[Dict[str, str]]) -> None:
    """
    S3 객체들을 배치로 삭제
    """
    try:
        if not objects:
            return
            
        response = s3_client.delete_objects(
            Bucket=bucket,
            Delete={
                'Objects': objects,
                'Quiet': True  # 성공한 삭제는 응답에서 제외
            }
        )
        
        # 삭제 실패한 객체들 로깅
        errors = response.get('Errors', [])
        if errors:
            logger.warning(f"Failed to delete {len(errors)} S3 objects: {errors[:5]}")  # 처음 5개만 로깅
            
    except Exception as e:
        logger.warning(f"Batch delete failed: {e}")


def _setup_s3_lifecycle_policy(bucket_name: str) -> None:
    """
    🎯 [Optimization] S3 Lifecycle Policy 설정으로 자동 정리
    
    중간 상태 객체들의 자동 만료 설정
    """
    try:
        import boto3
        
        s3_client = boto3.client('s3')
        
        lifecycle_config = {
            'Rules': [
                {
                    'ID': 'WorkflowIntermediateStatesCleanup',
                    'Status': 'Enabled',
                    'Filter': {
                        'Prefix': 'distributed-states/'
                    },
                    'Expiration': {
                        'Days': 7  # 7일 후 자동 삭제
                    },
                    'AbortIncompleteMultipartUpload': {
                        'DaysAfterInitiation': 1
                    }
                },
                {
                    'ID': 'WorkflowChunksCleanup',
                    'Status': 'Enabled',
                    'Filter': {
                        'Prefix': 'distributed-chunks/'
                    },
                    'Expiration': {
                        'Days': 3  # 청크 데이터는 3일 후 삭제
                    }
                },
                {
                    'ID': 'WorkflowStatesCleanup',
                    'Status': 'Enabled',
                    'Filter': {
                        'Prefix': 'workflow-states/'
                    },
                    'Expiration': {
                        'Days': 14  # 기존 워크플로우 상태는 14일 보관
                    }
                },
                {
                    'ID': 'FinalStatesLongTermRetention',
                    'Status': 'Enabled',
                    'Filter': {
                        'Prefix': 'final-states/'
                    },
                    'Transitions': [
                        {
                            'Days': 30,
                            'StorageClass': 'STANDARD_IA'  # 30일 후 IA로 이동
                        },
                        {
                            'Days': 90,
                            'StorageClass': 'GLACIER'  # 90일 후 Glacier로 이동
                        }
                    ]
                }
            ]
        }
        
        s3_client.put_bucket_lifecycle_configuration(
            Bucket=bucket_name,
            LifecycleConfiguration=lifecycle_config
        )
        
        logger.info(f"S3 Lifecycle policy configured for bucket: {bucket_name}")
        
    except Exception as e:
        logger.warning(f"Failed to setup S3 lifecycle policy: {e}")
        # 정책 설정 실패는 치명적이지 않음
    """
    집계 결과의 유효성을 검증
    """
    validation = {
        'is_valid': True,
        'warnings': [],
        'recommendations': []
    }
    
    # 상태 크기 검증
    try:
        state_size = len(json.dumps(aggregated_state, ensure_ascii=False).encode('utf-8'))
        if state_size > 200000:  # 200KB
            validation['warnings'].append(f"Large aggregated state: {state_size} bytes")
            validation['recommendations'].append("Consider using S3 storage for large states")
    except Exception as e:
        validation['warnings'].append(f"Failed to calculate state size: {e}")
    
    # 청크 결과 검증
    chunks = aggregated_state.get('chunks', {})
    if len(chunks) == 0:
        validation['warnings'].append("No chunk results found in aggregated state")
    
    return validation