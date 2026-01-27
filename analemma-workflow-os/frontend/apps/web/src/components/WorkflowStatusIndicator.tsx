/**
 * WorkflowStatusIndicator: Real-time Design Validation Badge
 * ===========================================================
 *
 * Shows workflow health status in the canvas toolbar.
 * Inspired by IDE status bars and circuit design DRC indicators.
 *
 * States:
 * - Ready: No issues, workflow can be executed
 * - Warnings: Non-critical issues detected
 * - Errors: Critical issues preventing execution
 * - Validating: Background check in progress
 */
import { Button } from '@/components/ui/button';
import { CheckCircle2, AlertTriangle, XCircle, Loader2, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { GraphAnalysisWarning } from '@/lib/graphAnalysis';

interface WorkflowStatusIndicatorProps {
  issueCount: number;
  hasErrors: boolean;
  hasWarnings: boolean;
  isValidating?: boolean;
  warnings?: GraphAnalysisWarning[];
  onNodeClick?: (nodeId: string) => void;
  onClick?: () => void;
}

export function WorkflowStatusIndicator({
  issueCount,
  hasErrors,
  hasWarnings,
  isValidating = false,
  warnings = [],
  onNodeClick,
  onClick
}: WorkflowStatusIndicatorProps) {
  // Determine status
  const status = isValidating
    ? 'validating'
    : hasErrors
    ? 'error'
    : hasWarnings
    ? 'warning'
    : 'ready';

  const config = {
    validating: {
      icon: Loader2,
      label: 'Validating...',
      variant: 'secondary' as const,
      className: 'text-slate-400 border-slate-700',
      iconClassName: 'animate-spin'
    },
    error: {
      icon: XCircle,
      label: `${issueCount} Critical ${issueCount === 1 ? 'Issue' : 'Issues'}`,
      variant: 'destructive' as const,
      className: 'bg-red-500/10 text-red-400 border-red-500/30 hover:bg-red-500/20',
      iconClassName: 'animate-pulse'
    },
    warning: {
      icon: AlertTriangle,
      label: `${issueCount} ${issueCount === 1 ? 'Warning' : 'Warnings'}`,
      variant: 'secondary' as const,
      className: 'bg-amber-500/10 text-amber-400 border-amber-500/30 hover:bg-amber-500/20',
      iconClassName: ''
    },
    ready: {
      icon: CheckCircle2,
      label: 'Ready to Execute',
      variant: 'secondary' as const,
      className: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/20',
      iconClassName: ''
    }
  };

  const { icon: Icon, label, className, iconClassName } = config[status];

  // Simple button that triggers onClick when there are issues
  return (
    <Button
      variant="outline"
      size="sm"
      className={cn(
        'gap-2 h-8 px-3 font-mono text-xs transition-all',
        (status === 'error' || status === 'warning') && 'cursor-pointer',
        className
      )}
      disabled={isValidating}
      onClick={(status === 'error' || status === 'warning') ? onClick : undefined}
    >
      <Icon className={cn('w-3.5 h-3.5', iconClassName)} />
      <span className="font-semibold">{label}</span>
      {(status === 'error' || status === 'warning') && (
        <ChevronRight className="w-3 h-3" />
      )}
    </Button>
  );
}
