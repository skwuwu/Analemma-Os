import { useCallback, useState, useMemo, useEffect, Fragment } from 'react';
import {
  ReactFlow,
  Background,
  Edge,
  Node,
  NodeTypes,
  ReactFlowProvider,
  ReactFlowInstance,
  BackgroundVariant,
  useReactFlow,
  useOnSelectionChange,
  SelectionMode,
  OnSelectionChangeParams,
} from '@xyflow/react';
import { useShallow } from 'zustand/react/shallow';
import '@xyflow/react/dist/style.css';
import { AIModelNode } from './nodes/AIModelNode';
import { OperatorNode } from './nodes/OperatorNode';
import { TriggerNode } from './nodes/TriggerNode';
import { ControlNode } from './nodes/ControlNode';
import { GroupNode } from './nodes/GroupNode';
import { SmartEdge } from './edges/SmartEdge';
import { NodeEditorDialog } from './NodeEditorDialog';
import { GroupNameDialog } from './GroupNameDialog';
import { PlanBriefingModal } from './PlanBriefingModal';
import { CheckpointTimeline } from './CheckpointTimeline';
import { RollbackDialog } from './RollbackDialog';
import { SuggestionOverlay } from './SuggestionOverlay';
import { SuggestionList } from './SuggestionList';
import { AuditPanel } from './AuditPanel';
import { EmptyCanvasGuide } from './EmptyCanvasGuide';
import { Button } from './ui/button';
import { Trash2, Keyboard, Layers, ChevronRight, Play, History, PanelRightOpen, PanelRightClose } from 'lucide-react';
import { TooltipProvider } from './ui/tooltip';
import { Tooltip, TooltipContent, TooltipTrigger } from './ui/tooltip';
import { useWorkflowStore } from '@/lib/workflowStore';
import { useCodesignStore } from '@/lib/codesignStore';
import { useCanvasMode } from '@/hooks/useCanvasMode';
import { usePlanBriefing, useCheckpoints, useTimeMachine } from '@/hooks/useBriefingAndCheckpoints';
import { toast } from 'sonner';
import type { TimelineItem, RollbackRequest } from '@/lib/types';

const nodeTypes: NodeTypes = {
  aiModel: AIModelNode,
  operator: OperatorNode,
  trigger: TriggerNode,
  control: ControlNode,
  group: GroupNode,
};

const edgeTypes = {
  smart: SmartEdge,
};

const WorkflowCanvasInner = () => {
  // 1. Store 최적화: nodes/edges만 shallow 비교로 구독
  const { nodes, edges, subgraphs, navigationPath } = useWorkflowStore(
    useShallow((state) => ({ 
      nodes: state.nodes, 
      edges: state.edges,
      subgraphs: state.subgraphs || {},
      navigationPath: state.navigationPath || ['root'],
    }))
  );
  // Actions (separate subscription to avoid re-renders on node/edge changes)
  const {
    addNode,
    updateNode,
    removeNode,
    removeEdge,
    addEdge,
    clearWorkflow,
    loadWorkflow,
    onNodesChange,
    onEdgesChange,
    onConnect,
    groupNodes,
    ungroupNode,
    navigateToSubgraph,
    navigateUp,
    setSelectedNodeId,
  } = useWorkflowStore();

  useOnSelectionChange({
    onChange: ({ nodes }) => {
      if (nodes.length > 0) {
        setSelectedNodeId(nodes[0].id);
      } else {
        setSelectedNodeId(null);
      }
    },
  });

  // Co-design store
  const {
    recordChange,
    clearChanges,
    pendingSuggestions,
    activeSuggestionId,
    setActiveSuggestion,
    acceptSuggestion,
    rejectSuggestion,
    auditIssues,
    setSyncStatus,
    requestSuggestions,
    requestAudit,
    recentChanges,
    addMessage,
  } = useCodesignStore();

  // Canvas mode detection
  const canvasMode = useCanvasMode();

  // Wrap workflow actions to record changes for Co-design
  const addNodeWithTracking = useCallback((node: Node) => {
    addNode(node);
    recordChange('add_node', {
      id: node.id,
      type: node.type,
      position: node.position,
      data: node.data,
    });
  }, [addNode, recordChange]);

  const updateNodeWithTracking = useCallback((id: string, changes: Partial<Node>) => {
    updateNode(id, changes);
    recordChange('update_node', { id, changes });
  }, [updateNode, recordChange]);

  const removeNodeWithTracking = useCallback((id: string) => {
    removeNode(id);
    recordChange('delete_node', { id });
  }, [removeNode, recordChange]);

  const addEdgeWithTracking = useCallback((edge: Edge) => {
    addEdge(edge);
    recordChange('add_edge', {
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
    });
  }, [addEdge, recordChange]);

  const removeEdgeWithTracking = useCallback((id: string) => {
    removeEdge(id);
    recordChange('delete_edge', { id });
  }, [removeEdge, recordChange]);

  // Wrap change handlers to record position/drag changes
  const onNodesChangeWithTracking = useCallback((changes: NodeChange[]) => {
    onNodesChange(changes);
    
    // Record position changes (when dragging stops)
    const positionChanges = changes.filter(c => c.type === 'position' && c.dragging === false);
    positionChanges.forEach(change => {
      recordChange('move_node', {
        id: change.id,
        position: change.position,
      });
    });
  }, [onNodesChange, recordChange]);

  const onEdgesChangeWithTracking = useCallback((changes: EdgeChange[]) => {
    onEdgesChange(changes);
    
    // Record edge removals
    const removeChanges = changes.filter(c => c.type === 'remove');
    removeChanges.forEach(change => {
      recordChange('delete_edge', { id: change.id });
    });
  }, [onEdgesChange, recordChange]);

  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [selectedNodes, setSelectedNodes] = useState<Node[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [groupDialogOpen, setGroupDialogOpen] = useState(false);
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance<any, any> | null>(null);
  
  // Plan Briefing & Time Machine state
  const [briefingOpen, setBriefingOpen] = useState(false);
  const [timelinePanelOpen, setTimelinePanelOpen] = useState(false);
  const [rollbackDialogOpen, setRollbackDialogOpen] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState<TimelineItem | null>(null);
  const [currentExecutionId, setCurrentExecutionId] = useState<string | null>(null);
  
  // Hooks for briefing and checkpoints
  const planBriefing = usePlanBriefing({
    onSuccess: () => {
      toast.success('실행 계획이 생성되었습니다');
    },
    onError: (error) => {
      toast.error(`미리보기 생성 실패: ${error.message}`);
    },
  });
  
  const checkpoints = useCheckpoints({
    executionId: currentExecutionId || undefined,
    enabled: !!currentExecutionId,
    refetchInterval: timelinePanelOpen ? 5000 : false,
  });
  
  const timeMachine = useTimeMachine({
    executionId: currentExecutionId || '',
    onRollbackSuccess: (result) => {
      toast.success(`롤백 성공: 새 브랜치 ${result.branched_thread_id} 생성됨`);
      setRollbackDialogOpen(false);
      checkpoints.refetch();
    },
    onRollbackError: (error) => {
      toast.error(`롤백 실패: ${error.message}`);
    },
  });

  // ID generation with fallback for environments without crypto.randomUUID
  const generateId = useCallback(() => {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    return 'node-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
  }, []);

  // Handle multi-selection changes
  const onSelectionChange = useCallback((params: OnSelectionChangeParams) => {
    setSelectedNodes(params.nodes);
  }, []);

  // Handle grouping selected nodes
  const handleGroupSelection = useCallback(() => {
    if (selectedNodes.length < 2) {
      toast.error('그룹화하려면 2개 이상의 노드를 선택하세요');
      return;
    }
    setGroupDialogOpen(true);
  }, [selectedNodes]);

  const handleGroupConfirm = useCallback((groupName: string) => {
    const nodeIds = selectedNodes.map(n => n.id);
    groupNodes(nodeIds, groupName);
    setGroupDialogOpen(false);
    setSelectedNodes([]);
    toast.success(`${nodeIds.length}개 노드가 "${groupName}"으로 그룹화됨`);
  }, [selectedNodes, groupNodes]);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      const type = event.dataTransfer.getData('application/reactflow');
      const label = event.dataTransfer.getData('label');
      const blockId = event.dataTransfer.getData('blockId');
      const dataString = event.dataTransfer.getData('defaultData');

      if (!type || !reactFlowInstance) return;

      // Parse initial data safely
      let initialData = { label };
      try {
        if (dataString) {
          initialData = { ...JSON.parse(dataString), label };
        }
      } catch (e) {
        console.error('Failed to parse drop data', e);
      }

      const position = reactFlowInstance.screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      const newNode: Node = {
        id: generateId(),
        type,
        position,
        data: { ...initialData, blockId },
      };

      addNodeWithTracking(newNode);
    },
    [addNode, reactFlowInstance]
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onNodeDoubleClick = useCallback((_event: React.MouseEvent, node: Node) => {
    // 그룹 노드(서브그래프)인 경우 내부로 진입
    if (node.type === 'group' && node.data?.subgraphId) {
      navigateToSubgraph(node.data.subgraphId);
      return;
    }
    setSelectedNode(node);
    setEditorOpen(true);
  }, [navigateToSubgraph]);

  const handleNodeUpdate = useCallback((nodeId: string, updates: any) => {
    updateNodeWithTracking(nodeId, { data: updates });
  }, [updateNodeWithTracking]);

  const handleNodeDelete = useCallback((nodeId: string) => {
    removeNode(nodeId);
  }, [removeNode]);

  const clearCanvas = useCallback(() => {
    clearWorkflow();
  }, [clearWorkflow]);

  // Note: low-level add/update/remove wrappers removed — use store actions directly

  // Memoize nodes with onDelete handler to prevent unnecessary re-renders
  const nodesWithHandlers = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          onDelete: handleNodeDelete,
        },
      })),
    [nodes, handleNodeDelete]
  );

  // 다이얼로그에 전달할 연결 데이터를 미리 계산
  const dialogConnectionData = useMemo(() => {
    if (!selectedNode || !editorOpen) return null;

    // 들어오는 연결
    const incoming = edges
      .filter(e => e.target === selectedNode.id)
      .map(edge => ({
        id: edge.id,
        sourceLabel: (nodes.find(n => n.id === edge.source)?.data?.label as string) || edge.source
      }));

    // 나가는 연결
    const outgoing = edges
      .filter(e => e.source === selectedNode.id)
      .map(edge => ({
        id: edge.id,
        target: edge.target,
        targetLabel: (nodes.find(n => n.id === edge.target)?.data?.label as string) || edge.target
      }));

    // 연결 가능한 대상
    const outgoingTargetNodeIds = new Set(outgoing.map(e => e.target));
    const available = nodes
      .filter(n =>
        n.id !== selectedNode.id &&
        n.type !== 'trigger' &&
        !outgoingTargetNodeIds.has(n.id)
      )
      .map(n => ({ id: n.id, label: (n.data?.label as string) || n.id }));

    return { incoming, outgoing, available };
  }, [selectedNode, nodes, edges, editorOpen]);

  const handleEdgeDeleteInDialog = useCallback((edgeId: string) => {
    removeEdge(edgeId);
  }, [removeEdge]);

  const handleEdgeCreateInDialog = useCallback((source: string, target: string) => {
    const newEdge = {
      id: generateId(),
      source,
      target,
      animated: true,
      style: { stroke: 'hsl(263 70% 60%)', strokeWidth: 2 },
    };
    addEdge(newEdge);
  }, [addEdge, generateId]);

  // Co-design: 변경사항이 있을 때 AI 제안 요청
  useEffect(() => {
    if (recentChanges.length > 0) {
      const timeoutId = setTimeout(() => {
        requestSuggestions({ nodes, edges });
        requestAudit({ nodes, edges });
      }, 2000); // 2초 디바운스
      
      return () => clearTimeout(timeoutId);
    }
  }, [recentChanges.length, nodes, edges, requestSuggestions, requestAudit]);

  // 키보드 단축키 핸들러
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // 입력 필드에서는 단축키 무시
      const target = event.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) {
        return;
      }

      // Delete/Backspace: 선택된 노드 삭제
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedNode) {
        event.preventDefault();
        handleNodeDelete(selectedNode.id);
        setSelectedNode(null);
        setEditorOpen(false);
        toast.success('노드가 삭제되었습니다');
      }

      // Escape: 선택 해제 및 다이얼로그 닫기
      if (event.key === 'Escape') {
        setSelectedNode(null);
        setEditorOpen(false);
      }

      // Enter: 선택된 노드 편집
      if (event.key === 'Enter' && selectedNode && !editorOpen) {
        event.preventDefault();
        setEditorOpen(true);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedNode, editorOpen, handleNodeDelete]);

  // 노드 선택 핸들러
  const onNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    setSelectedNode(node);
  }, []);

  // Note: imperative handle removed — store exposes actions directly.

  // 미리보기 실행 핸들러
  const handlePreviewExecution = useCallback(async () => {
    try {
      await planBriefing.generate({
        workflow_config: {
          name: 'Current Workflow',
          nodes: nodes,
          edges: edges,
        },
        initial_statebag: {},
        use_llm: false,
      });
      setBriefingOpen(true);
    } catch (error) {
      console.error('Failed to generate preview:', error);
    }
  }, [planBriefing, nodes, edges]);
  
  // 실행 확인 핸들러
  const handleConfirmExecution = useCallback(async () => {
    // TODO: 실제 실행 로직 연결
    toast.success('워크플로우 실행이 시작됩니다');
    setBriefingOpen(false);
    
    // 실행 시작 시 타임라인 패널 열기 및 executionId 설정
    // setCurrentExecutionId('new-execution-id');
    // setTimelinePanelOpen(true);
  }, []);
  
  // 롤백 핸들러
  const handleRollbackClick = useCallback((item: TimelineItem) => {
    setRollbackTarget(item);
    setRollbackDialogOpen(true);
  }, []);
  
  const handleRollbackPreview = useCallback(async (checkpointId: string) => {
    await timeMachine.loadPreview(checkpointId);
  }, [timeMachine]);
  
  const handleRollbackExecute = useCallback(async (request: Omit<RollbackRequest, 'preview_only'>) => {
    return await timeMachine.executeRollback(request);
  }, [timeMachine]);

  // 빈 Canvas에서 AI Designer 시작 핸들러
  const handleQuickStart = useCallback(async (prompt: string, persona?: string, systemPrompt?: string) => {
    try {
      addMessage('user', prompt);
      
      if (persona && systemPrompt) {
        addMessage('system', `도메인 전문가 모드 활성화: ${persona.replace('_', ' ')}`);
        addMessage('assistant', `${persona.replace('_', ' ')} 전문가로서 워크플로우를 설계하겠습니다.`);
      }
      
      addMessage('assistant', 'AI Designer가 워크플로우 초안을 생성하고 있습니다...');
      
      // 여기서 실제 API 호출을 하거나 WorkflowChat 컴포넌트의 로직을 재사용할 수 있습니다.
      // 현재는 사용자가 채팅에서 직접 입력하도록 안내합니다.
      toast.success('채팅창에서 AI Designer와 대화를 시작하세요!');
    } catch (error) {
      console.error('Quick start failed:', error);
      toast.error('빠른 시작에 실패했습니다.');
    }
  }, [addMessage]);

  return (
    <>
      <div className="h-full w-full relative" onDrop={onDrop} onDragOver={onDragOver}>
        {/* Empty Canvas Guide - 빈 Canvas일 때만 표시 */}
        {canvasMode.isEmpty && (
          <EmptyCanvasGuide 
            onQuickStart={handleQuickStart}
            className="absolute inset-0 z-10 bg-background/95 backdrop-blur-sm"
          />
        )}
        {/* 상단 툴바 */}
        <div className="absolute top-4 right-4 z-10 flex gap-2">
          {/* 미리보기 실행 버튼 */}
          <Button
            variant="default"
            size="sm"
            onClick={handlePreviewExecution}
            disabled={planBriefing.isLoading || nodes.length === 0}
            className="gap-2"
          >
            <Play className="w-4 h-4" />
            {planBriefing.isLoading ? '생성 중...' : '미리보기 실행'}
          </Button>
          
          {/* 타임라인 패널 토글 */}
          {currentExecutionId && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setTimelinePanelOpen(!timelinePanelOpen)}
              className="gap-2"
            >
              {timelinePanelOpen ? (
                <PanelRightClose className="w-4 h-4" />
              ) : (
                <PanelRightOpen className="w-4 h-4" />
              )}
              <History className="w-4 h-4" />
            </Button>
          )}
          
          {/* 그룹 버튼 - 2개 이상 선택시 활성화 */}
          {selectedNodes.length >= 2 && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setGroupDialogOpen(true)}
              className="gap-2"
            >
              <Layers className="w-4 h-4" />
              그룹화 ({selectedNodes.length})
            </Button>
          )}
          <Button
            variant="destructive"
            size="sm"
            onClick={clearCanvas}
            className="gap-2"
          >
            <Trash2 className="w-4 h-4" />
            Clear Canvas
          </Button>
        </div>

        {/* 네비게이션 브레드크럼 - 서브그래프 내부에 있을 때 표시 */}
        {navigationPath.length > 0 && (
          <div className="absolute top-4 left-4 z-10 flex items-center gap-1 bg-background/90 backdrop-blur-sm px-3 py-2 rounded-lg border shadow-sm">
            <button
              onClick={() => navigateUp(navigationPath.length)}
              className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              루트
            </button>
            {navigationPath.map((subgraphId, index) => {
              const subgraph = subgraphs[subgraphId];
              const isLast = index === navigationPath.length - 1;
              return (
                <Fragment key={subgraphId}>
                  <ChevronRight className="w-4 h-4 text-muted-foreground" />
                  {isLast ? (
                    <span className="text-sm font-medium">
                      {subgraph?.metadata?.name || subgraphId}
                    </span>
                  ) : (
                    <button
                      onClick={() => navigateUp(navigationPath.length - index - 1)}
                      className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
                    >
                      {subgraph?.metadata?.name || subgraphId}
                    </button>
                  )}
                </Fragment>
              );
            })}
          </div>
        )}

        <ReactFlow
          nodes={nodesWithHandlers}
          edges={edges}
          onNodesChange={onNodesChangeWithTracking}
          onEdgesChange={onEdgesChangeWithTracking}
          onConnect={onConnect}
          onNodeClick={onNodeClick}
          onNodeDoubleClick={onNodeDoubleClick}
          onInit={setReactFlowInstance}
          onSelectionChange={onSelectionChange}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          className="bg-[#1a1a1a]"
          deleteKeyCode={null} // 커스텀 키보드 핸들러 사용
          selectionOnDrag={true}
          panOnDrag={[1, 2]} // 중간/오른쪽 버튼으로 패닝
          selectionMode={SelectionMode.Partial}
        >
          <Background color="#333" gap={20} size={1} variant={BackgroundVariant.Lines} />
        </ReactFlow>

        {/* 키보드 단축키 힌트 */}
        <div className="absolute bottom-4 left-4 z-10">
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="flex items-center gap-1 text-xs text-muted-foreground bg-background/80 backdrop-blur-sm px-2 py-1 rounded border">
                <Keyboard className="w-3 h-3" />
                <span>단축키</span>
              </div>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">
              <div className="space-y-1">
                <div><kbd className="px-1 bg-muted rounded">Delete</kbd> 노드 삭제</div>
                <div><kbd className="px-1 bg-muted rounded">Enter</kbd> 노드 편집</div>
                <div><kbd className="px-1 bg-muted rounded">Esc</kbd> 선택 해제</div>
                <div><kbd className="px-1 bg-muted rounded">더블클릭</kbd> 노드 편집</div>
                <div><kbd className="px-1 bg-muted rounded">드래그</kbd> 다중 선택</div>
                <div><kbd className="px-1 bg-muted rounded">그룹화</kbd> 서브그래프 생성</div>
              </div>
            </TooltipContent>
          </Tooltip>
        </div>
      </div>

      <NodeEditorDialog
        key={selectedNode?.id}
        node={selectedNode}
        open={editorOpen}
        onClose={() => setEditorOpen(false)}
        onSave={handleNodeUpdate}
        onDelete={handleNodeDelete}
        incomingConnections={dialogConnectionData?.incoming}
        outgoingConnections={dialogConnectionData?.outgoing}
        availableTargets={dialogConnectionData?.available}
        onEdgeDelete={handleEdgeDeleteInDialog}
        onEdgeCreate={handleEdgeCreateInDialog}
      />

      <GroupNameDialog
        open={groupDialogOpen}
        onClose={() => setGroupDialogOpen(false)}
        onConfirm={handleGroupConfirm}
        nodeCount={selectedNodes.length}
      />
      
      {/* Plan Briefing Modal */}
      <PlanBriefingModal
        open={briefingOpen}
        onOpenChange={setBriefingOpen}
        briefing={planBriefing.briefing}
        loading={planBriefing.isLoading}
        onConfirm={handleConfirmExecution}
        onCancel={() => setBriefingOpen(false)}
      />
      
      {/* Rollback Dialog */}
      <RollbackDialog
        open={rollbackDialogOpen}
        onOpenChange={setRollbackDialogOpen}
        targetCheckpoint={rollbackTarget}
        preview={timeMachine.preview}
        loading={timeMachine.isPreviewLoading}
        onPreview={handleRollbackPreview}
        onExecute={handleRollbackExecute}
        onSuccess={() => {
          checkpoints.refetch();
        }}
      />
      
      {/* Timeline Side Panel */}
      {timelinePanelOpen && currentExecutionId && (
        <div className="absolute top-0 right-0 h-full w-80 bg-background border-l z-20 overflow-hidden">
          <div className="p-4 h-full overflow-auto">
            <CheckpointTimeline
              items={checkpoints.timeline}
              loading={checkpoints.isLoading}
              selectedId={timeMachine.selectedCheckpointId}
              compareId={timeMachine.compareCheckpointId}
              onSelect={(item) => {
                // 선택 시 해당 노드 하이라이트 등 추가 가능
              }}
              onRollback={handleRollbackClick}
              onCompare={(item) => {
                if (timeMachine.selectedCheckpointId && timeMachine.selectedCheckpointId !== item.checkpoint_id) {
                  timeMachine.compare(timeMachine.selectedCheckpointId, item.checkpoint_id);
                }
              }}
              onPreview={(item) => {
                checkpoints.getDetail(item.checkpoint_id);
              }}
            />
          </div>
        </div>
      )}

      {/* Co-design Assistant Components */}
      <SuggestionOverlay />
      <SuggestionList />
      <AuditPanel />
    </>
  );
}

export const WorkflowCanvas = () => (
  <ReactFlowProvider>
    <TooltipProvider>
      <WorkflowCanvasInner />
    </TooltipProvider>
  </ReactFlowProvider>
);
