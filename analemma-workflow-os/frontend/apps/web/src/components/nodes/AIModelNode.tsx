import { Handle, Position } from '@xyflow/react';
import { Brain, X, Settings2, Hammer, Activity, Clock, DollarSign, FileText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import { useRef, useEffect } from 'react';

interface AIModelNodeProps {
  data: {
    label: string;
    modelName?: string;
    temperature?: number;
    toolsCount?: number;
    status?: 'idle' | 'running' | 'error' | 'success';
    tokens?: number;
    latency?: number;
    cost?: number;
    systemPrompt?: string;
    streamContent?: string;  // 추가: 실시간 스트리밍 텍스트
  };
  id: string;
  onDelete?: (id: string) => void;
  selected?: boolean;
}

export const AIModelNode = ({ data, id, onDelete, selected }: AIModelNodeProps) => {
  const statusColor = {
    idle: 'border-primary',
    running: 'border-blue-500 shadow-[0_0_15px_rgba(59,130,246,0.5)] animate-pulse',
    error: 'border-destructive shadow-[0_0_10px_rgba(239,68,68,0.4)]',
    success: 'border-green-500',
  }[data.status || 'idle'];

  const formattedCost = data.cost !== undefined
    ? `$${data.cost.toFixed(4)}`
    : null;

  const formattedLatency = data.latency
    ? data.latency > 1000
      ? `${(data.latency / 1000).toFixed(2)}s`
      : `${data.latency}ms`
    : null;

  // 스트리밍 영역 자동 스크롤
  const streamRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (streamRef.current && data.streamContent) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight;
    }
  }, [data.streamContent]);

  return (
    <div className={cn(
      "px-3 py-3 rounded-lg border bg-card backdrop-blur-sm min-w-[200px] transition-all duration-200 relative group",
      statusColor,
      selected && "ring-2 ring-ring ring-offset-1"
    )}>
      <Handle type="target" position={Position.Left} className="w-2.5 h-2.5 bg-muted-foreground" />

      {onDelete && (
        <Button
          size="icon"
          variant="ghost"
          className="absolute -top-2 -right-2 h-5 w-5 rounded-full bg-destructive text-destructive-foreground opacity-0 group-hover:opacity-100 hover:bg-destructive/90 shadow-sm transition-all z-10"
          onClick={(e) => {
            e.stopPropagation();
            onDelete(id);
          }}
        >
          <X className="w-3 h-3" />
        </Button>
      )}

      {/* 상단: 설정값 영역 */}
      <div className="flex items-start gap-3 mb-2">
        <Tooltip>
          <TooltipTrigger asChild>
            <div className={cn(
              "p-2 rounded-md flex-shrink-0 cursor-help transition-colors",
              data.status === 'error' ? "bg-destructive/10" : "bg-primary/10 hover:bg-primary/20"
            )}>
              <Brain className={cn(
                "w-4 h-4",
                data.status === 'error' ? "text-destructive" : "text-primary"
              )} />
            </div>
          </TooltipTrigger>
          <TooltipContent className="max-w-[250px] text-xs">
            <div className="font-semibold mb-1 flex items-center gap-1">
              <FileText className="w-3 h-3"/> System Prompt
            </div>
            <p className="line-clamp-4 text-muted-foreground">
              {data.systemPrompt || "No system prompt defined."}
            </p>
          </TooltipContent>
        </Tooltip>

        <div className="flex flex-col min-w-0">
          <span className="text-xs text-muted-foreground font-medium uppercase tracking-wider">LLM Step</span>
          <span className="text-sm font-bold truncate text-foreground">{data.label}</span>
        </div>
      </div>

      {/* 모델명 & 비용 */}
      <div className="flex items-center justify-between text-[10px] bg-muted/50 p-1.5 rounded mb-1.5">
        <span className="text-muted-foreground flex items-center gap-1">
          <Settings2 className="w-3 h-3" />
          {data.modelName || 'GPT-3.5'}
        </span>
        {formattedCost && (
          <span className="font-mono font-medium text-foreground flex items-center gap-0.5">
            <DollarSign className="w-2.5 h-2.5" />
            {formattedCost}
          </span>
        )}
      </div>

      {/* 파라미터 뱃지들 */}
      <div className="flex gap-1 mb-2">
        {data.temperature !== undefined && (
           <Badge variant="outline" className="text-[10px] h-5 px-1.5 gap-1 font-mono text-muted-foreground">
             T: {data.temperature}
           </Badge>
        )}

        {(data.toolsCount && data.toolsCount > 0) && (
          <Badge variant="secondary" className="text-[10px] h-5 px-1.5 gap-1 bg-blue-500/10 text-blue-600 border-blue-200">
            <Hammer className="w-3 h-3" />
            {data.toolsCount}
          </Badge>
        )}
      </div>

      {/* 스트리밍 영역 (running 상태일 때만) */}
      {data.status === 'running' && data.streamContent && (
        <div className="mb-2">
          <div
            ref={streamRef}
            className="bg-black/90 text-green-400 text-[10px] font-mono p-2 rounded max-h-20 overflow-y-auto border border-border/50 streaming-scroll"
          >
            {data.streamContent}
          </div>
        </div>
      )}

      {/* 하단: 결과값 영역 */}
      {(data.tokens || data.latency) && (
         <div className="flex items-center justify-between pt-1 border-t border-border/50">
           {formattedLatency && (
             <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
               <Clock className="w-3 h-3" />
               <span>{formattedLatency}</span>
             </div>
           )}

           {data.tokens && (
             <div className="flex items-center gap-1 text-[10px] text-muted-foreground ml-auto">
               <Activity className="w-3 h-3" />
               <span>{data.tokens.toLocaleString()} tks</span>
             </div>
           )}
         </div>
      )}

      <Handle type="source" position={Position.Right} className="w-2.5 h-2.5 bg-muted-foreground" />
    </div>
  );
};
