import React from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Trash } from 'lucide-react';
import { NotificationItem } from '@/lib/types';

// 작은 유틸: relative time formatting - 컴포넌트 밖에 두어 렌더마다 재생성 방지
const formatRelative = (ts?: number) => {
  if (!ts) return '';
  const t = typeof ts === 'number' ? ts : Number(ts);
  const ms = t < 10000000000 ? t * 1000 : t;
  const diff = Date.now() - ms;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return 'Just now';
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return new Date(ms).toLocaleDateString();
};

interface Props {
  notifications: NotificationItem[];
  onSelect: (id: string | null) => void;
  onRemove: (id: string) => void;
  onMarkRead: (id: string) => void;
}

const NotificationsList: React.FC<Props> = ({ notifications, onSelect, onRemove, onMarkRead }) => {
  if (!notifications || notifications.length === 0) {
    return (
      <div className="text-center py-12 text-muted-foreground">
        <div className="text-sm">No notifications</div>
        <div className="text-xs mt-1">Real-time events will appear here</div>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-3">
      {notifications.map((n) => {
        const payload = n.payload || {};
        const title = (payload as any).title || (n as any).title || (payload as any).workflow_name || 'Notification';
        // Stable key: prefer n.id; otherwise use combination of execution/conversation id + receivedAt/timestamp
        const stableId = n.id || `${payload.execution_id || n.execution_id || n.conversation_id || 'no-exec'}:${n.receivedAt ?? (n as any).timestamp ?? 'unknown'}`;
        const isUnread = !n.read;
        const containerClass = `p-3 cursor-pointer hover:bg-accent ${isUnread ? 'bg-blue-50' : ''}`;

        const handleSelect = () => {
          const exec = payload.execution_id || n.execution_id;
          if (exec) {
            onSelect(exec);
          } else if (n.id) {
            // Parent expects notification:<id> for direct notification selection
            onSelect(`notification:${n.id}`);
          } else {
            onSelect(null);
          }
        };

        return (
          <Card key={stableId} className={containerClass} onClick={handleSelect}>
            <CardContent className="p-3 flex items-start justify-between">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  {isUnread && <span className="w-2 h-2 rounded-full bg-blue-600" aria-hidden />}
                  <div className="font-medium text-sm truncate">{title}</div>
                </div>
                <div className="text-xs text-muted-foreground truncate">{payload.message || n.message || ''}</div>
                <div className="text-xs text-muted-foreground mt-1">{formatRelative(n.receivedAt)}</div>
              </div>
              <div className="flex items-center gap-2 ml-3">
                {!n.read ? (
                  <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); onMarkRead(n.id); }}>
                    읽음 처리
                  </Button>
                ) : (
                  <div className="text-xs text-muted-foreground">읽음</div>
                )}
                <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); onRemove(n.id); }}>
                  <Trash className="w-4 h-4" />
                </Button>
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
};

export default React.memo(NotificationsList);
