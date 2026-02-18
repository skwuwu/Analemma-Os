# -*- coding: utf-8 -*-
"""
[Phase 5] Async Commit Service - Redis TOCTOU 완화 (S3 Strong Consistency 최적화)

핵심 기능:
1. S3 Strong Consistency 활용 (2020년 말 이후)
2. Exponential Backoff + Jitter (Thundering Herd 방지)
3. Fail-fast 전략: 최대 3회 재시도 (0.1s → 0.4s)

아키텍처 원칙:
- Merkle DAG는 새로운 해시 키로 생성 (덮어쓰기 없음)
- S3 강한 일관성 보장 → 즉시 조회 가능
- 조회 실패는 진짜 시스템 장애 → 빠른 실패 응답
- Jitter로 동시 재시도 부하 분산 (Thundering Herd 방지)
"""

import time
import random
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

try:
    import redis
    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False
    redis = None

logger = logging.getLogger(__name__)

# Exponential Backoff + Jitter 설정
RETRY_ATTEMPTS = 3  # 5 → 3 (Fail-fast: S3 Strong Consistency 활용)
INITIAL_DELAY = 0.1  # 100ms
MAX_DELAY = 0.4  # 400ms (1.6s → 0.4s, 빠른 실패)


@dataclass
class CommitStatus:
    """커밋 상태 정보"""
    is_committed: bool
    s3_available: bool
    redis_status: Optional[str]
    retry_count: int
    total_wait_ms: float


class AsyncCommitService:
    """
    비동기 커밋 서비스 (S3 Strong Consistency 최적화)
    
    AWS S3 강한 일관성 (2020년 말 이후):
    - 모든 새 객체 쓰기는 즉시 읽기 가능
    - Merkle DAG는 새로운 해시 키로 생성 (덮어쓰기 없음)
    - 조회 실패는 시스템 장애 → Fail-fast 전략
    
    Thundering Herd 방지:
    - Exponential Backoff + Jitter (동시 재시도 부하 분산)
    - random.uniform(-10%, +10%) 노이즈 추가
    - 0.1s → 0.2s → 0.4s (최대 3회)
    """
    
    def __init__(self, redis_client=None, s3_client=None):
        self.redis_client = redis_client
        self.s3 = s3_client or boto3.client('s3')
    
    def verify_commit_with_retry(
        self,
        execution_id: str,
        s3_bucket: str,
        s3_key: str,
        redis_key: Optional[str] = None
    ) -> CommitStatus:
        """
        Redis 커밋 상태 확인 + S3 파일 존재 검증 (Fail-fast + Jitter)
        
        S3 Strong Consistency 활용:
        1. Redis에 'committed' 상태 기록됨
        2. 다른 Lambda가 즉시 조회 → Redis는 'committed'
        3. S3 새 객체는 즉시 조회 가능 (2020년 말 이후 보장)
        4. 실패 시 Exponential Backoff + Jitter로 재시도
        5. 3회 실패 = 시스템 장애 → Fail-fast
        
        Args:
            execution_id: 실행 ID
            s3_bucket: S3 버킷
            s3_key: S3 키 (새로운 해시 키, 덮어쓰기 없음)
            redis_key: Redis 키 (None이면 execution_id 사용)
        
        Returns:
            CommitStatus: 커밋 상태 정보
        """
        if redis_key is None:
            redis_key = f"commit:{execution_id}"
        
        total_wait = 0.0
        delay = INITIAL_DELAY
        
        for attempt in range(RETRY_ATTEMPTS):
            # 1. Redis 상태 확인
            redis_status = None
            if self.redis_client and _HAS_REDIS:
                try:
                    redis_status = self.redis_client.get(redis_key)
                    if redis_status:
                        redis_status = redis_status.decode('utf-8') if isinstance(redis_status, bytes) else redis_status
                except Exception as e:
                    logger.warning(f"Redis check failed: {e}")
            
            # 2. S3 파일 존재 확인
            s3_exists = self._check_s3_exists(s3_bucket, s3_key)
            
            # 3. 둘 다 일치하면 성공
            if redis_status == 'committed' and s3_exists:
                logger.info(
                    f"[AsyncCommit] ✅ Verified after {attempt + 1} attempts, "
                    f"total_wait={total_wait * 1000:.1f}ms"
                )
                return CommitStatus(
                    is_committed=True,
                    s3_available=True,
                    redis_status=redis_status,
                    retry_count=attempt + 1,
                    total_wait_ms=total_wait * 1000
                )
            
            # 4. TOCTOU 케이스: Redis는 committed인데 S3는 없음
            if redis_status == 'committed' and not s3_exists:
                if attempt < RETRY_ATTEMPTS - 1:
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # [Jitter] Thundering Herd 방지
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # random.uniform(-0.1, 0.1) → ±10% 노이즈 추가
                    # 동시 재시도 시 부하를 시간적으로 분산
                    jitter = random.uniform(-0.1, 0.1)
                    actual_delay = delay * (1 + jitter)
                    actual_delay = max(0.05, min(actual_delay, MAX_DELAY))  # 50ms ~ 400ms
                    
                    logger.warning(
                        f"[TOCTOU Gap] Redis=committed but S3 unavailable, "
                        f"retrying in {actual_delay * 1000:.0f}ms (attempt {attempt + 1}/{RETRY_ATTEMPTS}, "
                        f"jitter={jitter * 100:+.1f}%)"
                    )
                    time.sleep(actual_delay)
                    total_wait += actual_delay
                    delay = min(delay * 2, MAX_DELAY)  # Exponential backoff
                    continue
                else:
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # [Fail-fast] 3회 실패 = 시스템 장애
                    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                    # S3 Strong Consistency를 고려하면 새 객체는 즉시 조회되어야 함
                    # 3회 실패는 S3 장애, 네트워크 장애 등 시스템 문제
                    logger.error(
                        f"[TOCTOU Failed] [System Fault] Redis=committed but S3 still unavailable "
                        f"after {RETRY_ATTEMPTS} attempts, total_wait={total_wait * 1000:.1f}ms. "
                        f"S3 Strong Consistency violation detected! "
                        f"Bucket={s3_bucket}, Key={s3_key[:50]}..."
                    )
                    return CommitStatus(
                        is_committed=True,  # Redis는 committed
                        s3_available=False,  # S3는 여전히 없음
                        redis_status=redis_status,
                        retry_count=RETRY_ATTEMPTS,
                        total_wait_ms=total_wait * 1000
                    )
            
            # 5. 아직 커밋 안 됨
            if redis_status != 'committed':
                return CommitStatus(
                    is_committed=False,
                    s3_available=s3_exists,
                    redis_status=redis_status,
                    retry_count=attempt + 1,
                    total_wait_ms=total_wait * 1000
                )
        
        # Should not reach here
        return CommitStatus(
            is_committed=False,
            s3_available=False,
            redis_status=redis_status,
            retry_count=RETRY_ATTEMPTS,
            total_wait_ms=total_wait * 1000
        )
    
    def mark_committed(
        self,
        execution_id: str,
        redis_key: Optional[str] = None,
        ttl: int = 3600
    ):
        """
        Redis에 커밋 상태 기록
        
        Args:
            execution_id: 실행 ID
            redis_key: Redis 키 (None이면 execution_id 사용)
            ttl: TTL (초)
        """
        if not self.redis_client or not _HAS_REDIS:
            logger.debug("Redis not available, skipping commit mark")
            return
        
        if redis_key is None:
            redis_key = f"commit:{execution_id}"
        
        try:
            self.redis_client.setex(redis_key, ttl, 'committed')
            logger.info(f"[AsyncCommit] Marked as committed: {redis_key}")
        except Exception as e:
            logger.error(f"Failed to mark committed in Redis: {e}")
    
    def _check_s3_exists(self, bucket: str, key: str) -> bool:
        """
        S3 파일 존재 확인
        
        Args:
            bucket: S3 버킷
            key: S3 키
        
        Returns:
            True면 파일 존재
        """
        try:
            self.s3.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            # 다른 에러는 재시도 가치 있음
            logger.warning(f"S3 head_object error: {e}")
            return False


# Singleton 인스턴스
_async_commit_service = None


def get_async_commit_service(redis_client=None) -> AsyncCommitService:
    """AsyncCommitService 싱글톤 인스턴스 반환"""
    global _async_commit_service
    if _async_commit_service is None:
        _async_commit_service = AsyncCommitService(redis_client=redis_client)
    return _async_commit_service
