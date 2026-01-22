/**
 * SelfHealingPanel Component (v3.9)
 * ==================================
 * 
 * Self-Healing 상태를 표시하고 수동 승인 버튼을 제공합니다.
 * 
 * 표시 상태:
 * - AUTO_HEALING_IN_PROGRESS: "자동 복구 중..." 토스트
 * - AWAITING_MANUAL_HEALING: Gemini 제안 + [승인] 버튼
 * - HEALING_SUCCESS: 복구 완료 메시지
 * - HEALING_FAILED: 에스컬레이션 안내
 */

import React, { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from '@/components/ui/card';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Loader2, Wand2, AlertTriangle, CheckCircle, XCircle, RefreshCw, Shield } from 'lucide-react';
import { toast } from 'sonner';

interface SelfHealingPanelProps {
    executionArn: string;
    ownerId: string;
    healingStatus: 'AUTO_HEALING_IN_PROGRESS' | 'AWAITING_MANUAL_HEALING' | 'HEALING_SUCCESS' | 'HEALING_FAILED' | null;
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

    const handleApprove = async () => {
        if (!onApproveHealing) return;

        setIsApproving(true);
        try {
            await onApproveHealing();
            toast.success('Self-Healing이 승인되었습니다. 복구가 시작됩니다.');
        } catch (error) {
            toast.error('승인 처리 중 오류가 발생했습니다.');
            console.error('Approve healing error:', error);
        } finally {
            setIsApproving(false);
        }
    };

    const handleReject = () => {
        if (onRejectHealing) {
            onRejectHealing();
        }
        toast.info('Self-Healing이 취소되었습니다. 수동으로 문제를 해결해주세요.');
    };

    if (!healingStatus) return null;

    // 🔄 자동 복구 진행 중
    if (healingStatus === 'AUTO_HEALING_IN_PROGRESS') {
        return (
            <Alert className="border-blue-500/50 bg-blue-500/10">
                <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
                <AlertTitle className="text-blue-400">자동 복구 진행 중...</AlertTitle>
                <AlertDescription className="text-blue-300/80">
                    오류가 감지되었습니다. Gemini가 코드를 분석하고 자동으로 수정 중입니다.
                    <br />
                    <span className="text-xs text-blue-400/60 mt-1 block">
                        복구 시도: {healingCount + 1} / {maxHealingAttempts}
                    </span>
                </AlertDescription>
            </Alert>
        );
    }

    // ⏳ 수동 승인 대기 중
    if (healingStatus === 'AWAITING_MANUAL_HEALING') {
        return (
            <Card className="border-amber-500/50 bg-amber-500/5">
                <CardHeader className="pb-3">
                    <div className="flex items-center gap-2">
                        <AlertTriangle className="h-5 w-5 text-amber-500" />
                        <CardTitle className="text-amber-400">수동 승인 필요</CardTitle>
                        <Badge variant="outline" className="ml-auto border-amber-500/50 text-amber-400">
                            Semantic Error
                        </Badge>
                    </div>
                    <CardDescription className="text-amber-300/70">
                        자동 복구가 불가능한 오류입니다. 아래 제안을 검토하고 승인해주세요.
                    </CardDescription>
                </CardHeader>

                <CardContent className="space-y-4">
                    {/* 에러 정보 */}
                    <div className="space-y-2">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <XCircle className="h-4 w-4 text-red-500" />
                            <span className="font-medium text-red-400">{errorType || 'Unknown Error'}</span>
                        </div>
                        {errorMessage && (
                            <div className="bg-red-500/10 border border-red-500/20 rounded-md p-3 max-h-24 overflow-y-auto">
                                <code className="text-xs text-red-300 whitespace-pre-wrap break-all">
                                    {errorMessage}
                                </code>
                            </div>
                        )}
                    </div>

                    {/* 차단 사유 */}
                    {blockedReason && (
                        <div className="flex items-start gap-2 text-sm">
                            <Shield className="h-4 w-4 text-amber-500 mt-0.5" />
                            <div>
                                <span className="font-medium text-amber-400">차단 사유: </span>
                                <span className="text-amber-300/80">{blockedReason}</span>
                            </div>
                        </div>
                    )}

                    {/* Gemini 제안 */}
                    {suggestedFix && (
                        <div className="space-y-2">
                            <div className="flex items-center gap-2 text-sm">
                                <Wand2 className="h-4 w-4 text-purple-500" />
                                <span className="font-medium text-purple-400">Gemini 수정 제안</span>
                            </div>
                            <div className="bg-purple-500/10 border border-purple-500/20 rounded-md p-3">
                                <p className="text-sm text-purple-300/90 whitespace-pre-wrap">
                                    {suggestedFix}
                                </p>
                            </div>
                        </div>
                    )}

                    {/* 복구 시도 횟수 */}
                    <div className="flex items-center justify-between text-xs text-muted-foreground pt-2 border-t border-border/30">
                        <span>복구 시도: {healingCount} / {maxHealingAttempts}</span>
                        <span className="text-xs">
                            {executionArn.split(':').pop()}
                        </span>
                    </div>
                </CardContent>

                <CardFooter className="flex gap-2 pt-0">
                    <Button
                        onClick={handleApprove}
                        disabled={isApproving}
                        className="flex-1 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-700 hover:to-blue-700"
                    >
                        {isApproving ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                승인 중...
                            </>
                        ) : (
                            <>
                                <Wand2 className="mr-2 h-4 w-4" />
                                Self-Healing 승인
                            </>
                        )}
                    </Button>
                    <Button
                        variant="outline"
                        onClick={handleReject}
                        disabled={isApproving}
                    >
                        취소
                    </Button>
                </CardFooter>
            </Card>
        );
    }

    // ✅ 복구 성공
    if (healingStatus === 'HEALING_SUCCESS') {
        return (
            <Alert className="border-green-500/50 bg-green-500/10">
                <CheckCircle className="h-5 w-5 text-green-500" />
                <AlertTitle className="text-green-400">자동 복구 완료</AlertTitle>
                <AlertDescription className="text-green-300/80">
                    오류가 자동으로 수정되었습니다. 워크플로우가 정상적으로 재실행됩니다.
                    <br />
                    <span className="text-xs text-green-400/60 mt-1 block">
                        복구에 {healingCount}회 시도가 소요되었습니다.
                    </span>
                </AlertDescription>
                {onClose && (
                    <Button
                        variant="ghost"
                        size="sm"
                        onClick={onClose}
                        className="mt-2 text-green-400 hover:text-green-300"
                    >
                        닫기
                    </Button>
                )}
            </Alert>
        );
    }

    // ❌ 복구 실패
    if (healingStatus === 'HEALING_FAILED') {
        return (
            <Alert variant="destructive" className="border-red-500/50 bg-red-500/10">
                <XCircle className="h-5 w-5 text-red-500" />
                <AlertTitle className="text-red-400">자동 복구 실패</AlertTitle>
                <AlertDescription className="text-red-300/80">
                    최대 복구 시도({maxHealingAttempts}회)를 초과했습니다.
                    <br />
                    수동으로 문제를 해결하거나 관리자에게 에스컬레이션하세요.
                    <br />
                    {errorType && (
                        <span className="text-xs text-red-400/60 mt-1 block">
                            에러 타입: {errorType}
                        </span>
                    )}
                </AlertDescription>
                <div className="flex gap-2 mt-3">
                    <Button
                        variant="outline"
                        size="sm"
                        className="border-red-500/50 text-red-400 hover:bg-red-500/10"
                    >
                        <RefreshCw className="mr-2 h-4 w-4" />
                        수동 재시도
                    </Button>
                    {onClose && (
                        <Button
                            variant="ghost"
                            size="sm"
                            onClick={onClose}
                            className="text-red-400 hover:text-red-300"
                        >
                            닫기
                        </Button>
                    )}
                </div>
            </Alert>
        );
    }

    return null;
};

export default SelfHealingPanel;
