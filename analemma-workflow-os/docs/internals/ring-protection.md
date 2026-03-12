# Ring Protection Internals

**Analemma OS — 4-Ring Privilege Isolation for AI Agent Workflows**

This document describes the runtime mechanics of Analemma's ring protection system: how privilege levels are assigned, how boundaries are enforced at each stage of execution, how violations are detected and remediated, and how kernel and agent layers communicate through controlled interfaces.

---

## Table of Contents

1. [Design Principles](#1-design-principles)
2. [Ring Definitions](#2-ring-definitions)
3. [The Great Seal Protocol — Kernel ↔ Runtime Communication](#3-the-great-seal-protocol--kernel--runtime-communication)
4. [Pre-Execution: Prompt Security Guard and Semantic Shield](#4-pre-execution-prompt-security-guard-and-semantic-shield)
5. [During Execution: State Isolation and Output Validation](#5-during-execution-state-isolation-and-output-validation)
6. [Post-Execution: Governor Validation](#6-post-execution-governor-validation)
7. [Violation Detection and Response](#7-violation-detection-and-response)
8. [Capability Map — Default-Deny Tool Access](#8-capability-map--default-deny-tool-access)
9. [Constitutional AI Articles](#9-constitutional-ai-articles)
10. [End-to-End Execution Flow](#10-end-to-end-execution-flow)
11. [Configuration Reference](#11-configuration-reference)
12. [Source File Map](#12-source-file-map)

---

## 1. Design Principles

Analemma's ring model is built on three invariants:

**Invariant 1 — Agent output is untrusted data, never an instruction.**
Every value produced by an LLM-powered node (Ring 3) is treated as a data payload. It passes through output validation, key filtering, and governance checks before being merged into workflow state. An agent cannot issue commands to the kernel.

**Invariant 2 — All state mutations flow through one pipeline.**
Every Lambda function — regardless of purpose — exits through `seal_state_bag()`, which routes data through the Universal Sync Core (USC). There is no side-channel for state writes. This single chokepoint enables 256KB defense, S3 offloading, Merkle checkpointing, and control-plane isolation in one pass.

**Invariant 3 — Governance decisions produce immutable records.**
Every APPROVED / REJECTED / ROLLBACK decision is written to DynamoDB's GovernanceAuditLogV3 with a 90-day TTL. The Merkle chain makes retroactive tampering detectable without external signatures.

---

## 2. Ring Definitions

Rings are modeled after CPU protection rings (Intel x86). Lower numbers indicate higher privilege.

| Ring | Name | Trust Level | Who Operates Here | Examples |
|------|------|-------------|-------------------|----------|
| **0** | KERNEL | Absolute | Kernel protocol, USC, state versioning | `seal_state_bag()`, `open_state_bag()`, Merkle DAG 2PC |
| **1** | DRIVER / GOVERNOR | High | Governance engine, constitutional validators | `governor_runner.py`, `governance_engine.py` |
| **2** | SERVICE | Moderate | Verified internal tools, trusted API integrations | `api_call`, `db_query` nodes with validated configs |
| **3** | USER / AGENT | None | LLM-powered nodes, external tool calls | `llm_chat`, `vision`, `dynamic_router`, `skill_executor` |

**Source**: `constants.py` — `SecurityConfig.RING_0_KERNEL` through `RING_3_USER`

### Ring Assignment at Runtime

Ring level is determined by **node type**, not by configuration:

```
Ring 3 (Untrusted):  llm_chat, vision, dynamic_router, skill_executor, video_chunker
Ring 2 (Service):    api_call, db_query, safe_operator / operator_official
Ring 1 (Governor):   governor node (post-execution validation)
Ring 0 (Kernel):     kernel_protocol functions, USC, state_versioning_service
```

Ring level is set by the segment runner before node execution begins. It cannot be elevated by the node itself.

---

## 3. The Great Seal Protocol — Kernel ↔ Runtime Communication

The Great Seal Protocol (v3.13) defines the single I/O contract between Lambda functions and the ASL (Amazon States Language) state machine.

### 3.1 Entry: `open_state_bag(event)`

**File**: `kernel_protocol.py:120-163`

Extracts the actual state dictionary from the raw Step Functions event, regardless of nesting depth. Three extraction paths are tried in order:

```
Priority 1:  event["state_data"]["bag"]    ← Standard v3.13 path
Priority 2:  event["state_data"]           ← Flattened case
Priority 3:  event                         ← Direct invocation / legacy
```

This decouples Lambda code from ASL `ResultPath` configuration. Changing the ASL topology does not require Lambda code changes.

### 3.2 Exit: `seal_state_bag(base_state, result_delta, action)`

**File**: `kernel_protocol.py:55-117`

Every Lambda returns through this function. Internally:

1. **USC Pipeline**: Calls `universal_sync_core(base_state, new_result, context)` which executes:
   - `flatten_result()` — Action-specific extraction (unwrap execution wrappers, sort Map outputs)
   - `merge_logic()` — Copy-on-Write shallow merge with per-field strategies
   - `optimize_and_offload()` — Multi-layer 256KB defense (L1–L5 offloading)
   - `_compute_next_action()` — Routing decision (CONTINUE / COMPLETE / FAILED / PAUSED_FOR_HITP)

2. **Double-Wrapping Prevention**: Returns exactly two keys:
   ```json
   {
     "state_data": { "...merged, offloaded, optimized..." },
     "next_action": "CONTINUE"
   }
   ```

3. **ASL Contract**: SFN's Payload wrapper + ResultSelector maps this into:
   ```
   $.state_data.bag  ← state_data contents
   $.state_data.next_action  ← routing signal
   ```
   The next Lambda receives `$.state_data.bag` as its input.

### 3.3 Why This Matters for Ring Protection

The Great Seal is the **Ring 0 boundary**. No Lambda can bypass it:

- **Control-plane isolation**: `seal_state_bag` strips non-control-plane fields via USC dehydration. Agent-written data goes to S3 batch pointers; only kernel control fields survive in the top-level state.
- **256KB enforcement**: The USC's optimize_and_offload prevents any ring from exceeding the SFN payload limit. Fields >30KB are automatically S3-offloaded.
- **Routing sovereignty**: `_compute_next_action()` centralizes all routing decisions. Individual Lambdas never set `next_action` directly — the kernel computes it from state signals.

---

## 4. Pre-Execution: Prompt Security Guard and Semantic Shield

Before any LLM node executes, Ring 3 inputs are scanned for injection attempts and privilege escalation.

### 4.1 Semantic Shield — 3-Stage Normalization Pipeline

**File**: `semantic_shield.py`

```
┌─────────────────────────────────────────────┐
│  Stage 1: Text Normalization (All Rings)    │
│  ─────────────────────────────────────────  │
│  • Zero-Width Space removal                 │
│    (U+200B, U+200C, U+200D, U+FEFF, U+2060)│
│  • RTL Override removal                     │
│    (U+202E, U+202D, U+200F, U+200E)        │
│  • Base64 decode attempt                    │
│    (20+ char strings → extract payload)     │
│  • Homoglyph normalization                  │
│    (Cyrillic/Greek/Full-width → Latin)      │
├─────────────────────────────────────────────┤
│  Stage 2: Regex Pattern Matching            │
│  (on normalized text — bypass-resistant)    │
│  ─────────────────────────────────────────  │
│  • INJECTION_PATTERNS (10 English patterns) │
│  • Korean injection patterns (8 patterns)   │
│  • Ring 0 tag forgery detection             │
│    ([RING-0, [KERNEL], <RING_0>, etc.)      │
├─────────────────────────────────────────────┤
│  Stage 3: Semantic LLM Classification       │
│  (Ring 2/3 only — skipped for Ring 0/1)     │
│  ─────────────────────────────────────────  │
│  • Bedrock Guardrails or ShieldGemma        │
│  • Intent: BENIGN | INJECTION | JAILBREAK   │
│  • Graceful degradation: if unavailable,    │
│    Shield continues with Stage 1+2 only     │
└─────────────────────────────────────────────┘
```

**Cost optimization**: Stage 3 (LLM inference) is skipped for Ring 0/1 traffic, saving inference cost on trusted internal operations.

### 4.2 Prompt Security Guard — Validation Entry Point

**File**: `prompt_security_guard.py`

`validate_prompt(content, ring_level)` orchestrates the full scan:

1. Runs Semantic Shield normalization + pattern matching
2. For Ring 3: detects Ring 0 tag tampering (`[RING-0`, `[KERNEL]`, `<RING_0>`)
3. Evaluates severity: CRITICAL → `should_sigkill = True`
4. For MEDIUM/LOW: sanitizes content (strips detected injection patterns), allows execution to continue

**SIGKILL trigger condition**:
```python
if has_critical and self.enable_auto_sigkill:
    should_sigkill = True
    log_security_event("SIGKILL_TRIGGERED", severity="CRITICAL")
```

### 4.3 Tool Permission Check

`check_tool_permission(ring_level, tool_name)` enforces the capability map (see Section 8). Returns `(granted: bool, violation: Optional[SecurityViolation])`.

### 4.4 Integration with Segment Runner

**File**: `segment_runner_service.py:2728-2863`

`_apply_ring_protection()` is called before segment execution:
- Validates all LLM node prompts + system prompts
- Checks dangerous tool access for the segment's node types
- Returns a list of violations with `should_sigkill` flag

If `should_sigkill` is True, the segment runner skips execution entirely and returns a SIGKILL response.

---

## 5. During Execution: State Isolation and Output Validation

### 5.1 Hidden Context — Kernel Memory

**File**: `main.py:735-773`

Before each Ring 3 node executes, the kernel creates a hidden context in state:

```python
state["__hidden_context"] = {
    "original_state": {},           # Deep copy of protected keys
    "ring_violations": [],          # Violations detected during execution
    "protected_keys": set(RESERVED_STATE_KEYS)
}
```

`_snapshot_original_state()` deep-copies all `RESERVED_STATE_KEYS` values before the node runs. After execution, the kernel can compare the snapshot to detect unauthorized modifications.

**Ring 3 cannot access `__hidden_context`**: any attempt to read or write `__`-prefixed keys in output is flagged as `HIDDEN_CONTEXT_ACCESS`.

### 5.2 Reserved State Keys

**File**: `main.py:362-404`

36 keys are protected from Ring 3 modification:

| Category | Keys | Why Protected |
|----------|------|---------------|
| **System Context** | `workflowId`, `workflow_id`, `owner_id`, `execution_id`, `user_id`, `idempotency_key` | Identity spoofing prevention |
| **Flow Control** | `loop_counter`, `max_loop_iterations`, `segment_id`, `segment_to_run`, `total_segments`, `segment_type` | Prevent infinite loops, segment hijacking |
| **State Infrastructure** | `partition_map`, `__s3_offloaded`, `__s3_path`, `state_s3_path`, `final_state_s3_path` | S3 offloading integrity |
| **Telemetry** | `step_history`, `execution_logs`, `__new_history_logs`, `skill_execution_log`, `__kernel_actions` | Audit trail integrity |
| **Scheduling** | `scheduling_metadata`, `guardrail_verified`, `batch_count_actual` | Scheduler/guardrail bypass prevention |
| **Credentials** | `user_api_keys`, `aws_credentials` | Credential exposure prevention |
| **Response Envelope** | `status`, `error_info` | SFN JSONPath integrity |

### 5.3 Ring Protection Enforcement

**File**: `main.py:776-865`

`_enforce_ring_protection(state, output, node_id, ring_level)`:

```
Ring 0-2:  return output as-is (trusted)
Ring 3:    scan output for three violation types
```

**Three violation checks for Ring 3**:

| Check | Detection | Action |
|-------|-----------|--------|
| `PROTECTED_KEY_OUTPUT` | Any key from `RESERVED_STATE_KEYS` in output | Filter key from output |
| `HIDDEN_CONTEXT_ACCESS` | `__hidden_context` or `__original_state` in output | Filter key from output |
| `KERNEL_COMMAND_FORGERY` | Any `_kernel_*` prefixed key in output | Filter key + escalate to Governor |

If violations are detected, the kernel:
1. Logs every violation with node ID, ring level, and timestamp
2. Appends to `__hidden_context.ring_violations` for Governor review
3. Returns a **filtered output** with all forbidden keys removed
4. Execution continues with the sanitized output (non-SIGKILL path)

### 5.4 Kernel Command Forgery — Separate Guard

**File**: `main.py:972-1024`

`_validate_output_keys(output, node_id, ring_level)` provides a second layer of kernel command detection:

```python
KERNEL_CONTROL_KEYS = frozenset({
    "_kernel_skip_segments",
    "_kernel_skip_reason",
    "_kernel_inject_recovery",
    "_kernel_rollback_to_manifest",
    "_kernel_rollback_reason",
    "_kernel_rollback_type",
    "_kernel_modify_parallelism",
    "_kernel_request_human_approval"
})
```

If a Ring 3 node outputs any of these 8 keys, they are silently removed from the output and the attempt is logged as `KERNEL_COMMAND_FORGERY`. The Governor is notified for potential escalation.

**Why two guards?** `_enforce_ring_protection` catches broad category violations (any reserved key, hidden context, `_kernel_*` prefix). `_validate_output_keys` specifically targets the 8 kernel control commands with Governor notification. Defense in depth — if one check is bypassed, the other catches it.

---

## 6. Post-Execution: Governor Validation

### 6.1 Governor Runner — Ring 1 Gatekeeper

**File**: `governor_runner.py`

After each segment completes, the Governor (Ring 1) validates the agent's output.

**Governance Mode Selection** (ring-dependent):

| Ring | Mode | Behavior |
|------|------|----------|
| 0 — KERNEL | STRICT | Pre-validation + post-validation (synchronous) |
| 1 — DRIVER | STRICT | Synchronous validation |
| 2 — SERVICE | OPTIMISTIC | Async validation + rollback on failure |
| 3 — USER | OPTIMISTIC | Async validation + rollback on failure |

**STRICT mode**: Blocks execution until validation completes. Used for kernel/driver operations where a post-hoc rollback would be insufficient.

**OPTIMISTIC mode**: Allows execution to proceed, then validates asynchronously. If the anomaly score exceeds `OPTIMISTIC_ROLLBACK_THRESHOLD` (default 0.5), triggers rollback. This reduces latency for the common case (no violation) while still catching violations.

### 6.2 Anomaly Score Model

Each violation adds 0.2 to the anomaly score, capped at 1.0:

```
anomaly_score = min(len(violations) × 0.2, 1.0)

# Hard boosts (override base calculation):
KERNEL_COMMAND_FORGERY      → anomaly_score = 1.0   (immediate SIGKILL)
CONSTITUTIONAL_VIOLATION    → anomaly_score = max(score, 0.8)
```

**Decision thresholds**:
- `≥ 0.8` → **REJECTED** (HARD_ROLLBACK or TERMINAL_HALT)
- `≥ 0.5` → **ESCALATED** (SOFT_ROLLBACK or human review)
- `< 0.5` → **APPROVED** (proceed)

### 6.3 Governance Engine — Parallel Article Enforcement

**File**: `governance_engine.py`

All six constitutional articles are validated in parallel via `asyncio.gather()`:

```
asyncio.gather(
    Article1ToneValidator.validate(output),
    Article2HarmfulContentValidator.validate(output),
    Article3UserProtectionValidator.validate(output),
    Article4TransparencyValidator.validate(output),
    Article5SecurityPolicyValidator.validate(output),
    Article6PIILeakageValidator.validate(output),
)
```

Results are aggregated into a `GovernanceVerdict` with:
- `violations[]` — list of `ArticleViolation` objects
- `recommended_action` — APPROVE / WARN / SOFT_ROLLBACK / HARD_ROLLBACK / TERMINAL_HALT
- `low_violation_count` — 10+ LOW violations auto-escalate to MEDIUM

---

## 7. Violation Detection and Response

### 7.1 Severity Levels

| Severity | Response | Runtime Effect |
|----------|----------|----------------|
| **CRITICAL** | SIGKILL / TERMINAL_HALT | Workflow immediately terminates. State sealed with `status: "SIGKILL"`. |
| **HIGH** | HARD_ROLLBACK | Restore last safe manifest from DynamoDB. Orphaned blocks get 7-day GC grace period. |
| **MEDIUM** | SOFT_ROLLBACK + Sanitize | Retry current segment with agent feedback message. Content filtered before retry. |
| **LOW** | LOG_ONLY | Proceed unchanged. Logged for audit. |

### 7.2 SIGKILL Path

**File**: `segment_runner_service.py:4508-4532`

```
Critical Security Violation Detected
         │
         ▼
should_sigkill = True
         │
         ▼
_finalize_response({
    "status": "SIGKILL",
    "final_state": mask_pii_in_state(state),
    "error_info": {
        "error": "Security violation detected",
        "error_type": "RingProtectionViolation",
        "violations": [...]
    },
    "segment_type": "sigkill"
})
         │
         ▼
seal_state_bag() wraps into:
{
    "state_data": {...},
    "next_action": "FAILED"
}
         │
         ▼
ASL Choice State reads next_action == "FAILED"
         │
         ▼
Workflow → Fail state with full error details
```

### 7.3 HARD_ROLLBACK Path

**File**: `governor_runner.py:250-286`

```
Critical Violation (anomaly_score ≥ 0.8)
         │
         ▼
Governor issues kernel commands:
  _kernel_rollback_to_manifest = <last_safe_manifest_id>
  _kernel_rollback_type = "HARD_ROLLBACK"
         │
         ▼
Segment Runner detects _kernel_rollback_* commands
         │
         ▼
S3 GC Integration:
  mark_rollback_orphans(
      rollback_manifest_id = last_safe_manifest,
      abandoned_branch_root = current_manifest,
      grace_period_days = 7
  )
         │
         ▼
State restored to last_safe_manifest
         │
         ▼
Workflow resumes from safe checkpoint
```

### 7.4 SOFT_ROLLBACK Path

```
Medium Violation (0.5 ≤ anomaly_score < 0.8)
         │
         ▼
Governor injects feedback into state:
  _kernel_inject_recovery = "Previous output violated Article 2.
   Please revise without harmful content."
         │
         ▼
Segment retries with feedback message
prepended to the agent's prompt
```

### 7.5 Violation → Rollback Type Mapping

| Violation Type | Severity | Rollback | Mechanism |
|---------------|----------|----------|-----------|
| KERNEL_COMMAND_FORGERY | CRITICAL | TERMINAL_HALT | Immediate workflow SIGKILL |
| CONSTITUTIONAL_VIOLATION (Article 2, 3, 5, 6) | CRITICAL | HARD_ROLLBACK | Restore last `governance_decision=APPROVED` manifest |
| SLOP / CIRCUIT_BREAKER | HIGH | HARD_ROLLBACK | Same manifest restore |
| PLAN_DRIFT / GAS_FEE | MEDIUM | SOFT_ROLLBACK | Retry segment with agent feedback |
| INJECTION_ATTEMPT (non-critical) | MEDIUM | SOFT_ROLLBACK | Content sanitized + retry |
| TONE_VIOLATION | LOW | LOG_ONLY | Continue |
| TRANSPARENCY_VIOLATION | LOW | LOG_ONLY | Continue |

---

## 8. Capability Map — Default-Deny Tool Access

**File**: `prompt_security_guard.py:100-124`, `constants.py:249-266`

Every tool call from any agent is gated by `validate_capability(ring_level, tool_name)`. The policy is **Default-Deny**: any tool not explicitly listed for a ring returns `False`.

### Per-Ring Tool Access

| Ring | Allowed Tools |
|------|---------------|
| **0 — KERNEL** | `None` (sentinel — unrestricted access) |
| **1 — DRIVER** | `filesystem_read`, `filesystem_write`, `subprocess_call`, `network_limited`, `database_read`, `database_write`, `database_query`, `config_read`, `config_write`, `s3_read`, `cache_read`, `cache_write`, `event_publish` |
| **2 — SERVICE** | `network_read`, `database_query`, `database_read`, `cache_read`, `event_publish`, `s3_read`, `config_read` |
| **3 — USER** | `basic_query`, `read_only` |

### Dangerous vs Safe Tool Classification

| Category | Tools |
|----------|-------|
| **DANGEROUS** (Ring 3 blocked) | `s3_delete`, `s3_write`, `db_delete`, `db_write`, `db_update`, `execute_shell`, `run_command`, `exec`, `send_email`, `send_sms`, `payment_process`, `transfer_funds`, `delete_user`, `admin_action` |
| **SAFE** (Ring 3 allowed) | `s3_read`, `s3_get_object`, `s3_list`, `db_read`, `db_query`, `db_scan`, `api_get`, `http_get`, `llm_chat`, `llm_complete`, `log`, `print`, `format` |

---

## 9. Constitutional AI Articles

**File**: `constitution.py`, `governance_engine.py`

Six default articles are enforced at Ring 1. All run in parallel via `asyncio.gather()`.

| Article | Rule | Severity | Detection Method |
|---------|------|----------|-----------------|
| **1 — Tone** | Professional tone required | MEDIUM | Profanity keyword pattern matching |
| **2 — Harmful Content** | No harmful content generation | CRITICAL | `INJECTION_PATTERNS` + harmful keyword set |
| **3 — User Protection** | No PII solicitation | CRITICAL | PII solicitation regex (card numbers, passwords, SSN) |
| **4 — Transparency** | Acknowledge uncertainty | LOW | Over-certainty phrase detection |
| **5 — Security** | No security policy bypass | CRITICAL | Reuses `INJECTION_PATTERNS` set |
| **6 — PII Leakage** | No PII in output text | CRITICAL | `RetroactiveMaskingService.scan()` + email/phone/SSN regex |

**Escalation rule**: 10 or more LOW-severity violations in a single `verify()` call are automatically upgraded to MEDIUM.

**Custom articles**: Per-workflow custom clauses can be defined via `governance_policies.constitution[]` using article numbers > 6.

---

## 10. End-to-End Execution Flow

Complete lifecycle of a single segment execution, showing every ring boundary crossing:

```
┌─────────────────────────────────────────────────────────────────┐
│  Step Functions ASL                                             │
│  Delivers event to Lambda                                      │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  RING 0 — open_state_bag(event)                                 │
│  Extract actual state from ASL nesting                          │
│  Returns: bag (clean state dictionary)                          │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  RING 0 — _initialize_hidden_context(state)                     │
│  Create kernel-private memory for this execution                │
│                                                                  │
│  RING 0 — _snapshot_original_state(state, node_id)              │
│  Deep-copy all RESERVED_STATE_KEYS before node runs             │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  RING 1 — _apply_ring_protection()                              │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  Semantic Shield Stage 1: Normalize (zero-width, RTL,  │     │
│  │                           Base64, homoglyph)           │     │
│  │  Semantic Shield Stage 2: Regex pattern matching       │     │
│  │  Prompt Security Guard: Ring 0 tag tampering check     │     │
│  │  Tool Permission: validate_capability(ring, tools)     │     │
│  └────────────────────────────────────────────────────────┘     │
│                                                                  │
│  If should_sigkill → skip execution, return SIGKILL immediately │
│  If MEDIUM/LOW → sanitize content, allow execution              │
└──────────────────────┬───────────────────────────────────────────┘
                       │ (no SIGKILL)
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  RING 3 — Node Execution (LLM inference)                        │
│  Agent receives sanitized prompt + state context                │
│  Agent produces output dictionary                               │
│  ⚠ Agent has NO access to __hidden_context                      │
│  ⚠ Agent has NO access to RESERVED_STATE_KEYS                   │
│  ⚠ Agent CANNOT set _kernel_* commands                          │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  RING 0 — _validate_output_keys(output, node_id, ring_level=3) │
│  Remove any RESERVED_STATE_KEYS from output                     │
│  Detect & remove _kernel_* command forgery attempts             │
│                                                                  │
│  RING 0 — _enforce_ring_protection(state, output, node_id, 3)  │
│  Three checks: PROTECTED_KEY, HIDDEN_CONTEXT, KERNEL_FORGERY   │
│  Filter forbidden keys → return safe_output                     │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  RING 1 — Governor Validation (post-execution)                  │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  GovernanceEngine.verify(output)                       │     │
│  │  ├── Article 1: Tone         (MEDIUM)                  │     │
│  │  ├── Article 2: Harmful      (CRITICAL)  ─┐            │     │
│  │  ├── Article 3: PII Request  (CRITICAL)   │ parallel   │     │
│  │  ├── Article 4: Transparency (LOW)        │ asyncio    │     │
│  │  ├── Article 5: Security     (CRITICAL)   │ .gather()  │     │
│  │  └── Article 6: PII Leakage  (CRITICAL)  ─┘            │     │
│  │                                                        │     │
│  │  anomaly_score = min(violations × 0.2, 1.0)            │     │
│  │  KERNEL_FORGERY → score = 1.0                          │     │
│  │  CONSTITUTIONAL → score = max(score, 0.8)              │     │
│  └────────────────────────────────────────────────────────┘     │
│                                                                  │
│  Score ≥ 0.8 → REJECTED  → HARD_ROLLBACK / TERMINAL_HALT       │
│  Score ≥ 0.5 → ESCALATED → SOFT_ROLLBACK                       │
│  Score < 0.5 → APPROVED  → proceed                             │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  RING 0 — seal_state_bag(base_state, safe_output, action)       │
│  ┌────────────────────────────────────────────────────────┐     │
│  │  Universal Sync Core Pipeline:                         │     │
│  │  1. flatten_result()     — unwrap execution wrappers   │     │
│  │  2. merge_logic()        — CoW shallow merge           │     │
│  │  3. optimize_and_offload() — L1-L5 256KB defense       │     │
│  │  4. _compute_next_action() — routing decision          │     │
│  └────────────────────────────────────────────────────────┘     │
│                                                                  │
│  Returns: { "state_data": {...}, "next_action": "CONTINUE" }    │
│                                                                  │
│  Merkle DAG: 2PC commit (Phase 1: S3 pending → Phase 2: DDB)   │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  Step Functions ASL                                              │
│  ResultSelector maps state_data → $.state_data.bag              │
│  Choice state reads next_action for routing                     │
│  Next Lambda receives clean bag via open_state_bag()            │
└──────────────────────────────────────────────────────────────────┘
```

---

## 11. Configuration Reference

All configuration is via environment variables and `constants.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_RING_PROTECTION` | `true` | Master switch for ring enforcement |
| `ENABLE_AUTO_SIGKILL` | `true` | Auto-terminate on CRITICAL violations |
| `OPTIMISTIC_ROLLBACK_THRESHOLD` | `0.5` | Anomaly score threshold for rollback in OPTIMISTIC mode |

### Governance Mode by Ring

| Ring | Default Mode | Override |
|------|-------------|---------|
| 0 | STRICT | Not configurable |
| 1 | STRICT | Not configurable |
| 2 | OPTIMISTIC | Per-workflow `governance_policies` |
| 3 | OPTIMISTIC | Per-workflow `governance_policies` |

### Ring 0 Prefix Tags

| Tag | Purpose |
|-----|---------|
| `[RING-0:IMMUTABLE]` | Marks kernel-level system prompts. Ring 3 detection of this tag in agent output triggers RING_0_TAMPERING violation. |
| `[RING-3:UNTRUSTED]` | Marks agent-generated content in mixed prompts. Used by Governor to identify boundaries. |

---

## 12. Source File Map

| File | Ring | Responsibility |
|------|------|---------------|
| `common/kernel_protocol.py` | 0 | Great Seal Protocol — `open_state_bag()` / `seal_state_bag()` |
| `handlers/utils/universal_sync_core.py` | 0 | State merge pipeline — flatten, merge, offload, route |
| `services/state/state_versioning_service.py` | 0 | Merkle DAG 2PC — content-addressed blocks, manifest chain |
| `common/state_hydrator.py` | 0 | State dehydration/rehydration — control-plane isolation |
| `handlers/governance/governor_runner.py` | 1 | Post-execution governance — anomaly score, rollback decisions |
| `services/governance/governance_engine.py` | 1 | Constitutional article validation — parallel `asyncio.gather()` |
| `services/governance/constitution.py` | 1 | Article definitions — severity levels, clause text |
| `services/recovery/prompt_security_guard.py` | 1 | Pre-execution prompt scan — injection detection, capability check |
| `services/recovery/semantic_shield.py` | 1 | 3-stage normalization — zero-width, RTL, Base64, homoglyph, LLM |
| `handlers/core/main.py` | 0–3 | Node execution orchestrator — ring assignment, state snapshot, output validation |
| `services/execution/segment_runner_service.py` | 0–3 | Segment lifecycle — ring protection integration, SIGKILL path |
| `common/constants.py` | — | Ring definitions, KERNEL_CONTROL_KEYS, capability maps, injection patterns |

---

*"Ring 3 agents produce data. Ring 0 kernel produces state transitions. The boundary between the two is the entire security model."*
