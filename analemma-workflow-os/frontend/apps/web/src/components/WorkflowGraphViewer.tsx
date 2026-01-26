import { useMemo, useCallback, useState, useEffect, memo } from 'react';
import {
    ReactFlow,
    Background,
    Edge,
    Node,
    NodeTypes,
    ReactFlowProvider,
    BackgroundVariant,
    useReactFlow,
    Panel,
    MiniMap,
    Controls,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { AIModelNode } from './nodes/AIModelNode';
import { OperatorNode } from './nodes/OperatorNode';
import { TriggerNode } from './nodes/TriggerNode';
import { ControlNode } from './nodes/ControlNode';
import { SmartEdge } from './edges/SmartEdge';
import { cn } from '@/lib/utils';
import { AlertCircle } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

// --- Types ---
export type NodeStatus = 'idle' | 'running' | 'completed' | 'failed';

export interface WorkflowGraphViewerProps {
    nodes: Node[];
    edges: Edge[];
    activeNodeId?: string;
    completedNodeIds?: string[];
    failedNodeIds?: string[];
    onNodeClick?: (nodeId: string) => void;
    className?: string;
}

// --- Constants & Config ---
const nodeTypes: NodeTypes = {
    aiModel: AIModelNode,
    operator: OperatorNode,
    trigger: TriggerNode,
    control: ControlNode,
};

const edgeTypes = {
    smart: SmartEdge,
};

// --- Helpers ---

const resolveNodeStatus = (
    nodeId: string,
    activeId?: string,
    doneIds: string[] = [],
    failIds: string[] = []
): NodeStatus => {
    if (nodeId === activeId) return 'running';
    if (failIds.includes(nodeId)) return 'failed';
    if (doneIds.includes(nodeId)) return 'completed';
    return 'idle';
};

const getEdgeStyle = (
    edge: Edge,
    activeId?: string,
    doneIds: string[] = []
) => {
    const sourceDone = doneIds.includes(edge.source);
    const targetDone = doneIds.includes(edge.target) || edge.target === activeId;
    const isCompletedPath = sourceDone && targetDone;

    return {
        animated: edge.source === activeId,
        style: {
            ...edge.style,
            stroke: isCompletedPath ? '#22c55e' : (edge.source === activeId ? '#3b82f6' : 'rgba(255,255,255,0.1)'),
            strokeWidth: isCompletedPath ? 3 : 2,
            opacity: isCompletedPath || edge.source === activeId ? 1 : 0.4,
            transition: 'stroke 0.4s ease, stroke-width 0.4s ease, opacity 0.4s ease',
        },
    };
};

const STATUS_STYLES: Record<NodeStatus, string> = {
    running: "ring-2 ring-yellow-400 shadow-[0_0_20px_rgba(250,204,21,0.4)] animate-pulse",
    completed: "ring-2 ring-green-500 shadow-[0_0_15px_rgba(34,197,94,0.2)]",
    failed: "ring-2 ring-red-500 shadow-[0_0_15px_rgba(239,68,68,0.2)]",
    idle: "opacity-60 grayscale-[0.3]"
};

// --- Sub-components ---

const StatusLegend = () => (
    <Panel position="top-left" className="m-4">
        <div className="flex gap-4 p-3 bg-black/60 backdrop-blur-xl border border-white/5 rounded-2xl shadow-2xl overflow-hidden">
            {[
                { label: 'Running', color: 'bg-yellow-400' },
                { label: 'Completed', color: 'bg-green-500' },
                { label: 'Failed', color: 'bg-red-500' },
                { label: 'Waiting', color: 'bg-white/20' }
            ].map((s) => (
                <div key={s.label} className="flex items-center gap-2">
                    <div className={cn("w-2 h-2 rounded-full", s.color)} />
                    <span className="text-[10px] font-black uppercase tracking-widest text-white/40">
                        {s.label}
                    </span>
                </div>
            ))}
        </div>
    </Panel>
);

const WorkflowGraphViewerInner = ({
    nodes,
    edges,
    activeNodeId,
    completedNodeIds = [],
    failedNodeIds = [],
    onNodeClick,
    className,
}: WorkflowGraphViewerProps) => {
    const { fitView } = useReactFlow();
    const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

    const styledNodes = useMemo(() => {
        return nodes.map((node) => {
            const status = resolveNodeStatus(node.id, activeNodeId, completedNodeIds, failedNodeIds);
            return {
                ...node,
                className: cn(node.className, STATUS_STYLES[status]),
                data: {
                    ...node.data,
                    status,
                    _isSelected: node.id === selectedNodeId,
                },
            };
        });
    }, [nodes, activeNodeId, completedNodeIds, failedNodeIds, selectedNodeId]);

    const styledEdges = useMemo(() => {
        return edges.map((edge) => ({
            ...edge,
            ...getEdgeStyle(edge, activeNodeId, completedNodeIds),
        }));
    }, [edges, completedNodeIds, activeNodeId]);

    useEffect(() => {
        if (activeNodeId) {
            fitView({
                nodes: [{ id: activeNodeId }],
                duration: 600,
                padding: 0.8,
            });
        }
    }, [activeNodeId, fitView]);

    const handleNodesInitialized = useCallback(() => {
        fitView({ padding: 0.2, duration: 400 });
    }, [fitView]);

    const onNodeClickInternal = useCallback(
        (_: any, node: Node) => {
            setSelectedNodeId(node.id);
            onNodeClick?.(node.id);
        },
        [onNodeClick]
    );

    return (
        <div className={cn('h-full w-full relative bg-[#0a0a0a] overflow-hidden', className)}>
            <ReactFlow
                nodes={styledNodes}
                edges={styledEdges}
                onNodeClick={onNodeClickInternal}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                onInit={handleNodesInitialized}
                nodesDraggable={false}
                nodesConnectable={false}
                elementsSelectable={true}
                panOnDrag={true}
                zoomOnScroll={true}
                className="bg-transparent"
                minZoom={0.2}
                maxZoom={1.5}
            >
                <Background
                    color="#ffffff"
                    gap={25}
                    size={0.5}
                    variant={BackgroundVariant.Dots}
                    className="opacity-[0.03]"
                />

                <StatusLegend />

                <MiniMap
                    style={{ background: '#111', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.05)' }}
                    maskColor="rgba(0,0,0,0.6)"
                    nodeColor={(n) => {
                        const s = resolveNodeStatus(n.id, activeNodeId, completedNodeIds, failedNodeIds);
                        if (s === 'running') return '#fbbf24';
                        if (s === 'completed') return '#22c55e';
                        if (s === 'failed') return '#ef4444';
                        return '#333';
                    }}
                />
                <Controls showInteractive={false} className="bg-black/40 border border-white/5 rounded-xl overflow-hidden" />
            </ReactFlow>

            <AnimatePresence>
                {failedNodeIds.length > 0 && (
                    <motion.div
                        initial={{ opacity: 0, x: 20 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: 20 }}
                        className="absolute bottom-6 right-6 z-20 pointer-events-none"
                    >
                        <div className="flex items-center gap-3 bg-red-500/10 backdrop-blur-md border border-red-500/20 px-4 py-2 rounded-2xl">
                            <AlertCircle className="w-4 h-4 text-red-400" />
                            <span className="text-[11px] font-bold text-red-400 uppercase tracking-wider">
                                {failedNodeIds.length} Failure Detected
                            </span>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
};

export const WorkflowGraphViewer = memo((props: WorkflowGraphViewerProps) => (
    <ReactFlowProvider>
        <WorkflowGraphViewerInner {...props} />
    </ReactFlowProvider>
));

WorkflowGraphViewer.displayName = 'WorkflowGraphViewer';

export default WorkflowGraphViewer;
