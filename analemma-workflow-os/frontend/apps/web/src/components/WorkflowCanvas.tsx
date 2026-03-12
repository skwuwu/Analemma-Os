import { useCallback, useState, useMemo, useEffect, useRef, Fragment } from 'react';
import {
  ReactFlow,
  Background,
  MiniMap,
  Edge,
  Node,
  NodeTypes,
  ReactFlowProvider,
  ReactFlowInstance,
  BackgroundVariant,
  useOnSelectionChange,
  NodeChange,
  EdgeChange,
  MarkerType,
} from '@xyflow/react';
import { fetchAuthSession } from '@aws-amplify/auth';
import '@xyflow/react/dist/style.css';

import { AIModelNode } from './nodes/AIModelNode';
import { OperatorNode } from './nodes/OperatorNode';
import { TriggerNode } from './nodes/TriggerNode';
import { ControlNode } from './nodes/ControlNode';
import { ControlBlockNode } from './nodes/ControlBlockNode';
import { GroupNode } from './nodes/GroupNode';
import { OrthogonalEdge } from './edges/OrthogonalEdge';

// Dialog/Modal/Panel components - static imports to avoid runtime initialization issues
import { NodeEditorDialog } from './NodeEditorDialog';
import { GroupNameDialog } from './GroupNameDialog';
import { RollbackDialog } from './RollbackDialog';
import { SuggestionOverlay } from './SuggestionOverlay';
import { AuditPanel } from './AuditPanel';

import { Button } from './ui/button';
import {
  Keyboard,
  Layers,
  ChevronRight,
  Trash2,
  LayoutGrid,
} from 'lucide-react';
import { analyzeWorkflowGraph } from '@/lib/graphAnalysis';
import { computeAutoLayout } from '@/lib/autoLayout';
import { useWorkflowStore } from '@/lib/workflowStore';
import { useCodesignStore } from '@/lib/codesignStore';
import { WorkflowStatusIndicator } from './WorkflowStatusIndicator';
import { useTimeMachine } from '@/hooks/useBriefingAndCheckpoints';
import { toast } from 'sonner';
import type { TimelineItem, RollbackRequest } from '@/lib/types';
import { createWorkflowNode, generateNodeId } from '@/lib/nodeFactory';
import { cn } from '@/lib/utils';
import { motion, AnimatePresence } from 'framer-motion';

// MiniMap node color mapping
const MINIMAP_NODE_COLOR = (node: Node) => {
  switch (node.type) {
    case 'aiModel': return '#8b5cf6';
    case 'trigger': return '#22c55e';
    case 'operator': return '#3b82f6';
    case 'control': return '#eab308';
    case 'control_block': return '#eab308';
    case 'group': return '#a855f7';
    default: return '#64748b';
  }
};

const WorkflowCanvasInner = () => {
  // ==========================================
  // 1. ALL STATE DECLARATIONS FIRST (CRITICAL: Must be before any useEffect)
  // ==========================================
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [selectedNodes, setSelectedNodes] = useState<Node[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [groupDialogOpen, setGroupDialogOpen] = useState(false);
  const [reactFlowInstance, setReactFlowInstance] = useState<ReactFlowInstance<any, any> | null>(null);

  // Time Machine state (for rollback)
  const [rollbackDialogOpen, setRollbackDialogOpen] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState<TimelineItem | null>(null);
  const [currentExecutionId, setCurrentExecutionId] = useState<string | null>(null);

  // Audit Panel state (Local validation only)
  const [auditPanelOpen, setAuditPanelOpen] = useState(false);

  // Flow highlight state — click a node to highlight its upstream/downstream path
  const [highlightedNodeIds, setHighlightedNodeIds] = useState<Set<string>>(new Set());
  const [highlightedEdgeIds, setHighlightedEdgeIds] = useState<Set<string>>(new Set());

  // ==========================================
  // 2. NODE/EDGE TYPES (useMemo)
  // ==========================================
  const nodeTypes: NodeTypes = useMemo(() => ({
    aiModel: AIModelNode,
    operator: OperatorNode,
    trigger: TriggerNode,
    control: ControlNode,
    control_block: ControlBlockNode,
    group: GroupNode,
  }), []);

  const edgeTypes = useMemo(() => ({
    smart: OrthogonalEdge,
  }), []);

  // ==========================================
  // 3. STORE SUBSCRIPTIONS — individual selectors for granular re-render control.
  // ==========================================
  const nodes = useWorkflowStore(state => state.nodes);
  const edges = useWorkflowStore(state => state.edges);
  const subgraphs = useWorkflowStore(state => state.subgraphs);
  const navigationPath = useWorkflowStore(state => state.navigationPath);

  // Actions — stable function references (created once in store closure, never change)
  const addNode = useWorkflowStore(state => state.addNode);
  const updateNode = useWorkflowStore(state => state.updateNode);
  const removeNode = useWorkflowStore(state => state.removeNode);
  const removeEdge = useWorkflowStore(state => state.removeEdge);
  const addEdge = useWorkflowStore(state => state.addEdge);
  const clearWorkflow = useWorkflowStore(state => state.clearWorkflow);
  const loadWorkflow = useWorkflowStore(state => state.loadWorkflow);
  const onNodesChange = useWorkflowStore(state => state.onNodesChange);
  const onEdgesChange = useWorkflowStore(state => state.onEdgesChange);
  const onConnect = useWorkflowStore(state => state.onConnect);
  const groupNodes = useWorkflowStore(state => state.groupNodes);
  const ungroupNode = useWorkflowStore(state => state.ungroupNode);
  const navigateToSubgraph = useWorkflowStore(state => state.navigateToSubgraph);
  const navigateUp = useWorkflowStore(state => state.navigateUp);
  const setSelectedNodeId = useWorkflowStore(state => state.setSelectedNodeId);

  // Handle selection changes for both single and multi-node selection
  const handleSelectionChange = useCallback(({ nodes }: { nodes: Node[] }) => {
    setSelectedNodes(nodes);
    if (nodes.length === 1) {
      setSelectedNodeId(nodes[0].id);
    } else if (nodes.length === 0) {
      setSelectedNodeId(null);
    } else if (nodes.length > 1) {
      setSelectedNodeId(nodes[0].id);
    }
  }, [setSelectedNodeId]);

  useOnSelectionChange({
    onChange: handleSelectionChange,
  });

  // Co-design store — individual selectors only for what's actually used.
  const recordChange = useCodesignStore(state => state.recordChange);
  const requestSuggestions = useCodesignStore(state => state.requestSuggestions);
  const requestAudit = useCodesignStore(state => state.requestAudit);
  const recentChangesLength = useCodesignStore(state => state.recentChanges.length);

  // Stable structure keys — only change when graph topology changes
  const nodeStructureKey = useMemo(
    () => nodes.map(n => n.id).sort().join(','),
    [nodes]
  );
  const edgeStructureKey = useMemo(
    () => edges.map(e => `${e.source}-${e.target}`).sort().join(','),
    [edges]
  );

  // Auto-validation using graphAnalysis (recalculates only when topology changes)
  const analysisResult = useMemo(() => {
    if (nodes.length === 0) return null;
    return analyzeWorkflowGraph(nodes, edges);
  }, [nodeStructureKey, edgeStructureKey]);

  const validation = useMemo(() => {
    if (!analysisResult) {
      return { issueCount: 0, hasErrors: false, hasWarnings: false, warnings: [] };
    }
    const warnings = analysisResult.warnings || [];
    return {
      issueCount: warnings.length,
      hasErrors: warnings.some(w => w.type === 'unreachable_node'),
      hasWarnings: warnings.length > 0,
      warnings,
    };
  }, [analysisResult]);

  // ==========================================
  // Execution order numbering (from topological sort)
  // ==========================================
  const nodeOrderMap = useMemo(() => {
    if (!analysisResult) return new Map<string, number>();
    const map = new Map<string, number>();
    analysisResult.topologicalOrder.forEach((nodeId, idx) => {
      map.set(nodeId, idx + 1);
    });
    return map;
  }, [analysisResult]);

  // Inject execution order numbers into node data for rendering
  const nodesWithOrder = useMemo(() => {
    if (nodeOrderMap.size === 0) return nodes;
    return nodes.map(n => {
      const order = nodeOrderMap.get(n.id);
      if (order !== undefined && n.data?._executionOrder !== order) {
        return { ...n, data: { ...n.data, _executionOrder: order } };
      }
      if (order === undefined && n.data?._executionOrder !== undefined) {
        const { _executionOrder, ...rest } = n.data as any;
        return { ...n, data: rest };
      }
      return n;
    });
  }, [nodes, nodeOrderMap]);

  // ==========================================
  // Flow highlight — compute connected path on click
  // ==========================================
  const computeHighlightPath = useCallback((nodeId: string) => {
    const nodeSet = new Set<string>();
    const edgeSet = new Set<string>();

    // BFS upstream
    const upQueue = [nodeId];
    const upVisited = new Set<string>([nodeId]);
    while (upQueue.length > 0) {
      const cur = upQueue.shift()!;
      nodeSet.add(cur);
      for (const e of edges) {
        if (e.target === cur && !upVisited.has(e.source)) {
          upVisited.add(e.source);
          upQueue.push(e.source);
          edgeSet.add(e.id);
        }
      }
    }

    // BFS downstream
    const downQueue = [nodeId];
    const downVisited = new Set<string>([nodeId]);
    while (downQueue.length > 0) {
      const cur = downQueue.shift()!;
      nodeSet.add(cur);
      for (const e of edges) {
        if (e.source === cur && !downVisited.has(e.target)) {
          downVisited.add(e.target);
          downQueue.push(e.target);
          edgeSet.add(e.id);
        }
      }
    }

    return { nodeSet, edgeSet };
  }, [edges]);

  // Apply highlight dimming to edges
  const edgesWithHighlight = useMemo(() => {
    if (highlightedEdgeIds.size === 0) return edges;
    return edges.map(e => {
      const highlighted = highlightedEdgeIds.has(e.id);
      return {
        ...e,
        style: {
          ...e.style,
          opacity: highlighted ? 1 : 0.15,
        },
      };
    });
  }, [edges, highlightedEdgeIds]);

  // Apply highlight dimming to nodes
  const nodesWithHighlight = useMemo(() => {
    if (highlightedNodeIds.size === 0) return nodesWithOrder;
    return nodesWithOrder.map(n => ({
      ...n,
      style: {
        ...n.style,
        opacity: highlightedNodeIds.has(n.id) ? 1 : 0.2,
        transition: 'opacity 0.2s ease',
      },
    }));
  }, [nodesWithOrder, highlightedNodeIds]);

  // Default edge options: orthogonal with arrowhead
  const defaultEdgeOptions = useMemo(() => ({
    type: 'smart',
    markerEnd: {
      type: MarkerType.ArrowClosed,
      width: 16,
      height: 16,
      color: 'hsl(263 70% 60%)',
    },
    deletable: false,
    reconnectable: false,
    selectable: false,
    focusable: false,
  }), []);

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

  const onNodesChangeWithTracking = useCallback((changes: NodeChange[]) => {
    onNodesChange(changes);
    const positionChanges = changes.filter(c => c.type === 'position' && c.dragging === false);
    positionChanges.forEach(change => {
      recordChange('move_node', {
        id: (change as any).id,
        position: (change as any).position,
      });
    });
  }, [onNodesChange, recordChange]);

  const onEdgesChangeWithTracking = useCallback((changes: EdgeChange[]) => {
    onEdgesChange(changes);
    const removeChanges = changes.filter(c => c.type === 'remove');
    removeChanges.forEach(change => {
      recordChange('delete_edge', { id: (change as any).id });
    });
  }, [onEdgesChange, recordChange]);

  // ==========================================
  // 4. HOOKS FOR TIME MACHINE
  // ==========================================
  const handleTimeMachineSuccess = useCallback((result: any) => {
    toast.success(`Rollback success: new branch ${result.branched_thread_id} created`);
    setRollbackDialogOpen(false);
  }, []);

  const handleTimeMachineError = useCallback((error: Error) => {
    toast.error(`Rollback failed: ${error.message}`);
  }, []);

  const timeMachine = useTimeMachine({
    executionId: currentExecutionId || '',
    onRollbackSuccess: handleTimeMachineSuccess,
    onRollbackError: handleTimeMachineError,
  });

  const handleGroupConfirm = useCallback((groupName: string) => {
    const nodeIds = selectedNodes.map(n => n.id);
    groupNodes(nodeIds, groupName);
    setGroupDialogOpen(false);
    setSelectedNodes([]);
  }, [selectedNodes, groupNodes]);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      const type = event.dataTransfer.getData('application/reactflow');
      const label = event.dataTransfer.getData('label');
      const blockId = event.dataTransfer.getData('blockId');
      const dataString = event.dataTransfer.getData('defaultData');

      if (!type || !reactFlowInstance) return;

      const position = reactFlowInstance.screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      let data = { label };
      try {
        if (dataString) {
          data = { ...JSON.parse(dataString), label };
        }
      } catch (e) { }

      const newNode = createWorkflowNode({
        type,
        position,
        data,
        blockId
      });

      addNodeWithTracking(newNode);
    },
    [addNodeWithTracking, reactFlowInstance]
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onNodeDoubleClick = useCallback((_event: React.MouseEvent, node: Node) => {
    if (node.type === 'group' && node.data?.subgraphId) {
      navigateToSubgraph(node.data.subgraphId as string);
      return;
    }
    setSelectedNode(node);
    setEditorOpen(true);
  }, [navigateToSubgraph]);

  const handleNodeUpdate = useCallback((nodeId: string, updates: any) => {
    updateNodeWithTracking(nodeId, { data: updates });
  }, [updateNodeWithTracking]);

  const handleNodeDelete = useCallback((nodeId: string) => {
    removeNodeWithTracking(nodeId);
  }, [removeNodeWithTracking]);

  const clearCanvas = useCallback(() => {
    clearWorkflow();
  }, [clearWorkflow]);

  // ==========================================
  // Auto-Layout handler (Sugiyama / dagre)
  // ==========================================
  const handleAutoLayout = useCallback(() => {
    const currentNodes = useWorkflowStore.getState().nodes;
    const currentEdges = useWorkflowStore.getState().edges;
    if (currentNodes.length === 0) return;

    const layoutedNodes = computeAutoLayout(currentNodes, currentEdges, {
      direction: 'TB',
      nodeSpacingX: 80,
      nodeSpacingY: 100,
    });

    // Apply new positions via node changes
    const changes: NodeChange[] = layoutedNodes.map(n => ({
      type: 'position' as const,
      id: n.id,
      position: n.position,
      dragging: false,
    }));
    onNodesChange(changes);

    // Fit view after layout
    setTimeout(() => {
      reactFlowInstance?.fitView({ padding: 0.2, duration: 400 });
    }, 50);

    toast.success('Auto-layout applied');
  }, [onNodesChange, reactFlowInstance]);

  // Memoize ReactFlow config objects
  const fitViewOpts = useMemo(() => ({ padding: 0.2 }), []);
  const snapGridConfig: [number, number] = useMemo(() => [20, 20], []);
  const bgStyle = useMemo(() => ({ opacity: 0.4 }), []);

  const dialogConnectionData = useMemo(() => {
    if (!selectedNode || !editorOpen) return null;

    const incoming = edges
      .filter(e => e.target === selectedNode.id)
      .map(edge => ({
        id: edge.id,
        sourceLabel: (nodes.find(n => n.id === edge.source)?.data?.label as string) || edge.source
      }));

    const outgoing = edges
      .filter(e => e.source === selectedNode.id)
      .map(edge => ({
        id: edge.id,
        target: edge.target,
        targetLabel: (nodes.find(n => n.id === edge.target)?.data?.label as string) || edge.target
      }));

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
      id: generateNodeId(),
      source,
      target,
      animated: true,
      style: { stroke: 'hsl(263 70% 60%)', strokeWidth: 2 },
    };
    addEdge(newEdge);
  }, [addEdge]);

  // Co-design: request AI suggestions after user changes settle.
  useEffect(() => {
    if (recentChangesLength === 0) return;

    const currentNodes = useWorkflowStore.getState().nodes;
    if (currentNodes.length === 0) return;

    let isCancelled = false;
    const timeoutId = setTimeout(async () => {
      try {
        const session = await fetchAuthSession();
        if (isCancelled) return;
        const idToken = session.tokens?.idToken?.toString();

        const latestNodes = useWorkflowStore.getState().nodes;
        const latestEdges = useWorkflowStore.getState().edges;

        requestSuggestions({ nodes: latestNodes, edges: latestEdges }, idToken);
        requestAudit({ nodes: latestNodes, edges: latestEdges }, idToken);
      } catch (error) {
        if (!isCancelled) {
          console.error('Failed to get auth token for codesign:', error);
        }
      }
    }, 2000);

    return () => {
      isCancelled = true;
      clearTimeout(timeoutId);
    };
  }, [recentChangesLength]);

  // Keyboard shortcuts
  const selectedNodeRef = useRef(selectedNode);
  selectedNodeRef.current = selectedNode;
  const selectedNodesRef = useRef(selectedNodes);
  selectedNodesRef.current = selectedNodes;
  const editorOpenRef = useRef(editorOpen);
  editorOpenRef.current = editorOpen;

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return;

      const curSelectedNode = selectedNodeRef.current;
      const curSelectedNodes = selectedNodesRef.current;
      const curEditorOpen = editorOpenRef.current;

      if ((event.key === 'Delete' || event.key === 'Backspace') && (curSelectedNodes.length > 0 || curSelectedNode)) {
        event.preventDefault();
        if (curSelectedNodes.length > 1) {
          curSelectedNodes.forEach(n => handleNodeDelete(n.id));
          setSelectedNodes([]);
        } else if (curSelectedNode) {
          handleNodeDelete(curSelectedNode.id);
        }
        setSelectedNode(null);
        setEditorOpen(false);
      }

      if (event.key === 'Escape') {
        // Clear highlight first, then clear selection
        if (highlightedNodeIds.size > 0) {
          setHighlightedNodeIds(new Set());
          setHighlightedEdgeIds(new Set());
          return;
        }
        setSelectedNode(null);
        setEditorOpen(false);
      }

      if (event.key === 'Enter' && curSelectedNode && !curEditorOpen) {
        event.preventDefault();
        setEditorOpen(true);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleNodeDelete, highlightedNodeIds.size]);

  // Node click handler — single click highlights path, double click opens editor
  const onNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    setSelectedNode(node);

    // Compute and set highlight path
    const { nodeSet, edgeSet } = computeHighlightPath(node.id);
    setHighlightedNodeIds(nodeSet);
    setHighlightedEdgeIds(edgeSet);
  }, [computeHighlightPath]);

  // Clear highlight on pane click
  const onPaneClick = useCallback(() => {
    setHighlightedNodeIds(new Set());
    setHighlightedEdgeIds(new Set());
    setSelectedNode(null);
  }, []);

  // Rollback handler
  const handleRollbackClick = useCallback((item: TimelineItem) => {
    setRollbackTarget(item);
    setRollbackDialogOpen(true);
  }, []);

  const handleFocusNode = useCallback((nodeId: string) => {
    const node = useWorkflowStore.getState().nodes.find(n => n.id === nodeId);
    if (node && reactFlowInstance) {
      reactFlowInstance.fitView({ nodes: [node], duration: 400, padding: 0.5 });
      setSelectedNode(node);
      setEditorOpen(true);
    }
  }, [reactFlowInstance]);

  const handleOpenAuditPanel = useCallback(() => setAuditPanelOpen(true), []);
  const handleCloseAuditPanel = useCallback(() => setAuditPanelOpen(false), []);
  const handleCloseEditor = useCallback(() => setEditorOpen(false), []);
  const handleCloseGroupDialog = useCallback(() => setGroupDialogOpen(false), []);
  const handleRollbackDialogSuccess = useCallback(() => setRollbackDialogOpen(false), []);

  const handleRollbackPreview = useCallback(async (checkpointId: string) => {
    await timeMachine.loadPreview(checkpointId);
  }, [timeMachine.loadPreview]);

  const handleRollbackExecute = useCallback(async (request: Omit<RollbackRequest, 'preview_only'>) => {
    return await timeMachine.executeRollback(request);
  }, [timeMachine.executeRollback]);

  return (
    <>
      <div className="h-full w-full relative flex overflow-hidden bg-[#121212]">
        {/* Main Canvas Area */}
        <div className="flex-1 relative" onDrop={onDrop} onDragOver={onDragOver}>
          {/* Contextual Toolbar */}
          <div className="absolute top-4 right-4 z-10 flex gap-2">
            {/* Auto-Layout Button */}
            {nodes.length > 1 && (
              <div className="group/layout relative">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleAutoLayout}
                  className="gap-2 h-8 px-3 bg-slate-800/80 border-slate-700 hover:bg-blue-500/20 hover:border-blue-500/50 transition-colors"
                >
                  <LayoutGrid className="w-3.5 h-3.5" />
                  Auto-Layout
                </Button>
                <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 hidden group-hover/layout:block z-50 whitespace-nowrap rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md">
                  Arrange nodes in hierarchical layout
                </div>
              </div>
            )}

            {/* Clear Canvas Button */}
            {nodes.length > 0 && (
              <div className="group/clear relative">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    if (confirm('Are you sure you want to clear the entire canvas? This action cannot be undone.')) {
                      clearCanvas();
                    }
                  }}
                  className="gap-2 h-8 px-3 bg-slate-800/80 border-slate-700 hover:bg-red-500/20 hover:border-red-500/50 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  Clear
                </Button>
                <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 hidden group-hover/clear:block z-50 whitespace-nowrap rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md">
                  Clear entire canvas
                </div>
              </div>
            )}

            <AnimatePresence>
              {selectedNodes.length >= 2 && (
                <motion.div initial={{ scale: 0.8, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.8, opacity: 0 }}>
                  <Button variant="secondary" size="sm" onClick={() => setGroupDialogOpen(true)} className="gap-2 bg-slate-800 border-slate-700">
                    <Layers className="w-4 h-4 text-blue-400" />
                    Group Selection ({selectedNodes.length})
                  </Button>
                </motion.div>
              )}
            </AnimatePresence>

            {/* Status Indicator - Opens Audit Panel instead of Popover */}
            {nodes.length > 0 && (
              <WorkflowStatusIndicator
                issueCount={validation.issueCount}
                hasErrors={validation.hasErrors}
                hasWarnings={validation.hasWarnings}
                warnings={validation.warnings}
                onNodeClick={handleFocusNode}
                onClick={handleOpenAuditPanel}
              />
            )}
          </div>

          {/* Breadcrumbs for Subgraphs */}
          {navigationPath.length > 0 && (
            <div className="absolute top-4 left-4 z-10 flex items-center gap-1.5 bg-slate-900/60 backdrop-blur-md px-4 py-2 rounded-2xl border border-slate-800 shadow-xl">
              <button onClick={() => navigateUp(navigationPath.length)} className="text-xs font-black uppercase tracking-widest text-slate-500 hover:text-blue-400 transition-colors">ROOT</button>
              {navigationPath.map((subgraphId, index) => {
                const subgraph = subgraphs[subgraphId];
                const isLast = index === navigationPath.length - 1;
                return (
                  <Fragment key={subgraphId}>
                    <ChevronRight className="w-3.5 h-3.5 text-slate-700" />
                    {isLast ? (
                      <span className="text-xs font-black uppercase tracking-widest text-white">{subgraph?.metadata?.name || subgraphId}</span>
                    ) : (
                      <button onClick={() => navigateUp(navigationPath.length - index - 1)} className="text-xs font-black uppercase tracking-widest text-slate-500 hover:text-blue-400 transition-colors">{subgraph?.metadata?.name || subgraphId}</button>
                    )}
                  </Fragment>
                );
              })}
            </div>
          )}

          <ReactFlow
            nodes={nodesWithHighlight}
            edges={edgesWithHighlight}
            defaultEdgeOptions={defaultEdgeOptions}
            onNodesChange={onNodesChangeWithTracking}
            onEdgesChange={onEdgesChangeWithTracking}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onNodeDoubleClick={onNodeDoubleClick}
            onPaneClick={onPaneClick}
            onInit={setReactFlowInstance}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={fitViewOpts}
            minZoom={0.1}
            maxZoom={2}
            snapToGrid
            snapGrid={snapGridConfig}
            className="bg-[#121212]"
            deleteKeyCode={null}
            panOnDrag
            selectionOnDrag
            selectionKeyCode="Shift"
            zoomOnScroll
            panOnScroll={false}
          >
            {/* Background grid with cross pattern for directional hint */}
            <Background color="#333" gap={20} size={1} variant={BackgroundVariant.Cross} style={bgStyle} />

            {/* MiniMap for canvas overview */}
            <MiniMap
              nodeColor={MINIMAP_NODE_COLOR}
              nodeStrokeWidth={2}
              maskColor="rgba(0, 0, 0, 0.7)"
              style={{
                backgroundColor: '#1a1a2e',
                borderRadius: 12,
                border: '1px solid #333',
              }}
              pannable
              zoomable
            />
          </ReactFlow>

          {/* Shortcuts Info */}
          <div className="absolute bottom-4 left-4 z-10 flex items-center gap-3">
            <div className="group/shortcuts relative">
              <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500 bg-slate-900/80 backdrop-blur-sm px-3 py-1.5 rounded-xl border border-slate-800 cursor-help hover:text-slate-300 transition-colors">
                <Keyboard className="w-3.5 h-3.5" />
                SHORTCUTS
              </div>
              <div className="absolute bottom-full left-0 mb-2 hidden group-hover/shortcuts:block z-50 bg-slate-900 border border-slate-800 p-3 rounded-xl shadow-2xl">
                <div className="grid grid-cols-2 gap-x-6 gap-y-2">
                  <div className="flex items-center gap-2 text-xs font-medium text-slate-400"><kbd className="px-1.5 py-0.5 bg-slate-800 rounded border border-slate-700 text-[10px]">DEL</kbd> Delete Node</div>
                  <div className="flex items-center gap-2 text-xs font-medium text-slate-400"><kbd className="px-1.5 py-0.5 bg-slate-800 rounded border border-slate-700 text-[10px]">ENT</kbd> Edit Params</div>
                  <div className="flex items-center gap-2 text-xs font-medium text-slate-400"><kbd className="px-1.5 py-0.5 bg-slate-800 rounded border border-slate-700 text-[10px]">ESC</kbd> Clear Selection</div>
                  <div className="flex items-center gap-2 text-xs font-medium text-slate-400"><kbd className="px-1.5 py-0.5 bg-slate-800 rounded border border-slate-700 text-[10px]">⇧+DRAG</kbd> Multi-Select</div>
                  <div className="flex items-center gap-2 text-xs font-medium text-slate-400"><kbd className="px-1.5 py-0.5 bg-slate-800 rounded border border-slate-700 text-[10px]">DBL-CLICK</kbd> Enter Group</div>
                  <div className="flex items-center gap-2 text-xs font-medium text-slate-400"><kbd className="px-1.5 py-0.5 bg-slate-800 rounded border border-slate-700 text-[10px]">CLICK</kbd> Highlight Path</div>
                </div>
              </div>
            </div>
            <span className="text-[10px] text-slate-600">
              Click node to highlight path · <kbd className="px-1 py-0.5 bg-slate-800/50 rounded text-slate-500">ESC</kbd> to clear
            </span>
          </div>
        </div>

        <SuggestionOverlay />
      </div>

      <NodeEditorDialog
        node={selectedNode as any}
        open={editorOpen}
        onClose={handleCloseEditor}
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
        onClose={handleCloseGroupDialog}
        onConfirm={handleGroupConfirm}
        nodeCount={selectedNodes.length}
      />

      <RollbackDialog
        open={rollbackDialogOpen}
        onOpenChange={setRollbackDialogOpen}
        targetCheckpoint={rollbackTarget}
        preview={timeMachine.preview}
        loading={timeMachine.isPreviewLoading}
        onPreview={handleRollbackPreview}
        onExecute={handleRollbackExecute}
        onSuccess={handleRollbackDialogSuccess}
      />

      {/* Audit Panel Sidebar */}
      <AnimatePresence>
        {auditPanelOpen && (
          <motion.div
            initial={{ x: 400 }}
            animate={{ x: 0 }}
            exit={{ x: 400 }}
            transition={{ type: 'spring', damping: 25, stiffness: 200 }}
            className="absolute right-0 top-0 bottom-0 w-96 border-l border-slate-800 bg-slate-950/50 backdrop-blur-xl z-30 flex flex-col"
          >
            <AuditPanel
              issues={validation.warnings}
              onNodeClick={handleFocusNode}
              onClose={handleCloseAuditPanel}
              standalone
              key="validation-panel"
            />
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

export const WorkflowCanvas = () => (
  <ReactFlowProvider>
    <WorkflowCanvasInner />
  </ReactFlowProvider>
);
