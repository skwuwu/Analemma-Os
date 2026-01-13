import React, { useState } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog';
import StatusBadge from '@/components/StatusBadge';
import { NoExecutionsEmpty } from '@/components/ui/empty-state';
import { LoadingSpinner } from '@/components/ui/loading';
import { Skeleton } from '@/components/ui/skeleton';

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

const ExecutionList: React.FC<Props> = ({ executions, selectedExecutionId, onSelect, onLoadMore, isLoading, onDelete }) => {
  // Filter out running executions for the history tab
  const historyExecutions = executions?.filter(ex => ex.status !== 'RUNNING') || [];

  // Loading state with Skeleton
  if (isLoading && historyExecutions.length === 0) {
    return (
      <div className="p-4 space-y-3">
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-16 w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (historyExecutions.length === 0) {
    return <NoExecutionsEmpty />;
  }

  const handleDelete = (executionArn: string) => {
    if (onDelete) {
      onDelete(executionArn);
    }
  };

  // Threshold to distinguish between seconds and milliseconds timestamps
  const TIMESTAMP_SECONDS_THRESHOLD = 10000000000;

  const formatFullTimestamp = (timestamp: string | number | null) => {
    if (!timestamp && timestamp !== 0) return '—';
    let ts: number;
    if (typeof timestamp === 'string') {
      ts = new Date(timestamp).getTime();
    } else {
      ts = timestamp < TIMESTAMP_SECONDS_THRESHOLD ? timestamp * 1000 : timestamp;
    }
    // Check for invalid dates
    if (isNaN(ts)) return '—';
    return new Date(ts).toLocaleString('ko-KR', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const getDisplayName = (ex: Execution) => {
    if (ex.name) return ex.name;
    if (ex.workflowId) return ex.workflowId;
    return 'Unknown Workflow';
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="p-4 space-y-3">
        {historyExecutions.map((ex) => {
          const displayName = getDisplayName(ex);
          // Use executionArn as the unique identifier for selection
          const id = ex.executionArn;
          const isSelected = selectedExecutionId === id;

          if (!id) return null; // Skip invalid items without ARN

          return (
            <Card
              key={id}
              className={`cursor-pointer ${isSelected ? 'border-primary bg-primary/5' : 'hover:bg-accent'}`}
              onClick={() => onSelect(id)}
              title={displayName}
            >
              <CardContent className="p-3">
                <div className="flex justify-between items-center">
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm truncate">{displayName}</div>
                    <div className="text-xs text-muted-foreground">{ex.created_at ? formatFullTimestamp(ex.created_at) : '—'}</div>
                  </div>
                  <div className="ml-2 flex items-center gap-2">
                    <StatusBadge status={ex.status} />
                    {onDelete && (
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button variant="ghost" size="sm" onClick={(e) => e.stopPropagation()}>
                            Delete
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>Delete Execution</AlertDialogTitle>
                            <AlertDialogDescription>
                              Are you sure you want to delete this execution? This action cannot be undone.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel onClick={(e) => e.stopPropagation()}>Cancel</AlertDialogCancel>
                            <AlertDialogAction onClick={(e) => {
                              e.stopPropagation();
                              handleDelete(id);
                            }}>
                              Delete
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}

        {onLoadMore && (
          <div className="px-4 py-2">
            <div className="border-t mt-2 pt-2">
              <Button variant="outline" size="sm" className="w-full" onClick={onLoadMore} disabled={isLoading}>
                {isLoading ? 'Loading...' : 'Load more'}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default React.memo(ExecutionList);
