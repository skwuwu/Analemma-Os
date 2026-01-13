import React from 'react';
import { Badge } from '@/components/ui/badge';

type TabType = 'active' | 'history' | 'notifications';

interface TabNavigationProps {
  selectedTab: TabType;
  onTabChange: (tab: TabType) => void;
  activeCount: number;
  historyCount: number;
  notificationsCount: number;
}

export const TabNavigation: React.FC<TabNavigationProps> = ({
  selectedTab,
  onTabChange,
  activeCount,
  historyCount,
  notificationsCount
}) => {
  const tabs = [
    { id: 'active' as const, label: 'Active Workflows', count: activeCount },
    { id: 'history' as const, label: 'Execution History', count: historyCount },
    { id: 'notifications' as const, label: 'Notifications', count: notificationsCount }
  ];

  return (
    <div className="flex border-b" role="tablist" aria-label="Workflow tabs">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          id={`tab-${tab.id}`}
          aria-selected={selectedTab === tab.id}
          aria-controls={`tabpanel-${tab.id}`}
          className={`flex-1 p-3 text-sm font-medium border-b-2 transition-colors ${
            selectedTab === tab.id
              ? 'border-primary text-primary'
              : 'border-transparent text-muted-foreground hover:text-foreground'
          }`}
          onClick={() => onTabChange(tab.id)}
        >
          {tab.label}
          {tab.count > 0 && (
            <Badge variant="secondary" className="ml-2">
              {tab.count}
            </Badge>
          )}
        </button>
      ))}
    </div>
  );
};
