import { useState, useRef } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Loader2, Sparkles } from 'lucide-react';
import { toast } from 'sonner';
import { convertWorkflowFromBackendFormat } from '@/lib/workflowConverter';
import { streamDesignAssistant, resolveDesignAssistantEndpoint } from '@/lib/streamingFetch';
import { fetchAuthSession } from '@aws-amplify/auth';
// makeAuthenticatedRequest is retained for compatibility but WelcomeDialog uses streaming helper
import { makeAuthenticatedRequest } from '@/lib/api';

interface WelcomeDialogProps {
  open: boolean;
  onWorkflowGenerated: (workflow: { nodes: any[]; edges: any[] }) => void;
  onClose: () => void;
}

export const WelcomeDialog = ({ open, onWorkflowGenerated, onClose }: WelcomeDialogProps) => {
  const [input, setInput] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const abortCtrlRef = useRef<AbortController | null>(null);

  const handleGenerate = async () => {
    if (!input.trim() || isGenerating) return;

    setIsGenerating(true);

    try {
      // Resolve endpoint and auth like WorkflowChat
      const resolved = resolveDesignAssistantEndpoint();
      let token: string | null = null;
      if (resolved.requiresAuth) {
        try {
          const session = await fetchAuthSession();
          token = session.tokens?.idToken?.toString() || null;
        } catch (e) {
          console.error('Failed to get auth token for streaming:', e);
          throw new Error('Authentication required for this endpoint');
        }
      }

      // Prepare payload for creation
      const bodyPayload: Record<string, unknown> = { request: `워크플로우를 생성해주세요: ${input}` };

      // clear any previous abort controller
      abortCtrlRef.current = new AbortController();

      // For live streaming, accumulate nodes/edges and call onWorkflowGenerated incrementally
      const nodes: any[] = [];
      const edges: any[] = [];

      // Notify parent to clear canvas for fresh create
      onWorkflowGenerated({ nodes: [], edges: [] });

      await streamDesignAssistant(bodyPayload, {
        authToken: token,
        signal: abortCtrlRef.current.signal,
        onMessage: (obj) => {
          try {
            if (!obj || typeof obj !== 'object') return;

            // op-based patch handling
            if (obj.op) {
              const op = obj.op;
              const type = obj.type;
              if (type === 'node') {
                if (op === 'add' && obj.data) {
                  nodes.push(obj.data);
                } else if (op === 'update') {
                  const idx = nodes.findIndex(n => n.id === obj.id);
                  if (idx !== -1) nodes[idx] = { ...nodes[idx], ...(obj.changes ?? obj.data ?? {}) };
                } else if (op === 'remove') {
                  const idx = nodes.findIndex(n => n.id === obj.id);
                  if (idx !== -1) nodes.splice(idx, 1);
                }
              } else if (type === 'edge') {
                if (op === 'add' && obj.data) {
                  edges.push(obj.data);
                } else if (op === 'update') {
                  const idx = edges.findIndex(e => e.id === obj.id);
                  if (idx !== -1) edges[idx] = { ...edges[idx], ...(obj.changes ?? obj.data ?? {}) };
                } else if (op === 'remove') {
                  const idx = edges.findIndex(e => e.id === obj.id);
                  if (idx !== -1) edges.splice(idx, 1);
                }
              }
            } else if (obj.type === 'node' && obj.data) {
              nodes.push(obj.data);
            } else if (obj.type === 'edge' && obj.data) {
              edges.push(obj.data);
            } else if (obj.response && obj.response.tool_use) {
              // Non-streaming single JSON response path: convert and finish
              const wf = obj.response.tool_use.input?.workflow_json;
              if (wf && wf.nodes && wf.edges) {
                const frontendWorkflow = convertWorkflowFromBackendFormat(wf);
                onWorkflowGenerated(frontendWorkflow);
              }
            }

            // push incremental update to parent so canvas updates live
            onWorkflowGenerated({ nodes: [...nodes], edges: [...edges] });
          } catch (e) {
            console.error('stream onMessage error in WelcomeDialog:', e);
          }
        },
        onDone: () => {
          setIsGenerating(false);
          toast.success('Workflow generated successfully!');
          onClose();
        },
        onError: (e) => {
          setIsGenerating(false);
          const errMsg = e?.message || 'Streaming error';
          console.error('WelcomeDialog stream error:', e);
          toast.error(`Failed to generate workflow: ${errMsg}`);
        }
      });

    } catch (error) {
      console.error('Error generating workflow:', error);
      toast.error('Failed to generate workflow. Please try again.');
      setIsGenerating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && !isGenerating && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-xl">
            <Sparkles className="w-5 h-5 text-primary" />
            What would you like to automate?
          </DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground">
            Tell me about the workflow you need and I&apos;ll build a matching automation blueprint for you.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 pt-4">
          <p className="text-sm text-muted-foreground">
            Describe your automation needs and I'll generate a workflow for you.
          </p>
          <Input
            placeholder="e.g., Send a daily email summary of new leads..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={(e) => e.key === 'Enter' && handleGenerate()}
            disabled={isGenerating}
            autoFocus
          />
          <Button
            onClick={handleGenerate}
            disabled={!input.trim() || isGenerating}
            className="w-full"
          >
            {isGenerating ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Generating...
              </>
            ) : (
              'Generate Workflow'
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
};
