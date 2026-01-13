import { useState, useEffect, useRef, useCallback } from 'react';
import { NotificationItem } from '@/lib/types';
import { makeAuthenticatedRequest } from '@/lib/api';

export interface ExecutionTimelineHook {
    executionTimelines: Record<string, NotificationItem[]>;
    fetchExecutionTimeline: (executionId: string, force?: boolean) => Promise<NotificationItem[]>;
}

export const useExecutionTimeline = (
    notifications: NotificationItem[],
    API_BASE: string
): ExecutionTimelineHook => {
    const [executionTimelines, setExecutionTimelines] = useState<Record<string, NotificationItem[]>>({});
    const seqCountersRef = useRef<Record<string, number>>({});

    // Configuration
    const TIMELINE_TTL_MS = 1000 * 60 * 60 * 24; // 24 hours
    const MAX_TIMELINES = 500;
    const PRUNE_INTERVAL_MS = 1000 * 60 * 5; // 5 minutes

    // Helper: Normalize event timestamps
    const normalizeEventTs = (e: NotificationItem): number => {
        const candidate = e?.payload?.timestamp ?? e?.timestamp ?? e?.receivedAt ?? e?.start_time;
        if (!candidate && candidate !== 0) return 0;
        const num = Number(candidate) || 0;
        return num < 10000000000 ? num * 1000 : num;
    };

    // 1. Pruning Effect
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

                const next = Object.fromEntries(kept);
                const seq = seqCountersRef.current;
                for (const k of Object.keys(seq)) {
                    if (!next[k]) delete seq[k];
                }

                return next;
            });
        }, PRUNE_INTERVAL_MS);

        return () => clearInterval(id);
    }, []);

    // 2. Append new notifications
    useEffect(() => {
        if (!notifications || notifications.length === 0) return;

        setExecutionTimelines(prev => {
            const next = { ...prev };
            const seq = seqCountersRef.current;

            for (const n of notifications) {
                if (!n || !n.id) continue;

                const execId = n.payload?.execution_id || n.execution_id;
                const key = execId || `notification:${n.id}`;

                const arr = next[key] ? [...next[key]] : [];
                const exists = arr.some(e => e.id === n.id);

                if (!exists) {
                    seq[key] = (seq[key] || 0) + 1;
                    // Fix for 'any' type: explicit casting or extending type if needed.
                    // Here we attach __seq for internal ordering if needed, but NotificationItem doesn't have it.
                    // We can cast to a local intersection type if we really need it, or just use the object.
                    // For now, let's assume we can attach it safely or ignore it if not strictly typed in the interface.
                    // To be type-safe, we should probably extend the type, but for now we'll cast to 'any' locally to avoid TS error,
                    // OR better, update NotificationItem type. Since we can't easily change the imported type right now without checking,
                    // we will use a type assertion that is safer than 'any'.
                    const enhanced = { ...n, __seq: seq[key] } as NotificationItem & { __seq?: number };
                    arr.push(enhanced);
                }
                next[key] = arr;
            }
            return next;
        });
    }, [notifications]);

    // 3. Fetch Timeline Function
    const fetchExecutionTimeline = useCallback(async (executionId: string, force = false) => {
        if (!executionId) return [];

        const local = executionTimelines[executionId];
        if (!force && local && local.length > 0) {
            return local;
        }

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

            setExecutionTimelines(prev => {
                const existing = prev[executionId] || [];
                const byId = new Map<string, NotificationItem>();

                for (const e of events) {
                    if (e.id) byId.set(e.id, e);
                    else {
                        const key = `fetched:${Math.random().toString(36).slice(2, 9)}`;
                        byId.set(key, e);
                    }
                }

                for (const e of existing) {
                    if (e.id) {
                        if (byId.has(e.id)) {
                            const server = byId.get(e.id)!;
                            const merged = { ...e, ...server };
                            // eslint-disable-next-line @typescript-eslint/no-explicit-any
                            if ((e as any).__seq && !(merged as any).__seq) (merged as any).__seq = (e as any).__seq;
                            byId.set(e.id, merged);
                        } else {
                            byId.set(e.id, e);
                        }
                    } else {
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        const key = `local:seq:${(e as any).__seq || Math.random().toString(36).slice(2, 9)}`;
                        if (!Array.from(byId.values()).some(v => v === e)) {
                            byId.set(key, e);
                        }
                    }
                }

                const merged = Array.from(byId.values()).sort((a, b) => normalizeEventTs(a) - normalizeEventTs(b));
                return { ...prev, [executionId]: merged };
            });

            return events;
        } catch (e) {
            console.error('fetchExecutionTimeline failed', e);
        }

        setExecutionTimelines(prev => ({ ...prev, [executionId]: prev[executionId] || [] }));
        return [];
    }, [API_BASE, executionTimelines]);

    return { executionTimelines, fetchExecutionTimeline };
};
