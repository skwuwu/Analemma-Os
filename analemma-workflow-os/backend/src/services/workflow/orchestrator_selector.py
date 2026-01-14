"""
워크플로우 오케스트레이터 동적 선택 시스템

세그먼트 수와 복잡도에 따라 Standard 워크플로우와 Distributed Map 워크플로우를 
동적으로 선택하여 최적의 성능과 비용 효율성을 제공합니다.

🚀 주요 기능:
- 세그먼트 수 기반 자동 선택
- 병렬 그룹 복잡도 분석
- HITL 패턴 감지
- 이벤트 히스토리 제한 고려
- 레이턴시 최적화

🎯 선택 기준:
- Standard: 단순하고 빠른 워크플로우 (< 300 세그먼트)
- Distributed Map: 대규모 복잡한 워크플로우 (≥ 300 세그먼트)
"""

import os
import json
import logging
from typing import Dict, Any, Tuple, Optional
from src.dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class WorkflowComplexity:
    """워크플로우 복잡도 분석 결과"""
    total_segments: int
    parallel_groups: int
    max_parallel_branches: int
    hitl_segments: int
    loop_segments: int
    estimated_events: int
    has_heavy_llm: bool
    has_s3_operations: bool
    complexity_score: float


@dataclass
class OrchestratorSelection:
    """오케스트레이터 선택 결과"""
    orchestrator_type: str  # 'standard' or 'distributed'
    orchestrator_arn: str
    selection_reason: str
    complexity: WorkflowComplexity
    performance_prediction: Dict[str, Any]


def analyze_workflow_complexity(workflow_config: Dict[str, Any]) -> WorkflowComplexity:
    """
    워크플로우 설정을 분석하여 복잡도를 계산합니다.
    
    Args:
        workflow_config: 워크플로우 설정 딕셔너리
    
    Returns:
        WorkflowComplexity: 복잡도 분석 결과
    """
    try:
        segments = workflow_config.get('segments', [])
        total_segments = len(segments)
        
        # 복잡도 지표 초기화
        parallel_groups = 0
        max_parallel_branches = 0
        hitl_segments = 0
        loop_segments = 0
        has_heavy_llm = False
        has_s3_operations = False
        
        # 세그먼트별 분석
        for segment in segments:
            segment_type = segment.get('type', '')
            
            # 병렬 그룹 분석
            if segment_type == 'parallel_group':
                parallel_groups += 1
                branches = segment.get('branches', [])
                branch_count = len(branches)
                max_parallel_branches = max(max_parallel_branches, branch_count)
            
            # HITL 패턴 감지
            elif segment_type in ['human_input', 'approval', 'review']:
                hitl_segments += 1
            
            # 루프 패턴 감지
            elif segment_type in ['loop', 'while', 'for_each']:
                loop_segments += 1
            
            # LLM 작업 감지
            llm_config = segment.get('llm_config', {})
            if llm_config:
                model = llm_config.get('model', '')
                max_tokens = llm_config.get('max_tokens', 0)
                
                # 대용량 LLM 작업 감지 (GPT-4, Claude-3, 높은 토큰 수)
                if any(heavy_model in model.lower() for heavy_model in ['gpt-4', 'claude-3', 'gemini-pro']):
                    has_heavy_llm = True
                elif max_tokens > 4000:
                    has_heavy_llm = True
            
            # S3 작업 감지
            if segment.get('s3_operations') or segment.get('file_operations'):
                has_s3_operations = True
        
        # Step Functions 이벤트 수 추정
        estimated_events = _estimate_step_functions_events(
            total_segments, parallel_groups, max_parallel_branches, hitl_segments
        )
        
        # 복잡도 점수 계산 (0-100)
        complexity_score = _calculate_complexity_score(
            total_segments, parallel_groups, max_parallel_branches, 
            hitl_segments, loop_segments, has_heavy_llm, has_s3_operations
        )
        
        return WorkflowComplexity(
            total_segments=total_segments,
            parallel_groups=parallel_groups,
            max_parallel_branches=max_parallel_branches,
            hitl_segments=hitl_segments,
            loop_segments=loop_segments,
            estimated_events=estimated_events,
            has_heavy_llm=has_heavy_llm,
            has_s3_operations=has_s3_operations,
            complexity_score=complexity_score
        )
        
    except Exception as e:
        logger.error(f"워크플로우 복잡도 분석 실패: {e}")
        # 폴백: 기본 복잡도 반환
        return WorkflowComplexity(
            total_segments=0,
            parallel_groups=0,
            max_parallel_branches=0,
            hitl_segments=0,
            loop_segments=0,
            estimated_events=100,
            has_heavy_llm=False,
            has_s3_operations=False,
            complexity_score=0.0
        )


def _estimate_step_functions_events(
    total_segments: int, 
    parallel_groups: int, 
    max_parallel_branches: int, 
    hitl_segments: int
) -> int:
    """
    Step Functions 이벤트 히스토리 사용량을 추정합니다.
    
    Standard 워크플로우는 25,000개 이벤트 제한이 있으므로 이를 고려해야 합니다.
    """
    # 기본 세그먼트당 이벤트 (평균 3-5개)
    base_events = total_segments * 4
    
    # 병렬 그룹 이벤트 (브랜치당 추가 이벤트)
    parallel_events = 0
    if parallel_groups > 0 and max_parallel_branches > 0:
        # 보수적 추정: 브랜치당 100-200개 이벤트
        events_per_branch = min(200, max(100, max_parallel_branches * 10))
        parallel_events = parallel_groups * max_parallel_branches * events_per_branch
    
    # HITL 세그먼트 추가 이벤트 (대기 상태로 인한 추가 이벤트)
    hitl_events = hitl_segments * 50
    
    # 안전 마진 20%
    total_events = int((base_events + parallel_events + hitl_events) * 1.2)
    
    return total_events


def _calculate_complexity_score(
    total_segments: int,
    parallel_groups: int, 
    max_parallel_branches: int,
    hitl_segments: int,
    loop_segments: int,
    has_heavy_llm: bool,
    has_s3_operations: bool
) -> float:
    """
    워크플로우 복잡도 점수를 계산합니다 (0-100).
    
    높은 점수일수록 더 복잡한 워크플로우입니다.
    """
    score = 0.0
    
    # 세그먼트 수 기반 점수 (0-40점)
    if total_segments <= 50:
        score += total_segments * 0.4  # 최대 20점
    elif total_segments <= 300:
        score += 20 + (total_segments - 50) * 0.08  # 20-40점
    else:
        score += 40  # 300개 이상은 최대 점수
    
    # 병렬 처리 복잡도 (0-25점)
    if parallel_groups > 0:
        parallel_score = min(25, parallel_groups * 5 + max_parallel_branches * 2)
        score += parallel_score
    
    # HITL 복잡도 (0-15점)
    if hitl_segments > 0:
        hitl_score = min(15, hitl_segments * 3)
        score += hitl_score
    
    # 루프 복잡도 (0-10점)
    if loop_segments > 0:
        loop_score = min(10, loop_segments * 5)
        score += loop_score
    
    # 리소스 집약적 작업 (0-10점)
    if has_heavy_llm:
        score += 5
    if has_s3_operations:
        score += 5
    
    return min(100.0, score)


def select_orchestrator(workflow_config: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    """
    워크플로우 설정을 기반으로 최적의 오케스트레이터를 선택합니다.
    
    Args:
        workflow_config: 워크플로우 설정
    
    Returns:
        Tuple[orchestrator_arn, orchestrator_type, selection_metadata]
    """
    try:
        # 환경 변수에서 오케스트레이터 ARN 가져오기
        standard_arn = os.environ.get('WORKFLOW_ORCHESTRATOR_ARN')
        distributed_arn = os.environ.get('WORKFLOW_DISTRIBUTED_ORCHESTRATOR_ARN')
        
        if not standard_arn:
            raise ValueError("WORKFLOW_ORCHESTRATOR_ARN not configured")
        
        # 워크플로우 복잡도 분석
        complexity = analyze_workflow_complexity(workflow_config)
        
        # 선택 로직
        selection_result = _apply_selection_logic(complexity, standard_arn, distributed_arn)
        
        # 성능 예측
        performance_prediction = _predict_performance(complexity, selection_result.orchestrator_type)
        
        # 메타데이터 구성
        selection_metadata = {
            'complexity': {
                'total_segments': complexity.total_segments,
                'parallel_groups': complexity.parallel_groups,
                'max_parallel_branches': complexity.max_parallel_branches,
                'hitl_segments': complexity.hitl_segments,
                'estimated_events': complexity.estimated_events,
                'complexity_score': complexity.complexity_score,
                'has_heavy_llm': complexity.has_heavy_llm,
                'has_s3_operations': complexity.has_s3_operations
            },
            'selection_reason': selection_result.selection_reason,
            'performance_prediction': performance_prediction,
            'fallback_available': distributed_arn is not None
        }
        
        logger.info(
            f"오케스트레이터 선택 완료: {selection_result.orchestrator_type} "
            f"(세그먼트: {complexity.total_segments}, 복잡도: {complexity.complexity_score:.1f}, "
            f"이유: {selection_result.selection_reason})"
        )
        
        return selection_result.orchestrator_arn, selection_result.orchestrator_type, selection_metadata
        
    except Exception as e:
        logger.error(f"오케스트레이터 선택 실패, 기본값 사용: {e}")
        # 폴백: Standard 오케스트레이터 사용
        return standard_arn or '', 'standard', {
            'selection_reason': f'Selection failed, using fallback: {str(e)}',
            'fallback_used': True
        }


def _apply_selection_logic(
    complexity: WorkflowComplexity, 
    standard_arn: str, 
    distributed_arn: Optional[str]
) -> OrchestratorSelection:
    """
    복잡도 분석 결과를 기반으로 오케스트레이터 선택 로직을 적용합니다.
    """
    # 🚨 Critical: Distributed Map이 사용 불가능한 경우 Standard 강제 사용
    if not distributed_arn:
        return OrchestratorSelection(
            orchestrator_type='standard',
            orchestrator_arn=standard_arn,
            selection_reason='Distributed Map not available, using Standard',
            complexity=complexity,
            performance_prediction={}
        )
    
    # 🎯 주요 선택 기준들
    
    # 1. 세그먼트 수 기반 선택 (가장 중요한 기준)
    if complexity.total_segments >= 300:
        return OrchestratorSelection(
            orchestrator_type='distributed',
            orchestrator_arn=distributed_arn,
            selection_reason=f'Large workflow: {complexity.total_segments} segments ≥ 300 threshold',
            complexity=complexity,
            performance_prediction={}
        )
    
    # 2. Step Functions 이벤트 제한 고려
    if complexity.estimated_events > 20000:  # 25,000 제한의 80%
        return OrchestratorSelection(
            orchestrator_type='distributed',
            orchestrator_arn=distributed_arn,
            selection_reason=f'High event count: {complexity.estimated_events} events > 20K threshold',
            complexity=complexity,
            performance_prediction={}
        )
    
    # 3. 복잡한 병렬 처리 패턴
    if complexity.parallel_groups >= 5 and complexity.max_parallel_branches >= 10:
        return OrchestratorSelection(
            orchestrator_type='distributed',
            orchestrator_arn=distributed_arn,
            selection_reason=f'Complex parallelism: {complexity.parallel_groups} groups × {complexity.max_parallel_branches} branches',
            complexity=complexity,
            performance_prediction={}
        )
    
    # 4. 매우 높은 복잡도 점수
    if complexity.complexity_score >= 80:
        return OrchestratorSelection(
            orchestrator_type='distributed',
            orchestrator_arn=distributed_arn,
            selection_reason=f'High complexity score: {complexity.complexity_score:.1f}/100',
            complexity=complexity,
            performance_prediction={}
        )
    
    # 5. 중간 규모 워크플로우의 세밀한 판단
    if 100 <= complexity.total_segments < 300:
        # 중간 규모에서는 추가 요소들을 고려
        distributed_factors = 0
        
        if complexity.parallel_groups >= 3:
            distributed_factors += 1
        if complexity.hitl_segments >= 5:
            distributed_factors += 1
        if complexity.has_heavy_llm:
            distributed_factors += 1
        if complexity.estimated_events > 10000:
            distributed_factors += 1
        
        if distributed_factors >= 2:
            return OrchestratorSelection(
                orchestrator_type='distributed',
                orchestrator_arn=distributed_arn,
                selection_reason=f'Medium workflow with {distributed_factors} complexity factors',
                complexity=complexity,
                performance_prediction={}
            )
    
    # 6. 기본값: Standard 워크플로우 (단순하고 빠름)
    return OrchestratorSelection(
        orchestrator_type='standard',
        orchestrator_arn=standard_arn,
        selection_reason=f'Simple workflow: {complexity.total_segments} segments, score {complexity.complexity_score:.1f}',
        complexity=complexity,
        performance_prediction={}
    )


def _predict_performance(complexity: WorkflowComplexity, orchestrator_type: str) -> Dict[str, Any]:
    """
    선택된 오케스트레이터의 예상 성능을 예측합니다.
    """
    if orchestrator_type == 'standard':
        # Standard 워크플로우 성능 예측
        estimated_duration_minutes = complexity.total_segments * 0.1  # 세그먼트당 6초
        if complexity.has_heavy_llm:
            estimated_duration_minutes *= 2  # LLM 작업은 2배 시간
        
        return {
            'estimated_duration_minutes': round(estimated_duration_minutes, 1),
            'cold_start_impact': 'Low',
            'concurrency_model': 'Sequential with parallel groups',
            'cost_efficiency': 'High for simple workflows',
            'latency': 'Low',
            'scalability': 'Limited by event history (25K events)'
        }
    
    else:  # distributed
        # Distributed Map 성능 예측
        # 병렬 처리로 인한 시간 단축 고려
        chunk_size = min(100, max(10, complexity.total_segments // 10))
        parallel_chunks = (complexity.total_segments + chunk_size - 1) // chunk_size
        estimated_duration_minutes = parallel_chunks * 0.5  # 청크당 30초
        
        return {
            'estimated_duration_minutes': round(estimated_duration_minutes, 1),
            'cold_start_impact': 'Medium (multiple Lambda invocations)',
            'concurrency_model': f'Distributed chunks (size: {chunk_size})',
            'cost_efficiency': 'High for large workflows',
            'latency': 'Medium (distributed coordination overhead)',
            'scalability': 'Unlimited (no event history limit)'
        }


def get_orchestrator_selection_summary(orchestrator_type: str, selection_metadata: Dict[str, Any]) -> str:
    """
    오케스트레이터 선택 결과의 요약 문자열을 생성합니다.
    """
    try:
        complexity = selection_metadata.get('complexity', {})
        reason = selection_metadata.get('selection_reason', 'Unknown')
        performance = selection_metadata.get('performance_prediction', {})
        
        segments = complexity.get('total_segments', 0)
        score = complexity.get('complexity_score', 0)
        duration = performance.get('estimated_duration_minutes', 0)
        
        return (
            f"{orchestrator_type.upper()} selected: {segments} segments, "
            f"complexity {score:.1f}/100, ~{duration:.1f}min, "
            f"reason: {reason}"
        )
        
    except Exception as e:
        return f"{orchestrator_type.upper()} selected (summary generation failed: {e})"


def get_selection_statistics() -> Dict[str, Any]:
    """
    오케스트레이터 선택 통계를 반환합니다 (모니터링용).
    """
    # 실제 구현에서는 CloudWatch 메트릭이나 DynamoDB에서 통계를 가져올 수 있습니다.
    return {
        'selection_criteria': {
            'segment_threshold': 300,
            'event_threshold': 20000,
            'complexity_threshold': 80,
            'parallel_group_threshold': 5
        },
        'performance_factors': {
            'standard_advantages': ['Low latency', 'Simple debugging', 'Lower cold start'],
            'distributed_advantages': ['High scalability', 'Parallel processing', 'No event limit'],
            'selection_factors': ['Segment count', 'Parallelism', 'Event estimation', 'Complexity score']
        }
    }


# 🧪 테스트 및 디버깅용 함수들

def test_orchestrator_selection(test_cases: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    다양한 테스트 케이스로 오케스트레이터 선택 로직을 테스트합니다.
    """
    results = {}
    
    for case_name, workflow_config in test_cases.items():
        try:
            arn, orchestrator_type, metadata = select_orchestrator(workflow_config)
            results[case_name] = {
                'orchestrator_type': orchestrator_type,
                'selection_reason': metadata.get('selection_reason'),
                'complexity_score': metadata.get('complexity', {}).get('complexity_score'),
                'estimated_events': metadata.get('complexity', {}).get('estimated_events')
            }
        except Exception as e:
            results[case_name] = {'error': str(e)}
    
    return results


def analyze_selection_impact(workflow_configs: list) -> Dict[str, Any]:
    """
    여러 워크플로우에 대한 선택 영향을 분석합니다.
    """
    standard_count = 0
    distributed_count = 0
    total_segments = 0
    
    for config in workflow_configs:
        try:
            _, orchestrator_type, _ = select_orchestrator(config)
            if orchestrator_type == 'standard':
                standard_count += 1
            else:
                distributed_count += 1
            
            segments = len(config.get('segments', []))
            total_segments += segments
            
        except Exception:
            continue
    
    total_workflows = standard_count + distributed_count
    
    return {
        'total_workflows': total_workflows,
        'standard_selected': standard_count,
        'distributed_selected': distributed_count,
        'standard_percentage': (standard_count / total_workflows * 100) if total_workflows > 0 else 0,
        'distributed_percentage': (distributed_count / total_workflows * 100) if total_workflows > 0 else 0,
        'average_segments': total_segments / total_workflows if total_workflows > 0 else 0
    }