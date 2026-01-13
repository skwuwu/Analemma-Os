import React, { Suspense } from 'react';
import { Skeleton } from '@/components/ui/skeleton';

// Lazy-load heavy json viewer to avoid SSR issues
const ReactJson = React.lazy(() => import('@uiw/react-json-view'));

interface Props {
  src: unknown;
  collapsed?: number | boolean;
  className?: string;
  theme?: string;
}

const JsonViewer: React.FC<Props> = ({ src, collapsed = 1, className, theme }) => {
  const [isClient, setIsClient] = React.useState(false);

  React.useEffect(() => {
    setIsClient(true);
  }, []);

  const LoadingPlaceholder: React.FC = () => (
    <div className={`space-y-2 ${className ?? ''}`}>
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-4 w-1/2" />
      <Skeleton className="h-4 w-full" />
    </div>
  );

  if (!isClient) {
    return <LoadingPlaceholder />;
  }

  // ensure non-object values don't break the viewer
  const value = typeof src === 'object' && src !== null ? src : { raw: src };

  return (
    <Suspense fallback={<LoadingPlaceholder />}>
      <div className={`bg-muted p-4 rounded-md overflow-auto text-xs ${className ?? ''}`}>
        <ReactJson
          value={value}
          collapsed={collapsed}
          enableClipboard={true}
          displayDataTypes={false}
          displayObjectSize={false}
          theme={theme === 'dark' ? 'monokai' : 'rjv-default'}
          // make background transparent so it matches surrounding UI (dark mode friendly)
          style={{ backgroundColor: 'transparent', fontSize: '0.875rem' }}
        />
      </div>
    </Suspense>
  );
};

export default JsonViewer;
