/**
 * 지능형 지침 증류기 - 수정 로그 수집 훅
 */

import { useState, useCallback } from 'react';

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

export function useCorrectionLogger() {
  const [isLogging, setIsLogging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const logCorrection = useCallback(async (
    data: CorrectionLogData
  ): Promise<CorrectionLogResponse | null> => {
    setIsLogging(true);
    setError(null);

    try {
      // API 엔드포인트 호출
      const response = await fetch('/api/v1/corrections', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${getAuthToken()}`, // JWT 토큰
          'X-User-Id': getCurrentUserId(), // 개발용 헤더
        },
        body: JSON.stringify({
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
          context: data.context || {}
        })
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.error || 'Failed to log correction');
      }

      const result: CorrectionLogResponse = await response.json();
      return result;

    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Unknown error';
      setError(errorMessage);
      console.error('Error logging correction:', err);
      return null;

    } finally {
      setIsLogging(false);
    }
  }, []);

  const getRecentCorrections = useCallback(async (
    taskCategory?: string,
    hours: number = 24,
    limit: number = 50
  ) => {
    try {
      const params = new URLSearchParams({
        hours: hours.toString(),
        limit: limit.toString(),
        ...(taskCategory && { task_category: taskCategory })
      });

      const response = await fetch(`/api/v1/corrections/recent?${params}`, {
        headers: {
          'Authorization': `Bearer ${getAuthToken()}`,
          'X-User-Id': getCurrentUserId(),
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
  }, []);

  const searchCorrectionsByPattern = useCallback(async (
    metadataPattern: Record<string, string>,
    limit: number = 10
  ) => {
    try {
      const response = await fetch('/api/v1/corrections/search', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${getAuthToken()}`,
          'X-User-Id': getCurrentUserId(),
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
  }, []);

  return {
    logCorrection,
    getRecentCorrections,
    searchCorrectionsByPattern,
    isLogging,
    error
  };
}

// 유틸리티 함수들
function getAuthToken(): string {
  // 실제 구현에서는 인증 상태에서 JWT 토큰 가져오기
  return localStorage.getItem('auth_token') || 'mock_token';
}

function getCurrentUserId(): string {
  // 실제 구현에서는 인증된 사용자 ID 가져오기
  return localStorage.getItem('user_id') || 'default_user';
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