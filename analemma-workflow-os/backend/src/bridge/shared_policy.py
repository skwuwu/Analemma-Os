"""
shared_policy.py — 브릿지 모듈 공유 정책 상수

CAPABILITY_MAP과 INJECTION_PATTERNS를 단일 출처(Single Source of Truth)로 관리.
local_l1_checker.py와 virtual_segment_manager.py 모두 이 파일을 임포트한다.

변경 시 두 곳을 따로 수정하는 실수를 구조적으로 차단한다.
"""

from __future__ import annotations

from enum import IntEnum


# ─── Ring Level Enum ──────────────────────────────────────────────────────────

class BridgeRingLevel(IntEnum):
    """
    에이전트 Ring 레벨 (브릿지 레이어 전용 Enum).

    governance_engine.RingLevel과 동일한 값을 사용하되,
    순환 임포트 없이 브릿지 레이어에서 독립적으로 참조 가능하다.

    값:
        KERNEL  (0) — 커널 자체, 무제한 권한
        DRIVER  (1) — 드라이버 레이어, 신뢰 but 범위 제한
        SERVICE (2) — 서비스 레이어, 읽기/조회 중심
        USER    (3) — 외부 사용자 에이전트, 최소 권한
    """
    KERNEL = 0
    DRIVER = 1
    SERVICE = 2
    USER = 3

    @classmethod
    def from_int(cls, value: int) -> "BridgeRingLevel":
        """int → BridgeRingLevel. 범위 외 값은 USER(3)로 클램프."""
        try:
            return cls(value)
        except ValueError:
            return cls.USER


# ─── Ring 이름 ────────────────────────────────────────────────────────────────

RING_NAMES: dict[int, str] = {
    BridgeRingLevel.KERNEL: "KERNEL",
    BridgeRingLevel.DRIVER: "DRIVER",
    BridgeRingLevel.SERVICE: "SERVICE",
    BridgeRingLevel.USER: "USER",
}


# ─── Capability Map (Ring별 허용 도구 화이트리스트) ───────────────────────────

CAPABILITY_MAP: dict[BridgeRingLevel, frozenset[str]] = {
    BridgeRingLevel.KERNEL: frozenset({"*"}),   # 무제한 (와일드카드 심볼)
    BridgeRingLevel.DRIVER: frozenset({
        "filesystem_read", "subprocess_call", "network_limited",
        "database_write", "config_read", "network_read",
        "database_query", "cache_read", "event_publish",
        "basic_query", "read_only", "s3_get_object", "s3_put_object",
    }),
    BridgeRingLevel.SERVICE: frozenset({
        "network_read", "database_query", "cache_read",
        "event_publish", "basic_query", "read_only", "s3_get_object",
    }),
    BridgeRingLevel.USER: frozenset({
        "basic_query", "read_only",
    }),
}

# int 키 버전 — virtual_segment_manager.py 호환성
CAPABILITY_MAP_INT: dict[int, frozenset[str]] = {
    int(k): v for k, v in CAPABILITY_MAP.items()
}


# ─── 인젝션 패턴 (SemanticShield Stage 2 핵심 서브셋) ────────────────────────

INJECTION_PATTERNS: list[str] = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions|context)",
    r"you\s+are\s+now\s+(?:in\s+)?(?:developer|jailbreak|dan)\s+mode",
    r"system\s+prompt\s+(?:reveal|show|display|output)",
    r"print\s+(?:your\s+)?(?:system\s+)?instructions",
    r"act\s+as\s+(?:if\s+)?(?:you\s+(?:have\s+)?no\s+restrictions|an?\s+unrestricted)",
    r"이전\s+지시(?:사항)?\s*(?:무시|삭제|초기화)",
    r"시스템\s+프롬프트\s*(?:누설|출력|보여|공개)",
    r"제한\s*(?:없이|해제|무시)",
]


# ─── Hybrid Interceptor: 파괴적 행동 (Single Source of Truth) ─────────────────
# python_bridge.py와 ts/src/types.ts가 이 목록을 공용 출처로 참조한다.
# VSM /v1/policy/sync 응답에도 포함되어 TS 클라이언트가 동기화할 수 있다.

DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset({
    # 파일시스템 파괴
    "filesystem_write", "filesystem_delete", "rm", "rmdir", "truncate",
    # 프로세스 / 쉘 (임의 명령 가능)
    "shell_exec", "subprocess_call",
    # 데이터베이스 파괴
    "database_delete", "database_drop",
    # 클라우드 스토리지 삭제
    "s3_delete", "s3_delete_objects",
    # 기타
    "format", "wipe",
})

DESTRUCTIVE_PATTERNS: list[str] = [
    r"rm\s+-[rf]+",                     # rm -rf, rm -r, rm -f
    r"drop\s+table",                    # SQL DROP TABLE
    r"delete\s+from",                   # SQL DELETE FROM
    r"truncate\s+(?:table\s+)?\w+",     # SQL TRUNCATE TABLE
    r"format\s+(?:disk|drive|c:)",      # 디스크 포맷
    r"mkfs\.",                          # 리눅스 파일시스템 포맷
    r"dd\s+if=.+of=/dev/",             # 디스크 오버라이트
    r"파일\s*삭제",                      # 한국어 파괴 패턴
    r"데이터베이스\s*(?:삭제|드롭)",
    r"전체\s*삭제",
    r"모두\s*삭제",
]


def get_allowed_tools(ring_level: int) -> frozenset[str]:
    """
    ring_level에 해당하는 허용 도구 집합 반환.

    Ring 0(KERNEL)은 모든 도구를 허용하므로 빈 집합이 아닌 {"*"} 반환.
    Default-Deny: 알 수 없는 ring_level은 USER(3)와 동일하게 처리.

    Args:
        ring_level: int (0–3).

    Returns:
        frozenset[str]: 허용된 도구 이름 집합.
    """
    level = BridgeRingLevel.from_int(ring_level)
    return CAPABILITY_MAP.get(level, frozenset())


def is_capability_allowed(ring_level: int, action: str) -> bool:
    """
    지정한 ring_level에서 action이 허용되는지 확인.

    Default-Deny: 화이트리스트 미등재 도구는 항상 False.

    Args:
        ring_level: int (0–3).
        action:     확인할 도구/액션 이름.

    Returns:
        bool: 허용 여부.
    """
    level = BridgeRingLevel.from_int(ring_level)
    if level == BridgeRingLevel.KERNEL:
        return True  # KERNEL: 무제한
    allowed = CAPABILITY_MAP.get(level, frozenset())
    return action in allowed
