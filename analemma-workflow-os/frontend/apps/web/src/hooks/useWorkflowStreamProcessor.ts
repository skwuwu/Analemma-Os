import { useCallback, useRef } from 'react';
import { convertWorkflowFromBackendFormat } from '@/lib/workflowConverter';
import { useWorkflowStore } from '@/lib/workflowStore';

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
  onMessage?: (message: { role: 'user' | 'assistant'; content: string }) => void;
  onLog?: (log: string) => void;
}

export const useWorkflowStreamProcessor = ({
  onWorkflowUpdate,
  onMessage,
  onLog
}: UseWorkflowStreamProcessorProps) => {
  const generateId = useCallback(() => {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    // 추가 충돌 방지를 위한 내부 카운터 포함
    const counter = idCounterRef.current = (idCounterRef.current || 0) + 1;
    return `fallback-${Date.now()}-${counter}-${Math.random().toString(36).substr(2, 9)}`;
  }, []);

  // 내부 카운터 (crypto.randomUUID 미지원 환경에서 ID 충돌 방지)
  const idCounterRef = useRef<number>(0);

  // Helper function to add debug logs with operation summaries
  const addDebugLog = useCallback((message: string) => {
    onLog?.(message);
  }, [onLog]);

  // Direct workflow handler
  const handleDirectWorkflow = useCallback((message: WorkflowMessage) => {
    const nodeCount = Array.isArray(message.nodes) ? message.nodes.length : 0;
    const edgeCount = Array.isArray(message.edges) ? message.edges.length : 0;
    addDebugLog(`direct_workflow: ${message.name || 'unnamed'} (${nodeCount} nodes, ${edgeCount} edges)`);

    try {
      const workflowData = {
        name: message.name || 'Generated Workflow',
        nodes: message.nodes,
        edges: message.edges
      };

      const frontendWorkflow = convertWorkflowFromBackendFormat(workflowData);
      const { loadWorkflow } = useWorkflowStore.getState();
      loadWorkflow?.(frontendWorkflow);

      if (onWorkflowUpdate) {
        onWorkflowUpdate({
          ...frontendWorkflow,
          name: message.name || 'Generated Workflow',
          id: generateId()
        });
      }

      onMessage?.({
        role: 'assistant',
        content: `✨ Created workflow: "${message.name || 'Generated Workflow'}" with ${nodeCount} nodes`
      });
      return true;
    } catch (e) {
      console.error('Failed to load direct workflow:', e);
      addDebugLog(`error: failed to load direct workflow - ${(e as Error).message}`);
      onMessage?.({
        role: 'assistant',
        content: 'Generated workflow structure, but failed to render on canvas. Check logs for details.'
      });
      return true;
    }
  }, [onWorkflowUpdate, onMessage, addDebugLog, generateId]);

  // --- 타입 가드: Node/Edge 데이터 간단 검증 ---
  interface NodeData { id?: string; type?: string; [key: string]: any }
  interface EdgeData { id?: string; source?: string; target?: string; [key: string]: any }

  const isNodeData = (obj: unknown): obj is NodeData => {
    return obj !== null && typeof obj === 'object' && ('id' in (obj as any) || 'type' in (obj as any));
  };

  const isEdgeData = (obj: unknown): obj is EdgeData => {
    return obj !== null && typeof obj === 'object' && ('source' in (obj as any) || 'target' in (obj as any) || 'id' in (obj as any));
  };

  // Tool use response handler
  const handleToolUseResponse = useCallback((message: WorkflowMessage) => {
    if (!message.response?.tool_use) return false;

    const toolUse = message.response.tool_use;
    const nodeCount = Array.isArray(toolUse.nodes) ? toolUse.nodes.length : 0;
    const edgeCount = Array.isArray(toolUse.edges) ? toolUse.edges.length : 0;

    addDebugLog(`tool_use: ${toolUse.name || 'unnamed'} (${nodeCount} nodes, ${edgeCount} edges)`);

    // Check for direct nodes/edges in tool_use (OpenAPI spec format)
    if (hasNodesAndEdges(toolUse)) {
      return handleToolUseWorkflow(toolUse, message);
    }

    // Legacy support: check for nested workflow_json
    const input = toolUse.input;
    const wf = input?.workflow_json;
    if (wf && hasNodesAndEdges(wf)) {
      return handleLegacyWorkflowJson(wf, toolUse, message);
    }

    // If tool_use exists but no workflow data, show text response
    if (message.response.text) {
      const text = message.response.text;
      addDebugLog(`text_response: ${text.slice(0, 100)}...`);
      onMessage?.({ role: 'assistant', content: text });
      return true;
    }

    return false;
  }, [addDebugLog, onMessage]);

  // Tool use workflow handler
  const handleToolUseWorkflow = useCallback((toolUse: any, message: WorkflowMessage) => {
    try {
      const workflowData = {
        name: toolUse.name || 'Generated Workflow',
        nodes: toolUse.nodes,
        edges: toolUse.edges
      };

      const frontendWorkflow = convertWorkflowFromBackendFormat(workflowData);
      const { loadWorkflow } = useWorkflowStore.getState();
      loadWorkflow?.(frontendWorkflow);

      if (onWorkflowUpdate) {
        onWorkflowUpdate({
          ...frontendWorkflow,
          name: toolUse.name || 'Generated Workflow',
          id: generateId()
        });
      }

      const nodeCount = Array.isArray(toolUse.nodes) ? toolUse.nodes.length : 0;
      onMessage?.({
        role: 'assistant',
        content: `✨ Created workflow: "${toolUse.name || 'Generated Workflow'}" with ${nodeCount} nodes`
      });
      return true;
    } catch (e) {
      console.error('Failed to load workflow from tool_use:', e);
      addDebugLog(`error: failed to load tool_use workflow - ${(e as Error).message}`);
      onMessage?.({
        role: 'assistant',
        content: 'Generated workflow structure, but failed to render on canvas. Check logs for details.'
      });
      return true;
    }
  }, [onWorkflowUpdate, onMessage, addDebugLog, generateId]);

  // Legacy workflow JSON handler
  const handleLegacyWorkflowJson = useCallback((wf: any, toolUse: any, message: WorkflowMessage) => {
    try {
      const nodeCount = Array.isArray(wf.nodes) ? wf.nodes.length : 0;
      const edgeCount = Array.isArray(wf.edges) ? wf.edges.length : 0;
      addDebugLog(`legacy_workflow: ${wf.name || 'unnamed'} (${nodeCount} nodes, ${edgeCount} edges)`);

      const frontendWorkflow = convertWorkflowFromBackendFormat(wf);
      const { loadWorkflow } = useWorkflowStore.getState();
      loadWorkflow?.(frontendWorkflow);

      if (onWorkflowUpdate) {
        onWorkflowUpdate({
          ...frontendWorkflow,
          name: toolUse.name || wf.name || 'Generated Workflow',
          id: generateId()
        });
      }

      onMessage?.({
        role: 'assistant',
        content: toolUse.name ? `Workflow generated: ${toolUse.name}` : '워크플로우가 캔버스에 로드되었습니다.'
      });
      return true;
    } catch (e) {
      console.error('Failed to load legacy workflow format:', e);
      addDebugLog(`error: failed to load legacy workflow - ${(e as Error).message}`);
      return true;
    }
  }, [onWorkflowUpdate, onMessage, addDebugLog, generateId]);

  // Op-based update handler
  const handleOpBasedUpdate = useCallback((message: WorkflowMessage) => {
    const op = message.op!;
    const type = message.type!;
    const id = message.id;
    const data = message.data || {};

    addDebugLog(`op: ${op} ${type} ${id || data.id || ''}`);

    try {
      if (type === 'node') {
        return handleNodeOperation(op, message);
      } else if (type === 'edge') {
        return handleEdgeOperation(op, message);
      }
      return false;
    } catch (e) {
      console.error(`Failed to ${op} ${type}:`, e);
      addDebugLog(`error: ${op} ${type} failed - ${(e as Error).message}`);
      onMessage?.({ role: 'assistant', content: `Failed to ${op} ${type}. Check logs for details.` });
      return true;
    }
  }, [addDebugLog, onMessage]);

  // Node operation handler
  const handleNodeOperation = useCallback((op: string, message: WorkflowMessage) => {
    const id = message.id;
    const data = message.data || {};
    if (op === 'add' && message.data) {
      const nodeData = message.data;
      if (!isNodeData(nodeData)) {
        addDebugLog(`invalid node data for add: ${JSON.stringify(nodeData).slice(0,200)}`);
      } else {
        const { addNode } = useWorkflowStore.getState();
        addNode?.(nodeData as any);
        const nodeId = (nodeData.id as string) || 'new node';
        onMessage?.({ role: 'assistant', content: `➕ Added node: ${nodeId}` });
      }
    } else if (op === 'update' && id) {
      const changes = message.changes || message.data || {};
      if (typeof changes !== 'object' || changes === null) {
        addDebugLog(`invalid node changes for update: ${String(changes).slice(0,200)}`);
      } else {
        const { updateNode } = useWorkflowStore.getState();
        updateNode?.(id, changes as any);
        onMessage?.({ role: 'assistant', content: `✏️ Updated node: ${id}` });
      }
    } else if (op === 'remove' && id) {
      const { removeNode } = useWorkflowStore.getState();
      removeNode?.(id);
      onMessage?.({ role: 'assistant', content: `🗑️ Removed node: ${id}` });
    }
    return true;
  }, [onMessage]);

  // Edge operation handler
  const handleEdgeOperation = useCallback((op: string, message: WorkflowMessage) => {
    const id = message.id;
    const data = message.data || {};
    if (op === 'add' && message.data) {
      const edgeData = message.data;
      if (!isEdgeData(edgeData)) {
        addDebugLog(`invalid edge data for add: ${JSON.stringify(edgeData).slice(0,200)}`);
      } else {
        const { addEdge } = useWorkflowStore.getState();
        addEdge?.(edgeData as any);
        const source = (edgeData.source as string) || '?';
        const target = (edgeData.target as string) || '?';
        onMessage?.({ role: 'assistant', content: `🔗 Connected: ${source} → ${target}` });
      }
    } else if (op === 'update' && id) {
      const changes = message.changes || message.data || {};
      if (typeof changes !== 'object' || changes === null) {
        addDebugLog(`invalid edge changes for update: ${String(changes).slice(0,200)}`);
      } else {
        const { updateEdge } = useWorkflowStore.getState();
        updateEdge?.(id, changes as any);
        onMessage?.({ role: 'assistant', content: `✏️ Updated connection: ${id}` });
      }
    } else if (op === 'remove' && id) {
      const { removeEdge } = useWorkflowStore.getState();
      removeEdge?.(id);
      onMessage?.({ role: 'assistant', content: `❌ Disconnected: ${id}` });
    }
    return true;
  }, [onMessage]);

  // Legacy node handler
  const handleLegacyNode = useCallback((message: WorkflowMessage) => {
    const data = message.data || {};
    const nodeId = (data.id as string) || 'unnamed';
    addDebugLog(`legacy: add node ${nodeId}`);
    // Deprecated: legacy node additions from older stream formats.
    try {
      const nodeData = message.data;
      if (!isNodeData(nodeData)) {
        addDebugLog(`invalid legacy node data: ${JSON.stringify(nodeData).slice(0,200)}`);
        return true;
      }
        const { addNode } = useWorkflowStore.getState();
      addNode?.(nodeData as any);
      onMessage?.({ role: 'assistant', content: `➕ Added node: ${nodeId}` });
      return true;
    } catch (e) {
      console.error('Failed to add legacy node:', e);
      addDebugLog(`error: legacy node add failed - ${(e as Error).message}`);
      return true;
    }
  }, [onMessage, addDebugLog]);

  // Legacy edge handler
  const handleLegacyEdge = useCallback((message: WorkflowMessage) => {
    const data = message.data || {};
    const source = (data.source as string) || '?';
    const target = (data.target as string) || '?';
    addDebugLog(`legacy: add edge ${source} → ${target}`);
    // Deprecated: legacy edge additions from older stream formats.
    try {
      const edgeData = message.data;
      if (!isEdgeData(edgeData)) {
        addDebugLog(`invalid legacy edge data: ${JSON.stringify(edgeData).slice(0,200)}`);
        return true;
      }
      const { addEdge } = useWorkflowStore.getState();
      addEdge?.(edgeData as any);
      onMessage?.({ role: 'assistant', content: `🔗 Added connection` });
      return true;
    } catch (e) {
      console.error('Failed to add legacy edge:', e);
      addDebugLog(`error: legacy edge add failed - ${(e as Error).message}`);
      return true;
    }
  }, [onMessage, addDebugLog]);

  // Status message handler
  const handleStatusMessage = useCallback((message: WorkflowMessage) => {
    const statusData = typeof message.data === 'string' ? message.data : String(message.data || 'unknown');
    addDebugLog(`status: ${statusData}`);

    if (statusData === 'done') {
      onMessage?.({ role: 'assistant', content: '✅ Workflow generation complete!' });
    } else if (statusData) {
      onMessage?.({ role: 'assistant', content: `Status: ${statusData}` });
    }
    return true;
  }, [addDebugLog, onMessage]);

  // Text response handler
  const handleTextResponse = useCallback((message: WorkflowMessage) => {
    if (!message.response?.text) return false;

    const text = message.response.text;
    addDebugLog(`text: ${text.slice(0, 50)}...`);

    onMessage?.({ role: 'assistant', content: text });
    return true;
  }, [addDebugLog, onMessage]);

  // Main processing function
  const processStreamingChunk = useCallback((obj: unknown) => {
    if (!isWorkflowMessage(obj)) return false;

    const message = obj;

    // Handle direct workflow responses (no wrapper)
    if (hasNodesAndEdges(message)) {
      return handleDirectWorkflow(message);
    }

    // Handle complete tool_use responses (both static and streaming)
    if (hasToolUse(message)) {
      return handleToolUseResponse(message);
    }

    // Handle incremental updates via op-based commands
    if (hasOpAndType(message)) {
      return handleOpBasedUpdate(message);
    }

    // Handle legacy node/edge additions
    if (message.type === 'node' && message.data) {
      return handleLegacyNode(message);
    }

    if (message.type === 'edge' && message.data) {
      return handleLegacyEdge(message);
    }

    // Handle status and text responses
    if (message.type === 'status') {
      return handleStatusMessage(message);
    }

    if (message.response && typeof message.response === 'object' && message.response.text) {
      return handleTextResponse(message);
    }

    // Unhandled message: log for easier debugging of unexpected payloads
    try {
      addDebugLog(`unhandled message: ${JSON.stringify(message).slice(0,200)}`);
    } catch (e) {
      addDebugLog('unhandled message: [could not stringify]');
    }
    return false; // Unhandled message type
  }, [
    handleDirectWorkflow,
    handleToolUseResponse,
    handleOpBasedUpdate,
    handleLegacyNode,
    handleLegacyEdge,
    handleStatusMessage,
    handleTextResponse
  ]);

  return {
    processStreamingChunk
  };
};