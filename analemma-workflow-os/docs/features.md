# ✨ Features Guide

> [← Back to Main README](../README.md)

This document provides a comprehensive overview of Analemma OS features, including the Co-design Assistant, monitoring capabilities, Time Machine debugging, and other service features.

---

## Table of Contents

1. [Co-design Assistant](#1-co-design-assistant)
2. [Agentic Designer](#2-agentic-designer)
3. [Human-in-the-Loop (HITP)](#3-human-in-the-loop-hitp)
4. [Time Machine Debugging](#4-time-machine-debugging)
5. [Glass-Box Observability](#5-glass-box-observability)
6. [Self-Healing & Recovery](#6-self-healing--recovery)
7. [Mission Simulator](#7-mission-simulator)
8. [Skill Repository](#8-skill-repository)
9. [Real-time Monitoring](#9-real-time-monitoring)
10. [Model Router](#10-model-router)

---

## 1. Co-design Assistant

The Co-design Assistant enables **natural language workflow editing** through real-time AI collaboration.

### 1.1 Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Co-design Assistant                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   User: "Add error handling to the API calls"                   │
│                         │                                        │
│                         ▼                                        │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │              Natural Language Processing                 │   │
│   │   • Intent Detection (structure needs, complexity)      │   │
│   │   • Negation Awareness ("without loops")                │   │
│   │   • Context Analysis (existing workflow state)          │   │
│   └─────────────────────────────────────────────────────────┘   │
│                         │                                        │
│                         ▼                                        │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │              Workflow Modification                       │   │
│   │   • Generate new nodes/edges                            │   │
│   │   • Suggest optimizations                               │   │
│   │   • Explain changes                                     │   │
│   └─────────────────────────────────────────────────────────┘   │
│                         │                                        │
│                         ▼                                        │
│   Output: JSONL stream of node, edge, and suggestion updates    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Capabilities

| Feature | Description |
|---------|-------------|
| **Natural Language Editing** | Describe changes in plain English |
| **Incremental Updates** | Modify existing workflows without rebuilding |
| **Smart Suggestions** | AI-powered optimization recommendations |
| **Real-time Streaming** | See changes as they're generated |
| **Context Awareness** | Understands existing workflow structure |

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

The Agentic Designer **generates complete workflows from scratch** based on natural language descriptions.

### 2.1 When It Activates

The system automatically switches to Agentic Designer mode when:

- Canvas is empty (no nodes or edges)
- No conversation history exists
- User requests full workflow generation

### 2.2 Generation Process

```
User Request: "Create a workflow that monitors social media 
               mentions and sends alerts for negative sentiment"
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Agentic Designer                          │
├─────────────────────────────────────────────────────────────┤
│  1. Parse intent and requirements                            │
│  2. Select appropriate node types                            │
│  3. Design optimal graph structure                           │
│  4. Calculate layout positions                               │
│  5. Generate edges with proper connections                   │
│  6. Stream output in JSONL format                            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
Generated Workflow:
┌─────┐    ┌───────────┐    ┌───────────┐    ┌─────────┐    ┌─────┐
│Start│───>│ API Fetch │───>│ Sentiment │───>│ Condition│───>│ End │
└─────┘    │ (Twitter) │    │ Analysis  │    │ (< 0.3) │    └─────┘
           └───────────┘    └───────────┘    └────┬────┘
                                                   │
                                                   ▼
                                             ┌───────────┐
                                             │Send Alert │
                                             │ (Slack)   │
                                             └───────────┘
```

### 2.3 Node Types Supported

| Category | Node Types |
|----------|------------|
| **Control Flow** | start, end, condition, loop, parallel |
| **AI/LLM** | llm_chat, gemini_chat, anthropic_chat |
| **Data** | api_call, database_query, transform |
| **Integration** | webhook, email, slack, custom |
| **Human** | hitp, approval, input_form |

### 2.4 Layout Rules

The Agentic Designer follows consistent layout rules:

```
Layout Algorithm:
├── Sequential nodes: X=150, Y increases by 100
├── Parallel branches: Same Y, X spreads by 200
├── Conditional branches: Left (true), Right (false)
└── Loop internals: X offset +50 for nesting
```

---

## 3. Human-in-the-Loop (HITP)

HITP provides **physical pause points** in workflow execution for human oversight and approval.

### 3.1 HITP Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                      HITP Execution Flow                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   Workflow Execution                                            │
│         │                                                        │
│         ▼                                                        │
│   ┌───────────────────────────────────────┐                     │
│   │          HITP Node Reached             │                     │
│   │   • Store Task Token in DynamoDB      │                     │
│   │   • Persist current state to S3       │                     │
│   │   • Send WebSocket notification       │                     │
│   └───────────────────────────────────────┘                     │
│         │                                                        │
│         ▼                                                        │
│   ┌───────────────────────────────────────┐                     │
│   │     Step Functions WAIT State          │                     │
│   │   • Execution paused                   │                     │
│   │   • 24-hour timeout (configurable)    │                     │
│   └───────────────────────────────────────┘                     │
│         │                                                        │
│         │  User Response via API/WebSocket                      │
│         ▼                                                        │
│   ┌───────────────────────────────────────┐                     │
│   │     Resume Execution                   │                     │
│   │   • Validate user response            │                     │
│   │   • Inject response into state        │                     │
│   │   • Continue workflow                  │                     │
│   └───────────────────────────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Use Cases

| Use Case | Description |
|----------|-------------|
| **Approval Gates** | Require human approval before critical actions |
| **Data Validation** | Human review of AI-generated content |
| **Exception Handling** | Manual intervention for edge cases |
| **Compliance** | Audit trail with human sign-off |

### 3.3 Notification Channels

When HITP is triggered, users are notified through:

1. **WebSocket Push** - Real-time in-app notification
2. **Notification Center** - Persistent notification stored in DynamoDB
3. **Email/SMS** - Optional external notifications

---

## 4. Time Machine Debugging

Time Machine enables **checkpoint-based debugging** and state inspection throughout workflow execution.

### 4.1 Features

| Feature | Description |
|---------|-------------|
| **Execution Timeline** | View all events in chronological order |
| **Checkpoint Snapshots** | Full state captured at each segment |
| **State Diff** | Compare state between any two checkpoints |
| **Resume from Checkpoint** | Restart execution from any point |

### 4.2 Timeline View

```
Execution Timeline: exec_xyz789
────────────────────────────────────────────────────────────────
10:05:00.000 │ ● START        │ Execution initiated
10:05:00.150 │ ● SEGMENT_0    │ Started segment 0
10:05:01.200 │ ○ LLM_CALL     │ gemini-3-pro (1,250 tokens)
10:05:02.050 │ ● SEGMENT_0    │ Completed
10:05:02.100 │ ● SEGMENT_1    │ Started segment 1
10:05:03.500 │ ○ API_CALL     │ external-api.com/data
10:05:04.200 │ ● SEGMENT_1    │ Completed
10:05:04.250 │ ● HITP         │ Waiting for human approval
10:15:00.000 │ ● RESUME       │ User approved
10:15:00.100 │ ● SEGMENT_2    │ Started segment 2
10:15:01.500 │ ● END          │ Execution completed
────────────────────────────────────────────────────────────────
```

### 4.3 Checkpoint Comparison

```json
// Compare checkpoint_001 vs checkpoint_005

{
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

Glass-Box provides **transparent AI decision-making** by logging all LLM interactions and tool usage.

### 5.1 What's Logged

| Event Type | Data Captured |
|------------|---------------|
| `ai_thought` | LLM prompts, responses, reasoning |
| `tool_usage` | Tool calls, inputs, outputs |
| `decision` | Branch decisions, conditions evaluated |
| `error` | Failures with stack traces |

### 5.2 Log Structure

```json
{
  "type": "ai_thought",
  "timestamp": "2026-01-14T10:05:01.200Z",
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

### 5.3 PII Masking

Sensitive data is automatically masked in logs:

```python
# Before masking
"Please process email: john.doe@example.com with SSN 123-45-6789"

# After masking
"Please process email: j***@e***.com with SSN ***-**-****"
```

---

## 6. Self-Healing & Recovery

Analemma OS includes **automatic error recovery** through LLM-powered diagnostics.

### 6.1 Self-Healing Process

```
┌─────────────────────────────────────────────────────────────────┐
│                     Self-Healing Process                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   Execution Fails                                               │
│         │                                                        │
│         ▼                                                        │
│   ┌───────────────────────────────────────┐                     │
│   │     Error Analysis                     │                     │
│   │   • Parse error type and message      │                     │
│   │   • Identify affected segment/node    │                     │
│   │   • Check error history patterns      │                     │
│   └───────────────────────────────────────┘                     │
│         │                                                        │
│         ▼                                                        │
│   ┌───────────────────────────────────────┐                     │
│   │     Generate Fix Instruction           │                     │
│   │   • LLM analyzes error context        │                     │
│   │   • Proposes recovery strategy        │                     │
│   │   • Validates fix is applicable       │                     │
│   └───────────────────────────────────────┘                     │
│         │                                                        │
│         ▼                                                        │
│   ┌───────────────────────────────────────┐                     │
│   │     Inject into Retry                  │                     │
│   │   • Sandboxed prompt injection        │                     │
│   │   • Security validation               │                     │
│   │   • Retry with enhanced context       │                     │
│   └───────────────────────────────────────┘                     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 Recovery Strategies

| Error Type | Strategy |
|------------|----------|
| **LLM Timeout** | Retry with shorter prompt, different model |
| **Rate Limit** | Exponential backoff, queue pending requests |
| **Invalid Output** | Add format instructions to prompt |
| **External API Failure** | Retry with fallback endpoint |

### 6.3 Sandboxed Injection

Fix instructions are securely injected to prevent prompt injection attacks:

```
<!-- ANALEMMA_SELF_HEALING_ADVICE -->
<SYSTEM_ADVICE>
SYSTEM WARNING: The following is automated advice from error history.
Previous attempt failed with: "JSON parsing error - missing closing brace"
Ensure your output is valid JSON with all braces properly closed.
</SYSTEM_ADVICE>
```

---

## 7. Mission Simulator

The Mission Simulator is a **stress-testing suite** that validates workflow resilience against real-world failure scenarios.

### 7.1 Simulated Scenarios

| Scenario | Description |
|----------|-------------|
| **Network Latency** | Introduces random delays (100ms-5s) |
| **LLM Hallucination** | Returns invalid/unexpected responses |
| **Rate Limiting** | Simulates 429 responses |
| **Timeout** | Forces request timeouts |
| **Partial Failure** | Some nodes succeed, others fail |
| **State Corruption** | Injects invalid state data |
| **Concurrent Load** | Parallel execution stress test |
| **Memory Pressure** | Large payload handling |

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
════════════════════════════════════════════════════════════
Scenario: concurrent_load
Duration: 300s
Concurrency: 50 parallel executions

Results:
├── Total Executions: 1,247
├── Successful: 1,231 (98.7%)
├── Failed: 12 (1.0%)
├── Timed Out: 4 (0.3%)

Performance:
├── Avg Latency: 2.3s
├── P95 Latency: 4.8s
├── P99 Latency: 7.2s

Resource Usage:
├── Peak Lambda Concurrency: 48
├── DynamoDB RCU: 450/500
├── DynamoDB WCU: 380/500
════════════════════════════════════════════════════════════
```

---

## 8. Skill Repository

The Skill Repository provides **reusable, versioned workflow components**.

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
    "nodes": [...],
    "edges": [...]
  }
}
```

### 8.2 Skill Categories

| Category | Examples |
|----------|----------|
| **Data Processing** | CSV Parser, JSON Transformer, Data Validator |
| **AI/ML** | Sentiment Analysis, Text Summarizer, Image Classifier |
| **Integration** | Slack Notifier, Email Sender, Webhook Caller |
| **Utility** | Rate Limiter, Cache Manager, Error Handler |

### 8.3 Using Skills in Workflows

Skills can be referenced in workflow definitions:

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

Analemma OS provides **comprehensive real-time monitoring** through WebSocket connections and CloudWatch integration.

### 9.1 Dashboard Metrics

| Metric | Description |
|--------|-------------|
| **Active Executions** | Currently running workflows |
| **Execution Rate** | Workflows started per minute |
| **Success Rate** | Percentage of successful completions |
| **Avg Duration** | Average execution time |
| **Error Rate** | Failed executions per minute |
| **HITP Pending** | Workflows awaiting human input |

### 9.2 CloudWatch Metrics

Custom CloudWatch metrics published:

```
Namespace: Analemma/Workflow
├── ExecutionStarted (Count)
├── ExecutionCompleted (Count)
├── ExecutionFailed (Count)
├── SegmentDuration (Milliseconds)
├── LLMTokensUsed (Count)
├── SelfHealingTriggered (Count)
├── HITPRequested (Count)
└── HITPResponseTime (Seconds)
```

### 9.3 Alerts Configuration

Recommended CloudWatch alarms:

| Alarm | Condition | Action |
|-------|-----------|--------|
| High Error Rate | ErrorRate > 5% for 5 min | SNS notification |
| Long HITP Wait | HITPPending > 10 for 1 hour | Email alert |
| Lambda Throttling | ThrottleCount > 0 | Scale up concurrency |
| LLM Cost Spike | TokensUsed > 1M in 1 hour | Budget alert |

---

## 10. Model Router

The Model Router **intelligently selects the optimal LLM** for each request based on multiple factors.

### 10.1 Selection Criteria

```
┌─────────────────────────────────────────────────────────────────┐
│                    Model Selection Algorithm                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   Input: User request, Canvas mode, Workflow state              │
│                         │                                        │
│                         ▼                                        │
│   ┌───────────────────────────────────────┐                     │
│   │     Semantic Intent Detection          │                     │
│   │   • Structure needs (loop, parallel)  │                     │
│   │   • Negation awareness                │                     │
│   │   • Complexity estimation             │                     │
│   └───────────────────────────────────────┘                     │
│                         │                                        │
│                         ▼                                        │
│   ┌───────────────────────────────────────┐                     │
│   │     Context Analysis                   │                     │
│   │   • Workflow size                     │                     │
│   │   • History length                    │                     │
│   │   • Token estimation                  │                     │
│   └───────────────────────────────────────┘                     │
│                         │                                        │
│                         ▼                                        │
│   ┌───────────────────────────────────────┐                     │
│   │     Requirement Matching               │                     │
│   │   • Latency requirements              │                     │
│   │   • Context window needs              │                     │
│   │   • Cost constraints                  │                     │
│   └───────────────────────────────────────┘                     │
│                         │                                        │
│                         ▼                                        │
│   Output: Selected model (e.g., gemini-3-pro)                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 10.2 Available Models

| Model | Use Case | Context | Latency | Cost |
|-------|----------|---------|---------|------|
| `gemini-3-pro` | Full generation, complex reasoning | 2M tokens | ~500ms TTFT | $0.25/1M |
| `gemini-3-flash` | Real-time collaboration, streaming | 1M tokens | ~100ms TTFT | $0.10/1M |
| `gemini-3-flash-lite` | Pre-routing, classification | 1M tokens | ~80ms TTFT | $0.05/1M |
| `claude-3-sonnet` | Fallback, Bedrock integration | 200K tokens | ~1.5s TTFT | $3/1M |

### 10.3 Context Caching

For repeated contexts (system prompts, large documents), Context Caching reduces costs by 75%:

```python
# Automatic caching for contexts > 32K tokens
if token_count > 32000:
    enable_context_caching = True
    # Cached tokens billed at 25% of regular rate
```

---

## Summary: Feature Matrix

| Feature | Description | Status |
|---------|-------------|--------|
| Co-design Assistant | Natural language workflow editing | ✅ Available |
| Agentic Designer | Full workflow generation | ✅ Available |
| HITP | Human-in-the-Loop pause points | ✅ Available |
| Time Machine | Checkpoint debugging | ✅ Available |
| Glass-Box | AI transparency logging | ✅ Available |
| Self-Healing | Automatic error recovery | ✅ Available |
| Mission Simulator | Stress testing suite | ✅ Available |
| Skill Repository | Reusable components | ✅ Available |
| Real-time Monitoring | WebSocket + CloudWatch | ✅ Available |
| Model Router | Intelligent LLM selection | ✅ Available |

---

> [← Back to Main README](../README.md)
