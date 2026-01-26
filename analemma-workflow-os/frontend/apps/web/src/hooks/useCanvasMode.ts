/**
 * useCanvasMode: Canvas 상태에 따른 모드 전환 훅 (v2.0)
 * ============================================================
 * 
 * Canvas가 비어있으면서 대화 기록이 없으면 Agentic Designer 모드,
 * Canvas가 비어있어도 대화 기록이 있으면 Co-design 모드 유지,
 * 내용이 있으면 Co-design 모드로 전환합니다.
 * 
 * v2.0 Changes:
 * - 순수 함수로 모드 결정 로직 분리 (테스트 용이성)
 * - Zustand 의존성 최적화 (length만 추출)
 * - isGenerating 상태 추가 (AI 생성 중 감지)
 * - 코드 가독성 및 유지보수성 개선
 */
import { useMemo } from 'react';
import { useWorkflowStore } from '@/lib/workflowStore';
import { useCodesignStore } from '@/lib/codesignStore';
import { useShallow } from 'zustand/react/shallow';

export type CanvasMode = 'agentic-designer' | 'co-design' | 'generating';

export interface CanvasModeInfo {
  mode: CanvasMode;
  isEmpty: boolean;
  hasHistory: boolean;
  isGenerating: boolean;
  nodeCount: number;
  edgeCount: number;
  description: string;
  reason: string; // 모드 선택 이유
}

/**
 * 순수 함수: 모드 결정 로직 (테스트 가능)
 */
export const deriveMode = (
  nodeCount: number,
  edgeCount: number,
  hasHistory: boolean,
  isGenerating: boolean = false
): CanvasModeInfo => {
  const isEmpty = nodeCount === 0 && edgeCount === 0;

  // AI 생성 중 상태 (우선순위 높음)
  if (isGenerating) {
    return {
      mode: 'generating',
      isEmpty,
      hasHistory,
      isGenerating: true,
      nodeCount,
      edgeCount,
      description: 'AI가 워크플로우를 생성하고 있습니다...',
      reason: 'ai_generating',
    };
  }

  // 진짜 빈 Canvas - Agentic Designer 모드
  if (isEmpty && !hasHistory) {
    return {
      mode: 'agentic-designer',
      isEmpty: true,
      hasHistory: false,
      isGenerating: false,
      nodeCount: 0,
      edgeCount: 0,
      description: 'Canvas가 비어있습니다. AI가 워크플로우 초안을 빠르게 생성해드립니다.',
      reason: 'initial_state',
    };
  }

  // Co-design 모드 (Canvas 비어있어도 히스토리 있거나, 워크플로우 있음)
  return {
    mode: 'co-design',
    isEmpty,
    hasHistory,
    isGenerating: false,
    nodeCount,
    edgeCount,
    description: isEmpty
      ? '이전 맥락을 이어서 협업을 계속합니다.'
      : `기존 워크플로우를 AI와 함께 개선해보세요. (노드 ${nodeCount}개, 연결 ${edgeCount}개)`,
    reason: isEmpty ? 'context_preserved' : 'active_workflow',
  };
};

export function useCanvasMode(): CanvasModeInfo {
  // Zustand 최적화: length만 추출하여 불필요한 리렌더링 방지
  const { nodeCount, edgeCount } = useWorkflowStore(
    useShallow((state) => ({
      nodeCount: state.nodes.length,
      edgeCount: state.edges.length,
    }))
  );

  const { hasHistory, isGenerating } = useCodesignStore(
    useShallow((state) => ({
      hasHistory: state.recentChanges.length > 0 || state.messages.length > 0,
      // isGenerating이 없으면 false (향후 추가 가능)
      isGenerating: (state as any).isGenerating || false,
    }))
  );

  return useMemo(
    () => deriveMode(nodeCount, edgeCount, hasHistory, isGenerating),
    [nodeCount, edgeCount, hasHistory, isGenerating]
  );
}

export default useCanvasMode;