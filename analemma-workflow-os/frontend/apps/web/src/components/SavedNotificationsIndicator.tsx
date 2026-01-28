import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bell, ChevronDown, Clock, CheckCircle } from 'lucide-react';
import { useNotifications } from '@/hooks/useNotifications';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import type { NotificationItem } from '@/lib/types';

export const SavedNotificationsIndicator: React.FC = () => {
  const navigate = useNavigate();
  const { notifications, markRead } = useNotifications();
  const [isOpen, setIsOpen] = useState(false);

  // Filter saved notifications (completed, failed workflows)
  const savedNotifications = useMemo(() => {
    return (notifications || [])
      .filter(n => n.action === 'workflow_completed' || 
                   ['COMPLETE', 'FAILED', 'TIMED_OUT'].includes(n.status || ''))
      .slice(0, 10); // Show latest 10
  }, [notifications]);

  const unreadCount = savedNotifications.filter(n => !n.read).length;
  const hasUnread = unreadCount > 0;

  const handleNotificationClick = (notification: NotificationItem) => {
    if (notification.id) {
      markRead(notification.id);
    }
    if (notification.execution_id || notification.conversation_id) {
      const execId = notification.execution_id || notification.conversation_id;
      navigate(`/tasks?executionId=${encodeURIComponent(execId!)}`);
      setIsOpen(false);
    }
  };

  const getStatusColor = (status?: string) => {
    switch (status) {
      case 'COMPLETE':
        return 'text-green-600';
      case 'FAILED':
        return 'text-red-600';
      case 'TIMED_OUT':
        return 'text-orange-600';
      default:
        return 'text-muted-foreground';
    }
  };

  const getStatusIcon = (status?: string) => {
    switch (status) {
      case 'COMPLETE':
        return <CheckCircle className="w-3 h-3" />;
      case 'FAILED':
      case 'TIMED_OUT':
        return <Clock className="w-3 h-3" />;
      default:
        return <Bell className="w-3 h-3" />;
    }
  };

  const formatTimestamp = (timestamp?: number) => {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    const now = Date.now();
    const diff = now - timestamp;
    
    if (diff < 60000) return 'Just now';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    return date.toLocaleDateString();
  };

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className={cn(
            "h-9 gap-2 relative",
            hasUnread ? 'text-primary' : 'text-muted-foreground'
          )}
        >
          <Bell className={cn("w-4 h-4", hasUnread && 'animate-pulse')} />
          <span className="hidden sm:inline">Notifications</span>
          {hasUnread && (
            <Badge
              variant="secondary"
              className="ml-1 px-1.5 h-5 min-w-5 flex justify-center items-center bg-primary text-primary-foreground"
            >
              {unreadCount}
            </Badge>
          )}
          <ChevronDown className="w-3 h-3 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[400px] p-0" align="end">
        <div className="p-3 border-b bg-muted/30 flex items-center justify-between">
          <h4 className="text-sm font-semibold flex items-center gap-2">
            <Bell className="w-4 h-4" />
            Recent Notifications
          </h4>
          {hasUnread && (
            <Badge variant="secondary" className="text-xs">
              {unreadCount} unread
            </Badge>
          )}
        </div>
        <ScrollArea className="h-auto max-h-[400px]">
          {savedNotifications.length === 0 ? (
            <div className="p-6 text-center text-sm text-muted-foreground">
              <Bell className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p>No recent notifications</p>
            </div>
          ) : (
            <div className="divide-y">
              {savedNotifications.map((notification) => (
                <button
                  key={notification.id}
                  onClick={() => handleNotificationClick(notification)}
                  className={cn(
                    "w-full px-4 py-3 text-left hover:bg-muted/50 transition-colors",
                    !notification.read && "bg-primary/5"
                  )}
                >
                  <div className="flex items-start gap-3">
                    <div className={cn("mt-0.5", getStatusColor(notification.status))}>
                      {getStatusIcon(notification.status)}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <p className="text-sm font-medium truncate">
                          {notification.workflow_name || 'Workflow'}
                        </p>
                        {!notification.read && (
                          <div className="w-2 h-2 rounded-full bg-primary flex-shrink-0" />
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground truncate mb-1">
                        {notification.message || `Status: ${notification.status}`}
                      </p>
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <Clock className="w-3 h-3" />
                        <span>{formatTimestamp(notification.receivedAt)}</span>
                        {notification.status && (
                          <>
                            <span>•</span>
                            <Badge
                              variant="outline"
                              className={cn("text-xs h-5", getStatusColor(notification.status))}
                            >
                              {notification.status}
                            </Badge>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </ScrollArea>
        {savedNotifications.length > 0 && (
          <div className="p-2 border-t bg-muted/30">
            <Button
              variant="ghost"
              size="sm"
              className="w-full text-xs"
              onClick={() => {
                navigate('/tasks');
                setIsOpen(false);
              }}
            >
              View All Notifications
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
};
