/**
 * SelfHealingPanel Component (v4.0)
 * ==================================
 * 
 * 에이전트의 자가 치유(Self-Healing) 프로세스를 가시화하고 제어권을 부여하는 패널입니다.
 * 'Glassbox UX' 원칙에 따라 에러의 원인과 해결책을 투명하게 공개합니다.
 */

import React, { useState, useMemo } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import {
    Loader2,
    Wand2,
    AlertTriangle,
    CheckCircle,
    XCircle,
    RefreshCw,
    Shield,
    Copy,
    LifeBuoy,
    Terminal,
    ChevronDown,
    ChevronUp,
    Zap,
    History
} from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import { motion, AnimatePresence } from 'framer-motion';
import JsonViewer from './JsonViewer';

type HealingStatus = 'AUTO_HEALING_IN_PROGRESS' | 'AWAITING_MANUAL_HEALING' | 'HEALING_SUCCESS' | 'HEALING_FAILED' | null;

interface SelfHealingPanelProps {
    executionArn: string;
    ownerId: string;
    healingStatus: HealingStatus;
    errorType?: string;
    errorMessage?: string;
    suggestedFix?: string;
    healingCount?: number;
    maxHealingAttempts?: number;
    blockedReason?: string;
    onApproveHealing?: () => Promise<void>;
    onRejectHealing?: () => void;
    onClose?: () => void;
}

// --- CONFIGURATION ---

const HEALING_UI_CONFIG: Record<string, {
    color: string;
    icon: any;
    title: string;
    description: string;
    animate?: string;
    bg: string;
    border: string;
    text: string;
}> = {
    AUTO_HEALING_IN_PROGRESS: {
        color: 'blue',
        icon: Loader2,
        title: 'Agent Self-Correction Active',
        description: '오류가 감지되었습니다. Gemini가 코드를 분석하고 수정을 제안 중입니다.',
        animate: 'animate-spin',
        bg: 'bg-blue-500/10',
        border: 'border-blue-500/30',
        text: 'text-blue-500'
    },
    HEALING_SUCCESS: {
        color: 'emerald',
        icon: CheckCircle,
        title: 'Automatic Healing Synchronized',
        description: '시스템 무결성이 복구되었습니다. 워크플로우를 즉시 재개합니다.',
        bg: 'bg-emerald-500/10',
        border: 'border-emerald-500/30',
        text: 'text-emerald-500'
    },
    HEALING_FAILED: {
        color: 'rose',
        icon: XCircle,
        title: 'Self-Correction Ceiling Reached',
        description: '지정된 복구 시도 횟수 내에 안전한 정합성을 확보하지 못했습니다.',
        bg: 'bg-rose-500/10',
        border: 'border-rose-500/30',
        text: 'text-rose-500'
    }
};

export const SelfHealingPanel: React.FC<SelfHealingPanelProps> = ({
    executionArn,
    ownerId,
    healingStatus,
    errorType,
    errorMessage,
    suggestedFix,
    healingCount = 0,
    maxHealingAttempts = 3,
    blockedReason,
    onApproveHealing,
    onRejectHealing,
    onClose,
}) => {
    const [isApproving, setIsApproving] = useState(false);
    const [showFullError, setShowFullError] = useState(false);

    const handleApprove = async () => {
        if (!onApproveHealing) return;
        setIsApproving(true);
        try {
            await onApproveHealing();
        } catch (error) {
            console.error('Approve healing error:', error);
        } finally {
            setIsApproving(false);
        }
    };

    const handleCopyLog = () => {
        const log = `[Error] ${errorType}\n[Message] ${errorMessage}\n[Suggested Fix] ${suggestedFix}`;
        navigator.clipboard.writeText(log);
        toast.info('상세 로그가 클립보드에 복사되었습니다.');
    };

    const isJsonFix = useMemo(() => {
        if (!suggestedFix) return false;
        const trimmed = suggestedFix.trim();
        return (trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'));
    }, [suggestedFix]);

    if (!healingStatus) return null;

    // --- RENDER HEALING ALERT (AUTO, SUCCESS, FAILED) ---
    if (healingStatus !== 'AWAITING_MANUAL_HEALING') {
        const config = HEALING_UI_CONFIG[healingStatus as keyof typeof HEALING_UI_CONFIG];
        if (!config) return null;

        return (
            <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className="w-full"
            >
                <Alert className={cn("border-2 rounded-2xl py-5 px-6", config.border, config.bg)}>
                    <div className="flex items-start gap-4">
                        <div className={cn("p-2 rounded-xl bg-white dark:bg-black/20 shadow-sm", config.text)}>
                            <config.icon className={cn("h-5 w-5", config.animate)} />
                        </div>
                        <div className="flex-1 space-y-1">
                            <AlertTitle className={cn("text-base font-black tracking-tight uppercase", config.text)}>
                                {config.title}
                            </AlertTitle>
                            <AlertDescription className="text-sm font-medium opacity-80 leading-relaxed">
                                {config.description}
                                {healingStatus === 'AUTO_HEALING_IN_PROGRESS' && (
                                    <span className="flex items-center gap-1.5 mt-2 text-[10px] font-black uppercase tracking-widest opacity-60">
                                        <History className="h-3 w-3" /> Attempt {healingCount + 1} / {maxHealingAttempts}
                                    </span>
                                )}
                            </AlertDescription>

                            {healingStatus === 'HEALING_FAILED' && (
                                <div className="flex gap-2 mt-4">
                                    <Button size="sm" variant="outline" onClick={handleCopyLog} className="h-8 border-rose-500/20 bg-white/50 text-rose-600 font-bold hover:bg-rose-500/10">
                                        <Copy className="mr-2 h-3.5 w-3.5" /> Copy Log
                                    </Button>
                                    <Button size="sm" variant="ghost" className="h-8 text-rose-500/60 font-bold hover:bg-rose-500/5">
                                        <LifeBuoy className="mr-2 h-3.5 w-3.5" /> Request Escalation
                                    </Button>
                                </div>
                            )}

                            {onClose && healingStatus === 'HEALING_SUCCESS' && (
                                <Button size="sm" variant="ghost" onClick={onClose} className="h-8 mt-4 text-emerald-600 font-black uppercase tracking-widest hover:bg-emerald-500/10">
                                    Dismiss Report
                                </Button>
                            )}
                        </div>
                    </div>
                </Alert>
            </motion.div>
        );
    }

    // --- RENDER MANUAL APPROVAL PANEL ---
    return (
        <motion.div
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            className={cn("transition-all duration-300", isApproving && "opacity-60 pointer-events-none scale-[0.99]")}
        >
            <Card className="border-2 border-amber-500/30 bg-amber-500/5 rounded-3xl overflow-hidden shadow-2xl">
                <CardHeader className="px-8 py-6 border-b border-amber-500/10 bg-amber-500/5">
                    <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                            <div className="p-2.5 bg-amber-500 text-white rounded-xl shadow-lg shadow-amber-500/20">
                                <AlertTriangle className="h-5 w-5" />
                            </div>
                            <div className="space-y-0.5">
                                <CardTitle className="text-amber-600 dark:text-amber-500 font-black tracking-tight">Manual Reasoning Required</CardTitle>
                                <CardDescription className="text-xs font-bold text-amber-500/60 uppercase tracking-widest">Semantic Breach Detected</CardDescription>
                            </div>
                        </div>
                        <Badge className="bg-amber-100 text-amber-700 border-none font-black text-[10px] tracking-tighter uppercase px-2.5">
                            High Fidelity Check
                        </Badge>
                    </div>
                </CardHeader>

                <CardContent className="p-8 space-y-6">
                    {/* Error Analysis Section */}
                    <div className="space-y-3">
                        <div className="flex items-center gap-2">
                            <Terminal className="h-4 w-4 text-slate-400" />
                            <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">Error Payload Signature</span>
                        </div>
                        <div className="rounded-2xl border border-rose-500/20 bg-rose-500/5 p-5 space-y-3 relative overflow-hidden">
                            <div className="absolute top-0 right-0 p-2 bg-rose-500/20 rounded-bl-xl text-rose-600 font-mono text-[9px] font-bold">
                                {errorType || 'RUNTIME_EXCEPTION'}
                            </div>
                            <p className={cn("text-xs font-bold text-rose-700 dark:text-rose-400 leading-relaxed", !showFullError && "line-clamp-2")}>
                                {errorMessage}
                            </p>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => setShowFullError(!showFullError)}
                                className="h-6 px-2 text-[9px] font-black uppercase text-rose-500/60 hover:bg-rose-500/10"
                            >
                                {showFullError ? <ChevronUp className="h-3 w-3 mr-1" /> : <ChevronDown className="h-3 w-3 mr-1" />}
                                {showFullError ? 'Collapse Trace' : 'Expand Trace'}
                            </Button>
                        </div>
                    </div>

                    {/* Proposed Reconstruction Section */}
                    {suggestedFix && (
                        <div className="space-y-3">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <Zap className="h-4 w-4 text-purple-500 fill-purple-500" />
                                    <span className="text-[10px] font-black uppercase tracking-widest text-purple-600">Gemini Neural Repair Proposal</span>
                                </div>
                                <Badge variant="outline" className="border-purple-200 text-purple-500 text-[9px] font-bold">RECON_MODE=AUTO</Badge>
                            </div>
                            <div className="rounded-2xl border-2 border-purple-500/20 bg-white dark:bg-black/40 overflow-hidden">
                                {isJsonFix ? (
                                    <JsonViewer src={JSON.parse(suggestedFix)} collapsed={1} className="p-4 bg-transparent border-none" />
                                ) : (
                                    <div className="p-5">
                                        <p className="text-sm font-medium text-slate-700 dark:text-purple-200 leading-relaxed italic">
                                            "{suggestedFix}"
                                        </p>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}

                    {/* Block Reasoning Policy */}
                    {blockedReason && (
                        <div className="flex items-start gap-3 p-4 rounded-xl bg-slate-900/5 border border-slate-200">
                            <Shield className="h-5 w-5 text-slate-400 mt-0.5 shrink-0" />
                            <div className="space-y-1">
                                <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">Bypass Prevention Policy</span>
                                <p className="text-xs font-bold text-slate-600 dark:text-slate-400">{blockedReason}</p>
                            </div>
                        </div>
                    )}

                    {/* Meta Info */}
                    <div className="flex items-center justify-between pt-4 border-t border-amber-500/10">
                        <div className="flex items-center gap-4">
                            <div className="flex flex-col">
                                <span className="text-[9px] font-black text-slate-400 uppercase tracking-tighter">Attempts Logged</span>
                                <span className="text-xs font-black text-slate-700">{healingCount} / {maxHealingAttempts}</span>
                            </div>
                            <div className="w-px h-6 bg-slate-200" />
                            <div className="flex flex-col">
                                <span className="text-[9px] font-black text-slate-400 uppercase tracking-tighter">Correlation ID</span>
                                <span className="text-xs font-mono font-bold text-slate-400">{executionArn.split(':').pop()?.substring(0, 12)}...</span>
                            </div>
                        </div>
                        <Badge variant="outline" className="h-6 border-emerald-500/20 text-emerald-600 font-mono text-[10px]">VERIFIED_ORIGIN=LLM</Badge>
                    </div>
                </CardContent>

                <CardFooter className="px-8 py-5 flex gap-3 bg-amber-500/5">
                    <Button
                        variant="ghost"
                        onClick={onRejectHealing}
                        disabled={isApproving}
                        className="h-11 px-6 font-bold text-xs text-slate-400 hover:text-rose-500 hover:bg-rose-50 transition-all rounded-xl"
                    >
                        Discard Proposal
                    </Button>
                    <Button
                        onClick={handleApprove}
                        disabled={isApproving}
                        className="flex-1 h-11 bg-gradient-to-r from-purple-600 to-amber-600 hover:from-purple-700 hover:to-amber-700 text-white font-black text-xs uppercase tracking-widest rounded-xl shadow-xl shadow-amber-600/20 transition-all active:scale-95"
                    >
                        {isApproving ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Reconstructing State...
                            </>
                        ) : (
                            <>
                                <Wand2 className="mr-2 h-4 w-4" />
                                Authorize Global Repair
                            </>
                        )}
                    </Button>
                </CardFooter>
            </Card>
        </motion.div>
    );
};

export default SelfHealingPanel;
