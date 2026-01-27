/**
 * EmptyCanvasGuide: Empty Canvas State Guide Component
 * 
 * Provides Agentic Designer mode guidance when canvas is empty.
 */
import { useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { 
  Zap, 
  Sparkles, 
  Lightbulb,
  MessageSquare,
  Workflow
} from 'lucide-react';

interface EmptyCanvasGuideProps {
  onQuickStart?: (prompt: string, persona?: string, systemPrompt?: string) => void;
  className?: string;
}

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
      <div className="max-w-2xl w-full space-y-8">
        {/* Header */}
        <div className="text-center space-y-4">
          <div className="flex items-center justify-center gap-2">
            <Zap className="w-10 h-10 text-orange-500" />
            <h1 className="text-3xl font-bold">AI Designer</h1>
            <Badge variant="default" className="ml-2">
              Draft Mode
            </Badge>
          </div>
          <p className="text-muted-foreground text-xl">
            Canvas is empty. AI will quickly generate a workflow draft for you.
          </p>
        </div>

        {/* Feature Description - Centered */}
        <Card className="border-2">
          <CardHeader className="text-center">
            <CardTitle className="flex items-center justify-center gap-2 text-2xl">
              <Sparkles className="w-6 h-6 text-primary" />
              AI Designer Features
            </CardTitle>
            <CardDescription className="text-base mt-2">
              Describe in natural language and AI automatically generates complete workflows.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="flex flex-col items-center text-center gap-3 p-4">
                <MessageSquare className="w-8 h-8 text-blue-500" />
                <div>
                  <h4 className="font-semibold text-base mb-1">Natural Language Input</h4>
                  <p className="text-sm text-muted-foreground">
                    Describe your desired workflow
                  </p>
                </div>
              </div>
              <div className="flex flex-col items-center text-center gap-3 p-4">
                <Workflow className="w-8 h-8 text-green-500" />
                <div>
                  <h4 className="font-semibold text-base mb-1">Auto Generation</h4>
                  <p className="text-sm text-muted-foreground">
                    AI configures nodes and connections
                  </p>
                </div>
              </div>
              <div className="flex flex-col items-center text-center gap-3 p-4">
                <Lightbulb className="w-8 h-8 text-yellow-500" />
                <div>
                  <h4 className="font-semibold text-base mb-1">Collaborative Enhancement</h4>
                  <p className="text-sm text-muted-foreground">
                    Switches to Co-design mode after generation
                  </p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Custom Input */}
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-3">
              <label className="text-base font-semibold block text-center">Describe your workflow:</label>
              <div className="flex gap-2">
                <Input
                  value={customPrompt}
                  onChange={(e) => setCustomPrompt(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && handleCustomStart()}
                  placeholder="e.g., Create an order processing automation workflow"
                  className="flex-1 h-12 text-base"
                />
                <Button 
                  onClick={handleCustomStart}
                  disabled={!customPrompt.trim()}
                  className="gap-2 h-12 px-6"
                  size="lg"
                >
                  <Zap className="w-5 h-5" />
                  Generate
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Additional Guidance */}
        <div className="text-center">
          <p className="text-base text-muted-foreground">
            💡 Once generated, automatically switches to <strong>Co-design mode</strong> where you can 
            refine details collaboratively with AI.
          </p>
        </div>
      </div>
    </div>
  );
}

export default EmptyCanvasGuide;