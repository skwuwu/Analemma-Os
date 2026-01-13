import React, { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import StatusBadge from '@/components/StatusBadge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Card, CardContent } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { Activity, X } from 'lucide-react';
import { NotificationItem } from '@/lib/types';
import type { HistoryEntry } from '@/lib/types';
import { ActiveWorkflowDetail } from './ActiveWorkflowDetail';
import { HistoryWorkflowDetail } from './HistoryWorkflowDetail';


interface ResumeWorkflowRequest {
  execution_id?: string;
  conversation_id: string;
  user_input: Record<string, unknown>;
}

interface Props {
  selectedExecutionId: string | null;
  isNotificationSelection: boolean;
  selectedNotification: NotificationItem | null;
  selectedWorkflowTimeline: NotificationItem[];
  latestStatus: NotificationItem | null;
  responseText: string;
  setResponseText: (s: string) => void;
  sanitizeResumePayload: (workflow: NotificationItem, response: string) => ResumeWorkflowRequest;
  resumeWorkflow: (payload: ResumeWorkflowRequest) => any;
  isResuming?: boolean;
  stopExecution: (executionId: string) => void;
  isStopping?: boolean;
  fetchExecutionTimeline: (id: string, force?: boolean) => Promise<NotificationItem[]>;
  formatFullTimestamp: (t: number) => string;
  formatTimestamp: (t: number) => string;
  formatElapsedTime: (s: number) => string;
  formatTimeRemaining: (s?: number) => string;
  calculateProgress: (w: NotificationItem | null) => number;
  setSelectedExecutionId: (id: string | null) => void;
  selectedHistoryEntry?: HistoryEntry | null;
  deleteExecution?: (executionArn: string) => Promise<void> | void;
  isDeleting?: boolean;
  executionSummary?: {
    executionArn?: string;
    name?: string | null;
    status?: string | null;
    created_at?: number | null;
    [key: string]: any;
  } | null;
  isHistoryContext?: boolean;
}

function WorkflowDetailPaneInner(props: Props) {
  const {
    selectedExecutionId,
    isNotificationSelection,
    selectedNotification,
    selectedWorkflowTimeline,
    latestStatus,
    responseText,
    setResponseText,
    sanitizeResumePayload,
    resumeWorkflow,
    stopExecution,
    fetchExecutionTimeline,
    formatFullTimestamp,
    formatTimestamp,
    setSelectedExecutionId,
    deleteExecution,
    executionSummary,
    isHistoryContext
  } = props;

  // When execution selection changes, ensure timeline is loaded (non-forced)
  useEffect(() => {
    if (!isNotificationSelection && selectedExecutionId) {
      void fetchExecutionTimeline(selectedExecutionId, false);
    }
  }, [isNotificationSelection, selectedExecutionId, fetchExecutionTimeline]);

  // 1. Notification Selection View
  if (isNotificationSelection && selectedNotification) {
    return (
      <>
        <div className="p-6 border-b">
          <div className="flex items-start justify-between mb-4">
            <div className="flex-1">
              <h2 className="text-2xl font-semibold mb-2">
                {selectedNotification.workflow_name || selectedNotification.action || 'Notification'}
              </h2>
              <div className="flex items-center gap-3">
                <StatusBadge status={selectedNotification.status} />
                {selectedNotification.action && (
                  <Badge variant="secondary">{selectedNotification.action}</Badge>
                )}
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSelectedExecutionId(null)}
            >
              <X className="w-4 h-4" />
            </Button>
          </div>
        </div>

        <ScrollArea className="flex-1 p-6">
          <div className="space-y-6 max-w-3xl">
            <div>
              <h3 className="font-semibold mb-3">Execution / Notification Information</h3>
              <Card>
                <CardContent className="p-4 space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Execution ID:</span>
                    <code className="text-xs bg-muted px-2 py-1 rounded">
                      {(selectedNotification.execution_id || selectedNotification.payload?.execution_id)?.split(':').pop() || 'N/A'}
                    </code>
                  </div>
                  <Separator />
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Conversation ID:</span>
                    <code className="text-xs bg-muted px-2 py-1 rounded">
                      {selectedNotification.conversation_id || 'N/A'}
                    </code>
                  </div>
                  <Separator />
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Started:</span>
                    <span>
                      {selectedNotification.start_time
                        ? formatFullTimestamp(selectedNotification.start_time * 1000)
                        : formatFullTimestamp(selectedNotification.receivedAt)
                      }
                    </span>
                  </div>
                  <Separator />
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Last Update:</span>
                    <span>
                      {selectedNotification.last_update_time
                        ? formatFullTimestamp(selectedNotification.last_update_time * 1000)
                        : formatTimestamp(selectedNotification.receivedAt)
                      }
                    </span>
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        </ScrollArea>
      </>
    );
  }

  // 2. History Context View
  if (isHistoryContext && executionSummary) {
    return (
      <HistoryWorkflowDetail
        executionSummary={executionSummary}
        selectedWorkflowTimeline={selectedWorkflowTimeline}
        deleteExecution={deleteExecution}
        onClose={() => setSelectedExecutionId(null)}
        onShowFullHistory={() => {
          if (selectedExecutionId) {
            void fetchExecutionTimeline(selectedExecutionId, true);
          }
        }}
      />
    );
  }

  // 3. Active Context View
  if (selectedExecutionId && latestStatus) {
    return (
      <ActiveWorkflowDetail
        selectedExecutionId={selectedExecutionId}
        latestStatus={latestStatus}
        selectedWorkflowTimeline={selectedWorkflowTimeline}
        stopExecution={stopExecution}
        resumeWorkflow={resumeWorkflow}
        sanitizeResumePayload={sanitizeResumePayload}
        responseText={responseText}
        setResponseText={setResponseText}
        onClose={() => setSelectedExecutionId(null)}
      />
    );
  }

  // Default / Empty State
  return (
    <div className="flex-1 flex items-center justify-center text-muted-foreground">
      <div className="text-center">
        <Activity className="w-16 h-16 mx-auto mb-4 opacity-50" />
        <div className="text-lg font-medium">Select a workflow</div>
        <div className="text-sm mt-1">Choose a workflow from the list to view details</div>
      </div>
    </div>
  );
}

export const WorkflowDetailPane = React.memo(WorkflowDetailPaneInner);
export default WorkflowDetailPane;
