import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { useState } from 'react';
import { ArrowRight, ArrowLeft, Trash2, Plug } from 'lucide-react';
import { BLOCK_CATEGORIES } from './BlockLibrary';

// 설정 객체들 (BLOCK_CATEGORIES에서 추출)
const AI_MODELS = BLOCK_CATEGORIES.find(cat => cat.type === 'aiModel')?.items || [];
const OPERATORS = BLOCK_CATEGORIES.find(cat => cat.type === 'operator')?.items || [];
const TRIGGERS = BLOCK_CATEGORIES.find(cat => cat.type === 'trigger')?.items || [];
const CONTROLS = BLOCK_CATEGORIES.find(cat => cat.type === 'control')?.items || [];

interface NodeEditorDialogProps {
  node: any | null;
  open: boolean;
  onClose: () => void;
  onSave: (nodeId: string, updates: any) => void;
  onDelete?: (nodeId: string) => void;
  incomingConnections?: { id: string; sourceLabel: string }[];
  outgoingConnections?: { id: string; targetLabel: string }[];
  availableTargets?: { id: string; label: string }[];
  onEdgeDelete?: (edgeId: string) => void;
  onEdgeCreate?: (source: string, target: string) => void;
}

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
  // node.type은 불변이므로 초기값으로 고정
  const nodeType = node?.type || '';

  // 폼 상태 초기화 (부모의 key prop 변경 시 재마운트되어 초기화됨)
  const [formData, setFormData] = useState(() => ({
    label: node?.data.label || '',
    // AI Model fields
    prompt_content: node?.data.prompt_content || node?.data.prompt || '',
    temperature: node?.data.temperature ?? 0.7,
    model: node?.data.model || 'gpt-4',
    max_tokens: node?.data.max_tokens || node?.data.maxTokens || 2000,
    // Operator fields
    operatorType: node?.data.operatorType || 'email',
    operatorVariant: node?.data.operatorVariant || 'official',
    // Trigger fields
    triggerType: node?.data.triggerType || (node?.data.blockId as 'time' | 'request' | 'event') || 'request',
    triggerHour: node?.data.triggerHour ?? 9,
    triggerMinute: node?.data.triggerMinute ?? 0,
    // Control fields
    controlType: node?.data.controlType || 'while',
    whileCondition: node?.data.whileCondition || '',
    maxIterations: node?.data.max_iterations || node?.data.maxIterations || 10,
  }));

  const [selectedTargetNode, setSelectedTargetNode] = useState<string>('');

  const updateField = (key: string, value: any) => {
    setFormData(prev => ({ ...prev, [key]: value }));
  };

  const handleSave = () => {
    if (!node) return;

    const updates: any = { label: formData.label };

    // 현재 nodeType에 필요한 데이터만 선별적으로 저장
    switch (nodeType) {
      case 'aiModel':
        updates.prompt_content = formData.prompt_content;
        updates.temperature = formData.temperature;
        updates.model = formData.model;
        updates.max_tokens = Number(formData.max_tokens);
        break;
      case 'operator':
        updates.operatorType = formData.operatorType;
        updates.operatorVariant = formData.operatorVariant;
        break;
      case 'trigger':
        updates.triggerType = formData.triggerType;
        if (formData.triggerType === 'time') {
          updates.triggerHour = Number(formData.triggerHour);
          updates.triggerMinute = Number(formData.triggerMinute);
        }
        break;
      case 'control':
        updates.controlType = formData.controlType;
        if (formData.controlType === 'while') {
          updates.whileCondition = formData.whileCondition;
          updates.maxIterations = Number(formData.maxIterations);
        }
        break;
    }

    onSave(node.id, updates);
    onClose();
  };

  if (!node) return null;

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-[500px] max-h-[90vh] flex flex-col p-0 gap-0">
        <DialogHeader className="p-6 pb-4 border-b">
          <DialogTitle className="flex items-center gap-2">
            Edit Block
            <Badge variant="secondary" className="uppercase text-[10px] tracking-wider">{nodeType}</Badge>
          </DialogTitle>
          <DialogDescription>
            Configure properties and connections for this block.
          </DialogDescription>
        </DialogHeader>
        
        <ScrollArea className="flex-1 max-h-[60vh]">
          <div className="p-6 space-y-6">
            {/* Common Fields */}
            <div className="space-y-2">
              <Label htmlFor="label">Label</Label>
              <Input
                id="label"
                value={formData.label}
                onChange={(e) => updateField('label', e.target.value)}
                placeholder="Block Name"
              />
            </div>

            {/* AI Model Settings */}
            {nodeType === 'aiModel' && (
              <div className="space-y-4 border rounded-lg p-4 bg-muted/20">
                <div className="space-y-2">
                  <Label>Model</Label>
                  <Select value={formData.model} onValueChange={(val) => updateField('model', val)}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {AI_MODELS.map(m => <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>System Prompt</Label>
                  <Textarea
                    value={formData.prompt_content}
                    onChange={(e) => updateField('prompt_content', e.target.value)}
                    placeholder="You are a helpful assistant..."
                    className="min-h-[100px] font-mono text-xs"
                  />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between">
                    <Label>Temperature</Label>
                    <span className="text-xs text-muted-foreground">{formData.temperature.toFixed(1)}</span>
                  </div>
                  <Slider
                    value={[formData.temperature]}
                    onValueChange={(val) => updateField('temperature', val[0])}
                    min={0} max={2} step={0.1}
                  />
                </div>
                <div className="space-y-2">
                  <Label>Max Tokens</Label>
                  <Input
                    type="number"
                    value={formData.max_tokens}
                    onChange={(e) => updateField('max_tokens', e.target.value)}
                  />
                </div>
              </div>
            )}

            {/* Operator Settings */}
            {nodeType === 'operator' && (
              <div className="space-y-4 border rounded-lg p-4 bg-muted/20">
                <div className="space-y-2">
                  <Label>Action Type</Label>
                  <Select value={formData.operatorType} onValueChange={(val) => updateField('operatorType', val)}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {OPERATORS.map(op => <SelectItem key={op.id} value={op.id}>{op.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Operator Mode</Label>
                  <Select value={formData.operatorVariant} onValueChange={(val) => updateField('operatorVariant', val)}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="official">Official (템플릿/검증된 연동)</SelectItem>
                      <SelectItem value="custom">Custom (사용자 정의)</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    공식 모드는 향후 Gmail/GDrive 등 검증된 연동에 사용되며, 커스텀은 사용자 정의 코드/설정에 사용됩니다.
                  </p>
                </div>
              </div>
            )}

            {/* Trigger Settings */}
            {nodeType === 'trigger' && (
              <div className="space-y-4 border rounded-lg p-4 bg-muted/20">
                <div className="space-y-2">
                  <Label>Trigger Type</Label>
                  <Select value={formData.triggerType} onValueChange={(val) => updateField('triggerType', val)}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {TRIGGERS.map(t => <SelectItem key={t.id} value={t.id}>{t.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                {formData.triggerType === 'time' && (
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <Label>Hour (0-23)</Label>
                      <Input type="number" min={0} max={23} value={formData.triggerHour} onChange={(e) => updateField('triggerHour', e.target.value)} />
                    </div>
                    <div className="space-y-2">
                      <Label>Minute (0-59)</Label>
                      <Input type="number" min={0} max={59} value={formData.triggerMinute} onChange={(e) => updateField('triggerMinute', e.target.value)} />
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Control Settings */}
            {nodeType === 'control' && (
              <div className="space-y-4 border rounded-lg p-4 bg-muted/20">
                <div className="space-y-2">
                  <Label>Logic Type</Label>
                  <Select value={formData.controlType} onValueChange={(val) => updateField('controlType', val)}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {CONTROLS.map(c => <SelectItem key={c.id} value={c.id}>{c.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                {formData.controlType === 'while' && (
                  <>
                    <div className="space-y-2">
                      <Label>Loop Condition</Label>
                      <Input value={formData.whileCondition} onChange={(e) => updateField('whileCondition', e.target.value)} placeholder="e.g. data.status == 'pending'" />
                    </div>
                    <div className="space-y-2">
                      <Label>Max Iterations</Label>
                      <Input type="number" value={formData.maxIterations} onChange={(e) => updateField('maxIterations', e.target.value)} />
                    </div>
                  </>
                )}
              </div>
            )}

            {/* Connection Management */}
            <div className="space-y-4 pt-2">
              <div className="flex items-center gap-2 pb-2 border-b">
                <Plug className="w-4 h-4" />
                <h4 className="font-semibold text-sm">Connections</h4>
              </div>

              {/* Incoming */}
              <div className="space-y-2">
                <Label className="text-xs text-muted-foreground flex items-center gap-1">
                  <ArrowLeft className="w-3 h-3" /> Incoming
                </Label>
                {incomingConnections.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {incomingConnections.map((conn) => (
                      <Badge key={conn.id} variant="secondary" className="flex gap-1 items-center pr-1">
                        {conn.sourceLabel}
                        {onEdgeDelete && (
                          <Trash2 
                            className="w-3 h-3 cursor-pointer hover:text-destructive ml-1" 
                            onClick={() => onEdgeDelete(conn.id)}
                          />
                        )}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground italic">No incoming connections</p>
                )}
              </div>

              {/* Outgoing */}
              <div className="space-y-2">
                <Label className="text-xs text-muted-foreground flex items-center gap-1">
                  <ArrowRight className="w-3 h-3" /> Outgoing
                </Label>
                {outgoingConnections.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {outgoingConnections.map((conn) => (
                      <Badge key={conn.id} variant="secondary" className="flex gap-1 items-center pr-1">
                        {conn.targetLabel}
                        {onEdgeDelete && (
                          <Trash2 
                            className="w-3 h-3 cursor-pointer hover:text-destructive ml-1" 
                            onClick={() => onEdgeDelete(conn.id)}
                          />
                        )}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground italic">No outgoing connections</p>
                )}
              </div>

              {/* Add Connection */}
              {onEdgeCreate && availableTargets.length > 0 && (
                <div className="flex gap-2 pt-2">
                  <Select value={selectedTargetNode} onValueChange={setSelectedTargetNode}>
                    <SelectTrigger className="h-8 text-xs">
                      <SelectValue placeholder="Link to..." />
                    </SelectTrigger>
                    <SelectContent>
                      {availableTargets.map((t) => (
                        <SelectItem key={t.id} value={t.id}>{t.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button 
                    size="sm" 
                    className="h-8"
                    disabled={!selectedTargetNode}
                    onClick={() => {
                      if (selectedTargetNode && node) {
                        onEdgeCreate(node.id, selectedTargetNode);
                        setSelectedTargetNode('');
                      }
                    }}
                  >
                    Connect
                  </Button>
                </div>
              )}
            </div>
          </div>
        </ScrollArea>

        <div className="p-4 border-t bg-muted/10 flex justify-between items-center">
          {onDelete && (
            <Button variant="ghost" size="sm" onClick={() => { onDelete(node.id); onClose(); }} className="text-destructive hover:text-destructive hover:bg-destructive/10">
              <Trash2 className="w-4 h-4 mr-2" /> Delete
            </Button>
          )}
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button size="sm" onClick={handleSave}>Save Changes</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};
