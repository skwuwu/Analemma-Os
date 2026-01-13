/**
 * Task Manager Page
 * 
 * 기존 WorkflowMonitor를 Task Manager로 리브랜딩한 페이지입니다.
 * 좌측 리스트와 우측 상세 정보(Bento Grid)로 구성된 2-Pane 레이아웃입니다.
 */

import React, { useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Home,
  Bell,
  Search,
  Filter,
  RefreshCw,
  CheckCircle2,
  Clock,
  AlertCircle,
  Loader2,
  LayoutGrid,
  List,
  Bot,
  XCircle,
  Slash
} from 'lucide-react';
import { toast } from 'sonner';

// Components
import { TaskBentoGrid } from '@/components/TaskBentoGrid';
import { OutcomeManagerModal } from '@/components/OutcomeManagerModal';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Progress } from '@/components/ui/progress';

// Hooks
import { useTaskManager } from '@/hooks/useTaskManager';
import { useNotifications } from '@/hooks/useNotifications';
import { useWorkflowApi } from '@/hooks/useWorkflowApi';
import type { TaskSummary, TaskStatus } from '@/lib/types';

interface TaskManagerProps {
  signOut?: () => void;
}

const StatusIcon: React.FC<{ status: TaskStatus }> = ({ status }) => {
  const iconProps = { className: 'w-4 h-4' };
  
  switch (status) {
    case 'queued':
      return <Clock {...iconProps} className="w-4 h-4 text-slate-500" />;
    case 'in_progress':
      return <Loader2 {...iconProps} className="w-4 h-4 text-blue-500 animate-spin" />;
    case 'pending_approval':
      return <AlertCircle {...iconProps} className="w-4 h-4 text-amber-500" />;
    case 'completed':
      return <CheckCircle2 {...iconProps} className="w-4 h-4 text-green-500" />;
    case 'failed':
      return <XCircle {...iconProps} className="w-4 h-4 text-red-500" />;
    case 'cancelled':
      return <Slash {...iconProps} className="w-4 h-4 text-gray-400" />;
    default:
      return <Clock {...iconProps} className="w-4 h-4 text-gray-500" />;
  }
};

const StatusBadge = ({ status }: { status: string }) => {
    const variants: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
      in_progress: 'default',
      completed: 'secondary',
      failed: 'destructive',
      queued: 'outline',
      pending_approval: 'default',
    };
    
    const colorClass = status === 'pending_approval' ? 'bg-amber-500 hover:bg-amber-600' : '';
  
    return (
      <Badge variant={variants[status] || 'outline'} className={`uppercase text-[10px] ${colorClass}`}>
        {status.replace('_', ' ')}
      </Badge>
    );
};

export const TaskManager: React.FC<TaskManagerProps> = ({ signOut }) => {
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [outcomeModalOpen, setOutcomeModalOpen] = useState(false);
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | undefined>(undefined);
  
  // 기존 notifications 훅 (WebSocket 연결 유지)
  const { notifications } = useNotifications();
  
  // Task Manager 훅
  const taskManager = useTaskManager({
    statusFilter: statusFilter === 'all' ? undefined : statusFilter,
    autoRefresh: true,
    showTechnicalLogs: false,
  });
  
  // API 훅
  const { resumeWorkflow } = useWorkflowApi();
  
  // 검색 필터링
  const filteredTasks = useMemo(() => {
    if (!searchQuery.trim()) return taskManager.tasks;
    
    const query = searchQuery.toLowerCase();
    return taskManager.tasks.filter(task =>
      task.task_summary?.toLowerCase().includes(query) ||
      task.agent_name?.toLowerCase().includes(query) ||
      task.workflow_name?.toLowerCase().includes(query)
    );
  }, [taskManager.tasks, searchQuery]);
  
  // Task 선택 핸들러
  const handleTaskClick = useCallback((task: TaskSummary) => {
    taskManager.selectTask(task.task_id);
  }, [taskManager]);

  const handleArtifactClick = useCallback((artifactId: string) => {
    setSelectedArtifactId(artifactId);
    setOutcomeModalOpen(true);
  }, []);
  
  // 통계
  const stats = useMemo(() => ({
    total: taskManager.tasks.length,
    inProgress: taskManager.inProgressTasks.length,
    pendingApproval: taskManager.pendingApprovalTasks.length,
    completed: taskManager.tasks.filter(t => t.status === 'completed').length,
  }), [taskManager.tasks, taskManager.inProgressTasks, taskManager.pendingApprovalTasks]);

  return (
    <div className="flex flex-col h-screen bg-slate-950 text-slate-100 overflow-hidden">
      {/* 헤더 */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-slate-800 bg-slate-900/50 backdrop-blur-sm shrink-0 h-14">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" onClick={() => navigate('/')} className="text-slate-400 hover:text-slate-100 hover:bg-slate-800">
            <Home className="w-5 h-5" />
          </Button>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold text-slate-100">Task Manager</h1>
            <Badge variant="outline" className="text-slate-400 border-slate-700">Beta</Badge>
          </div>
        </div>
        
        <div className="flex items-center gap-3">
          {/* 알림 배지 */}
          {stats.pendingApproval > 0 && (
            <Badge variant="destructive" className="animate-pulse bg-red-600 text-white border-red-500">
              <Bell className="w-3 h-3 mr-1" />
              {stats.pendingApproval} 승인 대기
            </Badge>
          )}
          
          <Button
            variant="outline"
            size="sm"
            onClick={() => taskManager.refreshList()}
            disabled={taskManager.isLoading}
            className="border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700 hover:text-slate-100 h-8"
          >
            <RefreshCw className={`w-3 h-3 mr-2 ${taskManager.isLoading ? 'animate-spin' : ''}`} />
            새로고침
          </Button>
          
          {signOut && (
            <Button variant="ghost" size="sm" onClick={signOut} className="text-slate-400 hover:text-slate-100 hover:bg-slate-800 h-8">
              로그아웃
            </Button>
          )}
        </div>
      </header>
      
      <ResizablePanelGroup direction="horizontal" className="flex-1">
        {/* 좌측 패널: 작업 목록 */}
        <ResizablePanel defaultSize={25} minSize={20} maxSize={40} className="bg-slate-900/30 border-r border-slate-800 flex flex-col">
            <div className="p-4 border-b border-slate-800 space-y-3">
                <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                    <Input
                        placeholder="작업 검색..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="pl-9 bg-slate-800 border-slate-700 text-slate-100 placeholder:text-slate-500 focus:border-slate-600 h-9 text-sm"
                    />
                </div>
                <div className="flex gap-2">
                    <Select value={statusFilter} onValueChange={setStatusFilter}>
                        <SelectTrigger className="w-full bg-slate-800 border-slate-700 text-slate-100 h-8 text-xs">
                            <Filter className="w-3 h-3 mr-2" />
                            <SelectValue placeholder="상태" />
                        </SelectTrigger>
                        <SelectContent className="bg-slate-800 border-slate-700">
                            <SelectItem value="all">모든 상태</SelectItem>
                            <SelectItem value="in_progress">진행 중</SelectItem>
                            <SelectItem value="pending_approval">승인 대기</SelectItem>
                            <SelectItem value="completed">완료</SelectItem>
                            <SelectItem value="failed">실패</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
            </div>

            <ScrollArea className="flex-1">
                <div className="p-3 space-y-2">
                    {taskManager.isLoading ? (
                        [1, 2, 3].map((i) => (
                            <Skeleton key={i} className="h-24 w-full bg-slate-800 rounded-lg" />
                        ))
                    ) : filteredTasks.length === 0 ? (
                        <div className="text-center py-10 text-slate-500 text-sm">
                            작업이 없습니다.
                        </div>
                    ) : (
                        filteredTasks.map((task) => (
                            <div
                                key={task.task_id}
                                onClick={() => handleTaskClick(task)}
                                className={`
                                    p-3 rounded-lg border cursor-pointer transition-all duration-200
                                    ${taskManager.selectedTaskId === task.task_id 
                                        ? 'bg-slate-800 border-sky-500/50 shadow-md' 
                                        : 'bg-slate-800/40 border-slate-700/50 hover:bg-slate-800 hover:border-slate-600'}
                                `}
                            >
                                <div className="flex justify-between items-start mb-2">
                                    <div className="flex items-center gap-2">
                                        <div className="w-6 h-6 rounded-full bg-slate-700 flex items-center justify-center shrink-0">
                                            <Bot className="w-3 h-3 text-slate-300" />
                                        </div>
                                        <span className="text-xs font-medium text-slate-200 truncate max-w-[120px]">
                                            {task.agent_name}
                                        </span>
                                    </div>
                                    <StatusIcon status={task.status} />
                                </div>
                                <h4 className="text-sm font-medium text-slate-100 mb-1 line-clamp-1">
                                    {task.task_summary || '작업 진행 중'}
                                </h4>
                                <div className="flex items-center justify-between text-[10px] text-slate-400 mt-2">
                                    <span>{task.current_step_name || '대기 중'}</span>
                                    <span>{task.progress_percentage}%</span>
                                </div>
                                <Progress value={task.progress_percentage} className="h-1 mt-1 bg-slate-700" />
                            </div>
                        ))
                    )}
                </div>
            </ScrollArea>
        </ResizablePanel>
        
        <ResizableHandle className="bg-slate-800" />
        
        {/* 우측 패널: 상세 정보 (Bento Grid) */}
        <ResizablePanel defaultSize={75} className="bg-slate-950">
            {taskManager.selectedTask ? (
                <div className="h-full flex flex-col">
                    <div className="px-6 py-4 border-b border-slate-800 flex justify-between items-center bg-slate-900/30">
                        <div>
                            <h2 className="text-xl font-bold text-slate-100 flex items-center gap-2">
                                {taskManager.selectedTask.task_summary}
                                <StatusBadge status={taskManager.selectedTask.status} />
                            </h2>
                            <p className="text-sm text-slate-400 mt-1">
                                ID: {taskManager.selectedTask.task_id} • Workflow: {taskManager.selectedTask.workflow_name}
                            </p>
                        </div>
                        <div className="flex gap-2">
                            {/* Action Buttons can go here */}
                        </div>
                    </div>
                    <ScrollArea className="flex-1 bg-slate-950/50">
                        <TaskBentoGrid task={taskManager.selectedTask} onArtifactClick={handleArtifactClick} />
                    </ScrollArea>
                </div>
            ) : (
                <div className="h-full flex flex-col items-center justify-center text-slate-500">
                    <LayoutGrid className="w-16 h-16 mb-4 opacity-20" />
                    <p className="text-lg font-medium">작업을 선택하여 상세 정보를 확인하세요</p>
                    <p className="text-sm opacity-60">좌측 목록에서 작업을 클릭하면 대시보드가 표시됩니다.</p>
                </div>
            )}
        </ResizablePanel>
      </ResizablePanelGroup>

      {taskManager.selectedTask && (
        <OutcomeManagerModal 
            isOpen={outcomeModalOpen} 
            onClose={() => setOutcomeModalOpen(false)} 
            task={taskManager.selectedTask}
            initialArtifactId={selectedArtifactId}
        />
      )}
    </div>
  );
};

export default TaskManager;
