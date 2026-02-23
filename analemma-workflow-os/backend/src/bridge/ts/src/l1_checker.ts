/**
 * LocalL1Checker — Optimistic Mode 브릿지 내장 경량 보안 검사기 (TypeScript)
 *
 * 네트워크 없이 즉시 실행 (~1ms).
 * - Zero-Width Space / RTL Override 제거
 * - Homoglyph 정규화 (간소화 버전)
 * - 인젝션 패턴 매칭
 * - Capability Map 화이트리스트 확인 (Default-Deny)
 * - Policy Sync: syncFromKernel() → MappedPolicy | null 반환
 *   (AnalemmaBridge.create()가 DESTRUCTIVE 목록 동기화에 활용)
 */

import {
  BridgeRingLevel,
  CAPABILITY_MAP,
  L1CheckResult,
  PolicySyncResponse,
} from "./types";
import { proposeClient } from "./http_client";
import { PolicyMapper, MappedPolicy } from "./policy_mapper";

const MAX_PARAMS_SCAN_CHARS = 4_096;

// Zero-Width / RTL 제어 문자
const ZW_REGEX = /[\u200b\u200c\u200d\ufeff\u202e\u202d]/g;

// Homoglyph 치환 맵 (자주 사용되는 키릴 유사 문자)
const HOMOGLYPH_MAP: Record<string, string> = {
  "\u0430": "a", "\u0435": "e", "\u043e": "o",
  "\u0440": "p", "\u0441": "c", "\u0445": "x",
  "\u03b1": "a", "\u03bf": "o",
};
const HOMOGLYPH_REGEX = new RegExp(
  Object.keys(HOMOGLYPH_MAP).join("|"),
  "g",
);

const DEFAULT_INJECTION_PATTERNS: RegExp[] = [
  /ignore\s+(all\s+)?previous\s+instructions/i,
  /disregard\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions|context)/i,
  /you\s+are\s+now\s+(?:in\s+)?(?:developer|jailbreak|dan)\s+mode/i,
  /system\s+prompt\s+(?:reveal|show|display|output)/i,
  /print\s+(?:your\s+)?(?:system\s+)?instructions/i,
  /act\s+as\s+(?:if\s+)?(?:you\s+(?:have\s+)?no\s+restrictions|an?\s+unrestricted)/i,
  /이전\s+지시(?:사항)?\s*(?:무시|삭제|초기화)/,
  /시스템\s+프롬프트\s*(?:누설|출력|보여|공개)/,
  /제한\s*(?:없이|해제|무시)/,
];

function normalize(text: string): string {
  text = text.replace(ZW_REGEX, "");
  text = text.normalize("NFKC");
  text = text.replace(HOMOGLYPH_REGEX, (ch) => HOMOGLYPH_MAP[ch] ?? ch);
  return text;
}

export class LocalL1Checker {
  private injectionPatterns: RegExp[] = [...DEFAULT_INJECTION_PATTERNS];
  private capabilityMap: Map<BridgeRingLevel, Set<string>> = new Map(
    Object.entries(CAPABILITY_MAP).map(([k, v]) => [
      Number(k) as BridgeRingLevel,
      new Set(v),
    ])
  );
  private _policyVersion = "local_default";

  get policyVersion(): string {
    return this._policyVersion;
  }

  check(
    thought: string,
    action: string,
    ringLevel: BridgeRingLevel = BridgeRingLevel.USER,
    params?: Record<string, unknown>
  ): L1CheckResult {
    // 1. 텍스트 정규화 (ZWS + Homoglyph)
    const normThought = normalize(thought);
    const normAction = normalize(action);

    let paramsText = "";
    if (params) {
      try {
        const raw = JSON.stringify(params);
        paramsText = normalize(raw.slice(0, MAX_PARAMS_SCAN_CHARS));
      } catch {
        paramsText = String(params).slice(0, MAX_PARAMS_SCAN_CHARS);
      }
    }

    const scanText = `${normThought} ${normAction} ${paramsText}`;

    // 2. 인젝션 패턴 검사
    for (const pattern of this.injectionPatterns) {
      if (pattern.test(scanText)) {
        return {
          allowed: false,
          reason: `L1 injection pattern blocked: ${pattern.source}`,
        };
      }
    }

    // 3. Capability Map 확인 (Default-Deny)
    if (!this.checkCapability(ringLevel, normAction)) {
      const ringName = BridgeRingLevel[ringLevel] ?? `Ring${ringLevel}`;
      return {
        allowed: false,
        reason: `L1 capability denied: '${action}' not allowed at ${ringName} (Ring ${ringLevel})`,
      };
    }

    return { allowed: true, reason: null };
  }

  injectPatterns(
    injectionPatterns: string[],
    capabilityMap?: Record<string, string[]>,
    version?: string
  ): void {
    this.injectionPatterns = injectionPatterns.map(
      (p) => new RegExp(p, "iu")
    );
    if (capabilityMap) {
      this.capabilityMap = new Map(
        Object.entries(capabilityMap).map(([k, v]) => [
          Number(k) as BridgeRingLevel,
          new Set(v),
        ])
      );
    }
    if (version) this._policyVersion = version;
    console.debug(
      `[LocalL1Checker] Policy injected. patterns=${this.injectionPatterns.length} version=${this._policyVersion}`
    );
  }

  /**
   * VSM /v1/policy/sync에서 최신 정책을 내려받아 패턴·CapabilityMap을 갱신.
   *
   * @returns MappedPolicy — 성공 시 (버전 동일 포함). DESTRUCTIVE 목록 포함.
   *          null          — 네트워크 오류 또는 서버 응답 실패.
   *
   * AnalemmaBridge.create()가 반환값을 받아 updateDestructive()를 호출하여
   * DESTRUCTIVE_ACTIONS / DESTRUCTIVE_PATTERNS를 커널 기준으로 갱신한다.
   */
  async syncFromKernel(kernelEndpoint: string): Promise<MappedPolicy | null> {
    try {
      const resp = await proposeClient.get<PolicySyncResponse>(
        `${kernelEndpoint}/v1/policy/sync`
      );
      const data = resp.data;

      // PolicyMapper로 서버 응답 → TS 타입 변환 (패턴 컴파일, CapMap 빌드 포함)
      const mapped = PolicyMapper.fromSync(data);

      if (data.version !== this._policyVersion) {
        this.injectPatterns(
          data.injection_patterns,
          data.capability_map,
          data.version
        );
        console.info(
          `[LocalL1Checker] Synced from kernel. version=${data.version}`
        );
      }

      // 버전이 동일해도 MappedPolicy 반환 — DESTRUCTIVE 목록 동기화에 사용
      return mapped;
    } catch (err) {
      console.warn(
        `[LocalL1Checker] Policy sync failed (using local defaults): ${err}`
      );
      return null;
    }
  }

  private checkCapability(
    ringLevel: BridgeRingLevel,
    action: string
  ): boolean {
    if (ringLevel === BridgeRingLevel.KERNEL) return true;
    const allowed = this.capabilityMap.get(ringLevel);
    return allowed?.has(action) ?? false; // Default-Deny
  }
}
