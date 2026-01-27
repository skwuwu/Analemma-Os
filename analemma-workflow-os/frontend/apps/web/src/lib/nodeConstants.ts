/**
 * Node Constants
 * 
 * Pure data constants for node configurations.
 * NO UI components, NO store imports, NO circular dependencies.
 * This file sits at the bottom of the dependency tree.
 */
import { 
  Globe, 
  Database, 
  Clock, 
  Webhook, 
  Zap,
  CheckCircle2,
  Brain,
  GitBranch,
  type LucideIcon
} from 'lucide-react';

// Operator Node Config
export const OPERATOR_CONFIG = {
  custom: { icon: Globe, color: '25 95% 60%', label: 'Custom' },
  api_call: { icon: Globe, color: '200 100% 50%', label: 'API Call' },
  database: { icon: Database, color: '190 100% 28%', label: 'Database' },
  db_query: { icon: Database, color: '190 100% 28%', label: 'Database' },
  safe_operator: { icon: CheckCircle2, color: '142 76% 36%', label: 'Safe Transform' },
  operator_official: { icon: CheckCircle2, color: '142 76% 36%', label: 'Safe Transform' },
  default: { icon: Globe, color: '25 95% 60%', label: 'Operator' }
} as const;

// Trigger Node Config
export const TRIGGER_CONFIG = {
  time: {
    icon: Clock,
    label: 'Schedule',
    color: '142 76% 36%' // Green
  },
  request: {
    icon: Webhook,
    label: 'Webhook',
    color: '217 91% 60%' // Blue
  },
  event: {
    icon: Zap,
    label: 'Event',
    color: '45 93% 47%' // Yellow/Orange
  },
  default: {
    icon: Zap,
    label: 'Trigger',
    color: '142 76% 36%' // Default Green
  }
} as const;

// Control Node Config
export const CONTROL_CONFIG = {
  conditional: { icon: GitBranch, color: '280 100% 70%', label: 'Conditional' },
  parallel: { icon: GitBranch, color: '280 100% 70%', label: 'Parallel' },
  default: { icon: GitBranch, color: '280 100% 70%', label: 'Control' }
} as const;

// AI Model Node - Status configs
export const AI_NODE_STATUS_CONFIG = {
  idle: { border: 'border-primary/20', icon: null, text: 'text-muted-foreground' },
  running: { border: 'border-blue-500 shadow-blue-500/20', iconName: 'Loader2', text: 'text-blue-500' },
  failed: { border: 'border-destructive shadow-destructive/20', iconName: 'AlertCircle', text: 'text-destructive' },
  completed: { border: 'border-green-500 shadow-green-500/20', iconName: 'CheckCircle2', text: 'text-green-500' },
} as const;

// Operator Node - Status configs
export const OPERATOR_STATUS_ICONS = {
  running: 'Loader2',
  failed: 'AlertCircle',
  completed: 'CheckCircle2',
} as const;

// Type exports
export type OperatorType = keyof typeof OPERATOR_CONFIG;
export type TriggerType = keyof typeof TRIGGER_CONFIG;
export type ControlType = keyof typeof CONTROL_CONFIG;
export type NodeStatus = 'idle' | 'running' | 'failed' | 'completed';
