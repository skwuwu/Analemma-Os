import { useState, useEffect, useRef } from 'react';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Activity, Clock, Loader2, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import StatusBadge from '@/components/StatusBadge';
import { useWorkflowApi } from '@/hooks/useWorkflowApi';
import { formatDate, formatDuration } from '@/lib/utils';
import { toast } from 'sonner';

interface ExecutionStatus {
  executionArn: string;
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'TIMED_OUT' | 'ABORTED';
  startDate: string;
  stopDate?: string;
}

interface ExecutionDetailStatus extends ExecutionStatus {
  output?: unknown;
  error?: unknown;
  input?: unknown;
}

// Use shared StatusBadge component for status visuals

export const WorkflowStatusTab = () => {
  const { fetchExecutions, fetchExecutionDetail } = useWorkflowApi();
  const [isExpanded, setIsExpanded] = useState(false);
  const [executions, setExecutions] = useState<ExecutionStatus[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [nextToken, setNextToken] = useState<string | undefined>();
  const [hasMore, setHasMore] = useState(false);
  
  const [selectedExecution, setSelectedExecution] = useState<string | null>(null);
  const [executionDetail, setExecutionDetail] = useState<ExecutionDetailStatus | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);

  // [최적화] 렌더링을 유발하지 않도록 캐시를 useRef로 관리
  const executionDetailsCache = useRef(new Map<string, ExecutionDetailStatus>());
  
  // [버그 방지] Stale Closure 방지용 Ref
  const executionsRef = useRef(executions);

  useEffect(() => {
    executionsRef.current = executions;
  }, [executions]);

  const fetchExecutionsList = async (token?: string, isLoadMore = false) => {
    try {
      if (!isLoadMore) setIsLoading(true); // 더 불러오기 땐 전체 로딩 표시 안 함 (선택 사항)
      const response = await fetchExecutions(token);
      
      if (isLoadMore) {
        setExecutions(prev => [...prev, ...response.items]);
      } else {
        setExecutions(response.items);
      }
      
      setNextToken(response.nextToken);
      setHasMore(!!response.nextToken);
    } catch (error) {
      console.error('Failed to fetch executions:', error);
      toast.error('Failed to load workflow executions');
    } finally {
      setIsLoading(false);
    }
  };

  const updateCache = (arn: string, detail: ExecutionDetailStatus) => {
    const cache = executionDetailsCache.current;
    // LRU 구현: 기존 키가 있다면 삭제 후 다시 set하여 가장 최근으로 이동
    if (cache.has(arn)) {
      cache.delete(arn);
    }
    cache.set(arn, detail);
    
    // 크기 제한 (50개)
    if (cache.size > 50) {
      const firstKey = cache.keys().next().value;
      if (firstKey) cache.delete(firstKey); // 타입 가드 추가
    }
  };

  const fetchExecutionDetailCached = async (executionArn: string) => {
    // 1. 현재 선택된 것과 같으면 토글(닫기)
    if (selectedExecution === executionArn) {
      setSelectedExecution(null);
      setExecutionDetail(null);
      return;
    }

    const currentExecution = executions.find(exec => exec.executionArn === executionArn);
    const isRunning = currentExecution?.status === 'RUNNING';
    const cache = executionDetailsCache.current;

    // 2. 캐시 확인 (RUNNING이 아니고 캐시에 있다면 사용)
    if (!isRunning && cache.has(executionArn)) {
      const cachedDetail = cache.get(executionArn)!;
      // LRU 갱신을 위해 재삽입
      updateCache(executionArn, cachedDetail);
      setExecutionDetail(cachedDetail);
      setSelectedExecution(executionArn);
      return;
    }

    // 3. 네트워크 요청
    try {
      setIsLoadingDetail(true);
      setSelectedExecution(executionArn); // UI 즉시 반응을 위해 먼저 설정
      const detail = await fetchExecutionDetail(executionArn);
      
      if (detail.status !== 'RUNNING') {
        updateCache(executionArn, detail);
      }
      
      setExecutionDetail(detail);
    } catch (error) {
      console.error('Failed to fetch execution details:', error);
      toast.error('Failed to load execution details');
      setSelectedExecution(null); // 에러 시 선택 해제
    } finally {
      setIsLoadingDetail(false);
    }
  };

  const handleRefresh = () => {
    setNextToken(undefined);
    setHasMore(false);
    setSelectedExecution(null);
    setExecutionDetail(null);
    fetchExecutionsList();
  };

  const handleLoadMore = () => {
    if (nextToken && !isLoading) {
      fetchExecutionsList(nextToken, true);
    }
  };

  // [개선] RUNNING 상태 업데이트 시 로딩바 없이 조용히 업데이트
  const refreshRunningExecutions = async () => {
    const runningExecutions = executionsRef.current.filter(exec => exec.status === 'RUNNING');
    if (runningExecutions.length === 0) return;

    try {
      const updatedDetails = await Promise.all(
        runningExecutions.map(exec => fetchExecutionDetail(exec.executionArn))
      );

      // 1. 메인 리스트 업데이트
      setExecutions(prev => 
        prev.map(existing => {
          const updated = updatedDetails.find(d => d.executionArn === existing.executionArn);
          if (updated) {
            // 상태가 완료됨으로 바뀌었으면 캐시 업데이트
            if (existing.status === 'RUNNING' && updated.status !== 'RUNNING') {
              updateCache(updated.executionArn, updated);
            }
            return {
              executionArn: updated.executionArn,
              status: updated.status,
              startDate: updated.startDate,
              stopDate: updated.stopDate
            };
          }
          return existing;
        })
      );

      // 2. 현재 보고 있는 상세 화면이 있다면 "조용히(Silent)" 업데이트
      // (isLoadingDetail을 건드리지 않고 데이터만 교체)
      if (selectedExecution) {
        const currentDetailUpdate = updatedDetails.find(d => d.executionArn === selectedExecution);
        if (currentDetailUpdate) {
          setExecutionDetail(currentDetailUpdate);
        }
      }

    } catch (error) {
      console.error('Silent refresh failed:', error);
    }
  };

  useEffect(() => {
    if (!isExpanded) return;
    
    fetchExecutionsList();
    
    const interval = setInterval(() => {
      const hasRunning = executionsRef.current.some(exec => exec.status === 'RUNNING');
      if (hasRunning) {
        refreshRunningExecutions();
      }
    }, 10000);
    
    return () => clearInterval(interval);
  }, [isExpanded]);

  const runningCount = executions.filter(exec => exec.status === 'RUNNING').length;

  return (
    <div className="border-t border-border bg-card/30">
      <Button
        variant="ghost"
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full justify-between p-3 h-auto rounded-none hover:bg-card/50"
      >
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-primary" />
          <span className="font-medium text-sm">Workflow Executions</span>
          {runningCount > 0 && (
            <Badge variant="secondary" className="text-xs">
              {runningCount} running
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-1">
          {isLoading && <Loader2 className="w-3 h-3 animate-spin" />}
          {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </div>
      </Button>

      {isExpanded && (
        <div className="border-t border-border">
          <div className="p-3 border-b border-border bg-card/20">
            <Button
              size="sm"
              variant="outline"
              onClick={handleRefresh}
              disabled={isLoading}
              className="w-full"
            >
              <RefreshCw className={`w-3 h-3 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
              Refresh Status
            </Button>
          </div>

          <ScrollArea className="flex-1 max-h-[500px]">
            <div className="p-3 space-y-2">
              {executions.length === 0 && !isLoading ? (
                <div className="text-center py-4 text-muted-foreground text-sm">
                  No workflow executions found
                </div>
              ) : (
                executions.map((execution) => (
                  <div key={execution.executionArn} className="space-y-2">
                    <div
                      className={`p-2 rounded-md border transition-colors cursor-pointer ${
                        selectedExecution === execution.executionArn 
                          ? 'border-primary/50 bg-primary/5' 
                          : 'border-border bg-card/50 hover:bg-card/70'
                      }`}
                      onClick={() => fetchExecutionDetailCached(execution.executionArn)}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2 flex-1 min-w-0">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-1">
                              <StatusBadge status={String(execution.status || '').toUpperCase()} className="text-xs" />
                              <span className="text-xs text-muted-foreground">
                                {formatDuration(execution.startDate, execution.stopDate)}
                              </span>
                            </div>
                            <div className="text-xs font-mono text-muted-foreground truncate" title={execution.executionArn}>
                              {execution.executionArn.split(':').pop()}
                            </div>
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="text-xs text-muted-foreground">
                            {formatDate(execution.startDate)}
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Execution Detail Panel */}
                    {selectedExecution === execution.executionArn && (
                      <div className="ml-4 p-3 rounded-md border border-border bg-muted/20 animate-in slide-in-from-top-2 duration-200">
                        {isLoadingDetail && !executionDetail ? (
                          <div className="flex items-center justify-center py-4">
                            <Loader2 className="w-4 h-4 animate-spin mr-2" />
                            Loading details...
                          </div>
                        ) : executionDetail ? (
                          <div className="space-y-3">
                            <div className="grid grid-cols-1 gap-2 text-xs">
                              <div>
                                <span className="font-medium text-muted-foreground">Execution ARN:</span>
                                <div className="font-mono text-[10px] break-all mt-1 select-all">
                                  {executionDetail.executionArn}
                                </div>
                              </div>
                            </div>

                            {/* Input Section */}
                            {executionDetail.input && (
                              <div>
                                <span className="font-medium text-muted-foreground text-xs">Input:</span>
                                <details className="mt-1 group">
                                  <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground list-none flex items-center gap-1">
                                    <ChevronRight className="w-3 h-3 transition-transform group-open:rotate-90" />
                                    Show Input
                                  </summary>
                                  <div className="mt-1 p-2 bg-muted/50 rounded text-xs font-mono max-h-40 overflow-auto border border-border">
                                    <pre className="whitespace-pre-wrap break-words">
                                      {typeof executionDetail.input === 'string' 
                                        ? executionDetail.input 
                                        : JSON.stringify(executionDetail.input, null, 2)}
                                    </pre>
                                  </div>
                                </details>
                              </div>
                            )}

                            {/* Output Section */}
                            {executionDetail.output && (
                              <div>
                                <span className="font-medium text-muted-foreground text-xs">Output:</span>
                                <details className="mt-1 group" open>
                                  <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground list-none flex items-center gap-1">
                                    <ChevronRight className="w-3 h-3 transition-transform group-open:rotate-90" />
                                    Show Output
                                  </summary>
                                  <div className="mt-1 p-2 bg-muted/50 rounded text-xs font-mono max-h-60 overflow-auto border border-border">
                                    <pre className="whitespace-pre-wrap break-words">
                                      {typeof executionDetail.output === 'string' 
                                        ? executionDetail.output 
                                        : JSON.stringify(executionDetail.output, null, 2)}
                                    </pre>
                                  </div>
                                </details>
                              </div>
                            )}

                            {/* Error Section */}
                            {executionDetail.error && (
                              <div>
                                <span className="font-medium text-red-600 text-xs">Error:</span>
                                <div className="mt-1 p-2 bg-red-50 dark:bg-red-900/10 border border-red-200 dark:border-red-900/30 rounded text-xs font-mono max-h-40 overflow-auto">
                                  <pre className="whitespace-pre-wrap break-words text-red-700 dark:text-red-400">
                                    {typeof executionDetail.error === 'string' 
                                      ? executionDetail.error 
                                      : JSON.stringify(executionDetail.error, null, 2)}
                                  </pre>
                                </div>
                              </div>
                            )}
                          </div>
                        ) : (
                          <div className="text-center py-4 text-muted-foreground text-xs">
                            Failed to load execution details
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))
              )}

              {hasMore && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleLoadMore}
                  disabled={isLoading}
                  className="w-full mt-2"
                >
                  {isLoading ? <Loader2 className="w-3 h-3 mr-2 animate-spin" /> : 'Load more'}
                </Button>
              )}
            </div>
          </ScrollArea>
        </div>
      )}
    </div>
  );
};