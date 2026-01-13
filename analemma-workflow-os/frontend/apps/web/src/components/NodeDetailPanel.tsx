import { useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Clock, AlertCircle, CheckCircle2, Loader2, Cpu, Zap } from 'lucide-react';
import type { NodeDetail } from '@/hooks/useNodeExecutionStatus';
import type { HistoryEntry } from '@/lib/types';

interface NodeDetailPanelProps {
    nodeId: string | null;
    nodeDetails: Map<string, NodeDetail>;
    historyEntries?: HistoryEntry[];
    className?: string;
}

const StatusIcon = ({ status }: { status: string }) => {
    switch (status) {
        case 'running':
            return <Loader2 className="w-4 h-4 animate-spin text-yellow-400" />;
        case 'completed':
            return <CheckCircle2 className="w-4 h-4 text-green-500" />;
        case 'failed':
            return <AlertCircle className="w-4 h-4 text-red-500" />;
        default:
            return <Clock className="w-4 h-4 text-muted-foreground" />;
    }
};

const StatusBadge = ({ status }: { status: string }) => {
    const variants: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
        running: 'default',
        completed: 'secondary',
        failed: 'destructive',
        idle: 'outline',
    };

    const labels: Record<string, string> = {
        running: '실행 중',
        completed: '완료',
        failed: '실패',
        idle: '대기',
    };

    return (
        <Badge variant={variants[status] || 'outline'} className="gap-1">
            <StatusIcon status={status} />
            {labels[status] || status}
        </Badge>
    );
};

export const NodeDetailPanel = ({
    nodeId,
    nodeDetails,
    historyEntries,
    className,
}: NodeDetailPanelProps) => {
    // Get detail for selected node
    const detail = nodeId ? nodeDetails.get(nodeId) : null;

    // Get all history entries for this node
    const nodeHistory = useMemo(() => {
        if (!nodeId || !historyEntries) return [];
        return historyEntries
            .filter((e) => e.node_id === nodeId || e.name === nodeId)
            .sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
    }, [nodeId, historyEntries]);

    if (!nodeId) {
        return (
            <Card className={className}>
                <CardContent className="flex items-center justify-center h-full min-h-[200px] text-muted-foreground">
                    <div className="text-center">
                        <Cpu className="w-8 h-8 mx-auto mb-2 opacity-50" />
                        <p className="text-sm">노드를 클릭하여 상세 정보를 확인하세요</p>
                    </div>
                </CardContent>
            </Card>
        );
    }

    if (!detail) {
        return (
            <Card className={className}>
                <CardHeader>
                    <CardTitle className="text-sm font-medium">{nodeId}</CardTitle>
                </CardHeader>
                <CardContent>
                    <p className="text-sm text-muted-foreground">노드 정보를 찾을 수 없습니다.</p>
                </CardContent>
            </Card>
        );
    }

    return (
        <Card className={className}>
            <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                    <CardTitle className="text-base font-medium">{detail.name}</CardTitle>
                    <StatusBadge status={detail.status} />
                </div>
            </CardHeader>
            <CardContent className="space-y-4">
                {/* Duration */}
                {detail.duration !== undefined && (
                    <div className="flex items-center gap-2 text-sm">
                        <Clock className="w-4 h-4 text-muted-foreground" />
                        <span className="text-muted-foreground">실행 시간:</span>
                        <span className="font-mono">{(detail.duration / 1000).toFixed(2)}s</span>
                    </div>
                )}

                {/* Token Usage */}
                {detail.usage?.total_tokens && (
                    <div className="flex items-center gap-2 text-sm">
                        <Zap className="w-4 h-4 text-muted-foreground" />
                        <span className="text-muted-foreground">토큰:</span>
                        <span className="font-mono">{detail.usage.total_tokens.toLocaleString()}</span>
                        {detail.usage.model && (
                            <Badge variant="outline" className="text-xs">
                                {detail.usage.model}
                            </Badge>
                        )}
                    </div>
                )}

                {/* Error Message */}
                {detail.error && (
                    <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-md">
                        <div className="flex items-start gap-2">
                            <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
                            <div className="text-sm">
                                <p className="font-medium text-red-500">
                                    {typeof detail.error === 'object' && 'type' in detail.error
                                        ? (detail.error as { type?: string }).type || 'Error'
                                        : 'Error'}
                                </p>
                                <p className="text-muted-foreground">
                                    {typeof detail.error === 'string'
                                        ? detail.error
                                        : typeof detail.error === 'object' && 'message' in detail.error
                                            ? (detail.error as { message?: string }).message
                                            : JSON.stringify(detail.error)}
                                </p>
                            </div>
                        </div>
                    </div>
                )}

                {/* Output Content */}
                {detail.content && (
                    <div className="space-y-2">
                        <p className="text-sm font-medium">출력:</p>
                        <ScrollArea className="h-[150px]">
                            <pre className="text-xs bg-muted/50 p-3 rounded-md whitespace-pre-wrap break-words">
                                {typeof detail.content === 'string'
                                    ? detail.content
                                    : JSON.stringify(detail.content, null, 2)}
                            </pre>
                        </ScrollArea>
                    </div>
                )}

                {/* History Timeline */}
                {nodeHistory.length > 0 && (
                    <div className="space-y-2">
                        <p className="text-sm font-medium">실행 로그:</p>
                        <ScrollArea className="h-[120px]">
                            <div className="space-y-2">
                                {nodeHistory.map((entry, idx) => (
                                    <div
                                        key={entry.id || idx}
                                        className="flex items-center gap-2 text-xs text-muted-foreground"
                                    >
                                        <StatusIcon status={(entry.status || '').toLowerCase()} />
                                        <span className="font-mono">
                                            {entry.timestamp
                                                ? new Date(entry.timestamp * 1000).toLocaleTimeString()
                                                : '--:--:--'}
                                        </span>
                                        <span>{entry.type || entry.status}</span>
                                    </div>
                                ))}
                            </div>
                        </ScrollArea>
                    </div>
                )}
            </CardContent>
        </Card>
    );
};

export default NodeDetailPanel;
