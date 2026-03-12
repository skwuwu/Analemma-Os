# Analemma OS — Hybrid Architecture & Loop Virtualization
## Feasibility Report & Concrete Implementation Plan

**Created:** 2026-02-22
**Last Updated:** 2026-02-23 (Feedback incorporated: Polling Scalability, ZKP Storage, Memory Ghosting, Optimistic Commitment, Recovery Instruction, Audit over Score, Reordering Buffer, Hybrid Interceptor, shared_policy.py Single Source of Truth, BridgeRingLevel IntEnum, Redis AuditRegistry TTL, Ring 3 is_optimistic_report enforcement, Policy Sync endpoint, ZWS/Homoglyph normalization, TypeScript Bridge SDK — v1.3.0)
**Target Plans:** Plan A (B2B Hybrid Local+Cloud) / Plan B (Loop Virtualization Bridge SDK)
**Document Scope:** Feasibility assessment · Technical risk analysis · Phased implementation plan

---

## Table of Contents

1. [Document Purpose and Scope](#1-document-purpose-and-scope)
2. [Proposal Comparison Summary](#2-proposal-comparison-summary)
3. [Plan A — B2B Hybrid Architecture](#3-plan-a--b2b-hybrid-architecture)
   - 3.1 Suitability Assessment
   - 3.2 Technical Feasibility
   - 3.3 Concrete Implementation Plan
   - 3.4 Business Model Design
   - 3.5 Risks and Mitigation
4. [Plan B — Loop Virtualization / Bridge SDK](#4-plan-b--loop-virtualization--bridge-sdk)
   - 4.1 Suitability Assessment
   - 4.2 Technical Feasibility
   - 4.3 Enhanced ABI Specification
   - 4.4 Concrete Implementation Plan (Including Hybrid Interceptor)
   - 4.5 Kernel Integration: VirtualSegmentManager (Including Reordering Buffer)
   - 4.6 Risks and Mitigation
5. [Unified Architecture — Combining Both Plans](#5-unified-architecture--combining-both-plans)
6. [Implementation Roadmap](#6-implementation-roadmap)
7. [Conclusion and Recommendations](#7-conclusion-and-recommendations)

---

## 1. Document Purpose and Scope

This document provides a **candid technical review** of the following two expansion directions for Analemma OS.

| Plan | Core Idea |
|------|-----------|
| **Plan A** | Separate a local engine (Local Agent) from the AWS SFN control plane to implement a B2B hybrid deployment model |
| **Plan B** | Force-map an autonomous agent's unstructured loops (Thought-Action-Observation) to deterministic segments of the Analemma kernel |

Evaluation criteria: **Technical Feasibility (0–10)**, **Implementation Complexity (0–10, higher = harder)**, **Business Value (0–10)**

---

## 2. Proposal Comparison Summary

| Item | Plan A (Hybrid) | Plan B (Loop Virtualization) |
|------|-----------------|------------------------------|
| Technical Feasibility | **8 / 10** | **7 / 10** |
| Implementation Complexity | 6 / 10 (Medium-High) | 8 / 10 (High) |
| Business Value | **9 / 10** | 8 / 10 |
| Existing Code Reuse Rate | High (kernel_protocol, USC reused as-is) | Medium (GovernanceEngine, SemanticShield reused) |
| Solo Implementation Feasible | Yes | Yes |
| Recommended Priority | **1st Priority** | 2nd Priority (After Plan A completion) |

**Executive Summary:** Plan A is based on a proven pattern (AWS SFN Activity Worker) that can be started immediately, with a clear short-term monetization path. However, note the Polling Scalability limits and the S3 security paradox (resolved via ZKP Storage). Plan B is conceptually excellent but faces Memory Ghosting (resolved via State Rehydration), latency compounding (mitigated via Optimistic Commitment), and Hallucination Loop (requires mandatory Recovery Instructions) challenges. Pursue in parallel with a "Plan A = Revenue, Plan B = Authority" strategy.

---

## 3. Plan A — B2B Hybrid Architecture

### 3.1 Suitability Assessment

#### Market Fit

In the B2B enterprise market, the "data stays on-premises, governance in the cloud" model is an already proven sales strategy.

| Comparable Example | Architecture |
|-----------|------|
| HashiCorp Vault | Local secrets storage + Cloud policy management |
| Datadog Agent | Local metrics collection + Cloud analytics |
| GitHub Actions Self-hosted Runner | Local execution container + GitHub orchestration |
| **Analemma OS (Proposed)** | Local agent execution + AWS SFN governance |

Analemma's proposal follows a hybrid SaaS pattern validated in the existing market. In particular, the security guarantee that sensitive data never reaches the cloud is a powerful sales point in **GDPR/privacy regulation** environments.

#### Alignment with Current Architecture

Analemma's `open_state_bag` / `seal_state_bag` protocol already decouples Lambda from storage by design. By porting this contract identically to a local process, the local engine can run without any redesign.

**Current:**
```
Lambda (Cloud) → open_state_bag → [Execute] → seal_state_bag → SFN
```

**After Plan A:**
```
Lambda (Cloud) → SFN Activity Task Token issuance
                ↓
Local Agent (On-Premises) → open_state_bag → [Execute] → seal_state_bag → SendTaskSuccess
                ↓
SFN → Proceed to next step
```

Minimal code changes: `open_state_bag` / `seal_state_bag` function signatures reused without modification.

---

### 3.2 Technical Feasibility

#### Core Mechanism: AWS SFN Activity Worker

AWS Step Functions Activity is a production-proven long-poll-based distributed worker pattern.

| Item | Spec |
|------|------|
| Task Token validity | Up to 1 year |
| Heartbeat interval | Configurable (recommended: 30–60 seconds) |
| Polling latency | Up to 60 seconds (Long-Poll, typically 1–5 seconds) |
| Concurrent workers | Unlimited (horizontally scalable per ActivityARN) |
| Security | IAM Role-based authentication, only outbound from local → AWS required |

**Advantage:** No inbound port opening required on local machines (no firewall issues)
**Disadvantage:** ~1–3 second minimum latency due to polling overhead

#### SFN Activity vs HTTP Task Comparison

| Item | Activity Worker (Polling) | HTTP Task (Push) |
|------|--------------------------|-----------------|
| Local inbound port | Not required ✅ | Required (VPN or public URL) |
| Latency | 1–5 seconds | < 500ms |
| Implementation complexity | Low ✅ | High (TLS auth, ngrok, etc.) |
| Enterprise network compatibility | High ✅ | Low (firewall issues) |
| Recommendation | **B2B enterprise 1st choice** | Suitable for individual developers |

**Conclusion:** B2B enterprise deployment adopts the SFN Activity Worker approach.

#### ⚠️ Practical Bottleneck: Polling Scalability

> **Key Risk:** If a client runs hundreds of local agents simultaneously, they may hit the `GetActivityTask` API Rate Limit.

Actual limits of AWS `GetActivityTask`:
- Account-wide limit of approximately **200 req/s** (us-east-1, varies by region)
- 1 worker = 1 long-poll per 60 seconds → 100 workers = ~1.67 req/s (no problem)
- **1,000+ workers** = 16.7 req/s → Still below the limit, but burst scenarios can trigger ThrottlingException

**Mitigation Strategy — 2-Phase Scaling Plan:**

| Scale | Strategy | Architecture |
|------|------|------|
| Workers < 500 | Direct SFN Activity connection | Use default implementation as-is |
| Workers 500–5,000 | **Analemma Relay (SQS-based)** | SFN → SQS → Local workers (Fan-out) |
| Workers 5,000+ | SQS + FIFO Queue per Region | Multi-region distribution |

**Analemma Relay Architecture (SQS-based contingency plan):**

```
SFN (Work distributor)
  → SQS Standard Queue (analemma-work-queue)
      → Local Worker A: SQS ReceiveMessage (Long-Poll, up to 20 seconds)
      → Local Worker B: SQS ReceiveMessage (Long-Poll)
      → Local Worker N: SQS ReceiveMessage (Long-Poll)
  ← SendTaskSuccess(taskToken, result)  ← Workers report directly to SFN
```

SQS supports much higher throughput than `GetActivityTask` (tens of thousands per second), eliminating the bottleneck at scale. In this architecture, the Task Token is included in the SQS message body, and the pattern where workers call `SendTaskSuccess` directly after completing work remains unchanged.

**Implementation Principle:** Start with direct SFN Activity connection in Phase 0–1, and migrate to SQS Relay when the actual worker count at a client exceeds 500. Both architectures are designed so that only the Transport layer needs to be swapped without changing the `LocalAgentWorker` interface.

---

### 3.3 Concrete Implementation Plan

#### Step 1: ASL Modification — Insert Activity Task Node

Add a `LOCAL_EXECUTION` state to the existing SFN ASL.

```json
{
  "LocalExecutionState": {
    "Type": "Task",
    "Resource": "arn:aws:states:us-east-1:ACCOUNT_ID:activity:analemma-local-agent",
    "Parameters": {
      "segment_id.$": "$.state_data.bag.current_segment_id",
      "workflow_id.$": "$.state_data.bag.workflow_id",
      "agent_config.$": "$.state_data.bag.agent_config",
      "state_s3_pointer.$": "$.state_data.bag.state_s3_pointer"
    },
    "HeartbeatSeconds": 60,
    "TimeoutSeconds": 3600,
    "ResultPath": "$.state_data.bag.local_result",
    "Next": "GovernorValidationState"
  }
}
```

**Design Principle:** Local execution results are returned to SFN only as S3 pointers. Sensitive data never goes directly to the cloud.

#### Step 2: Local Agent Worker Implementation

```python
# analemma_local_agent/worker.py
import boto3
import json
import threading
import logging
from src.kernel.kernel_protocol import open_state_bag, seal_state_bag

logger = logging.getLogger(__name__)

ACTIVITY_ARN = "arn:aws:states:us-east-1:ACCOUNT_ID:activity:analemma-local-agent"


class LocalAgentWorker:
    """
    AWS SFN Activity Worker — Analemma Local Engine Main Loop

    Flow:
      1. GetActivityTask (Long-Poll, up to 60 second wait)
      2. On task receipt, acquire Task Token + Input
      3. Execute local kernel (open_state_bag → agent execution → seal_state_bag)
      4. Upload result to S3 and return only the pointer via SendTaskSuccess
      5. Heartbeat thread prevents timeout
    """

    def __init__(self, worker_name: str = "analemma-local-worker"):
        self.sfn = boto3.client("stepfunctions")
        self.s3 = boto3.client("s3")
        self.worker_name = worker_name
        self._shutdown = threading.Event()

    def run(self):
        logger.info(f"[LocalAgent] Worker '{self.worker_name}' started. Polling...")
        while not self._shutdown.is_set():
            self._poll_and_execute()

    def _poll_and_execute(self):
        try:
            response = self.sfn.get_activity_task(
                activityArn=ACTIVITY_ARN,
                workerName=self.worker_name
            )
        except Exception as e:
            logger.error(f"[LocalAgent] Polling error: {e}")
            return

        if "taskToken" not in response or not response["taskToken"]:
            return  # No task — continue polling

        token = response["taskToken"]
        raw_input = json.loads(response["input"])

        # Start heartbeat thread (30-second interval)
        heartbeat = threading.Thread(
            target=self._heartbeat_loop, args=(token,), daemon=True
        )
        heartbeat.start()

        try:
            # Execute local kernel
            state = open_state_bag(raw_input)
            result_delta = self._run_local_agent(state)
            sealed = seal_state_bag(state, result_delta, action="local_execute")

            # Offload result to S3, return only the pointer
            s3_pointer = self._upload_result_to_s3(sealed, raw_input["workflow_id"])

            self.sfn.send_task_success(
                taskToken=token,
                output=json.dumps({"state_s3_pointer": s3_pointer, "status": "SUCCESS"})
            )
        except Exception as e:
            logger.error(f"[LocalAgent] Execution failed: {e}")
            self.sfn.send_task_failure(
                taskToken=token,
                error=type(e).__name__,
                cause=str(e)[:256]
            )

    def _heartbeat_loop(self, token: str, interval: int = 30):
        while not self._shutdown.is_set():
            try:
                self.sfn.send_task_heartbeat(taskToken=token)
            except Exception:
                break
            self._shutdown.wait(interval)

    def _upload_result_to_s3(self, sealed_state: dict, workflow_id: str) -> str:
        import hashlib
        content = json.dumps(sealed_state, sort_keys=True).encode()
        key = f"local-results/{workflow_id}/{hashlib.sha256(content).hexdigest()}.json"
        bucket = self._get_state_bucket()
        self.s3.put_object(Bucket=bucket, Key=key, Body=content)
        return f"s3://{bucket}/{key}"

    def _get_state_bucket(self) -> str:
        import os
        return os.environ.get("WORKFLOW_STATE_BUCKET", "analemma-state")

    def _run_local_agent(self, state: dict) -> dict:
        """
        Actual local agent execution — can access filesystem, local processes, etc.
        Override point: inject client-specific custom logic
        """
        raise NotImplementedError("Subclass and implement _run_local_agent()")
```

#### Step 3: Local Agent Packaging — 3 Deployment Formats

| Format | Target | Advantages | Disadvantages |
|------|------|------|------|
| **Docker Image** | Enterprises with DevOps teams | Environment isolation, version pinning | Docker installation required |
| **pip Package** (`analemma-agent`) | Python developers | Instant install via `pip install` | Python environment dependency |
| **Single Binary** (PyInstaller) | PC deployment in non-dev departments | No dependencies | Complex build, difficult updates |

**Recommendation:** Docker first, pip package in parallel.

#### Step 3-b: Zero-Knowledge Storage — MinIO / On-Premises Storage Support

> **Security Paradox Fix:** The original design states "sensitive data never reaches the cloud," but if `_upload_result_to_s3()` uses AWS S3, the data ends up in the cloud anyway. The solution for true hybrid is a **Zero-Knowledge Governance architecture**.

**Goal:** No actual data is ever transmitted to the Analemma kernel (AWS); only **content hashes (SHA-256)** are transmitted.

```
Local execution result
  ↓
Client internal storage (MinIO / NFS / On-premises S3-compatible)
  ↓ Extract SHA-256 hash only
AWS SFN SendTaskSuccess:
  { "content_hash": "sha256:abc123...", "size_bytes": 42000, "status": "SUCCESS" }
  (Actual data content: not transmitted)
  ↓
AWS Governor Lambda:
  - Can verify integrity via hash (tamper detection)
  - Cannot access data content (Zero-Knowledge)
  - Records only hash in DynamoDB Audit Log
```

**Storage Provider Abstraction Interface:**

```python
# analemma_local_agent/storage.py
from abc import ABC, abstractmethod
import hashlib, json

class StorageProvider(ABC):
    """
    Local storage abstraction — Can implement MinIO, AWS S3, NFS, Azure Blob, etc.
    Always returns only hash and metadata to the kernel (no data content transmitted)
    """
    @abstractmethod
    def store(self, content: bytes, key: str) -> str:
        """Store and return storage URI (for local internal use)"""
        ...

    def store_and_hash(self, data: dict, key: str) -> dict:
        """
        Store data in internal storage and return
        only Zero-Knowledge metadata for the kernel
        """
        content = json.dumps(data, sort_keys=True).encode()
        content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        internal_uri = self.store(content, key)
        return {
            "content_hash": content_hash,
            "size_bytes": len(content),
            "internal_uri": internal_uri,   # Local internal reference (not sent to cloud)
        }


class MinIOProvider(StorageProvider):
    """MinIO / S3-compatible on-premises storage"""
    def __init__(self, endpoint: str, bucket: str,
                 access_key: str, secret_key: str):
        from minio import Minio
        self.client = Minio(endpoint, access_key=access_key,
                           secret_key=secret_key, secure=False)
        self.bucket = bucket

    def store(self, content: bytes, key: str) -> str:
        from io import BytesIO
        self.client.put_object(self.bucket, key, BytesIO(content), len(content))
        return f"minio://{self.bucket}/{key}"


class AWSS3Provider(StorageProvider):
    """Existing AWS S3 — For clients that don't need Enterprise ZKP"""
    def __init__(self, bucket: str):
        import boto3
        self.s3 = boto3.client("s3")
        self.bucket = bucket

    def store(self, content: bytes, key: str) -> str:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=content)
        return f"s3://{self.bucket}/{key}"
```

**`LocalAgentWorker._upload_result_to_s3()` replacement:**

```python
def _upload_result(self, sealed_state: dict, workflow_id: str) -> dict:
    """
    Upload via StorageProvider abstraction.
    Returns Zero-Knowledge metadata only (hash + size).
    """
    key = f"local-results/{workflow_id}/{self._generate_key()}.json"
    return self.storage_provider.store_and_hash(sealed_state, key)
```

Payload transmitted to SFN:
```json
{
  "content_hash": "sha256:3f4a91b...",
  "size_bytes": 18432,
  "status": "SUCCESS"
}
```

The kernel records the hash in the Merkle DAG for **integrity verification** but does not access the data content. Access permissions to the client's on-premises storage do not exist on the Analemma cloud.

**Enterprise Tier Sales Point Update:**

> "Our cloud infrastructure stores only the **SHA-256 fingerprint** of your data. Viewing actual data is technically impossible. This is an architectural guarantee, not just a policy."

```dockerfile
# Dockerfile.local-agent
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY analemma_local_agent/ ./analemma_local_agent/
COPY src/kernel/ ./src/kernel/
# Shared modules: open_state_bag, seal_state_bag, etc.

ENV ACTIVITY_ARN=""
ENV AWS_REGION="us-east-1"
ENV WORKFLOW_STATE_BUCKET=""

CMD ["python", "-m", "analemma_local_agent.worker"]
```

---

### 3.4 Business Model Design

#### Pricing Structure (SaaS + Agent License Hybrid)

```
Tier 1 — Starter (Individual Developers)
  · Cloud control plane: $49/month
  · Local agent: 1 free
  · Support: Community

Tier 2 — Team (Small Businesses)
  · Cloud control plane: $299/month
  · Local agents: Up to 10 (additional $20/month per agent)
  · Support: Email 48h SLA

Tier 3 — Enterprise (Large Corporations, Finance/Healthcare)
  · Cloud control plane: Negotiated (typically $2,000+/month)
  · Local agents: Unlimited (site license)
  · On-Prem SFN alternative (AWS GovCloud or self-hosted Step Functions-compatible engine) option
  · Support: Dedicated CSM + 4h SLA
```

#### Key Sales Message

> "Agents run exclusively within the client's internal network. Only **two types of metadata** are sent to our cloud: 'did the task succeed?' and 'were any security policies violated?' You can use Analemma's governance layer while complying with GDPR, K-ISMS, and medical data regulations."

---

### 3.5 Risks and Mitigation

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Difficulty managing local agent versions | Medium | Auto-update mechanism: check latest version on agent startup → Docker pull or pip upgrade |
| AWS credentials must be stored locally | High | Use AWS IAM Role for EC2/ECS or issue IAM Identity Center SSO tokens (short-lived tokens only) |
| Long-Poll latency (~3 seconds) | Low | Negligible for most agent loops (minute-scale). Provide HTTP Task option for real-time needs |
| Heterogeneous local execution environments | Medium | Docker standardization, fixed `requirements.txt`, `python:3.12-slim` base image |
| Heartbeat failure on network disconnect → SFN timeout | Medium | Set generous HeartbeatSeconds + retry queue (local SQLite checkpointing) |

---

## 4. Plan B — Loop Virtualization / Bridge SDK

### 4.1 Suitability Assessment

#### Strengths of the Concept

The fundamental problem with autonomous agents (AutoGPT, CrewAI, LangGraph, etc.) is that their loops are **opaque and non-deterministic**. Plan B forces these loops into segment-level granularity within the Analemma kernel, providing:

1. **Observability**: Snapshots generated at every TAO (Thought-Action-Observation) cycle
2. **Recoverability**: Restart possible from a specific loop index (Merkle DAG integration)
3. **Governance**: Explicit kernel approval required before each Action

This concept is practical because it operates at the **agent runtime layer**, requiring no changes to the agent's internal LLM prompts or logic.

#### Differentiation from Existing Solutions

| Existing Solution | Analemma Loop Virtualization |
|-------------|------------------------------|
| LangGraph (StateGraph) | Developers must explicitly define the graph |
| AutoGen | Conversation patterns are structured but no kernel-level governance |
| CrewAI | Task-level segmentation but no segment checkpointing |
| **Analemma** | The kernel intercepts and segments regardless of **which framework** the agent uses |

---

### 4.2 Technical Feasibility

#### 5 Key Technical Challenges

**Challenge 1: Memory Ghosting — Fundamental Limitation of State Serialization**

> **Reality Check:** External library objects used by agents — LangChain Memory, Vector Store, Tool Instances, etc. — will fail with **Circular Reference** or **Unserializable Object** errors 99% of the time when serialized to JSON. Attempting to snapshot the entire agent state will fail.

**Wrong Approach (Do not attempt):**

| Method | Result |
|------|------|
| `json.dumps(agent.__dict__)` | `TypeError: Object of type VectorStore is not JSON serializable` |
| `pickle.dumps(agent)` | Security vulnerability, Python version dependent, fails with external file handles |
| Recursive serialization of entire object graph | Circular Reference → `RecursionError` |

**Correct Strategy — State Rehydration:**

> Instead of saving the **entire** agent state, extract only the **Core Context** and **Next Intent**. Heavy objects (Vector Store, LLM client, etc.) are designed to be **Rehydrated (reloaded)** by the agent when it restarts.

```
Included in snapshot (Lightweight Core Context):
  - short_term_memory: Recent N messages (text)
  - current_intent: Purpose of current loop (string)
  - progress_markers: retry_count, step_name, found_flags, etc. (primitive values)
  - token_usage_total: Cumulative token count (number)

Excluded from snapshot (Rehydratable):
  - VectorStore instance → Recreated with same config on restart
  - LLM client → Reconnected via environment variables + API key
  - HTTP sessions, file handles, DB connections → Reconnected
```

**`StateRehydrationMixin` — Mixin for agent base classes:**

```python
# analemma_bridge/python/rehydration.py

class StateRehydrationMixin:
    """
    State Rehydration strategy implementation mixin.
    Agent classes that inherit this get automatic snapshot/restore support.

    Usage:
        class MyAgent(StateRehydrationMixin, BaseAgent):
            rehydratable_fields = ["vector_store", "llm_client"]
            snapshot_fields = ["retry_count", "current_step", "short_term_memory"]

            def rehydrate(self, snapshot: dict):
                # Recreate heavy objects on restart
                self.vector_store = VectorStore.from_config(self.config)
                self.llm_client = LLMClient(api_key=os.environ["LLM_KEY"])
    """

    snapshot_fields: list[str] = []      # Primitive value fields to serialize
    rehydratable_fields: list[str] = []  # Fields to recreate on restart

    def extract_snapshot(self) -> dict:
        """Extract only serializable core context"""
        snapshot = {}
        for field in self.snapshot_fields:
            val = getattr(self, field, None)
            try:
                import json
                json.dumps(val)           # Serialization test
                snapshot[field] = val
            except (TypeError, ValueError):
                snapshot[field] = str(val)   # Fallback: string conversion
        return snapshot

    def restore_from_snapshot(self, snapshot: dict):
        """Restore primitive values then recreate heavy objects (Rehydration)"""
        for field, value in snapshot.items():
            setattr(self, field, value)
        self.rehydrate(snapshot)          # Invoke recreation logic

    def rehydrate(self, snapshot: dict):
        """Override point: heavy object recreation logic"""
        pass   # Default: do nothing (for pure primitive-value agents)
```

| Approach | Feasibility | Notes |
|-----------|------------|------|
| Full object serialization | **Low** | Circular Reference, security issues |
| `pickle` serialization | Low | Version dependent, security vulnerability |
| **State Rehydration (primitive snapshot)** | **High** ✅ | Only serializable fields, heavy objects recreated |
| S3 offload pointer | High | Large text contexts stored in S3, only pointer transmitted |

**Recommendation:** Use `StateRehydrationMixin` + explicit `snapshot_fields`. The `rehydrate()` method lets agents declare their own recovery logic. This pattern structurally prevents serialization failures.

**Challenge 2: SFN Execution History Limit**

| Execution Mode | Max Events | Max Execution Duration |
|-----------|------------|---------------|
| Standard Workflow | 25,000 events | 1 year |
| Express Workflow | 100,000 events | 5 minutes |

Each segment ≈ ~5 SFN events. Standard Workflow supports a maximum of **~5,000 segments**. This is sufficient for typical agent loops of a few hundred iterations. However, long-running tasks requiring tens of thousands of loops must use the **Nested Execution pattern** (child SFN execution chains).

**Challenge 3: Async Agent Framework Compatibility**

LangGraph, CrewAI, AutoGen, etc. all operate on `async` foundations. If the Bridge SDK fails to properly handle this async boundary, deadlocks can occur.

**Solution:** `asyncio.run()` isolation, processing Bridge I/O in a separate event loop thread.

**Challenge 4: Action Intercept Timing**

The bridge must be called "just before" the agent invokes an LLM or tool. Three intercept methods are available:

| Method | Intrusiveness | Reliability |
|------|--------|--------|
| **Context Manager** (`with bridge.segment(...)`) | Medium — Code modification required | High ✅ |
| **Class Inheritance** (`class MyAgent(AnalemmaAgent)`) | Low — Minimal modification | Medium |
| **Monkey-patching** (Replace LLM call function) | None | Low (vulnerable to framework internal changes) |

**Recommendation:** Provide context manager as Primary and inheritance as Secondary.

**Challenge 5: Commit Order Guarantee — Reordering Buffer**

In multi-threaded agents, when multiple segments are simultaneously Proposed, network and scheduling delays can cause them to **arrive out of order**. The Merkle DAG's `parent_manifest_id` chain breaks, and sequence number validation fails.

**Example Scenario:**

```
Thread A: loop_42 → PROPOSE sent (50ms network delay)
Thread B: loop_43 → PROPOSE sent (5ms local)
Kernel receive order: loop_43 → loop_42  ← Inverted!
```

**Solution — VirtualSegmentManager's Reordering Buffer:**

Add a **sequence number (monotonic counter)** to the bridge, and place a **Reordering Buffer** in the kernel server (VirtualSegmentManager) that holds out-of-order arriving segments for up to `max_wait_ms`, then processes them in order. On timeout, Fail-Open (process as-is) ensures availability.

---

### 4.3 Enhanced ABI Specification

Enhance the proposed ABI to production level.

#### SEGMENT_PROPOSE (Agent → Kernel)

```json
{
  "protocol_version": "1.0",
  "op": "SEGMENT_PROPOSE",
  "idempotency_key": "wf_123:loop_042:sha256_of_action",
  "segment_context": {
    "workflow_id": "wf_123",
    "parent_segment_id": "seg_041",
    "loop_index": 42,
    "segment_type": "TOOL_CALL",
    "sequence_number": 42,
    "ring_level": 3,
    "estimated_duration_ms": 3000,
    "is_optimistic_report": false
  },
  "payload": {
    "thought": "I need to read the AWS billing report.",
    "action": "s3_get_object",
    "action_params": {
      "bucket": "my-billing",
      "key": "report.json"
    }
  },
  "state_snapshot": {
    "serializable_fields": {
      "retry_count": 0,
      "found_data": false,
      "current_step": "data_collection"
    },
    "large_state_s3_pointer": "s3://bucket/snapshots/loop_042.json",
    "token_usage_total": 1540,
    "elapsed_ms": 12000
  }
}
```

Added field descriptions:
- `idempotency_key`: Prevents duplicate processing on network retransmission
- `segment_type`: `LLM_CALL | TOOL_CALL | MEMORY_UPDATE | FINAL`
- `sequence_number`: Monotonically increasing counter for order guarantee
- `ring_level`: Agent Ring level (`0`=KERNEL, `1`=DRIVER, `2`=SERVICE, `3`=USER)
- `is_optimistic_report`: `false`=pre-proposal (before action execution), `true`=Optimistic post-report. Forced to `false` for Ring 3
- `estimated_duration_ms`: Used for dynamic HeartbeatSeconds adjustment
- `large_state_s3_pointer`: S3 offload pointer for unserializable objects

#### SEGMENT_COMMIT (Kernel → Agent)

```json
{
  "protocol_version": "1.0",
  "op": "SEGMENT_COMMIT",
  "idempotency_key": "wf_123:loop_042:sha256_of_action",
  "status": "APPROVED",
  "security_clearance": "RING_3",
  "checkpoint_id": "cp_sha256_merkle_root",
  "commands": {
    "action_override": null,
    "inject_recovery_instruction": null,
    "modify_action_params": null
  },
  "governance_feedback": {
    "warnings": [],
    "anomaly_score": 0.0,
    "article_violations": []
  }
}
```

Possible `status` values:
- `APPROVED`: Action execution permitted
- `MODIFIED`: Execute after modifying parameters via `commands.modify_action_params`
- `REJECTED`: Skip this loop, agent self-correction guided via `governance_feedback`
- `SOFT_ROLLBACK`: Roll back to previous checkpoint and retry
- `SIGKILL`: Immediately terminate the agent

---

### 4.4 Concrete Implementation Plan

#### Python Bridge SDK

```python
# analemma_bridge/python/bridge.py
import asyncio
import hashlib
import json
import threading
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SegmentResult:
    status: str
    checkpoint_id: str
    action_override: Optional[Dict] = None
    governance_feedback: Optional[Dict] = None

    @property
    def allowed(self) -> bool:
        return self.status in ("APPROVED", "MODIFIED")

    @property
    def should_kill(self) -> bool:
        return self.status == "SIGKILL"


class AnalemmaBridge:
    """
    Analemma Bridge SDK — For Python Agents

    Usage:
        bridge = AnalemmaBridge(workflow_id="wf_123", ring_level=3)

        # Context manager approach
        with bridge.segment(thought="...", action="s3_get_object", params={...}) as seg:
            if seg.allowed:
                result = execute_action(seg.action_params)
                seg.report_observation(result)
    """

    def __init__(self, workflow_id: str, ring_level: int = 3,
                 kernel_endpoint: str = "http://localhost:8765"):
        self.workflow_id = workflow_id
        self.ring_level = ring_level
        self.kernel_endpoint = kernel_endpoint
        self._loop_index = 0
        self._parent_segment_id: Optional[str] = None
        self._lock = threading.Lock()

    @contextmanager
    def segment(self, thought: str, action: str, params: Dict[str, Any],
                segment_type: str = "TOOL_CALL",
                state_snapshot: Optional[Dict] = None):
        """
        Synchronous context manager — kernel communication before and after agent action
        """
        with self._lock:
            self._loop_index += 1
            loop_index = self._loop_index

        proposal = self._build_proposal(
            thought, action, params, segment_type, loop_index, state_snapshot
        )
        commit = self._send_propose(proposal)
        seg = _SegmentHandle(commit, params)

        try:
            yield seg
            if seg._observation is not None:
                self._send_observation(commit.checkpoint_id, seg._observation)
        except Exception as e:
            self._send_failure(commit.checkpoint_id, str(e))
            raise
        finally:
            self._parent_segment_id = commit.checkpoint_id

    def _build_proposal(self, thought, action, params, segment_type,
                        loop_index, state_snapshot) -> Dict:
        content = f"{self.workflow_id}:loop_{loop_index}:{action}:{json.dumps(params, sort_keys=True)}"
        idempotency_key = hashlib.sha256(content.encode()).hexdigest()[:16]

        return {
            "protocol_version": "1.0",
            "op": "SEGMENT_PROPOSE",
            "idempotency_key": idempotency_key,
            "segment_context": {
                "workflow_id": self.workflow_id,
                "parent_segment_id": self._parent_segment_id,
                "loop_index": loop_index,
                "segment_type": segment_type,
                "sequence_number": loop_index,
            },
            "payload": {
                "thought": thought,
                "action": action,
                "action_params": params,
            },
            "state_snapshot": state_snapshot or {},
        }

    def _send_propose(self, proposal: Dict) -> SegmentResult:
        """Send SEGMENT_PROPOSE to kernel → Receive SEGMENT_COMMIT"""
        import requests
        try:
            resp = requests.post(
                f"{self.kernel_endpoint}/v1/segment/propose",
                json=proposal,
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            return SegmentResult(
                status=data["status"],
                checkpoint_id=data["checkpoint_id"],
                action_override=data.get("commands", {}).get("action_override"),
                governance_feedback=data.get("governance_feedback"),
            )
        except Exception as e:
            # Fail-Open when kernel unavailable (APPROVED) — availability first
            # Can switch to Fail-Closed in production
            import logging
            logging.warning(f"[Bridge] Kernel unreachable, fail-open: {e}")
            return SegmentResult(status="APPROVED", checkpoint_id="local_only")

    def _send_observation(self, checkpoint_id: str, observation: Any):
        """Report action result to kernel"""
        import requests
        try:
            requests.post(
                f"{self.kernel_endpoint}/v1/segment/observe",
                json={"checkpoint_id": checkpoint_id, "observation": str(observation)},
                timeout=5
            )
        except Exception:
            pass  # Observation report failure is not critical

    def _send_failure(self, checkpoint_id: str, error: str):
        import requests
        try:
            requests.post(
                f"{self.kernel_endpoint}/v1/segment/fail",
                json={"checkpoint_id": checkpoint_id, "error": error},
                timeout=5
            )
        except Exception:
            pass


class _SegmentHandle:
    """Handle exposed to the agent within the context manager"""

    def __init__(self, commit: SegmentResult, original_params: Dict):
        self._commit = commit
        self._observation = None
        self.action_params = commit.action_override or original_params

    @property
    def allowed(self) -> bool:
        return self._commit.allowed

    @property
    def should_kill(self) -> bool:
        return self._commit.should_kill

    @property
    def checkpoint_id(self) -> str:
        return self._commit.checkpoint_id

    def report_observation(self, observation: Any):
        self._observation = observation
```

#### Optimistic Commitment Mode — Addressing Latency Compounding

> **Key Problem:** If PROPOSE → kernel round-trip → COMMIT occurs on every loop, agent thinking speed becomes **serially dependent** on kernel network latency. 100ms × 1,000 loops = 100 seconds of additional overhead.

**Two Execution Modes:**

| Mode | Behavior | Latency | Security Strength |
|------|------|---------|-----------|
| **Strict Mode** (default) | PROPOSE → kernel approval → execute | +100–500ms/loop | Highest (all actions pre-verified) |
| **Optimistic Mode** | L1 local check → immediate execution → async result report | +5ms/loop | Medium (L1 non-bypassable, L2/L3 post-verified) |

**Optimistic Mode Operation:**

```
[Local Bridge] L1 Heuristic Check (sync, ~1ms):
  - Prohibited keyword patterns (INJECTION_PATTERNS subset)
  - Capability Map check (frozenset lookup, ~0.01ms)
  - On injection suspicion → immediate BLOCK (local, no network round-trip)

If L1 passes:
  - Agent immediately executes action
  - Simultaneously reports to kernel async (fire-and-forget)

[Kernel Server] Async receipt (post-hoc):
  - L2 pattern matching + L3 SemanticShield check
  - On violation detection → inject SOFT_ROLLBACK into agent's next loop
  - Record violation in DynamoDB (audit trail maintained)
```

```python
# Adding mode parameter to AnalemmaBridge

class AnalemmaBridge:
    def __init__(self, workflow_id: str, ring_level: int = 3,
                 kernel_endpoint: str = "http://localhost:8765",
                 mode: str = "strict"):  # "strict" | "optimistic"
        ...
        self.mode = mode
        self._l1_checker = LocalL1Checker()   # Local rule engine

    @contextmanager
    def segment(self, thought: str, action: str, params: dict, ...):
        if self.mode == "optimistic":
            # Only perform L1 local check (sync)
            l1_result = self._l1_checker.check(thought, action)
            if not l1_result.allowed:
                raise SecurityViolation(f"L1 blocked: {l1_result.reason}")
            # Return execution handle immediately
            seg = _OptimisticHandle(params)
            yield seg
            # Post-hoc async report
            self._async_report(thought, action, seg._observation)
        else:
            # Strict Mode: existing sync PROPOSE/COMMIT flow
            ...


class LocalL1Checker:
    """
    Bridge-embedded lightweight checker — executes instantly without network
    Carries a subset of kernel's SemanticShield Stage 1+2 core patterns
    """
    _QUICK_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"이전\s+지시사항\s+무시",
        r"system\s+prompt\s+reveal",
    ]

    def check(self, thought: str, action: str) -> "L1Result":
        import re
        text = f"{thought} {action}".lower()
        for pattern in self._QUICK_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return L1Result(allowed=False, reason=f"Pattern: {pattern}")
        # Capability Map lookup (frozenset, O(1))
        if not self._check_capability(action):
            return L1Result(allowed=False, reason=f"Capability denied: {action}")
        return L1Result(allowed=True, reason=None)
```

**Mode Selection Guide:**

| Situation | Recommended Mode | Reason |
|------|-----------|------|
| Ring 3 (External user agents) | Strict | Untrusted input, pre-verification required |
| Ring 2 (Internal service agents) | Optimistic | Trusted environment, minimize latency |
| Real-time conversational agents | Optimistic | Response speed UX priority |
| Financial/medical data processing agents | Strict | Regulatory compliance, pre-approval required |

**Risk Tolerance:** In Optimistic Mode, if L2/L3 violations are detected post-hoc, "already executed actions" cannot be rolled back. Explicitly disclose this trade-off to clients, and disable Optimistic Mode for Ring 3 agents.

#### Hybrid Interceptor — Structural Safeguard for Optimistic Mode

> **Key Problem:** Optimistic Mode performs L2/L3 verification asynchronously after the fact. However, **Destructive Actions** such as `rm -rf`, `DROP TABLE`, `filesystem_delete` are irreversible once executed. Even if SOFT_ROLLBACK is received afterward, disk deletion or DB drop has already completed.

**Hybrid Interceptor Operation:**

```
[Optimistic Mode segment() entry]
    ↓
[Hybrid Interceptor._is_destructive(action, thought, params)]
    ↓
 ┌──────────┐         ┌──────────────────────────────────────┐
 │ False    │         │ True                                  │
 │ (Safe)   │         │ (Destructive)                         │
 ↓          │         ↓                                       │
L1 local     │         effective_mode forced switch:           │
check        │         "optimistic" → "strict"                 │
Immediate    │         ↓                                       │
execution    │         PROPOSE → kernel sync approval → exec  │
Async report │                                                 │
            └─────────────────────────────────────────────────┘
```

**Destructive Action Classification Criteria:**

| Category | Applicable Actions |
|----------|-----------------|
| Filesystem destruction | `filesystem_write`, `filesystem_delete`, `rm`, `rmdir`, `truncate` |
| Process execution | `shell_exec`, `subprocess_call` |
| Database destruction | `database_delete`, `database_drop` |
| Cloud storage deletion | `s3_delete` |

Additionally, if destructive patterns (`rm -rf`, `drop table`, `delete from`, `truncate`, `format disk`, etc.) are detected in the `thought` or parameter strings, a forced Strict switch also occurs.

```python
# Hybrid Interceptor core logic (python_bridge.py)

DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset({
    "filesystem_write", "filesystem_delete", "shell_exec",
    "subprocess_call", "database_delete", "database_drop",
    "s3_delete", "rm", "rmdir", "truncate",
})

DESTRUCTIVE_PATTERNS: list[str] = [
    r"rm\s+-[rf]+",           # rm -rf, rm -r, rm -f
    r"drop\s+table",          # SQL DROP TABLE
    r"delete\s+from",         # SQL DELETE
    r"truncate\s+table",      # SQL TRUNCATE
    r"format\s+disk",         # Disk format
    r"파일\s*삭제",            # Korean destructive pattern
    r"데이터베이스\s*삭제",
    r"전체\s*삭제",
    r"mkfs\.",                # Filesystem format
    r"dd\s+if=.+of=/dev/",   # Disk overwrite
]

@contextmanager
def segment(self, thought: str, action: str, params: dict, ...):
    effective_mode = self.mode

    # Hybrid Interceptor: Force Strict switch when destructive action detected in Optimistic Mode
    if effective_mode == "optimistic" and self._is_destructive(action, thought, params):
        effective_mode = "strict"
        logger.warning(
            "[HybridInterceptor] Destructive action detected in Optimistic Mode. "
            "Forcing STRICT mode. action=%s, workflow=%s",
            action, self.workflow_id
        )

    if effective_mode == "optimistic":
        # Existing Optimistic flow
        ...
    else:
        # Strict flow (includes destructive actions)
        ...
```

**Design Principles:**
- Hybrid Interceptor is applied **without any agent code modification** — automatically activated just by setting `mode="optimistic"`
- Strict switch for destructive actions leaves a **log warning** to maintain audit trail
- DESTRUCTIVE_ACTIONS frozenset lookup is O(1) — no performance impact
- Scans `thought` + `action` + `params` entirely to reduce missed detection risk

#### TypeScript Bridge SDK (`@analemma/bridge-sdk`)

> **v1.3.0 Update:** Async factory pattern, `BridgeRingLevel` IntEnum, Policy Sync, Hybrid Interceptor integration.

```typescript
// analemma-bridge/src/types.ts
export enum BridgeRingLevel { KERNEL=0, DRIVER=1, SERVICE=2, USER=3 }

export const CAPABILITY_MAP: Record<BridgeRingLevel, ReadonlySet<string>> = {
  [BridgeRingLevel.KERNEL]: new Set(["*"]),
  [BridgeRingLevel.DRIVER]: new Set(["filesystem_read","subprocess_call","network_limited",
    "database_write","config_read","network_read","database_query","cache_read",
    "event_publish","basic_query","read_only","s3_get_object","s3_put_object"]),
  [BridgeRingLevel.SERVICE]: new Set(["network_read","database_query","cache_read",
    "event_publish","basic_query","read_only","s3_get_object"]),
  [BridgeRingLevel.USER]: new Set(["basic_query","read_only"]),
};

export const DESTRUCTIVE_ACTIONS: ReadonlySet<string> = new Set([
  "filesystem_write","filesystem_delete","rm","rmdir","truncate",
  "shell_exec","subprocess_call","database_delete","database_drop",
  "s3_delete","s3_delete_objects","format","wipe",
]);

export const DESTRUCTIVE_PATTERNS: RegExp[] = [
  /rm\s+-[rf]+/i, /drop\s+table/i, /delete\s+from/i,
  /truncate\s+(?:table\s+)?\w+/i, /format\s+(?:disk|drive|c:)/i,
  /mkfs\./i, /dd\s+if=.+of=\/dev\//i,
  /파일\s*삭제/, /데이터베이스\s*(?:삭제|드롭)/, /전체\s*삭제/, /모두\s*삭제/,
];
```

```typescript
// analemma-bridge/src/bridge.ts  (core summary)
import { BridgeRingLevel, DESTRUCTIVE_ACTIONS, DESTRUCTIVE_PATTERNS } from "./types";
import { LocalL1Checker } from "./l1_checker";

export interface BridgeConfig {
  workflowId: string;
  ringLevel?: BridgeRingLevel;
  kernelEndpoint?: string;   // Default: ANALEMMA_KERNEL_ENDPOINT env | "http://localhost:8765"
  mode?: "strict" | "optimistic";
  syncPolicy?: boolean;      // When true, automatically calls /v1/policy/sync during create()
}

export class AnalemmaBridge {
  /** Async factory — returns instance after PolicySync if syncPolicy=true */
  static async create(config: BridgeConfig): Promise<AnalemmaBridge> {
    const resolved = {
      ringLevel: config.ringLevel ?? BridgeRingLevel.USER,
      kernelEndpoint: config.kernelEndpoint
        ?? process.env.ANALEMMA_KERNEL_ENDPOINT ?? "http://localhost:8765",
      mode: config.mode ?? "strict",
      syncPolicy: config.syncPolicy
        ?? (process.env.ANALEMMA_SYNC_POLICY ?? "").trim() === "1",
    };
    const l1Checker = new LocalL1Checker();
    if (resolved.syncPolicy) {
      const synced = await l1Checker.syncFromKernel(resolved.kernelEndpoint);
      if (synced)
        console.info(`[AnalemmaBridge] Policy synced. version=${l1Checker.policyVersion}`);
      else
        console.warn("[AnalemmaBridge] Policy sync failed. Using local defaults.");
    }
    return new AnalemmaBridge(resolved, l1Checker);
  }

  async segment<T>(options: SegmentOptions<T>): Promise<SegmentOutcome<T>> {
    const loopIndex = ++this.loopIndex;

    // Hybrid Interceptor: DESTRUCTIVE_ACTIONS frozenset + pattern scan
    let effectiveMode = this.mode;
    if (effectiveMode === "optimistic" && this.isDestructive(options)) {
      effectiveMode = "strict";
      console.warn(
        `[HybridInterceptor] Destructive → forcing STRICT. action=${options.action}`
      );
    }

    return effectiveMode === "optimistic"
      ? this.optimisticSegment(options, loopIndex)
      : this.strictSegment(options, loopIndex);
  }

  private async strictSegment<T>(options, loopIndex): Promise<SegmentOutcome<T>> {
    const commit = await this.sendPropose(this.buildProposal(options, loopIndex, false));
    const recoveryInstruction = commit.commands.inject_recovery_instruction;

    if (commit.status === "SIGKILL")
      throw new SecurityViolation(`SIGKILL at loop ${loopIndex}. Recovery: ${recoveryInstruction}`);

    if (!["APPROVED","MODIFIED"].includes(commit.status)) {
      await this.sendObservation(commit.checkpoint_id, null, "SKIPPED");
      return { result: null, commit, recoveryInstruction };
    }

    const result = await options.execute(commit.commands.action_override ?? options.params);
    await this.sendObservation(commit.checkpoint_id, result, "SUCCESS");
    this.parentSegmentId = commit.checkpoint_id;
    return { result, commit, recoveryInstruction };
  }

  private async optimisticSegment<T>(options, loopIndex): Promise<SegmentOutcome<T>> {
    // L1 local check (including ZWS normalization + Homoglyph conversion, ~1ms)
    const l1 = this.l1Checker.check(options.thought, options.action, this.ringLevel, options.params);
    if (!l1.allowed) throw new SecurityViolation(`[L1 Blocked] ${l1.reason}`);

    const result = await options.execute(options.params);

    // Post-hoc async kernel report (fire-and-forget)
    this.asyncReport(options, loopIndex, result).catch((e) =>
      console.debug(`[AnalemmaBridge] Async report failed (non-critical): ${e}`)
    );
    return { result, commit: FAKE_APPROVED_COMMIT, recoveryInstruction: null };
  }

  private isDestructive(options: SegmentOptions<unknown>): boolean {
    if (DESTRUCTIVE_ACTIONS.has(options.action.toLowerCase())) return true;
    const text = options.thought + " " + JSON.stringify(options.params ?? {});
    return DESTRUCTIVE_PATTERNS.some((p) => p.test(text));
  }
}
```

```typescript
// analemma-bridge/src/l1_checker.ts  (including ZWS + Homoglyph normalization)
const ZW_REGEX = /[\u200b\u200c\u200d\ufeff\u202e\u202d]/g;
const HOMOGLYPH_MAP: Record<string, string> = {
  "\u0430":"a", "\u0435":"e", "\u043e":"o",
  "\u0440":"p", "\u0441":"c", "\u0445":"x",
  "\u03b1":"a", "\u03bf":"o",
};
const HOMOGLYPH_REGEX = new RegExp(Object.keys(HOMOGLYPH_MAP).join("|"), "g");

function normalize(text: string): string {
  return text
    .replace(ZW_REGEX, "")           // Remove Zero-Width + RTL Override
    .normalize("NFKC")               // Unicode normal form
    .replace(HOMOGLYPH_REGEX, (ch) => HOMOGLYPH_MAP[ch] ?? ch);  // Cyrillic homoglyph substitution
}

export class LocalL1Checker {
  async syncFromKernel(kernelEndpoint: string): Promise<boolean> {
    // GET /v1/policy/sync → compare version → update injection patterns & CapabilityMap
    const resp = await axios.get(`${kernelEndpoint}/v1/policy/sync`, { timeout: 5_000 });
    if (resp.data.version === this._policyVersion) return true;
    this.injectPatterns(resp.data.injection_patterns, resp.data.capability_map, resp.data.version);
    return true;
  }
}
```

**Module Package Structure:**

```
analemma-bridge/
├── package.json          # @analemma/bridge-sdk, axios peer dep
├── tsconfig.json         # ES2020, CommonJS, strict
└── src/
    ├── types.ts          # BridgeRingLevel, CAPABILITY_MAP, DESTRUCTIVE_ACTIONS, ABI types
    ├── l1_checker.ts     # LocalL1Checker (normalize + Pattern + CapMap + PolicySync)
    ├── bridge.ts         # AnalemmaBridge (Strict / Optimistic / Hybrid Interceptor)
    └── index.ts          # Public API re-export
```

---

### 4.5 Kernel Integration: VirtualSegmentManager

Server component that receives `SEGMENT_PROPOSE` from the bridge and integrates with the Analemma kernel's existing governance pipeline. (v1.3.0)

```
SEGMENT_PROPOSE (HTTP POST /v1/segment/propose)
  │
  ├─ [pre] Ring 3 is_optimistic_report forced block
  │    → If ring_level ≥ 3 and is_optimistic_report=true → force-correct to false + WARNING log
  │
  ├─ [0] ReorderingBuffer.wait_for_turn(workflow_id, sequence_number)
  │    → On out-of-order arrival, wait up to max_wait_ms=200ms to guarantee order
  │    → On timeout, Fail-Open (process ignoring order)
  │
  ├─ [1] SemanticShield.inspect(payload.thought)
  │    → On INJECTION detection → SIGKILL + Recovery Instruction returned
  │
  ├─ [2] validate_capability(ring_level, payload.action)
  │    → Unauthorized tool → REJECTED + "available alternative tools list" Recovery Instruction
  │    → If is_optimistic_report=true → SOFT_ROLLBACK
  │
  ├─ [3] BudgetWatchdog.check(state_snapshot.token_usage_total)
  │    → Budget exceeded → SOFT_ROLLBACK + "terminate with FINAL segment" Recovery Instruction
  │
  ├─ [4] GovernanceEngine.verify(thought + action_params)
  │    → CRITICAL Article violation → SIGKILL + "immediate termination" Recovery Instruction
  │    → MEDIUM Article violation → REJECTED / SOFT_ROLLBACK + "retry after correction" guidance
  │
  ├─ [5] _AuditRegistry.set(checkpoint_id, _ProposedRecord)
  │    → Redis setex(TTL=1h) / in-memory fallback
  │    → Auto-cleanup of ReorderingBuffer when segment_type == "FINAL"
  │
  └─ SEGMENT_COMMIT { status: APPROVED, checkpoint_id,
                       inject_recovery_instruction } returned

POST /v1/segment/observe  → Observation Audit Trail (consistency check)
GET  /v1/policy/sync      → Pattern & CapabilityMap for LocalL1Checker synchronization
DELETE /v1/workflow/{id}  → Manual Reordering Buffer cleanup
GET  /v1/health           → Overall component status + policy version
```

**v1.3 Key Changes Summary:**

| Item | v1.1 (Previous) | v1.3 (Current) |
|------|-------------|-------------|
| Policy constant management | Inline duplicate definitions | `shared_policy.py` single source import |
| Audit Registry | In-memory dict | `_AuditRegistry`: Redis TTL=1h / in-memory fallback |
| is_optimistic protection | None | Ring 3 `true` → `false` forced block |
| Recovery Instruction | Empty `null` return | Natural language guide per rejection reason (`_build_recovery_instruction`) |
| Observation validation | None | Proposed vs actual action consistency check |
| Policy Sync | None | `GET /v1/policy/sync` endpoint |
| FINAL cleanup | Manual DELETE only | Auto-cleanup on segment_type="FINAL" receipt |

**Reordering Buffer Detailed Design:**

```
Multi-threaded proposals from workflow wf_123:
  Thread A: seq=42 (50ms network delay)
  Thread B: seq=43 (2ms local processing)

Server receive order: seq=43 → seq=42

ReorderingBuffer.wait_for_turn("wf_123", seq=43):
  expected["wf_123"] = 42  (seq=42 not yet received)
  → Wait up to 200ms (polling at 10ms intervals)
  → If seq=42 arrives, immediately process seq=42 → then seq=43
  → On 200ms timeout: Fail-Open, process seq=43 immediately + warning log

Design Principles:
  - Per-workflow_id independent counters (different workflows have independent ordering)
  - Concurrency guaranteed via asyncio.Lock
  - max_wait_ms=200 default (configurable)
  - Fail-Open: Availability prioritized even after timeout
```

```python
# backend/src/bridge/virtual_segment_manager.py  — v1.3.0 core snippet
#
# Full implementation: backend/src/bridge/virtual_segment_manager.py
# Run: uvicorn backend.src.bridge.virtual_segment_manager:app --host 0.0.0.0 --port 8765

# ── Shared Policy Constants (Single Source of Truth) ─────────────────────────────────
# Removed inline duplicate definitions of _ALLOWED_TOOLS_BY_RING / _POLICY_INJECTION_PATTERNS
# from previous v1.1. Unified to shared_policy.py single source.
from .shared_policy import (
    CAPABILITY_MAP_INT as _ALLOWED_TOOLS_BY_RING,   # int-keyed dict
    RING_NAMES as _RING_NAMES,
    INJECTION_PATTERNS as _POLICY_INJECTION_PATTERNS,
)
_POLICY_VERSION: str = hashlib.md5(
    "|".join(sorted(_POLICY_INJECTION_PATTERNS)).encode()
).hexdigest()[:8]


# ── Audit Registry — Redis TTL / in-memory fallback ─────────────────────────
class _AuditRegistry:
    """
    Audit Registry backend abstraction.
    Uses Redis setex(TTL=1h) when ANALEMMA_REDIS_URL is configured (server restart safe).
    Falls back to in-memory dict when not configured (dev environment, single process).
    """
    def init(self) -> None:
        if not self._redis_url:
            return
        try:
            import redis as _redis_lib
            self._redis = _redis_lib.from_url(self._redis_url, decode_responses=True)
            self._redis.ping()
            self._use_redis = True
        except Exception as exc:
            logger.warning("[AuditRegistry] Redis unavailable, falling back to in-memory: %s", exc)

    def set(self, key: str, record: _ProposedRecord) -> None:
        if self._use_redis:
            self._redis.setex(f"audit:{key}", self._ttl, json.dumps(dataclasses.asdict(record)))
            return
        if len(self._memory) >= _REGISTRY_MAX_SIZE:
            del self._memory[next(iter(self._memory))]
        self._memory[key] = record

    def pop(self, key: str) -> Optional[_ProposedRecord]:
        if self._use_redis:
            data = self._redis.get(f"audit:{key}")
            if data:
                self._redis.delete(f"audit:{key}")
                return _ProposedRecord(**json.loads(data))
            return None
        return self._memory.pop(key, None)


# ── propose_segment core logic (v1.3 additions) ───────────────────────────────

@app.post("/v1/segment/propose")
async def propose_segment(req: SegmentProposalRequest):
    ctx = req.segment_context
    ring_level = ctx.ring_level
    is_optimistic = ctx.is_optimistic_report

    # ① Ring 3 (USER) is_optimistic_report forced block
    #    Prevents untrusted agents from bypassing pre-verification via is_optimistic_report=True
    if ring_level >= 3 and is_optimistic:
        is_optimistic = False
        logger.warning(
            "[VirtualSegmentManager] Ring 3 agent attempted is_optimistic_report=True. "
            "Forced to False. workflow=%s", ctx.workflow_id,
        )

    # ... [0]–[4] Pipeline (SemanticShield → Capability → Budget → Governance) ...

    # ② FINAL segment → Auto-cleanup ReorderingBuffer (memory leak prevention)
    if ctx.segment_type == "FINAL":
        _reorder_buffer.reset(ctx.workflow_id)
        logger.info("[VirtualSegmentManager] FINAL received. Buffer cleaned: %s", ctx.workflow_id)

    # ③ Audit Registry registration (for Observation consistency check)
    _registry.set(checkpoint_id, _ProposedRecord(
        workflow_id=ctx.workflow_id, action=action, action_params=action_params,
        thought=thought, ring_level=ring_level, loop_index=ctx.loop_index,
    ))

    # ④ Recovery Instruction generation — natural language guide per rejection reason
    #    Capability denial → "present list of available alternative tools"
    #    Injection detection → "prompt manipulation attempt prohibited notice"
    #    constitutional_critical → "immediate termination directive"
    #    Budget → "guide graceful termination via FINAL segment"
    return _commit("APPROVED", checkpoint_id)


# ── Policy Sync Endpoint (/v1/policy/sync) ─────────────────────────────────
# LocalL1Checker.syncFromKernel() calls this endpoint on initialization
# to synchronize local patterns with the kernel. Version field for cache validity check.
@app.get("/v1/policy/sync")
async def get_policy_sync():
    return {
        "version": _POLICY_VERSION,
        "injection_patterns": _POLICY_INJECTION_PATTERNS,  # shared_policy.INJECTION_PATTERNS
        "capability_map": {
            str(ring): sorted(tools)
            for ring, tools in _ALLOWED_TOOLS_BY_RING.items()
            if ring != 0  # Ring 0 unlimited — no need to expose to client
        },
        "audit_registry_backend": _registry.backend_name,
    }


# ── Observation Audit Trail (/v1/segment/observe) ────────────────────────────
# Consistency check of reported actual_action vs. proposed proposed_action.
# On mismatch, CONSISTENCY_MISMATCH warning log → triggers future Ring demotion.
@app.post("/v1/segment/observe")
async def observe_segment(body: Dict[str, Any]):
    proposed = _registry.pop(body.get("checkpoint_id", ""))
    if proposed and body.get("action") and body["action"] != proposed.action:
        logger.warning(
            "[AuditTrail] CONSISTENCY_MISMATCH checkpoint=%s proposed=%s actual=%s",
            body["checkpoint_id"], proposed.action, body["action"],
        )
```

---

### 4.6 Risks and Mitigation

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Agent stops when kernel server unavailable | High | Fail-Open policy + local cache Commit queue |
| State serialization failure (complex objects) | Medium | `StateRehydrationMixin._safe_serialize()` — max_depth + circular reference detection |
| SFN History limit exceeded (25,000 events) | Medium | Nested Execution: spawn child SFN execution every 1,000 loops |
| Bridge latency overhead (+100ms/loop) | Low | Switch HTTP → gRPC via local gRPC or Unix Socket, reducing to ~10ms |
| Async framework deadlock | Medium | Isolate Bridge I/O in separate event loop thread |
| Action order guarantee (multi-threaded agents) | High | `ReorderingBuffer` + `sequence_number` monotonic counter |
| Audit Registry volatile on server restart | Medium | `_AuditRegistry` Redis TTL=1h backend; in-memory + warning when disconnected |
| Policy constant duplicate management errors | Medium | `shared_policy.py` single source — both VSM and LocalL1Checker import from it |
| Encoding bypass injection (Homoglyph/ZWS) | High | `LocalL1Checker.normalize()` — ZWS removal + NFKC + Homoglyph substitution before pattern check |
| Optimistic Mode Ring 3 pre-verification bypass attempt | High | Server-side forced block in VSM when `ring_level ≥ 3 and is_optimistic_report=True` |

---

## 5. Unified Architecture — Combining Both Plans

Plan A and Plan B can be implemented independently, but synergy is maximized when deployed together.

```
┌─────────────────────────────────────────────────────────────────┐
│  Client Internal Network (On-Premises / Private Cloud)           │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  Autonomous Agent (Python / TypeScript)                  │    │
│  │                                                          │    │
│  │  bridge.segment(thought, action, params)                 │    │
│  │         ↓                                                │    │
│  │  VirtualSegmentManager (FastAPI, runs locally)           │    │
│  │    [SemanticShield] [CapabilityMap] [GovernanceEngine]   │    │
│  │         ↓                                                │    │
│  │  LocalAgentWorker                                        │    │
│  │    open_state_bag → [Execute] → seal_state_bag           │    │
│  │         ↓ (Only S3 pointer sent externally)              │    │
│  └─────────────────────────────────────────────────────────┘    │
│                         │                                        │
│                  AWS IAM credentials (outbound HTTPS only)       │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  AWS (Control Plane)                                             │
│                                                                  │
│  SFN Activity Task Token ← SendTaskSuccess(s3_pointer)          │
│         ↓                                                        │
│  Governor Lambda (Ring 1)                                        │
│    [governor_runner] [GovernanceEngine] [SemanticShield]         │
│         ↓                                                        │
│  DynamoDB Governance Audit Log (90-day TTL)                      │
│  Merkle DAG (S3 + DynamoDB)                                      │
│  CloudWatch Dashboard                                            │
└─────────────────────────────────────────────────────────────────┘
```

**Data Flow Principles (Zero-Knowledge Update):**

| Data Type | Location | Flow Direction |
|-------------|------|-----------|
| Raw agent output (sensitive data) | Client internal storage (MinIO/on-premises) | Not transmitted externally |
| Content hash (SHA-256) | AWS SFN → DynamoDB | Local → AWS (hash only) |
| Governance results (APPROVED/REJECTED) | AWS DynamoDB | Within AWS |
| Metadata (token count, latency) | AWS CloudWatch | Within AWS |

### ⚠️ Limits of Determinism — Hallucination Loop Risk

> **Key Problem:** When the kernel returns REJECTED, if the agent doesn't understand "why it was rejected," it repeats similar actions, falling into a **Hallucination Loop**. This is a fundamental tension where the deterministic kernel cannot control the probabilistic agent's internal state.

**Hallucination Loop Scenario:**

```
Loop 42: action=filesystem_write → Kernel REJECTED (Ring 3 unauthorized)
Loop 43: action=write_file_system → Kernel REJECTED (same reason)
Loop 44: action=save_to_disk     → Kernel REJECTED (same reason)
Loop 45: action=filesystem_write → ...  ← Infinite loop
```

**Solution: Mandatory Recovery Instructions**

Elevate `governance_feedback.inject_recovery_instruction` from a simple warning field to the **core mechanism for agent self-correction**.

Example `SEGMENT_COMMIT` returned by kernel on REJECTED:

```json
{
  "status": "REJECTED",
  "checkpoint_id": "cp_abc123",
  "commands": {
    "action_override": null,
    "inject_recovery_instruction": "Action 'filesystem_write' is not permitted at Ring 3. Use 'basic_query' or escalate to Ring 2 via human approval. Available actions: [basic_query, read_only]."
  },
  "governance_feedback": {
    "warnings": ["Capability denied: filesystem_write at RING_3"],
    "anomaly_score": 0.2,
    "article_violations": [],
    "suggested_alternatives": ["basic_query", "read_only"],
    "escalation_path": "Request HITP human approval to upgrade ring level"
  }
}
```

**Auto-injection of Recovery Instruction in Bridge SDK:**

```python
@contextmanager
def segment(self, thought: str, action: str, params: dict, ...):
    ...
    commit = self._send_propose(proposal)
    seg = _SegmentHandle(commit, params)

    # If Recovery Instruction exists, auto-inject into agent's next thought
    if commit.recovery_instruction:
        self._inject_to_next_thought(commit.recovery_instruction)

    yield seg
    ...

def _inject_to_next_thought(self, instruction: str):
    """
    Insert kernel directive as prefix into agent's next loop thought.
    Guides the agent to understand why it was rejected and try alternatives.
    """
    self._pending_system_injection = (
        f"[KERNEL GOVERNANCE] {instruction}\n"
        f"This instruction has priority over your original plan.\n"
    )
```

**Recovery Instruction Generation Logic in VirtualSegmentManager:**

```python
def _build_recovery_instruction(
    violation_type: str, action: str, ring_level: int
) -> str:
    """
    Generate specific recovery instructions per violation type.
    Present immediately actionable alternatives instead of abstract rejections.
    """
    instructions = {
        "CAPABILITY_DENIED": (
            f"Action '{action}' requires Ring {ring_level - 1} or higher. "
            f"Available at your current ring: {_get_allowed_actions(ring_level)}. "
            f"To request elevated access, use action 'request_human_approval'."
        ),
        "CONSTITUTIONAL_VIOLATION": (
            f"Your output violated Article 6 (PII in text). "
            f"Remove personal identifiers (email, phone, SSN) before proceeding. "
            f"Use masking: replace 'john@example.com' with '[REDACTED_EMAIL]'."
        ),
        "BUDGET_EXCEEDED": (
            f"Token budget exhausted. Summarize progress and request human approval "
            f"to continue. Current usage: {token_usage} / {max_tokens} tokens."
        ),
    }
    return instructions.get(violation_type, f"Action '{action}' was rejected. Try an alternative approach.")
```

**Loop Guard — Blocking Repeated Identical Actions:**

A repetition detection mechanism is added to VirtualSegmentManager to structurally block Hallucination Loops.

```python
# Same (action, params) combination REJECTED N consecutive times → SIGKILL
LOOP_GUARD_THRESHOLD = 3

if self._consecutive_rejections.get(idempotency_base) >= LOOP_GUARD_THRESHOLD:
    return _commit("SIGKILL", "local_only",
                   warnings=["Hallucination loop detected: same action rejected 3 times"],
                   recovery="Agent is stuck in a rejection loop. Human review required.")
```

---

## 6. Implementation Roadmap

### Phase 0 — Foundation (2 weeks)

**Goal:** Register SFN Activity + verify basic local worker operation

| Task | Difficulty | Estimated Duration |
|------|--------|-----------|
| Create SFN Activity ARN and modify ASL | Low | 1 day |
| Basic `LocalAgentWorker` implementation | Low | 2 days |
| Verify `open_state_bag` / `seal_state_bag` local porting | Low | 1 day |
| Docker image build + local testing | Low | 2 days |
| **Verification Goal:** Confirm in SFN console that local worker retrieves and completes Activity Tasks | — | — |

### Phase 1 — Plan A Completion (4 weeks)

**Goal:** Local agent packaging ready for B2B deployment

| Task | Difficulty | Estimated Duration |
|------|--------|-----------|
| S3 result offload + pointer return | Low | 1 day |
| Heartbeat thread stabilization | Medium | 2 days |
| pip package (`analemma-agent`) setup | Medium | 3 days |
| IAM Role minimum privilege documentation | Low | 1 day |
| Auto-update mechanism (version check API) | Medium | 3 days |
| Client deployment guide | Low | 2 days |
| **Verification Goal:** Operates within client VPN environment without firewall inbound | — | — |

### Phase 2 — Plan B Foundation (6 weeks)

**Goal:** Bridge SDK + VirtualSegmentManager initial version

| Task | Difficulty | Estimated Duration |
|------|--------|-----------|
| **Moltbot Bridge integration experiment** (First task of Phase 2) | High | 3 days |
| `VirtualSegmentManager` FastAPI server | Medium | 4 days |
| Python Bridge SDK (`AnalemmaBridge`) | Medium | 3 days |
| TypeScript Bridge SDK | Medium | 4 days |
| `StateRehydrationMixin` + `LocalL1Checker` implementation | Medium | 3 days |
| SemanticShield / CapabilityMap integration | Low (existing code reuse) | 2 days |
| GovernanceEngine integration + Recovery Instruction generation | Medium | 3 days |
| Loop Guard (Hallucination Loop detection) | Medium | 2 days |
| **Verification Goal:** Confirm in Moltbot loop that SIGKILL terminates immediately and Recovery Instruction is injected into next thought on REJECTED | — | — |

> **Why Moltbot First:** Moltbot currently operates on a TypeScript foundation and is the most likely candidate to surface serialization issues (Memory Ghosting) and async framework compatibility problems first. Discovering the gap between theory and practice early allows correcting the Bridge SDK design to match reality. Validate the full flow with Moltbot before LangChain integration.

### Phase 3 — Advanced Features (8+ weeks)

| Task | Notes |
|------|------|
| Nested Execution pattern (handling 1,000+ loops) | Leveraging SFN Express Workflow |
| gRPC Bridge (HTTP → gRPC, 10x latency reduction) | Optional |
| Auto `@snapshot_fields` inference (AST analysis) | Convenience improvement |
| Dashboard: per-loop anomaly_score visualization | CloudWatch Custom Metrics |
| CrewAI / AutoGen / LangGraph dedicated adapters | Ecosystem expansion |

---

## 7. Conclusion and Recommendations

### Overall Assessment

| Criterion | Plan A | Plan B |
|------|--------|--------|
| Technical Risk | **Low** (AWS-validated pattern) | Medium (serialization/ordering difficulty) |
| Implementation Duration | **6 weeks (Phase 0+1)** | 14 weeks (Phase 0+1+2) |
| Time to Monetization | **Fast** (immediately after Phase 1 completion) | Slow (requires ecosystem adoption) |
| Differentiation Strength | Medium (comparable examples exist) | **High** (unique in agent governance market) |
| Existing Analemma Code Reuse | **High** | Medium |

### Strategic Positioning

**Plan A is for 'Revenue', Plan B is for 'Authority'.**

Rapidly commercialize Plan A to generate cash flow, and use Plan B to establish the technical narrative that "autonomous agents are uncontrollable without Analemma OS." The two plans serve different roles in the market:

| | Plan A | Plan B |
|---|---|---|
| **Market Role** | Immediate revenue source (product sales) | Technical authority building (standard preemption) |
| **Client Persuasion Logic** | "AI governance without data leakage" | "Agents are uncontrollable without a kernel" |
| **Competitive Moat Effect** | Medium (similar products exist) | High (agent governance layer is unique) |

### Recommendations

1. **Start Immediately:** Plan A (Phase 0 → Phase 1). Complete a B2B-sellable product within 6 weeks.
2. **Early Moltbot Experiment:** Moltbot bridge integration as the first task of Phase 2. Identify actual occurrence points of serialization and async issues early.
3. **Fail-Open Policy:** Maintain Fail-Open as default to avoid stopping agents on bridge server failure. Enforce Strict Mode for Ring 3 agents.
4. **Optimistic Mode Limited to Ring 2:** Allowing Optimistic Mode for Ring 3 agents means post-detection violations cannot be rolled back for already-executed actions. Ring 3 is always Strict.
5. **Switch to gRPC After Measurement:** Verify operation with HTTP REST first, and switch to gRPC only when actual latency measurements exceed acceptable thresholds.
6. **Audit over Score:** Replace the removed `trust_score_manager.py` with an **Immutable Audit Trail**. B2B clients want immutable evidence of "who, when, what action, and why" rather than "agent trust scores." Strengthen the DynamoDB Governance Audit Log as the single source of truth for this evidence.

### Audit over Score — Immutable Audit Trail Design

Extend `GovernanceAuditLog` (DynamoDB, 90-day TTL) as **legal-evidence-grade records** for all governance decisions:

| Field | Content | Purpose |
|------|------|------|
| `agent_id` | Agent identifier | Subject tracking |
| `action` | Attempted tool/action | Action recording |
| `decision` | APPROVED / REJECTED / SIGKILL | Decision recording |
| `decision_reason` | Article violation, insufficient permissions, etc. | **Why** recording |
| `recovery_instruction` | Recovery directive injected to agent | Self-correction evidence |
| `content_hash` | Output SHA-256 | Integrity proof |
| `ring_level` | Permission level at execution time | Permission scope recording |
| `timestamp` | ISO-8601 UTC | Time recording |
| `immutable` | `true` (DynamoDB conditional write) | Retroactive modification impossible |

Based on this log, during regulatory audits it can be proven that "a specific agent attempted to access specific data at a specific time, the kernel rejected it, and the agent took an alternative action." This is 10x more valuable than Trust Score in B2B regulatory environments.

### Key Technical Asset Reuse Summary

Existing Analemma OS implementations are directly reused in both plans.

| Existing Implementation | Plan A Reuse | Plan B Reuse |
|-------------|---------------|---------------|
| `kernel_protocol.py` (open/seal) | ✅ Local execution contract | — |
| `universal_sync_core.py` | ✅ State merging | — |
| `SemanticShield` | — | ✅ Thought injection inspection |
| `GovernanceEngine` | — | ✅ Constitutional verification |
| `CAPABILITY_MAP` | — | ✅ Action permission gate |
| `RedisCircuitBreaker` | ✅ Shared CB between workers | ✅ Loop retry limiting |
| `MerkleDAG` (state_versioning) | ✅ Local result checkpointing | ✅ Loop checkpointing |

---

*This document serves as the rationale for Analemma OS architecture design decisions.*
*Technical differences discovered during implementation will be updated directly in this document.*
