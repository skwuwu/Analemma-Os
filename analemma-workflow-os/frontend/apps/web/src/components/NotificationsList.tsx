import React, { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Trash2, BellOff, CheckCheck, Check, AlertCircle, Info, X } from 'lucide-react';
import { NotificationItem } from '@/lib/types';
import { cn } from '@/lib/utils';
import { motion, AnimatePresence } from 'framer-motion';

// 작은 유틸: relative time formatting - 컴포넌트 밖에 두어 렌더마다 재생성 방지
const formatRelative = (ts?: number) => {
  if (!ts) return '';
  const t = typeof ts === 'number' ? ts : Number(ts);
  const ms = t < 10000000000 ? t * 1000 : t;
  const diff = Date.now() - ms;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return 'Just now';
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 84600) return `${Math.floor(sec / 3600)}h ago`;
  return new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
};

interface Props {
  notifications: NotificationItem[];
  onSelect: (id: string | null) => void;
  onRemove: (id: string) => void;
  onMarkRead: (id: string) => void;
  onMarkAllRead?: () => void;
  onClearAll?: () => void;
}

const NotificationsList: React.FC<Props> = ({
  notifications,
  onSelect,
  onRemove,
  onMarkRead,
  onMarkAllRead,
  onClearAll
}) => {
  const navigate = useNavigate();
  const unreadCount = useMemo(() => notifications.filter(n => !n.read).length, [notifications]);

  if (!notifications || notifications.length === 0) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex flex-col items-center justify-center py-24 text-slate-400 m-4 border-2 border-dashed border-slate-100 rounded-3xl bg-slate-50/30"
      >
        <div className="bg-white p-4 rounded-2xl shadow-sm border border-slate-100 mb-4">
          <BellOff className="w-8 h-8 text-slate-200" />
        </div>
        <p className="text-sm font-bold text-slate-400">고요한 에이전트</p>
        <p className="text-[11px] text-slate-300 mt-1 max-w-[180px] text-center leading-relaxed">
          현재 활성화된 이벤트가 없습니다.<br />워크플로우가 동기화되면 여기에 표시됩니다.
        </p>
      </motion.div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-slate-50/20">
      {/* Batch Actions Header */}
      {(onMarkAllRead || onClearAll) && (
        <div className="px-5 py-3 flex items-center justify-between border-b bg-white/50 backdrop-blur-sm sticky top-0 z-10 transition-all">
          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Queue Status</span>
            {unreadCount > 0 && (
              <Badge variant="default" className="bg-blue-500 hover:bg-blue-600 h-4 px-1.5 text-[9px] font-bold">
                {unreadCount} NEW
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-1">
            {unreadCount > 0 && onMarkAllRead && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onMarkAllRead}
                className="h-7 text-[10px] font-bold text-slate-400 hover:text-primary hover:bg-primary/5 gap-1.5 px-2"
              >
                <CheckCheck className="w-3 h-3" /> Mark all read
              </Button>
            )}
            {onClearAll && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onClearAll}
                className="h-7 text-[10px] font-bold text-slate-400 hover:text-destructive hover:bg-destructive/5 gap-1.5 px-2"
              >
                <X className="w-3 h-3" /> Clear list
              </Button>
            )}
          </div>
        </div>
      )}

      <div className="p-4 space-y-3">
        <AnimatePresence initial={false}>
          {notifications.map((n) => {
            const payload = n.payload || {};
            const title = (payload as any).title || (n as any).title || (payload as any).workflow_name || 'System Event';

            // Semantic status mapping
            const status = n.status || payload.status;
            const isError = status === 'FAILED' || status === 'TIMED_OUT';
            const isSuccess = status === 'COMPLETE' || status === 'COMPLETED';
            const isRunning = status === 'RUNNING' || status === 'STARTED';
            const isUnread = !n.read;

            // Stable key
            const stableId = n.id || `${payload.execution_id || n.execution_id || 'no-id'}-${n.receivedAt}`;
            
            // Extract task ID for navigation
            const taskId = payload.execution_id || n.execution_id;
            
            // Handle notification click - navigate to Task Manager
            const handleNotificationClick = (e: React.MouseEvent) => {
              // Don't navigate if clicking on action buttons
              if ((e.target as HTMLElement).closest('button')) {
                return;
              }
              
              if (taskId) {
                // Mark as read when navigating
                if (isUnread) {
                  onMarkRead(n.id);
                }
                // Navigate to Task Manager with selected task
                navigate(`/task-manager?taskId=${taskId}`);
              } else {
                // Fallback to existing onSelect behavior
                onSelect(taskId || (n.id ? `notification:${n.id}` : null));
              }
            };

            return (
              <motion.div
                key={stableId}
                layout
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, scale: 0.95 }}
                transition={{ duration: 0.2 }}
              >
                <Card
                  onClick={handleNotificationClick}
                  className={cn(
                    "group relative cursor-pointer border-slate-100 hover:border-slate-200 transition-all shadow-sm hover:shadow-md active:scale-[0.99] border-l-4 overflow-hidden",
                    isUnread ? "bg-white" : "bg-slate-50/40",
                    isError ? "border-l-red-500" :
                      isSuccess ? "border-l-green-500" :
                        isRunning ? "border-l-blue-500" : "border-l-slate-200"
                  )}
                  role="button"
                  tabIndex={0}
                  aria-label={`${title}: ${n.message || ''} - Click to view in Task Manager`}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      handleNotificationClick(e as any);
                    }
                  }}
                >
                  <CardContent className="p-4 flex items-start gap-4">
                    {/* Status Icon */}
                    <div className={cn(
                      "w-10 h-10 rounded-xl shrink-0 flex items-center justify-center transition-colors shadow-inner",
                      isError ? "bg-red-50 text-red-500" :
                        isSuccess ? "bg-green-50 text-green-500" :
                          isRunning ? "bg-blue-50 text-blue-500" : "bg-slate-100 text-slate-400"
                    )}>
                      {isError ? <AlertCircle className="w-5 h-5" /> :
                        isSuccess ? <Check className="w-5 h-5" /> :
                          isRunning ? <Activity className="w-5 h-5" /> : <Info className="w-5 h-5" />}
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0 space-y-1">
                      <div className="flex items-center gap-2">
                        {isUnread && (
                          <span className="w-2 h-2 rounded-full bg-blue-600 animate-pulse shadow-[0_0_8px_rgba(37,99,235,0.6)]" />
                        )}
                        <h4 className="font-bold text-[13px] tracking-tight truncate text-slate-700">
                          {title}
                        </h4>
                      </div>
                      <p className="text-[11px] text-slate-500 line-clamp-2 leading-relaxed font-medium">
                        {payload.message || n.message || 'No detailed message provided.'}
                      </p>
                      <div className="flex items-center gap-2 pt-1">
                        <time className="text-[9px] font-bold text-slate-300 uppercase tracking-tighter">
                          {formatRelative(n.receivedAt)}
                        </time>
                        {status && (
                          <span className={cn(
                            "text-[8px] font-black px-1.5 py-0.5 rounded-full border tracking-widest",
                            isError ? "border-red-100 bg-red-50/50 text-red-600" :
                              isSuccess ? "border-green-100 bg-green-50/50 text-green-600" :
                                isRunning ? "border-blue-100 bg-blue-50/50 text-blue-600" : "border-slate-100 bg-slate-50 text-slate-400"
                          )}>
                            {status}
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Quick Actions (Appear on Hover) */}
                    <div className="flex flex-col gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      {isUnread && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 text-blue-500 hover:text-blue-600 hover:bg-blue-50 rounded-lg"
                          onClick={(e) => { e.stopPropagation(); onMarkRead(n.id); }}
                          title="Mark as read"
                        >
                          <CheckCheck className="w-4 h-4" />
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-slate-400 hover:text-slate-600 hover:bg-slate-100 rounded-lg"
                        onClick={(e) => { e.stopPropagation(); onRemove(n.id); }}
                        title="Dismiss notification"
                      >
                        <X className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-slate-300 hover:text-red-500 hover:bg-red-50 rounded-lg"
                        onClick={(e) => { e.stopPropagation(); onRemove(n.id); }}
                        title="Delete notification"
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </div>
    </div>
  );
};

export default React.memo(NotificationsList);

const Badge = ({ children, className, variant }: { children: React.ReactNode, className?: string, variant?: 'default' | 'secondary' }) => (
  <span className={cn(
    "inline-flex items-center rounded-full px-2 py-1 text-[10px] font-medium ring-1 ring-inset",
    variant === 'secondary' ? "bg-slate-50 text-slate-600 ring-slate-500/10" : "bg-blue-500 text-white ring-blue-500/10",
    className
  )}>
    {children}
  </span>
);

const Activity = ({ className }: { className?: string }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
  >
    <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
  </svg>
);
