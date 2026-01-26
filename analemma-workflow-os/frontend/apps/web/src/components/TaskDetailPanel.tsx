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
  Zap,
  Search
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { motion, AnimatePresence } from 'framer-motion';
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
      return <Loader2 className={cn(iconClass, 'text-blue-500 animate-spin')} />;
    case 'decision':
      return <AlertCircle className={cn(iconClass, 'text-amber-500')} />;
    case 'question':
      return <HelpCircle className={cn(iconClass, 'text-purple-500')} />;
    case 'warning':
      return <AlertTriangle className={cn(iconClass, 'text-orange-500')} />;
    case 'success':
      return <CheckCircle2 className={cn(iconClass, 'text-emerald-500')} />;
    case 'error':
      return <XCircle className={cn(iconClass, 'text-rose-500')} />;
    default:
      return <MessageSquare className={cn(iconClass, 'text-slate-400')} />;
  }
};

const ArtifactIcon: React.FC<{ type: string }> = ({ type }) => {
  const iconClass = 'w-5 h-5';
  switch (type) {
    case 'text':
      return <FileText className={cn(iconClass, 'text-blue-500')} />;
    case 'file':
      return <Download className={cn(iconClass, 'text-emerald-500')} />;
    case 'image':
      return <Image className={cn(iconClass, 'text-purple-500')} />;
    case 'data':
      return <Database className={cn(iconClass, 'text-orange-500')} />;
    case 'link':
      return <Link className={cn(iconClass, 'text-cyan-500')} />;
    default:
      return <FileText className={cn(iconClass, 'text-slate-500')} />;
  }
};

const ThoughtHistoryItem = React.memo(({ thought, isLast }: { thought: AgentThought; isLast?: boolean }) => {
  const formatTime = (isoString: string) => {
    try {
      const date = new Date(isoString);
      return date.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return '';
    }
  };

  return (
    <div className="relative flex gap-5">
      {/* 🧬 Chain of Thought Connector Line */}
      <div className="flex flex-col items-center flex-shrink-0">
        <div className="z-10 bg-slate-800 p-1.5 rounded-full border border-slate-700 shadow-sm ring-4 ring-slate-900">
          <ThoughtIcon type={thought.thought_type} />
        </div>
        {!isLast && (
          <div className="w-0.5 h-full bg-slate-700/40 rounded-full my-1" />
        )}
      </div>

      <div className="flex-1 pb-8 min-w-0">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">
              {thought.node_id || 'System Core'}
            </span>
            {thought.is_important && (
              <Badge className="bg-amber-500/10 text-amber-500 border-amber-500/20 text-[8px] font-black h-4 px-1 p-0">CRITICAL</Badge>
            )}
          </div>
          <time className="text-[10px] text-slate-500 font-mono font-bold bg-slate-800/50 px-1.5 py-0.5 rounded border border-slate-700/50">
            {formatTime(thought.timestamp)}
          </time>
        </div>
        <p className="text-[13px] font-medium text-slate-200 leading-relaxed tracking-tight break-words">
          {thought.message}
        </p>

        {thought.technical_detail && (
          <div className="mt-3 p-3 bg-black/40 rounded-xl border border-slate-700/50 font-mono text-[11px] text-blue-400/80 shadow-inner group relative overflow-hidden">
            <div className="absolute top-0 right-0 p-1 bg-blue-500/10 text-[8px] font-black text-blue-500 uppercase rounded-bl-lg">LOG_TRACE</div>
            <code className="block whitespace-pre-wrap break-all leading-normal">
              {thought.technical_detail}
            </code>
          </div>
        )}
      </div>
    </div>
  );
});

const ArtifactCard = React.memo(({ artifact }: { artifact: ArtifactPreview }) => {
  return (
    <Card className="group hover:shadow-xl hover:shadow-primary/5 transition-all duration-300 bg-slate-800/50 border-slate-700/50 hover:border-primary/20 overflow-hidden">
      <CardContent className="p-0">
        <div className="flex items-stretch min-h-[80px]">
          {/* 🖼️ Enhanced Artifact Preview */}
          <div className="w-24 bg-slate-900/50 flex-shrink-0 border-r border-slate-700/30 flex items-center justify-center relative overflow-hidden">
            {artifact.artifact_type === 'image' && artifact.thumbnail_url ? (
              <img
                src={artifact.thumbnail_url}
                className="w-full h-full object-cover transition-transform group-hover:scale-110"
                alt={artifact.title}
              />
            ) : (
              <div className="flex flex-col items-center gap-1 opacity-40 group-hover:opacity-100 transition-opacity">
                <ArtifactIcon type={artifact.artifact_type} />
              </div>
            )}
            <div className="absolute inset-0 bg-primary/5 opacity-0 group-hover:opacity-100 transition-opacity" />
          </div>

          <div className="flex-1 p-4 min-w-0 pr-12 relative flex flex-col justify-center">
            <h4 className="text-[13px] font-black tracking-tight text-slate-100 line-clamp-1 mb-1">
              {artifact.title}
            </h4>
            <div className="flex items-center gap-2">
              <span className="text-[9px] font-black uppercase text-slate-500 tracking-widest">{artifact.artifact_type}</span>
              {artifact.preview_content && (
                <p className="text-[11px] font-medium text-slate-400 line-clamp-1 opacity-70">
                  {artifact.preview_content}
                </p>
              )}
            </div>
          </div>

          <div className="absolute top-1/2 -translate-y-1/2 right-3">
            <Button size="icon" variant="ghost" className="h-8 w-8 text-slate-400 hover:text-primary hover:bg-primary/10 rounded-full transition-colors">
              <Eye className="w-4 h-4" />
            </Button>
          </div>
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
    <Card className="border-amber-500/30 bg-amber-500/5 text-slate-100 rounded-[1.5rem] overflow-hidden shadow-2xl shadow-amber-500/5">
      <CardHeader className="pb-4 pt-6 px-6 bg-amber-500/5 border-b border-amber-500/10">
        <CardTitle className="text-base flex items-center gap-3 text-amber-500 font-black tracking-tight">
          <div className="p-2 bg-amber-500 text-white rounded-xl shadow-lg shadow-amber-500/20">
            <AlertCircle className="w-5 h-5" />
          </div>
          GOVERNANCE_INTERVENTION_REQUIRED
        </CardTitle>
      </CardHeader>
      <CardContent className="p-6">
        <div className="space-y-4 mb-6">
          <h5 className="text-[15px] font-black text-slate-100 leading-tight">{decision.question}</h5>
          <p className="text-[13px] font-medium text-slate-400 leading-relaxed italic border-l-2 border-amber-500/30 pl-4">
            {decision.context}
          </p>
        </div>

        {decision.options.length > 0 && (
          <div className="grid grid-cols-1 gap-2 mb-6">
            {decision.options.map((opt, idx) => (
              <Button
                key={idx}
                variant="outline"
                className="justify-start h-auto py-3 px-4 border-slate-700 bg-slate-800/40 hover:bg-slate-700 hover:border-amber-500/50 group text-left transition-all rounded-xl"
                onClick={() => onSelect?.(opt)}
              >
                <div className="flex flex-col gap-0.5">
                  <span className="text-[13px] font-black text-slate-200 group-hover:text-amber-500">{opt.label}</span>
                  {opt.description && (
                    <span className="text-[11px] font-bold text-slate-500 group-hover:text-slate-400 tracking-tight">{opt.description}</span>
                  )}
                </div>
                <ChevronRight className="ml-auto w-4 h-4 text-slate-600 group-hover:text-amber-500 transition-transform group-hover:translate-x-1" />
              </Button>
            ))}
          </div>
        )}

        <div className="flex gap-3">
          <Button onClick={onApprove} className="flex-1 h-12 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 text-white font-black text-xs uppercase tracking-widest rounded-xl shadow-xl shadow-blue-600/20">
            Authorize Protocol
          </Button>
          <Button variant="ghost" onClick={onReject} className="h-12 px-6 font-black text-xs uppercase tracking-widest text-slate-400 hover:text-rose-500 hover:bg-rose-500/5 rounded-xl">
            Deny Access
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
      <SheetContent side="right" className="w-full sm:max-w-xl p-0 bg-slate-900 border-l border-slate-700/50 shadow-2xl">
        {loading ? (
          <div className="p-8 space-y-6">
            <div className="flex items-center gap-4 mb-4">
              <Skeleton className="h-12 w-12 rounded-2xl bg-slate-800" />
              <div className="space-y-2">
                <Skeleton className="h-6 w-48 bg-slate-800" />
                <Skeleton className="h-4 w-32 bg-slate-800" />
              </div>
            </div>
            <Skeleton className="h-24 w-full rounded-2xl bg-slate-800" />
            <Skeleton className="h-64 w-full rounded-2xl bg-slate-800" />
          </div>
        ) : task ? (
          <div className="flex flex-col h-full">
            {/* 헤더 */}
            <SheetHeader className="p-8 pb-6 border-b border-slate-800 bg-slate-950/30">
              <div className="flex items-start gap-4">
                <Avatar className="w-12 h-12 rounded-2xl border-2 border-slate-800 shadow-lg">
                  {task.agent_avatar ? (
                    <AvatarImage src={task.agent_avatar} alt={task.agent_name} />
                  ) : null}
                  <AvatarFallback className="bg-slate-800 text-slate-100">
                    <Bot className="w-6 h-6" />
                  </AvatarFallback>
                </Avatar>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <SheetTitle className="text-xl font-black tracking-tight text-white line-clamp-1">{task.task_summary || 'Protocol Narrative'}</SheetTitle>
                    {task.status === 'in_progress' && <div className="w-2 h-2 rounded-full bg-blue-500 animate-ping" />}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-black uppercase tracking-widest text-primary">{task.agent_name}</span>
                    <span className="text-[10px] font-bold text-slate-500">/ Session ID </span>
                    <code className="text-[10px] font-mono text-slate-500 bg-slate-800/50 px-1 rounded">{actualExecutionId?.split(':').pop()?.substring(0, 8)}</code>
                  </div>
                </div>
              </div>

              {/* 진행률 & 사고 동기화 */}
              <div className="mt-8 space-y-5">
                {task.status === 'in_progress' && (
                  <div className="space-y-2">
                    <div className="flex justify-between items-end">
                      <div className="flex flex-col">
                        <span className="text-[9px] font-black uppercase tracking-[0.2em] text-slate-500 mb-0.5">Vector Stage</span>
                        <span className="text-sm font-bold text-slate-200">{task.current_step_name || 'Active Reasoning'}</span>
                      </div>
                      <span className="text-xl font-black text-blue-500 tracking-tighter">{task.progress_percentage}%</span>
                    </div>
                    <div className="h-2 bg-slate-800 rounded-full overflow-hidden border border-slate-700/50">
                      <motion.div
                        initial={{ width: 0 }}
                        animate={{ width: `${task.progress_percentage}%` }}
                        className="h-full bg-gradient-to-r from-blue-600 via-blue-400 to-indigo-500"
                      />
                    </div>
                  </div>
                )}

                <div className="p-5 bg-black/40 rounded-2xl border border-slate-700/50 shadow-inner group transition-all hover:bg-black/60">
                  <div className="flex items-center gap-2 mb-3">
                    <Zap className="w-4 h-4 text-amber-500 fill-amber-500" />
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Neural Sync</span>
                  </div>
                  <p className="text-[13px] font-medium text-slate-300 leading-relaxed italic">
                    "{task.current_thought || 'Initialising cognition matrix...'}"
                  </p>
                </div>
              </div>
            </SheetHeader>

            {/* 탭 콘텐츠 설계 (Lazy 렌더링 최적화) */}
            <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
              <div className="px-8 mt-6">
                <TabsList className="w-full grid grid-cols-3 h-12 bg-slate-800/50 border border-slate-700/50 p-1.5 rounded-2xl">
                  <TabsTrigger value="briefing" className="rounded-xl font-black text-[11px] uppercase tracking-widest data-[state=active]:bg-slate-700 data-[state=active]:text-white data-[state=active]:shadow-lg transition-all">Report</TabsTrigger>
                  <TabsTrigger value="history" className="rounded-xl font-black text-[11px] uppercase tracking-widest data-[state=active]:bg-slate-700 data-[state=active]:text-white data-[state=active]:shadow-lg transition-all">Timeline</TabsTrigger>
                  {showTechnicalTab && (
                    <TabsTrigger value="debug" className="rounded-xl font-black text-[11px] uppercase tracking-widest data-[state=active]:bg-slate-700 data-[state=active]:text-white data-[state=active]:shadow-lg transition-all">
                      Debug
                    </TabsTrigger>
                  )}
                </TabsList>
              </div>

              {/* Tab 1: 브리핑 (Lazy) */}
              <TabsContent value="briefing" className="flex-1 mt-0 focus-visible:outline-none min-h-0">
                {activeTab === 'briefing' && (
                  <ScrollArea className="h-full p-8 px-8">
                    <div className="space-y-8 pb-12">
                      {/* 의사결정 대기 */}
                      <AnimatePresence mode="popLayout">
                        {task.pending_decision && (
                          <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}>
                            <PendingDecisionCard
                              decision={task.pending_decision}
                              onApprove={onApprove}
                              onReject={onReject}
                              onSelect={onSelect}
                            />
                          </motion.div>
                        )}
                      </AnimatePresence>

                      {/* 위험/에러 경보 */}
                      {task.error_message && (
                        <Card className="border-rose-500/30 bg-rose-500/5 text-slate-100 rounded-2xl overflow-hidden shadow-2xl">
                          <CardContent className="p-5">
                            <div className="flex items-start gap-4">
                              <div className="p-2 bg-rose-500 text-white rounded-xl shadow-lg shadow-rose-500/20">
                                <AlertTriangle className="w-5 h-5" />
                              </div>
                              <div className="min-w-0">
                                <h4 className="text-sm font-black text-rose-500 uppercase tracking-widest mb-1">Anomaly Detected</h4>
                                <p className="text-sm font-bold text-slate-200">{task.error_message}</p>
                                {task.error_suggestion && (
                                  <div className="mt-3 p-3 bg-white/5 rounded-xl border border-rose-500/20">
                                    <p className="text-xs font-semibold text-rose-300">
                                      <span className="font-black mr-2 opacity-50 uppercase tracking-tighter">RECOVERY:</span>
                                      {task.error_suggestion}
                                    </p>
                                  </div>
                                )}
                              </div>
                            </div>
                          </CardContent>
                        </Card>
                      )}

                      {/* 아카이브 결과물 */}
                      {task.artifacts.length > 0 && (
                        <div className="space-y-4">
                          <div className="flex items-center justify-between px-1">
                            <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 flex items-center gap-2">
                              <FileText className="w-4 h-4" />
                              Mission Artifact Archive
                            </h3>
                            <Badge className="bg-slate-800 text-slate-400 font-black h-5 px-2 border-slate-700">{task.artifacts.length}</Badge>
                          </div>
                          <div className="grid grid-cols-1 gap-3">
                            {task.artifacts.map((artifact) => (
                              <ArtifactCard key={artifact.artifact_id} artifact={artifact} />
                            ))}
                          </div>
                        </div>
                      )}

                      {/* 리소스 소비 지표 */}
                      {task.token_usage && (
                        <div className="p-4 rounded-2xl bg-slate-900 border border-slate-800 flex items-center justify-between shadow-inner">
                          <div className="flex items-center gap-3">
                            <div className="p-2 bg-slate-800 rounded-xl">
                              <Database className="w-4 h-4 text-slate-400" />
                            </div>
                            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Resource Consumption</span>
                          </div>
                          <div className="flex items-center gap-4 text-[11px] font-black font-mono text-slate-400">
                            <div className="flex flex-col items-end">
                              <span className="text-[8px] opacity-40 uppercase tracking-tighter">Inbound</span>
                              <span>{task.token_usage.input?.toLocaleString() || 0}</span>
                            </div>
                            <div className="w-px h-6 bg-slate-800" />
                            <div className="flex flex-col items-end">
                              <span className="text-[8px] opacity-40 uppercase tracking-tighter">Outbound</span>
                              <span className="text-primary">{task.token_usage.output?.toLocaleString() || 0}</span>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  </ScrollArea>
                )}
              </TabsContent>

              {/* Tab 2: 타임라인 (Lazy) */}
              <TabsContent value="history" className="flex-1 mt-0 focus-visible:outline-none min-h-0">
                {activeTab === 'history' && (
                  <ScrollArea className="h-full px-8">
                    <div className="space-y-12 pb-20 pt-8">
                      {/* 하이-피델리티 타임머신 체크포인트 */}
                      {actualExecutionId && (
                        <section className="space-y-6">
                          <div className="flex items-center justify-between px-1">
                            <div className="flex items-center gap-2">
                              <History className="w-4 h-4 text-primary" />
                              <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Temporal Synchronization Points</h3>
                            </div>
                            <button onClick={() => setShowCheckpoints(!showCheckpoints)} className="text-[10px] font-black uppercase text-primary hover:underline">
                              {showCheckpoints ? 'Fold Matrix' : 'Expand Matrix'}
                            </button>
                          </div>

                          <AnimatePresence>
                            {showCheckpoints && (
                              <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                className="overflow-hidden"
                              >
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
                              </motion.div>
                            )}
                          </AnimatePresence>
                        </section>
                      )}

                      <Separator className="bg-slate-800" />

                      {/* 가시적인 사고 타임라인 (Chain of Thought Fiber) */}
                      <section className="space-y-8">
                        <div className="flex items-center justify-between px-1">
                          <div className="flex items-center gap-2">
                            <MessageSquare className="w-4 h-4 text-blue-500" />
                            <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Neural Chain of Thought</h3>
                          </div>
                          <Badge className="bg-slate-800 text-slate-400 font-bold h-5 px-2 border-slate-700">{task.thought_history.length}</Badge>
                        </div>

                        <div className="relative ml-2">
                          {task.thought_history.length > 0 ? (
                            task.thought_history.map((thought, idx) => (
                              <ThoughtHistoryItem
                                key={thought.thought_id}
                                thought={thought}
                                isLast={idx === task.thought_history.length - 1}
                              />
                            ))
                          ) : (
                            <div className="flex flex-col items-center justify-center py-20 bg-slate-800/20 rounded-3xl border border-dashed border-slate-800">
                              <MessageSquare className="w-12 h-12 mb-4 text-slate-700 opacity-30" />
                              <p className="text-xs font-black uppercase tracking-widest text-slate-600">Pending Thought Matrix</p>
                            </div>
                          )}
                        </div>
                      </section>
                    </div>
                  </ScrollArea>
                )}
              </TabsContent>

              {/* Tab 3: 디버그 (Lazy) */}
              {showTechnicalTab && (
                <TabsContent value="debug" className="flex-1 mt-0 focus-visible:outline-none min-h-0">
                  {activeTab === 'debug' && (
                    <ScrollArea className="h-full px-8">
                      <div className="space-y-6 pt-8 pb-12">
                        <div className="flex items-center justify-between mb-2">
                          <h3 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 flex items-center gap-2">
                            <Terminal className="w-4 h-4" />
                            Core Engine Execution Logs
                          </h3>
                          <Badge className="bg-blue-500/10 text-blue-400 border-blue-500/20 font-black text-[9px] uppercase tracking-tighter">
                            Advanced Audit Mode
                          </Badge>
                        </div>

                        {(task.technical_logs || technicalLogs).length > 0 ? (
                          <div className="font-mono text-[11px] leading-relaxed space-y-2 bg-black/50 p-6 rounded-2xl border border-slate-800 shadow-inner">
                            {(task.technical_logs || technicalLogs).map((log, idx) => (
                              <div key={idx} className="pb-2 border-b border-slate-800/50 last:border-0 last:pb-0 group">
                                <div className="flex items-center justify-between opacity-50 group-hover:opacity-100 transition-opacity">
                                  <span className="text-slate-500 font-black">
                                    T={new Date(log.timestamp).toLocaleTimeString()}
                                  </span>
                                  <span className="text-blue-500 font-bold tracking-tighter px-1.5 py-0.5 bg-blue-500/5 rounded">{log.node_id}</span>
                                </div>
                                <div className="mt-1 flex items-center gap-2">
                                  {log.duration && (
                                    <span className="text-emerald-500/60 text-[9px] font-bold">
                                      Δ {log.duration}ms
                                    </span>
                                  )}
                                  {log.error ? (
                                    <Badge variant="destructive" className="h-4 p-0 px-1 text-[8px] font-black uppercase">FAILED</Badge>
                                  ) : (
                                    <Badge className="bg-emerald-500/10 text-emerald-500 border-emerald-500/10 h-4 p-0 px-1 text-[8px] font-black uppercase">OK</Badge>
                                  )}
                                </div>
                                {log.error && (
                                  <div className="text-rose-400 mt-2 p-3 bg-rose-500/5 rounded-xl border border-rose-500/10 text-[10px] overflow-auto">
                                    <span className="font-black uppercase tracking-tighter mr-2 opacity-50 underline">Exception:</span>
                                    {typeof log.error === 'string' ? log.error : JSON.stringify(log.error, null, 2)}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="flex flex-col items-center justify-center py-32 opacity-20">
                            <Terminal className="w-16 h-16 mb-4" />
                            <p className="text-xs font-black uppercase tracking-widest">Null Log Stream</p>
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                  )}
                </TabsContent>
              )}
            </Tabs>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-slate-500 gap-4 opacity-30">
            <Search className="w-16 h-16" />
            <p className="text-sm font-black uppercase tracking-widest">Select Mission for Insight</p>
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
