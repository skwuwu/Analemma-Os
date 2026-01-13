/**
 * Plan Briefing Modal
 * 
 * 워크플로우 실행 전 미리보기를 보여주는 모달 컴포넌트입니다.
 * 실행 계획, 예상 결과물, 위험 분석을 표시합니다.
 */

import React, { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { 
  AlertTriangle, 
  CheckCircle2, 
  Clock, 
  ExternalLink, 
  Mail, 
  FileText, 
  Bell,
  Play,
  Edit,
  X,
  Info,
  Zap,
  Shield
} from 'lucide-react';
import type { PlanBriefing, PlanStep, DraftResult, RiskLevel } from '@/lib/types';

interface PlanBriefingModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  briefing: PlanBriefing | null;
  loading?: boolean;
  onConfirm: () => void;
  onEdit?: () => void;
  onCancel: () => void;
}

const RiskBadge: React.FC<{ level: RiskLevel }> = ({ level }) => {
  const config = {
    low: { variant: 'secondary' as const, icon: CheckCircle2, label: '낮음', className: 'bg-green-100 text-green-800' },
    medium: { variant: 'outline' as const, icon: AlertTriangle, label: '중간', className: 'bg-yellow-100 text-yellow-800' },
    high: { variant: 'destructive' as const, icon: AlertTriangle, label: '높음', className: 'bg-red-100 text-red-800' },
  };
  
  const { icon: Icon, label, className } = config[level];
  
  return (
    <Badge className={className}>
      <Icon className="w-3 h-3 mr-1" />
      {label}
    </Badge>
  );
};

const StepCard: React.FC<{ step: PlanStep }> = ({ step }) => {
  return (
    <Card className="mb-2">
      <CardHeader className="py-3 px-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="font-mono">
              {step.step_number}
            </Badge>
            <CardTitle className="text-sm font-medium">{step.node_name}</CardTitle>
          </div>
          <div className="flex items-center gap-2">
            <RiskBadge level={step.risk_level} />
            <Badge variant="secondary" className="text-xs">
              <Clock className="w-3 h-3 mr-1" />
              ~{step.estimated_duration_seconds}초
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="py-2 px-4">
        <p className="text-sm text-muted-foreground">{step.action_description}</p>
        
        {step.has_external_side_effect && (
          <div className="flex items-center gap-1 mt-2 text-xs text-orange-600">
            <ExternalLink className="w-3 h-3" />
            외부 연동: {step.external_systems.join(', ')}
          </div>
        )}
        
        {step.risk_description && (
          <div className="flex items-center gap-1 mt-1 text-xs text-yellow-600">
            <Info className="w-3 h-3" />
            {step.risk_description}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

const DraftResultCard: React.FC<{ draft: DraftResult }> = ({ draft }) => {
  const typeConfig: Record<string, { icon: React.ElementType; label: string }> = {
    email: { icon: Mail, label: '이메일' },
    document: { icon: FileText, label: '문서' },
    notification: { icon: Bell, label: '알림' },
    api_call: { icon: Zap, label: 'API 호출' },
    default: { icon: FileText, label: '결과물' },
  };
  
  const { icon: Icon, label } = typeConfig[draft.result_type] || typeConfig.default;
  
  return (
    <Card className="mb-2">
      <CardHeader className="py-3 px-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Icon className="w-4 h-4 text-muted-foreground" />
            <CardTitle className="text-sm font-medium">{draft.title}</CardTitle>
          </div>
          <Badge variant="outline">{label}</Badge>
        </div>
      </CardHeader>
      <CardContent className="py-2 px-4">
        <p className="text-sm text-muted-foreground whitespace-pre-wrap line-clamp-3">
          {draft.content_preview}
        </p>
        
        {draft.recipients && draft.recipients.length > 0 && (
          <div className="mt-2 text-xs text-muted-foreground">
            📧 수신자: {draft.recipients.join(', ')}
          </div>
        )}
        
        {draft.warnings.length > 0 && (
          <div className="mt-2 space-y-1">
            {draft.warnings.map((warning, i) => (
              <div key={i} className="text-xs text-yellow-600 flex items-center gap-1">
                <AlertTriangle className="w-3 h-3" />
                {warning}
              </div>
            ))}
          </div>
        )}
        
        {draft.requires_review && (
          <Badge className="mt-2 bg-blue-100 text-blue-800">
            <Shield className="w-3 h-3 mr-1" />
            검토 필요
          </Badge>
        )}
      </CardContent>
    </Card>
  );
};

export const PlanBriefingModal: React.FC<PlanBriefingModalProps> = ({
  open,
  onOpenChange,
  briefing,
  loading = false,
  onConfirm,
  onEdit,
  onCancel,
}) => {
  const [confirmLoading, setConfirmLoading] = useState(false);
  
  const handleConfirm = async () => {
    setConfirmLoading(true);
    try {
      await onConfirm();
    } finally {
      setConfirmLoading(false);
    }
  };
  
  const formatDuration = (seconds: number): string => {
    if (seconds < 60) return `${seconds}초`;
    const minutes = Math.floor(seconds / 60);
    const remaining = seconds % 60;
    return remaining > 0 ? `${minutes}분 ${remaining}초` : `${minutes}분`;
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            📋 실행 계획 미리보기
          </DialogTitle>
          {briefing && (
            <DialogDescription>
              {briefing.workflow_name}
            </DialogDescription>
          )}
        </DialogHeader>

        <ScrollArea className="flex-1 pr-4">
          {loading ? (
            <div className="space-y-4">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : briefing ? (
            <div className="space-y-4">
              {/* 요약 */}
              <Card>
                <CardContent className="pt-4">
                  <p className="text-sm">{briefing.summary}</p>
                  <div className="flex items-center gap-4 mt-3 text-sm text-muted-foreground">
                    <div className="flex items-center gap-1">
                      <Zap className="w-4 h-4" />
                      {briefing.total_steps}단계
                    </div>
                    <div className="flex items-center gap-1">
                      <Clock className="w-4 h-4" />
                      ~{formatDuration(briefing.estimated_total_duration_seconds)}
                    </div>
                    <div className="flex items-center gap-1">
                      전체 위험도: <RiskBadge level={briefing.overall_risk_level} />
                    </div>
                  </div>
                </CardContent>
              </Card>

              {/* 경고 */}
              {briefing.warnings.length > 0 && (
                <Alert variant="destructive">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertTitle>주의사항</AlertTitle>
                  <AlertDescription>
                    <ul className="list-disc list-inside mt-2 space-y-1">
                      {briefing.warnings.map((warning, i) => (
                        <li key={i} className="text-sm">{warning}</li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}

              <Accordion type="single" collapsible className="w-full">
                {/* 실행 단계 */}
                <AccordionItem value="steps">
                  <AccordionTrigger>
                    <div className="flex items-center gap-2">
                      <Play className="w-4 h-4" />
                      실행 단계 ({briefing.steps.length})
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    <div className="space-y-2 pt-2">
                      {briefing.steps.map((step) => (
                        <StepCard key={step.step_number} step={step} />
                      ))}
                    </div>
                  </AccordionContent>
                </AccordionItem>

                {/* 예상 결과물 */}
                {briefing.draft_results.length > 0 && (
                  <AccordionItem value="results">
                    <AccordionTrigger>
                      <div className="flex items-center gap-2">
                        <FileText className="w-4 h-4" />
                        예상 결과물 ({briefing.draft_results.length})
                      </div>
                    </AccordionTrigger>
                    <AccordionContent>
                      <div className="space-y-2 pt-2">
                        {briefing.draft_results.map((draft, i) => (
                          <DraftResultCard key={draft.result_id || i} draft={draft} />
                        ))}
                      </div>
                    </AccordionContent>
                  </AccordionItem>
                )}
              </Accordion>

              {/* 승인 필요 메시지 */}
              {briefing.requires_confirmation && briefing.confirmation_message && (
                <Alert>
                  <Shield className="h-4 w-4" />
                  <AlertTitle>승인 필요</AlertTitle>
                  <AlertDescription>
                    {briefing.confirmation_message}
                  </AlertDescription>
                </Alert>
              )}

              {/* 신뢰도 표시 */}
              <div className="text-xs text-muted-foreground text-right">
                예측 신뢰도: {Math.round(briefing.confidence_score * 100)}%
              </div>
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              미리보기를 생성할 수 없습니다.
            </div>
          )}
        </ScrollArea>

        <Separator className="my-2" />

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onCancel}>
            <X className="w-4 h-4 mr-2" />
            취소
          </Button>
          {onEdit && (
            <Button variant="outline" onClick={onEdit}>
              <Edit className="w-4 h-4 mr-2" />
              수정
            </Button>
          )}
          <Button 
            onClick={handleConfirm} 
            disabled={loading || confirmLoading || (briefing?.requires_confirmation && !briefing?.confirmation_token)}
          >
            {confirmLoading ? (
              <>로딩 중...</>
            ) : (
              <>
                <Play className="w-4 h-4 mr-2" />
                승인 및 실행
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default PlanBriefingModal;
