import heapq
import os
import json
import logging
import threading
from typing import Dict, Any, List, Set, Optional, Tuple, FrozenSet

logger = logging.getLogger(__name__)

# ============================================================================
# [v2.0 Production Hardening] 상수 및 설정
# ============================================================================

# 최대 재귀 깊이 제한 (무한 루프 방지)
MAX_PARTITION_DEPTH = int(os.environ.get("MAX_PARTITION_DEPTH", "50"))

# 최대 노드 수 제한 (대규모 그래프 보호)
MAX_NODES_LIMIT = int(os.environ.get("MAX_NODES_LIMIT", "500"))

# 성능 경고 임계값 (100개 노드 초과 시 경고)
# 복잡한 그래프에서 위상 정렬/사이클 감지 latency 증가
PERFORMANCE_WARNING_NODE_COUNT = int(os.environ.get("PERFORMANCE_WARNING_NODE_COUNT", "100"))

# LLM 노드 타입들 - 이 타입들을 만날 때마다 세그먼트를 분할합니다
# Note: Specific vendor types (openai_chat, anthropic_chat, etc.) are mapped to llm_chat via NODE_TYPE_ALIASES
LLM_NODE_TYPES: FrozenSet[str] = frozenset({
    "llm_chat",
    "aiModel"  # 범용 AI 모델 노드 타입 (llm_chat의 별칭)
})

# HITP (Human in the Loop) 엣지 타입들
HITP_EDGE_TYPES: FrozenSet[str] = frozenset({"hitp", "human_in_the_loop", "pause"})

# 세그먼트 타입들
SEGMENT_TYPES: FrozenSet[str] = frozenset({
    "normal", "llm", "hitp", "isolated", "complete", "parallel_group", "aggregator"
})


# ============================================================================
# [v3.17] Loop Limit Constants — Complexity-Budget Counting
# ============================================================================
#
# max_loop_iterations는 두 가지 역할을 합니다:
#   1. SFN loop_counter가 이 값을 초과하면 LoopLimitExceeded 발생 (안전 장치)
#   2. 워크플로우 복잡도에 비례하는 충분한 실행 여유를 제공 (예산 역할)
#
# [분석] for_each 처리 방식:
#   - for_each_runner는 Lambda 내부 ThreadPoolExecutor로 모든 아이템 처리
#   - SFN loop_counter 관점: parallel_group(PARALLEL_GROUP 경로, +0) +
#     aggregator(CONTINUE 경로, +1) = 총 1~2회 증가 (SFN 전이 무관)
#   - 그러나 Lambda 내부에서 sub_node_count × max_iterations 만큼 실행 발생
#   - 이 내부 복잡도를 무시하면 limit이 너무 낮아 테스트/안전 기준 미달
#   - 따라서 Lambda-internal 복잡도 예산: sub_node_count × max_iterations
#
# [분석] sequential loop 처리 방식:
#   - 각 반복마다 loop body 세그먼트들이 실제 SFN CONTINUE 전이 발생
#   - weight = segment_count × (max_iter - 1) (첫 번째 반복은 base count에 포함)
#
# [공식] loop_limit = max(int(raw * 1.5) + 20, 50)
#   - raw = base_segments + sequential_loop_weight + for_each_complexity_budget
#   - 1.5x: API 재시도·마이너 세그먼트 분할 여유
#   - +20: 규모 무관 최소 완충
#   - floor=50: 실질적 무한루프 차단

# 최종 estimated_executions에 곱할 안전 배수 (1.5x)
LOOP_LIMIT_SAFETY_MULTIPLIER: float = float(os.environ.get("LOOP_LIMIT_SAFETY_MULTIPLIER", "1.5"))

# loop_limit 고정 보너스 — 규모 무관하게 최소 완충 보장
LOOP_LIMIT_FLAT_BONUS: int = int(os.environ.get("LOOP_LIMIT_FLAT_BONUS", "20"))

# loop_limit 절대 하한선 — 어떤 경우에도 이 값 이상을 보장
LOOP_LIMIT_FLOOR: int = int(os.environ.get("LOOP_LIMIT_FLOOR", "50"))


# ============================================================================
# [Critical Fix #1] 사이클 감지 예외 및 DAG 검증
# ============================================================================

class CycleDetectedError(Exception):
    """그래프에서 사이클(순환 참조)이 감지되었을 때 발생하는 예외"""
    def __init__(self, cycle_path: List[str]):
        self.cycle_path = cycle_path
        super().__init__(
            f"Cycle detected in workflow graph: {' -> '.join(cycle_path)}. "
            f"Workflows must be DAGs (Directed Acyclic Graphs)."
        )


class PartitionDepthExceededError(Exception):
    """파티셔닝 재귀 깊이 초과 시 발생하는 예외"""
    def __init__(self, depth: int, max_depth: int):
        self.depth = depth
        self.max_depth = max_depth
        super().__init__(
            f"Partition recursion depth ({depth}) exceeded maximum ({max_depth}). "
            f"Consider simplifying the workflow or increasing MAX_PARTITION_DEPTH."
        )


class BranchTerminationError(Exception):
    """브랜치가 올바르게 종료되지 않았을 때 발생하는 예외"""
    def __init__(self, branch_id: str, message: str):
        self.branch_id = branch_id
        super().__init__(f"Branch '{branch_id}' termination error: {message}")


class AtomicGroupTimeoutError(Exception):
    """Atomic Group의 예상 실행 시간이 Lambda 제한을 초과할 때 발생하는 예외"""
    def __init__(self, group_id: str, estimated_duration: float, lambda_timeout: float):
        self.group_id = group_id
        self.estimated_duration = estimated_duration
        self.lambda_timeout = lambda_timeout
        super().__init__(
            f"Atomic Group '{group_id}' estimated duration ({estimated_duration:.1f}s) "
            f"exceeds safe limit ({lambda_timeout * 0.7:.1f}s = 70% of Lambda timeout {lambda_timeout}s). "
            f"Consider splitting the group or reducing node execution times."
        )


def validate_dag(
    nodes: Dict[str, Any], 
    outgoing_edges: Dict[str, List[Dict[str, Any]]]
) -> Tuple[bool, Optional[List[str]]]:
    """
    [Critical Fix #1] 그래프가 DAG(Directed Acyclic Graph)인지 검증합니다.
    
    Kahn's Algorithm (위상 정렬) 기반 사이클 감지.
    
    Args:
        nodes: 노드 ID -> 노드 정의 맵
        outgoing_edges: 노드 ID -> 나가는 엣지 리스트 맵
        
    Returns:
        Tuple[is_dag, cycle_path]: DAG이면 (True, None), 아니면 (False, cycle_path)
    """
    if not nodes:
        return True, None
    
    # 진입 차수(in-degree) 계산
    in_degree: Dict[str, int] = {nid: 0 for nid in nodes}
    
    for source_id, edges in outgoing_edges.items():
        for edge in edges:
            target = edge.get("target")
            if target and target in in_degree:
                in_degree[target] += 1
    
    # 진입 차수가 0인 노드들로 시작
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    visited_count = 0
    
    while queue:
        node_id = queue.pop(0)
        visited_count += 1
        
        for edge in outgoing_edges.get(node_id, []):
            target = edge.get("target")
            if target and target in in_degree:
                in_degree[target] -= 1
                if in_degree[target] == 0:
                    queue.append(target)
    
    # 모든 노드를 방문하지 못했다면 사이클 존재
    if visited_count < len(nodes):
        # 사이클 경로 추적 (DFS로 실제 사이클 찾기)
        cycle_path = _find_cycle_path(nodes, outgoing_edges)
        return False, cycle_path
    
    return True, None


def _find_cycle_path(
    nodes: Dict[str, Any], 
    outgoing_edges: Dict[str, List[Dict[str, Any]]]
) -> List[str]:
    """DFS로 실제 사이클 경로를 추적합니다."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {nid: WHITE for nid in nodes}
    parent: Dict[str, Optional[str]] = {nid: None for nid in nodes}
    
    def dfs(node_id: str, path: List[str]) -> Optional[List[str]]:
        color[node_id] = GRAY
        path.append(node_id)
        
        for edge in outgoing_edges.get(node_id, []):
            target = edge.get("target")
            if target and target in color:
                if color[target] == GRAY:
                    # 사이클 발견 - 경로 추출
                    cycle_start = path.index(target)
                    return path[cycle_start:] + [target]
                elif color[target] == WHITE:
                    result = dfs(target, path)
                    if result:
                        return result
        
        color[node_id] = BLACK
        path.pop()
        return None
    
    for nid in nodes:
        if color[nid] == WHITE:
            result = dfs(nid, [])
            if result:
                return result
    
    return ["unknown_cycle"]  # 폴백


# ============================================================================
# [Critical Fix #3] Atomic Group 타임아웃 검증
# ============================================================================

# Lambda 제한 (초)
LAMBDA_TIMEOUT_SECONDS = int(os.environ.get("LAMBDA_TIMEOUT_SECONDS", "900"))  # 15분

# 노드 타입별 평균 실행 시간 (초)
NODE_EXECUTION_ESTIMATES = {
    "llm_chat": 10.0,       # LLM 호출: 평균 10초
    "aiModel": 10.0,        # AI 모델: 평균 10초
    "api_call": 2.0,        # API 호출: 평균 2초
    "db_query": 1.0,        # DB 쿼리: 평균 1초
    "operator": 0.5,        # Operator: 평균 0.5초
    "safe_operator": 0.5,   # Safe Operator: 평균 0.5초
    "loop": 5.0,            # Loop: 평균 5초 (내부 노드 별도 계산)
    "for_each": 8.0,        # For Each: 평균 8초 (병렬 처리)
    "parallel_group": 5.0,  # Parallel Group: 평균 5초
    "aggregator": 0.3,      # Aggregator: 평균 0.3초
    "route_condition": 0.2, # Route Condition: 평균 0.2초
    "default": 1.0          # 기타: 평균 1초
}


def estimate_node_duration(node: Dict[str, Any]) -> float:
    """
    노드의 예상 실행 시간 추정
    
    Args:
        node: 노드 설정
    
    Returns:
        예상 실행 시간 (초)
    """
    node_type = node.get("type", "default")
    config = node.get("config", {})
    
    # 기본 실행 시간
    base_duration = NODE_EXECUTION_ESTIMATES.get(node_type, NODE_EXECUTION_ESTIMATES["default"])
    
    # 타입별 보정
    if node_type == "loop":
        # Loop: max_iterations 고려
        max_iterations = config.get("max_iterations", 5)
        sub_nodes = config.get("nodes", [])
        sub_duration = sum(estimate_node_duration(n) for n in sub_nodes)
        return max_iterations * sub_duration
    
    elif node_type == "for_each":
        # For Each: max_iterations 고려 (병렬 처리)
        max_iterations = config.get("max_iterations", 20)
        sub_workflow = config.get("sub_workflow", {})
        sub_nodes = sub_workflow.get("nodes", [])
        sub_duration = max(
            (estimate_node_duration(n) for n in sub_nodes),
            default=1.0
        )
        # 병렬 처리이므로 가장 긴 노드 시간만 고려
        return sub_duration
    
    elif node_type in ("llm_chat", "aiModel"):
        # LLM: max_tokens 기반 보정
        max_tokens = config.get("max_tokens", 256)
        # 토큰당 ~0.01초 추정 (GPT-4 기준)
        token_penalty = max_tokens * 0.01
        
        # Extended Thinking 활성화 시 추가 시간
        if config.get("enable_thinking", False):
            thinking_budget = config.get("thinking_budget_tokens", 4096)
            token_penalty += thinking_budget * 0.01
        
        return base_duration + token_penalty
    
    elif node_type == "api_call":
        # API Call: timeout 설정 고려
        timeout = config.get("timeout", 10)
        return min(timeout, base_duration)
    
    return base_duration


def analyze_loop_structures(nodes: List[Dict[str, Any]], node_to_seg_map: Dict[str, int] = None) -> Dict[str, Any]:
    """
    Analyze loop structures to estimate weighted execution count.
    
    🛡️ [Dynamic Loop Limit] Segment-based iteration counting
    - for_each: Adds 2 base segments (parallel_group + aggregator)
      * PLUS runtime sub_workflow execution: estimated_sub_segments × max_iterations
      * PLUS nested loop weights from sub_workflow
      * Critical: sub_workflow is PARTITIONED at runtime, creating additional segments
    - Sequential loop: Adds (internal_segment_count × (max_iterations - 1))
      * First iteration included in base segment count
      * Additional iterations = (max_iter - 1) × segment_count
    - Formula: Σ(2 + sub_segments × max_iter + nested_weights) for for_each + Σ(seg_count × (max_iter - 1)) for loops
    
    Args:
        nodes: List of workflow nodes
        node_to_seg_map: Mapping of node_id → segment_id (optional)
        
    Returns:
        {
            "loop_nodes": [...],
            "total_loop_weighted_segments": int,  # Weighted segment count
            "loop_count": int
        }
    """
    loop_nodes = []
    total_weighted = 0
    
    for node in nodes:
        node_type = node.get("type", "")
        config = node.get("config", {})
        
        if node_type == "loop":
            max_iter = config.get("max_iterations", 5)
            sub_nodes = config.get("nodes", [])
            
            # Calculate how many segments this loop's internal nodes span
            if node_to_seg_map:
                sub_node_ids = [n.get("id") for n in sub_nodes if n.get("id")]
                sub_segments = set(node_to_seg_map.get(nid) for nid in sub_node_ids if node_to_seg_map.get(nid) is not None)
                segment_count = len(sub_segments) if sub_segments else len(sub_nodes)
            else:
                # Fallback: estimate based on node count
                segment_count = max(1, len(sub_nodes))
            
            # Recursive analysis for nested loops
            sub_analysis = analyze_loop_structures(sub_nodes, node_to_seg_map)
            
            loop_nodes.append({
                "node_id": node.get("id"),
                "type": "loop",
                "max_iterations": max_iter,
                "sub_node_count": len(sub_nodes),
                "sub_segment_count": segment_count,
                "nested_loops": sub_analysis["loop_nodes"]
            })
            
            # ✅ [FIX] Segment-based counting: segment_count × (max_iter - 1)
            # Subtract 1 because total_segments already includes the first execution
            total_weighted += segment_count * (max_iter - 1) + sub_analysis["total_loop_weighted_segments"]
            
        elif node_type == "parallel_group":
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [v3.18 Fix] Inline Parallel Group → branches 재귀 탐색
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # for_each / loop 노드가 parallel_group.branches[].nodes[] 안에
            # 중첩된 경우, 상위 레벨 스캔에서는 완전히 누락된다.
            # → branches를 재귀적으로 탐색해 weighted 합산.
            #
            # [v3.18.1 Fix] branches 위치 이중 탐색:
            #   - 일부 워크플로우: branches가 노드 최상위에 위치 (STRESS 스타일)
            #   - 일부 워크플로우: branches가 config 하위에 위치 (MAP_AGGREGATOR 스타일)
            #   → 둘 다 확인하지 않으면 config.branches 안의 for_each가 누락됨
            branches = node.get("branches") or node.get("config", {}).get("branches") or []
            for branch in branches:
                branch_nodes = branch.get("nodes", [])
                if branch_nodes:
                    branch_analysis = analyze_loop_structures(branch_nodes, node_to_seg_map)
                    total_weighted += branch_analysis["total_loop_weighted_segments"]
                    loop_nodes.extend(branch_analysis["loop_nodes"])
                    logger.debug(
                        f"[Loop Analysis] parallel_group '{node.get('id')}' "
                        f"branch '{branch.get('id', '?')}': "
                        f"nested_weight={branch_analysis['total_loop_weighted_segments']}, "
                        f"nested_loops={branch_analysis['loop_count']}"
                    )

        elif node_type == "for_each":
            max_iter = config.get("max_iterations", 20)
            sub_workflow = config.get("sub_workflow", {})
            sub_nodes = sub_workflow.get("nodes", [])
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [v3.17] for_each Lambda-Internal Complexity Budget
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # for_each_runner는 Lambda 내부 ThreadPoolExecutor로 실행됨.
            # SFN loop_counter 증가: 최대 2회 (parallel_group + aggregator).
            # 그러나 Lambda 내부에서 sub_node_count × max_iterations 회 실행 발생.
            # max_loop_iterations는 SFN 전이 카운터이자 복잡도 예산이므로,
            # Lambda 내부 실행량을 complexity_budget으로 반영:
            #   self_weight = sub_node_count × max_iterations
            sub_node_count = len(sub_nodes)
            for_each_self_weight = sub_node_count * max_iter
            
            # Recursive analysis for nested loops (sequential loop inside for_each 대비)
            sub_analysis = analyze_loop_structures(sub_nodes, node_to_seg_map)
            
            loop_nodes.append({
                "node_id": node.get("id"),
                "type": "for_each",
                "max_iterations": max_iter,
                "sub_node_count": sub_node_count,
                "self_weight": for_each_self_weight,  # Lambda-internal complexity budget
                "nested_loops": sub_analysis["loop_nodes"]
            })
            
            # for_each 복잡도 예산 + 중첩 sequential loop 가중치
            for_each_weighted = for_each_self_weight + sub_analysis["total_loop_weighted_segments"]
            total_weighted += for_each_weighted
            
            logger.debug(
                f"[Loop Analysis] for_each '{node.get('id')}' (v3.17 complexity-budget): "
                f"max_iter={max_iter}, sub_nodes={sub_node_count}, "
                f"self_weight={for_each_self_weight} (Lambda-internal budget), "
                f"nested_weight={sub_analysis['total_loop_weighted_segments']}"
            )
    
    return {
        "loop_nodes": loop_nodes,
        "total_loop_weighted_segments": total_weighted,
        "loop_count": len(loop_nodes)
    }


def validate_atomic_group_timeout(
    group_id: str,
    nodes: List[Dict[str, Any]],
    lambda_timeout: float = LAMBDA_TIMEOUT_SECONDS
) -> None:
    """
    Atomic Group의 예상 실행 시간 검증
    
    Args:
        group_id: 그룹 ID
        nodes: 그룹 내 노드 목록
        lambda_timeout: Lambda 타임아웃 (초)
    
    Raises:
        AtomicGroupTimeoutError: 예상 시간이 안전 제한(70%)을 초과하는 경우
    """
    # 예상 실행 시간 합산
    total_duration = sum(estimate_node_duration(node) for node in nodes)
    
    # 안전 제한: Lambda 타임아웃의 70%
    safe_limit = lambda_timeout * 0.7
    
    logger.info(
        f"[ATOMIC_GROUP_VALIDATION] {group_id}: "
        f"{len(nodes)} nodes, estimated {total_duration:.1f}s "
        f"(limit: {safe_limit:.1f}s)"
    )
    
    if total_duration > safe_limit:
        logger.warning(
            f"[ATOMIC_GROUP_TIMEOUT_RISK] {group_id} exceeds safe limit: "
            f"{total_duration:.1f}s > {safe_limit:.1f}s"
        )
        raise AtomicGroupTimeoutError(group_id, total_duration, lambda_timeout)


def extract_atomic_groups(workflow_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    워크플로우에서 Atomic Group 추출
    
    명시적 그룹:
    - type="group", atomic=true
    
    암묵적 그룹:
    - DB 트랜잭션 패턴 (BEGIN ... COMMIT)
    - HTTP 세션 유지 패턴
    
    Args:
        workflow_config: 워크플로우 설정
    
    Returns:
        Atomic Group 목록
    """
    nodes = workflow_config.get("nodes", [])
    edges = workflow_config.get("edges", [])
    
    atomic_groups = []
    
    # 1. 명시적 그룹 추출
    for node in nodes:
        if node.get("type") == "group" and node.get("data", {}).get("atomic"):
            group_nodes = node.get("data", {}).get("nodes", [])
            atomic_groups.append({
                "group_id": node["id"],
                "nodes": [n for n in nodes if n["id"] in group_nodes],
                "is_explicit": True
            })
    
    # 2. 암묵적 그룹 감지 (DB 트랜잭션 패턴)
    implicit_groups = _detect_transaction_patterns(nodes, edges)
    atomic_groups.extend(implicit_groups)
    
    logger.info(
        f"[ATOMIC_GROUPS] Extracted {len(atomic_groups)} groups "
        f"({sum(1 for g in atomic_groups if g['is_explicit'])} explicit, "
        f"{sum(1 for g in atomic_groups if not g['is_explicit'])} implicit)"
    )
    
    return atomic_groups


def _detect_transaction_patterns(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    DB 트랜잭션 패턴 자동 감지
    
    패턴:
    1. BEGIN TRANSACTION → INSERT/UPDATE/DELETE → COMMIT
    2. START SESSION → API CALL → END SESSION
    
    Args:
        nodes: 노드 목록
        edges: 엣지 목록
    
    Returns:
        암묵적 Atomic Group 목록
    """
    implicit_groups = []
    node_map = {n["id"]: n for n in nodes}
    
    # 엣지 인접 리스트 구성
    adjacency = {n["id"]: [] for n in nodes}
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source and target:
            adjacency[source].append(target)
    
    # BEGIN → ... → COMMIT 패턴 감지
    for node in nodes:
        if node.get("type") == "db_query":
            query = node.get("config", {}).get("query", "").strip().upper()
            
            # BEGIN TRANSACTION 감지
            if "BEGIN" in query or "START TRANSACTION" in query:
                # 연결된 노드 추적
                group_nodes = []
                visited = set()
                queue = [node["id"]]
                
                while queue:
                    current_id = queue.pop(0)
                    if current_id in visited:
                        continue
                    visited.add(current_id)
                    
                    current_node = node_map.get(current_id)
                    if not current_node:
                        continue
                    
                    group_nodes.append(current_node)
                    
                    # COMMIT 발견 시 종료
                    if current_node.get("type") == "db_query":
                        current_query = current_node.get("config", {}).get("query", "").upper()
                        if "COMMIT" in current_query:
                            break
                    
                    # 다음 노드 추가
                    for next_id in adjacency.get(current_id, []):
                        if next_id not in visited:
                            queue.append(next_id)
                
                # 그룹 등록 (COMMIT 발견 시만)
                if len(group_nodes) > 1:
                    last_node = group_nodes[-1]
                    last_query = last_node.get("config", {}).get("query", "").upper()
                    if "COMMIT" in last_query:
                        implicit_groups.append({
                            "group_id": f"tx_{node['id']}",
                            "nodes": group_nodes,
                            "is_explicit": False,
                            "pattern": "db_transaction"
                        })
                        logger.info(
                            f"[TRANSACTION_PATTERN] Detected DB transaction group: "
                            f"{node['id']} → {last_node['id']} ({len(group_nodes)} nodes)"
                        )
    
    return implicit_groups


def partition_workflow_advanced(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    고급 워크플로우 분할: HITP 엣지와 LLM 노드 기반으로 세그먼트를 생성합니다.
    
    [v2.0 Production Hardening]
    - DAG(Directed Acyclic Graph) 사전 검증
    - 재귀 깊이 제한 (MAX_PARTITION_DEPTH)
    - 합류점(Convergence Node) 강제 분할
    - 브랜치 종료 검증
    - Thread-safe ID 생성
    
    개선된 알고리즘:
    - 병합 지점(Merge Point) 감지 및 처리
    - 병렬 그룹(Parallel Group) 생성 및 재귀적 파티셔닝
    - Convergence Node 찾기 및 브랜치 제한
    - 재귀적 Node-to-Segment 매핑
    
    Raises:
        CycleDetectedError: 그래프에 사이클이 있는 경우
        PartitionDepthExceededError: 재귀 깊이 초과 시
        ValueError: 노드 수 제한 초과 시
    """
    # 🛡️ [v3.8] None defense: filter out None elements from nodes list
    raw_nodes = config.get("nodes", [])
    nodes = {n["id"]: n for n in raw_nodes if n is not None and isinstance(n, dict) and "id" in n}
    edges = config.get("edges", []) if config.get("edges") else []
    
    # [Critical Fix] 노드 수 제한 검증
    if len(nodes) > MAX_NODES_LIMIT:
        raise ValueError(
            f"Workflow has {len(nodes)} nodes, exceeding maximum limit of {MAX_NODES_LIMIT}. "
            f"Consider splitting into subgraphs."
        )
    
    # 🚨 [Performance Warning] 100개 노드 초과 시 경고
    # Lambda 실행 시간(15분)보다 latency가 먼저 문제될 수 있음
    # 복잡한 그래프일 경우 위상 정렬/사이클 감지 단계에서 지연 발생
    performance_warnings = []
    if len(nodes) > PERFORMANCE_WARNING_NODE_COUNT:
        warning_msg = (
            f"⚠️ Workflow has {len(nodes)} nodes (threshold: {PERFORMANCE_WARNING_NODE_COUNT}). "
            f"Complex graphs may experience increased latency during topological sort and cycle detection. "
            f"Consider splitting into smaller subworkflows for better performance."
        )
        performance_warnings.append({
            "type": "high_node_count",
            "severity": "warning",
            "node_count": len(nodes),
            "threshold": PERFORMANCE_WARNING_NODE_COUNT,
            "message": warning_msg
        })
        logger.warning(warning_msg)
    
    # [Performance Optimization] 엣지 맵 생성 (Pre-indexed)
    # 향후 워크플로우 저장 시점에 메타데이터로 추출하여 재사용 가능
    incoming_edges: Dict[str, List[Dict[str, Any]]] = {}
    outgoing_edges: Dict[str, List[Dict[str, Any]]] = {}
    
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target") 
        if source:
            outgoing_edges.setdefault(source, []).append(edge)
        if target:
            incoming_edges.setdefault(target, []).append(edge)
    
    # [Critical Fix #1] DAG 검증 - 사이클 감지
    is_dag, cycle_path = validate_dag(nodes, outgoing_edges)
    if not is_dag:
        raise CycleDetectedError(cycle_path or ["unknown"])
    
    # [Critical Fix #3] Atomic Group 타임아웃 검증
    atomic_groups = extract_atomic_groups(config)
    for group in atomic_groups:
        try:
            validate_atomic_group_timeout(
                group_id=group["group_id"],
                nodes=group["nodes"],
                lambda_timeout=LAMBDA_TIMEOUT_SECONDS
            )
        except AtomicGroupTimeoutError as e:
            # 경고만 기록하고 계속 진행 (사용자가 위험 감수 가능)
            logger.warning(
                f"[ATOMIC_GROUP_WARNING] {e.group_id}: {e.estimated_duration:.1f}s "
                f"exceeds safe limit {e.lambda_timeout * 0.7:.1f}s. Consider optimizing."
            )
            # 엄격 모드에서는 예외 발생
            if os.environ.get("STRICT_ATOMIC_GROUP_VALIDATION", "false").lower() == "true":
                raise
    
    # [Critical Fix #2] 합류점 집합 - 이 노드들은 반드시 새 세그먼트 시작점이 됨
    # find_convergence_node로 찾은 모든 합류점을 미리 수집
    forced_segment_starts: Set[str] = set()
    
    # [Performance Optimization] Thread-safe ID 생성기
    class ThreadSafeIdGenerator:
        def __init__(self): 
            self._val = -1
            self._lock = threading.Lock()
        
        def next(self) -> int:
            with self._lock:
                self._val += 1
                return self._val
        
        @property
        def current(self) -> int:
            return self._val
    
    seg_id_gen = ThreadSafeIdGenerator()
    stats = {"llm": 0, "hitp": 0, "parallel_groups": 0, "branches": 0}
    
    # --- Helper: 합류 지점(Convergence Node) 찾기 ---
    def find_convergence_node(start_nodes: List[str]) -> Optional[str]:
        """
        브랜치들이 공통으로 도달하는 첫 번째 Merge Point를 찾습니다.
        in-degree > 1인 노드를 후보로 봅니다.
        
        [Critical Fix #2] 찾은 합류점은 forced_segment_starts에 등록되어
        반드시 새 세그먼트의 시작점이 됩니다.
        """
        queue = list(start_nodes)
        seen = set(queue)
        
        while queue:
            node_id = queue.pop(0)
            # Merge Point 후보 확인
            if len(incoming_edges.get(node_id, [])) > 1:
                if node_id not in start_nodes:
                    # [Critical Fix #2] 합류점은 반드시 새 세그먼트 시작점
                    forced_segment_starts.add(node_id)
                    logger.debug(f"Convergence node registered as forced segment start: {node_id}")
                    return node_id
            
            for out_edge in outgoing_edges.get(node_id, []):
                target = out_edge.get("target")
                if target and target not in seen:
                    seen.add(target)
                    queue.append(target)
        return None
    
    # --- [Critical Fix] 위상 정렬 헬퍼 ---
    def _topological_sort_nodes(nodes_map: Dict[str, Any], edges_list: List[Dict]) -> List[Dict[str, Any]]:
        """
        세그먼트 내 노드들을 위상 정렬하여 실행 순서대로 반환합니다.
        
        DynamicWorkflowBuilder는 nodes[0]을 entry point로 사용하므로,
        첫 번째 노드가 실제 시작 노드가 되어야 합니다.
        
        Args:
            nodes_map: {node_id: node_config} 매핑
            edges_list: 세그먼트 내부 엣지 리스트
            
        Returns:
            위상 정렬된 노드 설정 리스트
        """
        if len(nodes_map) <= 1:
            return list(nodes_map.values())
        
        # 세그먼트 내 노드 ID 집합
        node_ids = set(nodes_map.keys())
        
        # 인접 리스트 및 진입 차수(in-degree) 계산
        in_degree = {nid: 0 for nid in node_ids}
        adj = {nid: [] for nid in node_ids}
        
        for edge in edges_list:
            src = edge.get("source")
            tgt = edge.get("target")
            if src in node_ids and tgt in node_ids:
                adj[src].append(tgt)
                in_degree[tgt] += 1
        
        # Kahn's Algorithm: 진입 차수가 0인 노드부터 시작
        # heapq로 최솟값 추출 O(log n): 매 반복 queue.sort() O(n log n) 제거 → 전체 O(n log n)
        # 결정론적 순서 보장: heapq는 항상 알파벳 최솟값 노드 ID를 반환
        queue = sorted([nid for nid in node_ids if in_degree[nid] == 0])
        heapq.heapify(queue)
        sorted_ids = []

        while queue:
            node_id = heapq.heappop(queue)
            sorted_ids.append(node_id)

            for neighbor in adj[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    heapq.heappush(queue, neighbor)
        
        # 정렬되지 않은 노드가 있으면 (사이클 또는 연결 안됨) 원래 순서로 추가
        if len(sorted_ids) < len(node_ids):
            remaining = [nid for nid in nodes_map.keys() if nid not in sorted_ids]
            logger.warning(f"Some nodes not topologically sorted, appending in original order: {remaining}")
            sorted_ids.extend(remaining)
        
        result = [nodes_map[nid] for nid in sorted_ids]
        logger.debug(f"Topological sort result: {[n.get('id') for n in result]}")
        return result
    
    # --- Segment 생성 헬퍼 ---
    def create_segment(nodes_map, edges_list, s_type="normal", override_id=None, config=None):
        # 🛡️ [v2.6 P0 Fix] 'code' 타입 강제 정정 - ValueError 방지
        # 상위 레이어(프론트엔드, DB 등)에서 잘못된 타입이 들어올 수 있으므로 여기서 교정
        for node_id, node in nodes_map.items():
            if isinstance(node, dict) and node.get("type") == "code":
                logger.warning(
                    f"🛡️ [Kernel Defense] Fixing 'code' type to 'operator' for node {node_id} "
                    f"in partition_service.create_segment"
                )
                node["type"] = "operator"
        
        # [P0 Refactoring] Inter-segment edges 수집
        outgoing_edges = []
        if config:
            all_edges = config.get("edges", [])
            for edge in all_edges:
                source = edge.get("source")
                target = edge.get("target")
                
                # Intra-segment edge (양쪽 노드가 모두 이 세그먼트에 있음)
                if source in nodes_map and target in nodes_map:
                    if edge not in edges_list:  # 중복 방지
                        edges_list.append(edge)
                
                # Inter-segment edge (source만 이 세그먼트에 있고 target은 다른 세그먼트)
                elif source in nodes_map and target not in nodes_map:
                    edge_data = edge.get("data", {})
                    outgoing_edges.append({
                        "source_node": source,
                        "target_node": target,
                        "edge_type": edge.get("type", "normal"),
                        # ❌ REMOVED: condition, router_func, mapping (라우팅 주권 일원화)
                        # 이유: 모든 라우팅 결정은 노드가 수행 (route_condition, __next_node)
                        "is_loop_exit": edge_data.get("isLoopExit", False),
                        "is_back_edge": edge_data.get("isBackEdge", False),
                        "metadata": {
                            "label": edge.get("label"),
                            "style": edge.get("style"),
                            "animated": edge.get("animated"),
                            "edgeType": edge_data.get("edgeType"),
                            "loopType": edge_data.get("loopType")
                        }
                    })
        
        # [Critical Fix] 노드 순서를 위상 정렬하여 첫 번째 노드가 실제 시작 노드가 되도록 보장
        # DynamicWorkflowBuilder는 nodes[0]을 entry point로 사용하므로 순서가 중요함
        sorted_nodes = _topological_sort_nodes(nodes_map, edges_list)
        
        final_type = s_type
        if s_type == "normal":
            if any(n.get("hitp") in [True, "true"] for n in nodes_map.values()):
                final_type = "hitp"
        
        if final_type == "llm": 
            stats["llm"] += 1
        elif final_type == "hitp": 
            stats["hitp"] += 1
        
        logger.debug(f"[Segment Created] ID={seg_id_gen.current}, Type={final_type}, "
                    f"Nodes={len(sorted_nodes)}, IntraEdges={len(edges_list)}, "
                    f"OutgoingEdges={len(outgoing_edges)}")
            
        return {
            "id": override_id if override_id is not None else seg_id_gen.next(),
            "nodes": sorted_nodes,  # [Critical Fix] 위상 정렬된 노드 사용
            "edges": list(edges_list),
            "outgoing_edges": outgoing_edges,  # [P0 Refactoring] Inter-segment edges
            "type": final_type,
            "node_ids": [n["id"] for n in sorted_nodes]  # 정렬된 순서 반영
        }
    
    # --- 재귀적 파티셔닝 로직 ---
    visited_nodes: Set[str] = set()
    
    def run_partitioning(
        start_node_ids: List[str], 
        stop_at_nodes: Set[str] = None, 
        config=None,
        depth: int = 0  # [Critical Fix #1] 재귀 깊이 추적
    ) -> List[Dict[str, Any]]:
        """
        재귀적 파티셔닝 로직.
        
        [Critical Fix #1] depth 파라미터로 재귀 깊이 제한
        [Critical Fix #2] forced_segment_starts로 합류점 강제 분할
        """
        # [Critical Fix #1] 재귀 깊이 제한 검사
        if depth > MAX_PARTITION_DEPTH:
            raise PartitionDepthExceededError(depth, MAX_PARTITION_DEPTH)
        
        local_segments = []
        local_current_nodes = {}
        local_current_edges = []
        queue = list(start_node_ids)
        
        # [Critical Fix #1] 무한 루프 방지용 반복 카운터
        max_iterations = len(nodes) * 2  # 안전 마진
        iteration_count = 0
        
        def flush_local(seg_type="normal"):
            nonlocal local_current_nodes, local_current_edges
            if local_current_nodes or local_current_edges:
                seg = create_segment(local_current_nodes, local_current_edges, seg_type, config=config)
                local_segments.append(seg)
                local_current_nodes = {}
                local_current_edges = []
        
        while queue:
            # [Critical Fix #1] 무한 루프 방지
            iteration_count += 1
            if iteration_count > max_iterations:
                logger.error(
                    f"Partition iteration limit exceeded ({max_iterations}). "
                    f"Possible infinite loop. Queue: {queue[:5]}..."
                )
                raise PartitionDepthExceededError(iteration_count, max_iterations)
            
            node_id = queue.pop(0)
            
            # Stop Condition
            if node_id in visited_nodes: 
                continue
            if stop_at_nodes and node_id in stop_at_nodes: 
                continue
            
            # [Safety] 노드가 존재하는지 확인
            if node_id not in nodes:
                logger.warning(f"Node '{node_id}' referenced but not found in nodes map. Skipping.")
                continue
            
            node = nodes[node_id]
            
            # 트리거 조건 계산
            in_edges = incoming_edges.get(node_id, [])
            non_hitp_in = [e for e in in_edges if e.get("type") not in HITP_EDGE_TYPES]
            
            is_hitp_start = any(e.get("type") in HITP_EDGE_TYPES for e in in_edges)
            is_llm = node.get("type") in LLM_NODE_TYPES
            is_merge = len(non_hitp_in) > 1
            is_branch = len(outgoing_edges.get(node_id, [])) > 1
            
            # 🛡️ [v3.8] 인라인 parallel_group 노드 감지
            # 노드 자체가 type="parallel_group"이고 branches를 포함하는 경우
            is_inline_parallel = (
                node.get("type") == "parallel_group" and 
                isinstance(node.get("branches"), list) and
                len(node.get("branches", [])) > 0
            )
            
            # [Critical Fix #2] 합류점은 반드시 새 세그먼트 시작
            is_forced_start = node_id in forced_segment_starts
            
            # 세그먼트 분할 트리거 (is_forced_start 추가)
            if (is_hitp_start or is_llm or is_merge or is_branch or is_forced_start or is_inline_parallel) and local_current_nodes:
                if node_id not in local_current_nodes:
                    flush_local("normal")
            
            # 🛡️ [v3.8] 인라인 parallel_group 노드 처리 (우선순위 높음)
            if is_inline_parallel:
                flush_local("normal")  # 현재까지 저장
                visited_nodes.add(node_id)
                
                # 인라인 branches를 그대로 사용
                inline_branches = node.get("branches", [])
                branches_data = []
                
                for i, branch in enumerate(inline_branches):
                    if branch is None:
                        logger.warning(f"🛡️ [Self-Healing] Skipping None branch in inline parallel_group {node_id}")
                        continue
                    
                    branch_id = branch.get("id", f"B{i}")
                    branch_nodes = branch.get("nodes", [])
                    branch_edges = branch.get("edges", [])
                    
                    # 브랜치 내부를 서브 파티션으로 처리
                    branch_partition = []
                    if branch_nodes:
                        # 브랜치 내부 노드들로 세그먼트 생성
                        branch_nodes_map = {}
                        for bn in branch_nodes:
                            if bn is not None and isinstance(bn, dict) and "id" in bn:
                                branch_nodes_map[bn["id"]] = bn
                        
                        if branch_nodes_map:
                            branch_seg = create_segment(
                                branch_nodes_map, 
                                branch_edges, 
                                "normal",
                                config={"nodes": branch_nodes, "edges": branch_edges}
                            )
                            branch_partition.append(branch_seg)
                    
                    branch_data = {
                        "branch_id": branch_id,
                        "partition_map": branch_partition,
                        "has_end": False,
                        "target_node": branch_nodes[0].get("id") if branch_nodes else None
                    }
                    branches_data.append(branch_data)
                    stats["branches"] += 1
                
                # Parallel Group 세그먼트 생성
                if branches_data:
                    stats["parallel_groups"] += 1
                    p_seg_id = seg_id_gen.next()
                    parallel_seg = {
                        "id": p_seg_id,
                        "type": "parallel_group",
                        "branches": branches_data,
                        "node_ids": [node_id],
                        "branch_count": len(branches_data),
                        "resource_policy": node.get("resource_policy", {}),  # 원본 resource_policy 보존
                        "label": node.get("label", "")
                    }
                    local_segments.append(parallel_seg)
                    
                    # Aggregator 생성
                    agg_seg_id = seg_id_gen.next()
                    aggregator_seg = {
                        "id": agg_seg_id,
                        "type": "aggregator",
                        "nodes": [],
                        "edges": [],
                        "node_ids": [],
                        "source_parallel_group": p_seg_id
                    }
                    local_segments.append(aggregator_seg)
                    
                    # next 설정
                    parallel_seg["next_mode"] = "default"
                    parallel_seg["default_next"] = agg_seg_id
                
                # 다음 노드 탐색
                for out_edge in outgoing_edges.get(node_id, []):
                    tgt = out_edge.get("target")
                    if tgt and tgt not in visited_nodes and tgt not in queue:
                        if not (stop_at_nodes and tgt in stop_at_nodes):
                            queue.append(tgt)
                continue
            
            # 병렬 그룹 처리 (그래프 분기점 기반)
            if is_branch:
                flush_local("normal")  # 현재까지 저장
                
                # 분기점 노드 처리
                seg = create_segment({node_id: node}, [], "normal", config=config) 
                local_segments.append(seg)
                visited_nodes.add(node_id)
                
                out_edges = outgoing_edges.get(node_id, [])
                branch_targets = [e.get("target") for e in out_edges if e.get("target")]
                
                # 합류점 찾기 - [Critical Fix #2] 합류점이 forced_segment_starts에 등록됨
                convergence_node = find_convergence_node(branch_targets)
                stop_set = {convergence_node} if convergence_node else set()
                
                # 각 브랜치 실행
                branches_data = []
                for i, target in enumerate(branch_targets):
                    if target:
                        # [Critical Fix #1] 재귀 깊이 전달
                        branch_segs = run_partitioning(
                            [target], 
                            stop_at_nodes=stop_set, 
                            config=config,
                            depth=depth + 1
                        )
                        
                        # [Critical Fix #3] 브랜치 종료 검증
                        if branch_segs:
                            last_seg = branch_segs[-1]
                            # 브랜치 메타데이터 추가
                            branch_data = {
                                "branch_id": f"B{i}",
                                "partition_map": branch_segs,
                                "has_end": last_seg.get("next_mode") == "end",
                                "target_node": target
                            }
                            branches_data.append(branch_data)
                            stats["branches"] += 1
                        else:
                            # 빈 브랜치 경고
                            logger.warning(f"Branch {i} starting at {target} produced no segments")
                
                # Parallel Group 생성
                if branches_data:
                    stats["parallel_groups"] += 1
                    p_seg_id = seg_id_gen.next()
                    parallel_seg = {
                        "id": p_seg_id,
                        "type": "parallel_group",
                        "branches": branches_data,
                        "node_ids": [],
                        "branch_count": len(branches_data)  # [추가] 브랜치 수 메타데이터
                    }
                    local_segments.append(parallel_seg)
                    
                    # Aggregator 생성
                    agg_seg_id = seg_id_gen.next()
                    aggregator_seg = {
                        "id": agg_seg_id,
                        "type": "aggregator",
                        "nodes": [],
                        "edges": [],
                        "node_ids": [],
                        "convergence_node": convergence_node,  # 합류 노드 저장
                        "source_parallel_group": p_seg_id  # [추가] 원본 parallel_group 참조
                    }
                    local_segments.append(aggregator_seg)
                    
                    # Parallel Group의 next 설정
                    parallel_seg["next_mode"] = "default"
                    parallel_seg["default_next"] = agg_seg_id
                
                # 합류점이 있다면 큐에 추가
                if convergence_node and convergence_node not in visited_nodes:
                    queue.append(convergence_node)
                continue
            
            # 일반 노드 처리
            local_current_nodes[node_id] = node
            visited_nodes.add(node_id)
            
            # 특수 타입 처리 - HITP가 LLM보다 우선순위 높음 (HITP는 인간 개입 필요)
            if is_hitp_start:
                flush_local("hitp")
            elif is_llm:
                flush_local("llm")
            
            # 다음 노드 탐색
            for out_edge in outgoing_edges.get(node_id, []):
                tgt = out_edge.get("target")
                if tgt and tgt not in visited_nodes and tgt not in queue:
                    if not (stop_at_nodes and tgt in stop_at_nodes):
                        queue.append(tgt)
        
        flush_local()  # 남은 것 처리
        return local_segments
    
    # 시작 노드 찾기 및 실행
    start_nodes = [nid for nid in nodes if not incoming_edges.get(nid)]
    if not start_nodes and nodes: 
        start_nodes = [list(nodes.keys())[0]]
    
    segments = run_partitioning(start_nodes, config=config)
    
    # --- Pass 2: 재귀적 Node-to-Segment 매핑 ---
    node_to_seg_map = {}
    
    def map_nodes_recursive(seg_list):
        for seg in seg_list:
            for nid in seg.get("node_ids", []):
                node_to_seg_map[nid] = seg["id"]
            
            if seg["type"] == "parallel_group":
                for branch in seg["branches"]:
                    map_nodes_recursive(branch["partition_map"])
    
    map_nodes_recursive(segments)
    
    # --- Next Mode 설정 (재귀적) ---
    def process_links_recursive(seg_list: List[Dict[str, Any]], parent_aggregator_id: Optional[int] = None):
        """
        세그먼트 간 연결(next_mode) 설정.
        
        [Critical Fix #3] 브랜치 내부 세그먼트가 올바르게 종료되는지 검증.
        parent_aggregator_id가 주어지면, 브랜치 내 마지막 세그먼트는 이 aggregator로 연결되어야 함.
        """
        # Aggregator 세그먼트들의 ID 집합을 미리 파악
        aggregator_ids = {s["id"] for s in seg_list if s.get("type") == "aggregator"}
        
        for idx, seg in enumerate(seg_list):
            if seg["type"] == "parallel_group":
                # parallel_group 다음의 aggregator ID 찾기
                next_agg_id = seg.get("default_next")
                
                for branch in seg["branches"]:
                    branch_segs = branch.get("partition_map", [])
                    
                    # [Critical Fix #3] 브랜치 내부 재귀 처리 - aggregator ID 전달
                    process_links_recursive(branch_segs, parent_aggregator_id=next_agg_id)
                    
                    # [Critical Fix #3] 브랜치 마지막 세그먼트 검증
                    if branch_segs:
                        last_branch_seg = branch_segs[-1]
                        
                        # 마지막 세그먼트가 명시적 END가 아니고 next가 없으면
                        # aggregator로 암묵적 연결 설정
                        if last_branch_seg.get("next_mode") == "end" and next_agg_id:
                            # 비대칭 브랜치: 한 쪽은 끝나고 다른 쪽은 합류
                            # aggregator에서 이를 처리할 수 있도록 메타데이터 추가
                            last_branch_seg["implicit_aggregator_target"] = next_agg_id
                            branch["terminates_early"] = True
                            logger.debug(
                                f"Branch {branch['branch_id']} terminates early. "
                                f"Implicit aggregator target: {next_agg_id}"
                            )
                continue
            
            # Aggregator의 경우 convergence_node를 사용해 다음 세그먼트 연결
            if seg.get("type") == "aggregator":
                convergence_node = seg.get("convergence_node")
                source_p_seg_id = seg.get("source_parallel_group")
                
                # Case 1: Branch Convergence (Explicit Logic)
                if convergence_node and convergence_node in node_to_seg_map:
                    next_seg_id = node_to_seg_map[convergence_node]
                    seg["next_mode"] = "default"
                    seg["default_next"] = next_seg_id
                    continue
                
                # Case 2: Inline Parallel Group (Source Node Logic)
                # Aggregator created from inline parallel group should follow the parallel group node's edges
                elif source_p_seg_id is not None:
                    # Find source parallel segment
                    # Note: seg_list might be partial (recursive), but source_p_seg should be in the same list or parent?
                    # Actually for inline parallel, they are siblings in the same list.
                    source_seg = next((s for s in seg_list if s["id"] == source_p_seg_id), None)
                    
                    if source_seg and source_seg.get("node_ids"):
                        p_node_id = source_seg["node_ids"][0] # Parallel group node ID
                        
                        # Find target segment from outgoing edges of the parallel group node
                        # Similar to normal node exit logic
                        p_exit_edges = []
                        for out_edge in outgoing_edges.get(p_node_id, []):
                            tgt = out_edge.get("target")
                            if tgt and tgt in node_to_seg_map:
                                tgt_seg = node_to_seg_map[tgt]
                                if tgt_seg != source_p_seg_id and tgt_seg != seg["id"]:
                                     p_exit_edges.append({"edge": out_edge, "target_segment": tgt_seg})
                        
                        if p_exit_edges:
                            if len(p_exit_edges) == 1:
                                seg["next_mode"] = "default"
                                seg["default_next"] = p_exit_edges[0]["target_segment"]
                            else:
                                # [v3.27 Fix] Edge.condition 제거로 인한 수정
                                # parallel_group의 다중 exit edge도 동일하게 처리
                                logger.warning(
                                    f"[Partition] Parallel group segment {seg['id']} has {len(p_exit_edges)} "
                                    f"exit edges. Using default routing to first exit."
                                )
                                seg["next_mode"] = "default"
                                seg["default_next"] = p_exit_edges[0]["target_segment"]
                            continue

                # Fallback / Error Handling
                if convergence_node:
                    # [Critical Fix #2] 합류점이 맵에 없으면 강제로 찾기 시도
                    if convergence_node in forced_segment_starts:
                        logger.error(
                            f"Aggregator {seg['id']} has convergence node '{convergence_node}' "
                            f"which is a forced segment start but not mapped. "
                            f"This indicates a partitioning logic error."
                        )
                    else:
                        logger.warning(
                            f"Aggregator {seg['id']} has convergence node '{convergence_node}' "
                            f"but it is not mapped to any segment. Treating as workflow end."
                        )
                
                seg["next_mode"] = "end"
                seg["default_next"] = None
                continue
            
            exit_edges = []
            for nid in seg.get("node_ids", []):
                for out_edge in outgoing_edges.get(nid, []):
                    tgt = out_edge.get("target")
                    if tgt and tgt in node_to_seg_map:
                        tgt_seg = node_to_seg_map[tgt]
                        
                        # 타겟이 현재 세그먼트와 다르고
                        if tgt_seg != seg["id"]:
                            # 만약 타겟이 Aggregator라면, 브랜치 내부에서는 이를 연결하지 않음
                            # (ASL Map State가 끝나고 자연스럽게 넘어가도록 함)
                            if tgt_seg in aggregator_ids:
                                continue 
                                
                            exit_edges.append({"edge": out_edge, "target_segment": tgt_seg})
            
            if not exit_edges:
                # [Critical Fix #3] 부모 aggregator가 있으면 암묵적 연결
                if parent_aggregator_id is not None:
                    seg["next_mode"] = "implicit_aggregator"
                    seg["default_next"] = None
                    seg["parent_aggregator"] = parent_aggregator_id
                else:
                    seg["next_mode"] = "end"
                    seg["default_next"] = None
            elif len(exit_edges) == 1:
                seg["next_mode"] = "default"
                seg["default_next"] = exit_edges[0]["target_segment"]
            else:
                # [v3.27 Fix] Edge에서 condition 필드 제거됨 (라우팅 주권 일원화)
                # 다중 exit edge는 route_condition 노드가 처리해야 함
                # 세그먼트 레벨에서는 첫 번째 exit edge로 default routing
                logger.warning(
                    f"[Partition] Segment {seg['id']} has {len(exit_edges)} exit edges "
                    f"but Edge.condition field is removed. Using default routing to first exit. "
                    f"Consider using route_condition node for conditional branching."
                )
                seg["next_mode"] = "default"
                seg["default_next"] = exit_edges[0]["target_segment"]
    
    process_links_recursive(segments)
    
    # --- 재귀적 세그먼트 수 계산 ---
    def count_segments_recursive(seg_list):
        total = 0
        for seg in seg_list:
            total += 1
            if seg.get("type") == "parallel_group":
                for branch in seg.get("branches", []):
                    total += count_segments_recursive(branch.get("partition_map", []))
        return total
    
    total_segments_recursive = count_segments_recursive(segments)
    
    # 🛡️ [Critical Fix] Step Functions Loop Control requires Top-Level Count
    # execution_segments_count must be defined BEFORE use in loop limit calculation
    # It must match len(partition_map), otherwise loop will try to access non-existent indices.
    execution_segments_count = len(segments)
    
    # 🛡️ [P2 Fix] execution_segments_count가 0이면 최소 1로 보장 (빈 워크플로우 방어)
    if execution_segments_count < 1:
        logger.warning(f"execution_segments_count calculated as {execution_segments_count}, forcing to 1")
        execution_segments_count = 1
    
    # 🛡️ [Dynamic Loop Limit] Analyze loop structures for segment-based counting
    # nodes is Dict[str, Dict], but analyze_loop_structures expects List[Dict]
    loop_analysis = analyze_loop_structures(list(nodes.values()), node_to_seg_map)
    
    # [v3.17] Complexity-budget loop limit calculation
    # for_each: Lambda-internal budget = sub_node_count × max_iterations
    # sequential loop: SFN CONTINUE 전이 = segment_count × (max_iter - 1)
    # 두 가중치 모두 raw에 합산 → 복잡도 예산 기반 limit 계산
    weighted_loop_segments = loop_analysis["total_loop_weighted_segments"]
    raw_estimated_executions = execution_segments_count + weighted_loop_segments
    
    # loop_limit = max(raw × 1.5 + 20, 50)
    # - 1.5x: API 재시도·마이너 분할 여유
    # - +20: 규모 무관 최소 완충
    # - floor=50: 실질적 무한루프 차단
    estimated_executions = max(
        int(raw_estimated_executions * LOOP_LIMIT_SAFETY_MULTIPLIER) + LOOP_LIMIT_FLAT_BONUS,
        LOOP_LIMIT_FLOOR
    )
    
    logger.info(
        f"[Dynamic Loop Limit] Complexity-budget analysis (v3.17): "
        f"base_segments={execution_segments_count}, "
        f"loop_count={loop_analysis['loop_count']}, "
        f"total_complexity_weight={weighted_loop_segments}, "
        f"raw_estimate={raw_estimated_executions}, "
        f"formula=max(int({raw_estimated_executions}*{LOOP_LIMIT_SAFETY_MULTIPLIER})+{LOOP_LIMIT_FLAT_BONUS}, {LOOP_LIMIT_FLOOR}), "
        f"estimated_executions={estimated_executions}"
    )
    
    # [Performance Optimization] Pre-indexed 메타데이터 반환
    return {
        "partition_map": segments,
        "total_segments": execution_segments_count,  # [Fix] Use top-level count for execution loop
        "llm_segments": stats["llm"],
        "hitp_segments": stats["hitp"],
        # [v2.0] 추가 통계
        "parallel_groups": stats["parallel_groups"],
        "total_branches": stats["branches"],
        "forced_segment_starts": list(forced_segment_starts),
        # [Performance] Pre-indexed 데이터 (재사용 가능)
        "node_to_segment_map": node_to_seg_map,
        # 🛡️ [Dynamic Loop Limit] Loop analysis results
        "loop_analysis": loop_analysis,
        "estimated_executions": estimated_executions,
        # 🚨 [Performance Warnings] 대규모 워크플로우 경고
        "performance_warnings": performance_warnings,
        "metadata": {
            "max_partition_depth": MAX_PARTITION_DEPTH,
            "max_nodes_limit": MAX_NODES_LIMIT,
            "performance_warning_threshold": PERFORMANCE_WARNING_NODE_COUNT,
            "nodes_processed": len(visited_nodes),
            "total_nodes": len(nodes),
            "total_segments_recursive": total_segments_recursive,  # [Fix] Store recursive count in metadata
            "loop_nodes_count": loop_analysis["loop_count"],
            "weighted_execution_estimate": estimated_executions,
            "has_performance_warnings": len(performance_warnings) > 0,
            # [v3.17] Complexity-Budget Loop Limit metadata
            "raw_estimated_executions": raw_estimated_executions,
            "loop_limit_safety_multiplier": LOOP_LIMIT_SAFETY_MULTIPLIER,
            "loop_limit_flat_bonus": LOOP_LIMIT_FLAT_BONUS,
            "loop_limit_floor": LOOP_LIMIT_FLOOR,
        }
    }


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    PartitionWorkflow Lambda: 워크플로우를 지능적으로 분할합니다.
    
    [v2.0 Production Hardening]
    - DAG 검증 실패 시 명확한 에러 반환
    - 재귀 깊이 초과 시 에러 반환
    - 노드 수 제한 초과 시 에러 반환
    
    Input event:
        - workflow_config: 분할할 워크플로우 설정
        - ownerId: 소유자 ID (보안/로깅용)
        
    Output:
        - partition_result: partition_workflow_advanced() 결과
        - status: "success" | "error"
    """
    try:
        workflow_config = event.get("workflow_config")
        if not workflow_config:
            raise ValueError("workflow_config is required")
        
        owner_id = event.get("ownerId") or event.get("owner_id") or event.get("user_id")
        
        # 워크플로우 분할 실행
        partition_result = partition_workflow_advanced(workflow_config)
        
        logger.info(
            "Partitioned workflow for owner=%s: %d total segments "
            "(%d LLM, %d HITP, %d parallel groups, %d branches)", 
            owner_id,
            partition_result["total_segments"],
            partition_result["llm_segments"], 
            partition_result["hitp_segments"],
            partition_result.get("parallel_groups", 0),
            partition_result.get("total_branches", 0)
        )
        
        # 🛡️ [Critical Fix] 반환 구조 평탄화 - Step Functions ASL이 $.Payload.total_segments를 직접 참조할 수 있도록
        # 기존: {"status": "success", "partition_result": {...}} → ASL에서 $.Payload.partition_result.total_segments로 접근 필요
        # 수정: {"status": "success", "total_segments": N, ...} → ASL에서 $.Payload.total_segments로 직접 접근 가능
        return {
            "status": "success",
            **partition_result  # 🛡️ 결과를 평탄화하여 ASL 매핑 오류 해결
        }
    
    except CycleDetectedError as e:
        logger.error(f"Cycle detected in workflow: {e.cycle_path}")
        return {
            "status": "error",
            "error_type": "CycleDetectedError",
            "error_message": str(e),
            "cycle_path": e.cycle_path,
            "total_segments": 1,  # 🛡️ [P0] ASL null 참조 방지
            "partition_map": []
        }
    
    except AtomicGroupTimeoutError as e:
        logger.error(
            f"Atomic Group '{e.group_id}' timeout risk: "
            f"{e.estimated_duration:.1f}s > {e.lambda_timeout * 0.7:.1f}s (70% of {e.lambda_timeout}s)"
        )
        return {
            "status": "error",
            "error_type": "AtomicGroupTimeoutError",
            "error_message": str(e),
            "group_id": e.group_id,
            "estimated_duration": e.estimated_duration,
            "lambda_timeout": e.lambda_timeout,
            "total_segments": 1,  # 🛡️ [P0] ASL null 참조 방지
            "partition_map": []
        }
    
    except PartitionDepthExceededError as e:
        logger.error(f"Partition depth exceeded: {e.depth}/{e.max_depth}")
        return {
            "status": "error",
            "error_type": "PartitionDepthExceededError",
            "error_message": str(e),
            "depth": e.depth,
            "max_depth": e.max_depth,
            "total_segments": 1,  # 🛡️ [P0] ASL null 참조 방지
            "partition_map": []
        }
    
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return {
            "status": "error",
            "error_type": "ValidationError",
            "error_message": str(e),
            "total_segments": 1,  # 🛡️ [P0] ASL null 참조 방지
            "partition_map": []
        }
        
    except Exception as e:
        logger.exception("Failed to partition workflow")
        return {
            "status": "error",
            "error_type": type(e).__name__,
            "error_message": str(e),
            "total_segments": 1,  # 🛡️ [P0] ASL null 참조 방지
            "partition_map": []
        }
