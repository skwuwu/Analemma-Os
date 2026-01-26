/**
 * Rollback Dialog
 * 
 * 롤백 미리보기와 실행을 위한 다이얼로그 컴포넌트입니다.
 * 상태 변경 사항을 미리 보고, 선택적으로 일부만 롤백할 수 있습니다.
 */

import React, { useState, useEffect, useMemo } from 'react';
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
  Loader2,
  GitCommit,
  ArrowRight,
  Code
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import type { StateDiff, RollbackRequest, TimelineItem, RollbackPreview, BranchInfo } from '@/lib/types';
import { motion, AnimatePresence } from 'framer-motion';
import JsonViewer from './JsonViewer';

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

// --- CONSTANTS ---
const DELIMITER = '@@';

// --- SUB-COMPONENTS ---

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
    added: { icon: Plus, bgClass: 'bg-emerald-50/50 dark:bg-emerald-500/5', borderClass: 'border-emerald-100 dark:border-emerald-500/20', textClass: 'text-emerald-600', label: 'Restoring' },
    removed: { icon: Minus, bgClass: 'bg-rose-50/50 dark:bg-rose-500/5', borderClass: 'border-rose-100 dark:border-rose-500/20', textClass: 'text-rose-600', label: 'Removing' },
    modified: { icon: ChevronRight, bgClass: 'bg-amber-50/50 dark:bg-amber-500/5', borderClass: 'border-amber-100 dark:border-amber-500/20', textClass: 'text-amber-600', label: 'Reverting' },
  };

  const { icon: Icon, bgClass, borderClass, textClass, label } = config[type];

  const isObject = (val: any) => val !== null && typeof val === 'object';

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      className={cn(
        "p-4 rounded-2xl border mb-3 transition-all",
        bgClass,
        borderClass,
        selected && "ring-1 ring-inset ring-slate-400/20 shadow-sm"
      )}
    >
      <div className="flex items-start gap-4">
        {selectable && (
          <Checkbox
            checked={selected}
            onCheckedChange={onSelectChange}
            className="mt-1.5"
          />
        )}
        <div className="flex-1 min-w-0 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className={cn("p-1 rounded-md bg-white dark:bg-slate-900 shadow-sm", textClass)}>
                <Icon className="w-3.5 h-3.5" />
              </div>
              <span className={cn("text-[10px] font-black uppercase tracking-widest", textClass)}>
                {label}
              </span>
              <div className="w-1.5 h-1.5 rounded-full opacity-20 bg-slate-400" />
              <span className="font-mono text-xs font-bold text-slate-700 dark:text-slate-300 truncate">
                {keyName}
              </span>
            </div>
          </div>

          <div className="space-y-3">
            {type === 'modified' ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <div className="text-[9px] font-black text-slate-400 uppercase tracking-tighter flex items-center gap-1">
                    <Minus className="w-2.5 h-2.5" /> Current Value (Discard)
                  </div>
                  {isObject(previousValue) ? (
                    <JsonViewer src={previousValue} collapsed={true} className="text-[11px]" />
                  ) : (
                    <div className="p-3 bg-white dark:bg-slate-900 border border-slate-100 dark:border-slate-800 rounded-xl text-xs font-mono text-rose-500 line-through opacity-60">
                      {String(previousValue)}
                    </div>
                  )}
                </div>
                <div className="space-y-1.5">
                  <div className="text-[9px] font-black text-emerald-500 uppercase tracking-tighter flex items-center gap-1">
                    <Plus className="w-2.5 h-2.5" /> Restore Target
                  </div>
                  {isObject(value) ? (
                    <JsonViewer src={value} collapsed={true} className="text-[11px]" />
                  ) : (
                    <div className="p-3 bg-white dark:bg-slate-900 border border-emerald-100 dark:border-emerald-500/20 rounded-xl text-xs font-mono text-emerald-600 font-bold">
                      {String(value)}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="space-y-1.5">
                <div className="text-[9px] font-black opacity-40 uppercase tracking-tighter">
                  {type === 'added' ? 'Node State to be Restored' : 'State Fragment to be Excised'}
                </div>
                {isObject(value) ? (
                  <JsonViewer src={value} collapsed={true} className="text-[11px]" />
                ) : (
                  <div className={cn(
                    "p-3 rounded-xl text-xs font-mono bg-white dark:bg-slate-900 border transition-colors",
                    type === 'added' ? "border-emerald-100 text-emerald-600 font-bold" : "border-rose-100 text-rose-500 opacity-60 line-through"
                  )}>
                    {String(value)}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  );
};

// --- MAIN DIALOG ---

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
      Object.keys(preview.diff?.added || {}).forEach(k => allKeys.add(`added${DELIMITER}${k}`));
      Object.keys(preview.diff?.removed || {}).forEach(k => allKeys.add(`removed${DELIMITER}${k}`));
      Object.keys(preview.diff?.modified || {}).forEach(k => allKeys.add(`modified${DELIMITER}${k}`));
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
        // Safe parsing with safer delimiter
        const firstDelimiterIndex = key.indexOf(DELIMITER);
        if (firstDelimiterIndex === -1) return;

        const type = key.substring(0, firstDelimiterIndex);
        const name = key.substring(firstDelimiterIndex + DELIMITER.length);

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

  const totalChanges = useMemo(() => preview
    ? Object.keys(preview.diff?.added || {}).length +
    Object.keys(preview.diff?.removed || {}).length +
    Object.keys(preview.diff?.modified || {}).length
    : 0, [preview]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl h-[85vh] flex flex-col p-0 bg-white dark:bg-slate-950 border-none rounded-3xl overflow-hidden shadow-2xl">
        <DialogHeader className="px-8 py-6 border-b border-slate-100 dark:border-slate-800 bg-slate-50/20">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-primary text-white rounded-xl shadow-lg shadow-primary/20">
              <RotateCcw className="w-5 h-5" />
            </div>
            <div className="space-y-0.5">
              <DialogTitle className="text-lg font-black tracking-tight">
                {result ? 'Rollback Sequence Finalized' : 'Checkpoint State Audit'}
              </DialogTitle>
              <DialogDescription className="text-xs font-bold text-slate-400 uppercase tracking-widest">
                {targetCheckpoint?.node_name ? `Target Node: ${targetCheckpoint.node_name}` : 'Temporal State Reconstruction'}
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <ScrollArea className="flex-1 custom-scrollbar">
          {loading ? (
            <div className="p-8 space-y-6">
              <Skeleton className="h-24 w-full rounded-2xl" />
              <div className="space-y-4">
                <Skeleton className="h-10 w-48 rounded-lg" />
                <Skeleton className="h-32 w-full rounded-2xl" />
                <Skeleton className="h-32 w-full rounded-2xl" />
              </div>
            </div>
          ) : result ? (
            // 결과 표시
            <AnimatePresence>
              <motion.div
                initial={{ opacity: 0, scale: 0.98 }}
                animate={{ opacity: 1, scale: 1 }}
                className="p-8 space-y-6"
              >
                <Alert className={cn(
                  "rounded-3xl border-2 py-6 px-8",
                  result.success ? "border-emerald-100 bg-emerald-50/50 text-emerald-800" : "border-rose-100 bg-rose-50/50 text-rose-800"
                )}>
                  <div className="flex items-center gap-4 mb-2">
                    <div className={cn("p-2 rounded-xl text-white", result.success ? "bg-emerald-500" : "bg-rose-500")}>
                      {result.success ? <Check className="h-5 w-5" /> : <X className="h-5 w-5" />}
                    </div>
                    <AlertTitle className="text-lg font-black tracking-tight m-0">
                      {result.success ? 'Success: Reality Reconstructed' : 'Failure: Temporal Conflict'}
                    </AlertTitle>
                  </div>
                  <AlertDescription className="pl-14 text-sm font-medium leading-relaxed opacity-80">
                    {result.success
                      ? `The agent state was successfully rolled back. A new branch "${result.branched_thread_id}" has been established.`
                      : 'A system error occurred during state reconstruction. Please check node dependencies and try again.'}
                  </AlertDescription>
                </Alert>

                {result.success && result.branched_thread_id && (
                  <Card className="rounded-3xl border-slate-100 overflow-hidden shadow-sm">
                    <CardHeader className="py-4 px-6 border-b border-slate-50 bg-slate-50/30">
                      <CardTitle className="text-xs font-black uppercase tracking-widest text-slate-400 flex items-center gap-2">
                        <GitBranch className="w-4 h-4" /> Established Branch Handle
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="p-8 flex flex-col items-center gap-6">
                      <div className="flex items-center gap-4 py-4 px-6 bg-slate-900 rounded-3xl shadow-inner border border-slate-800 w-full justify-center">
                        <GitCommit className="w-5 h-5 text-blue-500" />
                        <code className="text-xl font-black text-blue-400 tracking-tighter">
                          {result.branched_thread_id}
                        </code>
                      </div>
                      <p className="text-sm font-medium text-slate-500 text-center max-w-sm">
                        The original execution history remains preserved in the "Primary Timeline". All subsequent actions will proceed from this new coordinate.
                      </p>
                    </CardContent>
                  </Card>
                )}
              </motion.div>
            </AnimatePresence>
          ) : preview ? (
            // 미리보기 표시
            <div className="p-8 space-y-8">
              {/* 대상 정보 */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="p-6 rounded-3xl border border-slate-100 dark:border-slate-800 bg-slate-50/20 space-y-4">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-400">Target Origin</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-white dark:bg-slate-900 border border-slate-100 dark:border-slate-800 flex items-center justify-center shadow-sm">
                      <GitCommit className="w-5 h-5 text-primary" />
                    </div>
                    <div>
                      <div className="text-sm font-black text-slate-700 dark:text-slate-200">{targetCheckpoint?.node_name}</div>
                      <time className="text-[10px] font-mono font-bold text-slate-400">
                        {new Date(targetCheckpoint?.timestamp || '').toLocaleString()}
                      </time>
                    </div>
                  </div>
                </div>

                <div className="p-6 rounded-3xl border border-blue-100 bg-blue-50/20 space-y-4">
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-black uppercase tracking-[0.2em] text-blue-500">Operation Impact</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-blue-100 flex items-center justify-center">
                      <ZapIcon className="w-5 h-5 text-blue-600" />
                    </div>
                    <div>
                      <div className="text-sm font-black text-blue-700">{preview.estimated_impact}</div>
                      <div className="text-[10px] font-bold text-blue-500/60 uppercase">Heuristic Analysis Results</div>
                    </div>
                  </div>
                </div>
              </div>

              {/* 상태 변경 사항 */}
              {totalChanges > 0 ? (
                <div className="space-y-4">
                  <div className="flex items-center justify-between px-2">
                    <div className="flex items-center gap-3">
                      <Code className="w-4 h-4 text-slate-400" />
                      <h4 className="text-xs font-black uppercase tracking-[0.1em] text-slate-500">State Delta Audit ({totalChanges})</h4>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 text-[10px] font-black uppercase tracking-widest text-slate-400 hover:text-primary transition-all"
                      onClick={() => toggleAllModifications(selectedModifications.size === 0)}
                    >
                      {selectedModifications.size === 0 ? 'Select All Samples' : 'Deselect All'}
                    </Button>
                  </div>

                  <div className="space-y-4">
                    {/* 복원될 항목 (제거된 것들 - 롤백 시 복원됨) */}
                    {Object.keys(preview.diff?.removed || {}).length > 0 && (
                      <div className="space-y-3">
                        {Object.entries(preview.diff?.removed || {}).map(([key, value]) => (
                          <DiffItem
                            key={`removed${DELIMITER}${key}`}
                            type="added"
                            keyName={key}
                            value={value}
                            selectable
                            selected={selectedModifications.has(`removed${DELIMITER}${key}`)}
                            onSelectChange={(s) => toggleModification(`removed${DELIMITER}${key}`, s)}
                          />
                        ))}
                      </div>
                    )}

                    {/* 제거될 항목 (추가된 것들 - 롤백 시 제거됨) */}
                    {Object.keys(preview.diff?.added || {}).length > 0 && (
                      <div className="space-y-3">
                        {Object.entries(preview.diff?.added || {}).map(([key, value]) => (
                          <DiffItem
                            key={`added${DELIMITER}${key}`}
                            type="removed"
                            keyName={key}
                            value={value}
                            selectable
                            selected={selectedModifications.has(`added${DELIMITER}${key}`)}
                            onSelectChange={(s) => toggleModification(`added${DELIMITER}${key}`, s)}
                          />
                        ))}
                      </div>
                    )}

                    {/* 변경될 항목 */}
                    {Object.keys(preview.diff?.modified || {}).length > 0 && (
                      <div className="space-y-3">
                        {Object.entries(preview.diff?.modified || {}).map(([key, change]: [string, any]) => (
                          <DiffItem
                            key={`modified${DELIMITER}${key}`}
                            type="modified"
                            keyName={key}
                            value={change.old}
                            previousValue={change.new}
                            selectable
                            selected={selectedModifications.has(`modified${DELIMITER}${key}`)}
                            onSelectChange={(s) => toggleModification(`modified${DELIMITER}${key}`, s)}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-12 border-2 border-dashed border-slate-100 rounded-3xl">
                  <Shield className="w-12 h-12 text-slate-100 mb-4" />
                  <p className="text-sm font-bold text-slate-400">Neutral State Delta</p>
                  <p className="text-[11px] text-slate-300 mt-1">No significant differences detected between timelines.</p>
                </div>
              )}

              {/* Strategy Policy Selection */}
              <div className="space-y-4 pt-4">
                <div className="flex items-center gap-3 px-2">
                  <GitBranch className="w-4 h-4 text-slate-400" />
                  <h4 className="text-xs font-black uppercase tracking-[0.1em] text-slate-500">Execution Strategy Profile</h4>
                </div>

                <Card className={cn(
                  "rounded-3xl border transition-all overflow-hidden",
                  createBranch ? "border-slate-100 bg-slate-50/20" : "border-rose-100 bg-rose-50/40 dark:bg-rose-500/5 shadow-lg shadow-rose-900/5"
                )}>
                  <CardContent className="p-6">
                    <div className="flex items-center justify-between mb-4">
                      <Label htmlFor="create-branch" className="flex flex-col gap-1.5 cursor-pointer">
                        <span className="font-black text-sm tracking-tight text-slate-700 dark:text-slate-200">Reality Branching Mode</span>
                        <span className="text-[11px] font-medium text-slate-400 max-w-[280px] leading-relaxed">
                          {createBranch
                            ? 'Preserve the legacy history and establish a new divergent timeline.'
                            : 'Permanently replace the existing timeline with this reconstruction.'}
                        </span>
                      </Label>
                      <Switch
                        id="create-branch"
                        checked={createBranch}
                        onCheckedChange={setCreateBranch}
                        className="data-[state=checked]:bg-blue-600 data-[state=unchecked]:bg-rose-500"
                      />
                    </div>

                    {!createBranch && (
                      <div className="flex items-start gap-2.5 p-3 rounded-2xl bg-white dark:bg-rose-900/20 border border-rose-200 dark:border-rose-500/30 animate-pulse">
                        <AlertTriangle className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
                        <p className="text-[10px] font-black text-rose-600 uppercase tracking-tighter leading-normal">
                          Warning: Overwrite mode is irreversible. The current state will be lost forever.
                        </p>
                      </div>
                    )}

                    {createBranch && preview.branch_depth !== undefined && (
                      <div className="flex items-center justify-between pt-2">
                        <div className="flex items-center gap-2">
                          <LayersIcon className="w-3.5 h-3.5 text-slate-400" />
                          <span className="text-[11px] font-bold text-slate-400">Current Depth:</span>
                        </div>
                        <Badge variant="outline" className="font-mono font-black text-[10px] border-slate-200 h-5 px-2">
                          LEVEL {preview.branch_depth + 1}
                        </Badge>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full p-12 text-center text-slate-400">
              <RotateCcw className="w-12 h-12 opacity-10 mb-4" />
              <p className="text-sm font-bold">Awaiting Selection</p>
              <p className="text-[11px] opacity-60 mt-1 max-w-[180px]">Select a valid temporal checkpoint from the timeline to audit the state delta.</p>
            </div>
          )}
        </ScrollArea>

        <Separator />

        <DialogFooter className="px-8 py-5 gap-3 bg-slate-50/50 backdrop-blur-sm">
          <Button variant="ghost" onClick={() => onOpenChange(false)} className="h-11 px-6 font-bold text-xs text-slate-400 hover:text-slate-100 hover:bg-slate-800 rounded-xl transition-all">
            <X className="w-4 h-4 mr-2" /> {result ? 'Close Audit' : 'Discard Proposal'}
          </Button>
          <div className="flex-1" />
          {!result && (
            <Button
              variant={createBranch ? "default" : "destructive"}
              onClick={handleExecute}
              disabled={loading || executing || !preview}
              className={cn(
                "h-11 px-8 font-black text-xs uppercase tracking-widest rounded-xl shadow-xl transition-all active:scale-95 min-w-[180px]",
                createBranch ? "bg-primary hover:bg-primary shadow-primary/20" : "bg-rose-600 hover:bg-rose-500 shadow-rose-900/20"
              )}
            >
              {executing ? (
                <div className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Reconstructing...</span>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  {createBranch ? <GitBranch className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
                  <span>{createBranch ? 'Branch & Reconstruct' : 'Overwrite Original'}</span>
                </div>
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

// --- ICON COMPONENTS ---

const ZapIcon = ({ className }: { className?: string }) => (
  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
    <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
  </svg>
);

const LayersIcon = ({ className }: { className?: string }) => (
  <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
    <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
  </svg>
);

export default RollbackDialog;
