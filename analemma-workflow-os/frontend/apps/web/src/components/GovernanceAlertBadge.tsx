/**
 * GovernanceAlertBadge Component
 * 
 * [v3.28] Governor가 발행한 정책 위반/제어 알림을 시각화합니다.
 * 
 * Features:
 * - Severity 기반 색상 코딩 (INFO=파랑, WARNING=노랑, CRITICAL=빨강)
 * - Tooltip으로 action_taken 상세 표시
 * - Category 아이콘 표시 (COST=💰, SECURITY=🔒, PERFORMANCE=⚡)
 * - 개발자 모드 시 technical_detail 표시
 */

import React from 'react';

export interface GovernanceAlert {
  alert_id: string;
  timestamp: string;
  severity: 'INFO' | 'WARNING' | 'CRITICAL';
  category: 'COST' | 'SECURITY' | 'PERFORMANCE' | 'COMPLIANCE' | 'PLAN_DRIFT';
  message: string;
  action_taken?: string;
  technical_detail?: Record<string, any>;
  related_node_id?: string;
  triggered_by_ring?: number;
}

interface GovernanceAlertBadgeProps {
  alerts: GovernanceAlert[];
  developerMode?: boolean;
}

const CATEGORY_ICONS: Record<string, string> = {
  COST: '💰',
  SECURITY: '🔒',
  PERFORMANCE: '⚡',
  COMPLIANCE: '📋',
  PLAN_DRIFT: '🎯',
};

const SEVERITY_COLORS: Record<string, string> = {
  INFO: 'bg-blue-100 text-blue-800 border-blue-300',
  WARNING: 'bg-yellow-100 text-yellow-800 border-yellow-300',
  CRITICAL: 'bg-red-100 text-red-800 border-red-300',
};

export function GovernanceAlertBadge({ alerts, developerMode = false }: GovernanceAlertBadgeProps) {
  if (!alerts || alerts.length === 0) {
    return null;
  }

  return (
    <div className="governance-alert-stack space-y-2">
      {alerts.map((alert) => {
        const icon = CATEGORY_ICONS[alert.category] || '⚠️';
        const colorClass = SEVERITY_COLORS[alert.severity] || SEVERITY_COLORS.INFO;
        
        return (
          <div
            key={alert.alert_id}
            className={`
              governance-alert-badge 
              px-3 py-2 
              rounded-md 
              border 
              ${colorClass}
              flex items-start gap-2
              transition-all duration-200
              hover:shadow-md
            `}
            title={alert.action_taken || undefined}
          >
            {/* Category Icon */}
            <span className="text-lg" role="img" aria-label={alert.category}>
              {icon}
            </span>
            
            {/* Alert Content */}
            <div className="flex-1">
              {/* Category & Message */}
              <div className="flex items-center gap-2">
                <span className="font-semibold text-xs uppercase">
                  {alert.category}
                </span>
                <span className="text-sm">
                  {alert.message}
                </span>
              </div>
              
              {/* Action Taken */}
              {alert.action_taken && (
                <div className="mt-1 text-xs opacity-80">
                  <strong>조치:</strong> {alert.action_taken}
                </div>
              )}
              
              {/* Developer Mode: Technical Details */}
              {developerMode && alert.technical_detail && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer font-mono opacity-60 hover:opacity-100">
                    🔧 Technical Details
                  </summary>
                  <pre className="mt-1 p-2 bg-black bg-opacity-5 rounded overflow-x-auto">
                    {JSON.stringify(alert.technical_detail, null, 2)}
                  </pre>
                </details>
              )}
              
              {/* Metadata (Developer Mode) */}
              {developerMode && (
                <div className="mt-1 text-xs opacity-60 font-mono">
                  {alert.related_node_id && (
                    <span className="mr-3">
                      Node: {alert.related_node_id}
                    </span>
                  )}
                  {alert.triggered_by_ring !== undefined && (
                    <span>
                      Ring: {alert.triggered_by_ring}
                    </span>
                  )}
                </div>
              )}
            </div>
            
            {/* Severity Badge */}
            <div className="flex-shrink-0">
              <span className={`
                inline-block 
                px-2 py-1 
                rounded-full 
                text-xs 
                font-bold
                ${alert.severity === 'CRITICAL' ? 'bg-red-600 text-white' : ''}
                ${alert.severity === 'WARNING' ? 'bg-yellow-600 text-white' : ''}
                ${alert.severity === 'INFO' ? 'bg-blue-600 text-white' : ''}
              `}>
                {alert.severity}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// Tooltip wrapper component (optional, requires Radix UI or similar)
export function GovernanceAlertWithTooltip({ alert }: { alert: GovernanceAlert }) {
  return (
    <div className="relative group">
      <div className={`
        inline-flex items-center gap-1 
        px-2 py-1 
        rounded 
        text-xs 
        ${SEVERITY_COLORS[alert.severity]}
      `}>
        <span>{CATEGORY_ICONS[alert.category]}</span>
        <span>{alert.category}</span>
      </div>
      
      {/* Tooltip */}
      {alert.action_taken && (
        <div className="
          absolute 
          bottom-full 
          left-1/2 
          transform 
          -translate-x-1/2 
          mb-2 
          px-3 
          py-2 
          bg-gray-900 
          text-white 
          text-xs 
          rounded 
          whitespace-nowrap 
          opacity-0 
          group-hover:opacity-100 
          transition-opacity 
          pointer-events-none
          z-10
        ">
          {alert.action_taken}
          {/* Arrow */}
          <div className="
            absolute 
            top-full 
            left-1/2 
            transform 
            -translate-x-1/2 
            border-4 
            border-transparent 
            border-t-gray-900
          " />
        </div>
      )}
    </div>
  );
}

export default GovernanceAlertBadge;
