import React from 'react';
import { Activity } from 'lucide-react';
import { useNotifications } from '@/hooks/useNotifications';

export const GlobalStatusBar: React.FC = () => {
  const { notifications } = useNotifications();
  const activeWorkflows = Array.isArray(notifications) ? notifications : [];

  // Filter running workflows from notifications
  const runningCount = notifications.filter(n =>
    n.payload?.status === 'RUNNING' || n.status === 'RUNNING'
  ).length;

  const isRunning = runningCount > 0;

  return (
    <div className="px-4 py-2 bg-background/95 backdrop-blur-sm border-b">
      <div className="flex items-center gap-2 text-sm">
        <Activity className={`w-4 h-4 ${isRunning ? 'text-green-600 animate-pulse' : 'text-gray-400'}`} />
        <span className="font-medium">
          {isRunning ? `${runningCount} workflow${runningCount > 1 ? 's' : ''} running` : 'no activated workflow'}
        </span>
      </div>
    </div>
  );
};