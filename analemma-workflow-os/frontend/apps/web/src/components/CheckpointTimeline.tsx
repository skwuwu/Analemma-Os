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

// 개별 노드 메모이제이션으로 대규모 타임라인 성능 최적화
const TimelineNode = React.memo(({
  item,
  isSelected,
  selectedId,
  isCompare,
  onSelect,
  onCompare,
  onRollback,
  onPreview,
  compact,
  isLast
}: {
  item: TimelineItem;
  isSelected?: boolean;
  selectedId?: string | null;
  isCompare?: boolean;
  onSelect?: () => void;
  onCompare?: () => void;
  onRollback?: () => void;
  onPreview?: () => void;
  compact?: boolean;
  isLast?: boolean;
}) => {
  return (
    <div className="relative flex group" role="listitem">
      {/* 타임라인 선 */}
      <div className="flex flex-col items-center mr-4" aria-hidden="true">
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
            "p-3 rounded-lg border transition-colors cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-primary",
            isSelected && "border-primary bg-primary/5 shadow-sm",
            isCompare && "border-purple-500 bg-purple-500/5 shadow-sm",
            !isSelected && !isCompare && "hover:bg-muted/50 hover:border-muted-foreground/20"
          )}
          onClick={onSelect}
          tabIndex={0}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect?.(); } }}
          aria-selected={isSelected}
          role="button"
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm truncate">{item.node_name}</span>
                {item.has_external_effect && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge variant="outline" className="text-[10px] px-1.5 border-amber-500/50 text-amber-600 bg-amber-50 shrink-0">
                        Side Effect
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      외부 시스템(DB, API 등)에 영구적인 변경을 가한 작업입니다.
                    </TooltipContent>
                  </Tooltip>
                )}
                {item.is_reversible === false && (
                  <Badge variant="destructive" className="text-[10px] px-1.5 shrink-0">
                    비가역
                  </Badge>
                )}
              </div>

              {!compact && item.description && (
                <p className="text-xs text-muted-foreground mt-1 line-clamp-2 italic">
                  {item.description}
                </p>
              )}

              <div className="flex items-center gap-2 mt-1">
                <Tooltip>
                  <TooltipTrigger asChild>
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
                  <Badge variant="secondary" className="text-[10px] px-1.5">
                    <GitBranch className="w-3 h-3 mr-1" />
                    {item.branch_id}
                  </Badge>
                )}
              </div>
            </div>

            {/* 액션 버튼 */}
            <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
              {onPreview && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={(e) => { e.stopPropagation(); onPreview(); }}
                      aria-label="상태 미리보기"
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
                      className={cn(
                        "h-7 w-7 transition-all",
                        isCompare && "bg-purple-100 text-purple-600 border border-purple-200",
                        isSelected && "hidden"
                      )}
                      onClick={(e) => { e.stopPropagation(); onCompare(); }}
                      aria-label="체크포인트 비교"
                    >
                      <GitCompare className="w-3.5 h-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {isSelected ? "선택됨" : selectedId ? "이 항목과 비교" : "비교 대상으로 선택"}
                  </TooltipContent>
                </Tooltip>
              )}
              {onRollback && item.is_reversible !== false && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 text-orange-500 hover:text-orange-600 hover:bg-orange-50"
                      onClick={(e) => { e.stopPropagation(); onRollback(); }}
                      aria-label="이 시점으로 롤백"
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
}, (prev, next) => {
  // 얕은 비교 수동 구현 (성능 최적화)
  return prev.isSelected === next.isSelected &&
    prev.isCompare === next.isCompare &&
    prev.selectedId === next.selectedId &&
    prev.item.status === next.item.status &&
    prev.item.checkpoint_id === next.item.checkpoint_id &&
    prev.compact === next.compact &&
    prev.isLast === next.isLast;
});

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
  // 비교할 대상 노드 찾기
  const selectedNode = items.find(i => i.checkpoint_id === selectedId);
  const compareNode = items.find(i => i.checkpoint_id === compareId);

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
      <Card className="border-muted-foreground/10 shadow-md overflow-hidden">
        <CardHeader className="pb-3 bg-muted/20 border-b">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <History className="w-4 h-4 text-primary" />
              실행 타임라인
            </CardTitle>
            <Badge variant="outline" className="font-mono text-[10px]">{items.length} Checkpoints</Badge>
          </div>
          {compareId ? (
            <CardDescription className="flex items-center gap-1.5 text-purple-600 font-medium animate-in fade-in slide-in-from-top-1">
              <GitCompare className="w-3.5 h-3.5" />
              <span>비교 중: </span>
              <Badge variant="outline" className="h-5 text-[10px] border-purple-200 bg-purple-50 text-purple-700">
                {selectedNode?.node_name || 'A'}
              </Badge>
              <span className="text-muted-foreground text-[10px]">vs</span>
              <Badge variant="outline" className="h-5 text-[10px] border-purple-400 bg-purple-100 text-purple-800">
                {compareNode?.node_name || 'B'}
              </Badge>
            </CardDescription>
          ) : (
            <CardDescription className="text-xs">
              체크포인트를 선택하여 상태를 비교하거나 롤백할 수 있습니다.
            </CardDescription>
          )}
        </CardHeader>
        <CardContent className="pt-4">
          <ScrollArea className={cn(
            "rounded-md",
            compact ? "max-h-[200px]" : "max-h-[450px]"
          )}>
            <div className="pr-4 pb-2" role="list">
              {items.map((item, index) => (
                <TimelineNode
                  key={item.checkpoint_id}
                  item={item}
                  isSelected={selectedId === item.checkpoint_id}
                  selectedId={selectedId}
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
