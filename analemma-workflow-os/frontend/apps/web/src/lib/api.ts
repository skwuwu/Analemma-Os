import { fetchAuthSession } from '@aws-amplify/auth';

// Token refresh state to prevent race conditions
let refreshPromise: Promise<string> | null = null;

const getIdToken = async (forceRefresh = false) => {
    try {
        // console.log(`🔑 Getting ID token (forceRefresh: ${forceRefresh})`);
        const session = await fetchAuthSession({ forceRefresh });

        const idToken = session.tokens?.idToken?.toString();

        if (!idToken) {
            console.error('❌ No ID token found in session:', session);
            throw new Error('Authentication required');
        }

        // console.log('✅ ID token retrieved successfully');
        return idToken;
    } catch (error) {
        console.error('❌ Failed to get ID token:', error);
        // 토큰 갱신 실패 시 더 구체적인 에러 메시지
        if (forceRefresh) {
            throw new Error('Token refresh failed. Please log in again.');
        }
        throw new Error('Authentication failed. Please log in again.');
    }
};

const getIdTokenWithRefresh = async (forceRefresh = false): Promise<string> => {
    // If already refreshing, wait for the existing refresh to complete
    if (refreshPromise && forceRefresh) {
        console.log('🔄 Token refresh already in progress, waiting...');
        return refreshPromise;
    }

    // If forcing refresh and no ongoing refresh, start a new one
    if (forceRefresh && !refreshPromise) {
        console.log('🔄 Starting token refresh...');
        refreshPromise = getIdToken(true).finally(() => {
            refreshPromise = null; // Clear the promise when done
        });
        return refreshPromise;
    }

    // Normal token retrieval
    return getIdToken(forceRefresh);
};

export const makeAuthenticatedRequest = async (url: string, options: RequestInit = {}) => {
    try {
        let idToken = await getIdTokenWithRefresh();

        let response = await fetch(url, {
            ...options,
            headers: {
                ...options.headers,
                Authorization: `Bearer ${idToken}`,
            },
        });

        // 400 에러 상세 로깅
        if (response.status === 400) {
            const errorText = await response.clone().text();
            console.error('❌ 400 Bad Request:', {
                url,
                method: options.method || 'GET',
                status: response.status,
                statusText: response.statusText,
                responseBody: errorText,
                headers: Object.fromEntries(response.headers.entries())
            });
        }

        // 401 오류 시 토큰 갱신 후 재시도
        if (response.status === 401) {
            console.log('401 error, refreshing token and retrying...');

            try {
                // 토큰 갱신 시도
                idToken = await getIdTokenWithRefresh(true);

                // 새 토큰으로 재시도
                response = await fetch(url, {
                    ...options,
                    headers: {
                        ...options.headers,
                        Authorization: `Bearer ${idToken}`,
                    },
                });

                // 재시도도 실패한 경우
                if (response.status === 401 || response.status === 403) {
                    console.error('Retry failed after refresh. Session invalid.');
                    throw new Error('Session expired. Please log in again.');
                }

            } catch (refreshError) {
                console.error('Failed to refresh token:', refreshError);
                throw new Error('Token refresh failed. Please log in again.');
            }
        }

        return response;
    } catch (error) {
        console.error('Request failed:', error);
        throw error;
    }
};

// Cognito sub(고유 사용자 ID) 추출 함수
export const getOwnerId = async (): Promise<string> => {
    try {
        const session = await fetchAuthSession();
        const ownerId = session.tokens?.idToken?.payload?.sub || '';
        return ownerId;
    } catch (error) {
        console.error('Failed to get owner ID:', error);
        return '';
    }
};

export const parseApiResponse = async <T>(response: Response): Promise<T> => {
    if (!response.ok) {
        throw new Error(`Request failed: ${response.status}`);
    }

    const rawText = await response.text();
    let parsed;
    try {
        parsed = JSON.parse(rawText);
    } catch {
        throw new Error('Invalid response format');
    }

    // Handle Lambda response format vs direct response
    const data: T = parsed.body ? JSON.parse(parsed.body) : parsed;
    return data;
};

// =============================================================================
// Task Metrics API (Bento Grid 전용)
// =============================================================================

export interface TaskMetricsDisplay {
    title: string;
    status_color: 'green' | 'yellow' | 'red' | 'blue' | 'gray';
    eta_text: string;
    status: string;
    status_label: string;
}

export interface TaskMetricsProgress {
    value: number;
    label: string;
    sub_text: string;
}

export interface TaskMetricsConfidence {
    value: number;
    level: 'High' | 'Medium' | 'Low';
    breakdown: {
        reflection: number;
        schema: number;
        alignment: number;
    };
}

export interface TaskMetricsAutonomy {
    value: number;
    display: string;
}

export interface TaskMetricsIntervention {
    count: number;
    summary: string;
    positive_count: number;
    negative_count: number;
    history: Array<{
        timestamp?: string;
        type: string;
        reason: string;
        node_id?: string;
    }>;
}

export interface TaskMetricsGridItems {
    progress: TaskMetricsProgress;
    confidence: TaskMetricsConfidence;
    autonomy: TaskMetricsAutonomy;
    intervention: TaskMetricsIntervention;
}

export interface TaskMetricsResponse {
    display: TaskMetricsDisplay;
    grid_items: TaskMetricsGridItems;
    last_updated: string;
}

/**
 * Task Metrics API 호출 (Bento Grid 전용)
 * 백엔드가 계산한 메트릭스를 1:1로 매핑할 수 있는 형태로 반환
 */
export const fetchTaskMetrics = async (taskId: string): Promise<TaskMetricsResponse> => {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
    const response = await makeAuthenticatedRequest(`${baseUrl}/tasks/${taskId}/metrics`);
    return parseApiResponse<TaskMetricsResponse>(response);
};

// =============================================================================
// Outcome Manager API (결과물 중심 UI)
// =============================================================================

export interface OutcomeItem {
    artifact_id: string;
    artifact_type: string;
    title: string;
    preview_text?: string | null;
    content_ref?: string | null;
    download_url?: string | null;
    is_final: boolean;
    version: number;
    created_at: string;
    logic_trace_id?: string | null;
    word_count?: number | null;
    file_size_bytes?: number | null;
}

export interface CollapsedHistory {
    summary: string;
    node_count: number;
    llm_call_count: number;
    total_duration_seconds?: number | null;
    key_decisions: string[];
    full_trace_available: boolean;
}

export interface OutcomesResponse {
    task_id: string;
    task_title: string;
    status: string;
    outcomes: OutcomeItem[];
    collapsed_history: CollapsedHistory;
    correction_applied: boolean;
    last_updated: string;
}

export interface ReasoningStep {
    step_id: string;
    timestamp: string;
    step_type: 'decision' | 'observation' | 'action' | 'reasoning';
    content: string;
    node_id?: string | null;
    confidence?: number | null;
}

export interface ReasoningPathResponse {
    artifact_id: string;
    artifact_title: string;
    reasoning_steps: ReasoningStep[];
    total_steps: number;
    total_duration_seconds?: number | null;
}

/**
 * 결과물 목록 조회 (Outcome-First)
 * 완성된 아티팩트를 먼저 보여주고, 히스토리는 축약형으로 제공
 */
export const fetchTaskOutcomes = async (taskId: string): Promise<OutcomesResponse> => {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
    const response = await makeAuthenticatedRequest(`${baseUrl}/tasks/${taskId}/outcomes`);
    return parseApiResponse<OutcomesResponse>(response);
};

/**
 * 특정 결과물의 상세 사고 과정 조회
 * "이 결과가 어떻게 나왔나요?" 버튼 클릭 시 호출
 */
export const fetchReasoningPath = async (taskId: string, artifactId: string): Promise<ReasoningPathResponse> => {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
    const response = await makeAuthenticatedRequest(`${baseUrl}/tasks/${taskId}/outcomes/${artifactId}/reasoning`);
    return parseApiResponse<ReasoningPathResponse>(response);
};

// =============================================================================
// Quick Fix API (동적 장애 복구)
// =============================================================================

export type QuickFixType = 'RETRY' | 'REDIRECT' | 'SELF_HEALING' | 'INPUT' | 'ESCALATE';

export interface QuickFixAction {
    fix_type: QuickFixType;
    label: string;
    action_id: string;
    context: Record<string, unknown>;
}

export interface ExecuteQuickFixRequest {
    action_id: string;
    execution_id: string;
    node_id?: string;
    context?: Record<string, unknown>;
}

export interface ExecuteQuickFixResponse {
    success: boolean;
    action_type: QuickFixType;
    message: string;
    new_execution_id?: string;
    redirect_url?: string;
}

/**
 * Quick Fix 액션 실행
 * 에러 발생 시 동적으로 생성된 복구 버튼 클릭 시 호출
 */
export const executeQuickFix = async (request: ExecuteQuickFixRequest): Promise<ExecuteQuickFixResponse> => {
    const baseUrl = import.meta.env.VITE_API_BASE_URL || '';
    const response = await makeAuthenticatedRequest(`${baseUrl}/tasks/quick-fix`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
    });
    return parseApiResponse<ExecuteQuickFixResponse>(response);
};

