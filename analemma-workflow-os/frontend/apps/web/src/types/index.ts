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
