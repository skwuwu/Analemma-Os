"""
State Rehydration Mixin — Memory Ghosting 문제 해결

에이전트 상태 직렬화의 근본 한계(Memory Ghosting):
  - `json.dumps(agent.__dict__)` → VectorStore 등 직렬화 불가 객체 포함 시 TypeError
  - `pickle.dumps(agent)` → 보안 취약, Python 버전 의존
  - 전체 객체 그래프 재귀 직렬화 → Circular Reference RecursionError

해결 전략 — State Rehydration:
  - snapshot_fields    : JSON 직렬화 가능한 원시값 필드만 스냅샷
  - rehydratable_fields: 재시작 시 재생성할 무거운 객체 (VectorStore 등)
  - rehydrate()        : 에이전트가 직접 선언하는 재생성 로직 override 포인트

안전 직렬화 (_safe_serialize):
  - max_depth: 재귀 깊이 제한 (기본 5). 초과 시 플레이스홀더 반환.
  - circular_reference: `seen` set(id())으로 감지, 플레이스홀더 반환.
  - 직렬화 불가 타입: str() 변환 후 경고 로그.

사용 예시:
    class MyAgent(StateRehydrationMixin, BaseAgent):
        snapshot_fields = ["retry_count", "current_step", "short_term_memory"]
        rehydratable_fields = ["vector_store", "llm_client"]

        def rehydrate(self, snapshot: dict):
            self.vector_store = VectorStore.from_config(self.config)
            self.llm_client = LLMClient(api_key=os.environ["LLM_KEY"])
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ─── 안전 직렬화 헬퍼 ────────────────────────────────────────────────────────

def _safe_serialize(val: Any, max_depth: int, seen: set[int], path: str) -> Any:
    """
    재귀 안전 직렬화.

    3가지 보호 장치:
      1. max_depth <= 0 → `<max_depth_exceeded: {path}>` 플레이스홀더 반환
      2. Circular Reference (id 기반) → `<circular_ref: {path}>` 플레이스홀더 반환
      3. JSON 직렬화 불가 타입 → `<non_serializable: {type}: {path}>` 플레이스홀더 반환

    Args:
        val:       직렬화할 값.
        max_depth: 남은 허용 재귀 깊이.
        seen:      현재 재귀 경로에서 방문한 객체 id 집합.
        path:      오류 메시지용 필드 경로 (예: "memory.messages[0]").

    Returns:
        JSON 직렬화 가능한 값 또는 플레이스홀더 문자열.
    """
    # ─ 깊이 제한 ─
    if max_depth <= 0:
        logger.warning(
            "[StateRehydration] max_depth exceeded at path='%s'. "
            "Field will be replaced with placeholder. Consider increasing max_depth "
            "or moving to rehydratable_fields.", path,
        )
        return f"<max_depth_exceeded: {path}>"

    # ─ 원시 타입은 즉시 반환 (id 추적 불필요) ─
    if isinstance(val, (bool, int, float, str, type(None))):
        return val

    # ─ Circular Reference 감지 ─
    obj_id = id(val)
    if obj_id in seen:
        logger.warning(
            "[StateRehydration] Circular reference detected at path='%s'. "
            "Consider moving the field to rehydratable_fields.", path,
        )
        return f"<circular_ref: {path}>"

    # ─ dict 재귀 ─
    if isinstance(val, dict):
        seen.add(obj_id)
        result = {
            str(k): _safe_serialize(v, max_depth - 1, seen, f"{path}.{k}")
            for k, v in val.items()
        }
        seen.discard(obj_id)
        return result

    # ─ list / tuple 재귀 ─
    if isinstance(val, (list, tuple)):
        seen.add(obj_id)
        result = [
            _safe_serialize(item, max_depth - 1, seen, f"{path}[{i}]")
            for i, item in enumerate(val)
        ]
        seen.discard(obj_id)
        return result if isinstance(val, list) else tuple(result)

    # ─ JSON 직렬화 가능 여부 최종 확인 ─
    try:
        json.dumps(val)
        return val
    except (TypeError, ValueError):
        type_name = type(val).__name__
        logger.warning(
            "[StateRehydration] Non-serializable type '%s' at path='%s'. "
            "Consider moving to rehydratable_fields.", type_name, path,
        )
        return f"<non_serializable: {type_name} at {path}>"


# ─── Mixin ────────────────────────────────────────────────────────────────────

class StateRehydrationMixin:
    """
    State Rehydration 전략 구현 믹스인.

    에이전트 클래스가 상속하면 snapshot / restore 자동 지원.
    직렬화 가능한 핵심 컨텍스트만 스냅샷하고,
    무거운 객체(VectorStore, LLM 클라이언트 등)는 재시작 시 재생성한다.

    Attributes:
        snapshot_fields:     직렬화할 원시값 필드명 목록.
                             int, float, str, bool, list, dict 등
                             JSON 직렬화 가능한 값이어야 한다.
        rehydratable_fields: 재시작 시 재생성(Rehydration)할 무거운 객체 필드명 목록.
                             스냅샷에서 제외되며, rehydrate() 내에서 재생성해야 한다.
    """

    snapshot_fields: list[str] = []
    rehydratable_fields: list[str] = []

    def extract_snapshot(self, max_depth: int = 5) -> dict[str, Any]:
        """
        안전 직렬화(max_depth + Circular Reference 감지)로 핵심 컨텍스트 추출.

        rehydratable_fields는 스냅샷에서 제외된다.
        직렬화 불가 필드는 플레이스홀더 문자열로 대체되며 경고 로그가 발행된다.

        Args:
            max_depth: 재귀 깊이 제한 (기본 5).
                       중첩 dict/list 구조가 깊으면 높여도 되지만,
                       직렬화 시간이 늘어나므로 가급적 낮게 유지한다.

        Returns:
            dict: {field_name: safe_value, ...}
        """
        snapshot: dict[str, Any] = {}
        for field in self.snapshot_fields:
            val = getattr(self, field, None)
            safe_val = _safe_serialize(val, max_depth=max_depth, seen=set(), path=field)
            snapshot[field] = safe_val
        return snapshot

    def restore_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        """
        스냅샷에서 원시값을 복원한 뒤 rehydrate()를 호출하여 무거운 객체를 재생성.

        플레이스홀더 값("<max_depth_exceeded: ...>" 등)은 그대로 복원되므로,
        rehydrate()에서 해당 필드를 직접 초기화해야 한다.

        Args:
            snapshot: extract_snapshot()이 반환한 dict.
        """
        for field, value in snapshot.items():
            setattr(self, field, value)
        self.rehydrate(snapshot)

    def rehydrate(self, snapshot: dict[str, Any]) -> None:
        """
        무거운 객체(VectorStore, LLM 클라이언트 등) 재생성 로직 override 포인트.

        기본 구현은 아무것도 하지 않는다 (순수 원시값만 가진 에이전트 대상).
        무거운 객체가 있는 에이전트는 이 메서드를 override해야 한다.

        Args:
            snapshot: 복원된 원시값 dict (참고용).

        Example::

            def rehydrate(self, snapshot: dict):
                self.vector_store = VectorStore.from_config(self.config)
                self.llm_client = LLMClient(api_key=os.environ["LLM_KEY"])
                self.db_session = create_session(self.db_url)
        """
        pass

    def validate_snapshot(self) -> list[str]:
        """
        현재 snapshot_fields 중 직렬화 문제가 있는 필드 목록 반환.
        설정 오류를 조기에 감지하는 용도로 사용.

        플레이스홀더 문자열("<...>")로 치환된 필드를 반환한다.

        Returns:
            list[str]: 문제 필드명 목록 (비어있으면 모두 정상).
        """
        problems: list[str] = []
        snapshot = self.extract_snapshot()
        for field, val in snapshot.items():
            if isinstance(val, str) and val.startswith("<") and val.endswith(">"):
                problems.append(field)
        return problems
