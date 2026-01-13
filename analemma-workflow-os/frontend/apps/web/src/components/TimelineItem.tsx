import React from 'react';
import { CheckCircle, Circle, Clock, Loader2, AlertCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';

interface TimelineItemProps {
  event: any;
  isLast: boolean;
}

export const TimelineItem: React.FC<TimelineItemProps> = ({ event, isLast }) => {
  const { status, timestamp, current_step_label, message } = event.payload || event;
  const date = new Date(timestamp ? (timestamp < 10000000000 ? timestamp * 1000 : timestamp) : Date.now());

  let Icon = Circle;
  let colorClass = 'text-gray-400';
  let borderClass = 'border-l-gray-200';

  if (status === 'RUNNING' || status === 'STARTED') {
    Icon = Loader2;
    colorClass = 'text-blue-500';
    borderClass = 'border-l-blue-200';
  } else if (status === 'SUCCEEDED' || status === 'COMPLETED' || status === 'COMPLETE') {
    Icon = CheckCircle;
    colorClass = 'text-green-500';
    borderClass = 'border-l-green-200';
  } else if (status === 'FAILED' || status === 'ABORTED') {
    Icon = AlertCircle;
    colorClass = 'text-red-500';
    borderClass = 'border-l-red-200';
  } else if (status === 'PAUSED_FOR_HITP') {
    Icon = Clock;
    colorClass = 'text-orange-500';
    borderClass = 'border-l-orange-200';
  }

  return (
    <div className={`relative pl-8 pb-8 ${isLast ? '' : borderClass} border-l-2 last:border-l-0`}>
      <div className={`absolute left-[-9px] top-0 bg-background p-1 rounded-full border ${isLast && status === 'RUNNING' ? 'animate-pulse border-blue-400' : 'border-gray-200'}`}>
        <Icon className={`w-4 h-4 ${colorClass} ${status === 'RUNNING' ? 'animate-spin' : ''}`} />
      </div>
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold">{current_step_label || status}</span>
          <span className="text-xs text-muted-foreground">{date.toLocaleTimeString()}</span>
        </div>
        {message && <p className="text-xs text-muted-foreground">{message}</p>}
        {event.payload?.estimated_remaining_seconds > 0 && isLast && (
          <Badge variant="outline" className="w-fit text-[10px] mt-1">
            예상 남은 시간: {Math.floor(event.payload.estimated_remaining_seconds / 60)}분 {event.payload.estimated_remaining_seconds % 60}초
          </Badge>
        )}
      </div>
    </div>
  );
};

export default TimelineItem;
