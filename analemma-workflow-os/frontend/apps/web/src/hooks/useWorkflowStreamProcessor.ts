import { useCallback, useRef } from 'react';
import { convertWorkflowFromBackendFormat } from '@/lib/workflowConverter';
import { useWorkflowStore } from '@/lib/workflowStore';
import { useCodesignStore } from '@/lib/codesignStore';

// 스트리밍 메시지 타입 정의
interface WorkflowMessage {
  name?: string;
  nodes?: unknown[];
  edges?: unknown[];
  response?: {
    tool_use?: {
      name?: string;
      nodes?: unknown[];
      edges?: unknown[];
      input?: {
        workflow_json?: {
          name?: string;
          nodes?: unknown[];
          edges?: unknown[];
        };
      };
    };
    text?: string;
  };
  op?: string;
  type?: string;
  id?: string;
  data?: Record<string, unknown>;
  changes?: Record<string, unknown>;
  thought?: string; // AI 사고 과정
}

// Type Guard 함수들
const isWorkflowMessage = (obj: unknown): obj is WorkflowMessage => {
  return obj !== null && typeof obj === 'object';
};

const hasNodesAndEdges = (msg: WorkflowMessage): boolean => {
  return Array.isArray(msg.nodes) && Array.isArray(msg.edges);
};

const hasToolUse = (msg: WorkflowMessage): boolean => {
  return msg.response !== undefined &&
    typeof msg.response === 'object' &&
    msg.response.tool_use !== undefined;
};

const hasOpAndType = (msg: WorkflowMessage): boolean => {
  return typeof msg.op === 'string' && typeof msg.type === 'string';
};

interface UseWorkflowStreamProcessorProps {
  onWorkflowUpdate?: (workflow: any) => void;
  onMessage?: (message: { role: 'user' | 'assistant' | 'thought'; content: string }) => void;
  onLog?: (log: string) => void;
}

export const useWorkflowStreamProcessor = ({
  onWorkflowUpdate,
  onMessage,
  onLog
}: UseWorkflowStreamProcessorProps) => {
  // Co-design store access
  const { recordChange, addMessage } = useCodesignStore();
  const idCounterRef = useRef<number>(0);

  const generateId = useCallback(() => {
    const counter = idCounterRef.current = (idCounterRef.current || 0) + 1;
    const timestamp = Date.now();

    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return `${crypto.randomUUID()}-${counter}`;
    }
    return `fallback-${timestamp}-${counter}-${Math.random().toString(36).substr(2, 9)}`;
  }, []);

  const addDebugLog = useCallback((message: string) => {
    onLog?.(message);
  }, [onLog]);

  /**
   * DRY: 공통 워크플로우 로드 로직
   */
  const loadWorkflowToCanvas = useCallback((workflowData: {
    name?: string;
    nodes: unknown[];
    edges: unknown[];
  }, context: string): boolean => {
    const nodeCount = workflowData.nodes.length;
    const edgeCount = workflowData.edges.length;
    const workflowName = workflowData.name || 'Generated Workflow';

    addDebugLog(`${context}: ${workflowName} (${nodeCount} nodes, ${edgeCount} edges)`);

    try {
      const frontendWorkflow = convertWorkflowFromBackendFormat(workflowData);
      const { loadWorkflow } = useWorkflowStore.getState();
      loadWorkflow?.(frontendWorkflow);

      // Record as major change for AI context
      recordChange('add_node', {
        summary: `Imported workflow: ${workflowName}`,
        nodeCount,
        edgeCount
      });

      if (onWorkflowUpdate) {
        onWorkflowUpdate({
          ...frontendWorkflow,
          name: workflowName,
          id: generateId()
        });
      }

      onMessage?.({
        role: 'assistant',
        content: `✨ Created workflow: "${workflowName}" with ${nodeCount} nodes`
      });

      return true;
    } catch (e) {
      console.error(`Failed to load ${context} workflow:`, e);
      addDebugLog(`error: failed to load ${context} workflow - ${(e as Error).message}`);
      onMessage?.({
        role: 'assistant',
        content: 'Generated workflow structure, but failed to render on canvas.'
      });
      return true;
    }
  }, [onWorkflowUpdate, onMessage, addDebugLog, generateId, recordChange]);

  // --- 타입 가드: Node/Edge 데이터 간단 검증 ---
  interface NodeData { id?: string; type?: string;[key: string]: any }
  interface EdgeData { id?: string; source?: string; target?: string;[key: string]: any }

  const isNodeData = (obj: unknown): obj is NodeData => {
    return obj !== null && typeof obj === 'object' && ('id' in (obj as any) || 'type' in (obj as any));
  };

  const isEdgeData = (obj: unknown): obj is EdgeData => {
    return obj !== null && typeof obj === 'object' && ('source' in (obj as any) || 'target' in (obj as any) || 'id' in (obj as any));
  };

  // Node operation handler with tracking
  const handleNodeOperation = useCallback((op: string, message: WorkflowMessage) => {
    const id = message.id;
    const data = message.data || {};
    const { addNode, updateNode, removeNode, nodes } = useWorkflowStore.getState();

    if (op === 'add' && message.data) {
      const nodeData = message.data;
      if (!isNodeData(nodeData)) return true;

      // Duplication check
      if (nodes.some(n => n.id === nodeData.id)) {
        addDebugLog(`skip: node ${nodeData.id} already exists`);
        return true;
      }

      addNode?.(nodeData as any);
      recordChange('add_node', { id: nodeData.id, data: nodeData, source: 'ai' });
      onMessage?.({ role: 'thought', content: `➕ Mapping node: ${nodeData.label || nodeData.id}` });
    } else if (op === 'update' && id) {
      const changes = message.changes || message.data || {};
      if (typeof changes !== 'object' || changes === null) return true;

      updateNode?.(id, changes as any);
      recordChange('update_node', { id, changes, source: 'ai' });
    } else if (op === 'remove' && id) {
      removeNode?.(id);
      recordChange('delete_node', { id, source: 'ai' });
    }
    return true;
  }, [onMessage, addDebugLog, recordChange]);

  // Edge operation handler with tracking
  const handleEdgeOperation = useCallback((op: string, message: WorkflowMessage) => {
    const id = message.id;
    const { addEdge, updateEdge, removeEdge, edges } = useWorkflowStore.getState();

    if (op === 'add' && message.data) {
      const edgeData = message.data;
      if (!isEdgeData(edgeData)) return true;

      // Duplication check
      if (edges.some(e => e.id === edgeData.id)) return true;

      addEdge?.(edgeData as any);
      recordChange('add_edge', { id: edgeData.id, data: edgeData, source: 'ai' });
    } else if (op === 'update' && id) {
      const changes = message.changes || message.data || {};
      updateEdge?.(id, changes as any);
      recordChange('update_node', { id, changes, source: 'ai' }); // Use recordChange generic
    } else if (op === 'remove' && id) {
      removeEdge?.(id);
      recordChange('delete_edge', { id, source: 'ai' });
    }
    return true;
  }, [recordChange]);

  // Status message handler
  const handleStatusMessage = useCallback((message: WorkflowMessage) => {
    const statusData = typeof message.data === 'string' ? message.data : String(message.data || 'unknown');
    addDebugLog(`status: ${statusData}`);

    if (statusData === 'done') {
      onMessage?.({ role: 'assistant', content: '✅ Optimization complete. The workflow is ready for review.' });
    }
    return true;
  }, [addDebugLog, onMessage]);

  // Main processing function
  const processStreamingChunk = useCallback((obj: unknown) => {
    if (!isWorkflowMessage(obj)) return false;
    const message = obj;

    // 1. Thought Process logging (Always higher priority)
    if (message.thought) {
      onMessage?.({ role: 'thought', content: message.thought });
    }

    // 2. Structural updates
    if (hasNodesAndEdges(message)) {
      return loadWorkflowToCanvas({
        name: message.name,
        nodes: message.nodes || [],
        edges: message.edges || []
      }, 'direct_workflow');
    }

    if (hasToolUse(message)) {
      const toolUse = message.response!.tool_use!;
      if (hasNodesAndEdges(toolUse)) {
        return loadWorkflowToCanvas({ name: toolUse.name, nodes: toolUse.nodes!, edges: toolUse.edges! }, 'tool_use');
      }
      const wf = toolUse.input?.workflow_json;
      if (wf && hasNodesAndEdges(wf)) {
        return loadWorkflowToCanvas({ name: wf.name, nodes: wf.nodes!, edges: wf.edges! }, 'legacy_workflow');
      }
    }

    // 3. Incremental updates (The "AI Designer" loop)
    if (hasOpAndType(message)) {
      const type = message.type!;
      if (type === 'node') return handleNodeOperation(message.op!, message);
      if (type === 'edge') return handleEdgeOperation(message.op!, message);
    }

    // 4. Fallbacks & metadata
    if (message.type === 'status') return handleStatusMessage(message);

    if (message.type === 'text' && message.data) {
      onMessage?.({ role: 'assistant', content: String(message.data) });
      return true;
    }

    if (message.response?.text) {
      onMessage?.({ role: 'assistant', content: message.response.text });
      return true;
    }

    return false;
  }, [
    loadWorkflowToCanvas,
    handleNodeOperation,
    handleEdgeOperation,
    handleStatusMessage,
    onMessage
  ]);

  return {
    processStreamingChunk
  };
};
