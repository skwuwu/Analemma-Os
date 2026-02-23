"""
Centralized Retry & Resilience Utilities (v1.0)

Manage standard patterns of distributed systems in one place:
- Exponential Backoff + Jitter
- Circuit Breaker
- Support both Sync/Async

Usage:
    from src.common.retry_utils import with_retry, with_retry_sync, circuit_breaker
    
    @with_retry(max_retries=3, exceptions=(ClientError,))
    async def call_external_api():
        ...
    
    @with_retry_sync(max_retries=3)
    def sync_call():
        ...

AWS 서비스별 권장 설정:
- DynamoDB: max_retries=3, base_delay=0.1 (Throttling 빈번)
- Step Functions: max_retries=2, base_delay=0.5
- ECS/Fargate: max_retries=3, base_delay=1.0 (프로비저닝 대기)
- Bedrock/LLM: max_retries=3, base_delay=2.0 (Rate Limit)
- EventBridge: max_retries=3, base_delay=0.5
- S3: max_retries=2, base_delay=0.5

참고: botocore의 기본 재시도와 별개로 애플리케이션 레벨 재시도
"""
import asyncio
import time
import random
import logging
import functools
from typing import Any, Callable, Tuple, Type, Optional, Dict, List
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass, field
from threading import Lock

logger = logging.getLogger(__name__)


# =============================================================================
# 재시도 가능한 예외 분류
# =============================================================================

class RetryableErrorCategory(Enum):
    """재시도 가능 에러 카테고리"""
    TRANSIENT = "transient"        # 일시적 네트워크/서비스 장애
    THROTTLING = "throttling"      # Rate Limit, Quota 초과
    TIMEOUT = "timeout"            # 타임아웃
    RESOURCE_BUSY = "resource_busy"  # 리소스 경합
    NON_RETRYABLE = "non_retryable"  # 재시도 불가 (인증, 권한 등)


# 재시도 가능한 네트워크/연결 예외 (화이트리스트)
RETRYABLE_NETWORK_EXCEPTIONS = (
    ConnectionError,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    TimeoutError,
    OSError,  # 저수준 네트워크 에러
)

try:
    import asyncio
    RETRYABLE_NETWORK_EXCEPTIONS += (asyncio.TimeoutError,)
except (ImportError, TypeError):
    pass

try:
    from urllib.error import URLError
    RETRYABLE_NETWORK_EXCEPTIONS += (URLError,)
except (ImportError, TypeError):
    pass

try:
    from botocore.exceptions import (
        EndpointConnectionError,
        ConnectionClosedError,
        ReadTimeoutError,
        ConnectTimeoutError
    )
    RETRYABLE_NETWORK_EXCEPTIONS += (
        EndpointConnectionError,
        ConnectionClosedError,
        ReadTimeoutError,
        ConnectTimeoutError
    )
except (ImportError, TypeError):
    pass


# AWS 에러 코드별 카테고리 매핑
AWS_ERROR_CATEGORIES: Dict[str, RetryableErrorCategory] = {
    # Transient
    "InternalServerError": RetryableErrorCategory.TRANSIENT,
    "ServiceUnavailable": RetryableErrorCategory.TRANSIENT,
    "InternalFailure": RetryableErrorCategory.TRANSIENT,
    "ServiceException": RetryableErrorCategory.TRANSIENT,
    
    # Throttling
    "ThrottlingException": RetryableErrorCategory.THROTTLING,
    "ProvisionedThroughputExceededException": RetryableErrorCategory.THROTTLING,
    "RequestLimitExceeded": RetryableErrorCategory.THROTTLING,
    "TooManyRequestsException": RetryableErrorCategory.THROTTLING,
    "Throttling": RetryableErrorCategory.THROTTLING,
    "SlowDown": RetryableErrorCategory.THROTTLING,  # S3
    
    # Timeout
    "RequestTimeout": RetryableErrorCategory.TIMEOUT,
    "RequestExpired": RetryableErrorCategory.TIMEOUT,
    
    # Resource Busy
    "ConditionalCheckFailedException": RetryableErrorCategory.RESOURCE_BUSY,
    "TransactionConflictException": RetryableErrorCategory.RESOURCE_BUSY,
    
    # Non-retryable
    "AccessDeniedException": RetryableErrorCategory.NON_RETRYABLE,
    "ValidationException": RetryableErrorCategory.NON_RETRYABLE,
    "InvalidParameterException": RetryableErrorCategory.NON_RETRYABLE,
    "ResourceNotFoundException": RetryableErrorCategory.NON_RETRYABLE,
    "UnauthorizedException": RetryableErrorCategory.NON_RETRYABLE,
}


def classify_aws_error(error: Exception) -> RetryableErrorCategory:
    """
    AWS 예외를 카테고리로 분류.
    
    [v2.1] ClientError가 아닌 경우 NON_RETRYABLE 반환.
    네트워크 예외는 is_retryable()에서 별도 처리.
    """
    try:
        from botocore.exceptions import ClientError
        if not isinstance(error, ClientError):
            return RetryableErrorCategory.NON_RETRYABLE
    except ImportError:
        pass
    
    error_code = getattr(error, 'response', {}).get('Error', {}).get('Code', '')
    
    # 에러 코드가 없으면 NON_RETRYABLE (기존: TRANSIENT)
    if not error_code:
        return RetryableErrorCategory.NON_RETRYABLE
    
    return AWS_ERROR_CATEGORIES.get(error_code, RetryableErrorCategory.NON_RETRYABLE)


def is_retryable(error: Exception) -> bool:
    """
    예외가 재시도 가능한지 판단 (v2.1 - 엄격한 화이트리스트 기반).
    
    재시도 가능한 경우:
    1. AWS ClientError 중 TRANSIENT, THROTTLING, TIMEOUT, RESOURCE_BUSY
    2. 네트워크/연결 예외 (ConnectionError, TimeoutError 등)
    
    재시도 불가능한 경우:
    1. AWS 인증/권한 에러 (AccessDeniedException 등)
    2. 일반 Python 로직 에러 (IndexError, ValueError 등)
    3. 기타 모든 예외 (명시적 화이트리스트에 없으면 재시도 안 함)
    
    Args:
        error: 발생한 예외
        
    Returns:
        재시도 가능하면 True, 아니면 False
    """
    # 1. AWS ClientError인 경우 - 카테고리 기반 판단
    try:
        from botocore.exceptions import ClientError
        if isinstance(error, ClientError):
            category = classify_aws_error(error)
            return category != RetryableErrorCategory.NON_RETRYABLE
    except ImportError:
        pass
    
    # 2. 네트워크/연결 관련 예외 - 화이트리스트 기반
    if isinstance(error, RETRYABLE_NETWORK_EXCEPTIONS):
        return True
    
    # 3. 그 외 모든 예외는 재시도하지 않음 (로직 에러, ValueError 등)
    # 예: IndexError, KeyError, TypeError, AttributeError 등은 즉시 실패
    return False


# =============================================================================
# Exponential Backoff + Jitter 계산
# =============================================================================

def calculate_backoff_delay(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    use_jitter: bool = True,
    jitter_mode: str = "full"  # "full", "equal", "decorrelated"
) -> float:
    """
    지연 시간 계산 (AWS 권장 패턴)
    
    Jitter 모드:
    - full: delay * random(0, 1) - 가장 일반적
    - equal: delay/2 + random(0, delay/2) - 더 예측 가능
    - decorrelated: min(max_delay, random(base_delay, prev_delay * 3))
    """
    # 지수 백오프 기본 계산
    exponential_delay = min(max_delay, base_delay * (exponential_base ** attempt))
    
    if not use_jitter:
        return exponential_delay
    
    if jitter_mode == "full":
        # Full Jitter: 0 ~ exponential_delay
        return exponential_delay * random.random()
    
    elif jitter_mode == "equal":
        # Equal Jitter: exponential_delay/2 ~ exponential_delay
        return exponential_delay / 2 + (exponential_delay / 2) * random.random()
    
    else:
        # Default to full jitter
        return exponential_delay * random.random()


# =============================================================================
# Async 재시도 데코레이터
# =============================================================================

def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    use_jitter: bool = True,
    on_retry: Optional[Callable[[Exception, int], None]] = None,
    should_retry: Optional[Callable[[Exception], bool]] = None,
    fallback_value: Any = None,
    use_fallback: bool = False
):
    """
    Async 재시도 데코레이터: Exponential Backoff + Jitter + Fallback
    
    Args:
        max_retries: 최대 재시도 횟수 (기본 시도 제외)
        base_delay: 기본 대기 시간 (초)
        max_delay: 최대 대기 시간 (초)
        exponential_base: 지수 증가율
        exceptions: 재시도할 예외 타입들
        use_jitter: Jitter 사용 여부
        on_retry: 재시도 시 호출할 콜백 (error, attempt)
        should_retry: 커스텀 재시도 판단 함수
        fallback_value: [v2.1] 모든 재시도 실패 시 반환할 값
        use_fallback: [v2.1] Fallback 사용 여부 (True면 예외 대신 fallback_value 반환)
    
    Usage:
        @with_retry(max_retries=3, exceptions=(ClientError,))
        async def call_api():
            ...
        
        # Fallback 사용 예시 (통계 조회 등)
        @with_retry(max_retries=2, use_fallback=True, fallback_value={})
        async def get_optional_stats():
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                    
                except exceptions as e:
                    last_exception = e
                    
                    # 커스텀 재시도 판단
                    if should_retry is not None and not should_retry(e):
                        logger.warning(f"🚫 재시도 불가 에러: {func.__name__} - {str(e)[:100]}")
                        raise
                    
                    # AWS 에러 분류 기반 판단
                    if not is_retryable(e):
                        logger.warning(f"🚫 재시도 불가 (AWS): {func.__name__} - {str(e)[:100]}")
                        raise
                    
                    # 최대 재시도 도달
                    if attempt == max_retries:
                        if use_fallback:
                            logger.warning(
                                f"💡 모든 재시도 실패, Fallback 값 반환: {func.__name__} - {fallback_value}"
                            )
                            return fallback_value
                        
                        logger.error(
                            f"❌ 최대 재시도 초과 ({max_retries}회): {func.__name__} - {str(e)[:100]}"
                        )
                        raise
                    
                    # 대기 시간 계산
                    delay = calculate_backoff_delay(
                        attempt, base_delay, max_delay, exponential_base, use_jitter
                    )
                    
                    logger.warning(
                        f"⚠️ 재시도 {attempt + 1}/{max_retries}: {func.__name__} | "
                        f"에러: {str(e)[:50]}... | {delay:.2f}초 후 재시도"
                    )
                    
                    # 콜백 호출
                    if on_retry:
                        try:
                            on_retry(e, attempt)
                        except Exception:
                            pass  # 콜백 실패는 무시
                    
                    await asyncio.sleep(delay)
            
            # 여기 도달하면 안 됨
            if use_fallback:
                logger.warning(f"💡 모든 재시도 실패, Fallback 값 반환: {fallback_value}")
                return fallback_value
            raise last_exception
        
        return wrapper
    return decorator


# =============================================================================
# Sync 재시도 데코레이터
# =============================================================================

def with_retry_sync(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    use_jitter: bool = True,
    on_retry: Optional[Callable[[Exception, int], None]] = None,
    should_retry: Optional[Callable[[Exception], bool]] = None,
    fallback_value: Any = None,
    use_fallback: bool = False
):
    """
    Sync 재시도 데코레이터: Exponential Backoff + Jitter + Fallback
    
    Args:
        fallback_value: [v2.1] 모든 재시도 실패 시 반환할 값
        use_fallback: [v2.1] Fallback 사용 여부
    
    Usage:
        @with_retry_sync(max_retries=3)
        def sync_call():
            ...
        
        # Fallback 예시
        @with_retry_sync(max_retries=2, use_fallback=True, fallback_value=None)
        def get_cache():
            ...  # 실패해도 None 반환하여 워크플로우 중단 방지
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                    
                except exceptions as e:
                    last_exception = e
                    
                    # 커스텀 재시도 판단
                    if should_retry is not None and not should_retry(e):
                        logger.warning(f"🚫 재시도 불가 에러: {func.__name__} - {str(e)[:100]}")
                        raise
                    
                    # AWS 에러 분류 기반 판단
                    if not is_retryable(e):
                        logger.warning(f"🚫 재시도 불가 (AWS): {func.__name__} - {str(e)[:100]}")
                        raise
                    
                    # 최대 재시도 도달
                    if attempt == max_retries:
                        if use_fallback:
                            logger.warning(
                                f"💡 모든 재시도 실패, Fallback 값 반환: {func.__name__} - {fallback_value}"
                            )
                            return fallback_value
                        
                        logger.error(
                            f"❌ 최대 재시도 초과 ({max_retries}회): {func.__name__} - {str(e)[:100]}"
                        )
                        raise
                    
                    # 대기 시간 계산
                    delay = calculate_backoff_delay(
                        attempt, base_delay, max_delay, exponential_base, use_jitter
                    )
                    
                    logger.warning(
                        f"⚠️ 재시도 {attempt + 1}/{max_retries}: {func.__name__} | "
                        f"에러: {str(e)[:50]}... | {delay:.2f}초 후 재시도"
                    )
                    
                    # 콜백 호출
                    if on_retry:
                        try:
                            on_retry(e, attempt)
                        except Exception:
                            pass
                    
                    time.sleep(delay)
            
            if use_fallback:
                logger.warning(f"💡 모든 재시도 실패, Fallback 값 반환: {fallback_value}")
                return fallback_value
            raise last_exception
        
        return wrapper
    return decorator


# =============================================================================
# 함수 래퍼 (데코레이터 대신 직접 호출)
# =============================================================================

def retry_call(
    func: Callable,
    args: tuple = (),
    kwargs: dict = None,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    use_jitter: bool = True,
    fallback_value: Any = None,
    use_fallback: bool = False
) -> Any:
    """
    함수를 재시도와 함께 호출 (데코레이터 대신 사용).
    
    Args:
        fallback_value: [v2.1] 모든 재시도 실패 시 반환할 값
        use_fallback: [v2.1] Fallback 사용 여부
    
    Usage:
        result = retry_call(
            dynamodb.get_item,
            kwargs={'Key': {'pk': 'value'}},
            max_retries=3
        )
        
        # Fallback 예시
        cache = retry_call(
            get_cache_from_redis,
            use_fallback=True,
            fallback_value={}
        )
    """
    kwargs = kwargs or {}
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
            
        except exceptions as e:
            last_exception = e
            
            if not is_retryable(e):
                raise
            
            if attempt == max_retries:
                if use_fallback:
                    logger.warning(f"💡 모든 재시도 실패, Fallback 값 반환: {func.__name__} - {fallback_value}")
                    return fallback_value
                
                logger.error(f"❌ 최대 재시도 초과: {func.__name__}")
                raise
            
            delay = calculate_backoff_delay(
                attempt, base_delay, max_delay, 2.0, use_jitter
            )
            
            logger.warning(
                f"⚠️ 재시도 {attempt + 1}/{max_retries}: {func.__name__} | {delay:.2f}초 후"
            )
            time.sleep(delay)
    
    if use_fallback:
        logger.warning(f"💡 모든 재시도 실패, Fallback 값 반환: {fallback_value}")
        return fallback_value
    raise last_exception


# =============================================================================
# Circuit Breaker 패턴
# =============================================================================

class CircuitState(Enum):
    """Circuit Breaker 상태"""
    CLOSED = "closed"      # 정상 - 요청 통과
    OPEN = "open"          # 장애 - 요청 즉시 실패
    HALF_OPEN = "half_open"  # 복구 테스트 - 제한적 요청 허용


@dataclass
class CircuitBreakerState:
    """Circuit Breaker 상태 정보"""
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[float] = None
    last_state_change: float = field(default_factory=time.time)


class CircuitBreaker:
    """
    Circuit Breaker 패턴 구현
    
    연속 실패 시 빠른 실패 반환으로 시스템 보호.
    
    상태 전이:
    CLOSED --[failure_threshold 도달]--> OPEN
    OPEN --[recovery_timeout 경과]--> HALF_OPEN
    HALF_OPEN --[성공]--> CLOSED
    HALF_OPEN --[실패]--> OPEN
    
    Usage:
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
        
        @breaker
        def call_external_api():
            ...
        
        # 또는 직접 호출
        if breaker.allow_request():
            try:
                result = call_api()
                breaker.record_success()
            except Exception as e:
                breaker.record_failure()
                raise
    """
    
    # 전역 Circuit Breaker 레지스트리 (서비스별 상태 공유)
    _registry: Dict[str, 'CircuitBreaker'] = {}
    _registry_lock = Lock()
    
    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
        success_threshold: int = 2
    ):
        """
        Args:
            name: Circuit Breaker 식별자 (서비스별)
            failure_threshold: OPEN 전환까지의 연속 실패 수
            recovery_timeout: OPEN → HALF_OPEN 전환 대기 시간 (초)
            half_open_max_calls: HALF_OPEN 상태에서 허용할 최대 호출 수
            success_threshold: HALF_OPEN → CLOSED 전환까지의 연속 성공 수
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold
        
        self._state = CircuitBreakerState()
        self._lock = Lock()
        self._half_open_calls = 0
    
    @classmethod
    def get_or_create(cls, name: str, **kwargs) -> 'CircuitBreaker':
        """
        서비스별 싱글톤 Circuit Breaker 획득.

        환경변수 REDIS_URL이 설정된 경우 RedisCircuitBreaker 반환.
        Redis 연결 실패 또는 REDIS_URL 미설정 시 인메모리 CircuitBreaker 반환.
        """
        import os
        redis_url = os.environ.get('REDIS_URL')
        if redis_url:
            try:
                return RedisCircuitBreaker.get_or_create(name, redis_url=redis_url, **kwargs)
            except Exception as e:
                logger.warning(
                    f"[CircuitBreaker] Redis unavailable, falling back to in-memory: {e}"
                )

        with cls._registry_lock:
            if name not in cls._registry:
                cls._registry[name] = cls(name=name, **kwargs)
            return cls._registry[name]
    
    @property
    def state(self) -> CircuitState:
        """현재 상태 (자동 전이 포함)"""
        with self._lock:
            self._check_state_transition()
            return self._state.state
    
    def _check_state_transition(self) -> None:
        """상태 전이 체크"""
        if self._state.state == CircuitState.OPEN:
            # OPEN → HALF_OPEN 전이 체크
            if self._state.last_failure_time:
                elapsed = time.time() - self._state.last_failure_time
                if elapsed >= self.recovery_timeout:
                    self._transition_to(CircuitState.HALF_OPEN)
    
    def _transition_to(self, new_state: CircuitState) -> None:
        """상태 전이"""
        old_state = self._state.state
        self._state.state = new_state
        self._state.last_state_change = time.time()
        
        if new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0
            self._state.success_count = 0
        elif new_state == CircuitState.CLOSED:
            self._state.failure_count = 0
        
        logger.info(f"🔌 Circuit Breaker [{self.name}]: {old_state.value} → {new_state.value}")
    
    def allow_request(self) -> bool:
        """요청 허용 여부 판단"""
        with self._lock:
            self._check_state_transition()
            
            if self._state.state == CircuitState.CLOSED:
                return True
            
            elif self._state.state == CircuitState.OPEN:
                return False
            
            elif self._state.state == CircuitState.HALF_OPEN:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False
        
        return False
    
    def record_success(self) -> None:
        """성공 기록"""
        with self._lock:
            if self._state.state == CircuitState.HALF_OPEN:
                self._state.success_count += 1
                if self._state.success_count >= self.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
            
            # CLOSED 상태에서는 실패 카운트 리셋
            elif self._state.state == CircuitState.CLOSED:
                self._state.failure_count = 0
    
    def record_failure(self) -> None:
        """실패 기록"""
        with self._lock:
            self._state.failure_count += 1
            self._state.last_failure_time = time.time()
            
            if self._state.state == CircuitState.HALF_OPEN:
                # HALF_OPEN에서 실패하면 다시 OPEN
                self._transition_to(CircuitState.OPEN)
            
            elif self._state.state == CircuitState.CLOSED:
                if self._state.failure_count >= self.failure_threshold:
                    self._transition_to(CircuitState.OPEN)
    
    def get_status(self) -> Dict[str, Any]:
        """현재 상태 정보"""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.state.value,
                "failure_count": self._state.failure_count,
                "success_count": self._state.success_count,
                "last_failure_time": self._state.last_failure_time,
                "config": {
                    "failure_threshold": self.failure_threshold,
                    "recovery_timeout": self.recovery_timeout
                }
            }
    
    def __call__(self, func: Callable) -> Callable:
        """데코레이터로 사용"""
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not self.allow_request():
                raise CircuitOpenError(
                    f"Circuit Breaker [{self.name}] is OPEN. "
                    f"Recovery in {self.recovery_timeout}s"
                )
            
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:
                self.record_failure()
                raise
        
        return wrapper


class CircuitOpenError(Exception):
    """Circuit Breaker가 OPEN 상태일 때 발생"""
    pass


# =============================================================================
# 조합 패턴: Retry + Circuit Breaker
# =============================================================================

def with_resilience(
    circuit_name: str,
    max_retries: int = 3,
    base_delay: float = 1.0,
    failure_threshold: int = 5,
    recovery_timeout: float = 30.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    Retry + Circuit Breaker 조합 데코레이터
    
    Usage:
        @with_resilience("bedrock-api", max_retries=3, failure_threshold=5)
        def call_bedrock():
            ...
    """
    breaker = CircuitBreaker.get_or_create(
        circuit_name,
        failure_threshold=failure_threshold,
        recovery_timeout=recovery_timeout
    )
    
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Circuit Breaker 체크
            if not breaker.allow_request():
                raise CircuitOpenError(
                    f"Circuit [{circuit_name}] is OPEN"
                )
            
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    breaker.record_success()
                    return result
                    
                except exceptions as e:
                    last_exception = e
                    
                    if not is_retryable(e):
                        breaker.record_failure()
                        raise
                    
                    if attempt == max_retries:
                        breaker.record_failure()
                        raise
                    
                    delay = calculate_backoff_delay(attempt, base_delay)
                    logger.warning(
                        f"⚠️ [{circuit_name}] 재시도 {attempt + 1}/{max_retries}: {delay:.2f}초"
                    )
                    time.sleep(delay)
            
            breaker.record_failure()
            raise last_exception
        
        return wrapper
    return decorator


# =============================================================================
# 서비스별 사전 정의 데코레이터 (Plug-and-Play)
# =============================================================================

# DynamoDB용 (Throttling 빈번)
retry_dynamodb = functools.partial(
    with_retry_sync,
    max_retries=3,
    base_delay=0.1,
    max_delay=2.0,
    use_jitter=True
)

# Step Functions용
retry_stepfunctions = functools.partial(
    with_retry_sync,
    max_retries=2,
    base_delay=0.5,
    max_delay=5.0,
    use_jitter=True
)

# ECS/Fargate용 (프로비저닝 대기)
retry_ecs = functools.partial(
    with_retry_sync,
    max_retries=3,
    base_delay=1.0,
    max_delay=10.0,
    use_jitter=True
)

# Bedrock/LLM용 (Rate Limit)
retry_llm = functools.partial(
    with_retry_sync,
    max_retries=3,
    base_delay=2.0,
    max_delay=30.0,
    use_jitter=True
)

# S3용
retry_s3 = functools.partial(
    with_retry_sync,
    max_retries=2,
    base_delay=0.5,
    max_delay=5.0,
    use_jitter=True
)

# EventBridge용
retry_eventbridge = functools.partial(
    with_retry_sync,
    max_retries=3,
    base_delay=0.5,
    max_delay=5.0,
    use_jitter=True
)

# Webhook/HTTP용
retry_http = functools.partial(
    with_retry_sync,
    max_retries=3,
    base_delay=1.0,
    max_delay=10.0,
    use_jitter=True
)


# =============================================================================
# 유틸리티 함수
# =============================================================================

def get_all_circuit_breakers() -> Dict[str, Dict[str, Any]]:
    """모든 Circuit Breaker 상태 조회 (모니터링용)"""
    with CircuitBreaker._registry_lock:
        return {
            name: breaker.get_status()
            for name, breaker in CircuitBreaker._registry.items()
        }


def reset_circuit_breaker(name: str) -> bool:
    """특정 Circuit Breaker 강제 리셋"""
    with CircuitBreaker._registry_lock:
        if name in CircuitBreaker._registry:
            breaker = CircuitBreaker._registry[name]
            with breaker._lock:
                breaker._state = CircuitBreakerState()
                breaker._half_open_calls = 0
            logger.info(f"🔄 Circuit Breaker [{name}] reset to CLOSED")
            return True
        return False


# =============================================================================
# Redis 기반 분산 Circuit Breaker
# =============================================================================

class RedisCircuitBreaker:
    """
    Redis INCR + Lua 원자 연산 기반 분산 Circuit Breaker.

    멀티 워커 환경에서 상태를 Redis에 공유:
    - 실패 카운트: Redis INCR + TTL (슬라이딩 윈도우)
    - 상태 전이: Lua 스크립트로 원자 실행 (Race Condition 방지)
    - 상태 브로드캐스트: Redis Pub/Sub (모든 워커 즉시 동기화)

    Redis 연결 불가 시 in-memory CircuitBreaker로 자동 폴백.

    Redis 키 구조:
        cb:{service_id}:failures   → INCR + EXPIRE (슬라이딩 윈도우)
        cb:{service_id}:state      → CLOSED | OPEN | HALF_OPEN
        cb:{service_id}:opened_at  → OPEN 전환 타임스탬프

    Usage:
        breaker = RedisCircuitBreaker.get_or_create("bedrock-api", redis_url="redis://...")
        if breaker.allow_request():
            try:
                result = call_api()
                breaker.record_success()
            except Exception:
                breaker.record_failure()
                raise
    """

    # Lua 스크립트: 실패 기록 + 임계치 초과 시 OPEN 전환 (원자 실행)
    _LUA_RECORD_FAILURE = """
local count = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
if count >= tonumber(ARGV[2]) then
    local current_state = redis.call('GET', KEYS[2])
    if current_state ~= 'OPEN' then
        redis.call('SET', KEYS[2], 'OPEN')
        redis.call('SET', KEYS[3], ARGV[3])
        redis.call('PUBLISH', ARGV[4], KEYS[2] .. ':OPEN')
    end
end
return count
"""

    # Lua 스크립트: 성공 기록 + HALF_OPEN에서 연속 성공 시 CLOSED 전환
    _LUA_RECORD_SUCCESS = """
local state = redis.call('GET', KEYS[1])
if state == 'HALF_OPEN' then
    local ok_count = redis.call('INCR', KEYS[2])
    redis.call('EXPIRE', KEYS[2], 60)
    if ok_count >= tonumber(ARGV[1]) then
        redis.call('SET', KEYS[1], 'CLOSED')
        redis.call('DEL', KEYS[2])
        redis.call('DEL', KEYS[3])
        redis.call('DEL', KEYS[4])
        redis.call('PUBLISH', ARGV[2], KEYS[1] .. ':CLOSED')
    end
end
return state or 'CLOSED'
"""

    # Pub/Sub 채널
    PUBSUB_CHANNEL = "analemma:cb:state_changes"

    _registry: Dict[str, 'RedisCircuitBreaker'] = {}
    _registry_lock = Lock()

    def __init__(
        self,
        name: str,
        redis_url: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        window_seconds: int = 60,
        success_threshold: int = 2,
    ):
        self.name = name
        self._redis_url = redis_url
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.window_seconds = window_seconds
        self.success_threshold = success_threshold

        # Redis 클라이언트 (lazy init)
        self._redis = None
        self._pubsub_thread = None

        # in-memory 폴백 CB
        self._fallback: Optional[CircuitBreaker] = None

        # Redis 키
        self._key_failures  = f"cb:{name}:failures"
        self._key_state     = f"cb:{name}:state"
        self._key_opened_at = f"cb:{name}:opened_at"
        self._key_ok_count  = f"cb:{name}:ok_count"

        # 로컬 상태 캐시 (Pub/Sub로 업데이트)
        self._local_state = CircuitState.CLOSED
        self._local_state_lock = Lock()

        self._init_redis()

    @classmethod
    def get_or_create(cls, name: str, redis_url: str, **kwargs) -> 'RedisCircuitBreaker':
        """서비스별 싱글톤 RedisCircuitBreaker."""
        with cls._registry_lock:
            if name not in cls._registry:
                cls._registry[name] = cls(name=name, redis_url=redis_url, **kwargs)
            return cls._registry[name]

    def _init_redis(self) -> None:
        """Redis 연결 초기화 + Pub/Sub 리스너 시작."""
        try:
            import redis as redis_lib
            self._redis = redis_lib.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            self._redis.ping()  # 연결 확인

            # Pub/Sub 리스너: 백그라운드 스레드로 상태 변경 구독
            self._start_pubsub_listener()
            logger.info(f"[RedisCircuitBreaker] Initialized: {self.name} @ {self._redis_url}")

        except Exception as e:
            logger.warning(
                f"[RedisCircuitBreaker] Redis init failed for '{self.name}', "
                f"falling back to in-memory: {e}"
            )
            self._redis = None
            self._fallback = CircuitBreaker(
                name=self.name,
                failure_threshold=self.failure_threshold,
                recovery_timeout=self.recovery_timeout,
            )

    def _start_pubsub_listener(self) -> None:
        """Redis Pub/Sub 리스너 스레드 시작."""
        import threading

        def _listen():
            try:
                import redis as redis_lib
                r = redis_lib.from_url(self._redis_url, decode_responses=True)
                pubsub = r.pubsub()
                pubsub.subscribe(self.PUBSUB_CHANNEL)
                for message in pubsub.listen():
                    if message['type'] != 'message':
                        continue
                    data = message.get('data', '')
                    # 형식: "cb:{name}:state:OPEN" or "cb:{name}:state:CLOSED"
                    if f"cb:{self.name}:state:" in data:
                        parts = data.split(':')
                        new_state_str = parts[-1]
                        try:
                            new_state = CircuitState(new_state_str.lower())
                            with self._local_state_lock:
                                self._local_state = new_state
                            logger.info(
                                f"[RedisCircuitBreaker][PubSub] '{self.name}' → {new_state_str}"
                            )
                        except ValueError:
                            pass
            except Exception as e:
                logger.debug(f"[RedisCircuitBreaker] PubSub listener error: {e}")

        t = threading.Thread(target=_listen, daemon=True, name=f"cb-pubsub-{self.name}")
        t.start()
        self._pubsub_thread = t

    # ── Public API ────────────────────────────────────────────────────────────

    def allow_request(self) -> bool:
        """요청 허용 여부."""
        if self._fallback:
            return self._fallback.allow_request()

        state = self._get_state()

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            # recovery_timeout 경과 시 HALF_OPEN으로 전환
            opened_at = self._redis_get_float(self._key_opened_at)
            if opened_at and (time.time() - opened_at) >= self.recovery_timeout:
                self._set_state('HALF_OPEN')
                return True
            return False

        if state == CircuitState.HALF_OPEN:
            return True

        return False

    def record_failure(self) -> CircuitState:
        """실패 기록. Lua 스크립트로 원자 실행."""
        if self._fallback:
            self._fallback.record_failure()
            return CircuitState(self._fallback.state.value)

        try:
            script = self._redis.register_script(self._LUA_RECORD_FAILURE)
            script(
                keys=[self._key_failures, self._key_state, self._key_opened_at],
                args=[
                    self.window_seconds,
                    self.failure_threshold,
                    time.time(),
                    self.PUBSUB_CHANNEL,
                ],
            )
        except Exception as e:
            logger.warning(f"[RedisCircuitBreaker] record_failure error: {e}")

        return self._get_state()

    def record_success(self) -> CircuitState:
        """성공 기록. HALF_OPEN → CLOSED 전환 처리."""
        if self._fallback:
            self._fallback.record_success()
            return CircuitState(self._fallback.state.value)

        try:
            script = self._redis.register_script(self._LUA_RECORD_SUCCESS)
            script(
                keys=[self._key_state, self._key_ok_count,
                      self._key_failures, self._key_opened_at],
                args=[self.success_threshold, self.PUBSUB_CHANNEL],
            )
        except Exception as e:
            logger.warning(f"[RedisCircuitBreaker] record_success error: {e}")

        return self._get_state()

    def get_state(self) -> CircuitState:
        """현재 CB 상태 반환."""
        if self._fallback:
            return CircuitState(self._fallback.state.value)
        return self._get_state()

    def get_status(self) -> Dict[str, Any]:
        """상태 정보 (모니터링용)."""
        if self._fallback:
            return self._fallback.get_status()

        try:
            failure_count = int(self._redis.get(self._key_failures) or 0)
        except Exception:
            failure_count = -1

        return {
            "name": self.name,
            "backend": "redis",
            "state": self._get_state().value,
            "failure_count": failure_count,
            "config": {
                "failure_threshold": self.failure_threshold,
                "recovery_timeout": self.recovery_timeout,
                "window_seconds": self.window_seconds,
            },
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _get_state(self) -> CircuitState:
        """Redis에서 현재 상태 읽기."""
        try:
            raw = self._redis.get(self._key_state) or 'CLOSED'
            return CircuitState(raw.lower())
        except Exception:
            # Redis 장애 → 로컬 캐시(Pub/Sub 업데이트) 사용
            with self._local_state_lock:
                return self._local_state

    def _set_state(self, state_str: str) -> None:
        try:
            self._redis.set(self._key_state, state_str)
            self._redis.publish(
                self.PUBSUB_CHANNEL,
                f"cb:{self.name}:state:{state_str}",
            )
        except Exception as e:
            logger.debug(f"[RedisCircuitBreaker] _set_state error: {e}")

    def _redis_get_float(self, key: str) -> Optional[float]:
        try:
            val = self._redis.get(key)
            return float(val) if val else None
        except Exception:
            return None
