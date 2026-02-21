# Analemma OS — Kernel Layer Technical Report

**Version:** v3.13 "The Great Seal"
**Date:** 2026-02-21
**Scope:** Ring 0 Kernel · Kernel Protocol · Universal Sync Core · Merkle DAG State Versioning · Ring Protection

---

## Table of Contents

1. [Overview](#1-overview)
2. [System Architecture](#2-system-architecture)
3. [Ring Protection Hierarchy](#3-ring-protection-hierarchy)
4. [Kernel Protocol (v3.13)](#4-kernel-protocol-v313)
5. [Universal Sync Core (USC)](#5-universal-sync-core-usc)
6. [State Lifecycle: Data Flow Walkthrough](#6-state-lifecycle-data-flow-walkthrough)
7. [Merkle DAG State Versioning](#7-merkle-dag-state-versioning)
8. [2-Phase Commit (2PC)](#8-2-phase-commit-2pc)
9. [256KB Payload Defense](#9-256kb-payload-defense)
10. [Distributed Execution Strategy](#10-distributed-execution-strategy)
11. [Governance Layer (Ring 1)](#11-governance-layer-ring-1)
12. [Key Constants & Environment Variables](#12-key-constants--environment-variables)
13. [Error Conditions & Failure Modes](#13-error-conditions--failure-modes)

---

## 1. Overview

Analemma OS is a serverless, agentic workflow execution platform built on AWS Step Functions (SFN). Its kernel layer is the invisible contract that makes every Lambda invocation predictable, composable, and safe under the 256 KB SFN payload constraint.

**Core guarantee of the kernel:**

> Any Lambda function, regardless of what it does, receives a clean `bag` dict and returns a standard sealed response. The Step Functions state machine never sees raw Lambda outputs directly — the kernel protocol mediates everything.

The kernel is not a single process; it is a **protocol + three runtime components**:

| Component | File | Role |
|---|---|---|
| **Kernel Protocol** | `common/kernel_protocol.py` | I/O contract: `open_state_bag` / `seal_state_bag` |
| **Universal Sync Core (USC)** | `handlers/utils/universal_sync_core.py` | State merge, S3 offloading, next_action computation |
| **State Versioning Service** | `services/state/state_versioning_service.py` | Merkle DAG, 2PC, immutable audit trail |

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    AWS Step Functions (ASL v3)               │
│                                                             │
│  ┌──────────┐   ResultSelector   ┌──────────┐              │
│  │ Lambda A │─────────────────→  │ State    │              │
│  │          │  $.Payload         │ Machine  │              │
│  │ (Ring 2) │  .state_data  →    │          │              │
│  └──────────┘  .next_action      └────┬─────┘              │
│                                       │ ResultPath:         │
│                                       │ $.state_data        │
│                                       ▼                     │
│                              $.state_data.bag               │
│                              → next Lambda Payload          │
└─────────────────────────────────────────────────────────────┘
            ▲                          │
            │  seal_state_bag()        │  open_state_bag()
            │                          ▼
┌───────────┴──────────────────────────────────────────────────┐
│                    KERNEL PROTOCOL (Ring 0)                   │
│                                                              │
│   open_state_bag(event) → bag dict (clean, flat)             │
│   seal_state_bag(base, delta, action) → {state_data, next}   │
│                          ↓                                    │
│              Universal Sync Core (USC)                        │
│         1. flatten_result()  → normalized delta              │
│         2. merge_logic()     → Shallow Merge + CoW           │
│         3. optimize_and_offload() → 256KB defense            │
│         4. _compute_next_action() → CONTINUE/COMPLETE/...    │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Ring Protection Hierarchy

The system enforces a 4-ring security model inspired by OS privilege levels:

```
Ring 0 — KERNEL
    ├─ kernel_protocol.py (seal/open)
    ├─ universal_sync_core.py
    └─ KERNEL_CONTROL_KEYS (reserved commands)

Ring 1 — GOVERNOR (Driver)
    ├─ governor_runner.py
    ├─ trust_score_manager.py
    └─ constitution.py / constitution_loader.py

Ring 2 — TRUSTED TOOLS
    ├─ Verified segment runners
    └─ Internal service integrations

Ring 3 — USER AGENTS
    ├─ LLM-generated agents (Manus, Moltbot, etc.)
    └─ External tools / API calls
```

### 3.1 Ring-Based Governance Mode

| Ring Level | Governance Mode | Behavior |
|---|---|---|
| Ring 0 (Kernel) | STRICT | Synchronous validation, immediate block |
| Ring 1 (Governor) | STRICT | Synchronous validation, immediate block |
| Ring 2 (Trusted) | OPTIMISTIC | Async logging + rollback |
| Ring 3 (User) | OPTIMISTIC | Async logging + rollback |

### 3.2 Kernel Control Keys

These keys are **reserved for Ring 0/1 only**. If a Ring 2/3 agent outputs any of these, it triggers `KERNEL_COMMAND_FORGERY` → `TERMINAL_HALT`:

```python
KERNEL_CONTROL_KEYS = {
    "_kernel_skip_segments",          # Skip obsolete segments after plan change
    "_kernel_skip_reason",
    "_kernel_inject_recovery",        # Inject HITP recovery segment
    "_kernel_rollback_to_manifest",   # Rollback to safe Merkle manifest
    "_kernel_rollback_reason",
    "_kernel_rollback_type",
    "_kernel_modify_parallelism",     # Cap concurrent branches (Gas Fee exceeded)
    "_kernel_request_human_approval", # Circuit breaker → HITP
    "_kernel_terminate_workflow",     # TERMINAL_HALT (security forgery)
    "_kernel_retry_current_segment",  # SOFT_ROLLBACK with feedback
}
```

---

## 4. Kernel Protocol (v3.13)

**File:** `backend/src/common/kernel_protocol.py`

The protocol defines the **single entry point** and **single exit point** for every Lambda function in the system.

### 4.1 Entry: `open_state_bag(event)`

Extracts the actual data (`bag`) from the raw SFN event, regardless of how deeply nested it is.

```
Search order:
  1. event.state_data.bag     ← Standard v3.13 path (ASL ResultSelector + ResultPath)
  2. event.state_data         ← Flattened case (legacy or direct invocation)
  3. event                    ← Root is data (direct Lambda test)
```

**ASL contract producing the standard path:**
```json
{
  "ResultSelector": {
    "bag.$": "$.Payload.state_data",
    "next_action.$": "$.Payload.next_action"
  },
  "ResultPath": "$.state_data"
}
```

After this, SFN state is:
```json
{
  "state_data": {
    "bag": { "execution_id": "...", "segment_to_run": 3, ... },
    "next_action": "CONTINUE"
  }
}
```

The next Lambda receives `$.state_data.bag` as its event payload — `open_state_bag()` finds it at `event.state_data.bag`.

### 4.2 Exit: `seal_state_bag(base_state, result_delta, action, context)`

All Lambda functions **must** exit through this function. It:

1. Calls `universal_sync_core(base_state, result_delta, context)` — merges state and offloads to S3 if needed.
2. Wraps the USC result in the standard double-key format:

```python
return {
    "state_data": usc_result["state_data"],   # The merged, optimized bag
    "next_action": usc_result["next_action"]  # CONTINUE | COMPLETE | FAILED | ...
}
```

SFN's `$.Payload` wrapper then gives the ASL:
```
$.Payload.state_data  →  ResultSelector maps this to bag key
$.Payload.next_action →  Used by Choice state for routing
```

### 4.3 Why This Solves Double-Wrapping

Without this protocol, a naive Lambda return like:
```python
return {"state_data": result}
```
Would become `$.Payload = {"state_data": {"state_data": result}}` — an accidental double-wrap. The protocol enforces that `state_data` at the Lambda level directly contains the bag (not a nested `state_data`).

---

## 5. Universal Sync Core (USC)

**File:** `backend/src/handlers/utils/universal_sync_core.py`

The USC is the **data pipeline kernel**. It is function-agnostic: regardless of which action a Lambda is performing (init, sync, callback merge, branch aggregation, distributed map reduce), all data flows through the same 4-step pipe.

### 5.1 The 4-Step Pipeline

```
Input: base_state (dict) + new_result (any) + context (SyncContext)
         │
         ▼
Step 1: flatten_result(new_result, context)
         │  Normalize input by action type
         │  Lists → distributed map aggregation
         │  ResultWriter manifest → S3 pointer extraction
         │  action='sync' → execution_result extraction + segment routing
         │  action='aggregate_branches' → parallel branch merge
         │  action='init' → bag extraction + required metadata injection
         ▼
         normalized_delta (dict)
         │
         ▼
Step 2: merge_logic(base_state, normalized_delta, context)
         │  Copy-on-Write shallow copy (avoid full deepcopy)
         │  CONTROL_FIELDS_NEVER_OFFLOAD: delta always wins
         │  List fields: per-field strategy (append/replace/dedupe_append/set_union)
         │  Dict fields: shallow merge (delta keys overwrite base keys)
         │  action='init': inject INIT_REQUIRED_METADATA defaults first
         ▼
         updated_state (dict)
         │
         ▼
Step 3: optimize_and_offload(updated_state, context)
         │  1. state_history archiving (>50 entries → S3)
         │  2. current_state field offloading (>30KB → S3 pointer)
         │  3. Full-state offloading (>100KB → S3 pointer)
         │  4. prevent_pointer_bloat() — scheduling_metadata, failed_segments
         │  5. 150KB warning → emergency_offload_large_arrays()
         │  6. Update payload_size_kb + last_update_time metadata
         ▼
         optimized_state (dict, guaranteed ≤ 200KB)
         │
         ▼
Step 4: _compute_next_action(optimized_state, delta, action)
         │  init → 'STARTED'
         │  FAILED/HALTED/SIGKILL → pass through
         │  COMPLETE → 'COMPLETE'
         │  PAUSED_FOR_HITP → 'PAUSED_FOR_HITP'
         │  All distributed chunks failed → 'FAILED'
         │  segment_to_run >= total_segments → 'COMPLETE'
         │  default → 'CONTINUE'
         ▼
Output: { "state_data": optimized_state, "next_action": "CONTINUE" }
```

### 5.2 Control Fields (Never Offloaded)

These fields are always kept in the SFN state (never moved to S3), because ASL Choice/Map states reference them directly:

```python
CONTROL_FIELDS_NEVER_OFFLOAD = frozenset({
    'execution_id',
    'segment_to_run',    # ASL routing: which segment to execute next
    'segment_id',        # Current segment identification
    'loop_counter',      # Infinite loop prevention
    'next_action',       # ASL Choice state routing
    'status',
    'idempotency_key',   # Exactly-once execution
    'state_s3_path',     # S3 pointer for hydration
    'pre_snapshot_s3_path',
    'post_snapshot_s3_path',
    'last_update_time',
    'payload_size_kb'
})
```

### 5.3 List Field Merge Strategies

```python
LIST_FIELD_STRATEGIES = {
    'state_history':     'dedupe_append',   # Deduplicate by node_id+timestamp
    'new_history_logs':  'dedupe_append',
    'failed_branches':   'append',          # Accumulate all failures
    'distributed_outputs': 'append',
    'branches':          'replace',         # Always use latest branch layout
    'chunk_results':     'replace',
}
```

### 5.4 Copy-on-Write (CoW) Optimization

Instead of `deepcopy(base_state)` (expensive for large states), USC uses:
```python
result = base_state.copy()           # Shallow copy (O(n) where n = key count)
for field in fields_to_modify:       # Deep copy only changed sub-trees
    if isinstance(value, dict):
        result[field] = value.copy()
    elif isinstance(value, list):
        result[field] = value.copy()
```

This reduces CPU + GC pressure for large workflow states (e.g., 14,000-line kernel states).

---

## 6. State Lifecycle: Data Flow Walkthrough

### 6.1 Workflow Initialization (action = 'init')

```
1. User triggers workflow → API Gateway → InitializeStateData Lambda
2. InitializeStateData:
   a. Loads workflow_config from DynamoDB (WorkflowsTableV3)
   b. Runs partition_workflow_advanced() → segment_manifest (array of pointers)
   c. Builds initial bag:
      {
        "execution_id": "arn:aws:...",
        "workflow_config": {...},       → force-offloaded to S3 (>30KB typical)
        "partition_map": {...},         → force-offloaded to S3
        "segment_manifest": [ptr0, ptr1, ...],  ← lightweight, stays in bag
        "segment_to_run": 0,
        "loop_counter": 0,
        "state_history": [],
        "total_segments": N
      }
   d. Calls seal_state_bag(base={}, delta=bag, action='init')
      → USC pipeline: flatten(init) → merge with INIT_REQUIRED_METADATA → optimize
      → Returns { state_data: {...}, next_action: "STARTED" }
3. SFN starts WorkflowDistributedOrchestrator state machine
   Initial input: seal_state_bag result
```

### 6.2 Segment Execution Loop (action = 'sync')

```
ASL State Machine Loop:
  ┌─────────────────────────────────────────────┐
  │  1. IncrementLoopCounter (Direct SDK)        │
  │     DynamoDB UpdateItem: loop_counter += 1   │
  │     (ASL handles loop_counter, NOT USC)      │
  │                                             │
  │  2. RunSegment (Lambda: SegmentRunner)        │
  │     Input: $.state_data.bag                  │
  │     open_state_bag(event) → bag              │
  │     Reads segment from partition_map          │
  │     Executes nodes (LLM, API, operator, etc.) │
  │     seal_state_bag(bag, result, 'sync')       │
  │     → USC pipeline → optimized state         │
  │     Returns { state_data, next_action }       │
  │                                             │
  │  3. ResultSelector maps $.Payload            │
  │     $.Payload.state_data → $.state_data.bag  │
  │     $.Payload.next_action → $.state_data.next│
  │                                             │
  │  4. Choice: next_action == "CONTINUE"?       │
  │     YES → goto 1 (increment loop)            │
  │     NO  → route to COMPLETE/FAILED/HITP      │
  └─────────────────────────────────────────────┘
```

### 6.3 Distributed Execution (BATCHED / MAP_REDUCE)

When `total_segments > 10` (BATCHED) or `> 100` with high independence (MAP_REDUCE):

```
ASL Map State:
  ItemsPath: "$.state_data.bag.segment_manifest"
  ↓
  Each item: { segment_id, s3_config_path, execution_order }
  ↓
  MaxConcurrency: N (determined by BATCHED/MAP_REDUCE strategy)
  ↓
  Each iteration → RunSegment Lambda
  ↓
AggregateDistributed Lambda:
  seal_state_bag(base, list_of_results, 'aggregate_distributed')
  → flatten_result([result0, result1, ...])
    - Sort by execution_order
    - Extract last successful state
    - Compute distributed_chunk_summary
  → merge_logic → optimize → next_action
```

---

## 7. Merkle DAG State Versioning

**File:** `backend/src/services/state/state_versioning_service.py`

Every segment execution creates an **immutable state manifest** — a Merkle DAG node that chains state snapshots for rollback, audit, and replay.

### 7.1 Manifest Structure

```
WorkflowManifestsV3 (DynamoDB):
┌──────────────────────────────────────────────────┐
│ manifest_id    : "sha256:abc123..."  (content hash)│
│ workflow_id    : "wf-uuid"                        │
│ execution_id   : "arn:aws:states:..."             │
│ parent_manifest_id : "sha256:prev..."  ← chain    │
│ segment_id     : 3                                │
│ merkle_root    : "sha256:..."  ← root of blocks   │
│ block_ids      : ["sha256:b1", "sha256:b2", ...]  │
│ s3_block_prefix: "manifests/wf-uuid/segment-3/"   │
│ status         : "committed"                      │
│ governance_decision: "APPROVED" / "REJECTED"      │
│ violations     : []                               │
│ timestamp      : 1740000000.0                     │
│ ttl            : 1755000000  (90-day auto-expire)  │
└──────────────────────────────────────────────────┘
```

### 7.2 Content-Addressable Block Storage (S3)

State data is split into **blocks** by field:
```
s3://[WORKFLOW_STATE_BUCKET]/manifests/[workflow_id]/[segment_id]/
  ├── block_current_state_sha256abc.json    (content-addressed)
  ├── block_state_history_sha256def.json
  └── block_workflow_config_sha256ghi.json
```

**Block ID = SHA-256 of content** → identical state produces identical block IDs → deduplication is automatic.

### 7.3 Merkle Root Computation

```python
merkle_root = SHA-256(sorted(block_ids).join(""))
manifest_id = SHA-256(
    workflow_id + parent_manifest_id + merkle_root + str(timestamp)
)
```

This ensures:
- Any change in any field → different block_id → different merkle_root → different manifest_id
- The chain of `parent_manifest_id` links creates an auditable, tamper-evident history
- Two executions with identical state at the same point share block storage (dedup)

### 7.4 Manifest Chain

```
manifest_v0 (init)
    │ parent_manifest_id = None
    ▼
manifest_v1 (segment 0 complete)
    │ parent_manifest_id = manifest_v0.manifest_id
    ▼
manifest_v2 (segment 1 complete)
    │ parent_manifest_id = manifest_v1.manifest_id
    ▼
    ... (Merkle DAG — immutable, content-addressed)
```

Rollback traverses the `parent_manifest_id` chain to find the last `governance_decision = "APPROVED"` manifest.

---

## 8. 2-Phase Commit (2PC)

**Purpose:** Guarantee that S3 blocks and DynamoDB manifest records are atomically consistent — no orphan blocks, no missing manifests.

### 8.1 Protocol Phases

```
Phase 1 — PREPARE (S3 upload with temp tag):
  ┌────────────────────────────────────────────┐
  │  For each block:                           │
  │    S3 PutObject: key=block_{sha256}.json   │
  │    Tagging: status=temp                    │
  │    → If Lambda crashes here, GC DLQ picks  │
  │      up orphan temp blocks after TTL       │
  └────────────────────────────────────────────┘

Phase 2 — COMMIT (DynamoDB TransactWriteItems + S3 tag):
  ┌────────────────────────────────────────────┐
  │  DynamoDB TransactWriteItems:              │
  │    Put: manifest record                    │
  │    ConditionExpression: attribute_not_exists(manifest_id)  │
  │    → Idempotent: second commit is no-op    │
  │                                            │
  │  S3 PutObjectTagging (parallel):           │
  │    tag: status=ready                       │
  │    → GC skips ready blocks                 │
  └────────────────────────────────────────────┘
```

### 8.2 Garbage Collection (GC DLQ)

Orphan `status=temp` blocks (from Lambda crashes in Phase 1) are cleaned up by the GC system:

```
env: GC_DLQ_URL → SQS Dead Letter Queue URL
StateVersioningService.__init__(gc_dlq_url=os.environ.get('GC_DLQ_URL'))
```

The GC service scans for blocks with `status=temp` beyond the grace period and deletes them. This is the reason `gc_dlq_url` is required at construction time — without it, `create_manifest()` raises `RuntimeError("GC DLQ URL is required for 2-Phase Commit")` immediately.

### 8.3 Rollback Orphan Cleanup

When `HARD_ROLLBACK` is triggered (critical governance violation), the abandoned branch's blocks are marked for cleanup:

```python
mark_rollback_orphans(
    rollback_manifest_id=last_safe_manifest["manifest_id"],
    abandoned_branch_root=current_manifest_id,
    grace_period_days=7
)
```

This traverses the abandoned chain from `abandoned_branch_root` to `rollback_manifest_id` and tags all intermediate blocks with a deletion timestamp.

---

## 9. 256KB Payload Defense

AWS Step Functions enforces a **256 KB hard limit** on state data. The kernel handles this through a multi-layer defense:

### 9.1 Defense Layers (USC `optimize_and_offload`)

| Layer | Trigger | Action |
|---|---|---|
| **L1: Field Offload** | Individual field > 30 KB | Move to S3, store `{field}_s3_path` pointer |
| **L2: Full State Offload** | Total state > 100 KB | Serialize entire state to S3, keep `state_s3_path` |
| **L3: History Archive** | `state_history` > 50 entries | Older entries → S3, keep latest 50 |
| **L4: Pointer Bloat** | scheduling_metadata or failed_segments bloat | Summarize + offload |
| **L5: Emergency Array** | State > 150 KB (75% of 200 KB cap) | Emergency offload of `distributed_outputs`, `chunk_results` |

### 9.2 Force-Offloaded Fields

These fields are **always** offloaded to S3 regardless of size (they are structurally large):

```python
force_offload = {
    'workflow_config',   # Workflow definition JSON (can be 100KB+)
    'partition_map',     # Full node-to-segment mapping
    'current_state',     # LLM outputs, intermediate results
    'input',             # Initial user input (can be large)
}
```

### 9.3 S3 Pointer Pattern

When a field is offloaded, the state bag contains:
```json
{
  "workflow_config_s3_path": "s3://analemma-state/wf-uuid/segment-3/workflow_config.json",
  "segment_manifest": [...]   ← NOT offloaded (ASL Map state needs it inline)
}
```

The next Lambda's `open_state_bag()` call returns the bag with the pointer. The Lambda is responsible for hydrating (`StateHydrator.load_from_s3()`) when it needs the full data.

### 9.4 S3 Bucket Priority

All components use the same priority chain to determine the S3 bucket:
```python
bucket = (
    os.environ.get('WORKFLOW_STATE_BUCKET')   # Primary (unified)
    or os.environ.get('S3_BUCKET')            # Secondary (legacy)
    or os.environ.get('SKELETON_S3_BUCKET')   # Tertiary (SAM template default)
)
```

---

## 10. Distributed Execution Strategy

The kernel automatically selects the execution strategy at workflow save time (`partition_workflow_advanced()`):

### 10.1 Strategy Selection

```
total_segments ≤ 10:
    SAFE — Sequential execution, single SFN loop
    ASL: standard Choice/Pass loop

total_segments 10–100:
    BATCHED — ASL Map state with inline ItemsPath
    ASL Map: ItemsPath: "$.state_data.bag.segment_manifest"
    MaxConcurrency: computed from Lambda memory

total_segments > 100 AND independence_ratio > 0.7:
    MAP_REDUCE — ASL Distributed Map with S3 ResultWriter
    ResultWriter: { Bucket: SKELETON_S3_BUCKET, Prefix: "results/" }
    → Results written to S3 manifest, not SFN state
    → AggregateDistributed reads manifest pointer
```

### 10.2 Segment Manifest (Critical for BATCHED/MAP_REDUCE)

The `segment_manifest` is a lightweight array of S3 pointers stored **inline** in the bag (not offloaded), because ASL Map state's `ItemsPath` must be able to reference it directly:

```json
"segment_manifest": [
    { "segment_id": 0, "s3_config_path": "s3://bucket/seg0.json", "execution_order": 0 },
    { "segment_id": 1, "s3_config_path": "s3://bucket/seg1.json", "execution_order": 1 },
    ...
]
```

Each entry is ~200 bytes. For 100 segments this is ~20 KB — well within the inline threshold. The actual segment config (which can be large) lives at the `s3_config_path`.

---

## 11. Governance Layer (Ring 1)

**Files:** `governor_runner.py`, `trust_score_manager.py`, `constitution.py`, `agent_guardrails.py`

The Governor intercepts agent outputs **after execution** and decides whether to allow, escalate, or rollback.

### 11.1 Analysis Pipeline

```
governor_node_runner(agent_id, agent_output, workflow_state, config):
  │
  ├─ 1. Determine Ring Level (from workflow_config.governance.ring_level)
  ├─ 2. Select GovernanceMode (Ring 3/2=OPTIMISTIC, Ring 1/0=STRICT)
  ├─ 3. _analyze_agent_behavior():
  │    ├─ Metric 1: Output size (SLOP detection, default 500KB limit)
  │    ├─ Metric 2: Plan hash change (plan drift)
  │    ├─ Metric 3: Gas fee accumulation (LLM cost budget)
  │    ├─ Metric 4: Retry count (circuit breaker, default 3)
  │    ├─ Metric 5: Prompt injection (TODO: PromptSecurityGuard)
  │    └─ Metric 6: Kernel command forgery (KERNEL_CONTROL_KEYS check)
  │    → AgentBehaviorAnalysis { anomaly_score: 0.0–1.0, violations: [...] }
  │
  ├─ 4. _make_governance_decision():
  │    ├─ anomaly_score ≥ 0.8 → REJECTED
  │    ├─ anomaly_score ≥ 0.5 → ESCALATED (with _kernel interventions)
  │    └─ anomaly_score < 0.5 → APPROVED
  │
  ├─ 5. Optimistic Rollback (if OPTIMISTIC mode + violations):
  │    ├─ KERNEL_COMMAND_FORGERY → TERMINAL_HALT (_kernel_terminate_workflow)
  │    ├─ SLOP / CIRCUIT_BREAKER → HARD_ROLLBACK (find last safe manifest)
  │    └─ PLAN_CHANGE / GAS_FEE → SOFT_ROLLBACK (_kernel_retry_current_segment)
  │
  ├─ 6. _save_governance_audit_log() → DynamoDB GovernanceAuditLog (90-day TTL)
  └─ 7. Return decision dict with _kernel commands merged in
```

### 11.2 Rollback Decision Matrix

```
Violation               Rollback Type    Action
─────────────────────────────────────────────────────────────────
KERNEL_COMMAND_FORGERY  TERMINAL_HALT    Immediate SIGKILL
SECURITY_VIOLATION      TERMINAL_HALT    Immediate SIGKILL
SLOP_DETECTED           HARD_ROLLBACK    Restore last safe manifest
CIRCUIT_BREAKER         HARD_ROLLBACK    Restore + HITP human approval
PLAN_CHANGE_DETECTED    SOFT_ROLLBACK    Retry with agent feedback
GAS_FEE_EXCEEDED        SOFT_ROLLBACK    Reduce parallelism to 5 branches
```

### 11.3 Trust Score Model (EMA-based Asymmetric Recovery)

```
T_new = max(0, min(1, T_old + delta_S - (alpha * A)))

where:
  delta_S  = BASE_SUCCESS_INCREMENT * (1 + EMA_ACCELERATION * streak_ratio)
           = 0.01 * (1 + 2.0 * streak_ratio)   # EMA: accelerated recovery
  alpha    = VIOLATION_MULTIPLIER = 0.5
  A        = anomaly_score (0.0–1.0)

  streak_ratio = recent_successes / recent_total   (last 10 decisions)

Ring Penalty Multipliers:
  Ring 0: 2.0x   Ring 1: 1.5x   Ring 2: 0.8x   Ring 3: 0.5x

Thresholds:
  initial_score = 0.8
  strict_threshold = 0.4  → score < 0.4 forces STRICT mode
```

**Asymmetric recovery benefit:** 5 consecutive successes → `streak_ratio=1.0` → `delta_S=0.03`. Recovery from 0.4→0.8 takes **14 iterations** instead of 40 (65% reduction).

### 11.4 Constitutional AI Clauses

Default constitution (6 articles, all enforced at Ring 1):

| Article | Title | Severity |
|---|---|---|
| 1 | Professional Business Tone | MEDIUM |
| 2 | No Harmful Content Generation | CRITICAL |
| 3 | User Protection Principle (no PII solicitation) | CRITICAL |
| 4 | Transparency Principle | LOW |
| 5 | Security Policy Compliance (no audit bypass) | CRITICAL |
| 6 | No PII Leakage in Text (email, phone, SSN in output) | CRITICAL |

Custom clauses can be added via `workflow_config.governance_policies.constitution[]` with article numbers > 6.

---

## 12. Key Constants & Environment Variables

### 12.1 Environment Variables (SAM Globals)

| Variable | Purpose | Required |
|---|---|---|
| `WORKFLOW_STATE_BUCKET` | Primary S3 bucket for state offloading | Yes (or fallback) |
| `SKELETON_S3_BUCKET` | SAM-managed legacy bucket name | Fallback |
| `WORKFLOWS_TABLE` | DynamoDB table for workflow definitions | Yes |
| `EXECUTIONS_TABLE` | DynamoDB table for execution records | Yes |
| `WORKFLOW_MANIFESTS_TABLE` | DynamoDB table for Merkle manifests | Yes (2PC) |
| `GC_DLQ_URL` | SQS DLQ for orphan block cleanup | Yes (2PC) |
| `GOVERNANCE_AUDIT_LOG_TABLE` | DynamoDB for governance audit trail | Optional (CW fallback) |
| `WORKFLOW_ORCHESTRATOR_ARN` | Standard SFN state machine ARN | Yes |
| `WORKFLOW_DISTRIBUTED_ORCHESTRATOR_ARN` | Distributed SFN state machine ARN | Yes |
| `USE_2PC` | Enable 2-Phase Commit (`"true"`) | Optional (default `"false"`) |
| `AWS_LAMBDA_FUNCTION_MEMORY_SIZE` | Used to calculate parallel S3 workers | Auto-set by Lambda |

### 12.2 USC Size Thresholds

| Constant | Value | Meaning |
|---|---|---|
| `FIELD_OFFLOAD_THRESHOLD_KB` | 30 KB | Individual field offload trigger |
| `FULL_STATE_OFFLOAD_THRESHOLD_KB` | 100 KB | Full state offload trigger |
| `MAX_PAYLOAD_SIZE_KB` | 200 KB | Internal cap (buffer before 256 KB SFN limit) |
| `POINTER_BLOAT_WARNING_THRESHOLD_KB` | 10 KB | Pointer size warning |

---

## 13. Error Conditions & Failure Modes

### 13.1 Critical Failure: SyntaxError in State Versioning

If `state_versioning_service.py` fails to parse (e.g., due to orphaned code from refactoring), the module sets `_HAS_VERSIONING = False`. All 2PC operations silently degrade to no-op. **Symptom:** No manifest records in DynamoDB despite successful workflow executions.

**Prevention:** CI syntax check:
```bash
python -c "import ast; ast.parse(open('state_versioning_service.py').read())"
```

### 13.2 Missing GC DLQ URL

`StateVersioningService.__init__` with `use_2pc=True` but no `gc_dlq_url` → `create_manifest()` always raises `RuntimeError`. Segment execution succeeds but no manifest is created → no rollback capability.

**Fix:** Always pass `gc_dlq_url=os.environ.get('GC_DLQ_URL')` to the constructor.

### 13.3 Circular Import (Cold Start)

Top-level `from src.handlers.core.main import run_workflow` in `segment_runner_service.py` creates a cold-start circular import. Fixed with lazy local imports at two call sites.

### 13.4 Segment Manifest Missing from Bag

If `bag['segment_manifest']` is not set during initialization, ASL Map state `ItemsPath: "$.state_data.bag.segment_manifest"` receives `null` → Map state iterates 0 times → all BATCHED/MAP_REDUCE workflows silently skip execution.

### 13.5 next_run_time Type Mismatch

Scheduled workflows auto-generate `next_run_time` as an ISO 8601 string. Later code casts it to `int()` → `ValueError` → HTTP 400. Fix: generate as Unix timestamp (`calendar.timegm()`).

### 13.6 Trust Score `get_state()` Always Returns None

If `get_state()` method body is accidentally empty (return statement misplaced in adjacent method), all callers receive `None` → trust score history is inaccessible for trend analysis and audit logging.

---

## Appendix A: Data Flow Diagram (Full Kernel Path)

```
User Request
    │
    ▼
API Gateway → Lambda: InitializeStateData
    │  open_state_bag(event) → {} (empty for init)
    │  partition_workflow_advanced() → segment_manifest
    │  build initial bag
    │  seal_state_bag({}, bag, 'init')
    │    └→ USC: flatten(init) → merge(INIT_REQUIRED_METADATA) → optimize → 'STARTED'
    │  return { state_data: {...}, next_action: 'STARTED' }
    ▼
SFN: WorkflowDistributedOrchestrator (starts)
    │
    ▼  [LOOP START]
ASL: IncrementLoopCounter (Direct DynamoDB SDK)
    │  loop_counter += 1 (atomic, no Lambda cold start)
    ▼
Lambda: SegmentRunner
    │  open_state_bag(event) → bag (with execution_id, segment_to_run, etc.)
    │  S3 hydrate: workflow_config_s3_path, partition_map_s3_path
    │  Execute segment nodes (LLM/API/operator)
    │  seal_state_bag(bag, result, 'sync')
    │    └→ USC pipeline → optimized state
    │  StateVersioningService.create_manifest() [if 2PC enabled]
    │    ├─ Phase 1: Upload blocks to S3 (status=temp)
    │    └─ Phase 2: TransactWrite to DynamoDB + tag blocks (status=ready)
    │  return { state_data, next_action }
    ▼
ASL: ResultSelector → maps $.Payload
    │  bag.$: $.Payload.state_data
    │  next_action.$: $.Payload.next_action
    │  ResultPath: $.state_data
    ▼
ASL: Choice (next_action == 'CONTINUE'?)
    ├─ YES → [LOOP START]
    ├─ HITP → WaitForCallback (human-in-the-loop)
    ├─ COMPLETE → FinalizeExecution
    └─ FAILED → ErrorHandler
    ▼
Lambda: Governor (Ring 1, post-execution validation)
    │  _analyze_agent_behavior() → AgentBehaviorAnalysis
    │  _make_governance_decision() → GovernanceDecision
    │  If violations:
    │    TERMINAL_HALT → _kernel_terminate_workflow
    │    HARD_ROLLBACK → _kernel_rollback_to_manifest
    │    SOFT_ROLLBACK → _kernel_retry_current_segment
    │  _save_governance_audit_log() → DynamoDB
    │  _emit_governance_metrics() → CloudWatch structured logs
    ▼
SFN: FinalizeExecution
    │  Update ExecutionsTableV3 (status=COMPLETE/FAILED)
    │  Publish execution result to Kinesis
    └─ Done
```

---

---

## Appendix B: StateViewContext — Ring-based Field Masking

**File:** `backend/src/services/execution/segment_runner_service.py` (FieldPolicyBuilder section)

The `StateViewContext` acts as a **read-proxy** over the state bag. When a Ring 3 agent accesses state fields, it receives a masked view based on field-level policies — not the raw values.

### Field Policy Rules

```python
builder = FieldPolicyBuilder()

# email field: hash at Ring 3 (agent sees SHA256, not actual email)
state_view.set_field_policy("email", builder.hash_at_ring3())

# ssn, password: fully redacted at Ring 2–3 (agent sees "[REDACTED]")
for field in ["ssn", "password"]:
    state_view.set_field_policy(field, builder.redact_at_ring2_3())

# _kernel_* keys: completely hidden above Ring 1 (agent sees nothing)
state_view.set_field_policy("_kernel_*", builder.hidden_above_ring1())
```

### Policy Matrix

| Field | Ring 0–1 (Kernel/Governor) | Ring 2 (Trusted) | Ring 3 (User) |
|---|---|---|---|
| `email` | Raw value | Raw value | SHA-256 hash |
| `ssn`, `password` | Raw value | `[REDACTED]` | `[REDACTED]` |
| `_kernel_*` | Raw value | Hidden | Hidden |
| All other fields | Raw value | Raw value | Raw value |

This enforces **Constitutional Clause 6** (No PII Leakage) at the data access layer, independent of the agent's prompt or output.

---

## Appendix C: ConcurrencyControllerV2 — Load-adaptive Throttling

**File:** `backend/src/services/quality_kernel/concurrency_controller.py`

The ConcurrencyController prevents Lambda concurrency exhaustion during high-parallelism BATCHED/MAP_REDUCE workflows.

### Load Levels

```
LoadLevel.LOW     → Full parallelism allowed
LoadLevel.MEDIUM  → Reduce concurrency by 30%
LoadLevel.HIGH    → Reduce concurrency by 60%
LoadLevel.CRITICAL → Allow only 1 concurrent segment (fall back to SAFE)
```

### Configuration (SegmentRunnerService defaults)

```python
ConcurrencyControllerV2(
    workflow_id="segment_runner",
    reserved_concurrency=200,      # RESERVED_CONCURRENCY env var
    max_budget_usd=10.0,           # MAX_BUDGET_USD env var
    enable_batching=True,
    enable_throttling=True
)
```

The controller is lazily initialized (cold-start safe) and checks the current Lambda service quota before issuing execution permits for each batch.

---

## Appendix D: Key Environment Variables — Runtime Toggles

| Variable | Default | Effect |
|---|---|---|
| `ENABLE_RING_PROTECTION` | `"true"` | Enable/disable Ring 0–3 enforcement |
| `ENABLE_AUTO_SIGKILL` | `"true"` | Auto-terminate on KERNEL_COMMAND_FORGERY |
| `OPTIMISTIC_ROLLBACK_THRESHOLD` | `0.5` | Anomaly score threshold for Optimistic Rollback |
| `USE_2PC` | `"false"` | Enable 2-Phase Commit globally (override per-service) |
| `USE_BATCHING` | `"false"` | Enable BatchedDehydrator (Hot/Warm/Cold tiering) |
| `USE_ZSTD` | `"false"` | Enable Zstd compression (68% vs Gzip 60%) |
| `ZSTD_LEVEL` | `"3"` | Zstd compression level (1–22) |
| `RESERVED_CONCURRENCY` | `200` | Lambda reserved concurrency budget |
| `MAX_BUDGET_USD` | `10.0` | Gas fee hard cap for ConcurrencyController |
| `STATE_SIZE_THRESHOLD` | `180000` | 180 KB soft limit before S3 offload triggers |
| `LOG_LEVEL` | `"INFO"` | USC + StateHydrator logging verbosity |

---

*This document reflects the codebase state as of commit `dee41c9` (2026-02-21).*
*All file paths are relative to `analemma-workflow-os/backend/`.*
