/**
 * Task Manager API Client
 * 
 * Task Manager UI를 위한 API 클라이언트입니다.
 * 비즈니스 관점의 Task 정보를 조회합니다.
 */

import { makeAuthenticatedRequest, parseApiResponse } from '@/lib/api';
import type { TaskSummary, TaskDetail, TaskListResponse } from '@/lib/types';

const API_BASE = import.meta.env.VITE_API_BASE_URL;

// ===== Task List API =====

export interface TaskListOptions {
  status?: string;
  limit?: number;
  includeCompleted?: boolean;
}

/**
 * Task 목록 조회
 */
export async function listTasks(options: TaskListOptions = {}): Promise<TaskListResponse> {
  const params = new URLSearchParams();

  if (options.status) {
    params.set('status', options.status);
  }
  if (options.limit !== undefined) {
    params.set('limit', String(options.limit));
  }
  if (options.includeCompleted !== undefined) {
    params.set('include_completed', String(options.includeCompleted));
  }

  const url = params.toString()
    ? `${API_BASE}/tasks?${params}`
    : `${API_BASE}/tasks`;

  const response = await makeAuthenticatedRequest(url);

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to list tasks: ${error}`);
  }

  return parseApiResponse<TaskListResponse>(response);
}

// ===== Task Detail API =====

export interface TaskDetailOptions {
  includeTechnicalLogs?: boolean;
}

/**
 * Task 상세 정보 조회
 */
export async function getTaskDetail(
  taskId: string,
  options: TaskDetailOptions = {}
): Promise<TaskDetail> {
  const params = new URLSearchParams();

  if (options.includeTechnicalLogs) {
    params.set('include_technical_logs', 'true');
  }

  const url = params.toString()
    ? `${API_BASE}/tasks/${encodeURIComponent(taskId)}?${params}`
    : `${API_BASE}/tasks/${encodeURIComponent(taskId)}`;

  const response = await makeAuthenticatedRequest(url);

  if (!response.ok) {
    if (response.status === 404) {
      throw new Error('Task not found');
    }
    const error = await response.text();
    throw new Error(`Failed to get task detail: ${error}`);
  }

  return parseApiResponse<TaskDetail>(response);
}

/**
 * Task 결과물 조회
 */
export async function getTaskOutcomes(taskId: string): Promise<any> {
  const response = await makeAuthenticatedRequest(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/outcomes`);
  if (!response.ok) throw new Error('Failed to get task outcomes');
  return parseApiResponse<any>(response);
}

/**
 * Task 비즈니스 지표 조회
 */
export async function getTaskMetrics(taskId: string): Promise<any> {
  const response = await makeAuthenticatedRequest(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/metrics`);
  if (!response.ok) throw new Error('Failed to get task metrics');
  return parseApiResponse<any>(response);
}

/**
 * Artifact의 추론 경로 조회 (Reasoning Path)
 * 
 * 특정 결과물이 어떤 사고 과정을 거쳐 생성되었는지 추적
 */
export async function getReasoningPath(taskId: string, artifactId: string): Promise<{
  artifact_id: string;
  artifact_title: string;
  reasoning_steps: Array<{
    step_id: string;
    timestamp: string;
    step_type: 'decision' | 'observation' | 'action' | 'reasoning';
    content: string;
    node_id?: string | null;
    confidence?: number | null;
  }>;
  total_steps: number;
  total_duration_seconds?: number | null;
}> {
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/tasks/${encodeURIComponent(taskId)}/outcomes/${encodeURIComponent(artifactId)}/reasoning`
  );
  if (!response.ok) {
    if (response.status === 404) {
      throw new Error('Reasoning path not found');
    }
    throw new Error('Failed to get reasoning path');
  }
  return parseApiResponse(response);
}

// ===== Execution Healing Status API =====

export interface HealingFix {
  timestamp: string;
  issue_type: string;
  fix_description: string;
  success: boolean;
}

export interface RemainingIssue {
  issue_id: string;
  issue_type: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  description: string;
}

export interface HealingStatusResponse {
  executionArn: string;
  healing_active: boolean;
  healing_attempts: number;
  max_attempts: number;
  current_strategy?: 'retry' | 'fallback' | 'skip' | 'escalate';
  fixes_applied: HealingFix[];
  remaining_issues: RemainingIssue[];
  last_healing_at?: string | null;
}

/**
 * 실행 Healing 상태 조회
 * 
 * 워크플로우 실행 중 자가 치유 시도 및 결과를 조회
 */
export async function getHealingStatus(executionId: string): Promise<HealingStatusResponse> {
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/executions/${encodeURIComponent(executionId)}/healing-status`
  );
  if (!response.ok) {
    if (response.status === 404) {
      throw new Error('Execution not found');
    }
    throw new Error('Failed to get healing status');
  }
  return parseApiResponse<HealingStatusResponse>(response);
}

// ===== Status Display Helpers =====

export const STATUS_DISPLAY_MAP: Record<string, { label: string; color: string; icon: string }> = {
  queued: { label: '대기 중', color: 'bg-slate-500', icon: 'clock' },
  in_progress: { label: '진행 중', color: 'bg-blue-500', icon: 'loader' },
  pending_approval: { label: '승인 대기', color: 'bg-amber-500', icon: 'alert-circle' },
  completed: { label: '완료', color: 'bg-green-500', icon: 'check-circle' },
  failed: { label: '실패', color: 'bg-red-500', icon: 'x-circle' },
  cancelled: { label: '취소됨', color: 'bg-gray-400', icon: 'slash' },
};

export function getStatusDisplay(status: string) {
  return STATUS_DISPLAY_MAP[status] || { label: status, color: 'bg-gray-500', icon: 'help-circle' };
}

// ===== Thought Type Display Helpers =====

export const THOUGHT_TYPE_MAP: Record<string, { color: string; icon: string }> = {
  progress: { color: 'text-blue-500', icon: 'loader' },
  decision: { color: 'text-amber-500', icon: 'help-circle' },
  question: { color: 'text-purple-500', icon: 'message-circle' },
  warning: { color: 'text-orange-500', icon: 'alert-triangle' },
  success: { color: 'text-green-500', icon: 'check' },
  error: { color: 'text-red-500', icon: 'x' },
};

export function getThoughtTypeDisplay(type: string) {
  return THOUGHT_TYPE_MAP[type] || { color: 'text-gray-500', icon: 'circle' };
}
