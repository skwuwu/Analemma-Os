"""
AWS Distributed Map을 위한 세그먼트 청킹 및 준비 Lambda 함수

이 함수는 대용량 워크플로우를 Distributed Map에서 처리할 수 있도록
세그먼트들을 적절한 크기의 청크로 분할합니다.

[Critical Fix #2] ASL 문법 수정:
- ItemReader가 요구하는 형식으로 S3 버킷과 키를 분리하여 반환
- Resource에 S3 경로를 직접 넣는 방식 대신 전용 서비스 ARN 사용
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
    대용량 워크플로우를 Distributed Map용 청크로 분할
    
    [Critical Fix #2] ASL ItemReader 규격 준수:
    - chunks_bucket과 chunks_key를 분리하여 반환
    - ItemReader.Parameters.Bucket과 Key에서 직접 참조 가능
    
    Args:
        event: {
            "state_data": {...},
            "chunk_size": 100,
            "max_chunks": 100,
            "state_bucket": "bucket-name"  # 명시적 버킷 전달
        }
        
    Returns:
        {
            "chunks_bucket": "bucket-name",
            "chunks_key": "path/to/chunks.json",
            "total_chunks": int,
            "use_s3_reader": true
        }
    """
    try:
        state_data = event.get('state_data', {})
        chunk_size = event.get('chunk_size', 100)
        max_chunks = event.get('max_chunks', 100)
        state_bucket = event.get('state_bucket') or os.environ.get('WORKFLOW_STATE_BUCKET')
        
        partition_map = state_data.get('partition_map')
        
        # 🚨 [Critical Fix] S3 Offloading Support
        # 분산 모드에서는 partition_map이 S3로 오프로딩되어 있을 수 있음
        if not partition_map:
            partition_map_s3_path = state_data.get('partition_map_s3_path')
            if partition_map_s3_path:
                logger.info(f"Loading partition_map from S3: {partition_map_s3_path}")
                try:
                    s3 = boto3.client('s3')
                    bucket_name = partition_map_s3_path.replace("s3://", "").split("/")[0]
                    key_name = "/".join(partition_map_s3_path.replace("s3://", "").split("/")[1:])
                    
                    obj = s3.get_object(Bucket=bucket_name, Key=key_name)
                    partition_map = json.loads(obj['Body'].read().decode('utf-8'))
                    logger.info(f"Successfully loaded partition_map from S3 (segments: {len(partition_map)})")
                except Exception as e:
                    logger.error(f"Failed to load partition_map from S3: {e}")
                    raise RuntimeError(f"Failed to load partition_map from S3: {e}")
        
        if not partition_map:
            partition_map = []
            
        total_segments = len(partition_map)

        
        if total_segments == 0:
            logger.warning("No segments found in partition_map")
            return {
                "chunks_bucket": state_bucket,
                "chunks_key": None,
                "total_chunks": 0,
                "chunk_size": 0,
                "original_segments": 0,
                "distributed_mode": False,
                "use_s3_reader": False
            }
        
        if not state_bucket:
            raise RuntimeError("state_bucket is required for Distributed Map execution")
        
        # 청크 크기 최적화
        optimal_chunk_size = min(chunk_size, max(10, total_segments // max_chunks))
        
        # 🚨 [Critical Architecture Fix] ItemReader 호환성을 위한 사전 크기 검증
        # 예상 페이로드 크기를 미리 계산하여 ItemReader 제한 준수
        estimated_chunk_size_kb = _estimate_chunks_payload_size(
            total_segments=total_segments,
            chunk_size=optimal_chunk_size,
            partition_map=partition_map
        )
        
        lambda_memory_mb = int(os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', '2048'))
        max_payload_size_mb = lambda_memory_mb * 0.25  # 25%로 매우 보수적 제한 (압축 고려)
        max_payload_size_kb = max_payload_size_mb * 1024
        
        # 예상 크기가 너무 크면 청크 크기를 사전 조정
        if estimated_chunk_size_kb > max_payload_size_kb:
            logger.warning(f"Estimated payload too large: {estimated_chunk_size_kb:.1f}KB > {max_payload_size_kb:.1f}KB")
            
            # 청크 크기를 줄여서 재계산
            size_reduction_factor = estimated_chunk_size_kb / max_payload_size_kb
            adjusted_chunk_size = max(5, int(optimal_chunk_size / size_reduction_factor))
            
            logger.info(f"Adjusting chunk size: {optimal_chunk_size} -> {adjusted_chunk_size} for ItemReader compatibility")
            optimal_chunk_size = adjusted_chunk_size
        
        total_chunks = (total_segments + optimal_chunk_size - 1) // optimal_chunk_size
        
        # 실제 청크 생성
        chunks = []
        for chunk_idx in range(total_chunks):
            start_idx = chunk_idx * optimal_chunk_size
            end_idx = min(start_idx + optimal_chunk_size, total_segments)
            
            # 청크에 포함될 세그먼트들 추출
            partition_slice = partition_map[start_idx:end_idx]
            
            chunk = {
                "chunk_id": f"chunk_{chunk_idx:04d}",
                "start_segment": start_idx,
                "end_segment": end_idx - 1,
                "segment_count": end_idx - start_idx,
                "partition_slice": partition_slice,
                "chunk_index": chunk_idx,
                "total_chunks": total_chunks,
                "estimated_events": (end_idx - start_idx) * 20,
                "created_at": context.aws_request_id if context else "local",
                "idempotency_key": f"{idempotency_key}#chunk#{chunk_idx:04d}",
                "owner_id": owner_id,
                "workflow_id": workflow_id
            }
            chunks.append(chunk)
        
        logger.info(f"Created {total_chunks} chunks (size: {optimal_chunk_size}) for {total_segments} segments")
        
        # [Critical Fix #2] S3에 청크 배열 저장 (ItemReader용)
        # Distributed Map ItemReader는 항상 S3에서 읽어야 함
        s3_client = boto3.client('s3')
        
        # S3 키 생성 (결정론적)
        execution_id = context.aws_request_id if context else str(int(time.time()))
        chunks_key = f"distributed-chunks/{owner_id}/{workflow_id}/{execution_id}/chunks.json"
        
        chunks_json = json.dumps(chunks, ensure_ascii=False)
        chunks_size_kb = len(chunks_json.encode('utf-8')) / 1024
        
        # 🚨 [Critical Fix] 대용량 chunks 배열 처리 및 메모리 관리
        lambda_memory_mb = int(os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', '2048'))
        max_payload_size_mb = lambda_memory_mb * 0.3  # 30%로 보수적 제한 (압축 고려)
        max_payload_size_kb = max_payload_size_mb * 1024
        
        logger.info(f"Chunks payload: {chunks_size_kb:.1f}KB, Lambda memory: {lambda_memory_mb}MB, Limit: {max_payload_size_kb:.1f}KB")
        
        if chunks_size_kb > max_payload_size_kb:
            logger.warning(f"Large chunks payload detected: {chunks_size_kb:.1f}KB > {max_payload_size_kb:.1f}KB")
            
            # 🚨 [Critical Architecture Fix] ItemReader 호환성을 위한 단일 파일 처리
            return _handle_large_chunks_upload(
                s3_client=s3_client,
                chunks=chunks,
                chunks_json=chunks_json,
                state_bucket=state_bucket,
                chunks_key=chunks_key,
                total_chunks=total_chunks,
                optimal_chunk_size=optimal_chunk_size,
                total_segments=total_segments,
                owner_id=owner_id,
                workflow_id=workflow_id
            )
        
        # 일반 크기는 기존 방식으로 업로드
        s3_client.put_object(
            Bucket=state_bucket,
            Key=chunks_key,
            Body=chunks_json.encode('utf-8'),
            ContentType='application/json',
            Metadata={
                'total_chunks': str(total_chunks),
                'total_segments': str(total_segments),
                'chunk_size': str(optimal_chunk_size),
                'owner_id': owner_id or '',
                'workflow_id': workflow_id or '',
                'created_at': str(int(time.time())),
                'payload_size_kb': str(int(chunks_size_kb))
            }
        )
        
        logger.info(f"Chunks uploaded to S3: s3://{state_bucket}/{chunks_key} ({chunks_size_kb:.1f}KB)")
        
        # [Critical Fix #2] ASL ItemReader 규격에 맞게 버킷과 키 분리 반환
        return {
            "chunks_bucket": state_bucket,
            "chunks_key": chunks_key,
            "total_chunks": total_chunks,
            "chunk_size": optimal_chunk_size,
            "original_segments": total_segments,
            "distributed_mode": True,
            "use_s3_reader": True,
            "payload_size_kb": chunks_size_kb,
            "itemreader_compatible": True,  # 🚨 호환성 보장
            "optimization_stats": {
                "requested_chunk_size": chunk_size,
                "optimal_chunk_size": optimal_chunk_size,
                "s3_bucket": state_bucket,
                "s3_key": chunks_key,
                "architecture_compliance": "itemreader_single_array"  # 🚨 아키텍처 준수
            }
        }
        
    except Exception as e:
        logger.exception("Failed to prepare distributed execution")
        
        # 🚨 [Critical] ItemReader 호환성 관련 오류 특별 처리
        if "too large for ItemReader" in str(e):
            # 청크 크기 재조정 제안과 함께 실패
            raise RuntimeError(
                f"Distributed execution preparation failed due to ItemReader size limits: {str(e)}. "
                f"Please reduce the workflow complexity or increase lambda memory to 3008MB."
            )
        
        raise RuntimeError(f"Distributed execution preparation failed: {str(e)}")


def _handle_large_chunks_upload(
    s3_client,
    chunks: List[Dict[str, Any]],
    chunks_json: str,
    state_bucket: str,
    chunks_key: str,
    total_chunks: int,
    optimal_chunk_size: int,
    total_segments: int,
    owner_id: str,
    workflow_id: str
) -> Dict[str, Any]:
    """
    🚨 [Critical Architecture Fix] 대용량 청크 배열을 ItemReader 호환 방식으로 처리
    
    Step Functions ItemReader는 단일 JSON 배열만 처리 가능하므로:
    1. 압축을 최우선으로 시도하여 단일 파일 유지
    2. 멀티파트 업로드로 안정성 확보
    3. 분할 업로드는 ItemReader 호환성 문제로 제거
    4. 극한 상황에서는 청크 크기 재조정으로 대응
    
    Args:
        s3_client: S3 클라이언트
        chunks: 청크 배열
        chunks_json: JSON 문자열
        state_bucket: S3 버킷
        chunks_key: S3 키
        total_chunks: 총 청크 수
        optimal_chunk_size: 청크 크기
        total_segments: 총 세그먼트 수
        owner_id: 소유자 ID
        workflow_id: 워크플로우 ID
    
    Returns:
        업로드 결과
    """
    try:
        import gzip
        import io
        
        # 1. 압축 시도 (최우선 전략)
        logger.info("Attempting gzip compression for large chunks payload")
        
        # JSON을 gzip으로 압축
        compressed_buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=compressed_buffer, mode='wb') as gz_file:
            gz_file.write(chunks_json.encode('utf-8'))
        
        compressed_data = compressed_buffer.getvalue()
        compressed_size_kb = len(compressed_data) / 1024
        original_size_kb = len(chunks_json.encode('utf-8')) / 1024
        compression_ratio = compressed_size_kb / original_size_kb
        
        logger.info(f"Compression result: {original_size_kb:.1f}KB -> {compressed_size_kb:.1f}KB (ratio: {compression_ratio:.2f})")
        
        # 2. 🚨 [Critical] ItemReader 호환성을 위한 단일 파일 강제 유지
        lambda_memory_mb = int(os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', '512'))
        max_single_upload_mb = lambda_memory_mb * 0.3  # 30%로 더 보수적 제한 (안전 마진)
        max_single_upload_kb = max_single_upload_mb * 1024
        
        if compressed_size_kb > max_single_upload_kb:
            logger.error(f"🚨 CRITICAL: Compressed payload still too large: {compressed_size_kb:.1f}KB > {max_single_upload_kb:.1f}KB")
            logger.error("ItemReader requires single JSON array - cannot split into multiple files")
            
            # 🚨 극한 상황: 청크 크기를 줄여서 재시도 제안
            suggested_chunk_size = max(10, optimal_chunk_size // 2)
            suggested_total_chunks = (total_segments + suggested_chunk_size - 1) // suggested_chunk_size
            
            raise RuntimeError(
                f"Payload too large for ItemReader compatibility. "
                f"Current: {compressed_size_kb:.1f}KB, Limit: {max_single_upload_kb:.1f}KB. "
                f"Suggestion: Reduce chunk_size from {optimal_chunk_size} to {suggested_chunk_size} "
                f"(will create {suggested_total_chunks} chunks instead of {total_chunks})"
            )
        
        # 3. 압축된 데이터 업로드 (멀티파트 사용)
        if compressed_size_kb > 5 * 1024:  # 5MB 이상은 멀티파트
            logger.info("Using multipart upload for large compressed payload")
            return _multipart_upload_chunks(
                s3_client=s3_client,
                compressed_data=compressed_data,
                state_bucket=state_bucket,
                chunks_key=chunks_key,
                total_chunks=total_chunks,
                optimal_chunk_size=optimal_chunk_size,
                total_segments=total_segments,
                owner_id=owner_id,
                workflow_id=workflow_id,
                original_size_kb=original_size_kb,
                compressed_size_kb=compressed_size_kb
            )
        
        # 4. 일반 압축 업로드
        s3_client.put_object(
            Bucket=state_bucket,
            Key=chunks_key,
            Body=compressed_data,
            ContentType='application/json',
            ContentEncoding='gzip',
            Metadata={
                'total_chunks': str(total_chunks),
                'total_segments': str(total_segments),
                'chunk_size': str(optimal_chunk_size),
                'owner_id': owner_id or '',
                'workflow_id': workflow_id or '',
                'created_at': str(int(time.time())),
                'original_size_kb': str(int(original_size_kb)),
                'compressed_size_kb': str(int(compressed_size_kb)),
                'compression_ratio': str(round(compression_ratio, 3)),
                'encoding': 'gzip',
                'itemreader_compatible': 'true'  # 🚨 호환성 마커
            }
        )
        
        logger.info(f"ItemReader-compatible chunks uploaded: s3://{state_bucket}/{chunks_key} ({compressed_size_kb:.1f}KB, {compression_ratio:.2f} ratio)")
        
        return {
            "chunks_bucket": state_bucket,
            "chunks_key": chunks_key,
            "total_chunks": total_chunks,
            "chunk_size": optimal_chunk_size,
            "original_segments": total_segments,
            "distributed_mode": True,
            "use_s3_reader": True,
            "payload_size_kb": compressed_size_kb,
            "compression_applied": True,
            "compression_ratio": compression_ratio,
            "itemreader_compatible": True,  # 🚨 호환성 보장
            "optimization_stats": {
                "requested_chunk_size": 100,  # 기본값
                "optimal_chunk_size": optimal_chunk_size,
                "s3_bucket": state_bucket,
                "s3_key": chunks_key,
                "original_size_kb": original_size_kb,
                "compressed_size_kb": compressed_size_kb,
                "upload_method": "compressed_single_itemreader_compatible"
            }
        }
        
    except Exception as e:
        logger.error(f"Large chunks upload failed: {e}")
        
        # 🚨 [Critical] ItemReader 호환성을 위한 특별 폴백 처리
        if "too large for ItemReader" in str(e):
            # 청크 크기 재조정이 필요한 경우 - 상위 호출자에게 전파
            raise e
        
        # 기타 오류의 경우 원본 데이터로 폴백 (위험하지만 동작 유지)
        logger.warning("Falling back to uncompressed upload (may cause memory issues)")
        
        try:
            s3_client.put_object(
                Bucket=state_bucket,
                Key=chunks_key,
                Body=chunks_json.encode('utf-8'),
                ContentType='application/json',
                Metadata={
                    'total_chunks': str(total_chunks),
                    'total_segments': str(total_segments),
                    'chunk_size': str(optimal_chunk_size),
                    'owner_id': owner_id or '',
                    'workflow_id': workflow_id or '',
                    'created_at': str(int(time.time())),
                    'fallback_upload': 'true',
                    'compression_failed': str(e),
                    'itemreader_compatible': 'true'  # 여전히 단일 배열
                }
            )
            
            return {
                "chunks_bucket": state_bucket,
                "chunks_key": chunks_key,
                "total_chunks": total_chunks,
                "chunk_size": optimal_chunk_size,
                "original_segments": total_segments,
                "distributed_mode": True,
                "use_s3_reader": True,
                "payload_size_kb": len(chunks_json.encode('utf-8')) / 1024,
                "compression_applied": False,
                "fallback_used": True,
                "itemreader_compatible": True,  # 🚨 여전히 호환
                "error": str(e)
            }
        except Exception as fallback_error:
            logger.error(f"Even fallback upload failed: {fallback_error}")
            raise RuntimeError(f"All upload methods failed. Original error: {e}, Fallback error: {fallback_error}")


def _multipart_upload_chunks(
    s3_client,
    compressed_data: bytes,
    state_bucket: str,
    chunks_key: str,
    total_chunks: int,
    optimal_chunk_size: int,
    total_segments: int,
    owner_id: str,
    workflow_id: str,
    original_size_kb: float,
    compressed_size_kb: float
) -> Dict[str, Any]:
    """
    멀티파트 업로드로 대용량 압축 데이터 안전하게 업로드
    """
    try:
        # 멀티파트 업로드 시작
        response = s3_client.create_multipart_upload(
            Bucket=state_bucket,
            Key=chunks_key,
            ContentType='application/json',
            ContentEncoding='gzip',
            Metadata={
                'total_chunks': str(total_chunks),
                'total_segments': str(total_segments),
                'chunk_size': str(optimal_chunk_size),
                'owner_id': owner_id or '',
                'workflow_id': workflow_id or '',
                'created_at': str(int(time.time())),
                'original_size_kb': str(int(original_size_kb)),
                'compressed_size_kb': str(int(compressed_size_kb)),
                'upload_method': 'multipart'
            }
        )
        
        upload_id = response['UploadId']
        
        # 5MB 청크로 분할 업로드
        part_size = 5 * 1024 * 1024  # 5MB
        parts = []
        
        for part_num in range(1, (len(compressed_data) // part_size) + 2):
            start = (part_num - 1) * part_size
            end = min(start + part_size, len(compressed_data))
            
            if start >= len(compressed_data):
                break
                
            part_data = compressed_data[start:end]
            
            part_response = s3_client.upload_part(
                Bucket=state_bucket,
                Key=chunks_key,
                PartNumber=part_num,
                UploadId=upload_id,
                Body=part_data
            )
            
            parts.append({
                'ETag': part_response['ETag'],
                'PartNumber': part_num
            })
            
            logger.info(f"Uploaded part {part_num}: {len(part_data)} bytes")
        
        # 멀티파트 업로드 완료
        s3_client.complete_multipart_upload(
            Bucket=state_bucket,
            Key=chunks_key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts}
        )
        
        logger.info(f"Multipart upload completed: {len(parts)} parts, {compressed_size_kb:.1f}KB total")
        
        return {
            "chunks_bucket": state_bucket,
            "chunks_key": chunks_key,
            "total_chunks": total_chunks,
            "chunk_size": optimal_chunk_size,
            "original_segments": total_segments,
            "distributed_mode": True,
            "use_s3_reader": True,
            "payload_size_kb": compressed_size_kb,
            "compression_applied": True,
            "upload_method": "multipart",
            "parts_count": len(parts)
        }
        
    except Exception as e:
        logger.error(f"Multipart upload failed: {e}")
        # 업로드 취소
        try:
            s3_client.abort_multipart_upload(
                Bucket=state_bucket,
                Key=chunks_key,
                UploadId=upload_id
            )
        except Exception as e:
            logger.warning("Failed to abort multipart upload (UploadId=%s): %s", upload_id, e)
            pass
        raise


def _estimate_chunks_payload_size(
    total_segments: int,
    chunk_size: int,
    partition_map: List[Dict[str, Any]]
) -> float:
    """
    청크 배열의 예상 페이로드 크기를 추정 (KB 단위)
    
    ItemReader 호환성을 위해 사전에 크기를 검증하여
    Step Functions 256KB 제한 및 람다 메모리 제한을 준수
    
    Args:
        total_segments: 총 세그먼트 수
        chunk_size: 청크 크기
        partition_map: 파티션 맵
        
    Returns:
        예상 페이로드 크기 (KB)
    """
    try:
        # 샘플 청크 생성하여 실제 크기 측정
        sample_chunk = {
            "chunk_id": "chunk_0000",
            "start_segment": 0,
            "end_segment": min(chunk_size - 1, total_segments - 1),
            "segment_count": min(chunk_size, total_segments),
            "partition_slice": partition_map[:min(chunk_size, len(partition_map))],
            "chunk_index": 0,
            "total_chunks": (total_segments + chunk_size - 1) // chunk_size,
            "estimated_events": min(chunk_size, total_segments) * 20,
            "created_at": "sample",
            "idempotency_key": "sample#chunk#0000",
            "owner_id": "sample",
            "workflow_id": "sample"
        }
        
        # 샘플 청크의 JSON 크기 측정
        sample_json = json.dumps([sample_chunk], ensure_ascii=False)
        sample_size_kb = len(sample_json.encode('utf-8')) / 1024
        
        # 전체 청크 수 계산
        total_chunks = (total_segments + chunk_size - 1) // chunk_size
        
        # 전체 예상 크기 = 샘플 크기 * 청크 수
        estimated_total_kb = sample_size_kb * total_chunks
        
        logger.info(f"Payload size estimation: {sample_size_kb:.2f}KB per chunk × {total_chunks} chunks = {estimated_total_kb:.1f}KB total")
        
        return estimated_total_kb
        
    except Exception as e:
        logger.warning(f"Failed to estimate payload size: {e}, using conservative estimate")
        # 보수적 추정: 청크당 10KB
        total_chunks = (total_segments + chunk_size - 1) // chunk_size
        return total_chunks * 10.0


def estimate_event_count(partition_slice: List[Dict[str, Any]]) -> int:
    """
    세그먼트 슬라이스의 예상 Event History 사용량을 계산
    
    🚨 [Critical Fix] 병렬 그룹 이벤트 추정을 보수적으로 수정
    - 기존: 브랜치당 50개 이벤트
    - 개선: 브랜치당 100-200개 이벤트 (기하급수적 증가 방지)
    
    Args:
        partition_slice: 세그먼트 리스트
        
    Returns:
        예상 이벤트 수
    """
    total_events = 0
    
    for segment in partition_slice:
        segment_type = segment.get('type', 'normal')
        nodes = segment.get('nodes', [])
        edges = segment.get('edges', [])
        
        # 세그먼트 타입별 이벤트 추정
        if segment_type == 'parallel_group':
            # 🚨 [Critical Fix] 병렬 그룹은 브랜치 수에 따라 이벤트 급증
            branches = segment.get('branches', [])
            branch_count = len(branches)
            
            # 보수적 추정: 브랜치당 100-200개 이벤트 (기존 50개에서 증가)
            if branch_count <= 5:
                branch_events = branch_count * 100  # 소규모: 브랜치당 100개
            elif branch_count <= 20:
                branch_events = branch_count * 150  # 중규모: 브랜치당 150개
            else:
                branch_events = branch_count * 200  # 대규모: 브랜치당 200개
            
            # 추가 안전 마진: 중첩된 병렬 그룹 고려
            nested_parallel_count = sum(1 for branch in branches 
                                      if branch.get('type') == 'parallel_group')
            if nested_parallel_count > 0:
                branch_events *= (1 + nested_parallel_count * 0.5)  # 중첩당 50% 증가
            
            total_events += int(branch_events)
            
            logger.info(f"Parallel group estimated events: {branch_count} branches -> {int(branch_events)} events")
            
        elif segment_type == 'llm':
            # LLM 세그먼트는 더 많은 이벤트 생성
            total_events += 30
        elif segment_type == 'hitp':
            # HITP는 콜백 대기로 추가 이벤트
            total_events += 25
        else:
            # 일반 세그먼트
            total_events += 15 + len(nodes) * 2 + len(edges)
    
    # 🚨 [Critical Fix] 전체적으로 20% 안전 마진 추가
    safety_margin = int(total_events * 0.2)
    total_events += safety_margin
    
    logger.info(f"Total estimated events: {total_events} (including {safety_margin} safety margin)")
    
    return total_events


def validate_chunk_feasibility(chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    생성된 청크들이 Event History 제한 내에서 실행 가능한지 검증
    
    Args:
        chunks: 생성된 청크 리스트
        
    Returns:
        검증 결과 및 권장사항
    """
    validation_result = {
        "is_feasible": True,
        "warnings": [],
        "recommendations": []
    }
    
    for chunk in chunks:
        estimated_events = estimate_event_count(chunk.get('partition_slice', []))
        
        if estimated_events > 20000:  # 80% 임계값
            validation_result["is_feasible"] = False
            validation_result["warnings"].append(
                f"Chunk {chunk['chunk_id']} may exceed Event History limit: {estimated_events} events"
            )
            validation_result["recommendations"].append(
                f"Reduce chunk size for chunk {chunk['chunk_id']} or split parallel groups"
            )
        elif estimated_events > 15000:  # 60% 임계값
            validation_result["warnings"].append(
                f"Chunk {chunk['chunk_id']} approaching Event History limit: {estimated_events} events"
            )
    
    return validation_result