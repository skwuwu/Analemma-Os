/**
 * useWorkflowRecovery: 워크플로우 생성 중단 시 복구 기능
 * 
 * Agentic Designer가 생성 도중 멈췄을 때 부분 롤백 또는 재시작 기능을 제공합니다.
 */
import { useCallback, useRef, useState } from 'react';
import { useWorkflowStore } from '@/lib/workflowStore';
import { useCodesignStore } from '@/lib/codesignStore';
import { toast } from 'sonner';

export interface WorkflowSnapshot {
  timestamp: number;
  nodes: any[];
  edges: any[];
  reason: string;
  sessionId?: string;
}

export interface RecoveryOptions {
  rollbackToSnapshot?: WorkflowSnapshot;
  resumeGeneration?: boolean;
  clearAndRestart?: boolean;
}

export function useWorkflowRecovery() {
  const [snapshots, setSnapshots] = useState<WorkflowSnapshot[]>([]);
  const [isRecovering, setIsRecovering] = useState(false);
  const generationStartRef = useRef<WorkflowSnapshot | null>(null);
  const lastValidStateRef = useRef<WorkflowSnapshot | null>(null);

  const { nodes, edges, clearWorkflow, loadWorkflow } = useWorkflowStore();
  const { addMessage, setSyncStatus } = useCodesignStore();

  /**
   * 워크플로우 스냅샷 생성
   */
  const createSnapshot = useCallback((reason: string, sessionId?: string): WorkflowSnapshot => {
    const snapshot: WorkflowSnapshot = {
      timestamp: Date.now(),
      nodes: JSON.parse(JSON.stringify(nodes)), // Deep copy
      edges: JSON.parse(JSON.stringify(edges)), // Deep copy
      reason,
      sessionId
    };

    setSnapshots(prev => [...prev.slice(-9), snapshot]); // 최근 10개 유지
    return snapshot;
  }, [nodes, edges]);

  /**
   * 생성 시작 시 호출 - 시작점 스냅샷 생성
   */
  const markGenerationStart = useCallback((sessionId?: string) => {
    const snapshot = createSnapshot('generation_start', sessionId);
    generationStartRef.current = snapshot;
    lastValidStateRef.current = snapshot;
    
    console.log('Workflow generation started, snapshot created:', snapshot.timestamp);
  }, [createSnapshot]);

  /**
   * 노드/엣지 추가 시 호출 - 중간 상태 저장
   */
  const markProgress = useCallback((reason: string = 'progress_update') => {
    if (generationStartRef.current) {
      const snapshot = createSnapshot(reason, generationStartRef.current.sessionId);
      lastValidStateRef.current = snapshot;
    }
  }, [createSnapshot]);

  /**
   * 생성 완료 시 호출
   */
  const markGenerationComplete = useCallback(() => {
    if (generationStartRef.current) {
      createSnapshot('generation_complete', generationStartRef.current.sessionId);
      generationStartRef.current = null;
    }
  }, [createSnapshot]);

  /**
   * 생성 중단 감지 및 복구 옵션 제공
   */
  const handleGenerationInterruption = useCallback((error?: Error) => {
    if (!generationStartRef.current) {
      return; // 생성 중이 아니었음
    }

    const errorMessage = error?.message || 'Unknown error';
    console.warn('Workflow generation interrupted:', errorMessage);

    // 현재 상태 스냅샷 생성
    const interruptedSnapshot = createSnapshot(`interrupted: ${errorMessage}`, generationStartRef.current.sessionId);

    // 복구 옵션 제공
    const recoveryOptions: RecoveryOptions = {
      rollbackToSnapshot: lastValidStateRef.current || generationStartRef.current,
      resumeGeneration: true,
      clearAndRestart: true
    };

    addMessage('system', `워크플로우 생성이 중단되었습니다: ${errorMessage}`);
    
    return {
      interruptedSnapshot,
      recoveryOptions,
      canRecover: true
    };
  }, [createSnapshot, addMessage]);

  /**
   * 부분 롤백 실행
   */
  const rollbackToSnapshot = useCallback(async (snapshot: WorkflowSnapshot) => {
    setIsRecovering(true);
    
    try {
      // 워크플로우 상태 복원
      clearWorkflow();
      
      // 약간의 지연 후 복원 (UI 업데이트를 위해)
      setTimeout(() => {
        loadWorkflow({
          name: `Recovered Workflow (${new Date(snapshot.timestamp).toLocaleTimeString()})`,
          nodes: snapshot.nodes,
          edges: snapshot.edges
        });
        
        addMessage('system', `워크플로우가 ${snapshot.reason} 시점으로 복원되었습니다.`);
        toast.success('워크플로우가 복원되었습니다.');
        
        setIsRecovering(false);
      }, 100);
      
    } catch (error) {
      console.error('Rollback failed:', error);
      addMessage('system', `복원 실패: ${error instanceof Error ? error.message : 'Unknown error'}`);
      toast.error('워크플로우 복원에 실패했습니다.');
      setIsRecovering(false);
    }
  }, [clearWorkflow, loadWorkflow, addMessage]);

  /**
   * 생성 재시작
   */
  const restartGeneration = useCallback(async (originalPrompt?: string) => {
    setIsRecovering(true);
    
    try {
      // Canvas 초기화
      clearWorkflow();
      
      // 새로운 생성 시작
      if (originalPrompt) {
        addMessage('user', `[재시작] ${originalPrompt}`);
        addMessage('system', '워크플로우 생성을 다시 시작합니다...');
      }
      
      toast.info('워크플로우 생성을 다시 시작합니다.');
      setIsRecovering(false);
      
    } catch (error) {
      console.error('Restart failed:', error);
      addMessage('system', `재시작 실패: ${error instanceof Error ? error.message : 'Unknown error'}`);
      toast.error('워크플로우 재시작에 실패했습니다.');
      setIsRecovering(false);
    }
  }, [clearWorkflow, addMessage]);

  /**
   * 생성 재개 (중단된 지점부터 계속)
   */
  const resumeGeneration = useCallback(async (originalPrompt?: string) => {
    if (!lastValidStateRef.current) {
      toast.error('재개할 수 있는 상태가 없습니다.');
      return;
    }

    setIsRecovering(true);
    setSyncStatus('syncing');
    
    try {
      const resumePrompt = originalPrompt 
        ? `이전에 "${originalPrompt}" 요청으로 워크플로우를 생성하다가 중단되었습니다. 현재 상태에서 이어서 완성해주세요.`
        : '중단된 워크플로우 생성을 이어서 완성해주세요.';
      
      addMessage('user', `[재개] ${resumePrompt}`);
      addMessage('system', '중단된 지점부터 워크플로우 생성을 재개합니다...');
      
      toast.info('워크플로우 생성을 재개합니다.');
      
      // 실제 API 호출은 WorkflowChat에서 처리
      return resumePrompt;
      
    } catch (error) {
      console.error('Resume failed:', error);
      addMessage('system', `재개 실패: ${error instanceof Error ? error.message : 'Unknown error'}`);
      toast.error('워크플로우 재개에 실패했습니다.');
    } finally {
      setIsRecovering(false);
      setSyncStatus('idle');
    }
  }, [addMessage, setSyncStatus]);

  /**
   * 스냅샷 목록 정리
   */
  const clearSnapshots = useCallback(() => {
    setSnapshots([]);
    generationStartRef.current = null;
    lastValidStateRef.current = null;
  }, []);

  /**
   * 현재 생성 중인지 확인
   */
  const isGenerating = useCallback(() => {
    return generationStartRef.current !== null;
  }, []);

  return {
    // 상태
    snapshots,
    isRecovering,
    isGenerating: isGenerating(),
    
    // 스냅샷 관리
    markGenerationStart,
    markProgress,
    markGenerationComplete,
    createSnapshot,
    
    // 복구 기능
    handleGenerationInterruption,
    rollbackToSnapshot,
    restartGeneration,
    resumeGeneration,
    
    // 유틸리티
    clearSnapshots,
  };
}

export default useWorkflowRecovery;