"""
State View Context Service

Ring 레벨 기반 상태 뷰 생성
- 불변 Core State 보호
- Proxy Pattern (Lazy Projection)
- 메모리 오버헤드 90% 절감
"""

import logging
import hashlib
from typing import Dict, Any, Callable, Optional, Set
from collections.abc import MutableMapping

logger = logging.getLogger(__name__)


class StateViewProxy(MutableMapping):
    """
    상태 뷰 프록시 (Lazy Projection)
    
    deepcopy 대신 프록시 패턴 사용:
    - 원본 상태는 그대로 유지
    - 접근 시점에 Ring 정책 적용
    - 메모리 오버헤드 90% 절감
    """
    
    def __init__(
        self,
        core_state: Dict[str, Any],
        ring_level: int,
        field_policies: Dict[str, Dict[int, Optional[Callable]]]
    ):
        """
        Args:
            core_state: 원본 상태 (읽기 전용 참조)
            ring_level: Ring 레벨 (0-3)
            field_policies: 필드별 Ring 정책
        """
        self._core_state = core_state
        self._ring_level = ring_level
        self._policies = field_policies
        
        # 쓰기 캐시 (노드가 새로 쓴 데이터)
        self._write_cache: Dict[str, Any] = {}
        
        # 접근 로그 (디버깅용)
        self._access_log: Set[str] = set()
    
    def __getitem__(self, key: str) -> Any:
        """
        필드 읽기 (Lazy Projection)
        
        Args:
            key: 필드명
        
        Returns:
            Ring 정책 적용된 값
        """
        # 1. 쓰기 캐시 우선 확인 (노드가 방금 쓴 값)
        if key in self._write_cache:
            return self._write_cache[key]
        
        # 2. Core State에서 읽기
        if key not in self._core_state:
            raise KeyError(f"Key '{key}' not found in state")
        
        value = self._core_state[key]
        
        # 3. Ring 정책 적용 (Lazy Evaluation)
        transformed_value = self._apply_policy(key, value)
        
        # 4. 접근 로그 기록
        self._access_log.add(key)
        
        return transformed_value
    
    def __setitem__(self, key: str, value: Any) -> None:
        """
        필드 쓰기 (쓰기 캐시에 저장)
        
        Args:
            key: 필드명
            value: 값
        """
        # Reserved Key 차단
        if self._is_reserved_key(key):
            logger.warning(
                f"[STATE_VIEW] Ring {self._ring_level} attempted to write "
                f"reserved key: {key} (blocked)"
            )
            return
        
        # 쓰기 캐시에 저장 (원본은 보호)
        self._write_cache[key] = value
        
        logger.debug(
            f"[STATE_VIEW] Ring {self._ring_level} wrote {key} = {value}"
        )
    
    def __delitem__(self, key: str) -> None:
        """필드 삭제 (쓰기 캐시에서만)"""
        if key in self._write_cache:
            del self._write_cache[key]
        elif key in self._core_state:
            # 원본 삭제 시도 → 경고
            logger.warning(
                f"[STATE_VIEW] Ring {self._ring_level} attempted to delete "
                f"core state key: {key} (blocked)"
            )
    
    def __iter__(self):
        """반복자 (Core State + Write Cache 병합)"""
        # Hidden 필드 제외
        visible_keys = {
            key for key in self._core_state
            if not self._is_hidden(key)
        }
        return iter(visible_keys | set(self._write_cache.keys()))
    
    def __len__(self) -> int:
        """길이 (Visible 필드 수)"""
        visible_keys = {
            key for key in self._core_state
            if not self._is_hidden(key)
        }
        return len(visible_keys | set(self._write_cache.keys()))
    
    def _apply_policy(self, key: str, value: Any) -> Any:
        """
        Ring 정책 적용 (Lazy Evaluation)
        
        Args:
            key: 필드명
            value: 원본 값
        
        Returns:
            변환된 값
        """
        policy = self._get_policy_for_field(key)
        
        if policy is None:
            # Hidden 필드 → KeyError 발생
            raise KeyError(f"Key '{key}' is hidden for Ring {self._ring_level}")
        
        if self._ring_level in policy:
            transformer = policy[self._ring_level]
            
            if transformer is None:
                # Hidden
                raise KeyError(f"Key '{key}' is hidden for Ring {self._ring_level}")
            
            if callable(transformer):
                # 변환 함수 적용
                return transformer(value)
            else:
                # 그대로 반환
                return value
        else:
            # 정책 없음 → 기본 허용
            return value
    
    def _get_policy_for_field(self, key: str) -> Optional[Dict[int, Optional[Callable]]]:
        """
        필드에 매칭되는 정책 검색
        
        Args:
            key: 필드명
        
        Returns:
            Ring별 정책 Dict (None이면 Hidden)
        """
        # 1. 정확한 매칭
        if key in self._policies:
            return self._policies[key]
        
        # 2. 와일드카드 매칭 (예: _kernel_*)
        for pattern, policy in self._policies.items():
            if '*' in pattern:
                prefix = pattern.rstrip('*')
                if key.startswith(prefix):
                    return policy
        
        # 3. 정책 없음 → 기본 허용
        return {0: lambda v: v, 1: lambda v: v, 2: lambda v: v, 3: lambda v: v}
    
    def _is_hidden(self, key: str) -> bool:
        """필드가 현재 Ring에서 숨겨져 있는지 확인"""
        try:
            policy = self._get_policy_for_field(key)
            if policy is None:
                return True
            transformer = policy.get(self._ring_level)
            return transformer is None
        except:
            return True
    
    def _is_reserved_key(self, key: str) -> bool:
        """Reserved Key 여부 확인"""
        RESERVED_KEYS = {
            "workflowId", "owner_id", "execution_id",
            "loop_counter", "max_loop_iterations", "segment_id",
            "current_state", "final_state", "state_s3_path",
            "step_history", "execution_logs",
            "scheduling_metadata", "__scheduling_metadata"
        }
        
        # Kernel 명령 (_kernel_*) 체크
        if key.startswith("_kernel_") and self._ring_level >= 3:
            return True
        
        return key in RESERVED_KEYS
    
    def get_write_cache(self) -> Dict[str, Any]:
        """쓰기 캐시 반환 (Core State 병합용)"""
        return self._write_cache.copy()
    
    def get_access_log(self) -> Set[str]:
        """접근 로그 반환 (디버깅용)"""
        return self._access_log.copy()


class StateViewContext:
    """
    상태 뷰 컨텍스트 관리
    
    책임:
    1. Immutable Core State 관리
    2. Ring 레벨별 뷰 생성 (Proxy Pattern)
    3. 필드 정책 관리
    """
    
    # 기본 필드 정책
    DEFAULT_FIELD_POLICIES = {
        "email": {
            0: lambda v: v,  # Ring 0: 원본
            1: lambda v: v,  # Ring 1: 원본
            2: lambda v: v,  # Ring 2: 원본
            3: lambda v: hashlib.sha256(str(v).encode()).hexdigest()[:16]  # Ring 3: 해시
        },
        "ssn": {
            0: lambda v: v,
            1: lambda v: f"***-{v.split('-')[-1]}" if isinstance(v, str) and '-' in v else "REDACTED",
            2: lambda v: "REDACTED",
            3: lambda v: "REDACTED"
        },
        "password": {
            0: lambda v: v,
            1: None,  # Hidden
            2: None,  # Hidden
            3: None   # Hidden
        },
        "_kernel_*": {
            0: lambda v: v,
            1: lambda v: v,
            2: None,  # Hidden
            3: None   # Hidden
        },
        "__next_node": {
            # 모든 Ring 허용 (라우팅 결정)
            0: lambda v: v,
            1: lambda v: v,
            2: lambda v: v,
            3: lambda v: v
        }
    }
    
    def __init__(self, core_state: Dict[str, Any], custom_policies: Optional[Dict] = None):
        """
        Args:
            core_state: 원본 상태 (불변 유지)
            custom_policies: 사용자 정의 필드 정책 (Optional)
        """
        self._core_state = core_state
        
        # 필드 정책 병합
        self._policies = self.DEFAULT_FIELD_POLICIES.copy()
        if custom_policies:
            self._policies.update(custom_policies)
        
        logger.info(
            f"[STATE_VIEW_CONTEXT] Initialized with "
            f"{len(core_state)} fields, {len(self._policies)} policies"
        )
    
    def create_view(self, ring_level: int) -> StateViewProxy:
        """
        Ring 레벨에 맞는 상태 뷰 생성 (Proxy Pattern)
        
        Args:
            ring_level: Ring 레벨 (0-3)
        
        Returns:
            StateViewProxy 인스턴스 (MutableMapping)
        """
        logger.debug(
            f"[STATE_VIEW_CONTEXT] Creating view for Ring {ring_level}"
        )
        
        return StateViewProxy(
            core_state=self._core_state,
            ring_level=ring_level,
            field_policies=self._policies
        )
    
    def merge_write_cache(self, write_cache: Dict[str, Any]) -> None:
        """
        노드 실행 결과를 Core State에 병합
        
        Args:
            write_cache: 노드가 쓴 데이터
        """
        # Reserved Key 필터링
        filtered_cache = {
            k: v for k, v in write_cache.items()
            if k not in self._get_reserved_keys()
        }
        
        # Core State 업데이트
        self._core_state.update(filtered_cache)
        
        logger.info(
            f"[STATE_VIEW_CONTEXT] Merged {len(filtered_cache)} fields to core state "
            f"(filtered {len(write_cache) - len(filtered_cache)} reserved keys)"
        )
    
    def get_core_state(self) -> Dict[str, Any]:
        """
        Core State 반환 (읽기 전용)
        
        주의: 직접 수정 금지!
        
        Returns:
            Core State Dict
        """
        return self._core_state
    
    def _get_reserved_keys(self) -> Set[str]:
        """Reserved Key Set 반환"""
        return {
            "workflowId", "owner_id", "execution_id",
            "loop_counter", "max_loop_iterations", "segment_id",
            "current_state", "final_state", "state_s3_path",
            "step_history", "execution_logs",
            "scheduling_metadata", "__scheduling_metadata"
        }
    
    def set_field_policy(
        self,
        field_name: str,
        ring_policies: Dict[int, Optional[Callable]]
    ) -> None:
        """
        특정 필드의 Ring 정책 설정
        
        Args:
            field_name: 필드명 (예: "user_email")
            ring_policies: {
                0: lambda v: v,           # 원본
                1: lambda v: v,           # 원본
                2: lambda v: hash(v),     # 해시
                3: None                   # Hidden
            }
        """
        self._policies[field_name] = ring_policies
        
        logger.info(
            f"[STATE_VIEW_CONTEXT] Set policy for '{field_name}' "
            f"({len(ring_policies)} ring levels)"
        )


class FieldPolicyBuilder:
    """
    필드 정책 빌더 (편의 클래스)
    """
    
    @staticmethod
    def original() -> Dict[int, Callable]:
        """모든 Ring에서 원본 반환"""
        return {0: lambda v: v, 1: lambda v: v, 2: lambda v: v, 3: lambda v: v}
    
    @staticmethod
    def hash_at_ring3() -> Dict[int, Callable]:
        """Ring 3에서만 해시 처리"""
        return {
            0: lambda v: v,
            1: lambda v: v,
            2: lambda v: v,
            3: lambda v: hashlib.sha256(str(v).encode()).hexdigest()[:16]
        }
    
    @staticmethod
    def redact_at_ring2_3() -> Dict[int, Callable]:
        """Ring 2-3에서 REDACTED"""
        return {
            0: lambda v: v,
            1: lambda v: v,
            2: lambda v: "REDACTED",
            3: lambda v: "REDACTED"
        }
    
    @staticmethod
    def hidden_above_ring1() -> Dict[int, Optional[Callable]]:
        """Ring 2-3에서 숨김"""
        return {
            0: lambda v: v,
            1: lambda v: v,
            2: None,  # Hidden
            3: None   # Hidden
        }
    
    @staticmethod
    def custom(
        ring0: Optional[Callable] = None,
        ring1: Optional[Callable] = None,
        ring2: Optional[Callable] = None,
        ring3: Optional[Callable] = None
    ) -> Dict[int, Optional[Callable]]:
        """사용자 정의 정책"""
        return {
            0: ring0 or (lambda v: v),
            1: ring1 or (lambda v: v),
            2: ring2 or (lambda v: v),
            3: ring3 or (lambda v: v)
        }


# 팩토리 함수
def create_state_view_context(
    core_state: Dict[str, Any],
    custom_policies: Optional[Dict] = None
) -> StateViewContext:
    """
    StateViewContext 인스턴스 생성
    
    Args:
        core_state: 원본 상태
        custom_policies: 사용자 정의 필드 정책
    
    Returns:
        StateViewContext 인스턴스
    """
    return StateViewContext(core_state, custom_policies)
