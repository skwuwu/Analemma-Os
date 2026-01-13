import React, { useState } from 'react';
import { CheckCircle, Circle, Clock, Loader2, AlertCircle, ChevronDown, ChevronRight, Terminal, Cpu, AlertTriangle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import JsonViewer from '@/components/JsonViewer';
import { NotificationItem } from '@/lib/types';
import { formatDistanceToNow } from 'date-fns';
import { ko } from 'date-fns/locale';
import { useTheme } from 'next-themes';

interface DetailedTimelineItemProps {
    event: NotificationItem;
    isLast: boolean;
}

export const DetailedTimelineItem: React.FC<DetailedTimelineItemProps> = ({ event, isLast }) => {
    const [isExpanded, setIsExpanded] = useState(false);
    const { theme } = useTheme();
    const { status, timestamp, current_step_label, message, usage, error, details, node_id, name, type } = event.payload || event;

    const date = new Date(timestamp ? (timestamp < 10000000000 ? timestamp * 1000 : timestamp) : Date.now());
    const relativeTime = formatDistanceToNow(date, { addSuffix: true, locale: ko });

    let Icon = Circle;
    let colorClass = 'text-gray-400';
    let borderClass = 'border-l-gray-200';
    let bgClass = 'bg-gray-50';

    if (status === 'RUNNING' || status === 'STARTED') {
        Icon = Loader2;
        colorClass = 'text-blue-500';
        borderClass = 'border-l-blue-200';
        bgClass = 'bg-blue-50/50';
    } else if (status === 'SUCCEEDED' || status === 'COMPLETED' || status === 'COMPLETE') {
        Icon = CheckCircle;
        colorClass = 'text-green-500';
        borderClass = 'border-l-green-200';
        bgClass = 'bg-green-50/50';
    } else if (status === 'FAILED' || status === 'ABORTED') {
        Icon = AlertCircle;
        colorClass = 'text-red-500';
        borderClass = 'border-l-red-200';
        bgClass = 'bg-red-50/50';
    } else if (status === 'PAUSED_FOR_HITP') {
        Icon = Clock;
        colorClass = 'text-orange-500';
        borderClass = 'border-l-orange-200';
        bgClass = 'bg-orange-50/50';
    }

    // Type-specific icons
    let TypeIcon: React.ElementType = ActivityIcon;
    if (type === 'ai_thought') TypeIcon = Cpu;
    else if (type === 'tool') TypeIcon = Terminal;

    return (
        <div className={`relative pl-8 pb-8 ${isLast ? '' : borderClass} border-l-2 last:border-l-0`}>
            <div className={`absolute left-[-9px] top-0 bg-background p-1 rounded-full border ${isLast && status === 'RUNNING' ? 'animate-pulse border-blue-400' : 'border-gray-200'}`}>
                <Icon className={`w-4 h-4 ${colorClass} ${status === 'RUNNING' ? 'animate-spin' : ''}`} />
            </div>

            <Card className={`mb-2 overflow-hidden transition-all ${isExpanded ? 'ring-1 ring-primary/20' : ''}`}>
                <div
                    className={`p-3 flex items-start gap-3 cursor-pointer hover:bg-muted/50 ${bgClass}`}
                    onClick={() => setIsExpanded(!isExpanded)}
                >
                    <div className="mt-1">
                        <TypeIcon className="w-4 h-4 text-muted-foreground" />
                    </div>

                    <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between mb-1">
                            <div className="flex items-center gap-2">
                                <span className="font-medium text-sm truncate">
                                    {name || current_step_label || node_id || 'Unknown Step'}
                                </span>
                                {node_id && <code className="text-[10px] bg-muted px-1 rounded text-muted-foreground">{node_id}</code>}
                            </div>
                            <span className="text-xs text-muted-foreground whitespace-nowrap" title={date.toLocaleString()}>
                                {relativeTime}
                            </span>
                        </div>

                        {message && <p className="text-sm text-muted-foreground line-clamp-2">{message}</p>}

                        {/* Summary Badges */}
                        <div className="flex flex-wrap gap-2 mt-2">
                            {usage && (
                                <Badge variant="secondary" className="text-[10px] h-5">
                                    {usage.total_tokens || 0} tokens
                                </Badge>
                            )}
                            {error && (
                                <Badge variant="destructive" className="text-[10px] h-5">
                                    Error
                                </Badge>
                            )}
                        </div>
                    </div>

                    <Button variant="ghost" size="icon" className="h-6 w-6 shrink-0">
                        {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                    </Button>
                </div>

                {/* Expanded Details with Animation */}
                <div style={{ maxHeight: isExpanded ? '1000px' : '0', opacity: isExpanded ? 1 : 0, transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)', overflow: 'hidden' }}>
                    <CardContent className="p-3 bg-card border-t text-sm space-y-3">
                        {/* Error Section */}
                        {error && (
                            <div className="bg-red-50 p-3 rounded-md border border-red-100 text-red-800">
                                <div className="flex items-center gap-2 font-semibold mb-1">
                                    <AlertTriangle className="w-4 h-4" />
                                    Execution Failed
                                </div>
                                <div className="text-xs font-mono whitespace-pre-wrap max-h-60 overflow-y-auto">
                                    {typeof error === 'string' ? error : JSON.stringify(error, null, 2)}
                                </div>
                            </div>
                        )}

                        {/* Usage Section */}
                        {usage && (
                            <div className="grid grid-cols-3 gap-2 bg-muted/30 p-2 rounded text-xs">
                                <div className="text-center">
                                    <div className="text-muted-foreground">Prompt</div>
                                    <div className="font-mono">{usage.prompt_tokens || 0}</div>
                                </div>
                                <div className="text-center">
                                    <div className="text-muted-foreground">Completion</div>
                                    <div className="font-mono">{usage.completion_tokens || 0}</div>
                                </div>
                                <div className="text-center font-semibold">
                                    <div className="text-muted-foreground">Total</div>
                                    <div className="font-mono">{usage.total_tokens || 0}</div>
                                </div>
                            </div>
                        )}

                        {/* Details (Input/Output) */}
                        {details && (
                            <div className="space-y-2">
                                {details.prompts && (
                                    <div>
                                        <div className="text-xs font-semibold text-muted-foreground mb-1">Prompts</div>
                                        <div className="bg-muted/50 rounded p-2 overflow-x-auto max-h-60">
                                            <JsonViewer src={details.prompts} collapsed={true} theme={theme} />
                                        </div>
                                    </div>
                                )}
                                {details.input && (
                                    <div>
                                        <div className="text-xs font-semibold text-muted-foreground mb-1">Input</div>
                                        <div className="bg-muted/50 rounded p-2 overflow-x-auto max-h-60">
                                            <pre className="text-xs font-mono whitespace-pre-wrap">{typeof details.input === 'string' ? details.input : JSON.stringify(details.input, null, 2)}</pre>
                                        </div>
                                    </div>
                                )}
                                {details.output && (
                                    <div>
                                        <div className="text-xs font-semibold text-muted-foreground mb-1">Output</div>
                                        <div className="bg-muted/50 rounded p-2 overflow-x-auto max-h-60">
                                            <pre className="text-xs font-mono whitespace-pre-wrap">{typeof details.output === 'string' ? details.output : JSON.stringify(details.output, null, 2)}</pre>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Raw Data Fallback */}
                        {!usage && !error && !details && (
                            <div className="text-xs text-muted-foreground italic">
                                No detailed information available for this step.
                            </div>
                        )}
                    </CardContent>
                </div>
            </Card>
        </div>
    );
};

function ActivityIcon(props: any) {
    return (
        <svg
            {...props}
            xmlns="http://www.w3.org/2000/svg"
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
        >
            <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
        </svg>
    )
}
