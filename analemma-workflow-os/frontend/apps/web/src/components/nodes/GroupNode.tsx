import { memo } from 'react';
import { Handle, Position, NodeProps } from '@xyflow/react';
import { Layers, ChevronRight, Ungroup } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '../ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '../ui/tooltip';
import { useWorkflowStore } from '@/lib/workflowStore';

interface GroupNodeData {
  label: string;
  subgraphId: string;
  nodeCount: number;
  description?: string;
}

export const GroupNode = memo(({ id, data, selected }: NodeProps) => {
  const nodeData = data as unknown as GroupNodeData;
  const { ungroupNode } = useWorkflowStore();

  const handleUngroup = (e: React.MouseEvent) => {
    e.stopPropagation();
    ungroupNode(id);
  };

  return (
    <div
      className={cn(
        'min-w-[180px] rounded-xl border-2 border-dashed transition-all duration-200',
        'bg-gradient-to-br from-violet-500/10 to-purple-500/10',
        'hover:shadow-lg hover:shadow-purple-500/20',
        selected
          ? 'border-purple-400 ring-2 ring-purple-400/50'
          : 'border-purple-500/50'
      )}
    >
      {/* 입력 핸들 */}
      <Handle
        type="target"
        position={Position.Left}
        className="!w-3 !h-3 !bg-purple-400 !border-2 !border-purple-600"
      />

      {/* 헤더 */}
      <div className="flex items-center justify-between gap-2 px-3 py-2 bg-purple-500/20 rounded-t-lg border-b border-purple-500/30">
        <div className="flex items-center gap-2">
          <div className="p-1 rounded bg-purple-500/30">
            <Layers className="w-4 h-4 text-purple-300" />
          </div>
          <span className="font-medium text-sm text-white">
            {nodeData.label}
          </span>
        </div>
        
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="w-6 h-6 hover:bg-purple-500/30"
              onClick={handleUngroup}
            >
              <Ungroup className="w-3 h-3 text-purple-300" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">
            <p className="text-xs">그룹 해제</p>
          </TooltipContent>
        </Tooltip>
      </div>

      {/* 본문 */}
      <div className="px-3 py-3 space-y-2">
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">포함된 노드</span>
          <span className="font-mono text-purple-300">{nodeData.nodeCount}개</span>
        </div>
        
        {nodeData.description && (
          <p className="text-xs text-muted-foreground line-clamp-2">
            {nodeData.description}
          </p>
        )}

        {/* 더블클릭 안내 */}
        <div className="flex items-center justify-center gap-1 pt-2 text-xs text-muted-foreground">
          <span>더블클릭하여 상세 보기</span>
          <ChevronRight className="w-3 h-3" />
        </div>
      </div>

      {/* 출력 핸들 */}
      <Handle
        type="source"
        position={Position.Right}
        className="!w-3 !h-3 !bg-purple-400 !border-2 !border-purple-600"
      />
    </div>
  );
});

GroupNode.displayName = 'GroupNode';
