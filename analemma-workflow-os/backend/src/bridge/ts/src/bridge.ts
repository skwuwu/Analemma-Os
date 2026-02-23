/**
 * AnalemmaBridge — TypeScript 에이전트용 Loop Virtualization Bridge SDK
 *
 * 에이전트의 TAO 루프(Thought-Action-Observation)를
 * Analemma 커널의 결정론적 세그먼트로 변환.
 *
 * 핵심 기능:
 *   1. Strict Mode     : PROPOSE → 커널 동기 승인 → 실행
 *   2. Optimistic Mode : L1 로컬 검사 → 즉시 실행 → 비동기 커널 보고
 *   3. Hybrid Interceptor: Optimistic Mode 중 파괴적 행동 감지 → Strict 강제 전환
 *   4. Policy Sync     : 초기화 시 커널 최신 패턴 자동 동기화
 *
 * 환경 변수:
 *   ANALEMMA_KERNEL_ENDPOINT : VSM 서버 URL (기본: http://localhost:8765)
 *   ANALEMMA_SYNC_POLICY     : "1" 설정 시 초기화 시 Policy Sync 자동 수행
 *
 * 사용 예시:
 *   const bridge = await AnalemmaBridge.create({
 *     workflowId: "wf_123",
 *     ringLevel: BridgeRingLevel.SERVICE,
 *     mode: "optimistic",
 *   });
 *
 *   const outcome = await bridge.segment({
 *     thought: "Read billing report.",
 *     action: "s3_get_object",
 *     params: { bucket: "billing", key: "report.json" },
 *     execute: async (approvedParams) => s3.getObject(approvedParams),
 *   });
 *
 *   // ③ Recovery Instruction: LLM System Message에 주입하여 자기 교정 유도
 *   const nextSysMsg = injectRecovery(baseSystemMsg, outcome.recoveryInstruction);
 */

import crypto from "crypto";
import {
  BridgeRingLevel,
  BridgeMode,
  DESTRUCTIVE_ACTIONS,
  DESTRUCTIVE_PATTERNS,
  SegmentCommit,
  SegmentProposal,
  SegmentType,
} from "./types";
import { LocalL1Checker } from "./l1_checker";
import { proposeClient, observeClient } from "./http_client";

// ─── 환경 변수 ─────────────────────────────────────────────────────────────────

const DEFAULT_KERNEL_ENDPOINT: string =
  process.env.ANALEMMA_KERNEL_ENDPOINT ?? "http://localhost:8765";
const AUTO_SYNC_POLICY: boolean =
  (process.env.ANALEMMA_SYNC_POLICY ?? "").trim() === "1";

// ─── 공개 타입 ─────────────────────────────────────────────────────────────────

export interface SegmentOptions<T> {
  thought: string;
  action: string;
  params: Record<string, unknown>;
  segmentType?: SegmentType;
  stateSnapshot?: Record<string, unknown>;
  /** 커널이 APPROVED 또는 MODIFIED 판정 시 실행될 콜백. */
  execute: (approvedParams: Record<string, unknown>) => Promise<T>;
}

export interface SegmentOutcome<T> {
  result: T | null;
  commit: SegmentCommit;
  /** 커널이 주입한 복구 지침 (다음 Thought에 활용 권장). */
  recoveryInstruction: string | null;
}

export class SecurityViolation extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SecurityViolation";
  }
}

export interface BridgeConfig {
  workflowId: string;
  ringLevel?: BridgeRingLevel;
  kernelEndpoint?: string;
  mode?: BridgeMode;
  syncPolicy?: boolean;
}

// ─── ③ Recovery Instruction 유틸리티 ──────────────────────────────────────────

/**
 * LLM System Message에 RecoveryInstruction을 주입한다.
 *
 * 에이전트의 다음 루프 Thought가 커널 거부 사유를 인지하고
 * 자기 수복(Self-correction)하도록 강제하는 패턴.
 *
 * 사용 방법:
 *   const nextSysMsg = injectRecovery(baseSysMsg, outcome.recoveryInstruction);
 *   // nextSysMsg를 LLM 호출의 system role 메시지로 사용
 *
 * @param systemMessage  기본 System Message
 * @param recovery       SegmentOutcome.recoveryInstruction (null이면 원본 반환)
 * @returns              [ANALEMMA_RECOVERY] 블록이 삽입된 System Message
 */
export function injectRecovery(
  systemMessage: string,
  recovery: string | null
): string {
  if (!recovery) return systemMessage;
  return (
    systemMessage.trimEnd() +
    `\n\n[ANALEMMA_RECOVERY]\n${recovery}\n[/ANALEMMA_RECOVERY]`
  );
}

// ─── AnalemmaBridge ────────────────────────────────────────────────────────────

export class AnalemmaBridge {
  private readonly workflowId: string;
  private readonly ringLevel: BridgeRingLevel;
  private readonly kernelEndpoint: string;
  private readonly mode: BridgeMode;
  private readonly l1Checker: LocalL1Checker;

  /** Hybrid Interceptor: PolicySync로 갱신 가능한 파괴적 행동 Set */
  private destructiveActions: Set<string> = new Set(
    [...DESTRUCTIVE_ACTIONS].map((a) => a.toLowerCase())
  );
  /** Hybrid Interceptor: PolicySync로 갱신 가능한 파괴적 패턴 목록 */
  private destructivePatterns: RegExp[] = [...DESTRUCTIVE_PATTERNS];

  private loopIndex = 0;
  private parentSegmentId: string | null = null;

  private constructor(config: Required<BridgeConfig>, l1Checker: LocalL1Checker) {
    this.workflowId = config.workflowId;
    this.ringLevel = config.ringLevel;
    this.kernelEndpoint = config.kernelEndpoint;
    this.mode = config.mode;
    this.l1Checker = l1Checker;
  }

  /**
   * AnalemmaBridge 팩토리 메서드.
   * syncPolicy=true이면 /v1/policy/sync를 먼저 호출한 뒤 인스턴스를 반환한다.
   * PolicySync 성공 시 DESTRUCTIVE 목록도 서버 기준으로 갱신된다.
   */
  static async create(config: BridgeConfig): Promise<AnalemmaBridge> {
    const resolved: Required<BridgeConfig> = {
      workflowId: config.workflowId,
      ringLevel: config.ringLevel ?? BridgeRingLevel.USER,
      kernelEndpoint: config.kernelEndpoint ?? DEFAULT_KERNEL_ENDPOINT,
      mode: config.mode ?? "strict",
      syncPolicy: config.syncPolicy ?? AUTO_SYNC_POLICY,
    };

    const l1Checker = new LocalL1Checker();
    const bridge = new AnalemmaBridge(resolved, l1Checker);

    if (resolved.syncPolicy) {
      const mapped = await l1Checker.syncFromKernel(resolved.kernelEndpoint);
      if (mapped) {
        bridge.updateDestructive(mapped.destructiveActions, mapped.destructivePatterns);
        console.info(
          `[AnalemmaBridge] Policy synced. version=${l1Checker.policyVersion}`
        );
      } else {
        console.warn("[AnalemmaBridge] Policy sync failed. Using local defaults.");
      }
    }

    return bridge;
  }

  /**
   * PolicySync 결과로 파괴적 행동 목록을 갱신한다.
   * AnalemmaBridge.create() 내부에서 자동 호출. 수동 갱신도 가능.
   */
  updateDestructive(actions: Set<string>, patterns: RegExp[]): void {
    this.destructiveActions = actions;
    this.destructivePatterns = patterns;
  }

  // ── 공개 API ─────────────────────────────────────────────────────────────────

  /**
   * 에이전트 행동을 커널 거버넌스 하에 실행.
   *
   * Hybrid Interceptor:
   *   mode="optimistic"이더라도 action이 destructiveActions에 포함되거나
   *   thought/params에서 파괴적 패턴이 감지되면 effective_mode="strict"로 강제 전환.
   */
  async segment<T>(options: SegmentOptions<T>): Promise<SegmentOutcome<T>> {
    const loopIndex = ++this.loopIndex;

    // ── Hybrid Interceptor ────────────────────────────────────────────────────
    let effectiveMode = this.mode;
    if (effectiveMode === "optimistic" && this.isDestructive(options)) {
      effectiveMode = "strict";
      console.warn(
        `[HybridInterceptor] Destructive action detected. Forcing STRICT mode. ` +
          `action=${options.action} workflow=${this.workflowId} loop=${loopIndex}`
      );
    }

    if (effectiveMode === "optimistic") {
      return this.optimisticSegment(options, loopIndex);
    } else {
      return this.strictSegment(options, loopIndex);
    }
  }

  // ── Strict Mode ───────────────────────────────────────────────────────────────

  private async strictSegment<T>(
    options: SegmentOptions<T>,
    loopIndex: number
  ): Promise<SegmentOutcome<T>> {
    const proposal = this.buildProposal(options, loopIndex, false);
    const commit = await this.sendPropose(proposal);
    const recoveryInstruction = commit.commands.inject_recovery_instruction;

    if (commit.status === "SIGKILL") {
      throw new SecurityViolation(
        `[AnalemmaBridge] SIGKILL at loop ${loopIndex}. ` +
          `Recovery: ${recoveryInstruction ?? "none"}`
      );
    }

    if (!["APPROVED", "MODIFIED"].includes(commit.status)) {
      await this.sendObservation(commit.checkpoint_id, null, "SKIPPED");
      return { result: null, commit, recoveryInstruction };
    }

    const approvedParams = commit.commands.action_override ?? options.params;
    let result: T | null = null;

    try {
      result = await options.execute(approvedParams);
      await this.sendObservation(commit.checkpoint_id, result, "SUCCESS");
    } catch (error) {
      await this.sendFailure(commit.checkpoint_id, String(error));
      throw error;
    } finally {
      this.parentSegmentId = commit.checkpoint_id;
    }

    return { result, commit, recoveryInstruction };
  }

  // ── Optimistic Mode ───────────────────────────────────────────────────────────

  private async optimisticSegment<T>(
    options: SegmentOptions<T>,
    loopIndex: number
  ): Promise<SegmentOutcome<T>> {
    const l1 = this.l1Checker.check(
      options.thought,
      options.action,
      this.ringLevel,
      options.params
    );
    if (!l1.allowed) {
      throw new SecurityViolation(`[L1 Blocked] ${l1.reason}`);
    }

    const fakeCommit: SegmentCommit = {
      protocol_version: "1.0",
      op: "SEGMENT_COMMIT",
      status: "APPROVED",
      checkpoint_id: "optimistic_local",
      commands: { action_override: null, inject_recovery_instruction: null },
      governance_feedback: { warnings: [], anomaly_score: 0, article_violations: [] },
    };

    const result = await options.execute(options.params);

    // 사후 비동기 보고 (fire-and-forget)
    this.asyncReport(options, loopIndex, result).catch((err) =>
      console.debug(`[AnalemmaBridge] Async report failed (non-critical): ${err}`)
    );

    return { result, commit: fakeCommit, recoveryInstruction: null };
  }

  private async asyncReport<T>(
    options: SegmentOptions<T>,
    loopIndex: number,
    _observation: T | null
  ): Promise<void> {
    const proposal = this.buildProposal(options, loopIndex, true);
    try {
      await proposeClient.post(
        `${this.kernelEndpoint}/v1/segment/propose`,
        proposal
      );
    } catch {
      // 비치명적 실패 — Optimistic 사후 보고 실패는 무시
    }
  }

  // ── Hybrid Interceptor ────────────────────────────────────────────────────────

  private isDestructive(options: SegmentOptions<unknown>): boolean {
    if (this.destructiveActions.has(options.action.toLowerCase())) return true;

    let scanText = options.thought;
    if (options.params) {
      try {
        scanText += " " + JSON.stringify(options.params);
      } catch {
        scanText += " " + String(options.params);
      }
    }

    return this.destructivePatterns.some((p) => p.test(scanText));
  }

  // ── 커널 통신 (② Axios 인터셉터 적용) ────────────────────────────────────────

  private buildProposal(
    options: SegmentOptions<unknown>,
    loopIndex: number,
    isOptimisticReport: boolean
  ): SegmentProposal {
    const content = `${this.workflowId}:loop_${loopIndex}:${options.action}`;
    const idempotencyKey = crypto
      .createHash("sha256")
      .update(content)
      .digest("hex")
      .slice(0, 16);

    return {
      protocol_version: "1.0",
      op: "SEGMENT_PROPOSE",
      idempotency_key: idempotencyKey,
      segment_context: {
        workflow_id: this.workflowId,
        parent_segment_id: this.parentSegmentId,
        loop_index: loopIndex,
        segment_type: options.segmentType ?? "TOOL_CALL",
        sequence_number: loopIndex,
        ring_level: this.ringLevel,
        is_optimistic_report: isOptimisticReport,
      },
      payload: {
        thought: options.thought,
        action: options.action,
        action_params: options.params,
      },
      state_snapshot: options.stateSnapshot ?? {},
    };
  }

  private async sendPropose(proposal: SegmentProposal): Promise<SegmentCommit> {
    try {
      const resp = await proposeClient.post<SegmentCommit>(
        `${this.kernelEndpoint}/v1/segment/propose`,
        proposal
      );
      return resp.data;
    } catch {
      // Fail-Open: MAX_RETRIES 소진 후 진입. 커널 불가 시 APPROVED 반환.
      console.warn("[AnalemmaBridge] Kernel unreachable after retries, fail-open.");
      return {
        protocol_version: "1.0",
        op: "SEGMENT_COMMIT",
        status: "APPROVED",
        checkpoint_id: "local_only",
        commands: { action_override: null, inject_recovery_instruction: null },
        governance_feedback: { warnings: [], anomaly_score: 0, article_violations: [] },
      };
    }
  }

  private async sendObservation(
    checkpointId: string,
    observation: unknown,
    status: string
  ): Promise<void> {
    try {
      await observeClient.post(
        `${this.kernelEndpoint}/v1/segment/observe`,
        { checkpoint_id: checkpointId, observation: JSON.stringify(observation), status }
      );
    } catch { /* 비치명적 */ }
  }

  private async sendFailure(checkpointId: string, error: string): Promise<void> {
    try {
      await observeClient.post(
        `${this.kernelEndpoint}/v1/segment/fail`,
        { checkpoint_id: checkpointId, error }
      );
    } catch { /* 비치명적 */ }
  }
}
