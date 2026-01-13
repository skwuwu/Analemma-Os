import React from 'react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Card, CardContent } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { X, Activity } from 'lucide-react';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog';
import StatusBadge from '@/components/StatusBadge';
import JsonViewer from '@/components/JsonViewer';
import { safeParseJson } from '@/lib/utils';
import { TimelineItem } from '@/components/TimelineItem';
import { DetailedTimelineItem } from '@/components/DetailedTimelineItem';
import { NotificationItem } from '@/lib/types';

interface HistoryWorkflowDetailProps {
    executionSummary: any;
    selectedWorkflowTimeline?: NotificationItem[];
    deleteExecution?: (executionArn: string) => Promise<void> | void;
    onClose: () => void;
    onShowFullHistory: () => void;
}

export const HistoryWorkflowDetail: React.FC<HistoryWorkflowDetailProps> = ({
    executionSummary,
    selectedWorkflowTimeline = [],
    deleteExecution,
    onClose,
    onShowFullHistory,
}) => {
    const [showDetails, setShowDetails] = React.useState(false);

    if (!executionSummary) return null;

    const exec = executionSummary;
    const execId = exec.executionArn || exec.execution_id;

    // Extract state durations if available
    const stateDurations = exec.step_function_state?.state_durations || {};
    const hasDurations = Object.keys(stateDurations).length > 0;

    // Extract error info if failed
    const isFailed = exec.status === 'FAILED';
    const errorInfo = exec.error || exec.cause || (exec.step_function_state as any)?.error;

    return (
        <div className="flex flex-col h-full">
            <div className="p-6 border-b">
                <div className="flex justify-between items-start mb-4">
                    <div>
                        <h2 className="text-2xl font-semibold">{exec.name || exec.executionArn || 'Execution'}</h2>
                        <p className="text-sm text-muted-foreground mt-1">
                            Started: {exec.created_at ? new Date(Number(exec.created_at)).toLocaleString('ko-KR') : '—'}
                        </p>
                        {exec.stopDate && (
                            <p className="text-sm text-muted-foreground">
                                Finished: {new Date(exec.stopDate).toLocaleString('ko-KR')}
                            </p>
                        )}
                    </div>
                    <Button variant="ghost" size="sm" onClick={onClose}>
                        <X className="w-4 h-4" />
                    </Button>
                </div>

                {/* Error Alert for FAILED status */}
                {isFailed && errorInfo && (
                    <div className="mb-4 bg-red-50 border border-red-200 rounded-md p-4 text-red-800 flex items-start gap-3">
                        <Activity className="w-5 h-5 mt-0.5 shrink-0 text-red-600" />
                        <div className="flex-1 overflow-hidden">
                            <h3 className="font-semibold mb-1">Execution Failed</h3>
                            <div className="text-sm font-mono whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
                                {typeof errorInfo === 'string' ? errorInfo : JSON.stringify(errorInfo, null, 2)}
                            </div>
                        </div>
                    </div>
                )}

                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <StatusBadge status={exec.status} />
                        <div className="text-sm text-muted-foreground">{execId?.split(':').pop()}</div>
                    </div>
                    <div className="flex items-center gap-2">
                        <Button
                            size="sm"
                            variant={showDetails ? "secondary" : "outline"}
                            onClick={() => {
                                onShowFullHistory();
                                setShowDetails(!showDetails);
                            }}
                        >
                            {showDetails ? "Hide Detailed Logs" : "Show Detailed Logs"}
                        </Button>
                        {deleteExecution && (
                            <AlertDialog>
                                <AlertDialogTrigger asChild>
                                    <Button variant="destructive" size="sm">Delete Execution</Button>
                                </AlertDialogTrigger>
                                <AlertDialogContent>
                                    <AlertDialogHeader>
                                        <AlertDialogTitle>Delete Execution</AlertDialogTitle>
                                        <AlertDialogDescription>
                                            Are you sure you want to delete this execution? This action cannot be undone.
                                        </AlertDialogDescription>
                                    </AlertDialogHeader>
                                    <AlertDialogFooter>
                                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                                        <AlertDialogAction onClick={() => {
                                            try {
                                                if (execId && deleteExecution) {
                                                    void Promise.resolve(deleteExecution(execId));
                                                }
                                            } catch (e) {
                                                console.error('Delete execution failed', e);
                                            }
                                        }}>
                                            Delete
                                        </AlertDialogAction>
                                    </AlertDialogFooter>
                                </AlertDialogContent>
                            </AlertDialog>
                        )}
                    </div>
                </div>
            </div>

            <ScrollArea className="flex-1 p-6">
                <div className="space-y-6 max-w-3xl">
                    {/* Timeline Section */}
                    {selectedWorkflowTimeline && selectedWorkflowTimeline.length > 0 && (
                        <div>
                            <h3 className="font-semibold mb-3 flex items-center gap-2">
                                <Activity className="w-4 h-4" /> Execution Timeline
                            </h3>
                            <div className="ml-2 border-l-2 pl-4 border-muted space-y-4">
                                {selectedWorkflowTimeline.map((event, idx) => (
                                    showDetails ? (
                                        <DetailedTimelineItem
                                            key={event.id || idx}
                                            event={event}
                                            isLast={idx === selectedWorkflowTimeline.length - 1}
                                        />
                                    ) : (
                                        <TimelineItem
                                            key={event.id || idx}
                                            event={event}
                                            isLast={idx === selectedWorkflowTimeline.length - 1}
                                        />
                                    )
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Initial State Section */}
                    {exec.step_function_state?.input && (
                        <div>
                            <h3 className="font-semibold mb-3">Initial State</h3>
                            <div className="border rounded p-4 bg-muted/30 max-h-60 overflow-y-auto">
                                <JsonViewer src={exec.step_function_state.input} theme="dark" />
                            </div>
                        </div>
                    )}

                    {/* Result / Output Section */}
                    <div>
                        <h3 className="font-semibold mb-3">Final Output</h3>
                        <div className="border rounded p-4 bg-muted/30 max-h-96 overflow-y-auto">
                            {exec.final_result || exec.output ? (
                                <JsonViewer src={safeParseJson(exec.final_result || exec.output)} theme="dark" />
                            ) : (
                                <div className="text-sm text-muted-foreground">No output data available.</div>
                            )}
                        </div>
                    </div>

                    {/* State Durations Section */}
                    {hasDurations && (
                        <div>
                            <h3 className="font-semibold mb-3">Step Durations</h3>
                            <Card>
                                <CardContent className="p-4">
                                    <div className="space-y-2">
                                        {Object.entries(stateDurations).map(([state, duration]) => (
                                            <div key={state} className="flex justify-between text-sm">
                                                <span className="text-muted-foreground">{state}:</span>
                                                <span className="font-mono">{Math.round(Number(duration))}s</span>
                                            </div>
                                        ))}
                                    </div>
                                </CardContent>
                            </Card>
                        </div>
                    )}

                    {/* Last State Info - Only show if NOT succeeded/complete */}
                    {exec.step_function_state && !['SUCCEEDED', 'COMPLETE'].includes(exec.status) && (
                        <div>
                            <h3 className="font-semibold mb-3">Last Known State</h3>
                            <Card>
                                <CardContent className="p-4 space-y-2 text-sm">
                                    <div className="flex justify-between">
                                        <span className="text-muted-foreground">Current Segment:</span>
                                        <span>{exec.step_function_state.current_segment} / {exec.step_function_state.total_segments}</span>
                                    </div>
                                    <Separator />
                                    <div className="flex justify-between">
                                        <span className="text-muted-foreground">Last Update:</span>
                                        <span>{exec.updated_at ? new Date(exec.updated_at).toLocaleString('ko-KR') : '—'}</span>
                                    </div>
                                </CardContent>
                            </Card>
                        </div>
                    )}
                </div>
            </ScrollArea>
        </div>
    );
};
