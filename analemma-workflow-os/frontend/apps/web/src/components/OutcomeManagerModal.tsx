import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { TaskDetail, ReasoningPathResponse, ReasoningStep } from '@/lib/types';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';
import { Download, ExternalLink, FileText, Lightbulb, Loader2, ChevronRight, Eye, Database, Code, Image as ImageIcon, CheckCircle2 } from 'lucide-react';
import { useState, useEffect, useMemo } from 'react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CheckpointTimeline } from '@/components/CheckpointTimeline';
import { useCheckpoints } from '@/hooks/useBriefingAndCheckpoints';
import { getReasoningPath } from '@/lib/taskApi';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { motion, AnimatePresence } from 'framer-motion';
import JsonViewer from '@/components/JsonViewer';

interface OutcomeManagerModalProps {
    isOpen: boolean;
    onClose: () => void;
    task: TaskDetail;
    initialArtifactId?: string;
}

// --- SUB-COMPONENTS ---

/**
 * 추론 단계 개별 항목 컴포넌트 (Timeline Connector & Semantic Logic)
 */
const ReasoningStepItem = ({ step, index, isLast }: { step: ReasoningStep, index: number, isLast: boolean }) => {
    const typeConfigs = {
        decision: { bg: 'bg-amber-500/5', border: 'border-amber-500/20', text: 'text-amber-400', label: 'Decision', icon: Lightbulb },
        action: { bg: 'bg-blue-500/5', border: 'border-blue-500/20', text: 'text-blue-400', label: 'Action', icon: ActivityIcon },
        observation: { bg: 'bg-emerald-500/5', border: 'border-emerald-500/20', text: 'text-emerald-400', label: 'Observation', icon: Eye },
        reasoning: { bg: 'bg-purple-500/5', border: 'border-purple-500/20', text: 'text-purple-400', label: 'Thought', icon: BrainIcon }
    };

    const config = typeConfigs[step.step_type as keyof typeof typeConfigs] || typeConfigs.reasoning;
    const Icon = config.icon;

    return (
        <div className="relative flex gap-6 group">
            {/* Timeline Line & Indicator */}
            <div className="flex flex-col items-center">
                <div className={cn(
                    "w-9 h-9 rounded-full bg-slate-900 border-2 flex items-center justify-center text-[11px] font-black z-10 transition-colors shadow-lg",
                    config.border,
                    config.text
                )}>
                    {index + 1}
                </div>
                {!isLast && <div className="w-0.5 h-full bg-slate-800 absolute top-9" />}
            </div>

            <div className={cn(
                "flex-1 mb-8 p-5 rounded-2xl border transition-all hover:bg-slate-800/20",
                config.bg,
                config.border
            )}>
                <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                        <Icon className={cn("w-3.5 h-3.5", config.text)} />
                        <span className={cn("text-[10px] font-black uppercase tracking-widest", config.text)}>
                            {config.label}
                        </span>
                    </div>
                    {step.confidence && (
                        <div className="px-2 py-0.5 rounded-full bg-slate-900 border border-slate-700">
                            <span className="text-[9px] text-slate-500 font-mono font-bold uppercase">
                                Confidence: {Math.round(step.confidence * 100)}%
                            </span>
                        </div>
                    )}
                </div>

                <p className="text-sm text-slate-200 leading-relaxed font-medium">{step.content}</p>

                <div className="mt-4 flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <time className="text-[10px] text-slate-500 font-mono">{new Date(step.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>
                        {step.node_id && (
                            <Badge variant="secondary" className="bg-slate-900 text-slate-400 border-slate-800 text-[9px] h-4">
                                {step.node_id}
                            </Badge>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

/**
 * Artifact Preview Component (Multi-format support)
 */
const ArtifactPreviewer = ({ artifact }: { artifact: any }) => {
    if (!artifact) return null;

    // Image Format
    if (artifact.artifact_type === 'image' || artifact.thumbnail_url) {
        return (
            <div className="flex-1 flex items-center justify-center bg-slate-900/40 rounded-3xl border border-slate-800 overflow-hidden">
                <img
                    src={artifact.thumbnail_url || artifact.download_url}
                    alt={artifact.title}
                    className="max-w-full max-h-full object-contain shadow-2xl"
                />
            </div>
        );
    }

    // JSON / Data Format
    const isJson = artifact.artifact_type === 'data' || (artifact.preview_content && (artifact.preview_content.trim().startsWith('{') || artifact.preview_content.trim().startsWith('[')));
    if (isJson && artifact.preview_content) {
        try {
            const jsonData = JSON.parse(artifact.preview_content);
            return (
                <div className="flex-1 rounded-3xl border border-slate-800 bg-slate-900 p-6 overflow-auto">
                    <JsonViewer src={jsonData} />
                </div>
            );
        } catch (e) {
            // Fallback to text if JSON parsing fails
        }
    }

    // Text / Markdown Format
    if (artifact.preview_content) {
        return (
            <div className="flex-1 rounded-3xl border border-slate-800 bg-slate-900 overflow-hidden flex flex-col">
                <div className="flex items-center justify-between px-6 py-3 border-b border-slate-800 bg-slate-900/50">
                    <div className="flex items-center gap-2">
                        <Code className="w-4 h-4 text-blue-400" />
                        <span className="text-[11px] font-bold uppercase tracking-widest text-slate-400">Content Preview</span>
                    </div>
                </div>
                <ScrollArea className="flex-1 p-6">
                    <pre className="text-xs font-mono text-slate-300 leading-relaxed whitespace-pre-wrap">
                        {artifact.preview_content}
                    </pre>
                </ScrollArea>
            </div>
        );
    }

    // Empty / Unknown
    return (
        <div className="flex-1 flex flex-col items-center justify-center text-slate-500 bg-slate-900/20 rounded-3xl border border-slate-800 border-dashed">
            <div className="w-16 h-16 rounded-full bg-slate-800/50 flex items-center justify-center mb-4">
                <FileText className="w-8 h-8 opacity-20" />
            </div>
            <h4 className="text-sm font-bold text-slate-400">Preview Not Available</h4>
            <p className="text-xs text-slate-500 mt-1">Please download the file to view its full content.</p>
        </div>
    );
};

// --- MAIN COMPONENT ---

export const OutcomeManagerModal = ({ isOpen, onClose, task, initialArtifactId }: OutcomeManagerModalProps) => {
    const [selectedArtifactId, setSelectedArtifactId] = useState<string | undefined>(initialArtifactId);
    const { timeline, isLoading: loadingCheckpoints } = useCheckpoints({ executionId: task.execution_id });

    // Reasoning Path state
    const [showReasoning, setShowReasoning] = useState(false);
    const [reasoningData, setReasoningData] = useState<ReasoningPathResponse | null>(null);
    const [loadingReasoning, setLoadingReasoning] = useState(false);
    const [reasoningError, setReasoningError] = useState<string | null>(null);

    useEffect(() => {
        if (isOpen) {
            if (initialArtifactId) {
                setSelectedArtifactId(initialArtifactId);
            } else if (!selectedArtifactId && task.artifacts.length > 0) {
                setSelectedArtifactId(task.artifacts[0].artifact_id);
            }
        }
    }, [isOpen, initialArtifactId, task.artifacts]);

    // Reset reasoning view when artifact changes
    useEffect(() => {
        setShowReasoning(false);
        setReasoningData(null);
        setReasoningError(null);
    }, [selectedArtifactId]);

    const selectedArtifact = useMemo(() =>
        task.artifacts.find(a => a.artifact_id === selectedArtifactId),
        [task.artifacts, selectedArtifactId]
    );

    const handleShowReasoning = async () => {
        if (!selectedArtifactId || !task.task_id) return;

        setLoadingReasoning(true);
        setReasoningError(null);

        try {
            const data = await getReasoningPath(task.task_id, selectedArtifactId);
            setReasoningData(data);
            setShowReasoning(true);
        } catch (error) {
            setReasoningError(error instanceof Error ? error.message : 'Failed to load reasoning path');
        } finally {
            setLoadingReasoning(false);
        }
    };

    return (
        <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
            <DialogContent className="max-w-6xl h-[85vh] flex flex-col p-0 bg-slate-950 border-none shadow-2xl rounded-3xl overflow-hidden text-slate-100 sm:max-w-[1000px]">
                {/* Header Branding Line */}
                <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-blue-600 via-purple-600 to-indigo-600 z-50" />

                <DialogHeader className="px-8 py-6 border-b border-slate-800 bg-slate-900/20 backdrop-blur-md">
                    <div className="flex items-center justify-between">
                        <div className="space-y-1">
                            <DialogTitle className="text-xl font-black tracking-tight flex items-center gap-3">
                                Outcome Manager
                                <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/5 text-emerald-400 text-[10px] uppercase tracking-widest font-bold">
                                    Verified Result
                                </Badge>
                            </DialogTitle>
                            <DialogDescription className="text-slate-400 font-medium">
                                {task.task_summary} • <span className="text-slate-500 text-xs">Run ID: {task.execution_id || 'LOCAL_RUN'}</span>
                            </DialogDescription>
                        </div>
                    </div>
                </DialogHeader>

                <Tabs defaultValue="artifacts" className="flex-1 flex flex-col overflow-hidden">
                    <div className="px-8 border-b border-slate-800 bg-slate-900/30">
                        <TabsList className="bg-transparent h-14 p-0 gap-8">
                            <TabsTrigger
                                value="artifacts"
                                className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-blue-500 data-[state=active]:bg-transparent data-[state=active]:text-blue-400 px-0 font-bold text-xs uppercase tracking-widest transition-all"
                            >
                                Result Artifacts
                            </TabsTrigger>
                            <TabsTrigger
                                value="history"
                                className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-blue-500 data-[state=active]:bg-transparent data-[state=active]:text-blue-400 px-0 font-bold text-xs uppercase tracking-widest transition-all"
                            >
                                Operational History
                            </TabsTrigger>
                        </TabsList>
                    </div>

                    <TabsContent value="artifacts" className="flex-1 flex overflow-hidden mt-0 data-[state=inactive]:hidden">
                        {/* Sidebar List */}
                        <div className="w-72 border-r border-slate-800 bg-slate-900/40 flex flex-col shadow-inner">
                            <div className="p-5 flex items-center justify-between">
                                <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest">
                                    Available Outputs ({task.artifacts.length})
                                </span>
                            </div>
                            <ScrollArea className="flex-1">
                                <div className="px-3 pb-6 space-y-1.5">
                                    {task.artifacts.map(artifact => (
                                        <button
                                            key={artifact.artifact_id}
                                            onClick={() => setSelectedArtifactId(artifact.artifact_id)}
                                            className={cn(
                                                "w-full text-left px-4 py-3.5 rounded-2xl text-sm transition-all flex items-center gap-3 group/item border",
                                                selectedArtifactId === artifact.artifact_id
                                                    ? 'bg-blue-600/10 border-blue-500/30 text-blue-300 shadow-lg shadow-blue-900/20'
                                                    : 'text-slate-400 border-transparent hover:bg-slate-800/50 hover:text-slate-200'
                                            )}
                                        >
                                            <div className={cn(
                                                "w-8 h-8 rounded-xl flex items-center justify-center shrink-0 transition-colors",
                                                selectedArtifactId === artifact.artifact_id ? "bg-blue-600/20 text-blue-400" : "bg-slate-800 text-slate-500 group-hover/item:text-slate-300"
                                            )}>
                                                {artifact.artifact_type === 'image' ? <ImageIcon className="w-4 h-4" /> :
                                                    artifact.artifact_type === 'data' ? <Database className="w-4 h-4" /> : <FileText className="w-4 h-4" />}
                                            </div>
                                            <div className="min-w-0 flex-1">
                                                <div className="font-bold text-xs truncate">{artifact.title}</div>
                                                <div className="text-[10px] opacity-50 font-mono tracking-tighter uppercase">{artifact.artifact_type}</div>
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            </ScrollArea>
                        </div>

                        {/* Main Content Area */}
                        <div className="flex-1 flex flex-col bg-slate-950 p-8">
                            {selectedArtifact ? (
                                <div className="h-full flex flex-col gap-6">
                                    <div className="flex justify-between items-start">
                                        <div className="space-y-1">
                                            <h3 className="text-2xl font-black text-slate-100 tracking-tight">{selectedArtifact.title}</h3>
                                            <div className="flex items-center gap-2">
                                                <Badge variant="secondary" className="bg-slate-900 text-slate-400 border-slate-800 text-[10px] px-2">
                                                    {selectedArtifact.artifact_type.toUpperCase()}
                                                </Badge>
                                                <span className="text-xs text-slate-500 font-medium">
                                                    Refined: {selectedArtifact.created_at ? new Date(selectedArtifact.created_at).toLocaleString() : 'N/A'}
                                                </span>
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            {showReasoning ? (
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    onClick={() => setShowReasoning(false)}
                                                    className="h-10 px-6 rounded-xl font-bold text-xs text-slate-400 hover:text-slate-100 hover:bg-slate-800/50"
                                                >
                                                    <ImageIcon className="w-4 h-4 mr-2" /> Show Result
                                                </Button>
                                            ) : (
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    className="h-10 px-6 rounded-xl font-bold text-xs border-slate-700 bg-slate-900/50 hover:bg-slate-800 text-amber-400 hover:text-amber-300 transition-all shadow-lg shadow-amber-900/5"
                                                    onClick={handleShowReasoning}
                                                    disabled={loadingReasoning}
                                                >
                                                    {loadingReasoning ? (
                                                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                                                    ) : (
                                                        <Lightbulb className="w-4 h-4 mr-2" />
                                                    )}
                                                    Reasoning Path
                                                </Button>
                                            )}

                                            <div className="h-6 w-px bg-slate-800 mx-1" />

                                            <Button size="icon" variant="outline" className="h-10 w-10 shrink-0 border-slate-800 bg-slate-900/50 hover:bg-slate-800 rounded-xl" title="Download">
                                                <Download className="w-4 h-4" />
                                            </Button>
                                            <Button size="icon" variant="outline" className="h-10 w-10 shrink-0 border-slate-800 bg-slate-900/50 hover:bg-slate-800 rounded-xl" title="Expand">
                                                <ExternalLink className="w-4 h-4" />
                                            </Button>
                                        </div>
                                    </div>

                                    <div className="flex-1 overflow-hidden">
                                        <AnimatePresence mode="wait">
                                            {showReasoning && reasoningData ? (
                                                <motion.div
                                                    key="reasoning"
                                                    initial={{ opacity: 0, y: 20 }}
                                                    animate={{ opacity: 1, y: 0 }}
                                                    exit={{ opacity: 0, y: -20 }}
                                                    className="h-full flex flex-col gap-6"
                                                >
                                                    <div className="flex items-center justify-between px-2">
                                                        <div className="flex items-center gap-3">
                                                            <div className="p-2 bg-amber-500/10 rounded-lg">
                                                                <Lightbulb className="w-4 h-4 text-amber-400" />
                                                            </div>
                                                            <div>
                                                                <h4 className="text-sm font-black text-slate-200 uppercase tracking-widest">Glassbox Reasoning Path</h4>
                                                                <p className="text-[10px] text-slate-500 font-mono">COMPILED VIA CHAIN-OF-THOUGHT • {reasoningData.total_steps} STEPS • {Math.round(reasoningData.total_duration_seconds || 0)}s TOTAL</p>
                                                            </div>
                                                        </div>
                                                    </div>

                                                    <ScrollArea className="flex-1 pr-4">
                                                        <div className="space-y-2 mt-4 ml-2">
                                                            {reasoningData.reasoning_steps.map((step, index) => (
                                                                <ReasoningStepItem
                                                                    key={step.step_id}
                                                                    step={step}
                                                                    index={index}
                                                                    isLast={index === reasoningData.reasoning_steps.length - 1}
                                                                />
                                                            ))}
                                                        </div>
                                                    </ScrollArea>
                                                </motion.div>
                                            ) : reasoningError ? (
                                                <motion.div
                                                    key="error"
                                                    className="h-full flex items-center justify-center p-12 bg-red-500/5 border border-red-500/10 rounded-3xl"
                                                >
                                                    <div className="text-center">
                                                        <div className="w-16 h-16 rounded-full bg-red-500/10 flex items-center justify-center mx-auto mb-6">
                                                            <AlertIcon className="w-8 h-8 text-red-500" />
                                                        </div>
                                                        <h4 className="text-lg font-bold text-red-400 mb-2">Failed to Extract Logic Path</h4>
                                                        <p className="text-sm text-red-400/60 max-w-sm mb-6">{reasoningError}</p>
                                                        <Button size="sm" onClick={handleShowReasoning} className="bg-red-600 hover:bg-red-500">Retry Extraction</Button>
                                                    </div>
                                                </motion.div>
                                            ) : (
                                                <motion.div
                                                    key="preview"
                                                    initial={{ opacity: 0, scale: 0.98 }}
                                                    animate={{ opacity: 1, scale: 1 }}
                                                    exit={{ opacity: 0, scale: 1.02 }}
                                                    className="h-full flex flex-col"
                                                >
                                                    <ArtifactPreviewer artifact={selectedArtifact} />
                                                </motion.div>
                                            )}
                                        </AnimatePresence>
                                    </div>
                                </div>
                            ) : (
                                <div className="h-full flex flex-col items-center justify-center text-slate-500 border-2 border-dashed border-slate-800 rounded-3xl">
                                    <Database className="w-12 h-12 opacity-10 mb-4" />
                                    <p className="text-sm font-medium">Select an artifact output entry to begin audit</p>
                                </div>
                            )}
                        </div>
                    </TabsContent>

                    <TabsContent value="history" className="flex-1 overflow-hidden mt-0 flex flex-col bg-slate-950 data-[state=inactive]:hidden">
                        <ScrollArea className="h-full">
                            <div className="p-8 pb-20">
                                <CheckpointTimeline items={timeline} loading={loadingCheckpoints} />
                            </div>
                        </ScrollArea>
                    </TabsContent>
                </Tabs>
            </DialogContent>
        </Dialog>
    );
};

// --- ICONS ---

const BrainIcon = ({ className }: { className?: string }) => (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
        <path d="M9.5 2t5 0a7.5 7.5 0 1 1 0 15h-5a7.5 7.5 0 1 1 0-15z" /><path d="M12 12V2" /><path d="M12 17a5 5 0 1 0 0-10" />
    </svg>
);

const ActivityIcon = ({ className }: { className?: string }) => (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
        <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
);

const AlertIcon = ({ className }: { className?: string }) => (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
        <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
);

