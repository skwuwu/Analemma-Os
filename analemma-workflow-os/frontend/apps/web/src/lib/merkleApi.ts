/**
 * Merkle DAG State Versioning API Client
 *
 * Communicates with the backend Merkle DAG endpoints to list, inspect,
 * and verify manifests produced by StateVersioningService.
 */

import { makeAuthenticatedRequest, parseApiResponse } from '@/lib/api';
import type {
  ManifestListResponse,
  ManifestDetail,
  SegmentData,
  IntegrityCheckResult,
} from '@/lib/types';

const API_BASE = import.meta.env.VITE_API_BASE_URL;

/**
 * List all manifest versions for an execution.
 */
export async function listManifests(
  executionId: string
): Promise<ManifestListResponse> {
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/executions/${encodeURIComponent(executionId)}/manifests`
  );

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to list manifests: ${error}`);
  }

  return parseApiResponse<ManifestListResponse>(response);
}

/**
 * Get the latest manifest pointer (tree entry point).
 */
export async function getLatestManifest(
  executionId: string
): Promise<ManifestDetail> {
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/executions/${encodeURIComponent(executionId)}/manifests/latest`
  );

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to get latest manifest: ${error}`);
  }

  return parseApiResponse<ManifestDetail>(response);
}

/**
 * Get full manifest detail including content blocks.
 */
export async function getManifestDetail(
  executionId: string,
  manifestId: string
): Promise<ManifestDetail> {
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/executions/${encodeURIComponent(executionId)}/manifests/${encodeURIComponent(manifestId)}`
  );

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to get manifest detail: ${error}`);
  }

  return parseApiResponse<ManifestDetail>(response);
}

/**
 * Load segment content from a manifest (lazy, with optional index filter).
 */
export async function getManifestSegments(
  executionId: string,
  manifestId: string,
  indices?: number[]
): Promise<SegmentData[]> {
  const params = new URLSearchParams();
  if (indices && indices.length > 0) {
    params.set('indices', indices.join(','));
  }

  const qs = params.toString();
  const url = `${API_BASE}/executions/${encodeURIComponent(executionId)}/manifests/${encodeURIComponent(manifestId)}/segments${qs ? `?${qs}` : ''}`;

  const response = await makeAuthenticatedRequest(url);

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to load manifest segments: ${error}`);
  }

  const result = await parseApiResponse<{ segments: SegmentData[] }>(response);
  return result.segments;
}

/**
 * Verify Merkle root integrity for a manifest.
 */
export async function verifyManifestIntegrity(
  executionId: string,
  manifestId: string
): Promise<IntegrityCheckResult> {
  const response = await makeAuthenticatedRequest(
    `${API_BASE}/executions/${encodeURIComponent(executionId)}/manifests/${encodeURIComponent(manifestId)}/integrity`
  );

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Failed to verify manifest integrity: ${error}`);
  }

  return parseApiResponse<IntegrityCheckResult>(response);
}
