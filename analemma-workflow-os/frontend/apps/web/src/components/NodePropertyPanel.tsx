import { useWorkflowStore } from '@/lib/workflowStore';
import { useShallow } from 'zustand/react/shallow';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Slider } from '@/components/ui/slider';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useState } from 'react';
import { ArrowRight, ArrowLeft, Trash2, Plug, Settings } from 'lucide-react';
import { BLOCK_CATEGORIES } from './BlockLibrary';
import { toast } from 'sonner';

const AI_MODELS = BLOCK_CATEGORIES.find(cat => cat.type === 'aiModel')?.items || [];
const OPERATORS = BLOCK_CATEGORIES.find(cat => cat.type === 'operator')?.items || [];
const TRIGGERS = BLOCK_CATEGORIES.find(cat => cat.type === 'trigger')?.items || [];
const CONTROLS = BLOCK_CATEGORIES.find(cat => cat.type === 'control')?.items || [];

export const NodePropertyPanel = () => {
  const { selectedNodeId, nodes, edges, updateNode, removeNode, addEdge, removeEdge } = useWorkflowStore(
    useShallow((state) => ({
      selectedNodeId: state.selectedNodeId,
      nodes: state.nodes,
      edges: state.edges,
      updateNode: state.updateNode,
      removeNode: state.removeNode,
      addEdge: state.addEdge,
      removeEdge: state.removeEdge,
    }))
  );

  const selectedNode = nodes.find((n) => n.id === selectedNodeId);

  if (!selectedNode) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground p-4 text-center">
        <Settings className="w-12 h-12 mb-4 opacity-20" />
        <p>캔버스에서 노드를 선택하여<br/>속성을 편집하세요.</p>
      </div>
    );
  }

  // Calculate connections
  const incomingConnections = edges
    .filter((e) => e.target === selectedNode.id)
    .map((e) => {
      const sourceNode = nodes.find((n) => n.id === e.source);
      return { id: e.id, sourceLabel: sourceNode?.data.label || e.source };
    });

  const outgoingConnections = edges
    .filter((e) => e.source === selectedNode.id)
    .map((e) => {
      const targetNode = nodes.find((n) => n.id === e.target);
      return { id: e.id, targetLabel: targetNode?.data.label || e.target };
    });

  const availableTargets = nodes
    .filter((n) => n.id !== selectedNode.id) // Prevent self-loop (simple check)
    .map((n) => ({ id: n.id, label: n.data.label || n.id }));

  return (
      <div className="h-full flex flex-col bg-background">
          <div className="p-4 border-b border-border flex items-center justify-between">
              <h2 className="font-semibold text-lg flex items-center gap-2">
                  <Settings className="w-4 h-4" />
                  속성 편집
              </h2>
              <Badge variant="outline">{selectedNode.type}</Badge>
          </div>
          <ScrollArea className="flex-1">
              <NodeForm 
                key={selectedNode.id} // Force remount on node change
                node={selectedNode} 
                onUpdate={(updates: any) => updateNode(selectedNode.id, updates)}
                onDelete={() => removeNode(selectedNode.id)}
                incomingConnections={incomingConnections}
                outgoingConnections={outgoingConnections}
                availableTargets={availableTargets}
                onEdgeDelete={removeEdge}
                onEdgeCreate={(source: string, target: string) => addEdge({ id: `e${source}-${target}`, source, target, type: 'smart' })}
              />
          </ScrollArea>
      </div>
  );
}

const NodeForm = ({ 
  node, 
  onUpdate, 
  onDelete,
  incomingConnections,
  outgoingConnections,
  availableTargets,
  onEdgeDelete,
  onEdgeCreate
}: any) => {
  const nodeType = node?.type || '';
  const [formData, setFormData] = useState(() => ({
    label: node?.data.label || '',
    prompt_content: node?.data.prompt_content || node?.data.prompt || '',
    temperature: node?.data.temperature ?? 0.7,
    model: node?.data.model || 'gpt-4',
    max_tokens: node?.data.max_tokens || node?.data.maxTokens || 2000,
    operatorType: node?.data.operatorType || 'email',
    operatorVariant: node?.data.operatorVariant || 'official',
    triggerType: node?.data.triggerType || (node?.data.blockId as 'time' | 'request' | 'event') || 'request',
    triggerHour: node?.data.triggerHour ?? 9,
    triggerMinute: node?.data.triggerMinute ?? 0,
    controlType: node?.data.controlType || 'while',
    whileCondition: node?.data.whileCondition || '',
    maxIterations: node?.data.max_iterations || node?.data.maxIterations || 10,
    // for_each 필드
    items_path: node?.data.items_path || 'state.items',
    item_key: node?.data.item_key || 'item',
    output_key: node?.data.output_key || 'results',
    // parallel 필드
    branches: node?.data.branches || [],
    // human(HITL) 필드
    approval_message: node?.data.approval_message || '',
  }));

  const [selectedTargetNode, setSelectedTargetNode] = useState<string>('');

  const updateField = (key: string, value: any) => {
    setFormData(prev => ({ ...prev, [key]: value }));
  };

  const handleSave = () => {
    const updates: any = { label: formData.label };
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
        } else if (formData.controlType === 'for_each') {
          updates.items_path = formData.items_path;
          updates.item_key = formData.item_key;
          updates.output_key = formData.output_key;
          updates.max_iterations = Number(formData.maxIterations);
        } else if (formData.controlType === 'parallel') {
          updates.branches = formData.branches;
        } else if (formData.controlType === 'human') {
          updates.approval_message = formData.approval_message;
        }
        break;
    }
    onUpdate(updates);
    toast.success("변경사항이 저장되었습니다.");
  };

  return (
      <div className="p-4 space-y-6">
        {/* Label */}
        <div className="space-y-2">
            <Label htmlFor="label">Label</Label>
            <Input
            id="label"
            value={formData.label}
            onChange={(e) => updateField('label', e.target.value)}
            placeholder="Block Name"
            />
        </div>

        {/* Type Specific Fields */}
        {nodeType === 'aiModel' && (
            <div className="space-y-4 border rounded-lg p-4 bg-muted/20">
            <div className="space-y-2">
                <Label>Model</Label>
                <Select value={formData.model} onValueChange={(val) => updateField('model', val)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                    {AI_MODELS.map((m: any) => <SelectItem key={m.id} value={m.id}>{m.label}</SelectItem>)}
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

        {nodeType === 'operator' && (
            <div className="space-y-4 border rounded-lg p-4 bg-muted/20">
            <div className="space-y-2">
                <Label>Action Type</Label>
                <Select value={formData.operatorType} onValueChange={(val) => updateField('operatorType', val)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                    {OPERATORS.map((op: any) => <SelectItem key={op.id} value={op.id}>{op.label}</SelectItem>)}
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
            </div>
            </div>
        )}

        {nodeType === 'trigger' && (
            <div className="space-y-4 border rounded-lg p-4 bg-muted/20">
            <div className="space-y-2">
                <Label>Trigger Type</Label>
                <Select value={formData.triggerType} onValueChange={(val) => updateField('triggerType', val)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                    {TRIGGERS.map((t: any) => <SelectItem key={t.id} value={t.id}>{t.label}</SelectItem>)}
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

        {nodeType === 'control' && (
            <div className="space-y-4 border rounded-lg p-4 bg-muted/20">
            <div className="space-y-2">
                <Label>Logic Type</Label>
                <Select value={formData.controlType} onValueChange={(val) => updateField('controlType', val)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                    {CONTROLS.map((c: any) => <SelectItem key={c.id} value={c.id}>{c.label}</SelectItem>)}
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
            {formData.controlType === 'for_each' && (
                <>
                <div className="space-y-2">
                    <Label>Items Path</Label>
                    <Input 
                      value={formData.items_path} 
                      onChange={(e) => updateField('items_path', e.target.value)} 
                      placeholder="e.g. state.items or $.data.list" 
                    />
                    <p className="text-xs text-muted-foreground">반복할 배열의 경로 (JSONPath 또는 점 표기법)</p>
                </div>
                <div className="space-y-2">
                    <Label>Item Key</Label>
                    <Input 
                      value={formData.item_key} 
                      onChange={(e) => updateField('item_key', e.target.value)} 
                      placeholder="item" 
                    />
                    <p className="text-xs text-muted-foreground">각 반복에서 현재 아이템을 참조할 변수명</p>
                </div>
                <div className="space-y-2">
                    <Label>Output Key</Label>
                    <Input 
                      value={formData.output_key} 
                      onChange={(e) => updateField('output_key', e.target.value)} 
                      placeholder="results" 
                    />
                    <p className="text-xs text-muted-foreground">결과가 저장될 배열 변수명</p>
                </div>
                <div className="space-y-2">
                    <Label>Max Iterations</Label>
                    <Input type="number" value={formData.maxIterations} onChange={(e) => updateField('maxIterations', e.target.value)} />
                </div>
                </>
            )}
            {formData.controlType === 'parallel' && (
                <>
                <div className="space-y-2">
                    <Label>Parallel Branches</Label>
                    <p className="text-xs text-muted-foreground">
                      이 노드에서 나가는 여러 연결이 병렬로 실행됩니다.<br/>
                      캔버스에서 이 노드를 여러 대상에 연결하세요.
                    </p>
                </div>
                </>
            )}
            {formData.controlType === 'human' && (
                <>
                <div className="space-y-2">
                    <Label>Approval Message</Label>
                    <Textarea 
                      value={formData.approval_message} 
                      onChange={(e) => updateField('approval_message', e.target.value)} 
                      placeholder="예: 고가 거래가 감지되었습니다. 승인하시겠습니까?" 
                      className="min-h-[80px]"
                    />
                    <p className="text-xs text-muted-foreground">
                      워크플로우가 여기서 일시 정지될 때 사용자에게 표시되는 메시지입니다.<br/>
                      사용자는 이 메시지를 보고 Resume 시 피드백을 입력합니다.
                    </p>
                </div>
                </>
            )}
            </div>
        )}

        {/* Connections */}
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
                {incomingConnections.map((conn: any) => (
                    <Badge key={conn.id} variant="secondary" className="flex gap-1 items-center pr-1">
                    {conn.sourceLabel}
                    <Trash2 
                        className="w-3 h-3 cursor-pointer hover:text-destructive ml-1" 
                        onClick={() => onEdgeDelete(conn.id)}
                    />
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
                {outgoingConnections.map((conn: any) => (
                    <Badge key={conn.id} variant="secondary" className="flex gap-1 items-center pr-1">
                    {conn.targetLabel}
                    <Trash2 
                        className="w-3 h-3 cursor-pointer hover:text-destructive ml-1" 
                        onClick={() => onEdgeDelete(conn.id)}
                    />
                    </Badge>
                ))}
                </div>
            ) : (
                <p className="text-xs text-muted-foreground italic">No outgoing connections</p>
            )}
            </div>

            {/* Add Connection */}
            {availableTargets.length > 0 && (
            <div className="flex gap-2 pt-2">
                <Select value={selectedTargetNode} onValueChange={setSelectedTargetNode}>
                <SelectTrigger className="h-8 text-xs">
                    <SelectValue placeholder="Link to..." />
                </SelectTrigger>
                <SelectContent>
                    {availableTargets.map((t: any) => (
                    <SelectItem key={t.id} value={t.id}>{t.label}</SelectItem>
                    ))}
                </SelectContent>
                </Select>
                <Button 
                size="sm" 
                className="h-8"
                disabled={!selectedTargetNode}
                onClick={() => {
                    if (selectedTargetNode) {
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

        {/* Actions */}
        <div className="pt-4 border-t flex justify-between items-center">
            <Button variant="ghost" size="sm" onClick={onDelete} className="text-destructive hover:text-destructive hover:bg-destructive/10">
                <Trash2 className="w-4 h-4 mr-2" /> Delete
            </Button>
            <Button size="sm" onClick={handleSave}>Save Changes</Button>
        </div>
      </div>
  );
}
