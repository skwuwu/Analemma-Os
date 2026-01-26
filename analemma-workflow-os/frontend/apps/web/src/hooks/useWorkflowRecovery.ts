import { useCallback, useRef, useState } from 'react';
import { useWorkflowStore } from '@/lib/workflowStore';
import { useCodesignStore } from '@/lib/codesignStore';
import { toast } from 'sonner';
import { makeAuthenticatedRequest, parseApiResponse } from '@/lib/api';

const API_BASE = import.meta.env.VITE_API_BASE_URL;

// 로컬 임시 스냅샷 (네트워크 끊김 시에만 사용)
export interface LocalSnapshot {
  timestamp: number;
  nodes: any[];
  edges: any[];
  reason: 'local_edit' | 'network_offline';
  isTemporary: true;
}

// 백엔드 체크포인트 정보
export interface CheckpointInfo {
  checkpoint_id: string;
  thread_id: string;
  execution_id: string;
  created_at: string;
  node_id: string;
  event_type: string;
  status: string;
  message?: string;
  is_important: boolean;
}

export interface RecoveryOptions {
  restoreFromCheckpoint?: CheckpointInfo;
  rollbackToLocal?: LocalSnapshot;
  clearAndRestart?: boolean;
}

export function useWorkflowRecovery() {
  const [localSnapshots, setLocalSnapshots] = useState<LocalSnapshot[]>([]);
  const [isRecovering, setIsRecovering] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [currentExecutionId, setCurrentExecutionId] = useState<string | null>(null);
  const [generationProgress, setGenerationProgress] = useState<string[]>([]);
  const lastLocalEditRef = useRef<LocalSnapshot | null>(null);

  const { nodes, edges, clearWorkflow, loadWorkflow } = useWorkflowStore();
  const { addMessage, setSyncStatus } = useCodesignStore();

  const createLocalSnapshot = useCallback((reason: 'local_edit' | 'network_offline'): LocalSnapshot => {
    const snapshot: LocalSnapshot = {
      timestamp: Date.now(),
      nodes: typeof structuredClone !== 'undefined' ? structuredClone(nodes) : JSON.parse(JSON.stringify(nodes)),
      edges: typeof structuredClone !== 'undefined' ? structuredClone(edges) : JSON.parse(JSON.stringify(edges)),
      reason,
      isTemporary: true
    };
    setLocalSnapshots(prev => [...prev.slice(-4), snapshot]);
    lastLocalEditRef.current = snapshot;
    return snapshot;
  }, [nodes, edges]);

  const markGenerationStart = useCallback((executionId: string) => {
    setCurrentExecutionId(executionId);
    setIsGenerating(true);
    setGenerationProgress([]);
  }, []);

  const markGenerationComplete = useCallback(() => {
    setIsGenerating(false);
  }, []);

  const markProgress = useCallback((step: string) => {
    setGenerationProgress(prev => [...prev, step]);
    createLocalSnapshot('local_edit');
  }, [createLocalSnapshot]);

  const fetchCheckpoints = useCallback(async (executionId: string): Promise<CheckpointInfo[]> => {
    try {
      const response = await makeAuthenticatedRequest(
        `${API_BASE}/executions/${encodeURIComponent(executionId)}/checkpoints?only_important=true`
      );
      const data = await parseApiResponse<{ checkpoints: CheckpointInfo[] }>(response);
      return data.checkpoints || [];
    } catch (error) {
      console.error('Failed to fetch checkpoints:', error);
      toast.error('체크포인트 목록을 불러올 수 없습니다.');
      return [];
    }
  }, []);

  const markExecutionStart = useCallback((executionId: string) => {
    setCurrentExecutionId(executionId);
  }, []);

  const handleGenerationInterruption = useCallback(async (error?: Error) => {
    if (!currentExecutionId) {
      toast.error('실행 ID를 찾을 수 없습니다.');
      return null;
    }
    const checkpoints = await fetchCheckpoints(currentExecutionId);
    if (checkpoints.length === 0) {
      toast.warning('복구 가능한 체크포인트가 없습니다. 처음부터 다시 시작해주세요.');
      return { canRecover: false, recoveryOptions: { clearAndRestart: true } };
    }
    const lastSuccessfulCheckpoint = checkpoints
      .filter(cp => cp.status === 'completed')
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
    addMessage('system', `워크플로우 생성이 중단되었습니다. 복구 가능한 체크포인트: ${checkpoints.length}개`);
    return {
      canRecover: true,
      checkpoints,
      lastSuccessfulCheckpoint,
      recoveryOptions: { restoreFromCheckpoint: lastSuccessfulCheckpoint, clearAndRestart: true }
    };
  }, [currentExecutionId, fetchCheckpoints, addMessage]);

  const restoreFromCheckpoint = useCallback(async (checkpoint: CheckpointInfo) => {
    setIsRecovering(true);
    try {
      const response = await makeAuthenticatedRequest(
        `${API_BASE}/executions/${encodeURIComponent(checkpoint.execution_id)}/restore`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ checkpoint_id: checkpoint.checkpoint_id })
        }
      );
      const result = await parseApiResponse<{ new_execution_id: string; restored_state: any; message: string; }>(response);
      setCurrentExecutionId(result.new_execution_id);
      if (result.restored_state?.workflow_config) {
        const config = result.restored_state.workflow_config;
        await new Promise(resolve => requestAnimationFrame(resolve));
        loadWorkflow({ nodes: config.nodes || [], edges: config.edges || [] });
      }
      addMessage('system', `체크포인트로부터 복원되었습니다.`);
      toast.success('워크플로우가 복원되었습니다.');
      return result.new_execution_id;
    } catch (error) {
      console.error('Checkpoint restore failed:', error);
      toast.error('워크플로우 복원에 실패했습니다.');
      return null;
    } finally {
      setIsRecovering(false);
    }
  }, [loadWorkflow, addMessage]);

  const rollbackToLocalSnapshot = useCallback(async (snapshot: LocalSnapshot) => {
    setIsRecovering(true);
    try {
      clearWorkflow();
      await new Promise(resolve => requestAnimationFrame(resolve));
      loadWorkflow({ nodes: snapshot.nodes, edges: snapshot.edges });
      addMessage('system', `로컬 백업으로부터 복원되었습니다.`);
      toast.info('로컬 백업이 복원되었습니다.');
    } catch (error) {
      console.error('Local rollback failed:', error);
      toast.error('로컬 복원에 실패했습니다.');
    } finally {
      setIsRecovering(false);
    }
  }, [clearWorkflow, loadWorkflow, addMessage]);

  const restartGeneration = useCallback(async (originalPrompt?: string) => {
    setIsRecovering(true);
    try {
      clearWorkflow();
      setCurrentExecutionId(null);
      if (originalPrompt) {
        addMessage('user', `[재시작] ${originalPrompt}`);
        addMessage('system', '워크플로우 생성을 다시 시작합니다...');
      }
      toast.info('워크플로우 생성을 다시 시작합니다.');
    } catch (error) {
      console.error('Restart failed:', error);
      toast.error('워크플로우 재시작에 실패했습니다.');
    } finally {
      setIsRecovering(false);
    }
  }, [clearWorkflow, addMessage]);

  const resumeGeneration = useCallback(async (checkpoint?: CheckpointInfo, originalPrompt?: string) => {
    if (!checkpoint && !currentExecutionId) {
      toast.error('재개할 수 있는 체크포인트가 없습니다.');
      return null;
    }
    setIsRecovering(true);
    setSyncStatus('syncing');
    try {
      let targetCheckpoint = checkpoint;
      if (!targetCheckpoint && currentExecutionId) {
        const checkpoints = await fetchCheckpoints(currentExecutionId);
        targetCheckpoint = checkpoints[0];
      }
      if (!targetCheckpoint) throw new Error('No checkpoint found');
      const newExecutionId = await restoreFromCheckpoint(targetCheckpoint);
      if (newExecutionId) {
        const resumePrompt = originalPrompt
          ? `체크포인트 ${targetCheckpoint.node_id}에서 이어서 완성해주세요.`
          : `체크포인트 ${targetCheckpoint.node_id}에서 워크플로우 생성을 재개합니다.`;
        addMessage('user', `[재개] ${resumePrompt}`);
        toast.info('워크플로우 생성을 재개합니다.');
        return resumePrompt;
      }
      return null;
    } catch (error) {
      console.error('Resume failed:', error);
      toast.error('워크플로우 재개에 실패했습니다.');
      return null;
    } finally {
      setIsRecovering(false);
      setSyncStatus('idle');
    }
  }, [currentExecutionId, fetchCheckpoints, restoreFromCheckpoint, addMessage, setSyncStatus]);

  const clearLocalSnapshots = useCallback(() => {
    setLocalSnapshots([]);
    lastLocalEditRef.current = null;
  }, []);

  return {
    localSnapshots,
    isRecovering,
    isGenerating,
    currentExecutionId,
    generationProgress,
    markGenerationStart,
    markGenerationComplete,
    markProgress,
    fetchCheckpoints,
    markExecutionStart,
    createLocalSnapshot,
    handleGenerationInterruption,
    restoreFromCheckpoint,
    rollbackToLocalSnapshot,
    restartGeneration,
    resumeGeneration,
    clearLocalSnapshots,
  };
}

export default useWorkflowRecovery;