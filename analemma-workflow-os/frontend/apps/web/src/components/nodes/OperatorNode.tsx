import { Handle, Position } from '@xyflow/react';
import {
  Globe,
  Database,
  X,
  CheckCircle2,
  AlertCircle,
  Loader2,
  ShieldAlert
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { useWorkflowStore } from '@/lib/workflowStore';
import { memo } from 'react';
import { OPERATOR_CONFIG, type OperatorType } from '@/lib/nodeConstants';

const ICON_MAP = {
  Globe,
  Database,
  CheckCircle2,
  AlertCircle,
  Loader2
} as const;

// Status styles extracted outside component — static lookup
const STATUS_STYLES = {
  running: { border: 'border-yellow-500 shadow-yellow-500/20', text: 'text-yellow-500' },
  completed: { border: 'border-green-500 shadow-green-500/20', text: 'text-green-500' },
  failed: { border: 'border-destructive shadow-destructive/20', text: 'text-destructive' },
  idle: { border: 'border-white/10', text: 'text-muted-foreground' },
} as const;

interface OperatorNodeProps {
  data: {
    label: string;
    operatorType?: keyof typeof OPERATOR_CONFIG;
    operatorVariant?: 'custom' | 'official';
    action?: string;
    status?: 'idle' | 'running' | 'failed' | 'completed';
    authStatus?: 'connected' | 'disconnected' | 'unknown';
    authMessage?: string;
  };
  id: string;
  selected?: boolean;
}

const OperatorNodeInner = ({ data, id, selected }: OperatorNodeProps) => {
  const removeNode = useWorkflowStore(state => state.removeNode);
  const config = OPERATOR_CONFIG[data.operatorType as keyof typeof OPERATOR_CONFIG] || OPERATOR_CONFIG.default;
  const status = data.status || 'idle';
  const Icon = ICON_MAP[config.iconName as keyof typeof ICON_MAP] || Globe;
  const statusStyle = STATUS_STYLES[status];
  const authDisconnected = data.authStatus === 'disconnected';

  return (
    <div
      className={cn(
        "px-3 py-2.5 rounded-xl border bg-card/80 backdrop-blur-md shadow-xl min-w-[150px] relative group transition-all duration-300",
        authDisconnected ? 'border-orange-500/60' : statusStyle.border,
        selected && "ring-2 ring-primary ring-offset-2"
      )}
      style={{
        background: `linear-gradient(to bottom right, hsl(${config.color} / 0.1), hsl(${config.color} / 0.02))`
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="w-2.5 h-2.5 bg-muted-foreground/50 border-none"
      />

      {/* Status icon — CSS transition instead of AnimatePresence */}
      {status !== 'idle' && (
        <div className={cn(
          "absolute top-2 right-2 p-1 rounded-full bg-background/50 backdrop-blur-sm border border-white/5 transition-opacity duration-200",
          statusStyle.text
        )}>
          {status === 'running' && <Loader2 className="w-3 h-3 animate-spin" />}
          {status === 'failed' && <AlertCircle className="w-3 h-3" />}
          {status === 'completed' && <CheckCircle2 className="w-3 h-3" />}
        </div>
      )}

      <div className="flex items-center gap-3">
        <div
          className="p-2 rounded-lg"
          style={{ backgroundColor: `hsl(${config.color} / 0.15)` }}
        >
          <Icon className="w-4 h-4" style={{ color: `hsl(${config.color})` }} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-[10px] text-muted-foreground/60 font-black uppercase tracking-tighter flex items-center justify-between">
            {config.label}
          </div>
          <div className="text-sm font-black text-white truncate leading-tight">
            {data.label}
          </div>

          {data.action && (
            <div className="mt-1">
              <Badge
                variant="outline"
                className="text-[9px] h-4 px-1 border-0"
                style={{ backgroundColor: `hsl(${config.color} / 0.1)`, color: `hsl(${config.color})` }}
              >
                {data.action}
              </Badge>
            </div>
          )}
        </div>
      </div>

      {/* Auth status warning */}
      {authDisconnected && (
        <div className="flex items-center gap-1.5 mt-2 px-2 py-1 rounded-md bg-orange-500/10 text-[10px] text-orange-400 border border-orange-500/20">
          <ShieldAlert className="w-3 h-3 flex-shrink-0" />
          <span className="truncate">{data.authMessage || 'Auth disconnected'}</span>
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        className="w-2.5 h-2.5 bg-muted-foreground/50 border-none"
      />

      <Button
        size="icon"
        variant="ghost"
        className="absolute top-1 right-1 h-6 w-6 rounded-full bg-destructive text-destructive-foreground opacity-0 group-hover:opacity-100 hover:bg-destructive shadow-lg transition-all z-20"
        onClick={(e) => { e.stopPropagation(); removeNode(id); }}
      >
        <X className="w-3 h-3" />
      </Button>
    </div>
  );
};

export const OperatorNode = memo(OperatorNodeInner, (prev, next) => {
  if (prev.selected !== next.selected) return false;
  if (prev.id !== next.id) return false;
  const pd = prev.data;
  const nd = next.data;
  return (
    pd.label === nd.label &&
    pd.operatorType === nd.operatorType &&
    pd.action === nd.action &&
    pd.status === nd.status &&
    pd.authStatus === nd.authStatus &&
    pd.authMessage === nd.authMessage
  );
});

export default OperatorNode;
