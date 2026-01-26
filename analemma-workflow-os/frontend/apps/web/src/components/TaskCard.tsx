/**
 * Task Card Component
 * 
 * Task Manager의 리스트에 표시되는 카드 형태 UI입니다.
 * 프로그레스 바, 에이전트 아바타, 한 줄 요약을 표시합니다.
 */

import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import {
  Clock,
  Loader2,
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Slash,
  Bot,
  TrendingUp,
  Shield
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { TaskSummary, TaskStatus, InterventionHistory } from '@/lib/types';

interface TaskCardProps {
  task: TaskSummary;
  isSelected?: boolean;
  onClick?: () => void;
}

const StatusIcon: React.FC<{ status: TaskStatus }> = ({ status }) => {
  const iconProps = { className: 'w-4 h-4' };

  switch (status) {
    case 'queued':
      return <Clock {...iconProps} className="w-4 h-4 text-slate-500" />;
    case 'in_progress':
      return <Loader2 {...iconProps} className="w-4 h-4 text-blue-500 animate-spin" />;
    case 'pending_approval':
      return <AlertCircle {...iconProps} className="w-4 h-4 text-amber-500" />;
    case 'completed':
      return <CheckCircle2 {...iconProps} className="w-4 h-4 text-green-500" />;
    case 'failed':
      return <XCircle {...iconProps} className="w-4 h-4 text-red-500" />;
    case 'cancelled':
      return <Slash {...iconProps} className="w-4 h-4 text-gray-400" />;
    default:
      return <Clock {...iconProps} className="w-4 h-4 text-gray-500" />;
  }
};

// 상수를 컴포넌트 외부에 정의하여 매 렌더링마다 재생성 방지
const STATUS_CONFIG: Record<TaskStatus, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' }> = {
  queued: { label: '대기 중', variant: 'secondary' },
  in_progress: { label: '진행 중', variant: 'default' },
  pending_approval: { label: '승인 대기', variant: 'outline' },
  completed: { label: '완료', variant: 'secondary' },
  failed: { label: '실패', variant: 'destructive' },
  cancelled: { label: '취소됨', variant: 'secondary' },
};

const StatusBadge: React.FC<{ status: TaskStatus }> = ({ status }) => {
  const config = STATUS_CONFIG[status] || { label: status, variant: 'secondary' as const };

  return (
    <Badge
      variant={config.variant}
      className={cn(
        'text-xs',
        status === 'pending_approval' && 'border-amber-500 text-amber-600 bg-amber-50',
        status === 'in_progress' && 'bg-blue-500',
        status === 'completed' && 'bg-green-100 text-green-700',
      )}
    >
      {config.label}
    </Badge>
  );
};

// --- HELPERS & SUB-COMPONENTS ---

/**
 * 지표별 색상/아이콘 매핑 헬퍼
 */
const getMetricStyle = (value: number, type: 'confidence' | 'autonomy') => {
  if (value >= 80) return {
    text: 'text-emerald-600 dark:text-emerald-400',
    bg: 'bg-emerald-50/50 dark:bg-emerald-500/10',
    icon: type === 'confidence' ? Shield : TrendingUp
  };
  if (value >= 60) return {
    text: 'text-amber-600 dark:text-amber-400',
    bg: 'bg-amber-50/50 dark:bg-amber-500/10',
    icon: AlertCircle
  };
  return {
    text: 'text-rose-600 dark:text-rose-400',
    bg: 'bg-rose-50/50 dark:bg-rose-500/10',
    icon: AlertTriangle
  };
};

/**
 * 지표 아이템 컴포넌트
 */
const MetricItem: React.FC<{
  label: string;
  value: number;
  style: ReturnType<typeof getMetricStyle>
}> = ({ label, value, style }) => {
  const Icon = style.icon;
  return (
    <div className={cn("flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 transition-all hover:bg-white dark:hover:bg-slate-800 shadow-sm border border-transparent hover:border-slate-100", style.bg)}>
      <Icon className={cn("w-3 h-3", style.text.split(' ')[0].replace('text-', 'text-'))} />
      <div className="flex flex-col items-center">
        <span className={cn("font-black tracking-tighter leading-none", style.text)}>
          {value.toFixed(0)}%
        </span>
        <span className="text-[8px] font-black uppercase tracking-widest text-slate-400 mt-0.5">{label}</span>
      </div>
    </div>
  );
};

export const TaskCard = React.memo<TaskCardProps>(({ task, isSelected, onClick }) => {
  const isRunning = task.status === 'in_progress';
  const needsAttention = task.status === 'pending_approval';

  // 시간 포맷팅
  const formatTime = (isoString?: string | null) => {
    if (!isoString) return '';
    try {
      const date = new Date(isoString);
      return date.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if ((event.key === 'Enter' || event.key === ' ') && onClick) {
      event.preventDefault();
      onClick();
    }
  };

  return (
    <Card
      className={cn(
        'group cursor-pointer transition-all duration-300 rounded-2xl overflow-hidden',
        isSelected ? 'ring-2 ring-primary shadow-xl shadow-primary/10' : 'hover:shadow-lg hover:border-primary/20',
        needsAttention && 'border-amber-300 bg-amber-50/30',
        task.status === 'failed' && 'border-rose-200 bg-rose-50/30',
      )}
      onClick={onClick}
      onKeyDown={handleKeyDown}
      tabIndex={onClick ? 0 : -1}
      role={onClick ? 'button' : undefined}
      aria-label={`${task.agent_name} 작업: ${task.current_step_name || '진행 중'}`}
    >
      <CardContent className="p-5">
        {/* 상단: 에이전트 정보 및 상태 */}
        <div className="flex items-start justify-between mb-4">
          <div className="flex items-center gap-3">
            <Avatar className="w-10 h-10 border-2 border-slate-100 group-hover:border-primary/20 transition-colors">
              {task.agent_avatar ? (
                <AvatarImage src={task.agent_avatar} alt={task.agent_name} />
              ) : null}
              <AvatarFallback className="bg-primary/5">
                <Bot className="w-5 h-5 text-primary" />
              </AvatarFallback>
            </Avatar>
            <div className="space-y-0.5">
              <p className="text-sm font-black tracking-tight text-slate-800 dark:text-slate-100">{task.agent_name}</p>
              {task.workflow_name && (
                <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{task.workflow_name}</p>
              )}
            </div>
          </div>
          <div className="flex flex-col items-end gap-1.5">
            <StatusBadge status={task.status} />
            <StatusIcon status={task.status} />
          </div>
        </div>

        {/* 업무 요약 */}
        <h3 className="text-sm font-black mb-1 line-clamp-1 text-slate-700 dark:text-slate-200">
          {task.execution_alias || task.task_summary || 'MISSION_PROTOCOL_ACTIVE'}
        </h3>

        {/* 현재 상태/생각 */}
        <p className="text-[11px] font-medium text-slate-500 dark:text-slate-400 mb-4 line-clamp-2 min-h-[2.2rem] leading-relaxed italic">
          "{task.current_thought || task.current_step_name || 'Synchronizing with core neural link...'}"
        </p>

        {/* 비즈니스 가치 지표 - 벤토 그리드 레이아웃 */}
        {/* 
          Grid 매핑:
          - execution_alias: center-top (col-span-2, 업무 요약 섹션에서 표시)
          - ETA: right-top
          - confidence: left-bottom  
          - autonomy: center-bottom
          - intervention: right-bottom
        */}
        {(task.estimated_completion_time || task.confidence_score || task.autonomy_rate || task.intervention_history) && (
          <div className="grid grid-cols-3 gap-2 mb-4">
            {/* Row 1: 전체 너비 ETA */}
            {task.estimated_completion_time && (
              <div className="col-span-3 flex items-center justify-between gap-1 text-primary bg-primary/5 rounded-xl px-4 py-2 border border-primary/10 mb-1">
                <div className="flex items-center gap-2">
                  <Clock className="w-3.5 h-3.5 opacity-60" />
                  <span className="text-[9px] font-black uppercase tracking-[0.2em] opacity-60">Estimated T-Minus</span>
                </div>
                <span className="text-sm font-black tracking-tighter">{task.estimated_completion_time}</span>
              </div>
            )}

            {/* Row 2: 3열 그리드 (신뢰도 | 자율도 | 개입이력) */}
            {/* 신뢰도 점수 - Left */}
            <MetricItem
              label="CONF"
              value={task.confidence_score || 0}
              style={getMetricStyle(task.confidence_score || 0, 'confidence')}
            />

            {/* 자율도 - Center */}
            <MetricItem
              label="AUTO"
              value={task.autonomy_rate || 0}
              style={getMetricStyle(task.autonomy_rate || 0, 'autonomy')}
            />

            {/* 개입 이력 - Right */}
            <div className={cn(
              "flex items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 transition-all hover:bg-white dark:hover:bg-slate-800 shadow-sm border border-transparent hover:border-slate-100",
              task.intervention_history && task.intervention_history.negative_count > 0 ? "bg-rose-50/50 dark:bg-rose-500/10" : "bg-slate-50 dark:bg-slate-900"
            )}>
              {task.intervention_history && task.intervention_history.total_count > 0 ? (
                <>
                  <AlertCircle className={cn(
                    'w-3 h-3',
                    task.intervention_history.negative_count > 0 ? 'text-rose-500' : 'text-amber-500'
                  )} />
                  <div className="flex flex-col items-center">
                    <span className={cn(
                      'font-black tracking-tighter leading-none',
                      task.intervention_history.negative_count > 0 ? 'text-rose-600' : 'text-amber-600'
                    )}>
                      {task.intervention_history.total_count}
                    </span>
                    <span className="text-[8px] font-black uppercase tracking-widest text-slate-400 mt-0.5">INTV</span>
                  </div>
                </>
              ) : (
                <>
                  <CheckCircle2 className="w-3 h-3 text-emerald-500" />
                  <div className="flex flex-col items-center">
                    <span className="text-emerald-600 font-black tracking-tighter leading-none">ZERO</span>
                    <span className="text-[8px] font-black uppercase tracking-widest text-slate-400 mt-0.5">SAFE</span>
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* 진행률 바 (진행 중일 때만) */}
        {(isRunning || task.progress_percentage > 0) && (
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{task.current_step_name || '진행 중'}</span>
              <span>{task.progress_percentage}%</span>
            </div>
            <Progress
              value={task.progress_percentage}
              className={cn(
                'h-1.5',
                needsAttention && '[&>div]:bg-amber-500'
              )}
            />
          </div>
        )}

        {/* 에러 메시지 */}
        {task.error_message && (
          <div className="mt-2 p-2 bg-red-50 rounded text-xs text-red-600">
            {task.error_message}
          </div>
        )}

        {/* 하단: 시간 정보 */}
        <div className="flex justify-between items-center mt-3 pt-2 border-t text-xs text-muted-foreground">
          <span>
            {task.started_at && `시작: ${formatTime(task.started_at)}`}
          </span>
          <span>
            {task.updated_at && `업데이트: ${formatTime(task.updated_at)}`}
          </span>
        </div>
      </CardContent>
    </Card>
  );
});

export default TaskCard;
