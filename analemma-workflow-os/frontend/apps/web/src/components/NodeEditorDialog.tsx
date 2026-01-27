import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Slider } from '@/components/ui/slider';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useState, useMemo, useCallback } from 'react';
import { ArrowRight, ArrowLeft, Trash2, Plug, Settings2, Info } from 'lucide-react';
import { BLOCK_CATEGORIES } from './BlockLibrary';
import { cn } from '@/lib/utils';
import { motion, AnimatePresence } from 'framer-motion';

// --- TYPES & CONSTANTS ---

interface NodeData {
  label: string;
  prompt_content?: string;
  temperature?: number;
  model?: string;
  max_tokens?: number;
  operatorType?: string;
  operatorVariant?: string;
  triggerType?: string;
  triggerHour?: number;
  triggerMinute?: number;
  controlType?: string;
  whileCondition?: string;
  maxIterations?: number;
  [key: string]: any;
}

interface NodeEditorDialogProps {
  node: { id: string; type?: string; data: NodeData } | null;
  open: boolean;
  onClose: () => void;
  onSave: (nodeId: string, updates: Partial<NodeData>) => void;
  onDelete?: (nodeId: string) => void;
  incomingConnections?: { id: string; sourceLabel: string }[];
  outgoingConnections?: { id: string; targetLabel: string }[];
  availableTargets?: { id: string; label: string }[];
  onEdgeDelete?: (edgeId: string) => void;
  onEdgeCreate?: (source: string, target: string) => void;
}

const AI_MODELS = BLOCK_CATEGORIES.find(cat => cat.type === 'aiModel')?.items || [];
const OPERATORS = BLOCK_CATEGORIES.find(cat => cat.type === 'operator')?.items || [];
const TRIGGERS = BLOCK_CATEGORIES.find(cat => cat.type === 'trigger')?.items || [];
const CONTROLS = BLOCK_CATEGORIES.find(cat => cat.type === 'control')?.items || [];

// --- SUB-COMPONENTS ---

/**
 * AI 모델 설정 컴포넌트
 */
const AIModelSettings = ({ data, onChange }: { data: NodeData, onChange: (key: string, val: any) => void }) => (
  <motion.div
    initial={{ opacity: 0, y: 10 }}
    animate={{ opacity: 1, y: 0 }}
    className="space-y-4 border border-slate-700 rounded-2xl p-5 bg-slate-900/50"
  >
    <div className="space-y-2">
      <Label className="text-[11px] font-bold uppercase tracking-wider text-slate-400 pl-1">Engine Identity</Label>
      <Select value={data.model} onValueChange={(val) => onChange('model', val)}>
        <SelectTrigger className="h-10 bg-slate-800 border-slate-700 text-slate-100"><SelectValue /></SelectTrigger>
        <SelectContent>
          {AI_MODELS.map(m => <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
    <div className="space-y-2">
      <Label className="text-[11px] font-bold uppercase tracking-wider text-slate-400 pl-1">Core Instruction (System Prompt)</Label>
      <Textarea
        value={data.prompt_content}
        onChange={(e) => onChange('prompt_content', e.target.value)}
        placeholder="에이전트의 성격과 임무를 정의하세요..."
        className="min-h-[120px] font-mono text-xs bg-slate-800 border-slate-700 text-slate-100 leading-relaxed shadow-inner placeholder:text-slate-500"
      />
    </div>
    <div className="space-y-3 pt-2">
      <div className="flex justify-between items-center px-1">
        <Label className="text-[11px] font-bold uppercase tracking-wider text-slate-400">Creativity (Temp)</Label>
        <span className="text-xs font-mono font-bold text-primary bg-primary/10 px-2 py-0.5 rounded-full">{data.temperature?.toFixed(1)}</span>
      </div>
      <Slider
        value={[data.temperature ?? 0.7]}
        onValueChange={(val) => onChange('temperature', val[0])}
        min={0} max={2} step={0.1}
        className="py-2"
      />
    </div>
    <div className="space-y-2">
      <Label className="text-[11px] font-bold uppercase tracking-wider text-slate-400 pl-1">Token Limit</Label>
      <Input
        type="number"
        value={data.max_tokens}
        onChange={(e) => onChange('max_tokens', parseInt(e.target.value))}
        className="bg-slate-800 border-slate-700 text-slate-100"
      />
    </div>
  </motion.div>
);

/**
 * 연산 및 액션 설정 컴포넌트
 */
const OperatorSettings = ({ data, onChange }: { data: NodeData, onChange: (key: string, val: any) => void }) => (
  <motion.div
    initial={{ opacity: 0, y: 10 }}
    animate={{ opacity: 1, y: 0 }}
    className="space-y-4 border border-slate-700 rounded-2xl p-5 bg-slate-900/50"
  >
    <div className="space-y-2">
      <Label className="text-[11px] font-bold uppercase tracking-wider text-slate-400 pl-1">Operator Blueprint</Label>
      <Select value={data.operatorType} onValueChange={(val) => onChange('operatorType', val)}>
        <SelectTrigger className="h-10 bg-slate-800 border-slate-700 text-slate-100"><SelectValue /></SelectTrigger>
        <SelectContent>
          {OPERATORS.map(op => <SelectItem key={op.id} value={op.id}>{op.label}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
    <div className="space-y-2">
      <Label className="text-[11px] font-bold uppercase tracking-wider text-slate-400 pl-1">Execution Mode</Label>
      <Select value={data.operatorVariant} onValueChange={(val) => onChange('operatorVariant', val)}>
        <SelectTrigger className="h-10 bg-slate-800 border-slate-700 text-slate-100"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="official">Standard (Validated)</SelectItem>
          <SelectItem value="custom">Edge (Experimental)</SelectItem>
        </SelectContent>
      </Select>
      <div className="flex gap-2 p-3 bg-blue-50/50 border border-blue-100 rounded-xl mt-2">
        <Info className="w-4 h-4 text-blue-400 shrink-0" />
        <p className="text-[10px] text-blue-600/80 leading-snug">
          Standard 모드는 Gmail/Notion 등 검증된 API 연동을 제공하며, Edge 모드는 커스텀 훅이나 스크립트 실행에 최적화되어 있습니다.
        </p>
      </div>
    </div>
  </motion.div>
);

/**
 * 트리거 및 이벤트 설정 컴포넌트
 */
const TriggerSettings = ({ data, onChange }: { data: NodeData, onChange: (key: string, val: any) => void }) => (
  <motion.div
    initial={{ opacity: 0, y: 10 }}
    animate={{ opacity: 1, y: 0 }}
    className="space-y-4 border border-slate-700 rounded-2xl p-5 bg-slate-900/50"
  >
    <div className="space-y-2">
      <Label className="text-[11px] font-bold uppercase tracking-wider text-slate-400 pl-1">Trigger Protocol</Label>
      <Select value={data.triggerType} onValueChange={(val) => onChange('triggerType', val)}>
        <SelectTrigger className="h-10 bg-slate-800 border-slate-700 text-slate-100"><SelectValue /></SelectTrigger>
        <SelectContent>
          {TRIGGERS.map(t => <SelectItem key={t.id} value={t.id}>{t.label}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
    <AnimatePresence>
      {data.triggerType === 'time' && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: 'auto' }}
          exit={{ opacity: 0, height: 0 }}
          className="grid grid-cols-2 gap-4 pt-2"
        >
          <div className="space-y-2">
            <Label className="text-[10px] text-slate-400 pl-1 font-bold">Hour (24h)</Label>
            <Input type="number" min={0} max={23} value={data.triggerHour} onChange={(e) => onChange('triggerHour', parseInt(e.target.value))} className="bg-white" />
          </div>
          <div className="space-y-2">
            <Label className="text-[10px] text-slate-400 pl-1 font-bold">Minute (mm)</Label>
            <Input type="number" min={0} max={59} value={data.triggerMinute} onChange={(e) => onChange('triggerMinute', parseInt(e.target.value))} className="bg-white" />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  </motion.div>
);

/**
 * 로직 및 흐름 제어 설정 컴포넌트
 */
const ControlSettings = ({ data, onChange }: { data: NodeData, onChange: (key: string, val: any) => void }) => (
  <motion.div
    initial={{ opacity: 0, y: 10 }}
    animate={{ opacity: 1, y: 0 }}
    className="space-y-4 border border-slate-700 rounded-2xl p-5 bg-slate-900/50"
  >
    <div className="space-y-2">
      <Label className="text-[11px] font-bold uppercase tracking-wider text-slate-400 pl-1">Flow Control Behavior</Label>
      <Select value={data.controlType} onValueChange={(val) => onChange('controlType', val)}>
        <SelectTrigger className="h-10 bg-slate-800 border-slate-700 text-slate-100"><SelectValue /></SelectTrigger>
        <SelectContent>
          {CONTROLS.map(c => <SelectItem key={c.id} value={c.id}>{c.label}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
    {data.controlType === 'while' && (
      <div className="space-y-4 pt-2 animate-in slide-in-from-top-2">
        <div className="space-y-2">
          <Label className="text-[10px] text-slate-400 pl-1 font-bold italic">Stop Condition Path</Label>
          <Input value={data.whileCondition} onChange={(e) => onChange('whileCondition', e.target.value)} placeholder="e.g. data.status == 'ready'" className="bg-white" />
        </div>
        <div className="space-y-2">
          <Label className="text-[10px] text-slate-400 pl-1 font-bold">Max Iteration Guard</Label>
          <Input type="number" value={data.maxIterations} onChange={(e) => onChange('maxIterations', parseInt(e.target.value))} className="bg-white" />
        </div>
      </div>
    )}
  </motion.div>
);

/**
 * 연결 관리 컴포넌트
 */
const ConnectionManager = ({
  incoming,
  outgoing,
  available,
  onDelete,
  onCreate,
  nodeId
}: any) => {
  const [selectedTarget, setSelectedTarget] = useState('');

  return (
    <div className="space-y-6 pt-4">
      <div className="flex items-center gap-3 border-b pb-3">
        <div className="p-1.5 bg-slate-800 rounded-lg">
          <Plug className="w-4 h-4 text-slate-500" />
        </div>
        <h4 className="font-bold text-sm tracking-tight text-slate-300">Link Infrastructure</h4>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Incoming Section */}
        <div className="space-y-3">
          <div className="flex items-center gap-1.5">
            <ArrowLeft className="w-3 h-3 text-slate-300" />
            <Label className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Incoming</Label>
          </div>
          {incoming.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {incoming.map((conn: any) => (
                <Badge key={conn.id} variant="secondary" className="pl-2 pr-1 h-6 bg-slate-800 border-slate-700 text-slate-300 group/badge">
                  <span className="truncate max-w-[80px] text-[10px] font-medium">{conn.sourceLabel}</span>
                  <Button
                    variant="ghost"
                    className="h-4 w-4 p-0 ml-1 hover:text-destructive transition-colors opacity-0 group-hover/badge:opacity-100"
                    onClick={() => onDelete?.(conn.id)}
                  >
                    <X className="w-2.5 h-2.5" />
                  </Button>
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-[10px] text-slate-300 italic pl-1">No dependency</p>
          )}
        </div>

        {/* Outgoing Section */}
        <div className="space-y-3">
          <div className="flex items-center gap-1.5">
            <ArrowRight className="w-3 h-3 text-slate-300" />
            <Label className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Outgoing</Label>
          </div>
          {outgoing.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {outgoing.map((conn: any) => (
                <Badge key={conn.id} variant="secondary" className="pl-2 pr-1 h-6 bg-slate-800 border-slate-700 text-slate-300 group/badge">
                  <span className="truncate max-w-[80px] text-[10px] font-medium">{conn.targetLabel}</span>
                  <Button
                    variant="ghost"
                    className="h-4 w-4 p-0 ml-1 hover:text-destructive transition-colors opacity-0 group-hover/badge:opacity-100"
                    onClick={() => onDelete?.(conn.id)}
                  >
                    <X className="w-2.5 h-2.5" />
                  </Button>
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-[10px] text-slate-300 italic pl-1">Endpoint terminal</p>
          )}
        </div>
      </div>

      {/* Create New Link */}
      {onCreate && available.length > 0 && (
        <div className="flex gap-2 p-3 bg-slate-800/50 rounded-2xl border border-slate-700">
          <Select value={selectedTarget} onValueChange={setSelectedTarget}>
            <SelectTrigger className="h-9 text-xs bg-slate-800 border-slate-700 text-slate-100">
              <SelectValue placeholder="Propagate to..." />
            </SelectTrigger>
            <SelectContent>
              {available.map((t: any) => (
                <SelectItem key={t.id} value={t.id} disabled={t.id === nodeId}>
                  {t.label}
                  {t.id === nodeId && <span className="text-[10px] ml-2 text-red-400 opacity-50">(Self)</span>}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            className="h-9 px-4 font-bold active:scale-95 transition-transform"
            disabled={!selectedTarget}
            onClick={() => {
              onCreate(nodeId, selectedTarget);
              setSelectedTarget('');
            }}
          >
            Connect
          </Button>
        </div>
      )}
    </div>
  );
};

// --- MAIN COMPONENT ---

export const NodeEditorDialog = ({
  node,
  open,
  onClose,
  onSave,
  onDelete,
  incomingConnections = [],
  outgoingConnections = [],
  availableTargets = [],
  onEdgeDelete,
  onEdgeCreate
}: NodeEditorDialogProps) => {
  const nodeType = node?.type || '';

  // 폼 상태 데이터 (Any 제거 후 정규화된 키 사용)
  const [formData, setFormData] = useState<NodeData>(() => ({
    label: node?.data.label || '',
    prompt_content: node?.data.prompt_content || node?.data.prompt || '',
    temperature: node?.data.temperature ?? 0.7,
    model: node?.data.model || (nodeType === 'aiModel' ? 'gpt-4' : ''),
    max_tokens: node?.data.max_tokens || node?.data.maxTokens || 2000,
    operatorType: node?.data.operatorType || (nodeType === 'operator' ? 'email' : ''),
    operatorVariant: node?.data.operatorVariant || 'official',
    triggerType: node?.data.triggerType || (nodeType === 'trigger' ? (node?.data.blockId as string || 'request') : ''),
    triggerHour: node?.data.triggerHour ?? 9,
    triggerMinute: node?.data.triggerMinute ?? 0,
    controlType: node?.data.controlType || (nodeType === 'control' ? 'while' : ''),
    whileCondition: node?.data.whileCondition || '',
    maxIterations: node?.data.max_iterations || node?.data.maxIterations || 10,
  }));

  const updateField = useCallback((key: string, value: any) => {
    setFormData(prev => ({ ...prev, [key]: value }));
  }, []);

  const handleSave = () => {
    if (!node) return;

    // Payload extraction logic based on type
    const updates: Partial<NodeData> = { label: formData.label };

    if (nodeType === 'aiModel') {
      const { prompt_content, temperature, model, max_tokens } = formData;
      Object.assign(updates, { prompt_content, temperature, model, max_tokens: Number(max_tokens) });
    } else if (nodeType === 'operator') {
      const { operatorType, operatorVariant } = formData;
      Object.assign(updates, { operatorType, operatorVariant });
    } else if (nodeType === 'trigger') {
      const { triggerType, triggerHour, triggerMinute } = formData;
      Object.assign(updates, { triggerType, triggerHour: Number(triggerHour || 0), triggerMinute: Number(triggerMinute || 0) });
    } else if (nodeType === 'control') {
      const { controlType, whileCondition, maxIterations } = formData;
      Object.assign(updates, { controlType, whileCondition, maxIterations: Number(maxIterations || 0) });
    }

    onSave(node.id, updates);
    onClose();
  };

  if (!node) return null;

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-[520px] max-h-[90vh] flex flex-col p-0 gap-0 overflow-hidden bg-slate-950 border-slate-800 shadow-2xl rounded-3xl">
        <div className="absolute top-0 left-0 w-full h-1.5 bg-gradient-to-r from-primary/80 to-indigo-500/80" />

        <DialogHeader className="p-7 pb-4">
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <DialogTitle className="flex items-center gap-3 text-xl font-bold tracking-tight text-slate-100">
                Block Configuration
                <Badge variant="outline" className="h-5 text-[9px] font-bold uppercase tracking-widest border-slate-700 bg-slate-800 text-slate-400">
                  {nodeType}
                </Badge>
              </DialogTitle>
              <DialogDescription className="text-slate-400">
                ID: <span className="font-mono text-[10px] font-bold">{node.id}</span> • Specify block behavior and connectivity.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <ScrollArea className="flex-1 max-h-[70vh] custom-scrollbar">
          <div className="px-7 py-2 space-y-8">
            {/* Common Fields */}
            <div className="space-y-2.5">
              <Label htmlFor="label" className="text-[11px] font-bold uppercase tracking-wider text-slate-400 pl-1">Block Alias (Display Name)</Label>
              <Input
                id="label"
                value={formData.label}
                onChange={(e) => updateField('label', e.target.value)}
                placeholder="e.g. Master Intelligence Unit"
                className="h-11 bg-slate-800 border-slate-700 text-slate-100 font-medium placeholder:text-slate-500"
              />
            </div>

            <div className="relative">
              <div className="absolute -left-7 top-0 bottom-0 w-1 bg-primary/20 rounded-r-full" />
              <div className="space-y-2 mb-4 flex items-center gap-2">
                <Settings2 className="w-4 h-4 text-primary" />
                <h3 className="text-xs font-bold text-slate-300">Internal Logic Settings</h3>
              </div>

              {/* Switch settings UI based on type */}
              {nodeType === 'aiModel' && <AIModelSettings data={formData} onChange={updateField} />}
              {nodeType === 'operator' && <OperatorSettings data={formData} onChange={updateField} />}
              {nodeType === 'trigger' && <TriggerSettings data={formData} onChange={updateField} />}
              {nodeType === 'control' && <ControlSettings data={formData} onChange={updateField} />}
            </div>

            {/* Connection Infrastructure */}
            <ConnectionManager
              incoming={incomingConnections}
              outgoing={outgoingConnections}
              available={availableTargets}
              onDelete={onEdgeDelete}
              onCreate={onEdgeCreate}
              nodeId={node.id}
            />
          </div>
          <div className="h-8" /> {/* Scroll margin */}
        </ScrollArea>

        <DialogFooter className="p-6 border-t border-slate-800 bg-slate-900/80 flex-row justify-between items-center sm:justify-between">
          <div className="flex items-center">
            {onDelete && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => { onDelete(node.id); onClose(); }}
                className="text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-xl h-10 px-4 font-bold text-xs gap-2 transition-all"
              >
                <Trash2 className="w-4 h-4" /> Discard Block
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="ghost" className="h-10 rounded-xl font-bold text-xs" onClick={onClose}>Dismiss</Button>
            <Button
              onClick={handleSave}
              className="h-10 px-8 rounded-xl font-bold text-xs bg-primary shadow-lg shadow-primary/20 active:scale-95 transition-all"
            >
              Commit Changes
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default NodeEditorDialog;

const X = ({ className, onClick }: { className?: string, onClick?: () => void }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    onClick={onClick}
  >
    <path d="M18 6 6 18" /><path d="m6 6 12 12" />
  </svg>
);
