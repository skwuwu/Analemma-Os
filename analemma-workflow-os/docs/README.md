# Analemma OS — Documentation

> [Back to Main README](../README.md)

---

## Architecture

| Document | Description |
|----------|-------------|
| [Architecture Overview](architecture-overview.md) | High-level system design: SFN execution loop, Merkle DAG auditing, workflow partitioning |
| [Architecture Deep-Dive](architecture-deep-dive.md) | Comprehensive reference: 4-Ring protection, Great Seal protocol, state pipeline, 256KB defense, 2PC, governance |

## Internals

Detailed technical documentation for core subsystems. Derived from runtime code analysis.

| Document | Description |
|----------|-------------|
| [Ring Protection](internals/ring-protection.md) | 4-Ring privilege isolation, Great Seal Protocol, security guards, state isolation, governor validation |
| [Merkle DAG & Audit](internals/merkle-dag-audit.md) | Content-addressable state versioning, hash generation pipeline, integrity verification, garbage collection |
| [Two-Phase Commit](internals/two-phase-commit.md) | Distributed transaction protocol for S3/DynamoDB atomicity, rollback semantics, GC DLQ |
| [State Management v3.3](internals/state-management-v3.3.md) | Delta-based persistence, distributed transaction consistency, state explosion defense |
| [Kernel Layer Report](internals/kernel-layer-report.md) | v3.13 "The Great Seal" kernel specification: Ring Protection, Universal Sync Core, 256KB payload defense |

## Guides

| Document | Description |
|----------|-------------|
| [Installation](guides/installation.md) | Local development setup, AWS deployment, environment configuration, troubleshooting |
| [API Reference](guides/api-reference.md) | REST endpoints, WebSocket protocol, SDK integration, Cognito authentication |
| [Features](guides/features.md) | Co-design Assistant, HITP, Time Machine, Glass-Box Observability, Self-Healing, REACT Agent |
| [Local Agent Runner](guides/local-agent-runner.md) | Autonomous agent integration (Python/TypeScript), governance modes, tool registry, Ring-level capabilities |

## Audit Reports

Historical code audit reports. All findings from these reports have been addressed in subsequent versions (v3.32–v3.35).

| Document | Date | Scope |
|----------|------|-------|
| [Backend Inspection Report](audits/backend-inspection-report.md) | 2026-02-22 | 18-file audit across Security/Trust, State/Consistency, Execution Infrastructure layers |
| [State Management Audit](audits/state-management-audit-report.md) | 2026-02-20 | v3.3 state pipeline: initialization, segment execution, Merkle DAG, hydration |
| [Hybrid Architecture Report](audits/hybrid-architecture-report.md) | 2026-02-22 | B2B Hybrid Local+Cloud feasibility, Loop Virtualization Bridge SDK implementation plan |

## Reference

| Document | Description |
|----------|-------------|
| [LinkedIn Series](linkedin-series.md) | Technical deep-dives for Articles 4–8: Quality Kernel, Information Density, Entropy Analysis, Cost Guardrails, Concurrency Control |
