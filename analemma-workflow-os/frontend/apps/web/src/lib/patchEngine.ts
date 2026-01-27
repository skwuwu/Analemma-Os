/**
 * Patch Engine Utility
 * 
 * JSON Patch 기반 혹은 커스텀 OP 기반의 스트리밍 데이터 업데이트를 처리합니다.
 * 워크플로우 디자인 어시스턴트의 스트리밍 출력을 최적화하기 위해 사용됩니다.
 */

export interface PatchItem {
    op: 'add' | 'update' | 'remove' | string;
    id: string;
    type?: 'node' | 'edge' | string;
    data?: any;
    changes?: any;
}

/**
 * 배열 데이터에 패치를 적용합니다. (Immutable Update)
 * @param currentItems 현재 아이템 배열
 * @param patch 적용할 패치 정보
 * @returns 패치가 적용된 새로운 배열
 */
export const applyWorkflowPatch = <T extends { id: string }>(
    currentItems: T[],
    patch: PatchItem
): T[] => {
    const { op, id, data, changes } = patch;
    const items = [...currentItems];
    const idx = items.findIndex(item => item.id === id);

    if (op === 'add' && data) {
        // 중복 방지: 이미 존재한다면 업데이트로 취급하거나 무시
        if (idx === -1) {
            items.push(data);
        } else {
            items[idx] = { ...items[idx], ...data };
        }
    } else if (op === 'update') {
        if (idx !== -1) {
            items[idx] = { ...items[idx], ...(changes ?? data ?? {}) };
        } else if (data) {
            // 존재하지 않는데 업데이트 요청이 온 경우 (순서 보장 안될 시), 데이터가 있다면 추가
            items.push(data);
        }
    } else if (op === 'remove') {
        if (idx !== -1) {
            items.splice(idx, 1);
        }
    }

    return items;
};
