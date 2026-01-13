/**
 * EmptyCanvasGuide: 빈 Canvas 상태 안내 컴포넌트
 * 
 * Canvas가 비어있을 때 사용자에게 Agentic Designer 모드 안내를 제공합니다.
 */
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { 
  Zap, 
  Sparkles, 
  ArrowRight, 
  Lightbulb,
  MessageSquare,
  Workflow
} from 'lucide-react';

interface EmptyCanvasGuideProps {
  onQuickStart?: (prompt: string, persona?: string, systemPrompt?: string) => void;
  className?: string;
}

const QUICK_START_EXAMPLES = [
  {
    title: "이메일 자동 응답",
    description: "고객 문의 이메일을 분류하고 자동 응답하는 워크플로우",
    prompt: "고객 이메일을 받아서 문의 유형을 분류하고, 각 유형에 맞는 자동 응답을 생성하는 워크플로우를 만들어줘",
    persona: "customer_service",
    systemPrompt: `당신은 고객 서비스 자동화 전문가입니다. 
이메일 처리, 문의 분류, 자동 응답 시스템에 특화된 워크플로우를 설계합니다.
- 고객 만족도를 최우선으로 고려
- 신속하고 정확한 응답 시스템 구축
- 에스컬레이션 프로세스 포함
- 감정 분석 및 우선순위 처리`
  },
  {
    title: "데이터 처리 파이프라인",
    description: "CSV 파일을 읽어서 데이터를 정제하고 분석하는 워크플로우",
    prompt: "CSV 파일을 업로드하면 데이터를 정제하고, 통계를 계산해서 리포트를 생성하는 워크플로우를 만들어줘",
    persona: "data_engineer",
    systemPrompt: `당신은 데이터 엔지니어링 전문가입니다.
데이터 파이프라인, ETL 프로세스, 데이터 품질 관리에 특화된 워크플로우를 설계합니다.
- 데이터 검증 및 정제 프로세스 강화
- 에러 핸들링 및 복구 메커니즘
- 성능 최적화 및 스케일링 고려
- 데이터 품질 모니터링 포함`
  },
  {
    title: "콘텐츠 생성 자동화",
    description: "주제를 입력하면 블로그 포스트를 자동 생성하는 워크플로우",
    prompt: "주제를 입력하면 관련 정보를 수집하고, SEO 최적화된 블로그 포스트를 자동으로 생성하는 워크플로우를 만들어줘",
    persona: "content_creator",
    systemPrompt: `당신은 콘텐츠 마케팅 전문가입니다.
SEO 최적화, 콘텐츠 생성, 마케팅 자동화에 특화된 워크플로우를 설계합니다.
- SEO 키워드 최적화
- 콘텐츠 품질 검증
- 다양한 플랫폼 배포 고려
- 성과 측정 및 분석 포함`
  },
  {
    title: "주문 처리 자동화",
    description: "주문 접수부터 배송까지 전체 프로세스를 자동화하는 워크플로우",
    prompt: "온라인 주문을 받아서 재고 확인, 결제 처리, 배송 준비까지 자동화하는 워크플로우를 만들어줘",
    persona: "ecommerce_manager",
    systemPrompt: `당신은 이커머스 운영 전문가입니다.
주문 처리, 재고 관리, 배송 자동화에 특화된 워크플로우를 설계합니다.
- 실시간 재고 관리
- 결제 보안 및 검증
- 배송 추적 및 알림
- 고객 커뮤니케이션 자동화`
  },
  {
    title: "소셜미디어 모니터링",
    description: "브랜드 멘션을 모니터링하고 자동으로 대응하는 워크플로우",
    prompt: "소셜미디어에서 브랜드 멘션을 실시간으로 모니터링하고, 감정 분석 후 적절한 대응을 하는 워크플로우를 만들어줘",
    persona: "social_media_manager",
    systemPrompt: `당신은 소셜미디어 마케팅 전문가입니다.
브랜드 모니터링, 감정 분석, 소셜 리스닝에 특화된 워크플로우를 설계합니다.
- 실시간 모니터링 및 알림
- 감정 분석 및 위기 감지
- 자동 응답 및 에스컬레이션
- 인플루언서 및 트렌드 분석`
  }
];

export function EmptyCanvasGuide({ onQuickStart, className }: EmptyCanvasGuideProps) {
  const [customPrompt, setCustomPrompt] = useState('');

  const handleQuickStart = (prompt: string, persona?: string, systemPrompt?: string) => {
    onQuickStart?.(prompt, persona, systemPrompt);
  };

  const handleCustomStart = () => {
    if (customPrompt.trim()) {
      onQuickStart?.(customPrompt.trim());
      setCustomPrompt('');
    }
  };

  return (
    <div className={`flex items-center justify-center h-full p-8 ${className}`}>
      <div className="max-w-2xl w-full space-y-6">
        {/* 헤더 */}
        <div className="text-center space-y-4">
          <div className="flex items-center justify-center gap-2">
            <Zap className="w-8 h-8 text-orange-500" />
            <h1 className="text-2xl font-bold">AI Designer</h1>
            <Badge variant="default" className="ml-2">
              초안 생성 모드
            </Badge>
          </div>
          <p className="text-muted-foreground text-lg">
            Canvas가 비어있습니다. AI가 워크플로우 초안을 빠르게 생성해드립니다.
          </p>
        </div>

        {/* 기능 설명 */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-primary" />
              AI Designer 기능
            </CardTitle>
            <CardDescription>
              자연어로 설명하면 AI가 완전한 워크플로우를 자동으로 생성합니다.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="flex items-start gap-3">
                <MessageSquare className="w-5 h-5 text-blue-500 mt-0.5" />
                <div>
                  <h4 className="font-medium text-sm">자연어 입력</h4>
                  <p className="text-xs text-muted-foreground">
                    원하는 워크플로우를 자연어로 설명
                  </p>
                </div>
              </div>
              <div className="flex items-start gap-3">
                <Workflow className="w-5 h-5 text-green-500 mt-0.5" />
                <div>
                  <h4 className="font-medium text-sm">자동 생성</h4>
                  <p className="text-xs text-muted-foreground">
                    AI가 노드와 연결을 자동으로 구성
                  </p>
                </div>
              </div>
              <div className="flex items-start gap-3">
                <Lightbulb className="w-5 h-5 text-yellow-500 mt-0.5" />
                <div>
                  <h4 className="font-medium text-sm">협업 개선</h4>
                  <p className="text-xs text-muted-foreground">
                    생성 후 Co-design 모드로 전환
                  </p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 빠른 시작 예제 */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">빠른 시작 예제</CardTitle>
            <CardDescription>
              아래 예제 중 하나를 선택하거나 직접 입력해보세요.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3">
              {QUICK_START_EXAMPLES.map((example, index) => (
                <div
                  key={index}
                  className="p-4 border rounded-lg hover:bg-muted/50 transition-colors cursor-pointer group"
                  onClick={() => handleQuickStart(example.prompt, example.persona, example.systemPrompt)}
                >
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <h4 className="font-medium text-sm group-hover:text-primary transition-colors">
                          {example.title}
                        </h4>
                        <Badge variant="outline" className="text-xs">
                          {example.persona?.replace('_', ' ')}
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">
                        {example.description}
                      </p>
                    </div>
                    <ArrowRight className="w-4 h-4 text-muted-foreground group-hover:text-primary transition-colors ml-3 mt-1" />
                  </div>
                </div>
              ))}
            </div>

            {/* 커스텀 입력 */}
            <div className="pt-4 border-t">
              <div className="space-y-3">
                <label className="text-sm font-medium">또는 직접 입력하세요:</label>
                <div className="flex gap-2">
                  <Input
                    value={customPrompt}
                    onChange={(e) => setCustomPrompt(e.target.value)}
                    onKeyPress={(e) => e.key === 'Enter' && handleCustomStart()}
                    placeholder="예: 주문 처리 자동화 워크플로우를 만들어줘"
                    className="flex-1"
                  />
                  <Button 
                    onClick={handleCustomStart}
                    disabled={!customPrompt.trim()}
                    className="gap-2"
                  >
                    <Zap className="w-4 h-4" />
                    생성
                  </Button>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 추가 안내 */}
        <div className="text-center">
          <p className="text-sm text-muted-foreground">
            💡 워크플로우가 생성되면 자동으로 <strong>Co-design 모드</strong>로 전환되어 
            AI와 함께 세부사항을 개선할 수 있습니다.
          </p>
        </div>
      </div>
    </div>
  );
}

export default EmptyCanvasGuide;