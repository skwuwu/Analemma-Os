import React from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  AlertTriangle,
  XCircle,
  Info,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  PanelRightClose,
  RefreshCw
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface AuditPanelProps {
  issues?: any[]; // local validation warnings
  onNodeClick?: (nodeId: string) => void;
  onRefresh?: () => void;
  onClose?: () => void;
  isLoading?: boolean;
  className?: string;
  standalone?: boolean;
}

const levelConfig = {
  error: {
    icon: XCircle,
    color: 'text-destructive',
    bgColor: 'bg-destructive/10',
    borderColor: 'border-destructive/30',
    badgeVariant: 'destructive' as const,
    label: '오류'
  },
  warning: {
    icon: AlertTriangle,
    color: 'text-yellow-600 dark:text-yellow-500',
    bgColor: 'bg-yellow-500/10',
    borderColor: 'border-yellow-500/30',
    badgeVariant: 'secondary' as const,
    label: '경고'
  },
  info: {
    icon: Info,
    color: 'text-blue-600 dark:text-blue-500',
    bgColor: 'bg-blue-500/10',
    borderColor: 'border-blue-500/30',
    badgeVariant: 'outline' as const,
    label: '정보'
  }
};

export function AuditPanel({
  issues: externalIssues,
  onNodeClick,
  onRefresh,
  onClose,
  isLoading = false,
  className,
  standalone = false
}: AuditPanelProps) {
  // 외부에서 전달된 issues 사용 (로컬 validation 결과)
  const issues = externalIssues || [];

  // GraphAnalysisWarning을 AuditIssue 형식으로 변환
  const convertedIssues = issues.map(issue => ({
    level: issue.type === 'unreachable_node' ? 'error' : 'warning' as const,
    type: issue.type,
    message: issue.message,
    affectedNodes: issue.nodeIds,
    suggestion: issue.suggestion
  }));

  // issueSummary 계산
  const issueSummary = {
    errors: convertedIssues.filter(i => i.level === 'error').length,
    warnings: convertedIssues.filter(i => i.level === 'warning').length,
    info: convertedIssues.filter(i => i.level === 'info').length,
    total: convertedIssues.length
  };

  // handleClose 함수 추가
  const handleClose = () => {
    if (onClose) {
      onClose();
    } else {
      console.warn("onClose prop is missing in AuditPanel");
    }
  };

  // 레벨별 정렬 (error > warning > info)
  const sortedIssues = [...convertedIssues].sort((a, b) => {
    const order = { error: 0, warning: 1, info: 2 };
    return (order[a.level] ?? 3) - (order[b.level] ?? 3);
  });

  // 이슈 없음 상태
  if (issues.length === 0) {
    return (
      <div className={cn("p-6 text-center", className)}>
        <div className="inline-flex p-3 bg-green-100 dark:bg-green-900 rounded-full mb-3">
          <CheckCircle2 className="w-6 h-6 text-green-600 dark:text-green-400" />
        </div>
        <h4 className="text-sm font-medium mb-1">검증 이슈 없음</h4>
        <p className="text-xs text-muted-foreground mb-4">
          워크플로우에 발견된 문제가 없습니다
        </p>
        {onRefresh && (
          <Button
            variant="outline"
            size="sm"
            onClick={onRefresh}
            disabled={isLoading}
          >
            <RefreshCw className={cn("w-4 h-4 mr-1", isLoading && "animate-spin")} />
            다시 검증
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className={cn("flex flex-col", className)}>
      {/* 요약 헤더 */}
      <div className="px-4 py-3 border-b bg-muted/30 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">검증 결과</span>
          <div className="flex gap-1">
            {issueSummary.errors > 0 && (
              <Badge variant="destructive" className="text-xs px-1.5 py-0">
                {issueSummary.errors} 오류
              </Badge>
            )}
            {issueSummary.warnings > 0 && (
              <Badge variant="secondary" className="text-xs px-1.5 py-0 bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300">
                {issueSummary.warnings} 경고
              </Badge>
            )}
            {issueSummary.info > 0 && (
              <Badge variant="outline" className="text-xs px-1.5 py-0">
                {issueSummary.info} 정보
              </Badge>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {onClose && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              onClick={handleClose}
            >
              <PanelRightClose className="w-3.5 h-3.5" />
            </Button>
          )}
        </div>
      </div>

      {/* 이슈 목록 */}
      <ScrollArea className="flex-1">
        <div className="divide-y">
          {sortedIssues.map((issue, idx) => (
            <IssueItem
              key={`${issue.type}-${idx}`}
              issue={issue}
              onNodeClick={onNodeClick}
            />
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}

interface IssueItemProps {
  issue: AuditIssue;
  onNodeClick?: (nodeId: string) => void;
}

function IssueItem({ issue, onNodeClick }: IssueItemProps) {
  const [isExpanded, setIsExpanded] = React.useState(issue.level === 'error');
  const config = levelConfig[issue.level] || levelConfig.info;
  const Icon = config.icon;

  // 안전하게 message를 문자열로 변환
  const messageText = React.useMemo(() => {
    if (typeof issue.message === 'string') {
      return issue.message;
    }
    if (issue.message && typeof issue.message === 'object') {
      return JSON.stringify(issue.message);
    }
    return String(issue.message || '알 수 없는 오류');
  }, [issue.message]);

  // 안전하게 suggestion을 문자열로 변환
  const suggestionText = React.useMemo(() => {
    if (!issue.suggestion) return null;
    if (typeof issue.suggestion === 'string') {
      return issue.suggestion;
    }
    if (typeof issue.suggestion === 'object') {
      return JSON.stringify(issue.suggestion);
    }
    return String(issue.suggestion);
  }, [issue.suggestion]);

  return (
    <div
      className={cn(
        "px-4 py-3 transition-colors cursor-pointer",
        config.bgColor,
        "hover:opacity-90"
      )}
      onClick={() => setIsExpanded(!isExpanded)}
    >
      {/* 메인 라인 */}
      <div className="flex items-start gap-2">
        <Icon className={cn("w-4 h-4 mt-0.5 flex-shrink-0", config.color)} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <Badge
              variant={config.badgeVariant}
              className="text-[10px] px-1.5 py-0 h-4"
            >
              {config.label}
            </Badge>
            {issue.type && (
              <span className="text-[10px] text-muted-foreground font-mono">
                {issue.type}
              </span>
            )}
          </div>
          <p className="text-sm leading-relaxed">
            {messageText}
          </p>
        </div>
        <ChevronRight
          className={cn(
            "w-4 h-4 text-muted-foreground transition-transform flex-shrink-0",
            isExpanded && "rotate-90"
          )}
        />
      </div>

      {/* 확장 내용 */}
      {isExpanded && (
        <div className="mt-3 pl-6 space-y-2">
          {/* 제안 */}
          {suggestionText && (
            <div className="flex items-start gap-1.5 text-xs text-muted-foreground">
              <span className="text-base leading-none">💡</span>
              <span>{suggestionText}</span>
            </div>
          )}

          {/* 영향받는 노드 */}
          {issue.affectedNodes && issue.affectedNodes.length > 0 && (
            <div>
              <p className="text-[10px] text-muted-foreground mb-1">관련 노드:</p>
              <div className="flex flex-wrap gap-1">
                {issue.affectedNodes.map(nodeId => (
                  <button
                    key={nodeId}
                    onClick={(e) => {
                      e.stopPropagation();
                      onNodeClick?.(nodeId);
                    }}
                    className={cn(
                      "inline-flex items-center gap-0.5 text-xs px-2 py-0.5 rounded",
                      "bg-secondary hover:bg-secondary/80 transition-colors",
                      "font-mono"
                    )}
                  >
                    {nodeId}
                    <ExternalLink className="w-3 h-3 opacity-50" />
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// 이슈 요약 컴포넌트 (외부에서 사용 가능)
export function AuditSummary({ className }: { className?: string }) {
  const issueSummary = useCodesignStore(selectIssueSummary);

  if (issueSummary.total === 0) {
    return (
      <div className={cn("flex items-center gap-1.5 text-green-600", className)}>
        <CheckCircle2 className="w-4 h-4" />
        <span className="text-xs">이슈 없음</span>
      </div>
    );
  }

  return (
    <div className={cn("flex items-center gap-1", className)}>
      {issueSummary.errors > 0 && (
        <div className="flex items-center gap-0.5 text-destructive">
          <XCircle className="w-3.5 h-3.5" />
          <span className="text-xs font-medium">{issueSummary.errors}</span>
        </div>
      )}
      {issueSummary.warnings > 0 && (
        <div className="flex items-center gap-0.5 text-yellow-600">
          <AlertTriangle className="w-3.5 h-3.5" />
          <span className="text-xs font-medium">{issueSummary.warnings}</span>
        </div>
      )}
      {issueSummary.info > 0 && (
        <div className="flex items-center gap-0.5 text-blue-600">
          <Info className="w-3.5 h-3.5" />
          <span className="text-xs font-medium">{issueSummary.info}</span>
        </div>
      )}
    </div>
  );
}

export default AuditPanel;
