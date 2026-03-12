import { Handle, Position } from '@xyflow/react';
import { Clock, Webhook, Zap, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { useWorkflowStore } from '@/lib/workflowStore';
import { memo } from 'react';
import { TRIGGER_CONFIG, type TriggerType } from '@/lib/nodeConstants';
import { ExecutionOrderBadge } from './ExecutionOrderBadge';

const ICON_MAP = {
  Clock,
  Webhook,
  Zap
} as const;

interface TriggerNodeProps {
  data: {
    label: string;
    triggerType?: keyof typeof TRIGGER_CONFIG;
    configValue?: string;
    triggerId?: string;
    _executionOrder?: number;
  };
  id: string;
  selected?: boolean;
}

const TriggerNodeInner = ({ data, id, selected }: TriggerNodeProps) => {
  const removeNode = useWorkflowStore(state => state.removeNode);
  const config = TRIGGER_CONFIG[data.triggerType || 'default'] || TRIGGER_CONFIG.default;
  const Icon = ICON_MAP[config.iconName as keyof typeof ICON_MAP] || Zap;

  return (
    <div
      className={cn(
        "px-3 py-3 rounded-lg border bg-card backdrop-blur-sm min-w-[150px] relative group transition-all duration-200",
        selected && "ring-2 ring-ring ring-offset-1"
      )}
      style={{
        borderColor: `hsl(${config.color})`,
        background: `linear-gradient(to bottom right, hsl(${config.color} / 0.15), hsl(${config.color} / 0.05))`,
        boxShadow: `0 0 20px hsl(${config.color} / 0.15)`
      }}
    >
      <ExecutionOrderBadge order={data._executionOrder} />
      <Button
        size="icon"
        variant="ghost"
        className="absolute top-1 right-1 h-6 w-6 rounded-full bg-destructive text-destructive-foreground opacity-0 group-hover:opacity-100 hover:bg-destructive shadow-lg transition-all z-20"
        onClick={(e) => { e.stopPropagation(); removeNode(id); }}
      >
        <X className="w-3 h-3" />
      </Button>

      <div className="flex items-start gap-3">
        <div
          className="p-2 rounded-md flex-shrink-0"
          style={{ backgroundColor: `hsl(${config.color} / 0.15)` }}
        >
          <Icon
            className="w-4 h-4"
            style={{ color: `hsl(${config.color})` }}
          />
        </div>

        <div className="flex flex-col min-w-0">
          <span className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider">
            {config.label}
          </span>
          <span className="text-sm font-bold truncate text-white leading-tight">
            {data.label}
          </span>

          {data.configValue && (
            <Badge
              variant="secondary"
              className="mt-1.5 w-fit max-w-[120px] truncate text-[9px] h-4 px-1.5 font-mono border-0"
              style={{
                backgroundColor: `hsl(${config.color} / 0.1)`,
                color: `hsl(${config.color})`
              }}
            >
              {data.configValue}
            </Badge>
          )}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="w-2.5 h-2.5"
        style={{ backgroundColor: `hsl(${config.color})` }}
      />
    </div>
  );
};

export const TriggerNode = memo(TriggerNodeInner, (prev, next) => {
  if (prev.selected !== next.selected) return false;
  if (prev.id !== next.id) return false;
  const pd = prev.data;
  const nd = next.data;
  return (
    pd.label === nd.label &&
    pd.triggerType === nd.triggerType &&
    pd.configValue === nd.configValue &&
    pd.triggerId === nd.triggerId
  );
});
