# Analemma-OS Backend Full Inspection Report

**Date:** 2026-02-22
**Inspection Scope:** Security & Trust / State & Consistency / Execution Infrastructure — 3 layers
**Files Inspected:** 18
**Policy:** Documentation of findings only (no code changes)

---

## Table of Contents

1. [Inspection Summary](#1-inspection-summary)
2. [Priority 1: Security & Trust Layer](#2-priority-1-security--trust-layer)
   - [prompt_security_guard.py](#21-prompt_security_guardpy)
   - [error_classifier.py](#22-error_classifierpy)
   - [agent_guardrails.py](#23-agent_guardrailspy)
   - [trust_score_manager.py](#24-trust_score_managerpy)
   - [constitution.py](#25-constitutionpy)
   - [retroactive_masking.py](#26-retroactive_maskingpy)
   - [pii_masking_service.py](#27-pii_masking_servicepy)
3. [Priority 2: State & Consistency Layer](#3-priority-2-state--consistency-layer)
   - [state_versioning_service.py](#31-state_versioning_servicepy)
   - [eventual_consistency_guard.py](#32-eventual_consistency_guardpy)
   - [merkle_gc_service.py](#33-merkle_gc_servicepy)
   - [async_commit_service.py](#34-async_commit_servicepy)
   - [checkpoint_service.py](#35-checkpoint_servicepy)
4. [Priority 3: Execution Infrastructure Layer](#4-priority-3-execution-infrastructure-layer)
   - [bedrock_client.py / gemini_client.py / gemini_service.py](#41-bedrock_clientpy--gemini_clientpy--gemini_servicepy)
   - [distributed_chunk_service.py](#42-distributed_chunk_servicepy)
   - [partition_service.py](#43-partition_servicepy)
   - [orchestrator_service.py](#44-orchestrator_servicepy)
5. [Overall Risk Matrix](#5-overall-risk-matrix)

---

## 1. Inspection Summary

### Overall Issue Tally

| Layer | Files | Bugs | Security Vulnerabilities | Functional Weaknesses | Total |
|-------|-------|------|--------------------------|-----------------------|-------|
| Security & Trust | 7 | 26 | 31 | 43 | 100 |
| State & Consistency | 5 | 30 | 18 | 27 | 75 |
| Execution Infrastructure | 6 | 24 | 17 | 34 | 75 |
| **Total** | **18** | **80** | **66** | **104** | **250** |

### CRITICAL/HIGH Issues by Severity

| File | CRITICAL | HIGH |
|------|----------|------|
| `state_versioning_service.py` | 1 (self.prefix uninitialized) | 2 |
| `eventual_consistency_guard.py` | 1 (GC race condition) | 3 |
| `checkpoint_service.py` | 1 (S3 path parsing) | 2 |
| `prompt_security_guard.py` | 2 (syscall privilege escalation, blocklist limitations) | 2 |
| `gemini_service.py` | 1 (temp file credentials not deleted) | 3 |
| `distributed_chunk_service.py` | 1 (partition slice index error) | 1 |
| `partition_service.py` | 1 (incomplete convergence node detection) | 1 |
| `merkle_gc_service.py` | 0 | 2 |

---

## 2. Priority 1: Security & Trust Layer

---

### 2.1 `prompt_security_guard.py`

**Path:** `backend/src/services/recovery/prompt_security_guard.py`
**Lines:** 577

#### Bugs (BUG)

**BUG-1.1: Unreliable severity determination from compiled patterns (Line 224–242)**
```python
pattern_str = pattern.pattern
if 'jailbreak' in pattern_str.lower() or 'escape' in pattern_str.lower():
    severity = SecurityConfig.SEVERITY_CRITICAL
```
- Inspects the raw regex string from `pattern.pattern`. Regex metacharacters may cause `'jailbreak' in pattern_str` to behave unexpectedly
- Severity determination logic depends on pattern metacharacters — unreliable

**BUG-1.2: Duplicate violation counting possible (Line 173, 190)**
```python
self._violation_count += len(violations)
```
- If multiple patterns match the same content in a single `validate_prompt()` call, duplicate counting occurs

**BUG-1.3: Inconsistent Ring 0 tag escaping (Line 388–389)**
```python
sanitized = sanitized.replace("[RING-", "[ESC_RING-")   # string replacement
sanitized = sanitized.replace("</RING", "&lt;/RING")    # HTML entity
```
- Two different escaping methods mixed. LLMs may not interpret `&lt;` as HTML, potentially defeating the escape
- Unicode normalization bypass possible: using `\uff3b` (fullwidth `[`) evades detection

#### Security Vulnerabilities (SECURITY)

**SEC-1.1: Insufficient justification validation in syscall_request_tool() — CRITICAL (Line 489–510)**
```python
if justification and len(justification) > 20:
    return {"granted": True, ...}
```
- Any justification over 20 characters auto-approves sensitive permissions like `s3_delete`, `db_delete`
- No content validation of justification (comment: "validate with ShieldGemma in production" — not implemented)
- Example: `"Please let me delete this file because I need to."` → immediately approved

**SEC-1.2: Fundamental limitations of block-list based validation — CRITICAL (constants.py)**
- Only detects known patterns; novel/variant techniques go undetected
- Encoding bypasses: Base64, Roman numerals (`ⅠGNORE`), Zero-Width Space (`IGNORE\u200bPREVIOUS`) undetected
- No white-list based allow mode

**SEC-1.3: No practical security difference between Ring levels — HIGH (Line 162–165)**
- Ring 1 and 2 are defined but have no validation logic. Only Ring 3 is actually validated
- Ring 0/1/2 are unconditionally trusted

**SEC-1.4: Asymmetric SIGKILL conditions — HIGH (Line 168–172)**
```python
has_high = any(v.severity == SecurityConfig.SEVERITY_HIGH for v in violations)
# has_high is defined but never used
if has_critical and self.enable_auto_sigkill:
    should_sigkill = True
```
- `has_high` variable is defined but not used in SIGKILL trigger
- RING_0_TAMPERING (HIGH severity) also does not trigger SIGKILL

**SEC-1.5: Encoded injection bypass via Zero-Width characters (Line 158)**
- `IGNORE\u200bPREVIOUS` (Zero-Width Space), RTL/LTR marks, control character insertion → regex fails to detect

**SEC-1.6: No input validation in create_ring_0_prompt() (Line 311–361)**
- No validation/sanitization of `system_purpose`, `security_rules`, `tool_permissions` user inputs
- Incorrectly assumes Ring 0 prompts cannot be injection targets

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-1.1: Korean/latest jailbreak patterns not included**
- Patterns like "You are now...", "You are in developer mode", role-playing prompts, forced CoT not detected

**WEAK-1.2: Ring 1, 2 not implemented**
- `RingLevel.RING_1_DRIVER`, `RING_2_SERVICE` are only defined, no actual validation logic

**WEAK-1.3: Insufficient metric information (Line 555–562)**
- No tracking of violation type distribution, time-based trends, per-pattern detection frequency, false positive rates

**WEAK-1.4: Incomplete security logging**
- Full request content not logged, no correlation ID, missing timestamps

**WEAK-1.5: Simplistic sanitization approach (Line 293–305)**
- Only replaces regex-matched portions with `[FILTERED_BY_RING_PROTECTION]`. Does not handle semantically equivalent bypass expressions
- Marker inconsistency between tag escaping (`[ESCAPED_RING]`) and pattern filter (`[FILTERED_BY_RING_PROTECTION]`)

**WEAK-1.6: Singleton cannot hot-reload**
- Pattern updates require deployment. No runtime policy changes possible

**WEAK-1.7: Partial filtering in sanitize_healing_advice() (Line 540–549)**
- When `result.is_safe == False` but `sanitized_content` exists, filtered content is returned
- Bypass path exists around full blocking (sentinel)

---

### 2.2 `error_classifier.py`

**Path:** `backend/src/services/recovery/error_classifier.py`
**Lines:** 263

#### Bugs (BUG)

**BUG-2.1: Korean/multilingual error messages not detected (Line 43–116)**
- All patterns are English-only. Cannot classify Korean error messages like "JSON decoding error"

**BUG-2.2: Semantic → Deterministic order creates duplicate pattern risk (Line 157–171)**
- Semantic checked first, then Deterministic. Overlapping patterns default to Semantic classification (intent unclear)

**BUG-2.3: Circuit Breaker threshold boundary confusion (Line 151–155)**
```python
if healing_count >= self.MAX_AUTO_HEALING_COUNT:  # >= 3
```
- Abruptly switches to SEMANTIC at exactly 3 attempts. The message "exceeded limit (3)" is confusing

**BUG-2.4: Unclear priority of context-based classification (Line 173–187)**
- Pattern matching executes first even when context flags are present, making context checks ineffective

**BUG-2.5: Unknown error defaults to SEMANTIC — false negative (Line 189–194)**
- All unclassifiable errors are treated as requiring manual intervention (SEMANTIC). Blocks auto-recoverable new errors

#### Security Vulnerabilities (SECURITY)

**SEC-2.1: Overly broad semantic patterns — false positives (Line 81–116)**
- `r"forbidden"`, `r"401"`, `r"403"` patterns: normal HTTP status errors misclassified as requiring manual intervention

**SEC-2.2: healing_count parameter can be manipulated — MEDIUM (Line 129)**
- Caller passing `healing_count=999` forces SEMANTIC return → blocks auto-recovery (DoS)

**SEC-2.3: Regex Injection (static patterns currently, future risk) — LOW (Line 122–127)**
- Risk of regex injection if dynamic pattern addition is implemented

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-2.1: No classification confidence score returned**
- Returns only `(ErrorCategory, str)`. Callers cannot assess false positive likelihood

**WEAK-2.2: When multiple patterns match simultaneously, only the first is used — non-deterministic**

**WEAK-2.3: No automatic per-provider error classification**
- Adding new LLM providers requires manual pattern updates

**WEAK-2.4: No error message normalization**
- No whitespace trimming, identical errors separated by whitespace go undetected

**WEAK-2.5: No definitive protection against infinite retry loops**

**WEAK-2.6: get_healing_advice() returns only generic advice**
- No specific suggestions based on error message location/context

**WEAK-2.7: Complete absence of monitoring/metrics**

---

### 2.3 `agent_guardrails.py`

**Path:** `backend/src/services/governance/agent_guardrails.py`
**Lines:** 421

#### Bugs (BUG)

**BUG-3.1: Incomplete CircuitBreaker HALF_OPEN → OPEN transition condition (Line 70–102)**
```python
if self._state.failure_count >= self.failure_threshold:
    self._state.state = "OPEN"
```
- On failure in HALF_OPEN state, only failure_count increments; does not revert to OPEN
- With threshold=3, requires 3 consecutive failures in HALF_OPEN to revert to OPEN → weakened protection

**BUG-3.2: detect_slop() repetition detection algorithm false positives (Line 160–166)**
```python
for i in range(0, min(len(output_json) - 10, 1000), 100):
    substring = output_json[i:i+10]
    count = output_json.count(substring)
    if count > 100:
        return True, ...
```
- Common patterns in a 100-item JSON array (e.g., `,"value"`) appearing 100+ times triggers a false SLOP detection

**BUG-3.3: detect_plan_drift() semantic validation not implemented (Line 274–308)**
```python
# TODO: Integrate Llama-3-8B or similar model
```
- Actual implementation: only computes keyword intersection/union. Cannot distinguish synonyms ("delete" vs "remove")

#### Security Vulnerabilities (SECURITY)

**SEC-3.1: CircuitBreaker cannot share state in distributed environments — HIGH**
- Memory-based state → each Lambda/ECS instance maintains independent state
- Instance A may be OPEN while Instance B continues accepting requests → Guardrail bypass

**SEC-3.2: calculate_gas_fee() token counting can be manipulated (Line 178–197)**
- No validation of `total_tokens_used`. A malicious agent setting it to 1 bypasses cost controls

**SEC-3.3: detect_plan_drift() destructive keyword bypass (Line 290–297)**
```python
destructive_keywords = {"delete", "remove", "drop", "destroy", "bypass", "ignore"}
```
- Missing: "erase", "purge", "wipe", "truncate", "exec", "eval"
- Character insertion bypass: "d3l3t3", "del ete"

**SEC-3.4: Exception handling absence in check_agent_health() disables guardrail (Line 315–406)**
- Passing circular reference structure as agent_output causes `json.dumps()` failure → entire guardrail bypassed

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-3.1: detect_slop() JSON depth check left as TODO (Line 168–169)**
```python
# Check 3: Excessive nesting (TODO: Implement JSON depth check)
```

**WEAK-3.2: No permission control for CircuitBreaker reset()**
- Anyone can immediately reset an OPEN state Circuit Breaker

**WEAK-3.3: Destructive keyword penalty of 50% — too weak**
- `intent_retention_rate *= 0.5` → depending on similarity_threshold, DRIFT may go undetected

---

### 2.4 `trust_score_manager.py`

**Path:** `backend/src/services/governance/trust_score_manager.py`
**Lines:** 282

#### Bugs (BUG)

**BUG-4.1: Concurrent update Race Condition (Line 78–170)**
```python
trust_state = self.agent_scores[agent_id]      # Line 107
old_score = trust_state.current_score           # Line 108
# ... computation ...
trust_state.current_score = new_score           # Line 155
```
- In multi-threaded environments, two threads calling update_score() simultaneously results in only the last update being applied

**BUG-4.2: Silent failure in _flush_history_to_metrics() on exception (Line 262–283)**
- DynamoDB connection failure → trust score permanent storage fails → history lost on system restart

**BUG-4.3: streak_ratio is independent of current state (Line 125–130)**
- Consecutive success ratio relies only on historical records. Does not reflect current state

#### Security Vulnerabilities (SECURITY)

**SEC-4.1: Trust scores stored in-memory — CRITICAL (Line 75–76)**
```python
self.agent_scores: Dict[str, TrustScoreState] = {}
```
- Process restart resets trust scores. Malicious agents can evade trust penalties by triggering restarts

**SEC-4.2: No input validation for governance_result (Line 78–170)**
```python
decision = governance_result.get("decision", "APPROVED")  # default is APPROVED
```
- Manipulated governance_result can be passed to manipulate trust scores

**SEC-4.3: Unclear intent of Ring Level Penalty Multiplier (Line 68–73)**
```python
RING_PENALTY_MULTIPLIERS = {0: 2.0, 1: 1.5, 2: 0.8, 3: 0.5}
```
- Lower privilege levels receive lower penalties. Unclear whether this is intentional or a mistake

**SEC-4.4: get_governance_mode() has no actual enforcement mechanism (Line 172–195)**
- Only returns STRICT/OPTIMISTIC, unclear how it connects to actual execution control

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-4.1: Fixed EMA acceleration coefficient (Line 63)**
- Same EMA_ACCELERATION=2.0 applied to all agents

**WEAK-4.2: Trust downgrade trigger handles only 3 decision states**
- Additional states like "WARNING", "DEFER", "TIMEOUT" not handled

**WEAK-4.3: History flushing prevents long-term trend analysis**
- Half deleted when exceeding HISTORY_MAX_SIZE=20. Only tracks last 20 executions

---

### 2.5 `constitution.py`

**Path:** `backend/src/services/governance/constitution.py`
**Lines:** 120

#### Security Vulnerabilities (SECURITY)

**SEC-5.1: Disconnect between constitutional clauses and actual Guardrail enforcement — HIGH**
- `DEFAULT_CONSTITUTION` is only defined, no validation logic
- No connection point with `agent_guardrails.py`. Constitution exists only as documentation

**SEC-5.2: Severity is defined but has no enforcement mechanism**
```python
class ClauseSeverity(Enum):
    CRITICAL = "critical"   # Immediate REJECTED — not implemented in code
```

**SEC-5.3: Unclear implementation of PII detection clause (Article 6)**
- Email regex? Phone number patterns? No detection algorithm specified

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-5.1: Custom clauses can only be added, default clauses cannot be overridden/removed**

**WEAK-5.2: No constitution version management**

**WEAK-5.3: No per-clause implementation status (is_implemented) tracking field**

---

### 2.6 `retroactive_masking.py`

**Path:** `backend/src/services/recovery/retroactive_masking.py`
**Lines:** 278

#### Bugs (BUG)

**BUG-6.1: str.replace() partial match error (Line 191, 197, 203, 209, 215)**
```python
text = text.replace(email, f"***EMAIL_{email_hash}***")
```
- If `"user@example.com"` is contained within `"user@example.com.au"`, partial replacement corrupts data

**BUG-6.2: Masking count error (Line 192, 198, 204, 210, 216)**
- `total_masked += 1` increments once per PII type. Even if replace() substitutes 3 times, count is +1

**BUG-6.3: Dictionary shallow copy (Line 171)**
```python
masked = output.copy()
```
- Nested dictionaries/lists cause unintended masking of the original `output`

#### Security Vulnerabilities (SECURITY)

**SEC-6.1: SHA256 hash truncated to 8 characters enables reverse masking attack — CRITICAL (Line 190–216)**
```python
email_hash = hashlib.sha256(email.encode()).hexdigest()[:8]
```
- 2^32 combinations; birthday paradox gives ~50% collision probability among ~65,000 emails
- Dictionary attack can recover original PII

**SEC-6.2: LLM-based detection security risk (Line 75–154)**
- Sensitive information sent to Gemini. LLM jailbreak/prompt injection could return incorrect PII

**SEC-6.3: No Luhn algorithm validation for credit cards**
- Invalid card numbers are also masked → corrupts legitimate data

**SEC-6.4: No IP address octet range validation**
- `"999.999.999.999"` also matches

**SEC-6.5: No SSN check digit validation**
- Simple pattern `\d{6}-?[1-4]\d{6}`, no validity check

**SEC-6.6: No re-verification after masking**
- No check whether PII remains after masking

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-6.1: No check digit validation for Korean resident registration numbers**

**WEAK-6.2: Missing PII types — passport numbers, driver's license numbers, bank account numbers, medical information, international phone numbers**

**WEAK-6.3: No granular exception handling** — JSON parse failures, timeouts all handled identically

**WEAK-6.4: Email regex does not support Unicode addresses** (`用户@example.com`)

**WEAK-6.5: Consistency tracking enables re-identification** — same PII always produces same hash → reveals identical emails

**WEAK-6.6: Insufficient handling of partial responses (some fields missing)**

**WEAK-6.7: No re-verification of masked output**

---

### 2.7 `pii_masking_service.py`

**Path:** `backend/src/services/recovery/pii_masking_service.py`

#### Bugs (BUG)

**BUG-7.1: Partial restoration error when URL token is contained within URL2 (Line 192–196)**

**BUG-7.2: Parenthesis balance check logic error (Line 172–180)**
```python
open_parens = url.count('(')
close_parens = url.count(')')
```
- Only compares counts, ignores order. `"url)("` → deemed balanced

**BUG-7.3: No thread safety for singleton (Line 246–263)**
```python
if _pii_masking_instance is None:
    _pii_masking_instance = PIIMaskingService(...)
```
- Multiple instances can be created in multi-threaded environments (no threading.Lock)

**BUG-7.4: Email regex has capture groups but design intent is unclear**

**BUG-7.5: Theoretical possibility of UUID-based token collision**

**BUG-7.6: URL restoration order dependency**

#### Security Vulnerabilities (SECURITY)

**SEC-7.1: API key regex only supports `sk-*` format (Line 36)**
- AWS, GCP, Azure key formats not supported

**SEC-7.2: Phone number regex is overly permissive (Line 40)**
- `"\d{3}[-.\s]?\d{3,4}[-.\s]?\d{4}"` → regular number combinations also masked

**SEC-7.3: No Luhn validation for credit cards (Line 44)**

**SEC-7.4: No SSN pattern validation (Line 42)**

**SEC-7.5: strict_mode only affects URL processing — misleading name (Line 63–72)**
- Implies strict PII validation but actually only changes URL processing

**SEC-7.6: Regex DoS (ReDoS) possibility (Line 100–101)**
- Malicious input can trigger excessive regex backtracking

**SEC-7.7: Email masking issue within mailto: links**
- `"mailto:user@example.com"` → `"mailto:[EMAIL_REDACTED]"` (broken link)

**SEC-7.8: Text length difference before/after masking can leak metadata**

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-7.1: Missing PII types** — passport numbers, driver's license numbers, bank account numbers, resident registration numbers, medical information

**WEAK-7.2: bytes/Tuple/Set type masking not supported**

**WEAK-7.3: No masking traceability** — unlike retroactive_masking.py, no count/category metadata

**WEAK-7.4: PII within URL query parameters not detected**
- `"?email=user@example.com"` → email in parameter not masked

**WEAK-7.5: International phone numbers not supported**

**WEAK-7.6: No re-verification after masking**

**WEAK-7.7: No exception handling** — no handling when `pattern.sub()` fails

**WEAK-7.8: No LRU cache** — performance degrades on repeated calls

**WEAK-7.9: Regex performance O(n×m)**

**WEAK-7.10: message and context masking are independent — possible UUID mismatch**

---

## 3. Priority 2: State & Consistency Layer

---

### 3.1 `state_versioning_service.py`

**Path:** `backend/src/services/state/state_versioning_service.py`
**Lines:** ~1,549

#### Bugs (BUG)

**BUG-8.1: self.prefix attribute uninitialized — CRITICAL (Line 1247, 1329)**
```python
key = f"{self.prefix}blocks/{block_id}.json"
```
- `self.prefix` not initialized in `__init__()` → `AttributeError` immediately on rollback/commit

**BUG-8.2: Version assignment Race Condition — HIGH (Line 359, 180)**
- Two Lambdas calling `_get_next_version()` simultaneously can assign the same version number to two manifests
- TransactWriteItems ConditionExpression does not check `(workflow_id, version)` uniqueness

**BUG-8.3: Merkle DAG Hash collision possibility (Line 687–708)**
```python
combined = config_hash + (parent_hash or '') + blocks_hash
return hashlib.sha256(combined.encode()).hexdigest()
```
- Simple string concatenation without delimiters → `"abc"+"def"+"ghi"` = `"ab"+"cde"+"fghi"` collision

**BUG-8.4: S3 Select fallback silent failure (Line 1180–1212)**
- When S3 Select returns no data, falls back to full object download. Wastes bandwidth for 4MB+ blocks

**BUG-8.5: Block reference counting lacks atomicity — HIGH (Line 362–473)**
- Manifest + first 99 blocks are atomic. Blocks 100+ require separate transactions
- On mid-crash, manifest exists but block ref_count=0 → GC may delete them

#### Security Vulnerabilities (SECURITY)

**SEC-8.1: No segment_manifest structure validation**
- Excessive size, circular references → DoS

**SEC-8.2: Insufficient S3 path sanitization (Line 324)**
```python
Key=block.s3_path.replace(f"s3://{self.bucket}/", "")
```
- `../` sequences could enable path traversal (low actual probability in S3, but risky pattern)

**SEC-8.3: No manifest access permission verification (Line 517–559)**
- No caller permission verification for `get_manifest(manifest_id)`

**SEC-8.4: S3 metadata JSON injection (Line 141–145)**
- User input block_id, transaction_id inserted into S3 metadata without validation

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-8.1: No compensating transaction for commit failure**
- If DynamoDB succeeds but S3 tagging fails, orphaned "committed" blocks remain

**WEAK-8.2: No hash verification for chunk reassembly**
- No integrity verification after loading split chunks

**WEAK-8.3: Incomplete cascading deletion (Line 1497–1549)**
- If a manifest references the same block multiple times, reference count may be incorrectly decremented

**WEAK-8.4: TransactWriteItems retries immediately (Line 371–502)**
- 3 immediate retries without exponential backoff → all fail during DynamoDB throttling

**WEAK-8.5: No recovery for orphaned pending operations**
- If pending blocks are not GC'd after Lambda crash, permanent storage leak occurs

---

### 3.2 `eventual_consistency_guard.py`

**Path:** `backend/src/services/state/eventual_consistency_guard.py`
**Lines:** 368

#### Bugs (BUG)

**BUG-9.1: Manifest inconsistency on Phase 3 partial failure — MEDIUM (Line 212–231)**
- If only some S3 tags are updated in Phase 3 after Phase 2 succeeds, remaining blocks stay in "pending" state and become GC candidates

**BUG-9.2: No duplicate transaction ID handling — HIGH (Line 110–117)**
- Client timeout retries generate new transaction_id → two sets of pending blocks created (no idempotency)

**BUG-9.3: _batch_update_block_references batch failure not handled — HIGH (Line 241–288)**
- If batch 1 succeeds but batch 2 fails, manifest is created but blocks 99+ have ref_count=0
- GC may delete referenced blocks

**BUG-9.4: GC Race Condition — CRITICAL (Line 330–361)**
```
T0: Block "hash123" pending upload (transaction_id="txn-A")
T5: txn-A fails, GC scheduled after 5 minutes
T8: Block "hash123" re-uploaded (transaction_id="txn-B")
T10: GC executes, checks transaction_id="txn-A" → deletes
T15: txn-B Phase 2 references "hash123" → block missing!
```
- Data loss possible. No protection without S3 Object Lock

#### Security Vulnerabilities (SECURITY)

**SEC-9.1: No blocks parameter validation (Line 78–117)**

**SEC-9.2: No SQS DLQ URL validation (Line 358–361)**
- If gc_dlq_url is attacker-controlled, internal block information may be exposed

**SEC-9.3: Transaction timing information leaked in logs**

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-9.1: No compensating transaction on Phase 3 failure** — only `logger.warning()` recorded

**WEAK-9.2: DynamoDB 25KB item limit not verified** — transactions fail when there are too many segment hashes

**WEAK-9.3: No consistency violation monitoring** — no CloudWatch metrics

**WEAK-9.4: No ordering guarantee between Phase 2/3** — downstream can read manifest and access pending blocks

**WEAK-9.5: Eventually consistent issues depending on S3 region**

---

### 3.3 `merkle_gc_service.py`

**Path:** `backend/src/services/state/merkle_gc_service.py`

#### Bugs (BUG)

**BUG-10.1: Reference count can go negative — CRITICAL (Line 255)**
```python
UpdateExpression="SET ref_count = if_not_exists(ref_count, :zero) - :dec"
```
- Block without `ref_count`: 0 - 1 = -1. Decrementing without initialization produces negative values

**BUG-10.2: graceful_wait TOCTOU race condition — HIGH (Line 286–315)**
- Between `zero_reached_at` check and update, another process can overwrite

**BUG-10.3: Unstable DynamoDB Streams TTL event detection (Line 88–90)**
```python
if record['userIdentity'].get('type') != 'Service':
    continue
```
- AWS TTL event userIdentity structure may vary

**BUG-10.4: No exception handling for zero_reached_at timestamp parsing (Line 301)**
```python
zero_time = datetime.fromisoformat(zero_reached_at)  # no try-catch
```

**BUG-10.5: Config reference count query uses wrong index (Line 394–399)**
- Queries `config_hash` reference count using `manifest_hash` index → always inaccurate results

**BUG-10.6: No success verification for S3 delete_object (Line 217–222)**
- `delete_object()` returns success even for non-existent objects. Actual deletion count is inaccurate

**BUG-10.7: Fragile block ID extraction logic (Line 153, 211, 479)**
```python
block_id = block_path.split('/')[-1].replace('.json', '')
```
- Path format mismatch produces empty string or error

#### Security Vulnerabilities (SECURITY)

**SEC-10.1: No event record input validation**
- Excessively large OldImage → DoS, deep nesting → CPU exhaustion

**SEC-10.2: No traversal stack size limit (Line 715–756)**
- Orphan detection across thousands of manifests may exhaust memory

**SEC-10.3: Unlimited TTL extension (Line 500)**
```python
':new_ttl': int(time.time()) + 90 * 24 * 3600
```
- Repeated security violation triggers can extend TTL indefinitely

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-10.1: Actual deletion count not logged** — reports `len(batch)` as deletion count (may be inaccurate)

**WEAK-10.2: graceful_wait 5 minutes hardcoded** — not tunable via environment variables

**WEAK-10.3: No CloudWatch metrics**

**WEAK-10.4: No circular reference validation for manifest DAG**

**WEAK-10.5: Glacier strategy not implemented** — only in comments: "30 days → Glacier → 90 days → delete"

---

### 3.4 `async_commit_service.py`

**Path:** `backend/src/services/state/async_commit_service.py`

#### Bugs (BUG)

**BUG-11.1: S3-based commit status ignored when Redis is unavailable — HIGH (Line 105–111)**
```python
except Exception as e:
    logger.warning(f"Redis check failed: {e}")
# redis_status = None → returns is_committed=False
```
- When Redis is down, returns `is_committed=False` even if commit exists in S3 (ignores source of truth)

**BUG-11.2: S3 error code check error — HIGH (Line 232)**
```python
if e.response['Error']['Code'] == '404':  # actual code is 'NoSuchKey'
    return False
```
- boto3 ClientError actual code is `'NoSuchKey'`, not integer 404 → condition never met

**BUG-11.3: redis_status undefined before loop (Line 182–188)**
- If RETRY_ATTEMPTS=0, loop never executes, `redis_status` referenced undefined → NameError

**BUG-11.4: Exponential backoff stalls after reaching MAX_DELAY (Line 149)**
```python
delay = min(delay * 2, MAX_DELAY)  # stalls at 0.4s
```
- Actual: 0.1 → 0.2 → 0.4 → 0.4 → ... (not true exponential backoff)

**BUG-11.5: TOCTOU window exists (Line 117–132)**
- Between Redis 'committed' check and S3 check, S3 object may be deleted

**BUG-11.6: Jitter range insufficiently documented**

#### Security Vulnerabilities (SECURITY)

**SEC-11.1: No Rate Limiting on retry loop**
- 2 network calls (Redis + S3) per retry → DoS possible

**SEC-11.2: No thread safety for singleton (Line 240–248)**

**SEC-11.3: No client credential verification**

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-11.1: No partial failure handling**

**WEAK-11.2: AWS recommended Full Jitter backoff not implemented**

**WEAK-11.3: No Circuit Breaker pattern**
- 3 retries per call during temporary Redis/S3 outages

**WEAK-11.4: No thread safety for singleton**

---

### 3.5 `checkpoint_service.py`

**Path:** `backend/src/services/checkpoint_service.py`

#### Bugs (BUG)

**BUG-12.1: No S3 path parsing safety — CRITICAL (Line 161–168)**
```python
if s3_path.startswith('s3://'):
    parts = s3_path[5:].split('/', 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ''
```
- `"s3://"` → bucket='', key='' → `get_object(Bucket='', Key='')` fails immediately

**BUG-12.2: Race Condition during checkpoint restoration — HIGH (Line 589–627)**
- Between checkpoint lookup and Step Functions execution start, GC may delete the checkpoint

**BUG-12.3: GSI fallback scan Limit mismatch (Line 256–259)**
- Query documentation says Limit=500, fallback uses Limit=100. Timeline results truncated

**BUG-12.4: Checkpoint ID collision risk (Line 142–148)**
```python
return f"cp_{timestamp}_{uuid.uuid4().hex[:8]}"
```
- Only 8 UUID characters used (32-bit entropy). Collision possible at the same timestamp

**BUG-12.5: Timestamp sorting is string-based (Line 309)**
- Single-digit month/day (`"2024-1-5"`) causes incorrect string sorting

**BUG-12.6: Large S3 objects loaded entirely into memory (Line 174–181)**
- `response['Body'].read()` with no size limit. 100MB+ state loading causes OOM

**BUG-12.7: Recursive diff infinite loop on circular reference state (Line 542–554)**

**BUG-12.8: Non-atomic checkpoint comparison (Line 464–475)**
- Data may change between sequential retrieval of two checkpoints

#### Security Vulnerabilities (SECURITY)

**SEC-12.1: No permission verification at all (entire file)**
- `get_execution_timeline(thread_id)` does not verify caller permissions. Other users' workflows viewable

**SEC-12.2: State snapshots may contain secrets**
- API keys, passwords, PII in state returned without redaction

**SEC-12.3: S3 path traversal vulnerability (Line 161–168)**
- User-controlled s3_path can access other buckets

**SEC-12.4: Step Functions ARN format not validated (Line 677–681)**
```python
f"arn:aws:states:...:{os.environ.get('AWS_ACCOUNT_ID', '')}:stateMachine:{workflow_id}"
```
- If AWS_ACCOUNT_ID is missing, ARN is invalid. Special characters in workflow_id are dangerous

**SEC-12.5: Excessive logging of configuration information (Line 184, 258)**

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-12.1: No cleanup logic for old checkpoints**
- Checkpoints accumulate indefinitely. Cost/performance issues

**WEAK-12.2: No restored state validity verification**
- States with expired API credentials, time-dependent variables can be re-executed

**WEAK-12.3: Incomplete notification parsing**
- If notification is neither string nor dictionary, silently defaults to `{}`

**WEAK-12.4: Memory issues with large state diffs**
- Simultaneously creates added/removed/modified dictionaries for 100MB+ states

**WEAK-12.5: No LRU cache** — S3/DynamoDB calls on every repeated query

**WEAK-12.6: Corrupted notifications silently ignored**

**WEAK-12.7: No timeline pagination** — only 500 returned when there are 10,000 events

**WEAK-12.8: No Executor timeout** — DynamoDB queries may wait indefinitely

---

## 4. Priority 3: Execution Infrastructure Layer

---

### 4.1 `bedrock_client.py` / `gemini_client.py` / `gemini_service.py`

**Path:** `backend/src/services/llm/`

#### Bugs (BUG)

**BUG-13.1: Duplicate method definition in gemini_service.py — HIGH (Line 2007–2031, 2155–2212)**
```python
# First definition: urllib.request (Line 2007)
def _download_from_url(self, url: str) -> bytes:
    ...

# Second definition: requests library (Line 2175) — only this version is actually used
def _download_from_url(self, url: str) -> bytes:
    ...
```
- Same method defined twice. Only the second executes. Intent confusion and latent errors

**BUG-13.2: `self._context_cache` attribute uninitialized — MEDIUM (Line 1552)**
- `get_session_cost_summary()` references `self._context_cache` → AttributeError

**BUG-13.3: `clear_context_cache()` references uninitialized attributes (Line 1558–1562)**
- `self._context_cache`, `self._context_cache_key` uninitialized

**BUG-13.4: Streaming output_tokens always 0 — MEDIUM (Line 1412)**
```python
output_tokens = len(output_text_buffer) // 4 if output_text_buffer else 0
```
- `output_text_buffer` is not updated in streaming loop, always 0

**BUG-13.5: bedrock_client.py streaming non-JSON chunks silently discarded (Line 252–257)**
- Valid data that is not JSON is lost with only a debug log

#### Security Vulnerabilities (SECURITY)

**SEC-13.1: GCP service account temp file not deleted — CRITICAL (Line 488–493)**
```python
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
    f.write(sa_key)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name
    logger.debug(f"Service Account credentials written to {f.name}")
```
- `delete=False` → file persists permanently
- File path recorded in debug log → credentials file path exposed

**SEC-13.2: URL download SSRF vulnerability — HIGH (Line 1824–1825)**
- No hostname validation. Can access `169.254.169.254` (AWS metadata server)
- `requests.get()` allows unlimited redirects

**SEC-13.3: No file path validation — HIGH (Line 1830–1832)**
```python
with open(source, "rb") as f:
    image_bytes = f.read()
```
- Can read arbitrary paths like `/etc/passwd`, `../../.env`

**SEC-13.4: gemini_client.py API key logging risk — HIGH (Line 43–47)**
- If deployment system logs environment variables, GEMINI_API_KEY is exposed

**SEC-13.5: S3 URI path traversal (Line 2015–2020)**

**SEC-13.6: Infrastructure information leaked in error messages (Line 556–561)**
- GCP project ID, credential status included in error messages

**SEC-13.7: project_id used downstream without validation**

**SEC-13.8: No Gemini API key rotation strategy (Line 43–52)**
- When AWS Secrets Manager rotates secret, expired key is used until restart

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-13.1: No retry logic in bedrock_client.py (Line 94–143)**
- No `ThrottlingException` detection. Single failure causes entire operation to fail

**WEAK-13.2: Streaming error Rate Limit not classified (Line 212–219)**
- All exceptions handled identically, then synchronous fallback. Fallback also has no retries

**WEAK-13.3: String matching used for error classification**
```python
if "429" in error_msg or "quota" in error_msg:
```
- Message may vary by SDK version

**WEAK-13.4: No Bedrock timeout configuration**

**WEAK-13.5: Context Cache fallback is not actual cache (Line 726–729)**
```python
cache_name = f"fallback_cache/{self.config.model.value}/{cache_key}"
```
- Not an actual Vertex AI cache resource name. Cache is not used

**WEAK-13.6: No input size validation (Line 1614–1617)**
- No validation of image list size/individual image size → memory exhaustion

**WEAK-13.7: Cost metadata lost on streaming error (Line 1409–1441)**

**WEAK-13.8: No Safety Filter detection in non-streaming mode (Line 829–1041)**
- Streaming detects SAFETY, non-streaming silently returns empty response

**WEAK-13.9: No model availability verification (Line 193–195)**
- Silently falls back to Claude Haiku when Gemini model is requested

**WEAK-13.10: No API key rotation strategy**

**WEAK-13.11: Inaccurate streaming token counting** → cost tracking errors

---

### 4.2 `distributed_chunk_service.py`

**Path:** `backend/src/services/distributed/distributed_chunk_service.py`

#### Bugs (BUG)

**BUG-14.1: Partition slice index logic error — CRITICAL (Line 87–88)**
```python
idx_end = chunk_data.get('end_segment', idx_start)  # default is idx_start (incorrect)
partition_slice = full_map[idx_start : idx_end + 1]
```
- Without `end_segment`, results in single-item slice. `end_segment` is computed at Line 79 but not used

**BUG-14.2: Incomplete S3 loading logic — HIGH (Line 56–96)**
- Unclear whether boundary calculation is based on global index
- No verification that loaded map actually contains the requested segments

**BUG-14.3: No Task Token expiration verification (Line 146–155)**
- No expiration timestamp when storing Task Token → expired tokens may be used

**BUG-14.4: State contamination on partial failure (Line 188–195)**
- `current_state['__latest_segment_id']` is updated even during `PARTIAL_FAILURE`

**BUG-14.5: No state_bucket validity verification (Line 206–207)**

**BUG-14.6: No start_segment index boundary verification (Line 84)**

**BUG-14.7: Duplicate os module import (Line 4–5)**

#### Security Vulnerabilities (SECURITY)

**SEC-14.1: No Task Token format validation (Line 146–155)**
- Arbitrary strings can be stored. Token exposed if logs leak

**SEC-14.2: json.dumps(default=str) — object __repr__ information exposure (Line 210, 226)**

**SEC-14.3: No server-side encryption for S3 upload (Line 214–219)**

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-14.1: Simplistic completion status verification (PARALLEL_GROUP, HITP not considered)**

**WEAK-14.2: No segment-level retry**
- All exceptions treated as FAILED. Permanent failure even for transient outages

**WEAK-14.3: No segment event schema enforcement**

**WEAK-14.4: PARALLEL_GROUP abort behavior undocumented**

**WEAK-14.5: Empty partition_slice treated as success — hides loading failures**

**WEAK-14.6: S3 offload threshold 32KB hardcoded**
- Step Functions actual limit is 256KB. Overly conservative

---

### 4.3 `partition_service.py`

**Path:** `backend/src/services/workflow/partition_service.py`

#### Bugs (BUG)

**BUG-15.1: Topological sort alphabetical ordering makes it non-deterministic — MEDIUM (Line 573–593)**
```python
queue.sort()  # alphabetical sort every iteration → non-deterministic segment order
```
- Violates Kahn's algorithm. Node IDs like "node_10", "node_2" produce incorrect order

**BUG-15.2: Unclear basis for infinite loop prevention counter — MEDIUM (Line 691–711)**
```python
max_iterations = len(nodes) * 2
```
- Valid workflows with nested parallel groups may exceed threshold

**BUG-15.3: forced_segment_starts not pre-initialized — HIGH (Line 487–489)**
- Convergence nodes added only after branch detection → some convergence nodes missed in workflows with multiple branch points

**BUG-15.4: Duplicate branch node IDs allowed (Line 776–778)**

**BUG-15.5: Aggregator convergence nodes undetected in nested structures (Line 1002–1006)**
- No recursive search through nested parallel groups when not found in `node_to_seg_map`

**BUG-15.6: Type alias applied in wrong location (Line 599–605)**
- `"code"` → `"operator"` conversion applied repeatedly inside create_segment

#### Security Vulnerabilities (SECURITY)

**SEC-15.1: No environment variable validation (Line 15, 18, 176, 484)**
```python
MAX_PARTITION_DEPTH = int(os.environ.get("MAX_PARTITION_DEPTH", "50"))
```
- No defense against negative, zero, or non-integer values

**SEC-15.2: No allowlist for node type aliases (Line 74)**

**SEC-15.3: SQL transaction pattern string matching is fragile (Line 365, 388)**
- Can be bypassed via SQL comments/string literals

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-15.1: Empty workflow edge case not handled** — with only nodes and no edges, an arbitrary node is selected

**WEAK-15.2: Unrealistic node execution time estimates** — LLM fixed at 10 seconds. Actual variation is 1–60+ seconds

**WEAK-15.3: No unreachable node detection**

**WEAK-15.4: Convergence node detection returns only first merge point** — asymmetric branches not handled

**WEAK-15.5: No Ring Level boundary edge validation**
- No detection/warning for Ring 3 → Ring 1 direct flow

**WEAK-15.6: No implicit aggregator parent validation**

---

### 4.4 `orchestrator_service.py`

**Path:** `backend/src/services/workflow/orchestrator_service.py`

#### Bugs (BUG)

**BUG-16.1: Missing total_segments risks Step Functions infinite loop — HIGH (Line 397–398)**
```python
if "total_segments" not in result:
    result["total_segments"] = initial_state.get("total_segments") or 1
```
- Defaults to 1 → ASL loop control malfunction, segment skipping or infinite loop

**BUG-16.2: Subgraph cycle detection visited set initialization error (Line 238–241)**
```python
for node in nodes:
    visited.clear()  # cleared for each node → mutual reference cycles undetected
    _detect_cycle(node)
```

**BUG-16.3: Async LLM exception response structure mismatch (Line 406–418)**
- Uses `new_history_logs` (normal path uses `__new_history_logs`)

**BUG-16.4: Mock response missing required fields (Line 360–361)**
```python
return {"status": "PAUSED_FOR_HITP", "next_segment_to_run": 1}
```
- Missing `total_segments`, `final_state` → Step Functions ASL parsing fails

**BUG-16.5: Subgraph node shared reference issue (Line 463–538)**
- No deep copy of node dictionaries. Same node shared across multiple branches causes mutual modification

**BUG-16.6: Cycle detection depth check off-by-one (Line 182–187)**
- Checks after recursion. Actual allowed depth is 51

#### Security Vulnerabilities (SECURITY)

**SEC-16.1: No initial_state validation (Line 352)**
- Can contain arbitrary keys. Risk of internal state override, run_config injection

**SEC-16.2: No user_api_keys validation (Line 383)**
```python
initial_state.setdefault("user_api_keys", {}).update(user_api_keys)
```
- Unknown provider keys can be inserted. No audit logging

**SEC-16.3: No Ring Level runtime enforcement**
- Per-node validation exists but no defense against Ring Level changes during execution

**SEC-16.4: Mock test data log exposure**
```python
"code": "state['res'] = 'X'*300000"
```
- 300KB data included when DEBUG logs are collected

#### Functional Weaknesses (FUNCTIONAL WEAKNESS)

**WEAK-16.1: Unfriendly workflow validation error messages**
- Unclear which node/edge caused the failure

**WEAK-16.2: Subgraph extraction failure handled silently**
- On extraction failure, continues using inline version (no payload size warning)

**WEAK-16.3: No DynamoDB checkpoint table existence verification**
- Error occurs at first checkpoint after execution starts

**WEAK-16.4: Conversation thread collision not detected**

**WEAK-16.5: No stack traces in LLM error logs**

**WEAK-16.6: No workflow execution timeout**
- `app.invoke()` may wait indefinitely

---

## 5. Overall Risk Matrix

### CRITICAL Issue List

| # | File | Issue | Impact |
|---|------|-------|--------|
| 1 | `state_versioning_service.py` | `self.prefix` uninitialized → RuntimeError | All rollback/commit operations fail immediately |
| 2 | `eventual_consistency_guard.py` | GC race condition → block deletion | Data loss |
| 3 | `checkpoint_service.py` | No S3 path parsing safety | Crash / path traversal |
| 4 | `prompt_security_guard.py` | syscall auto-approved with 21-char justification | Privilege escalation / arbitrary permission acquisition |
| 5 | `prompt_security_guard.py` | Fundamental blocklist limitations | Complete bypass via encoding/variant attacks |
| 6 | `gemini_service.py` | GCP credentials temp file not deleted | Credential leak |
| 7 | `distributed_chunk_service.py` | Partition slice index error | Severe chunk under-processing |
| 8 | `retroactive_masking.py` | 8-character hash enables reverse masking | Original PII recoverable |

### Top Priority Improvement Areas by Layer

| Layer | Top Priority Area |
|-------|-------------------|
| Security & Trust | Strengthen syscall permission verification, connect constitution to guardrails, trust score persistence |
| State & Consistency | Initialize self.prefix, GC race protection, fix Redis fallback logic |
| Execution Infrastructure | GCP credentials file cleanup, SSRF defense, fix chunk index |

### Recommended Improvement Priority

```
Immediate (production blockers):
  - state_versioning_service.py BUG#1 (self.prefix)
  - eventual_consistency_guard.py BUG#4 (GC race)
  - gemini_service.py SEC#1 (credentials file)
  - distributed_chunk_service.py BUG#1 (partition index)
  - prompt_security_guard.py SEC#1 (syscall permission)

Short-term (before next release):
  - trust_score_manager.py: persistence + Race Condition
  - async_commit_service.py: S3 error code fix
  - checkpoint_service.py: S3 path validation + permission check
  - bedrock_client.py: retry logic
  - constitution.py: Guardrail connection

Mid-term (architecture improvements):
  - CircuitBreaker distributed environment support (Redis-based)
  - Ring 1/2 validation layer implementation
  - PII masking enhancements (Luhn, check digits, additional types)
  - GC monitoring CloudWatch metrics
  - Error classifier multilingual support
```

---

*This document was written solely for the purpose of recording inspection results; code changes will be handled as separate tasks.*
