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

import json
import time
import logging
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


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
    "_kernel_request_human_approval"
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
    # 5. Optimistic Rollback Policy (v2.1)
    # ────────────────────────────────────────────────────────────────────
    if governance_mode == GovernanceMode.OPTIMISTIC and decision.violations:
        logger.warning(f"🚨 [Optimistic Rollback] Violations detected: {decision.violations}")
        
        # Get last safe manifest (violations=[], approved=True)
        last_safe_manifest = _get_last_safe_manifest(state)
        
        if last_safe_manifest:
            decision.kernel_commands["_kernel_rollback_to_manifest"] = last_safe_manifest["manifest_id"]
            decision.kernel_commands["_kernel_rollback_reason"] = (
                f"Optimistic violation detected: {decision.violations[0]}"
            )
            decision.kernel_commands["_kernel_rollback_type"] = "OPTIMISTIC_RECOVERY"
            decision.decision = "ROLLBACK"
            
            logger.info(f"✅ [Optimistic Rollback] Rolling back to manifest: "
                       f"{last_safe_manifest['manifest_id']}")
    
    # ────────────────────────────────────────────────────────────────────
    # 6. Persist Audit Log (DynamoDB)
    # ────────────────────────────────────────────────────────────────────
    _save_governance_audit_log(decision.audit_log)
    
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
    # Metric 5: Prompt Injection Validation (Ring Protection)
    # ────────────────────────────────────────────────────────────────────
    # TODO: Integrate with PromptSecurityGuard
    # security_guard = get_security_guard()
    # security_result = security_guard.validate_prompt(...)
    
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
    # Decision 3: Gas Fee Exceeded → Reduce Parallelism
    # ────────────────────────────────────────────────────────────────────
    if any("GAS_FEE" in v for v in analysis.violations):
        kernel_commands["_kernel_modify_parallelism"] = {
            "max_concurrent_branches": 5,
            "reason": f"Cost guardrail triggered: {state.get('total_llm_cost', 0):.2f} USD"
        }
    
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
    
    Implementation (v2.1 - Optimistic Rollback):
        1. Traverse manifest history via parent_hash chain
        2. Find first manifest where governance_decision == "APPROVED"
        3. Return that manifest for time-travel rollback
    
    Args:
        workflow_state: 전체 워크플로우 상태
            - manifest_history: List[Dict] (chronological manifest list)
            - current_manifest_id: str
    
    Returns:
        Optional[Dict]: Last safe manifest or None
    """
    manifest_history = workflow_state.get("manifest_history", [])
    
    if not manifest_history:
        logger.warning("[Governor] No manifest history available for rollback")
        return None
    
    # Traverse in reverse chronological order
    for manifest in reversed(manifest_history):
        governance_decision = manifest.get("governance_decision", "APPROVED")
        violations = manifest.get("violations", [])
        
        if governance_decision == "APPROVED" and not violations:
            logger.info(f"[Governor] Found last safe manifest: {manifest['manifest_id']}")
            return manifest
    
    # Fallback: Return first manifest (initial state)
    logger.warning("[Governor] No safe manifest found, falling back to initial manifest")
    return manifest_history[0] if manifest_history else None


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
    
    TODO: DynamoDB client 구현 (Priority 2)
    """
    # Placeholder: Log to CloudWatch for now
    logger.info(f"📝 [Governance Audit] {json.dumps(audit_log, indent=2)}")
    
    # TODO: Implement DynamoDB put_item()
    # dynamodb_client.put_item(
    #     TableName='GovernanceAuditLog',
    #     Item={
    #         'workflow_id': workflow_state['workflow_id'],
    #         'timestamp': audit_log['timestamp'],
    #         'audit_data': audit_log
    #     }
    # )


# ============================================================================
# 🛡️ Node Registration Helper
# ============================================================================

# This function is registered in handlers/core/main.py's NODE_TYPE_RUNNERS dict:
# 
# NODE_TYPE_RUNNERS = {
#     ...
#     "governor": governor_node_runner,
# }
