"""
Cycle Detector Service

위상 정렬 전 순환 감지
- 명시적 순환 (loop/for_each 내부) vs 암묵적 순환 (일반 엣지) 분리
- DFS 기반 순환 경로 추적
- Pre-compilation 검증
"""

import logging
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class IllegalCycleError(Exception):
    """금지된 순환 구조 발견"""
    pass


@dataclass
class CycleInfo:
    """순환 정보"""
    path: List[str]  # 순환 경로 (예: ['node_A', 'node_B', 'node_C', 'node_A'])
    cycle_type: str  # 'illegal' | 'explicit_loop' | 'explicit_for_each'
    
    def __str__(self) -> str:
        return " → ".join(self.path)


class CycleDetector:
    """
    위상 정렬 전 엄격한 순환 감지
    
    순환 정책:
    - ✅ 허용: loop/for_each 노드 내부의 sub_workflow 순환
    - ❌ 금지: 일반 엣지로 연결된 노드 간 순환 (A → B → C → A)
    """
    
    def __init__(self, nodes: List[Dict], edges: List[Dict]):
        """
        Args:
            nodes: 워크플로우 노드 목록
            edges: 워크플로우 엣지 목록
        """
        self.nodes = nodes
        self.edges = edges
        self.node_map = {node["id"]: node for node in nodes}
    
    def detect_illegal_cycles(self) -> List[CycleInfo]:
        """
        금지된 순환 감지 (DFS 기반)
        
        Returns:
            발견된 순환 목록 (빈 리스트면 순환 없음)
        
        Raises:
            IllegalCycleError: 금지된 순환 발견 시
        """
        # 1. 그래프 구성 (loop/for_each 내부는 제외)
        graph = self._build_graph()
        
        # 2. DFS 순환 감지
        cycles = []
        visited = set()
        rec_stack = set()
        
        for node_id in graph:
            if node_id not in visited:
                path = []
                cycle_info = self._dfs_cycle(
                    node_id, graph, visited, rec_stack, path
                )
                if cycle_info:
                    cycles.append(cycle_info)
        
        # 3. 결과 반환 또는 예외 발생
        if cycles:
            logger.error(
                f"[CYCLE_DETECTOR] Found {len(cycles)} illegal cycles:\n" +
                "\n".join(f"  {i+1}. {cycle}" for i, cycle in enumerate(cycles))
            )
            raise IllegalCycleError(
                f"Illegal cycles detected in workflow:\n" +
                "\n".join(f"  {i+1}. {cycle}" for i, cycle in enumerate(cycles)) +
                "\n\nTip: Use 'loop' or 'for_each' node for intentional repetition."
            )
        
        logger.info(
            f"[CYCLE_DETECTOR] No illegal cycles found "
            f"({len(self.nodes)} nodes, {len(self.edges)} edges)"
        )
        return cycles
    
    def _dfs_cycle(
        self,
        node_id: str,
        graph: Dict[str, List[str]],
        visited: Set[str],
        rec_stack: Set[str],
        path: List[str]
    ) -> Optional[CycleInfo]:
        """
        DFS 기반 순환 감지
        
        Args:
            node_id: 현재 노드 ID
            graph: 인접 리스트 그래프
            visited: 방문한 노드 Set
            rec_stack: 재귀 스택 (현재 경로)
            path: 경로 추적
        
        Returns:
            순환 정보 (순환 없으면 None)
        """
        visited.add(node_id)
        rec_stack.add(node_id)
        path.append(node_id)
        
        for neighbor in graph.get(node_id, []):
            if neighbor not in visited:
                # 미방문 노드: 재귀 탐색
                cycle_info = self._dfs_cycle(
                    neighbor, graph, visited, rec_stack, path
                )
                if cycle_info:
                    return cycle_info
            
            elif neighbor in rec_stack:
                # 순환 발견!
                cycle_start = path.index(neighbor)
                cycle_path = path[cycle_start:] + [neighbor]
                
                logger.warning(
                    f"[CYCLE_FOUND] {' → '.join(cycle_path)}"
                )
                
                return CycleInfo(
                    path=cycle_path,
                    cycle_type='illegal'
                )
        
        # 백트래킹
        rec_stack.remove(node_id)
        path.pop()
        return None
    
    def _build_graph(self) -> Dict[str, List[str]]:
        """
        그래프 구성 (loop/for_each 내부는 제외)
        
        Returns:
            인접 리스트 그래프 {node_id: [target_ids]}
        """
        # 1. loop/for_each 내부 노드 추출
        loop_internals = self._extract_loop_internals()
        
        logger.debug(
            f"[GRAPH_BUILD] Excluding {len(loop_internals)} loop-internal nodes"
        )
        
        # 2. 그래프 초기화
        graph = {node["id"]: [] for node in self.nodes}
        
        # 3. 엣지 추가 (loop 내부는 무시)
        for edge in self.edges:
            source = edge["source"]
            target = edge["target"]
            
            # loop 내부 엣지는 무시 (허용된 순환)
            if source in loop_internals or target in loop_internals:
                continue
            
            graph[source].append(target)
        
        logger.info(
            f"[GRAPH_BUILD] Built graph with {len(graph)} nodes, "
            f"{sum(len(v) for v in graph.values())} edges "
            f"(excluded {len(loop_internals)} loop-internal nodes)"
        )
        
        return graph
    
    def _extract_loop_internals(self) -> Set[str]:
        """
        loop/for_each 노드 내부의 모든 노드 ID 추출
        
        Returns:
            loop 내부 노드 ID Set
        """
        internals = set()
        
        for node in self.nodes:
            node_type = node.get("type")
            config = node.get("config", {})
            
            # loop/for_each 노드
            if node_type in ("loop", "for_each"):
                # sub_workflow의 노드 추출
                sub_nodes = config.get("nodes", [])
                for sub_node in sub_nodes:
                    internals.add(sub_node["id"])
                    
                    # 중첩 loop (재귀적 추출)
                    if sub_node.get("type") in ("loop", "for_each"):
                        nested_nodes = sub_node.get("config", {}).get("nodes", [])
                        for nested_node in nested_nodes:
                            internals.add(nested_node["id"])
                
                logger.debug(
                    f"[LOOP_INTERNAL] {node['id']} contains "
                    f"{len(sub_nodes)} internal nodes"
                )
            
            # parallel_group 노드 (브랜치 내부)
            elif node_type == "parallel_group":
                branches = config.get("branches", [])
                for branch in branches:
                    branch_nodes = branch.get("nodes", [])
                    for branch_node in branch_nodes:
                        internals.add(branch_node["id"])
                
                logger.debug(
                    f"[PARALLEL_INTERNAL] {node['id']} contains "
                    f"{len(branches)} branches"
                )
        
        return internals
    
    def validate_dag_assumption(self) -> bool:
        """
        워크플로우가 DAG(Directed Acyclic Graph)인지 검증
        
        Returns:
            True if DAG (순환 없음), False if cyclic
        """
        try:
            self.detect_illegal_cycles()
            return True
        except IllegalCycleError:
            return False
    
    def get_topological_order(self) -> List[str]:
        """
        위상 정렬 (Kahn's Algorithm)
        
        Returns:
            위상 정렬된 노드 ID 리스트
        
        Raises:
            IllegalCycleError: 순환 존재 시
        """
        # 1. 순환 검사
        self.detect_illegal_cycles()
        
        # 2. 그래프 구성
        graph = self._build_graph()
        
        # 3. in-degree 계산
        in_degree = {node_id: 0 for node_id in graph}
        for node_id in graph:
            for neighbor in graph[node_id]:
                in_degree[neighbor] += 1
        
        # 4. Kahn's Algorithm
        queue = [node_id for node_id, degree in in_degree.items() if degree == 0]
        topological_order = []
        
        while queue:
            node_id = queue.pop(0)
            topological_order.append(node_id)
            
            for neighbor in graph[node_id]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        # 5. 검증
        if len(topological_order) != len(graph):
            # 이론적으로는 도달 불가 (사전 순환 검사 통과 시)
            raise IllegalCycleError(
                f"Topological sort failed: {len(topological_order)} != {len(graph)}"
            )
        
        logger.info(
            f"[TOPOLOGICAL_SORT] Generated order: {topological_order}"
        )
        
        return topological_order


class CycleAnalyzer:
    """
    순환 분석 도구 (통계 및 시각화)
    """
    
    @staticmethod
    def analyze_cycles(cycles: List[CycleInfo]) -> Dict:
        """
        순환 통계 분석
        
        Args:
            cycles: 발견된 순환 목록
        
        Returns:
            분석 결과 Dict
        """
        if not cycles:
            return {
                "total_cycles": 0,
                "max_cycle_length": 0,
                "avg_cycle_length": 0
            }
        
        cycle_lengths = [len(cycle.path) - 1 for cycle in cycles]  # 마지막 노드는 시작과 동일
        
        return {
            "total_cycles": len(cycles),
            "max_cycle_length": max(cycle_lengths),
            "avg_cycle_length": sum(cycle_lengths) / len(cycle_lengths),
            "cycle_details": [
                {
                    "path": cycle.path,
                    "length": len(cycle.path) - 1,
                    "type": cycle.cycle_type
                }
                for cycle in cycles
            ]
        }
    
    @staticmethod
    def suggest_fix(cycle: CycleInfo, nodes: List[Dict]) -> str:
        """
        순환 해결 제안
        
        Args:
            cycle: 순환 정보
            nodes: 노드 목록
        
        Returns:
            해결 제안 메시지
        """
        cycle_nodes = cycle.path[:-1]  # 마지막은 시작과 동일
        
        # 노드 타입 분석
        node_types = [
            next(n["type"] for n in nodes if n["id"] == node_id)
            for node_id in cycle_nodes
        ]
        
        # 제안 생성
        if all(t in ("llm_chat", "operator") for t in node_types):
            return (
                f"Suggestion: Wrap nodes {cycle_nodes} in a 'loop' node "
                f"with explicit convergence condition."
            )
        else:
            return (
                f"Suggestion: Review dependencies in {cycle_nodes}. "
                f"Consider using 'route_condition' node to break the cycle."
            )


# 팩토리 함수
def create_cycle_detector(
    nodes: List[Dict],
    edges: List[Dict]
) -> CycleDetector:
    """
    CycleDetector 인스턴스 생성
    
    Args:
        nodes: 워크플로우 노드 목록
        edges: 워크플로우 엣지 목록
    
    Returns:
        CycleDetector 인스턴스
    """
    return CycleDetector(nodes, edges)
