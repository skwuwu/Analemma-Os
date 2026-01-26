/**
 * State Diff Viewer (v2.0)
 * =========================
 * 
 * 두 체크포인트 간의 상태 차이를 시각화하는 정밀 진단 도구입니다.
 * Deep Diffing을 지원하며, JsonViewer를 통해 복잡한 상태 변화를 투명하게 공개합니다.
 */

import React, { useState, useMemo } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Plus,
  Minus,
  ChevronDown,
  GitCompare,
  Copy,
  Check,
  ArrowLeftRight,
  Database,
  Search,
  Zap
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { StateDiff, CheckpointCompareResult } from '@/lib/types';
import { motion, AnimatePresence } from 'framer-motion';
import JsonViewer from './JsonViewer';

interface StateDiffViewerProps {
  diff: StateDiff | null;
  compareResult?: CheckpointCompareResult;
  loading?: boolean;
  sourceLabel?: string;
  targetLabel?: string;
}

interface DiffRowProps {
  keyName: string;
  type: 'added' | 'removed' | 'modified';
  value?: any;
  oldValue?: any;
  newValue?: any;
}

// --- SUB-COMPONENTS ---

/**
 * 변경 사항의 한 줄(Row)을 렌더링하는 컴포넌트
 */
const DiffRow: React.FC<DiffRowProps> = ({ keyName, type, value, oldValue, newValue }) => {
  const [copied, setCopied] = useState(false);

  const config = {
    added: {
      icon: Plus,
      bgClass: 'bg-emerald-500/5',
      borderClass: 'border-emerald-500/20',
      textClass: 'text-emerald-500',
      label: 'RESTORED/ADDED'
    },
    removed: {
      icon: Minus,
      bgClass: 'bg-rose-500/5',
      borderClass: 'border-rose-500/20',
      textClass: 'text-rose-500',
      label: 'REMOVED'
    },
    modified: {
      icon: ArrowLeftRight,
      bgClass: 'bg-amber-500/5',
      borderClass: 'border-amber-500/20',
      textClass: 'text-amber-500',
      label: 'MODIFIED'
    },
  };

  const { icon: Icon, bgClass, borderClass, textClass, label } = config[type];

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const dataToCopy = type === 'modified' ? { old: oldValue, new: newValue } : value;
    await navigator.clipboard.writeText(JSON.stringify(dataToCopy, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      className="mb-3"
    >
      <Collapsible className={cn("group border-2 rounded-2xl overflow-hidden transition-all duration-200", borderClass)}>
        <CollapsibleTrigger asChild>
          <div className={cn("w-full flex items-center justify-between p-4 cursor-pointer hover:bg-slate-50/50 dark:hover:bg-slate-900/50", bgClass)}>
            <div className="flex items-center gap-3 min-w-0">
              <div className={cn("p-1.5 rounded-lg bg-white dark:bg-slate-900 shadow-sm transition-transform active:scale-90", textClass)}>
                <Icon className="w-3.5 h-3.5" />
              </div>
              <div className="flex flex-col">
                <span className={cn("text-[9px] font-black uppercase tracking-widest leading-none mb-1 opacity-70", textClass)}>
                  {label}
                </span>
                <code className="text-[13px] font-black font-mono tracking-tight text-slate-700 dark:text-slate-300 truncate">
                  {keyName}
                </code>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 hover:bg-slate-200/50 dark:hover:bg-slate-800/50 transition-colors"
                onClick={handleCopy}
              >
                {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5 text-slate-400" />}
              </Button>
              <div className="w-px h-4 bg-slate-200 dark:bg-slate-800 mx-1" />
              <ChevronDown className="w-4 h-4 text-slate-400 group-data-[state=open]:rotate-180 transition-transform duration-300" />
            </div>
          </div>
        </CollapsibleTrigger>

        <CollapsibleContent className="bg-white/50 dark:bg-black/20 border-t-2 border-slate-100 dark:border-slate-800">
          <div className="p-5 space-y-4">
            {type === 'modified' ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <div className="flex items-center gap-1.5 px-1">
                    <div className="w-1.5 h-1.5 rounded-full bg-rose-500" />
                    <span className="text-[10px] font-black text-rose-500 uppercase tracking-widest">Previous State</span>
                  </div>
                  <JsonViewer src={oldValue} theme="light" className="border-rose-100 bg-rose-50/10 min-h-[100px]" />
                </div>
                <div className="space-y-2">
                  <div className="flex items-center gap-1.5 px-1">
                    <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                    <span className="text-[10px] font-black text-emerald-500 uppercase tracking-widest">New State</span>
                  </div>
                  <JsonViewer src={newValue} theme="dark" className="border-emerald-100 bg-emerald-50/10 min-h-[100px]" />
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="flex items-center gap-1.5 px-1">
                  <div className={cn("w-1.5 h-1.5 rounded-full", textClass)} />
                  <span className={cn("text-[10px] font-black uppercase tracking-widest", textClass)}>Payload Value</span>
                </div>
                <JsonViewer src={value} className={cn("border-opacity-30", borderClass)} />
              </div>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </motion.div>
  );
};

/**
 * 엔트리 리스트를 렌더링하는 헬퍼 컴포넌트
 */
const DiffList = ({ entries, type }: { entries: [string, any][], type: 'added' | 'removed' | 'modified' }) => (
  <div className="space-y-1">
    <AnimatePresence mode="popLayout">
      {entries.length > 0 ? (
        entries.map(([key, value]) => (
          <DiffRow
            key={`${type}-${key}`}
            keyName={key}
            type={type}
            value={type === 'modified' ? undefined : value}
            oldValue={type === 'modified' ? value.old : undefined}
            newValue={type === 'modified' ? value.new : undefined}
          />
        ))
      ) : (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-col items-center justify-center py-20 text-slate-300"
        >
          <Search className="w-10 h-10 opacity-20 mb-3" />
          <p className="text-xs font-bold uppercase tracking-widest opacity-40">No entries in this Category</p>
        </motion.div>
      )}
    </AnimatePresence>
  </div>
);

// --- MAIN COMPONENT ---

export const StateDiffViewer: React.FC<StateDiffViewerProps> = ({
  diff,
  compareResult,
  loading = false,
  sourceLabel = 'Previous',
  targetLabel = 'Current',
}) => {
  const activeDiff = diff || (compareResult as StateDiff);

  const categorized = useMemo(() => ({
    added: Object.entries(activeDiff?.added || {}),
    removed: Object.entries(activeDiff?.removed || {}),
    modified: Object.entries(activeDiff?.modified || {}),
  }), [activeDiff]);

  const totalChanges = categorized.added.length + categorized.removed.length + categorized.modified.length;

  if (loading) {
    return (
      <Card className="rounded-3xl border-slate-100 shadow-xl overflow-hidden">
        <CardHeader className="bg-slate-50/50 border-b border-slate-100">
          <Skeleton className="h-6 w-48 mb-2" />
          <Skeleton className="h-4 w-64" />
        </CardHeader>
        <CardContent className="p-6">
          <div className="space-y-4">
            <Skeleton className="h-14 w-full rounded-2xl" />
            <Skeleton className="h-14 w-full rounded-2xl" />
            <Skeleton className="h-14 w-full rounded-2xl" />
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!activeDiff || totalChanges === 0) {
    return (
      <Card className="rounded-3xl border-slate-100 shadow-xl overflow-hidden py-12">
        <CardContent className="flex flex-col items-center justify-center text-center p-8">
          <div className="w-16 h-16 rounded-full bg-slate-50 flex items-center justify-center mb-6">
            <GitCompare className="w-8 h-8 text-slate-200" />
          </div>
          <h3 className="text-lg font-black tracking-tight text-slate-400">Neutral State Delta</h3>
          <p className="text-sm font-medium text-slate-300 mt-2 max-w-[240px]">
            두 시점 사이에 저장된 데이터의 물리적 변화가 감지되지 않았습니다.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="rounded-3xl border-slate-100 shadow-2xl overflow-hidden">
      <CardHeader className="bg-slate-50/50 border-b border-slate-100 px-8 py-6">
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <CardTitle className="text-lg font-black tracking-tight flex items-center gap-3">
              <div className="p-2 bg-primary text-white rounded-xl shadow-lg shadow-primary/20">
                <GitCompare className="w-4 h-4" />
              </div>
              Temporal State Audit
            </CardTitle>
            {compareResult && (
              <CardDescription className="font-bold text-slate-400 flex items-center gap-1.5 uppercase text-[10px] tracking-widest">
                <span>{compareResult.source_node_name}</span>
                <ArrowLeftRight className="w-2.5 h-2.5 opacity-40 mx-1" />
                <span className="text-primary">{compareResult.target_node_name}</span>
              </CardDescription>
            )}
          </div>
          <div className="flex items-center gap-2">
            {categorized.added.length > 0 && (
              <Badge className="bg-emerald-50 text-emerald-700 border-emerald-100 font-bold px-2 rounded-lg">
                +{categorized.added.length}
              </Badge>
            )}
            {categorized.removed.length > 0 && (
              <Badge className="bg-rose-50 text-rose-700 border-rose-100 font-bold px-2 rounded-lg">
                -{categorized.removed.length}
              </Badge>
            )}
            {categorized.modified.length > 0 && (
              <Badge className="bg-amber-50 text-amber-700 border-amber-100 font-bold px-2 rounded-lg">
                ~{categorized.modified.length}
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-8">
        <Tabs defaultValue="all" className="w-full">
          <TabsList className="grid w-full grid-cols-4 h-12 bg-slate-100/50 p-1.5 rounded-2xl mb-8">
            <TabsTrigger value="all" className="rounded-xl font-black text-[11px] uppercase tracking-widest data-[state=active]:bg-white data-[state=active]:shadow-lg">
              All ({totalChanges})
            </TabsTrigger>
            <TabsTrigger value="added" className="rounded-xl font-black text-[11px] uppercase tracking-widest data-[state=active]:bg-white data-[state=active]:text-emerald-600">
              Added ({categorized.added.length})
            </TabsTrigger>
            <TabsTrigger value="removed" className="rounded-xl font-black text-[11px] uppercase tracking-widest data-[state=active]:bg-white data-[state=active]:text-rose-600">
              Removed ({categorized.removed.length})
            </TabsTrigger>
            <TabsTrigger value="modified" className="rounded-xl font-black text-[11px] uppercase tracking-widest data-[state=active]:bg-white data-[state=active]:text-amber-600">
              Diff ({categorized.modified.length})
            </TabsTrigger>
          </TabsList>

          <ScrollArea className="max-h-[500px] pr-5 -mr-5">
            <TabsContent value="all" className="mt-0 focus-visible:outline-none">
              <DiffList entries={categorized.added} type="added" />
              <DiffList entries={categorized.removed} type="removed" />
              <DiffList entries={categorized.modified} type="modified" />
            </TabsContent>

            <TabsContent value="added" className="mt-0 focus-visible:outline-none">
              <DiffList entries={categorized.added} type="added" />
            </TabsContent>

            <TabsContent value="removed" className="mt-0 focus-visible:outline-none">
              <DiffList entries={categorized.removed} type="removed" />
            </TabsContent>

            <TabsContent value="modified" className="mt-0 focus-visible:outline-none">
              <DiffList entries={categorized.modified} type="modified" />
            </TabsContent>
          </ScrollArea>
        </Tabs>

        <div className="flex items-center gap-2 mt-8 p-4 rounded-2xl bg-slate-50 border border-slate-100 dark:bg-slate-900 dark:border-slate-800">
          <Database className="w-4 h-4 text-slate-400" />
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">
            System state comparison is performed using deep recursive diffing logic.
          </p>
          <div className="flex-1" />
          <Zap className="w-3 h-3 text-primary animate-pulse" />
        </div>
      </CardContent>
    </Card>
  );
};

export default StateDiffViewer;
