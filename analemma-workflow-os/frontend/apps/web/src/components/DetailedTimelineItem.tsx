import React, { useState, useMemo } from 'react';
import {
    CheckCircle,
    Circle,
    Clock,
    Loader2,
    AlertCircle,
    ChevronDown,
    ChevronRight,
    Terminal,
    Cpu,
    AlertTriangle,
    Copy,
    Check,
    Activity
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import JsonViewer from '@/components/JsonViewer';
import { NotificationItem } from '@/lib/types';
import { formatDistanceToNow } from 'date-fns';
import { ko } from 'date-fns/locale';
import { useTheme } from 'next-themes';
import { cn, normalizeEventTs } from '@/lib/utils';
import { toast } from 'sonner';

interface DetailedTimelineItemProps {
    event: NotificationItem;
    isLast: boolean;
}

export const DetailedTimelineItem: React.FC<DetailedTimelineItemProps> = ({ event, isLast }) => {
    const [isExpanded, setIsExpanded] = useState(false);
    const { theme } = useTheme();

    // 1. 데이터 가공 로직 메모이제이션 (성능 및 일관성 최적화)
    const payload = event.payload || event;
    const { status, current_step_label, message, usage, error, details, node_id, name, type } = payload;

    const { relativeTime, dateLabel, Icon, colorClass, borderClass, bgClass } = useMemo(() => {
        const ts = normalizeEventTs(payload);
        const date = new Date(ts);

        // 상태별 메타데이터 매핑
        const config: Record<string, any> = {
            RUNNING: { icon: Loader2, color: 'text-blue-500', bg: 'bg-blue-50/40', border: 'border-l-blue-200' },
            STARTED: { icon: Loader2, color: 'text-blue-500', bg: 'bg-blue-50/40', border: 'border-l-blue-200' },
            COMPLETED: { icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-50/40', border: 'border-l-green-200' },
            COMPLETE: { icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-50/40', border: 'border-l-green-200' },
            SUCCEEDED: { icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-50/40', border: 'border-l-green-200' },
            FAILED: { icon: AlertCircle, color: 'text-red-500', bg: 'bg-red-50/40', border: 'border-l-red-200' },
            ABORTED: { icon: AlertCircle, color: 'text-red-500', bg: 'bg-red-50/40', border: 'border-l-red-200' },
            PAUSED_FOR_HITP: { icon: Clock, color: 'text-orange-500', bg: 'bg-orange-50/40', border: 'border-l-orange-200' },
            DEFAULT: { icon: Circle, color: 'text-slate-400', bg: 'bg-slate-50/30', border: 'border-l-slate-200' }
        };

        const res = config[status] || config.DEFAULT;
        return {
            relativeTime: formatDistanceToNow(date, { addSuffix: true, locale: ko }),
            dateLabel: date.toLocaleString(),
            Icon: res.icon,
            colorClass: res.color,
            bgClass: res.bg,
            borderClass: res.border
        };
    }, [status, payload]);

    // Type-specific icons
    const TypeIcon = useMemo(() => {
        if (type === 'ai_thought') return Cpu;
        if (type === 'tool') return Terminal;
        return Activity;
    }, [type]);

    const handleCopy = (data: any, label: string) => {
        const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
        navigator.clipboard.writeText(text);
        toast.info(`${label} copied to clipboard`);
    };

    return (
        <div className={cn(
            "relative pl-8 pb-8 border-l-2 last:border-l-0 transition-colors",
            isLast ? "border-transparent" : borderClass
        )}>
            {/* 타임라인 노드 아이콘 - 정중앙 배치를 위해 미세 조정 */}
            <div className={cn(
                "absolute left-[-11px] top-0 bg-background p-1 rounded-full border-2 z-10",
                isLast && (status === 'RUNNING' || status === 'STARTED') ? 'border-blue-400 animate-pulse' : 'border-background shadow-sm'
            )}>
                <Icon className={cn(
                    "w-4 h-4",
                    colorClass,
                    (status === 'RUNNING' || status === 'STARTED') && "animate-spin"
                )} />
            </div>

            <Card className={cn(
                "group border transition-all duration-200 overflow-hidden",
                isExpanded ? "shadow-md ring-1 ring-primary/10" : "hover:shadow-sm"
            )}>
                <div
                    className={cn(
                        "p-3 flex items-start gap-3 cursor-pointer",
                        bgClass
                    )}
                    onClick={() => setIsExpanded(!isExpanded)}
                >
                    <div className="p-1.5 bg-background rounded-md shadow-sm mt-0.5">
                        <TypeIcon className="w-3.5 h-3.5 text-slate-500" />
                    </div>

                    <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between mb-1">
                            <div className="flex items-center gap-2 overflow-hidden flex-1">
                                <span className="font-semibold text-sm truncate">
                                    {name || current_step_label || node_id || 'System Operation'}
                                </span>
                                {node_id && (
                                    <Badge variant="outline" className="text-[9px] font-mono py-0 h-4 opacity-70 shrink-0">
                                        {node_id}
                                    </Badge>
                                )}
                            </div>
                            <time className="text-[10px] text-slate-400 font-medium shrink-0 ml-2" title={dateLabel}>
                                {relativeTime}
                            </time>
                        </div>

                        {message && <p className="text-xs text-slate-500 line-clamp-1 italic">{message}</p>}

                        {/* Summary Badges */}
                        <div className="flex flex-wrap gap-2 mt-2">
                            {usage && (
                                <Badge variant="secondary" className="text-[9px] h-4 font-mono">
                                    {usage.total_tokens || 0} tokens
                                </Badge>
                            )}
                            {error && (
                                <Badge variant="destructive" className="text-[9px] h-4">
                                    Error
                                </Badge>
                            )}
                        </div>
                    </div>

                    <div className="shrink-0 pt-1">
                        {isExpanded ? (
                            <ChevronDown className="w-4 h-4 text-primary" />
                        ) : (
                            <ChevronRight className="w-4 h-4 text-slate-300 group-hover:text-slate-500" />
                        )}
                    </div>
                </div>

                {/* Expanded Details with Framer Motion and Lazy Rendering */}
                <AnimatePresence>
                    {isExpanded && (
                        <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: 'auto', opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            transition={{ duration: 0.2, ease: "circOut" }}
                            className="overflow-hidden border-t"
                        >
                            <CardContent className="p-4 bg-card text-sm space-y-4">
                                {/* Error Section */}
                                {error && (
                                    <div className="bg-red-50/50 p-3 rounded-lg border border-red-100 text-red-800">
                                        <div className="flex items-center justify-between font-semibold mb-2">
                                            <div className="flex items-center gap-2">
                                                <AlertTriangle className="w-4 h-4" />
                                                <span>Execution Failed</span>
                                            </div>
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-6 w-6 text-red-700 hover:bg-red-100"
                                                onClick={(e) => { e.stopPropagation(); handleCopy(error, 'Error log'); }}
                                            >
                                                <Copy className="w-3 h-3" />
                                            </Button>
                                        </div>
                                        <div className="text-[11px] font-mono whitespace-pre-wrap max-h-60 overflow-y-auto bg-white/50 p-2 rounded border border-red-100/50">
                                            {typeof error === 'string' ? error : JSON.stringify(error, null, 2)}
                                        </div>
                                    </div>
                                )}

                                {/* Usage Section */}
                                {usage && (
                                    <div className="grid grid-cols-3 gap-3 bg-slate-50 p-3 rounded-lg border border-slate-100 text-[11px]">
                                        <div className="text-center">
                                            <div className="text-slate-400 font-bold mb-1 uppercase tracking-tighter">Prompt</div>
                                            <div className="font-mono text-slate-700">{usage.prompt_tokens || 0}</div>
                                        </div>
                                        <div className="text-center border-x border-slate-200">
                                            <div className="text-slate-400 font-bold mb-1 uppercase tracking-tighter">Completion</div>
                                            <div className="font-mono text-slate-700">{usage.completion_tokens || 0}</div>
                                        </div>
                                        <div className="text-center font-bold">
                                            <div className="text-primary font-bold mb-1 uppercase tracking-tighter">Total</div>
                                            <div className="font-mono text-primary">{usage.total_tokens || 0}</div>
                                        </div>
                                    </div>
                                )}

                                {/* Details (Input/Output) */}
                                {details && (
                                    <div className="space-y-4">
                                        {Object.entries(details).map(([key, val]) => (
                                            <div key={key} className="space-y-1.5">
                                                <div className="flex items-center justify-between">
                                                    <label className="text-[10px] font-bold uppercase tracking-wider text-slate-400">{key}</label>
                                                    <Button
                                                        variant="ghost"
                                                        size="xs"
                                                        className="h-5 px-1.5 text-[9px] flex gap-1 items-center"
                                                        onClick={(e) => { e.stopPropagation(); handleCopy(val, key); }}
                                                    >
                                                        <Copy className="w-2.5 h-2.5" /> Copy
                                                    </Button>
                                                </div>
                                                <div className="bg-background border rounded-lg p-2 overflow-hidden shadow-inner">
                                                    <JsonViewer src={val} collapsed={1} theme={theme} />
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                )}

                                {/* Raw Data Fallback */}
                                {!usage && !error && !details && (
                                    <div className="text-xs text-slate-400 text-center py-4 bg-slate-50/50 rounded-lg border border-dashed italic">
                                        No telemetry recorded for this step.
                                    </div>
                                )}
                            </CardContent>
                        </motion.div>
                    )}
                </AnimatePresence>
            </Card>
        </div>
    );
};
