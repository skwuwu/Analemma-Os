import { useMemo } from 'react';
import type { HistoryEntry } from '@/lib/types';

export interface NodeExecutionStatus {
    activeNodeId: string | null;
    completedNodeIds: string[];
    failedNodeIds: string[];
    nodeDetails: Map<string, NodeDetail>;
}

export interface NodeDetail {
    nodeId: string;
    name: string;
    status: 'running' | 'completed' | 'failed' | 'idle';
    startTime?: number;
    endTime?: number;
    duration?: number;
    content?: string;
    error?: { message: string; type: string } | object | string | null;
    usage?: { total_tokens?: number; model?: string;[key: string]: any };
}

/**
 * Extract node execution status from history entries.
 * Maps new_history_logs to node IDs and their states.
 */
export function useNodeExecutionStatus(
    historyEntries: HistoryEntry[] | undefined
): NodeExecutionStatus {
    return useMemo(() => {
        const result: NodeExecutionStatus = {
            activeNodeId: null,
            completedNodeIds: [],
            failedNodeIds: [],
            nodeDetails: new Map(),
        };

        if (!historyEntries || !Array.isArray(historyEntries)) {
            return result;
        }

        // Process entries in chronological order
        const sortedEntries = [...historyEntries].sort(
            (a, b) => (a.timestamp || 0) - (b.timestamp || 0)
        );

        for (const entry of sortedEntries) {
            const nodeId = entry.node_id || entry.name || 'unknown';
            const status = (entry.status || '').toUpperCase();

            // Get or create node detail
            let detail = result.nodeDetails.get(nodeId);
            if (!detail) {
                detail = {
                    nodeId,
                    name: entry.name || nodeId,
                    status: 'idle',
                };
                result.nodeDetails.set(nodeId, detail);
            }

            // Update based on status
            if (status === 'RUNNING') {
                detail.status = 'running';
                detail.startTime = entry.timestamp;
                result.activeNodeId = nodeId;

                // Remove from completed/failed if re-running
                result.completedNodeIds = result.completedNodeIds.filter(id => id !== nodeId);
                result.failedNodeIds = result.failedNodeIds.filter(id => id !== nodeId);
            } else if (status === 'COMPLETED') {
                detail.status = 'completed';
                detail.endTime = entry.timestamp;
                if (detail.startTime && detail.endTime) {
                    detail.duration = detail.endTime - detail.startTime;
                }
                detail.content = entry.content;
                detail.usage = entry.usage;

                // Add to completed if not already there
                if (!result.completedNodeIds.includes(nodeId)) {
                    result.completedNodeIds.push(nodeId);
                }

                // Clear active if this was active
                if (result.activeNodeId === nodeId) {
                    result.activeNodeId = null;
                }
            } else if (status === 'FAILED' || status === 'ERROR') {
                detail.status = 'failed';
                detail.endTime = entry.timestamp;
                detail.error = entry.error;

                // Add to failed if not already there
                if (!result.failedNodeIds.includes(nodeId)) {
                    result.failedNodeIds.push(nodeId);
                }

                // Clear active if this was active
                if (result.activeNodeId === nodeId) {
                    result.activeNodeId = null;
                }
            }
        }

        return result;
    }, [historyEntries]);
}

export default useNodeExecutionStatus;
