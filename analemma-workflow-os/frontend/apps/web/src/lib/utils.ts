import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { TEST_KEYWORD_PATTERN, LEGACY_MOCK_PATTERN, GENERIC_TEST_PATTERN } from './testConstants';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const formatTimestamp = (timestamp: number): string => {
  // 백엔드에서 초(seconds) 단위로 올 수 있으므로 자동 보정
  // 10자리: 초 단위, 13자리: 밀리초 단위
  const normalizedTimestamp = timestamp.toString().length === 10 ? timestamp * 1000 : timestamp;
  const date = new Date(normalizedTimestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / (1000 * 60));
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  return date.toLocaleDateString();
};

// 날짜 포맷팅 (WorkflowStatusTab에서 사용)
export const formatDate = (dateString: string): string => {
  try {
    const date = new Date(dateString);
    return date.toLocaleString();
  } catch {
    return dateString;
  }
};

// 실행 시간 포맷팅 (WorkflowStatusTab에서 사용)
export const formatDuration = (start: string, end?: string): string => {
  try {
    const startTime = new Date(start).getTime();
    const endTime = end ? new Date(end).getTime() : Date.now();
    const duration = Math.floor((endTime - startTime) / 1000);

    if (duration < 60) return `${duration}s`;
    if (duration < 3600) return `${Math.floor(duration / 60)}m ${duration % 60}s`;
    return `${Math.floor(duration / 3600)}h ${Math.floor((duration % 3600) / 60)}m`;
  } catch {
    return 'Unknown';
  }
};

// 초기 상태 파싱 유틸리티 (SavedWorkflows에서 사용)
export interface WorkflowInput {
  initial_state: {
    user_prompt?: string;
    mock_behavior?: string;
    [key: string]: unknown;
  };
  source_file?: string;
  [key: string]: unknown;
}

export const parseInitialState = (userInput: string, currentWorkflow?: { inputs?: Record<string, any>; source_file?: string }): WorkflowInput => {
  const inputs: WorkflowInput = { initial_state: { user_prompt: userInput } };

  // If user provided a JSON object already containing initial_state, prefer that
  if (userInput.startsWith('{')) {
    try {
      const parsed = JSON.parse(userInput);
      if (parsed && typeof parsed === 'object' && parsed.initial_state) {
        // Use provided structured initial_state directly
        Object.assign(inputs, parsed as Record<string, unknown>);
        console.log('🧪 Using provided structured initial_state JSON');
      }
    } catch (e) {
      // malformed JSON — ignore and keep natural-language wrapper
      console.warn('Failed to parse JSON initial state; using natural-language wrapper', e);
    }
  }

  // Place any mock keywords inside inputs.initial_state.mock_behavior to match backend expectations
  const initialStateObj = (inputs.initial_state && typeof inputs.initial_state === 'object') ? (inputs.initial_state as Record<string, unknown>) : {};

  // 우선순위 1: 기존에 정의된 빌드 타임 키워드 검사
  const testKeywordMatch = userInput.match(TEST_KEYWORD_PATTERN);
  if (testKeywordMatch) {
    initialStateObj.mock_behavior = testKeywordMatch[1].toUpperCase();
    console.log('🧪 Known test keyword detected:', initialStateObj.mock_behavior);
  } else {
    // 우선순위 2: 범용 패턴 (대문자+언더스코어 조합) 검사
    const genericMatch = userInput.match(GENERIC_TEST_PATTERN);
    if (genericMatch) {
      initialStateObj.mock_behavior = genericMatch[0].toUpperCase();
      console.log('🧪 Generic test pattern detected:', initialStateObj.mock_behavior);
    }
  }

  // Also support legacy MOCK_BEHAVIOR_ prefix for backward compatibility
  const legacyMockMatch = userInput.match(LEGACY_MOCK_PATTERN);
  if (legacyMockMatch && !initialStateObj.mock_behavior) {
    initialStateObj.mock_behavior = legacyMockMatch[1].toUpperCase();
    console.log('🧪 Legacy mock behavior detected:', initialStateObj.mock_behavior);
  }

  // Reassign the possibly-updated initial_state back to inputs
  inputs.initial_state = initialStateObj;

  // Preserve source_file at top-level if present on the workflow
  if (currentWorkflow?.source_file) {
    inputs.source_file = currentWorkflow.source_file;
  } else if (currentWorkflow?.inputs?.source_file) {
    inputs.source_file = currentWorkflow.inputs.source_file;
  }

  return inputs;
};

/**
 * 타임스탬프 정규화 (ISO string 지원 및 초/밀리초 자동 판별)
 */
export function normalizeEventTs(candidate: any): number {
  if (!candidate && candidate !== 0) return 0;

  // Handle object with common timestamp fields
  let val = candidate;
  if (typeof candidate === 'object') {
    val = candidate.payload?.timestamp ??
      candidate.timestamp ??
      candidate.receivedAt ??
      candidate.start_time ??
      candidate.created_at ??
      candidate;
  }

  if (!val && val !== 0) return 0;

  // Try parsing as Date for ISO strings
  if (typeof val === 'string') {
    const parsed = new Date(val).getTime();
    if (!isNaN(parsed)) return parsed;
  }

  const num = Number(val) || 0;
  // Timestamp is in seconds if less than 10 billion
  return num < 10000000000 ? num * 1000 : num;
}

/**
 * Progress 계산 유틸리티 (중복 제거)
 * - 0-100% 범위 보장
 * - NaN 방지
 * - 100% 초과 방지 (AI가 예상보다 많은 세그먼트 실행 시)
 */
export function calculateProgress(current: number | undefined, total: number | undefined): number {
  const curr = current || 0;
  const tot = Math.max(total || 1, 1); // 0 분모 방지
  const raw = Math.round((curr / tot) * 100);
  return Math.min(Math.max(isNaN(raw) ? 0 : raw, 0), 100);
}

/**
 * 상대 시간 포맷팅 (중복 제거)
 * - "Just now", "5m ago", "2h ago", "날짜" 형식
 */
export function formatRelativeTime(timestamp: number | undefined): string {
  if (!timestamp) return '';
  const ms = timestamp < 10000000000 ? timestamp * 1000 : timestamp;
  const diffMs = Date.now() - ms;
  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffSecs / 60);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffSecs < 60) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  
  return new Date(ms).toLocaleDateString();
}

// Safe JSON parse helper used across UI: returns parsed object or { raw: original }
export const safeParseJson = (data: unknown): any => {
  if (typeof data === 'string') {
    try {
      return JSON.parse(data);
    } catch {
      return { raw: data };
    }
  }
  return data;
};

// Sanitize resume payload for resume API - accept minimal workflow reference shape
// Backend expects { conversation_id: string, user_input: object }
export const sanitizeResumePayload = (workflowRef: { conversation_id?: string | null; execution_id?: string | null } | null | undefined, response: string) => {
  if (!workflowRef) {
    throw new Error('Workflow reference is missing');
  }

  const sanitizedResponse = typeof response === 'string' ? response.trim() : String(response);
  if (!sanitizedResponse || sanitizedResponse.length === 0) {
    throw new Error('Response text is required');
  }

  const conversationId = workflowRef.conversation_id || workflowRef.execution_id;
  if (!conversationId) {
    throw new Error('Missing conversation_id or execution_id');
  }

  const payload: { user_input: Record<string, unknown>; conversation_id: string; execution_id?: string } = {
    user_input: { response: sanitizedResponse },  // Wrap in object as backend expects
    conversation_id: conversationId,
  };

  if (workflowRef.execution_id) payload.execution_id = workflowRef.execution_id;

  return payload;
};
