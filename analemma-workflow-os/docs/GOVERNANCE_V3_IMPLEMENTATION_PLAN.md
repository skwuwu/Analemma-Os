# Agent Governance v3.0 Implementation Plan
**Dual-Layer Governance: Law & Spirit**

**Date**: February 18, 2026  
**Version**: 3.0 (Evolution from v2.1.1)  
**Authors**: Analemma OS Architecture Team  
**Status**: DRAFT (Pending Approval)

---

## 📋 Executive Summary

현재 v2.1.1 Agent Governance는 **사후 검증(Post-execution)** 중심의 Reactive 시스템입니다. v3.0에서는 **선제적 차단(Pre-execution)** + **해석적 판단(Constitutional AI)**의 이중 계층 아키텍처로 진화하여:

- ✅ **비용 90% 절감**: 단순 위반은 LLM 없이 JSON Schema로 차단
- ✅ **설명 가능성 100%**: 모든 결정에 자연어 근거(Rationale) 제공
- ✅ **에이전트 신뢰도 추적**: Trust Score 기반 동적 검증 모드 전환
- ✅ **실시간 모니터링**: Frontend Dashboard와 WebSocket 연동

---

## 🏗️ Architecture Overview

### Dual-Layer Governance Model

```
┌─────────────────────────────────────────────────────────────────┐
│                    WORKFLOW EXECUTION REQUEST                    │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
    ┌──────────────────────────────────────────────────────────────┐
    │  LAYER 1: Stateless Policy Evaluator (Pre-execution)         │
    │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
    │  • JSON Schema Validation (Hard Rules)                       │
    │  • Time-based Restrictions (09:00-18:00)                     │
    │  • Budget Limits ($50 max)                                   │
    │  • PII Field Detection (email, ssn, card_number)             │
    │  • Ring Level Enforcement (Child <= Parent - 1)              │
    │                                                               │
    │  ⚡ Latency: < 1ms | Cost: $0 (No LLM)                       │
    └──────────────────┬───────────────────────────────────────────┘
                       │
                       ▼ (90% violations blocked here)
                       │
              ┌────────┴────────┐
              │   PASS          │   BLOCK → Return Error
              ▼                 │
    ┌──────────────────────────┴───────────────────────────────────┐
    │         SEGMENT EXECUTION (Agent Output)                     │
    └──────────────────────────┬──────────────────────────────────┘
                               ▼
    ┌──────────────────────────────────────────────────────────────┐
    │  LAYER 2: Constitutional Governor (Post-execution)           │
    │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
    │  • LLM-based Intent Analysis (Spirit)                        │
    │  • Constitutional Clause Mapping                             │
    │  • Anomaly Score Calculation                                 │
    │  • Trust Score Update                                        │
    │                                                               │
    │  ⚡ Latency: 200-500ms | Cost: ~$0.0005/request (Optimized) │
    └──────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
              ┌────────┴────────┐
              │   APPROVED      │   REJECTED/ROLLBACK
              ▼                 │
    ┌──────────────────┐        │
    │   CONTINUE       │        │
    └──────────────────┘        │
                                ▼
                    ┌──────────────────────────────────┐
                    │  IMMEDIATE ACTION                │
                    │  • Trust Score Update            │
                    │  • DynamoDB Audit Log            │
                    │  • SNS Event Publish (Async)     │
                    └──────────┬───────────────────────┘
                               │
                               ▼
              ┌────────────────────────────────────────────────┐
              │  ASYNC PIPELINE: Rationale Generation          │
              │  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │
              │  1. SQS Queue receives violation event         │
              │  2. Rationale Lambda consumes queue            │
              │  3. Triage: Tier 1 (f-string) or Tier 2 (SLM)  │
              │  4. Generate explanation                       │
              │  5. Patch Merkle Manifest metadata             │
              │                                                 │
              │  ⚡ Decoupled from main workflow               │
              └────────────────────────────────────────────────┘
```

---

## 🎯 Phase 1: Stateless Policy Evaluator (Priority: P0)

### 1.1 Implementation Scope

**File**: `src/services/governance/policy_evaluator.py` (NEW)

**Responsibilities**:
- Pre-execution validation (before SegmentRunner)
- Zero-cost rule checking (no LLM)
- Hard rule violations → immediate BLOCK

**Schema Definition**:
```python
@dataclass
class PolicyRule:
    """정형 정책 규칙"""
    rule_id: str
    rule_type: PolicyRuleType  # TIME_BASED, BUDGET, DATA_ISOLATION, PERMISSION
    condition: Dict[str, Any]  # JSON Schema condition
    decision: PolicyDecision  # ALLOW, DENY, MASK
    message: str

class PolicyRuleType(Enum):
    TIME_BASED = "time_based"
    BUDGET_LIMIT = "budget_limit"
    DATA_ISOLATION = "data_isolation"
    PERMISSION_INHERITANCE = "permission_inheritance"
    RETRY_QUOTA = "retry_quota"

@dataclass
class PolicyEvaluationResult:
    """정책 평가 결과"""
    allowed: bool
    violated_rules: List[PolicyRule]
    blocked_reason: Optional[str]
    evaluation_time_ms: float
```

### 1.2 Hard Rules Implementation

#### Time-based Restrictions
```python
def _check_time_restriction(rule: PolicyRule, context: Dict[str, Any]) -> bool:
    """
    시간 기반 제한 체크
    
    Example rule:
    {
        "rule_id": "payment_api_after_hours",
        "rule_type": "TIME_BASED",
        "condition": {
            "forbidden_hours": {"start": 18, "end": 9},  # 18:00 - 09:00
            "timezone": "Asia/Seoul",
            "actions": ["payment_api.charge", "billing.refund"]
        },
        "decision": "DENY",
        "message": "Payment APIs disabled outside business hours (09:00-18:00 KST)"
    }
    """
    from datetime import datetime
    import pytz
    
    tz = pytz.timezone(rule.condition.get("timezone", "UTC"))
    now = datetime.now(tz)
    current_hour = now.hour
    
    forbidden_start = rule.condition["forbidden_hours"]["start"]
    forbidden_end = rule.condition["forbidden_hours"]["end"]
    
    # Check if current time falls in forbidden range
    if forbidden_start > forbidden_end:  # Overnight (e.g., 18:00 - 09:00)
        is_forbidden = current_hour >= forbidden_start or current_hour < forbidden_end
    else:
        is_forbidden = forbidden_start <= current_hour < forbidden_end
    
    if is_forbidden:
        # Check if current action is in forbidden list
        current_action = context.get("node_config", {}).get("action")
        forbidden_actions = rule.condition.get("actions", [])
        return current_action in forbidden_actions
    
    return False
```

#### Data Isolation (PII Masking)
```python
def _check_data_isolation(rule: PolicyRule, context: Dict[str, Any]) -> bool:
    """
    데이터 격리 규칙 (PII 필드 접근 차단)
    
    Example rule:
    {
        "rule_id": "ring3_pii_access",
        "rule_type": "DATA_ISOLATION",
        "condition": {
            "ring_level": 3,
            "forbidden_fields": ["customer.email", "customer.ssn", "billing.card_number"],
            "mask_strategy": "sha256"  # or "redact", "deny"
        },
        "decision": "MASK",
        "message": "Ring 3 agents cannot access PII fields"
    }
    """
    agent_ring_level = context.get("agent_ring_level", 3)
    target_ring = rule.condition.get("ring_level")
    
    if agent_ring_level != target_ring:
        return False
    
    # Check if state contains forbidden fields
    state = context.get("current_state", {})
    forbidden_fields = rule.condition.get("forbidden_fields", [])
    
    for field_path in forbidden_fields:
        if _has_nested_field(state, field_path):
            # Apply masking or denial
            if rule.decision == PolicyDecision.MASK:
                _mask_field(state, field_path, rule.condition.get("mask_strategy", "sha256"))
            return rule.decision == PolicyDecision.DENY
    
    return False

def _mask_field(state: Dict, field_path: str, strategy: str):
    """필드 마스킹 (in-place)"""
    keys = field_path.split(".")
    target = state
    for key in keys[:-1]:
        target = target.get(key, {})
    
    last_key = keys[-1]
    if last_key in target:
        if strategy == "sha256":
            import hashlib
            original = str(target[last_key])
            target[last_key] = hashlib.sha256(original.encode()).hexdigest()[:16] + "***"
        elif strategy == "redact":
            target[last_key] = "***REDACTED***"
```

#### Permission Inheritance
```python
def _check_permission_inheritance(rule: PolicyRule, context: Dict[str, Any]) -> bool:
    """
    권한 승계 통제 (Child Workflow의 Ring 강제 하향)
    
    Example rule:
    {
        "rule_id": "child_workflow_downgrade",
        "rule_type": "PERMISSION_INHERITANCE",
        "condition": {
            "workflow_type": "CHILD",
            "max_ring_level_offset": -1  # Parent Ring - 1
        },
        "decision": "ENFORCE",
        "message": "Child workflows must have lower ring level than parent"
    }
    """
    workflow_type = context.get("workflow_type")
    if workflow_type != "CHILD":
        return False
    
    parent_ring = context.get("parent_ring_level", 0)
    child_ring = context.get("agent_ring_level", 0)
    max_offset = rule.condition.get("max_ring_level_offset", -1)
    
    max_allowed_ring = parent_ring + max_offset
    
    if child_ring > max_allowed_ring:
        # Force downgrade
        context["agent_ring_level"] = max_allowed_ring
        logger.warning(
            f"[Policy] Child workflow ring downgraded: {child_ring} → {max_allowed_ring}"
        )
        return True
    
    return False
```

### 1.3 Integration Point

**Location**: `src/services/execution/segment_runner_service.py`

```python
class SegmentRunnerService:
    def execute_segment(self, event: Dict[str, Any]) -> Dict[str, Any]:
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # [v3.0] Layer 1: Stateless Policy Evaluation (Pre-execution)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        from services.governance.policy_evaluator import evaluate_pre_execution_policies
        
        workflow_policies = event.get("workflow_config", {}).get("governance_policies", {})
        
        policy_result = evaluate_pre_execution_policies(
            policies=workflow_policies.get("hard_rules", []),
            context={
                "node_config": segment_config,
                "current_state": current_state,
                "agent_ring_level": segment_config.get("ring_level", 3),
                "workflow_type": event.get("workflow_type", "MAIN"),
                "parent_ring_level": event.get("parent_ring_level")
            }
        )
        
        if not policy_result.allowed:
            return {
                "status": "BLOCKED",
                "error_info": {
                    "type": "POLICY_VIOLATION",
                    "message": policy_result.blocked_reason,
                    "violated_rules": [r.rule_id for r in policy_result.violated_rules],
                    "evaluation_time_ms": policy_result.evaluation_time_ms
                }
            }
        
        # Continue with normal execution...
```

---

## 🧠 Phase 2: Constitutional Governor (Priority: P0)

### 2.1 Constitutional Schema

**File**: `src/services/governance/constitution.py` (NEW)

```python
@dataclass
class ConstitutionalClause:
    """AI 헌법 조항"""
    clause_id: str  # "article_3_user_protection"
    article_number: int
    title: str  # "사용자 보호 원칙"
    description: str  # "에이전트는 사용자의 개인정보를 유도하거나 보안을 우회하려 시도해선 안 됨"
    severity: ClauseSeverity  # CRITICAL, HIGH, MEDIUM, LOW
    examples: List[str]  # 위반 예시

class ClauseSeverity(Enum):
    CRITICAL = "critical"  # 즉시 REJECTED
    HIGH = "high"          # ESCALATED + HITP
    MEDIUM = "medium"      # WARNING
    LOW = "low"            # LOG_ONLY

# Default Constitution (Anthropic-inspired)
DEFAULT_CONSTITUTION = [
    ConstitutionalClause(
        clause_id="article_1_professional_tone",
        article_number=1,
        title="전문적 비즈니스 톤 유지",
        description="에이전트는 친근하되 전문적인 어조를 유지해야 하며, 비속어나 공격적 표현을 사용해선 안 됨",
        severity=ClauseSeverity.MEDIUM,
        examples=[
            "❌ '이 멍청한 API는...'",
            "✅ '해당 API 응답이 예상과 다릅니다...'"
        ]
    ),
    ConstitutionalClause(
        clause_id="article_2_no_harmful_content",
        article_number=2,
        title="유해 콘텐츠 생성 금지",
        description="에이전트는 폭력, 차별, 불법 행위를 조장하는 콘텐츠를 생성해선 안 됨",
        severity=ClauseSeverity.CRITICAL,
        examples=[
            "❌ '보안을 우회하기 위해 SQL Injection을...'",
            "✅ '정상적인 API 인증 절차를 따릅니다...'"
        ]
    ),
    ConstitutionalClause(
        clause_id="article_3_user_protection",
        article_number=3,
        title="사용자 보호 원칙",
        description="에이전트는 사용자의 비밀번호, 카드번호, 개인식별정보를 요구하거나 유도해선 안 됨",
        severity=ClauseSeverity.CRITICAL,
        examples=[
            "❌ '카드번호를 입력해주세요'",
            "✅ '결제는 안전한 외부 게이트웨이를 통해 진행됩니다'"
        ]
    ),
    ConstitutionalClause(
        clause_id="article_4_transparency",
        article_number=4,
        title="투명성 원칙",
        description="에이전트는 자신의 한계를 인정하고, 확실하지 않은 정보는 명시해야 함",
        severity=ClauseSeverity.LOW,
        examples=[
            "❌ '이 정보는 100% 정확합니다'",
            "✅ '제공된 데이터 기준으로 추정하면...'"
        ]
    ),
    ConstitutionalClause(
        clause_id="article_5_no_security_bypass",
        article_number=5,
        title="보안 정책 준수",
        description="에이전트는 시스템의 보안 정책, 접근 제어, 감사 로그를 우회하려 시도해선 안 됨",
        severity=ClauseSeverity.CRITICAL,
        examples=[
            "❌ 'DynamoDB 스캔으로 모든 사용자 데이터를...'",
            "✅ 'GSI를 통해 인가된 범위의 데이터만 조회합니다'"
        ]
    )
]
```

### 2.2 Constitutional Evaluation (Governor Node)

**File**: `src/handlers/governance/governor_runner.py` (MODIFY)

```python
def governor_node_runner(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    v3.0 Enhancement: Constitutional AI Integration
    """
    # ... existing code ...
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [v3.0] Constitutional Clause Mapping + Retroactive Masking
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    violated_clauses, masked_output = _evaluate_constitution(
        agent_output=agent_output,
        constitution=config.get("constitution", DEFAULT_CONSTITUTION),
        agent_thought=agent_output.get("thought", "")
    )
    
    # Replace agent output with masked version if PII detected
    if masked_output != agent_output:
        logger.warning(
            f"[Governor] PII text leakage detected - Agent output has been retroactively masked"
        )
        agent_output = masked_output
        state["agent_output"] = masked_output  # Update state with masked version
    
    # Add constitutional violations to analysis
    for clause_violation in violated_clauses:
        analysis.violations.append(
            f"CONSTITUTIONAL_VIOLATION: Article {clause_violation.article_number} - "
            f"{clause_violation.title}"
        )
        
        # Adjust anomaly score based on severity
        if clause_violation.severity == ClauseSeverity.CRITICAL:
            analysis.anomaly_score = min(analysis.anomaly_score + 0.4, 1.0)
        elif clause_violation.severity == ClauseSeverity.HIGH:
            analysis.anomaly_score = min(analysis.anomaly_score + 0.2, 1.0)
    
    # ... rest of decision logic ...


def _evaluate_constitution(
    agent_output: Dict[str, Any],
    constitution: List[ConstitutionalClause],
    agent_thought: str
) -> Tuple[List[ConstitutionalClause], Dict[str, Any]]:
    """
    LLM을 사용하여 에이전트 출력이 헌법 조항을 위반했는지 평가
    
    Returns:
        (위반된 조항 리스트, 마스킹된 출력)
    """
    # Build constitutional prompt
    clauses_text = "\n".join([
        f"Article {c.article_number}: {c.title}\n  - {c.description}"
        for c in constitution
    ])
    
    prompt = f"""You are a Constitutional Compliance Judge.

Constitution:
{clauses_text}

Agent Output:
{json.dumps(agent_output, indent=2)}

Agent's Internal Thought:
{agent_thought}

Your task: 
1. Determine which constitutional articles (if any) were violated.
2. If Article 6 (PII Text Leakage) is violated, extract all PII instances found.

Respond in JSON format:
{{
    "violated_articles": [1, 3],  // Article numbers
    "reasoning": "Article 1 violated because..., Article 3 violated because...",
    "pii_detected": {{  // Only if Article 6 violated
        "email": ["john.doe@example.com"],
        "phone": ["010-1234-5678"],
        "card": ["1234-5678-9012-3456"]
    }}
}}
"""
    
    try:
        response = call_llm(
            prompt=prompt,
            model="gemini-2.0-flash",
            max_tokens=500
        )
        
        result = json.loads(response)
        violated_article_numbers = result.get("violated_articles", [])
        pii_detected = result.get("pii_detected", {})
        
        violated_clauses = [
            clause for clause in constitution
            if clause.article_number in violated_article_numbers
        ]
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # [v3.0] Retroactive Masking for PII Text Leakage
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        masked_output = agent_output.copy()
        if 6 in violated_article_numbers and pii_detected:
            masked_output = _apply_retroactive_masking(
                output=agent_output,
                pii_map=pii_detected
            )
            logger.warning(
                f"[Constitutional] Article 6 violated - Applied retroactive PII masking. "
                f"Detected: {list(pii_detected.keys())}"
            )
        
        logger.info(
            f"[Constitutional] Evaluated {len(constitution)} articles, "
            f"found {len(violated_clauses)} violations"
        )
        
        return violated_clauses, masked_output
        
    except Exception as e:
        logger.error(f"[Constitutional] Evaluation failed: {e}")
        return [], agent_output


def _apply_retroactive_masking(
    output: Dict[str, Any],
    pii_map: Dict[str, List[str]]
) -> Dict[str, Any]:
    """
    텍스트 내 PII를 사후적으로 마스킹
    
    Args:
        output: 에이전트 출력
        pii_map: {"email": [...], "phone": [...], "card": [...]}
    
    Returns:
        마스킹된 출력
    """
    import re
    import hashlib
    
    masked = output.copy()
    
    # Maskable text fields
    text_fields = ["thought", "message", "response", "reasoning"]
    
    for field in text_fields:
        if field not in masked:
            continue
        
        text = masked[field]
        if not isinstance(text, str):
            continue
        
        # Mask emails
        for email in pii_map.get("email", []):
            email_hash = hashlib.sha256(email.encode()).hexdigest()[:8]
            text = text.replace(email, f"***EMAIL_{email_hash}***")
        
        # Mask phones
        for phone in pii_map.get("phone", []):
            phone_hash = hashlib.sha256(phone.encode()).hexdigest()[:8]
            text = text.replace(phone, f"***PHONE_{phone_hash}***")
        
        # Mask card numbers
        for card in pii_map.get("card", []):
            card_hash = hashlib.sha256(card.encode()).hexdigest()[:8]
            text = text.replace(card, f"***CARD_{card_hash}***")
        
        masked[field] = text
    
    return masked
```

---

## 📝 Phase 3: Governance Rationale Generator (Priority: P0)

### 3.1 Triage Strategy: 계층적 설명 생성

**핵심 원칙**: 모든 위반에 동일한 비용을 지불하지 않는다.

#### Tier 1: 결정론적 위반 (Zero-Cost)

**대상**:
- 시간 제한 위반 ("오후 6시 이후 실행 금지")
- 예산 초과 ("$50 한도 초과")
- 호출 횟수 제한 ("API 10회 초과 호출")
- PII 필드 접근 ("Ring 3 에이전트가 customer.ssn 필드 접근")

**처리**: f-string 템플릿으로 즉시 생성 (LLM 미사용)

**File**: `src/services/governance/rationale_templates.py` (NEW)

```python
"""
결정론적 위반 설명 템플릿 (Zero-Cost)
"""

RATIONALE_TEMPLATES = {
    "TIME_BASED_VIOLATION": (
        "워크플로우가 허용 시간({allowed_hours}) 외에 실행되어 차단되었습니다. "
        "현재 시각: {current_time} {timezone}"
    ),
    "BUDGET_EXCEEDED": (
        "에이전트 {agent_id}가 예산 한도(${max_budget:.2f})를 "
        "${exceeded_amount:.2f} 초과하여 실행이 중단되었습니다. "
        "(누적 비용: ${total_cost:.2f})"
    ),
    "RETRY_QUOTA_EXCEEDED": (
        "노드 {node_id}가 허용된 재시도 횟수({max_retries}회)를 초과했습니다. "
        "(현재: {current_retries}회)"
    ),
    "PII_ACCESS_VIOLATION": (
        "Ring {ring_level} 에이전트가 금지된 PII 필드({field_path})에 "
        "접근을 시도하여 차단되었습니다."
    ),
    "PERMISSION_DOWNGRADE": (
        "Child 워크플로우의 Ring 레벨이 Parent보다 높아 "
        "강제로 하향 조정되었습니다. (Parent: Ring {parent_ring} → Child: Ring {child_ring})"
    )
}

def generate_tier1_rationale(violation_type: str, context: Dict[str, Any]) -> str:
    """
    Tier 1: f-string 템플릿 기반 설명 생성 (즉시, 무료)
    
    Args:
        violation_type: RATIONALE_TEMPLATES의 키
        context: 템플릿 치환용 변수 딕셔너리
    
    Returns:
        즉시 생성된 설명 문자열
    """
    template = RATIONALE_TEMPLATES.get(
        violation_type,
        "정책 위반이 감지되어 실행이 차단되었습니다."
    )
    
    try:
        return template.format(**context)
    except KeyError as e:
        logger.error(f"[Tier1] Missing template variable: {e}")
        return f"정책 '{violation_type}' 위반이 감지되었습니다."
```

#### Tier 2: 해석적 위반 (Low-Cost SLM)

**대상**:
- AI 헌법 위반 ("무례한 어조", "보안 우회 의도")
- Plan Drift (의도 이탈)
- SLOP (출력 과다)
- Circuit Breaker (연속 실패)

**처리**: 사용자가 "상세 설명 생성" 옵션을 활성화한 경우에만 SLM 호출

**File**: `src/handlers/governance/rationale_generator_lambda.py` (NEW)

```python
"""
비동기 Rationale 생성 Lambda
SQS 트리거로 실행되며 메인 워크플로우와 분리됨
"""
import boto3
import json
from typing import Dict, Any
from datetime import datetime

bedrock_runtime = boto3.client('bedrock-runtime', region_name='us-east-1')
manifest_table = boto3.resource('dynamodb').Table('WorkflowManifestsV3')

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    SQS 큐에서 위반 이벤트를 받아 Rationale 생성
    
    Event Structure:
    {
        "violation_type": "CONSTITUTIONAL_VIOLATION",
        "tier": 2,
        "manifest_id": "mf_abc123",
        "agent_id": "Manus-v2",
        "context": {...}
    }
    """
    processed_count = 0
    
    for record in event.get('Records', []):
        try:
            violation_event = json.loads(record['body'])
            
            # Triage: Tier 1 또는 Tier 2
            if violation_event.get('tier') == 1:
                rationale = generate_tier1_rationale(
                    violation_type=violation_event['violation_type'],
                    context=violation_event['context']
                )
            else:
                # Tier 2: Bedrock Haiku 사용
                rationale = _generate_tier2_rationale_bedrock(
                    violation_event=violation_event
                )
            
            # Merkle Manifest 메타데이터 비동기 업데이트
            _patch_manifest_rationale(
                manifest_id=violation_event['manifest_id'],
                rationale=rationale
            )
            
            processed_count += 1
            
        except Exception as e:
            logger.error(f"[RationaleLambda] Processing failed: {e}")
            # DLQ로 자동 전송 (SQS 설정)
    
    return {
        'statusCode': 200,
        'body': json.dumps({'processed': processed_count})
    }


def _generate_tier2_rationale_bedrock(violation_event: Dict[str, Any]) -> str:
    """
    Tier 2: Bedrock Haiku를 사용한 해석적 설명 생성
    
    Cost: ~$0.00025 per request (Haiku: $0.25/MTok input, $1.25/MTok output)
    Latency: ~200-400ms
    """
    context = violation_event['context']
    
    prompt = f"""다음 거버넌스 위반을 1-2문장의 한국어로 설명하세요.

에이전트: {context.get('agent_id', 'unknown')}
위반 유형: {violation_event['violation_type']}
이상 점수: {context.get('anomaly_score', 0):.2f}
위반 사항: {', '.join(context.get('violations', [])[:3])}

설명:"""
    
    try:
        response = bedrock_runtime.invoke_model(
            modelId='anthropic.claude-3-haiku-20240307-v1:0',
            contentType='application/json',
            accept='application/json',
            body=json.dumps({
                'anthropic_version': 'bedrock-2023-05-31',
                'max_tokens': 150,
                'temperature': 0.3,
                'messages': [
                    {
                        'role': 'user',
                        'content': prompt
                    }
                ]
            })
        )
        
        response_body = json.loads(response['body'].read())
        rationale = response_body['content'][0]['text'].strip()
        
        logger.info(f"[Tier2] Bedrock Haiku generated: {rationale[:100]}...")
        return rationale
        
    except Exception as e:
        logger.error(f"[Tier2] Bedrock call failed: {e}")
        # Fallback to template
        return f"에이전트 {context.get('agent_id')}의 {violation_event['violation_type']} 위반이 감지되었습니다."


def _patch_manifest_rationale(manifest_id: str, rationale: str) -> None:
    """
    Merkle Manifest 메타데이터에 Rationale 비동기 업데이트
    
    주의: 해시 체인에 영향을 주지 않도록 별도 속성에 저장
    """
    try:
        manifest_table.update_item(
            Key={'manifest_id': manifest_id},
            UpdateExpression="SET governance.rationale = :r, governance.rationale_generated_at = :t",
            ExpressionAttributeValues={
                ':r': rationale,
                ':t': datetime.utcnow().isoformat() + 'Z'
            }
        )
        logger.info(f"[Patch] Manifest {manifest_id} rationale updated")
        
    except Exception as e:
        logger.error(f"[Patch] Failed to update manifest {manifest_id}: {e}")
```

### 3.2 비동기 이벤트 파이프라인 (Event-Driven Architecture)

**핵심 설계**: Rationale 생성을 메인 워크플로우에서 분리하여 실행 속도 보존

#### Step 1: Violation 감지 시 즉시 보호 조치

**File**: `src/handlers/governance/governor_runner.py` (MODIFY)

```python
def governor_node_runner(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    v3.0: 위반 감지 시 즉시 보호 + 비동기 이벤트 발행
    """
    # ... existing analysis and decision logic ...
    
    if decision.decision in ["REJECTED", "ROLLBACK", "ESCALATED"]:
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 1: 즉시 보호 조치 (시스템 안전성 최우선)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        kernel_commands = [
            {
                "command": "_kernel_rollback" if decision.decision == "ROLLBACK" else "_kernel_halt",
                "reason": f"Governance violation: {', '.join(analysis.violations[:2])}",
                "target_manifest_id": last_safe_manifest.get("manifest_id") if decision.decision == "ROLLBACK" else None
            }
        ]
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 2: 비동기 이벤트 발행 (설명 생성용)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        _publish_violation_event(
            manifest_id=state.get("manifest_id"),
            violation_type=_determine_violation_tier(analysis),
            context={
                "agent_id": analysis.agent_id,
                "decision": decision.decision,
                "anomaly_score": analysis.anomaly_score,
                "violations": analysis.violations,
                "violated_clauses": [c.clause_id for c in violated_clauses],
                "output_size_bytes": analysis.output_size_bytes,
                "retry_count": analysis.retry_count
            },
            enable_rationale=config.get("enable_detailed_rationale", False)  # 사용자 설정
        )
        
        return {
            "_kernel_commands": kernel_commands,
            "governance_decision": decision.decision,
            "anomaly_score": analysis.anomaly_score,
            # rationale은 나중에 비동기로 생성됨
        }


def _determine_violation_tier(analysis: AgentBehaviorAnalysis) -> Dict[str, Any]:
    """
    위반 유형을 Tier 1 또는 Tier 2로 분류
    
    Returns:
        {"tier": 1 or 2, "violation_type": str}
    """
    # Tier 1: 결정론적 위반
    tier1_violations = [
        "BUDGET_EXCEEDED",
        "TIME_BASED_VIOLATION",
        "RETRY_QUOTA_EXCEEDED",
        "PII_ACCESS_VIOLATION"
    ]
    
    for violation in analysis.violations:
        if any(t1 in violation for t1 in tier1_violations):
            return {"tier": 1, "violation_type": violation.split(":")[0]}
    
    # Tier 2: 해석적 위반 (헌법, SLOP, Plan Drift 등)
    return {"tier": 2, "violation_type": "CONSTITUTIONAL_VIOLATION"}


def _publish_violation_event(
    manifest_id: str,
    violation_type: Dict[str, Any],
    context: Dict[str, Any],
    enable_rationale: bool
) -> None:
    """
    SNS로 위반 이벤트 발행 → SQS → Rationale Lambda
    
    주의: 이 함수는 즉시 반환되며 워크플로우를 블록하지 않음
    """
    if not enable_rationale:
        logger.info("[Event] Rationale generation disabled by user config")
        return
    
    import boto3
    sns_client = boto3.client('sns')
    
    event_payload = {
        "manifest_id": manifest_id,
        "tier": violation_type["tier"],
        "violation_type": violation_type["violation_type"],
        "context": context,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    
    try:
        sns_client.publish(
            TopicArn=os.environ['GOVERNANCE_VIOLATION_TOPIC_ARN'],
            Message=json.dumps(event_payload),
            MessageAttributes={
                'tier': {'DataType': 'Number', 'StringValue': str(violation_type["tier"])}
            }
        )
        logger.info(f"[Event] Published violation event for manifest {manifest_id}")
        
    except Exception as e:
        logger.error(f"[Event] Failed to publish violation event: {e}")
        # 이벤트 발행 실패해도 메인 워크플로우는 계속 진행
```

#### Step 2: Infrastructure (SAM Template)

**File**: `backend/template.yaml` (ADD)

```yaml
  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  # Governance Rationale 비동기 생성 인프라
  # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  
  GovernanceViolationTopic:
    Type: AWS::SNS::Topic
    Properties:
      TopicName: AnalemmaGovernanceViolations
      DisplayName: Governance Violation Events
  
  GovernanceViolationQueue:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: AnalemmaGovernanceViolationQueue
      VisibilityTimeout: 300  # 5분 (Lambda 실행 시간)
      MessageRetentionPeriod: 1209600  # 14일
      RedrivePolicy:
        deadLetterTargetArn: !GetAtt GovernanceViolationDLQ.Arn
        maxReceiveCount: 3
  
  GovernanceViolationDLQ:
    Type: AWS::SQS::Queue
    Properties:
      QueueName: AnalemmaGovernanceViolationDLQ
      MessageRetentionPeriod: 1209600
  
  GovernanceViolationQueueSubscription:
    Type: AWS::SNS::Subscription
    Properties:
      Protocol: sqs
      TopicArn: !Ref GovernanceViolationTopic
      Endpoint: !GetAtt GovernanceViolationQueue.Arn
  
  RationaleGeneratorLambda:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: AnalemmaRationaleGenerator
      Runtime: python3.11
      Handler: rationale_generator_lambda.lambda_handler
      CodeUri: src/handlers/governance/
      Timeout: 120
      MemorySize: 512
      Environment:
        Variables:
          MANIFESTS_TABLE_NAME: !Ref WorkflowManifestsV3
          BEDROCK_REGION: us-east-1
      Events:
        SQSEvent:
          Type: SQS
          Properties:
            Queue: !GetAtt GovernanceViolationQueue.Arn
            BatchSize: 10
            MaximumBatchingWindowInSeconds: 5
      Policies:
        - DynamoDBCrudPolicy:
            TableName: !Ref WorkflowManifestsV3
        - Statement:
            - Effect: Allow
              Action:
                - bedrock:InvokeModel
              Resource:
                - arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-haiku-20240307-v1:0
```

---

## 📈 Phase 4: Trust Score Tracking (Priority: P1)

### 4.1 Trust Score Algorithm

**File**: `src/services/governance/trust_score_manager.py` (NEW)

```python
@dataclass
class TrustScoreState:
    """에이전트 신뢰도 상태"""
    agent_id: str
    current_score: float  # 0.0 ~ 1.0
    score_history: List[Tuple[str, float]]  # (manifest_id, score)
    violation_count: int
    success_count: int
    last_updated: str

class TrustScoreManager:
    """
    에이전트 신뢰도 관리 (v3.0 Enhanced with EMA)
    
    Algorithm (Asymmetric Recovery with EMA):
    
    수학적 모델:
    $$T_{new} = \max(0, \min(1, T_{old} + \Delta S - (\alpha \cdot A)))$$
    
    여기서:
    - T_old: 이전 신뢰도
    - ΔS: 성공 증분 (가변, EMA 기반)
    - A: Anomaly Score (0.0-1.0)
    - α: 위반 승수 (0.5)
    
    비대칭적 수렴 문제 해결:
    - 기존: 고정 +0.01 → 40번 성공 필요 (0.4 → 0.8 복구)
    - 개선: 지수 이동 평균(EMA) → 최근 성공 가중치 상승
    
    EMA Formula:
    $$\Delta S = \Delta S_{base} \cdot (1 + \beta \cdot \text{streak\_ratio})$$
    
    여기서:
    - streak_ratio = recent_successes / total_recent
    - β = 2.0 (가속 계수)
    
    Example:
    - 5번 연속 성공 시: ΔS = 0.01 * (1 + 2.0 * 1.0) = 0.03
    - 복구 시간: 40번 → 14번으로 65% 단축
    """
    
    INITIAL_SCORE = 0.8
    BASE_SUCCESS_INCREMENT = 0.01
    VIOLATION_MULTIPLIER = 0.5
    STRICT_MODE_THRESHOLD = 0.4
    EMA_ACCELERATION = 2.0
    RECENT_WINDOW = 10  # 최근 10건 기준
    
    def __init__(self):
        self.agent_scores: Dict[str, TrustScoreState] = {}
    
    def update_score(
        self,
        agent_id: str,
        manifest_id: str,
        governance_result: GovernanceDecision
    ) -> float:
        """
        거버넌스 결정 기반으로 신뢰도 업데이트
        
        Returns:
            새로운 trust_score
        """
        # Get or create trust state
        if agent_id not in self.agent_scores:
            self.agent_scores[agent_id] = TrustScoreState(
                agent_id=agent_id,
                current_score=self.INITIAL_SCORE,
                score_history=[],
                violation_count=0,
                success_count=0,
                last_updated=datetime.utcnow().isoformat() + "Z"
            )
        
        trust_state = self.agent_scores[agent_id]
        old_score = trust_state.current_score
        
        # Update based on decision
        if governance_result.decision == "APPROVED":
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [v3.0 Enhanced] EMA-based Asymmetric Recovery
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Calculate success streak ratio
            recent_decisions = trust_state.score_history[-self.RECENT_WINDOW:] if len(trust_state.score_history) >= self.RECENT_WINDOW else trust_state.score_history
            
            if recent_decisions:
                # Count recent successes (score increased or stable)
                recent_successes = sum(
                    1 for i in range(1, len(recent_decisions))
                    if recent_decisions[i][1] >= recent_decisions[i-1][1]
                )
                streak_ratio = recent_successes / max(len(recent_decisions) - 1, 1)
            else:
                streak_ratio = 0.0
            
            # Accelerated recovery for consistent success
            delta_s = self.BASE_SUCCESS_INCREMENT * (1 + self.EMA_ACCELERATION * streak_ratio)
            new_score = min(old_score + delta_s, 1.0)
            trust_state.success_count += 1
            
            logger.info(
                f"[TrustScore EMA] {agent_id}: streak_ratio={streak_ratio:.2f}, "
                f"delta_s={delta_s:.4f} (base={self.BASE_SUCCESS_INCREMENT})"
            )
            
        elif governance_result.decision in ["REJECTED", "ESCALATED", "ROLLBACK"]:
            # Violation detected (asymmetric penalty)
            anomaly_score = governance_result.audit_log.get("anomaly_score", 0.5)
            penalty = anomaly_score * self.VIOLATION_MULTIPLIER
            new_score = max(old_score - penalty, 0.0)
            trust_state.violation_count += 1
        
        else:
            new_score = old_score
        
        # Update state
        trust_state.current_score = new_score
        trust_state.score_history.append((manifest_id, new_score))
        trust_state.last_updated = datetime.utcnow().isoformat() + "Z"
        
        # Keep only last 20 scores
        if len(trust_state.score_history) > 20:
            trust_state.score_history = trust_state.score_history[-20:]
        
        logger.info(
            f"[TrustScore] {agent_id}: {old_score:.3f} → {new_score:.3f} "
            f"(Decision: {governance_result.decision})"
        )
        
        return new_score
    
    def get_governance_mode(self, agent_id: str) -> GovernanceMode:
        """
        신뢰도 기반으로 거버넌스 모드 결정
        
        Returns:
            STRICT (신뢰도 낮음) 또는 OPTIMISTIC (신뢰도 높음)
        """
        if agent_id not in self.agent_scores:
            return GovernanceMode.OPTIMISTIC  # Default
        
        score = self.agent_scores[agent_id].current_score
        
        if score < self.STRICT_MODE_THRESHOLD:
            logger.warning(
                f"[TrustScore] {agent_id} trust score too low ({score:.2f} < {self.STRICT_MODE_THRESHOLD}). "
                f"Forcing STRICT mode."
            )
            return GovernanceMode.STRICT
        
        return GovernanceMode.OPTIMISTIC
    
    def get_trend(self, agent_id: str) -> str:
        """
        신뢰도 추세 분석
        
        Returns:
            "IMPROVING" | "STABLE" | "DEGRADING"
        """
        if agent_id not in self.agent_scores:
            return "STABLE"
        
        history = self.agent_scores[agent_id].score_history
        if len(history) < 3:
            return "STABLE"
        
        recent_5 = [score for _, score in history[-5:]]
        avg_recent = sum(recent_5) / len(recent_5)
        
        older_5 = [score for _, score in history[-10:-5]] if len(history) >= 10 else recent_5
        avg_older = sum(older_5) / len(older_5)
        
        diff = avg_recent - avg_older
        
        if diff > 0.05:
            return "IMPROVING"
        elif diff < -0.05:
            return "DEGRADING"
        else:
            return "STABLE"
```

### 4.2 Integration with Governor

**File**: `src/handlers/governance/governor_runner.py` (MODIFY)

```python
# Global trust score manager
trust_manager = TrustScoreManager()

def governor_node_runner(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    v3.0: Trust Score Integration
    """
    agent_id = state.get("agent_id", "unknown_agent")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [v3.0] Determine Governance Mode based on Trust Score
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    governance_mode = trust_manager.get_governance_mode(agent_id)
    
    if governance_mode == GovernanceMode.STRICT:
        logger.warning(
            f"[Governor] Agent {agent_id} trust score below threshold. "
            f"Enforcing STRICT mode (synchronous validation)."
        )
    
    # ... existing analysis ...
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [v3.0] Update Trust Score
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    manifest_id = state.get("manifest_id", "unknown")
    new_trust_score = trust_manager.update_score(
        agent_id=agent_id,
        manifest_id=manifest_id,
        governance_result=decision
    )
    
    # Add to audit log
    decision.audit_log["trust_score"] = new_trust_score
    decision.audit_log["trust_trend"] = trust_manager.get_trend(agent_id)
    
    # ... rest of code ...
```

---

## 🎨 Phase 5: Frontend Dashboard Integration (Priority: P1)

### 5.1 Real-time Metrics (WebSocket/AppSync)

**File**: `backend/src/handlers/websocket/governance_stream.py` (NEW)

```python
"""
실시간 거버넌스 메트릭 스트리밍
"""

def publish_governance_event(event_type: str, payload: Dict[str, Any]):
    """
    AppSync GraphQL Subscription으로 이벤트 발행
    
    Subscription Types:
    1. onGovernanceDecision
    2. onAnomalyDetected
    3. onTrustScoreUpdated
    """
    import boto3
    
    appsync_client = boto3.client('appsync')
    
    mutation = """
    mutation PublishGovernanceEvent($input: GovernanceEventInput!) {
        publishGovernanceEvent(input: $input) {
            eventId
            timestamp
        }
    }
    """
    
    variables = {
        "input": {
            "eventType": event_type,
            "payload": json.dumps(payload),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    }
    
    # Send to AppSync
    # ... (implementation depends on AppSync setup)
```

### 5.2 Frontend Components

**File**: `frontend/apps/web/src/components/GovernanceDashboard.tsx` (NEW)

```typescript
/**
 * Governance Dashboard - 3대 핵심 뷰
 * 
 * 1. Merkle Lineage View: Hash Chain 시각화
 * 2. Live Anomaly Radar: 실시간 위험 요소
 * 3. Rollback Command Center: 즉시 롤백 UI
 */

interface GovernanceMetrics {
  currentManifestId: string;
  manifestHash: string;
  parentHash: string;
  trustScore: number;
  anomalyScore: number;
  liveViolations: string[];
  governanceMode: 'OPTIMISTIC' | 'STRICT';
}

export function GovernanceDashboard() {
  const [metrics, setMetrics] = useState<GovernanceMetrics>();
  
  // WebSocket subscription
  useEffect(() => {
    const subscription = subscribeToGovernance((event) => {
      setMetrics(event.payload);
    });
    
    return () => subscription.unsubscribe();
  }, []);
  
  return (
    <div className="governance-dashboard">
      {/* 1. Merkle Lineage View */}
      <MerkleLineageGraph 
        currentManifest={metrics?.currentManifestId}
        manifestHash={metrics?.manifestHash}
        parentHash={metrics?.parentHash}
      />
      
      {/* 2. Live Anomaly Radar */}
      <AnomalyRadar
        anomalyScore={metrics?.anomalyScore}
        trustScore={metrics?.trustScore}
        liveViolations={metrics?.liveViolations}
      />
      
      {/* 3. Rollback Command Center */}
      {metrics?.anomalyScore > 0.5 && (
        <RollbackButton
          targetManifest="last_safe"
          currentScore={metrics.anomalyScore}
        />
      )}
    </div>
  );
}
```

---

## 🤖 Model Evolution Roadmap

### Phase 1: Bedrock Haiku (2026 Q1-Q2)

**선택 이유**:
- ✅ **Zero Infra**: Lambda에서 즉시 사용 가능 (서버리스)
- ✅ **가성비**: $0.25/MTok input, $1.25/MTok output (GPT-4 대비 90% 저렴)
- ✅ **저지연**: 200-400ms (Rationale 생성에 충분)
- ✅ **관리형**: 모델 업데이트, 스케일링 자동화

**Cost Estimate**:
```
평균 Rationale 생성:
- Input: 500 tokens (위반 컨텍스트)
- Output: 100 tokens (1-2문장 설명)

Cost per request:
= (500 * $0.25 / 1M) + (100 * $1.25 / 1M)
= $0.000125 + $0.000125
= $0.00025 (약 0.3원)

월 10,000건 위반 발생 시:
= 10,000 * $0.00025 = $2.50/월
```

**Integration**:
```python
# Bedrock Runtime SDK
response = bedrock_runtime.invoke_model(
    modelId='anthropic.claude-3-haiku-20240307-v1:0',
    body=json.dumps({
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 150,
        'temperature': 0.3
    })
)
```

---

### Phase 2: 오픈소스 SLM (2026 Q3-Q4)

**목표**: 비용을 추가 90% 절감 + 데이터 프라이버시 강화

**후보 모델**:

#### Option 1: Mistral 7B Instruct
- **장점**: 요약 태스크 성능 우수, 한국어 지원 양호
- **인프라**: SageMaker Serverless Inference (Auto-scaling)
- **비용**: $0.20/시간 (Idle 시 $0) + 추론당 $0.000003

#### Option 2: Llama 3.2 3B
- **장점**: 경량화, 초저지연 (100ms)
- **인프라**: Lambda (컨테이너 이미지 10GB)
- **비용**: Lambda 실행 비용만 (~$0.00001/request)

#### Option 3: Gemma 2 2B
- **장점**: Google 지원, 한국어 성능 최상
- **인프라**: Fargate Spot (99% 저렴)
- **비용**: ~$0.05/일 (상시 대기)

**마이그레이션 전략**:
```python
class RationaleGenerator:
    def __init__(self):
        self.provider = os.environ.get('RATIONALE_PROVIDER', 'bedrock')  # bedrock | sagemaker | lambda
    
    def generate(self, prompt: str) -> str:
        if self.provider == 'bedrock':
            return self._call_bedrock_haiku(prompt)
        elif self.provider == 'sagemaker':
            return self._call_sagemaker_endpoint(prompt)
        elif self.provider == 'lambda':
            return self._call_lambda_slm(prompt)
```

**Decision Criteria** (2026 Q3):
- 월 위반 건수 > 50,000건 → 오픈소스 SLM 전환
- 규제 요구사항 (데이터 격리) → 즉시 전환
- Bedrock 가격 인상 → 전환 검토

---

### Phase 3: Fine-tuned Domain Model (2027+)

**목표**: Analemma 특화 Rationale 생성 모델

**Training Data**:
- 누적된 위반 사례 100,000+ 건
- 사람이 작성한 Rationale 예시 (Quality Gate)

**Base Model**: Llama 3.2 3B

**Fine-tuning**:
```python
# LoRA (Low-Rank Adaptation) - 파라미터 0.1%만 학습
from peft import LoraConfig, get_peft_model

config = LoraConfig(
    r=8,  # Rank
    lora_alpha=16,
    target_modules=["q_proj", "v_proj"],
    lora_dropout=0.05
)

model = get_peft_model(base_model, config)
# 학습 비용: ~$50 (A100 GPU 4시간)
```

**Expected Performance**:
- Latency: 50ms (Haiku 대비 4배 빠름)
- Accuracy: 95%+ (도메인 특화)
- Cost: $0.00001/request (Haiku 대비 25배 저렴)

---

## 📊 Implementation Timeline

### Phase 1: Stateless Policy Evaluator (Week 1-2)
- ✅ Day 1-3: `policy_evaluator.py` 구현
- ✅ Day 4-5: Time-based, Budget, PII 규칙 구현
- ✅ Day 6-7: SegmentRunner 통합
- ✅ Day 8-10: 테스트 및 벤치마크

### Phase 2: Constitutional Governor (Week 2-3)
- ✅ Day 11-13: `constitution.py` + 기본 헌법 정의
- ✅ Day 14-16: LLM 기반 위반 평가 구현
- ✅ Day 17-18: Governor Node 통합
- ✅ Day 19-21: 테스트 케이스 작성

### Phase 3: Rationale Generator (Week 3-4)
- ✅ Day 19-20: Tier 1 템플릿 시스템 구현 (`rationale_templates.py`)
- ✅ Day 21-22: Tier 2 Bedrock Haiku 통합
- ✅ Day 23-25: 비동기 파이프라인 구축 (SNS → SQS → Lambda)
- ✅ Day 26-27: Rationale Lambda 배포 및 테스트
- ✅ Day 28: Governor에서 이벤트 발행 통합

### Phase 4: Trust Score Tracking (Week 4)
- ✅ Day 22-24: `trust_score_manager.py` 구현
- ✅ Day 25-26: Governor 통합
- ✅ Day 27-28: DynamoDB 스키마 업데이트

### Phase 5: Frontend Dashboard (Week 5-6)
- ✅ Day 29-31: WebSocket/AppSync 이벤트 발행
- ✅ Day 32-35: Frontend 컴포넌트 개발
- ✅ Day 36-38: 통합 테스트
- ✅ Day 39-42: 프로덕션 배포 준비

---

## 🎯 Success Metrics

### Performance Targets

| Metric | Current (v2.1.1) | Target (v3.0) |
|--------|------------------|---------------|
| **Policy Evaluation Latency** | N/A | < 1ms (Pre-execution) |
| **Governor Latency** | 200-500ms | 200-500ms (unchanged) |
| **Rationale Generation Latency** | N/A | 0ms (Tier 1), 300ms (Tier 2 async) |
| **Cost per Governance Check** | ~$0.002 | ~$0.0002 (90% reduction) |
| **Rationale Cost** | N/A | $0 (Tier 1), $0.00025 (Tier 2) |
| **Tier 1 Coverage** | N/A | 60-70% of violations |
| **False Positive Rate** | Unknown | < 5% |
| **Explainability Score** | 70% | 95% (Rationale + Clauses) |
| **Async Processing SLA** | N/A | 95% within 5 seconds |

### Business Impact

- ✅ **비용 절감**: 단순 위반 90%를 LLM 없이 차단 → 연간 $10,000+ 절감
- ✅ **보안 강화**: 선제적 차단으로 위반 발생률 60% 감소
- ✅ **감사 용이성**: 모든 결정에 자연어 근거 제공 → 규제 대응 시간 80% 단축
- ✅ **운영 효율**: Trust Score 기반 자동 모드 전환 → 수동 개입 70% 감소

---

## 🚨 Risk Assessment

### High Risk

1. **Constitutional Evaluation 정확도**
   - Risk: LLM이 위반을 놓치거나 과도하게 탐지
   - Mitigation: 3개월 프롬프트 튜닝 + 평가 데이터셋 구축

2. **Trust Score 남용**
   - Risk: 에이전트가 의도적으로 점수를 올리려는 시도
   - Mitigation: Anomaly Score 계산 로직 암호화 + Ring 0 보호

### Medium Risk

3. **Policy Schema 복잡도**
   - Risk: 사용자가 정책을 잘못 정의
   - Mitigation: JSON Schema 검증 + 기본 템플릿 제공

4. **Frontend 실시간 동기화**
   - Risk: WebSocket 연결 끊김 시 데이터 불일치
   - Mitigation: 폴백 폴링 메커니즘 + 재연결 로직

5. **EMA Trust Score 조작**
   - Risk: 에이전트가 의도적으로 간단한 태스크만 수행하여 점수 부풀림
   - Mitigation: 태스크 복잡도 가중치 도입 + 무작위 감사 샘플링

6. **Retroactive Masking 누락**
   - Risk: LLM이 PII 패턴을 놓치거나 새로운 형식 미탐지
   - Mitigation: Regex 백업 패턴 + 주기적 프롬프트 개선 + 사람 검증 샘플링

---

## 📚 References

1. **Anthropic Constitutional AI**: https://www.anthropic.com/index/constitutional-ai-harmlessness-from-ai-feedback
2. **Analemma v2.1.1 Governance**: `docs/AGENT_GOVERNANCE_IMPLEMENTATION_PLAN.md`
3. **Merkle DAG Architecture**: `docs/MERKLE_DAG_REFACTORING.md`
4. **Ring Protection System**: `src/services/recovery/prompt_security_guard.py`

---

## ✅ Approval Checklist

- [ ] Architecture Review (Lead Architect)
- [ ] Security Audit (Security Team)
- [ ] Cost Analysis (FinOps)
- [ ] Frontend Feasibility (Frontend Team)
- [ ] Deployment Plan (DevOps)

**Expected Approval Date**: February 25, 2026  
**Deployment Target**: March 15, 2026 (v3.0 Release)

---

**End of Document**
