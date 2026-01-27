/**
 * 실시간 타임라인 오케스트레이션 훅 (v2.0)
 * =====================================================
 * 
 * 실시간 스트리밍 데이터와 서버 과거 이력을 통합하는 데이터 엔진
 * 
 * v2.0 Changes:
 * - Deterministic ID generation (hash-based)
 * - Pure function extraction for data merging
 * - Enhanced type safety (TimelineEntry with __source)
 * - Improved normalizeEventTs (ISO string support)
 * - Error state management (loading/error tracking)
 * - Optimized pruning (single-pass seqCounter cleanup)
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { NotificationItem } from '@/lib/types';
import { makeAuthenticatedRequest } from '@/lib/api';
import { normalizeEventTs } from '@/lib/utils';

// 타임라인 전용 확장 타입
interface TimelineEntry extends NotificationItem {
    __seq: number;
    __source: 'stream' | 'fetch';
}

interface TimelineState {
    data: TimelineEntry[];
    loading: boolean;
    error: Error | null;
}

export interface ExecutionTimelineHook {
    executionTimelines: Record<string, TimelineEntry[]>;
    timelineStates: Record<string, TimelineState>;
    fetchExecutionTimeline: (executionId: string, force?: boolean) => Promise<NotificationItem[]>;
}

/**
 * Extract or generate ID (v2.0: 백엔드 checkpoint_id 우선 사용)
 * Backend CheckpointService already generates deterministic checkpoint_id
 */
function generateDeterministicId(event: NotificationItem): string {
    // 1. 백엔드 checkpoint_id가 있으면 그대로 사용 (동기화)
    if ((event as any).checkpoint_id) {
        return (event as any).checkpoint_id;
    }
    
    // 2. notification_id가 있으면 사용
    if ((event as any).notification_id || event.id) {
        return `timeline-${(event as any).notification_id || event.id}`;
    }
    
    // 3. Fallback: 간단한 hash 생성 (레거시 호환)
    const execId = event.payload?.execution_id || event.execution_id || '';
    const ts = event.payload?.timestamp || event.timestamp || event.receivedAt || 0;
    const msg = event.message || event.payload?.message || '';
    const type = event.type || '';

    const combined = `${execId}-${ts}-${type}-${msg.substring(0, 50)}`;

    // Simple hash function
    let hash = 0;
    for (let i = 0; i < combined.length; i++) {
        const char = combined.charCodeAt(i);
        hash = ((hash << 5) - hash) + char;
        hash = hash & hash; // Convert to 32bit integer
    }

    return `event:${Math.abs(hash).toString(36)}`;
}


/**
 * 타임라인 병합 로직 (순수 함수)
 */
function mergeTimelines(
    existing: TimelineEntry[],
    fetched: NotificationItem[],
    source: 'stream' | 'fetch',
    seqCounter: number
): { merged: TimelineEntry[], nextSeq: number } {
    const byId = new Map<string, TimelineEntry>();
    let currentSeq = seqCounter;

    // Add fetched events first
    for (const e of fetched) {
        const id = e.id || generateDeterministicId(e);
        const entry: TimelineEntry = {
            ...e,
            __seq: ++currentSeq,
            __source: source
        };
        byId.set(id, entry);
    }

    // Merge with existing, preserving local __seq if present
    for (const e of existing) {
        const id = e.id || generateDeterministicId(e);
        if (byId.has(id)) {
            const server = byId.get(id)!;
            const merged: TimelineEntry = {
                ...e,
                ...server,
                __seq: e.__seq, // Preserve original sequence
                __source: e.__source // Preserve original source
            };
            byId.set(id, merged);
        } else {
            byId.set(id, e);
        }
    }

    const merged = Array.from(byId.values()).sort((a, b) => normalizeEventTs(a) - normalizeEventTs(b));
    return { merged, nextSeq: currentSeq };
}

export const useExecutionTimeline = (
    notifications: NotificationItem[],
    API_BASE: string
): ExecutionTimelineHook => {
    const [executionTimelines, setExecutionTimelines] = useState<Record<string, TimelineEntry[]>>({});
    const [timelineStates, setTimelineStates] = useState<Record<string, TimelineState>>({});
    const seqCountersRef = useRef<Record<string, number>>({});

    // Configuration
    const TIMELINE_TTL_MS = 1000 * 60 * 60 * 24; // 24 hours
    const MAX_TIMELINES = 500;
    const PRUNE_INTERVAL_MS = 1000 * 60 * 5; // 5 minutes

    // 1. Pruning Effect (최적화: single-pass seqCounter cleanup)
    useEffect(() => {
        const id = setInterval(() => {
            setExecutionTimelines(prev => {
                const now = Date.now();
                const entries = Object.entries(prev);

                const fresh = entries.filter(([_, events]) => {
                    if (!events || events.length === 0) return false;
                    const lastTs = normalizeEventTs(events[events.length - 1]);
                    return now - lastTs <= TIMELINE_TTL_MS;
                });

                let kept = fresh;
                if (fresh.length > MAX_TIMELINES) {
                    kept = fresh
                        .sort((a, b) => normalizeEventTs(b[1][b[1].length - 1]) - normalizeEventTs(a[1][a[1].length - 1]))
                        .slice(0, MAX_TIMELINES);
                }

                // Single-pass: Build next state and clean seqCounters simultaneously
                const next = Object.fromEntries(kept);
                const keptKeys = new Set(kept.map(([k]) => k));
                const seq = seqCountersRef.current;

                for (const k of Object.keys(seq)) {
                    if (!keptKeys.has(k)) delete seq[k];
                }

                return next;
            });

            // Prune timeline states as well
            setTimelineStates(prev => {
                const next: Record<string, TimelineState> = {};
                for (const [key, state] of Object.entries(prev)) {
                    if (executionTimelines[key]) {
                        next[key] = state;
                    }
                }
                return next;
            });
        }, PRUNE_INTERVAL_MS);

        return () => clearInterval(id);
    }, [executionTimelines]);

    // 2. Append new notifications (with deterministic IDs)
    useEffect(() => {
        if (!notifications || notifications.length === 0) return;

        setExecutionTimelines(prev => {
            const next = { ...prev };
            const seq = seqCountersRef.current;

            for (const n of notifications) {
                if (!n) continue;

                const execId = n.payload?.execution_id || n.execution_id;
                const key = execId || `notification:${n.id || generateDeterministicId(n)}`;

                const arr = next[key] ? [...next[key]] : [];
                const eventId = n.id || generateDeterministicId(n);
                const exists = arr.some(e => (e.id || generateDeterministicId(e)) === eventId);

                if (!exists) {
                    seq[key] = (seq[key] || 0) + 1;
                    const enhanced: TimelineEntry = {
                        ...n,
                        __seq: seq[key],
                        __source: 'stream'
                    };
                    arr.push(enhanced);
                }
                next[key] = arr;
            }
            return next;
        });
    }, [notifications]);

    // 3. Fetch Timeline Function (with error state management)
    const fetchExecutionTimeline = useCallback(async (executionId: string, force = false) => {
        if (!executionId) return [];

        const local = executionTimelines[executionId];
        if (!force && local && local.length > 0) {
            return local;
        }

        // Set loading state
        setTimelineStates(prev => ({
            ...prev,
            [executionId]: {
                data: local || [],
                loading: true,
                error: null
            }
        }));

        try {
            const url = `${API_BASE}/executions/${encodeURIComponent(executionId)}/history`;
            const resp = await makeAuthenticatedRequest(url);
            if (!resp.ok) {
                throw new Error(`Failed to fetch timeline (${resp.status})`);
            }

            const txt = await resp.text();
            let parsed: unknown = null;
            try { parsed = JSON.parse(txt); } catch { parsed = txt; }

            let body = parsed;
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            if (parsed && (parsed as any).body) {
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                try { body = JSON.parse((parsed as any).body); } catch { body = (parsed as any).body; }
            }

            let events: NotificationItem[] = [];
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const b = body as any;
            if (Array.isArray(b)) events = b;
            else if (b?.events && Array.isArray(b.events)) events = b.events;
            else if (b?.items && Array.isArray(b.items)) events = b.items;
            else if (b?.step_function_state) {
                const sfs = b.step_function_state;
                if (Array.isArray(sfs.state_history)) {
                    events = sfs.state_history;
                } else if (sfs.state_data && Array.isArray(sfs.state_data.state_history)) {
                    events = sfs.state_data.state_history;
                }
            }
            else if (b && typeof b === 'object' && ('payload' in b || 'message' in b || 'id' in b)) events = [b];

            // Use pure merge function
            setExecutionTimelines(prev => {
                const existing = prev[executionId] || [];
                const seq = seqCountersRef.current;
                const currentSeq = seq[executionId] || 0;

                const { merged, nextSeq } = mergeTimelines(existing, events, 'fetch', currentSeq);
                seq[executionId] = nextSeq;

                return { ...prev, [executionId]: merged };
            });

            // Update state: success
            setTimelineStates(prev => ({
                ...prev,
                [executionId]: {
                    data: events as TimelineEntry[],
                    loading: false,
                    error: null
                }
            }));

            return events;
        } catch (e) {
            const error = e instanceof Error ? e : new Error('Unknown error');
            console.error('fetchExecutionTimeline failed', error);

            // Update state: error
            setTimelineStates(prev => ({
                ...prev,
                [executionId]: {
                    data: prev[executionId]?.data || [],
                    loading: false,
                    error
                }
            }));
        }

        setExecutionTimelines(prev => ({ ...prev, [executionId]: prev[executionId] || [] }));
        return [];
    }, [API_BASE, executionTimelines]);

    return { executionTimelines, timelineStates, fetchExecutionTimeline };
};
