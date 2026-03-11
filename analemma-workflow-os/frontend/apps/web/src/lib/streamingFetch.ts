/*
  streamingFetch.ts
  - fetch + ReadableStream 기반 SSE-like JSONL 파서 (견고한 파싱 적용)
  - import.meta.env.VITE_LFU_FUNCTION_URL, VITE_LFU_HTTPAPI_URL, VITE_LFU_USE_FUNCTION_URL 참조
  - 사용: streamDesignAssistant(body, handlers) 또는 callDesignAssistantSync(url, body, authToken)
  - Co-design 지원: streamCoDesignAssistant(body, handlers) 또는 callCoDesignAssistantSync(url, body, authToken)
*/
import { JSONLParser, ParsedChunk } from './jsonlParser';

type OnMessage = (obj: unknown) => void;
type OnDone = () => void;
type OnError = (err: Error) => void;

interface StreamOptions {
  onMessage?: OnMessage;
  onDone?: OnDone;
  onError?: OnError;
  authToken?: string | null;
  url?: string;
  signal?: AbortSignal | null;
  /** Timeout in milliseconds. Default: 5 minutes (300000ms) */
  timeout?: number;
}

// Default timeout: 5 minutes for long-running LLM requests
const DEFAULT_TIMEOUT_MS = 5 * 60 * 1000;

/**
 * 공통 스트리밍 처리 로직 (견고한 JSONL 파싱 적용)
 */
async function processStreamingResponse(
  response: Response, 
  onMessage?: OnMessage, 
  onDone?: OnDone, 
  onError?: OnError,
  cleanup?: () => void
): Promise<void> {
  const contentType = (response.headers.get('content-type') || '').toLowerCase();
  
  if (contentType.includes('application/json')) {
    const json = await response.json();
    onMessage && onMessage(json);
    onDone && onDone();
    return;
  }

  if (!response.body) {
    const txt = await response.text();
    try {
      const j = JSON.parse(txt);
      onMessage && onMessage(j);
    } catch {
      // ignore non-JSON
    }
    onDone && onDone();
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder('utf-8');
  const parser = new JSONLParser(); // 견고한 JSONL 파서 사용

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      
      const chunk = decoder.decode(value, { stream: true });
      
      // 견고한 파싱 처리
      const parseResults = parser.processChunk(chunk);
      
      for (const result of parseResults) {
        if (result.type === 'complete' && result.data) {
          onMessage && onMessage(result.data);
          
          // 완료 신호 확인
          if (result.data?.type === 'status' && result.data?.data === 'done') {
            onDone && onDone();
          }
        } else if (result.type === 'error') {
          console.warn('JSONL parsing error:', result.error, 'Raw:', result.raw);
          // 에러는 로그만 남기고 계속 진행
        }
        // partial 타입은 무시 (다음 청크에서 완성될 예정)
      }
    }

    // 스트림 종료 시 남은 버퍼 처리
    const flushResults = parser.flush();
    for (const result of flushResults) {
      if (result.type === 'complete' && result.data) {
        onMessage && onMessage(result.data);
        if (result.data?.type === 'status' && result.data?.data === 'done') {
          onDone && onDone();
        }
      }
    }
  } catch (_err: unknown) {
    const e = _err instanceof Error ? _err : new Error(String(_err));
    onError && onError(e);
    throw e;
  } finally {
    // Clean up timeout controller
    cleanup && cleanup();
    try {
      reader.cancel();
    } catch (_err) {
      // ignore cancel errors
    }
  }
}

/**
 * Creates an AbortController with timeout support.
 * Returns the signal to use and a cleanup function.
 */
function createTimeoutController(
  externalSignal?: AbortSignal | null,
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): { signal: AbortSignal; cleanup: () => void } {
  const controller = new AbortController();
  
  // Set up timeout
  const timeoutId = setTimeout(() => {
    controller.abort(new Error(`Request timed out after ${timeoutMs}ms`));
  }, timeoutMs);
  
  // If external signal is provided, abort when it aborts
  const externalAbortHandler = () => {
    controller.abort(externalSignal?.reason);
  };
  
  if (externalSignal) {
    externalSignal.addEventListener('abort', externalAbortHandler);
  }
  
  const cleanup = () => {
    clearTimeout(timeoutId);
    if (externalSignal) {
      externalSignal.removeEventListener('abort', externalAbortHandler);
    }
  };
  
  return { signal: controller.signal, cleanup };
}

/**
 * Waits for a CoDesign worker result delivered via WebSocket.
 * The WebSocketManager dispatches a 'codesign_result' CustomEvent on window.
 */
function waitForCoDesignResult(taskId: string | null, timeoutMs: number): Promise<any> {
  return new Promise((resolve, reject) => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      // If no taskId filter, accept any result; otherwise match task_id
      if (!taskId || detail.task_id === taskId) {
        cleanup();
        resolve(detail);
      }
    };

    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`CoDesign result timeout after ${Math.round(timeoutMs / 1000)}s`));
    }, timeoutMs);

    function cleanup() {
      window.removeEventListener('codesign_result', handler);
      clearTimeout(timer);
    }

    window.addEventListener('codesign_result', handler);
  });
}

const getEnv = (k: string) => (import.meta.env ? (import.meta.env[k] as string | undefined) : undefined);
export const resolveDesignAssistantEndpoint = (): { url: string; requiresAuth: boolean } => {
  const useFunctionUrl = String(getEnv('VITE_LFU_USE_FUNCTION_URL') ?? 'false').toLowerCase() === 'true';
  const funcUrl = getEnv('VITE_LFU_FUNCTION_URL') || '';
  const httpApi = getEnv('VITE_LFU_HTTPAPI_URL') || '';
  const defaultApiBase = getEnv('VITE_API_BASE_URL') || '';

  if (useFunctionUrl && funcUrl) {
    return { url: funcUrl, requiresAuth: false };
  }
  if (httpApi) {
    return { url: httpApi.endsWith('/design-assistant') ? httpApi : `${httpApi.replace(/\/$/, '')}/design-assistant`, requiresAuth: true };
  }
  if (defaultApiBase) {
    return { url: `${defaultApiBase.replace(/\/$/, '')}/design-assistant`, requiresAuth: true };
  }
  throw new Error('Design assistant endpoint not configured (VITE_LFU_FUNCTION_URL or VITE_LFU_HTTPAPI_URL or VITE_API_BASE_URL required)');
};

export const resolveCoDesignEndpoint = (endpoint: string = 'codesign'): { url: string; requiresAuth: boolean } => {
  const httpApi = getEnv('VITE_LFU_HTTPAPI_URL') || '';
  const defaultApiBase = getEnv('VITE_API_BASE_URL') || '';

  // Map endpoints that require /workflows/ prefix
  const workflowEndpoints: Record<string, string> = {
    'audit': 'workflows/audit',
    'simulate': 'workflows/simulate',
  };
  const resolvedEndpoint = workflowEndpoints[endpoint] || endpoint;

  if (httpApi) {
    return { url: httpApi.endsWith(`/${resolvedEndpoint}`) ? httpApi : `${httpApi.replace(/\/$/, '')}/${resolvedEndpoint}`, requiresAuth: true };
  }
  if (defaultApiBase) {
    return { url: `${defaultApiBase.replace(/\/$/, '')}/${resolvedEndpoint}`, requiresAuth: true };
  }
  throw new Error('Co-design endpoint not configured (VITE_LFU_HTTPAPI_URL or VITE_API_BASE_URL required)');
};

export async function streamDesignAssistant(body: unknown, opts: StreamOptions = {}) {
  const { onMessage, onDone, onError, authToken, timeout = DEFAULT_TIMEOUT_MS } = opts;
  const resolved = opts.url ? { url: opts.url, requiresAuth: false } : resolveDesignAssistantEndpoint();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (resolved.requiresAuth && authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }

  // Create timeout controller
  const { signal, cleanup } = createTimeoutController(opts.signal, timeout);

  const fetchOptions: RequestInit = {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  };

  let res: Response;
  try {
    res = await fetch(resolved.url, fetchOptions);
  } catch (err) {
    cleanup();
    const e = err instanceof Error ? err : new Error(String(err));
    onError && onError(e);
    throw e;
  }

  if (!res.ok) {
    cleanup();
    const text = await res.text();
    const e = new Error(`HTTP ${res.status}: ${text}`);
    onError && onError(e);
    throw e;
  }

  // 공통 스트리밍 처리 로직 사용
  await processStreamingResponse(res, onMessage, onDone, onError, cleanup);
}

export async function streamCoDesignAssistant(endpoint: string, body: unknown, opts: StreamOptions = {}) {
  const { onMessage, onDone, onError, authToken, timeout = DEFAULT_TIMEOUT_MS } = opts;
  const resolved = opts.url ? { url: opts.url, requiresAuth: false } : resolveCoDesignEndpoint(endpoint);
  
  // 디버깅: 요청 URL 및 설정 로그
  console.log('[CoDesign] Request Details:', {
    endpoint,
    resolvedUrl: resolved.url,
    requiresAuth: resolved.requiresAuth,
    hasAuthToken: !!authToken,
    body: JSON.stringify(body).substring(0, 200) + '...'
  });
  
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (resolved.requiresAuth && authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }

  // Create timeout controller
  const { signal, cleanup } = createTimeoutController(opts.signal, timeout);

  const fetchOptions: RequestInit = {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  };

  let res: Response;
  try {
    console.log('[CoDesign] Sending fetch request to:', resolved.url);
    res = await fetch(resolved.url, fetchOptions);
    console.log('[CoDesign] Response received:', { status: res.status, ok: res.ok });
  } catch (err) {
    cleanup();
    console.error('[CoDesign] Fetch error:', err);
    const e = err instanceof Error ? err : new Error(String(err));
    onError && onError(e);
    throw e;
  }

  if (!res.ok) {
    cleanup();
    const text = await res.text();
    const e = new Error(`HTTP ${res.status}: ${text}`);
    onError && onError(e);
    throw e;
  }

  // Handle 202 Accepted (async worker pattern):
  // Backend spawns a worker Lambda and sends result via WebSocket.
  if (res.status === 202) {
    let taskId: string | null = null;
    try {
      const body = await res.json();
      taskId = body.task_id;
      console.log('[CoDesign] Async mode — task_id:', taskId);
    } catch { /* body may not be JSON */ }

    // Show processing indicator in chat
    onMessage?.({ type: 'text', data: 'Generating workflow... (this may take a moment)' });

    // Wait for WebSocket codesign_result event
    const asyncTimeout = timeout || DEFAULT_TIMEOUT_MS;
    try {
      const result = await waitForCoDesignResult(taskId, asyncTimeout);
      console.log('[CoDesign] Received async result:', result.result_type);

      if (result.result_type === 'error') {
        const errMsg = result.data?.error || 'CoDesign processing failed';
        onError?.(new Error(errMsg));
      } else {
        // Send the assembled workflow object first.
        // processStreamingChunk expects {nodes: [], edges: []} for loadWorkflowToCanvas,
        // which routes through convertWorkflowFromBackendFormat for proper type mapping.
        // Individual chunks ({type: "node", data: ...}) lack the "op" field that
        // processStreamingChunk requires for incremental updates, so they would be dropped.
        if (result.data?.workflow) {
          onMessage?.(result.data.workflow);
        }

        // Replay non-node/edge chunks (suggestions, text, status, audit) for chat display
        const chunks = result.data?.chunks;
        if (Array.isArray(chunks)) {
          for (const chunk of chunks) {
            const ct = (chunk as any)?.type;
            if (ct && ct !== 'node' && ct !== 'edge' && ct !== 'workflow') {
              onMessage?.(chunk);
            }
          }
        }
        onDone?.();
      }
    } catch (e) {
      onError?.(e instanceof Error ? e : new Error(String(e)));
    } finally {
      cleanup();
    }
    return;
  }

  // Standard streaming response
  await processStreamingResponse(res, onMessage, onDone, onError, cleanup);
}

export async function callDesignAssistantSync(url: string, body: unknown, authToken?: string | null) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;
  const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
  const contentType = (res.headers.get('content-type') || '').toLowerCase();
  if (contentType.includes('application/json')) return res.json();
  const txt = await res.text();
  return { text: txt };
}

export async function callCoDesignAssistantSync(endpoint: string, body: unknown, authToken?: string | null) {
  const resolved = resolveCoDesignEndpoint(endpoint);
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (resolved.requiresAuth && authToken) headers['Authorization'] = `Bearer ${authToken}`;
  const res = await fetch(resolved.url, { method: 'POST', headers, body: JSON.stringify(body) });
  const contentType = (res.headers.get('content-type') || '').toLowerCase();
  if (contentType.includes('application/json')) return res.json();
  const txt = await res.text();
  return { text: txt };
}

export default {
  streamDesignAssistant,
  streamCoDesignAssistant,
  callDesignAssistantSync,
  callCoDesignAssistantSync,
  resolveDesignAssistantEndpoint,
  resolveCoDesignEndpoint,
};
