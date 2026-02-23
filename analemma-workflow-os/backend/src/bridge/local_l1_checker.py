"""
LocalL1Checker — Optimistic Mode 브릿지 내장 경량 보안 검사기

네트워크 왕복 없이 즉시 실행 (~1ms 목표).
커널의 SemanticShield Stage 1+2 핵심 패턴 서브셋 및
Capability Map을 로컬에 복사 탑재하여 L1 수준 차단을 수행한다.

설계 원칙:
  - 네트워크 의존성 없음 (오프라인 실행 가능)
  - frozenset 룩업 O(1) — Capability Map (shared_policy.py에서 가져옴)
  - 패턴 컴파일 캐싱 — re.compile() 사전 수행
  - ring_level: int 대신 BridgeRingLevel Enum으로 타입 안전 보장
  - 텍스트 정규화 (Zero-Width Space, RTL Override, Homoglyph) 후 패턴 매칭
  - params 스캔 크기 제한 (MAX_PARAMS_SCAN_BYTES) — 대용량 params 성능 보호
  - Policy Sync: inject_patterns() + sync_from_kernel()
"""

from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Optional

from .shared_policy import (
    CAPABILITY_MAP,
    INJECTION_PATTERNS,
    BridgeRingLevel,
    is_capability_allowed,
)

logger = logging.getLogger(__name__)

# params JSON 스캔 최대 바이트 (성능 보호)
MAX_PARAMS_SCAN_BYTES: int = 4_096

# Zero-Width 문자 + RTL Override 제거 대상
_ZERO_WIDTH_CHARS: str = (
    "\u200b"  # ZERO WIDTH SPACE
    "\u200c"  # ZERO WIDTH NON-JOINER
    "\u200d"  # ZERO WIDTH JOINER
    "\ufeff"  # ZERO WIDTH NO-BREAK SPACE (BOM)
    "\u202e"  # RIGHT-TO-LEFT OVERRIDE
    "\u202d"  # LEFT-TO-RIGHT OVERRIDE
)
_ZW_TABLE = str.maketrans("", "", _ZERO_WIDTH_CHARS)

# Homoglyph 정규화 — 흔한 키릴/그리스 유사 문자 → 라틴 ASCII 교체
# (SemanticShield NormalizationPipeline과 동일 목록 유지)
_HOMOGLYPH_MAP: dict[str, str] = {
    # 키릴 → 라틴
    "\u0430": "a",  # а → a
    "\u0435": "e",  # е → e
    "\u043e": "o",  # о → o
    "\u0440": "p",  # р → p
    "\u0441": "c",  # с → c
    "\u0445": "x",  # х → x
    # 그리스
    "\u03b1": "a",  # α → a
    "\u03bf": "o",  # ο → o
    # 수학 글꼴 유사 문자 (일부)
    "\u1d00": "a",  # ᴀ → a
    "\u1d07": "e",  # ᴇ → e
}
_HOMOGLYPH_TABLE = str.maketrans(_HOMOGLYPH_MAP)


@dataclass(frozen=True)
class L1Result:
    """L1 검사 결과."""
    allowed: bool
    reason: Optional[str] = None


def _normalize(text: str) -> str:
    """
    텍스트 정규화 파이프라인 (3단계).

    1. Zero-Width 문자 + RTL Override 제거
    2. NFKC 유니코드 정규화 (합성 문자 분해·재조합)
    3. Homoglyph 치환 (키릴·그리스 유사 문자 → 라틴)

    Args:
        text: 원본 텍스트.

    Returns:
        str: 정규화된 텍스트.
    """
    text = text.translate(_ZW_TABLE)           # 1. ZWS + RTL 제거
    text = unicodedata.normalize("NFKC", text) # 2. NFKC 정규화
    text = text.translate(_HOMOGLYPH_TABLE)    # 3. Homoglyph 치환
    return text


class LocalL1Checker:
    """
    브릿지 내장 경량 L1 보안 검사기.

    Optimistic Mode에서 네트워크 없이 즉시 실행 (~1ms).
    두 가지 검사를 수행한다:
      1. 텍스트 정규화 후 인젝션 패턴 매칭 (ZWS/Homoglyph 우회 방지)
      2. Capability Map 화이트리스트 확인 (ring_level별, Default-Deny)

    Policy Sync:
      inject_patterns()  — 외부에서 패턴/Capability 직접 주입
      sync_from_kernel() — VSM /v1/policy/sync 엔드포인트에서 자동 다운로드

    사용:
        checker = LocalL1Checker()
        checker.sync_from_kernel("http://localhost:8765")  # 선택적
        result = checker.check(thought="...", action="s3_get_object", ring_level=2)
        if not result.allowed:
            raise SecurityViolation(result.reason)
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # 인스턴스 레벨 패턴 (inject_patterns로 업데이트 가능)
        self._patterns_raw: list[str] = list(INJECTION_PATTERNS)
        self._compiled: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE | re.UNICODE)
            for p in self._patterns_raw
        ]
        # 인스턴스 레벨 Capability Map (inject_patterns로 업데이트 가능)
        self._capability_map: dict[BridgeRingLevel, frozenset[str]] = dict(CAPABILITY_MAP)
        self._policy_version: str = "local_default"

    # ── 공개 API ───────────────────────────────────────────────────────────────

    def check(
        self,
        thought: str,
        action: str,
        ring_level: int = 3,
        params: dict | None = None,
    ) -> L1Result:
        """
        L1 보안 검사 수행.

        텍스트 정규화(ZWS/Homoglyph 제거) 후 패턴 매칭을 수행하므로
        Unicode 우회 공격을 차단한다.

        params 스캔은 MAX_PARAMS_SCAN_BYTES(4KB) 까지만 수행하여
        대용량 params로 인한 1ms 목표 저해를 방지한다.

        Args:
            thought:    에이전트의 현재 사고(Thought) 텍스트.
            action:     실행 예정 액션 이름.
            ring_level: 에이전트의 Ring 레벨 (0–3, 기본 3).
            params:     액션 파라미터 dict (선택적, 크기 제한 적용).

        Returns:
            L1Result(allowed=True) — 통과
            L1Result(allowed=False, reason=...) — 차단
        """
        # 1. 텍스트 정규화 (ZWS, Homoglyph 제거)
        normalized_thought = _normalize(thought)
        normalized_action = _normalize(action)

        # params 직렬화 (크기 제한 적용)
        params_text = ""
        if params:
            try:
                raw = json.dumps(params)
                params_text = raw[:MAX_PARAMS_SCAN_BYTES]
                if len(raw) > MAX_PARAMS_SCAN_BYTES:
                    logger.debug(
                        "[LocalL1Checker] params truncated to %d bytes for scan "
                        "(original: %d bytes).", MAX_PARAMS_SCAN_BYTES, len(raw),
                    )
            except (TypeError, ValueError):
                params_text = str(params)[:MAX_PARAMS_SCAN_BYTES]
            params_text = _normalize(params_text)

        scan_text = f"{normalized_thought} {normalized_action} {params_text}"

        # 2. 인젝션 패턴 검사
        with self._lock:
            compiled = list(self._compiled)

        for pattern in compiled:
            if pattern.search(scan_text):
                logger.warning(
                    "[LocalL1Checker] Injection pattern matched. "
                    "pattern=%s action=%s ring=%d",
                    pattern.pattern, action, ring_level,
                )
                return L1Result(
                    allowed=False,
                    reason=f"L1 injection pattern blocked: {pattern.pattern}",
                )

        # 3. Capability Map 확인 (BridgeRingLevel Enum 기반)
        ring = BridgeRingLevel.from_int(ring_level)
        if not self._check_capability(ring, normalized_action):
            logger.warning(
                "[LocalL1Checker] Capability denied. action=%s ring=%s", action, ring.name,
            )
            return L1Result(
                allowed=False,
                reason=(
                    f"L1 capability denied: '{action}' not allowed at "
                    f"{ring.name} (Ring {ring_level})"
                ),
            )

        return L1Result(allowed=True)

    # ── Policy Sync ────────────────────────────────────────────────────────────

    def inject_patterns(
        self,
        injection_patterns: list[str],
        capability_map: dict[int, list[str]] | None = None,
        version: str | None = None,
    ) -> None:
        """
        커널에서 동기화된 최신 패턴 및 Capability Map 주입.

        sync_from_kernel()이 내부적으로 호출하며, 직접 호출도 가능.

        Args:
            injection_patterns: 새 인젝션 패턴 정규식 목록.
            capability_map:     {ring_level(int): [allowed_tool, ...]} (선택적).
            version:            정책 버전 식별자 (로그용).
        """
        with self._lock:
            self._patterns_raw = injection_patterns
            self._compiled = [
                re.compile(p, re.IGNORECASE | re.UNICODE)
                for p in injection_patterns
            ]
            if capability_map:
                self._capability_map = {
                    BridgeRingLevel.from_int(int(k)): frozenset(v)
                    for k, v in capability_map.items()
                }
            if version:
                self._policy_version = version

        logger.info(
            "[LocalL1Checker] Policy injected. patterns=%d rings=%d version=%s",
            len(self._patterns_raw), len(self._capability_map),
            self._policy_version,
        )

    def sync_from_kernel(
        self,
        kernel_endpoint: str,
        timeout: int = 5,
    ) -> bool:
        """
        VSM /v1/policy/sync 엔드포인트에서 최신 정책 다운로드 및 inject_patterns() 호출.

        실패 시 기존 로컬 패턴을 유지 (Fail-Open — 오프라인 환경 지원).

        Args:
            kernel_endpoint: VSM 서버 URL (예: "http://localhost:8765").
            timeout:         요청 타임아웃 (초).

        Returns:
            bool: 동기화 성공 여부.
        """
        import requests
        try:
            resp = requests.get(
                f"{kernel_endpoint}/v1/policy/sync",
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            # 현재 버전과 동일하면 재로드 불필요
            new_version = data.get("version")
            if new_version == self._policy_version:
                logger.debug(
                    "[LocalL1Checker] Policy already up-to-date (version=%s).", new_version,
                )
                return True

            self.inject_patterns(
                injection_patterns=data.get("injection_patterns", self._patterns_raw),
                capability_map=data.get("capability_map"),
                version=new_version,
            )
            logger.info(
                "[LocalL1Checker] Policy synced from kernel. "
                "version=%s patterns=%d", new_version, len(self._patterns_raw),
            )
            return True

        except Exception as exc:
            logger.warning(
                "[LocalL1Checker] Policy sync failed (using local defaults): %s", exc,
            )
            return False

    @property
    def policy_version(self) -> str:
        """현재 로드된 정책 버전."""
        return self._policy_version

    # ── 내부 헬퍼 ──────────────────────────────────────────────────────────────

    def _check_capability(self, ring: BridgeRingLevel, action: str) -> bool:
        """
        Capability Map 화이트리스트 확인 (BridgeRingLevel Enum 기반).

        Ring KERNEL은 모든 도구 허용.
        그 외 Ring은 명시적 화이트리스트 외 도구 차단 (Default-Deny).

        Args:
            ring:   BridgeRingLevel enum 값.
            action: 액션 이름 (정규화 완료 후).

        Returns:
            bool: 허용 여부.
        """
        if ring == BridgeRingLevel.KERNEL:
            return True

        with self._lock:
            allowed = self._capability_map.get(ring, frozenset())
        return action in allowed  # Default-Deny
