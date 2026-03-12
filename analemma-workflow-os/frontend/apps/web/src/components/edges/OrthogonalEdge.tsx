/**
 * OrthogonalEdge
 *
 * Renders edges using strictly horizontal/vertical (orthogonal) segments
 * with automatic waypoint calculation. Inspired by circuit-diagram routing.
 *
 * Features:
 * - Right-angle only paths (no Bezier curves)
 * - Clear arrowhead at the target end
 * - Edge type selector (reuses SmartEdge UI dropdown)
 * - Back-edge animated dashes
 */

import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  EdgeProps,
  useReactFlow,
  MarkerType,
} from '@xyflow/react';
import {
  Activity,
  ChevronDown,
  ArrowRight,
  RefreshCw,
  GitBranch,
  Hand,
  Pause,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { toast } from 'sonner';
import { useState, useCallback, useMemo, useEffect, useRef } from 'react';

// Module-level constant
const HIT_AREA_STYLE = {
  strokeWidth: 20,
  stroke: 'transparent',
  cursor: 'pointer',
} as const;

export type BackendEdgeType =
  | 'edge'
  | 'if'
  | 'while'
  | 'for_each'
  | 'hitp'
  | 'conditional_edge'
  | 'pause';

const EDGE_TYPE_CONFIG: Record<
  BackendEdgeType,
  {
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    color: string;
    description: string;
    needsCondition?: boolean;
    needsIterationConfig?: boolean;
  }
> = {
  edge: {
    label: 'Normal',
    icon: ArrowRight,
    color: 'hsl(263 70% 60%)',
    description: 'Normal connection (always proceeds)',
  },
  if: {
    label: 'Conditional',
    icon: GitBranch,
    color: 'hsl(142 76% 36%)',
    description: 'Proceeds only when condition is true',
    needsCondition: true,
  },
  while: {
    label: 'While Loop',
    icon: RefreshCw,
    color: 'hsl(38 92% 50%)',
    description: 'Loop exits when condition is True (evaluated at end of each iteration)',
    needsCondition: true,
  },
  for_each: {
    label: 'For Each Loop',
    icon: RefreshCw,
    color: 'hsl(280 70% 50%)',
    description: 'Runs sub-workflow for each item in a list',
    needsIterationConfig: true,
  },
  hitp: {
    label: 'Human Approval',
    icon: Hand,
    color: 'hsl(280 70% 50%)',
    description: 'Proceeds after human approval',
  },
  conditional_edge: {
    label: 'Multi-Branch',
    icon: GitBranch,
    color: 'hsl(200 70% 50%)',
    description: 'Router function determines branch',
    needsCondition: true,
  },
  pause: {
    label: 'Pause',
    icon: Pause,
    color: 'hsl(0 70% 50%)',
    description: 'Pause and resume',
  },
};

interface OrthogonalEdgeData extends Record<string, unknown> {
  label?: string;
  active?: boolean;
  stateDelta?: string;
  edgeType?: BackendEdgeType;
  condition?: string;
  natural_condition?: string;
  eval_mode?: 'expression' | 'natural_language';
  max_iterations?: number;
  items_path?: string;
  item_key?: string;
  isBackEdge?: boolean;
  isInCycle?: boolean;
}

export const OrthogonalEdge = ({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  markerEnd,
  data,
}: EdgeProps) => {
  const { setEdges, getEdges, getNodes } = useReactFlow();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [conditionInput, setConditionInput] = useState('');
  const [isEditingLoop, setIsEditingLoop] = useState(false);

  // Close dropdown on outside click
  useEffect(() => {
    if (!isDropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setIsDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [isDropdownOpen]);

  const [loopConfig, setLoopConfig] = useState<{
    items_path?: string;
    item_key?: string;
    max_iterations?: number;
    eval_mode?: 'expression' | 'natural_language';
    natural_condition?: string;
  }>({
    items_path: '',
    item_key: 'item',
    max_iterations: 100,
  });

  const edgeData = data as OrthogonalEdgeData | undefined;
  const currentType: BackendEdgeType =
    (edgeData?.edgeType as BackendEdgeType) || 'edge';
  const typeConfig = EDGE_TYPE_CONFIG[currentType];

  const isLoopEdge = useMemo(() => {
    if (edgeData?.isBackEdge) return true;
    const allNodes = getNodes();
    const targetNode = allNodes.find((n) => n.id === target);
    if (
      targetNode?.type === 'control' &&
      (targetNode.data?.controlType === 'while' ||
        targetNode.data?.controlType === 'for_each')
    ) {
      return true;
    }
    const allEdges = getEdges();
    const backEdges = allEdges.filter((e) => e.data?.isBackEdge);
    for (const backEdge of backEdges) {
      if (target === backEdge.target) return true;
      if (source === backEdge.source) return true;
    }
    return false;
  }, [source, target, edgeData?.isBackEdge, getEdges, getNodes]);

  const handleTypeChange = useCallback(
    (newType: BackendEdgeType) => {
      if (isLoopEdge) {
        toast.error(
          'Loop structure edges cannot be changed. Control flow should be handled before entering the loop.'
        );
        return;
      }
      setEdges((edges) =>
        edges.map((edge) =>
          edge.id === id
            ? {
                ...edge,
                data: {
                  ...edge.data,
                  edgeType: newType,
                  condition: EDGE_TYPE_CONFIG[newType].needsCondition
                    ? edge.data?.condition
                    : undefined,
                },
              }
            : edge
        )
      );
    },
    [id, isLoopEdge, setEdges]
  );

  const handleConditionSave = useCallback(() => {
    setEdges((edges) =>
      edges.map((edge) =>
        edge.id === id
          ? { ...edge, data: { ...edge.data, condition: conditionInput } }
          : edge
      )
    );
    setIsEditing(false);
  }, [id, conditionInput, setEdges]);

  const handleLoopConfigSave = useCallback(() => {
    setEdges((edges) =>
      edges.map((edge) =>
        edge.id === id
          ? {
              ...edge,
              data: {
                ...edge.data,
                items_path: loopConfig.items_path,
                item_key: loopConfig.item_key,
                max_iterations: loopConfig.max_iterations,
                eval_mode: loopConfig.eval_mode,
                natural_condition: loopConfig.natural_condition,
              },
            }
          : edge
      )
    );
    setIsEditingLoop(false);
  }, [id, loopConfig, setEdges]);

  // Use getSmoothStepPath for orthogonal routing (right-angle segments)
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8,
  });

  const isBackEdge = edgeData?.isBackEdge === true;

  // Orthogonal edge styles
  const edgeStyle = {
    ...(style as Record<string, unknown>),
    stroke: edgeData?.active
      ? '#3b82f6'
      : isBackEdge
        ? 'hsl(38 92% 50%)'
        : typeConfig.color,
    strokeWidth: edgeData?.active ? 2.5 : isBackEdge ? 2.5 : 2,
    animation: edgeData?.active
      ? 'dashdraw 0.5s linear infinite'
      : isBackEdge
        ? 'dashdraw 2s linear infinite'
        : currentType === 'while'
          ? 'dashdraw 2s linear infinite'
          : 'none',
    strokeDasharray: edgeData?.active
      ? '5'
      : isBackEdge
        ? '8 4'
        : currentType === 'while'
          ? '8 4'
          : currentType === 'if'
            ? '4 2'
            : '0',
  };

  const IconComponent = typeConfig.icon;

  return (
    <>
      {/* Invisible hit-area edge */}
      <BaseEdge path={edgePath} style={HIT_AREA_STYLE} />

      {/* Visible orthogonal edge */}
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={edgeStyle} />

      {/* Interactive label UI */}
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: 'all',
            zIndex: isBackEdge ? 15 : 10,
          }}
          className="nodrag nopan"
        >
          <div className="flex flex-col items-center gap-1">
            {/* Edge type selector */}
            <div className="relative" ref={dropdownRef}>
              <button
                onClick={() => setIsDropdownOpen((v) => !v)}
                className="flex items-center gap-1 px-2 py-1 rounded-md bg-background border shadow-sm hover:bg-muted transition-colors text-xs"
                style={{
                  borderColor: isBackEdge
                    ? 'hsl(38 92% 50%)'
                    : typeConfig.color,
                  backgroundColor: isBackEdge
                    ? 'hsla(38 92% 50% / 0.1)'
                    : undefined,
                }}
              >
                <IconComponent
                  className="w-3 h-3"
                  style={{
                    color: isBackEdge ? 'hsl(38 92% 50%)' : typeConfig.color,
                  }}
                />
                <span
                  className="font-medium"
                  style={{
                    color: isBackEdge ? 'hsl(38 92% 50%)' : typeConfig.color,
                  }}
                >
                  {isBackEdge ? 'Loop Back' : typeConfig.label}
                </span>
                <ChevronDown className="w-3 h-3 text-muted-foreground" />
              </button>
              {isDropdownOpen && (
                <div className="absolute top-full left-1/2 -translate-x-1/2 mt-1 w-48 z-50 rounded-md border bg-popover p-1 text-popover-foreground shadow-md">
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">
                    {isLoopEdge ? 'Loop Structure Edge (Fixed)' : 'Edge Type'}
                  </div>
                  <div className="-mx-1 my-1 h-px bg-muted" />
                  {isLoopEdge ? (
                    <div className="px-2 py-2 text-xs text-muted-foreground">
                      Loop structure edges (entry/exit/back) are fixed.
                      Control flow should be handled in edges before the loop.
                    </div>
                  ) : (
                    Object.entries(EDGE_TYPE_CONFIG).map(([type, config]) => {
                      const Icon = config.icon;
                      return (
                        <button
                          key={type}
                          onClick={() => {
                            handleTypeChange(type as BackendEdgeType);
                            setIsDropdownOpen(false);
                          }}
                          className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm cursor-pointer hover:bg-accent hover:text-accent-foreground transition-colors"
                        >
                          <Icon
                            className="w-4 h-4"
                            style={{ color: config.color }}
                          />
                          <div className="flex flex-col text-left">
                            <span className="font-medium">{config.label}</span>
                            <span className="text-[10px] text-muted-foreground">
                              {config.description}
                            </span>
                          </div>
                        </button>
                      );
                    })
                  )}
                </div>
              )}
            </div>

            {/* Condition input (if, while, conditional_edge) */}
            {typeConfig.needsCondition &&
              (isEditing ? (
                <div className="flex flex-col gap-1 p-2 bg-background border rounded-md shadow-md min-w-[200px]">
                  {currentType === 'while' && (
                    <div className="flex flex-col gap-1">
                      <label className="text-[9px] text-muted-foreground font-bold">
                        Evaluation Mode
                      </label>
                      <select
                        value={loopConfig.eval_mode || 'expression'}
                        onChange={(e) => {
                          setLoopConfig({
                            ...loopConfig,
                            eval_mode: e.target.value as
                              | 'expression'
                              | 'natural_language',
                          });
                        }}
                        className="h-6 text-xs border rounded px-1"
                      >
                        <option value="expression">Expression (code)</option>
                        <option value="natural_language">
                          Natural Language (LLM)
                        </option>
                      </select>
                    </div>
                  )}

                  {(currentType !== 'while' ||
                    loopConfig.eval_mode === 'expression' ||
                    !loopConfig.eval_mode) && (
                    <>
                      <label className="text-[9px] text-muted-foreground font-bold">
                        {currentType === 'while'
                          ? 'Exit Condition (True = stop)'
                          : 'Condition Expression'}
                      </label>
                      <Input
                        value={conditionInput}
                        onChange={(e) => setConditionInput(e.target.value)}
                        placeholder={
                          currentType === 'while'
                            ? 'state.count >= 10'
                            : "state.status == 'ready'"
                        }
                        className="h-6 text-xs w-full"
                        autoFocus
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleConditionSave();
                          if (e.key === 'Escape') setIsEditing(false);
                        }}
                      />
                      {currentType === 'while' && (
                        <div className="text-[8px] text-amber-400 px-1">
                          Evaluated at end of each iteration
                        </div>
                      )}
                    </>
                  )}

                  {currentType === 'while' &&
                    loopConfig.eval_mode === 'natural_language' && (
                      <>
                        <label className="text-[9px] text-muted-foreground font-bold">
                          Natural Language Condition
                        </label>
                        <textarea
                          value={loopConfig.natural_condition || ''}
                          onChange={(e) =>
                            setLoopConfig({
                              ...loopConfig,
                              natural_condition: e.target.value,
                            })
                          }
                          placeholder="Example: Is the generated content detailed enough and high quality?"
                          className="text-xs w-full border rounded p-2 min-h-[60px]"
                          autoFocus
                        />
                        <div className="text-[8px] text-blue-400 px-1">
                          LLM will evaluate this condition automatically
                        </div>
                      </>
                    )}

                  <button
                    onClick={handleConditionSave}
                    className="px-2 h-6 text-xs bg-primary text-primary-foreground rounded"
                  >
                    Save
                  </button>
                </div>
              ) : (
                <Badge
                  variant="outline"
                  className="bg-background text-[10px] px-1.5 py-0 h-5 border-muted-foreground/30 shadow-sm whitespace-nowrap cursor-pointer hover:bg-muted"
                  onClick={() => {
                    setConditionInput(
                      (edgeData?.condition as string) || ''
                    );
                    setIsEditing(true);
                  }}
                >
                  {edgeData?.natural_condition
                    ? edgeData.natural_condition.slice(0, 20) + '...'
                    : edgeData?.condition ||
                      edgeData?.label ||
                      'Set condition'}
                </Badge>
              ))}

            {/* For Each loop config */}
            {typeConfig.needsIterationConfig &&
              (isEditingLoop ? (
                <div className="flex flex-col gap-1 p-2 bg-background border rounded-md shadow-md min-w-[240px]">
                  <label className="text-[9px] text-muted-foreground font-bold">
                    List Path (State Path)
                  </label>
                  <Input
                    value={loopConfig.items_path}
                    onChange={(e) =>
                      setLoopConfig((prev) => ({
                        ...prev,
                        items_path: e.target.value,
                      }))
                    }
                    placeholder="e.g. state.users"
                    className="h-6 text-xs w-full"
                    autoFocus
                  />
                  <div className="text-[8px] text-blue-400 px-1">
                    state.users [user1, user2, ...]
                  </div>

                  <div className="flex gap-1 mt-1">
                    <div className="flex-1">
                      <label className="text-[9px] text-muted-foreground font-bold">
                        Item Variable
                      </label>
                      <Input
                        value={loopConfig.item_key}
                        onChange={(e) =>
                          setLoopConfig((prev) => ({
                            ...prev,
                            item_key: e.target.value,
                          }))
                        }
                        placeholder="item"
                        className="h-6 text-xs"
                      />
                    </div>
                    <div className="w-20">
                      <label className="text-[9px] text-muted-foreground font-bold">
                        Max Iter
                      </label>
                      <Input
                        type="number"
                        value={loopConfig.max_iterations}
                        onChange={(e) =>
                          setLoopConfig((prev) => ({
                            ...prev,
                            max_iterations: parseInt(e.target.value) || 100,
                          }))
                        }
                        className="h-6 text-xs"
                      />
                    </div>
                  </div>

                  <button
                    onClick={handleLoopConfigSave}
                    className="px-2 h-6 text-xs bg-primary text-primary-foreground rounded mt-1"
                  >
                    Save
                  </button>
                </div>
              ) : (
                <Badge
                  variant="outline"
                  className="bg-background text-[10px] px-1.5 py-0 h-5 border-muted-foreground/30 shadow-sm whitespace-nowrap cursor-pointer hover:bg-muted"
                  onClick={() => {
                    setLoopConfig({
                      items_path: (edgeData?.items_path as string) || '',
                      item_key: (edgeData?.item_key as string) || 'item',
                      max_iterations:
                        (edgeData?.max_iterations as number) || 100,
                    });
                    setIsEditingLoop(true);
                  }}
                >
                  {edgeData?.items_path || 'Set list path'}
                </Badge>
              ))}

            {/* Data inspection tooltip */}
            <div className="group relative">
              <div
                className="flex items-center justify-center w-5 h-5 rounded-full bg-background border cursor-pointer hover:bg-muted transition-colors shadow-sm"
                onClick={(e) => e.stopPropagation()}
              >
                <Activity
                  className={`w-3 h-3 ${edgeData?.stateDelta ? 'text-blue-500' : 'text-muted-foreground'}`}
                />
              </div>
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 hidden group-hover:block z-50 max-w-[300px] rounded-md border bg-popover p-2 text-popover-foreground shadow-md">
                <div className="space-y-1">
                  {isBackEdge && (
                    <div className="mb-2 p-2 bg-orange-500/10 border border-orange-500/30 rounded">
                      <p className="text-xs font-bold text-orange-500">
                        Circular Structure (Back-Edge)
                      </p>
                      <p className="text-[10px] text-muted-foreground mt-1">
                        This edge is a feedback path for iteration.
                      </p>
                    </div>
                  )}
                  <h4 className="font-medium leading-none text-xs text-muted-foreground mb-2">
                    {isBackEdge && 'Loop Feedback Edge'}
                    {!isBackEdge &&
                      currentType === 'while' &&
                      'While Loop Config'}
                    {!isBackEdge &&
                      currentType === 'if' &&
                      'Conditional Branch'}
                    {!isBackEdge &&
                      currentType === 'edge' &&
                      'State Update'}
                    {!isBackEdge &&
                      currentType === 'hitp' &&
                      'Human Approval'}
                  </h4>
                  {edgeData?.condition && (
                    <p className="text-xs">
                      <strong>Condition:</strong>{' '}
                      {edgeData.condition as string}
                    </p>
                  )}
                  {edgeData?.max_iterations && (
                    <p className="text-xs">
                      <strong>Max iterations:</strong>{' '}
                      {edgeData.max_iterations as number}
                    </p>
                  )}
                  {edgeData?.stateDelta && (
                    <pre className="text-[10px] bg-muted p-2 rounded overflow-auto max-h-32 font-mono whitespace-pre-wrap">
                      {edgeData.stateDelta}
                    </pre>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  );
};
