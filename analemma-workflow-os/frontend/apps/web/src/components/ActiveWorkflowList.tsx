import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { MotionCard } from '@/components/ui/motion-card';
import NumberTicker from '@/components/ui/number-ticker';
import { Progress } from '@/components/ui/progress';
import { Activity, FileJson, X } from 'lucide-react';
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

// 렌더링과 무관한 헬퍼 함수
const formatTimeRemaining = (seconds?: number): string => {
  if (!seconds || seconds <= 0) return '';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m`;
  return '<1m';
};

const ActiveWorkflowList: React.FC<Props> = ({
  groupedActiveWorkflows,
  selectedExecutionId,
  onSelect,
  isLoadingSaved,
  onRemove,
  compact = false,
}) => {
  // Loading state with Skeleton
  if (isLoadingSaved) {
    return (
      <div className={`space-y-3 ${compact ? 'p-2' : 'p-4'}`}>
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className={`${compact ? 'h-16' : 'h-24'} w-full rounded-lg`} />
        ))}
      </div>
    );
  }

  if (groupedActiveWorkflows.length === 0) {
    return (
      <EmptyState
        icon={Activity}
        title="활성 워크플로우 없음"
        description="워크플로우를 실행하면 여기에 표시됩니다."
        compact={compact}
      />
    );
  }

  return (
    <div className={`space-y-3 ${compact ? 'p-2' : 'p-4'}`}>
      {groupedActiveWorkflows.map((workflow) => {
        // NOTE: In a real "list" scenario where each item needs independent smoothing,
        // we'd ideally extract this item into a separate component that calls useSmoothTaskUpdates.
        // For now, as optimization, we'll apply NumberTicker to the progress for visual smoothness.

        const payload = workflow.payload || {};
        const current = payload.current_segment || 0;
        const total = payload.total_segments || 1;
        const rawProgress = Math.round((current / Math.max(1, total)) * 100);
        const isSelected = selectedExecutionId === (payload.execution_id || workflow.execution_id);
        const workflowStart = (payload.start_time || workflow.start_time) as number | undefined;
        const isWorkflowRunning = (payload.status || workflow.status) === 'RUNNING';

        // Compact Mode Rendering
        if (compact) {
          return (
            <MotionCard
              key={workflow.id}
              isActive={isWorkflowRunning} // Pulse only when running
              className={`cursor-pointer transition-colors shadow-sm ${isSelected ? 'bg-primary/5' : 'hover:bg-accent'}`}
              onClick={() => { onSelect(payload.execution_id || workflow.execution_id); }}
            >
              <CardContent className="p-3 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0 flex-1">
                    <div className={`p-1.5 rounded-md shrink-0 ${isWorkflowRunning ? 'bg-green-100 text-green-700' : 'bg-muted text-muted-foreground'}`}>
                      <Activity className="w-3.5 h-3.5" />
                    </div>
                    <div className="font-medium text-sm truncate">
                      {payload.workflow_alias || payload.workflow_name || 'Workflow'}
                    </div>
                  </div>
                  <StatusBadge status={payload.status || workflow.status} className="text-[10px] px-1.5 py-0 h-5" />
                </div>

                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <div className="flex items-center gap-2">
                    {workflowStart && (
                      <WorkflowTimer startTime={workflowStart} status={payload.status || workflow.status} />
                    )}
                  </div>
                  {total > 1 && (
                    <div className="flex items-center gap-0.5">
                      <NumberTicker value={rawProgress} />
                      <span>%</span>
                    </div>
                  )}
                </div>

                {total > 1 && (
                  <Progress value={rawProgress} className="h-1" />
                )}
              </CardContent>
            </MotionCard>
          );
        }

        return (
          <MotionCard
            key={workflow.id}
            isActive={isWorkflowRunning} // Pulse when running
            className={`cursor-pointer transition-colors ${isSelected ? 'bg-primary/5' : 'hover:bg-accent'}`}
            onClick={() => { onSelect(payload.execution_id || workflow.execution_id); }}
          >
            <CardContent className="p-4 space-y-2">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <div className="bg-muted p-2 rounded-md shrink-0">
                    <FileJson className="w-4 h-4 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm truncate">
                      {payload.workflow_alias || payload.workflowId || payload.workflow_name || workflow.workflow_name || 'Workflow'}
                    </div>

                  </div>
                </div>
                <div className="shrink-0">
                  <StatusBadge status={payload.status || workflow.status} />
                </div>
              </div>

              {workflowStart && (
                <div className="flex items-center gap-2 px-1">
                  <WorkflowTimer startTime={workflowStart} status={payload.status || workflow.status} className={isWorkflowRunning ? 'text-green-600' : 'text-orange-600'} />
                </div>
              )}

              {total && total > 1 && (
                <div className="space-y-1">
                  <div className="flex justify-between text-xs">
                    <span>{payload.current_step_label || `Step ${(payload.current_segment ?? 0) + 1}/${total}`}</span>
                    <div className="flex items-center gap-0.5">
                      <NumberTicker value={rawProgress} />
                      <span>%</span>
                    </div>
                  </div>
                  <Progress value={rawProgress} className={`h-2 ${isWorkflowRunning ? 'animate-pulse' : ''}`} />
                  {payload.estimated_remaining_seconds && (
                    <div className="text-xs text-muted-foreground">
                      {payload.estimated_remaining_seconds ? formatTimeRemaining(payload.estimated_remaining_seconds) : null}
                    </div>
                  )}
                </div>
              )}

              <div className="flex justify-end pt-1 items-center gap-2">
                <div className="text-[10px] text-muted-foreground flex items-center gap-1 flex-1">
                  <Activity className="w-3 h-3" />
                  Updated: {formatTimestamp((payload.last_update_time ? payload.last_update_time * 1000 : workflow.receivedAt) || Date.now())}
                </div>
                {onRemove && (['COMPLETE', 'FAILED', 'TIMED_OUT'].includes(payload.status || workflow.status || '') || !isWorkflowRunning) && (
                  <button
                    className="text-muted-foreground hover:text-foreground p-1 rounded-full hover:bg-muted transition-colors"
                    onClick={(e) => {
                      e.stopPropagation();
                      onRemove(payload.execution_id || workflow.execution_id || workflow.id);
                    }}
                    title="Dismiss"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </CardContent>
          </MotionCard>
        );
      })}
    </div>
  );
};

export default React.memo(ActiveWorkflowList);
