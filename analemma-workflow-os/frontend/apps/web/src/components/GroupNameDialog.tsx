import { useState, useCallback } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Layers } from 'lucide-react';

interface GroupNameDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: (name: string) => void;
  nodeCount: number;
}

export function GroupNameDialog({
  open,
  onClose,
  onConfirm,
  nodeCount,
}: GroupNameDialogProps) {
  const [name, setName] = useState('');

  const handleClose = useCallback(() => {
    setName('');
    onClose();
  }, [onClose]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (name.trim()) {
      onConfirm(name.trim());
      handleClose();
    }
  };

  return (
    <Dialog open={open} onOpenChange={(val) => !val && handleClose()}>
      <DialogContent className="sm:max-w-[420px] overflow-hidden border-none shadow-2xl">
        <div className="absolute top-0 left-0 w-full h-1.5 bg-gradient-to-r from-purple-500 to-indigo-600" />

        <form onSubmit={onSubmit}>
          <DialogHeader className="pt-4">
            <div className="flex items-center gap-3 mb-1">
              <div className="p-2 bg-purple-100 rounded-lg">
                <Layers className="w-5 h-5 text-purple-600" />
              </div>
              <DialogTitle className="text-xl font-bold tracking-tight">
                서브그래프(Subgraph) 생성
              </DialogTitle>
            </div>
            <DialogDescription className="text-slate-500 leading-relaxed pt-2">
              선택한 <span className="font-bold text-purple-600">{nodeCount}개</span>의 노드를 하나의 논리적 모듈로 그룹화합니다.
              복잡한 워크플로우를 단순화하고 재사용성을 높일 수 있습니다.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-5 py-8">
            <div className="space-y-2.5">
              <Label
                htmlFor="groupName"
                className="text-[11px] font-bold uppercase tracking-wider text-slate-400 pl-1"
              >
                그룹 식별자 이름
              </Label>
              <Input
                id="groupName"
                placeholder="예: 데이터 전처리 엔진"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
                className="h-12 text-md border-slate-200 focus:ring-purple-500/20 focus:border-purple-500 transition-all placeholder:text-slate-300"
                autoComplete="off"
              />
              <p className="text-[10px] text-slate-400 pl-1 italic">
                * 서브그래프는 이후 'Block Library'에서 하나의 노드처럼 다시 사용할 수 있습니다.
              </p>
            </div>
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="ghost"
              onClick={handleClose}
              className="hover:bg-slate-100 text-slate-500"
            >
              취소
            </Button>
            <Button
              type="submit"
              disabled={!name.trim()}
              className="bg-purple-600 hover:bg-purple-700 text-white px-8 h-10 font-bold shadow-lg shadow-purple-200 active:scale-95 transition-all"
            >
              그룹 생성하기
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
