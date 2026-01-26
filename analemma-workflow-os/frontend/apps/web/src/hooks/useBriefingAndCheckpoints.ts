/**
 * Plan Briefing & Checkpoint Hooks (v2.0)
 * ==========================================
 * 
 * Plan Briefing과 Checkpoint 기능을 위한 React Query 훅입니다.
 * 
 * v2.0 Changes:
 * - PlanBriefing을 React Query 캐시로 관리 (useState → setQueryData)
 * - executionId 유효성 검사 강화
 * - optionsRef 패턴으로 콜백 안정성 개선
 * - useMemo로 체크포인트/브랜치 트리 구조 가공
 */

import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { 
  generateWorkflowPreview,
  getDetailedDraft,
  getExecutionTimeline,
  listCheckpoints,
  getCheckpointDetail,
  previewRollback,
  executeRollback,
  compareCheckpoints,
  getBranchHistory,
  getRollbackSuggestions,
  type PreviewWorkflowRequest,
  type DetailedDraftRequest,
} from '@/lib/briefingApi';
import type { 
  PlanBriefing, 
  DraftResult, 
  Checkpoint, 
  TimelineItem,
  RollbackRequest,
  StateDiff,
  CheckpointCompareResult,
  RollbackPreview,
  BranchInfo,
} from '@/lib/types';

// ============ Plan Briefing Hooks ============

interface UsePlanBriefingOptions {
  onSuccess?: (briefing: PlanBriefing) => void;
  onError?: (error: Error) => void;
}

export function usePlanBriefing(options: UsePlanBriefingOptions = {}) {
  const optionsRef = useRef(options);
  const queryClient = useQueryClient();
  const [briefingId, setBriefingId] = useState<string | null>(null);
  
  // Keep callbacks stable
  useEffect(() => {
    optionsRef.current = options;
  });
  
  // Query briefing from cache (React Query pattern)
  const briefingQuery = useQuery({
    queryKey: ['briefing', briefingId],
    queryFn: async () => {
      // Data is already in cache from mutation
      return queryClient.getQueryData<PlanBriefing>(['briefing', briefingId]) || null;
    },
    enabled: !!briefingId,
    staleTime: Infinity, // Briefing은 변경되지 않으므로 무한 캐시
  });
  
  const generateMutation = useMutation({
    mutationFn: async (request: PreviewWorkflowRequest) => {
      return await generateWorkflowPreview(request);
    },
    onSuccess: (data) => {
      // React Query 캐시에 저장 (다른 컴포넌트에서도 사용 가능)
      const id = data.briefing_id || `briefing-${Date.now()}`;
      queryClient.setQueryData(['briefing', id], data);
      setBriefingId(id);
      optionsRef.current.onSuccess?.(data);
    },
    onError: (error: Error) => {
      optionsRef.current.onError?.(error);
    },
  });
  
  const draftDetailQuery = useMutation({
    mutationFn: async (request: DetailedDraftRequest) => {
      return await getDetailedDraft(request);
    },
  });
  
  const generate = useCallback(
    (request: PreviewWorkflowRequest) => {
      return generateMutation.mutateAsync(request);
    },
    [generateMutation]
  );
  
  const getDetailedDraftContent = useCallback(
    (request: DetailedDraftRequest) => {
      return draftDetailQuery.mutateAsync(request);
    },
    [draftDetailQuery]
  );
  
  const clear = useCallback(() => {
    if (briefingId) {
      queryClient.removeQueries({ queryKey: ['briefing', briefingId] });
    }
    setBriefingId(null);
  }, [briefingId, queryClient]);
  
  return {
    briefing: briefingQuery.data || null,
    briefingId,
    isLoading: generateMutation.isPending,
    isError: generateMutation.isError,
    error: generateMutation.error,
    generate,
    getDetailedDraftContent,
    clear,
    // 드래프트 상세 조회 상태
    isDraftLoading: draftDetailQuery.isPending,
    draftError: draftDetailQuery.error,
  };
}

// ============ Checkpoint Hooks ============

interface UseCheckpointsOptions {
  executionId?: string;
  enabled?: boolean;
  refetchInterval?: number | false;
}

export function useCheckpoints({ 
  executionId, 
  enabled = true,
  refetchInterval = false 
}: UseCheckpointsOptions = {}) {
  const queryClient = useQueryClient();
  
  // 체크포인트 목록 조회
  const checkpointsQuery = useQuery({
    queryKey: ['checkpoints', executionId],
    queryFn: async () => {
      if (!executionId) throw new Error('executionId is required');
      const result = await listCheckpoints(executionId);
      return result.checkpoints;
    },
    enabled: enabled && !!executionId,
    refetchInterval,
  });
  
  // 타임라인 조회
  const timelineQuery = useQuery({
    queryKey: ['timeline', executionId],
    queryFn: async () => {
      if (!executionId) throw new Error('executionId is required');
      const result = await getExecutionTimeline(executionId);
      return result.timeline;
    },
    enabled: enabled && !!executionId,
    refetchInterval,
  });
  
  // 체크포인트 트리 구조 생성 (UI 렌더링용)
  const checkpointTree = useMemo(() => {
    const checkpoints = checkpointsQuery.data || [];
    const timeline = timelineQuery.data || [];
    
    // 트리 노드 생성
    const nodes = checkpoints.map(cp => ({
      id: cp.checkpoint_id,
      label: cp.checkpoint_id,
      timestamp: cp.created_at,
      data: cp,
      type: 'checkpoint' as const,
    }));
    
    // 엣지 생성 (타임라인 기반)
    const edges = timeline
      .map((item: any, index, arr) => {
        if (index === 0) return null;
        const prev = arr[index - 1] as any;
        return {
          source: prev.checkpoint_id || prev.id || '',
          target: item.checkpoint_id || item.id || '',
          label: `${Math.floor((new Date(item.timestamp).getTime() - new Date(prev.timestamp).getTime()) / 1000)}s`,
        };
      })
      .filter(Boolean);
    
    return { nodes, edges };
  }, [checkpointsQuery.data, timelineQuery.data]);
  
  // 특정 체크포인트 상세 조회
  const getDetail = useCallback(
    async (checkpointId: string) => {
      if (!executionId) throw new Error('executionId is required');
      return await queryClient.fetchQuery({
        queryKey: ['checkpoint', executionId, checkpointId],
        queryFn: () => getCheckpointDetail(executionId, checkpointId),
        staleTime: 5 * 60 * 1000, // 5분간 캐시
      });
    },
    [queryClient, executionId]
  );
  
  // 캐시 무효화
  const invalidate = useCallback(() => {
    if (executionId) {
      queryClient.invalidateQueries({ queryKey: ['checkpoints', executionId] });
      queryClient.invalidateQueries({ queryKey: ['timeline', executionId] });
    }
  }, [queryClient, executionId]);
  
  return {
    checkpoints: checkpointsQuery.data || [],
    timeline: timelineQuery.data || [],
    checkpointTree, // 트리 구조 (시각화용)
    isLoading: checkpointsQuery.isLoading || timelineQuery.isLoading,
    isError: checkpointsQuery.isError || timelineQuery.isError,
    error: checkpointsQuery.error || timelineQuery.error,
    getDetail,
    invalidate,
    refetch: () => {
      checkpointsQuery.refetch();
      timelineQuery.refetch();
    },
  };
}

// ============ Time Machine (Rollback) Hooks ============

interface UseTimeMachineOptions {
  executionId: string;
  onRollbackSuccess?: (result: BranchInfo) => void;
  onRollbackError?: (error: Error) => void;
}

export function useTimeMachine({ 
  executionId, 
  onRollbackSuccess, 
  onRollbackError 
}: UseTimeMachineOptions) {
  const optionsRef = useRef({ onRollbackSuccess, onRollbackError });
  const [preview, setPreview] = useState<RollbackPreview | null>(null);
  const [selectedCheckpointId, setSelectedCheckpointId] = useState<string | null>(null);
  const [compareCheckpointId, setCompareCheckpointId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  
  // Keep callbacks stable
  useEffect(() => {
    optionsRef.current = { onRollbackSuccess, onRollbackError };
  });
  
  // executionId 유효성 검사
  const validateExecutionId = useCallback((id?: string) => {
    const effectiveId = id || executionId;
    if (!effectiveId || effectiveId.trim() === '') {
      throw new Error('executionId is required for time machine operations');
    }
    return effectiveId;
  }, [executionId]);
  
  // 롤백 미리보기 (executionId를 인자로 받아 클로저 문제 방지)
  const previewMutation = useMutation({
    mutationFn: async ({ checkpointId, execId }: { checkpointId: string; execId?: string }) => {
      const validExecId = validateExecutionId(execId);
      const request: Omit<RollbackRequest, 'preview_only'> = {
        thread_id: validExecId,
        target_checkpoint_id: checkpointId,
        state_modifications: {},
      };
      return await previewRollback(request);
    },
    onSuccess: (data) => {
      setPreview(data);
    },
  });
  
  // 롤백 실행
  const rollbackMutation = useMutation({
    mutationFn: async ({ request, execId }: { request: Omit<RollbackRequest, 'preview_only'>; execId?: string }) => {
      const validExecId = validateExecutionId(execId);
      // 요청에 올바른 executionId 덮어쓰기
      return await executeRollback({ ...request, thread_id: validExecId });
    },
    onSuccess: (data) => {
      const validExecId = executionId; // 성공 후 캐시 무효화용
      if (validExecId) {
        queryClient.invalidateQueries({ queryKey: ['checkpoints', validExecId] });
        queryClient.invalidateQueries({ queryKey: ['timeline', validExecId] });
        queryClient.invalidateQueries({ queryKey: ['branches', validExecId] });
      }
      
      optionsRef.current.onRollbackSuccess?.(data);
      setPreview(null);
    },
    onError: (error: Error) => {
      optionsRef.current.onRollbackError?.(error);
    },
  });
  
  // 체크포인트 비교
  const compareMutation = useMutation({
    mutationFn: async ({ sourceId, targetId, execId }: { sourceId: string; targetId: string; execId?: string }) => {
      const validExecId = validateExecutionId(execId);
      return await compareCheckpoints(validExecId, sourceId, targetId);
    },
  });
  
  // 브랜치 히스토리
  const branchesQuery = useQuery({
    queryKey: ['branches', executionId],
    queryFn: () => {
      const validExecId = validateExecutionId();
      return getBranchHistory(validExecId);
    },
    enabled: !!executionId,
  });
  
  // 브랜치 트리 구조 (DAG 시각화용)
  const branchTree = useMemo(() => {
    const branches = branchesQuery.data?.branches || [];
    
    // 브랜치를 노드와 엣지로 변환
    const nodes = branches.map((branch: any) => ({
      id: branch.branch_id || branch.id || '',
      label: branch.name || branch.branch_id || branch.id || 'Unknown',
      checkpointId: branch.checkpoint_id,
      createdAt: branch.created_at,
      type: 'branch' as const,
    }));
    
    const edges = branches
      .filter((branch: any) => branch.parent_branch_id)
      .map((branch: any) => ({
        source: branch.parent_branch_id,
        target: branch.branch_id || branch.id || '',
        label: 'branch',
      }));
    
    return { nodes, edges };
  }, [branchesQuery.data]);
  
  // 롤백 제안
  const suggestionsQuery = useQuery({
    queryKey: ['rollback-suggestions', executionId],
    queryFn: async () => {
      const validExecId = validateExecutionId();
      const result = await getRollbackSuggestions(validExecId);
      return result.suggestions;
    },
    enabled: !!executionId,
    staleTime: 30 * 1000, // 30초 캐시
  });
  
  const loadPreview = useCallback(
    (checkpointId: string, execId?: string) => {
      setSelectedCheckpointId(checkpointId);
      return previewMutation.mutateAsync({ checkpointId, execId });
    },
    [previewMutation, validateExecutionId]
  );
  
  const executeRollbackAction = useCallback(
    (request: Omit<RollbackRequest, 'preview_only'>, execId?: string) => {
      return rollbackMutation.mutateAsync({ request, execId });
    },
    [rollbackMutation, validateExecutionId]
  );
  
  const compare = useCallback(
    (sourceId: string, targetId: string, execId?: string) => {
      setCompareCheckpointId(targetId);
      return compareMutation.mutateAsync({ sourceId, targetId, execId });
    },
    [compareMutation, validateExecutionId]
  );
  
  const clearPreview = useCallback(() => {
    setPreview(null);
    setSelectedCheckpointId(null);
  }, []);
  
  const clearCompare = useCallback(() => {
    setCompareCheckpointId(null);
  }, []);
  
  // 체크포인트 선택
  const selectCheckpoint = useCallback((checkpointId: string) => {
    setSelectedCheckpointId(checkpointId);
  }, []);
  
  // 비교 대상 선택
  const selectCompare = useCallback((checkpointId: string) => {
    setCompareCheckpointId(checkpointId);
  }, []);
  
  return {
    // 미리보기
    preview,
    selectedCheckpointId,
    isPreviewLoading: previewMutation.isPending,
    previewError: previewMutation.error,
    loadPreview,
    clearPreview,
    selectCheckpoint,
    
    // 롤백 실행
    isRollbackLoading: rollbackMutation.isPending,
    rollbackError: rollbackMutation.error,
    rollback: executeRollbackAction,  // 별칭 추가
    executeRollback: executeRollbackAction,
    
    // 비교
    compareCheckpointId,
    compareResult: compareMutation.data,
    isCompareLoading: compareMutation.isPending,
    compare,
    clearCompare,
    selectCompare,
    
    // 브랜치
    branches: branchesQuery.data?.branches || [],
    branchTree, // DAG 구조 (시각화용)
    isBranchesLoading: branchesQuery.isLoading,
    
    // 제안
    suggestions: suggestionsQuery.data || [],
    isSuggestionsLoading: suggestionsQuery.isLoading,
  };
}

// ============ Combined Hook for Workflow Execution ============

interface UseWorkflowBriefingAndCheckpointsOptions {
  workflowId: string;
  executionId?: string;
  autoRefetchCheckpoints?: boolean;
}

export function useWorkflowBriefingAndCheckpoints({
  workflowId,
  executionId,
  autoRefetchCheckpoints = false,
}: UseWorkflowBriefingAndCheckpointsOptions) {
  const briefing = usePlanBriefing();
  const checkpoints = useCheckpoints({
    executionId,
    enabled: !!executionId,
    refetchInterval: autoRefetchCheckpoints ? 5000 : false,
  });
  const timeMachine = useTimeMachine({
    executionId: executionId || '',
  });
  
  return {
    // Briefing
    briefing: briefing.briefing,
    generateBriefing: briefing.generate,
    isBriefingLoading: briefing.isLoading,
    
    // Checkpoints
    checkpoints: checkpoints.checkpoints,
    timeline: checkpoints.timeline,
    isCheckpointsLoading: checkpoints.isLoading,
    refetchCheckpoints: checkpoints.refetch,
    
    // Time Machine
    preview: timeMachine.preview,
    loadRollbackPreview: timeMachine.loadPreview,
    executeRollback: timeMachine.executeRollback,
    isRollbackLoading: timeMachine.isRollbackLoading,
    
    // Compare
    compareCheckpoints: timeMachine.compare,
    compareResult: timeMachine.compareResult,
    
    // Branches
    branches: timeMachine.branches,
  };
}

export default {
  usePlanBriefing,
  useCheckpoints,
  useTimeMachine,
  useWorkflowBriefingAndCheckpoints,
};
