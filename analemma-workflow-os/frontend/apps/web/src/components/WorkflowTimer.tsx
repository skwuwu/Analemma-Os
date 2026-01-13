import React from 'react';
import useWorkflowTimer from '@/hooks/useWorkflowTimer';

interface Props {
  // Accept either `startTime` or `startTimeSec` for flexibility (seconds or millis)
  startTime?: number | null;
  startTimeSec?: number | null; // legacy name
  status?: string | null;
  format?: (s: number) => string;
  className?: string;
}

const WorkflowTimer: React.FC<Props> = ({ startTime, startTimeSec, status, format, className = '' }) => {
  const ts = startTime ?? startTimeSec ?? undefined;
  const { elapsed } = useWorkflowTimer(ts ?? undefined, status ?? undefined);
  const display = format ? format(elapsed) : `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, '0')}`;
  return <span className={`text-xs font-mono ${className}`}>{display}</span>;
};

export default React.memo(WorkflowTimer);
