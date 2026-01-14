# 🏗️ Architecture Deep-Dive

> [← Back to Main README](../README.md)

This document provides a comprehensive technical overview of the Analemma OS kernel architecture, abstraction layers, and core implementation patterns.

---

## Table of Contents

1. [System Architecture Overview](#1-system-architecture-overview)
2. [Kernel Layer Design](#2-kernel-layer-design)
3. [State Management System](#3-state-management-system)
4. [Workflow Execution Engine](#4-workflow-execution-engine)
5. [Distributed Execution (Distributed Map)](#5-distributed-execution-distributed-map)
6. [LLM Integration Layer](#6-llm-integration-layer)
7. [Recovery & Self-Healing](#7-recovery--self-healing)
8. [Security Architecture](#8-security-architecture)

---

## 1. System Architecture Overview

Analemma OS is built on a **3-Layer Kernel Model** that separates concerns between agent logic, orchestration, and infrastructure:

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              SYSTEM ARCHITECTURE                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                        USER SPACE (Agent Logic)                          │   │
│   │  ┌───────────────┐  ┌────────────────┐  ┌──────────────────────────┐   │   │
│   │  │  LangGraph    │  │  Co-design     │  │  Skill Repository        │   │   │
│   │  │  Workflows    │  │  Assistant     │  │  (Reusable Capabilities) │   │   │
│   │  └───────────────┘  └────────────────┘  └──────────────────────────┘   │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                      │                                           │
│                                      ▼                                           │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                      KERNEL SPACE (Orchestration)                        │   │
│   │  ┌───────────────┐  ┌────────────────┐  ┌──────────────────────────┐   │   │
│   │  │  Scheduler    │  │  State Manager │  │  Partition Service       │   │   │
│   │  │  (Dynamic)    │  │  (S3 Offload)  │  │  (Segment Chunking)      │   │   │
│   │  └───────────────┘  └────────────────┘  └──────────────────────────┘   │   │
│   │  ┌───────────────┐  ┌────────────────┐  ┌──────────────────────────┐   │   │
│   │  │  Model Router │  │  Glass-Box     │  │  Checkpoint Service      │   │   │
│   │  │  (LLM Select) │  │  Callback      │  │  (Time Machine)          │   │   │
│   │  └───────────────┘  └────────────────┘  └──────────────────────────┘   │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                      │                                           │
│                                      ▼                                           │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                   HARDWARE ABSTRACTION (Serverless)                      │   │
│   │  ┌───────────────┐  ┌────────────────┐  ┌──────────────────────────┐   │   │
│   │  │  AWS Lambda   │  │  Step Functions│  │  DynamoDB / S3           │   │   │
│   │  │  (Compute)    │  │  (State Machine│  │  (Persistence)           │   │   │
│   │  └───────────────┘  └────────────────┘  └──────────────────────────┘   │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
backend/src/
├── handlers/                 # Lambda entry points (Thin Controllers)
│   ├── core/                 # Core workflow handlers
│   │   ├── run_workflow.py           # Main API entry point
│   │   ├── segment_runner_handler.py # Segment execution
│   │   ├── agentic_designer_handler.py # AI workflow generation
│   │   └── ...
│   ├── simulator/            # Mission simulator handlers
│   └── utils/                # Utility handlers (CRUD, WebSocket)
│
├── services/                 # Business logic layer
│   ├── llm/                  # LLM provider integrations
│   │   ├── gemini_service.py
│   │   ├── bedrock_service.py
│   │   └── structure_tools.py
│   ├── state/                # State management
│   │   ├── state_manager.py
│   │   └── state_persistence_service.py
│   ├── execution/            # Segment execution
│   ├── distributed/          # Distributed Map services
│   ├── recovery/             # Self-healing services
│   └── workflow/             # Orchestration services
│
├── common/                   # Shared utilities
│   ├── statebag.py           # Kernel-level state normalization
│   ├── pagination_utils.py   # Token encoding with HMAC
│   ├── model_router.py       # Intelligent model selection
│   └── ...
│
└── models/                   # Data models and schemas
```

---

## 2. Kernel Layer Design

### 2.1 Handler Pattern: Thin Controllers

Analemma follows the **"Tiny Handler"** pattern where Lambda handlers are thin routing layers that delegate all business logic to services:

```python
# handlers/core/run_workflow.py (Entry Point)
def lambda_handler(event, context):
    # 1. Authentication
    owner_id = extract_owner_id_from_event(event)
    
    # 2. Idempotency Check
    if check_existing_execution(idempotency_key):
        return existing_execution
    
    # 3. Orchestrator Selection (Delegate to Service)
    orchestrator = select_orchestrator(workflow)
    
    # 4. Start Step Functions Execution
    return start_execution(orchestrator, workflow, owner_id)
```

### 2.2 Service Layer Responsibilities

| Service | Responsibility |
|---------|----------------|
| `OrchestratorService` | Workflow validation, graph building, execution management |
| `SegmentRunnerService` | Individual segment execution with Tiny Handler pattern |
| `StateManager` | S3 offloading when payload > 256KB |
| `CheckpointService` | Time Machine timeline and state diff management |
| `SelfHealingService` | Automatic error analysis and recovery injection |

### 2.3 Orchestrator Selection Algorithm

The system dynamically selects between **Standard** and **Distributed Map** orchestrators based on workflow complexity:

```python
# services/workflow_orchestrator_selector.py

def select_orchestrator(workflow: dict) -> str:
    analysis = analyze_complexity(workflow)
    
    # Complexity scoring (0-100)
    score = (
        analysis['segment_count'] * 0.3 +
        analysis['parallel_groups'] * 0.2 +
        analysis['hitp_nodes'] * 0.15 +
        analysis['event_estimate'] * 0.35
    )
    
    if analysis['segment_count'] >= 300:
        return "DISTRIBUTED_MAP"
    if analysis['event_estimate'] > 20000:
        return "DISTRIBUTED_MAP"
    if score >= 80:
        return "DISTRIBUTED_MAP"
    
    return "STANDARD"
```

**Complexity Analysis Factors:**
- `segment_count`: Total executable segments
- `parallel_groups`: Number of parallel branch groups
- `hitp_nodes`: Human-in-the-Loop pause points
- `event_estimate`: Predicted Step Functions events (25K limit consideration)

---

## 3. State Management System

### 3.1 StateBag: Kernel-Level State Normalization

The `StateBag` module (`common/statebag.py`) provides kernel-level state protection and normalization:

#### Kernel Protected Fields

```python
KERNEL_PROTECTED_FIELDS = frozenset({
    # Execution Identity
    "execution_id", "execution_arn", "workflow_id",
    
    # User Identity (Immutable)
    "owner_id", "user_id", "auth_context", "permissions", "tenant_id",
    
    # Kernel Metadata
    "_kernel_version", "_created_at", "_task_token",
    
    # Additional reserved fields
    "trace_id", "parent_execution_id", "segment_index"
})
```

These fields **cannot be overwritten** by user workflows or external inputs, ensuring security and execution integrity.

#### Deep Merge Algorithm

```python
def deep_merge(base: dict, overlay: dict, protect_kernel_fields: bool = True) -> dict:
    """
    Recursively merges overlay into base with kernel field protection.
    
    Example:
        base = {"config": {"timeout": 30}, "name": "test"}
        overlay = {"config": {"retries": 3, "timeout": 60}}
        
        Result (protect_kernel_fields=True):
        → {"config": {"timeout": 30, "retries": 3}, "name": "test"}
        
        Note: base["config"]["timeout"] preserved because it existed first
    """
```

#### JIT Schema Validation

```python
def validate_state(event: dict, 
                   required_fields: list = None,
                   field_types: dict = None,
                   raise_on_error: bool = False) -> ValidationResult:
    """
    Runtime schema validation without static type definitions.
    
    Example:
        validate_state(event, 
                       required_fields=["workflow_id", "owner_id"],
                       field_types={"timeout": int, "retries": int})
    """
```

### 3.2 S3 State Offloading

AWS Lambda and Step Functions have payload limits. Analemma automatically offloads large states to S3:

```python
# services/state/state_manager.py

class StateManager:
    DEFAULT_THRESHOLD = 256 * 1024  # 256KB
    
    def handle_state_storage(self, state: dict, threshold: int = None) -> tuple:
        serialized = json.dumps(state)
        
        if len(serialized) > (threshold or self.DEFAULT_THRESHOLD):
            # Offload to S3
            s3_path = f"state/{execution_id}/{segment_id}/state.json"
            self._upload_to_s3(s3_path, serialized)
            return {"_state_s3_path": s3_path}, s3_path
        
        return state, None
```

**S3 Path Structure:**
```
s3://analemma-state-bucket/
├── state/
│   └── {execution_id}/
│       └── {segment_id}/
│           └── state.json
```

### 3.3 WorkflowState Schema

```python
# LangGraph-compatible state definition
class WorkflowState(TypedDict, total=False):
    # User Context
    user_query: str
    user_api_keys: Dict[str, str]
    
    # Execution Tracking
    step_history: List[str]
    messages: Annotated[List[Dict], add_messages]  # Accumulating reducer
    
    # Skills Integration
    injected_skills: List[str]
    active_skills: Dict[str, Any]
    skill_execution_log: Annotated[List[Dict], operator.add]
    
    # Glass-Box Observability
    glass_box_logs: List[Dict]
```

---

## 4. Workflow Execution Engine

### 4.1 Step Functions State Machine

The core execution is driven by AWS Step Functions with this flow:

```
┌──────────────────────────────────────────────────────────────────┐
│                    Step Functions State Machine                   │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│   CheckForInjectedConfig                                         │
│           │                                                       │
│           ▼                                                       │
│   CheckForExistingExecution (Idempotency)                        │
│           │                                                       │
│           ▼                                                       │
│   InitializeStateData (Lambda)                                   │
│           │                                                       │
│           ▼                                                       │
│   ┌─────────────────────────────────────────────────┐            │
│   │            RunSegment Loop                       │            │
│   │                    │                             │            │
│   │                    ▼                             │            │
│   │   segment_runner_handler.lambda_handler          │            │
│   │                    │                             │            │
│   │                    ▼                             │            │
│   │           CheckSegmentResult                     │            │
│   │      ┌─────────┬──────┬─────────────┐           │            │
│   │      ▼         ▼      ▼             ▼           │            │
│   │  COMPLETE  CONTINUE  PAUSED    PARALLEL_GROUP   │            │
│   │      │         │      │             │           │            │
│   │      ▼         │      ▼             ▼           │            │
│   │   Succeed   ───┘   StoreToken    MapState      │            │
│   │                        │                        │            │
│   │                        ▼                        │            │
│   │               WaitForCallback                   │            │
│   │                        │                        │            │
│   │                        ▼                        │            │
│   │               ResumeFromHITP ───────────────────┘            │
│   └─────────────────────────────────────────────────┘            │
│                                                                   │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 Segment Partitioning

Workflows are partitioned into **segments** at specific boundaries:

```python
# LLM nodes trigger segment boundaries
LLM_NODE_TYPES = {
    "llm_chat", "openai_chat", "anthropic_chat", 
    "gemini_chat", "aiModel", "agent"
}

# HITP edges trigger segment boundaries
HITP_EDGE_TYPES = {
    "hitp", "human_in_the_loop", "pause", "approval"
}

def partition_workflow(workflow: dict) -> List[Segment]:
    segments = []
    current_segment = Segment()
    
    for node in topological_sort(workflow['nodes']):
        current_segment.add_node(node)
        
        if node['type'] in LLM_NODE_TYPES:
            segments.append(current_segment)
            current_segment = Segment()
        
        for edge in get_outgoing_edges(node):
            if edge['type'] in HITP_EDGE_TYPES:
                segments.append(current_segment)
                current_segment = Segment()
    
    return segments
```

### 4.3 Human-in-the-Loop (HITP) Integration

When a workflow reaches an HITP node:

```python
# 1. Store Task Token (Step Functions callback integration)
def store_task_token(event: dict):
    table.put_item(Item={
        'pk': execution_id,
        'sk': f'HITP#{segment_id}',
        'taskToken': event['task_token'],
        'state_data': event['current_state'],
        'ttl': int(time.time()) + 86400  # 24 hour expiry
    })

# 2. Notify via WebSocket
def notify_hitp_required(owner_id: str, execution_id: str, prompt: str):
    connections = get_connections_for_owner(owner_id)
    broadcast_to_connections(connections, {
        'type': 'hitp_required',
        'execution_id': execution_id,
        'prompt': prompt
    })

# 3. Resume when user responds
def resume_from_hitp(task_token: str, user_response: dict):
    sfn_client.send_task_success(
        taskToken=task_token,
        output=json.dumps({
            'userResponse': user_response,
            'resumeTimestamp': datetime.utcnow().isoformat()
        })
    )
```

---

## 5. Distributed Execution (Distributed Map)

For large workflows (300+ segments), Analemma uses AWS Step Functions Distributed Map:

### 5.1 Chunking Algorithm

```python
# handlers/core/prepare_distributed_execution.py

def prepare_chunks(partition_map: List[Segment], 
                   max_chunks: int = 100,
                   chunk_size: int = 50) -> List[Chunk]:
    """
    Splits segments into chunks for parallel processing.
    
    Chunk Structure:
    {
        "chunk_id": "chunk_0001",
        "start_segment": 100,
        "end_segment": 199,
        "segment_count": 100,
        "partition_slice": [...],
        "idempotency_key": "workflow_123#chunk#0001"
    }
    """
    total = len(partition_map)
    optimal_size = min(chunk_size, max(10, total // max_chunks))
    
    chunks = []
    for i in range(0, total, optimal_size):
        chunks.append(Chunk(
            chunk_id=f"chunk_{i:04d}",
            start_segment=i,
            end_segment=min(i + optimal_size, total),
            partition_slice=partition_map[i:i + optimal_size]
        ))
    
    return chunks
```

### 5.2 Distributed Map Flow

```
┌────────────────────────────────────────────────────────────────────┐
│                    Distributed Map Execution                        │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   prepare_distributed_execution                                     │
│   (Chunking + S3 Upload)                                           │
│           │                                                         │
│           ▼                                                         │
│   ┌─────────────────────────────────────────────────────────────┐  │
│   │                   Distributed Map                            │  │
│   │                   (ItemReader: S3)                           │  │
│   │                         │                                    │  │
│   │    ┌────────────────────┼────────────────────┐              │  │
│   │    ▼                    ▼                    ▼              │  │
│   │ ┌────────┐         ┌────────┐          ┌────────┐          │  │
│   │ │Chunk 1 │         │Chunk 2 │   ...    │Chunk N │          │  │
│   │ │Worker  │         │Worker  │          │Worker  │          │  │
│   │ └────────┘         └────────┘          └────────┘          │  │
│   │    │                    │                    │              │  │
│   └────┼────────────────────┼────────────────────┼──────────────┘  │
│        │                    │                    │                  │
│        └────────────────────┼────────────────────┘                  │
│                             ▼                                       │
│   aggregate_distributed_results                                     │
│   (K-way Merge + Final State)                                      │
│                             │                                       │
│                             ▼                                       │
│                        Final Output                                 │
│                                                                     │
└────────────────────────────────────────────────────────────────────┘
```

### 5.3 Result Aggregation

```python
# handlers/core/aggregate_distributed_results.py

def aggregate_results(chunk_results: List[dict]) -> dict:
    # Classify results
    successful = [r for r in chunk_results if r['status'] == 'COMPLETED']
    failed = [r for r in chunk_results if r['status'] == 'FAILED']
    paused = [r for r in chunk_results if r['status'] == 'PAUSED_FOR_HITP']
    
    # K-way merge for sorted logs (heap-based)
    merged_logs = []
    heap = []
    for chunk in successful:
        for log in chunk['logs']:
            heapq.heappush(heap, (log['timestamp'], chunk['chunk_id'], log))
    
    while heap:
        _, _, log = heapq.heappop(heap)
        merged_logs.append(log)
    
    # Apply failure policy
    if failed and FAIL_ON_ANY_FAILURE:
        return {'status': 'FAILED', 'failed_chunks': failed}
    
    return {
        'status': 'COMPLETED',
        'merged_state': merge_states(successful),
        'execution_logs': merged_logs
    }
```

---

## 6. LLM Integration Layer

### 6.1 Multi-Provider Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Model Router                            │
│            (model_router.py - Intelligent Selection)         │
├─────────────────────────────────────────────────────────────┤
│                            │                                 │
│    ┌───────────────────────┼───────────────────────┐        │
│    ▼                       ▼                       ▼        │
│ ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐ │
│ │GeminiService │   │BedrockService│   │  (Future: OpenAI)│ │
│ │(Native API)  │   │(Claude/Llama)│   │                  │ │
│ └──────────────┘   └──────────────┘   └──────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 Model Selection Algorithm

```python
# common/model_router.py

NEGATION_PATTERNS = ["without", "except", "no", "disable", ...]

def select_optimal_model(request: str, canvas_mode: str, workflow: dict) -> str:
    """
    Selects the best model based on:
    1. Semantic intent (with negation detection)
    2. Context length requirements
    3. Latency requirements
    4. Canvas mode (agentic-designer vs co-design)
    """
    
    # Detect structural needs (loop, parallel, conditional)
    needs_structure, confidence = detect_structure_intent(request)
    
    # Check for negation: "without loops" should not trigger structure model
    if _is_negated_keyword(request, detected_keyword):
        needs_structure = False
    
    # Model selection logic
    if canvas_mode == "agentic-designer" or needs_structure:
        return "gemini-2.0-flash"  # Best for full generation
    
    if needs_long_context(request, workflow):
        return "gemini-1.5-pro"   # 1M token context
    
    if requires_low_latency(canvas_mode):
        return "gemini-1.5-flash" # 100ms TTFT
    
    return "gemini-1.5-flash"     # Default
```

### 6.3 Streaming Implementation

```python
# services/llm/gemini_service.py

def stream_with_incremental_decoder(prompt: str) -> Generator[str, None, None]:
    """
    Streaming with UTF-8 incremental decoder to prevent
    multi-byte character corruption (CJK, emoji).
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    buffer = ""
    
    for chunk in model.generate_content(prompt, stream=True):
        if chunk.text:
            # Decode incrementally (handles partial UTF-8 sequences)
            text = decoder.decode(chunk.text.encode(), final=False)
            buffer += text
            
            # Yield complete JSONL lines
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if line.strip():
                    yield line + "\n"
    
    # Final flush
    tail = decoder.decode(b"", final=True)
    if buffer.strip() or tail.strip():
        yield (buffer + tail).strip() + "\n"
```

### 6.4 Response Schema Enforcement

```python
# Gemini Native Response Schema for workflow generation
WORKFLOW_NODE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string", "enum": ["start", "end", "llm_chat", ...]},
        "data": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "config": {"type": "object"}
            }
        },
        "position": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"}
            }
        }
    },
    "required": ["id", "type", "data", "position"]
}

# Applied in generation config
generation_config = {
    "response_mime_type": "application/json",
    "response_schema": {
        "type": "array",
        "items": {"anyOf": [NODE_SCHEMA, EDGE_SCHEMA, STATUS_SCHEMA]}
    }
}
```

---

## 7. Recovery & Self-Healing

### 7.1 Self-Healing Service

```python
# services/recovery/self_healing_service.py

class SelfHealingService:
    """
    Learns from previous execution failures and injects
    recovery instructions into prompts.
    """
    
    ADVICE_TAG = "<!-- ANALEMMA_SELF_HEALING_ADVICE -->"
    SANDBOX_START = "<SYSTEM_ADVICE>"
    SANDBOX_END = "</SYSTEM_ADVICE>"
    
    def inject_healing_advice(self, prompt: str, error_history: list) -> str:
        """
        Sandboxed injection to prevent prompt injection attacks.
        """
        if not error_history:
            return prompt
        
        # Generate fix instructions from error patterns
        fix_instruction = self._analyze_errors(error_history)
        
        # Security: Escape closing tags to prevent sandbox escape
        safe_instruction = fix_instruction.replace(
            self.SANDBOX_END, 
            "[POTENTIAL ATTACK: CLOSING TAG REMOVED]"
        )
        
        sandboxed_advice = f"""
{self.ADVICE_TAG}
{self.SANDBOX_START}
SYSTEM WARNING: The following is automated advice from error history.
{safe_instruction}
{self.SANDBOX_END}
"""
        return self._inject_idempotent(prompt, sandboxed_advice)
```

### 7.2 Resume Handler (HITP Recovery)

```python
# handlers/core/resume_handler.py

def lambda_handler(event, context):
    # SECURITY: JWT-only authentication (ignore body.ownerId)
    owner_id = event['requestContext']['authorizer']['jwt']['claims']['sub']
    
    # SECURITY: Strip forbidden fields from request
    body = parse_body(event)
    for forbidden in ('current_state', 'state_s3_path', 'final_state'):
        body.pop(forbidden, None)
    
    # Retrieve stored task token
    task_token = get_task_token(execution_id)
    
    # Resume Step Functions execution
    sfn.send_task_success(
        taskToken=task_token,
        output=json.dumps({
            'userResponse': body.get('user_response'),
            'state_data': stored_state_data
        })
    )
    
    # Conditional delete to prevent token reuse
    table.delete_item(
        Key={'pk': execution_id, 'sk': f'HITP#{segment_id}'},
        ConditionExpression='taskToken = :tt',
        ExpressionAttributeValues={':tt': task_token}
    )
```

### 7.3 Checkpoint Service (Time Machine)

```python
# services/checkpoint_service.py

class CheckpointService:
    async def get_execution_timeline(self, execution_id: str) -> List[dict]:
        """Returns chronological execution events for debugging."""
        return self._query_notifications_by_execution(execution_id)
    
    async def compare_checkpoints(self, 
                                   checkpoint_a: str, 
                                   checkpoint_b: str) -> dict:
        """Diff between two checkpoints for debugging."""
        state_a = await self._get_checkpoint_state(checkpoint_a)
        state_b = await self._get_checkpoint_state(checkpoint_b)
        
        return {
            "added_keys": list(set(state_b.keys()) - set(state_a.keys())),
            "removed_keys": list(set(state_a.keys()) - set(state_b.keys())),
            "modified_keys": [
                k for k in state_a 
                if k in state_b and state_a[k] != state_b[k]
            ]
        }
```

---

## 8. Security Architecture

### 8.1 Authentication Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    JWT Authentication Flow                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   API Request                                                │
│       │                                                      │
│       ▼                                                      │
│   ┌─────────────────────────────────────────────────────┐   │
│   │ Fast Path: Check requestContext.authorizer          │   │
│   │   ├─ HTTP API: jwt.claims.sub                       │   │
│   │   └─ REST API: claims.sub or principalId            │   │
│   └─────────────────────────────────────────────────────┘   │
│       │                                                      │
│       ▼ (if not found)                                       │
│   ┌─────────────────────────────────────────────────────┐   │
│   │ Slow Path: Parse Authorization header               │   │
│   │   ├─ Extract Bearer token                           │   │
│   │   ├─ Fetch JWKS from Cognito (cached 1hr)          │   │
│   │   └─ Verify signature + claims                      │   │
│   └─────────────────────────────────────────────────────┘   │
│       │                                                      │
│       ▼                                                      │
│   owner_id (Cognito 'sub' claim)                            │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 8.2 Kernel Field Protection

The kernel protects critical fields from user manipulation:

```python
# When processing user input
def normalize_event(event: dict, protect_kernel: bool = True) -> dict:
    normalized = {}
    
    for key, value in event.items():
        if protect_kernel and key in KERNEL_PROTECTED_FIELDS:
            # Preserve original kernel value, ignore user input
            if key in original_kernel_state:
                normalized[key] = original_kernel_state[key]
            continue
        
        normalized[key] = value
    
    return normalized
```

### 8.3 Pagination Token Security

```python
# HMAC-signed pagination tokens prevent tampering
def encode_pagination_token(last_evaluated_key: dict, 
                            ttl_seconds: int = 3600) -> str:
    payload = {
        "k": last_evaluated_key,
        "v": 2,  # Token version
        "e": int(time.time()) + ttl_seconds  # Expiration
    }
    
    encoded = urlsafe_b64encode(json.dumps(payload))
    signature = hmac.new(SECRET_KEY, encoded, 'sha256').digest()[:8]
    
    return f"{encoded}.{urlsafe_b64encode(signature)}"
```

---

## Summary: Design Principles

| Principle | Implementation |
|-----------|----------------|
| **Separation of Concerns** | 3-Layer Model (User/Kernel/Hardware) |
| **Tiny Handler Pattern** | Handlers route, Services execute |
| **Kernel Protection** | Immutable fields for security/integrity |
| **Automatic Scaling** | Standard → Distributed Map based on complexity |
| **Fault Tolerance** | Self-healing + Time Machine recovery |
| **Observability** | Glass-Box callbacks for AI transparency |

---

> [← Back to Main README](../README.md)
