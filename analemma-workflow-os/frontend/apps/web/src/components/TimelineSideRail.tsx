/**
 * TimelineSideRail: Timeline-only side rail for TaskManager
 * ==========================================================
 * 
 * TaskManager용 간소화된 사이드 레일 (Timeline만 표시)
 */
import { Button } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Activity } from 'lucide-react';
import { cn } from '@/lib/utils';

interface TimelineSideRailProps {
  isExecuting?: boolean;
  panelOpen: boolean;
  onTogglePanel: () => void;
}

export function TimelineSideRail({
  isExecuting = false,
  panelOpen,
  onTogglePanel
}: TimelineSideRailProps) {
  
  return (
    <div className="fixed top-1/2 right-6 -translate-y-1/2 z-30">
      <div className="flex flex-col gap-1 p-1.5 bg-slate-900/80 backdrop-blur-xl border border-slate-800 rounded-2xl shadow-2xl">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={onTogglePanel}
              className={cn(
                'relative h-12 w-12 rounded-xl transition-all',
                panelOpen && 'bg-slate-800 text-blue-400',
                !panelOpen && 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/50',
                isExecuting && !panelOpen && 'text-amber-400'
              )}
            >
              <Activity className={cn(
                'w-5 h-5',
                isExecuting && 'animate-pulse'
              )} />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="left" className="bg-slate-900 border-slate-800">
            <p className="font-semibold">Timeline</p>
            <p className="text-xs text-slate-400">Execution checkpoints</p>
          </TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
}
