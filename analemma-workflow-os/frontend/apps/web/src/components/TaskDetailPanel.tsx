/**
 * Task Detail Panel Component
 * 
 * Task 상세 정보를 표시하는 Slide-over(Drawer) 형태의 패널입니다.
 * - Tab 1: 브리핑 (Business) - 진행 상황, 결과물, 의사결정 필요 사항
 * - Tab 2: 타임라인 (History) - 사고 과정 히스토리 + 체크포인트
 * - Tab 3: 디버그 (Technical) - Raw 로그 (권한별 노출)
 */

import React, { useState, useCallback } from 'react';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Skeleton } from '@/components/ui/skeleton';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  Bot,
  Clock,
  Loader2,
  AlertCircle,
  CheckCircle2,
  XCircle,
  MessageSquare,
  FileText,
  Image,
  Link,
  Database,
  ChevronRight,
  ChevronDown,
  AlertTriangle,
  HelpCircle,
  Terminal,
  Play,
  Eye,
  Download,
  History,
  RotateCcw,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { TaskDetail, AgentThought, ArtifactPreview, PendingDecision, TimelineItem, RollbackRequest, BranchInfo, DecisionOption } from '@/lib/types';
import { useCheckpoints, useTimeMachine } from '@/hooks/useBriefingAndCheckpoints';
import { CheckpointTimeline } from './CheckpointTimeline';
import { RollbackDialog } from './RollbackDialog';

interface TaskDetailPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  task: TaskDetail | null | undefined;
  executionId?: string | null;  // 체크포인트 조회용
  loading?: boolean;
  showTechnicalTab?: boolean;
  technicalLogs?: any[];
  onResume?: (response: string) => void;
  onApprove?: () => void;
  onReject?: () => void;
  onSelect?: (option: { label: string; value: string; description?: string }) => void;
  onRollbackSuccess?: (result: BranchInfo) => void;
}

// ===== Sub Components =====

const ThoughtIcon: React.FC<{ type: string }> = ({ type }) => {
  const iconClass = 'w-4 h-4';
  switch (type) {
    case 'progress':
      return <Loader2 className={cn(iconClass, 'text-blue-500')} />;
    case 'decision':
      return <AlertCircle className={cn(iconClass, 'text-amber-500')} />;
    case 'question':
      return <HelpCircle className={cn(iconClass, 'text-purple-500')} />;
    case 'warning':
      return <AlertTriangle className={cn(iconClass, 'text-orange-500')} />;
    case 'success':
      return <CheckCircle2 className={cn(iconClass, 'text-green-500')} />;
    case 'error':
      return <XCircle className={cn(iconClass, 'text-red-500')} />;
    default:
      return <MessageSquare className={cn(iconClass, 'text-gray-500')} />;
  }
};

const ArtifactIcon: React.FC<{ type: string }> = ({ type }) => {
  const iconClass = 'w-5 h-5';
  switch (type) {
    case 'text':
      return <FileText className={cn(iconClass, 'text-blue-500')} />;
    case 'file':
      return <Download className={cn(iconClass, 'text-green-500')} />;
    case 'image':
      return <Image className={cn(iconClass, 'text-purple-500')} />;
    case 'data':
      return <Database className={cn(iconClass, 'text-orange-500')} />;
    case 'link':
      return <Link className={cn(iconClass, 'text-cyan-500')} />;
    default:
      return <FileText className={cn(iconClass, 'text-gray-500')} />;
  }
};

const ThoughtHistoryItem = React.memo<React.FC<{ thought: AgentThought }>>(({ thought }) => {
  const formatTime = (isoString: string) => {
    try {
      const date = new Date(isoString);
      return date.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return '';
    }
  };

  return (
    <div className="flex gap-3 py-2">
      <div className="flex-shrink-0 mt-0.5">
        <ThoughtIcon type={thought.thought_type} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-foreground">{thought.message}</p>
        {thought.technical_detail && (
          <p className="text-xs text-muted-foreground mt-1 font-mono bg-muted/50 p-1 rounded">
            {thought.technical_detail}
          </p>
        )}
        <p className="text-xs text-muted-foreground mt-1">
          {formatTime(thought.timestamp)}
          {thought.node_id && ` · ${thought.node_id}`}
        </p>
      </div>
    </div>
  );
});

const ArtifactCard = React.memo<React.FC<{ artifact: ArtifactPreview }>>(({ artifact }) => {
  return (
    <Card className="hover:shadow-sm transition-shadow">
      <CardContent className="p-3">
        <div className="flex items-start gap-3">
          <div className="flex-shrink-0 p-2 bg-muted rounded">
            <ArtifactIcon type={artifact.artifact_type} />
          </div>
          <div className="flex-1 min-w-0">
            <h4 className="text-sm font-medium line-clamp-1">{artifact.title}</h4>
            {artifact.preview_content && (
              <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                {artifact.preview_content}
              </p>
            )}
          </div>
          <Button variant="ghost" size="icon" className="flex-shrink-0">
            <Eye className="w-4 h-4" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
});

const PendingDecisionCard: React.FC<{ 
  decision: PendingDecision;
  onApprove?: () => void;
  onReject?: () => void;
  onSelect?: (option: DecisionOption) => void;
}> = ({ decision, onApprove, onReject, onSelect }) => {
  return (
    <Card className="border-amber-600/50 bg-amber-900/20 text-slate-100">
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2 text-amber-300">
          <AlertCircle className="w-5 h-5 text-amber-400" />
          결정이 필요합니다
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-sm font-medium mb-2 text-slate-100">{decision.question}</p>
        <p className="text-sm text-slate-400 mb-4">{decision.context}</p>
        
        {decision.options.length > 0 && (
          <div className="space-y-2 mb-4">
            {decision.options.map((opt, idx) => (
              <div 
                key={idx}
                className="p-2 border border-slate-600 rounded text-sm hover:bg-slate-700/50 cursor-pointer text-slate-100"
                onClick={() => onSelect?.(opt)}
              >
                <span className="font-medium">{opt.label}</span>
                {opt.description && (
                  <span className="text-slate-400 ml-2">{opt.description}</span>
                )}
              </div>
            ))}
          </div>
        )}
        
        <div className="flex gap-2">
          <Button onClick={onApprove} className="flex-1 bg-blue-600 hover:bg-blue-700 text-white">
            승인하고 계속
          </Button>
          <Button variant="outline" onClick={onReject} className="border-slate-600 text-slate-300 hover:bg-slate-700">
            거부
          </Button>
        </div>
      </CardContent>
    </Card>
  );
};

// ===== Main Component =====

export const TaskDetailPanel: React.FC<TaskDetailPanelProps> = ({
  open,
  onOpenChange,
  task,
  executionId,
  loading,
  showTechnicalTab = false,
  technicalLogs = [],
  onResume,
  onApprove,
  onReject,
  onSelect,
  onRollbackSuccess,
}) => {
  const [activeTab, setActiveTab] = useState<string>('briefing');
  const [showThoughtHistory, setShowThoughtHistory] = useState(true);
  const [showCheckpoints, setShowCheckpoints] = useState(true);
  const [rollbackDialogOpen, setRollbackDialogOpen] = useState(false);
  const [selectedCheckpoint, setSelectedCheckpoint] = useState<TimelineItem | null>(null);

  // 패널이 열릴 때 activeTab을 초기화
  React.useEffect(() => {
    if (open) {
      setActiveTab('briefing');
    }
  }, [open]);
  
  // 체크포인트 훅 (executionId 있을 때만 활성화)
  const actualExecutionId = executionId || task?.execution_id;
  const { 
    timeline, 
    isLoading: checkpointsLoading,
    refetch: refetchCheckpoints 
  } = useCheckpoints({
    executionId: actualExecutionId || undefined,
    enabled: !!actualExecutionId && open,
    refetchInterval: task?.status === 'in_progress' ? 3000 : false,
  });
  
  const timeMachine = useTimeMachine({
    executionId: actualExecutionId || '',
    onRollbackSuccess: (result) => {
      setRollbackDialogOpen(false);
      refetchCheckpoints();
      onRollbackSuccess?.(result);
    },
  });
  
  // 체크포인트 선택 핸들러
  const selectCheckpoint = timeMachine.selectCheckpoint;
  const handleCheckpointSelect = useCallback((item: TimelineItem) => {
    selectCheckpoint(item.checkpoint_id);
  }, [selectCheckpoint]);
  
  const loadPreview = timeMachine.loadPreview;
  const handleRollbackClick = useCallback((item: TimelineItem) => {
    setSelectedCheckpoint(item);
    setRollbackDialogOpen(true);
    loadPreview(item.checkpoint_id);
  }, [loadPreview]);
  
  const rollback = timeMachine.rollback;
  const handleRollbackExecute = useCallback(async (request: RollbackRequest) => {
    return await rollback(request);
  }, [rollback]);
  
  if (!open) return null;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-xl p-0 bg-slate-900 border-l border-slate-700">
        {loading ? (
          <div className="p-6 space-y-4">
            <Skeleton className="h-8 w-3/4 bg-slate-700" />
            <Skeleton className="h-4 w-1/2 bg-slate-700" />
            <Skeleton className="h-32 w-full bg-slate-700" />
            <Skeleton className="h-24 w-full bg-slate-700" />
          </div>
        ) : task ? (
          <div className="flex flex-col h-full">
            {/* 헤더 */}
            <SheetHeader className="p-6 pb-4 border-b border-slate-700 bg-slate-800/50">
              <div className="flex items-start gap-3">
                <Avatar className="w-10 h-10">
                  {task.agent_avatar ? (
                    <AvatarImage src={task.agent_avatar} alt={task.agent_name} />
                  ) : null}
                  <AvatarFallback className="bg-slate-700 text-slate-100">
                    <Bot className="w-5 h-5" />
                  </AvatarFallback>
                </Avatar>
                <div className="flex-1">
                  <SheetTitle className="text-lg text-slate-100">{task.task_summary || '작업 상세'}</SheetTitle>
                  <p className="text-sm text-slate-400">{task.agent_name}</p>
                </div>
              </div>
              
              {/* 진행률 */}
              {task.status === 'in_progress' && (
                <div className="mt-4 space-y-1">
                  <div className="flex justify-between text-sm">
                    <span className="text-slate-300">{task.current_step_name || '진행 중'}</span>
                    <span className="font-medium text-slate-100">{task.progress_percentage}%</span>
                  </div>
                  <Progress value={task.progress_percentage} className="h-2 bg-slate-700 [&>div]:bg-blue-500" />
                </div>
              )}
              
              {/* 현재 사고 */}
              <div className="mt-3 p-3 bg-slate-800/50 rounded-lg border border-slate-700">
                <p className="text-sm text-slate-300">{task.current_thought || '준비 중...'}</p>
              </div>
            </SheetHeader>
            
            {/* 탭 콘텐츠 */}
            <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col">
              <TabsList className="mx-6 mt-4 grid grid-cols-3 bg-slate-800 border border-slate-700">
                <TabsTrigger value="briefing" className="text-slate-300 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-100">브리핑</TabsTrigger>
                <TabsTrigger value="history" className="text-slate-300 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-100">타임라인</TabsTrigger>
                {showTechnicalTab && (
                  <TabsTrigger value="debug" className="text-slate-300 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-100">
                    <Terminal className="w-4 h-4 mr-1" />
                    디버그
                  </TabsTrigger>
                )}
              </TabsList>
              
              {/* Tab 1: 브리핑 */}
              <TabsContent value="briefing" className="flex-1 mt-0">
                <ScrollArea className="h-full p-6">
                  <div className="space-y-6">
                    {/* 의사결정 대기 (있을 경우) */}
                    {task.pending_decision && (
                      <PendingDecisionCard 
                        decision={task.pending_decision}
                        onApprove={onApprove}
                        onReject={onReject}
                        onSelect={onSelect}
                      />
                    )}
                    
                    {/* 에러 메시지 */}
                    {task.error_message && (
                      <Card className="border-red-600/50 bg-red-900/20 text-slate-100">
                        <CardContent className="p-4">
                          <div className="flex items-start gap-3">
                            <XCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
                            <div>
                              <p className="text-sm font-medium text-red-300">{task.error_message}</p>
                              {task.error_suggestion && (
                                <p className="text-sm text-red-400 mt-1">{task.error_suggestion}</p>
                              )}
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    )}
                    
                    {/* 결과물 */}
                    {task.artifacts.length > 0 && (
                      <div>
                        <h3 className="text-sm font-semibold mb-3 flex items-center gap-2 text-slate-100">
                          <FileText className="w-4 h-4 text-slate-400" />
                          생성된 결과물 ({task.artifacts.length})
                        </h3>
                        <div className="space-y-2">
                          {task.artifacts.map((artifact) => (
                            <Card key={artifact.artifact_id} className="hover:shadow-sm transition-shadow bg-slate-800/50 border-slate-700 text-slate-100">
                              <CardContent className="p-3">
                                <div className="flex items-start gap-3">
                                  <div className="flex-shrink-0 p-2 bg-slate-700 rounded">
                                    <ArtifactIcon type={artifact.artifact_type} />
                                  </div>
                                  <div className="flex-1 min-w-0">
                                    <h4 className="text-sm font-medium line-clamp-1 text-slate-100">{artifact.title}</h4>
                                    {artifact.preview_content && (
                                      <p className="text-xs text-slate-400 mt-1 line-clamp-2">
                                        {artifact.preview_content}
                                      </p>
                                    )}
                                  </div>
                                  <Button variant="ghost" size="icon" className="flex-shrink-0 text-slate-400 hover:text-slate-100 hover:bg-slate-700">
                                    <Eye className="w-4 h-4" />
                                  </Button>
                                </div>
                              </CardContent>
                            </Card>
                          ))}
                        </div>
                      </div>
                    )}
                    
                    {/* 토큰 사용량 */}
                    {task.token_usage && (
                      <div className="text-xs text-slate-500 bg-slate-800/50 p-3 rounded border border-slate-700">
                        토큰 사용: 입력 {task.token_usage.input?.toLocaleString() || 0} / 
                        출력 {task.token_usage.output?.toLocaleString() || 0}
                      </div>
                    )}
                  </div>
                </ScrollArea>
              </TabsContent>
              
              {/* Tab 2: 타임라인 */}
              <TabsContent value="history" className="flex-1 mt-0">
                <ScrollArea className="h-full p-6">
                  <div className="space-y-4">
                    {/* 체크포인트 섹션 */}
                    {actualExecutionId && (
                      <Collapsible open={showCheckpoints} onOpenChange={setShowCheckpoints}>
                        <CollapsibleTrigger asChild>
                          <Button variant="ghost" className="w-full justify-between p-0 h-auto hover:bg-transparent">
                            <div className="flex items-center gap-2 text-sm font-semibold">
                              <History className="w-4 h-4 text-primary" />
                              체크포인트
                              {timeline.length > 0 && (
                                <Badge variant="secondary" className="ml-1">{timeline.length}</Badge>
                              )}
                            </div>
                            {showCheckpoints ? (
                              <ChevronDown className="w-4 h-4 text-muted-foreground" />
                            ) : (
                              <ChevronRight className="w-4 h-4 text-muted-foreground" />
                            )}
                          </Button>
                        </CollapsibleTrigger>
                        <CollapsibleContent className="mt-3">
                          <CheckpointTimeline
                            items={timeline}
                            loading={checkpointsLoading}
                            selectedId={timeMachine.selectedCheckpointId}
                            compareId={timeMachine.compareCheckpointId}
                            onSelect={handleCheckpointSelect}
                            onRollback={handleRollbackClick}
                            onCompare={(item) => timeMachine.selectCompare(item.checkpoint_id)}
                            compact
                          />
                        </CollapsibleContent>
                      </Collapsible>
                    )}
                    
                    {/* 사고 히스토리 섹션 */}
                    <Collapsible open={showThoughtHistory} onOpenChange={setShowThoughtHistory}>
                      <CollapsibleTrigger asChild>
                        <Button variant="ghost" className="w-full justify-between p-0 h-auto hover:bg-transparent">
                          <div className="flex items-center gap-2 text-sm font-semibold">
                            <MessageSquare className="w-4 h-4 text-primary" />
                            에이전트 사고 과정
                            {task.thought_history.length > 0 && (
                              <Badge variant="secondary" className="ml-1">{task.thought_history.length}</Badge>
                            )}
                          </div>
                          {showThoughtHistory ? (
                            <ChevronDown className="w-4 h-4 text-muted-foreground" />
                          ) : (
                            <ChevronRight className="w-4 h-4 text-muted-foreground" />
                          )}
                        </Button>
                      </CollapsibleTrigger>
                      <CollapsibleContent className="mt-3">
                        <div className="space-y-1">
                          {task.thought_history.length > 0 ? (
                            task.thought_history.map((thought, idx) => (
                              <React.Fragment key={thought.thought_id}>
                                <ThoughtHistoryItem thought={thought} />
                                {idx < task.thought_history.length - 1 && (
                                  <Separator className="my-1" />
                                )}
                              </React.Fragment>
                            ))
                          ) : (
                            <div className="text-center py-4 text-muted-foreground">
                              <MessageSquare className="w-6 h-6 mx-auto mb-2 opacity-30" />
                              <p className="text-sm">아직 기록된 활동이 없습니다.</p>
                            </div>
                          )}
                        </div>
                      </CollapsibleContent>
                    </Collapsible>
                  </div>
                </ScrollArea>
              </TabsContent>
              
              {/* Tab 3: 디버그 (권한별) */}
              {showTechnicalTab && (
                <TabsContent value="debug" className="flex-1 mt-0">
                  <ScrollArea className="h-full p-6">
                    <div className="space-y-2">
                      <div className="flex items-center justify-between mb-4">
                        <h3 className="text-sm font-semibold flex items-center gap-2">
                          <Terminal className="w-4 h-4" />
                          시스템 로그
                        </h3>
                        <Badge variant="outline" className="text-xs">
                          Developer Mode
                        </Badge>
                      </div>
                      
                      {(task.technical_logs || technicalLogs).length > 0 ? (
                        <div className="font-mono text-xs space-y-1 bg-muted p-3 rounded">
                          {(task.technical_logs || technicalLogs).map((log, idx) => (
                            <div key={idx} className="py-1 border-b border-muted-foreground/10 last:border-0">
                              <span className="text-muted-foreground">
                                [{new Date(log.timestamp).toLocaleTimeString()}]
                              </span>{' '}
                              <span className="text-primary">{log.node_id}</span>
                              {log.duration && (
                                <span className="text-muted-foreground ml-2">
                                  ({log.duration}ms)
                                </span>
                              )}
                              {log.error && (
                                <div className="text-red-500 mt-1 pl-4">
                                  Error: {JSON.stringify(log.error)}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="text-center py-8 text-muted-foreground">
                          <Terminal className="w-8 h-8 mx-auto mb-2 opacity-30" />
                          <p>기술 로그가 없습니다.</p>
                        </div>
                      )}
                    </div>
                  </ScrollArea>
                </TabsContent>
              )}
            </Tabs>
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-muted-foreground">
            Task를 선택하세요.
          </div>
        )}
      </SheetContent>
      
      {/* 롤백 다이얼로그 */}
      <RollbackDialog
        open={rollbackDialogOpen}
        onOpenChange={setRollbackDialogOpen}
        targetCheckpoint={selectedCheckpoint}
        preview={timeMachine.preview}
        loading={timeMachine.isPreviewLoading}
        onPreview={async (checkpointId) => {
          await timeMachine.loadPreview(checkpointId);
        }}
        onExecute={handleRollbackExecute}
        onSuccess={onRollbackSuccess}
      />
    </Sheet>
  );
};

export default React.memo(TaskDetailPanel);
