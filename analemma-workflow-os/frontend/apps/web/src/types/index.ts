// API Response Types
export interface ApiResponse<T = any> {
  data?: T;
  error?: string;
  message?: string;
}

// Workflow Types
export interface Workflow {
  id: string;
  name: string;
  status: 'RUNNING' | 'COMPLETED' | 'FAILED' | 'PENDING';
  createdAt: string;
  updatedAt: string;
  executionId?: string;
}

// Execution Types
export interface ExecutionSummary {
  executionId: string;
  workflowName: string;
  status: 'RUNNING' | 'COMPLETED' | 'FAILED' | 'PENDING';
  startTime: string;
  endTime?: string;
  duration?: number;
}

// Notification Types
export interface NotificationItem {
  id: string;
  action: string;
  payload: any;
  read: boolean;
  receivedAt: number;
  execution_id?: string;
}

// History Types
export interface HistoryEntry {
  id: string;
  executionId: string;
  timestamp: number;
  status: string;
  message?: string;
  details?: any;
}

// Resume Workflow Types
export interface ResumeWorkflowRequest {
  executionId: string;
  payload: any;
}

// Node Types
export interface WorkflowNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: any;
}

// Edge Types
export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  type?: string;
}

// Form Types
export interface WorkflowFormData {
  name: string;
  description?: string;
  config: any;
}

// Dashboard Types
export interface DashboardStats {
  activeWorkflows: number;
  completedToday: number;
  failedToday: number;
  totalExecutions: number;
}

// Error Types
export interface ApiError {
  code: string;
  message: string;
  details?: any;
}

// Generic Types
export type LoadingState = 'idle' | 'loading' | 'success' | 'error';
export type Theme = 'light' | 'dark' | 'system';

// Task Manager Types (aligned with OpenAPI spec)
export interface TaskSummary {
  id: string;
  name: string;
  status: 'queued' | 'in_progress' | 'pending_approval' | 'completed' | 'failed' | 'cancelled';
  progress_pct: number;
  eta_display?: string;
  confidence_score?: number;
  created_at: string;
  completed_at?: string | null;
  pending_action?: PendingAction;
  quick_fix?: QuickFix;
}

export interface PendingAction {
  type: 'approval' | 'input' | 'decision';
  prompt: string;
  options?: string[];
  timeout_seconds?: number;
}

export interface QuickFix {
  suggestion: string;
  action_url: string;
  auto_apply?: boolean;
}

export interface Artifact {
  artifact_id: string;
  artifact_type: string;
  name: string;
  url: string;
  thumbnail_url?: string;
  created_at: string;
  size_bytes?: number;
}

// Reasoning Path Types (aligned with OpenAPI spec)
export interface ReasoningStep {
  step_id: string;
  step_type: 'decision' | 'action' | 'observation' | 'thought';
  content: string;
  timestamp: string;
  confidence?: number | null;
  node_id?: string | null;
}

export interface ReasoningPathResponse {
  artifact_id: string;
  reasoning_steps: ReasoningStep[];
  total_steps: number;
  total_duration_seconds?: number | null;
}

// Healing Status Types (aligned with OpenAPI spec)
export interface HealingFix {
  timestamp: string;
  issue_type: string;
  fix_description: string;
  success: boolean;
}

export interface RemainingIssue {
  issue_id: string;
  issue_type: string;
  severity: 'low' | 'medium' | 'high' | 'critical';
  description: string;
}

export interface HealingStatusResponse {
  executionArn: string;
  healing_active: boolean;
  healing_attempts: number;
  max_attempts: number;
  current_strategy?: 'retry' | 'fallback' | 'skip' | 'escalate';
  fixes_applied: HealingFix[];
  remaining_issues: RemainingIssue[];
  last_healing_at?: string | null;
}
