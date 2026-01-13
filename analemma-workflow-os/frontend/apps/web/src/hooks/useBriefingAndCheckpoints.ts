/**
 * Plan Briefing & Checkpoint Hooks
 * 
 * Plan Briefing과 Checkpoint 기능을 위한 React Query 훅입니다.
 */

import { useState, useCallback, useRef } from 'react';
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
  const [briefing, setBriefing] = useState<PlanBriefing | null>(null);
  const queryClient = useQueryClient();
  
  const generateMutation = useMutation({
    mutationFn: async (request: PreviewWorkflowRequest) => {
      return await generateWorkflowPreview(request);
    },
    onSuccess: (data) => {
      setBriefing(data);
      options.onSuccess?.(data);
    },
    onError: (error: Error) => {
      options.onError?.(error);
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
    setBriefing(null);
  }, []);
  
  return {
    briefing,
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
      const result = await listCheckpoints(executionId!);
      return result.checkpoints;
    },
    enabled: enabled && !!executionId,
    refetchInterval,
  });
  
  // 타임라인 조회
  const timelineQuery = useQuery({
    queryKey: ['timeline', executionId],
    queryFn: async () => {
      const result = await getExecutionTimeline(executionId!);
      return result.timeline;
    },
    enabled: enabled && !!executionId,
    refetchInterval,
  });
  
  // 특정 체크포인트 상세 조회
  const getDetail = useCallback(
    async (checkpointId: string) => {
      return await queryClient.fetchQuery({
        queryKey: ['checkpoint', executionId, checkpointId],
        queryFn: () => getCheckpointDetail(executionId!, checkpointId),
        staleTime: 5 * 60 * 1000, // 5분간 캐시
      });
    },
    [queryClient, executionId]
  );
  
  // 캐시 무효화
  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['checkpoints', executionId] });
    queryClient.invalidateQueries({ queryKey: ['timeline', executionId] });
  }, [queryClient, executionId]);
  
  return {
    checkpoints: checkpointsQuery.data || [],
    timeline: timelineQuery.data || [],
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
  const [preview, setPreview] = useState<RollbackPreview | null>(null);
  const [selectedCheckpointId, setSelectedCheckpointId] = useState<string | null>(null);
  const [compareCheckpointId, setCompareCheckpointId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const executionIdRef = useRef(executionId);
  
  // executionId 업데이트
  executionIdRef.current = executionId;
  
  // 롤백 미리보기
  const previewMutation = useMutation({
    mutationFn: async (checkpointId: string) => {
      const currentExecutionId = executionIdRef.current;
      if (!currentExecutionId || currentExecutionId.trim() === '') {
        throw new Error('executionId is required for rollback preview');
      }
      const request: Omit<RollbackRequest, 'preview_only'> = {
        thread_id: currentExecutionId,
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
    mutationFn: async (request: Omit<RollbackRequest, 'preview_only'>) => {
      return await executeRollback(request);
    },
    onSuccess: (data) => {
      // 캐시 무효화
      queryClient.invalidateQueries({ queryKey: ['checkpoints', executionId] });
      queryClient.invalidateQueries({ queryKey: ['timeline', executionId] });
      queryClient.invalidateQueries({ queryKey: ['branches', executionId] });
      
      onRollbackSuccess?.(data);
      setPreview(null);
    },
    onError: (error: Error) => {
      onRollbackError?.(error);
    },
  });
  
  // 체크포인트 비교
  const compareMutation = useMutation({
    mutationFn: async ({ sourceId, targetId }: { sourceId: string; targetId: string }) => {
      return await compareCheckpoints(executionId, sourceId, targetId);
    },
  });
  
  // 브랜치 히스토리
  const branchesQuery = useQuery({
    queryKey: ['branches', executionId],
    queryFn: () => getBranchHistory(executionId),
    enabled: !!executionId,
  });
  
  // 롤백 제안
  const suggestionsQuery = useQuery({
    queryKey: ['rollback-suggestions', executionId],
    queryFn: async () => {
      const result = await getRollbackSuggestions(executionId);
      return result.suggestions;
    },
    enabled: !!executionId,
    staleTime: 30 * 1000, // 30초 캐시
  });
  
  const loadPreview = useCallback(
    (checkpointId: string) => {
      setSelectedCheckpointId(checkpointId);
      return previewMutation.mutateAsync(checkpointId);
    },
    [previewMutation]
  );
  
  const executeRollbackAction = useCallback(
    (request: Omit<RollbackRequest, 'preview_only'>) => {
      return rollbackMutation.mutateAsync(request);
    },
    [rollbackMutation]
  );
  
  const compare = useCallback(
    (sourceId: string, targetId: string) => {
      setCompareCheckpointId(targetId);
      return compareMutation.mutateAsync({ sourceId, targetId });
    },
    [compareMutation]
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
