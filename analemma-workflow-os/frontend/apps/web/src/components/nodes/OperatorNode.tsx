import { Handle, Position } from '@xyflow/react';
import { Mail, Globe, Calendar, Github, Database, MessageCircle, FolderOpen, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

// 서비스별 색상 및 설정 객체
const OPERATOR_CONFIG = {
  email: {
    icon: Mail,
    color: '6 78% 55%', // 빨간색 (#EA4335)
    label: 'Email'
  },
  browser: {
    icon: Globe,
    color: '25 95% 60%', // 주황색 (기존)
    label: 'Browser'
  },
  calendar: {
    icon: Calendar,
    color: '200 100% 50%', // 파란색
    label: 'Calendar'
  },
  github: {
    icon: Github,
    color: '0 0% 9%', // 검정 (#181717)
    label: 'GitHub'
  },
  database: {
    icon: Database,
    color: '190 100% 28%', // 청록색 (#00758F)
    label: 'Database'
  },
  slack: {
    icon: MessageCircle,
    color: '295 45% 22%', // 보라색 (#4A154B)
    label: 'Slack'
  },
  drive: {
    icon: FolderOpen,
    color: '45 90% 45%', // 진한 황금색 (가독성 개선)
    label: 'Drive'
  },
  default: {
    icon: Mail,
    color: '25 95% 60%', // 주황색 (기본)
    label: 'Integration'
  }
} as const;

interface OperatorNodeProps {
  data: {
    label: string;
    operatorType?: keyof typeof OPERATOR_CONFIG; // 타입 강화
    operatorVariant?: 'custom' | 'official';
    action?: string;        // 구체적인 동작 (예: "Create PR", "Send Email")
    authStatus?: 'connected' | 'disconnected' | 'unknown'; // 인증 상태
    authMessage?: string;   // 인증 상태 메시지 (툴팁용)
  };
  id: string;
  onDelete?: (id: string) => void;
}

export const OperatorNode = ({ data, id, onDelete }: OperatorNodeProps) => {
  const config = OPERATOR_CONFIG[data.operatorType as keyof typeof OPERATOR_CONFIG] || OPERATOR_CONFIG.default;

  const Icon = config.icon;

  // 인증 상태 스타일 매핑
  const getAuthStyle = () => {
    switch (data.authStatus) {
      case 'connected':
        return { color: 'bg-green-500', label: 'Connected', ring: 'ring-green-500/30' };
      case 'disconnected':
        return { color: 'bg-red-500', label: 'Disconnected', ring: 'ring-red-500/30' };
      default:
        return { color: 'bg-gray-400', label: 'Unknown', ring: 'ring-gray-400/30' };
    }
  };

  const authStyle = getAuthStyle();

  return (
    <div
      className="px-3 py-2 rounded-lg border bg-card backdrop-blur-sm shadow-lg min-w-[130px] relative group transition-all duration-200"
      style={{
        borderColor: `hsl(${config.color})`,
        background: `linear-gradient(to bottom right, hsl(${config.color} / 0.1), hsl(${config.color} / 0.02))`,
        boxShadow: `0 4px 12px hsl(${config.color} / 0.15)`
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="w-2.5 h-2.5"
        style={{ backgroundColor: `hsl(${config.color})` }}
      />

      {onDelete && (
        <Button
          size="icon"
          variant="ghost"
          className="absolute -top-2 -right-2 h-5 w-5 rounded-full bg-destructive text-destructive-foreground opacity-0 group-hover:opacity-100 hover:bg-destructive/90 shadow-sm transition-all z-10 scale-90 hover:scale-100"
          onClick={(e) => {
            e.stopPropagation();
            onDelete(id);
          }}
        >
          <X className="w-3 h-3" />
        </Button>
      )}

      <div className="flex items-center gap-3">
        {/* 아이콘 + 상태 표시등 결합 */}
        <div className="relative">
          <div
            className="p-1.5 rounded-md"
            style={{ backgroundColor: `hsl(${config.color} / 0.15)` }}
          >
            <Icon
              className="w-4 h-4"
              style={{ color: `hsl(${config.color})` }}
            />
          </div>

          {/* CSS 기반 Status Dot (툴팁 포함) */}
          <Tooltip>
            <TooltipTrigger asChild>
              <div className={cn(
                "absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full border-2 border-background shadow-sm cursor-help",
                authStyle.color,
                data.authStatus === 'connected' && "animate-pulse" // 연결됨 상태일 때만 은은하게 깜빡임
              )} />
            </TooltipTrigger>
            <TooltipContent side="top" className="text-[10px] px-2 py-1">
              <p>{data.authMessage || authStyle.label}</p>
            </TooltipContent>
          </Tooltip>
        </div>

        <div className="flex-1 min-w-0">
          <div className="text-[10px] text-muted-foreground font-medium flex items-center justify-between">
            {config.label}
            {data.operatorVariant && (
              <Badge variant="outline" className="text-[9px] h-4 px-1 ml-2 font-mono">
                {data.operatorVariant === 'official' ? 'official' : 'custom'}
              </Badge>
            )}
          </div>
          <div className="text-xs font-bold text-foreground truncate leading-tight">
            {data.label}
          </div>

          {/* 액션 표시 */}
          {data.action && (
            <Badge
              variant="outline"
              className="text-[9px] h-4 px-1 mt-1 font-mono border-0"
              style={{
                backgroundColor: `hsl(${config.color} / 0.1)`,
                color: `hsl(${config.color})`
              }}
            >
              {data.action}
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
