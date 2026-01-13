/**
 * useCodesignSync: Co-design 동기화 훅
 * 
 * 워크플로우 변경 사항을 추적하고 AI와 동기화합니다.
 */
import { useCallback, useRef, useEffect } from 'react';
import { useCodesignStore, ChangeType, AuditIssue, SuggestionPreview } from '@/lib/codesignStore';
import { useWorkflowStore } from '@/lib/workflowStore';
import { streamDesignAssistant, resolveDesignAssistantEndpoint } from '@/lib/streamingFetch';

interface UseCodesignSyncOptions {
  authToken?: string | null;
  autoAudit?: boolean;
  auditDebounceMs?: number;
}

interface CodesignStreamMessage {
  type: 'node' | 'edge' | 'suggestion' | 'audit' | 'text' | 'status' | 'error';
  data: any;
}

export function useCodesignSync(options: UseCodesignSyncOptions = {}) {
  const { authToken, autoAudit = true, auditDebounceMs = 2000 } = options;
  
  const {
    recentChanges,
    pendingSuggestions,
    addSuggestion,
    setAuditIssues,
    addMessage,
    setSyncStatus,
    setLastSyncTime,
    clearChanges,
  } = useCodesignStore();
  
  const { nodes, edges, addNode, addEdge: addWorkflowEdge } = useWorkflowStore();
  
  const abortControllerRef = useRef<AbortController | null>(null);
  const auditTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  
  /**
   * Co-design 요청 전송
   */
  const sendToCodesign = useCallback(async (
    userMessage: string,
    options: { 
      mode?: 'codesign' | 'explain' | 'suggest';
      onNodeReceived?: (node: any) => void;
      onEdgeReceived?: (edge: any) => void;
      onSuggestionReceived?: (suggestion: SuggestionPreview) => void;
      onAuditReceived?: (issue: AuditIssue) => void;
      onTextReceived?: (text: string) => void;
    } = {}
  ) => {
    // 이전 요청 취소
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();
    
    setSyncStatus('syncing');
    addMessage('user', userMessage);
    
    const payload = {
      request: userMessage,
      current_workflow: { nodes, edges },
      recent_changes: recentChanges,
      mode: options.mode || 'codesign'
    };
    
    let textBuffer = '';
    const receivedSuggestions: SuggestionPreview[] = [];
    const receivedAuditIssues: AuditIssue[] = [];
    
    try {
      await streamDesignAssistant(payload, {
        authToken,
        signal: abortControllerRef.current.signal,
        onMessage: (obj: CodesignStreamMessage) => {
          switch (obj.type) {
            case 'node':
              if (obj.data) {
                // 노드 데이터를 워크플로우에 추가
                const nodeData = obj.data;
                const newNode = {
                  id: nodeData.id,
                  type: nodeData.type,
                  position: nodeData.position || { x: 150, y: 50 },
                  data: {
                    label: nodeData.label || nodeData.id,
                    ...nodeData.config,
                    ...nodeData.data
                  }
                };
                addNode(newNode);
                options.onNodeReceived?.(newNode);
              }
              break;
              
            case 'edge':
              if (obj.data) {
                const edgeData = obj.data;
                const newEdge = {
                  id: edgeData.id,
                  source: edgeData.source,
                  target: edgeData.target,
                  sourceHandle: edgeData.source_handle,
                  targetHandle: edgeData.target_handle,
                  animated: true,
                  style: { stroke: 'hsl(263 70% 60%)', strokeWidth: 2 }
                };
                addWorkflowEdge(newEdge);
                options.onEdgeReceived?.(newEdge);
              }
              break;
              
            case 'suggestion':
              if (obj.data) {
                const suggestion: SuggestionPreview = {
                  id: obj.data.id || `sug_${Date.now()}`,
                  action: obj.data.action,
                  reason: obj.data.reason,
                  affectedNodes: obj.data.affected_nodes || [],
                  proposedChange: obj.data.proposed_change || {},
                  confidence: obj.data.confidence || 0.5,
                  status: 'pending'
                };
                addSuggestion(suggestion);
                receivedSuggestions.push(suggestion);
                options.onSuggestionReceived?.(suggestion);
              }
              break;
              
            case 'audit':
              if (obj.data) {
                const issue: AuditIssue = {
                  level: obj.data.level || 'info',
                  type: obj.data.type || 'unknown',
                  message: obj.data.message,
                  affectedNodes: obj.data.affected_nodes || [],
                  suggestion: obj.data.suggestion
                };
                receivedAuditIssues.push(issue);
                options.onAuditReceived?.(issue);
              }
              break;
              
            case 'text':
              if (obj.data) {
                textBuffer += obj.data;
                options.onTextReceived?.(obj.data);
              }
              break;
              
            case 'status':
              if (obj.data === 'done') {
                // 완료 처리
                if (textBuffer) {
                  addMessage('assistant', textBuffer, {
                    suggestionsCount: receivedSuggestions.length,
                    issuesCount: receivedAuditIssues.length
                  });
                }
                if (receivedAuditIssues.length > 0) {
                  setAuditIssues(receivedAuditIssues);
                }
              }
              break;
              
            case 'error':
              addMessage('system', `오류: ${obj.data}`);
              break;
          }
        },
        onDone: () => {
          setSyncStatus('idle');
          setLastSyncTime(Date.now());
          clearChanges();
        },
        onError: (error) => {
          setSyncStatus('error');
          addMessage('system', `연결 오류: ${error.message}`);
        }
      });
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        setSyncStatus('error');
        addMessage('system', `요청 실패: ${(error as Error).message}`);
      }
    }
  }, [
    nodes, 
    edges, 
    recentChanges, 
    authToken, 
    addNode, 
    addWorkflowEdge,
    addSuggestion, 
    setAuditIssues, 
    addMessage, 
    setSyncStatus, 
    setLastSyncTime, 
    clearChanges
  ]);
  
  /**
   * 워크플로우 검증 요청
   */
  const auditWorkflow = useCallback(async () => {
    const endpoint = resolveDesignAssistantEndpoint();
    const auditUrl = endpoint.url.replace('/design-assistant', '/audit');
    
    try {
      const response = await fetch(auditUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(authToken ? { 'Authorization': `Bearer ${authToken}` } : {})
        },
        body: JSON.stringify({ workflow: { nodes, edges } })
      });
      
      if (response.ok) {
        const result = await response.json();
        setAuditIssues(result.issues || []);
        return result;
      }
    } catch (error) {
      console.error('Audit request failed:', error);
    }
    
    return null;
  }, [nodes, edges, authToken, setAuditIssues]);
  
  /**
   * 워크플로우 시뮬레이션 요청
   */
  const simulateWorkflow = useCallback(async (mockInputs: Record<string, any> = {}) => {
    const endpoint = resolveDesignAssistantEndpoint();
    const simulateUrl = endpoint.url.replace('/design-assistant', '/simulate');
    
    try {
      const response = await fetch(simulateUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(authToken ? { 'Authorization': `Bearer ${authToken}` } : {})
        },
        body: JSON.stringify({ 
          workflow: { nodes, edges },
          mock_inputs: mockInputs
        })
      });
      
      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Simulate request failed:', error);
    }
    
    return null;
  }, [nodes, edges, authToken]);
  
  /**
   * 워크플로우 설명 요청
   */
  const explainWorkflow = useCallback(async () => {
    const endpoint = resolveDesignAssistantEndpoint();
    const explainUrl = endpoint.url.replace('/design-assistant', '/explain');
    
    try {
      const response = await fetch(explainUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(authToken ? { 'Authorization': `Bearer ${authToken}` } : {})
        },
        body: JSON.stringify({ workflow: { nodes, edges } })
      });
      
      if (response.ok) {
        return await response.json();
      }
    } catch (error) {
      console.error('Explain request failed:', error);
    }
    
    return null;
  }, [nodes, edges, authToken]);
  
  /**
   * 요청 취소
   */
  const cancelRequest = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setSyncStatus('idle');
  }, [setSyncStatus]);
  
  // 자동 검증 (변경 후 디바운스)
  useEffect(() => {
    if (!autoAudit || recentChanges.length === 0) return;
    
    // 기존 타이머 취소
    if (auditTimeoutRef.current) {
      clearTimeout(auditTimeoutRef.current);
    }
    
    // 디바운스된 검증 예약
    auditTimeoutRef.current = setTimeout(() => {
      auditWorkflow();
    }, auditDebounceMs);
    
    return () => {
      if (auditTimeoutRef.current) {
        clearTimeout(auditTimeoutRef.current);
      }
    };
  }, [recentChanges.length, autoAudit, auditDebounceMs, auditWorkflow]);
  
  // 컴포넌트 언마운트 시 정리
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
      if (auditTimeoutRef.current) {
        clearTimeout(auditTimeoutRef.current);
      }
    };
  }, []);
  
  return {
    // 상태
    recentChanges,
    pendingSuggestions,
    
    // 액션
    sendToCodesign,
    auditWorkflow,
    simulateWorkflow,
    explainWorkflow,
    cancelRequest,
  };
}

export default useCodesignSync;
