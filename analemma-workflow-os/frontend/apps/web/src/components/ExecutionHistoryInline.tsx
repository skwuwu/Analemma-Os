import React from 'react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useWorkflowApi, useExecutionHistory } from '@/hooks/useWorkflowApi';
import type { HistoryEntry } from '@/lib/types';

interface ExecutionHistoryInlineProps {
  executionArn: string;
  // Callback when a history entry is selected from the list
  onSelectEntry?: (payload: { entry: HistoryEntry; key: string }) => void;
  // Currently selected entry id (optional) to allow external control/highlight
  selectedEntryKey?: string | null;
}

const ExecutionHistoryInline: React.FC<ExecutionHistoryInlineProps> = ({ executionArn, onSelectEntry, selectedEntryKey = null }) => {
  const { data, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } = useExecutionHistory(executionArn);

  const history = data?.pages.flatMap(page => page.history) || [];

  const formatTimestamp = (timestamp: number | string) => {
    const ts = typeof timestamp === 'string' 
      ? (isNaN(Number(timestamp)) ? new Date(timestamp).getTime() : Number(timestamp))
      : timestamp;
    return new Date(ts).toLocaleString('ko-KR');
  };

  const formatDuration = (duration: number) => {
    return `${duration}ms`;
  };

  const safeParseJson = (data: any) => {
    if (typeof data === 'string') {
      try {
        return JSON.parse(data);
      } catch {
        return { raw: data };
      }
    }
    return data;
  };


  return (
    <ScrollArea className="h-96">
      {isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="border rounded-lg p-4">
              <div className="flex justify-between items-start mb-2">
                <Skeleton className="h-6 w-20" />
                <Skeleton className="h-4 w-16" />
              </div>
              <div className="grid grid-cols-2 gap-4 text-sm mb-2">
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-4 w-32" />
              </div>
              <Skeleton className="h-20 w-full" />
            </div>
          ))}
        </div>
      ) : history.length === 0 ? (
        <div className="text-center py-4 text-muted-foreground">No history available</div>
      ) : (
        <div className="space-y-2">
          {/* Render a compact clickable list on the left; details are shown in the right pane via callback */}
          {history.map((entry: HistoryEntry, index: number) => {
            const key = `${entry.entered_at}-${entry.state_name}-${index}`;
            const isSelected = selectedEntryKey === key;
            return (
              <button
                  key={key}
                  onClick={() => {
                    console.debug('[ExecutionHistoryInline] select entry', { key, state: entry.state_name });
                    onSelectEntry?.({ entry, key });
                  }}
                  className={`w-full text-left border rounded-lg p-3 transition ${isSelected ? 'ring-2 ring-primary bg-primary/5' : 'hover:bg-muted'}`}
                >
                <div className="flex justify-between items-start mb-1">
                  <Badge variant="outline">{entry.state_name}</Badge>
                  <div className="text-sm text-muted-foreground">{formatDuration(entry.duration)}</div>
                </div>
                <div className="text-sm text-muted-foreground">
                  <div><strong>Entered:</strong> {formatTimestamp(entry.entered_at)}</div>
                  <div><strong>Exited:</strong> {entry.exited_at ? formatTimestamp(entry.exited_at) : '—'}</div>
                </div>
              </button>
            );
          })}
          {hasNextPage && (
            <div className="text-center py-4">
              <Button
                variant="outline"
                onClick={() => fetchNextPage()}
                disabled={isFetchingNextPage}
              >
                {isFetchingNextPage ? 'Loading...' : 'Load more'}
              </Button>
            </div>
          )}
        </div>
      )}
    </ScrollArea>
  );
};

export default ExecutionHistoryInline;