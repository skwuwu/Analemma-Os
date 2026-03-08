import { Handle, Position } from '@xyflow/react';
import { Clock, X, GitBranch, Repeat, User, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { useWorkflowStore } from '@/lib/workflowStore';
import { memo } from 'react';

const CONTROL_CONFIG = {
  branch: {
    icon: GitBranch,
    color: '142 76% 36%',
    label: 'Branch'
  },
  loop: {
    icon: Repeat,
    color: '48 96% 53%',
    label: 'Loop'
  },
  wait: {
    icon: Clock,
    color: '25 95% 60%',
    label: 'Wait'
  },
  human: {
    icon: User,
    color: '263 70% 60%',
    label: 'Human'
  },
  conditional: {
    icon: AlertTriangle,
    color: '0 84% 60%',
    label: 'Conditional'
  },
  default: {
    icon: Clock,
    color: '280 65% 60%',
    label: 'Control'
  }
} as const;

// Status styles — use opacity/transform animations instead of box-shadow (avoids layout/paint thrashing)
const STATUS_STYLES = {
  waiting: 'animate-pulse opacity-90',
  active: 'opacity-100',
  completed: 'opacity-75',
  idle: '',
} as const;

interface ControlNodeProps {
  data: {
    label: string;
    controlType?: keyof typeof CONTROL_CONFIG;
    condition?: string;
    status?: 'idle' | 'waiting' | 'active' | 'completed';
    loopCount?: number;
    maxIterations?: number;
  };
  id: string;
  selected?: boolean;
}

const ControlNodeInner = ({ data, id, selected }: ControlNodeProps) => {
  const removeNode = useWorkflowStore(state => state.removeNode);
  const config = CONTROL_CONFIG[data?.controlType || 'default'] || CONTROL_CONFIG.default;
  const IconComponent = config.icon;
  const statusClass = STATUS_STYLES[data?.status || 'idle'] || '';
  const hasMultipleHandles = data?.controlType === 'branch' || data?.controlType === 'conditional';
  const color = config.color;

  return (
    <div
      className={cn(
        "px-3 py-3 rounded-lg border bg-card backdrop-blur-sm min-w-[140px] transition-all duration-200 relative group",
        statusClass,
        selected && "ring-2 ring-ring ring-offset-1"
      )}
      style={{
        borderColor: `hsl(${color})`,
        background: `linear-gradient(to bottom right, hsl(${color} / 0.2), hsl(${color} / 0.05))`,
        boxShadow: `0 0 15px hsl(${color} / 0.3)`
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="w-2.5 h-2.5"
        style={{ backgroundColor: `hsl(${color})` }}
      />

      <Button
        size="icon"
        variant="ghost"
        className="absolute -top-2 -right-2 h-5 w-5 rounded-full bg-destructive text-destructive-foreground opacity-0 group-hover:opacity-100 hover:bg-destructive/90 shadow-sm transition-all z-10"
        onClick={(e) => {
          e.stopPropagation();
          removeNode(id);
        }}
      >
        <X className="w-3 h-3" />
      </Button>

      <div className="flex items-start gap-3 mb-2">
        <div className="p-2 rounded-md flex-shrink-0">
          <IconComponent
            className="w-4 h-4"
            style={{ color: `hsl(${color})` }}
          />
        </div>
        <div className="flex flex-col min-w-0">
          <span className="text-xs text-muted-foreground font-medium uppercase tracking-wider">
            {config.label}
          </span>
          <span className="text-sm font-bold truncate text-foreground">
            {data?.label || 'Untitled'}
          </span>
        </div>
      </div>

      <div className="space-y-1.5">
        {data?.condition && (
          <div className="flex items-center justify-between text-[10px] bg-muted/50 p-1.5 rounded">
            <span className="text-muted-foreground">Condition</span>
            <Badge variant="outline" className="text-[9px] h-4 px-1 font-mono">
              {data.condition}
            </Badge>
          </div>
        )}

        {data?.controlType === 'loop' && (data.loopCount !== undefined || data.maxIterations) && (
          <div className="flex items-center justify-between text-[10px] bg-muted/50 p-1.5 rounded">
            <span className="text-muted-foreground">Iteration</span>
            <Badge variant="secondary" className="text-[9px] h-4 px-1 font-mono bg-blue-500/10 text-blue-600">
              {data.loopCount ?? 0} / {data.maxIterations ?? '\u221E'}
            </Badge>
          </div>
        )}

        {data?.status && data.status !== 'idle' && (
          <div className="flex justify-center">
            <Badge
              variant={data.status === 'waiting' ? 'destructive' : 'default'}
              className="text-[9px] h-4 px-1.5"
            >
              {data.status === 'waiting' ? 'Waiting' :
               data.status === 'active' ? 'Active' :
               data.status === 'completed' ? 'Done' : data.status}
            </Badge>
          </div>
        )}
      </div>

      {hasMultipleHandles ? (
        <>
          <div className="absolute -right-1 top-[30%] -translate-y-1/2 translate-x-3 pointer-events-none">
            <span className="text-[9px] font-mono text-muted-foreground">T</span>
          </div>
          <Handle
            type="source"
            position={Position.Right}
            id="true"
            className="w-2.5 h-2.5"
            style={{ backgroundColor: `hsl(${color})`, top: '30%' }}
          />
          <div className="absolute -right-1 top-[70%] -translate-y-1/2 translate-x-3 pointer-events-none">
            <span className="text-[9px] font-mono text-muted-foreground">F</span>
          </div>
          <Handle
            type="source"
            position={Position.Right}
            id="false"
            className="w-2.5 h-2.5"
            style={{ backgroundColor: `hsl(${color})`, top: '70%' }}
          />
        </>
      ) : (
        <Handle
          type="source"
          position={Position.Right}
          className="w-2.5 h-2.5"
          style={{ backgroundColor: `hsl(${color})` }}
        />
      )}
    </div>
  );
};

export const ControlNode = memo(ControlNodeInner, (prev, next) => {
  if (prev.selected !== next.selected) return false;
  if (prev.id !== next.id) return false;
  const pd = prev.data;
  const nd = next.data;
  return (
    pd.label === nd.label &&
    pd.controlType === nd.controlType &&
    pd.condition === nd.condition &&
    pd.status === nd.status &&
    pd.loopCount === nd.loopCount &&
    pd.maxIterations === nd.maxIterations
  );
});
