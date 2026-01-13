import React, { useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Button } from '@/components/ui/button';
import { Clock, AlertCircle, X } from 'lucide-react';
import StatusBadge from '@/components/StatusBadge';
import { useNotifications } from '@/hooks/useNotifications';
import { formatTimestamp } from '@/lib/utils';

interface WorkflowProgressProps {
  className?: string;
}

export const WorkflowProgress: React.FC<WorkflowProgressProps> = ({ className = '' }) => {
  const { notifications, remove } = useNotifications({});

  // activeWorkflows 계산 최적화
  const activeWorkflows = useMemo(() => {
    // 1. 실행 ID(execution_id) 기준으로 최신 상태만 남기기 (중복 제거)
    const latestWorkflowsMap = new Map<string, any>();

    notifications.forEach((n: any) => {
      // execution_id가 없으면 id나 workflow_id를 임시 키로 사용
      const key = n.execution_id || n.workflow_id || n.id;
      if (!key) return;

      const existing = latestWorkflowsMap.get(key);
      // 기존 것이 없거나, 현재 알림이 더 최신이면 교체
      if (!existing || (n.receivedAt || 0) > (existing.receivedAt || 0)) {
        latestWorkflowsMap.set(key, n);
      }
    });

    // 2. 필터링 및 정렬
    return Array.from(latestWorkflowsMap.values())
      .filter((n: any) => {
        const status = n.status ? String(n.status).toUpperCase() : '';

        // [수정] 대기 상태(QUEUED, PENDING)도 실행 중으로 포함
        const runningStatuses = [
          'RUNNING', 'IN_PROGRESS',
          'PAUSED_FOR_HITP', 'PAUSED',
          'QUEUED', 'PENDING', 'STARTING'
        ];
        
        // [수정] 완료 상태 확장 (과거형 포함)
        const finishedStatuses = [
          'COMPLETE', 'COMPLETED',
          'SUCCEEDED', 'SUCCESS',
          'FAILED', 'ERROR',
          'CANCELED', 'TERMINATED'
        ];

        const isActive = runningStatuses.includes(status);
        const isNotFinished = !finishedStatuses.includes(status);

        // 상태값이 아예 없거나(초기화 직후), 실행 중이거나, 완료되지 않은 경우 표시
        return isActive && isNotFinished;
      })
      .sort((a: any, b: any) => (b.receivedAt || 0) - (a.receivedAt || 0)) // 최신순 정렬
      .slice(0, 3); // 최대 3개 표시
  }, [notifications]);

  // Use StatusBadge for consistent status visuals

  const dismissWorkflow = (workflowId: string) => {
    // ID가 없으면 무시
    if(!workflowId) return;
    remove(workflowId);
  };

  const formatEta = (seconds?: number) => {
    if (seconds === undefined || seconds === null) return null;
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return mins > 0 ? `${mins}m ${secs}s left` : `${secs}s left`;
  };

  const formatUnixTimestamp = (timestamp?: number) => {
    if (!timestamp) return null;
    const date = new Date(timestamp * 1000);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const formatStateDurations = (durations?: { [status: string]: number }) => {
    if (!durations || Object.keys(durations).length === 0) return null;
    return Object.entries(durations)
      .map(([status, seconds]) => `${status}: ${seconds.toFixed(1)}s`)
      .join(', ');
  };

  // 렌더링 시작
  if (activeWorkflows.length === 0) {
    const wsUrl = import.meta.env.VITE_WS_URL;
    if (!wsUrl) {
      return (
        <Card className={`w-full max-w-md ${className}`}>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2 text-muted-foreground">
              <AlertCircle className="w-4 h-4 text-orange-500" /> WebSocket Not Connected
            </CardTitle>
          </CardHeader>
        </Card>
      );
    }
    return (
      <Card className={`w-full max-w-md ${className}`}>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm flex items-center gap-2">
            <Clock className="w-4 h-4" /> Active Workflows
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center py-4 text-muted-foreground">
            <div className="text-sm">No active workflows</div>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={`w-full max-w-md ${className}`}>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Clock className="w-4 h-4" />
          Active Workflows ({activeWorkflows.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {activeWorkflows.map((workflow: any) => {
          // 진행률 계산
          const currentStep = (workflow.current_segment ?? 0) + 1;
          const totalSteps = workflow.total_segments && workflow.total_segments > 0 
            ? workflow.total_segments 
            : 1; // 0 방지
          
          // 백엔드에서 progressPercent를 직접 줄 수도 있고, 아니면 계산
          const progressPercent = workflow.progress_percent ?? Math.min(100, Math.round((currentStep / totalSteps) * 100));

          return (
            <div key={workflow.execution_id || workflow.id} className="border rounded-lg p-3 space-y-2">
              {/* Header: Status & Actions */}
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <StatusBadge status={String(workflow.status || '').toUpperCase()} showLabel={false} className="p-0" />
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm truncate">
                      {workflow.workflow_name || 'Processing...'}
                    </div>
                    <div className="text-xs text-muted-foreground truncate font-mono">
                      ID: {workflow.execution_id?.split('-').pop() ?? '...'}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 ml-2">
                  <StatusBadge
                    status={String(workflow.status || '').toUpperCase()}
                    showLabel={true}
                    className="text-[10px] px-1.5 py-0 h-5"
                  />
                  <Button 
                    variant="ghost" 
                    size="sm" 
                    onClick={() => dismissWorkflow(workflow.id)}
                    className="h-6 w-6 p-0 hover:bg-muted"
                  >
                    <X className="w-3 h-3" />
                  </Button>
                </div>
              </div>

              {/* Progress Bar (항상 표시하되 단계가 1개면 심플하게) */}
              <div className="space-y-1.5">
                <div className="flex justify-between text-xs items-end">
                  <span className="text-muted-foreground">
                    {workflow.current_step_label || `Step ${currentStep} of ${totalSteps}`}
                  </span>
                  <div className="flex gap-2">
                    {workflow.estimated_remaining_seconds !== undefined && (
                       <span className="text-muted-foreground">{formatEta(workflow.estimated_remaining_seconds)}</span>
                    )}
                    <span className="font-medium">{progressPercent}%</span>
                  </div>
                </div>
                <Progress value={progressPercent} className="h-1.5" />
              </div>

              {/* Footer Info */}
              <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground pt-1 border-t mt-2">
                {workflow.start_time && (
                   <span>Started: {formatUnixTimestamp(workflow.start_time)}</span>
                )}
                <span>Updated: {formatTimestamp(workflow.receivedAt)}</span>
                
                {/* 메시지가 있으면 표시 */}
                {workflow.message && (
                  <div className="w-full text-foreground/80 italic mt-1">
                    "{workflow.message}"
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
};

export default WorkflowProgress;