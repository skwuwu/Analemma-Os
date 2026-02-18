# -*- coding: utf-8 -*-
"""
[Phase 4] S3 Select 최적화 서비스

핵심 기능:
1. CloudWatch 기반 동적 임계값 튜닝 (레이턴시 지터 대응)
2. 필드별 선택적 로딩 (네트워크 비용 80% 절감)
3. GetObject vs S3 Select 자동 선택
"""

import time
import logging
from typing import Dict, List, Optional, Any, Set
from datetime import datetime, timedelta
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# 초기 임계값 (CloudWatch 메트릭 기반으로 동적 조정)
DEFAULT_SIZE_THRESHOLD = 10 * 1024  # 10KB
DEFAULT_FIELD_RATIO_THRESHOLD = 0.2  # 20% 이하 필드만 필요하면 S3 Select


@dataclass
class S3SelectMetrics:
    """S3 Select 성능 메트릭"""
    avg_select_latency_ms: float
    avg_getobject_latency_ms: float
    select_success_rate: float
    recommended_threshold_kb: int
    last_updated: datetime


class S3SelectOptimizer:
    """
    S3 Select 동적 최적화 서비스
    
    피드백 반영:
    - S3 Select 쿼리 파싱 오버헤드는 데이터 양에 따라 변동
    - 작은 객체(<10KB)는 GetObject가 더 빠를 수 있음
    - CloudWatch 메트릭 기반 동적 조정 필요
    """
    
    def __init__(
        self,
        s3_client=None,
        cloudwatch_client=None,
        enable_dynamic_tuning: bool = True
    ):
        self.s3 = s3_client or boto3.client('s3')
        self.cloudwatch = cloudwatch_client or boto3.client('cloudwatch')
        self.enable_dynamic_tuning = enable_dynamic_tuning
        
        # 현재 임계값 (동적 조정됨)
        self.size_threshold = DEFAULT_SIZE_THRESHOLD
        self.field_ratio_threshold = DEFAULT_FIELD_RATIO_THRESHOLD
        
        # 성능 메트릭 캐시 (5분 TTL)
        self._metrics_cache: Optional[S3SelectMetrics] = None
        self._metrics_cache_expiry = 0
    
    def should_use_s3_select(
        self,
        object_size: int,
        total_fields: int,
        required_fields: int
    ) -> bool:
        """
        S3 Select 사용 여부 결정
        
        Decision Tree:
        1. object_size < threshold → GetObject (빠름)
        2. required_fields / total_fields > 80% → GetObject (대부분 필요)
        3. required_fields / total_fields < 20% → S3 Select (일부만 필요)
        4. 나머지 → CloudWatch 메트릭 기반 판단
        
        Args:
            object_size: S3 객체 크기 (bytes)
            total_fields: 전체 필드 수
            required_fields: 필요한 필드 수
        
        Returns:
            True면 S3 Select 사용
        """
        # 1. 작은 객체는 무조건 GetObject
        if object_size < self.size_threshold:
            logger.debug(
                f"[S3SelectOptimizer] GetObject: size={object_size}B < threshold={self.size_threshold}B"
            )
            return False
        
        # 2. 필드 비율 계산
        field_ratio = required_fields / max(total_fields, 1)
        
        # 대부분 필드 필요 → GetObject
        if field_ratio > 0.8:
            logger.debug(
                f"[S3SelectOptimizer] GetObject: field_ratio={field_ratio:.2f} > 0.8 "
                f"({required_fields}/{total_fields})"
            )
            return False
        
        # 일부 필드만 필요 → S3 Select
        if field_ratio < self.field_ratio_threshold:
            logger.debug(
                f"[S3SelectOptimizer] S3 Select: field_ratio={field_ratio:.2f} < {self.field_ratio_threshold} "
                f"({required_fields}/{total_fields})"
            )
            return True
        
        # 3. 동적 튜닝: CloudWatch 메트릭 기반 판단
        if self.enable_dynamic_tuning:
            metrics = self._get_or_refresh_metrics()
            
            if metrics:
                # S3 Select가 2배 이상 느리면 GetObject 선호
                if metrics.avg_select_latency_ms > metrics.avg_getobject_latency_ms * 2:
                    logger.info(
                        f"[S3SelectOptimizer] GetObject: Select latency too high "
                        f"({metrics.avg_select_latency_ms:.1f}ms vs {metrics.avg_getobject_latency_ms:.1f}ms)"
                    )
                    return False
        
        # 기본값: S3 Select 사용
        logger.debug(
            f"[S3SelectOptimizer] S3 Select: default choice for size={object_size}B, "
            f"field_ratio={field_ratio:.2f}"
        )
        return True
    
    def load_with_s3_select(
        self,
        bucket: str,
        key: str,
        fields_to_extract: Set[str]
    ) -> Dict[str, Any]:
        """
        S3 Select로 필드별 선택적 로딩
        
        Args:
            bucket: S3 버킷
            key: S3 키
            fields_to_extract: 추출할 필드명 집합
        
        Returns:
            추출된 데이터 딕셔너리
        """
        start_time = time.time()
        
        try:
            # SQL 쿼리 생성: SELECT s.field1, s.field2 FROM S3Object[*] s
            select_fields = ", ".join([f"s.{field}" for field in fields_to_extract])
            sql_query = f"SELECT {select_fields} FROM S3Object[*] s"
            
            logger.info(
                f"[S3 Select] Query: {sql_query[:100]}... "
                f"(extracting {len(fields_to_extract)} fields)"
            )
            
            response = self.s3.select_object_content(
                Bucket=bucket,
                Key=key,
                ExpressionType='SQL',
                Expression=sql_query,
                InputSerialization={'JSON': {'Type': 'DOCUMENT'}},
                OutputSerialization={'JSON': {'RecordDelimiter': '\n'}}
            )
            
            # 스트림에서 결과 수집
            result_data = {}
            for event in response['Payload']:
                if 'Records' in event:
                    records = event['Records']['Payload'].decode('utf-8')
                    for line in records.strip().split('\n'):
                        if line:
                            import json
                            result_data.update(json.loads(line))
            
            latency_ms = (time.time() - start_time) * 1000
            
            # CloudWatch 메트릭 기록
            self._record_metric('S3Select', latency_ms, True)
            
            logger.info(
                f"[S3 Select] Success: {len(result_data)} fields loaded in {latency_ms:.1f}ms"
            )
            
            return result_data
            
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            
            # 실패 메트릭 기록
            self._record_metric('S3Select', latency_ms, False)
            
            logger.error(f"[S3 Select] Failed after {latency_ms:.1f}ms: {e}")
            raise
    
    def load_with_getobject(
        self,
        bucket: str,
        key: str,
        fields_to_extract: Optional[Set[str]] = None
    ) -> Dict[str, Any]:
        """
        GetObject로 전체 로딩 후 필터링
        
        Args:
            bucket: S3 버킷
            key: S3 키
            fields_to_extract: 추출할 필드명 (필터링용, None이면 전체)
        
        Returns:
            로드된 데이터 딕셔너리
        """
        start_time = time.time()
        
        try:
            response = self.s3.get_object(Bucket=bucket, Key=key)
            content = response['Body'].read().decode('utf-8')
            
            import json
            data = json.loads(content)
            
            # 필드 필터링
            if fields_to_extract:
                data = {k: v for k, v in data.items() if k in fields_to_extract}
            
            latency_ms = (time.time() - start_time) * 1000
            
            # CloudWatch 메트릭 기록
            self._record_metric('GetObject', latency_ms, True)
            
            logger.info(
                f"[GetObject] Success: {len(data)} fields loaded in {latency_ms:.1f}ms"
            )
            
            return data
            
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            
            # 실패 메트릭 기록
            self._record_metric('GetObject', latency_ms, False)
            
            logger.error(f"[GetObject] Failed after {latency_ms:.1f}ms: {e}")
            raise
    
    def smart_load(
        self,
        bucket: str,
        key: str,
        fields_to_extract: Optional[Set[str]] = None
    ) -> Dict[str, Any]:
        """
        자동 최적화 로딩 (GetObject vs S3 Select 선택)
        
        Args:
            bucket: S3 버킷
            key: S3 키
            fields_to_extract: 추출할 필드명 (None이면 전체)
        
        Returns:
            로드된 데이터
        """
        # 객체 크기 확인
        try:
            head = self.s3.head_object(Bucket=bucket, Key=key)
            object_size = head['ContentLength']
        except Exception as e:
            logger.warning(f"Failed to get object size: {e}, using GetObject")
            return self.load_with_getobject(bucket, key, fields_to_extract)
        
        # 전체 로드면 무조건 GetObject
        if not fields_to_extract:
            return self.load_with_getobject(bucket, key, None)
        
        # 전체 필드 수 추정 (메타데이터에서)
        total_fields = head.get('Metadata', {}).get('total_fields', 100)
        if isinstance(total_fields, str):
            total_fields = int(total_fields)
        
        # S3 Select 사용 여부 결정
        use_select = self.should_use_s3_select(
            object_size=object_size,
            total_fields=total_fields,
            required_fields=len(fields_to_extract)
        )
        
        if use_select:
            try:
                return self.load_with_s3_select(bucket, key, fields_to_extract)
            except Exception as e:
                logger.warning(f"S3 Select failed, falling back to GetObject: {e}")
                return self.load_with_getobject(bucket, key, fields_to_extract)
        else:
            return self.load_with_getobject(bucket, key, fields_to_extract)
    
    def _get_or_refresh_metrics(self) -> Optional[S3SelectMetrics]:
        """
        CloudWatch 메트릭 캐시 조회 또는 갱신
        
        Returns:
            성능 메트릭 (실패 시 None)
        """
        now = time.time()
        
        # 캐시 유효하면 반환
        if self._metrics_cache and now < self._metrics_cache_expiry:
            return self._metrics_cache
        
        # CloudWatch에서 최근 1일 메트릭 조회
        try:
            end_time = datetime.utcnow()
            start_time = end_time - timedelta(days=1)
            
            # S3 Select 평균 레이턴시
            select_response = self.cloudwatch.get_metric_statistics(
                Namespace='Analemma/S3',
                MetricName='S3SelectLatency',
                Dimensions=[],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1시간 단위
                Statistics=['Average']
            )
            
            # GetObject 평균 레이턴시
            getobject_response = self.cloudwatch.get_metric_statistics(
                Namespace='Analemma/S3',
                MetricName='GetObjectLatency',
                Dimensions=[],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Average']
            )
            
            # 평균 계산
            select_latencies = [dp['Average'] for dp in select_response.get('Datapoints', [])]
            getobject_latencies = [dp['Average'] for dp in getobject_response.get('Datapoints', [])]
            
            if not select_latencies or not getobject_latencies:
                logger.debug("No CloudWatch metrics available yet")
                return None
            
            avg_select = sum(select_latencies) / len(select_latencies)
            avg_getobject = sum(getobject_latencies) / len(getobject_latencies)
            
            # 추천 임계값 계산
            # S3 Select가 2배 이상 느리면 임계값 상향 (50KB)
            if avg_select > avg_getobject * 2:
                recommended_threshold = 50 * 1024
            else:
                recommended_threshold = 10 * 1024
            
            # 메트릭 캐시 업데이트
            self._metrics_cache = S3SelectMetrics(
                avg_select_latency_ms=avg_select,
                avg_getobject_latency_ms=avg_getobject,
                select_success_rate=1.0,  # TODO: 성공률 메트릭 추가
                recommended_threshold_kb=recommended_threshold // 1024,
                last_updated=datetime.utcnow()
            )
            
            # 임계값 자동 조정
            self.size_threshold = recommended_threshold
            
            # 캐시 만료 시간 설정 (5분)
            self._metrics_cache_expiry = now + 300
            
            logger.info(
                f"[CloudWatch] Metrics updated: Select={avg_select:.1f}ms, "
                f"GetObject={avg_getobject:.1f}ms, "
                f"New threshold={recommended_threshold // 1024}KB"
            )
            
            return self._metrics_cache
            
        except Exception as e:
            logger.warning(f"Failed to fetch CloudWatch metrics: {e}")
            return None
    
    def _record_metric(self, operation: str, latency_ms: float, success: bool):
        """
        CloudWatch에 성능 메트릭 기록
        
        Args:
            operation: 'S3Select' 또는 'GetObject'
            latency_ms: 레이턴시 (밀리초)
            success: 성공 여부
        """
        try:
            metric_name = f"{operation}Latency"
            
            self.cloudwatch.put_metric_data(
                Namespace='Analemma/S3',
                MetricData=[
                    {
                        'MetricName': metric_name,
                        'Value': latency_ms,
                        'Unit': 'Milliseconds',
                        'Timestamp': datetime.utcnow()
                    },
                    {
                        'MetricName': f"{operation}Success",
                        'Value': 1.0 if success else 0.0,
                        'Unit': 'None',
                        'Timestamp': datetime.utcnow()
                    }
                ]
            )
            
            logger.debug(f"[CloudWatch] Recorded {metric_name}={latency_ms:.1f}ms, success={success}")
            
        except Exception as e:
            # 메트릭 기록 실패는 critical하지 않음
            logger.debug(f"Failed to record CloudWatch metric: {e}")


# Singleton 인스턴스
_optimizer = S3SelectOptimizer()


def smart_load_from_s3(
    bucket: str,
    key: str,
    fields: Optional[Set[str]] = None
) -> Dict[str, Any]:
    """
    S3에서 데이터 스마트 로딩 (편의 함수)
    
    Args:
        bucket: S3 버킷
        key: S3 키
        fields: 필요한 필드명 집합 (None이면 전체)
    
    Returns:
        로드된 데이터
    """
    return _optimizer.smart_load(bucket, key, fields)
