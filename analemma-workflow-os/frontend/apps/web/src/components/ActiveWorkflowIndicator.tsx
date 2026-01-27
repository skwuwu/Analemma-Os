import React, { useState, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Activity, ChevronDown, AlertCircle } from 'lucide-react';
import { useNotifications } from '@/hooks/useNotifications';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import ActiveWorkflowList from '@/components/ActiveWorkflowList';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn, normalizeEventTs } from '@/lib/utils';

export const ActiveWorkflowIndicator: React.FC = () => {
    const navigate = useNavigate();
    const { notifications } = useNotifications();
    const [isOpen, setIsOpen] = useState(false);

    // Filter running/started workflows and memoize for performance
    const activeWorkflows = useMemo(() => {
        return (notifications || [])
            .filter(e => ['RUNNING', 'STARTED', 'PAUSED_FOR_HITP'].includes(e.status))
            .map(e => {
                const sfs = (e.step_function_state as any) || {};
                return {
                    ...e,
                    payload: {
                        execution_id: e.executionArn || e.execution_id,
                        status: e.status,
                        start_time: normalizeEventTs(e.startDate || e.start_time),
                        workflowId: e.workflowId,
                        workflow_alias: e.workflow_alias,
                        workflow_name: e.workflow_name,
                        current_segment: sfs.current_segment,
                        total_segments: sfs.total_segments,
                        estimated_remaining_seconds: sfs.estimated_remaining_seconds,
                        last_update_time: e.updated_at,
                    }
                };
            });
    }, [notifications]);

    const runningCount = activeWorkflows.length;
    const isRunning = runningCount > 0;
    const hasPausedWorkflow = activeWorkflows.some(w => w.status === 'PAUSED_FOR_HITP');

    const handleSelectWorkflow = useCallback((executionId: string | null) => {
        if (executionId) {
            // Monitor 페이지에서 이 ID를 읽어 자동으로 상세창을 띄우도록 딥링크 활용
            navigate(`/workflows?executionId=${encodeURIComponent(executionId)}`);
            setIsOpen(false);
        }
    }, [navigate]);

    return (
        <Popover open={isOpen} onOpenChange={setIsOpen}>
            <PopoverTrigger asChild>
                <Button
                    variant="ghost"
                    size="sm"
                    className={`h-9 gap-2 ${hasPausedWorkflow
                        ? 'text-orange-600 bg-orange-50 hover:bg-orange-100 hover:text-orange-700'
                        : isRunning ? 'text-primary' : 'text-muted-foreground'
                        }`}
                >
                    {hasPausedWorkflow ? (
                        <AlertCircle className="w-4 h-4 animate-pulse" />
                    ) : (
                        <Activity className={`w-4 h-4 ${isRunning ? 'animate-pulse' : ''}`} />
                    )}
                    <span className="hidden sm:inline">
                        {hasPausedWorkflow ? 'Input Required' : isRunning ? `${runningCount} Running` : 'No Active Workflows'}
                    </span>
                    {isRunning && (
                        <Badge
                            variant="secondary"
                            className={cn(
                                "ml-1 px-1.5 h-5 min-w-5 flex justify-center items-center",
                                hasPausedWorkflow ? "bg-orange-500 text-white" : ""
                            )}
                        >
                            {runningCount}
                        </Badge>
                    )}
                    <ChevronDown className="w-3 h-3 opacity-50" />
                </Button>
            </PopoverTrigger>
            <PopoverContent className="w-[350px] p-0" align="end">
                <div className="p-3 border-b bg-muted/30">
                    <h4 className="text-sm font-semibold flex items-center gap-2">
                        <Activity className="w-4 h-4" />
                        Active Workflows
                    </h4>
                </div>
                <ScrollArea className="h-auto max-h-[60vh] max-w-[350px] overflow-hidden">
                    <ActiveWorkflowList
                        groupedActiveWorkflows={activeWorkflows}
                        selectedExecutionId={null}
                        onSelect={handleSelectWorkflow}
                        isLoadingSaved={false}
                        compact={true}
                    // onRemove is optional, omitting it here prevents accidental dismissal from the mini-list
                    />
                </ScrollArea>
                <div className="p-2 border-t bg-muted/30 flex justify-center">
                    <Button variant="link" size="sm" className="text-xs h-auto py-1" onClick={() => { setIsOpen(false); navigate('/workflows'); }}>
                        View All in Monitor
                    </Button>
                </div>
            </PopoverContent>
        </Popover>
    );
};
