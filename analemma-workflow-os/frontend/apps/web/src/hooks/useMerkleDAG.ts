/**
 * Merkle DAG React Query Hooks
 *
 * Provides lazy-loaded, cached access to the Merkle DAG state tree.
 * Follows the same patterns as useBriefingAndCheckpoints.ts.
 */

import { useCallback, useRef, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  listManifests,
  getLatestManifest,
  getManifestDetail,
  getManifestSegments,
  verifyManifestIntegrity,
} from '@/lib/merkleApi';
import type {
  ManifestListResponse,
  ManifestDetail,
  SegmentData,
  IntegrityCheckResult,
} from '@/lib/types';

// ============ Manifest List Hook ============

interface UseManifestListOptions {
  executionId?: string;
  enabled?: boolean;
}

export function useManifestList({
  executionId,
  enabled = true,
}: UseManifestListOptions = {}) {
  return useQuery({
    queryKey: ['manifests', executionId],
    queryFn: async () => {
      if (!executionId) throw new Error('executionId is required');
      return await listManifests(executionId);
    },
    enabled: enabled && !!executionId,
    staleTime: 30 * 1000, // 30s
  });
}

// ============ Latest Manifest Hook ============

interface UseLatestManifestOptions {
  executionId?: string;
  enabled?: boolean;
}

export function useLatestManifest({
  executionId,
  enabled = true,
}: UseLatestManifestOptions = {}) {
  return useQuery({
    queryKey: ['manifest-latest', executionId],
    queryFn: async () => {
      if (!executionId) throw new Error('executionId is required');
      return await getLatestManifest(executionId);
    },
    enabled: enabled && !!executionId,
    staleTime: 30 * 1000,
  });
}

// ============ Manifest Detail Hook (on-demand) ============

interface UseManifestDetailOptions {
  executionId?: string;
  manifestId?: string;
  enabled?: boolean;
}

export function useManifestDetail({
  executionId,
  manifestId,
  enabled = true,
}: UseManifestDetailOptions = {}) {
  return useQuery({
    queryKey: ['manifest-detail', executionId, manifestId],
    queryFn: async () => {
      if (!executionId || !manifestId)
        throw new Error('executionId and manifestId are required');
      return await getManifestDetail(executionId, manifestId);
    },
    enabled: enabled && !!executionId && !!manifestId,
    staleTime: 5 * 60 * 1000, // 5 min
  });
}

// ============ Manifest Segments Hook (on-demand) ============

interface UseManifestSegmentsOptions {
  executionId?: string;
  manifestId?: string;
  indices?: number[];
  enabled?: boolean;
}

export function useManifestSegments({
  executionId,
  manifestId,
  indices,
  enabled = true,
}: UseManifestSegmentsOptions = {}) {
  return useQuery({
    queryKey: ['manifest-segments', executionId, manifestId, indices],
    queryFn: async () => {
      if (!executionId || !manifestId)
        throw new Error('executionId and manifestId are required');
      return await getManifestSegments(executionId, manifestId, indices);
    },
    enabled: enabled && !!executionId && !!manifestId,
    staleTime: 5 * 60 * 1000,
  });
}

// ============ Verify Integrity Mutation ============

interface UseVerifyIntegrityOptions {
  onSuccess?: (result: IntegrityCheckResult) => void;
  onError?: (error: Error) => void;
}

export function useVerifyIntegrity(options: UseVerifyIntegrityOptions = {}) {
  const optionsRef = useRef(options);
  const queryClient = useQueryClient();

  useEffect(() => {
    optionsRef.current = options;
  });

  const mutation = useMutation({
    mutationFn: async ({
      executionId,
      manifestId,
    }: {
      executionId: string;
      manifestId: string;
    }) => {
      return await verifyManifestIntegrity(executionId, manifestId);
    },
    onSuccess: (data) => {
      if (data.is_valid) {
        toast.success(`Manifest ${data.manifest_id.substring(0, 8)}... integrity verified`);
      } else {
        toast.error(`Manifest ${data.manifest_id.substring(0, 8)}... integrity check FAILED`);
      }
      optionsRef.current.onSuccess?.(data);
    },
    onError: (error: Error) => {
      toast.error(`Integrity check failed: ${error.message}`);
      optionsRef.current.onError?.(error);
    },
  });

  const verify = useCallback(
    (executionId: string, manifestId: string) => {
      return mutation.mutateAsync({ executionId, manifestId });
    },
    [mutation]
  );

  return {
    verify,
    isVerifying: mutation.isPending,
    lastResult: mutation.data || null,
    error: mutation.error,
  };
}
