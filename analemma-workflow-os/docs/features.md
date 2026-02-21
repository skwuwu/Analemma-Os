# Features Guide

> [Back to Main README](../README.md)

This document covers Analemma OS features: the Co-design Assistant, workflow execution controls, observability tools, and operational utilities.

---

## Table of Contents

1. [Co-design Assistant](#1-co-design-assistant)
2. [Agentic Designer](#2-agentic-designer)
3. [Human-in-the-Loop (HITP)](#3-human-in-the-loop-hitp)
4. [Time Machine Debugging](#4-time-machine-debugging)
5. [Glass-Box Observability](#5-glass-box-observability)
6. [Self-Healing and Recovery](#6-self-healing-and-recovery)
7. [Mission Simulator](#7-mission-simulator)
8. [Skill Repository](#8-skill-repository)
9. [Real-time Monitoring](#9-real-time-monitoring)
10. [Model Router](#10-model-router)
11. [Instruction Distiller](#11-instruction-distiller)
12. [Task Manager](#12-task-manager)
13. [Scheduled Workflows](#13-scheduled-workflows)

---

## 1. Co-design Assistant

The Co-design Assistant enables natural language workflow editing through real-time AI collaboration.

### 1.1 Overview

```
User: "Add error handling to the API calls"
                 |
                 v
+------------------------------------------------------------+
|              Natural Language Processing                    |
|   - Intent Detection (structure needs, complexity)          |
|   - Negation Awareness ("without loops")                    |
|   - Context Analysis (existing workflow state)              |
+------------------------------------------------------------+
                 |
                 v
+------------------------------------------------------------+
|              Workflow Modification                          |
|   - Generate new nodes/edges                               |
|   - Suggest optimizations                                  |
|   - Explain changes                                        |
+------------------------------------------------------------+
                 |
                 v
Output: JSONL stream of node, edge, and suggestion updates
```

### 1.2 Capabilities

| Feature | Description |
|---|---|
| Natural Language Editing | Describe changes in plain English |
| Incremental Updates | Modify existing workflows without rebuilding |
| Smart Suggestions | AI-powered optimization recommendations |
| Real-time Streaming | See changes as they are generated |
| Context Awareness | Understands existing workflow structure |

### 1.3 Supported Commands

**Structural Commands:**
- "Add a loop that processes each item in the list"
- "Create a parallel branch for image and text processing"
- "Add error handling with retry logic"
- "Insert a human approval step before execution"

**Optimization Commands:**
- "Optimize this workflow for speed"
- "Reduce the number of API calls"
- "Add caching to expensive operations"

**Query Commands:**
- "Explain what this workflow does"
- "What are the bottlenecks in this flow?"
- "How can I improve error handling?"

### 1.4 Output Format

The Co-design Assistant streams responses in JSONL format:

```jsonl
{"type": "node", "data": {"id": "node_retry", "type": "retry_wrapper", "position": {"x": 150, "y": 250}}}
{"type": "edge", "data": {"source": "node_api", "target": "node_retry"}}
{"type": "suggestion", "data": {"text": "Consider adding a circuit breaker", "confidence": 0.85}}
{"type": "audit", "data": {"level": "info", "message": "Added retry wrapper with 3 attempts"}}
{"type": "status", "data": "done"}
```

---

## 2. Agentic Designer

The Agentic Designer generates complete workflows from scratch based on natural language descriptions.

### 2.1 When It Activates

The system switches to Agentic Designer mode when:

- Canvas is empty (no nodes or edges)
- No conversation history exists
- User explicitly requests full workflow generation

### 2.2 Generation Process

```
User Request: "Create a workflow that monitors social media
               mentions and sends alerts for negative sentiment"
                              |
                              v
+------------------------------------------------------------+
|                    Agentic Designer                         |
|  1. Parse intent and requirements                           |
|  2. Select appropriate node types                           |
|  3. Design optimal graph structure                          |
|  4. Calculate layout positions                              |
|  5. Generate edges with proper connections                  |
|  6. Stream output in JSONL format                           |
+------------------------------------------------------------+
                              |
                              v
Generated Workflow:
+-----+    +-----------+    +-----------+    +---------+    +-----+
|Start|   >| API Fetch |   >| Sentiment |   >|Condition|   >| End |
+-----+    | (Twitter) |    | Analysis  |    | (< 0.3) |    +-----+
           +-----------+    +-----------+    +----+----+
                                                  |
                                                  v
                                            +-----------+
                                            |Send Alert |
                                            | (Slack)   |
                                            +-----------+
```

### 2.3 Node Types Supported

| Category | Node Types |
|---|---|
| Control Flow | start, end, condition, loop, parallel |
| AI/LLM | llm_chat, gemini_chat, anthropic_chat |
| Data | api_call, database_query, transform |
| Integration | webhook, email, slack, custom |
| Human | hitp, approval, input_form |

### 2.4 Layout Rules

```
Layout Algorithm:
  Sequential nodes: X=150, Y increases by 100
  Parallel branches: Same Y, X spreads by 200
  Conditional branches: Left (true), Right (false)
  Loop internals: X offset +50 for nesting
```

---

## 3. Human-in-the-Loop (HITP)

HITP provides physical pause points in workflow execution for human oversight and approval. When an HITP node is reached, the kernel stores a Step Functions task token in DynamoDB and suspends execution. Resumption requires the task token to be returned via the API.

### 3.1 HITP Flow

```
Workflow Execution
      |
      v
+------------------------------------------+
|         HITP Node Reached                 |
|   - Store Task Token in DynamoDB          |
|   - Persist current state to S3           |
|   - Send WebSocket notification           |
+------------------------------------------+
      |
      v
+------------------------------------------+
|     Step Functions WAIT State             |
|   - Execution paused                      |
|   - 24-hour timeout (configurable)        |
+------------------------------------------+
      |
      | User Response via API/WebSocket
      v
+------------------------------------------+
|     Resume Execution                      |
|   - Validate user response               |
|   - Inject response into state           |
|   - Continue workflow                     |
+------------------------------------------+
```

### 3.2 Use Cases

| Use Case | Description |
|---|---|
| Approval Gates | Require human approval before critical actions |
| Data Validation | Human review of AI-generated content |
| Exception Handling | Manual intervention for edge cases |
| Compliance | Audit trail with human sign-off |

### 3.3 Notification Channels

When HITP is triggered:

1. **WebSocket Push** — Real-time in-app notification
2. **Notification Center** — Persistent notification stored in DynamoDB
3. **Email/SMS** — Optional external notifications (configurable)

---

## 4. Time Machine Debugging

Time Machine enables checkpoint-based debugging and state inspection throughout workflow execution. Every segment execution produces a Merkle-linked manifest. The Time Machine queries DynamoDB for the manifest chain and reconstructs state at any historical point.

### 4.1 Features

| Feature | Description |
|---|---|
| Execution Timeline | View all events in chronological order |
| Checkpoint Snapshots | Full state captured at each segment via Merkle DAG |
| State Diff | Compare state between any two manifests |
| Resume from Checkpoint | Restart execution from any approved manifest |

### 4.2 Timeline View

```
Execution Timeline: exec_xyz789
------------------------------------------------------------------------
10:05:00.000 | START        | Execution initiated
10:05:00.150 | SEGMENT_0    | Started segment 0
10:05:01.200 | LLM_CALL     | gemini-3-pro (1,250 tokens)
10:05:02.050 | SEGMENT_0    | Completed - manifest_id: abc001
10:05:02.100 | SEGMENT_1    | Started segment 1
10:05:03.500 | API_CALL     | external-api.com/data
10:05:04.200 | SEGMENT_1    | Completed - manifest_id: abc002
10:05:04.250 | HITP         | Waiting for human approval
10:15:00.000 | RESUME       | User approved
10:15:00.100 | SEGMENT_2    | Started segment 2
10:15:01.500 | END          | Execution completed - manifest_id: abc003
------------------------------------------------------------------------
```

### 4.3 Checkpoint Comparison

```json
{
  "manifest_a": "abc001",
  "manifest_b": "abc003",
  "added_keys": ["processed_items", "sentiment_scores"],
  "removed_keys": ["temp_buffer"],
  "modified_keys": ["counter", "status", "results"],
  "state_diff": {
    "counter": { "before": 0, "after": 150 },
    "status": { "before": "initializing", "after": "processing" },
    "results": { "before": [], "after": ["...150 items..."] }
  }
}
```

---

## 5. Glass-Box Observability

Glass-Box provides transparent AI decision-making by streaming all LLM interactions, tool usage, and Governor decisions to connected WebSocket clients in real time. The `trace_id` kernel-protected field correlates all events across distributed Lambda invocations.

### 5.1 What Is Logged

| Event Type | Data Captured |
|---|---|
| `ai_thought` | LLM prompts, responses, Thinking Mode reasoning chain |
| `tool_usage` | Tool calls, inputs, outputs |
| `decision` | Branch decisions, conditions evaluated |
| `governance_result` | Governor APPROVED / REJECTED / ROLLBACK decisions |
| `kernel_event` | State transitions, offload operations, ring boundary crossings |
| `error` | Failures with stack traces |

### 5.2 Log Structure

```json
{
  "type": "ai_thought",
  "timestamp": "2026-01-14T10:05:01.200Z",
  "trace_id": "exec_xyz789",
  "segment_id": 0,
  "data": {
    "model": "gemini-3-pro",
    "prompt_preview": "Analyze the sentiment of...",
    "response_preview": "The sentiment is negative...",
    "tokens": {
      "input": 850,
      "output": 400,
      "total": 1250
    },
    "duration_ms": 850,
    "cost_usd": 0.00015
  }
}
```

### 5.3 Ring-Based PII Filtering

Sensitive fields are filtered according to the Ring protection policy before appearing in Glass-Box streams. At Ring 3 (agent output level), email addresses are SHA-256 hashed and SSN / password fields are fully redacted. This filtering is applied by `StateViewContext` prior to streaming and cannot be bypassed by agent output.

---

## 6. Self-Healing and Recovery

Analemma OS includes automatic error recovery through Gemini-powered diagnostics. When a segment fails, the Governor checks the circuit breaker count and, if within the retry budget, passes the error context to Gemini for diagnosis.

### 6.1 Self-Healing Process

```
Execution Fails
      |
      v
+------------------------------------------+
|     Governor: Error Analysis              |
|   - Parse error type and message         |
|   - Check circuit breaker count          |
|   - Retrieve error history from state    |
+------------------------------------------+
      |
      | Within retry budget
      v
+------------------------------------------+
|     Gemini: Diagnosis                     |
|   - Full execution context injected      |
|   - 2M token window for history analysis |
|   - Returns targeted recovery action     |
+------------------------------------------+
      |
      v
+------------------------------------------+
|     Kernel: SOFT_ROLLBACK                 |
|   - Inject fix instruction into state    |
|   - Retry current segment               |
|   - Increment circuit breaker counter   |
+------------------------------------------+
```

Recovery actions are targeted, not generic retries. Example:

```
"Previous 3 attempts failed due to JSON parsing errors.
 Injecting structured output enforcement schema into next prompt."
```

### 6.2 Recovery Strategies

| Error Type | Governor Decision | Recovery Action |
|---|---|---|
| LLM output format error | `SOFT_ROLLBACK` | Inject format enforcement schema |
| Gas fee exceeded | `SOFT_ROLLBACK` | Inject token budget instruction |
| Plan drift detected | `SOFT_ROLLBACK` | Inject original plan reminder |
| Circuit breaker exhausted | `HARD_ROLLBACK` | Restore last approved manifest |
| Kernel command forgery | `TERMINAL_HALT` | Immediate workflow termination |

### 6.3 Fix Instruction Injection

Fix instructions are injected into the next segment's prompt context via a sandboxed key in the state bag (`_kernel_inject_recovery`). This key is in `KERNEL_CONTROL_KEYS` — Ring 3 agents cannot write to it. Only the Governor (Ring 1) can set recovery instructions.

---

## 7. Mission Simulator

The Mission Simulator is a stress-testing suite that validates workflow resilience against real-world failure scenarios before production deployment.

### 7.1 Simulated Scenarios

| Scenario | Description |
|---|---|
| Network Latency | Introduces random delays (100ms–5s) |
| LLM Hallucination | Returns invalid or structurally malformed responses |
| Rate Limiting | Simulates 429 responses with Retry-After headers |
| Timeout | Forces request timeouts at configurable thresholds |
| Partial Failure | Some nodes succeed, others fail (tests partial success paths) |
| State Corruption | Injects invalid state data to test USC defensive guards |
| Concurrent Load | Parallel execution stress test |
| Memory Pressure | Large payload handling to test L1-L5 offload cascade |

### 7.2 Running Simulations

```bash
# Run all simulation scenarios
python -m tests.simulator.run_all

# Run specific scenario
python -m tests.simulator.trigger_test --scenario network_latency

# Run with custom parameters
python -m tests.simulator.trigger_test \
  --scenario concurrent_load \
  --concurrency 50 \
  --duration 300
```

### 7.3 Report Output

```
Mission Simulator Report
====================================================================
Scenario: concurrent_load
Duration: 300s
Concurrency: 50 parallel executions

Results:
  Total Executions: 1,247
  Successful: 1,231 (98.7%)
  Failed: 12 (1.0%)
  Timed Out: 4 (0.3%)

Performance:
  Avg Latency: 2.3s
  P95 Latency: 4.8s
  P99 Latency: 7.2s

Resource Usage:
  Peak Lambda Concurrency: 48
  DynamoDB RCU: 450/500
  DynamoDB WCU: 380/500
====================================================================
```

---

## 8. Skill Repository

The Skill Repository provides reusable, versioned workflow components that can be referenced inline in workflow definitions.

### 8.1 Skill Structure

```json
{
  "skill_id": "skill_csv_parser",
  "name": "CSV Parser",
  "version": "1.2.0",
  "category": "data-processing",
  "description": "Parses CSV files with configurable delimiters and headers",
  "schema": {
    "input": {
      "type": "object",
      "properties": {
        "file_url": { "type": "string", "format": "uri" },
        "delimiter": { "type": "string", "default": "," },
        "has_headers": { "type": "boolean", "default": true }
      },
      "required": ["file_url"]
    },
    "output": {
      "type": "object",
      "properties": {
        "rows": { "type": "array" },
        "headers": { "type": "array" },
        "row_count": { "type": "integer" }
      }
    }
  },
  "subgraph": {
    "nodes": [],
    "edges": []
  }
}
```

### 8.2 Skill Categories

| Category | Examples |
|---|---|
| Data Processing | CSV Parser, JSON Transformer, Data Validator |
| AI/ML | Sentiment Analysis, Text Summarizer, Image Classifier |
| Integration | Slack Notifier, Email Sender, Webhook Caller |
| Utility | Rate Limiter, Cache Manager, Error Handler |

### 8.3 Using Skills in Workflows

```json
{
  "id": "node_parse_data",
  "type": "skill",
  "data": {
    "skill_id": "skill_csv_parser",
    "skill_version": "1.2.0",
    "config": {
      "delimiter": ";",
      "has_headers": true
    }
  }
}
```

---

## 9. Real-time Monitoring

Analemma OS provides real-time monitoring through WebSocket connections and CloudWatch integration.

### 9.1 Dashboard Metrics

| Metric | Description |
|---|---|
| Active Executions | Currently running workflows |
| Execution Rate | Workflows started per minute |
| Success Rate | Percentage of successful completions |
| Avg Duration | Average execution time |
| Error Rate | Failed executions per minute |
| HITP Pending | Workflows awaiting human input |

### 9.2 CloudWatch Metrics

```
Namespace: Analemma/Workflow
  ExecutionStarted            (Count)
  ExecutionCompleted          (Count)
  ExecutionFailed             (Count)
  SegmentDuration             (Milliseconds)
  LLMTokensUsed               (Count)
  GovernanceApproved          (Count)
  GovernanceRejected          (Count)
  SelfHealingTriggered        (Count)
  HITPRequested               (Count)
  HITPResponseTime            (Seconds)
  PayloadSizeKB               (Kilobytes)
  S3OffloadTriggered          (Count)
```

### 9.3 Recommended Alarms

| Alarm | Condition | Action |
|---|---|---|
| High Error Rate | ErrorRate > 5% for 5 min | SNS notification |
| Long HITP Wait | HITPPending > 10 for 1 hour | Email alert |
| Lambda Throttling | ThrottleCount > 0 | Scale up concurrency |
| LLM Cost Spike | TokensUsed > 1M in 1 hour | Budget alert |
| Governance Rejection Spike | GovernanceRejected > 10 in 5 min | Operational alert |

---

## 10. Model Router

The Model Router selects the optimal Gemini variant for each request based on context length, task complexity, and latency requirements.

### 10.1 Selection Criteria

```
Input: User request, canvas mode, workflow state
                 |
                 v
+------------------------------------------+
|     Semantic Intent Detection             |
|   - Structure needs (loop, parallel)     |
|   - Negation awareness                   |
|   - Complexity estimation                |
+------------------------------------------+
                 |
                 v
+------------------------------------------+
|     Context Analysis                      |
|   - Workflow size                        |
|   - History length                       |
|   - Token estimation                     |
+------------------------------------------+
                 |
                 v
+------------------------------------------+
|     Requirement Matching                  |
|   - Latency requirements                 |
|   - Context window needs                 |
|   - Cost constraints                     |
+------------------------------------------+
                 |
                 v
Output: Selected model (e.g., gemini-3-pro)
```

### 10.2 Available Models

| Model | Use Case | Context Window | Approx. TTFT |
|---|---|---|---|
| `gemini-3-pro` | Full generation, complex reasoning, self-healing | 2M tokens | ~500ms |
| `gemini-3-flash` | Real-time collaboration, streaming, Distributed Map workers | 1M tokens | ~100ms |
| `gemini-3-flash-lite` | Pre-routing, classification | 1M tokens | ~80ms |
| `claude-3-sonnet` | Fallback, Bedrock integration | 200K tokens | ~1.5s |

### 10.3 Context Caching

Context caching activates automatically for contexts above 32K tokens. System prompts and large reference documents cached via `CachedContent` API are billed at the provider's reduced token rate.

---

## 11. Instruction Distiller

The Instruction Distiller extracts implicit user preferences from HITP corrections and applies them to future executions.

### 11.1 Overview

```
Original Output ----------------> Diff Analysis
      |                                |
      v                                v
User Corrects -----------> Instruction Extraction
      |                                |
      v                                v
Corrected Output            DistilledInstructions DB
                                       |
                                       v
                             Future Executions Apply
```

### 11.2 Instruction Categories

| Category | Description | Example |
|---|---|---|
| `style` | Writing style preferences | "Use active voice instead of passive" |
| `content` | Content requirements | "Always include source citations" |
| `format` | Output formatting rules | "Use bullet points for lists" |
| `tone` | Tone and voice guidelines | "Maintain professional tone" |
| `prohibition` | Things to avoid | "Never use emoji in formal documents" |

### 11.3 Weight Management

Instructions have dynamic weights that decay when repeatedly re-corrected:

| Event | Weight Change |
|---|---|
| New instruction | 1.0 (initial) |
| User re-corrects | -0.3 |
| Below 0.1 threshold | Archived (inactive) |

Maximum 10 active instructions per node. When exceeded, LLM-based semantic compression reduces to 3 core instructions while preserving meaning.

### 11.4 Conflict Resolution

| Conflict Type | Resolution |
|---|---|
| `contradiction` | Keep higher-weight instruction |
| `redundancy` | Merge into single instruction |
| `ambiguity` | LLM-based clarification |

---

## 12. Task Manager

The Task Manager provides a business-friendly abstraction layer over technical workflow executions.

### 12.1 Status Mapping

| Technical Status | Task Status | Display |
|---|---|---|
| `RUNNING` | `in_progress` | "In Progress" |
| `SUCCEEDED` | `completed` | "Completed" |
| `FAILED` | `failed` | "Failed" |
| `TIMED_OUT` | `failed` | "Timed Out" |
| `ABORTED` | `cancelled` | "Cancelled" |
| `PENDING` | `pending` | "Pending" |
| `WAITING_FOR_CALLBACK` | `awaiting_input` | "Awaiting Input" |

### 12.2 Artifact Types

| Type | Preview Strategy |
|---|---|
| `text` | First 500 characters |
| `code` | Syntax-highlighted snippet |
| `image` | Thumbnail URL |
| `data` | Schema summary |
| `report` | Executive summary |

### 12.3 API Endpoints

```
GET  /tasks                    List all tasks (paginated)
GET  /tasks/{task_id}          Get task details
GET  /tasks/{task_id}/context  Get full task context
POST /tasks/{task_id}/cancel   Cancel running task
POST /tasks/{task_id}/retry    Retry failed task
```

---

## 13. Scheduled Workflows

The Cron Scheduler enables time-based automatic workflow execution using EventBridge rules.

### 13.1 Scheduling Configuration

| Field | Type | Description |
|---|---|---|
| `schedule_enabled` | boolean | Enable/disable scheduling |
| `cron_expression` | string | Standard cron syntax |
| `next_run_at` | timestamp | Unix timestamp of next execution |
| `last_run_at` | timestamp | Last execution timestamp |
| `timezone` | string | Timezone for cron evaluation |

### 13.2 Cron Expression Format

```
+-------------- minute (0-59)
| +------------ hour (0-23)
| | +---------- day of month (1-31)
| | | +-------- month (1-12)
| | | | +------ day of week (0-6)
| | | | |
* * * * *
```

Examples:
- `0 9 * * 1-5` — Every weekday at 9:00 AM
- `*/15 * * * *` — Every 15 minutes
- `0 0 1 * *` — First day of every month at midnight

### 13.3 Scheduler Lambda

Runs on a fixed interval (default: every minute):

1. Queries `ScheduledWorkflowsIndex` GSI for workflows where `next_run_at <= current_time`
2. Starts Step Functions execution for each due workflow
3. Updates `next_run_at` based on cron expression

### 13.4 Parallel Execution Strategy

| Strategy | Description | Use Case |
|---|---|---|
| `SPEED_OPTIMIZED` | All branches in parallel | Time-critical workflows |
| `COST_OPTIMIZED` | Batched execution | Budget-constrained workflows |
| `BALANCED` | Dynamic batching based on resources | Default |

---

## Feature Matrix

| Feature | Description | Status |
|---|---|---|
| Co-design Assistant | Natural language workflow editing | Available |
| Agentic Designer | Full workflow generation from description | Available |
| HITP | Human-in-the-Loop pause points with task token | Available |
| Time Machine | Merkle-chain checkpoint debugging | Available |
| Glass-Box | Real-time AI reasoning transparency | Available |
| Self-Healing | Governor-directed automatic error recovery | Available |
| Mission Simulator | Chaos engineering stress-testing suite | Available |
| Skill Repository | Reusable versioned workflow components | Available |
| Real-time Monitoring | WebSocket + CloudWatch | Available |
| Model Router | Intelligent LLM selection | Available |
| Instruction Distiller | Learning from HITP corrections | Available |
| Task Manager | Business-friendly task abstraction | Available |
| Cron Scheduler | Time-based workflow execution | Available |

---

> [Back to Main README](../README.md)
