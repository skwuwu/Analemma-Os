import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { TaskDetail } from '@/lib/types';
import {
  Activity,
  Clock,
  CheckCircle2,
  AlertCircle,
  FileText,
  TrendingUp,
  ShieldCheck,
  AlertTriangle,
  MessageSquare,
  Zap,
  ChevronRight,
  Search,
  BrainCircuit,
  Cpu,
  ExternalLink
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useEffect, useState, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';

interface TaskBentoGridProps {
  task: TaskDetail;
  onArtifactClick?: (artifactId: string) => void;
}

// --- SUB-COMPONENTS ---

/**
 * AI의 실시간 사고 과정을 글자 단위로 출력하는 효과
 */
const TypingEffect = ({ text }: { text: string }) => {
  const [displayedText, setDisplayedText] = useState('');

  useEffect(() => {
    let i = 0;
    const streamSpeed = 30; // 글자당 30ms
    setDisplayedText('');

    // 텍스트가 비어있지 않을 때만 스트리밍 시작
    if (!text) return;

    const timer = setInterval(() => {
      setDisplayedText(text.slice(0, i + 1));
      i++;
      if (i >= text.length) clearInterval(timer);
    }, streamSpeed);

    return () => clearInterval(timer);
  }, [text]);

  return (
    <div className="relative group">
      <p className="text-sm font-medium text-slate-600 dark:text-slate-400 leading-relaxed break-words min-h-[4.5rem]">
        {displayedText}
        <motion.span
          animate={{ opacity: [1, 0, 1] }}
          transition={{ duration: 0.8, repeat: Infinity }}
          className="inline-block w-1.5 h-4 ml-1 bg-primary align-middle rounded-full shadow-[0_0_8px_rgba(var(--primary),0.5)]"
        />
      </p>
    </div>
  );
};

/**
 * Artifact Hub의 개별 결과물 카드
 */
const ArtifactItem = ({ artifact, onClick }: { artifact: any, onClick?: (id: string) => void }) => (
  <motion.div
    whileHover={{ scale: 1.02, y: -2 }}
    whileTap={{ scale: 0.98 }}
    className="group relative aspect-video bg-slate-100 dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 overflow-hidden cursor-pointer shadow-sm hover:shadow-xl hover:shadow-primary/5 transition-all"
    onClick={() => onClick?.(artifact.artifact_id)}
  >
    {artifact.thumbnail_url ? (
      <img
        src={artifact.thumbnail_url}
        alt={artifact.title}
        className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-110"
      />
    ) : (
      <div className="w-full h-full flex flex-col items-center justify-center bg-slate-50 dark:bg-slate-900/50">
        <FileText className="w-8 h-8 opacity-10 mb-1" />
        <span className="text-[9px] font-black uppercase opacity-20 tracking-tighter">No Preview</span>
      </div>
    )}

    {/* Glassmorphism Overlay on Hover */}
    <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300 p-4 flex flex-col justify-end backdrop-blur-[2px]">
      <div className="flex items-center justify-between gap-2 overflow-hidden">
        <p className="text-[11px] font-black text-white truncate drop-shadow-md">
          {artifact.title}
        </p>
        <div className="bg-white/20 p-1 rounded-md">
          <ExternalLink className="w-3 h-3 text-white" />
        </div>
      </div>
    </div>

    {/* Default Badge */}
    {!artifact.thumbnail_url && (
      <div className="absolute top-2 left-2">
        <Badge variant="secondary" className="bg-white/80 dark:bg-black/80 text-[8px] font-black uppercase tracking-tighter py-0 px-1.5 h-4 ring-1 ring-slate-950/5">
          DOC_ARCHIVE
        </Badge>
      </div>
    )}
  </motion.div>
);

// --- MAIN COMPONENT ---

export const TaskBentoGrid = ({ task, onArtifactClick }: TaskBentoGridProps) => {
  // SVG Donut Circle Params
  const radius = 15.9155;
  const circumference = 2 * Math.PI * radius; // Approx 100
  const offset = circumference - ((task.confidence_score || 0) / 100) * circumference;

  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-6 p-6 bg-slate-50/30 dark:bg-slate-950/30 rounded-3xl border border-slate-100 dark:border-slate-900 shadow-inner">
      {/* 1. Active Execution (Core Strategic Hub) - 2x2 */}
      <Card className="col-span-1 md:col-span-2 md:row-span-2 flex flex-col bg-white dark:bg-slate-950 border-slate-200/60 dark:border-slate-800 shadow-2xl shadow-slate-200/20 dark:shadow-none rounded-[2rem] overflow-hidden">
        <CardHeader className="p-8 pb-4">
          <div className="flex justify-between items-start">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <div className="p-1 px-2 rounded-lg bg-primary/10 text-primary text-[10px] font-black uppercase tracking-widest border border-primary/20">
                  Live Session
                </div>
              </div>
              <CardTitle className="text-2xl font-black tracking-tight text-slate-800 dark:text-slate-100 flex items-center gap-3">
                <BrainCircuit className="w-7 h-7 text-primary" />
                {task.execution_alias || task.task_summary || "Active Protocol"}
              </CardTitle>
            </div>
            <StatusBadge status={task.status} />
          </div>
        </CardHeader>
        <CardContent className="flex-1 flex flex-col justify-end p-8 pt-4">
          <div className="bg-slate-50 dark:bg-slate-900 rounded-2xl p-6 border border-slate-100 dark:border-slate-800/50 shadow-inner relative group transition-all hover:border-primary/20">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-8 h-8 rounded-full bg-white dark:bg-black shadow-sm flex items-center justify-center border border-slate-100 dark:border-slate-800 group-hover:scale-110 transition-transform">
                <Zap className="w-4 h-4 text-yellow-500 fill-yellow-500" />
              </div>
              <span className="text-xs font-black uppercase tracking-[0.2em] text-slate-400">Cognitive Stream</span>
            </div>
            <TypingEffect text={task.current_thought || "Initialising neural interface..."} />
            <div className="absolute right-4 bottom-4 opacity-10 group-hover:opacity-30 transition-opacity">
              <Cpu className="w-10 h-10 text-primary" />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 2. Progress & Temporal ETA - 2x1 */}
      <Card className="col-span-1 md:col-span-2 bg-white dark:bg-slate-950 border-slate-200/60 dark:border-slate-800 shadow-lg rounded-[1.5rem] flex flex-col justify-center p-8">
        <div className="flex items-center justify-between mb-6">
          <div className="flex flex-col">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Execution Vector</span>
            <span className="text-sm font-bold text-slate-600">{task.current_step_name || 'Idle'}</span>
          </div>
          <div className="text-right">
            <span className="text-[10px] font-black uppercase tracking-widest text-primary/60">Estimated T-Minus</span>
            <div className="text-lg font-black text-primary tracking-tighter">{task.estimated_completion_time || "CALC_ETA..."}</div>
          </div>
        </div>
        <div className="space-y-2">
          <div className="flex justify-between text-[11px] font-black uppercase tracking-tighter mb-1">
            <span className="text-slate-400">Quantization Progress</span>
            <span className="text-primary">{task.progress_percentage}%</span>
          </div>
          <div className="relative h-2.5 bg-slate-100 dark:bg-slate-900 rounded-full overflow-hidden border border-slate-200/50 dark:border-slate-800 ring-4 ring-primary/5">
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: `${task.progress_percentage}%` }}
              transition={{ duration: 1.5, ease: "easeOut" }}
              className="absolute top-0 left-0 h-full bg-gradient-to-r from-primary via-primary/80 to-blue-400 rounded-full"
            />
          </div>
        </div>
      </Card>

      {/* 3. Real-time Confidence Calibration - 1x1 */}
      <Card className="col-span-1 bg-white dark:bg-slate-950 border-slate-200/60 dark:border-slate-800 shadow-lg rounded-[1.5rem] p-6 flex flex-col items-center justify-center relative overflow-hidden group">
        <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-primary/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
        <div className="relative w-28 h-28 flex items-center justify-center">
          <svg viewBox="0 0 36 36" className="w-full h-full rotate-[-90deg]">
            <path
              d={`M18 2.0845 a ${radius} ${radius} 0 0 1 0 31.831 a ${radius} ${radius} 0 0 1 0 -31.831`}
              fill="none"
              stroke="currentColor"
              className="text-slate-100 dark:text-slate-900"
              strokeWidth="4"
            />
            <motion.path
              initial={{ strokeDashoffset: circumference }}
              animate={{ strokeDashoffset: offset }}
              transition={{ duration: 2, ease: "easeInOut" }}
              d={`M18 2.0845 a ${radius} ${radius} 0 0 1 0 31.831 a ${radius} ${radius} 0 0 1 0 -31.831`}
              fill="none"
              stroke={getConfidenceColor(task.confidence_score || 0)}
              strokeWidth="4"
              strokeDasharray={circumference}
              strokeLinecap="round"
            />
          </svg>
          <div className="absolute flex flex-col items-center">
            <span className="text-2xl font-black tracking-tighter text-slate-800 dark:text-slate-100">{task.confidence_score || 0}%</span>
            <span className="text-[9px] font-black uppercase tracking-widest text-slate-400">Stable</span>
          </div>
        </div>
        <div className="mt-4 flex items-center gap-1.5 px-3 py-1 bg-slate-50 dark:bg-slate-900 rounded-full border border-slate-100 dark:border-slate-800 shadow-sm">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.5)]" />
          <span className="text-[9px] font-black text-slate-500 uppercase tracking-widest">Hi-Fi Confidence</span>
        </div>
      </Card>

      {/* 4. Intervention Log Audit - 1x1 */}
      <Card className="col-span-1 bg-white dark:bg-slate-950 border-slate-200/60 dark:border-slate-800 shadow-lg rounded-[1.5rem] p-6 flex flex-col">
        <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-400 mb-4 flex items-center gap-2">
          <ShieldCheck className="w-3.5 h-3.5" /> Safety Log
        </h4>
        <ScrollArea className="flex-1 -mr-2 pr-2">
          <div className="space-y-3">
            {(task.intervention_history?.history?.length || 0) > 0 ? (
              task.intervention_history?.history?.map((item, idx) => (
                <motion.div
                  initial={{ opacity: 0, x: -5 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: idx * 0.1 }}
                  key={idx}
                  className="flex items-start gap-3 p-2 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-100 dark:border-slate-800 group hover:border-slate-300 dark:hover:border-slate-700 transition-colors"
                >
                  <div className={cn(
                    "p-1.5 rounded-lg shrink-0",
                    item.type === 'positive' ? 'bg-emerald-100 text-emerald-600' :
                      item.type === 'negative' ? 'bg-rose-100 text-rose-600' : 'bg-blue-100 text-blue-600'
                  )}>
                    {item.type === 'positive' ? <CheckCircle2 className="w-3 h-3" /> :
                      item.type === 'negative' ? <AlertTriangle className="w-3 h-3" /> : <MessageSquare className="w-3 h-3" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-[10px] font-bold text-slate-700 dark:text-slate-300 break-words leading-tight">
                      {item.reason}
                    </p>
                    <span className="text-[8px] font-black text-slate-400 uppercase tracking-tighter">Event Sequence {idx + 1}</span>
                  </div>
                </motion.div>
              ))
            ) : (
              <div className="h-full flex flex-col items-center justify-center opacity-30 mt-4">
                <CheckCircle2 className="w-8 h-8 mb-2" />
                <span className="text-[9px] font-black uppercase tracking-widest">No Violations</span>
              </div>
            )}
          </div>
        </ScrollArea>
      </Card>

      {/* 5. High-Fidelity Artifact Hub - 2x2 */}
      <Card className="col-span-1 md:col-span-2 md:row-span-2 bg-white dark:bg-slate-950 border-slate-200/60 dark:border-slate-800 shadow-2xl rounded-[2rem] flex flex-col overflow-hidden">
        <CardHeader className="p-8 pb-4 border-b border-slate-50 dark:border-slate-900">
          <div className="flex items-center justify-between">
            <CardTitle className="text-lg font-black tracking-tight flex items-center gap-3">
              <FileText className="w-6 h-6 text-primary" />
              Artifact Archive
            </CardTitle>
            <Badge variant="secondary" className="rounded-full bg-slate-100 text-slate-600 font-black text-[10px] h-6 px-3 border border-slate-200/60">
              {task.artifacts.length} ENTITIES
            </Badge>
          </div>
        </CardHeader>
        <CardContent className="flex-1 p-8">
          <ScrollArea className="h-[200px] -mr-4 pr-4">
            <div className="grid grid-cols-2 gap-4">
              {task.artifacts.map((artifact) => (
                <ArtifactItem
                  key={artifact.artifact_id}
                  artifact={artifact}
                  onClick={onArtifactClick}
                />
              ))}
              {task.artifacts.length === 0 && (
                <div className="col-span-2 flex flex-col items-center justify-center py-12 bg-slate-50 dark:bg-slate-900/40 rounded-3xl border-2 border-dashed border-slate-200 dark:border-slate-800 opacity-50">
                  <Search className="w-10 h-10 mb-3 text-slate-300" />
                  <p className="text-xs font-black uppercase tracking-widest text-slate-400">Null Artifact Matrix</p>
                  <p className="text-[10px] font-bold text-slate-300 mt-1">Pending generation cycle completion.</p>
                </div>
              )}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>

      {/* 6. System Autonomy & Efficiency - 1x1 Cards */}
      <Card className="col-span-1 bg-white dark:bg-slate-950 border-slate-200/60 dark:border-slate-800 shadow-lg rounded-[1.5rem] p-8 flex flex-col items-center justify-center relative overflow-hidden group">
        <motion.div
          animate={{ rotate: [0, 10, -10, 0] }}
          transition={{ duration: 5, repeat: Infinity }}
          className="p-4 rounded-3xl bg-emerald-50 dark:bg-emerald-500/5 mb-4 group-hover:scale-110 transition-transform"
        >
          <ShieldCheck className="w-8 h-8 text-emerald-500" />
        </motion.div>
        <div className="text-center">
          <div className="text-3xl font-black tracking-tighter text-slate-800 dark:text-slate-100">{task.autonomy_rate || 100}%</div>
          <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-400 mt-1">Autonomy Quota</div>
        </div>
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-4 px-3 py-1 bg-emerald-500/10 rounded-full border border-emerald-500/20 text-[9px] font-black text-emerald-600 uppercase tracking-tighter"
        >
          {task.autonomy_display || "MAXIMUM_TRUST"}
        </motion.div>
      </Card>

      <Card className="col-span-1 bg-white dark:bg-slate-950 border-slate-200/60 dark:border-slate-800 shadow-lg rounded-[1.5rem] p-8 flex flex-col items-center justify-center relative overflow-hidden group">
        <div className="p-4 rounded-3xl bg-blue-50 dark:bg-blue-500/5 mb-4 group-hover:scale-110 transition-transform">
          <TrendingUp className="w-8 h-8 text-blue-500" />
        </div>
        <div className="text-center">
          <div className="text-2xl font-black text-slate-800 dark:text-slate-100 tracking-tighter">{task.throughput || 0}<span className="text-sm opacity-30">/h</span></div>
          <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-400 mt-1">Throughput Velocity</div>
        </div>
        <div className="absolute -bottom-2 -right-2 opacity-5 border-8 border-current rounded-full w-24 h-24" />
      </Card>
    </div>
  );
};

const StatusBadge = ({ status }: { status: string }) => {
  const variants: Record<string, string> = {
    in_progress: 'bg-primary text-white border-primary/20',
    completed: 'bg-emerald-500 text-white border-emerald-600/20',
    failed: 'bg-rose-500 text-white border-rose-600/20',
    queued: 'bg-slate-200 text-slate-600 border-slate-300 dark:bg-slate-800 dark:text-slate-400',
    pending_approval: 'bg-amber-500 text-white border-amber-600/20 shadow-lg shadow-amber-500/20 animate-pulse',
  };

  return (
    <Badge className={cn(
      "px-4 py-1.5 rounded-full font-black text-[10px] uppercase tracking-widest border-2 transition-all",
      variants[status] || 'bg-slate-500 text-white'
    )}>
      {status === 'in_progress' && <Loader2 className="w-3 h-3 mr-2 animate-spin" />}
      {status === 'pending_approval' && <AlertTriangle className="w-3 h-3 mr-2" />}
      {status.replace('_', ' ')}
    </Badge>
  );
};

const getConfidenceColor = (score: number) => {
  if (score >= 80) return "hsl(142 70% 45%)"; // Green
  if (score >= 50) return "hsl(48 90% 50%)"; // Yellow
  return "hsl(0 84% 60%)"; // Red
};

const Loader2 = ({ className }: { className?: string }) => (
  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
    <path d="M12 2v4" /><path d="m16.2 7.8 2.9-2.9" /><path d="M18 12h4" /><path d="m16.2 16.2 2.9 2.9" /><path d="M12 18v4" /><path d="m4.9 19.1 2.9-2.9" /><path d="M2 12h4" /><path d="m4.9 4.9 2.9 2.9" />
  </svg>
);

export default TaskBentoGrid;
