# Architecture Deep-Dive

> [Back to Main README](../README.md)

This document provides a comprehensive technical reference for the Analemma OS kernel architecture, covering the Ring protection hierarchy, The Great Seal protocol, state management pipeline, distributed execution strategies, and governance layer.

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [4-Ring Kernel Protection Model](#2-4-ring-kernel-protection-model)
3. [The Great Seal Protocol (v3.13)](#3-the-great-seal-protocol-v313)
4. [Universal Sync Core: State Pipeline](#4-universal-sync-core-state-pipeline)
5. [256KB Payload Defense: L1-L5 Layers](#5-256kb-payload-defense-l1-l5-layers)
6. [Merkle DAG State Versioning](#6-merkle-dag-state-versioning)
7. [2-Phase Commit Protocol](#7-2-phase-commit-protocol)
8. [Distributed Execution Strategies](#8-distributed-execution-strategies)
9. [Governance Layer](#9-governance-layer)
10. [Gemini Integration Architecture](#10-gemini-integration-architecture)
11. [Glass-Box Observability](#11-glass-box-observability)

---

## 1. System Architecture Overview

Analemma OS is a serverless kernel that enforces deterministic state management over probabilistic AI agent execution. Every component is organized into a 4-Ring privilege hierarchy modeled after CPU protection rings.

```
+-----------------------------------------------------------------------+
|  Ring 3 - USER AGENTS (Untrusted)                                     |
|  Gemini-powered agents, external tools                                 |
|  All output treated as unverified data                                 |
|                                                                        |
|  +-------------------------------------------------------------------+ |
|  | Ring 2 - TRUSTED TOOLS                                            | |
|  | Verified internal services, validated API integrations             | |
|  |                                                                   | |
|  | +---------------------------------------------------------------+ | |
|  | | Ring 1 - GOVERNOR (Deterministic Gatekeeper)                  | | |
|  | | governor_runner, trust_score_manager, constitution.py          | | |
|  | |                                                               | | |
|  | | +-----------------------------------------------------------+ | | |
|  | | | Ring 0 - KERNEL (Immutable System Core)                   | | | |
|  | | | kernel_protocol.py   seal_state_bag / open_state_bag      | | | |
|  | | | universal_sync_core  Unified state merge pipeline          | | | |
|  | | | state_versioning     Merkle DAG + 2-Phase Commit           | | | |
|  | | +-----------------------------------------------------------+ | | |
|  | +---------------------------------------------------------------+ | |
|  +-------------------------------------------------------------------+ |
+-----------------------------------------------------------------------+
```

**Execution flow:** Lambda invocations enter at Ring 3 (agent output) and are processed upward through Ring 1 (Governor validation) before Ring 0 (kernel) commits state. Agent output is never executed as kernel instructions.

---

## 2. 4-Ring Kernel Protection Model

### Ring Definitions

| Ring | Name | Components | Trust Level |
|---|---|---|---|
| 0 | KERNEL | `kernel_protocol.py`, `universal_sync_core.py`, `state_versioning_service.py` | Immutable system core |
| 1 | GOVERNOR | `governor_runner.py`, `trust_score_manager.py`, `constitution.py` | Deterministic gatekeeper |
| 2 | TRUSTED TOOLS | Verified internal services, validated API integrations | Controlled access |
| 3 | USER AGENTS | Gemini agents, external tools, user-defined logic | Untrusted input |

### Ring Enforcement Invariants

**Invariant 1: Kernel Control Key Reservation**

The kernel maintains a `KERNEL_CONTROL_KEYS` set (`governor_runner.py`) containing all reserved command keys:

```python
KERNEL_CONTROL_KEYS = {
    "_kernel_skip_segments",
    "_kernel_skip_reason",
    "_kernel_inject_recovery",
    "_kernel_rollback_to_manifest",
    "_kernel_rollback_reason",
    "_kernel_rollback_type",
    "_kernel_modify_parallelism",
    "_kernel_request_human_approval",
    "_kernel_terminate_workflow",
    "_kernel_retry_current_segment",
}
```

If any Ring 3 agent output contains a key matching this set, the Governor immediately triggers `KERNEL_COMMAND_FORGERY → TERMINAL_HALT`. There is no bypass path. This check runs before any business logic processing.

**Invariant 2: Unified State Mutation Pipeline**

Every Lambda function — regardless of purpose — exits through `seal_state_bag()`, which routes all state through the Universal Sync Core. There is no side-channel for state writes. Any Lambda attempting to return a state payload without passing through this contract produces a malformed response that the ASL `ResultSelector` rejects.

**Invariant 3: Immutable Governance Audit Records**

Every APPROVED / REJECTED / ROLLBACK decision from the Governor is written to DynamoDB with a 90-day TTL. The Merkle chain (`parent_manifest_id` linkage) makes retroactive tampering detectable: modifying any historical state block changes its SHA-256 hash, which invalidates all downstream manifest IDs.

### StateViewContext: Ring-Based Field Masking

Above Ring 0, field access is mediated by `StateViewContext`, which applies a per-ring masking policy:

| Field Category | Ring 0 | Ring 1 | Ring 2 | Ring 3 |
|---|---|---|---|---|
| `_kernel_*` control fields | Full access | Full access | Hidden | Hidden |
| PII fields (email, ssn, password) | Plaintext | Plaintext | Redacted | Redacted |
| Email fields | Plaintext | Plaintext | Hashed (SHA-256) | Hashed |
| Business data | Full access | Full access | Full access | Full access |

---

## 3. The Great Seal Protocol (v3.13)

The kernel defines a single I/O contract for every Lambda function in the system. This contract — called "The Great Seal" — standardizes how Lambda functions receive and return state, decoupling business logic from ASL topology.

### Entry: `open_state_bag(event)`

Extracts the state dictionary from the raw Step Functions event regardless of nesting depth. Searches three paths in priority order:

1. `event["state_data"]["bag"]` — standard v3.13 format (ASL `ResultSelector` maps `$.Payload.state_data` → `$.state_data.bag`)
2. `event["state_data"]` — flattened format
3. `event` — direct Lambda invocation (testing)

This decouples Lambda code from ASL `ResultPath` configuration. Changing the ASL topology (adding a `Pass` state, changing `ResultPath`) does not require Lambda code changes.

### Exit: `seal_state_bag(base_state, result_delta, action)`

Standardizes the Lambda return value:

1. Passes `(base_state, result_delta, action)` through the Universal Sync Core
2. Returns the canonical two-key response:

```python
{
    "state_data": { ...merged, offloaded, optimized... },
    "next_action": "CONTINUE" | "COMPLETE" | "FAILED" | "PAUSED_FOR_HITP"
}
```

The ASL `ResultSelector` maps `$.Payload.state_data` to `$.state_data.bag`, so the next Lambda receives a clean state bag.

**Double-Wrapping Prevention**

Without a unified protocol, ad hoc implementations cause `state_data` to nest inside `state_data` when `ResultPath` and return values are independently configured. The Great Seal eliminates this failure mode by making the wrapping structure a kernel invariant rather than a convention.

---

## 4. Universal Sync Core: State Pipeline

All state transitions pass through a single pipeline: `universal_sync_core.py`. The pipeline has four steps, executed in sequence for every Lambda invocation.

```
Lambda Output
     |
     v
Step 1: flatten_result(new_result, context)
     Action-specific extraction. Unwraps execution_result wrappers.
     Sorts distributed Map outputs by execution_order.
     Resolves S3 ResultWriter manifests to lightweight pointers.
     NEVER returns None (v3.4 deep guard).
     |
     v
Step 2: merge_logic(base_state, delta, context)
     Copy-on-Write shallow merge.
     Only the fields in `fields_to_modify` are copied; unchanged
     fields retain existing references (no full deepcopy).
     List fields apply per-field strategies:
       - state_history:  dedupe_append (deduplication by node_id + timestamp)
       - branches:       replace (latest branch topology only)
       - failed_branches: append
     Control fields (CONTROL_FIELDS_NEVER_OFFLOAD) always take delta value.
     NEVER returns None (v3.4 deep guard).
     |
     v
Step 3: optimize_and_offload(state, context)
     Multi-layer 256KB defense (see Section 5).
     NEVER returns None (v3.4 deep guard).
     |
     v
Step 4: _compute_next_action(state, delta, action)
     Centralized routing decision.
     Returns: STARTED | CONTINUE | COMPLETE | FAILED | PAUSED_FOR_HITP
     No routing logic lives in individual Lambda functions.
     |
     v
{ "state_data": ..., "next_action": ... }
```

### Key Constants

```python
# universal_sync_core.py
FIELD_OFFLOAD_THRESHOLD_KB    = 30   # L1: individual field threshold
FULL_STATE_OFFLOAD_THRESHOLD_KB = 100  # L2: full state threshold
MAX_PAYLOAD_SIZE_KB           = 200  # Internal cap (56KB below AWS 256KB limit)
POINTER_BLOAT_WARNING_THRESHOLD_KB = 10
```

### List Field Merge Strategies

| Field | Strategy | Behavior |
|---|---|---|
| `state_history` | `dedupe_append` | Append, deduplicate by `node_id + timestamp` |
| `new_history_logs` | `dedupe_append` | Merged into `state_history` |
| `branches` | `replace` | Replace with latest branch topology |
| `chunk_results` | `replace` | Replace (latest aggregation only) |
| `failed_branches` | `append` | Always accumulate |
| `distributed_outputs` | `append` | Always accumulate |

---

## 5. 256KB Payload Defense: L1-L5 Layers

AWS Step Functions enforces a 256KB hard limit on state payloads. The internal cap is set at 200KB, preserving a 56KB margin for SFN metadata overhead. The defense executes in five layers within `optimize_and_offload()`.

**Execution order** (L3 runs before L1/L2):

```
optimize_and_offload() execution sequence:
  1. L3: History archiving          (optimize_state_history, max_entries=50)
  2. L1/L2: Field + state offload   (optimize_current_state)
       L1: Individual fields > 30KB  -> S3, store pointer
       L2: Total current_state > 100KB -> full state to S3, store pointer
  3. L4: Pointer bloat prevention   (prevent_pointer_bloat)
       failed_segments > 5 items    -> offload, keep 5-item sample
       scheduling_metadata > 5 batches -> summarize + delete inline copy
  4. L5: Emergency array offload    (emergency_offload_large_arrays)
       Trigger: total state > 150KB (75% of 200KB internal cap)
       Action: distributed_outputs > 10 items -> offload, keep 10-item sample
```

**Layer summary:**

| Layer | Trigger | Action |
|---|---|---|
| L1 | Individual field > 30KB | Offload field to S3, replace with `{field}_s3_path` pointer |
| L2 | `current_state` > 100KB after L1 | Offload entire `current_state` to S3 |
| L3 | `state_history` > 50 entries | Archive oldest entries to S3 |
| L4 | `failed_segments` > 5 or `scheduling_metadata` > 5 batches | Summarize, offload bulk data |
| L5 | Total state > 150KB (75% of 200KB cap) | Emergency offload of `distributed_outputs` |

**Fields exempt from offloading** (`CONTROL_FIELDS_NEVER_OFFLOAD`):

`execution_id`, `segment_to_run`, `segment_id`, `loop_counter`, `next_action`, `status`, `idempotency_key`, `state_s3_path`, `last_update_time`, `payload_size_kb`

These fields are required by the ASL state machine for routing decisions and must remain inline at all times.

---

## 6. Merkle DAG State Versioning

Every segment execution produces an immutable state manifest. Manifests are linked by `parent_manifest_id` into a tamper-evident Merkle chain.

### Content-Addressed Block Storage

State data is serialized into blocks keyed by SHA-256 hash of content:

```
s3://[WORKFLOW_STATE_BUCKET]/manifests/[workflow_id]/[segment]/
    block_[sha256_of_content].json
```

Because block IDs are content hashes, identical state fields across executions share the same block. No upload occurs for unchanged fields (content-addressed deduplication). The Merkle root is a three-term SHA-256 derived from `_compute_merkle_root()`:

```
B = SHA-256( CONCAT sorted_by_block_id(block.checksum) )
R = SHA-256( config_hash || parent_manifest_hash || B )
```

The inclusion of `config_hash` and `parent_manifest_hash` (the hash of the parent manifest's `manifest_hash` field) means identical block sets produce different roots if the workflow configuration or parent chain differs. Any field change propagates to a new `B`, a new root `R`, and a new manifest ID, making retroactive tampering detectable without a separate signature mechanism.

### Manifest Structure

```json
{
  "manifest_id": "uuid-v4",
  "parent_manifest_id": "uuid-v4",
  "version": 42,
  "workflow_id": "wf_abc123",
  "segment_id": 5,
  "merkle_root": "sha256-of-sorted-block-ids",
  "governance_decision": "APPROVED",
  "blocks": [
    {
      "block_id": "sha256-hash",
      "s3_key": "workflows/{id}/blocks/{hash}.json",
      "fields": ["llm_response", "current_state"]
    }
  ],
  "ttl": 1736899200
}
```

### Rollback Capability

The Governor triggers rollback at three severity levels:

| Violation | Rollback Type | Mechanism |
|---|---|---|
| `KERNEL_COMMAND_FORGERY` | `TERMINAL_HALT` | Immediate SFN `SIGKILL` — no state written |
| `SLOP` / `CIRCUIT_BREAKER` | `HARD_ROLLBACK` | Restore last `governance_decision=APPROVED` manifest via DynamoDB GSI query; abandoned branch blocks marked for GC with 7-day grace period |
| `PLAN_DRIFT` / `GAS_FEE` | `SOFT_ROLLBACK` | Retry current segment with agent feedback message injected |

---

## 7. 2-Phase Commit Protocol

S3 and DynamoDB are kept consistent through a two-phase atomic protocol implemented in `state_versioning_service.py`. An optional third step (tag promotion) is non-critical and handled by the GC worker if it fails.

The codebase contains two parallel code paths with different tag naming conventions:

| Code Path | Phase 1 Tag | Post-Commit Tag |
|---|---|---|
| `create_manifest()` / `EventualConsistencyGuard` | `status=pending` | `status=committed` |
| `save_state_delta()` / `KernelStateManager` | `status=temp` | `status=ready` |

Both paths implement the same 2PC invariants. The descriptions below apply to both.

### Phase 1 — PREPARE

Upload each block to S3 with an in-flight tag (`status=pending` or `status=temp`) and a `transaction_id`. If any upload fails, synchronously delete all previously uploaded blocks — no DynamoDB state has been written yet, so rollback is clean.

```
S3 PutObject (status=pending|temp, transaction_id=txn_id)
  -> Upload failure: synchronous delete of all uploaded blocks
  -> All uploads succeed: proceed to Phase 2
```

### Phase 2 — COMMIT

DynamoDB `TransactWriteItems` (atomic, exactly-once):

1. Update block reference counts in batches of 99 (DynamoDB `TransactWriteItems` hard limit is 100 operations; 1 slot reserved for the manifest write)
2. Write manifest record with condition `attribute_not_exists(manifest_id)` — idempotent: duplicate commits are no-ops

If Phase 2 succeeds, the transaction is committed. Phase 2 failures schedule blocks for asynchronous GC via SQS DLQ.

### Post-Commit: Tag Promotion

After Phase 2 commits, update S3 block tags to `status=committed` or `status=ready`. This step is non-critical: tag update failures do not fail the transaction. The GC worker re-verifies tags before deletion and skips committed/ready blocks.

### GC Worker

Two complementary mechanisms handle orphaned blocks:

- **Primary GC**: DynamoDB TTL expiry → DynamoDB Streams event → GC Lambda (`merkle_gc_service.py`). Event-driven, not a periodic scan. Handles manifest TTL expiry for retired workflow state.
- **Transaction failure GC**: Phase 2 failure → SQS DLQ (5-minute delay) → GC Lambda. Targets only blocks from the failed transaction. Re-verifies S3 object tag before deletion (idempotent guard against late post-commit success).

This architecture ensures zero orphan blocks without periodic S3 `ListObjects` scans.

---

## 8. Distributed Execution Strategies

The kernel selects execution strategy automatically at workflow save time, based on segment count and independence score.

| Condition | Strategy | ASL Mechanism |
|---|---|---|
| Total segments <= 10 | `SAFE` (sequential) | Standard Choice/Pass loop |
| Total segments 10–100 | `BATCHED` | ASL Map state, `ItemsPath: $.state_data.bag.segment_manifest` |
| Total segments > 100, independence > 0.7 | `MAP_REDUCE` | ASL Distributed Map with S3 ResultWriter |

### Segment Manifest Design

`segment_manifest` is a lightweight array of S3 pointers (~200 bytes per segment). It is intentionally excluded from S3 offloading so the ASL `Map` state can reference it inline via `ItemsPath`. Full segment configuration lives at the referenced S3 path.

### MAP_REDUCE: Manifest-Only Aggregation

For large Distributed Map executions, the aggregator Lambda uses manifest-only aggregation to avoid payload explosion:

```
Distributed Map (N workers)
     |
     | Each worker:
     |   1. Load full segment config from S3
     |   2. Execute segment with Gemini
     |   3. Write output to S3
     |   4. Return S3 pointer (not full data)
     v
ResultWriter -> manifest.json (N pointers)
     |
     v
Aggregate Lambda
     |
     | Reads manifest.json only (not individual outputs)
     | Computes statistics: total, succeeded, failed
     | Passes manifest_s3_path to state
     | Does NOT load individual result files
     v
State: { distributed_chunk_summary, segment_manifest_s3_path }
```

This ensures aggregation cost is O(1) in state payload size regardless of N.

---

## 9. Governance Layer

The Governor (Ring 1) validates agent output after every segment execution. Validation runs on the raw agent output before it is merged into kernel state.

### Detection Metrics

| Metric | Method | Default Threshold |
|---|---|---|
| Output size (SLOP) | `len(json.dumps(output).encode('utf-8'))` | 500KB |
| Plan drift | SHA-256 hash + keyword-overlap Intent Retention Rate (IRR) | IRR < 0.7 |
| Gas fee | Accumulated `total_tokens_used * cost_per_token` | $100 USD |
| Circuit breaker | Per-agent retry counter from workflow state | 3 retries |
| Kernel command forgery | Intersection of agent output keys with `KERNEL_CONTROL_KEYS` | Zero tolerance |

### Trust Score Model

Agent trust scores use an Exponential Moving Average to solve the asymmetric recovery problem inherent in fixed-increment systems:

```
streak_ratio = consecutive_successes / recent_window (last 10 decisions)

delta_S = BASE_INCREMENT * (1 + EMA_ACCELERATION * streak_ratio)
        = 0.01 * (1 + 2.0 * streak_ratio)

T_new = clamp(T_old + delta_S - (ring_penalty_multiplier * anomaly_score), 0.0, 1.0)
```

With a 5-step consecutive success streak (`streak_ratio = 1.0`): `delta_S = 0.03`. Recovery from 0.4 to 0.8 converges in 14 iterations versus 40 with a fixed +0.01 increment — a 65% reduction derived directly from the model parameters in `trust_score_manager.py`.

Ring-level penalty multipliers:

| Ring | Multiplier |
|---|---|
| 0 (Kernel) | 2.0x |
| 1 (Governor) | 1.5x |
| 2 (Trusted Tools) | 0.8x |
| 3 (User Agents) | 0.5x |

Scores below 0.4 force `STRICT` governance mode regardless of ring level.

### Constitutional AI Clauses

Six default articles are enforced at Ring 1. `CRITICAL`-severity violations produce immediate `REJECTED` decisions:

| Article | Rule | Severity |
|---|---|---|
| 1 | Professional tone | MEDIUM |
| 2 | No harmful content generation | CRITICAL |
| 3 | No PII solicitation (passwords, card numbers) | CRITICAL |
| 4 | Transparency about uncertainty | LOW |
| 5 | No security policy bypass | CRITICAL |
| 6 | No PII in output text (email, phone, SSN detected via regex) | CRITICAL |

Custom clauses can be added per-workflow via `governance_policies.constitution[]` with article numbers above 6. Article numbers 1–6 are reserved.

---

## 10. Gemini Integration Architecture

The kernel infrastructure (Step Functions orchestration, Merkle DAG, 2PC, Ring protection) is model-agnostic. The intelligence layer is not.

| Capability | Analemma Requirement | Why Gemini 3 |
|---|---|---|
| 2M token context window | Load full execution history for self-healing diagnosis | GPT-4: 128K, Claude 3.5: 200K — insufficient for full-history analysis |
| Sub-500ms TTFT | Real-time kernel scheduling decisions between segments | Higher latency degrades execution loop to batch |
| Native structured output | Zero-parsing overhead for Merkle manifest serialization | Prompt-engineered JSON is brittle at scale |
| Vertex AI context caching | Cache 500K+ token system prompts across executions | Gemini-specific API |
| Thinking Mode | Expose reasoning chain to Glass-Box callbacks | Native capability |
| Multimodal input | Analyze logs + architecture diagrams + metrics simultaneously | Required for self-healing across heterogeneous signals |

### Model Router

The `model_router.py` service dynamically selects the Gemini variant based on context length, task complexity, and latency requirements. Selection criteria:

1. Semantic intent detection (structure vs. query vs. edit)
2. Token count estimation (triggers Pro vs. Flash selection)
3. Latency requirements (interactive vs. batch)

Context caching activates automatically for contexts above 32K tokens. Cached tokens are billed at the provider's reduced rate.

---

## 11. Glass-Box Observability

Glass-Box streams real-time agent reasoning to connected clients via WebSocket API Gateway. The `trace_id` field is a `KERNEL_PROTECTED_FIELD` that persists across all Lambda invocations in a workflow execution, enabling distributed trace correlation.

### WebSocket Payload Architecture

Because the state bag uses S3 pointers, raw payloads are not directly renderable by the frontend. The WebSocket notifier performs surgical field hydration:

1. Receive pointer-only payload from kernel
2. Fetch only the fields required for UI rendering (logs, status descriptors) from S3
3. Prune fields exceeding the API Gateway WebSocket frame limit (128KB)
4. Stream enriched payload to connected clients

### Thinking Mode Integration

When Gemini Thinking Mode is active, intermediate reasoning steps are streamed as `ai_thought` events before the final response. These events carry the same `trace_id` as the parent execution, enabling correlation of reasoning steps to their producing segment.

### Log Structure

```json
{
  "type": "ai_thought",
  "timestamp": "2026-01-14T10:05:01.200Z",
  "trace_id": "exec_xyz789",
  "segment_id": 0,
  "data": {
    "model": "gemini-3-pro",
    "tokens": { "input": 850, "output": 400, "total": 1250 },
    "duration_ms": 850
  }
}
```

Event types: `ai_thought`, `tool_usage`, `decision`, `error`, `governance_result`, `kernel_event`

---

> [Back to Main README](../README.md)
