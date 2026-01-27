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

    // 타입별 기본 데이터 병합
    const defaultData: Record<string, any> = {
        label: data.label || 'New Block',
    };

    // 노드별 특성 파라미터 초기화
    if (type === 'aiModel') {
        defaultData.provider = data.provider || 'openai';
        defaultData.model = data.model || 'gpt-4';
        defaultData.temperature = data.temperature ?? 0.7;
        defaultData.max_tokens = data.max_tokens ?? 512;
    } else if (type === 'operator') {
        defaultData.operatorType = data.operatorType || 'function';
    } else if (type === 'trigger') {
        defaultData.triggerType = data.triggerType || 'request';
    } else if (type === 'control') {
        defaultData.controlType = data.controlType || 'loop';
    }

    return {
        id: generateNodeId(),
        type,
        position,
        data: {
            ...defaultData,
            ...data,
            blockId,
        },
    };
};
