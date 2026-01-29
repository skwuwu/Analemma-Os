import { Node } from '@xyflow/react';

/**
 * Universal Node Factory Utility
 * 
 * 에이전트 워크플로우를 위한 표준화된 노드 생성 로직을 제공합니다.
 * Canvas 드롭, WelcomeDialog 스트리밍 생성, 채팅 기반 자동 생성 등에서 동일한 로직을 보장합니다.
 */

export const generateNodeId = (): string => {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return crypto.randomUUID();
    }
    return 'node-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
};

export interface NodeCreationParams {
    type: string;
    position?: { x: number; y: number };
    data?: any;
    blockId?: string;
}

/**
 * 표준 노드 객체를 생성합니다.
 */
export const createWorkflowNode = (params: NodeCreationParams): Node => {
    const { type, position = { x: 0, y: 0 }, data = {}, blockId } = params;

    // 타입별 기본 데이터 초기화 (data에 없는 경우만 fallback)
    const defaultData: Record<string, any> = {
        label: data.label || 'New Block',
    };

    // 노드별 특성 파라미터 초기화 (data 값 우선, 없으면 기본값)
    if (type === 'aiModel') {
        // Determine provider based on model if not explicitly set
        let provider = data.provider;
        let modelName = data.model || data.modelName || 'gpt-4';
        
        if (!provider) {
            if (modelName.includes('gemini') || data.modelType === 'gemini') {
                provider = 'google';
                modelName = 'gemini-2.0-flash-exp';
            } else if (modelName.includes('claude') || data.modelType === 'claude') {
                provider = 'anthropic';
                modelName = 'claude-3-5-sonnet-20241022';
            } else if (modelName.includes('gpt') || data.modelType === 'gpt4') {
                provider = 'openai';
                modelName = 'gpt-4';
            } else {
                provider = 'openai';
            }
        }
        
        defaultData.provider = provider;
        defaultData.model = data.modelType || data.model || modelName;
        defaultData.modelName = modelName;
        defaultData.temperature = data.temperature ?? 0.7;
        defaultData.max_tokens = data.max_tokens ?? 512;
    } else if (type === 'operator') {
        defaultData.operatorType = data.operatorType || 'function';
    } else if (type === 'trigger') {
        defaultData.triggerType = data.triggerType || 'request';
    } else if (type === 'control') {
        defaultData.controlType = data.controlType || 'loop';
    } else if (type === 'control_block') {
        defaultData.blockType = data.blockType || 'conditional';
        defaultData.branches = data.branches || [
            { id: 'branch_0', label: 'Branch 1' },
            { id: 'branch_1', label: 'Branch 2' },
        ];
        if (data.blockType === 'while') {
            defaultData.max_iterations = data.max_iterations || 10;
            defaultData.natural_condition = data.natural_condition || '';
        }
    }

    return {
        id: generateNodeId(),
        type,
        position,
        data: {
            ...defaultData, // 기본값 먼저
            ...data,        // data로 덮어쓰기 (Library에서 전달된 값 우선)
            blockId,        // blockId는 마지막에 추가
        },
    };
};
