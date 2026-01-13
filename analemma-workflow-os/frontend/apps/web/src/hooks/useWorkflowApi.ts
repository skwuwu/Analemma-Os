import { useInfiniteQuery, useMutation, useQueryClient, type QueryFunctionContext } from '@tanstack/react-query';
import { useToast } from '@/hooks/use-toast';
import { useState } from 'react';
import {
  type WorkflowConfig,
  type PendingNotification,
  type NotificationItem,
  type HistoryEntry,
  type WorkflowSummary,
  type WorkflowListResult,
  type WorkflowDetailResponse,
  type ExecutionHistoryResponse,
  type ExecutionSummary,
  type ResumeWorkflowRequest
} from '@/lib/types';
import { makeAuthenticatedRequest, parseApiResponse } from '@/lib/api';

const API_BASE = import.meta.env.VITE_API_BASE_URL;

interface NotificationsResponse {
  notifications: PendingNotification[];
  nextToken?: string;
}

interface ResumeWorkflowResponse {
  message: string;
  executionArn: string;
  conversation_id: string;
  status: 'success';
}

interface TestKeyword {
  keyword: string;
  description: string;
  category: 'basic' | 's3' | 'async' | 'edge_case';
}

interface TestKeywordsResponse {
  keywords: TestKeyword[];
}

interface ExecutionListResponse {
  executions: ExecutionSummary[];
  nextToken?: string;
}

interface ExecutionDetailStatus {
  type: "workflow_status";
  payload: {
    conversation_id: string;
    message: string | null;
    status: string;
    workflow_config: object | null;
    total_segments: number | null;
    current_state: object | null;
    step_function_state: object | null;
    pre_hitp_output: object | null;
    startDate: string | null;
    stopDate: string | null;
    current_segment: number;
    average_segment_duration: number | null;
    estimated_remaining_seconds: number | null;
    estimated_completion_time: number | null;
  };
}

const parseWorkflowDetail = (raw: WorkflowDetailResponse): WorkflowDetailResponse => {
  if (!raw || typeof raw !== 'object') {
    throw new Error('Invalid workflow detail response');
  }
  // config가 null이거나 undefined일 수 있음 - 이는 유효한 상태
  if (raw.config !== null && raw.config !== undefined && typeof raw.config !== 'object') {
    throw new Error('Invalid workflow configuration format');
  }
  // Helper: 안전한 숫자 변환 (문자열 숫자 -> number, NaN/빈값 -> undefined/null)
  const toNumberOrUndefined = (v: unknown): number | undefined => {
    if (v === undefined || v === null || v === '') return undefined;
    if (typeof v === 'number') return v;
    if (typeof v === 'string' && v.trim() !== '') {
      const n = Number(v);
      return Number.isFinite(n) ? n : undefined;
    }
    return undefined;
  };

  const toNumberOrNull = (v: unknown): number | null => {
    if (v === undefined || v === null || v === '') return null;
    if (typeof v === 'number') return v;
    if (typeof v === 'string' && v.trim() !== '') {
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    }
    return null;
  };

  return {
    ...raw,
    createdAt: toNumberOrUndefined(raw.createdAt as unknown),
    updatedAt: toNumberOrUndefined(raw.updatedAt as unknown),
    next_run_time: toNumberOrNull(raw.next_run_time as unknown),
    // DynamoDB may store booleans as strings "true"/"false" — normalize to boolean
    is_scheduled: raw.is_scheduled === true || String(raw.is_scheduled) === 'true',
  } as WorkflowDetailResponse;
};

const buildWorkflowDetailUrl = (name: string) => {
  return `${API_BASE}/by-name?name=${encodeURIComponent(name)}`;
};

export const useWorkflowApi = () => {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const {
    data: workflowData,
    isLoading,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery<WorkflowListResult>({
    queryKey: ['workflows'],
    queryFn: async ({ pageParam = null }: QueryFunctionContext) => {
      const pageToken = typeof pageParam === 'string' && pageParam.length > 0 ? pageParam : null;
      const params = pageToken ? `?nextToken=${encodeURIComponent(pageToken)}` : '';
      const response = await makeAuthenticatedRequest(`${API_BASE}/workflows${params}`);
      const body = await parseApiResponse(response);
      console.log('📋 Workflow list API response:', { body, workflows: body?.workflows });
      const workflowItems: WorkflowSummary[] = Array.isArray(body?.workflows)
        ? body.workflows.map((item: any) => {
          const mapped = {
            name: item.name,
            workflowId: item.workflowId,
            ownerId: item.ownerId,
          } as WorkflowSummary;
          console.log('📄 Mapping workflow item:', { original: item, mapped });
          return mapped;
        }).filter((w: WorkflowSummary) => w.name && w.name.trim().length > 0)
        : [];
      const next = typeof body?.nextToken === 'string' && body.nextToken.trim().length > 0 ? body.nextToken : null;
      return {
        workflows: workflowItems,
        nextToken: next,
      };
    },
    getNextPageParam: (lastPage) => lastPage.nextToken || undefined,
    initialPageParam: null,
  });

  const workflows = workflowData?.pages.flatMap(page => page.workflows) ?? [];

  const saveWorkflow = useMutation({
    mutationFn: async ({ workflowId, config, is_scheduled, next_run_time, name }: {
      workflowId?: string;
      config: any;
      is_scheduled?: boolean;
      next_run_time?: any;
      name?: string;
    }) => {
      // IMPORTANT: ownerId is intentionally NOT sent from the client.
      // Backend must extract ownerId from the authenticated JWT to prevent IDOR.
      const payload: any = { config };
      if (name) payload.name = name;
      if (is_scheduled !== undefined) payload.is_scheduled = is_scheduled;
      if (next_run_time !== undefined) payload.next_run_time = next_run_time;

      let response: Response;
      if (workflowId) {
        response = await makeAuthenticatedRequest(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(payload),
        });
      } else {
        response = await makeAuthenticatedRequest(`${API_BASE}/workflows`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(payload),
        });
      }
      return await parseApiResponse(response);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] });
      toast({ title: 'Workflow saved successfully' });
    },
    onError: (_error: unknown) => {
      toast({ title: 'Failed to save workflow', variant: 'destructive' });
    },
  });

  const deleteWorkflow = useMutation({
    mutationFn: async (workflowId: string) => {
      // Delete is authenticated via JWT; backend will verify owner from token.
      const response = await makeAuthenticatedRequest(`${API_BASE}/workflows/${encodeURIComponent(workflowId)}`, {
        method: 'DELETE',
      });

      return await parseApiResponse(response);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] });
      toast({ title: 'Workflow deleted' });
    },
    onError: (error: unknown) => {
      console.error('Delete workflow error:', error);
      const message = error instanceof Error ? error.message : 'Failed to delete workflow';
      toast({ title: message, variant: 'destructive' });
    },
  });

  const runWorkflow = useMutation({
    // Accept optional idempotencyKey so callers can provide an idempotency header
    mutationFn: async ({ workflowId, inputs, idempotencyKey }: { workflowId: string; inputs: unknown; idempotencyKey?: string }) => {
      // Defensive check: ensure workflowId is provided and valid to avoid sending
      // requests to URLs containing 'undefined' or empty ids.
      if (!workflowId || typeof workflowId !== 'string' || workflowId.trim().length === 0 || workflowId === 'undefined') {
        console.error('runWorkflow called without valid workflowId:', workflowId);
        // show user-facing feedback and abort
        try {
          toast({ title: 'Failed to run workflow: missing workflow ID', variant: 'destructive' });
        } catch (e) {
          // ignore toast failures
          void e;
        }
        throw new Error('runWorkflow: workflowId is required');
      }

      const url = `${API_BASE}/workflows/${encodeURIComponent(workflowId)}/run`;
      // Include workflowId in the body to make server-side debugging/logging easier
      // and to provide an explicit payload field the backend may also validate.
      const requestBody = JSON.stringify({ workflowId, input_data: inputs });
      const baseHeaders: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (idempotencyKey && typeof idempotencyKey === 'string' && idempotencyKey.trim().length > 0) {
        // RFC doesn't mandate header casing; backend accepts Idempotency-Key or idempotency-key
        baseHeaders['Idempotency-Key'] = idempotencyKey;
      }
      try {
        // Debug log: show exact request being made for easier troubleshooting
        console.log('➡️ runWorkflow request', {
          workflowId,
          encodedId: encodeURIComponent(workflowId),
          url,
          body: requestBody,
          apiBase: API_BASE
        });
      } catch (e) {
        // ignore logging errors
        void e;
      }

      const response = await makeAuthenticatedRequest(url, {
        method: 'POST',
        headers: baseHeaders,
        body: requestBody,
      });

      return await parseApiResponse(response);
    },
    onSuccess: (data) => {
      toast({ title: 'Workflow executed successfully' });
      return data;
    },
    onError: (_error: unknown) => {
      toast({ title: 'Failed to run workflow', variant: 'destructive' });
    },
  });

  const getWorkflowByNameMutation = useMutation({
    mutationFn: async (workflowName: string) => {
      if (!workflowName) {
        throw new Error('Workflow name is required');
      }
      const response = await makeAuthenticatedRequest(buildWorkflowDetailUrl(workflowName));

      const body = await parseApiResponse(response);
      console.log('🔍 getWorkflowByName raw response:', body);
      const result = {
        ...body,
        is_scheduled: body.is_scheduled === 'true',
        config: body.config ?? null,
      };
      console.log('📋 getWorkflowByName processed result:', result);
      const finalResult = parseWorkflowDetail(result as WorkflowDetailResponse);
      console.log('✅ getWorkflowByName final result:', finalResult);
      return finalResult;
    },
    onError: (error: unknown) => {
      console.error('Get workflow error:', error);
      const message = error instanceof Error ? error.message : 'Failed to load workflow';
      toast({ title: message, variant: 'destructive' });
    },
  });

  // 알림 센터 API
  const fetchNotifications = async (params?: {
    status?: 'pending' | 'sent' | 'all';  // API 규약에 'all' 추가
    type?: 'hitp_pause' | 'execution_progress' | 'workflow_completed';
    limit?: number;
    nextToken?: string;
  }): Promise<NotificationsResponse> => {
    const queryParams = new URLSearchParams();
    if (params?.status) queryParams.set('status', params.status);
    if (params?.type) queryParams.set('type', params.type);
    if (params?.limit) queryParams.set('limit', params.limit.toString());
    if (params?.nextToken) queryParams.set('nextToken', params.nextToken);

    const response = await makeAuthenticatedRequest(
      `${API_BASE}/notifications?${queryParams.toString()}`
    );

    return parseApiResponse<NotificationsResponse>(response);
  };

  const resumeWorkflowMutation = useMutation({
    mutationFn: async (request: ResumeWorkflowRequest): Promise<ResumeWorkflowResponse> => {
      const response = await makeAuthenticatedRequest(`${API_BASE}/resume`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(request),
      });

      return parseApiResponse<ResumeWorkflowResponse>(response);
    },
    onSuccess: () => {
      toast({ title: 'Workflow resumed successfully' });
    },
    onError: (_error: unknown) => {
      toast({ title: 'Failed to resume workflow', variant: 'destructive' });
    },
  });

  // Fetch executions list
  const fetchExecutions = async (nextToken?: string): Promise<ExecutionListResponse> => {
    const params = new URLSearchParams();
    params.set('limit', '20');
    if (nextToken) params.set('nextToken', nextToken);

    const response = await makeAuthenticatedRequest(
      `${API_BASE}/executions?${params.toString()}`
    );

    return parseApiResponse<ExecutionListResponse>(response);
  };

  // Fetch execution details
  const fetchExecutionDetail = async (executionArn: string): Promise<ExecutionDetailStatus> => {
    const response = await makeAuthenticatedRequest(
      `${API_BASE}/status?executionArn=${encodeURIComponent(executionArn)}`
    );

    return parseApiResponse<ExecutionDetailStatus>(response);
  };

  // Stop execution
  const stopExecutionMutation = useMutation({
    mutationFn: async (executionArn: string) => {
      const response = await makeAuthenticatedRequest(`${API_BASE}/executions/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ executionArn })
      });

      return parseApiResponse(response);
    },
    onSuccess: () => {
      toast({ title: 'Execution stopped successfully' });
    },
    onError: (error: unknown) => {
      console.error('Stop execution error:', error);
      const message = error instanceof Error ? error.message : 'Failed to stop execution';
      toast({ title: message, variant: 'destructive' });
    },
  });

  const deleteExecutionMutation = useMutation({
    mutationFn: async (executionArn: string) => {
      const url = `${API_BASE}/executions?executionArn=${encodeURIComponent(executionArn)}`;
      const response = await makeAuthenticatedRequest(url, {
        method: 'DELETE',
      });

      return parseApiResponse(response);
    },
    onSuccess: () => {
      toast({ title: 'Execution deleted successfully' });
    },
    onError: (error: unknown) => {
      console.error('Delete execution error:', error);
      const message = error instanceof Error ? error.message : 'Failed to delete execution';
      toast({ title: message, variant: 'destructive' });
    },
  });

  // [추가] 알림 닫기 (CORS 해결을 위해 POST 방식 + Body 사용)
  const dismissNotificationMutation = useMutation({
    mutationFn: async (executionId: string) => {
      // ARN이 포함된 executionId를 URL 경로에 넣지 않고 Body에 담아 보냅니다.
      const response = await makeAuthenticatedRequest(`${API_BASE}/notifications/dismiss`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ executionId }),
      });

      return parseApiResponse(response);
    },
    onSuccess: () => {
      // 알림 목록 갱신
      queryClient.invalidateQueries({ queryKey: ['notifications'] });
      // 필요하다면 active 실행 목록도 갱신
      queryClient.invalidateQueries({ queryKey: ['executions'] });
    },
    onError: (error: unknown) => {
      console.error('Dismiss notification error:', error);
      toast({ title: 'Failed to dismiss notification', variant: 'destructive' });
    },
  });

  // [추가] 지침 복제 (기존 워크플로우의 학습된 스타일을 새 워크플로우에 적용)
  const cloneInstructionsMutation = useMutation({
    mutationFn: async ({ sourceWorkflowId, targetWorkflowId }: {
      sourceWorkflowId: string;
      targetWorkflowId: string
    }) => {
      const response = await makeAuthenticatedRequest(`${API_BASE}/instructions/clone`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          source_workflow_id: sourceWorkflowId,
          target_workflow_id: targetWorkflowId
        }),
      });
      return parseApiResponse<{ message: string; cloned_count: number }>(response);
    },
    onSuccess: (data) => {
      toast({
        title: '지침 복제 완료',
        description: data.message || '기존 에이전트의 학습된 스타일이 적용되었습니다.'
      });
    },
    onError: (error: unknown) => {
      console.error('Clone instructions error:', error);
      const message = error instanceof Error ? error.message : '지침 복제에 실패했습니다.';
      toast({ title: message, variant: 'destructive' });
    },
  });

  return {
    workflows,
    isLoading,
    hasMoreWorkflows: hasNextPage,
    loadMoreWorkflows: fetchNextPage,
    isLoadingMore: isFetchingNextPage,
    saveWorkflow: saveWorkflow.mutate,
    // Async helpers for callers that need to await server response (e.g., get server-generated workflowId)
    saveWorkflowAsync: saveWorkflow.mutateAsync,
    deleteWorkflow: deleteWorkflow.mutate,
    runWorkflow: runWorkflow.mutate,
    // Async run helper
    runWorkflowAsync: runWorkflow.mutateAsync,
    isRunning: runWorkflow.isPending,
    getWorkflowByName: getWorkflowByNameMutation.mutateAsync,
    isLoadingWorkflow: getWorkflowByNameMutation.isPending,
    // 새로 추가된 API 메서드들
    fetchNotifications,
    resumeWorkflow: resumeWorkflowMutation.mutateAsync,
    isResuming: resumeWorkflowMutation.isPending,
    fetchExecutions,
    fetchExecutionDetail,
    stopExecution: stopExecutionMutation.mutateAsync,
    isStopping: stopExecutionMutation.isPending,
    deleteExecution: deleteExecutionMutation.mutateAsync,
    isDeleting: deleteExecutionMutation.isPending,
    // [추가] 외부에서 사용할 수 있도록 export
    dismissNotification: dismissNotificationMutation.mutateAsync,
    isDismissing: dismissNotificationMutation.isPending,
    // [추가] 지침 복제 API
    cloneInstructions: cloneInstructionsMutation.mutateAsync,
    isCloningInstructions: cloneInstructionsMutation.isPending,
    // getExecutionHistory는 별도의 useExecutionHistory 훅으로 분리됨
  };
};

export const useExecutionHistory = (executionArn: string, limit: number = 50, mask: boolean = true) => {
  return useInfiniteQuery({
    queryKey: ['executionHistory', executionArn, limit, mask],
    queryFn: async ({ pageParam }: QueryFunctionContext) => {
      const params = new URLSearchParams();
      params.set('limit', limit.toString());
      if (mask !== undefined) params.set('mask', mask.toString());
      if (typeof pageParam === 'string' && pageParam.length > 0) {
        params.set('nextToken', pageParam);
      }

      const response = await makeAuthenticatedRequest(
        `${API_BASE}/executions/history?executionArn=${encodeURIComponent(executionArn)}&${params.toString()}`
      );

      return parseApiResponse<ExecutionHistoryResponse>(response);
    },
    getNextPageParam: (lastPage) => lastPage.nextToken || undefined,
    initialPageParam: undefined as string | undefined,
    enabled: !!executionArn, // executionArn이 있을 때만 쿼리 실행
  });
};
