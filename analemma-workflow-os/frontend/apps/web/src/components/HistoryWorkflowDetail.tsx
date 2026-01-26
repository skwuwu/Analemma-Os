import React, { useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { X, Activity, AlertTriangle, Trash2, Maximize2, BarChart3, Clock, ArrowRight } from 'lucide-react';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
    AlertDialogTrigger
} from '@/components/ui/alert-dialog';
import StatusBadge from '@/components/StatusBadge';
import JsonViewer from '@/components/JsonViewer';
import { safeParseJson, normalizeEventTs, cn } from '@/lib/utils';
import { TimelineItem } from '@/components/TimelineItem';
import { DetailedTimelineItem } from '@/components/DetailedTimelineItem';
import { NotificationItem } from '@/lib/types';
import { motion, AnimatePresence } from 'framer-motion';

// 1. 타입 정의 강화
interface ExecutionSummary {
    status: string;
    name?: string | null;
    executionArn?: string;
    execution_id?: string;
    created_at?: number | string | null;
    stopDate?: string | null;
    updated_at?: number | null;
    error?: any;
    cause?: any;
    final_result?: any;
    output?: any;
    step_function_state?: {
        input?: any;
        state_durations?: Record<string, number>;
        current_segment?: number;
        total_segments?: number;
        error?: any;
        [key: string]: any;
    };
}

interface HistoryWorkflowDetailProps {
    executionSummary: ExecutionSummary;
    selectedWorkflowTimeline?: NotificationItem[];
    deleteExecution?: (executionArn: string) => Promise<void> | void;
    onClose: () => void;
    onShowFullHistory: () => void;
}

/**
 * 소요 시간 시각화 헬퍼 (Bar representation)
 */
const DurationBar = ({ duration, maxDuration }: { duration: number, maxDuration: number }) => {
    const percentage = Math.max(2, (duration / maxDuration) * 100);

    return (
        <div className="flex items-center gap-3 w-full group">
            <div className="flex-1 bg-slate-100 rounded-full h-2 overflow-hidden relative">
                <motion.div
                    initial={{ width: 0 }}
                    animate={{ width: `${percentage}%` }}
                    transition={{ duration: 0.8, ease: "circOut" }}
                    className={cn(
                        "h-full rounded-full transition-all duration-300",
                        duration > 60 ? "bg-orange-400" : "bg-primary"
                    )}
                />
            </div>
            <span className="text-[10px] font-mono font-bold shrink-0 w-12 text-right text-slate-500 group-hover:text-primary transition-colors">
                {Math.round(duration)}s
            </span>
        </div>
    );
};

/**
 * 재사용 가능한 데이터 섹션 컴포넌트
 */
const DataSection = ({ title, data, icon: Icon }: { title: string, data: any, icon?: any }) => (
    <div className="space-y-3">
        <div className="flex items-center gap-2">
            {Icon && <Icon className="w-3.5 h-3.5 text-slate-400" />}
            <h3 className="text-[11px] font-bold text-slate-400 uppercase tracking-widest">{title}</h3>
        </div>
        <div className="rounded-xl border bg-slate-50/50 p-4 max-h-72 overflow-auto shadow-inner group transition-all hover:bg-slate-50">
            {data ? (
                <JsonViewer src={safeParseJson(data)} theme="dark" />
            ) : (
                <div className="flex items-center justify-center py-8 text-xs italic text-slate-300 gap-2 font-medium">
                    No artifact data recorded
                </div>
            )}
        </div>
    </div>
);

export const HistoryWorkflowDetail: React.FC<HistoryWorkflowDetailProps> = ({
    executionSummary,
    selectedWorkflowTimeline = [],
    deleteExecution,
    onClose,
    onShowFullHistory,
}) => {
    const [showDetails, setShowDetails] = React.useState(false);

    // 1. 성능 메타데이터 추출
    const stateDurations = useMemo(() =>
        executionSummary?.step_function_state?.state_durations || {},
        [executionSummary]
    );

    const maxDuration = useMemo(() =>
        Math.max(...Object.values(stateDurations), 1),
        [stateDurations]
    );

    if (!executionSummary) return null;

    const execId = executionSummary.executionArn || executionSummary.execution_id;
    const errorInfo = executionSummary.error || executionSummary.cause || executionSummary.step_function_state?.error;
    const isFailed = executionSummary.status === 'FAILED';

    return (
        <div className="flex flex-col h-full bg-card">
            {/* Header 섹션: 상태 및 핵심 정보 */}
            <div className="p-6 border-b space-y-5 bg-gradient-to-b from-slate-50/50 to-transparent">
                <div className="flex justify-between items-start">
                    <div className="space-y-1.5 flex-1 min-w-0 pr-4">
                        <h2 className="text-2xl font-bold tracking-tight text-slate-900 truncate">
                            {executionSummary.name || 'Anonymous Execution'}
                        </h2>
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] font-medium text-slate-400">
                            <div className="flex items-center gap-1.5">
                                <Clock className="w-3 h-3" />
                                <span>Started: {formatTimestamp(normalizeEventTs(executionSummary.created_at))}</span>
                            </div>
                            {executionSummary.stopDate && (
                                <div className="flex items-center gap-1.5 border-l pl-4 border-slate-200">
                                    <Maximize2 className="w-3 h-3 rotate-45" />
                                    <span>Finished: {new Date(executionSummary.stopDate).toLocaleString('ko-KR')}</span>
                                </div>
                            )}
                        </div>
                    </div>
                    <Button variant="ghost" size="icon" onClick={onClose} className="rounded-full hover:bg-slate-100 h-9 w-9">
                        <X className="w-5 h-5" />
                    </Button>
                </div>

                <div className="flex items-center justify-between bg-white border border-slate-100 p-4 rounded-2xl shadow-sm">
                    <div className="flex items-center gap-4">
                        <StatusBadge status={executionSummary.status} />
                        <span className="text-[10px] font-mono font-bold text-slate-300 tracking-tighter uppercase px-2 py-0.5 bg-slate-50 rounded">
                            Ref: {execId?.split(':').pop()?.substring(0, 16)}...
                        </span>
                    </div>
                    <div className="flex gap-2">
                        <Button
                            size="sm"
                            variant="outline"
                            onClick={onShowFullHistory}
                            className="rounded-lg h-9 border-slate-200 hover:bg-slate-50 font-bold text-xs gap-2"
                        >
                            <Activity className="w-3.5 h-3.5" /> Full Logs
                        </Button>

                        {deleteExecution && execId && (
                            <AlertDialog>
                                <AlertDialogTrigger asChild>
                                    <Button size="sm" variant="ghost" className="rounded-lg h-9 text-slate-400 hover:text-destructive hover:bg-destructive/5">
                                        <Trash2 className="w-4 h-4" />
                                    </Button>
                                </AlertDialogTrigger>
                                <AlertDialogContent className="rounded-2xl border-none shadow-2xl">
                                    <AlertDialogHeader>
                                        <AlertDialogTitle className="text-xl font-bold">실행 기록을 삭제하시겠습니까?</AlertDialogTitle>
                                        <AlertDialogDescription className="text-slate-500">
                                            이 작업은 취소할 수 없으며 관련된 모든 실행 데이터가 영구적으로 파괴됩니다.
                                        </AlertDialogDescription>
                                    </AlertDialogHeader>
                                    <AlertDialogFooter>
                                        <AlertDialogCancel className="rounded-xl border-slate-200">취소</AlertDialogCancel>
                                        <AlertDialogAction
                                            onClick={() => deleteExecution(execId)}
                                            className="bg-destructive hover:bg-destructive/90 rounded-xl font-bold"
                                        >
                                            데이터 파괴
                                        </AlertDialogAction>
                                    </AlertDialogFooter>
                                </AlertDialogContent>
                            </AlertDialog>
                        )}
                    </div>
                </div>
            </div>

            <ScrollArea className="flex-1">
                <div className="p-6 space-y-10 max-w-4xl mx-auto pb-20">
                    {/* 에러 분석 최상단 노출 */}
                    {isFailed && errorInfo && (
                        <Card className="border-red-200 bg-red-50/50 overflow-hidden shadow-sm rounded-2xl border">
                            <CardHeader className="py-3.5 px-5 bg-red-100/50 border-b border-red-100 flex flex-row items-center justify-between">
                                <CardTitle className="text-sm font-bold text-red-700 flex items-center gap-2">
                                    <AlertTriangle className="w-4 h-4" /> Execution Breakdown Error
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="p-5 font-mono text-xs leading-relaxed text-red-900/80 whitespace-pre-wrap break-all bg-white/30 rounded-b-2xl">
                                {typeof errorInfo === 'string' ? errorInfo : JSON.stringify(errorInfo, null, 2)}
                            </CardContent>
                        </Card>
                    )}

                    {/* 성능 데이터 시각화 (Step Bottleneck Analysis) */}
                    {Object.keys(stateDurations).length > 0 && (
                        <div className="space-y-4">
                            <div className="flex items-center gap-2">
                                <BarChart3 className="w-4 h-4 text-primary" />
                                <h3 className="text-[11px] font-bold text-slate-400 uppercase tracking-widest">
                                    Step Performance Analysis
                                </h3>
                            </div>
                            <div className="grid gap-3.5 border rounded-2xl p-5 bg-slate-50/30">
                                {Object.entries(stateDurations).map(([state, duration]) => (
                                    <div key={state} className="space-y-1.5">
                                        <div className="flex justify-between items-center px-1">
                                            <span className="text-[11px] font-bold text-slate-700">{state}</span>
                                        </div>
                                        <DurationBar duration={duration} maxDuration={maxDuration} />
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* 데이터 입출력 비교 */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                        <DataSection title="Initial Input" data={executionSummary.step_function_state?.input} />
                        <DataSection title="Analytical Output" data={executionSummary.final_result || executionSummary.output} />
                    </div>

                    {/* 타임라인 히스토리 */}
                    <div className="space-y-5">
                        <div className="flex items-center justify-between border-b pb-2.5">
                            <div className="flex items-center gap-2">
                                <Activity className="w-4 h-4 text-slate-400" />
                                <h3 className="text-[11px] font-bold text-slate-400 uppercase tracking-widest">Execution Timeline</h3>
                            </div>
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 text-[10px] font-bold bg-slate-50 hover:bg-slate-100 text-slate-500 rounded-lg gap-1.5"
                                onClick={() => setShowDetails(!showDetails)}
                            >
                                {showDetails ? "Simple View" : "Detailed Flow"}
                                <ArrowRight className={cn("w-3 h-3 transition-transform", showDetails && "rotate-90")} />
                            </Button>
                        </div>

                        <div className="relative pt-2">
                            <div className="absolute left-4 top-0 bottom-0 w-0.5 bg-slate-100 rounded-full" />
                            <div className="space-y-8 pl-10 relative">
                                {selectedWorkflowTimeline.map((event, idx) => (
                                    <div key={event.id || idx} className="animate-in fade-in slide-in-from-left-4 duration-300" style={{ animationDelay: `${idx * 50}ms` }}>
                                        {showDetails
                                            ? <DetailedTimelineItem event={event} isLast={idx === selectedWorkflowTimeline.length - 1} />
                                            : <TimelineItem event={event} isLast={idx === selectedWorkflowTimeline.length - 1} />
                                        }
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>

                    {/* Raw State Fallback (Not Succeeded) */}
                    {executionSummary.step_function_state && !['SUCCEEDED', 'COMPLETE'].includes(executionSummary.status) && (
                        <div className="pt-6 border-t">
                            <Card className="border-dashed bg-slate-50/50 rounded-2xl">
                                <CardContent className="p-4 space-y-3 text-[11px]">
                                    <div className="flex justify-between items-center">
                                        <span className="text-slate-400 font-bold uppercase tracking-tighter">Current Progress</span>
                                        <span className="bg-white px-2 py-1 rounded-lg border shadow-sm font-bold text-primary">
                                            {executionSummary.step_function_state.current_segment || 0} / {executionSummary.step_function_state.total_segments || 0} Segments
                                        </span>
                                    </div>
                                    <Separator className="bg-slate-200" />
                                    <div className="flex justify-between items-center text-slate-400">
                                        <span className="font-bold uppercase tracking-tighter">Last Telemetry Update</span>
                                        <span>{executionSummary.updated_at ? new Date(executionSummary.updated_at).toLocaleString('ko-KR') : '—'}</span>
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

// Helper to format timestamps gracefully
const formatTimestamp = (ts: number): string => {
    if (!ts) return '—';
    return new Date(ts).toLocaleString('ko-KR', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
};
