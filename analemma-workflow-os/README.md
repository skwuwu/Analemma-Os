# Analemma OS
> **The Deterministic Runtime for Autonomous AI Agents**
> *Bridging the gap between probabilistic intelligence and deterministic infrastructure.*

<div align="center">

[![Multi-LLM](https://img.shields.io/badge/LLM-Claude%20%7C%20Gemini%20%7C%20OpenAI-blueviolet.svg)]()
[![AWS Bedrock](https://img.shields.io/badge/AWS-Bedrock%20%2B%20Step%20Functions-FF9900.svg?logo=amazon-aws)](https://aws.amazon.com/bedrock/)
[![Google Vertex AI](https://img.shields.io/badge/Google-Vertex%20AI%20(Gemini)-4285F4.svg?logo=google-cloud)](https://cloud.google.com/vertex-ai)
[![License: BSL 1.1](https://img.shields.io/badge/License-BSL%201.1-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB.svg)](https://python.org)

</div>

---

## Executive Summary

Analemma OS is a **Hyperscale Agentic Operating System** designed to orchestrate complex, long-running AI workflows that exceed the context limits of traditional architectures.

Born from the need to handle massive software engineering tasks (1M+ LOC repositories), Analemma OS introduces the **Hyper-Context State Bag Architecture**, enabling agents to carry infinite memory without crashing serverless payloads.

The system is **model-agnostic at the infrastructure layer** — the kernel (Step Functions, Merkle DAG, 2PC, Ring protection) works with any LLM. The intelligence layer supports **Claude (via Bedrock), Gemini (via Vertex AI), and OpenAI**, with a Model Router that selects the optimal provider per task.

---

## Architecture Overview

### 4-Ring Kernel Protection Model

Modeled after CPU protection rings, every component operates at a defined privilege level:

```
+-----------------------------------------------------------------------+
|  Ring 3 - USER AGENTS (Untrusted)                                     |
|  Claude/Gemini-powered agents, external tools, ReactExecutor          |
|  All output treated as unverified data                                |
|                                                                       |
|  +-------------------------------------------------------------------+|
|  | Ring 2 - TRUSTED TOOLS                                            ||
|  | Verified internal services, validated API integrations            ||
|  |                                                                   ||
|  | +---------------------------------------------------------------+||
|  | | Ring 1 - GOVERNOR (Deterministic Gatekeeper)                  |||
|  | | governor_runner, governance_engine, constitution.py           |||
|  | | prompt_security_guard, AnalemmaBridge (VSM)                   |||
|  | |                                                               |||
|  | | +-----------------------------------------------------------+|||
|  | | | Ring 0 - KERNEL (Immutable System Core)                   ||||
|  | | | kernel_protocol   seal_state_bag / open_state_bag         ||||
|  | | | universal_sync    Unified state merge pipeline             ||||
|  | | | state_versioning  Merkle DAG + 2-Phase Commit              ||||
|  | | +-----------------------------------------------------------+|||
|  | +---------------------------------------------------------------+||
|  +-------------------------------------------------------------------+|
+-----------------------------------------------------------------------+
```

### Distributed SFN Orchestration

```
InitializeStateBag → ExecuteSegment (loop) → EvaluateNextAction → PrepareSuccessOutput
```

Every Lambda invocation enters through `open_state_bag()` and exits through `seal_state_bag()` — The Great Seal protocol ensures no state mutation bypasses kernel control.

---

## Core Innovations

### 1. Zero-Gravity State Bag (Infinite Virtual Memory)
Traditional engines crash with payloads >256KB. Analemma employs a **"Pointer-First"** architecture:
- **Auto-Dehydration**: SmartStateBag with temperature-based field classification (HOT/WARM/COLD)
- **Batched S3 Offload**: Compressed batch pointers replace inline data at L1-L5 defense layers
- **Surgical Hydration**: Agents access only the fields they need via S3 Select
- **Delta-Based Persistence**: Only changed fields are persisted, reducing S3 writes by 70-85%

### 2. Bridge-Governed ReAct Executor
Autonomous agent execution with full governance at every tool call boundary:
- **ReactExecutor**: Claude Sonnet 4 (via Bedrock) runs a ReAct loop with tool_use protocol
- **AnalemmaBridge**: Every tool call passes through `bridge.segment()` for Ring-level governance
- **Atomic Governance**: Parallel tool batches — if ANY tool receives SIGKILL, ALL tools abort
- **Budget Gates**: Token budget checked at 3 points (pre-loop, post-LLM, post-tool)
- **Wall-Clock Timeout**: Hard limit reserves time for seal + S3 offload + serialization

### 3. Speculative Execution Controller
CPU branch-prediction analogy for the governance layer:
- Stage 1 PASS (combined_score >= 0.75) triggers **async background verification** while the next segment starts immediately
- Side-effect guard: NEVER speculate across segments with side-effectful nodes (webhook, email, database_write)
- If background Stage 2 fails, speculative segment is aborted and state rolls back to previous manifest
- Max 1 in-flight speculation — no cascading speculation

### 4. Incremental Dirty-Key Hashing
Instead of `json.dumps(entire_state)` -> SHA-256 on every segment exit:
- **SubBlockHashRegistry**: Temperature-aligned sub-blocks (HOT/WARM/COLD/CONTROL)
- **Cold Block Skip**: workflow_config, partition_map hashed once at init; steady-state hashing is O(hot_fields)
- **Streaming Hash**: `hashlib.update()` releases GIL, enabling multi-threaded sub-block hashing
- **Block-Level Dirty Tracking**: SmartStateBag tracks `_dirty_blocks` for O(1) resolution

### 5. Optimistic Verification (Write-Lock Pattern)
Trust Chain and LLM execution run in parallel:
- LLM result held in Write-Lock until merkle verification passes
- Early Exit on hash mismatch — cancels pending execution
- Lambda Memory Guard: requires >= 1024MB for ThreadPoolExecutor; sequential fallback below

### 6. Distributed Manifest Architecture
Scales to **10,000+ parallel agents** without memory explosion:
- **Manifest-Only Aggregation**: Lightweight `manifest.json` instead of merging results in memory
- **MAP_REDUCE Strategy**: Auto-selected for >100 segments with independence score >0.7
- **Temperature-Aware Merkle Manifest**: Per-tier hashes (hot_hash, warm_hash, cold_hash) in DynamoDB

### 7. The "Time Machine" Runtime
A **Deterministic Operating System** that treats time as a variable:
- **Universal Checkpointing**: Every segment transition creates an immutable Merkle-linked manifest
- **Rewind & Replay**: Jump to any previous state, modify the prompt, fork reality
- **State Diffing**: Visualize exactly what changed between Step T and Step T+1

### 8. Intelligent Instruction Distiller (Self-Learning)
The Kernel learns from every human correction:
- **HITL Diff Analysis**: Gemini analyzes user modifications to extract implicit preferences
- **Weighted Instructions**: Dynamic weights (1.0 to 0.1) based on usage frequency
- **Lazy Distillation**: EventBridge-triggered background processing via Gemini Flash — never blocks the main PASS path
- **Atomic Version Coherence**: DynamoDB TransactWriteItems for instruction + version hash

### 9. Self-Healing Error Recovery
The Kernel acts as a "Senior Engineer" watching over the agents:
- **Error Distillation**: LLM analyzes error logs and distills targeted fix instructions
- **Dynamic Injection**: Fix instructions injected into retry context via kernel-protected `_kernel_inject_recovery` key
- **Sandboxed Advice**: Ring 3 agents cannot write recovery instructions — only the Governor (Ring 1) can

### 10. Glassbox UX (Real-time Transparency)
Full visibility into agent reasoning:
- **Live Thought Streaming**: Agent monologue streamed in real-time via WebSocket
- **MerkleDAG TreeView**: Frontend visualization of state version tree
- **Correction Confirmation HUD**: Real-time feedback on human corrections
- **Audit Panel**: Governance decision history with full traceability

---

## LLM Integration

### Multi-Provider Architecture

The kernel infrastructure is model-agnostic. The intelligence layer supports multiple providers:

| Provider | Models | Use Case | Integration |
|---|---|---|---|
| **Claude (Bedrock)** | Sonnet 4, Haiku 4.5, Opus 4.5 | REACT autonomous agents, complex reasoning | `AnthropicBedrock` via APAC inference profiles |
| **Gemini (Vertex AI)** | 2.0 Flash, 1.5 Pro | Background distillation, self-healing diagnosis, context caching | `google-cloud-aiplatform` SDK |
| **OpenAI** | GPT-4o | Fallback, specific task routing | `openai` SDK |

### Model Router

`model_router.py` dynamically selects the optimal model based on:
1. Semantic intent detection (structure vs. query vs. edit)
2. Token count estimation (triggers Pro vs. Flash selection)
3. Latency requirements (interactive vs. batch)
4. Context caching eligibility (>32K tokens)

### ReactExecutor Configuration

```json
{
  "react_executor": {
    "enabled": true,
    "model_id": "apac.anthropic.claude-sonnet-4-20250514-v1:0",
    "max_iterations": 25,
    "token_budget": 500000,
    "wall_clock_timeout": 240,
    "tool_timeout": 30
  }
}
```

---

## Governance Layer

### Governor (Ring 1) Validation

Every segment output is validated before kernel state merge:

| Metric | Method | Threshold |
|---|---|---|
| Output size (SLOP) | Byte-length check | 500KB |
| Plan drift | SHA-256 hash + Intent Retention Rate | IRR < 0.7 |
| Gas fee | Accumulated `total_tokens * cost_per_token` | $100 USD |
| Circuit breaker | Per-agent retry counter | 3 retries |
| Kernel command forgery | Key intersection with `KERNEL_CONTROL_KEYS` | Zero tolerance |

### SemanticShield (3-Stage Injection Defense)

| Stage | Action | Ring Scope |
|---|---|---|
| 1 — Normalization | Zero-Width Strip, RTL remove, Base64 decode, Homoglyph map | All rings |
| 2 — Pattern Match | English + Korean injection patterns on normalized text | All rings |
| 3 — Semantic LLM | Bedrock Guardrails / Gemini intent classification | Ring 2/3 only |

### Constitutional AI (6 Articles)

| Article | Rule | Severity |
|---|---|---|
| 1 | Professional tone | MEDIUM |
| 2 | No harmful content generation | CRITICAL |
| 3 | No PII solicitation | CRITICAL |
| 4 | Transparency about uncertainty | LOW |
| 5 | No security policy bypass | CRITICAL |
| 6 | No PII in output text | CRITICAL |

---

## Quick Start

### Prerequisites
- **Python** 3.12+, **Node.js** 18+, **Docker** 20+
- **AWS CLI** 2.x, **AWS SAM CLI** 1.100+
- AWS account with Lambda, Step Functions, DynamoDB, S3 access

### Deploy

```bash
git clone https://github.com/skwuwu/Analemma-Os.git
cd Analemma-Os/analemma-workflow-os/backend

python -m venv .venv && source .venv/bin/activate
pip install -r src/requirements.txt

sam build
sam deploy --guided
```

### Run REACT Agent Test

```bash
# Start a REACT autonomous agent via Step Functions
aws stepfunctions start-execution \
  --state-machine-arn "arn:aws:states:<REGION>:<ACCOUNT>:stateMachine:WorkflowDistributedOrchestrator-dev" \
  --input '{
    "ownerId": "test_owner",
    "workflowId": "test_react",
    "idempotency_key": "test_react",
    "workflow_config": {
      "name": "autonomous_agent",
      "segments": [{"id": "seg_0", "type": "REACT", "nodes": []}],
      "react_executor": {"enabled": true, "max_iterations": 5, "token_budget": 100000}
    },
    "partition_map": [{"id": "seg_0", "type": "REACT", "nodes": []}],
    "task_prompt": "Use read_only to echo hello world and give a final answer.",
    "total_segments": 1, "segment_to_run": 0
  }'
```

### Verify

```bash
aws stepfunctions describe-execution --execution-arn <ARN> --query "output" --output text | python -m json.tool
```

---

## Technical Documentation

> Full index: [docs/README.md](docs/README.md)

| Document | Description |
|---|---|
| [Architecture Deep-Dive](docs/architecture-deep-dive.md) | Ring protection model, Great Seal protocol, Merkle DAG, 2PC, governance |
| [Features Guide](docs/guides/features.md) | Co-design Assistant, Time Machine, Mission Simulator, Model Router, REACT Agent |
| [Installation Guide](docs/guides/installation.md) | Deployment, environment configuration, database setup, troubleshooting |
| [API Reference](docs/guides/api-reference.md) | REST API, WebSocket protocol, Task Manager API |
| [Ring Protection](docs/internals/ring-protection.md) | 4-Ring privilege isolation, Great Seal Protocol, security guards |
| [Merkle DAG & Audit](docs/internals/merkle-dag-audit.md) | Content-addressable state versioning, hash pipeline, integrity verification |
| [2PC Implementation](docs/internals/two-phase-commit.md) | Two-phase commit protocol for S3/DynamoDB atomicity |
| [State Management v3.3](docs/internals/state-management-v3.3.md) | Delta-based persistence, 2-Phase Commit, temperature batching |
| [Kernel Layer Report](docs/internals/kernel-layer-report.md) | Ring protection, Great Seal, USC pipeline, Merkle DAG |
| [Local Agent Runner](docs/guides/local-agent-runner.md) | Bridge SDK, Virtual Segment Manager, local agent integration |

---

## Core Logic Navigation

> Direct links to the critical functions/classes an engineer needs to understand the system.
> All paths relative to `backend/src/`.

### Ring 0 — Kernel Protocol

| Function | Location | Purpose |
|---|---|---|
| [`seal_state_bag()`](backend/src/common/kernel_protocol.py#L55) | `common/kernel_protocol.py:55` | Every Lambda exit — standardized state serialization + dehydration |
| [`open_state_bag()`](backend/src/common/kernel_protocol.py#L120) | `common/kernel_protocol.py:120` | Every Lambda entry — extract payload from SFN envelope |
| [`run_workflow()`](backend/src/handlers/core/main.py#L4978) | `handlers/core/main.py:4978` | Top-level workflow execution entry point |

### Segment Execution Pipeline

| Function | Location | Purpose |
|---|---|---|
| [`SegmentRunnerService`](backend/src/services/execution/segment_runner_service.py#L448) | `services/execution/segment_runner_service.py:448` | Core service orchestrating node-by-node execution |
| [`execute_segment()`](backend/src/services/execution/segment_runner_service.py#L3225) | `services/execution/segment_runner_service.py:3225` | Main execution loop (rehydrate → execute → verify → seal) |

### State Management

| Function | Location | Purpose |
|---|---|---|
| [`SmartStateBag`](backend/src/common/state_hydrator.py#L309) | `common/state_hydrator.py:309` | Temperature-aware dict with dirty-block tracking |
| [`get_control_plane()`](backend/src/common/state_hydrator.py#L427) | `common/state_hydrator.py:427` | Extract CONTROL_PLANE_FIELDS — the only keys surviving dehydration |
| [`StateHydrator`](backend/src/common/state_hydrator.py#L458) | `common/state_hydrator.py:458` | Dehydrate (→ S3 batches) / Rehydrate (S3 → SmartStateBag) |

### Merkle DAG & State Versioning

| Function | Location | Purpose |
|---|---|---|
| [`ManifestPointer`](backend/src/services/state/state_versioning_service.py#L60) | `services/state/state_versioning_service.py:60` | Immutable pointer — `parent_hash` links form the DAG chain |
| [`_compute_merkle_root()`](backend/src/services/state/state_versioning_service.py#L835) | `services/state/state_versioning_service.py:835` | `SHA-256(config_hash ‖ parent_hash ‖ blocks_hash)` |
| [`save_state_delta()`](backend/src/services/state/state_versioning_service.py#L1746) | `services/state/state_versioning_service.py:1746` | Delta persistence: parallel S3 upload + incremental Merkle root |
| [`create_manifest_with_consistency()`](backend/src/services/state/eventual_consistency_guard.py#L88) | `services/state/eventual_consistency_guard.py:88` | 2PC: S3 upload → DynamoDB transact → S3 tag confirm |
| [`_verify_still_unreachable()`](backend/src/services/state/merkle_gc_service.py#L410) | `services/state/merkle_gc_service.py:410` | GC TOCTOU defense — conditional delete with reachability re-check |

### Incremental Hashing

| Function | Location | Purpose |
|---|---|---|
| [`SubBlockHashRegistry`](backend/src/common/hash_utils.py#L258) | `common/hash_utils.py:258` | Temperature-aligned sub-blocks (HOT/WARM/COLD/CONTROL) |
| [`compute_incremental_root()`](backend/src/common/hash_utils.py#L282) | `common/hash_utils.py:282` | O(dirty_blocks) Merkle root — unchanged blocks reuse previous hash |

### Quality Gate & Speculative Execution

| Function | Location | Purpose |
|---|---|---|
| [`QualityGate.evaluate()`](backend/src/services/quality_kernel/quality_gate.py#L495) | `services/quality_kernel/quality_gate.py:495` | Full Stage 1 + Stage 2 pipeline |
| [`evaluate_stage1_only()`](backend/src/services/quality_kernel/quality_gate.py#L818) | `services/quality_kernel/quality_gate.py:818` | Fast path: <5ms, $0 — entropy + slop scoring only |
| [`should_speculate()`](backend/src/services/execution/speculative_controller.py#L192) | `services/execution/speculative_controller.py:192` | Default-Deny gate: all nodes must be in SPECULATABLE allowlist |
| [`begin_speculative()`](backend/src/services/execution/speculative_controller.py#L242) | `services/execution/speculative_controller.py:242` | Snapshot state + start background verification thread |
| [`check_abort()`](backend/src/services/execution/speculative_controller.py#L337) | `services/execution/speculative_controller.py:337` | Next segment entry: COMMITTED → continue, ABORTED → rollback |

### Bridge & REACT Agent

| Function | Location | Purpose |
|---|---|---|
| [`AnalemmaBridge.segment()`](backend/src/bridge/python_bridge.py#L231) | `bridge/python_bridge.py:231` | Ring-level governance gate for every tool call |
| [`ReactExecutor`](backend/src/bridge/react_executor.py#L143) | `bridge/react_executor.py:143` | Autonomous agent — Claude Sonnet 4 via Bedrock with tool_use |
| [`ReactExecutor.run()`](backend/src/bridge/react_executor.py#L274) | `bridge/react_executor.py:274` | Main REACT loop: LLM → tool dispatch → budget check → repeat |

---

## Project Structure

```
analemma-workflow-os/
  backend/
    src/
      bridge/                  # Bridge SDK for agent governance
        python_bridge.py       # AnalemmaBridge — Ring-level governance
        react_executor.py      # REACT autonomous agent executor
        virtual_segment_manager.py  # FastAPI loop virtualization
      handlers/core/           # Lambda handlers
      handlers/governance/     # Governor runner
      services/execution/      # Segment runner, speculative controller, optimistic verifier
      services/quality_kernel/ # Quality gate, kernel middleware, slop detector
      services/state/          # State versioning, merkle GC, eventual consistency
      common/                  # Hash utils, state hydrator, kernel protocol
    template.yaml              # SAM template (78 Lambda functions, 23 DynamoDB tables)
  frontend/
    apps/web/src/
      components/              # React components
      hooks/                   # Custom hooks
      lib/                     # API clients, stores, utilities
  tests/
    backend/                   # Unit + benchmark tests
  docs/                        # Technical whitepapers
```

---

## Mission Simulator (Chaos Engineering)

Built-in stress-testing suite for production validation:
- **Network Blackouts**: S3/API failure simulation
- **LLM Hallucinations**: Malformed response injection
- **Time Machine Stress**: 100+ save/restore cycles for consistency verification
- **Payload Pressure**: 10MB data injection for L1-L5 offload cascade
- **Concurrent Load**: Parallel execution up to 50 workers

---

## Built Under Constraints

This project was architected and developed during active military service. With severely limited access to development environments and intermittent network connectivity, I could not focus on building a flashy UI. Instead, I poured every available second into engineering the most robust, crash-proof kernel possible.

- **Engineering over Aesthetics**: I deprioritized the frontend to build the Mission Simulator — a chaos engineering tool designed to validate the system against the harsh realities of production.
- **Analemma OS is the result of focusing on the 'Core' when everything else was stripped away.** It is not just an app; it is a testament to the belief that a solid foundation defines the height of the skyscraper.

<div align="center">
  <sub>Built with determination</sub>
</div>
