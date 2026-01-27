import { useState, useRef, useEffect, useCallback } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import {
  Sparkles,
  Send,
  Loader2,
  Zap,
  Users,
  RotateCcw,
  Play,
  AlertTriangle,
  Brain,
  History,
  XCircle
} from 'lucide-react';
import { useWorkflowStore } from '@/lib/workflowStore';
import { useCodesignStore } from '@/lib/codesignStore';
import { useCanvasMode } from '@/hooks/useCanvasMode';
import { useWorkflowRecovery } from '@/hooks/useWorkflowRecovery';
import { toast } from 'sonner';
import { streamCoDesignAssistant } from '@/lib/streamingFetch';
import { fetchAuthSession } from '@aws-amplify/auth';
import { useNotifications } from '@/hooks/useNotifications';
import { useWorkflowStreamProcessor } from '@/hooks/useWorkflowStreamProcessor';
import { cn } from '@/lib/utils';
import { motion, AnimatePresence } from 'framer-motion';

interface WorkflowChatProps {
  onWorkflowUpdate?: (workflow: any) => void;
}

export const WorkflowChat = ({ onWorkflowUpdate }: WorkflowChatProps) => {
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const scrollBottomRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const abortCtrlRef = useRef<AbortController | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);

  const canvasMode = useCanvasMode();
  const recovery = useWorkflowRecovery();

  const {
    messages: codesignMessages,
    addMessage,
  } = useCodesignStore();

  const { processStreamingChunk } = useWorkflowStreamProcessor({
    onWorkflowUpdate,
    onMessage: (msg) => addMessage(msg.role as any, msg.content),
    onLog: (log) => setLogs(prev => [...prev, `[${new Date().toLocaleTimeString()}] ${log}`].slice(-60))
  });

  const handleWorkflowComponentStream = useCallback((componentData: any) => {
    try {
      const { addNode } = useWorkflowStore.getState();
      addNode?.(componentData as any);
    } catch (e) {
      console.error('Failed to handle workflow component stream:', e);
    }
  }, []);

  useNotifications({
    onWorkflowComponentStream: handleWorkflowComponentStream
  });

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

  // Request cleanup
  useEffect(() => {
    return () => {
      if (abortCtrlRef.current) abortCtrlRef.current.abort();
    };
  }, []);

  const handleSend = async () => {
    if (!input.trim() || isLoading) return;

    const userMessage = input.trim();
    setInput('');
    addMessage('user', userMessage);
    setIsLoading(true);
    setUserScrolledUp(false);

    try {
      console.log('[WorkflowChat] Starting codesign request...');
      const session = await fetchAuthSession();
      const token = session.tokens?.accessToken?.toString();
      console.log('[WorkflowChat] Auth token obtained:', !!token);
      
      const { nodes, edges } = useWorkflowStore.getState();
      console.log('[WorkflowChat] Workflow state:', { nodeCount: nodes.length, edgeCount: edges.length });

      const isDesignerMode = canvasMode.mode === 'agentic-designer';
      if (isDesignerMode) {
        recovery.markGenerationStart(`session_${Date.now()}`);
      }

      const bodyPayload = {
        user_request: userMessage,
        current_workflow: { nodes, edges },
        recent_changes: useCodesignStore.getState().recentChanges,
        mode: isDesignerMode ? 'designer' : 'codesign',
        session_id: isDesignerMode ? `session_${Date.now()}` : undefined
      };

      console.log('[WorkflowChat] Payload prepared:', {
        mode: bodyPayload.mode,
        userRequest: userMessage.substring(0, 50),
        recentChangesCount: bodyPayload.recent_changes.length
      });

      abortCtrlRef.current = new AbortController();

      console.log('[WorkflowChat] Calling streamCoDesignAssistant...');
      await streamCoDesignAssistant('codesign', bodyPayload, {
        authToken: token,
        signal: abortCtrlRef.current.signal,
        onMessage: processStreamingChunk,
        onDone: () => {
          setIsLoading(false);
          if (isDesignerMode) recovery.markGenerationComplete();
        },
        onError: (e) => {
          setIsLoading(false);
          const errMsg = e?.message || 'Co-design error';

          if (isDesignerMode) {
            recovery.handleGenerationInterruption(e);
            addMessage('system', 'Generation interrupted. Recovery options available.');
          } else {
            toast.error(errMsg);
            addMessage('assistant', `Error occurred: ${errMsg}`);
          }
        }
      });
    } catch (error) {
      console.error('Chat error:', error);
      setIsLoading(false);
      addMessage('assistant', `System Error: ${(error as Error).message}`);
    }
  };

  return (
    <div className="flex flex-col h-full bg-[#0d0d0d] border-l border-white/5 relative overflow-hidden">
      {/* Dynamic Header */}
      <div className="flex items-center justify-between p-4 bg-gradient-to-b from-white/[0.03] to-transparent border-b border-white/5">
        <div className="flex items-center gap-3">
          <div className={cn(
            "p-2 rounded-xl transition-all duration-500",
            canvasMode.mode === 'agentic-designer'
              ? "bg-orange-500/10 text-orange-400 shadow-[0_0_15px_rgba(249,115,22,0.2)]"
              : "bg-blue-500/10 text-blue-400 shadow-[0_0_15px_rgba(59,130,246,0.2)]"
          )}>
            {canvasMode.mode === 'agentic-designer' ? <Zap className="w-4 h-4" /> : <Users className="w-4 h-4" />}
          </div>
          <div>
            <h3 className="text-xs font-black uppercase tracking-[0.2em] text-white/90">
              {canvasMode.mode === 'agentic-designer' ? 'Agentic Designer' : 'Co-design Intelligence'}
            </h3>
            <p className="text-[10px] text-white/40 font-medium">Session Active</p>
          </div>
        </div>
        <Button variant="ghost" size="icon" onClick={() => setShowLogs(!showLogs)} className="h-8 w-8 text-white/20 hover:text-white/60">
          <History className="w-4 h-4" />
        </Button>
      </div>

      {/* Main Chat Area */}
      <div className="flex-1 relative flex flex-col min-h-0">
        <AnimatePresence>
          {recovery.localSnapshots.length > 0 && recovery.isGenerating && (
            <motion.div
              initial={{ y: -20, opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={{ y: -20, opacity: 0 }}
              className="px-4 py-3 z-20"
            >
              <Alert className="bg-orange-500/5 border-orange-500/20 backdrop-blur-xl rounded-2xl overflow-hidden relative group">
                <div className="absolute top-0 left-0 w-1 h-full bg-orange-500" />
                <AlertTriangle className="h-4 w-4 text-orange-400" />
                <AlertTitle className="text-[10px] font-black uppercase text-orange-400 tracking-wider">Interruption Detected</AlertTitle>
                <AlertDescription className="flex items-center justify-between mt-2">
                  <span className="text-xs text-white/60">Would you like to resume?</span>
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 px-3 text-[10px] font-bold uppercase hover:bg-orange-500/10 text-orange-400"
                      onClick={() => recovery.rollbackToLocalSnapshot(recovery.localSnapshots[recovery.localSnapshots.length - 1])}
                    >
                      Rollback
                    </Button>
                    <Button
                      size="sm"
                      className="h-7 px-3 text-[10px] font-bold uppercase bg-orange-500 hover:bg-orange-600 text-white border-none"
                      onClick={() => recovery.resumeGeneration()}
                    >
                      Resume
                    </Button>
                  </div>
                </AlertDescription>
              </Alert>
            </motion.div>
          )}
        </AnimatePresence>

        <ScrollArea className="flex-1 px-4 py-2" onScrollCapture={handleScroll}>
          <div className="space-y-6 pb-4">
            {codesignMessages.map((message, i) => (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                key={message.id || i}
                className={cn(
                  "flex flex-col",
                  message.type === 'user' ? "items-end" : "items-start"
                )}
              >
                {message.type === 'thought' ? (
                  <div className="flex items-start gap-3 w-full max-w-[90%] px-1">
                    <div className="mt-1 p-1 bg-white/5 rounded-md border border-white/5">
                      <Brain className="w-3 h-3 text-white/40" />
                    </div>
                    <div className="flex-1 py-1 border-l-2 border-white/5 pl-4 border-dashed">
                      <p className="text-[11px] font-medium leading-relaxed italic text-white/30 tracking-tight">
                        {message.content}
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className={cn(
                    "max-w-[85%] px-4 py-2.5 rounded-2xl text-[13px] leading-relaxed",
                    message.type === 'user'
                      ? "bg-blue-600 text-white font-medium rounded-tr-none shadow-lg shadow-blue-600/10"
                      : "bg-[#1a1a1a] border border-white/5 text-white/80 rounded-tl-none shadow-xl"
                  )}>
                    {message.content || <Loader2 className="w-4 h-4 animate-spin opacity-40" />}
                  </div>
                )}
              </motion.div>
            ))}
            <div ref={scrollBottomRef} />
          </div>
        </ScrollArea>

        {showLogs && (
          <motion.div
            initial={{ height: 0 }}
            animate={{ height: 'auto' }}
            className="border-t border-white/5 bg-black/40 backdrop-blur-md max-h-32 overflow-auto custom-scrollbar"
          >
            <div className="p-3 font-mono text-[10px] text-white/40 space-y-1">
              {logs.length === 0 ? <p>Awaiting operations...</p> : logs.map((l, i) => <p key={i}>{l}</p>)}
            </div>
          </motion.div>
        )}
      </div>

      {/* Input Section */}
      <div className="p-4 bg-gradient-to-t from-black to-transparent">
        <div className="relative group">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleSend()}
            placeholder={
              canvasMode.mode === 'agentic-designer'
                ? "Describe your logic..."
                : "Ask for improvements..."
            }
            className="pl-4 pr-12 h-12 bg-[#1a1a1a] border-white/5 focus:border-blue-500/50 focus:ring-0 rounded-2xl text-sm text-white/90 placeholder:text-white/20 transition-all duration-300"
            disabled={isLoading}
          />
          <Button
            size="icon"
            onClick={handleSend}
            disabled={isLoading || !input.trim()}
            className={cn(
              "absolute right-1.5 top-1.5 h-9 w-9 rounded-xl transition-all duration-300",
              input.trim() ? "bg-blue-600 text-white opacity-100" : "bg-white/5 text-white/20 opacity-40"
            )}
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </Button>
        </div>
        <p className="mt-3 text-[10px] text-center text-white/10 font-black uppercase tracking-widest">
          Analemma OS Agentic Interface v1.0
        </p>
      </div>
    </div>
  );
};
