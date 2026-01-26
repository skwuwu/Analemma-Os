import React, { Component, ErrorInfo, ReactNode } from 'react';
import { AlertTriangle, RefreshCw, Home, ChevronRight, Bug } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface Props {
  children: ReactNode;
  /** 에러 발생 시 표시할 커스텀 UI */
  fallback?: ReactNode;
  /** 에러 상태를 초기화하고 다시 시도할 때 호출할 콜백 (Zustand 리셋 등) */
  onReset?: () => void;
  /** 피처 단위 바운더리인지 여부 (UI 스타일 조정용) */
  compact?: boolean;
  /** 에러 리포팅 시 포함할 컨텍스트 이름 */
  featureName?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
  isRecovering: boolean;
}

/**
 * ErrorBoundary 컴포넌트
 * Analemma-Os의 'Resilience'를 보장하기 위한 에러 격리 시스템입니다.
 */
class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      isRecovering: false
    };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.group(`🚨 Error caught in [${this.props.featureName || 'Global'}] Boundary`);
    console.error('Error:', error);
    console.error('Component Stack:', errorInfo.componentStack);
    console.groupEnd();

    this.setState({ errorInfo });

    // TODO: Sentry 연동 시 Execution ID 등을 함께 전송
    // Sentry.captureException(error, { 
    //   tags: { feature: this.props.featureName },
    //   extra: { componentStack: errorInfo.componentStack } 
    // });
  }

  handleReset = async (): Promise<void> => {
    this.setState({ isRecovering: true });

    try {
      // 주입된 리셋 로직 실행 (Zustand 스토어 초기화 등)
      if (this.props.onReset) {
        await this.props.onReset();
      }

      // 약간의 딜레이를 주어 리렌더링 안정성 확보
      setTimeout(() => {
        this.setState({
          hasError: false,
          error: null,
          errorInfo: null,
          isRecovering: false
        });
      }, 300);
    } catch (e) {
      console.error('Failed to reset error state:', e);
      this.setState({ isRecovering: false });
    }
  };

  handleReload = (): void => {
    window.location.reload();
  };

  handleGoHome = (): void => {
    window.location.href = '/';
  };

  render(): ReactNode {
    const { compact, children, fallback, featureName } = this.props;
    const { hasError, error, errorInfo, isRecovering } = this.state;

    if (hasError) {
      // 커스텀 fallback이 제공된 경우
      if (fallback) {
        return fallback;
      }

      // 콤팩트 모드 (Feature-level)
      if (compact) {
        return (
          <div className="flex flex-col items-center justify-center p-6 border-2 border-dashed border-destructive/20 rounded-xl bg-destructive/5 text-center space-y-3 animate-in fade-in zoom-in-95">
            <div className="p-2 bg-destructive/10 rounded-full">
              <Bug className="w-5 h-5 text-destructive" />
            </div>
            <div className="space-y-1">
              <h4 className="text-sm font-bold text-foreground">
                {featureName || '컴포넌트'} 로딩 오류
              </h4>
              <p className="text-xs text-muted-foreground max-w-[200px]">
                이 섹션에서 문제가 발생했습니다. 다른 기능은 계속 사용할 수 있습니다.
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={this.handleReset}
              disabled={isRecovering}
              className="h-8 gap-2 bg-background"
            >
              <RefreshCw className={cn("w-3 h-3", isRecovering && "animate-spin")} />
              다시 시도
            </Button>
          </div>
        );
      }

      // 기본 전역 에러 UI (Global-level) - 프리미엄 디자인
      return (
        <div className="min-h-screen flex items-center justify-center bg-background p-6 overflow-hidden relative">
          {/* 장식용 배경 요소 */}
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-primary/5 rounded-full blur-3xl -z-10" />
          <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-destructive/5 rounded-full blur-3xl -z-10" />

          <div className="max-w-lg w-full text-center space-y-8 animate-in slide-in-from-bottom-8 duration-500">
            <div className="relative mx-auto w-24 h-24">
              <div className="absolute inset-0 bg-destructive/20 rounded-3xl rotate-12 blur-xl animate-pulse" />
              <div className="relative w-full h-full rounded-2xl bg-destructive/10 border border-destructive/20 flex items-center justify-center shadow-2xl">
                <AlertTriangle className="w-10 h-10 text-destructive" />
              </div>
            </div>

            <div className="space-y-3">
              <h1 className="text-3xl font-extrabold tracking-tight text-foreground sm:text-4xl">
                시스템 로딩 임계치 초과
              </h1>
              <p className="text-lg text-muted-foreground leading-relaxed">
                에이전트 실행 중 예상치 못한 격절이 발생했습니다. <br className="hidden sm:block" />
                현재 상태를 복구하거나 시스템을 초기화해 주세요.
              </p>
            </div>

            {/* 에러 상세 (개발 모드 전용) */}
            {import.meta.env.DEV && error && (
              <div className="group relative bg-muted/50 backdrop-blur-sm border border-muted rounded-2xl p-5 text-left transition-all hover:bg-muted/80">
                <div className="flex items-center gap-2 mb-2 text-destructive font-bold text-xs uppercase tracking-widest">
                  <Bug className="w-3 h-3" /> Technical Trace
                </div>
                <p className="text-sm font-mono text-destructive/90 break-all bg-destructive/5 p-2 rounded border border-destructive/10">
                  {error.toString()}
                </p>
                {errorInfo && (
                  <div className="mt-4 max-h-40 overflow-auto custom-scrollbar">
                    <pre className="text-[10px] leading-relaxed text-muted-foreground/80 font-mono whitespace-pre-wrap">
                      {errorInfo.componentStack}
                    </pre>
                  </div>
                )}
              </div>
            )}

            <div className="flex flex-col sm:flex-row gap-4 justify-center pt-4">
              <Button
                variant="ghost"
                onClick={this.handleGoHome}
                className="gap-2 h-12 px-6 rounded-xl hover:bg-accent/50"
              >
                <Home className="w-5 h-5 text-muted-foreground" />
                홈으로 이동
              </Button>

              <Button
                onClick={this.handleReset}
                disabled={isRecovering}
                className="gap-2 h-12 px-8 rounded-xl bg-primary hover:bg-primary/90 shadow-xl shadow-primary/20 text-md font-bold"
              >
                <RefreshCw className={cn("w-5 h-5", isRecovering && "animate-spin")} />
                시스템 자동 복구 시도
              </Button>

              <Button
                variant="outline"
                onClick={this.handleReload}
                className="gap-2 h-12 px-6 rounded-xl border-muted-foreground/20 hover:bg-background/80"
              >
                페이지 강제 새로고침
              </Button>
            </div>

            <p className="text-sm text-muted-foreground/60 italic font-medium">
              Tip: 복구가 되지 않는다면 브라우저 캐시를 삭제 후 다시 시도해 주세요.
            </p>
          </div>
        </div>
      );
    }

    return children;
  }
}

export default ErrorBoundary;
