import { useState, useRef, useEffect } from 'react';

export function useSmoothTaskUpdates(initialData: any) {
    const [displayData, setDisplayData] = useState(initialData);
    const lastTargetData = useRef(initialData);
    const animationFrame = useRef<number>();

    // 타겟 데이터가 변경되면 보간 애니메이션 시작
    useEffect(() => {
        if (!initialData) return;
        smoothUpdate(initialData);
    }, [initialData]);

    const smoothUpdate = (newData: any) => {
        if (!newData) return;

        lastTargetData.current = newData;

        const step = () => {
            setDisplayData((prev: any) => {
                if (!prev) return newData;

                const next = { ...prev };
                let isAnimating = false;

                // 1. 수치 데이터 보간 (Lerp)
                // - progress_percentage: 진행률
                // - autonomy_rate: 자율도
                // - confidence_score: 신뢰도 (중첩 객체 내부 값은 별도 처리 필요할 수 있음)

                const numericKeys = ['progress_percentage', 'autonomy_rate'];

                numericKeys.forEach(key => {
                    if (prev[key] !== undefined && newData[key] !== undefined) {
                        const target = Number(newData[key]);
                        const current = Number(prev[key]);
                        const diff = target - current;

                        // 차이가 작으면 바로 완료 (Jitter 방지)
                        if (Math.abs(diff) > 0.5) {
                            // Lerp: 감속 계수 0.15 (낮을수록 부드러움)
                            next[key] = current + diff * 0.15;
                            isAnimating = true;
                        } else {
                            next[key] = target;
                        }
                    } else if (newData[key] !== undefined) {
                        next[key] = newData[key];
                    }
                });

                // confidence_score 처리 (중첩되거나 최상위일 수 있음)
                // business_metrics_calculator.py의 결과에 따라 구조가 다를 수 있으므로 체크
                if (newData.confidence_score !== undefined) {
                    const target = Number(newData.confidence_score);
                    const current = Number(prev.confidence_score || 0);
                    const diff = target - current;
                    if (Math.abs(diff) > 0.5) {
                        next.confidence_score = current + diff * 0.15;
                        isAnimating = true;
                    } else {
                        next.confidence_score = target;
                    }
                }

                // 2. 텍스트/상태값/나머지는 즉시 업데이트 (반응성)
                // 객체의 다른 모든 키 복사 (deep merge가 아니므로 주의, 얕은 복사 후 덮어쓰기)
                Object.keys(newData).forEach(key => {
                    if (!numericKeys.includes(key) && key !== 'confidence_score') {
                        // 깊은 비교가 아니므로, 객체나 배열은 참조가 바뀌면 업데이트
                        // 여기서는 단순성을 위해 덮어씁니다.
                        next[key] = newData[key];
                    }
                });

                if (isAnimating) {
                    animationFrame.current = requestAnimationFrame(step);
                }
                return next;
            });
        };

        if (animationFrame.current) cancelAnimationFrame(animationFrame.current);
        animationFrame.current = requestAnimationFrame(step);
    };

    return displayData;
}
