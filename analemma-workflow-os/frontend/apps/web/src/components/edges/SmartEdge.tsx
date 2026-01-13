import { BaseEdge, EdgeLabelRenderer, getBezierPath, useReactFlow, EdgeProps } from '@xyflow/react';
import { Activity } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

// Edge 데이터 타입 정의 - Edge 인터페이스 확장
interface SmartEdgeData extends Record<string, unknown> {
  label?: string;           // 조건 라벨 (예: "Yes", "Tool Call")
  active?: boolean;         // 현재 실행 중인지 여부 (애니메이션 트리거)
  stateDelta?: string;      // 전달되는 상태 데이터 요약 (JSON string)
}

export const SmartEdge = ({
  id,
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
  const { setEdges } = useReactFlow();

  // 타입 단언으로 data를 SmartEdgeData로 처리
  const edgeData = data as SmartEdgeData | undefined;

  // 1. 베지에 곡선 경로 계산
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  });

  // 2. 활성 상태일 때 애니메이션 스타일 적용
  const edgeStyle = {
    ...(style as Record<string, unknown>),
    stroke: edgeData?.active ? '#3b82f6' : '#94a3b8', // 활성: 파랑, 비활성: 회색
    strokeWidth: edgeData?.active ? 2 : 1.5,
    animation: edgeData?.active ? 'dashdraw 0.5s linear infinite' : 'none',
    strokeDasharray: edgeData?.active ? '5' : '0',
  };

  return (
    <>
      {/* 인터랙션용 투명 엣지 (클릭 범위 확장용) */}
      <BaseEdge
        path={edgePath}
        style={{ strokeWidth: 20, stroke: 'transparent', cursor: 'pointer' }}
      />

      {/* 실제 눈에 보이는 엣지 */}
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={edgeStyle} />

      {/* 엣지 위의 인터랙티브 UI 렌더링 */}
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: 'all', // 이게 있어야 엣지 위 버튼 클릭 가능
            zIndex: 10, // 라벨이 선 위에 표시되도록 z-index 추가
          }}
          className="nodrag nopan"
        >
          <div className="flex flex-col items-center gap-1">
            {/* A. 조건 라벨 (분기 조건 표시) */}
            {edgeData?.label && (
              <Badge
                variant="outline"
                className="bg-background text-[10px] px-1.5 py-0 h-5 border-muted-foreground/30 shadow-sm whitespace-nowrap"
              >
                {edgeData.label}
              </Badge>
            )}

            {/* B. 데이터 검사 버튼 (호버 시 데이터 표시) */}
            <Tooltip>
              <TooltipTrigger asChild>
                <div
                  className="flex items-center justify-center w-5 h-5 rounded-full bg-background border cursor-pointer hover:bg-muted transition-colors shadow-sm"
                  onClick={(e) => e.stopPropagation()} // 이벤트 전파 방지
                >
                   {/* 데이터가 있으면 Activity 아이콘, 없으면 단순 연결점 */}
                   <Activity className={`w-3 h-3 ${edgeData?.stateDelta ? 'text-blue-500' : 'text-muted-foreground'}`} />
                </div>
              </TooltipTrigger>

              {/* C. 전달 데이터 툴팁 */}
              {edgeData?.stateDelta && (
                <TooltipContent className="max-w-[300px] p-2">
                  <div className="space-y-1">
                    <h4 className="font-medium leading-none text-xs text-muted-foreground mb-2">State Update</h4>
                    <pre className="text-[10px] bg-muted p-2 rounded overflow-auto max-h-32 font-mono whitespace-pre-wrap">
                      {edgeData.stateDelta}
                    </pre>
                  </div>
                </TooltipContent>
              )}
            </Tooltip>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  );
};