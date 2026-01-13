/**
 * SuggestionOverlay: AI 제안 프리뷰 오버레이
 * 
 * 워크플로우 캔버스 위에 AI 제안을 시각적으로 표시합니다.
 * 영향받는 노드들을 하이라이트하고, 제안 카드를 표시합니다.
 */
import React, { useMemo } from 'react';
import { useCodesignStore, selectActiveSuggestion } from '@/lib/codesignStore';
import { useWorkflowStore } from '@/lib/workflowStore';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Check, X, Eye, Layers, Plus, Edit, Trash2, ArrowRightLeft, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';

interface SuggestionOverlayProps {
  className?: string;
}

const actionIcons: Record<string, React.ReactNode> = {
  group: <Layers className="w-4 h-4" />,
  add_node: <Plus className="w-4 h-4" />,
  modify: <Edit className="w-4 h-4" />,
  delete: <Trash2 className="w-4 h-4" />,
  reorder: <ArrowRightLeft className="w-4 h-4" />,
  connect: <ArrowRightLeft className="w-4 h-4" />,
  optimize: <Zap className="w-4 h-4" />,
};

const actionLabels: Record<string, string> = {
  group: '그룹화 제안',
  add_node: '노드 추가 제안',
  modify: '수정 제안',
  delete: '삭제 제안',
  reorder: '순서 변경 제안',
  connect: '연결 제안',
  optimize: '최적화 제안',
};

export function SuggestionOverlay({ className }: SuggestionOverlayProps) {
  const { 
    pendingSuggestions,
    activeSuggestionId, 
    acceptSuggestion, 
    rejectSuggestion, 
    setActiveSuggestion 
  } = useCodesignStore();
  const { nodes } = useWorkflowStore();
  
  const activeSuggestion = pendingSuggestions.find(s => s.id === activeSuggestionId);
  
  // 영향받는 노드의 위치로 바운딩 박스 계산
  const bounds = useMemo(() => {
    if (!activeSuggestion || activeSuggestion.affectedNodes.length === 0) return null;
    
    const affectedPositions = activeSuggestion.affectedNodes
      .map(nodeId => nodes.find(n => n.id === nodeId))
      .filter((n): n is NonNullable<typeof n> => n !== undefined)
      .map(node => node.position);
    
    if (affectedPositions.length === 0) return null;
    
    const xs = affectedPositions.map(p => p.x);
    const ys = affectedPositions.map(p => p.y);
    
    const padding = 30;
    const nodeWidth = 200;
    const nodeHeight = 80;
    
    return {
      x: Math.min(...xs) - padding,
      y: Math.min(...ys) - padding - 30, // 배지용 여유 공간
      width: Math.max(...xs) - Math.min(...xs) + nodeWidth + padding * 2,
      height: Math.max(...ys) - Math.min(...ys) + nodeHeight + padding * 2 + 30
    };
  }, [activeSuggestion, nodes]);
  
  if (!activeSuggestion || activeSuggestion.status !== 'pending' || !bounds) {
    return null;
  }
  
  const confidencePercent = Math.round(activeSuggestion.confidence * 100);
  
  return (
    <div className={cn("pointer-events-none absolute inset-0 z-10", className)}>
      {/* 고스트 오버레이 - 영향받는 영역 하이라이트 */}
      <div
        className="absolute pointer-events-none animate-pulse"
        style={{
          left: bounds.x,
          top: bounds.y,
          width: bounds.width,
          height: bounds.height,
          border: '2px dashed rgba(59, 130, 246, 0.6)',
          borderRadius: '12px',
          backgroundColor: 'rgba(59, 130, 246, 0.08)',
          boxShadow: '0 0 20px rgba(59, 130, 246, 0.2)',
        }}
      >
        {/* 액션 타입 배지 */}
        <div className="absolute -top-7 left-3 flex items-center gap-2">
          <Badge 
            variant="secondary" 
            className="bg-blue-500 text-white border-0 shadow-sm"
          >
            {actionIcons[activeSuggestion.action]}
            <span className="ml-1">{actionLabels[activeSuggestion.action] || activeSuggestion.action}</span>
          </Badge>
          <Badge 
            variant="outline" 
            className="bg-background/80 backdrop-blur-sm"
          >
            신뢰도: {confidencePercent}%
          </Badge>
        </div>
      </div>
      
      {/* 제안 카드 */}
      <div
        className="absolute pointer-events-auto bg-card border shadow-lg rounded-lg p-4 max-w-xs animate-in fade-in slide-in-from-left-2 duration-200"
        style={{
          left: bounds.x + bounds.width + 16,
          top: bounds.y,
          maxWidth: '280px'
        }}
      >
        {/* 헤더 */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className="p-1.5 bg-blue-100 dark:bg-blue-900 rounded">
              {actionIcons[activeSuggestion.action]}
            </div>
            <span className="text-sm font-semibold">AI 제안</span>
          </div>
          <button
            onClick={() => setActiveSuggestion(null)}
            className="p-1 hover:bg-muted rounded transition-colors"
            aria-label="닫기"
          >
            <X className="w-4 h-4 text-muted-foreground" />
          </button>
        </div>
        
        {/* 제안 내용 */}
        <p className="text-sm text-muted-foreground mb-4 leading-relaxed">
          {activeSuggestion.reason}
        </p>
        
        {/* 영향받는 노드 목록 */}
        {activeSuggestion.affectedNodes.length > 0 && (
          <div className="mb-4">
            <p className="text-xs text-muted-foreground mb-1.5">영향받는 노드:</p>
            <div className="flex flex-wrap gap-1">
              {activeSuggestion.affectedNodes.slice(0, 5).map(nodeId => (
                <Badge 
                  key={nodeId} 
                  variant="secondary" 
                  className="text-xs font-mono"
                >
                  {nodeId}
                </Badge>
              ))}
              {activeSuggestion.affectedNodes.length > 5 && (
                <Badge variant="outline" className="text-xs">
                  +{activeSuggestion.affectedNodes.length - 5}
                </Badge>
              )}
            </div>
          </div>
        )}
        
        {/* 신뢰도 바 */}
        <div className="mb-4">
          <div className="flex justify-between text-xs mb-1">
            <span className="text-muted-foreground">신뢰도</span>
            <span className={cn(
              "font-medium",
              confidencePercent >= 80 ? "text-green-600" :
              confidencePercent >= 60 ? "text-yellow-600" : "text-orange-600"
            )}>
              {confidencePercent}%
            </span>
          </div>
          <div className="h-1.5 bg-muted rounded-full overflow-hidden">
            <div 
              className={cn(
                "h-full rounded-full transition-all",
                confidencePercent >= 80 ? "bg-green-500" :
                confidencePercent >= 60 ? "bg-yellow-500" : "bg-orange-500"
              )}
              style={{ width: `${confidencePercent}%` }}
            />
          </div>
        </div>
        
        {/* 액션 버튼 */}
        <div className="flex gap-2">
          <Button
            size="sm"
            className="flex-1"
            onClick={() => acceptSuggestion(activeSuggestion.id)}
          >
            <Check className="w-4 h-4 mr-1" />
            적용
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="flex-1"
            onClick={() => rejectSuggestion(activeSuggestion.id)}
          >
            <X className="w-4 h-4 mr-1" />
            거절
          </Button>
        </div>
      </div>
    </div>
  );
}

export default SuggestionOverlay;
