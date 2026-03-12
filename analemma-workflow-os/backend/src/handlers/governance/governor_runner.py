"""
🛡️ Governor Node Runner (Ring 1 Level)

Purpose:
    자율형 에이전트(Manus, Moltbot 등)의 출력을 검증하고
    _kernel 명령을 생성하여 동적 스케줄링을 제어합니다.

Architecture:
    - Ring 1 Security Level (Kernel보다 낮지만 Agent보다 높음)
    - Agent 출력의 "사후 검증" 수행 (Ring Protection은 "사전 검증")
    - Merkle Manifest 재생성 트리거 (Phase 8.3)

v2.1 Enhancements:
    - Optimistic Rollback: 비동기 검증 중 violation → 즉시 무결 상태로 복구
    - Feedback Loop: 에이전트에게 차단 사유 피드백 → Self-Correction 유도
    - S3 GC Integration: 롤백으로 버려진 블록 자동 정리

Integration:
    1. workflow_config에 governor_enabled: true 설정
    2. Agent 노드 실행 직후 자동으로 Governor 노드 실행
    3. Governor 출력(_kernel 명령)을 SegmentRunnerService가 처리
"""

import asyncio
import json
import os
import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


# ============================================================================
# 🔧 Async Compatibility Helper
# ============================================================================

def _get_verdict_sync(engine, output_text: str, context: dict):
    """
    GovernanceEngine.verify()를 동기 컨텍스트에서 안전하게 호출하는 헬퍼.

    Lambda Warm Start / FastAPI 내부 호출 모두 커버:
      - 이미 실행 중인 루프가 있으면 nest_asyncio로 중첩 실행 허용
      - 루프가 없으면 asyncio.run()으로 새 루프 생성

    nest_asyncio 설치: pip install nest_asyncio>=1.6.0
    """
    try:
        loop = asyncio.get_running_loop()
        # 실행 중인 루프 존재 → nest_asyncio 패치 후 중첩 실행
        import nest_asyncio
        nest_asyncio.apply(loop)
        return loop.run_until_complete(engine.verify(output_text, context))
    except RuntimeError:
        # 실행 중인 루프 없음 → 새 루프 생성
        return asyncio.run(engine.verify(output_text, context))


# ============================================================================
# 🛡️ Constants: Governance Configuration
# ============================================================================

class GovernanceMode(str, Enum):
    """거버넌스 모드"""
    OPTIMISTIC = "OPTIMISTIC"  # 비동기 검증 (로그 + 롤백)
    STRICT = "STRICT"          # 동기 검증 (즉시 차단)


class RingLevel(Enum):
    """Ring Protection Security Levels"""
    RING_0_KERNEL = 0      # Kernel commands only
    RING_1_DRIVER = 1      # Governor, critical validators
    RING_2_TRUSTED = 2     # Trusted tools, verified agents
    RING_3_USER = 3        # User-generated agents, external tools


# Ring-based Governance Mode Selection
GOVERNANCE_MODE_MAP = {
    RingLevel.RING_3_USER: GovernanceMode.OPTIMISTIC,
    RingLevel.RING_2_TRUSTED: GovernanceMode.OPTIMISTIC,
    RingLevel.RING_1_DRIVER: GovernanceMode.STRICT,
    RingLevel.RING_0_KERNEL: GovernanceMode.STRICT
}

# Kernel Control Keys (Reserved for Ring 0/1 only)
KERNEL_CONTROL_KEYS = {
    "_kernel_skip_segments",
    "_kernel_skip_reason",
    "_kernel_inject_recovery",
    "_kernel_rollback_to_manifest",
    "_kernel_rollback_reason",
    "_kernel_rollback_type",
    "_kernel_modify_parallelism",
    "_kernel_request_human_approval",
    "_kernel_terminate_workflow",       # [BUG-GX-03 FIX] TERMINAL_HALT 시 사용되는 키 추가.
    "_kernel_retry_current_segment",    # [BUG-GX-03 FIX] SOFT_ROLLBACK 시 사용되는 키 추가.
}


# ============================================================================
# 🛡️ Data Classes: Governance Decision Models
# ============================================================================

@dataclass
class AgentBehaviorAnalysis:
    """에이전트 행동 분석 결과"""
    agent_id: str
    execution_time_ms: float
    output_size_bytes: int
    token_count: Optional[int]
    plan_changed: bool
    plan_hash: Optional[str]
    retry_count: int
    violations: List[str]
    anomaly_score: float  # 0.0 (safe) ~ 1.0 (critical)
    ring_level: RingLevel


@dataclass
class GovernanceDecision:
    """Governor의 최종 결정"""
    approved: bool
    decision: str  # APPROVED / REJECTED / ESCALATED / ROLLBACK
    violations: List[str]
    kernel_commands: Dict[str, Any]
    audit_log: Dict[str, Any]
    governance_mode: GovernanceMode


# ============================================================================
# 🛡️ Governor Node Runner
# ============================================================================

def governor_node_runner(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Governor Node: 에이전트 출력 검증 및 _kernel 명령 생성
    
    Args:
        state: 전체 워크플로우 상태 (에이전트 출력 포함)
        config: Governor 노드 설정
            {
                "agent_node_id": "manus_planner",  # 검증 대상 에이전트 노드 ID
                "ring_level": 3,  # Agent의 Ring Level (default: Ring 3)
                "guardrails": {
                    "max_output_size_kb": 500,
                    "max_token_count": 10000,
                    "max_gas_fee_usd": 100,
                    "max_retry_count": 3
                }
            }
    
    Returns:
        Dict containing:
            - governance_decision: 승인/거부/에스컬레이션/롤백
            - _kernel_skip_segments: 스킵할 세그먼트 ID 리스트 (조건부)
            - _kernel_inject_recovery: 복구 세그먼트 삽입 요청 (조건부)
            - _kernel_rollback_to_manifest: 롤백 대상 Manifest ID (v2.1)
            - governance_audit_log: DynamoDB 저장용 audit log
    """
    start_time = time.time()
    
    # ────────────────────────────────────────────────────────────────────
    # 1. Extract Agent Output
    # ────────────────────────────────────────────────────────────────────
    agent_node_id = config.get("agent_node_id", "unknown_agent")
    agent_output_key = f"{agent_node_id}_output"
    agent_output = state.get(agent_output_key, {})
    
    if not agent_output:
        logger.warning(f"🟡 [Governor] No output from agent {agent_node_id}, skipping validation")
        return {
            "governance_decision": "SKIPPED",
            "reason": "No agent output to validate"
        }
    
    # ────────────────────────────────────────────────────────────────────
    # 2. Determine Governance Mode (Ring-based)
    # ────────────────────────────────────────────────────────────────────
    ring_level = RingLevel(config.get("ring_level", 3))
    governance_mode = GOVERNANCE_MODE_MAP.get(ring_level, GovernanceMode.OPTIMISTIC)
    
    logger.info(f"🔍 [Governor] Agent: {agent_node_id}, "
               f"Ring: {ring_level.name}, Mode: {governance_mode.value}")
    
    # ────────────────────────────────────────────────────────────────────
    # 3. Agent Behavior Analysis
    # ────────────────────────────────────────────────────────────────────
    analysis = _analyze_agent_behavior(
        agent_id=agent_node_id,
        agent_output=agent_output,
        workflow_state=state,
        config=config,
        ring_level=ring_level
    )
    
    logger.info(f"🔍 [Governor] Analysis complete: "
               f"Anomaly Score: {analysis.anomaly_score:.2f}, "
               f"Violations: {len(analysis.violations)}")
    
    # ────────────────────────────────────────────────────────────────────
    # 4. Generate Governance Decision
    # ────────────────────────────────────────────────────────────────────
    decision = _make_governance_decision(
        analysis=analysis,
        state=state,
        config=config,
        governance_mode=governance_mode
    )
    
    # ────────────────────────────────────────────────────────────────────
    # 4.5. Emit CloudWatch Metrics (v2.1)
    # ────────────────────────────────────────────────────────────────────
    # [BUG-GX-02 FIX] decision은 GovernanceDecision 객체이므로 .decision 문자열 필드를 전달해야 함.
    # 기존 코드는 객체 전체를 넘겨 CloudWatch 메트릭 파서가 "decision=GovernanceDecision(...)"를 받았음.
    _emit_governance_metrics(analysis, decision.decision)
    
    # ────────────────────────────────────────────────────────────────────
    # 5. Optimistic Rollback Policy (v2.1) - Differential Rollback Strategy
    # ────────────────────────────────────────────────────────────────────
    if governance_mode == GovernanceMode.OPTIMISTIC and decision.violations:
        logger.warning(f"🚨 [Optimistic Rollback] Violations detected: {decision.violations}")

        # Determine rollback type based on violation severity
        rollback_type = _determine_rollback_type(decision.violations)

        # [v3.35] Rollback budget enforcement — prevent infinite SOFT_ROLLBACK loops
        max_rollbacks = int(os.environ.get('MAX_ROLLBACKS_PER_EXECUTION', '5'))
        current_rollback_count = state.get('__rollback_count', 0)

        if rollback_type in ("SOFT_ROLLBACK", "HARD_ROLLBACK") and current_rollback_count >= max_rollbacks:
            logger.error(
                f"🚨 [Rollback Budget Exhausted] {current_rollback_count}/{max_rollbacks} rollbacks used. "
                f"Escalating {rollback_type} → TERMINAL_HALT to prevent resource exhaustion."
            )
            rollback_type = "TERMINAL_HALT"

        if rollback_type == "TERMINAL_HALT":
            # Security forgery detected or rollback budget exhausted → Immediate SIGKILL
            decision.kernel_commands["_kernel_terminate_workflow"] = {
                "reason": f"SECURITY_VIOLATION: {decision.violations[0]}",
                "severity": "CRITICAL",
                "rollback_count": current_rollback_count,
            }
            decision.decision = "REJECTED"
            logger.error(f"🚨 [TERMINAL_HALT] Workflow terminated due to security violation")

        elif rollback_type == "SOFT_ROLLBACK":
            # [v3.35] Increment rollback counter (protected by RESERVED_STATE_KEYS)
            state['__rollback_count'] = current_rollback_count + 1

            # Minor violation → Feedback + current segment retry
            decision.kernel_commands["_kernel_retry_current_segment"] = {
                "reason": f"Minor violation: {decision.violations[0]}",
                "rollback_attempt": current_rollback_count + 1,
                "max_rollbacks": max_rollbacks,
                "feedback_to_agent": _generate_agent_feedback(
                    violations=decision.violations,
                    agent_id=analysis.agent_id,
                    context={"output_size_bytes": analysis.output_size_bytes}
                )
            }
            decision.decision = "SOFT_ROLLBACK"
            logger.info(
                f"🔄 [SOFT_ROLLBACK] Retrying current segment with feedback "
                f"(attempt {current_rollback_count + 1}/{max_rollbacks})"
            )
            
        elif rollback_type == "HARD_ROLLBACK":
            # [v3.35] Increment rollback counter
            state['__rollback_count'] = current_rollback_count + 1

            # Critical violation → Get last safe manifest from DynamoDB
            last_safe_manifest = _get_last_safe_manifest(state)

            if last_safe_manifest:
                current_manifest_id = state.get("manifest_id", state.get("current_manifest_id"))

                decision.kernel_commands["_kernel_rollback_to_manifest"] = last_safe_manifest["manifest_id"]
                decision.kernel_commands["_kernel_rollback_reason"] = (
                    f"Critical violation detected: {decision.violations[0]}"
                )
                decision.kernel_commands["_kernel_rollback_type"] = "HARD_ROLLBACK"
                decision.decision = "ROLLBACK"

                logger.info(
                    f"⏪ [HARD_ROLLBACK] Rolling back to manifest: "
                    f"{last_safe_manifest['manifest_id']} "
                    f"(rollback {current_rollback_count + 1}/{max_rollbacks})"
                )
                
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # [v2.1] S3 GC Integration: Mark Rollback Orphans (HARD_ROLLBACK only)
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                if current_manifest_id and current_manifest_id != last_safe_manifest["manifest_id"]:
                    try:
                        from services.state.merkle_gc_service import mark_rollback_orphans
                        
                        orphan_stats = mark_rollback_orphans(
                            rollback_manifest_id=last_safe_manifest["manifest_id"],
                            abandoned_branch_root=current_manifest_id,
                            grace_period_days=7  # Shorter grace period for HARD_ROLLBACK (data corruption risk)
                        )
                        
                        logger.warning(
                            f"🗑️ [GC] [Rollback Orphans] Marked {orphan_stats['orphaned_manifests']} "
                            f"manifests and {orphan_stats['orphaned_blocks']} blocks for deletion. "
                            f"Expires: {orphan_stats['grace_period_expires_at']}"
                        )
                    except Exception as e:
                        logger.error(f"[GC] Failed to mark rollback orphans: {e}")
                        # Continue execution (don't block rollback)
    
    # ────────────────────────────────────────────────────────────────────
    # 6. Persist Audit Log (DynamoDB) + Update Manifest with governance_decision
    # ────────────────────────────────────────────────────────────────────
    _save_governance_audit_log(decision.audit_log)

    # [FIX] GovernanceDecisionIndex GSI는 workflow_id + governance_decision 복합키로
    # WorkflowManifestsV3 항목에서 governance_decision 필드를 조회한다.
    # 하지만 manifest 저장 코드에서 이 필드를 쓰지 않았으므로 GSI가 항상 빈 결과를 반환함.
    # Governor 결정 시 manifest 항목에 governance_decision을 기록해 rollback 복원 경로를 살린다.
    manifest_id_for_decision = state.get("manifest_id") or state.get("current_manifest_id")
    if manifest_id_for_decision:
        try:
            import boto3 as _boto3
            from datetime import datetime as _datetime
            _manifests_table_name = os.environ.get(
                "WORKFLOW_MANIFESTS_TABLE", "WorkflowManifests-v3-dev"
            )
            _manifests_table = _boto3.resource("dynamodb").Table(_manifests_table_name)
            _manifests_table.update_item(
                Key={"manifest_id": manifest_id_for_decision},
                UpdateExpression=(
                    "SET governance_decision = :decision, "
                    "governed_at = :now"
                ),
                ExpressionAttributeValues={
                    ":decision": decision.decision,
                    ":now": _datetime.utcnow().isoformat(),
                },
            )
            logger.info(
                f"✅ [Governor] Manifest {manifest_id_for_decision} tagged with "
                f"governance_decision={decision.decision}"
            )
        except Exception as _ge:
            # Non-blocking — manifest tag failure must not break the execution flow
            logger.warning(f"⚠️ [Governor] Failed to tag manifest with governance_decision: {_ge}")
    
    # ────────────────────────────────────────────────────────────────────
    # 7. Return _kernel Commands
    # ────────────────────────────────────────────────────────────────────
    result = {
        "governance_decision": decision.decision,
        "approved": decision.approved,
        "violations": decision.violations,
        "governance_mode": governance_mode.value,
        "execution_time_ms": (time.time() - start_time) * 1000
    }
    
    # Merge _kernel commands into result
    result.update(decision.kernel_commands)
    
    logger.info(f"✅ [Governor] Decision: {decision.decision}, "
               f"Kernel Commands: {list(decision.kernel_commands.keys())}")
    
    return result


# ============================================================================
# 🛡️ Agent Behavior Analysis
# ============================================================================

def _analyze_agent_behavior(
    agent_id: str,
    agent_output: Dict[str, Any],
    workflow_state: Dict[str, Any],
    config: Dict[str, Any],
    ring_level: RingLevel
) -> AgentBehaviorAnalysis:
    """
    에이전트 출력 분석
    
    Checks:
        1. Plan Change Detection (재계획 감지)
        2. SLOP Detection (Suspicious Large Output Pattern)
        3. Gas Fee Monitoring
        4. Circuit Breaker (재시도 한계)
        5. Prompt Injection Validation
        6. Kernel Command Forgery Detection (v2.1)
    """
    violations = []
    
    # ────────────────────────────────────────────────────────────────────
    # Metric 1: Output Size Analysis
    # ────────────────────────────────────────────────────────────────────
    output_json = json.dumps(agent_output, ensure_ascii=False)
    output_size_bytes = len(output_json.encode('utf-8'))
    guardrails = config.get("guardrails", {})
    max_output_size_kb = guardrails.get("max_output_size_kb", 500)
    
    if output_size_bytes > max_output_size_kb * 1024:
        violations.append(
            f"SLOP_DETECTED: Output size {output_size_bytes/1024:.1f}KB exceeds {max_output_size_kb}KB"
        )
    
    # ────────────────────────────────────────────────────────────────────
    # Metric 2: Plan Change Detection
    # ────────────────────────────────────────────────────────────────────
    current_plan_hash = agent_output.get("plan_hash")
    last_plan_hash = workflow_state.get("last_plan_hash")
    plan_changed = False
    
    if current_plan_hash and last_plan_hash and current_plan_hash != last_plan_hash:
        plan_changed = True
        violations.append(
            f"PLAN_CHANGE_DETECTED: {last_plan_hash[:8]} → {current_plan_hash[:8]}"
        )
    
    # ────────────────────────────────────────────────────────────────────
    # Metric 3: Gas Fee Monitoring
    # ────────────────────────────────────────────────────────────────────
    total_llm_cost = workflow_state.get("total_llm_cost", 0)
    max_gas_fee_usd = guardrails.get("max_gas_fee_usd", 100)
    
    if total_llm_cost > max_gas_fee_usd:
        violations.append(
            f"GAS_FEE_EXCEEDED: ${total_llm_cost:.2f} > ${max_gas_fee_usd}"
        )
    
    # ────────────────────────────────────────────────────────────────────
    # Metric 4: Retry Count (Circuit Breaker)
    # ────────────────────────────────────────────────────────────────────
    retry_count = workflow_state.get(f"{agent_id}_retry_count", 0)
    max_retry_count = guardrails.get("max_retry_count", 3)
    
    if retry_count > max_retry_count:
        violations.append(
            f"CIRCUIT_BREAKER_TRIGGERED: {retry_count} retries > {max_retry_count}"
        )
    
    # ────────────────────────────────────────────────────────────────────
    # Metric 5: Constitutional Article Validation (GovernanceEngine)
    # ────────────────────────────────────────────────────────────────────
    # Article 1–6 병렬 검증 (asyncio.gather 기반)
    try:
        from src.services.governance.governance_engine import GovernanceEngine
        engine = GovernanceEngine.get_instance()
        output_text = json.dumps(agent_output, ensure_ascii=False)
        verdict = _get_verdict_sync(
            engine,
            output_text=output_text,
            context={"agent_id": agent_id, "ring_level": ring_level.value},
        )
        for av in verdict.violations:
            violations.append(
                f"CONSTITUTIONAL_VIOLATION[Article {av.article_num}]: "
                f"{av.description} (evidence: {av.evidence[:40]})"
            )
    except Exception as e:
        logger.warning(f"[Governor] GovernanceEngine check failed, skipping: {e}")
    
    # ────────────────────────────────────────────────────────────────────
    # Metric 6: Kernel Command Forgery Detection (v2.1)
    # ────────────────────────────────────────────────────────────────────
    for kernel_key in KERNEL_CONTROL_KEYS:
        if kernel_key in agent_output:
            violations.append(
                f"KERNEL_COMMAND_FORGERY: Agent attempted to output {kernel_key}"
            )
            logger.error(f"🚨 [SecurityViolation] Agent {agent_id} (Ring {ring_level.value}) "
                        f"attempted to forge kernel command: {kernel_key}")
    
    # ────────────────────────────────────────────────────────────────────
    # Calculate Anomaly Score (0.0 ~ 1.0)
    # ────────────────────────────────────────────────────────────────────
    anomaly_score = min(len(violations) * 0.2, 1.0)  # Each violation adds 0.2
    
    # Boost score for critical violations
    if any("KERNEL_COMMAND_FORGERY" in v for v in violations):
        anomaly_score = 1.0  # Maximum threat
    if any("CONSTITUTIONAL_VIOLATION" in v for v in violations):
        # CRITICAL Article 위반 → 최소 0.8 (REJECTED 임계치 이상)
        anomaly_score = max(anomaly_score, 0.8)
    
    return AgentBehaviorAnalysis(
        agent_id=agent_id,
        execution_time_ms=workflow_state.get(f"{agent_id}_execution_time_ms", 0),
        output_size_bytes=output_size_bytes,
        token_count=agent_output.get("token_count"),
        plan_changed=plan_changed,
        plan_hash=current_plan_hash,
        retry_count=retry_count,
        violations=violations,
        anomaly_score=anomaly_score,
        ring_level=ring_level
    )


# ============================================================================
# 🛡️ Governance Decision Logic
# ============================================================================

def _make_governance_decision(
    analysis: AgentBehaviorAnalysis,
    state: Dict[str, Any],
    config: Dict[str, Any],
    governance_mode: GovernanceMode
) -> GovernanceDecision:
    """
    분석 결과를 바탕으로 _kernel 명령 생성
    
    Decision Matrix:
        - anomaly_score >= 0.8 → REJECTED (SIGKILL)
        - anomaly_score >= 0.5 → ESCALATED (HITP 삽입)
        - anomaly_score < 0.5 → APPROVED
    
    v2.1 Enhancements:
        - Feedback Loop: 에이전트에게 차단 사유 피드백
        - Optimistic Rollback: 비동기 모드에서도 안전 보장
    
    Returns:
        GovernanceDecision with _kernel commands
    """
    kernel_commands = {}
    
    # ────────────────────────────────────────────────────────────────────
    # Decision 1: Plan Change → Skip obsolete segments
    # ────────────────────────────────────────────────────────────────────
    if analysis.plan_changed:
        obsolete_segment_ids = _calculate_obsolete_segments(state, analysis)
        
        if obsolete_segment_ids:
            kernel_commands["_kernel_skip_segments"] = obsolete_segment_ids
            kernel_commands["_kernel_skip_reason"] = (
                f"Agent {analysis.agent_id} re-planned workflow. "
                f"Previous plan hash: {state.get('last_plan_hash', 'N/A')[:8]}, "
                f"New plan hash: {analysis.plan_hash[:8] if analysis.plan_hash else 'N/A'}"
            )
    
    # ────────────────────────────────────────────────────────────────────
    # Decision 2: SLOP or Security Violation → Inject HITP + Feedback
    # ────────────────────────────────────────────────────────────────────
    critical_violations = [v for v in analysis.violations 
                          if "SLOP" in v or "SECURITY" in v or "KERNEL_COMMAND_FORGERY" in v]
    
    if critical_violations:
        hitp_segment = {
            "type": "hitp",
            "config": {
                "message": f"⚠️ Governor Alert: {len(critical_violations)} violation(s) detected",
                "context": {
                    "agent_id": analysis.agent_id,
                    "violations": critical_violations,
                    "anomaly_score": analysis.anomaly_score
                }
            }
        }
        
        # [v2.1] Generate Feedback Message for Agent Self-Correction
        feedback_message = _generate_agent_feedback(
            violations=critical_violations,
            agent_id=analysis.agent_id,
            context={"output_size_bytes": analysis.output_size_bytes}
        )
        
        kernel_commands["_kernel_inject_recovery"] = {
            "segments": [hitp_segment],
            "reason": f"Security anomaly detected (score: {analysis.anomaly_score:.2f})",
            "feedback_to_agent": feedback_message  # v2.1 NEW
        }
        
        # Inject feedback into StateBag for next agent execution
        state["governor_feedback"] = {
            "timestamp": time.time(),
            "message": feedback_message,
            "violations": critical_violations
        }
    
    # ────────────────────────────────────────────────────────────────────
    # Decision 3: Gas Fee Exceeded → Reduce Parallelism + Emit Governance Event
    # ────────────────────────────────────────────────────────────────────
    if any("GAS_FEE" in v for v in analysis.violations):
        kernel_commands["_kernel_modify_parallelism"] = {
            "max_concurrent_branches": 5,
            "reason": f"Cost guardrail triggered: {state.get('total_llm_cost', 0):.2f} USD"
        }
        
        # [v3.28] Emit Governance Event to __hidden_context
        _emit_governance_event(
            state=state,
            category="COST",
            severity="CRITICAL",
            message="예산 한도 초과로 인해 실행 속도를 제한합니다.",
            action_taken="병렬 브랜치를 5개로 제한함",
            technical_detail={
                "total_llm_cost_usd": state.get('total_llm_cost', 0),
                "violation": next((v for v in analysis.violations if "GAS_FEE" in v), ""),
                "agent_id": analysis.agent_id
            },
            related_node_id=analysis.agent_id,
            triggered_by_ring=analysis.ring_level.value
        )
    
    # ────────────────────────────────────────────────────────────────────
    # Decision 4: Circuit Breaker → Request Human Approval
    # ────────────────────────────────────────────────────────────────────
    if any("CIRCUIT_BREAKER" in v for v in analysis.violations):
        kernel_commands["_kernel_request_human_approval"] = {
            "hitp_message": (
                f"Agent {analysis.agent_id} exceeded retry limit ({analysis.retry_count}). "
                "Approve continuation or cancel?"
            ),
            "context": {
                "agent_id": analysis.agent_id,
                "retry_count": analysis.retry_count,
                "violations": analysis.violations
            },
            "timeout_seconds": 1800
        }
    
    # ────────────────────────────────────────────────────────────────────
    # Final Decision
    # ────────────────────────────────────────────────────────────────────
    if analysis.anomaly_score >= 0.8:
        decision = "REJECTED"
        approved = False
    elif analysis.anomaly_score >= 0.5 or kernel_commands:
        decision = "ESCALATED"
        approved = True  # Execution continues but with _kernel interventions
    else:
        decision = "APPROVED"
        approved = True
    
    # ────────────────────────────────────────────────────────────────────
    # Audit Log
    # ────────────────────────────────────────────────────────────────────
    audit_log = {
        "workflow_id": state.get("workflowId") or state.get("workflow_id", "unknown"),  # [v2.1] Required for DynamoDB PK
        "timestamp": time.time(),
        "agent_id": analysis.agent_id,
        "decision": decision,
        "approved": approved,
        "anomaly_score": analysis.anomaly_score,
        "violations": analysis.violations,
        "kernel_commands_issued": list(kernel_commands.keys()),
        "output_size_bytes": analysis.output_size_bytes,
        "plan_hash": analysis.plan_hash,
        "ring_level": analysis.ring_level.value,
        "governance_mode": governance_mode.value
    }
    
    return GovernanceDecision(
        approved=approved,
        decision=decision,
        violations=analysis.violations,
        kernel_commands=kernel_commands,
        audit_log=audit_log,
        governance_mode=governance_mode
    )


# ============================================================================
# 🛡️ Helper Functions
# ============================================================================

def _calculate_obsolete_segments(
    workflow_state: Dict[str, Any],
    analysis: AgentBehaviorAnalysis
) -> List[int]:
    """
    새로운 플랜에 따라 obsolete된 세그먼트 ID 계산
    
    Implementation Strategy (v2.0 - partition_map 기반):
        1. partition_map에서 {node_id → [segment_ids]} 매핑 조회
        2. Agent가 "Skip Node X"를 요청하면 즉시 해당 segment_id 반환
        3. 정확도 99% (노드-세그먼트 직접 매핑)
    
    Args:
        workflow_state: 전체 워크플로우 상태
            - partition_map: Dict[str, List[int]] (node_id → segment_ids)
            - agent_skip_nodes: List[str] (에이전트가 스킵 요청한 node_id 리스트)
        analysis: 에이전트 행동 분석 결과
    
    Returns:
        List[int]: Obsolete된 segment_id 리스트
    """
    partition_map = workflow_state.get("partition_map", {})
    agent_skip_nodes = workflow_state.get("agent_skip_nodes", [])
    
    if not partition_map or not agent_skip_nodes:
        logger.warning("[Governor] partition_map or agent_skip_nodes missing, "
                      "cannot calculate obsolete segments")
        return []
    
    obsolete_segment_ids = []
    
    for node_id in agent_skip_nodes:
        segment_ids = partition_map.get(node_id, [])
        if segment_ids:
            obsolete_segment_ids.extend(segment_ids)
            logger.info(f"[Governor] Node '{node_id}' → Segments {segment_ids} marked obsolete")
        else:
            logger.warning(f"[Governor] Node '{node_id}' not found in partition_map")
    
    return obsolete_segment_ids


def _get_last_safe_manifest(workflow_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Get the last safe manifest (violations=[], approved=True) for rollback
    
    Implementation (v2.1.1 - DynamoDB GSI Query):
        ❌ OLD: Traverse workflow_state['manifest_history'] → State bloat risk
        ✅ NEW: Query DynamoDB WorkflowManifestsV3 table via GSI:
            - GSI: GovernanceDecisionIndex (workflow_id + governance_decision + timestamp)
            - Filter: governance_decision = "APPROVED" AND violations = []
            - Sort: timestamp DESC, LIMIT 1
        
        Benefits:
            - No state bloat (manifest_history stays small)
            - Sub-100ms query latency
            - Scalable to 1000+ segment workflows
    
    Args:
        workflow_state: 전체 워크플로우 상태
            - workflow_id: str (required for DynamoDB query)
            - current_manifest_id: str (optional, for logging)
    
    Returns:
        Optional[Dict]: Last safe manifest or None
    """
    import os
    import boto3
    
    workflow_id = workflow_state.get("workflowId") or workflow_state.get("workflow_id")
    
    if not workflow_id:
        logger.error("[Governor] No workflow_id in state, cannot query DynamoDB for safe manifest")
        return None
    
    try:
        table_name = os.environ.get("WORKFLOW_MANIFESTS_TABLE", "WorkflowManifests-v3-dev")
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(table_name)
        
        # Query GSI: GovernanceDecisionIndex (workflow_id + governance_decision)
        # Note: Assumes WorkflowManifestsV3 table has this GSI (must be added in SAM template)
        response = table.query(
            IndexName='GovernanceDecisionIndex',
            KeyConditionExpression='workflow_id = :wf_id AND governance_decision = :approved',
            ExpressionAttributeValues={
                ':wf_id': workflow_id,
                ':approved': 'APPROVED'
            },
            ScanIndexForward=False,  # DESC order by timestamp
            Limit=10  # Get last 10 safe manifests
        )
        
        # Filter for manifests with no violations
        safe_manifests = [
            item for item in response.get('Items', [])
            if not item.get('violations') or len(item.get('violations', [])) == 0
        ]
        
        if safe_manifests:
            last_safe = safe_manifests[0]  # Most recent
            logger.info(f"[Governor] Found last safe manifest from DynamoDB: {last_safe.get('manifest_id')}")
            return last_safe
        
        logger.warning(f"[Governor] No safe manifest found for workflow {workflow_id}")
        return None
        
    except Exception as e:
        logger.error(f"[Governor] Failed to query DynamoDB for safe manifest: {e}")
        # Fallback: Try in-memory manifest_history (degraded mode)
        manifest_history = workflow_state.get("manifest_history", [])
        if manifest_history:
            for manifest in reversed(manifest_history[-10:]):  # Only check last 10
                if manifest.get("governance_decision") == "APPROVED" and not manifest.get("violations"):
                    logger.warning(f"[Governor] Fallback: Using in-memory manifest {manifest['manifest_id']}")
                    return manifest
        return None


def _determine_rollback_type(violations: List[str]) -> str:
    """
    Determine rollback type based on violation severity (v2.1.1)
    
    Rollback Strategies:
        - TERMINAL_HALT: Security forgery detected → Immediate SIGKILL
        - HARD_ROLLBACK: Critical violation (SLOP, Circuit Breaker) → Previous safe manifest
        - SOFT_ROLLBACK: Minor violation (Plan Change, Gas Fee) → Current segment retry with feedback
    
    Args:
        violations: List of violation strings
    
    Returns:
        str: "TERMINAL_HALT" | "HARD_ROLLBACK" | "SOFT_ROLLBACK"
    """
    # Priority 1: Security violations → TERMINAL_HALT
    for violation in violations:
        if "KERNEL_COMMAND_FORGERY" in violation or "SECURITY_VIOLATION" in violation:
            return "TERMINAL_HALT"
    
    # Priority 2: Critical violations → HARD_ROLLBACK
    for violation in violations:
        if "SLOP_DETECTED" in violation or "CIRCUIT_BREAKER" in violation:
            return "HARD_ROLLBACK"
    
    # Priority 3: Minor violations → SOFT_ROLLBACK
    return "SOFT_ROLLBACK"


def _generate_agent_feedback(
    violations: List[str],
    agent_id: str,
    context: Dict[str, Any]
) -> str:
    """
    Generate human-readable feedback message for agent self-correction
    
    Purpose (v2.1):
        에이전트에게 '왜 차단되었는지' 명확한 피드백을 제공하여
        무한 루프 방지 및 행동 교정(Self-Correction)을 유도합니다.
    
    Args:
        violations: 발견된 위반 사항 리스트
        agent_id: 에이전트 ID
        context: 추가 컨텍스트 (output_size_bytes, gas_fee, 등)
    
    Returns:
        str: 에이전트에게 주입할 피드백 메시지
    
    Example:
        Input: ["SLOP_DETECTED: Output size 600KB exceeds 500KB"]
        Output: "Governor: Your output (600KB) exceeded the 500KB limit. 
                 Plan modified to include human review. 
                 Please reduce output size in retry by summarizing content."
    """
    feedback_parts = [f"Governor Feedback for {agent_id}:"]
    
    for violation in violations:
        if "SLOP_DETECTED" in violation:
            size_kb = context.get("output_size_bytes", 0) / 1024
            feedback_parts.append(
                f"- Your output ({size_kb:.1f}KB) exceeded the size limit. "
                "Recommendation: Summarize content or split into multiple responses."
            )
        
        elif "GAS_FEE" in violation:
            feedback_parts.append(
                f"- Cost limit reached. Parallelism reduced to 5 branches. "
                "Recommendation: Use smaller models or reduce API calls."
            )
        
        elif "PLAN_CHANGE" in violation:
            feedback_parts.append(
                f"- Plan drift detected. Previous workflow segments may be obsolete. "
                "Recommendation: Verify alignment with original mission goal."
            )
        
        elif "CIRCUIT_BREAKER" in violation:
            feedback_parts.append(
                f"- Maximum retry limit exceeded. Human approval required. "
                "Recommendation: Review error patterns before retry."
            )
        
        elif "SECURITY_VIOLATION" in violation:
            feedback_parts.append(
                f"- Security policy violation detected. "
                "Recommendation: Remove suspicious patterns and retry."
            )
        
        elif "KERNEL_COMMAND_FORGERY" in violation:
            feedback_parts.append(
                f"- Attempted to output reserved _kernel commands (Ring 0/1 only). "
                "Recommendation: Remove all _kernel_* keys from output."
            )
    
    feedback_parts.append(
        "\nNote: This workflow has been modified by the Governor. "
        "Adjust your strategy accordingly to avoid repeated violations."
    )
    
    return "\n".join(feedback_parts)


def _save_governance_audit_log(audit_log: Dict[str, Any]) -> None:
    """
    Governance Audit Log를 DynamoDB에 저장
    
    Table: GovernanceAuditLog
    Schema:
        - PK: workflow_id (str)
        - SK: timestamp (float)
        - agent_id (str)
        - decision (str)
        - anomaly_score (float)
        - violations (List[str])
        - kernel_commands_issued (List[str])
    
    v2.1 Implementation: DynamoDB put_item with boto3
    """
    import os
    import boto3
    from decimal import Decimal
    
    # Get table name from environment variable
    table_name = os.environ.get("GOVERNANCE_AUDIT_LOG_TABLE")
    
    if not table_name:
        logger.warning(
            "[Governance Audit] GOVERNANCE_AUDIT_LOG_TABLE env var not set. "
            "Audit log will only be logged to CloudWatch."
        )
        logger.info(f"📝 [Governance Audit] {json.dumps(audit_log, indent=2)}")
        return
    
    try:
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(table_name)
        
        # Convert floats to Decimal for DynamoDB
        def convert_floats(obj):
            """Recursively convert float to Decimal for DynamoDB compatibility"""
            if isinstance(obj, float):
                return Decimal(str(obj))
            elif isinstance(obj, dict):
                return {k: convert_floats(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_floats(item) for item in obj]
            return obj
        
        # Prepare item for DynamoDB
        item = convert_floats(audit_log.copy())
        
        # Add TTL (90 days retention for security audit)
        import time
        item['ttl'] = int(time.time()) + (90 * 24 * 3600)
        
        # Ensure required fields exist
        if 'workflow_id' not in item:
            logger.error("[Governance Audit] Missing workflow_id in audit log, skipping save")
            return
        
        if 'timestamp' not in item:
            item['timestamp'] = Decimal(str(time.time()))
        
        # Write to DynamoDB
        table.put_item(Item=item)
        
        logger.info(
            f"✅ [Governance Audit] Saved to DynamoDB: "
            f"workflow_id={item.get('workflow_id')}, "
            f"agent_id={item.get('agent_id')}, "
            f"decision={item.get('decision')}"
        )
        
    except Exception as e:
        logger.error(
            f"🚨 [Governance Audit] Failed to save to DynamoDB: {e}. "
            f"Audit log: {json.dumps(audit_log, indent=2)}"
        )
        # Continue execution even if audit log fails (don't block workflow)


# ============================================================================
# � CloudWatch Metrics Emission (v2.1)
# ============================================================================

def _emit_governance_metrics(
    analysis: "GovernanceAnalysis",
    decision: str
) -> None:
    """
    Emit governance metrics via structured CloudWatch Logs (v2.1.1)
    
    ❌ OLD: CloudWatch put_metric_data → API throttling in parallel execution (100+ governors)
    ✅ NEW: Structured logging + CloudWatch Logs Metric Filters
    
    Benefits:
        - No API rate limits (logs are async)
        - Cost-effective (metric filters are free)
        - Automatic aggregation by CloudWatch
        - No boto3 client overhead
    
    Metrics emitted:
    1. AnomalyScore (per agent_id)
    2. ViolationCount (per violation_type)
    3. KernelCommandIssuedCount (per command)
    4. GovernanceDecisionRate (per decision)
    
    Args:
        analysis: Governance analysis result
        decision: Final governance decision (APPROVED/REJECTED/ESCALATED/ROLLBACK)
    
    Setup Required:
        - CloudWatch Logs Metric Filter on Lambda log group:
            Pattern: [GOVERNANCE_METRIC]
            Metric Namespace: Analemma/Governance
            Metric Name: $metric_name
            Metric Value: $metric_value
            Dimensions: $dimensions
    """
    try:
        # Extract violation types for structured logging
        violation_types = []
        for violation in analysis.violations:
            # Parse violation string: "VIOLATION_TYPE: message"
            if ':' in violation:
                violation_type = violation.split(':')[0].strip()
            else:
                violation_type = "UNKNOWN"
            violation_types.append(violation_type)
        
        # Structured log entry for CloudWatch Logs Metric Filter
        # Format: [GOVERNANCE_METRIC] metric_name=<name> metric_value=<value> agent_id=<id> ring=<level>
        logger.info(
            f"[GOVERNANCE_METRIC] metric_name=AnomalyScore metric_value={analysis.anomaly_score:.3f} "
            f"agent_id={analysis.agent_id} ring={analysis.ring_level.value} decision={decision}"
        )
        
        # Violation count per type
        for violation_type in violation_types:
            logger.info(
                f"[GOVERNANCE_METRIC] metric_name=ViolationCount metric_value=1 "
                f"violation_type={violation_type} agent_id={analysis.agent_id}"
            )
        
        # Kernel command count
        kernel_commands_issued = analysis.__dict__.get('kernel_commands_issued', [])
        for command in kernel_commands_issued:
            logger.info(
                f"[GOVERNANCE_METRIC] metric_name=KernelCommandIssued metric_value=1 "
                f"command={command} agent_id={analysis.agent_id}"
            )
        
        # Governance decision rate
        logger.info(
            f"[GOVERNANCE_METRIC] metric_name=GovernanceDecision metric_value=1 "
            f"decision={decision} agent_id={analysis.agent_id} ring={analysis.ring_level.value}"
        )
        
    except Exception as e:
        logger.error(f"🚨 [CloudWatch] Failed to emit structured metrics: {e}")
        # Continue execution even if metrics fail (don't block workflow)


# ============================================================================# 🔔 Governance Event Emission to __hidden_context (v3.28)
# ============================================================================

def _emit_governance_event(
    state: Dict[str, Any],
    category: str,  # "COST", "SECURITY", "PERFORMANCE", "COMPLIANCE", "PLAN_DRIFT"
    severity: str,  # "INFO", "WARNING", "CRITICAL"
    message: str,
    action_taken: Optional[str] = None,
    technical_detail: Optional[Dict[str, Any]] = None,
    related_node_id: Optional[str] = None,
    triggered_by_ring: Optional[int] = None
) -> None:
    """
    [v3.28.1] Governance Event를 __hidden_context에 기록 (Event Debouncing 적용)
    
    Governor가 정책 위반이나 제어 액션을 수행할 때,
    TaskManager가 파싱할 수 있도록 표준화된 이벤트를 추가합니다.
    
    [v3.28.1 개선사항]:
    1. Event Flood 방어: 동일 노드/카테고리의 중복 이벤트를 occurrence_count로 압축
    2. 타임스탬프 정밀도: time.time_ns()로 나노초 단위 기록 + sequence_number
    3. S3 크기 최적화: 1,000번 반복 시에도 매니페스트 크기 4MB 이하 유지
    
    이벤트는 __hidden_context["governance_events"] 배열에 누적되며,
    TaskService가 TaskContext.governance_alerts로 변환합니다.
    
    Args:
        state: 워크플로우 상태 (변경됨 - in-place mutation)
        category: 이벤트 카테고리
        severity: 심각도
        message: 사용자 친화적 메시지 (한글)
        action_taken: Governor가 취한 조치
        technical_detail: 개발자 모드용 상세 정보
        related_node_id: 관련 노드 ID
        triggered_by_ring: Ring Level
    
    Example:
        _emit_governance_event(
            state=state,
            category="COST",
            severity="CRITICAL",
            message="예산 한도 초과로 인해 실행 속도를 제한합니다.",
            action_taken="병렬 브랜치를 5개로 제한함",
            technical_detail={"total_cost": 105.50},
            related_node_id="manus_planner",
            triggered_by_ring=3
        )
    """
    import uuid
    
    try:
        # Ensure __hidden_context exists
        if "__hidden_context" not in state:
            state["__hidden_context"] = {}
        
        # Ensure governance_events array exists
        if "governance_events" not in state["__hidden_context"]:
            state["__hidden_context"]["governance_events"] = []
        
        # Ensure sequence counter exists
        if "governance_event_sequence" not in state["__hidden_context"]:
            state["__hidden_context"]["governance_event_sequence"] = 0
        
        events = state["__hidden_context"]["governance_events"]
        
        # ────────────────────────────────────────────────────────────────────
        # [v3.28.1] Event Debouncing: 동일 이벤트 압축 (OS Kernel 스타일)
        # ────────────────────────────────────────────────────────────────────
        # 동일 노드에서 발생한 동일 카테고리/심각도 이벤트를 찾음
        debounce_key = f"{related_node_id}:{category}:{severity}"
        existing_event = None
        
        # 최근 10개 이벤트만 검색 (O(1) 성능 보장)
        for event in reversed(events[-10:]):
            event_key = f"{event.get('related_node_id')}:{event.get('category')}:{event.get('severity')}"
            if event_key == debounce_key:
                existing_event = event
                break
        
        if existing_event:
            # 중복 이벤트 발견 → occurrence_count 증가
            existing_event["occurrence_count"] = existing_event.get("occurrence_count", 1) + 1
            existing_event["last_occurrence_timestamp_ns"] = time.time_ns()
            existing_event["last_occurrence_sequence"] = state["__hidden_context"]["governance_event_sequence"]
            
            # 최신 technical_detail 업데이트 (선택적)
            if technical_detail:
                existing_event["technical_detail"] = technical_detail
            
            logger.info(
                f"📡 [Governance Event] Debounced duplicate: "
                f"{debounce_key} (count={existing_event['occurrence_count']})"
            )
            return  # 새 이벤트를 추가하지 않고 종료
        
        # ────────────────────────────────────────────────────────────────────
        # [v3.28.1] 신규 이벤트 생성
        # ────────────────────────────────────────────────────────────────────
        sequence_number = state["__hidden_context"]["governance_event_sequence"]
        state["__hidden_context"]["governance_event_sequence"] += 1
        
        event = {
            "alert_id": str(uuid.uuid4()),
            
            # [v3.28.1] 타임스탬프 정밀도 향상
            "timestamp_ns": time.time_ns(),  # 나노초 단위 (비동기 환경에서 순서 보장)
            "timestamp": time.time(),  # 하위 호환성 (초 단위)
            "sequence_number": sequence_number,  # 전역 시퀀스 번호
            
            "category": category,
            "severity": severity,
            "message": message,
            "action_taken": action_taken,
            "technical_detail": technical_detail,
            "related_node_id": related_node_id,
            "triggered_by_ring": triggered_by_ring,
            
            # [v3.28.1] Event Debouncing 지원
            "occurrence_count": 1,  # 최초 발생 시 1
            "last_occurrence_timestamp_ns": time.time_ns(),
            "last_occurrence_sequence": sequence_number,
        }
        
        # Append to governance_events
        events.append(event)
        
        logger.info(
            f"📡 [Governance Event] Emitted (seq={sequence_number}): "
            f"category={category}, severity={severity}, "
            f"message={message[:50]}..."
        )
        
    except Exception as e:
        logger.error(f"🚨 [Governance Event] Failed to emit event: {e}")
        # Don't raise - event emission failure shouldn't block workflow


# ============================================================================# �🛡️ Node Registration Helper
# ============================================================================

# This function is registered in handlers/core/main.py's NODE_TYPE_RUNNERS dict:
# 
# NODE_TYPE_RUNNERS = {
#     ...
#     "governor": governor_node_runner,
# }
