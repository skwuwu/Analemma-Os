/**
 * @analemma/bridge-sdk — TypeScript 공개 API
 *
 * 배포 분리:
 *   [SDK — 에이전트 프로세스에 번들]
 *     import { AnalemmaBridge } from "@analemma/bridge-sdk";
 *
 *   [Server — VirtualSegmentManager 독립 프로세스]
 *     Python FastAPI: uvicorn backend.src.bridge.virtual_segment_manager:app
 *
 * 환경 변수:
 *   ANALEMMA_KERNEL_ENDPOINT : VSM 서버 URL (기본: http://localhost:8765)
 *   ANALEMMA_SYNC_POLICY     : "1" 설정 시 초기화 시 Policy Sync 자동 수행
 */

export { AnalemmaBridge, SecurityViolation, injectRecovery } from "./bridge";
export type { SegmentOptions, SegmentOutcome, BridgeConfig } from "./bridge";

export { LocalL1Checker } from "./l1_checker";

export { PolicyMapper } from "./policy_mapper";
export type { MappedPolicy } from "./policy_mapper";

export {
  BridgeRingLevel,
  RING_NAMES,
  CAPABILITY_MAP,
  DESTRUCTIVE_ACTIONS,
  DESTRUCTIVE_PATTERNS,
} from "./types";
export type {
  SegmentType,
  BridgeMode,
  CommitStatus,
  SegmentProposal,
  SegmentCommit,
  L1CheckResult,
  PolicySyncResponse,
} from "./types";
