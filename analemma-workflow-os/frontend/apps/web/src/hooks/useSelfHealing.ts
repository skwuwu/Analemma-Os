/**
 * useSelfHealing Hook (v4.0)
 * ===========================
 * 
 * Self-Healing API 호출 및 상태 관리 훅.
 * WebSocket을 통해 실시간 Self-Healing 상태 업데이트를 수신합니다.
 * 
 * v4.0 Changes:
 * - optionsRef로 콜백 안정성 개선
 * - fetchAuthSession 통합 (Amplify)
 * - WebSocketManager 연동 구조 개선
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import { toast } from 'sonner';
import { fetchAuthSession } from '@aws-amplify/auth';
import { makeAuthenticatedRequest } from '@/lib/api';

// API Base URL
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

export type HealingStatus =
    | 'AUTO_HEALING_IN_PROGRESS'
    | 'AWAITING_MANUAL_HEALING'
    | 'HEALING_SUCCESS'
    | 'HEALING_FAILED'
    | null;

export interface HealingState {
    status: HealingStatus;
    errorType?: string;
    errorMessage?: string;
    suggestedFix?: string;
    healingCount: number;
    maxHealingAttempts: number;
    blockedReason?: string;
    lastUpdated?: string;
}

export interface UseSelfHealingOptions {
    executionArn: string;
    ownerId: string;
    onHealingComplete?: () => void;
    onHealingFailed?: (error: Error) => void;
}

export function useSelfHealing(options: UseSelfHealingOptions) {
    const { executionArn, ownerId } = options;
    
    // Keep callbacks stable with ref
    const optionsRef = useRef(options);
    useEffect(() => {
        optionsRef.current = options;
    });

    const [healingState, setHealingState] = useState<HealingState>({
        status: null,
        healingCount: 0,
        maxHealingAttempts: 3,
    });
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<Error | null>(null);

    // Self-Healing 상태 조회
    const fetchHealingStatus = useCallback(async () => {
        try {
            const response = await makeAuthenticatedRequest(
                `${API_BASE_URL}/executions/${encodeURIComponent(executionArn)}/healing-status`,
                {
                    method: 'GET',
                }
            );

            if (!response.ok) {
                throw new Error(`Failed to fetch healing status: ${response.status}`);
            }

            const data = await response.json();

            setHealingState({
                status: data.healing_status || null,
                errorType: data.error_type,
                errorMessage: data.error_message,
                suggestedFix: data.pending_suggested_fix || data.suggested_fix,
                healingCount: data._self_healing_count || 0,
                maxHealingAttempts: data.max_healing_attempts || 3,
                blockedReason: data.healing_blocked_reason,
                lastUpdated: data.updated_at,
            });

        } catch (err) {
            console.error('Failed to fetch healing status:', err);
            setError(err instanceof Error ? err : new Error('Unknown error'));
        }
    }, [executionArn]);

    // Self-Healing 승인 (수동 트리거)
    const approveHealing = useCallback(async (): Promise<void> => {
        setIsLoading(true);
        setError(null);

        try {
            const response = await makeAuthenticatedRequest(`${API_BASE_URL}/tasks/quick-fix`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    task_id: executionArn,
                    fix_type: 'self_healing',
                    payload: {
                        suggested_fix: healingState.suggestedFix,
                        manual_approval: true,
                    },
                    owner_id: ownerId,
                }),
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.message || `API error: ${response.status}`);
            }

            const result = await response.json();

            setHealingState(prev => ({
                ...prev,
                status: 'AUTO_HEALING_IN_PROGRESS',
                healingCount: prev.healingCount + 1,
            }));

            toast.success('Self-Healing이 시작되었습니다.');

            if (optionsRef.current.onHealingComplete) {
                // 일정 시간 후 완료 콜백 (실제로는 WebSocket으로 상태 업데이트 수신)
                setTimeout(optionsRef.current.onHealingComplete, 5000);
            }

            return result;

        } catch (err) {
            const error = err instanceof Error ? err : new Error('Unknown error');
            setError(error);

            if (optionsRef.current.onHealingFailed) {
                optionsRef.current.onHealingFailed(error);
            }

            toast.error(`Self-Healing 실패: ${error.message}`);
            throw error;

        } finally {
            setIsLoading(false);
        }
    }, [executionArn, ownerId, healingState.suggestedFix]);

    // Self-Healing 거부 (수동)
    const rejectHealing = useCallback(async (): Promise<void> => {
        try {
            await makeAuthenticatedRequest(
                `${API_BASE_URL}/executions/${encodeURIComponent(executionArn)}/healing-status`,
                {
                    method: 'PATCH',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        healing_status: 'HEALING_REJECTED',
                        rejected_by: ownerId,
                    }),
                }
            );

            setHealingState(prev => ({
                ...prev,
                status: null,
            }));

        } catch (err) {
            console.error('Failed to reject healing:', err);
        }
    }, [executionArn, ownerId]);

    // 재시도 트리거
    const triggerRetry = useCallback(async (): Promise<void> => {
        setIsLoading(true);

        try {
            const response = await makeAuthenticatedRequest(`${API_BASE_URL}/tasks/quick-fix`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    task_id: executionArn,
                    fix_type: 'retry',
                    owner_id: ownerId,
                }),
            });

            if (!response.ok) {
                throw new Error(`Retry failed: ${response.status}`);
            }

            toast.success('재시도가 시작되었습니다.');

        } catch (err) {
            toast.error('재시도 실패');
            throw err;

        } finally {
            setIsLoading(false);
        }
    }, [executionArn, ownerId]);

    // WebSocket 메시지 핸들러 (stable callback with optionsRef)
    const handleWebSocketMessage = useCallback((message: any) => {
        if (message.type === 'SelfHealingStatusUpdate' && message.executionArn === executionArn) {
            setHealingState(prev => ({
                ...prev,
                status: message.healing_status,
                suggestedFix: message.suggested_fix || prev.suggestedFix,
                healingCount: message.healing_count ?? prev.healingCount,
                blockedReason: message.reason || prev.blockedReason,
                lastUpdated: message.timestamp,
            }));

            // 토스트 알림
            if (message.message) {
                if (message.healing_status === 'AUTO_HEALING_IN_PROGRESS') {
                    toast.info(message.message);
                } else if (message.healing_status === 'HEALING_SUCCESS') {
                    toast.success(message.message);
                    if (optionsRef.current.onHealingComplete) {
                        optionsRef.current.onHealingComplete();
                    }
                } else if (message.healing_status === 'HEALING_FAILED') {
                    toast.error(message.message);
                    if (optionsRef.current.onHealingFailed) {
                        optionsRef.current.onHealingFailed(new Error(message.message));
                    }
                } else if (message.healing_status === 'AWAITING_MANUAL_HEALING') {
                    toast.warning(message.message);
                }
            }
        }
    }, [executionArn]);

    // 초기 상태 조회
    useEffect(() => {
        if (executionArn) {
            fetchHealingStatus();
        }
    }, [executionArn, fetchHealingStatus]);

    return {
        healingState,
        isLoading,
        error,
        approveHealing,
        rejectHealing,
        triggerRetry,
        fetchHealingStatus,
        handleWebSocketMessage,
    };
}

export default useSelfHealing;
