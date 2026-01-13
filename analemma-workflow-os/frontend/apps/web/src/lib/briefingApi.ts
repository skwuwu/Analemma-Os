/**
 * Plan Briefing & Time Machine Debugging API Client
 * 
 * 체크포인트 및 브리핑 기능을 위한 API 클라이언트입니다.
 */

import { makeAuthenticatedRequest, parseApiResponse } from '@/lib/api';
import type {
  PlanBriefing,
  DetailedDraft,
  TimelineItem,
  Checkpoint,
  CheckpointDetail,
  RollbackRequest,
  RollbackPreview,
  BranchInfo,
  StateDiff,
  RollbackSuggestion,
} from '@/lib/types';

const API_BASE = import.meta.env.VITE_API_BASE_URL;

// ===== Plan Briefing API =====

export interface PreviewWorkflowRequest {
  workflow_config: {
    id?: string;
    name?: string;
    nodes: unknown[];
    edges: unknown[];
  };
  initial_statebag?: Record<string, unknown>;
  user_context?: Record<string, unknown>;
  use_llm?: boolean;
}

/**
 * 워크플로우 실행 전 미리보기 생성
 */
export async function generateWorkflowPreview(
  request: PreviewWorkflowRequest
): Promise<PlanBriefing> {
  const response = await makeAuthenticatedRequest(`${API_BASE}/workflows/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to generate preview: ${error}`);
  }

  return parseApiResponse<PlanBriefing>(response);
}

export interface DetailedDraftRequest {
  node_config: Record<string, unknown>;
  input_data: Record<string, unknown>;
  output_type: string;
}

/**
 * 특정 노드의 상세 초안 조회
 */
export async function getDetailedDraft(
  request: DetailedDraftRequest
): Promise<DetailedDraft> {
  const response = await makeAuthenticatedRequest(`${API_BASE}/workflows/preview/detailed-draft`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to get detailed draft: ${error}`);
  }

  return parseApiResponse<DetailedDraft>(response);
}

// ===== Checkpoint API =====

/**
 * 실행 타임라인 조회
 */
export async function getExecutionTimeline(
  threadId: string,
  includeState: boolean = true
): Promise<{ thread_id: string; timeline: TimelineItem[] }> {
  const params = new URLSearchParams({ include_state: String(includeState) });
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/executions/${encodeURIComponent(threadId)}/timeline?${params}`
  );

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to get timeline: ${error}`);
  }

  return parseApiResponse(response);
}

/**
 * 체크포인트 목록 조회
 */
export async function listCheckpoints(
  threadId: string,
  limit: number = 50
): Promise<{ thread_id: string; checkpoints: Checkpoint[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/executions/${encodeURIComponent(threadId)}/checkpoints?${params}`
  );

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to list checkpoints: ${error}`);
  }

  return parseApiResponse(response);
}

/**
 * 특정 체크포인트 상세 조회
 */
export async function getCheckpointDetail(
  threadId: string,
  checkpointId: string
): Promise<CheckpointDetail> {
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/executions/${encodeURIComponent(threadId)}/checkpoints/${encodeURIComponent(checkpointId)}`
  );

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to get checkpoint: ${error}`);
  }

  return parseApiResponse(response);
}

// ===== Time Machine API =====

/**
 * 롤백 미리보기
 */
export async function previewRollback(
  request: Omit<RollbackRequest, 'preview_only'>
): Promise<RollbackPreview> {
  const response = await makeAuthenticatedRequest(`${API_BASE}/executions/rollback/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...request, preview_only: true }),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to preview rollback: ${error}`);
  }

  return parseApiResponse(response);
}

/**
 * 롤백 및 분기 생성
 */
export async function executeRollback(
  request: Omit<RollbackRequest, 'preview_only'>
): Promise<BranchInfo> {
  const response = await makeAuthenticatedRequest(`${API_BASE}/executions/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...request, preview_only: false }),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to execute rollback: ${error}`);
  }

  return parseApiResponse(response);
}

/**
 * 체크포인트 비교
 */
export async function compareCheckpoints(
  threadId: string,
  checkpointIdA: string,
  checkpointIdB: string
): Promise<StateDiff & { checkpoint_a: string; checkpoint_b: string }> {
  const response = await makeAuthenticatedRequest(`${API_BASE}/executions/checkpoints/compare`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      thread_id: threadId,
      checkpoint_id_a: checkpointIdA,
      checkpoint_id_b: checkpointIdB,
    }),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to compare checkpoints: ${error}`);
  }

  return parseApiResponse(response);
}

/**
 * 분기 히스토리 조회
 */
export async function getBranchHistory(
  threadId: string
): Promise<{ thread_id: string; branches: unknown[] }> {
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/executions/${encodeURIComponent(threadId)}/branches`
  );

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to get branch history: ${error}`);
  }

  return parseApiResponse(response);
}

/**
 * 롤백 추천 지점 조회
 */
export async function getRollbackSuggestions(
  threadId: string
): Promise<{ thread_id: string; suggestions: RollbackSuggestion[] }> {
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/executions/${encodeURIComponent(threadId)}/rollback-suggestions`
  );

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to get rollback suggestions: ${error}`);
  }

  return parseApiResponse(response);
}
