import { Handle, Position } from '@xyflow/react';
import { Clock, X, GitBranch, Repeat, User, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

// 컴포넌트 밖으로 설정 객체 분리 (성능 최적화)
const CONTROL_CONFIG = {
  branch: {
    icon: GitBranch,
    color: '142 76% 36%', // 초록색 (HSL 값만)
    label: 'Branch'
  },
  loop: {
    icon: Repeat,
    color: '48 96% 53%', // 노란색 (HSL 값만)
    label: 'Loop'
  },
  wait: {
    icon: Clock,
    color: '25 95% 60%', // 주황색 (HSL 값만)
    label: 'Wait'
  },
  human: {
    icon: User,
    color: '263 70% 60%', // 보라색 (HSL 값만)
    label: 'Human'
  },
  conditional: {
    icon: AlertTriangle,
    color: '0 84% 60%', // 빨간색 (HSL 값만)
    label: 'Conditional'
  },
  default: {
    icon: Clock,
    color: '280 65% 60%', // 기존 색상 (HSL 값만)
    label: 'Control'
  }
} as const;

interface ControlNodeProps {
  data: {
    label: string;
    controlType?: keyof typeof CONTROL_CONFIG;
    condition?: string;      // 제어 조건 (예: "count < 3", "approved", "timeout")
    status?: 'idle' | 'waiting' | 'active' | 'completed';
    loopCount?: number;      // 반복 횟수
    maxIterations?: number;  // 최대 반복 횟수
  };
  id: string;
  onDelete?: (id: string) => void;
  selected?: boolean;
}

export const ControlNode = ({ data, id, onDelete, selected }: ControlNodeProps) => {
  // 방어적 코드: data가 없거나 controlType이 맵에 없을 경우 대비
  const config = CONTROL_CONFIG[data?.controlType || 'default'] || CONTROL_CONFIG.default;

  // 상태에 따른 스타일
  const getStatusStyle = (status?: string) => {
    switch (status) {
      case 'waiting':
        return 'animate-pulse shadow-[0_0_20px_hsl(25_95%_60%/0.5)]';
      case 'active':
        return 'shadow-[0_0_15px_currentColor/0.3]';
      case 'completed':
        return 'opacity-75';
      default:
        return '';
    }
  };

  const IconComponent = config?.icon || Clock;

  // 다중 핸들 지원: branch와 conditional 타입은 true/false 핸들
  const hasMultipleHandles = data?.controlType === 'branch' || data?.controlType === 'conditional';

  return (
    <div
      className={cn(
        "px-3 py-3 rounded-lg border bg-card backdrop-blur-sm min-w-[140px] transition-all duration-200 relative group",
        getStatusStyle(data?.status),
        selected && "ring-2 ring-ring ring-offset-1"
      )}
      style={{
        borderColor: `hsl(${config?.color || '280 65% 60%'})`,
        background: `linear-gradient(to bottom right, hsl(${config?.color || '280 65% 60%'} / 0.2), hsl(${config?.color || '280 65% 60%'} / 0.05))`,
        boxShadow: `0 0 15px hsl(${config?.color || '280 65% 60%'} / 0.3)`
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="w-2.5 h-2.5"
        style={{ backgroundColor: `hsl(${config?.color || '280 65% 60%'})` }}
      />

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

      {/* 헤더: 아이콘 + 타입 라벨 */}
      <div className="flex items-start gap-3 mb-2">
        <div className={cn(
          "p-2 rounded-md flex-shrink-0",
          data?.status === 'waiting' ? "animate-pulse" : ""
        )}>
          <IconComponent
            className="w-4 h-4"
            style={{ color: `hsl(${config?.color || '280 65% 60%'})` }}
          />
        </div>
        <div className="flex flex-col min-w-0">
          <span className="text-xs text-muted-foreground font-medium uppercase tracking-wider">
            {config?.label || 'Control'}
          </span>
          <span className="text-sm font-bold truncate text-foreground">
            {data?.label || 'Untitled'}
          </span>
        </div>
      </div>

      {/* 제어 조건/값 표시 */}
      <div className="space-y-1.5">
        {/* 조건 표시 */}
        {data?.condition && (
          <div className="flex items-center justify-between text-[10px] bg-muted/50 p-1.5 rounded">
            <span className="text-muted-foreground">Condition</span>
            <Badge variant="outline" className="text-[9px] h-4 px-1 font-mono">
              {data.condition}
            </Badge>
          </div>
        )}

        {/* 반복 횟수 표시 (Loop 타입일 때) */}
        {data?.controlType === 'loop' && (data.loopCount !== undefined || data.maxIterations) && (
          <div className="flex items-center justify-between text-[10px] bg-muted/50 p-1.5 rounded">
            <span className="text-muted-foreground">Iteration</span>
            <Badge variant="secondary" className="text-[9px] h-4 px-1 font-mono bg-blue-500/10 text-blue-600">
              {data.loopCount ?? 0} / {data.maxIterations ?? '∞'}
            </Badge>
          </div>
        )}

        {/* 상태 표시 */}
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

      {/* 단일 핸들 또는 다중 핸들 */}
      {hasMultipleHandles ? (
        <>
          {/* True 핸들 */}
          <div className="absolute -right-1 top-[30%] -translate-y-1/2 translate-x-3 pointer-events-none">
            <span className="text-[9px] font-mono text-muted-foreground">T</span>
          </div>
          <Handle
            type="source"
            position={Position.Right}
            id="true"
            className="w-2.5 h-2.5"
            style={{ backgroundColor: `hsl(${config?.color || '280 65% 60%'})`, top: '30%' }}
          />

          {/* False 핸들 */}
          <div className="absolute -right-1 top-[70%] -translate-y-1/2 translate-x-3 pointer-events-none">
            <span className="text-[9px] font-mono text-muted-foreground">F</span>
          </div>
          <Handle
            type="source"
            position={Position.Right}
            id="false"
            className="w-2.5 h-2.5"
            style={{ backgroundColor: `hsl(${config?.color || '280 65% 60%'})`, top: '70%' }}
          />
        </>
      ) : (
        <Handle
          type="source"
          position={Position.Right}
          className="w-2.5 h-2.5"
          style={{ backgroundColor: `hsl(${config?.color || '280 65% 60%'})` }}
        />
      )}
    </div>
  );
};
