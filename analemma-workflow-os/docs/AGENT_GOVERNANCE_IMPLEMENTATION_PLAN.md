# Agent Governance Implementation Plan
## 자율형 에이전트 통제 시스템 구현 계획

**작성일**: 2024-12-XX  
**상태**: DRAFT  
**목적**: Manus, Moltbot 등 자율형 에이전트의 안전한 운영을 위한 Governor 시스템 설계

---

## 📋 Executive Summary

현재 동적 스케줄링 기능(`_mark_segments_for_skip`, `_inject_recovery_segments`)은 **100% 구현**되어 있으나, **에이전트 출력을 검증하고 제어하는 Governor 레이어가 부재**합니다. 이로 인해 다음과 같은 위험이 존재합니다:

### 🚨 Current Risks
1. **Trust Gap**: 에이전트가 `_kernel_skip_segments`를 직접 출력 → 커널 명령 위조 가능
2. **No Validation**: 에이전트 플랜 변경(re-planning) 감지 메커니즘 없음
3. **No Guardrails**: SLOP(Suspicious Large Output Pattern), Gas Fee 폭주 탐지 없음
4. **No Audit Trail**: 에이전트 행동 기록이 Merkle Manifest에만 의존 (governance 메트릭 없음)

### ✅ Implementation Priorities
```
Priority 1 (Immediate): Governor Node + Interface Standardization + Optimistic Governance
Priority 2 (Short-term): Agent Guardrails Library + Intent Retention Rate + Metrics Collection
Priority 3 (Medium-term): Ring Architecture Formalization + Time-Travel Rollback (Killer Feature)
```

### 🔥 Critical Feedback Integration (v2.0)

**1. Optimistic Governance (레이턴시 문제 해결)**:
- ❌ 문제: 모든 에이전트 노드 뒤 Governor 실행 → 레이턴시 2배
- ✅ 해결: Ring 2 (저위험) = 비동기 검증 (로그만), Ring 1 (고위험) = 동기 검증
- 📊 예상 성과: 90% 워크플로우에서 레이턴시 영향 < 5%

**2. Intent Retention Rate (의미론적 Plan Drift 검증)**:
- ❌ 문제: Hash 비교는 단어 하나 바뀌어도 변경 감지 → false positive
- ✅ 해결: 소형 모델(Llama-3-8B)로 "새 계획이 원래 목표 지향?" 판단 (0.0~1.0)
- 📊 임계값: Intent Retention < 0.7 → Plan Drift 경고

**3. partition_map 기반 Obsolete Segments 계산**:
- ❌ 문제: 에이전트 "Skip Node X" 시 어떤 segment_id인지 모름
- ✅ 해결: partition_map에 {node_id → [segment_ids]} 매핑 보존
- 📊 예상 정확도: 99% (노드-세그먼트 직접 매핑)

---

## 🏗️ Part 1: Governance Interface Standardization

### Current State Analysis

**_kernel 제어 키 현황** (`segment_runner_service.py:3808-3822`):
```python
# ✅ Already Implemented
skip_next_segments = result_state.get('_kernel_skip_segments', [])
skip_reason = result_state.get('_kernel_skip_reason', 'Condition not met')
recovery_request = result_state.get('_kernel_inject_recovery')

# ❌ Not Implemented
# _kernel_rollback_to_manifest
# _kernel_modify_parallelism
# _kernel_request_human_approval
```

### 🎯 Proposed _kernel Interface Specification

```python
# ============================================================================
# 🛡️ [Ring 0] Kernel Control Interface (Reserved Keys)
# ============================================================================
# 
# CRITICAL SECURITY POLICY:
# - Only Ring 0 (Kernel) or Ring 1 (Governor) nodes can WRITE these keys
# - Ring 3 (Agent) nodes attempting to write will trigger SecurityViolation
# - All _kernel commands MUST be validated by Governor Node before execution
# 
# ============================================================================

KERNEL_CONTROL_KEYS = {
    # ──────────────────────────────────────────────────────────────────────
    # Dynamic Scheduling (Phase 8 Complete)
    # ──────────────────────────────────────────────────────────────────────
    "_kernel_skip_segments": {
        "type": "List[int]",
        "description": "세그먼트 ID 리스트 스킵 (조건부 실행)",
        "ring_level": "Ring 0 (Kernel) or Ring 1 (Governor)",
        "validation": "Must reference valid segment IDs in current manifest",
        "example": [5, 6, 7],
        "phase": "Phase 8 - Trust Chain Complete"
    },
    "_kernel_skip_reason": {
        "type": "str",
        "description": "스킵 사유 (audit log용)",
        "ring_level": "Ring 0/1",
        "max_length": 500,
        "example": "Condition X not met, skipping validation path"
    },
    "_kernel_inject_recovery": {
        "type": "Dict[str, Any]",
        "description": "복구 세그먼트 동적 삽입",
        "ring_level": "Ring 0/1",
        "schema": {
            "segments": "List[Dict] - 삽입할 세그먼트 설정 목록",
            "reason": "str - 삽입 사유"
        },
        "example": {
            "segments": [
                {"type": "hitp", "config": {"message": "Manual approval required"}}
            ],
            "reason": "Security anomaly detected: SLOP pattern in agent output"
        },
        "phase": "Phase 8 - Trust Chain Complete"
    },
    
    # ──────────────────────────────────────────────────────────────────────
    # 🔥 Time-Travel & Rollback (KILLER FEATURE - Priority 3)
    # ──────────────────────────────────────────────────────────────────────
    "_kernel_rollback_to_manifest": {
        "type": "str",
        "description": "특정 Manifest ID로 워크플로우 상태 롤백 (Time-Travel)",
        "ring_level": "Ring 0 (Kernel only)",
        "validation": "Must be valid manifest_id with parent_hash chain",
        "example": "manifest-abc123-v5",
        "dependencies": ["StateVersioningService.get_manifest_by_id()"],
        "priority": 3,
        "security_value": "에이전트 폭주 시 1ms 만에 과거 안전 시점으로 복구 (Git Rebase-style)",
        "use_case": "Agent goal drift detected → rollback to Manifest before corruption"
    },
    "_kernel_rollback_reason": {
        "type": "str",
        "description": "롤백 사유 (Critical incident logging)",
        "ring_level": "Ring 0",
        "example": "Agent plan rollback: Suspected adversarial goal drift"
    },
    
    # ──────────────────────────────────────────────────────────────────────
    # 💰 Runtime Resource Control (COST THROTTLE - Priority 2)
    # ──────────────────────────────────────────────────────────────────────
    "_kernel_modify_parallelism": {
        "type": "Dict[str, int]",
        "description": "병렬 실행 파라미터 동적 수정 (Gas Fee 제어)",
        "ring_level": "Ring 1 (Governor)",
        "schema": {
            "max_concurrent_branches": "int - 최대 동시 실행 브랜치 수",
            "reason": "str - 수정 사유"
        },
        "example": {
            "max_concurrent_branches": 5,
            "reason": "Cost guardrail triggered: current gas fee > $100"
        },
        "priority": 2,
        "security_value": "비용 폭주 시 '소방수' 역할 - 즉시 스케줄러 동시성 감소 (Throttle)",
        "use_case": "Gas fee > $100 → reduce parallelism from 120 to 5 branches"
    },
    
    # ──────────────────────────────────────────────────────────────────────
    # Human-in-the-Loop Escalation (TODO: Priority 2)
    # ──────────────────────────────────────────────────────────────────────
    "_kernel_request_human_approval": {
        "type": "Dict[str, Any]",
        "description": "HITP 노드 동적 삽입 (Circuit Breaker 트리거 시)",
        "ring_level": "Ring 1 (Governor)",
        "schema": {
            "hitp_message": "str - 사람에게 보여줄 메시지",
            "context": "Dict - 승인 판단에 필요한 컨텍스트",
            "timeout_seconds": "int - 승인 대기 시간 (default: 3600)"
        },
        "example": {
            "hitp_message": "Agent Manus re-planned workflow 3 times. Approve continuation?",
            "context": {
                "agent_id": "Manus-v2",
                "replan_count": 3,
                "last_plan_hash": "sha256:abc123..."
            },
            "timeout_seconds": 1800
        },
        "priority": 2
    }
}

# ============================================================================
# 🛡️ Security Enforcement Strategy
# ============================================================================
# 
# 1. **Ring 3 Agent Output Validation** (Governor Node):
#    - Agent가 _kernel 키를 출력하면 → 즉시 제거 후 SecurityViolation 로그
#    - Governor가 Agent 행동 분석 → 필요 시 _kernel 명령 생성
# 
# 2. **Reserved Key Blacklist** (commons/state_guard.py:_validate_output_keys):
#    - KERNEL_CONTROL_KEYS 목록 검증 강화
#    - Ring 3 노드가 _kernel 키 반환 시 SecurityError raise
# 
# 3. **Manifest Regeneration Audit** (Phase 8.3):
#    - _kernel 명령 실행 시마다 manifest_id, parent_hash 업데이트
#    - GovernanceAuditLog DynamoDB table에 command 기록
# 
# ============================================================================
```

### 📝 Action Items

1. **Update `commons/constants.py`**:
   ```python
   # 추가: KERNEL_CONTROL_KEYS constant
   KERNEL_CONTROL_KEYS = {
       "_kernel_skip_segments", "_kernel_skip_reason", 
       "_kernel_inject_recovery", "_kernel_rollback_to_manifest",
       "_kernel_modify_parallelism", "_kernel_request_human_approval"
   }
   ```

2. **Enhance `commons/state_guard.py:_validate_output_keys()`**:
   ```python
   def _validate_output_keys(output: Dict, node_id: str, ring_level: int = 3) -> Dict:
       """Ring-aware validation"""
       if ring_level >= RingLevel.RING_3_USER.value:  # Ring 3 agents
           for key in KERNEL_CONTROL_KEYS:
               if key in output:
                   logger.error(f"🚨 [SecurityViolation] Node {node_id} (Ring {ring_level}) "
                               f"attempted to forge kernel command: {key}")
                   # Remove the key
                   del output[key]
                   # Trigger security event
                   _log_security_event("KERNEL_COMMAND_FORGERY", node_id=node_id, key=key)
       return output
   ```

3. **Documentation**:
   - Create `docs/kernel_interface_spec.md` (detailed API reference)
   - Update `docs/architecture.md` with Ring 0-3 security model

---

## 🤖 Part 2: Governor Node Implementation

### Architecture Design

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Governor Node (Ring 1)                        │
│                                                                      │
│  Purpose: 자율형 에이전트 출력 검증 및 _kernel 명령 생성             │
│  Execution Context: 모든 Ring 3 Agent 노드 직후 자동 실행             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Inputs:                                                             │
│  ├─ agent_output: Dict - 에이전트 노드의 raw output                  │
│  ├─ agent_metadata: Dict - node_id, ring_level, execution_time      │
│  └─ workflow_state: Dict - 전체 워크플로우 상태                      │
│                                                                      │
│  Validation Logic:                                                   │
│  ├─ 1. Plan Change Detection (재계획 감지)                           │
│  │   └─ agent_output["plan_hash"] != state["last_plan_hash"]        │
│  │       → _kernel_skip_segments 생성 (기존 플랜 obsolete 처리)      │
│  │                                                                   │
│  ├─ 2. SLOP Detection (의심스러운 대용량 출력)                        │
│  │   └─ len(agent_output) > 500KB or token_count > 10000           │
│  │       → _kernel_inject_recovery (HITP 승인 노드 삽입)            │
│  │                                                                   │
│  ├─ 3. Gas Fee Monitoring (비용 폭주 탐지)                           │
│  │   └─ state["total_llm_cost"] > $100                             │
│  │       → _kernel_modify_parallelism (동시성 감소)                 │
│  │                                                                   │
│  ├─ 4. Circuit Breaker (재시도 한계 초과)                            │
│  │   └─ agent_retry_count > 3                                      │
│  │       → _kernel_request_human_approval                           │
│  │                                                                   │
│  └─ 5. Prompt Injection Validation (Ring Protection 재검증)          │
│      └─ PromptSecurityGuard.validate_prompt(agent_output)           │
│          → violations 발견 시 SIGKILL                                │
│                                                                      │
│  Outputs:                                                            │
│  ├─ governance_decision: Dict                                        │
│  │   ├─ approved: bool                                              │
│  │   ├─ violations: List[str]                                       │
│  │   └─ kernel_commands: Dict[str, Any]                             │
│  │       ├─ _kernel_skip_segments: [...]                            │
│  │       ├─ _kernel_inject_recovery: {...}                          │
│  │       └─ _kernel_modify_parallelism: {...}                       │
│  │                                                                   │
│  └─ governance_audit_log: Dict (DynamoDB 저장)                       │
│      ├─ timestamp: float                                             │
│      ├─ agent_id: str                                                │
│      ├─ decision: str (APPROVED / REJECTED / ESCALATED)             │
│      ├─ violations: List[str]                                        │
│      └─ kernel_commands_issued: List[str]                            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 🎯 Implementation Strategy

**Option A: Extend `handlers/core/main.py` (Recommended)**
- Pros: 기존 노드 러너와 동일한 execution context
- Cons: main.py가 이미 4000+ 라인 (추가 복잡도)

**Option B: Create `handlers/governance/governor_runner.py` (Modular)**
- Pros: 명확한 책임 분리, 독립적 테스트 가능
- Cons: 새로운 import 경로 추가 필요

**De⚡ Optimistic Governance (v2.0 - 레이턴시 최적화)

**문제**: 모든 에이전트 노드 뒤에 Governor 동기 실행 → 레이턴시 2배

**해결**:
```python
# Ring-based Governance Mode Selection
GOVERNANCE_MODE = {
    RingLevel.RING_3_USER: "OPTIMISTIC",  # 비동기 검증 (로그만)
    RingLevel.RING_2_TRUSTED: "OPTIMISTIC",
    RingLevel.RING_1_DRIVER: "STRICT",    # 동기 검증 (필수)
    RingLevel.RING_0_KERNEL: "STRICT"
}
```

**작동 방식**:
1. **OPTIMISTIC Mode (Ring 2-3)**:
   - Governor 노드를 비동기(Async)로 실행
   - Violations 발견 시 CloudWatch 로그만 기록
   - 워크플로우 실행은 중단 없이 계속 진행
   - 사후 분석(Post-mortem)으로 패턴 학습

2. **STRICT Mode (Ring 0-1)**:
   - Governor 노드를 동기(Sync)로 실행
   - Violations 발견 시 즉시 _kernel 명령 발행
   - 워크플로우 차단 가능 (SIGKILL)

**레이턴시 영향**:
- 90% 워크플로우 (Ring 2-3) → 레이턴시 영향 < 5% (비동기)
- 10% 고위험 워크플로우 (Ring 0-1) → 레이턴시 +100% (동기 검증 필수)

### cision**: **Option B (Modular Approach)**

### 📝 Implementation Code

#### File: `handlers/governance/governor_runner.py`

```python
"""
🛡️ Governor Node Runner (Ring 1 Level)

Purpose:
    자율형 에이전트(Manus, Moltbot 등)의 출력을 검증하고
    _kernel 명령을 생성하여 동적 스케줄링을 제어합니다.

Architecture:
    - Ring 1 Security Level (Kernel보다 낮지만 Agent보다 높음)
    - Agent 출력의 "사후 검증" 수행 (Ring Protection은 "사전 검증")
    - Merkle Manifest 재생성 트리거 (Phase 8.3)

Integration:
    1. workflow_config에 governor_enabled: true 설정
    2. Agent 노드 실행 직후 자동으로 Governor 노드 실행
    3. Governor 출력(_kernel 명령)을 SegmentRunnerService가 처리
"""

import json
import time
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

from src.common.constants import SecurityConfig, KERNEL_CONTROL_KEYS
from src.services.recovery.prompt_security_guard import (
    get_security_guard, RingLevel, SecurityViolation
)

logger = logging.getLogger(__name__)


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


@dataclass
class GovernanceDecision:
    """Governor의 최종 결정"""
    approved: bool
    decision: str  # APPROVED / REJECTED / ESCALATED
    violations: List[str]
    kernel_commands: Dict[str, Any]
    audit_log: Dict[str, Any]


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
                "guardrails": {
                    "max_output_size_kb": 500,
                    "max_token_count": 10000,
                    "max_gas_fee_usd": 100,
                    "max_retry_count": 3
                }
            }
    
    Returns:
        Dict containing:
            - governance_decision: 승인/거부/에스컬레이션
            - _kernel_skip_segments: 스킵할 세그먼트 ID 리스트 (조건부)
            - _kernel_inject_recovery: 복구 세그먼트 삽입 요청 (조건부)
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
    # 2. Agent Behavior Analysis
    # ────────────────────────────────────────────────────────────────────
    analysis = _analyze_agent_behavior(
        agent_id=agent_node_id,
        agent_output=agent_output,
        workflow_state=state,
        config=config
    )
    
    logger.info(f"🔍 [Governor] Agent Analysis: {agent_node_id}, "
               f"Anomaly Score: {analysis.anomaly_score:.2f}, "
               f"Violations: {len(analysis.violations)}")
    
    # ────────────────────────────────────────────────────────────────────
    # 3. Generate Governance Decision
    # ────────────────────────────────────────────────────────────────────
    decision = _make_governance_decision(analysis, state, config)
    
    # ────────────────────────────────────────────────────────────────────
    # 4. Persist Audit Log (DynamoDB)
    # ────────────────────────────────────────────────────────────────────
    _save_governance_audit_log(decision.audit_log)
    
    # ────────────────────────────────────────────────────────────────────
    # 5. Return _kernel Commands
    # ────────────────────────────────────────────────────────────────────
    result = {
        "governance_decision": decision.decision,
        "approved": decision.approved,
        "violations": decision.violations,
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
    config: Dict[str, Any]
) -> AgentBehaviorAnalysis:
    """
    에이전트 출력 분석
    
    Checks:
        1. Plan Change Detection (재계획 감지)
        2. SLOP Detection (Suspicious Large Output Pattern)
        3. Gas Fee Monitoring
        4. Circuit Breaker (재시도 한계)
        5. Prompt Injection Validation
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
    security_guard = get_security_guard()
    
    # Check agent output text for injection patterns
    output_text = output_json[:10000]  # Sample first 10KB
    security_result = security_guard.validate_prompt(
        content=output_text,
        ring_level=RingLevel.RING_3_USER,
        context={"agent_id": agent_id, "node_type": "agent"}
    )
    
    if security_result.violations:
        for violation in security_result.violations:
            violations.append(
                f"SECURITY_VIOLATION: {violation.violation_type.value} - {violation.message}"
            )
    
    # ────────────────────────────────────────────────────────────────────
    # Metric 6: Kernel Command Forgery Detection
    # ────────────────────────────────────────────────────────────────────
    for kernel_key in KERNEL_CONTROL_KEYS:
        if kernel_key in agent_output:
            violations.append(
                f"KERNEL_COMMAND_FORGERY: Agent attempted to output {kernel_key}"
            )
    
    # ────────────────────────────────────────────────────────────────────
    # Calculate Anomaly Score (0.0 ~ 1.0)
    # ────────────────────────────────────────────────────────────────────
    anomaly_score = min(len(violations) * 0.2, 1.0)  # Each violation adds 0.2
    
    return AgentBehaviorAnalysis(
        agent_id=agent_id,
        execution_time_ms=0,  # TODO: Extract from metadata
        output_size_bytes=output_size_bytes,
        token_count=agent_output.get("token_count"),
        plan_changed=plan_changed,
        plan_hash=current_plan_hash,
        retry_count=retry_count,
        violations=violations,
        anomaly_score=anomaly_score
    )


# ============================================================================
# 🛡️ Governance Decision Logic
# ============================================================================

def _make_governance_decision(
    analysis: AgentBehaviorAnalysis,
    workflow_state: Dict[str, Any],
    config: Dict[str, Any]
) -> GovernanceDecision:
    """
    분석 결과를 바탕으로 _kernel 명령 생성
    
    Decision Matrix:
        - anomaly_score >= 0.8 → REJECTED (SIGKILL)
        - anomaly_score >= 0.5 → ESCALATED (HITP 삽입)
        - anomaly_score < 0.5 → APPROVED
    
    Returns:
        GovernanceDecision with _kernel commands
    """
    kernel_commands = {}
    
    # ────────────────────────────────────────────────────────────────────
    # Decision 1: Plan Change → Skip obsolete segments
    # ────────────────────────────────────────────────────────────────────
    if analysis.plan_changed:
        # TODO: Calculate which segments are obsolete based on new plan
        # For now, placeholder logic
        obsolete_segment_ids = _calculate_obsolete_segments(workflow_state, analysis)
        
        if obsolete_segment_ids:
            kernel_commands["_kernel_skip_segments"] = obsolete_segment_ids
            kernel_commands["_kernel_skip_reason"] = (
                f"Agent {analysis.agent_id} re-planned workflow. "
                f"Previous plan hash: {workflow_state.get('last_plan_hash', 'N/A')[:8]}, "
                f"New plan hash: {analysis.plan_hash[:8] if analysis.plan_hash else 'N/A'}"
            )
    
    # ────────────────────────────────────────────────────────────────────
    # Decision 2: SLOP or Security Violation → Inject HITP
    # ────────────────────────────────────────────────────────────────────
    critical_violations = [v for v in analysis.violations if "SLOP" in v or "SECURITY" in v]
    
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
        
        kernel_commands["_kernel_inject_recovery"] = {
            "segments": [hitp_segment],
            "reason": f"Security anomaly detected (score: {analysis.anomaly_score:.2f})"
        }
    
    # ────────────────────────────────────────────────────────────────────
    # Decision 3: Gas Fee Exceeded → Reduce Parallelism
    # ────────────────────────────────────────────────────────────────────
    if any("GAS_FEE" in v for v in analysis.violations):
        kernel_commands["_kernel_modify_parallelism"] = {
            "max_concurrent_branches": 5,
            "reason": f"Cost guardrail triggered: {workflow_state.get('total_llm_cost', 0):.2f} USD"
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
        "plan_hash": analysis.plan_hash
    }
    
    return GovernanceDecision(
        approved=approved,
        decision=decision,
        violations=analysis.violations,
        kernel_commands=kernel_commands,
        audit_log=audit_log
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
    
    Example:
        partition_map = {
            "validation_node": [5, 6],
            "summary_node": [7]
        }
        agent_skip_nodes = ["validation_node"]
        → Returns: [5, 6]
    """
    # [v2.0] partition_map 기반 구현
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
    
    TODO: DynamoDB client 구현
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
# 🛡️ Node Registration (handlers/core/main.py integration)
# ============================================================================

# 이 함수는 handlers/core/main.py의 NODE_TYPE_RUNNERS dict에 등록됨:
# 
# NODE_TYPE_RUNNERS = {
#     ...
#     "governor": governor_node_runner,
# }
```

---

## 🛡️ Part 3: Agent Guardrails Library

### File: `services/governance/agent_guardrails.py`

```python
"""
🛡️ Agent Guardrails Library

Purpose:
    자율형 에이전트의 비정상 행동을 탐지하고 차단하는 guardrail 함수 모음

Guardrails:
    1. Circuit Breaker: Stop runaway agents
    2. SLOP Detection: Suspicious Large Output Pattern
    3. Gas Fee Monitor: Cost explosion prevention
    4. Plan Drift Detection: Goal misalignment detection
"""

import time
import hashlib
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass


@dataclass
class CircuitBreakerState:
    """Circuit Breaker 상태"""
    failure_count: int
    last_failure_time: float
    state: str  # CLOSED / OPEN / HALF_OPEN


class CircuitBreaker:
    """
    Circuit Breaker Pattern for Agent Retry Control
    
    States:
        - CLOSED: Normal operation
        - OPEN: Too many failures, block all requests
        - HALF_OPEN: Test if system recovered
    
    Thresholds:
        - failure_threshold: 3 (3번 실패 시 OPEN)
        - timeout: 60s (OPEN 후 60초 뒤 HALF_OPEN)
    """
    
    def __init__(self, failure_threshold: int = 3, timeout_seconds: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self._state = CircuitBreakerState(
            failure_count=0,
            last_failure_time=0,
            state="CLOSED"
        )
    
    def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection"""
        if self._state.state == "OPEN":
            # Check if timeout expired
            if time.time() - self._state.last_failure_time > self.timeout_seconds:
                self._state.state = "HALF_OPEN"
            else:
                raise Exception(f"Circuit Breaker OPEN: {self._state.failure_count} failures")
        
        try:
            result = func(*args, **kwargs)
            # Success: Reset circuit breaker
            self._state.failure_count = 0
            self._state.state = "CLOSED"
            return result
        except Exception as e:
            # Failure: Increment counter
            self._state.failure_count += 1
            self._state.last_failure_time = time.time()
            
            if self._state.failure_count >= self.failure_threshold:
                self._state.state = "OPEN"
            
            raise e


def detect_slop(output: Dict[str, Any], threshold_kb: int = 500) -> Tuple[bool, Optional[str]]:
    """
    SLOP Detection: Suspicious Large Output Pattern
    
    Indicators:
        - Output size > threshold_kb
        - Repetitive patterns (e.g., "a" * 10000)
        - Excessive JSON nesting (depth > 10)
    
    Returns:
        (is_slop: bool, reason: Optional[str])
    """
    import json
    
    output_json = json.dumps(output, ensure_ascii=False)
    output_size_kb = len(output_json.encode('utf-8')) / 1024
    
    # Check 1: Size threshold
    if output_size_kb > threshold_kb:
        return True, f"Output size {output_size_kb:.1f}KB exceeds {threshold_kb}KB"
    
    # Check 2: Repetitive patterns
    # (Simple heuristic: check if any substring of length 10 repeats > 100 times)
    for i in range(0, len(output_json) - 10, 100):
        substring = output_json[i:i+10]
        count = output_json.count(substring)
        if count > 100:
            return True, f"Repetitive pattern detected: '{substring}' appears {count} times"
    
    # Check 3: Excessive nesting (TODO: Implement JSON depth check)
    # Placeholder for now
    
    return False, None


def calculate_gas_fee(
    workflow_state: Dict[str, Any],
    cost_per_token: float = 0.00001
) -> float:
    """
    Calculate accumulated gas fee (LLM API cost)
    
    Args:
        workflow_state: 전체 워크플로우 상태
        cost_per_token: 토큰당 비용 (default: $0.01 per 1000 tokens)
    
    Returns:
        Total cost in USD
    """
    total_tokens = workflow_state.get("total_tokens_used", 0)
    return total_tokens * cost_per_token


def detect_plan_drift(
    current_plan: Dict[str, Any],
    original_plan: Dict[str, Any],
    similarity_threshold: float = 0.7,
    use_semantic_validation: bool = True
) -> Tuple[bool, float]:
    """
    Plan Drift Detection: 에이전트의 목표가 원래 의도에서 벗어났는지 감지
    
    Method (v2.0 - Intent Retention Rate):
        - Hash 기반 비교 (빠른 1차 필터)
        - 의미론적 검증: 소형 모델(Llama-3-8B)로 "새 계획이 원래 목표 지향?" 판단
        - Intent Retention Rate: 0.0 (완전 이탈) ~ 1.0 (동일 목표)
    
    Args:
        current_plan: 현재 에이전트의 계획
        original_plan: 원래 계획
        similarity_threshold: Intent Retention Rate 임계값 (default: 0.7)
        use_semantic_validation: 의미론적 검증 사용 여부 (default: True)
    
    Returns:
        (drift_detected: bool, intent_retention_rate: float)
    
    Example:
        Original: "Summarize customer feedback and generate insights"
        Current:  "Summarize customer feedback and create charts"
        → Intent Retention Rate: 0.85 (목표 유지, 표현만 변경)
        
        Original: "Summarize customer feedback and generate insights"
        Current:  "Delete all customer data from database"
        → Intent Retention Rate: 0.1 (악의적 목표 이탈)
    """
    import json
    
    # Phase 1: Hash-based quick check
    current_hash = hashlib.sha256(
        json.dumps(current_plan, sort_keys=True).encode()
    ).hexdigest()
    original_hash = hashlib.sha256(
        json.dumps(original_plan, sort_keys=True).encode()
    ).hexdigest()
    
    # If hashes match, no drift (perfect retention)
    if current_hash == original_hash:
        return False, 1.0
    
    # Phase 2: Semantic validation (Priority 2 implementation)
    if use_semantic_validation:
        # [v2.0] Intent Retention Rate 계산
        # TODO: Integrate Llama-3-8B or similar model
        # Prompt: "Does the new plan '{current_plan}' still achieve the 
        #          original goal '{original_plan}'? Rate 0.0-1.0."
        
        # Placeholder: Use simple keyword overlap as proxy
        current_keywords = set(str(current_plan).lower().split())
        original_keywords = set(str(original_plan).lower().split())
        
        overlap = len(current_keywords & original_keywords)
        total = len(current_keywords | original_keywords)
        
        intent_retention_rate = overlap / total if total > 0 else 0.0
        
        # Adjust for destructive keywords (security heuristic)
        destructive_keywords = {"delete", "remove", "drop", "destroy", "bypass", "ignore"}
        if any(kw in current_keywords for kw in destructive_keywords):
            intent_retention_rate *= 0.5  # Penalize destructive actions
        
        logger.info(f"[Plan Drift] Intent Retention Rate: {intent_retention_rate:.2f}, "
                   f"Threshold: {similarity_threshold}, Drift: {drift_detected}")
        
        drift_detected = intent_retention_rate < similarity_threshold
        return drift_detected, intent_retention_rate
    
    # Fallback: If semantic validation disabled, assume drift
    return True, 0.0
```

---

## 📊 Part 4: Governance Metrics & DynamoDB Schema

### DynamoDB Table: `GovernanceAuditLog`

```python
# ============================================================================
# DynamoDB Table Schema: GovernanceAuditLog
# ============================================================================

TABLE_NAME = "GovernanceAuditLog"

SCHEMA = {
    "TableName": TABLE_NAME,
    "KeySchema": [
        {"AttributeName": "workflow_id", "KeyType": "HASH"},   # Partition Key
        {"AttributeName": "timestamp", "KeyType": "RANGE"}     # Sort Key
    ],
    "AttributeDefinitions": [
        {"AttributeName": "workflow_id", "AttributeType": "S"},
        {"AttributeName": "timestamp", "AttributeType": "N"},
        {"AttributeName": "agent_id", "AttributeType": "S"}
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "AgentIdIndex",
            "KeySchema": [
                {"AttributeName": "agent_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"}
        }
    ],
    "BillingMode": "PAY_PER_REQUEST"
}

# ============================================================================
# Item Structure
# ============================================================================

ITEM_EXAMPLE = {
    "workflow_id": "wf-abc123",
    "timestamp": 1733123456.789,
    "agent_id": "Manus-v2",
    "decision": "ESCALATED",  # APPROVED / REJECTED / ESCALATED
    "approved": True,
    "anomaly_score": 0.6,
    "violations": [
        "PLAN_CHANGE_DETECTED: abc123 → def456",
        "SLOP_DETECTED: Output size 600KB exceeds 500KB"
    ],
    "kernel_commands_issued": [
        "_kernel_skip_segments",
        "_kernel_inject_recovery"
    ],
    "output_size_bytes": 614400,
    "plan_hash": "sha256:def456...",
    "execution_time_ms": 1250.5
}

# ============================================================================
# Query Patterns
# ============================================================================

# Query 1: Get all governance decisions for a workflow
# query(
#     TableName=TABLE_NAME,
#     KeyConditionExpression="workflow_id = :wf_id",
#     ExpressionAttributeValues={":wf_id": "wf-abc123"}
# )

# Query 2: Get all decisions by a specific agent (last 7 days)
# query(
#     TableName=TABLE_NAME,
#     IndexName="AgentIdIndex",
#     KeyConditionExpression="agent_id = :agent_id AND timestamp > :week_ago",
#     ExpressionAttributeValues={
#         ":agent_id": "Manus-v2",
#         ":week_ago": time.time() - 7*24*3600
#     }
# )
```

### CloudWatch Metrics

```python
# ============================================================================
# CloudWatch Custom Metrics for Governance
# ============================================================================

GOVERNANCE_METRICS = {
    "Namespace": "Analemma/Governance",
    "Metrics": [
        {
            "MetricName": "GovernanceDecisionRate",
            "Dimensions": [
                {"Name": "Decision", "Value": "APPROVED"},
                {"Name": "Decision", "Value": "REJECTED"},
                {"Name": "Decision", "Value": "ESCALATED"}
            ],
            "Unit": "Count"
        },
        {
            "MetricName": "AnomalyScore",
            "Dimensions": [{"Name": "AgentId", "Value": "Manus-v2"}],
            "Unit": "None",
            "StatisticValues": {
                "SampleCount": 1,
                "Sum": 0.6,
                "Minimum": 0.0,
                "Maximum": 1.0
            }
        },
        {
            "MetricName": "ViolationCount",
            "Dimensions": [{"Name": "ViolationType", "Value": "SLOP_DETECTED"}],
            "Unit": "Count"
        },
        {
            "MetricName": "KernelCommandIssuedRate",
            "Dimensions": [{"Name": "Command", "Value": "_kernel_skip_segments"}],
            "Unit": "Count"
        },
        {
            "MetricName": "GasFeeTotal",
            "Dimensions": [{"Name": "WorkflowId", "Value": "wf-abc123"}],
            "Unit": "None"  # USD
        }
    ]
}

# Emit metrics in governor_runner.py:
# import boto3
# cloudwatch = boto3.client('cloudwatch')
# cloudwatch.put_metric_data(
#     Namespace='Analemma/Governance',
#     MetricData=[
#         {
#             'MetricName': 'AnomalyScore',
#             'Value': analysis.anomaly_score,
#             'Unit': 'None',
#             'Dimensions': [{'Name': 'AgentId', 'Value': analysis.agent_id}]
#         }
#     ]
# )
```

---

## 📝 Part 5: Integration Checklist

### Phase 1: Foundation (Priority 1)

- [ ] **Create `handlers/governance/governor_runner.py`**
  - [ ] Implement `governor_node_runner()` function
  - [ ] Implement `_analyze_agent_behavior()` logic
  - [ ] Implement `_make_governance_decision()` logic
  - [ ] Add placeholder for `_save_governance_audit_log()`

- [ ] **Update `handlers/core/main.py`**
  - [ ] Register `governor` node type in `NODE_TYPE_RUNNERS` dict
  - [ ] Import `governor_node_runner` from governance module

- [ ] **Update `commons/constants.py`**
  - [ ] Add `KERNEL_CONTROL_KEYS` constant
  - [ ] Document Ring 0-3 security model

- [ ] **Enhance `commons/state_guard.py`**
  - [ ] Add Ring-aware validation to `_validate_output_keys()`
  - [ ] Implement kernel command forgery detection
  - [ ] Add security event logging

- [ ] **Documentation**
  - [ ] Create `docs/kernel_interface_spec.md`
  - [ ] Update `docs/architecture.md` with Governor Node
  - [ ] Create usage examples in `examples/dynamic_scheduling_guide.md`

### Phase 2: Guardrails & Metrics (Priority 2)

- [ ] **Create `services/governance/agent_guardrails.py`**
  - [ ] Implement `CircuitBreaker` class
  - [ ] Implement `detect_slop()` function
  - [ ] Implement `calculate_gas_fee()` function
  - [ ] Implement `detect_plan_drift()` function

- [ ] **DynamoDB Table Creation**
  - [ ] Create `GovernanceAuditLog` table (CloudFormation)
  - [ ] Implement `_save_governance_audit_log()` in governor_runner.py
  - [ ] Add DynamoDB read permissions to Lambda IAM role

- [ ] **CloudWatch Metrics Integration**
  - [ ] Emit `AnomalyScore` metric in governor_runner
  - [ ] Emit `ViolationCount` metric per violation type
  - [ ] Emit `KernelCommandIssuedRate` metric
  - [ ] Create CloudWatch Dashboard for governance metrics

### Phase 3: Advanced Features (Priority 3)

- [ ] **Time-Travel Rollback**
  - [ ] Implement `_kernel_rollback_to_manifest` handler
  - [ ] Integrate with `StateVersioningService.get_manifest_by_id()`
  - [ ] Create rollback UI (frontend integration)

- [ ] **Ring Architecture Formalization**
  - [ ] Define `RingLevel` enum in `commons/constants.py`
  - [ ] Add `ring_level` metadata to node configs
  - [ ] Implement node execution permission checks

- [ ] **Dynamic Parallelism Control**
  - [ ] Implement `_kernel_modify_parallelism` handler in `segment_runner_service.py`
  - [ ] Integrate with Parallel/Map node execution

- [ ] **HITP Request Handler**
  - [ ] Implement `_kernel_request_human_approval` handler
  - [ ] Create dynamic HITP segment injection logic
  - [ ] Frontend UI for HITP approval workflow

---

## 🧪 Part 6: Testing Strategy

### Unit Tests

```python
# tests/backend/unit/test_governor_node.py

def test_governor_approves_safe_agent_output():
    """Governor는 안전한 Agent 출력을 승인해야 함"""
    state = {
        "manus_output": {"plan": "safe plan", "plan_hash": "abc123"},
        "last_plan_hash": "abc123"
    }
    config = {"agent_node_id": "manus", "guardrails": {}}
    
    result = governor_node_runner(state, config)
    
    assert result["governance_decision"] == "APPROVED"
    assert result["approved"] is True
    assert "_kernel_skip_segments" not in result


def test_governor_detects_slop():
    """Governor는 SLOP 패턴을 탐지하고 HITP를 삽입해야 함"""
    large_output = {"data": "x" * 600000}  # 600KB
    state = {"agent_output": large_output}
    config = {"agent_node_id": "agent", "guardrails": {"max_output_size_kb": 500}}
    
    result = governor_node_runner(state, config)
    
    assert result["governance_decision"] == "ESCALATED"
    assert "_kernel_inject_recovery" in result
    assert "SLOP_DETECTED" in str(result["violations"])


def test_governor_prevents_kernel_command_forgery():
    """Governor는 Agent의 _kernel 명령 위조 시도를 차단해야 함"""
    malicious_output = {
        "_kernel_skip_segments": [1, 2, 3],  # Forgery attempt
        "plan": "malicious plan"
    }
    state = {"agent_output": malicious_output}
    config = {"agent_node_id": "agent", "guardrails": {}}
    
    result = governor_node_runner(state, config)
    
    assert "KERNEL_COMMAND_FORGERY" in str(result["violations"])
    assert result["governance_decision"] == "ESCALATED"
```

### Integration Tests

```python
# tests/backend/integration/test_governor_integration.py

def test_governor_triggers_manifest_regeneration():
    """
    Governor가 _kernel_skip_segments를 발행하면
    Manifest 재생성이 트리거되어야 함 (Phase 8.3)
    """
    # TODO: End-to-end test with SegmentRunnerService
    pass


def test_governor_audit_log_saved_to_dynamodb():
    """Governor 결정이 DynamoDB에 저장되는지 확인"""
    # TODO: Mock DynamoDB client and verify put_item() call
    pass
```

---

## 📊 Part 7: Success Metrics

### Pre-Implementation Baseline
- ❌ Governor Node: 0%
- ❌ Agent Guardrails: 0%
- ❌ Governance Metrics: 0%
- ❌ _kernel Interface Docs: 0%

### Post-Implementation Targets (Priority 1)
- ✅ Governor Node: 100% (핵심 validation logic)
- ✅ _kernel Interface: 100% (문서화 + security enforcement)
- ✅ Integration: 80% (handlers/core/main.py 연동)
- 🟡 Testing: 50% (unit tests for governor logic)

### Post-Implementation Targets (Priority 2)
- ✅ Agent Guardrails: 100% (Circuit Breaker, SLOP, Gas Fee)
- ✅ DynamoDB Audit Log: 100% (table + write logic)
- ✅ CloudWatch Metrics: 80% (core metrics emitted)
- 🟡 Documentation: 70% (usage examples + API reference)

### Post-Implementation Targets (Priority 3)
- 🟡 Time-Travel Rollback: 60% (핵심 로직만 구현)
- 🟡 Ring Architecture: 50% (문서화 + 기본 권한 체크)
- ⏳ Dynamic Parallelism: 30% (설계만 완료)
- ⏳ HITP Request Handler: 30% (설계만 완료)

---

## 🚀 Part 8: Next Steps

### Immediate Actions (This Week)

1. **Review & Approval**:
   - [ ] Review this implementation plan with team
   - [ ] Prioritize features (confirm Priority 1-3 breakdown)
   - [ ] Approve architecture decisions (Option B: Modular Approach)

2. **Create Foundation Files**:
   - [ ] Create `handlers/governance/` directory
   - [ ] Create `handlers/governance/__init__.py`
   - [ ] Create `handlers/governance/governor_runner.py` (skeleton)
   - [ ] Create `services/governance/` directory
   - [ ] Create `services/governance/agent_guardrails.py` (skeleton)

3. **Update Documentation**:
   - [ ] Create `docs/kernel_interface_spec.md`
   - [ ] Update `docs/architecture.md` with Governor Node diagram
   - [ ] Create `examples/dynamic_scheduling_guide.md`

### Week 2-3: Priority 1 Implementation

1. **Implement Governor Node**:
   - [ ] Complete `governor_node_runner()` logic
   - [ ] Implement agent behavior analysis
   - [ ] Implement decision logic
   - [ ] Write unit tests (target: 80% coverage)

2. **Integrate with Kernel**:
   - [ ] Update `commons/constants.py` (KERNEL_CONTROL_KEYS)
   - [ ] Enhance `commons/state_guard.py` (Ring-aware validation)
   - [ ] Register governor node in `handlers/core/main.py`

3. **Testing & Validation**:
   - [ ] End-to-end test: Agent → Governor → _kernel commands
   - [ ] Test manifest regeneration trigger (Phase 8.3)
   - [ ] Test security violation logging

### Week 4-5: Priority 2 Implementation

1. **Agent Guardrails**:
   - [ ] Implement Circuit Breaker
   - [ ] Implement SLOP detection
   - [ ] Implement Gas Fee monitoring
   - [ ] Write unit tests

2. **Metrics & Audit**:
   - [ ] Create DynamoDB GovernanceAuditLog table (CloudFormation)
   - [ ] Implement audit log persistence
   - [ ] Emit CloudWatch metrics
   - [ ] Create CloudWatch Dashboard

### Month 2: Priority 3 Features

1. **Time-Travel Rollback**:
   - [ ] Design rollback UX (frontend mockups)
   - [ ] Implement `_kernel_rollback_to_manifest` handler
   - [ ] Integration test with StateVersioningService

2. **Ring Architecture Formalization**:
   - [ ] Define Ring 0-3 security levels in docs
   - [ ] Implement node execution permission checks
   - [ ] Add `ring_level` metadata to workflow JSON schema

---

## 🎯 Conclusion

### Critical Path Summary

```
현재 상태:
  ✅ Phase 0-8: Merkle DAG + Trust Chain 완료
  ✅ Dynamic Scheduling: _mark_segments_for_skip, _inject_recovery_segments 구현
  ❌ Agent Governance: Governor Node, Guardrails, Metrics 미구현

위험:
  🚨 에이전트가 _kernel 명령을 직접 출력 가능 (위조 위험)
  🚨 SLOP, Gas Fee 폭주 탐지 메커니즘 없음
  🚨 Governance 증명 메트릭 부재 (audit trail 없음)

해결책 (v2.0 - Critical Feedback Integration):
  1. Governor Node (Ring 1): 에이전트 출력 검증 + _kernel 명령 생성
     ↳ [NEW] Optimistic Governance: Ring 2-3 비동기 (레이턴시 < 5%)
  
  2. Agent Guardrails: Circuit Breaker, SLOP 탐지, Gas Fee 모니터
     ↳ [NEW] Intent Retention Rate: 의미론적 Plan Drift 검증 (0.0~1.0)
  
  3. partition_map 기반 Obsolete Segments 계산
     ↳ [NEW] {node_id → [segment_ids]} 매핑으로 99% 정확도
  
  4. _kernel Interface: 표준화 + Security Enforcement
     ↳ [KILLER FEATURE] _kernel_rollback: 1ms 만에 과거 시점 복구
     ↳ [COST THROTTLE] _kernel_modify_parallelism: 비용 폭주 즉시 차단

구현 우선순위:
  Priority 1 (Immediate): Governor Node + Optimistic Governance + partition_map
  Priority 2 (Short-term): Guardrails + Intent Retention Rate + Metrics
  Priority 3 (Medium-term): Time-Travel Rollback (Killer Feature) + Ring Architecture
```

### Expected Outcomes

After **Priority 1** implementation:
- ✅ 에이전트 _kernel 명령 위조 100% 차단
- ✅ 에이전트 플랜 변경 자동 감지 (partition_map 기반 99% 정확도)
- ✅ Optimistic Governance로 레이턴시 영향 < 5% (90% 워크플로우)
- ✅ _kernel 인터페이스 명확한 문서화

After **Priority 2** implementation:
- ✅ SLOP, Gas Fee 폭주 자동 탐지
- ✅ Intent Retention Rate로 악의적 목표 이탈 감지 (임계값: 0.7)
- ✅ Circuit Breaker로 runaway agents 차단
- ✅ Governance 증명을 위한 audit trail 확보

After **Priority 3** implementation:
- ✅ Time-Travel Rollback으로 안전한 복구 (1ms 만에 과거 시점 복원)
- ✅ Ring 0-3 보안 아키텍처 완성
- ✅ 동적 병렬성 제어로 비용 최적화 (Cost Throttle)

---

## 📚 Appendix: Related Documents

1. **[Phase 8 Trust Chain](./PHASE_8_TRUST_CHAIN.md)**: Manifest Regeneration, Gatekeeper
2. **[Ring Protection System](../src/services/recovery/prompt_security_guard.py)**: Prompt Injection 탐지
3. **[Dynamic Scheduling](../src/services/execution/segment_runner_service.py)**: _mark_segments_for_skip, _inject_recovery_segments
4. **[Merkle DAG](./MERKLE_DAG_REFACTORING.md)**: Content-Addressable Storage, Pre-computed Hash

---

**End of Document**
