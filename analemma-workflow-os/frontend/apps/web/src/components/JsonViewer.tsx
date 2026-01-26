import React, { Suspense, useState, useEffect } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { useTheme } from 'next-themes';
import { cn } from '@/lib/utils';

// Lazy-load heavy json viewer to avoid SSR issues and improve initial bundle size
const ReactJson = React.lazy(() => import('@uiw/react-json-view')) as any;

interface Props {
  /** JSON data to display */
  src: any;
  /** Initial collapse level (default: 1) */
  collapsed?: number | boolean;
  /** Additional CSS classes */
  className?: string;
  /** Explicit theme override ('light' | 'dark'). If omitted, uses system theme. */
  theme?: string;
}

/**
 * JsonViewer: 에이전트의 복잡한 페이로드를 시각화하기 위한 고성능 JSON 뷰어입니다.
 * SSR 호환성 및 테마 동기화가 최적화되어 있습니다.
 */
const JsonViewer: React.FC<Props> = ({ src, collapsed = 1, className, theme: themeOverride }) => {
  const [isClient, setIsClient] = useState(false);
  const { theme: systemTheme, resolvedTheme } = useTheme();

  useEffect(() => {
    setIsClient(true);
  }, []);

  // 테마 결정 로직: props override -> resolvedTheme (next-themes) -> default
  const activeTheme = themeOverride || resolvedTheme || systemTheme || 'light';

  const LoadingPlaceholder: React.FC = () => (
    <div className={cn("space-y-2 py-2", className)}>
      <Skeleton className="h-4 w-[85%]" />
      <Skeleton className="h-4 w-[60%]" />
      <Skeleton className="h-4 w-[75%]" />
    </div>
  );

  if (!isClient) {
    return <LoadingPlaceholder />;
  }

  // 데이터 안정성 확보: null이나 원시 타입이 들어오더라도 렌더링이 깨지지 않게 래핑
  const value = typeof src === 'object' && src !== null ? src : { value: src };

  return (
    <Suspense fallback={<LoadingPlaceholder />}>
      <div
        className={cn(
          "relative rounded-xl overflow-hidden text-[13px] font-mono leading-relaxed transition-colors",
          activeTheme === 'dark' ? "bg-slate-900/50" : "bg-slate-50/80 border border-slate-100",
          className
        )}
      >
        <ReactJson
          value={value}
          collapsed={collapsed}
          enableClipboard={true}
          displayDataTypes={false}
          displayObjectSize={true}
          shortenTextAfterLength={100}
          // @uiw/react-json-view에서 지원하는 최적의 테마 조합
          theme={activeTheme === 'dark' ? 'monokai' : 'light'}
          style={{
            backgroundColor: 'transparent',
            padding: '1rem',
            fontFamily: 'inherit'
          }}
        />

        {/* 대용량 데이터 처리임을 알리는 미세한 인디케이터 (데이터가 100개 이상의 키를 가질 때) */}
        {Object.keys(value).length > 100 && (
          <div className="absolute top-2 right-2 px-1.5 py-0.5 bg-primary/10 text-primary text-[9px] font-bold rounded uppercase tracking-tighter">
            Large Payload Optimized
          </div>
        )}
      </div>
    </Suspense>
  );
};

export default JsonViewer;
