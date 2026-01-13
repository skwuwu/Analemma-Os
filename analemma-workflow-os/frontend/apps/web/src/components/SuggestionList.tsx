/**
 * SuggestionList: AI 제안 목록 패널
 * 
 * 캔버스 우하단에 표시되는 AI 제안 목록입니다.
 * 제안을 클릭하면 해당 영역을 하이라이트합니다.
 */
import React from 'react';
import { 
  useCodesignStore, 
  selectPendingSuggestions,
  SuggestionPreview 
} from '@/lib/codesignStore';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { 
  Lightbulb, 
  Check, 
  X, 
  ChevronRight, 
  Layers,
  Plus,
  Edit,
  Trash2,
  ArrowRightLeft,
  Zap,
  Sparkles
} from 'lucide-react';
import { cn } from '@/lib/utils';

interface SuggestionListProps {
  className?: string;
  maxHeight?: string;
  onSuggestionApply?: (suggestion: SuggestionPreview) => void;
}

const actionIcons: Record<string, React.ReactNode> = {
  group: <Layers className="w-3.5 h-3.5" />,
  add_node: <Plus className="w-3.5 h-3.5" />,
  modify: <Edit className="w-3.5 h-3.5" />,
  delete: <Trash2 className="w-3.5 h-3.5" />,
  reorder: <ArrowRightLeft className="w-3.5 h-3.5" />,
  connect: <ArrowRightLeft className="w-3.5 h-3.5" />,
  optimize: <Zap className="w-3.5 h-3.5" />,
};

const actionLabels: Record<string, string> = {
  group: '그룹화',
  add_node: '노드 추가',
  modify: '수정',
  delete: '삭제',
  reorder: '순서 변경',
  connect: '연결',
  optimize: '최적화',
};

export function SuggestionList({ 
  className, 
  maxHeight = '320px',
  onSuggestionApply 
}: SuggestionListProps) {
  const { 
    pendingSuggestions, 
    activeSuggestionId, 
    setActiveSuggestion,
    acceptSuggestion,
    rejectSuggestion
  } = useCodesignStore();
  
  const pendingOnly = pendingSuggestions.filter(s => s.status === 'pending');
  
  const handleAccept = (e: React.MouseEvent, suggestion: SuggestionPreview) => {
    e.stopPropagation();
    acceptSuggestion(suggestion.id);
    onSuggestionApply?.(suggestion);
  };
  
  const handleReject = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    rejectSuggestion(id);
  };
  
  if (pendingOnly.length === 0) {
    return null;
  }
  
  return (
    <div 
      className={cn(
        "w-80 bg-card/95 backdrop-blur-sm border rounded-lg shadow-lg overflow-hidden",
        className
      )}
    >
      {/* 헤더 */}
      <div className="px-3 py-2.5 border-b bg-muted/50 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="p-1 bg-yellow-100 dark:bg-yellow-900 rounded">
            <Lightbulb className="w-4 h-4 text-yellow-600 dark:text-yellow-400" />
          </div>
          <span className="text-sm font-medium">AI 제안</span>
          <Badge variant="secondary" className="text-xs px-1.5 py-0">
            {pendingOnly.length}
          </Badge>
        </div>
        <Sparkles className="w-4 h-4 text-muted-foreground" />
      </div>
      
      {/* 제안 목록 */}
      <ScrollArea style={{ maxHeight }}>
        <div className="divide-y">
          {pendingOnly.map((suggestion) => (
            <SuggestionItem
              key={suggestion.id}
              suggestion={suggestion}
              isActive={activeSuggestionId === suggestion.id}
              onToggle={() => setActiveSuggestion(
                activeSuggestionId === suggestion.id ? null : suggestion.id
              )}
              onAccept={(e) => handleAccept(e, suggestion)}
              onReject={(e) => handleReject(e, suggestion.id)}
            />
          ))}
        </div>
      </ScrollArea>
      
      {/* 푸터 - 전체 적용/거절 */}
      {pendingOnly.length > 1 && (
        <div className="px-3 py-2 border-t bg-muted/30 flex gap-2">
          <Button
            size="sm"
            variant="outline"
            className="flex-1 h-7 text-xs"
            onClick={() => pendingOnly.forEach(s => rejectSuggestion(s.id))}
          >
            <X className="w-3 h-3 mr-1" />
            모두 거절
          </Button>
        </div>
      )}
    </div>
  );
}

interface SuggestionItemProps {
  suggestion: SuggestionPreview;
  isActive: boolean;
  onToggle: () => void;
  onAccept: (e: React.MouseEvent) => void;
  onReject: (e: React.MouseEvent) => void;
}

function SuggestionItem({
  suggestion,
  isActive,
  onToggle,
  onAccept,
  onReject
}: SuggestionItemProps) {
  const confidencePercent = Math.round(suggestion.confidence * 100);
  
  return (
    <div
      className={cn(
        "px-3 py-2.5 cursor-pointer transition-colors",
        "hover:bg-muted/50",
        isActive && "bg-blue-50 dark:bg-blue-950 border-l-2 border-l-blue-500"
      )}
      onClick={onToggle}
    >
      {/* 상단: 액션 타입 + 버튼 */}
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-1.5">
          <span className="text-muted-foreground">
            {actionIcons[suggestion.action]}
          </span>
          <span className="text-sm font-medium">
            {actionLabels[suggestion.action] || suggestion.action}
          </span>
          <Badge 
            variant="outline" 
            className={cn(
              "text-[10px] px-1 py-0 h-4",
              confidencePercent >= 80 ? "text-green-600 border-green-300" :
              confidencePercent >= 60 ? "text-yellow-600 border-yellow-300" :
              "text-orange-600 border-orange-300"
            )}
          >
            {confidencePercent}%
          </Badge>
        </div>
        
        <div className="flex items-center gap-0.5">
          <button
            onClick={onAccept}
            className={cn(
              "p-1 rounded transition-colors",
              "hover:bg-green-100 dark:hover:bg-green-900"
            )}
            title="적용"
          >
            <Check className="w-3.5 h-3.5 text-green-600" />
          </button>
          <button
            onClick={onReject}
            className={cn(
              "p-1 rounded transition-colors",
              "hover:bg-red-100 dark:hover:bg-red-900"
            )}
            title="거절"
          >
            <X className="w-3.5 h-3.5 text-red-600" />
          </button>
          <ChevronRight 
            className={cn(
              "w-4 h-4 text-muted-foreground transition-transform",
              isActive && "rotate-90"
            )} 
          />
        </div>
      </div>
      
      {/* 제안 이유 */}
      <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">
        {suggestion.reason}
      </p>
      
      {/* 확장 시 영향받는 노드 표시 */}
      {isActive && suggestion.affectedNodes.length > 0 && (
        <div className="mt-2 pt-2 border-t border-dashed">
          <p className="text-[10px] text-muted-foreground mb-1">영향받는 노드:</p>
          <div className="flex flex-wrap gap-1">
            {suggestion.affectedNodes.slice(0, 4).map(nodeId => (
              <Badge 
                key={nodeId} 
                variant="secondary" 
                className="text-[10px] font-mono px-1.5 py-0 h-4"
              >
                {nodeId}
              </Badge>
            ))}
            {suggestion.affectedNodes.length > 4 && (
              <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4">
                +{suggestion.affectedNodes.length - 4}
              </Badge>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default SuggestionList;
