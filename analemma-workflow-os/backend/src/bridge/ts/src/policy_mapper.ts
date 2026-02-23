/**
 * policy_mapper.ts — VSM PolicySyncResponse → TS 내부 타입 변환 유틸리티
 *
 * /v1/policy/sync 응답(Python shared_policy.py 기반)을 TypeScript의
 * Map, Set, RegExp 타입으로 변환한다.
 *
 * 역할:
 *   - string[] injection_patterns  → RegExp[] (iu 플래그)
 *   - Record<string,string[]> cap  → Map<BridgeRingLevel, Set<string>>
 *   - string[] destructive_actions → Set<string>
 *   - string[] destructive_patterns→ RegExp[] (i 플래그)
 *
 * 사용:
 *   const mapped = PolicyMapper.fromSync(resp.data);
 *   l1Checker.injectPatterns(mapped);
 *   bridge.updateDestructive(mapped.destructiveActions, mapped.destructivePatterns);
 */

import { BridgeRingLevel, PolicySyncResponse } from "./types";

// ─── 변환 결과 타입 ────────────────────────────────────────────────────────────

export interface MappedPolicy {
  version: string;
  injectionPatterns: RegExp[];
  capabilityMap: Map<BridgeRingLevel, Set<string>>;
  destructiveActions: Set<string>;
  destructivePatterns: RegExp[];
}

// ─── PolicyMapper ──────────────────────────────────────────────────────────────

export class PolicyMapper {
  /**
   * PolicySyncResponse → MappedPolicy 변환.
   *
   * 정규식 컴파일 오류(서버가 잘못된 패턴을 반환한 경우)는 개별적으로
   * 무시하고 경고 로그를 남긴다 — 한 패턴이 깨져도 나머지는 동작한다.
   */
  static fromSync(resp: PolicySyncResponse): MappedPolicy {
    return {
      version: resp.version,
      injectionPatterns: PolicyMapper._compilePatterns(resp.injection_patterns, "iu"),
      capabilityMap: PolicyMapper._buildCapabilityMap(resp.capability_map),
      destructiveActions: new Set(resp.destructive_actions.map((a) => a.toLowerCase())),
      destructivePatterns: PolicyMapper._compilePatterns(resp.destructive_patterns, "i"),
    };
  }

  // ── 내부 헬퍼 ──────────────────────────────────────────────────────────────

  private static _compilePatterns(patterns: string[], flags: string): RegExp[] {
    const compiled: RegExp[] = [];
    for (const p of patterns) {
      try {
        compiled.push(new RegExp(p, flags));
      } catch (e) {
        console.warn(
          `[PolicyMapper] Invalid regex pattern (skipped): "${p}" — ${e}`
        );
      }
    }
    return compiled;
  }

  private static _buildCapabilityMap(
    raw: Record<string, string[]>
  ): Map<BridgeRingLevel, Set<string>> {
    const map = new Map<BridgeRingLevel, Set<string>>();
    for (const [key, tools] of Object.entries(raw)) {
      const ring = Number(key) as BridgeRingLevel;
      if (ring in BridgeRingLevel) {
        map.set(ring, new Set(tools));
      }
    }
    return map;
  }
}
