"""
AnalemmaBridge — Python 에이전트용 Loop Virtualization Bridge SDK

에이전트의 비정형 TAO 루프(Thought-Action-Observation)를
Analemma 커널의 결정론적 세그먼트(SEGMENT_PROPOSE / SEGMENT_COMMIT)로 변환.

핵심 기능:
  1. Strict Mode     : PROPOSE → 커널 동기 승인 → 실행 (사전 검증, 보안 최상)
  2. Optimistic Mode : L1 로컬 검사 → 즉시 실행 → 비동기 커널 보고 (레이턴시 최소)
  3. Hybrid Interceptor:
     Optimistic Mode 실행 중 파괴적 행동(rm -rf, DROP TABLE 등) 감지 시
     effective_mode를 자동으로 "strict"로 전환.
     되돌릴 수 없는 행동은 반드시 사전 승인을 거친다.

사용 예시:
    bridge = AnalemmaBridge(
        workflow_id="wf_123",
        ring_level=2,
        mode="optimistic",     # Hybrid Interceptor 자동 활성화
    )

    with bridge.segment(
        thought="I need to read the billing report.",
        action="s3_get_object",
        params={"bucket": "billing", "key": "report.json"},
    ) as seg:
        if seg.allowed:
            result = s3_client.get_object(...)
            seg.report_observation(result)

    # 파괴적 행동 — Hybrid Interceptor가 자동으로 Strict Mode 강제
    with bridge.segment(
        thought="I need to delete the temp file.",
        action="filesystem_delete",
        params={"path": "/tmp/work.tmp"},
    ) as seg:
        if seg.allowed:          # Strict Mode로 커널 승인 후 실행
            os.remove("/tmp/work.tmp")
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional

from .local_l1_checker import LocalL1Checker, L1Result
from .shared_policy import DESTRUCTIVE_ACTIONS, DESTRUCTIVE_PATTERNS

logger = logging.getLogger(__name__)

# 환경 변수: VSM 서버 URL (기본값: localhost:8765)
_DEFAULT_KERNEL_ENDPOINT: str = os.environ.get(
    "ANALEMMA_KERNEL_ENDPOINT", "http://localhost:8765"
)
# 환경 변수: 초기화 시 Policy Sync 자동 수행 여부
_AUTO_SYNC_POLICY: bool = os.environ.get("ANALEMMA_SYNC_POLICY", "").strip() == "1"


# ─── Hybrid Interceptor: 파괴적 행동 분류 ─────────────────────────────────────
# DESTRUCTIVE_ACTIONS / DESTRUCTIVE_PATTERNS 는 shared_policy.py 단일 출처 사용.
# types.ts의 동명 상수와 /v1/policy/sync 응답을 통해 동기화된다.

_COMPILED_DESTRUCTIVE_PATTERNS = [
    re.compile(p, re.IGNORECASE | re.UNICODE) for p in DESTRUCTIVE_PATTERNS
]


# ─── 세그먼트 결과 ─────────────────────────────────────────────────────────────

@dataclass
class SegmentResult:
    """SEGMENT_COMMIT 응답 파싱 결과."""
    status: str
    checkpoint_id: str
    action_override: Optional[Dict[str, Any]] = None
    governance_feedback: Optional[Dict[str, Any]] = None
    recovery_instruction: Optional[str] = None

    @property
    def allowed(self) -> bool:
        return self.status in ("APPROVED", "MODIFIED")

    @property
    def should_kill(self) -> bool:
        return self.status == "SIGKILL"

    @property
    def should_rollback(self) -> bool:
        return self.status == "SOFT_ROLLBACK"


# ─── 세그먼트 핸들 (컨텍스트 매니저 내 노출) ───────────────────────────────────

class _SegmentHandle:
    """
    `with bridge.segment(...) as seg:` 블록에서 에이전트에 노출되는 핸들.

    속성:
        allowed      : 행동 실행 허가 여부.
        should_kill  : 에이전트 즉시 종료 요청.
        should_rollback: 이전 체크포인트로 되감기 요청.
        action_params: 커널이 승인(또는 수정)한 파라미터.
        checkpoint_id: 현재 세그먼트의 Merkle DAG 체크포인트 ID.
    """

    def __init__(self, commit: SegmentResult, original_params: Dict[str, Any]):
        self._commit = commit
        self._observation: Optional[Any] = None
        self.action_params: Dict[str, Any] = commit.action_override or original_params

    @property
    def allowed(self) -> bool:
        return self._commit.allowed

    @property
    def should_kill(self) -> bool:
        return self._commit.should_kill

    @property
    def should_rollback(self) -> bool:
        return self._commit.should_rollback

    @property
    def checkpoint_id(self) -> str:
        return self._commit.checkpoint_id

    @property
    def recovery_instruction(self) -> Optional[str]:
        return self._commit.recovery_instruction

    def report_observation(self, observation: Any) -> None:
        """행동 결과를 커널에 보고하기 위해 저장."""
        self._observation = observation


# ─── Optimistic Mode 핸들 ──────────────────────────────────────────────────────

class _OptimisticHandle:
    """Optimistic Mode에서 즉시 실행 허가 핸들."""

    def __init__(self, original_params: Dict[str, Any]):
        self._observation: Optional[Any] = None
        self.action_params: Dict[str, Any] = original_params

    allowed = True
    should_kill = False
    should_rollback = False
    checkpoint_id = "optimistic_local"
    recovery_instruction = None

    def report_observation(self, observation: Any) -> None:
        self._observation = observation


# ─── AnalemmaBridge ────────────────────────────────────────────────────────────

class AnalemmaBridge:
    """
    Analemma Bridge SDK — Python 에이전트용.

    에이전트의 각 행동을 `segment()` 컨텍스트 매니저로 감싸면
    Analemma 커널의 거버넌스 파이프라인을 통과시킨다.

    Args:
        workflow_id:      워크플로 고유 ID.
        ring_level:       에이전트의 Ring 레벨 (0=커널, 3=외부사용자).
        kernel_endpoint:  VirtualSegmentManager 서버 URL.
        mode:             "strict" | "optimistic".
                          Hybrid Interceptor는 "optimistic"에서 자동 활성화.
    """

    def __init__(
        self,
        workflow_id: str,
        ring_level: int = 3,
        kernel_endpoint: Optional[str] = None,
        mode: str = "strict",
        sync_policy: Optional[bool] = None,
    ):
        """
        Args:
            workflow_id:      워크플로 고유 ID.
            ring_level:       에이전트의 Ring 레벨 (0=KERNEL, 3=USER).
            kernel_endpoint:  VSM 서버 URL.
                              None이면 ANALEMMA_KERNEL_ENDPOINT 환경 변수 사용.
                              환경 변수도 없으면 http://localhost:8765.
            mode:             "strict" | "optimistic".
            sync_policy:      True이면 초기화 시 /v1/policy/sync 호출.
                              None이면 ANALEMMA_SYNC_POLICY 환경 변수 사용.
        """
        self.workflow_id = workflow_id
        self.ring_level = ring_level
        self.kernel_endpoint = kernel_endpoint or _DEFAULT_KERNEL_ENDPOINT
        self.mode = mode

        self._loop_index: int = 0
        self._parent_segment_id: Optional[str] = None
        self._lock = threading.Lock()
        self._l1_checker = LocalL1Checker()

        # Policy Sync: 커널 최신 패턴 다운로드 (선택적)
        should_sync = sync_policy if sync_policy is not None else _AUTO_SYNC_POLICY
        if should_sync:
            synced = self._l1_checker.sync_from_kernel(self.kernel_endpoint)
            if synced:
                logger.info(
                    "[AnalemmaBridge] Policy synced. version=%s",
                    self._l1_checker.policy_version,
                )
            else:
                logger.warning(
                    "[AnalemmaBridge] Policy sync failed. Using local defaults."
                )

    # ── 공개 API ───────────────────────────────────────────────────────────────

    @contextmanager
    def segment(
        self,
        thought: str,
        action: str,
        params: Dict[str, Any],
        segment_type: str = "TOOL_CALL",
        state_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Iterator[_SegmentHandle | _OptimisticHandle]:
        """
        에이전트 행동을 커널 거버넌스 하에 실행하는 컨텍스트 매니저.

        Hybrid Interceptor:
          mode="optimistic"이더라도 `action`이 DESTRUCTIVE_ACTIONS에 포함되거나
          `thought`/`params`에서 파괴적 패턴이 감지되면 effective_mode를
          자동으로 "strict"로 전환하여 커널 사전 승인을 요구한다.

        Args:
            thought:       에이전트의 현재 사고 텍스트.
            action:        실행할 액션 이름.
            params:        액션 파라미터 dict.
            segment_type:  TOOL_CALL | LLM_CALL | MEMORY_UPDATE | FINAL.
            state_snapshot: 직렬화 가능한 상태 스냅샷 (선택).

        Yields:
            _SegmentHandle (Strict) 또는 _OptimisticHandle (Optimistic).

        Example::

            with bridge.segment("Read billing report.", "s3_get_object", params) as seg:
                if seg.allowed:
                    result = s3.get_object(...)
                    seg.report_observation(result)
        """
        with self._lock:
            self._loop_index += 1
            loop_index = self._loop_index

        # ── Hybrid Interceptor ─────────────────────────────────────────────────
        effective_mode = self.mode
        if effective_mode == "optimistic" and self._is_destructive(action, thought, params):
            effective_mode = "strict"
            logger.warning(
                "[HybridInterceptor] Destructive action detected in Optimistic Mode. "
                "Forcing STRICT mode. action=%s workflow=%s loop=%d",
                action, self.workflow_id, loop_index,
            )

        if effective_mode == "optimistic":
            yield from self._optimistic_segment(
                thought, action, params, loop_index
            )
        else:
            yield from self._strict_segment(
                thought, action, params, segment_type, loop_index, state_snapshot
            )

    # ── Strict Mode ────────────────────────────────────────────────────────────

    @contextmanager
    def _strict_segment(
        self,
        thought: str,
        action: str,
        params: Dict[str, Any],
        segment_type: str,
        loop_index: int,
        state_snapshot: Optional[Dict[str, Any]],
    ) -> Iterator[_SegmentHandle]:
        """PROPOSE → 커널 동기 승인 → 실행."""
        proposal = self._build_proposal(
            thought, action, params, segment_type, loop_index, state_snapshot
        )
        commit = self._send_propose(proposal)
        seg = _SegmentHandle(commit, params)

        try:
            yield seg
            if seg._observation is not None:
                self._send_observation(commit.checkpoint_id, seg._observation)
        except Exception as exc:
            self._send_failure(commit.checkpoint_id, str(exc))
            raise
        finally:
            self._parent_segment_id = commit.checkpoint_id

    # ── Optimistic Mode ────────────────────────────────────────────────────────

    @contextmanager
    def _optimistic_segment(
        self,
        thought: str,
        action: str,
        params: Dict[str, Any],
        loop_index: int,
    ) -> Iterator[_OptimisticHandle]:
        """L1 로컬 검사 → 즉시 실행 → 비동기 커널 보고."""
        l1_result: L1Result = self._l1_checker.check(
            thought=thought,
            action=action,
            ring_level=self.ring_level,
            params=params,
        )
        if not l1_result.allowed:
            # L1 차단 — SecurityViolation 발생 (에이전트가 잡을 수 있음)
            raise SecurityViolation(f"[L1 Blocked] {l1_result.reason}")

        seg = _OptimisticHandle(params)
        try:
            yield seg
        finally:
            # 사후 비동기 보고 (fire-and-forget)
            self._async_report(thought, action, params, loop_index, seg._observation)

    def _async_report(
        self,
        thought: str,
        action: str,
        params: Dict[str, Any],
        loop_index: int,
        observation: Optional[Any],
    ) -> None:
        """별도 스레드에서 커널에 비동기 보고."""
        def _do_report():
            try:
                proposal = self._build_proposal(
                    thought, action, params, "TOOL_CALL", loop_index, None
                )
                import requests
                requests.post(
                    f"{self.kernel_endpoint}/v1/segment/propose",
                    json=proposal,
                    timeout=10,
                )
            except Exception as exc:
                logger.debug(
                    "[Bridge] Async report failed (non-critical): %s", exc
                )

        t = threading.Thread(target=_do_report, daemon=True)
        t.start()

    # ── Hybrid Interceptor 로직 ────────────────────────────────────────────────

    def _is_destructive(
        self,
        action: str,
        thought: str,
        params: Optional[Dict[str, Any]],
    ) -> bool:
        """
        행동이 파괴적(비가역적)인지 판별.

        두 가지 기준:
          1. action이 DESTRUCTIVE_ACTIONS frozenset에 포함되는가. (O(1))
          2. thought + params 텍스트에서 DESTRUCTIVE_PATTERNS 정규식이 매칭되는가.

        Args:
            action:  액션 이름.
            thought: 에이전트 사고 텍스트.
            params:  액션 파라미터 dict.

        Returns:
            True  — 파괴적 행동 (Strict Mode 강제 필요)
            False — 안전한 행동 (Optimistic Mode 유지 가능)
        """
        # 1. frozenset 룩업 (O(1), 대소문자 무시)
        if action.lower() in DESTRUCTIVE_ACTIONS:
            return True

        # 2. 정규식 패턴 스캔
        scan_text = thought
        if params:
            try:
                scan_text += " " + json.dumps(params)
            except (TypeError, ValueError):
                scan_text += " " + str(params)

        for pattern in _COMPILED_DESTRUCTIVE_PATTERNS:
            if pattern.search(scan_text):
                logger.debug(
                    "[HybridInterceptor] Destructive pattern matched: %s",
                    pattern.pattern,
                )
                return True

        return False

    # ── 커널 통신 ───────────────────────────────────────────────────────────────

    def _build_proposal(
        self,
        thought: str,
        action: str,
        params: Dict[str, Any],
        segment_type: str,
        loop_index: int,
        state_snapshot: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """SEGMENT_PROPOSE 페이로드 구성."""
        content = (
            f"{self.workflow_id}:loop_{loop_index}:{action}:"
            f"{json.dumps(params, sort_keys=True)}"
        )
        idempotency_key = hashlib.sha256(content.encode()).hexdigest()[:16]

        return {
            "protocol_version": "1.0",
            "op": "SEGMENT_PROPOSE",
            "idempotency_key": idempotency_key,
            "segment_context": {
                "workflow_id": self.workflow_id,
                "parent_segment_id": self._parent_segment_id,
                "loop_index": loop_index,
                "segment_type": segment_type,
                "sequence_number": loop_index,
                "ring_level": self.ring_level,
            },
            "payload": {
                "thought": thought,
                "action": action,
                "action_params": params,
            },
            "state_snapshot": state_snapshot or {},
        }

    def _send_propose(self, proposal: Dict[str, Any]) -> SegmentResult:
        """커널에 SEGMENT_PROPOSE 전송 → SEGMENT_COMMIT 수신 파싱."""
        import requests
        try:
            resp = requests.post(
                f"{self.kernel_endpoint}/v1/segment/propose",
                json=proposal,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return SegmentResult(
                status=data["status"],
                checkpoint_id=data["checkpoint_id"],
                action_override=(
                    data.get("commands", {}).get("action_override")
                ),
                governance_feedback=data.get("governance_feedback"),
                recovery_instruction=(
                    data.get("commands", {}).get("inject_recovery_instruction")
                ),
            )
        except Exception as exc:
            logger.warning(
                "[Bridge] Kernel unreachable, fail-open: %s", exc
            )
            # 커널 불가 시 Fail-Open (APPROVED) — 프로덕션 환경에서는 Fail-Closed 옵션 고려
            return SegmentResult(status="APPROVED", checkpoint_id="local_only")

    def _send_observation(self, checkpoint_id: str, observation: Any) -> None:
        """행동 결과 커널 보고 (비치명적 실패 무시)."""
        import requests
        try:
            requests.post(
                f"{self.kernel_endpoint}/v1/segment/observe",
                json={
                    "checkpoint_id": checkpoint_id,
                    "observation": str(observation),
                    "status": "SUCCESS",
                },
                timeout=5,
            )
        except Exception:
            pass

    def _send_failure(self, checkpoint_id: str, error: str) -> None:
        """세그먼트 실패 커널 보고 (비치명적 실패 무시)."""
        import requests
        try:
            requests.post(
                f"{self.kernel_endpoint}/v1/segment/fail",
                json={"checkpoint_id": checkpoint_id, "error": error},
                timeout=5,
            )
        except Exception:
            pass


# ─── 예외 ──────────────────────────────────────────────────────────────────────

class SecurityViolation(RuntimeError):
    """L1 검사 차단 또는 커널 SIGKILL 수신 시 발생."""
    pass
