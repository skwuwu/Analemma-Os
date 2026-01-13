import { useMemo, useCallback, useState, useEffect } from 'react';
import {
    ReactFlow,
    Background,
    Edge,
    Node,
    NodeTypes,
    ReactFlowProvider,
    BackgroundVariant,
    useReactFlow,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { AIModelNode } from './nodes/AIModelNode';
import { OperatorNode } from './nodes/OperatorNode';
import { TriggerNode } from './nodes/TriggerNode';
import { ControlNode } from './nodes/ControlNode';
import { SmartEdge } from './edges/SmartEdge';
import { cn } from '@/lib/utils';

// Node status types for visual feedback
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

// Custom nodeTypes with status-aware styling
const nodeTypes: NodeTypes = {
    aiModel: AIModelNode,
    operator: OperatorNode,
    trigger: TriggerNode,
    control: ControlNode,
};

const edgeTypes = {
    smart: SmartEdge,
};

// Status-based styles
const getNodeStatusStyle = (status: NodeStatus) => {
    switch (status) {
        case 'running':
            return {
                boxShadow: '0 0 0 3px #fbbf24, 0 0 20px rgba(251, 191, 36, 0.5)',
                animation: 'pulse 1.5s ease-in-out infinite',
            };
        case 'completed':
            return {
                boxShadow: '0 0 0 3px #22c55e',
            };
        case 'failed':
            return {
                boxShadow: '0 0 0 3px #ef4444',
            };
        default:
            return {};
    }
};

// Inner component to use ReactFlow hooks
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

    // Determine node status
    const getNodeStatus = useCallback(
        (nodeId: string): NodeStatus => {
            if (nodeId === activeNodeId) return 'running';
            if (failedNodeIds.includes(nodeId)) return 'failed';
            if (completedNodeIds.includes(nodeId)) return 'completed';
            return 'idle';
        },
        [activeNodeId, completedNodeIds, failedNodeIds]
    );

    // Apply status styles to nodes
    const styledNodes = useMemo(() => {
        return nodes.map((node) => {
            const status = getNodeStatus(node.id);
            return {
                ...node,
                style: {
                    ...node.style,
                    ...getNodeStatusStyle(status),
                },
                data: {
                    ...node.data,
                    _status: status, // Pass status to custom node components
                    _isSelected: node.id === selectedNodeId,
                },
            };
        });
    }, [nodes, getNodeStatus, selectedNodeId]);

    // Apply status styles to edges (completed path turns green)
    const styledEdges = useMemo(() => {
        return edges.map((edge) => {
            const sourceCompleted = completedNodeIds.includes(edge.source);
            const targetCompleted = completedNodeIds.includes(edge.target) || edge.target === activeNodeId;
            const isActivePath = sourceCompleted && targetCompleted;

            return {
                ...edge,
                animated: edge.source === activeNodeId,
                style: {
                    ...edge.style,
                    stroke: isActivePath ? '#22c55e' : (edge.style?.stroke || 'hsl(263 70% 60%)'),
                    strokeWidth: isActivePath ? 3 : 2,
                    opacity: isActivePath || edge.source === activeNodeId ? 1 : 0.6,
                },
            };
        });
    }, [edges, completedNodeIds, activeNodeId]);

    // Handle node click
    const handleNodeClick = useCallback(
        (_event: React.MouseEvent, node: Node) => {
            setSelectedNodeId(node.id);
            onNodeClick?.(node.id);
        },
        [onNodeClick]
    );

    // Auto-focus on active node
    useEffect(() => {
        if (activeNodeId) {
            // Small delay to allow ReactFlow to render
            setTimeout(() => {
                fitView({
                    nodes: [{ id: activeNodeId }],
                    duration: 500,
                    padding: 0.5,
                });
            }, 100);
        }
    }, [activeNodeId, fitView]);

    // Fit view on initial load
    useEffect(() => {
        if (nodes.length > 0) {
            setTimeout(() => fitView({ padding: 0.2, duration: 300 }), 100);
        }
    }, [nodes.length, fitView]);

    return (
        <div className={cn('h-full w-full relative', className)}>
            {/* Status Legend */}
            <div className="absolute top-4 left-4 z-10 flex gap-3 text-xs bg-background/80 backdrop-blur-sm px-3 py-2 rounded-lg border">
                <div className="flex items-center gap-1.5">
                    <div className="w-3 h-3 rounded-full bg-yellow-400 animate-pulse" />
                    <span>실행 중</span>
                </div>
                <div className="flex items-center gap-1.5">
                    <div className="w-3 h-3 rounded-full bg-green-500" />
                    <span>완료</span>
                </div>
                <div className="flex items-center gap-1.5">
                    <div className="w-3 h-3 rounded-full bg-red-500" />
                    <span>실패</span>
                </div>
                <div className="flex items-center gap-1.5">
                    <div className="w-3 h-3 rounded-full bg-gray-500" />
                    <span>대기</span>
                </div>
            </div>

            <ReactFlow
                nodes={styledNodes}
                edges={styledEdges}
                onNodeClick={handleNodeClick}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                nodesDraggable={false}
                nodesConnectable={false}
                elementsSelectable={true}
                panOnDrag={true}
                zoomOnScroll={true}
                fitView
                className="bg-[#1a1a1a]"
            >
                <Background color="#333" gap={20} size={1} variant={BackgroundVariant.Lines} />
            </ReactFlow>

            {/* CSS for pulse animation */}
            <style>{`
        @keyframes pulse {
          0%, 100% {
            box-shadow: 0 0 0 3px #fbbf24, 0 0 20px rgba(251, 191, 36, 0.5);
          }
          50% {
            box-shadow: 0 0 0 5px #fbbf24, 0 0 30px rgba(251, 191, 36, 0.8);
          }
        }
      `}</style>
        </div>
    );
};

// Exported component with ReactFlowProvider wrapper
export const WorkflowGraphViewer = (props: WorkflowGraphViewerProps) => (
    <ReactFlowProvider>
        <WorkflowGraphViewerInner {...props} />
    </ReactFlowProvider>
);

export default WorkflowGraphViewer;
