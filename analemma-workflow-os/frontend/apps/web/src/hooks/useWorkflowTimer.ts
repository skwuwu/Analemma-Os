import { useEffect, useState, useCallback } from 'react';

// startTime may be seconds or milliseconds. Return elapsed seconds and whether running.
export default function useWorkflowTimer(startTime?: number, status?: string) {
  // 1) derive running state directly from props to avoid extra state and renders
  const isRunning = status === 'RUNNING';

  // 2) calculate elapsed seconds; protect against seconds vs ms and negative values
  const calculateElapsed = useCallback(() => {
    if (!startTime) return 0;
    const startMs = startTime < 10000000000 ? startTime * 1000 : startTime;
    const now = Date.now();
    return Math.max(0, Math.floor((now - startMs) / 1000));
  }, [startTime]);

  // 3) initialize lazily to avoid initial "0" flash on mount
  const [elapsed, setElapsed] = useState<number>(() => calculateElapsed());

  useEffect(() => {
    // sync immediately when props change
    setElapsed(calculateElapsed());

    if (!isRunning) return;

    const id = setInterval(() => {
      setElapsed(calculateElapsed());
    }, 1000);

    return () => clearInterval(id);
  }, [calculateElapsed, isRunning]);

  return { elapsed, isRunning };
}
