/**
 * useCanvasMode: Canvas 상태에 따른 모드 전환 훅 (개선된 버전)
 * 
 * Canvas가 비어있으면서 대화 기록이 없으면 Agentic Designer 모드,
 * Canvas가 비어있어도 대화 기록이 있으면 Co-design 모드 유지,
 * 내용이 있으면 Co-design 모드로 전환합니다.
 */
import { useMemo } from 'react';
import { useWorkflowStore } from '@/lib/workflowStore';
import { useCodesignStore } from '@/lib/codesignStore';
import { useShallow } from 'zustand/react/shallow';

export type CanvasMode = 'agentic-designer' | 'co-design';

export interface CanvasModeInfo {
  mode: CanvasMode;
  isEmpty: boolean;
  hasHistory: boolean;
  nodeCount: number;
  edgeCount: number;
  description: string;
  reason: string; // 모드 선택 이유
}

export function useCanvasMode(): CanvasModeInfo {
  const { nodes, edges } = useWorkflowStore(
    useShallow((state) => ({
      nodes: state.nodes,
      edges: state.edges,
    }))
  );

  const { recentChanges, messages } = useCodesignStore(
    useShallow((state) => ({
      recentChanges: state.recentChanges,
      messages: state.messages,
    }))
  );

  const modeInfo = useMemo((): CanvasModeInfo => {
    const nodeCount = nodes.length;
    const edgeCount = edges.length;
    const isEmpty = nodeCount === 0 && edgeCount === 0;
    const hasHistory = recentChanges.length > 0 || messages.length > 0;

    if (isEmpty && !hasHistory) {
      // 진짜 빈 Canvas - Agentic Designer 모드
      return {
        mode: 'agentic-designer',
        isEmpty: true,
        hasHistory: false,
        nodeCount: 0,
        edgeCount: 0,
        description: 'Canvas가 비어있습니다. AI가 워크플로우 초안을 빠르게 생성해드립니다.',
        reason: 'empty_canvas_no_history'
      };
    } else if (isEmpty && hasHistory) {
      // Canvas는 비어있지만 대화 기록 존재 - Co-design 모드 유지
      return {
        mode: 'co-design',
        isEmpty: true,
        hasHistory: true,
        nodeCount: 0,
        edgeCount: 0,
        description: 'Canvas가 비어있지만 대화 기록이 있습니다. 이전 맥락을 이어서 협업을 계속합니다.',
        reason: 'empty_canvas_with_history'
      };
    } else {
      // 기존 워크플로우 존재 - Co-design 모드
      return {
        mode: 'co-design',
        isEmpty: false,
        hasHistory: hasHistory,
        nodeCount,
        edgeCount,
        description: `기존 워크플로우를 AI와 함께 개선해보세요. (노드 ${nodeCount}개, 연결 ${edgeCount}개)`,
        reason: 'existing_workflow'
      };
    }
  }, [nodes.length, edges.length, recentChanges.length, messages.length]);

  return modeInfo;
}

export default useCanvasMode;