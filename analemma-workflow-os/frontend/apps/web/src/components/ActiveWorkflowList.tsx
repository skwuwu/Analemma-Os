import React from 'react';
import { CardContent } from '@/components/ui/card';
import { MotionCard } from '@/components/ui/motion-card';
import NumberTicker from '@/components/ui/number-ticker';
import { Progress } from '@/components/ui/progress';
import { Activity, FileJson, X, Clock } from 'lucide-react';
import StatusBadge from '@/components/StatusBadge';
import WorkflowTimer from '@/components/WorkflowTimer';
import { formatTimestamp } from '@/lib/utils';
import type { NotificationItem } from '@/lib/types';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';

interface Props {
  groupedActiveWorkflows: NotificationItem[];
  selectedExecutionId: string | null;
  onSelect: (id: string | null) => void;
  isLoadingSaved: boolean;
  onRemove?: (id: string) => void;
  compact?: boolean;
}

// 개별 아이템을 별도 컴포넌트로 분리하여 리렌더링 최적화
const ActiveWorkflowItem = React.memo(({
  workflow,
  isSelected,
  onSelect,
  onRemove,
  compact
}: {
  workflow: NotificationItem;
  isSelected: boolean;
  onSelect: (id: string | null) => void;
  onRemove?: (id: string) => void;
  compact: boolean;
}) => {
  const payload = workflow.payload || {};
  const current = payload.current_segment || 0;
  const total = payload.total_segments || 1;
  const rawProgress = Math.min(Math.round((current / Math.max(1, total)) * 100), 100);

  const executionId = payload.execution_id || workflow.execution_id || workflow.id;
  const isWorkflowRunning = ['RUNNING', 'STARTED', 'PAUSED_FOR_HITP'].includes(payload.status || workflow.status || '');
  const workflowStart = (payload.start_time || workflow.start_time) as number | undefined;

  // 컴팩트 모드 (Popover/Sidebar 용)
  if (compact) {
    return (
      <MotionCard
        isActive={isWorkflowRunning}
        className={`cursor-pointer transition-all border-none shadow-none hover:bg-accent ${isSelected ? 'bg-primary/5' : ''}`}
        onClick={() => onSelect(executionId)}
      >
        <CardContent className="p-2 space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 truncate flex-1 min-w-0">
              <Activity className={`w-3 h-3 shrink-0 ${isWorkflowRunning ? 'text-green-500' : 'text-muted-foreground'}`} />
              <span className="text-xs font-medium truncate">
                {payload.workflow_alias || payload.workflow_name || 'Untitled'}
              </span>
            </div>
            <StatusBadge status={payload.status || workflow.status} className="h-4 text-[9px] px-1 shrink-0" />
          </div>
          {total > 1 && <Progress value={rawProgress} className="h-1" />}
        </CardContent>
      </MotionCard>
    );
  }

  // 전체 모드 (Monitor Page 용)
  return (
    <MotionCard
      isActive={isWorkflowRunning}
      className={`cursor-pointer border-l-4 ${isWorkflowRunning ? 'border-l-green-500' : 'border-l-muted'} ${isSelected ? 'bg-primary/5 ring-1 ring-primary/20' : ''}`}
      onClick={() => onSelect(executionId)}
    >
      <CardContent className="p-4 space-y-3">
        <div className="flex justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-muted rounded-lg">
              <FileJson className="w-4 h-4 text-primary" />
            </div>
            <div>
              <div className="text-sm font-semibold truncate max-w-[200px]">
                {payload.workflow_alias || payload.workflowId || 'Workflow Execution'}
              </div>
              <div className="text-[10px] text-muted-foreground flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {workflowStart ? <WorkflowTimer startTime={workflowStart} status={payload.status || workflow.status} /> : 'Waiting...'}
              </div>
            </div>
          </div>
          <StatusBadge status={payload.status || workflow.status} />
        </div>

        {total > 1 && (
          <div className="space-y-1.5">
            <div className="flex justify-between text-[11px]">
              <span className="text-muted-foreground">{payload.current_step_label || `Step ${current + 1} of ${total}`}</span>
              <span className="font-mono font-medium"><NumberTicker value={rawProgress} />%</span>
            </div>
            <Progress value={rawProgress} className="h-1.5" />
          </div>
        )}

        <div className="flex items-center justify-between text-[10px] text-muted-foreground pt-1">
          <span>Updated: {formatTimestamp(payload.last_update_time ? payload.last_update_time * 1000 : workflow.receivedAt)}</span>
          {onRemove && !isWorkflowRunning && (
            <button
              onClick={(e) => { e.stopPropagation(); onRemove(executionId); }}
              className="p-1 hover:bg-destructive/10 hover:text-destructive rounded-md transition-colors"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
      </CardContent>
    </MotionCard>
  );
});

// 메인 리스트 컴포넌트
const ActiveWorkflowList: React.FC<Props> = ({
  groupedActiveWorkflows,
  selectedExecutionId,
  onSelect,
  isLoadingSaved,
  onRemove,
  compact = false,
}) => {
  if (isLoadingSaved) {
    return (
      <div className={`space-y-3 ${compact ? 'p-2' : 'p-4'}`}>
        {[1, 2, 3].map((i) => <Skeleton key={i} className={compact ? 'h-12' : 'h-28'} />)}
      </div>
    );
  }

  if (groupedActiveWorkflows.length === 0) {
    return <EmptyState icon={Activity} title="No Active Workflows" description="실행 중인 작업이 없습니다." compact={compact} />;
  }

  return (
    <div className={`space-y-3 ${compact ? 'p-2' : 'p-4'}`}>
      {groupedActiveWorkflows.map((workflow) => (
        <ActiveWorkflowItem
          key={workflow.id}
          workflow={workflow}
          isSelected={selectedExecutionId === (workflow.payload?.execution_id || workflow.execution_id)}
          onSelect={onSelect}
          onRemove={onRemove}
          compact={compact}
        />
      ))}
    </div>
  );
};

export default React.memo(ActiveWorkflowList);
