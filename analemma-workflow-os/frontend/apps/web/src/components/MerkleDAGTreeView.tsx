/**
 * MerkleDAGTreeView — expandable tree visualising the Merkle DAG
 * state versioning system produced by StateVersioningService.
 *
 * Tree hierarchy:
 *   Execution Root
 *     ├── Manifest v3 (latest)   [hash preview + timestamp]
 *     │     ├── ContentBlock #1  [size + fields]
 *     │     │     ├── Segment 0 data (JsonViewer)
 *     │     │     └── Segment 1 data (JsonViewer)
 *     │     └── ContentBlock #2
 *     ├── Manifest v2
 *     └── Manifest v1
 *
 * Lazy loading: blocks fetch on manifest expand; segments fetch on block expand.
 */

import React, { useState, useCallback } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  GitBranch,
  Box,
  ChevronRight,
  ChevronDown,
  CheckCircle2,
  XCircle,
  ShieldCheck,
  Loader2,
  Database,
  FileJson,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import JsonViewer from '@/components/JsonViewer';
import {
  useManifestList,
  useManifestDetail,
  useManifestSegments,
  useVerifyIntegrity,
} from '@/hooks/useMerkleDAG';
import type {
  ManifestSummary,
  ManifestDetail,
  MerkleContentBlock,
  SegmentData,
  IntegrityCheckResult,
} from '@/lib/types';

// ─── Props ───────────────────────────────────────────────────

interface MerkleDAGTreeViewProps {
  executionId: string;
  className?: string;
}

// ─── Integrity Badge ─────────────────────────────────────────

function IntegrityBadge({ result }: { result?: IntegrityCheckResult | null }) {
  if (!result) {
    return (
      <Badge variant="outline" className="text-[10px] h-5 text-slate-400 border-slate-600">
        unverified
      </Badge>
    );
  }
  return result.is_valid ? (
    <Badge variant="outline" className="text-[10px] h-5 text-green-400 border-green-700 bg-green-900/20">
      <CheckCircle2 className="w-3 h-3 mr-1" />
      verified
    </Badge>
  ) : (
    <Badge variant="destructive" className="text-[10px] h-5">
      <XCircle className="w-3 h-3 mr-1" />
      invalid
    </Badge>
  );
}

// ─── Segment Node (leaf) ────────────────────────────────────

function SegmentNode({ segment }: { segment: SegmentData }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="ml-8 border-l border-slate-700 pl-4 py-1">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 text-xs text-slate-300 hover:text-slate-100 transition-colors w-full text-left"
      >
        {open ? <ChevronDown className="w-3 h-3 shrink-0" /> : <ChevronRight className="w-3 h-3 shrink-0" />}
        <FileJson className="w-3 h-3 text-blue-400 shrink-0" />
        <span className="font-mono">Segment {segment.segment_index}</span>
      </button>
      {open && (
        <div className="mt-2 ml-5 max-h-64 overflow-auto rounded border border-slate-700 bg-slate-900/60 p-2">
          <JsonViewer data={segment.data} />
        </div>
      )}
    </div>
  );
}

// ─── Block Node ──────────────────────────────────────────────

interface BlockNodeProps {
  block: MerkleContentBlock;
  executionId: string;
  manifestId: string;
  blockIndex: number;
}

function BlockNode({ block, executionId, manifestId, blockIndex }: BlockNodeProps) {
  const [expanded, setExpanded] = useState(false);

  // Lazy-fetch segments for this block's fields when expanded
  const segmentsQuery = useManifestSegments({
    executionId,
    manifestId,
    indices: [blockIndex],
    enabled: expanded,
  });

  return (
    <div className="ml-6 border-l border-slate-700 pl-4 py-1">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-xs text-slate-300 hover:text-slate-100 transition-colors w-full text-left"
      >
        {expanded ? <ChevronDown className="w-3 h-3 shrink-0" /> : <ChevronRight className="w-3 h-3 shrink-0" />}
        <Box className="w-3 h-3 text-purple-400 shrink-0" />
        <span className="font-mono truncate max-w-[160px]">{block.block_id.substring(0, 12)}...</span>
        <Badge variant="outline" className="text-[9px] h-4 text-slate-400 border-slate-600 ml-auto shrink-0">
          {(block.size / 1024).toFixed(1)} KB
        </Badge>
        <Badge variant="outline" className="text-[9px] h-4 text-slate-500 border-slate-700 shrink-0">
          {block.fields.length} fields
        </Badge>
      </button>

      {expanded && (
        <div className="mt-1">
          {/* Field list */}
          <div className="ml-8 mb-2 flex flex-wrap gap-1">
            {block.fields.map((f) => (
              <span key={f} className="text-[9px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 font-mono">
                {f}
              </span>
            ))}
          </div>

          {/* Segments */}
          {segmentsQuery.isLoading && (
            <div className="ml-8 flex items-center gap-2 text-xs text-slate-500 py-2">
              <Loader2 className="w-3 h-3 animate-spin" />
              Loading segment data...
            </div>
          )}
          {segmentsQuery.data?.map((seg) => (
            <SegmentNode key={seg.segment_index} segment={seg} />
          ))}
          {segmentsQuery.isError && (
            <div className="ml-8 text-xs text-red-400 py-1">
              Failed to load segments: {segmentsQuery.error?.message}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Manifest Node ───────────────────────────────────────────

interface ManifestNodeProps {
  manifest: ManifestSummary;
  executionId: string;
  isLatest: boolean;
  integrityResults: Map<string, IntegrityCheckResult>;
  onVerify: (manifestId: string) => void;
  isVerifying: boolean;
}

function ManifestNode({
  manifest,
  executionId,
  isLatest,
  integrityResults,
  onVerify,
  isVerifying,
}: ManifestNodeProps) {
  const [expanded, setExpanded] = useState(false);

  // Lazy-fetch detail when expanded
  const detailQuery = useManifestDetail({
    executionId,
    manifestId: manifest.manifest_id,
    enabled: expanded,
  });

  const detail: ManifestDetail | undefined = detailQuery.data;
  const integrityResult = integrityResults.get(manifest.manifest_id);

  return (
    <div className="border-l-2 border-slate-700 pl-4 py-2">
      {/* Manifest header */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2 text-sm text-slate-200 hover:text-white transition-colors flex-1 text-left"
        >
          {expanded ? <ChevronDown className="w-4 h-4 shrink-0" /> : <ChevronRight className="w-4 h-4 shrink-0" />}
          <GitBranch className="w-4 h-4 text-cyan-400 shrink-0" />
          <span className="font-semibold">v{manifest.version}</span>
          <span className="font-mono text-xs text-slate-500 truncate max-w-[120px]">
            {manifest.manifest_hash.substring(0, 10)}...
          </span>
          {isLatest && (
            <Badge className="text-[9px] h-4 bg-cyan-600 hover:bg-cyan-600">latest</Badge>
          )}
          <span className="text-[10px] text-slate-500 ml-auto shrink-0">
            {new Date(manifest.created_at).toLocaleString()}
          </span>
        </button>

        {/* Integrity badge + verify button */}
        <IntegrityBadge result={integrityResult} />
        <Button
          variant="ghost"
          size="sm"
          className="h-6 px-2 text-[10px] text-slate-400 hover:text-white"
          onClick={(e) => {
            e.stopPropagation();
            onVerify(manifest.manifest_id);
          }}
          disabled={isVerifying}
        >
          {isVerifying ? (
            <Loader2 className="w-3 h-3 animate-spin" />
          ) : (
            <ShieldCheck className="w-3 h-3" />
          )}
        </Button>
      </div>

      {/* Metadata row */}
      {expanded && (
        <div className="ml-6 mt-1 mb-2 flex flex-wrap gap-3 text-[10px] text-slate-500">
          <span>{manifest.total_segments} segments</span>
          {manifest.parent_hash && (
            <span>parent: {manifest.parent_hash.substring(0, 8)}...</span>
          )}
          <span>config: {manifest.config_hash.substring(0, 8)}...</span>
        </div>
      )}

      {/* Blocks */}
      {expanded && detailQuery.isLoading && (
        <div className="ml-6 flex items-center gap-2 text-xs text-slate-500 py-2">
          <Loader2 className="w-3 h-3 animate-spin" />
          Loading blocks...
        </div>
      )}
      {expanded && detail?.blocks?.map((block, idx) => (
        <BlockNode
          key={block.block_id}
          block={block}
          executionId={executionId}
          manifestId={manifest.manifest_id}
          blockIndex={idx}
        />
      ))}
      {expanded && detailQuery.isError && (
        <div className="ml-6 text-xs text-red-400 py-1">
          Failed to load blocks: {detailQuery.error?.message}
        </div>
      )}
    </div>
  );
}

// ─── Root Component ──────────────────────────────────────────

export function MerkleDAGTreeView({ executionId, className }: MerkleDAGTreeViewProps) {
  const manifestListQuery = useManifestList({ executionId });
  const { verify, isVerifying } = useVerifyIntegrity();
  const [integrityResults, setIntegrityResults] = useState<Map<string, IntegrityCheckResult>>(new Map());

  const handleVerify = useCallback(
    async (manifestId: string) => {
      try {
        const result = await verify(executionId, manifestId);
        setIntegrityResults((prev) => {
          const next = new Map(prev);
          next.set(manifestId, result);
          return next;
        });
      } catch {
        // Toast handled by hook
      }
    },
    [executionId, verify]
  );

  const manifests = manifestListQuery.data?.manifests || [];
  const latestId = manifests.length > 0
    ? manifests.reduce((a, b) => (a.version > b.version ? a : b)).manifest_id
    : null;

  if (manifestListQuery.isLoading) {
    return (
      <div className={cn('flex items-center justify-center h-full text-slate-500', className)}>
        <Loader2 className="w-6 h-6 animate-spin mr-3" />
        <span className="text-sm">Loading Merkle DAG...</span>
      </div>
    );
  }

  if (manifestListQuery.isError) {
    return (
      <div className={cn('flex flex-col items-center justify-center h-full text-slate-500', className)}>
        <XCircle className="w-10 h-10 mb-3 text-red-400 opacity-40" />
        <p className="text-sm mb-2">Failed to load state DAG</p>
        <p className="text-xs text-slate-600">{manifestListQuery.error?.message}</p>
        <Button
          variant="outline"
          size="sm"
          className="mt-4"
          onClick={() => manifestListQuery.refetch()}
        >
          Retry
        </Button>
      </div>
    );
  }

  if (manifests.length === 0) {
    return (
      <div className={cn('flex flex-col items-center justify-center h-full text-slate-500', className)}>
        <Database className="w-12 h-12 mb-4 opacity-20" />
        <p className="text-sm font-medium">No Merkle DAG Data</p>
        <p className="text-xs mt-1">State versioning data is not available for this execution.</p>
      </div>
    );
  }

  return (
    <ScrollArea className={cn('h-full', className)}>
      <div className="p-4 space-y-1">
        {/* Root label */}
        <div className="flex items-center gap-2 mb-3">
          <Database className="w-4 h-4 text-cyan-400" />
          <span className="text-sm font-semibold text-slate-200">
            Execution State DAG
          </span>
          <Badge variant="outline" className="text-[10px] h-4 text-slate-400 border-slate-600">
            {manifests.length} versions
          </Badge>
        </div>

        {/* Manifests — newest first */}
        {[...manifests]
          .sort((a, b) => b.version - a.version)
          .map((m) => (
            <ManifestNode
              key={m.manifest_id}
              manifest={m}
              executionId={executionId}
              isLatest={m.manifest_id === latestId}
              integrityResults={integrityResults}
              onVerify={handleVerify}
              isVerifying={isVerifying}
            />
          ))}
      </div>
    </ScrollArea>
  );
}

export default MerkleDAGTreeView;
