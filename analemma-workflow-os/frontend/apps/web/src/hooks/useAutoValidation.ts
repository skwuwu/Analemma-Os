/**
 * useAutoValidation: Background Linter for Workflow Canvas
 * =========================================================
 * 
 * Automatically validates workflow logic as users design, similar to:
 * - VSCode's real-time linter
 * - Figma's auto-layout validation
 * - Circuit design DRC (Design Rule Check)
 * 
 * Eliminates the need for manual "Simulate Run" button.
 */
import { useEffect, useRef } from 'react';
import { useWorkflowStore } from '@/lib/workflowStore';
import { useCodesignStore } from '@/lib/codesignStore';

interface UseAutoValidationOptions {
  enabled?: boolean;
  debounceMs?: number;
  onValidationComplete?: (issueCount: number) => void;
}

export function useAutoValidation(options: UseAutoValidationOptions = {}) {
  const { enabled = true, debounceMs = 1500, onValidationComplete } = options;
  
  const nodes = useWorkflowStore((state) => state.nodes);
  const edges = useWorkflowStore((state) => state.edges);
  const auditIssues = useCodesignStore((state) => state.auditIssues);
  const requestAudit = useCodesignStore((state) => state.requestAudit);
  
  const timeoutRef = useRef<NodeJS.Timeout>();
  const prevWorkflowHashRef = useRef<string>('');

  useEffect(() => {
    if (!enabled || nodes.length === 0) {
      return;
    }

    // Generate workflow hash to detect changes
    const workflowHash = JSON.stringify({
      nodeCount: nodes.length,
      edgeCount: edges.length,
      nodeIds: nodes.map(n => n.id).sort(),
      edgeIds: edges.map(e => `${e.source}-${e.target}`).sort()
    });

    // Skip if workflow hasn't changed
    if (workflowHash === prevWorkflowHashRef.current) {
      return;
    }

    prevWorkflowHashRef.current = workflowHash;

    // Debounce validation to avoid excessive calls during rapid edits
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }

    timeoutRef.current = setTimeout(async () => {
      console.log('[AutoValidation] Running background audit...');
      
      await requestAudit(
        { nodes, edges },
        undefined // authToken handled by codesignStore
      );

      if (onValidationComplete) {
        onValidationComplete(auditIssues.length);
      }
    }, debounceMs);

    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
    };
  }, [nodes, edges, enabled, debounceMs, requestAudit, onValidationComplete, auditIssues.length]);

  return {
    issueCount: auditIssues.length,
    issues: auditIssues,
    hasErrors: auditIssues.some(issue => issue.level === 'error'),
    hasWarnings: auditIssues.some(issue => issue.level === 'warning'),
  };
}
