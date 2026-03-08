# Analemma OS — Architecture Overview

> How the kernel executes workflows, why it runs on serverless, and how Merkle DAG keeps every state transition auditable.

---

## 1. Core Execution Logic

A workflow in Analemma OS is a directed graph of **segments**, where each segment contains one or more computational nodes (LLM calls, tool invocations, human approvals). The entire execution is orchestrated by a single AWS Step Functions (SFN) state machine that follows a deterministic loop:

```
InitializeStateBag → ExecuteSegment → EvaluateNextAction ─┐
                          ▲                                │
                          └── CONTINUE ────────────────────┘
                              COMPLETE → PrepareSuccessOutput
                              PAUSED   → WaitForCallback (HITP)
                              FAILED   → NotifyAndFail
```

**Initialize** resolves the workflow definition, partitions it into segments, computes a distributed execution strategy, and creates the first Merkle manifest. **ExecuteSegment** is a Lambda that processes one segment at a time — loading the state bag, running nodes, then sealing the result. **EvaluateNextAction** is a pure ASL Choice state that routes on the `next_action` field: `CONTINUE` loops back, `COMPLETE` exits, `PAUSED_FOR_HITP` parks execution until a human responds via `SendTaskSuccess`.

The data contract between SFN and Lambda is fixed by a ResultSelector:

```json
{ "bag.$": "$.Payload.state_data", "next_action.$": "$.Payload.next_action" }
```

Every Lambda receives the flat state bag as its event and returns `{state_data, next_action}`. This invariant means the orchestrator never interprets application logic — it only routes on a single string field.

---

## 2. Why Serverless

The fundamental constraint is **unbounded execution time**. A workflow with 500 segments, each invoking an LLM that takes 5–30 seconds, can run for hours. Traditional server architectures require long-lived connections and dedicated memory. Serverless solves three problems simultaneously:

**Payload isolation.** Each segment runs in a fresh Lambda invocation with its own 10GB memory ceiling. A segment that processes 50MB of LLM context does not compete with the next segment's memory. After invocation, the memory is released — there is no cumulative leak.

**Cost proportionality.** A 500-segment workflow with HITP (Human-in-the-Loop) pauses may span days of wall-clock time but only consume minutes of actual compute. Step Functions charges per state transition ($0.025/1000), not per second of existence. A paused workflow waiting for human approval costs exactly $0.

**Elastic parallelism.** Distributed Map mode fans out up to 10,000 concurrent Lambda invocations for independent segments. No capacity planning, no thread pool tuning — the ceiling is the AWS account's Lambda concurrency quota.

The trade-off is the **256KB SFN payload limit**. This is where the Zero-Gravity architecture comes in: the state bag is split into a Control Plane (~2KB of IDs, counters, and S3 pointers) that stays in the SFN payload, and a Data Plane (workflow configs, LLM responses, execution history) that lives in S3. The `SmartStateBag` lazily hydrates data-plane fields from S3 only when accessed, and the `BatchedDehydrator` groups changed fields by temperature (HOT/WARM/COLD) before compressing and uploading them back. The SFN payload never exceeds a few kilobytes regardless of how large the actual state grows.

---

## 3. Merkle DAG: How It Works and Why

Every segment transition produces a **manifest** — a lightweight record that captures what changed, links to the content blocks in S3, and chains back to the previous manifest via its parent hash.

```
Manifest N-1                    Manifest N
┌──────────────────┐            ┌──────────────────┐
│ manifest_id      │◄───────────│ parent_hash      │
│ manifest_hash    │            │ manifest_id      │
│ config_hash      │            │ manifest_hash    │
│ blocks: [        │            │ blocks: [        │
│   {block_id,     │            │   {block_id,     │
│    s3_path,      │            │    s3_path,      │
│    fields}       │            │    fields}       │
│ ]                │            │ ]                │
└──────────────────┘            └──────────────────┘
```

**Content-addressable storage.** Each state field delta is serialized, Gzip-compressed, and stored in S3 at a path derived from its SHA-256 hash: `merkle-blocks/{workflow_id}/{hash[:2]}/{hash}.json`. Two segments that produce identical output for a field will reference the same block — automatic deduplication.

**Tamper evidence.** The manifest hash is computed over all block hashes plus the parent hash. Modifying any historical state field changes its block hash, which changes the manifest hash, which breaks the chain for every subsequent manifest. This is the same integrity guarantee as a Git commit graph.

### 2-Phase Commit with Parallel I/O

Block persistence uses a three-phase transactional protocol, designed to separate concerns that fail independently:

**Phase 1 — Parallel S3 PUT.** All delta field blocks are compressed and uploaded concurrently via `ThreadPoolExecutor` (4–32 workers scaled by Lambda memory). The serialization and compression step (CPU-bound, GIL-held) runs sequentially to produce upload descriptors, then the I/O-bound S3 PUTs fan out in parallel. For 10 delta fields, this reduces Phase 1 wall-clock time from ~200ms (sequential) to ~30ms (parallel).

**Phase 2a — Atomic DynamoDB commit.** A single `TransactWriteItems` call atomically writes the manifest record and increments block reference counts. If the transaction fails, no manifest is created — blocks remain tagged `status=temp` and Background GC cleans them up within 24 hours.

**Phase 2b — Conditional pointer advancement.** The global `latest_manifest_id` pointer in WorkflowsTableV3 is updated via a separate `update_item` with a monotonic guard: `ConditionExpression: latest_segment_id <= :segment_id`. This is deliberately separated from Phase 2a so that a `ConditionalCheckFailedException` — expected when parallel branches race — does not abort block persistence. The blocks and manifest are always persisted; only the pointer advancement is best-effort.

**Phase 3 — Parallel tag flip.** S3 object tags are flipped from `status=temp` to `status=ready` using the same `ThreadPoolExecutor`. Tag flip failures are non-fatal — the manifest in DynamoDB is already committed, and Background GC treats untagged blocks conservatively.

### Parallel Branch Merge (DAG Join)

When Distributed Map fans out N parallel branches, each writes its own independent manifest chain. At the fan-in aggregation point, the system creates a **merge manifest** — a single manifest whose `parent_manifest_id` field references all branch manifests (comma-separated), forming the DAG join node. This provides:

1. **Time Machine.** Any manifest can be loaded to reconstruct the exact state at that segment boundary. Rewind to segment 47, modify the prompt, fork execution from that point.
2. **Auditability.** The merge manifest explicitly records which branch manifests were joined, enabling post-mortem trace of parallel execution.
3. **Garbage collection.** Block reference counting (incremented on creation, decremented on deletion) enables safe cleanup. Blocks with zero references are GC candidates after a 7-day grace period.

---

## 4. Kernel Layer

The kernel operates at Ring 0 — the highest privilege level in the 4-ring protection model. Its two fundamental primitives are **open** and **seal**:

- **`open_state_bag(event)`** extracts the state bag from the SFN event payload, handling three possible injection formats (nested `state_data.bag`, flat `state_data`, or root-level). Every Lambda starts here.
- **`seal_state_bag(base, delta, action)`** is the mandatory exit. It calls `universal_sync_core` to merge the execution result into the base state, triggers `BatchedDehydrator` to offload data-plane fields to S3, and forces the output into the `{state_data, next_action}` shape that the SFN ResultSelector expects.

No Lambda can return data to the orchestrator without passing through `seal_state_bag`. This is the **Great Seal protocol** — the kernel's guarantee that every state transition is merged, dehydrated, and formatted identically regardless of what the segment's application logic did.

The kernel also owns **incremental hashing**. Rather than `SHA-256(json.dumps(entire_state))` at every segment exit, the `SubBlockHashRegistry` divides state fields into temperature-aligned sub-blocks (HOT/WARM/COLD/CONTROL). Only dirty blocks are re-hashed; cold blocks (workflow_config, partition_map) are hashed once at initialization and reused for the entire workflow lifetime. This reduces steady-state hashing from O(total_state) to O(changed_fields).

---

## 5. Governance Layer

Between `open_state_bag` and `seal_state_bag`, every segment output passes through a multi-layered governance pipeline. The system is structured around a 4-ring protection model inspired by CPU privilege rings:

```
Ring 0 — KERNEL         open_state_bag / seal_state_bag / universal_sync
Ring 1 — GOVERNOR       governor_runner / quality_gate / constitution
Ring 2 — TRUSTED TOOLS  verified internal services, validated APIs
Ring 3 — USER AGENTS    LLM agents, ReactExecutor, external tools
```

### Governor (Ring 1) — 6-Check Validation

Every segment that produces agent output is validated by the Governor before the kernel accepts the state merge:

| Check | What is validated | Violation response |
|-------|-------------------|--------------------|
| **Output size** | `len(output) > max_output_size_kb * 1024` | SLOP_DETECTED |
| **Plan drift** | `SHA-256(current_plan) != SHA-256(last_plan)` | PLAN_CHANGE_DETECTED |
| **Gas fee** | `accumulated_llm_cost > max_gas_fee_usd` | GAS_FEE_EXCEEDED |
| **Circuit breaker** | `retry_count > max_retry_count` | CIRCUIT_BREAKER_TRIGGERED |
| **Constitutional** | 6 articles checked in parallel (tone, harm, PII, transparency, security, output PII) | CONSTITUTIONAL_VIOLATION |
| **Kernel forgery** | Agent output contains kernel control keys | KERNEL_COMMAND_FORGERY |

Violations trigger a graduated response:

- **SOFT_ROLLBACK** — Minor violation. Retry the segment with corrective feedback injected into agent context.
- **HARD_ROLLBACK** — Critical violation. Revert to the last safe Merkle manifest. Orphaned blocks enter a 7-day GC grace period.
- **TERMINAL_HALT** — Security violation (e.g., kernel command forgery). Kill the workflow immediately via `SIGKILL`.

### Quality Gate — 2-Stage LLM Output Filter

The Quality Gate provides content-level validation beyond structural governance checks:

**Stage 1 (local, <5ms, $0):** Shannon entropy analysis and SLOP pattern detection produce a `combined_score`. Scores above 0.75 pass immediately. Scores below 0.4 fail immediately. The middle band triggers Stage 2.

**Stage 2 (LLM, ~500ms, ~$0.001):** A lightweight model (Gemini Flash) rates information density on a 1–10 scale. Outputs scoring below 7 are rejected. Stage 2 is budget-capped — after the per-workflow call limit is reached, it is bypassed entirely.

### Speculative Execution — CPU Branch Prediction Analogy

When Stage 1 produces a high-confidence PASS (score >= 0.75), the system starts the next segment immediately without waiting for Stage 2 verification. Stage 2 and Merkle hash verification run in a background thread. If the background check fails, the speculative segment is aborted and state rolls back to the previous manifest.

Safety constraints prevent speculation across dangerous boundaries:
- HITP edges (human approval required)
- REACT segments (autonomous agents)
- Parallel branches
- Side-effectful nodes (webhook, email, database_write, etc.)
- Maximum 1 speculative segment in-flight

### Optimistic Verification — Write-Lock Pattern

Trust Chain verification and LLM execution run in parallel. The LLM result is held in a write-lock until Merkle hash verification passes. If the trust chain detects a hash mismatch, the LLM execution is cancelled immediately (Early Exit), saving both time and tokens. Lambda memory guard: requires >= 1024MB for `ThreadPoolExecutor`; falls back to sequential below that threshold.

---

> [Back to Architecture Deep-Dive](architecture.md) | [Back to Main README](../README.md)
