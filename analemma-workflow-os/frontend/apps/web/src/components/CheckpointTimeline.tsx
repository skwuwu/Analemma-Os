/**
 * Checkpoint Timeline
 * 
 * 실행 체크포인트의 타임라인을 시각화하는 컴포넌트입니다.
 * 체크포인트를 클릭하여 롤백하거나 상태를 비교할 수 있습니다.
 */

import React, { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Clock,
  GitBranch,
  History,
  ChevronRight,
  Check,
  X,
  AlertTriangle,
  Pause,
  RotateCcw,
  Eye,
  GitCompare,
  Archive
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { TimelineItem, CheckpointStatus } from '@/lib/types';

interface CheckpointTimelineProps {
  items: TimelineItem[];
  loading?: boolean;
  selectedId?: string | null;
  compareId?: string | null;
  onSelect?: (item: TimelineItem) => void;
  onCompare?: (item: TimelineItem) => void;
  onRollback?: (item: TimelineItem) => void;
  onPreview?: (item: TimelineItem) => void;
  compact?: boolean;
}

// 상수를 컴포넌트 외부에 정의하여 매 렌더링마다 재생성 방지
const STATUS_ICON_CONFIG: Record<string, { icon: React.ElementType; className: string }> = {
  completed: { icon: Check, className: 'text-green-500' },
  failed: { icon: X, className: 'text-red-500' },
  pending: { icon: Pause, className: 'text-yellow-500' },
  running: { icon: Clock, className: 'text-blue-500 animate-pulse' },
  skipped: { icon: ChevronRight, className: 'text-gray-400' },
  warning: { icon: AlertTriangle, className: 'text-yellow-500' },
  branched: { icon: GitBranch, className: 'text-purple-500' },
  archived: { icon: Archive, className: 'text-gray-500' },
  active: { icon: Check, className: 'text-green-500' },
};

const StatusIcon: React.FC<{ status: string }> = ({ status }) => {
  const { icon: Icon, className } = STATUS_ICON_CONFIG[status] || STATUS_ICON_CONFIG.pending;
  
  return <Icon className={cn('w-4 h-4', className)} />;
};

const formatRelativeTime = (timestamp: string): string => {
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSecs = Math.floor(diffMs / 1000);
  const diffMins = Math.floor(diffSecs / 60);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);
  
  if (diffSecs < 60) return '방금 전';
  if (diffMins < 60) return `${diffMins}분 전`;
  if (diffHours < 24) return `${diffHours}시간 전`;
  if (diffDays < 7) return `${diffDays}일 전`;
  
  return date.toLocaleDateString('ko-KR', { 
    month: 'short', 
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  });
};

const formatExactTime = (timestamp: string): string => {
  return new Date(timestamp).toLocaleString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });
};

const TimelineNode: React.FC<{
  item: TimelineItem;
  isSelected?: boolean;
  isCompare?: boolean;
  onSelect?: () => void;
  onCompare?: () => void;
  onRollback?: () => void;
  onPreview?: () => void;
  compact?: boolean;
  isLast?: boolean;
}> = ({
  item,
  isSelected,
  isCompare,
  onSelect,
  onCompare,
  onRollback,
  onPreview,
  compact,
  isLast
}) => {
  return (
    <div className="relative flex group">
      {/* 타임라인 선 */}
      <div className="flex flex-col items-center mr-4">
        <div 
          className={cn(
            "w-8 h-8 rounded-full border-2 flex items-center justify-center bg-background z-10",
            isSelected && "border-primary bg-primary/10",
            isCompare && "border-purple-500 bg-purple-500/10",
            !isSelected && !isCompare && "border-border"
          )}
        >
          <StatusIcon status={item.status} />
        </div>
        {!isLast && (
          <div className="w-0.5 h-full bg-border min-h-[20px]" />
        )}
      </div>
      
      {/* 콘텐츠 */}
      <div className={cn(
        "flex-1 pb-4 min-w-0",
        compact && "pb-2"
      )}>
        <div 
          className={cn(
            "p-3 rounded-lg border transition-colors cursor-pointer",
            isSelected && "border-primary bg-primary/5",
            isCompare && "border-purple-500 bg-purple-500/5",
            !isSelected && !isCompare && "hover:bg-muted/50"
          )}
          onClick={onSelect}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm truncate">{item.node_name}</span>
                {item.has_external_effect && (
                  <Tooltip>
                    <TooltipTrigger>
                      <Badge variant="outline" className="text-xs px-1.5">
                        외부
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      외부 시스템에 영향을 미쳤습니다
                    </TooltipContent>
                  </Tooltip>
                )}
                {item.is_reversible === false && (
                  <Badge variant="destructive" className="text-xs px-1.5">
                    비가역
                  </Badge>
                )}
              </div>
              
              {!compact && item.description && (
                <p className="text-xs text-muted-foreground mt-1 line-clamp-2">
                  {item.description}
                </p>
              )}
              
              <div className="flex items-center gap-2 mt-1">
                <Tooltip>
                  <TooltipTrigger>
                    <span className="text-xs text-muted-foreground flex items-center gap-1">
                      <Clock className="w-3 h-3" />
                      {formatRelativeTime(item.timestamp)}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {formatExactTime(item.timestamp)}
                  </TooltipContent>
                </Tooltip>
                
                {item.branch_id && item.branch_id !== 'main' && (
                  <Badge variant="secondary" className="text-xs px-1.5">
                    <GitBranch className="w-3 h-3 mr-1" />
                    {item.branch_id}
                  </Badge>
                )}
              </div>
            </div>
            
            {/* 액션 버튼 */}
            <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
              {onPreview && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button 
                      variant="ghost" 
                      size="icon" 
                      className="h-7 w-7"
                      onClick={(e) => { e.stopPropagation(); onPreview(); }}
                    >
                      <Eye className="w-3.5 h-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>상태 보기</TooltipContent>
                </Tooltip>
              )}
              {onCompare && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button 
                      variant="ghost" 
                      size="icon" 
                      className={cn("h-7 w-7", isCompare && "bg-purple-100")}
                      onClick={(e) => { e.stopPropagation(); onCompare(); }}
                    >
                      <GitCompare className="w-3.5 h-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>비교하기</TooltipContent>
                </Tooltip>
              )}
              {onRollback && item.is_reversible !== false && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button 
                      variant="ghost" 
                      size="icon" 
                      className="h-7 w-7 text-orange-500 hover:text-orange-600"
                      onClick={(e) => { e.stopPropagation(); onRollback(); }}
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>이 시점으로 롤백</TooltipContent>
                </Tooltip>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export const CheckpointTimeline: React.FC<CheckpointTimelineProps> = ({
  items,
  loading = false,
  selectedId,
  compareId,
  onSelect,
  onCompare,
  onRollback,
  onPreview,
  compact = false,
}) => {
  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <History className="w-4 h-4" />
            실행 타임라인
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {[1, 2, 3].map((i) => (
              <div key={i} className="flex gap-4">
                <Skeleton className="w-8 h-8 rounded-full" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  if (items.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <History className="w-4 h-4" />
            실행 타임라인
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8 text-muted-foreground">
            <History className="w-8 h-8 mx-auto mb-2 opacity-50" />
            <p>아직 체크포인트가 없습니다.</p>
            <p className="text-xs mt-1">워크플로우를 실행하면 체크포인트가 기록됩니다.</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <TooltipProvider>
      <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <History className="w-4 h-4" />
            실행 타임라인
          </CardTitle>
          <Badge variant="secondary">{items.length}개</Badge>
        </div>
        {compareId && (
          <CardDescription className="flex items-center gap-1 text-purple-600">
            <GitCompare className="w-3 h-3" />
            비교 모드 - 두 체크포인트를 선택하세요
          </CardDescription>
        )}
      </CardHeader>
      <CardContent>
        <ScrollArea className={cn(
          compact ? "max-h-[200px]" : "max-h-[400px]"
        )}>
          <div className="pr-4">
            {items.map((item, index) => (
              <TimelineNode
                key={item.checkpoint_id}
                item={item}
                isSelected={selectedId === item.checkpoint_id}
                isCompare={compareId === item.checkpoint_id}
                onSelect={onSelect ? () => onSelect(item) : undefined}
                onCompare={onCompare ? () => onCompare(item) : undefined}
                onRollback={onRollback ? () => onRollback(item) : undefined}
                onPreview={onPreview ? () => onPreview(item) : undefined}
                compact={compact}
                isLast={index === items.length - 1}
              />
            ))}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
    </TooltipProvider>
  );
};

export default CheckpointTimeline;
