/**
 * types.ts — Analemma Bridge SDK 공유 타입 정의
 */

// ─── Ring Level ───────────────────────────────────────────────────────────────

export enum BridgeRingLevel {
  KERNEL = 0,
  DRIVER = 1,
  SERVICE = 2,
  USER = 3,
}

export const RING_NAMES: Record<BridgeRingLevel, string> = {
  [BridgeRingLevel.KERNEL]: "KERNEL",
  [BridgeRingLevel.DRIVER]: "DRIVER",
  [BridgeRingLevel.SERVICE]: "SERVICE",
  [BridgeRingLevel.USER]: "USER",
};

// ─── Capability Map ───────────────────────────────────────────────────────────

export const CAPABILITY_MAP: Record<BridgeRingLevel, ReadonlySet<string>> = {
  [BridgeRingLevel.KERNEL]: new Set(["*"]),
  [BridgeRingLevel.DRIVER]: new Set([
    "filesystem_read", "subprocess_call", "network_limited",
    "database_write", "config_read", "network_read",
    "database_query", "cache_read", "event_publish",
    "basic_query", "read_only", "s3_get_object", "s3_put_object",
  ]),
  [BridgeRingLevel.SERVICE]: new Set([
    "network_read", "database_query", "cache_read",
    "event_publish", "basic_query", "read_only", "s3_get_object",
  ]),
  [BridgeRingLevel.USER]: new Set([
    "basic_query", "read_only",
  ]),
};

// ─── Hybrid Interceptor: 파괴적 행동 ─────────────────────────────────────────

export const DESTRUCTIVE_ACTIONS: ReadonlySet<string> = new Set([
  "filesystem_write", "filesystem_delete", "rm", "rmdir", "truncate",
  "shell_exec", "subprocess_call",
  "database_delete", "database_drop",
  "s3_delete", "s3_delete_objects",
  "format", "wipe",
]);

export const DESTRUCTIVE_PATTERNS: RegExp[] = [
  /rm\s+-[rf]+/i,
  /drop\s+table/i,
  /delete\s+from/i,
  /truncate\s+(?:table\s+)?\w+/i,
  /format\s+(?:disk|drive|c:)/i,
  /mkfs\./i,
  /dd\s+if=.+of=\/dev\//i,
  /파일\s*삭제/,
  /데이터베이스\s*(?:삭제|드롭)/,
  /전체\s*삭제/,
  /모두\s*삭제/,
];

// ─── ABI 타입 ──────────────────────────────────────────────────────────────────

export type SegmentType = "LLM_CALL" | "TOOL_CALL" | "MEMORY_UPDATE" | "FINAL";
export type BridgeMode = "strict" | "optimistic";
export type CommitStatus =
  | "APPROVED"
  | "MODIFIED"
  | "REJECTED"
  | "SOFT_ROLLBACK"
  | "SIGKILL";

export interface SegmentProposal {
  protocol_version: string;
  op: "SEGMENT_PROPOSE";
  idempotency_key: string;
  segment_context: {
    workflow_id: string;
    parent_segment_id: string | null;
    loop_index: number;
    segment_type: SegmentType;
    sequence_number: number;
    ring_level: number;
    is_optimistic_report: boolean;
  };
  payload: {
    thought: string;
    action: string;
    action_params: Record<string, unknown>;
  };
  state_snapshot: Record<string, unknown>;
}

export interface SegmentCommit {
  protocol_version: string;
  op: "SEGMENT_COMMIT";
  status: CommitStatus;
  checkpoint_id: string;
  commands: {
    action_override: Record<string, unknown> | null;
    inject_recovery_instruction: string | null;
  };
  governance_feedback: {
    warnings: string[];
    anomaly_score: number;
    article_violations: string[];
  };
}

export interface L1CheckResult {
  allowed: boolean;
  reason: string | null;
}

export interface PolicySyncResponse {
  version: string;
  injection_patterns: string[];
  capability_map: Record<string, string[]>;
  /** shared_policy.py DESTRUCTIVE_ACTIONS와 동기화 — PolicyMapper가 Set<string>으로 변환 */
  destructive_actions: string[];
  /** shared_policy.py DESTRUCTIVE_PATTERNS와 동기화 — PolicyMapper가 RegExp[]으로 컴파일 */
  destructive_patterns: string[];
  audit_registry_backend: string;
}
