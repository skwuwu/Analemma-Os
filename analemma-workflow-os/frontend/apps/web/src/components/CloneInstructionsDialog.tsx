import { useState } from 'react';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from '@/components/ui/command';
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from '@/components/ui/popover';
import { Loader2, Sparkles, Copy, Check, ChevronsUpDown } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { WorkflowSummary } from '@/lib/types';

interface CloneInstructionsDialogProps {
    /** 다이얼로그 열림 상태 */
    open: boolean;
    /** 다이얼로그 닫기 핸들러 */
    onOpenChange: (open: boolean) => void;
    /** 사용 가능한 워크플로우 목록 */
    workflows: WorkflowSummary[];
    /** 복제 완료 핸들러 (선택된 소스 워크플로우 ID 전달) */
    onConfirm: (sourceWorkflowId: string | null) => void;
    /** 복제 진행 중 여부 */
    isCloning?: boolean;
    /** 새로 생성된 워크플로우 이름 (UI 표시용) */
    targetWorkflowName?: string;
    /** 현재 저장 중인 워크플로우 ID (목록에서 제외용) */
    targetWorkflowId?: string | null;
}

export function CloneInstructionsDialog({
    open,
    onOpenChange,
    workflows,
    onConfirm,
    isCloning = false,
    targetWorkflowName = '새 워크플로우',
    targetWorkflowId = null,
}: CloneInstructionsDialogProps) {
    const [enableClone, setEnableClone] = useState(false);
    const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
    const [comboOpen, setComboOpen] = useState(false);

    const handleConfirm = () => {
        if (enableClone && selectedSourceId) {
            onConfirm(selectedSourceId);
        } else {
            onConfirm(null);
        }
    };

    const handleClose = () => {
        // 다이얼로그 닫을 때 상태 초기화
        setEnableClone(false);
        setSelectedSourceId(null);
        onOpenChange(false);
    };

    // 복제 가능한 워크플로우만 필터링 (현재 워크플로우 및 ID 없는 항목 제외)
    const availableWorkflows = workflows.filter(
        (w) => w.name && w.workflowId && w.workflowId !== targetWorkflowId
    );

    const selectedWorkflow = availableWorkflows.find(w => w.workflowId === selectedSourceId);

    return (
        <Dialog open={open} onOpenChange={handleClose}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                        <Sparkles className="h-5 w-5 text-amber-500" />
                        워크플로우 저장 완료
                    </DialogTitle>
                    <DialogDescription>
                        <strong>{targetWorkflowName}</strong>이(가) 저장되었습니다.
                        기존 에이전트의 학습된 스타일을 적용하시겠습니까?
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-4">
                    {/* 복제 활성화 체크박스 */}
                    <div className="flex items-start space-x-3">
                        <Checkbox
                            id="enable-clone"
                            checked={enableClone}
                            onCheckedChange={(checked) => {
                                setEnableClone(checked === true);
                                if (!checked) setSelectedSourceId(null);
                            }}
                            disabled={availableWorkflows.length === 0}
                        />
                        <div className="grid gap-1.5 leading-none">
                            <Label
                                htmlFor="enable-clone"
                                className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70"
                            >
                                기존 에이전트의 스타일 상속
                            </Label>
                            <p className="text-xs text-muted-foreground">
                                이전에 학습된 응답 스타일과 규칙을 새 에이전트에 적용합니다.
                            </p>
                        </div>
                    </div>

                    {/* 소스 워크플로우 선택 (Combobox Searchable) */}
                    {enableClone && (
                        <div className="pl-6 space-y-2">
                            <Label className="text-sm font-medium">
                                스타일을 복제할 에이전트 선택
                            </Label>

                            <Popover open={comboOpen} onOpenChange={setComboOpen}>
                                <PopoverTrigger asChild>
                                    <Button
                                        variant="outline"
                                        role="combobox"
                                        aria-expanded={comboOpen}
                                        className="w-full justify-between font-normal"
                                    >
                                        {selectedSourceId ? (
                                            <div className="flex items-center gap-2 truncate">
                                                <Copy className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                                                <span className="truncate">{selectedWorkflow?.name}</span>
                                            </div>
                                        ) : (
                                            <span className="text-muted-foreground">에이전트를 선택하세요</span>
                                        )}
                                        <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                                    </Button>
                                </PopoverTrigger>
                                <PopoverContent className="w-[var(--radix-popover-trigger-width)] p-0" align="start">
                                    <Command>
                                        <CommandInput placeholder="에이전트 검색..." />
                                        <CommandList>
                                            <CommandEmpty>검색 결과가 없습니다.</CommandEmpty>
                                            <CommandGroup>
                                                {availableWorkflows.map((workflow) => (
                                                    <CommandItem
                                                        key={workflow.workflowId}
                                                        value={workflow.name}
                                                        onSelect={() => {
                                                            setSelectedSourceId(workflow.workflowId!);
                                                            setComboOpen(false);
                                                        }}
                                                    >
                                                        <Check
                                                            className={cn(
                                                                "mr-2 h-4 w-4",
                                                                selectedSourceId === workflow.workflowId ? "opacity-100" : "opacity-0"
                                                            )}
                                                        />
                                                        {workflow.name}
                                                    </CommandItem>
                                                ))}
                                            </CommandGroup>
                                        </CommandList>
                                    </Command>
                                </PopoverContent>
                            </Popover>

                            {availableWorkflows.length === 0 && (
                                <p className="text-xs text-muted-foreground italic">
                                    복제 가능한 기존 워크플로우가 없습니다.
                                </p>
                            )}
                        </div>
                    )}
                </div>

                <DialogFooter className="flex gap-2 sm:gap-0">
                    <Button variant="outline" onClick={handleClose} disabled={isCloning}>
                        건너뛰기
                    </Button>
                    <Button
                        onClick={handleConfirm}
                        disabled={isCloning || (enableClone && !selectedSourceId)}
                        className={cn(enableClone && selectedSourceId && "bg-amber-600 hover:bg-amber-700")}
                    >
                        {isCloning ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                복제 중...
                            </>
                        ) : enableClone && selectedSourceId ? (
                            '스타일 적용'
                        ) : (
                            '완료'
                        )}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export default CloneInstructionsDialog;
