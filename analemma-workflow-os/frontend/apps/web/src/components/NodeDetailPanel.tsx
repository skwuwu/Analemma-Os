import { useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Clock, AlertCircle, CheckCircle2, Loader2, Cpu, Zap, History, ChevronRight } from 'lucide-react';
import type { NodeDetail } from '@/hooks/useNodeExecutionStatus';
import type { HistoryEntry } from '@/lib/types';
import JsonViewer from '@/components/JsonViewer';
import { cn } from '@/lib/utils';
import { motion, AnimatePresence } from 'framer-motion';

interface NodeDetailPanelProps {
    nodeId: string | null;
    nodeDetails: Map<string, NodeDetail>;
    historyEntries?: HistoryEntry[];
    className?: string;
}

/**
 * 전용 상태 아이콘
 */
const StatusIcon = ({ status }: { status: string }) => {
    switch (status.toLowerCase()) {
        case 'running':
            return <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />;
        case 'completed':
        case 'success':
            return <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />;
        case 'failed':
        case 'error':
            return <AlertCircle className="w-3.5 h-3.5 text-red-500" />;
        default:
            return <Clock className="w-3.5 h-3.5 text-slate-300" />;
    }
};

/**
 * 전용 상태 배지
 */
const StatusBadge = ({ status }: { status: string }) => {
    const s = status.toLowerCase();
    const config: Record<string, { variant: any, label: string }> = {
        running: { variant: 'default', label: '실행 중' },
        completed: { variant: 'secondary', label: '완료' },
        success: { variant: 'secondary', label: '성공' },
        failed: { variant: 'destructive', label: '실패' },
        error: { variant: 'destructive', label: '오류' },
        idle: { variant: 'outline', label: '대기' },
    };

    const item = config[s] || { variant: 'outline', label: status };

    return (
        <Badge variant={item.variant} className="gap-1.5 px-2.5 h-6 font-bold uppercase tracking-tighter text-[10px]">
            <StatusIcon status={s} />
            {item.label}
        </Badge>
    );
};

/**
 * 정보 행 컴포넌트
 */
const InfoRow = ({ icon: Icon, label, value, badge, className }: any) => (
    <div className={cn("flex items-center justify-between text-[11px] group", className)}>
        <div className="flex items-center gap-2">
            <div className="p-1.5 bg-slate-50 rounded-lg group-hover:bg-slate-100 transition-colors">
                <Icon className="w-3.5 h-3.5 text-slate-400" />
            </div>
            <span className="text-slate-400 font-bold uppercase tracking-widest">{label}</span>
        </div>
        <div className="flex items-center gap-2">
            <span className="font-mono font-bold text-slate-700">{value}</span>
            {badge && (
                <Badge variant="outline" className="text-[9px] font-mono h-4 px-1 border-slate-200 text-slate-400">
                    {badge}
                </Badge>
            )}
        </div>
    </div>
);

/**
 * 초기 빈 화면 상태
 */
const EmptyState = ({ className }: { className?: string }) => (
    <Card className={cn("flex flex-col items-center justify-center p-8 bg-slate-50/50 border-dashed border-2", className)}>
        <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            className="text-center"
        >
            <div className="w-16 h-16 bg-white rounded-2xl shadow-sm border border-slate-100 flex items-center justify-center mx-auto mb-4">
                <Cpu className="w-8 h-8 text-slate-200" />
            </div>
            <h3 className="text-sm font-bold text-slate-400">감사 대상 미선택</h3>
            <p className="text-[11px] text-slate-300 mt-1 max-w-[180px] mx-auto">
                캔버스에서 노드를 선택하여 실시간 실행 상태와 로그를 확인하세요.
            </p>
        </motion.div>
    </Card>
);

export const NodeDetailPanel = ({
    nodeId,
    nodeDetails,
    historyEntries,
    className,
}: NodeDetailPanelProps) => {
    // 1. 선택된 노드 상세 정보
    const detail = useMemo(() => (nodeId ? nodeDetails.get(nodeId) : null), [nodeId, nodeDetails]);

    // 2. 히스토리 필터링 최적화
    const nodeHistory = useMemo(() => {
        if (!nodeId || !historyEntries) return [];
        return historyEntries
            .filter((e) => e.node_id === nodeId || e.name === nodeId)
            .sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
    }, [nodeId, historyEntries]);

    if (!nodeId) return <EmptyState className={className} />;

    if (!detail) {
        return (
            <Card className={cn("p-6 text-center space-y-3", className)}>
                <AlertCircle className="w-10 h-10 text-slate-200 mx-auto" />
                <p className="text-sm font-bold text-slate-400">노드 ({nodeId}) 데이터를 로드할 수 없습니다.</p>
            </Card>
        );
    }

    return (
        <Card className={cn("flex flex-col h-full overflow-hidden border-slate-100 shadow-xl shadow-slate-200/50", className)}>
            <CardHeader className="p-5 border-b bg-gradient-to-b from-slate-50/50 to-transparent">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3 min-w-0">
                        <div className="w-10 h-10 bg-primary/10 rounded-xl flex items-center justify-center shrink-0 shadow-inner">
                            <Cpu className="w-5 h-5 text-primary" />
                        </div>
                        <CardTitle className="text-sm font-bold truncate pr-2">
                            {detail.name}
                        </CardTitle>
                    </div>
                    <StatusBadge status={detail.status} />
                </div>
            </CardHeader>

            <ScrollArea className="flex-1">
                <CardContent className="p-5 space-y-8">
                    {/* 메타데이터 섹션 */}
                    <div className="grid gap-3.5">
                        {detail.duration !== undefined && (
                            <InfoRow
                                icon={Clock}
                                label="Execution Time"
                                value={`${(detail.duration / 1000).toFixed(2)}s`}
                            />
                        )}
                        {detail.usage?.total_tokens && (
                            <InfoRow
                                icon={Zap}
                                label="Token Consumption"
                                value={detail.usage.total_tokens.toLocaleString()}
                                badge={detail.usage.model}
                            />
                        )}
                    </div>

                    {/* 에러 피드백 섹션 */}
                    {detail.error && (
                        <div className="group relative">
                            <div className="absolute -inset-1 bg-gradient-to-r from-red-500/10 to-orange-500/10 rounded-2xl blur opacity-25 group-hover:opacity-50 transition duration-1000" />
                            <div className="relative p-4 bg-red-50 border border-red-100 rounded-xl space-y-2">
                                <div className="flex items-center gap-2 text-red-700 font-bold text-[11px] uppercase tracking-widest">
                                    <AlertCircle className="w-3.5 h-3.5" /> Logical Fault Detected
                                </div>
                                <div className="text-[11px] text-red-600 font-medium leading-relaxed font-mono whitespace-pre-wrap">
                                    {typeof detail.error === 'string'
                                        ? detail.error
                                        : JSON.stringify(detail.error, null, 2)}
                                </div>
                            </div>
                        </div>
                    )}

                    {/* 출력 데이터 (JsonViewer 재사용) */}
                    <div>
                        <div className="flex items-center gap-2 mb-3">
                            <ChevronRight className="w-3.5 h-3.5 text-slate-400" />
                            <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Node Payload Output</h4>
                        </div>
                        <JsonViewer
                            src={detail.content}
                            collapsed={1}
                            className="border-none bg-slate-50"
                        />
                    </div>

                    {/* 실행 타임라인 스냅샷 */}
                    {nodeHistory.length > 0 && (
                        <div className="space-y-4">
                            <div className="flex items-center gap-2">
                                <History className="w-3.5 h-3.5 text-slate-400" />
                                <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Local Execution Logs</h4>
                            </div>
                            <div className="space-y-3 pl-4 border-l-2 border-slate-100">
                                {nodeHistory.map((entry, idx) => (
                                    <div
                                        key={entry.id || idx}
                                        className="flex items-center justify-between group/item"
                                    >
                                        <div className="flex items-center gap-3">
                                            <div className="w-2 h-2 rounded-full border-2 border-white shadow-sm ring-1 ring-slate-100 bg-slate-200 group-hover/item:scale-125 transition-transform" />
                                            <span className="text-[11px] font-bold text-slate-600 group-hover/item:text-primary transition-colors">
                                                {entry.type || entry.status || 'STEP EXECUTION'}
                                            </span>
                                        </div>
                                        <div className="flex items-center gap-2 font-mono text-[9px] font-bold text-slate-300">
                                            <Clock className="w-3 h-3" />
                                            {entry.timestamp
                                                ? new Date(entry.timestamp * 1000).toLocaleTimeString()
                                                : '--:--'}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </CardContent>
            </ScrollArea>
        </Card>
    );
};

export default NodeDetailPanel;
