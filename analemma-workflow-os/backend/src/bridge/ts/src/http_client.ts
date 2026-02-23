/**
 * http_client.ts — 커널 전용 Axios 인스턴스
 *
 * 모든 커널 통신(sendPropose, sendObservation, sendFailure, syncFromKernel)은
 * 이 파일의 클라이언트를 사용하여 타임아웃·재시도 로직을 중앙화한다.
 *
 * 재시도 정책:
 *   - 대상: 네트워크 오류(ECONNRESET, ETIMEDOUT, ECONNABORTED) + 5xx 응답
 *   - 최대 횟수: MAX_RETRIES (기본 2회)
 *   - 대기: 지수 백오프 (200ms, 400ms)
 *   - 4xx 오류는 재시도하지 않음 (클라이언트 오류 — 재시도해도 무의미)
 */

import axios, { AxiosInstance, AxiosError } from "axios";

// ─── 설정 ──────────────────────────────────────────────────────────────────────

const MAX_RETRIES = 2;
const RETRY_BASE_MS = 200;

const RETRYABLE_ERROR_CODES = new Set([
  "ECONNRESET",
  "ETIMEDOUT",
  "ECONNABORTED",
  "ENOTFOUND",
  "ERR_NETWORK",
]);

// ─── 재시도 인터셉터 팩토리 ────────────────────────────────────────────────────

function attachRetryInterceptor(instance: AxiosInstance): void {
  instance.interceptors.response.use(
    undefined, // 성공 응답은 그대로 통과
    async (error: AxiosError) => {
      const config = error.config as (typeof error.config & { _retryCount?: number });
      if (!config) throw error;

      config._retryCount = (config._retryCount ?? 0) + 1;

      const isNetworkError =
        !error.response && RETRYABLE_ERROR_CODES.has(error.code ?? "");
      const isServerError =
        error.response != null && error.response.status >= 500;

      if (config._retryCount <= MAX_RETRIES && (isNetworkError || isServerError)) {
        const delayMs = RETRY_BASE_MS * config._retryCount;
        await new Promise((resolve) => setTimeout(resolve, delayMs));
        return instance(config);
      }

      throw error;
    }
  );
}

// ─── 공개 클라이언트 ───────────────────────────────────────────────────────────

/**
 * proposeClient — SEGMENT_PROPOSE / Policy Sync용
 * 타임아웃: 10s (거버넌스 판정 포함 왕복)
 */
export const proposeClient: AxiosInstance = axios.create({
  timeout: 10_000,
  headers: { "Content-Type": "application/json" },
});
attachRetryInterceptor(proposeClient);

/**
 * observeClient — Observation / Fail 보고용
 * 타임아웃: 5s (비치명적, fire-and-forget 시나리오에서도 사용)
 */
export const observeClient: AxiosInstance = axios.create({
  timeout: 5_000,
  headers: { "Content-Type": "application/json" },
});
attachRetryInterceptor(observeClient);
