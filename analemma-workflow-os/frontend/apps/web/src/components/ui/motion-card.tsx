import React from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { Card } from '@/components/ui/card';

interface MotionCardProps extends Omit<React.ComponentProps<typeof Card>, 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart' | 'onAnimationEnd'> {
    isActive?: boolean;
    children: React.ReactNode;
}

export const MotionCard = React.forwardRef<HTMLDivElement, MotionCardProps>(
    ({ className, isActive, children, ...props }, ref) => {
        return (
            <motion.div
                ref={ref}
                className={cn("rounded-xl overflow-hidden bg-card text-card-foreground shadow-sm", className)}
                animate={{
                    borderColor: isActive ? "hsl(var(--primary))" : "hsl(var(--border))",
                    boxShadow: isActive ? "0 0 15px rgba(56, 189, 248, 0.15)" : "none", // Sky-400 equivalent-ish
                }}
                transition={{ duration: 0.4, ease: "easeInOut" }}
                // @ts-ignore - passing props to motion.div compatible way
                {...props}
            >
                {/* Breathing Border Effect Overlay */}
                {isActive && (
                    <motion.div
                        className="absolute inset-0 rounded-xl pointer-events-none border-2 border-primary"
                        animate={{ opacity: [0.3, 0.6, 0.3] }}
                        transition={{
                            duration: 2.5,
                            repeat: Infinity,
                            ease: "easeInOut"
                        }}
                    />
                )}
                {children}
            </motion.div>
        );
    }
);

MotionCard.displayName = 'MotionCard';
