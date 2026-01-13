import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Activity, Bell, Home } from 'lucide-react';

interface WorkflowMonitorHeaderProps {
  activeWorkflowsCount: number;
  unreadCount: number;
  onNotificationsClick: () => void;
  signOut?: () => void;
}

export const WorkflowMonitorHeader: React.FC<WorkflowMonitorHeaderProps> = ({
  activeWorkflowsCount,
  unreadCount,
  onNotificationsClick,
  signOut
}) => {
  const navigate = useNavigate();

  return (
    <div className="flex justify-between items-center p-4 border-b">
      <div className="flex items-center gap-3">
        <Activity className="w-6 h-6" />
        <h1 className="text-xl font-semibold">Workflow Monitor</h1>
        {activeWorkflowsCount > 0 && (
          <Badge variant="secondary">{activeWorkflowsCount} Active</Badge>
        )}
        {unreadCount > 0 && (
          <button
            className="ml-3 inline-flex items-center gap-2"
            onClick={onNotificationsClick}
            aria-label="View notifications"
          >
            <Bell className="w-5 h-5" />
            <span className="inline-flex items-center justify-center rounded-full bg-red-600 text-white text-xs px-2 py-0.5">
              {unreadCount}
            </span>
          </button>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => navigate('/')}
          className="flex items-center gap-2"
        >
          <Home className="w-4 h-4" />
          Home
        </Button>
        {signOut && (
          <Button
            variant="outline"
            size="sm"
            onClick={signOut}
          >
            Sign Out
          </Button>
        )}
      </div>
    </div>
  );
};
