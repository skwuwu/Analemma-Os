import { Handle, Position } from '@xyflow/react';
import {
  Brain,
  X,
  Settings2,
  Activity,
  Clock,
  DollarSign,
  CheckCircle2,
  AlertCircle,
  Loader2,
  Wrench
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { useWorkflowStore } from '@/lib/workflowStore';
import { memo, useRef, useEffect, useMemo } from 'react';
import { ExecutionOrderBadge } from './ExecutionOrderBadge';

export interface ToolDefinition {
  name: string;
  description: string;
  parameters?: Record<string, unknown>;
  required_api_keys?: string[];
  handler_type?: string;
  handler_config?: Record<string, unknown>;
  skill_id?: string;
  skill_version?: string;
}

interface AIModelNodeProps {
  data: {
    label: string;
    modelName?: string;
    model?: string;
    temperature?: number;
    toolsCount?: number;
    tools?: ToolDefinition[];
    status?: 'idle' | 'running' | 'failed' | 'completed';
    tokens?: number;
    latency?: number;
    cost?: number;
    systemPrompt?: string;
    streamContent?: string;
    _executionOrder?: number;
  };
  id: string;
  selected?: boolean;
}

// Status config moved outside component — static, never recreated
const STATUS_CONFIG = {
  idle: { border: 'border-primary/20', text: 'text-muted-foreground' },
  running: { border: 'border-blue-500 shadow-blue-500/20', text: 'text-blue-500' },
  failed: { border: 'border-destructive shadow-destructive/20', text: 'text-destructive' },
  completed: { border: 'border-green-500 shadow-green-500/20', text: 'text-green-500' },
} as const;

// Extracted streaming area to isolate re-renders from streamContent changes
const StreamingArea = memo(({ streamContent }: { streamContent: string }) => {
  const streamRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [streamContent]);

  return (
    <div className="mb-3">
      <div
        ref={streamRef}
        className="bg-[#0a0a0a] text-blue-400 text-[10px] font-mono p-2.5 rounded-lg border border-blue-500/20 max-h-24 overflow-y-auto leading-relaxed shadow-inner"
      >
        <div className="flex items-center gap-2 mb-1 opacity-50">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
          <span className="text-[8px] uppercase tracking-widest font-black">Streaming Response</span>
        </div>
        {streamContent}
      </div>
    </div>
  );
});
StreamingArea.displayName = 'StreamingArea';

const AIModelNodeInner = ({ data, id, selected }: AIModelNodeProps) => {
  const removeNode = useWorkflowStore(state => state.removeNode);
  const status = data.status || 'idle';
  const statusStyle = STATUS_CONFIG[status];

  const formattedCost = data.cost !== undefined ? `$${data.cost.toFixed(4)}` : null;
  const formattedLatency = data.latency
    ? (data.latency > 1000 ? `${(data.latency / 1000).toFixed(2)}s` : `${data.latency}ms`)
    : null;

  // Memoize tool summary string to avoid slice/map/join on every render
  const toolsSummary = useMemo(() => {
    if (!data.tools || data.tools.length === 0) return null;
    const names = data.tools.slice(0, 2).map(t => t.name).join(', ');
    return data.tools.length > 2 ? `(${names}...)` : `(${names})`;
  }, [data.tools]);

  const toolsCount = data.tools?.length || data.toolsCount;

  return (
    <div className={cn(
      "px-3 py-3 rounded-xl border backdrop-blur-md min-w-[220px] transition-all duration-300 relative group",
      statusStyle.border,
      selected && "ring-2 ring-primary ring-offset-2"
    )}
    style={{
      background: 'linear-gradient(to bottom right, hsl(217 91% 60% / 0.15), hsl(217 91% 60% / 0.05))',
      boxShadow: '0 0 20px hsl(217 91% 60% / 0.15)'
    }}>
      <ExecutionOrderBadge order={data._executionOrder} />
      {/* Status icon — CSS transition instead of AnimatePresence */}
      {status !== 'idle' && (
        <div className={cn(
          "absolute top-2 right-2 p-1 rounded-full bg-background/80 border border-white/5 backdrop-blur-md z-10 transition-opacity duration-200",
          statusStyle.text
        )}>
          {status === 'running' && <Loader2 className="w-3 h-3 animate-spin" />}
          {status === 'failed' && <AlertCircle className="w-3 h-3" />}
          {status === 'completed' && <CheckCircle2 className="w-3 h-3" />}
        </div>
      )}

      <Handle type="target" position={Position.Left} className="w-2.5 h-2.5 bg-muted-foreground/50 border-none" />

      <Button
        size="icon"
        variant="ghost"
        className="absolute top-1 right-1 h-6 w-6 rounded-full bg-destructive text-destructive-foreground opacity-0 group-hover:opacity-100 hover:bg-destructive shadow-lg transition-all z-20"
        onClick={(e) => { e.stopPropagation(); removeNode(id); }}
      >
        <X className="w-3 h-3" />
      </Button>

      {/* Header */}
      <div className="flex items-start gap-3 mb-3">
        <div className="p-2 rounded-lg bg-primary/10">
          <Brain className="w-4 h-4 text-primary" />
        </div>
        <div className="flex flex-col min-w-0">
          <span className="text-[10px] text-muted-foreground font-bold uppercase tracking-tighter">AI Agent Step</span>
          <span className="text-sm font-black truncate text-white">{data.label}</span>
        </div>
      </div>

      {/* Model Context */}
      <div className="grid grid-cols-2 gap-1.5 mb-3">
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-white/10 text-[10px] text-white/70 border border-white/10 truncate">
          <Settings2 className="w-3 h-3 opacity-70" />
          {data.modelName || data.model || 'GPT-4o'}
        </div>
        {formattedCost && (
          <div className="flex items-center gap-1 px-2 py-1 rounded-md bg-green-500/5 text-[10px] text-green-500 border border-green-500/10 truncate font-mono">
            <DollarSign className="w-2.5 h-2.5" />
            {formattedCost}
          </div>
        )}
      </div>

      {/* Tools/Skills Badge */}
      {toolsCount ? (
        <div className="group/tools relative">
          <div className="flex items-center gap-1.5 px-2 py-1.5 mb-3 rounded-lg bg-amber-500/10 text-[10px] text-amber-400 border border-amber-500/20 cursor-help">
            <Wrench className="w-3 h-3" />
            <span className="font-bold">{toolsCount} Tools</span>
            {toolsSummary && (
              <span className="text-amber-400/60 truncate ml-1">{toolsSummary}</span>
            )}
          </div>
          {data.tools && data.tools.length > 0 && (
            <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 hidden group-hover/tools:block z-50 max-w-[250px] rounded-md border bg-popover p-2 text-popover-foreground shadow-md">
              <div className="space-y-1">
                <p className="font-bold text-xs">Enabled Tools:</p>
                {data.tools.map((tool, i) => (
                  <p key={i} className="text-[10px] text-muted-foreground">{'\u2022'} {tool.name}</p>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : null}

      {/* Streaming — isolated component to prevent full node re-render */}
      {status === 'running' && data.streamContent && (
        <StreamingArea streamContent={data.streamContent} />
      )}

      {/* Analytics Footer */}
      {(data.tokens || data.latency) && (
        <div className="flex items-center justify-between pt-2 border-t border-white/5 mt-auto">
          {formattedLatency && (
            <div className="flex items-center gap-1.5 text-[9px] text-muted-foreground/60 font-medium">
              <Clock className="w-3 h-3" />
              {formattedLatency}
            </div>
          )}
          {data.tokens && (
            <div className="flex items-center gap-1.5 text-[9px] text-muted-foreground/60 font-mono ml-auto">
              <Activity className="w-3 h-3 opacity-40" />
              {data.tokens.toLocaleString()}
            </div>
          )}
        </div>
      )}

      <Handle type="source" position={Position.Right} className="w-2.5 h-2.5 bg-muted-foreground/50 border-none" />
    </div>
  );
};

// React.memo with custom areEqual — only re-render when meaningful data changes
export const AIModelNode = memo(AIModelNodeInner, (prev, next) => {
  if (prev.selected !== next.selected) return false;
  if (prev.id !== next.id) return false;
  const pd = prev.data;
  const nd = next.data;
  return (
    pd.label === nd.label &&
    pd.modelName === nd.modelName &&
    pd.model === nd.model &&
    pd.status === nd.status &&
    pd.tokens === nd.tokens &&
    pd.latency === nd.latency &&
    pd.cost === nd.cost &&
    pd.toolsCount === nd.toolsCount &&
    pd.tools === nd.tools &&
    pd.streamContent === nd.streamContent
  );
});
