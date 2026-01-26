import React, { useEffect } from 'react';
import { useInView } from 'react-intersection-observer';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { motion } from 'framer-motion';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Loader2, AlertCircle, History } from 'lucide-react';
import { useExecutionHistory } from '@/hooks/useWorkflowApi';
import type { HistoryEntry } from '@/lib/types';
import { cn, formatTimestamp } from '@/lib/utils';

interface ExecutionHistoryInlineProps {
  executionArn: string;
  /** 이력 항목 선택 시 호출되는 콜백 */
  onSelectEntry?: (payload: { entry: HistoryEntry; key: string }) => void;
  /** 현재 선택된 항목의 키 (강조용) */
  selectedEntryKey?: string | null;
}

/**
 * 전용 스켈레톤 로더
 */
const HistorySkeleton = () => (
  <div className="space-y-3 animate-in fade-in duration-500">
    {Array.from({ length: 4 }).map((_, i) => (
      <div key={i} className="border border-slate-100 rounded-xl p-4 bg-card/50">
        <div className="flex justify-between items-start mb-3">
          <Skeleton className="h-5 w-24 rounded-full" />
          <Skeleton className="h-4 w-12" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-3 w-full opacity-50" />
          <Skeleton className="h-3 w-2/3 opacity-30" />
        </div>
      </div>
    ))}
  </div>
);

export const ExecutionHistoryInline: React.FC<ExecutionHistoryInlineProps> = ({
  executionArn,
  onSelectEntry,
  selectedEntryKey = null
}) => {
  const {
    data,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isLoading,
    isError,
    refetch
  } = useExecutionHistory(executionArn);

  // 1. 무한 스크롤을 위한 Observer 설정
  const { ref, inView } = useInView({
    threshold: 0.1,
  });

  useEffect(() => {
    if (inView && hasNextPage && !isFetchingNextPage) {
      fetchNextPage();
    }
  }, [inView, hasNextPage, isFetchingNextPage, fetchNextPage]);

  const history = data?.pages.flatMap(page => page.history) || [];

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center py-12 px-4 border rounded-xl bg-destructive/5 border-destructive/10 text-center space-y-4">
        <AlertCircle className="w-8 h-8 text-destructive" />
        <div className="space-y-1">
          <p className="text-sm font-bold text-destructive">이력을 불러오지 못했습니다</p>
          <p className="text-xs text-muted-foreground">네트워크 연결 상태를 확인해 주세요.</p>
        </div>
        <Button size="sm" variant="outline" onClick={() => refetch()} className="h-8 gap-2 bg-background">
          <Loader2 className="w-3 h-3" /> 다시 시도
        </Button>
      </div>
    );
  }

  return (
    <ScrollArea className="h-[500px] pr-4 custom-scrollbar">
      {isLoading ? (
        <HistorySkeleton />
      ) : history.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-muted-foreground bg-slate-50/50 rounded-xl border border-dashed border-slate-200">
          <History className="w-10 h-10 mb-3 opacity-20" />
          <p className="text-sm font-medium">실행 이력이 존재하지 않습니다</p>
          <p className="text-xs opacity-70">첫 번째 실행을 시작해 보세요.</p>
        </div>
      ) : (
        <div className="space-y-3 pb-6">
          {history.map((entry: HistoryEntry, index: number) => {
            // 더 안전하고 고유한 키 생성 로직
            const entryTs = typeof entry.entered_at === 'number' ? entry.entered_at : new Date(entry.entered_at).getTime();
            const key = entry.id || `${entryTs}-${entry.state_name}-${index}`;
            const isSelected = selectedEntryKey === key;

            return (
              <button
                key={key}
                onClick={() => {
                  console.debug('[ExecutionHistoryInline] Entry selected:', { key, state: entry.state_name });
                  onSelectEntry?.({ entry, key });
                }}
                className={cn(
                  "w-full text-left border rounded-xl p-4 transition-all duration-200 group relative overflow-hidden active:scale-[0.98]",
                  isSelected
                    ? "ring-2 ring-primary bg-primary/5 border-primary shadow-sm"
                    : "hover:bg-muted/80 border-slate-100 bg-card hover:border-slate-200 shadow-sm"
                )}
              >
                {/* 선택 시 강조 인디케이터 */}
                {isSelected && (
                  <motion.div
                    layoutId="active-indicator"
                    className="absolute left-0 top-0 w-1 h-full bg-primary"
                  />
                )}

                <div className="flex justify-between items-center mb-3">
                  <Badge
                    variant={isSelected ? "default" : "outline"}
                    className={cn(
                      "font-mono text-[10px] tracking-tighter uppercase px-1.5 h-5",
                      !isSelected && "bg-slate-50 font-bold text-slate-600"
                    )}
                  >
                    {entry.state_name}
                  </Badge>
                  <span className="text-[10px] text-slate-400 font-mono font-bold bg-slate-50 px-1.5 py-0.5 rounded border border-slate-100">
                    {entry.duration}ms
                  </span>
                </div>

                <div className="space-y-2 text-[11px]">
                  <div className="flex justify-between items-center">
                    <span className="text-slate-400 font-medium">Entered</span>
                    <span className="text-slate-600 font-bold">
                      {formatTimestamp(entryTs)}
                    </span>
                  </div>
                  <div className="flex justify-between items-center pt-1 border-t border-slate-50 group-hover:border-slate-100 transition-colors">
                    <span className="text-slate-400 font-medium tracking-tight">Status</span>
                    <span className={cn(
                      "font-bold",
                      entry.error ? "text-red-500" : "text-green-600"
                    )}>
                      {entry.error ? 'FAILED' : 'SUCCESS'}
                    </span>
                  </div>
                </div>
              </button>
            );
          })}

          {/* 인피니트 스크롤 감지 영역 */}
          <div ref={ref} className="h-10 w-full flex items-center justify-center">
            {isFetchingNextPage && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/50 px-3 py-1.5 rounded-full animate-in fade-in">
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>데이터를 더 가져오는 중...</span>
              </div>
            )}
            {!hasNextPage && history.length > 5 && (
              <p className="text-[10px] text-slate-300 font-medium uppercase tracking-widest">End of History</p>
            )}
          </div>
        </div>
      )}
    </ScrollArea>
  );
};

export default ExecutionHistoryInline;
