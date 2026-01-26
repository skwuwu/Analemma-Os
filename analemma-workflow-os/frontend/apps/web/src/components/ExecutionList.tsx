import React, { useCallback, useMemo } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
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
import { NoExecutionsEmpty } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { cn, normalizeEventTs } from '@/lib/utils';
import { Trash2, Calendar, ChevronRight } from 'lucide-react';

interface Execution {
  status: string;
  startDate: string | null;
  stopDate: string | null;
  error: string | null;
  name: string | null;
  final_result: object | string | null;
  created_at: number | null;
  updated_at: number | null;
  step_function_state: object | null;
  executionArn?: string;
  workflowId?: string;
}

interface Props {
  executions: Execution[];
  selectedExecutionId: string | null;
  onSelect: (id: string | null) => void;
  onLoadMore?: () => void;
  isLoading?: boolean;
  onDelete?: (executionArn: string) => void;
}

/**
 * 컴포넌트 외부 유틸리티: 표시 이름 추출
 */
const getExecutionDisplayName = (ex: Execution): string => {
  return ex.name || ex.workflowId || 'Unknown Workflow';
};

/**
 * 컴포넌트 외부 유틸리티: 포맷팅된 날짜 반환
 */
const formatExecutionDate = (timestamp: any): string => {
  const ts = normalizeEventTs(timestamp);
  if (!ts) return '—';

  return new Date(ts).toLocaleString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
};

export const ExecutionList: React.FC<Props> = ({
  executions,
  selectedExecutionId,
  onSelect,
  onLoadMore,
  isLoading,
  onDelete
}) => {
  // 1. 메모이제이션된 필터링 로직
  // 부모에서 필터링해서 넘겨주는 것을 권장하지만, 현재 호환성을 위해 유지
  const historyExecutions = useMemo(() =>
    executions?.filter(ex => ex.status !== 'RUNNING') || [],
    [executions]
  );

  const handleDeleteClick = useCallback((e: React.MouseEvent, executionArn: string) => {
    e.stopPropagation();
    // Native Confirm 대신 Shadcn UI의 AlertDialog를 사용하므로 트리거 이벤트만 처리
  }, []);

  // 로딩 스켈레톤 상태
  if (isLoading && historyExecutions.length === 0) {
    return (
      <div className="p-4 space-y-4">
        {[1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="flex gap-4 p-4 border rounded-xl bg-card">
            <Skeleton className="h-10 w-10 rounded-lg shrink-0" />
            <div className="flex-1 space-y-2">
              <Skeleton className="h-4 w-1/3" />
              <Skeleton className="h-3 w-1/2 opacity-50" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (historyExecutions.length === 0) {
    return <NoExecutionsEmpty />;
  }

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="p-4 space-y-3">
        {historyExecutions.map((ex) => {
          const displayName = getExecutionDisplayName(ex);
          const id = ex.executionArn;
          if (!id) return null;

          const isSelected = selectedExecutionId === id;

          return (
            <div key={id} className="relative group">
              <Card
                className={cn(
                  "cursor-pointer transition-all duration-200 border-slate-100 overflow-hidden relative",
                  isSelected
                    ? "ring-2 ring-primary border-primary bg-primary/5 shadow-md shadow-primary/10 scale-[1.01]"
                    : "hover:bg-slate-50 hover:border-slate-200 active:scale-[0.99]"
                )}
                onClick={() => onSelect(id)}
              >
                <CardContent className="p-4">
                  <div className="flex justify-between items-center gap-4">
                    <div className="flex-1 min-w-0 flex items-center gap-3">
                      <div className={cn(
                        "w-10 h-10 rounded-xl flex items-center justify-center shrink-0 transition-colors",
                        isSelected ? "bg-primary text-white" : "bg-slate-100 text-slate-400 group-hover:bg-slate-200"
                      )}>
                        <Calendar className="w-5 h-5" />
                      </div>
                      <div className="min-w-0">
                        <h4 className="font-bold text-sm text-slate-800 truncate leading-tight mb-1">
                          {displayName}
                        </h4>
                        <div className="flex items-center gap-1.5 text-[11px] text-slate-400 font-medium">
                          <span className="shrink-0 font-mono tracking-tighter">
                            {formatExecutionDate(ex.created_at)}
                          </span>
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-3">
                      <StatusBadge status={ex.status} className="h-6" />

                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        {onDelete && (
                          <AlertDialog>
                            <AlertDialogTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 text-slate-400 hover:text-destructive hover:bg-destructive/10 rounded-lg"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <Trash2 className="w-4 h-4" />
                              </Button>
                            </AlertDialogTrigger>
                            <AlertDialogContent>
                              <AlertDialogHeader>
                                <AlertDialogTitle>실행 이력 삭제</AlertDialogTitle>
                                <AlertDialogDescription>
                                  정말로 이 실행 이력을 삭제하시겠습니까? 삭제된 데이터는 복구할 수 없습니다.
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel onClick={(e) => e.stopPropagation()}>취소</AlertDialogCancel>
                                <AlertDialogAction
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onDelete(id);
                                  }}
                                  className="bg-destructive hover:bg-destructive/90"
                                >
                                  삭제
                                </AlertDialogAction>
                              </AlertDialogFooter>
                            </AlertDialogContent>
                          </AlertDialog>
                        )}
                        <ChevronRight className={cn(
                          "w-4 h-4 text-slate-300 transition-transform",
                          isSelected && "text-primary translate-x-1"
                        )} />
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          );
        })}

        {onLoadMore && (
          <div className="pt-4 pb-8">
            <Button
              variant="outline"
              className="w-full h-11 rounded-xl border-dashed border-slate-300 text-slate-500 hover:text-primary hover:border-primary hover:bg-primary/5 transition-all"
              onClick={onLoadMore}
              disabled={isLoading}
            >
              {isLoading ? (
                <div className="flex items-center gap-2">
                  <div className="w-4 h-4 border-2 border-primary border-t-transparent rounded-full animate-spin" />
                  데이터를 불러오는 중...
                </div>
              ) : (
                '이력 더 보기'
              )}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
};

export default React.memo(ExecutionList);
