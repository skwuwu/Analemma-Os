/**
 * useSmoothTaskUpdates Hook (v2.0)
 * ===================================
 * 
 * 수치 데이터에 대한 부드러운 보간 애니메이션을 제공합니다.
 * 
 * v2.0 Changes:
 * - setState 업데이터 함수에서 부수 효과 제거 (Pure function)
 * - 애니메이션 로직을 useEffect로 분리
 * - ref 기반 타겟 관리로 안정성 개선
 * - 메모리 누수 방지를 위한 cleanup 강화
 */

import { useState, useRef, useEffect } from 'react';

const LERP_FACTOR = 0.15; // 감속 계수 (낮을수록 부드러움)
const CONVERGENCE_THRESHOLD = 0.5; // 수렴 임계값 (Jitter 방지)
const NUMERIC_KEYS = ['progress_percentage', 'autonomy_rate', 'confidence_score'] as const;

export function useSmoothTaskUpdates(initialData: any) {
    const [displayData, setDisplayData] = useState(initialData);
    const targetDataRef = useRef(initialData);
    const animationFrameRef = useRef<number>();
    const isAnimatingRef = useRef(false);

    // Update target when initialData changes
    useEffect(() => {
        if (!initialData) return;
        targetDataRef.current = initialData;
        
        // Start animation if not already running
        if (!isAnimatingRef.current) {
            isAnimatingRef.current = true;
            animationFrameRef.current = requestAnimationFrame(animate);
        }
    }, [initialData]);

    // Animation loop (separate from React rendering)
    const animate = () => {
        const targetData = targetDataRef.current;
        if (!targetData) return;

        setDisplayData((prevData: any) => {
            if (!prevData) return targetData;

            const nextData = { ...prevData };
            let hasActiveAnimation = false;

            // 1. Interpolate numeric fields (Lerp)
            NUMERIC_KEYS.forEach(key => {
                if (targetData[key] !== undefined) {
                    const target = Number(targetData[key]);
                    const current = Number(prevData[key] ?? 0);
                    const diff = target - current;

                    if (Math.abs(diff) > CONVERGENCE_THRESHOLD) {
                        nextData[key] = current + diff * LERP_FACTOR;
                        hasActiveAnimation = true;
                    } else {
                        nextData[key] = target; // Snap to target
                    }
                } else if (prevData[key] !== undefined) {
                    nextData[key] = prevData[key]; // Keep existing value
                }
            });

            // 2. Immediately update non-numeric fields (reactivity)
            Object.keys(targetData).forEach(key => {
                if (!NUMERIC_KEYS.includes(key as any)) {
                    nextData[key] = targetData[key];
                }
            });

            return nextData;
        });

        // Continue animation if needed
        const targetData2 = targetDataRef.current;
        if (targetData2) {
            let stillAnimating = false;
            NUMERIC_KEYS.forEach(key => {
                if (targetData2[key] !== undefined) {
                    const current = displayData?.[key] ?? 0;
                    const target = Number(targetData2[key]);
                    if (Math.abs(target - current) > CONVERGENCE_THRESHOLD) {
                        stillAnimating = true;
                    }
                }
            });

            if (stillAnimating) {
                animationFrameRef.current = requestAnimationFrame(animate);
            } else {
                isAnimatingRef.current = false;
            }
        } else {
            isAnimatingRef.current = false;
        }
    };

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (animationFrameRef.current !== undefined) {
                cancelAnimationFrame(animationFrameRef.current);
            }
            isAnimatingRef.current = false;
        };
    }, []);

    return displayData;
}
