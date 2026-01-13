import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { TaskDetail } from '@/lib/types';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';
import { Download, ExternalLink, FileText } from 'lucide-react';
import { useState, useEffect } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CheckpointTimeline } from '@/components/CheckpointTimeline';
import { useCheckpoints } from '@/hooks/useBriefingAndCheckpoints';

interface OutcomeManagerModalProps {
  isOpen: boolean;
  onClose: () => void;
  task: TaskDetail;
  initialArtifactId?: string;
}

export const OutcomeManagerModal = ({ isOpen, onClose, task, initialArtifactId }: OutcomeManagerModalProps) => {
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | undefined>(initialArtifactId);
  const { timeline, isLoading: loadingCheckpoints } = useCheckpoints({ executionId: task.execution_id });

  useEffect(() => {
    if (isOpen && initialArtifactId) {
      setSelectedArtifactId(initialArtifactId);
    } else if (isOpen && !selectedArtifactId && task.artifacts.length > 0) {
        setSelectedArtifactId(task.artifacts[0].artifact_id);
    }
  }, [isOpen, initialArtifactId, task.artifacts, selectedArtifactId]);

  const selectedArtifact = task.artifacts.find(a => a.artifact_id === selectedArtifactId);

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-4xl h-[80vh] flex flex-col p-0 bg-slate-950 border-slate-800 text-slate-100 sm:max-w-[900px]">
        <DialogHeader className="px-6 py-4 border-b border-slate-800">
          <DialogTitle>Outcome Manager</DialogTitle>
          <DialogDescription className="text-slate-400">
            {task.task_summary} - 결과물 및 히스토리
          </DialogDescription>
        </DialogHeader>
        
        <Tabs defaultValue="artifacts" className="flex-1 flex flex-col overflow-hidden">
            <div className="px-6 border-b border-slate-800 bg-slate-900/30">
                <TabsList className="bg-transparent h-12 p-0">
                    <TabsTrigger 
                        value="artifacts" 
                        className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-blue-500 data-[state=active]:bg-transparent data-[state=active]:text-blue-400 px-4"
                    >
                        Artifacts
                    </TabsTrigger>
                    <TabsTrigger 
                        value="history" 
                        className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-blue-500 data-[state=active]:bg-transparent data-[state=active]:text-blue-400 px-4"
                    >
                        History
                    </TabsTrigger>
                </TabsList>
            </div>

            <TabsContent value="artifacts" className="flex-1 flex overflow-hidden mt-0 data-[state=inactive]:hidden">
                {/* Sidebar List */}
                <div className="w-64 border-r border-slate-800 bg-slate-900/50 flex flex-col">
                    <div className="p-4 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                        Artifacts ({task.artifacts.length})
                    </div>
                    <ScrollArea className="flex-1">
                        <div className="px-2 space-y-1">
                            {task.artifacts.map(artifact => (
                                <button
                                    key={artifact.artifact_id}
                                    onClick={() => setSelectedArtifactId(artifact.artifact_id)}
                                    className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors flex items-center gap-2
                                        ${selectedArtifactId === artifact.artifact_id 
                                            ? 'bg-blue-600/20 text-blue-300' 
                                            : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'}
                                    `}
                                >
                                    <FileText className="w-4 h-4 shrink-0" />
                                    <span className="truncate">{artifact.title}</span>
                                </button>
                            ))}
                            {task.artifacts.length === 0 && (
                                <div className="px-3 py-2 text-sm text-slate-500">
                                    결과물이 없습니다.
                                </div>
                            )}
                        </div>
                    </ScrollArea>
                </div>

                {/* Main Content */}
                <div className="flex-1 flex flex-col bg-slate-950">
                    {selectedArtifact ? (
                        <>
                            <div className="p-6 border-b border-slate-800 flex justify-between items-start">
                                <div>
                                    <h3 className="text-lg font-semibold text-slate-100">{selectedArtifact.title}</h3>
                                    <p className="text-sm text-slate-400 mt-1">
                                        {selectedArtifact.artifact_type} • {selectedArtifact.created_at ? new Date(selectedArtifact.created_at).toLocaleString() : 'Unknown Date'}
                                    </p>
                                </div>
                                <div className="flex gap-2">
                                    <Button variant="outline" size="sm" className="border-slate-700 text-slate-300 hover:bg-slate-800">
                                        <Download className="w-4 h-4 mr-2" />
                                        Download
                                    </Button>
                                    <Button variant="outline" size="sm" className="border-slate-700 text-slate-300 hover:bg-slate-800">
                                        <ExternalLink className="w-4 h-4 mr-2" />
                                        Open
                                    </Button>
                                </div>
                            </div>
                            <div className="flex-1 p-6 overflow-auto flex items-center justify-center bg-slate-900/20">
                                {/* Content Preview Placeholder */}
                                {selectedArtifact.thumbnail_url ? (
                                    <img src={selectedArtifact.thumbnail_url} alt="Preview" className="max-w-full max-h-full object-contain shadow-lg rounded-lg border border-slate-800" />
                                ) : (
                                    <div className="text-center text-slate-500">
                                        <FileText className="w-16 h-16 mx-auto mb-4 opacity-20" />
                                        <p>Preview not available for this file type.</p>
                                    </div>
                                )}
                            </div>
                        </>
                    ) : (
                        <div className="flex-1 flex items-center justify-center text-slate-500">
                            <p>Select an artifact to view details</p>
                        </div>
                    )}
                </div>
            </TabsContent>

            <TabsContent value="history" className="flex-1 overflow-hidden mt-0 p-6 bg-slate-950 data-[state=inactive]:hidden">
                <ScrollArea className="h-full">
                    <CheckpointTimeline items={timeline} loading={loadingCheckpoints} />
                </ScrollArea>
            </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
};

