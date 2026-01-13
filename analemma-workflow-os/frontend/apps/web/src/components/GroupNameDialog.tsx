import { useState } from 'react';
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

  const handleConfirm = () => {
    if (name.trim()) {
      onConfirm(name.trim());
      setName('');
      onClose();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && name.trim()) {
      handleConfirm();
    }
  };

  const handleClose = () => {
    setName('');
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-[400px]">
        <DialogHeader>
          <DialogTitle>서브그래프 생성</DialogTitle>
          <DialogDescription>
            선택한 {nodeCount}개의 노드를 하나의 서브그래프로 그룹화합니다.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="groupName">그룹 이름</Label>
            <Input
              id="groupName"
              placeholder="예: 데이터 전처리 파이프라인"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={handleKeyDown}
              autoFocus
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            취소
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={!name.trim()}
            className="bg-purple-600 hover:bg-purple-700"
          >
            그룹 생성
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
