/**
 * Rollback Dialog
 * 
 * 롤백 미리보기와 실행을 위한 다이얼로그 컴포넌트입니다.
 * 상태 변경 사항을 미리 보고, 선택적으로 일부만 롤백할 수 있습니다.
 */

import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Checkbox } from '@/components/ui/checkbox';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import {
  AlertTriangle,
  RotateCcw,
  GitBranch,
  History,
  X,
  Check,
  Minus,
  Plus,
  ChevronRight,
  Shield,
  Loader2
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import type { StateDiff, RollbackRequest, TimelineItem, RollbackPreview, BranchInfo } from '@/lib/types';

interface RollbackDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  targetCheckpoint: TimelineItem | null;
  preview: RollbackPreview | null;
  loading?: boolean;
  onPreview: (checkpointId: string) => Promise<void>;
  onExecute: (request: Omit<RollbackRequest, 'preview_only'>) => Promise<BranchInfo>;
  onSuccess?: (result: BranchInfo) => void;
}

const DiffItem: React.FC<{
  type: 'added' | 'removed' | 'modified';
  keyName: string;
  value: any;
  previousValue?: any;
  selected?: boolean;
  onSelectChange?: (selected: boolean) => void;
  selectable?: boolean;
}> = ({ type, keyName, value, previousValue, selected, onSelectChange, selectable }) => {
  const config = {
    added: { icon: Plus, bgClass: 'bg-green-50', borderClass: 'border-green-200', textClass: 'text-green-700' },
    removed: { icon: Minus, bgClass: 'bg-red-50', borderClass: 'border-red-200', textClass: 'text-red-700' },
    modified: { icon: ChevronRight, bgClass: 'bg-yellow-50', borderClass: 'border-yellow-200', textClass: 'text-yellow-700' },
  };
  
  const { icon: Icon, bgClass, borderClass, textClass } = config[type];
  
  const formatValue = (val: any): string => {
    if (val === null) return 'null';
    if (val === undefined) return 'undefined';
    if (typeof val === 'object') return JSON.stringify(val, null, 2);
    return String(val);
  };
  
  return (
    <div className={cn(
      "p-2 rounded border mb-2",
      bgClass,
      borderClass,
      selectable && "cursor-pointer hover:opacity-80"
    )}>
      <div className="flex items-start gap-2">
        {selectable && (
          <Checkbox
            checked={selected}
            onCheckedChange={onSelectChange}
            className="mt-1"
          />
        )}
        <Icon className={cn("w-4 h-4 mt-0.5 shrink-0", textClass)} />
        <div className="flex-1 min-w-0">
          <div className={cn("font-mono text-sm", textClass)}>{keyName}</div>
          {type === 'modified' ? (
            <div className="text-xs mt-1 space-y-1">
              <div className="flex items-start gap-1">
                <span className="text-red-600">-</span>
                <pre className="text-red-600 whitespace-pre-wrap break-all">
                  {formatValue(previousValue)}
                </pre>
              </div>
              <div className="flex items-start gap-1">
                <span className="text-green-600">+</span>
                <pre className="text-green-600 whitespace-pre-wrap break-all">
                  {formatValue(value)}
                </pre>
              </div>
            </div>
          ) : (
            <pre className={cn("text-xs mt-1 whitespace-pre-wrap break-all", textClass)}>
              {formatValue(value)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
};

export const RollbackDialog: React.FC<RollbackDialogProps> = ({
  open,
  onOpenChange,
  targetCheckpoint,
  preview,
  loading = false,
  onPreview,
  onExecute,
  onSuccess,
}) => {
  const [executing, setExecuting] = useState(false);
  const [createBranch, setCreateBranch] = useState(true);
  const [selectedModifications, setSelectedModifications] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<BranchInfo | null>(null);
  
  // 미리보기 로드
  useEffect(() => {
    if (open && targetCheckpoint) {
      onPreview(targetCheckpoint.checkpoint_id);
    }
  }, [open, targetCheckpoint, onPreview]);
  
  // 다이얼로그 닫을 때 초기화
  useEffect(() => {
    if (!open) {
      setResult(null);
      setSelectedModifications(new Set());
    }
  }, [open]);
  
  // 전체 선택/해제
  const toggleAllModifications = (selected: boolean) => {
    if (selected && preview) {
      const allKeys = new Set<string>();
      Object.keys(preview.diff?.added || {}).forEach(k => allKeys.add(`added:${k}`));
      Object.keys(preview.diff?.removed || {}).forEach(k => allKeys.add(`removed:${k}`));
      Object.keys(preview.diff?.modified || {}).forEach(k => allKeys.add(`modified:${k}`));
      setSelectedModifications(allKeys);
    } else {
      setSelectedModifications(new Set());
    }
  };
  
  const toggleModification = (key: string, selected: boolean) => {
    const newSet = new Set(selectedModifications);
    if (selected) {
      newSet.add(key);
    } else {
      newSet.delete(key);
    }
    setSelectedModifications(newSet);
  };
  
  const handleExecute = async () => {
    if (!targetCheckpoint || !preview) return;
    
    setExecuting(true);
    try {
      // 선택된 수정사항만 포함
      const stateModifications: Record<string, any> = {};
      selectedModifications.forEach(key => {
        const [type, ...nameParts] = key.split(':');
        const name = nameParts.join(':');
        if (type === 'removed' && preview.diff?.removed) {
          stateModifications[name] = preview.diff.removed[name];
        } else if (type === 'modified' && preview.diff?.modified) {
          const mod = preview.diff.modified[name] as { old: unknown; new: unknown };
          stateModifications[name] = mod?.old;
        }
      });
      
      const request: Omit<RollbackRequest, 'preview_only'> = {
        thread_id: targetCheckpoint.execution_id,
        target_checkpoint_id: targetCheckpoint.checkpoint_id,
        state_modifications: stateModifications,
      };
      
      const result = await onExecute(request);
      setResult(result);
      
      if (result.success && onSuccess) {
        onSuccess(result);
      }
    } catch (error) {
      console.error('Rollback failed:', error);
      toast.error('롤백 처리 중 오류가 발생했습니다.');
    } finally {
      setExecuting(false);
    }
  };
  
  const totalChanges = preview 
    ? Object.keys(preview.diff?.added || {}).length +
      Object.keys(preview.diff?.removed || {}).length +
      Object.keys(preview.diff?.modified || {}).length
    : 0;
  
  const formatTimestamp = (ts: string) => {
    return new Date(ts).toLocaleString('ko-KR', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <RotateCcw className="w-5 h-5" />
            {result ? '롤백 완료' : '롤백 미리보기'}
          </DialogTitle>
          {targetCheckpoint && !result && (
            <DialogDescription>
              {targetCheckpoint.node_name} 시점으로 롤백
            </DialogDescription>
          )}
        </DialogHeader>

        <ScrollArea className="flex-1 pr-4">
          {loading ? (
            <div className="space-y-4">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : result ? (
            // 결과 표시
            <div className="space-y-4">
              <Alert variant={result.success ? "default" : "destructive"}>
                {result.success ? (
                  <Check className="h-4 w-4" />
                ) : (
                  <X className="h-4 w-4" />
                )}
                <AlertTitle>{result.success ? '롤백 성공' : '롤백 실패'}</AlertTitle>
                <AlertDescription>
                  {result.success 
                    ? `새 브랜치 ${result.branched_thread_id}가 생성되었습니다.` 
                    : '롤백 중 오류가 발생했습니다.'}
                </AlertDescription>
              </Alert>
              
              {result.success && result.branched_thread_id && (
                <Card>
                  <CardHeader className="py-3">
                    <CardTitle className="text-sm flex items-center gap-2">
                      <GitBranch className="w-4 h-4" />
                      새 브랜치가 생성되었습니다
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <Badge variant="secondary" className="font-mono">
                      {result.branched_thread_id}
                    </Badge>
                    <p className="text-xs text-muted-foreground mt-2">
                      원본 실행 기록은 보존됩니다.
                    </p>
                  </CardContent>
                </Card>
              )}
            </div>
          ) : preview ? (
            // 미리보기 표시
            <div className="space-y-4">
              {/* 대상 정보 */}
              <Card>
                <CardContent className="pt-4">
                  <div className="flex items-center gap-2 text-sm">
                    <History className="w-4 h-4 text-muted-foreground" />
                    <span className="font-medium">
                      {targetCheckpoint?.node_name || '체크포인트'}
                    </span>
                    {targetCheckpoint && (
                      <span className="text-muted-foreground">
                        ({formatTimestamp(targetCheckpoint.timestamp)})
                      </span>
                    )}
                  </div>
                  {preview.resume_from_node && (
                    <div className="mt-2">
                      <span className="text-xs text-muted-foreground">재개 노드: </span>
                      <span className="text-xs">{preview.resume_from_node}</span>
                    </div>
                  )}
                  <div className="mt-2 text-xs text-muted-foreground">
                    예상 영향: {preview.estimated_impact}
                  </div>
                </CardContent>
              </Card>
              
              {/* 상태 변경 사항 */}
              {totalChanges > 0 && (
                <Card>
                  <CardHeader className="py-3">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-sm">상태 변경 사항</CardTitle>
                      <Button 
                        variant="ghost" 
                        size="sm"
                        onClick={() => toggleAllModifications(selectedModifications.size === 0)}
                      >
                        {selectedModifications.size === 0 ? '전체 선택' : '선택 해제'}
                      </Button>
                    </div>
                  </CardHeader>
                  <CardContent>
                    {/* 제거될 항목 (추가된 것들 - 롤백 시 제거됨) */}
                    {Object.keys(preview.diff?.added || {}).length > 0 && (
                      <div className="mb-4">
                        <div className="text-xs font-medium text-red-600 mb-2 flex items-center gap-1">
                          <Minus className="w-3 h-3" />
                          제거될 항목
                        </div>
                        {Object.entries(preview.diff?.added || {}).map(([key, value]) => (
                          <DiffItem
                            key={`added:${key}`}
                            type="added"
                            keyName={key}
                            value={value}
                            selectable
                            selected={selectedModifications.has(`added:${key}`)}
                            onSelectChange={(s) => toggleModification(`added:${key}`, s)}
                          />
                        ))}
                      </div>
                    )}
                    
                    {/* 복원될 항목 (제거된 것들 - 롤백 시 복원됨) */}
                    {Object.keys(preview.diff?.removed || {}).length > 0 && (
                      <div className="mb-4">
                        <div className="text-xs font-medium text-green-600 mb-2 flex items-center gap-1">
                          <Plus className="w-3 h-3" />
                          복원될 항목
                        </div>
                        {Object.entries(preview.diff?.removed || {}).map(([key, value]) => (
                          <DiffItem
                            key={`removed:${key}`}
                            type="removed"
                            keyName={key}
                            value={value}
                            selectable
                            selected={selectedModifications.has(`removed:${key}`)}
                            onSelectChange={(s) => toggleModification(`removed:${key}`, s)}
                          />
                        ))}
                      </div>
                    )}
                    
                    {/* 변경될 항목 */}
                    {Object.keys(preview.diff?.modified || {}).length > 0 && (
                      <div>
                        <div className="text-xs font-medium text-yellow-600 mb-2 flex items-center gap-1">
                          <ChevronRight className="w-3 h-3" />
                          이전 값으로 되돌릴 항목
                        </div>
                        {Object.entries(preview.diff?.modified || {}).map(([key, change]: [string, any]) => (
                          <DiffItem
                            key={`modified:${key}`}
                            type="modified"
                            keyName={key}
                            value={change.old}
                            previousValue={change.new}
                            selectable
                            selected={selectedModifications.has(`modified:${key}`)}
                            onSelectChange={(s) => toggleModification(`modified:${key}`, s)}
                          />
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}
              
              {/* 옵션 */}
              <Card>
                <CardContent className="pt-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <GitBranch className="w-4 h-4 text-muted-foreground" />
                      <Label htmlFor="create-branch">새 브랜치로 분기</Label>
                    </div>
                    <Switch
                      id="create-branch"
                      checked={createBranch}
                      onCheckedChange={setCreateBranch}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground mt-2">
                    {createBranch 
                      ? '원본 실행 기록을 보존하고 새로운 브랜치를 생성합니다.'
                      : '⚠️ 원본 실행 기록을 덮어씁니다. 주의하세요!'}
                  </p>
                  {createBranch && preview.branch_depth && (
                    <Badge variant="outline" className="mt-2 font-mono text-xs">
                      브랜치 깊이: {preview.branch_depth}
                    </Badge>
                  )}
                </CardContent>
              </Card>
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              롤백할 체크포인트를 선택해주세요.
            </div>
          )}
        </ScrollArea>

        <Separator className="my-2" />

        <DialogFooter className="gap-2">
          {result ? (
            <Button onClick={() => onOpenChange(false)}>
              닫기
            </Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                취소
              </Button>
              <Button
                variant="destructive"
                onClick={handleExecute}
                disabled={loading || executing || !preview}
              >
                {executing ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    롤백 중...
                  </>
                ) : (
                  <>
                    <RotateCcw className="w-4 h-4 mr-2" />
                    롤백 실행
                  </>
                )}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default RollbackDialog;
