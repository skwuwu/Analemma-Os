import React, { useMemo } from 'react';
import {
  CheckCircle,
  Circle,
  Clock,
  Loader2,
  AlertCircle,
  ArrowRight,
  type LucideIcon
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { motion } from 'framer-motion';
import type { NotificationItem } from '@/lib/types';

/**
 * 상태별 UI 구성 객체 (Config-driven Design)
 */
const STATUS_UI_CONFIG: Record<string, {
  icon: LucideIcon;
  color: string;
  bg: string;
  border: string;
  label: string;
  animate?: boolean;
}> = {
  RUNNING: { icon: Loader2, color: 'text-blue-500', bg: 'bg-blue-500/10', border: 'border-l-blue-400/30', label: 'Processing', animate: true },
  STARTED: { icon: Loader2, color: 'text-blue-400', bg: 'bg-blue-400/10', border: 'border-l-blue-400/30', label: 'Initiated', animate: true },
  SUCCEEDED: { icon: CheckCircle, color: 'text-emerald-500', bg: 'bg-emerald-500/10', border: 'border-l-emerald-500/20', label: 'Success' },
  COMPLETE: { icon: CheckCircle, color: 'text-emerald-500', bg: 'bg-emerald-500/10', border: 'border-l-emerald-500/20', label: 'Success' },
  FAILED: { icon: AlertCircle, color: 'text-rose-500', bg: 'bg-rose-500/10', border: 'border-l-rose-500/20', label: 'Error' },
  ABORTED: { icon: AlertCircle, color: 'text-rose-500', bg: 'bg-rose-500/10', border: 'border-l-rose-500/20', label: 'Aborted' },
  PAUSED_FOR_HITP: { icon: Clock, color: 'text-amber-500', bg: 'bg-amber-500/10', border: 'border-l-amber-500/20', label: 'Wait' },
  DEFAULT: { icon: Circle, color: 'text-slate-400', bg: 'bg-slate-400/5', border: 'border-l-slate-200', label: 'Pending' }
};

interface TimelineItemProps {
  event: NotificationItem;
  isLast: boolean;
}

export const TimelineItem: React.FC<TimelineItemProps> = ({ event, isLast }) => {
  const payload = event.payload || event;
  const {
    status = 'DEFAULT',
    timestamp,
    current_step_label,
    message,
    estimated_remaining_seconds
  } = payload;

  // 데이터 가공 로직 최적화
  const { Icon, colorClass, borderClass, bgClass, date, config } = useMemo(() => {
    const uiConfig = STATUS_UI_CONFIG[status] || STATUS_UI_CONFIG.DEFAULT;
    const ts = timestamp ? (timestamp < 10000000000 ? timestamp * 1000 : timestamp) : Date.now();

    return {
      Icon: uiConfig.icon,
      colorClass: uiConfig.color,
      borderClass: uiConfig.border,
      bgClass: uiConfig.bg,
      date: new Date(ts),
      config: uiConfig
    };
  }, [status, timestamp]);

  const isRunning = status === 'RUNNING';

  return (
    <motion.li
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      className={cn(
        "relative pl-10 pb-10 border-l-2 transition-all duration-500 ease-in-out",
        isLast ? "border-l-transparent" : borderClass
      )}
      role="listitem"
    >
      {/* 🚀 Node Point Marker */}
      <div className={cn(
        "absolute left-[-13px] top-0 p-1.5 rounded-full border-2 z-10 transition-transform duration-300",
        "bg-slate-900 ring-4 ring-slate-950",
        isRunning ? "border-blue-500 shadow-lg shadow-blue-500/20" : "border-slate-800"
      )}>
        <Icon className={cn(
          "w-3.5 h-3.5",
          colorClass,
          config.animate && "animate-spin"
        )} />
      </div>

      {/* 📦 Event Content */}
      <div className="flex flex-col gap-2 group">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={cn(
              "text-xs font-black tracking-widest uppercase",
              isRunning ? "text-blue-400" : "text-slate-300"
            )}>
              {current_step_label || status}
            </span>
            {isRunning && (
              <span className="flex h-1.5 w-1.5 rounded-full bg-blue-500 animate-pulse" />
            )}
          </div>
          <time className="text-[9px] font-mono font-black text-slate-500 bg-slate-800/50 px-2 py-0.5 rounded border border-slate-700/30">
            {date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
          </time>
        </div>

        {message && (
          <div className="relative">
            <p className="text-[12px] font-medium text-slate-400 leading-relaxed max-w-[95%] group-hover:text-slate-200 transition-colors">
              {message}
            </p>
          </div>
        )}

        {/* ⏱️ Estimated T-Remaining (Only for the active pulse) */}
        {estimated_remaining_seconds > 0 && isLast && isRunning && (
          <Badge className="w-fit text-[9px] h-6 bg-blue-500/10 text-blue-400 border-blue-500/20 font-black tracking-tight rounded-lg">
            <Clock className="w-3 h-3 mr-1.5 opacity-60" />
            REMAINING: {Math.floor(estimated_remaining_seconds / 60)}M {Math.round(estimated_remaining_seconds % 60)}S
          </Badge>
        )}
      </div>

      {/* Connection Indicator for next node */}
      {!isLast && (
        <div className="absolute left-[-1px] bottom-0 w-0.5 h-4 bg-gradient-to-b from-transparent to-slate-800/50" />
      )}
    </motion.li>
  );
};

export default React.memo(TimelineItem);
