/**
 * WorkflowStatusIndicator: Real-time Design Validation Badge
 * ===========================================================
 * 
 * Shows workflow health status in the canvas toolbar.
 * Inspired by IDE status bars and circuit design DRC indicators.
 * 
 * States:
 * - ✅ Ready: No issues, workflow can be executed
 * - ⚠️ Warnings: Non-critical issues detected
 * - ❌ Errors: Critical issues preventing execution
 * - 🔄 Validating: Background check in progress
 */
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CheckCircle2, AlertTriangle, XCircle, Loader2 } from 'lucide-react';
import { cn } from '@/lib/utils';

interface WorkflowStatusIndicatorProps {
  issueCount: number;
  hasErrors: boolean;
  hasWarnings: boolean;
  isValidating?: boolean;
  onClick?: () => void;
}

export function WorkflowStatusIndicator({
  issueCount,
  hasErrors,
  hasWarnings,
  isValidating = false,
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

  const { icon: Icon, label, variant, className, iconClassName } = config[status];

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={onClick}
      className={cn(
        'gap-2 h-8 px-3 font-mono text-xs transition-all',
        className,
        onClick && 'cursor-pointer'
      )}
      disabled={isValidating}
    >
      <Icon className={cn('w-3.5 h-3.5', iconClassName)} />
      <span className="font-semibold">{label}</span>
    </Button>
  );
}
