"""
Analemma Bridge — Loop Virtualization SDK

에이전트의 비정형 TAO 루프(Thought-Action-Observation)를
Analemma 커널의 결정론적 세그먼트로 변환하는 브릿지 레이어.

─── 배포 분리 ────────────────────────────────────────────────────────────────

  [SDK — 에이전트 프로세스에 포함]
    from src.bridge import AnalemmaBridge, LocalL1Checker, StateRehydrationMixin
    pip install analemma-bridge-sdk   (향후 독립 패키지)

  [Server — VirtualSegmentManager 독립 프로세스]
    uvicorn backend.src.bridge.virtual_segment_manager:app --port 8765
    (에이전트 프로세스와 분리 실행)

  두 컴포넌트를 같은 프로세스에서 실행하는 것은 개발 편의를 위한 것일 뿐,
  프로덕션에서는 반드시 분리하여 배포한다.

─── 환경 변수 ────────────────────────────────────────────────────────────────

  ANALEMMA_KERNEL_ENDPOINT  : VSM 서버 URL (기본: http://localhost:8765)
  ANALEMMA_REDIS_URL        : Audit Registry Redis URL (미설정 시 in-memory)
  ANALEMMA_AUDIT_TTL_SECONDS: Audit TTL (기본: 3600초)
  ANALEMMA_SYNC_POLICY      : "1" 설정 시 AnalemmaBridge 초기화 시 Policy Sync 자동 수행

─── 주요 컴포넌트 ────────────────────────────────────────────────────────────

  SDK:
    AnalemmaBridge        — Python 에이전트 컨텍스트 매니저
    SegmentResult         — SEGMENT_COMMIT 파싱 결과
    LocalL1Checker        — Optimistic Mode 로컬 보안 검사기
    L1Result              — L1 검사 결과
    StateRehydrationMixin — 에이전트 상태 스냅샷/복원 믹스인

  Server (별도 프로세스):
    virtual_segment_manager.app  — FastAPI ASGI 앱 (직접 import 불필요)
    ReorderingBuffer             — VSM 내부 순서 보장 버퍼 (테스트용)

  Shared:
    BridgeRingLevel  — Ring 레벨 Enum
    CAPABILITY_MAP   — Ring별 허용 도구 화이트리스트 (단일 출처)
"""

# ── SDK exports (에이전트 라이브러리) ─────────────────────────────────────────
from .python_bridge import AnalemmaBridge, SegmentResult, SecurityViolation
from .local_l1_checker import LocalL1Checker, L1Result
from .rehydration import StateRehydrationMixin
from .shared_policy import BridgeRingLevel, CAPABILITY_MAP, get_allowed_tools

# ── Server exports (테스트 / 진단용) ──────────────────────────────────────────
# 주의: 프로덕션 에이전트 코드에서 vsm_app을 직접 import하지 말 것.
# VirtualSegmentManager는 별도 uvicorn 프로세스로 실행한다.
from .virtual_segment_manager import ReorderingBuffer  # 테스트용 접근

__all__ = [
    # SDK
    "AnalemmaBridge",
    "SegmentResult",
    "SecurityViolation",
    "LocalL1Checker",
    "L1Result",
    "StateRehydrationMixin",
    # Shared
    "BridgeRingLevel",
    "CAPABILITY_MAP",
    "get_allowed_tools",
    # Server (테스트용)
    "ReorderingBuffer",
]
