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
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { CheckCircle2, AlertTriangle, XCircle, Loader2, ChevronRight, Lightbulb, ExternalLink } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { GraphAnalysisWarning } from '@/lib/graphAnalysis';

interface WorkflowStatusIndicatorProps {
  issueCount: number;
  hasErrors: boolean;
  hasWarnings: boolean;
  isValidating?: boolean;
  warnings?: GraphAnalysisWarning[];
  onNodeClick?: (nodeId: string) => void;
}

const warningTypeConfig = {
  orphan_node: {
    label: 'Orphan Node',
    icon: AlertTriangle,
    color: 'text-amber-400',
    bgColor: 'bg-amber-500/10',
  },
  unreachable_node: {
    label: 'Unreachable Node',
    icon: XCircle,
    color: 'text-red-400',
    bgColor: 'bg-red-500/10',
  },
  accidental_cycle: {
    label: 'Deadlock Risk',
    icon: XCircle,
    color: 'text-red-400',
    bgColor: 'bg-red-500/10',
  },
  many_branches: {
    label: 'Performance Warning',
    icon: AlertTriangle,
    color: 'text-amber-400',
    bgColor: 'bg-amber-500/10',
  },
};

export function WorkflowStatusIndicator({
  issueCount,
  hasErrors,
  hasWarnings,
  isValidating = false,
  warnings = [],
  onNodeClick
}: WorkflowStatusIndicatorProps) {
  const [open, setOpen] = useState(false);

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

  // If no issues or validating, just show the button without popover
  if (status === 'ready' || status === 'validating') {
    return (
      <Button
        variant="outline"
        size="sm"
        className={cn(
          'gap-2 h-8 px-3 font-mono text-xs transition-all',
          className
        )}
        disabled={isValidating}
      >
        <Icon className={cn('w-3.5 h-3.5', iconClassName)} />
        <span className="font-semibold">{label}</span>
      </Button>
    );
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn(
            'gap-2 h-8 px-3 font-mono text-xs transition-all cursor-pointer',
            className
          )}
        >
          <Icon className={cn('w-3.5 h-3.5', iconClassName)} />
          <span className="font-semibold">{label}</span>
          <ChevronRight className={cn('w-3 h-3 transition-transform', open && 'rotate-90')} />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[380px] p-0 bg-slate-900 border-slate-700"
        align="end"
        sideOffset={8}
      >
        {/* Header */}
        <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-200">Validation Issues</span>
            <Badge
              variant="secondary"
              className={cn(
                'text-[10px] px-1.5',
                hasErrors ? 'bg-red-500/20 text-red-400' : 'bg-amber-500/20 text-amber-400'
              )}
            >
              {issueCount}
            </Badge>
          </div>
        </div>

        {/* Issues List */}
        <ScrollArea className="max-h-[300px]">
          <div className="divide-y divide-slate-800">
            {warnings.map((warning, idx) => {
              const typeConfig = warningTypeConfig[warning.type] || warningTypeConfig.orphan_node;
              const TypeIcon = typeConfig.icon;

              return (
                <div
                  key={`${warning.type}-${idx}`}
                  className={cn(
                    'px-4 py-3',
                    typeConfig.bgColor
                  )}
                >
                  {/* Issue Header */}
                  <div className="flex items-start gap-2.5">
                    <TypeIcon className={cn('w-4 h-4 mt-0.5 flex-shrink-0', typeConfig.color)} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <Badge
                          variant="outline"
                          className={cn(
                            'text-[9px] px-1.5 py-0 h-4 border-slate-600',
                            typeConfig.color
                          )}
                        >
                          {typeConfig.label}
                        </Badge>
                      </div>

                      {/* Message */}
                      <p className="text-sm text-slate-300 leading-relaxed">
                        {warning.message}
                      </p>

                      {/* Suggestion */}
                      {warning.suggestion && (
                        <div className="flex items-start gap-1.5 mt-2 text-[11px] text-slate-500">
                          <Lightbulb className="w-3 h-3 mt-0.5 flex-shrink-0 text-amber-500" />
                          <span>{warning.suggestion}</span>
                        </div>
                      )}

                      {/* Affected Nodes */}
                      {warning.nodeIds && warning.nodeIds.length > 0 && (
                        <div className="mt-2">
                          <p className="text-[10px] text-slate-500 mb-1.5">Affected Nodes:</p>
                          <div className="flex flex-wrap gap-1">
                            {warning.nodeIds.slice(0, 5).map(nodeId => (
                              <button
                                key={nodeId}
                                onClick={() => {
                                  onNodeClick?.(nodeId);
                                  setOpen(false);
                                }}
                                className={cn(
                                  'inline-flex items-center gap-0.5 text-[10px] px-2 py-0.5 rounded',
                                  'bg-slate-800 hover:bg-slate-700 transition-colors',
                                  'text-slate-400 hover:text-slate-200 font-mono'
                                )}
                              >
                                {nodeId.length > 12 ? `${nodeId.slice(0, 12)}...` : nodeId}
                                <ExternalLink className="w-2.5 h-2.5 opacity-50" />
                              </button>
                            ))}
                            {warning.nodeIds.length > 5 && (
                              <span className="text-[10px] text-slate-500 px-1">
                                +{warning.nodeIds.length - 5} more
                              </span>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </ScrollArea>

        {/* Footer hint */}
        <div className="px-4 py-2 border-t border-slate-800 text-[10px] text-slate-500 text-center">
          Click on a node ID to navigate to it
        </div>
      </PopoverContent>
    </Popover>
  );
}
