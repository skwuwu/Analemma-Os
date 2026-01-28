/**
 * Intelligent Instruction Distiller - Correction Confirmation HUD
 * Conservative approach: passive logging after 5 seconds (null handling)
 */

import React, { useState, useEffect, useCallback } from 'react';
import { X, Check, Brain, Clock, Pause, Play } from 'lucide-react';
import { cn } from '@/lib/utils';

interface CorrectionHUDProps {
  originalText: string;
  correctedText: string;
  onConfirm: (shouldRemember: boolean | null) => void;
  isVisible?: boolean;
}

/**
 * 텍스트 차이를 강조하기 위한 간단한 헬퍼 (글자 단위)
 */
function SimpleDiff({ original, corrected }: { original: string; corrected: string }) {
  // 실제 정교한 diff 알고리즘 대신, 시인성을 위해 줄바꿈 및 강조 처리
  return (
    <div className="space-y-2 font-sans text-[11px]">
      <div className="bg-red-50/50 p-2 rounded border border-red-100/50 relative overflow-hidden group">
        <div className="absolute top-0 left-0 w-1 h-full bg-red-400 opacity-50" />
        <div className="flex gap-2">
          <span className="text-red-500 font-bold shrink-0">−</span>
          <span className="text-red-700/70 whitespace-pre-wrap break-all line-through">
            {original}
          </span>
        </div>
      </div>
      <div className="bg-green-50/50 p-2 rounded border border-green-100/50 relative overflow-hidden group">
        <div className="absolute top-0 left-0 w-1 h-full bg-green-400 opacity-50" />
        <div className="flex gap-2">
          <span className="text-green-500 font-bold shrink-0">+</span>
          <span className="text-green-800 whitespace-pre-wrap break-all">
            {corrected}
          </span>
        </div>
      </div>
    </div>
  );
}

export function CorrectionConfirmationHUD({
  originalText,
  correctedText,
  onConfirm,
  isVisible: initialVisible = true
}: CorrectionHUDProps) {
  const [isVisible, setIsVisible] = useState(initialVisible);
  const [timeLeft, setTimeLeft] = useState(8); // 좀 더 넉넉하게 8초로 상향
  const [isPaused, setIsPaused] = useState(false);

  const TOTAL_TIME = 8;

  const handleAction = useCallback((shouldRemember: boolean | null) => {
    onConfirm(shouldRemember);
    setIsVisible(false);
  }, [onConfirm]);

  // 카운트다운 로직 (Pause 기능 포함)
  useEffect(() => {
    if (!isVisible || isPaused) return;

    const interval = setInterval(() => {
      setTimeLeft(prev => {
        if (prev <= 0.1) {
          handleAction(null);
          return 0;
        }
        return prev - 0.1;
      });
    }, 100);

    return () => clearInterval(interval);
  }, [isVisible, isPaused, handleAction]);

  useEffect(() => {
    if (initialVisible) {
      setIsVisible(true);
      setTimeLeft(TOTAL_TIME);
    }
  }, [initialVisible]);

  if (!isVisible) return null;

  return (
    <div
      className={cn(
        "fixed bottom-6 right-6 bg-white/95 backdrop-blur-md shadow-2xl rounded-xl p-5 w-80 sm:w-96 border border-blue-100 z-50 transition-all duration-300 transform animate-in fade-in slide-in-from-bottom-4",
        isPaused ? "ring-2 ring-blue-400/30" : ""
      )}
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
      role="alert"
      aria-live="polite"
      aria-labelledby="hud-title"
    >
      {/* 헤더 */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="p-1.5 bg-blue-100 rounded-lg">
            <Brain className="w-4 h-4 text-blue-600" />
          </div>
          <h3 id="hud-title" className="text-sm font-bold text-gray-800">
            Instruction Learning Proposal
          </h3>
        </div>
        <div className="flex items-center gap-2">
          {isPaused ? (
            <Badge variant="outline" className="text-[10px] h-5 bg-blue-50 text-blue-600 border-blue-200 flex items-center gap-1 px-1.5 font-normal">
              <Pause className="w-2.5 h-2.5" /> Paused
            </Badge>
          ) : (
            <div className="text-[10px] font-mono font-medium text-gray-400 bg-gray-50 border px-1.5 h-5 flex items-center rounded-md">
              {Math.ceil(timeLeft)}s
            </div>
          )}
          <button
            onClick={() => handleAction(null)}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <p className="text-xs text-gray-600 mb-3 leading-relaxed">
        Agent detected your correction. Would you like to train it with the same pattern in the future?
      </p>

      {/* 수정 내용 비교 뷰 */}
      <div className="max-h-48 overflow-y-auto pr-1 mb-4 custom-scrollbar">
        <SimpleDiff original={originalText} corrected={correctedText} />
      </div>

      {/* 액션 버튼 */}
      <div className="flex gap-2">
        <button
          onClick={() => handleAction(true)}
          className="flex-1 flex items-center justify-center gap-2 py-2.5 bg-blue-600 text-white text-xs font-bold rounded-lg hover:bg-blue-700 transition-all active:scale-95 shadow-lg shadow-blue-500/20"
        >
          <Check className="w-3.5 h-3.5" />
          Learn Permanently
        </button>
        <button
          onClick={() => handleAction(false)}
          className="px-4 py-2.5 bg-gray-100 text-gray-600 text-xs font-semibold rounded-lg hover:bg-gray-200 transition-all active:scale-95"
        >
          One-time Only
        </button>
      </div>

      {/* 진행 바 (타이머) */}
      <div className="mt-4 w-full bg-gray-100 rounded-full h-1 relative overflow-hidden">
        <div
          className={cn(
            "h-full rounded-full transition-all duration-100 ease-linear",
            isPaused ? "bg-blue-300" : "bg-blue-600"
          )}
          style={{ width: `${(timeLeft / TOTAL_TIME) * 100}%` }}
        />
      </div>
    </div>
  );
}

// Badge 컴포넌트 간이 구현 (UI 라이브러리 미참조 시 대비)
function Badge({ children, variant, className }: any) {
  return (
    <span className={cn(
      "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium",
      variant === "outline" ? "border" : "bg-gray-100",
      className
    )}>
      {children}
    </span>
  );
}

export default CorrectionConfirmationHUD;