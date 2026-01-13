import React from 'react';
import { LucideIcon, FileX, Inbox, Search, FolderOpen, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface EmptyStateProps {
  /** 표시할 아이콘 */
  icon?: LucideIcon;
  /** 제목 */
  title: string;
  /** 설명 텍스트 */
  description?: string;
  /** CTA 버튼 설정 */
  action?: {
    label: string;
    onClick: () => void;
    variant?: 'default' | 'outline' | 'secondary';
  };
  /** 추가 클래스명 */
  className?: string;
  /** 컴팩트 모드 (작은 공간용) */
  compact?: boolean;
}

/**
 * 빈 상태를 표시하는 표준화된 컴포넌트
 * 데이터가 없거나 검색 결과가 없을 때 사용합니다.
 */
export const EmptyState: React.FC<EmptyStateProps> = ({
  icon: Icon = Inbox,
  title,
  description,
  action,
  className,
  compact = false,
}) => {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center',
        compact ? 'py-6 px-4' : 'py-12 px-6',
        className
      )}
    >
      <div
        className={cn(
          'rounded-full bg-muted flex items-center justify-center mb-4',
          compact ? 'w-10 h-10' : 'w-16 h-16'
        )}
      >
        <Icon
          className={cn(
            'text-muted-foreground',
            compact ? 'w-5 h-5' : 'w-8 h-8'
          )}
        />
      </div>

      <h3
        className={cn(
          'font-semibold text-foreground',
          compact ? 'text-sm' : 'text-lg'
        )}
      >
        {title}
      </h3>

      {description && (
        <p
          className={cn(
            'text-muted-foreground mt-1 max-w-sm',
            compact ? 'text-xs' : 'text-sm'
          )}
        >
          {description}
        </p>
      )}

      {action && (
        <Button
          variant={action.variant || 'default'}
          onClick={action.onClick}
          className={cn('mt-4', compact && 'h-8 text-xs')}
          size={compact ? 'sm' : 'default'}
        >
          {action.label}
        </Button>
      )}
    </div>
  );
};

// 자주 사용되는 Empty State 프리셋

export const NoDataEmpty: React.FC<{ onAction?: () => void }> = ({ onAction }) => (
  <EmptyState
    icon={FileX}
    title="데이터가 없습니다"
    description="아직 데이터가 없습니다. 새로운 항목을 추가해보세요."
    action={onAction ? { label: '새로 만들기', onClick: onAction } : undefined}
  />
);

export const NoSearchResultsEmpty: React.FC<{ query?: string; onClear?: () => void }> = ({
  query,
  onClear,
}) => (
  <EmptyState
    icon={Search}
    title="검색 결과가 없습니다"
    description={
      query
        ? `"${query}"에 대한 검색 결과가 없습니다.`
        : '검색 조건에 맞는 결과가 없습니다.'
    }
    action={onClear ? { label: '검색 초기화', onClick: onClear, variant: 'outline' } : undefined}
  />
);

export const NoWorkflowsEmpty: React.FC<{ onCreate?: () => void }> = ({ onCreate }) => (
  <EmptyState
    icon={Zap}
    title="워크플로우가 없습니다"
    description="첫 번째 워크플로우를 만들어 자동화를 시작하세요."
    action={onCreate ? { label: '워크플로우 만들기', onClick: onCreate } : undefined}
  />
);

export const NoExecutionsEmpty: React.FC = () => (
  <EmptyState
    icon={FolderOpen}
    title="실행 기록이 없습니다"
    description="워크플로우를 실행하면 여기에 기록이 표시됩니다."
  />
);

export default EmptyState;
