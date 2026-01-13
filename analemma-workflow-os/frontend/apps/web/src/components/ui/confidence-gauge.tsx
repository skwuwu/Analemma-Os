import { useEffect } from "react";
import { motion, useSpring, useMotionValue, useTransform } from "framer-motion";
import { cn } from "@/lib/utils";

interface ConfidenceGaugeProps {
    value: number; // 0 to 100
    label?: string;
    className?: string;
}

export const ConfidenceGauge = ({ value, label, className }: ConfidenceGaugeProps) => {
    const motionValue = useMotionValue(0);
    const springValue = useSpring(motionValue, {
        damping: 20,
        stiffness: 100,
        restDelta: 0.001
    });

    useEffect(() => {
        // 0-100 범위로 클램핑
        motionValue.set(Math.min(100, Math.max(0, value)));
    }, [value, motionValue]);

    // 색상 보간 (Red -> Yellow -> Green)
    const color = useTransform(
        springValue,
        [0, 50, 100],
        ["#ef4444", "#eab308", "#22c55e"]
    );

    return (
        <div className={cn("w-full space-y-1", className)}>
            {label && (
                <div className="flex justify-between items-end mb-1">
                    <span className="text-xs font-medium text-muted-foreground">{label}</span>
                    <span className="text-xs font-mono font-semibold">
                        {Math.round(value)}%
                    </span>
                </div>
            )}
            <div className="h-2 w-full bg-muted/50 rounded-full overflow-hidden">
                <motion.div
                    className="h-full rounded-full"
                    style={{
                        width: useTransform(springValue, (v) => `${v}%`),
                        backgroundColor: color
                    }}
                />
            </div>
        </div>
    );
};
