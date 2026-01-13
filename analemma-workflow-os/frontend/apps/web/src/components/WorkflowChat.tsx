import { useState, useRef, useEffect, useCallback } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Sparkles, Send, Loader2, Zap, Users, RotateCcw, Play, AlertTriangle } from 'lucide-react';
import { useWorkflowStore } from '@/lib/workflowStore';
import { useCodesignStore } from '@/lib/codesignStore';
import { useCanvasMode } from '@/hooks/useCanvasMode';
import { useWorkflowRecovery } from '@/hooks/useWorkflowRecovery';
import { toast } from 'sonner';
import { streamDesignAssistant, streamCoDesignAssistant, resolveDesignAssistantEndpoint } from '@/lib/streamingFetch';
import { fetchAuthSession } from '@aws-amplify/auth';
import { useNotifications } from '@/hooks/useNotifications';
import { useWorkflowStreamProcessor } from '@/hooks/useWorkflowStreamProcessor';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface WorkflowChatProps {
  onWorkflowUpdate?: (workflow: any) => void;
}

export const WorkflowChat = ({ onWorkflowUpdate }: WorkflowChatProps) => {
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [_isStreaming, setIsStreaming] = useState(false);
  const scrollBottomRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const abortCtrlRef = useRef<AbortController | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);

  // Canvas mode detection
  const canvasMode = useCanvasMode();

  // Workflow recovery
  const recovery = useWorkflowRecovery();

  // Co-design store
  const { 
    messages: codesignMessages, 
    addMessage, 
    clearMessages,
    requestSuggestions,
    requestAudit,
    requestSimulation
  } = useCodesignStore();

  // Use the custom hook for stream processing (hook uses store directly)
  const { processStreamingChunk } = useWorkflowStreamProcessor({
    onWorkflowUpdate,
    onMessage: (message) => addMessage('assistant', message.content),
    onLog: (log) => setLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${log}`].slice(-100))
  });

  // WebSocket notifications for workflow component streaming
  const handleWorkflowComponentStream = useCallback((componentData: any) => {
    try {
      console.log('Received workflow component stream:', componentData);
      // Assuming componentData is a node object, add it to store
      const { addNode } = useWorkflowStore.getState();
      addNode?.(componentData as any);
    } catch (e) {
      console.error('Failed to handle workflow component stream:', e);
    }
  }, []);

  useNotifications({
    onWorkflowComponentStream: handleWorkflowComponentStream
  });

  // Smart auto-scroll logic
  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
    const isAtBottom = scrollHeight - scrollTop - clientHeight < 50;
    setUserScrolledUp(!isAtBottom);
  };

  useEffect(() => {
    if (!userScrolledUp && scrollBottomRef.current) {
      scrollBottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [codesignMessages, userScrolledUp]);

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    const userMessage = input.trim();
    setInput('');
    addMessage('user', userMessage);
    setIsLoading(true);
    setUserScrolledUp(false); // Reset scroll lock on send

    try {
      const token = (await fetchAuthSession()).tokens?.accessToken?.toString();
      const { nodes, edges } = useWorkflowStore.getState();
      const currentWorkflow = { nodes, edges };

      // Canvas 모드에 따라 다른 API 엔드포인트 사용
      if (canvasMode.mode === 'agentic-designer') {
        // 빈 Canvas - Agentic Designer 모드
        addMessage('assistant', '빈 Canvas가 감지되었습니다. AI가 초안을 생성해드리겠습니다...');
        
        // 생성 시작 마킹
        recovery.markGenerationStart(`session_${Date.now()}`);
        
        const bodyPayload = {
          user_request: userMessage,
          current_workflow: currentWorkflow,
          recent_changes: [],
          session_id: `session_${Date.now()}`
        };

        setIsStreaming(true);
        abortCtrlRef.current = new AbortController();

        await streamCoDesignAssistant('codesign', bodyPayload, {
          authToken: token,
          signal: abortCtrlRef.current.signal,
          onMessage: (obj: any) => {
            try {
              if (obj.type === 'node') {
                // 노드 추가
                const nodeData = obj.data;
                const { addNode } = useWorkflowStore.getState();
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
                recovery.markProgress('node_added');
                setLogs(prev => [...prev, `Added node: ${nodeData.id}`].slice(-100));
              } else if (obj.type === 'edge') {
                // 엣지 추가
                const edgeData = obj.data;
                const { addEdge } = useWorkflowStore.getState();
                const newEdge = {
                  id: edgeData.id,
                  source: edgeData.source,
                  target: edgeData.target,
                  sourceHandle: edgeData.source_handle,
                  targetHandle: edgeData.target_handle,
                  animated: true,
                  style: { stroke: 'hsl(263 70% 60%)', strokeWidth: 2 }
                };
                addEdge(newEdge);
                recovery.markProgress('edge_added');
                setLogs(prev => [...prev, `Added edge: ${edgeData.id}`].slice(-100));
              } else if (obj.type === 'status' && obj.data === 'done') {
                addMessage('assistant', '워크플로우 초안이 생성되었습니다! 이제 함께 개선해보세요.');
                recovery.markGenerationComplete();
              } else if (obj.type === 'text') {
                addMessage('assistant', obj.data || '');
              }
            } catch (e) {
              console.error('Agentic designer stream error:', e);
              setLogs(prev => [...prev, `Error: ${(e as Error).message}`].slice(-100));
            }
          },
          onDone: () => {
            setIsStreaming(false);
            setIsLoading(false);
            recovery.markGenerationComplete();
            setLogs(prev => [...prev, 'Agentic designer completed'].slice(-100));
          },
          onError: (e) => {
            setIsStreaming(false);
            setIsLoading(false);
            const errMsg = e?.message || 'Agentic designer error';
            
            // 복구 옵션 제공
            const recoveryInfo = recovery.handleGenerationInterruption(e);
            if (recoveryInfo?.canRecover) {
              toast.error(`${errMsg} - 복구 옵션을 확인하세요.`);
            } else {
              toast.error(errMsg);
            }
            
            addMessage('assistant', `오류가 발생했습니다: ${errMsg}`);
            setLogs(prev => [...prev, `Error: ${errMsg}`].slice(-100));
          }
        });
      } else {
        // 기존 워크플로우 - Co-design 모드
        const bodyPayload = {
          user_request: userMessage,
          current_workflow: currentWorkflow,
          recent_changes: useCodesignStore.getState().recentChanges,
          mode: 'codesign'
        };

        setIsStreaming(true);
        abortCtrlRef.current = new AbortController();

        await streamCoDesignAssistant('codesign', bodyPayload, {
          authToken: token,
          signal: abortCtrlRef.current.signal,
          onMessage: (obj: any) => {
            try {
              if (obj.type === 'text') {
                addMessage('assistant', obj.data || '');
              } else if (obj.type === 'response') {
                addMessage('assistant', obj.content || obj.data || '');
              } else if (obj.type === 'suggestion') {
                const suggestionData = obj.data || obj;
                useCodesignStore.getState().addSuggestion({
                  id: suggestionData.id || `suggestion-${Date.now()}`,
                  action: suggestionData.action,
                  reason: suggestionData.reason,
                  affectedNodes: suggestionData.affected_nodes || suggestionData.affectedNodes || [],
                  proposedChange: suggestionData.proposed_change || suggestionData.proposedChange || {},
                  confidence: suggestionData.confidence || 0.8
                });
                console.log('Received suggestion:', obj);
              } else if (obj.type === 'audit') {
                const auditData = obj.data || obj;
                console.log('Received audit:', auditData);
              } else if (obj.type === 'node' || obj.type === 'edge') {
                console.log(`Received ${obj.type}:`, obj.data);
              }
            } catch (e) {
              console.error('Co-design stream error:', e);
              setLogs(prev => [...prev, `Error: ${(e as Error).message}`].slice(-100));
            }
          },
          onDone: () => {
            setIsStreaming(false);
            setIsLoading(false);
            setLogs(prev => [...prev, 'Co-design completed'].slice(-100));
          },
          onError: (e) => {
            setIsStreaming(false);
            setIsLoading(false);
            const errMsg = e?.message || 'Co-design error';
            toast.error(errMsg);
            addMessage('assistant', `오류가 발생했습니다: ${errMsg}`);
            setLogs(prev => [...prev, `Error: ${errMsg}`].slice(-100));
          }
        });
      }
    } catch (error) {
      const errorMessage = (error as Error).message || 'Failed to get response from assistant';
      toast.error(errorMessage);
      addMessage('assistant', `죄송합니다. 오류가 발생했습니다: ${errorMessage}`);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-full border-t border-border">
      <div className="flex items-center gap-2 p-3 border-b border-border bg-card/50">
        <div className="flex items-center gap-2">
          {canvasMode.mode === 'agentic-designer' ? (
            <Zap className="w-4 h-4 text-orange-500" />
          ) : (
            <Users className="w-4 h-4 text-blue-500" />
          )}
          <h3 className="font-semibold text-sm">
            {canvasMode.mode === 'agentic-designer' ? 'AI Designer' : 'Co-design'}
          </h3>
          <Badge variant={canvasMode.mode === 'agentic-designer' ? 'default' : 'secondary'} className="text-xs">
            {canvasMode.mode === 'agentic-designer' ? '초안 생성' : '협업 개선'}
          </Badge>
        </div>
        <div className="ml-auto">
          <Button size="sm" variant="ghost" onClick={() => setShowLogs((s) => !s)}>
            {showLogs ? 'Hide Logs' : 'Show Logs'}
          </Button>
        </div>
      </div>

      {/* Mode description */}
      <div className="px-3 py-2 bg-muted/30 border-b border-border">
        <p className="text-xs text-muted-foreground">
          {canvasMode.description}
        </p>
      </div>

      <div className="flex-1 flex flex-col">
        {/* Recovery Alert - 복구 가능한 상태일 때 표시 */}
        {recovery.snapshots.length > 0 && recovery.isGenerating && (
          <Alert className="m-3 mb-0">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription className="flex items-center justify-between">
              <span>워크플로우 생성 중 문제가 발생했습니다.</span>
              <div className="flex gap-2">
                <Button 
                  size="sm" 
                  variant="outline"
                  onClick={() => recovery.rollbackToSnapshot(recovery.snapshots[recovery.snapshots.length - 1])}
                  disabled={recovery.isRecovering}
                >
                  <RotateCcw className="w-3 h-3 mr-1" />
                  복원
                </Button>
                <Button 
                  size="sm" 
                  variant="outline"
                  onClick={() => recovery.resumeGeneration()}
                  disabled={recovery.isRecovering}
                >
                  <Play className="w-3 h-3 mr-1" />
                  재개
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        )}

        <ScrollArea className="flex-1 p-3" onScrollCapture={handleScroll}>
          <div className="space-y-3">
          {codesignMessages.map((message, i) => (
            <div
              key={i}
              className={`flex ${message.type === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                  message.type === 'user'
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-secondary text-secondary-foreground'
                }`}
              >
                {message.content || <Loader2 className="w-4 h-4 animate-spin" />}
              </div>
            </div>
          ))}
            <div ref={scrollBottomRef} />
          </div>
        </ScrollArea>

        {showLogs && (
          <div className="p-2 border-t border-border bg-[#0b1220] text-xs font-mono text-white max-h-36 overflow-auto">
            {logs.length === 0 ? (
              <div className="text-muted-foreground">No logs yet</div>
            ) : (
              logs.map((l, i) => (
                <div key={i} className="mb-1">
                  {l}
                </div>
              ))
            )}
          </div>
        )}

      <div className="p-3 border-t border-border">
        <div className="flex gap-2">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleSend()}
            placeholder={
              canvasMode.mode === 'agentic-designer' 
                ? "워크플로우를 설명해주세요. AI가 초안을 생성합니다..."
                : "워크플로우 개선 사항을 말씀해주세요..."
            }
            className="flex-1"
            disabled={isLoading}
          />
          <Button size="icon" onClick={handleSend} disabled={isLoading}>
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </Button>
        </div>
        </div>
      </div>
    </div>
  );
};
