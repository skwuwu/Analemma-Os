/**
 * 노드 실행 상태 추적 훅 (v2.0)
 * =====================================================
 * 
 * 워크플로우 이력 로그로부터 노드별 실행 상태를 추출
 * 
 * v2.0 Changes:
 * - Parallel execution support (activeNodeIds: string[])
 * - Content size limit (MAX_CONTENT_SIZE)
 * - Sorting optimization (check if already sorted)
 * - Instance ID support (execution_instance_id)
 * - Switch statement for cleaner status handling
 * - Content truncation for large payloads
 */

import { useMemo } from 'react';
import type { HistoryEntry } from '@/lib/types';

const MAX_CONTENT_SIZE = 10 * 1024; // 10KB limit for content storage

export interface NodeExecutionStatus {
    activeNodeIds: string[]; // Multiple parallel nodes support
    completedNodeIds: string[];
    failedNodeIds: string[];
    nodeDetails: Map<string, NodeDetail>;
}

export interface NodeDetail {
    nodeId: string;
    instanceId?: string; // Execution instance for loop/retry scenarios
    name: string;
    status: 'running' | 'completed' | 'failed' | 'idle';
    startTime?: number;
    endTime?: number;
    duration?: number;
    content?: string;
    contentTruncated?: boolean; // Flag if content was truncated
    error?: { message: string; type: string } | object | string | null;
    usage?: { total_tokens?: number; model?: string;[key: string]: any };
}

/**
 * Content truncation helper
 */
function truncateContent(content: string | undefined): { content?: string; truncated: boolean } {
    if (!content) return { truncated: false };
    
    if (content.length <= MAX_CONTENT_SIZE) {
        return { content, truncated: false };
    }
    
    return {
        content: content.substring(0, MAX_CONTENT_SIZE) + '... [truncated]',
        truncated: true
    };
}

/**
 * Check if array is already sorted by timestamp
 */
function isAlreadySorted(entries: HistoryEntry[]): boolean {
    for (let i = 1; i < entries.length; i++) {
        if ((entries[i].timestamp || 0) < (entries[i - 1].timestamp || 0)) {
            return false;
        }
    }
    return true;
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
            activeNodeIds: [],
            completedNodeIds: [],
            failedNodeIds: [],
            nodeDetails: new Map(),
        };

        if (!historyEntries?.length) {
            return result;
        }

        // Optimization: Skip sort if already sorted
        const sortedEntries = isAlreadySorted(historyEntries)
            ? historyEntries
            : [...historyEntries].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));

        for (const entry of sortedEntries) {
            // Support instance ID for loop/retry scenarios
            const instanceId = entry.execution_instance_id || entry.instance_id;
            const nodeId = entry.node_id || entry.name || 'unknown';
            const detailKey = instanceId ? `${nodeId}:${instanceId}` : nodeId;
            const status = (entry.status || '').toUpperCase();

            // Get or create node detail
            let detail = result.nodeDetails.get(detailKey);
            if (!detail) {
                detail = {
                    nodeId,
                    instanceId,
                    name: entry.name || nodeId,
                    status: 'idle',
                };
                result.nodeDetails.set(detailKey, detail);
            }

            // Update based on status using switch for cleaner code
            switch (status) {
                case 'RUNNING':
                    detail.status = 'running';
                    detail.startTime = entry.timestamp;
                    
                    // Support multiple active nodes (parallel execution)
                    if (!result.activeNodeIds.includes(detailKey)) {
                        result.activeNodeIds.push(detailKey);
                    }

                    // Remove from completed/failed if re-running
                    result.completedNodeIds = result.completedNodeIds.filter(id => id !== detailKey);
                    result.failedNodeIds = result.failedNodeIds.filter(id => id !== detailKey);
                    break;

                case 'COMPLETED':
                    detail.status = 'completed';
                    detail.endTime = entry.timestamp;
                    
                    if (detail.startTime && detail.endTime) {
                        detail.duration = detail.endTime - detail.startTime;
                    }
                    
                    // Truncate large content
                    const { content, truncated } = truncateContent(entry.content);
                    detail.content = content;
                    detail.contentTruncated = truncated;
                    detail.usage = entry.usage;

                    // Add to completed if not already there
                    if (!result.completedNodeIds.includes(detailKey)) {
                        result.completedNodeIds.push(detailKey);
                    }

                    // Remove from active
                    result.activeNodeIds = result.activeNodeIds.filter(id => id !== detailKey);
                    break;

                case 'FAILED':
                case 'ERROR':
                    detail.status = 'failed';
                    detail.endTime = entry.timestamp;
                    detail.error = entry.error;

                    // Add to failed if not already there
                    if (!result.failedNodeIds.includes(detailKey)) {
                        result.failedNodeIds.push(detailKey);
                    }

                    // Remove from active
                    result.activeNodeIds = result.activeNodeIds.filter(id => id !== detailKey);
                    break;
            }
        }

        return result;
    }, [historyEntries]);
}

export default useNodeExecutionStatus;
