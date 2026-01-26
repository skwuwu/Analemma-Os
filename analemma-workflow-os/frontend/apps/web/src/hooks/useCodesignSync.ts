/**
 * useCodesignSync: Co-design 동기화 훅 (v2.0)
 * ====================================================
 * 
 * 워크플로우 변경 사항을 추적하고 AI와 동기화합니다.
 * 
 * v2.0 Changes:
 * - API 엔드포인트 상수화 (URL 파싱 견고함)
 * - sendToCodesign 의존성 최적화 (getState() 패턴)
 * - 스트리밍 데이터 검증 및 에러 핸들링 강화
 * - optionsRef 패턴으로 콜백 안정성 개선
 */
import { useCallback, useRef, useEffect } from 'react';
import { useCodesignStore, ChangeType, AuditIssue, SuggestionPreview } from '@/lib/codesignStore';
import { useWorkflowStore } from '@/lib/workflowStore';
import { streamDesignAssistant, resolveDesignAssistantEndpoint } from '@/lib/streamingFetch';

// API 엔드포인트 상수 (URL 파싱 대신)
const API_ENDPOINTS = {
  DESIGN_ASSISTANT: '/design-assistant',
  AUDIT: '/audit',
  SIMULATE: '/simulate',
  EXPLAIN: '/explain',
} as const;

interface UseCodesignSyncOptions {
  authToken?: string | null;
  autoAudit?: boolean;
  auditDebounceMs?: number;
  onNodeReceived?: (node: any) => void;
  onEdgeReceived?: (edge: any) => void;
  onSuggestionReceived?: (suggestion: SuggestionPreview) => void;
  onAuditReceived?: (issue: AuditIssue) => void;
  onTextReceived?: (text: string) => void;
}

interface CodesignStreamMessage {
  type: 'node' | 'edge' | 'suggestion' | 'audit' | 'text' | 'status' | 'error';
  data: any;
}

export function useCodesignSync(options: UseCodesignSyncOptions = {}) {
  const { authToken, autoAudit = true, auditDebounceMs = 2000 } = options;
  const optionsRef = useRef(options);
  
  // Keep callbacks stable
  useEffect(() => {
    optionsRef.current = options;
  });
  
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
  
  // Store reference만 가져오기 (getState로 최신 상태 접근)
  const workflowStore = useWorkflowStore;
  
  const abortControllerRef = useRef<AbortController | null>(null);
  const auditTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  
  /**
   * API URL 빌더 (견고한 엔드포인트 관리)
   */
  const getApiUrl = useCallback((endpoint: keyof typeof API_ENDPOINTS): string => {
    const baseEndpoint = resolveDesignAssistantEndpoint();
    const baseUrl = baseEndpoint.url.replace(API_ENDPOINTS.DESIGN_ASSISTANT, '');
    return `${baseUrl}${API_ENDPOINTS[endpoint]}`;
  }, []);
  
  /**
   * Co-design 요청 전송 (최적화: getState()로 최신 상태 접근)
   */
  const sendToCodesign = useCallback(async (
    userMessage: string,
    requestOptions: { 
      mode?: 'codesign' | 'explain' | 'suggest';
    } = {}
  ) => {
    // 이전 요청 취소
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    abortControllerRef.current = new AbortController();
    
    setSyncStatus('syncing');
    addMessage('user', userMessage);
    
    // getState()로 최신 상태 가져오기 (함수 재생성 방지)
    const { nodes, edges, addNode, addEdge: addWorkflowEdge } = workflowStore.getState();
    
    const payload = {
      request: userMessage,
      current_workflow: { nodes, edges },
      recent_changes: recentChanges,
      mode: requestOptions.mode || 'codesign'
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
                try {
                  const { id, type, position, label, config, data: extraData } = obj.data;
                  
                  // 필수 필드 검증
                  if (!id || !type) {
                    console.warn('Invalid node data: missing id or type', obj.data);
                    return;
                  }

                  const newNode = {
                    id,
                    type,
                    position: position || { 
                      x: Math.random() * 200 + 50, 
                      y: Math.random() * 200 + 50 
                    },
                    data: {
                      label: label || config?.label || id,
                      ...config,
                      ...extraData
                    }
                  };
                  
                  addNode(newNode);
                  optionsRef.current.onNodeReceived?.(newNode);
                } catch (e) {
                  console.error('Failed to process streamed node:', e, obj.data);
                }
              }
              break;
              
            case 'edge':
              if (obj.data) {
                try {
                  const { id, source, target, source_handle, target_handle, sourceHandle, targetHandle } = obj.data;
                  
                  // 필수 필드 검증
                  if (!id || !source || !target) {
                    console.warn('Invalid edge data: missing required fields', obj.data);
                    return;
                  }

                  const newEdge = {
                    id,
                    source,
                    target,
                    sourceHandle: source_handle || sourceHandle,
                    targetHandle: target_handle || targetHandle,
                    animated: true,
                    style: { stroke: 'hsl(263 70% 60%)', strokeWidth: 2 }
                  };
                  
                  addWorkflowEdge(newEdge);
                  optionsRef.current.onEdgeReceived?.(newEdge);
                } catch (e) {
                  console.error('Failed to process streamed edge:', e, obj.data);
                }
              }
              break;
              
            case 'suggestion':
              if (obj.data) {
                try {
                  const suggestion: SuggestionPreview = {
                    id: obj.data.id || `sug_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
                    action: obj.data.action || 'unknown',
                    reason: obj.data.reason || '',
                    affectedNodes: obj.data.affected_nodes || obj.data.affectedNodes || [],
                    proposedChange: obj.data.proposed_change || obj.data.proposedChange || {},
                    confidence: obj.data.confidence || 0.5,
                    status: 'pending'
                  };
                  addSuggestion(suggestion);
                  receivedSuggestions.push(suggestion);
                  optionsRef.current.onSuggestionReceived?.(suggestion);
                } catch (e) {
                  console.error('Failed to process suggestion:', e, obj.data);
                }
              }
              break;
              
            case 'audit':
              if (obj.data) {
                try {
                  const issue: AuditIssue = {
                    level: obj.data.level || 'info',
                    type: obj.data.type || 'unknown',
                    message: obj.data.message || 'No message provided',
                    affectedNodes: obj.data.affected_nodes || obj.data.affectedNodes || [],
                    suggestion: obj.data.suggestion
                  };
                  receivedAuditIssues.push(issue);
                  optionsRef.current.onAuditReceived?.(issue);
                } catch (e) {
                  console.error('Failed to process audit issue:', e, obj.data);
                }
              }
              break;
              
            case 'text':
              if (obj.data) {
                textBuffer += obj.data;
                optionsRef.current.onTextReceived?.(obj.data);
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
    recentChanges,
    authToken,
    workflowStore,
    addSuggestion,
    setAuditIssues,
    addMessage,
    setSyncStatus,
    setLastSyncTime,
    clearChanges
  ]);
  
  /**
   * 워크플로우 검증 요청 (견고한 URL 관리)
   */
  const auditWorkflow = useCallback(async () => {
    const auditUrl = getApiUrl('AUDIT');
    const { nodes, edges } = workflowStore.getState();
    
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
  }, [authToken, workflowStore, setAuditIssues, getApiUrl]);
  
  /**
   * 워크플로우 시뮬레이션 요청
   */
  const simulateWorkflow = useCallback(async (mockInputs: Record<string, any> = {}) => {
    const simulateUrl = getApiUrl('SIMULATE');
    const { nodes, edges } = workflowStore.getState();
    
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
  }, [authToken, workflowStore, getApiUrl]);
  
  /**
   * 워크플로우 설명 요청
   */
  const explainWorkflow = useCallback(async () => {
    const explainUrl = getApiUrl('EXPLAIN');
    const { nodes, edges } = workflowStore.getState();
    
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
  }, [authToken, workflowStore, getApiUrl]);
  
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
