# 🌌 Analemma OS

> **The Deterministic Runtime for Autonomous AI Agents**  
> *Bridging the gap between probabilistic intelligence and deterministic infrastructure.*

[![License: BSL 1.1](https://img.shields.io/badge/License-BSL%201.1-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB.svg)](https://python.org)
[![AWS SAM](https://img.shields.io/badge/AWS-SAM-FF9900.svg)](https://aws.amazon.com/serverless/sam/)

---

## 🎯 What is Analemma OS?

**Analemma OS** is a serverless, enterprise-grade operating system designed to orchestrate, govern, and scale autonomous AI agents. By transforming unreliable AI loops into managed, stateful, and self-healing cloud processes, Analemma provides the **"Trust Layer"** that production-ready AI demands.

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Analemma OS                                  │
│    "Virtualizing Agent Logic into Deterministic Kernel Processes"   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐ │
│  │   User Space     │   │   Kernel Space   │   │   Hardware       │ │
│  │   (AI Agents)    │   │   (Scheduler)    │   │   (Serverless)   │ │
│  └────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘ │
│           │                      │                      │           │
│           ▼                      ▼                      ▼           │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ LangGraph │ Natural │ Workflow   │ Step      │ Lambda │ S3     ││
│  │ Workflows │ Language│ Partitioner│ Functions │ Compute│ State  ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

---

## 💡 The Problem: The Trust Gap

While LLMs have become incredibly capable, deploying them as autonomous agents in production remains risky:

| Problem | Traditional Approach | Analemma Solution |
|---------|---------------------|-------------------|
| **Unpredictable Loops** | Agents get stuck in infinite, costly cycles | Kernel-level loop detection + automatic termination |
| **State Volatility** | Progress lost during mid-process failures | S3-backed virtual memory + checkpoint persistence |
| **Resource Throttling** | Infrastructure collapse under agent spikes | Reserved concurrency + intelligent backoff |
| **Human Oversight** | No structured pause points for approval | Physical HITP interrupts via AWS Task Tokens |

---

## 🏗️ Core Architecture: The 3-Layer Kernel Model

### Layer 1: User Space (Agent Logic)
- **Framework Agnostic**: Optimized for LangGraph, accepts any graph-based logic via Analemma IR
- **Co-design Interface**: Natural language-to-workflow compilation using Gemini 2.0 Flash
- **Skill Repository**: Reusable agent capabilities with version control

### Layer 2: Kernel Space (Orchestration Core)
- **Intelligent Scheduler**: Gemini-powered dynamic workflow partitioning
- **Virtual Memory Manager**: Automatic S3 offloading for payloads > 256KB
- **State Machine Controller**: AWS Step Functions with deterministic execution

### Layer 3: Hardware Abstraction (Serverless Infrastructure)
- **Compute Layer**: AWS Lambda with reserved concurrency protection
- **Resilience Layer**: Declarative Retry/Catch at infrastructure level
- **Distributed Execution**: Step Functions Distributed Map for parallel processing

---

## 📚 Documentation

| Document | Description |
|----------|-------------|
| [**Architecture Deep-Dive**](docs/architecture.md) | Kernel design, abstraction layers, state management patterns |
| [**API Reference**](docs/api-reference.md) | REST API, WebSocket protocol, SDK integration |
| [**Features Guide**](docs/features.md) | Co-design assistant, monitoring, Time Machine debugging |
| [**Installation Guide**](docs/installation.md) | Serverless deployment, environment setup, configuration |

---

## ⚡ Quick Start

```bash
# Clone the repository
git clone https://github.com/skwuwu/Analemma-Os.git
cd Analemma-Os/analemma-workflow-os/backend

# Install dependencies
pip install -r requirements.txt

# Deploy to AWS
sam build && sam deploy --guided
```

> 📖 See [Installation Guide](docs/installation.md) for detailed setup instructions.

---

## 🛠️ Tech Stack

| Category | Technologies |
|----------|--------------|
| **Runtime** | Python 3.12, AWS Lambda |
| **Orchestration** | AWS Step Functions, LangGraph |
| **AI/LLM** | Gemini 2.0 Flash (Primary), Claude 3.5 Sonnet (Fallback) |
| **Storage** | DynamoDB (Metadata), S3 (State Offload) |
| **Real-time** | WebSocket API (API Gateway) |
| **Infrastructure** | AWS SAM, CloudFormation |

---

## 🔑 Key Innovations

### 🎯 Mission Simulator
Built-in stress-testing suite simulating 8+ real-world failure scenarios: network latency, LLM hallucinations, infrastructure throttling.

### ⏱️ Time Machine
Every agent step is persisted. Resume from exact failure point with zero data loss.

### 🔄 Self-Healing
Automatic error analysis and recovery path suggestions using LLM-powered diagnostics.

### 🤝 Human-in-the-Loop (HITP)
Physical pause points for human approval, integrated with Step Functions Task Tokens.

---

## 📄 License

This project is licensed under the **Business Source License 1.1 (BSL 1.1)**.

- **Non-Production Use**: Free for development, testing, and personal projects
- **Production Use**: Contact for commercial licensing
- **Change Date**: Converts to open source (Apache 2.0) on 2029-01-14

See [LICENSE](LICENSE) for full terms.

---

## 🤝 Contributing

Contributions are welcome! Please read our contributing guidelines before submitting PRs.

---

<div align="center">
  <sub>Built with ❤️ for the AI Agent ecosystem</sub>
</div>
