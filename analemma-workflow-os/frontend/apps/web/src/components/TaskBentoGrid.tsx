import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { TaskDetail } from '@/lib/types';
import { Activity, Clock, CheckCircle2, AlertCircle, FileText, TrendingUp, ShieldCheck, AlertTriangle, MessageSquare, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useEffect, useState } from 'react';

interface TaskBentoGridProps {
  task: TaskDetail;
  onArtifactClick?: (artifactId: string) => void;
}

export const TaskBentoGrid = ({ task, onArtifactClick }: TaskBentoGridProps) => {
  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-4 p-4">
      {/* 1. Active Execution (Alias) - 2x2 */}
      <Card className="col-span-1 md:col-span-2 md:row-span-2 flex flex-col bg-card border-border shadow-card">
        <CardHeader className="pb-2">
          <div className="flex justify-between items-start">
            <div className="space-y-1">
              <CardTitle className="text-lg font-bold text-primary flex items-center gap-2">
                <Activity className="w-5 h-5" />
                {task.execution_alias || task.task_summary || "실행 중인 작업"}
              </CardTitle>
              <p className="text-xs text-muted-foreground">{task.workflow_name}</p>
            </div>
            <StatusBadge status={task.status} />
          </div>
        </CardHeader>
        <CardContent className="flex-1 flex flex-col justify-center min-h-[120px]">
          <div className="bg-muted/30 rounded-lg p-4 border border-border/50 h-full">
            <div className="flex items-center gap-2 mb-2 text-sm font-semibold text-foreground">
              <Zap className="w-4 h-4 text-yellow-500" />
              Current Thought
            </div>
            <TypingEffect text={task.current_thought || "대기 중..."} />
          </div>
        </CardContent>
      </Card>

      {/* 2. Progress & ETA - 2x1 */}
      <Card className="col-span-1 md:col-span-2 bg-card border-border shadow-card">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-muted-foreground flex justify-between">
            <span>진행 상황</span>
            <span className="text-primary">{task.estimated_completion_time || "계산 중..."}</span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            <div className="flex justify-between text-xs">
              <span>{task.current_step_name}</span>
              <span>{task.progress_percentage}%</span>
            </div>
            <Progress value={task.progress_percentage} className="h-2" />
          </div>
        </CardContent>
      </Card>

      {/* 3. Confidence Score - 1x1 */}
      <Card className="col-span-1 bg-card border-border shadow-card flex flex-col justify-center items-center p-4">
        <div className="relative w-24 h-24 flex items-center justify-center">
           {/* Simple Donut Chart using SVG */}
           <svg viewBox="0 0 36 36" className="w-full h-full rotate-[-90deg]">
              <path
                d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                fill="none"
                stroke="hsl(var(--muted))"
                strokeWidth="3"
              />
              <path
                d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                fill="none"
                stroke={getConfidenceColor(task.confidence_score || 0)}
                strokeWidth="3"
                strokeDasharray={`${task.confidence_score || 0}, 100`}
              />
           </svg>
           <div className="absolute flex flex-col items-center">
             <span className="text-xl font-bold">{task.confidence_score || 0}</span>
             <span className="text-[10px] text-muted-foreground">Confidence</span>
           </div>
        </div>
      </Card>

      {/* 4. Intervention History - 1x1 */}
      <Card className="col-span-1 bg-card border-border shadow-card">
        <CardHeader className="pb-2 p-4">
          <CardTitle className="text-sm font-medium text-muted-foreground">개입 이력</CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0 h-[100px]">
          <ScrollArea className="h-full">
            <div className="space-y-2">
              {task.intervention_history?.history?.map((item, idx) => (
                <div key={idx} className="flex items-center gap-2 text-xs">
                  {item.type === 'positive' ? (
                    <CheckCircle2 className="w-3 h-3 text-green-500" />
                  ) : item.type === 'negative' ? (
                    <AlertTriangle className="w-3 h-3 text-red-500" />
                  ) : (
                    <MessageSquare className="w-3 h-3 text-blue-500" />
                  )}
                  <span className="truncate">{item.reason}</span>
                </div>
              )) || <p className="text-xs text-muted-foreground">이력 없음</p>}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>

      {/* 5. Artifact Hub - 2x2 */}
      <Card className="col-span-1 md:col-span-2 md:row-span-2 bg-card border-border shadow-card flex flex-col">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <FileText className="w-4 h-4" />
            Artifact Hub
            <Badge variant="secondary" className="ml-auto">{task.artifacts.length}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex-1 p-4">
          <ScrollArea className="h-[180px]">
            <div className="grid grid-cols-2 gap-2">
              {task.artifacts.map((artifact) => (
                <div 
                  key={artifact.artifact_id} 
                  className="group relative aspect-video bg-muted/50 rounded-md border border-border overflow-hidden hover:border-primary transition-colors cursor-pointer"
                  onClick={() => onArtifactClick?.(artifact.artifact_id)}
                >
                  {artifact.thumbnail_url ? (
                    <img src={artifact.thumbnail_url} alt={artifact.title} className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-muted-foreground">
                      <FileText className="w-8 h-8 opacity-20" />
                    </div>
                  )}
                  <div className="absolute inset-x-0 bottom-0 bg-black/60 p-2 backdrop-blur-sm">
                    <p className="text-xs font-medium text-white truncate">{artifact.title}</p>
                  </div>
                </div>
              ))}
              {task.artifacts.length === 0 && (
                <div className="col-span-2 flex flex-col items-center justify-center h-full text-muted-foreground py-8">
                  <FileText className="w-8 h-8 mb-2 opacity-20" />
                  <p className="text-xs">생성된 결과물이 없습니다.</p>
                </div>
              )}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>

      {/* 6. Performance - 1x1 */}
      <Card className="col-span-1 bg-card border-border shadow-card flex flex-col justify-center p-4">
        <div className="text-center space-y-2">
          <div className="flex justify-center">
            <ShieldCheck className="w-8 h-8 text-primary opacity-80" />
          </div>
          <div>
            <div className="text-2xl font-bold">{task.autonomy_rate || 100}%</div>
            <div className="text-xs text-muted-foreground">자율도</div>
          </div>
          <div className="text-[10px] text-muted-foreground bg-muted/50 rounded px-2 py-1">
            {task.autonomy_display || "데이터 부족"}
          </div>
        </div>
      </Card>
      
      {/* Placeholder for 4th column if needed, or adjust layout */}
      <Card className="col-span-1 bg-card border-border shadow-card flex flex-col justify-center p-4">
         <div className="text-center space-y-2">
            <div className="flex justify-center">
                <TrendingUp className="w-8 h-8 text-green-500 opacity-80" />
            </div>
            <div>
                <div className="text-lg font-bold">{task.throughput || 0}/h</div>
                <div className="text-xs text-muted-foreground">처리량</div>
            </div>
         </div>
      </Card>

    </div>
  );
};

const StatusBadge = ({ status }: { status: string }) => {
  const variants: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
    in_progress: 'default',
    completed: 'secondary',
    failed: 'destructive',
    queued: 'outline',
    pending_approval: 'default', // Warning color usually
  };
  
  const colorClass = status === 'pending_approval' ? 'bg-yellow-500 hover:bg-yellow-600' : '';

  return (
    <Badge variant={variants[status] || 'outline'} className={cn("uppercase", colorClass)}>
      {status.replace('_', ' ')}
    </Badge>
  );
};

const getConfidenceColor = (score: number) => {
    if (score >= 80) return "hsl(142 70% 45%)"; // Green
    if (score >= 50) return "hsl(48 90% 50%)"; // Yellow
    return "hsl(0 84% 60%)"; // Red
};

const TypingEffect = ({ text }: { text: string }) => {
    const [displayedText, setDisplayedText] = useState('');
    
    useEffect(() => {
        setDisplayedText(text);
        // Simple implementation for now, can be enhanced
    }, [text]);

    return (
        <p className="text-sm text-muted-foreground leading-relaxed break-words animate-in fade-in duration-500">
            {displayedText}
            <span className="inline-block w-1.5 h-4 ml-1 bg-primary animate-pulse align-middle" />
        </p>
    );
};
