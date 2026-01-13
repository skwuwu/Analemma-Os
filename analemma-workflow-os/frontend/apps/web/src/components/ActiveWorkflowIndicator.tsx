import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Activity, ChevronDown } from 'lucide-react';
import { useNotifications } from '@/hooks/useNotifications';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import ActiveWorkflowList from '@/components/ActiveWorkflowList';
import { ScrollArea } from '@/components/ui/scroll-area';

export const ActiveWorkflowIndicator: React.FC = () => {
    const navigate = useNavigate();
    const { notifications } = useNotifications();
    const [isOpen, setIsOpen] = useState(false);

    // Filter running/started workflows
    const activeWorkflows = (notifications || [])
        .filter(e => ['RUNNING', 'STARTED', 'PAUSED_FOR_HITP'].includes(e.status))
        .map(e => ({
            ...e,
            payload: {
                execution_id: e.executionArn || e.execution_id,
                status: e.status,
                start_time: e.startDate ? new Date(e.startDate).getTime() : undefined,
                workflowId: e.workflowId,
                workflow_alias: e.workflow_alias,
                workflow_name: e.workflow_name,
                current_segment: (e.step_function_state as any)?.current_segment,
                total_segments: (e.step_function_state as any)?.total_segments,
                estimated_remaining_seconds: (e.step_function_state as any)?.estimated_remaining_seconds,
                last_update_time: e.updated_at ? e.updated_at : undefined,
            }
        }));

    const runningCount = activeWorkflows.length;
    const isRunning = runningCount > 0;

    const handleSelectWorkflow = (executionId: string | null) => {
        if (executionId) {
            // Navigate to monitor page with this execution selected
            // We can't directly select it via URL params yet (maybe?), but navigating to the page is a start.
            // Ideally, the monitor page should accept a query param ?executionId=...
            // For now, just go to /workflows
            navigate('/workflows');
            setIsOpen(false);
        }
    };

    return (
        <Popover open={isOpen} onOpenChange={setIsOpen}>
            <PopoverTrigger asChild>
                <Button
                    variant="ghost"
                    size="sm"
                    className={`h-9 gap-2 ${isRunning ? 'text-primary' : 'text-muted-foreground'}`}
                >
                    <Activity className={`w-4 h-4 ${isRunning ? 'animate-pulse' : ''}`} />
                    <span className="hidden sm:inline">
                        {isRunning ? `${runningCount} Running` : 'No Active Workflows'}
                    </span>
                    {isRunning && (
                        <Badge variant="secondary" className="ml-1 px-1.5 h-5 min-w-5 flex justify-center items-center">
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
