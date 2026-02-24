# Analemma OS

**The Deterministic Runtime for Autonomous AI Agents**

Official Submission — Google Gemini API Developer Competition 2026

[![Google Gemini API](https://img.shields.io/badge/Powered%20by-Gemini%203%20Pro-4285F4.svg?logo=google)](https://ai.google.dev/)
[![License: BSL 1.1](https://img.shields.io/badge/License-BSL%201.1-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB.svg)](https://python.org)

---

## Executive Summary

**Analemma OS** is a serverless operating system kernel that converts probabilistic AI agent loops into deterministic, self-healing cloud processes.

> "AI agents are probabilistic. Infrastructure must be deterministic.
> Analemma bridges this gap with a kernel-level governance layer powered by Gemini 3 Pro."

The core thesis is that a reliable AI agent system requires two orthogonal properties: **intelligent reasoning** (Gemini's domain) and **deterministic state management** (the kernel's domain). Most frameworks conflate the two. Analemma enforces their strict separation through a 4-ring privilege architecture.

---

## The Problem: The AI Trust Gap

| Problem | Engineering Impact | Analemma Solution |
|---|---|---|
| Unpredictable agent loops | Uncapped token cost, runaway execution | Kernel-enforced loop counter + Gas Fee circuit breaker |
| State volatility on failure | Hours of work lost to a single API timeout | Merkle DAG checkpointing — resume from exact failure point |
| Agent command forgery | Agent outputs `_kernel_*` commands to hijack execution | Ring 0/1 reservation — forgery triggers immediate SIGKILL |
| Opaque reasoning | Post-mortem debugging requires log archaeology | Glass-Box callbacks stream real-time reasoning via WebSocket |
| 256KB Step Functions payload limit | Large state causes silent execution failure | Universal Sync Core auto-offloads to S3 with content-addressed blocks |

---

## Architecture: 4-Ring Kernel Model

Analemma OS enforces a privilege hierarchy modeled after CPU protection rings. Agent output is treated as untrusted input — never as an instruction to the kernel.

```
Ring 0 — KERNEL (Immutable System Core)
    |   kernel_protocol.py    seal_state_bag / open_state_bag
    |   universal_sync_core   Unified state merge pipeline
    |   state_versioning      Merkle DAG + 2-Phase Commit
    |
    v
Ring 1 — GOVERNOR (Deterministic Gatekeeper)
    |   governor_runner       Post-execution output validation
    |   governance_engine     Article 1–6 parallel validation (asyncio.gather)
    |   constitution.py       Constitutional AI clause enforcement
    |   prompt_security_guard Capability map + semantic injection shield
    |
    v
Ring 2 — TRUSTED TOOLS
    |   Verified internal services, validated API integrations
    |
    v
Ring 3 — USER AGENTS (Untrusted)
        Gemini-powered agents, external tools
        All output treated as unverified data
```

### Ring Enforcement Invariants

1. **Kernel Control Keys are read-only above Ring 1.** If a Ring 3 agent outputs any reserved `_kernel_*` key, the Governor immediately triggers `TERMINAL_HALT`. There is no bypass path.

2. **State mutations flow through one pipeline.** Every Lambda function — regardless of what it does — exits through `seal_state_bag()`, which passes all data through the Universal Sync Core. There is no side-channel for state writes.

3. **Governance decisions produce immutable audit records.** Every APPROVED / REJECTED / ROLLBACK decision is written to DynamoDB with a 90-day TTL. The Merkle chain makes retroactive tampering detectable.

---

## Kernel Design: "The Great Seal" Protocol (v3.13)

The kernel defines a single I/O contract for every Lambda function in the system.

### Entry: `open_state_bag(event)`

Extracts the actual state dictionary from the raw Step Functions event, regardless of nesting depth. Searches three paths in order: `event.state_data.bag` (standard v3.13), `event.state_data` (flattened), `event` (direct invocation). This decouples Lambda code from ASL `ResultPath` configuration — changing the ASL topology does not require Lambda code changes.

### Exit: `seal_state_bag(base_state, result_delta, action)`

Standardizes the Lambda return value. Internally:

1. Calls the Universal Sync Core with the action type (init / sync / aggregate_branches / merge_callback / etc.)
2. Returns the canonical two-key response:

```python
{
    "state_data": { ...merged, offloaded, optimized... },
    "next_action": "CONTINUE" | "COMPLETE" | "FAILED" | "PAUSED_FOR_HITP"
}
```

The ASL `ResultSelector` then maps `$.Payload.state_data` to `$.state_data.bag`, and the next Lambda receives a clean bag. This design prevents double-wrapping — a subtle failure mode where `state_data` nests inside `state_data` when the protocol is implemented ad hoc.

### Universal Sync Core — The State Pipeline

All state transitions pass through a four-step pipeline:

```
flatten_result(new_result, context)
    Action-specific extraction: unwraps execution_result wrappers,
    sorts distributed Map outputs by execution_order, resolves S3
    ResultWriter manifests to lightweight pointers.

merge_logic(base_state, delta, context)
    Copy-on-Write shallow merge: copies only modified sub-trees
    instead of full deepcopy. List fields use per-field strategies
    (dedupe_append for history, replace for branch topology,
    append for failure records). Control fields always take delta.

optimize_and_offload(state, context)
    Multi-layer 256KB defense:
      L1: Individual fields > 30KB  → S3, store pointer
      L2: Total state > 100KB       → full state to S3
      L3: state_history > 50 entries → archive older entries
      L4: Pointer bloat             → summarize + offload
      L5: State > 150KB (75% of internal 200KB cap) → emergency array offload
    The 200KB internal cap preserves a 56KB margin for SFN metadata overhead
    below the AWS hard limit of 256KB.

_compute_next_action(state, delta, action)
    Centralised routing decision: STARTED / CONTINUE / COMPLETE /
    FAILED / PAUSED_FOR_HITP. No routing logic lives in individual
    Lambda functions.
```

---

## Merkle DAG State Versioning and 2-Phase Commit

Every segment execution produces an immutable state manifest. Manifests are linked by `parent_manifest_id` into a tamper-evident Merkle chain.

### Content-Addressed Block Storage

State data is serialized into blocks keyed by SHA-256 hash of content:

```
s3://[WORKFLOW_STATE_BUCKET]/manifests/[workflow_id]/[segment]/
    block_[sha256_of_content].json
```

Because block IDs are content hashes, identical state fields across executions share the same block. No upload occurs for unchanged fields (content-addressed deduplication). The Merkle root is a three-term SHA-256:

```
B = SHA-256( CONCAT sorted_by_block_id(block.checksum) )
R = SHA-256( config_hash || parent_manifest_hash || B )
```

The inclusion of `config_hash` and `parent_manifest_hash` means identical block sets produce different roots if the workflow configuration or parent chain differs. Any field change propagates to a new `B`, a new root `R`, and a new manifest ID — making retroactive tampering detectable without a separate signature mechanism.

### 2-Phase Commit Protocol

S3 and DynamoDB are kept consistent through an atomic two-phase protocol:

```
Phase 1 — PREPARE
    Upload each block to S3 with tag: status=pending (create_manifest path)
                                   or status=temp    (save_state_delta path)
    If the Lambda crashes here, blocks remain in pending/temp state.
    GC worker (DynamoDB Streams TTL expiry → async Lambda) identifies and
    deletes orphaned blocks. SQS DLQ handles transaction failure orphans.
    No manual cleanup required.

Phase 2 — COMMIT
    DynamoDB TransactWriteItems (atomic, exactly-once):
      - Put manifest record with condition: attribute_not_exists(manifest_id)
      - Idempotent: duplicate commits are no-ops
    Update S3 block tags: status=pending -> status=committed
                       or status=temp    -> status=ready
    GC workers skip committed/ready blocks.
```

### Rollback Capability

The Governor can trigger three rollback types:

| Violation | Rollback | Mechanism |
|---|---|---|
| KERNEL_COMMAND_FORGERY | TERMINAL_HALT | Immediate workflow SIGKILL |
| SLOP / CIRCUIT_BREAKER | HARD_ROLLBACK | Restore last `governance_decision=APPROVED` manifest via DynamoDB GSI query |
| PLAN_DRIFT / GAS_FEE | SOFT_ROLLBACK | Retry current segment with agent feedback message |

HARD_ROLLBACK marks the abandoned branch's blocks for GC with a 7-day grace period. No data loss occurs for approved manifests.

---

## Governance Layer: Constitutional AI at the Kernel Level

The Governor (Ring 1) validates agent output after every segment execution.

### Detection Metrics

| Metric | Method | Threshold |
|---|---|---|
| Output size (SLOP) | `len(json.dumps(output).encode('utf-8'))` | Configurable, default 500KB |
| Plan drift | SHA-256 hash comparison + keyword-overlap Intent Retention Rate | 0.7 IRR minimum |
| Gas fee | Accumulated `total_tokens_used * cost_per_token` | Configurable, default $100 USD |
| Circuit breaker | Per-agent retry counter from workflow state | Configurable, default 3 retries |
| Constitutional articles (1–6) | `GovernanceEngine.verify()` — asyncio.gather parallel validators | CRITICAL → anomaly_score ≥ 0.8 |
| Kernel command forgery | Intersection of agent output keys with `KERNEL_CONTROL_KEYS` set | Zero tolerance |

### Anomaly Score Model

The Governor produces an `anomaly_score` (0.0–1.0) for every agent output. Each violation adds 0.2, capped at 1.0. Two classes of violations trigger hard boosts:

```
anomaly_score = min(len(violations) * 0.2, 1.0)

# Hard boosts (applied after base calculation)
KERNEL_COMMAND_FORGERY      → anomaly_score = 1.0   (immediate SIGKILL)
CONSTITUTIONAL_VIOLATION    → anomaly_score = max(anomaly_score, 0.8)
```

Decision thresholds: `≥ 0.8` → REJECTED, `≥ 0.5` → ESCALATED, `< 0.5` → APPROVED.

### GovernanceEngine — Parallel Article Enforcement

`GovernanceEngine` (`governance_engine.py`) bridges the gap between Article definitions and runtime enforcement. All six articles are validated in parallel via `asyncio.gather()`:

| Article | Validator | Logic |
|---|---|---|
| 1 — Tone | `Article1ToneValidator` | Profanity keyword patterns |
| 2 — Harmful | `Article2HarmfulContentValidator` | `INJECTION_PATTERNS` + harmful keyword set |
| 3 — User Protection | `Article3UserProtectionValidator` | PII solicitation regex (card numbers, passwords) |
| 4 — Transparency | `Article4TransparencyValidator` | Over-certainty phrase detection |
| 5 — Security | `Article5SecurityPolicyValidator` | Reuses `INJECTION_PATTERNS` |
| 6 — PII Leakage | `Article6PIILeakageValidator` | `RetroactiveMaskingService.scan()` + regex fallback |

LOW-severity violations accumulate: 10 or more LOW hits within a single verify call are upgraded to MEDIUM automatically.

### SemanticShield — 3-Stage Injection Defense

`SemanticShield` (`semantic_shield.py`) replaces the static blocklist with a layered normalization-then-match pipeline:

```
Stage 1 — Normalization (all rings)
    Zero-Width Space removal (U+200B/C/D, FEFF, 2060)
    RTL Override removal (U+202E/D)
    Base64 decode attempt (injection extraction)
    Homoglyph normalization (Cyrillic/Greek/Full-width → Latin)

Stage 2 — Pattern Matching (all rings)
    English INJECTION_PATTERNS + 8 Korean injection patterns
    Matching runs on normalized text → bypass-resistant

Stage 3 — Semantic LLM Classification (Ring 2 / Ring 3 only)
    Bedrock Guardrails or Gemini ShieldGemma
    Intent classification: BENIGN | INJECTION | JAILBREAK
    Skipped for Ring 0/1 to reduce inference cost
```

Graceful degradation: if Stage 3 is unavailable, the shield continues with Stage 1+2.

### Ring-fenced Capability Map

Every tool call from any agent is gated by `validate_capability(ring_level, tool_name)` — Default-Deny: any tool not explicitly listed returns `False`.

| Ring | Allowed Tools |
|---|---|
| 0 — KERNEL | All (unrestricted) |
| 1 — DRIVER | `filesystem_read/write`, `subprocess_call`, `network_limited`, `database_*`, `config_*`, `s3_read`, `cache_*`, `event_publish` |
| 2 — SERVICE | `network_read`, `database_query/read`, `cache_read`, `event_publish`, `s3_read`, `config_read` |
| 3 — USER | `basic_query`, `read_only` |

### Constitutional AI Clauses

Six default articles are enforced at Ring 1. CRITICAL-severity violations produce immediate REJECTED decisions:

| Article | Rule | Severity |
|---|---|---|
| 1 | Professional tone | MEDIUM |
| 2 | No harmful content generation | CRITICAL |
| 3 | No PII solicitation (passwords, card numbers) | CRITICAL |
| 4 | Transparency about uncertainty | LOW |
| 5 | No security policy bypass | CRITICAL |
| 6 | No PII in output text (email, phone, SSN detected via regex) | CRITICAL |

Custom clauses can be added per-workflow via `governance_policies.constitution[]` with article numbers above 6.

### Distributed CircuitBreaker (Redis-backed)

`RedisCircuitBreaker` (`retry_utils.py`) provides cross-worker Circuit Breaker consensus using Lua atomic scripts and Redis Pub/Sub:

- **Lua INCR + EXPIRE**: failure count increment and TTL window reset are atomic — no race condition between concurrent workers.
- **Pub/Sub broadcast**: when any worker opens a circuit, all other workers receive the `cb:state_changes` event and update their local state immediately.
- **Graceful degradation**: if Redis is unreachable, the factory falls back to in-memory CircuitBreaker automatically.

```
REDIS_URL set → RedisCircuitBreaker (distributed, cross-worker consensus)
REDIS_URL unset → CircuitBreaker (in-memory, single-worker)
```

---

## Why Gemini 3 Is Architecturally Required

The kernel infrastructure (Step Functions orchestration, Merkle DAG, 2PC, Ring protection) is model-agnostic. The **intelligence layer** is not.

| Capability | Analemma Requirement | Why Gemini 3 |
|---|---|---|
| 2M token context window | Load full execution history for self-healing diagnosis | GPT-4: 128K, Claude 3.5: 200K — insufficient for full-history analysis |
| Sub-500ms time-to-first-token | Real-time kernel scheduling decisions between segments | Higher latency degrades the execution loop to batch, not interactive |
| Native structured output | Zero-parsing overhead for Merkle manifest serialization | Prompt-engineered JSON is brittle at scale |
| Vertex AI context caching | Cache 500K+ token system prompts across executions | Gemini-specific API — no equivalent in other providers |
| Thinking Mode | Expose reasoning chain to Glass-Box callbacks | Native capability; cannot be replicated via prompt engineering |
| Multimodal input | Analyze logs + architecture diagrams + metrics simultaneously | Required for self-healing diagnosis across heterogeneous signals |

The "Gemini Dependency Test": removing Gemini 3 and substituting any other current model degrades or eliminates: (1) full-history self-healing due to context window, (2) context caching economics, (3) Thinking Mode transparency. The kernel continues to function; the intelligence layer does not.

---

## Distributed Execution Strategy

The kernel selects execution strategy automatically at workflow save time:

| Condition | Strategy | ASL Mechanism |
|---|---|---|
| Total segments <= 10 | SAFE (sequential) | Standard Choice/Pass loop |
| Total segments 10–100 | BATCHED | ASL Map state, ItemsPath: `$.state_data.bag.segment_manifest` |
| Total segments > 100, independence > 0.7 | MAP_REDUCE | ASL Distributed Map with S3 ResultWriter |

`segment_manifest` — a lightweight array of S3 pointers (~200 bytes per segment) — is intentionally excluded from S3 offloading so the ASL Map state can reference it inline via `ItemsPath`. Full segment configuration lives at the referenced S3 path.

---

## Key Innovations

### Mission Simulator (Chaos Engineering)
Built-in stress-testing suite simulating failure scenarios including network partitioning, LLM hallucination injection, token exhaustion, rate limiting (429 responses), and cold start cascades. Validates system behavior under adversarial conditions before production deployment.

### Time Machine (State Recovery)
Every agent step is checkpointed via the Merkle DAG. Execution can be resumed from the exact failure point by restoring the last approved manifest. Supports point-in-time state comparison between any two manifests.

### Self-Healing via Gemini Context
When failures occur, Gemini analyzes the full execution context within its 2M token window. The kernel injects structured failure context, and Gemini returns a targeted recovery action — not a generic retry. Example:

```
"Previous 3 attempts failed due to JSON parsing errors.
 Injecting structured output enforcement schema into next prompt."
```

### Glass-Box Observability
Real-time WebSocket streaming exposes the agent's Thinking Mode trace at each decision point. Production debugging without reproduction. Trace correlation is maintained across distributed Map state segments via shared `execution_id`.

---

## Tech Stack

| Category | Technology |
|---|---|
| AI Core | Gemini 3 Pro (Orchestration, Reasoning, Self-Healing) |
| Runtime | Python 3.12 |
| Orchestration | AWS Step Functions (portable to Cloud Workflows) |
| Compute | AWS Lambda arm64 / Graviton2 (portable to Cloud Run) |
| State Storage | S3 (blocks) + DynamoDB (manifests) |
| Real-time | WebSocket API Gateway |
| IaC | AWS SAM / CloudFormation |

---

## Deployment Prerequisites

Analemma OS is a production-grade serverless kernel. Deployment requires pre-existing AWS infrastructure and credentials for multiple external services. There is no single-command quick start.

**AWS Infrastructure (must exist before deployment):**

| Resource | Purpose |
|---|---|
| Cognito User Pool | API authentication — `CognitoIssuerUrl`, `CognitoAudience` required |
| ECR Image URI | Lambda container image built via `docker build` and pushed separately |
| S3 Bucket | Workflow state block storage |
| DynamoDB Tables | Manifest store, governance audit log, trust score metrics |
| SQS Dead Letter Queue | GC worker for orphaned temp blocks (2PC Phase 1 recovery) |

**API Keys (all required at deploy time):**

| Key | Environment Variable |
|---|---|
| Gemini API key | `GEMINI_API_KEY` |
| OpenAI API key | `OPEN_AI_API_KEY` |
| Anthropic API key | `ANTHROPIC_API_KEY` |
| Google AI API key | `GOOGLE_API_KEY` |

**Deployment:**

```bash
git clone https://github.com/skwuwu/Analemma-Os.git
cd Analemma-Os/analemma-workflow-os/backend

# Build Lambda container image and push to ECR first
docker build -t analemma-backend .
# aws ecr ... (tag and push)

# Deploy SAM stack with required parameters
sam build
sam deploy --guided \
  --parameter-overrides \
    CognitoIssuerUrl=<your-cognito-issuer-url> \
    CognitoAudience=<your-cognito-audience> \
    BackendLambdaImageUri=<your-ecr-image-uri> \
    GeminiApiKey=<your-gemini-key> \
    OpenAiApiKey=<your-openai-key> \
    AnthropicApiKey=<your-anthropic-key> \
    GoogleApiKey=<your-google-key>
```

See the [Installation Guide](analemma-workflow-os/docs/installation.md) for the complete deployment walkthrough, IAM permissions, and optional parameters (Fargate async worker, VPC configuration, Kinesis streaming).

---

## Documentation

| Document | Description |
|---|---|
| [Architecture Deep-Dive](analemma-workflow-os/docs/architecture.md) | Kernel design, state management, Gemini integration patterns |
| [Kernel Layer Technical Report](analemma-workflow-os/docs/KERNEL_LAYER_TECHNICAL_REPORT.md) | Ring protection, Great Seal protocol, USC pipeline, Merkle DAG, 2PC |
| [API Reference](analemma-workflow-os/docs/api-reference.md) | REST API, WebSocket protocol |
| [Features Guide](analemma-workflow-os/docs/features.md) | Co-design assistant, Time Machine, Mission Simulator |
| [Installation Guide](analemma-workflow-os/docs/installation.md) | Deployment, configuration, environment setup |

---

## Hackathon Context: Foundation vs. New Work

This submission builds on independent prior research (Serverless Agent Kernel architecture) as infrastructure foundation. The Gemini-native intelligence layer was developed for this competition.

| Layer | Description | Status |
|---|---|---|
| Foundation | Step Functions orchestration, S3 state management, Lambda compute, WebSocket | Pre-existing personal research |
| Application | Gemini Scheduler, Self-Healing Engine, Glass-Box Callbacks, Context Caching integration, Thinking Mode visualization | Built for this competition |

The kernel infrastructure is a prerequisite, not the submission. The submission is the demonstration that Gemini 3's specific capabilities — 2M context, Thinking Mode, context caching, native structured output — enable a class of AI agent reliability that is architecturally impossible to achieve with any other currently available model.

---

## Vertex AI Readiness

| Component | Current | GCP Target | Estimated Effort |
|---|---|---|---|
| AI Core | Gemini API (vertexai SDK) | Vertex AI (same SDK) | 2 weeks |
| Compute | Lambda | Cloud Run | 4 weeks |
| Storage | S3 + DynamoDB | Cloud Storage + Firestore | 5 weeks |
| Orchestration | Step Functions | Cloud Workflows | 8 weeks |
| Real-time | API Gateway WebSocket | Firebase + Pub/Sub | 3 weeks |

The Gemini API integration already uses the `vertexai` SDK. Context caching uses `CachedContent` API. Authentication is GCP Service Account. The kernel logic is independent of the infrastructure substrate.

---

## License

**Business Source License 1.1 (BSL 1.1)**

- Free for development, testing, and personal use
- Commercial licensing available on request
- Converts to Apache 2.0 on 2029-01-14

---

*"AI agents are probabilistic. Operating systems are not. Analemma OS is the boundary layer between the two."*
