/**
 * 지능형 지침 증류기 - 수정 확인 HUD
 * 보수적 접근: 5초 후 passive logging (null 처리)
 */

import React, { useState, useEffect } from 'react';

interface CorrectionHUDProps {
  originalText: string;
  correctedText: string;
  onConfirm: (shouldRemember: boolean | null) => void; // null 추가
  isVisible?: boolean;
}

export function CorrectionConfirmationHUD({ 
  originalText, 
  correctedText, 
  onConfirm,
  isVisible: initialVisible = true 
}: CorrectionHUDProps) {
  const [isVisible, setIsVisible] = useState(initialVisible);
  const [timeLeft, setTimeLeft] = useState(5);

  // 5초 카운트다운 및 자동 닫기
  useEffect(() => {
    if (!isVisible) return;

    const interval = setInterval(() => {
      setTimeLeft(prev => {
        if (prev <= 1) {
          // 시간 만료 시 passive logging (null로 처리)
          onConfirm(null);
          setIsVisible(false);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(interval);
  }, [isVisible, onConfirm]);

  // 컴포넌트 마운트 시 애니메이션 트리거
  useEffect(() => {
    if (initialVisible) {
      setIsVisible(true);
    }
  }, [initialVisible]);

  if (!isVisible) return null;

  const handleConfirm = (shouldRemember: boolean) => {
    onConfirm(shouldRemember);
    setIsVisible(false);
  };

  return (
    <div className="fixed bottom-4 right-4 bg-white shadow-lg rounded-lg p-4 max-w-sm border animate-slide-up z-50">
      {/* 헤더 */}
      <div className="flex items-center gap-2 mb-2">
        <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse"></div>
        <div className="text-sm font-medium text-gray-700">
          이 수정을 학습할까요?
        </div>
        <div className="ml-auto text-xs text-gray-400 bg-gray-100 px-2 py-1 rounded">
          {timeLeft}초
        </div>
      </div>
      
      {/* 수정 내용 미리보기 */}
      <div className="text-xs bg-gray-50 p-3 rounded mb-3 max-h-24 overflow-hidden border-l-2 border-gray-200">
        {/* 원본 텍스트 */}
        <div className="line-through text-gray-400 mb-2 leading-relaxed">
          <span className="text-red-400 mr-1">−</span>
          {originalText.slice(0, 80)}{originalText.length > 80 ? '...' : ''}
        </div>
        
        {/* 수정된 텍스트 */}
        <div className="text-green-600 font-medium leading-relaxed">
          <span className="text-green-500 mr-1">+</span>
          {correctedText.slice(0, 80)}{correctedText.length > 80 ? '...' : ''}
        </div>
      </div>
      
      {/* 액션 버튼 */}
      <div className="flex gap-2">
        <button 
          onClick={() => handleConfirm(true)}
          className="flex-1 px-3 py-2 bg-blue-500 text-white text-sm rounded hover:bg-blue-600 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-300"
        >
          <span className="mr-1">✓</span>
          학습하기
        </button>
        <button 
          onClick={() => handleConfirm(false)}
          className="flex-1 px-3 py-2 bg-gray-300 text-gray-700 text-sm rounded hover:bg-gray-400 transition-colors focus:outline-none focus:ring-2 focus:ring-gray-300"
        >
          <span className="mr-1">✗</span>
          일회성
        </button>
      </div>
      
      {/* 안내 메시지 */}
      <div className="text-xs text-gray-400 mt-2 text-center">
        {timeLeft}초 후 자동으로 닫힙니다
      </div>
      
      {/* 진행 바 */}
      <div className="mt-2 w-full bg-gray-200 rounded-full h-1">
        <div 
          className="bg-blue-500 h-1 rounded-full transition-all duration-1000 ease-linear"
          style={{ width: `${(timeLeft / 5) * 100}%` }}
        ></div>
      </div>
    </div>
  );
}

// 애니메이션 CSS (Tailwind 확장)
const styles = `
  @keyframes slide-up {
    from {
      transform: translateY(100%);
      opacity: 0;
    }
    to {
      transform: translateY(0);
      opacity: 1;
    }
  }
  
  .animate-slide-up {
    animation: slide-up 0.3s ease-out;
  }
`;

// 스타일 주입 (개발 환경에서만)
if (typeof document !== 'undefined') {
  const styleSheet = document.createElement('style');
  styleSheet.textContent = styles;
  document.head.appendChild(styleSheet);
}

export default CorrectionConfirmationHUD;