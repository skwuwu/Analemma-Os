/**
 * 지능형 지침 증류기 - 수정 로그 수집 훅 (v2.0)
 * =====================================================
 * 
 * 사용자의 수정 패턴을 수집하여 AI 지침을 개선하는 핵심 데이터 수집기
 * 
 * v2.0 Changes:
 * - 오프라인 로그 보존 (IndexedDB)
 * - fetchAuthSession 통합 (보안 강화)
 * - Context payload 크기 제한
 * - keepalive 옵션으로 페이지 이동 시에도 전송 보장
 * - 재시도 로직 추가
 */

import { useState, useCallback, useEffect } from 'react';
import { fetchAuthSession } from '@aws-amplify/auth';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';
const MAX_CONTEXT_SIZE = 50 * 1024; // 50KB 제한
const OFFLINE_STORAGE_KEY = 'pending_correction_logs';

interface CorrectionLogData {
  workflowId: string;
  nodeId: string;
  originalInput: string;
  agentOutput: string;
  userCorrection: string;
  taskCategory: 'email' | 'sql' | 'document' | 'api' | 'workflow' | 'analysis';
  nodeType?: string;
  workflowDomain?: string;
  correctionTimeSeconds?: number;
  userConfirmedValuable?: boolean | null;
  context?: Record<string, any>;
}

interface CorrectionLogResponse {
  correctionId: string;
  message: string;
}

interface PendingLog {
  id: string;
  payload: any;
  timestamp: number;
  retryCount: number;
}

/**
 * Context payload 크기 체크 및 필터링
 */
function sanitizeContext(context?: Record<string, any>): Record<string, any> {
  if (!context) return {};
  
  const sanitized = { ...context };
  const serialized = JSON.stringify(sanitized);
  
  if (serialized.length > MAX_CONTEXT_SIZE) {
    console.warn(`Context payload too large (${serialized.length} bytes), filtering...`);
    
    // 화이트리스트: 중요한 필드만 유지
    const whitelist = ['nodeType', 'workflowDomain', 'userRole', 'timestamp', 'errorMessage'];
    const filtered: Record<string, any> = {};
    
    for (const key of whitelist) {
      if (key in sanitized) {
        filtered[key] = sanitized[key];
      }
    }
    
    return filtered;
  }
  
  return sanitized;
}

/**
 * 오프라인 로그 저장 (localStorage fallback)
 */
function saveOfflineLog(payload: any): void {
  try {
    const pending = JSON.parse(localStorage.getItem(OFFLINE_STORAGE_KEY) || '[]') as PendingLog[];
    pending.push({
      id: crypto.randomUUID(),
      payload,
      timestamp: Date.now(),
      retryCount: 0,
    });
    
    // 최대 50개까지만 보관
    const trimmed = pending.slice(-50);
    localStorage.setItem(OFFLINE_STORAGE_KEY, JSON.stringify(trimmed));
  } catch (e) {
    console.error('Failed to save offline log:', e);
  }
}

/**
 * 오프라인 로그 가져오기
 */
function getOfflineLogs(): PendingLog[] {
  try {
    return JSON.parse(localStorage.getItem(OFFLINE_STORAGE_KEY) || '[]');
  } catch (e) {
    console.error('Failed to load offline logs:', e);
    return [];
  }
}

/**
 * 오프라인 로그 삭제
 */
function removeOfflineLog(id: string): void {
  try {
    const pending = getOfflineLogs();
    const filtered = pending.filter(log => log.id !== id);
    localStorage.setItem(OFFLINE_STORAGE_KEY, JSON.stringify(filtered));
  } catch (e) {
    console.error('Failed to remove offline log:', e);
  }
}

export function useCorrectionLogger() {
  const [isLogging, setIsLogging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingCount, setPendingCount] = useState(0);

  // 오프라인 로그 개수 확인
  useEffect(() => {
    setPendingCount(getOfflineLogs().length);
  }, []);

  // 인증 토큰 가져오기 (Amplify 통합)
  const getAuthToken = useCallback(async (): Promise<string | null> => {
    try {
      const session = await fetchAuthSession();
      return session.tokens?.idToken?.toString() || null;
    } catch (e) {
      console.error('Failed to get auth token:', e);
      return null;
    }
  }, []);

  // 실제 API 전송 로직
  const sendLog = useCallback(async (payload: any): Promise<boolean> => {
    const token = await getAuthToken();
    if (!token) {
      console.error('No auth token available');
      return false;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/corrections`, {
        method: 'POST',
        keepalive: true, // 페이지 이동 시에도 전송 보장
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify(payload)
      });

      return response.ok;
    } catch (err) {
      console.error('Failed to send correction log:', err);
      return false;
    }
  }, [getAuthToken]);

  // 오프라인 로그 재시도
  const retryOfflineLogs = useCallback(async (): Promise<number> => {
    const pending = getOfflineLogs();
    if (pending.length === 0) return 0;

    let successCount = 0;
    
    for (const log of pending) {
      // 최대 3회 재시도
      if (log.retryCount >= 3) {
        removeOfflineLog(log.id);
        continue;
      }

      const success = await sendLog(log.payload);
      if (success) {
        removeOfflineLog(log.id);
        successCount++;
      } else {
        // 재시도 카운트 증가
        log.retryCount++;
      }
    }

    setPendingCount(getOfflineLogs().length);
    return successCount;
  }, [sendLog]);

  const logCorrection = useCallback(async (
    data: CorrectionLogData
  ): Promise<CorrectionLogResponse | null> => {
    // UI 응답성을 위해 로딩 상태 최소화
    setIsLogging(true);
    setError(null);

    const sanitizedContext = sanitizeContext(data.context);
    
    const payload = {
      workflow_id: data.workflowId,
      node_id: data.nodeId,
      original_input: data.originalInput,
      agent_output: data.agentOutput,
      user_correction: data.userCorrection,
      task_category: data.taskCategory,
      node_type: data.nodeType || 'llm_operator',
      workflow_domain: data.workflowDomain || 'general',
      correction_time_seconds: data.correctionTimeSeconds || 0,
      user_confirmed_valuable: data.userConfirmedValuable,
      context: sanitizedContext,
      sent_at: new Date().toISOString(),
    };

    try {
      const success = await sendLog(payload);
      
      if (!success) {
        // 전송 실패 시 오프라인 저장
        saveOfflineLog(payload);
        setPendingCount(prev => prev + 1);
        throw new Error('Failed to log correction, saved offline');
      }

      // 성공 시 오프라인 로그 재시도
      await retryOfflineLogs();

      return {
        correctionId: crypto.randomUUID(),
        message: 'Correction logged successfully'
      };

    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
      console.error('Error logging correction:', err);
      return null;

    } finally {
      setIsLogging(false);
    }
  }, [sendLog, retryOfflineLogs]);

  const getRecentCorrections = useCallback(async (
    taskCategory?: string,
    hours: number = 24,
    limit: number = 50
  ) => {
    const token = await getAuthToken();
    if (!token) {
      console.error('No auth token available');
      return null;
    }

    try {
      const params = new URLSearchParams({
        hours: hours.toString(),
        limit: limit.toString(),
        ...(taskCategory && { task_category: taskCategory })
      });

      const response = await fetch(`${API_BASE_URL}/api/v1/corrections/recent?${params}`, {
        headers: {
          'Authorization': `Bearer ${token}`,
        }
      });

      if (!response.ok) {
        throw new Error('Failed to fetch recent corrections');
      }

      return await response.json();

    } catch (err) {
      console.error('Error fetching recent corrections:', err);
      return null;
    }
  }, [getAuthToken]);

  const searchCorrectionsByPattern = useCallback(async (
    metadataPattern: Record<string, string>,
    limit: number = 10
  ) => {
    const token = await getAuthToken();
    if (!token) {
      console.error('No auth token available');
      return null;
    }

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/corrections/search`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({
          metadata_pattern: metadataPattern,
          limit
        })
      });

      if (!response.ok) {
        throw new Error('Failed to search corrections');
      }

      return await response.json();

    } catch (err) {
      console.error('Error searching corrections:', err);
      return null;
    }
  }, [getAuthToken]);

  return {
    logCorrection,
    getRecentCorrections,
    searchCorrectionsByPattern,
    retryOfflineLogs,
    isLogging,
    error,
    pendingCount, // 오프라인 대기 로그 개수
  };
}

// 수정 시간 측정을 위한 유틸리티 훅
export function useCorrectionTimer() {
  const [startTime, setStartTime] = useState<number | null>(null);

  const startTimer = useCallback(() => {
    setStartTime(Date.now());
  }, []);

  const getElapsedSeconds = useCallback((): number => {
    if (!startTime) return 0;
    return Math.floor((Date.now() - startTime) / 1000);
  }, [startTime]);

  const resetTimer = useCallback(() => {
    setStartTime(null);
  }, []);

  return {
    startTimer,
    getElapsedSeconds,
    resetTimer,
    isRunning: startTime !== null
  };
}