"""
Routing Resolver Service

단일 책임: 노드 출력 기반 다음 타겟 결정
- 노드 중심 라우팅 (Node-Centric Routing)
- 화이트리스트 검증 (Whitelist Validation)
- 보안 정책 기반 점프 제한
"""

import logging
from typing import Dict, List, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class RoutingAmbiguityError(Exception):
    """다중 outgoing edge 존재 시 __next_node 미설정 오류"""
    pass


class InvalidTargetError(Exception):
    """유효하지 않은 타겟 노드로 라우팅 시도"""
    pass


class UnauthorizedRoutingError(Exception):
    """보안 정책상 금지된 노드로 라우팅 시도"""
    pass


@dataclass
class EdgeModel:
    """엣지 모델 (순수 연결 정보만 보유)"""
    type: str  # 'edge' | 'hitp' | 'dynamic'
    source: str
    target: str


@dataclass
class RoutingContext:
    """라우팅 컨텍스트 (검증에 필요한 정보)"""
    valid_node_ids: Set[str]  # 현재 매니페스트 내 유효한 노드 ID
    restricted_nodes: Set[str]  # 보안 정책상 접근 금지된 노드
    current_ring_level: int  # 현재 노드의 Ring 레벨 (0-3)


class RoutingResolver:
    """
    라우팅 결정 엔진
    
    책임:
    1. 노드 출력 기반 다음 타겟 결정
    2. 화이트리스트 검증 (O(1))
    3. 보안 정책 기반 점프 제한
    
    라우팅 우선순위:
    1. state["__next_node"] (노드가 명시적으로 지정)
    2. outgoing edges 중 첫 번째 (기본 흐름)
    3. None (워크플로우 종료)
    """
    
    def __init__(self, routing_context: RoutingContext):
        """
        Args:
            routing_context: 라우팅 검증에 필요한 컨텍스트
        """
        self.context = routing_context
        logger.info(
            f"[ROUTING_RESOLVER] Initialized with "
            f"{len(self.context.valid_node_ids)} valid nodes, "
            f"{len(self.context.restricted_nodes)} restricted nodes"
        )
    
    def resolve_next_target(
        self,
        current_node_id: str,
        state: Dict,
        edges: List[EdgeModel]
    ) -> Optional[str]:
        """
        다음 실행 타겟 결정
        
        Args:
            current_node_id: 현재 노드 ID
            state: 워크플로우 상태
            edges: 전체 엣지 목록
        
        Returns:
            다음 노드 ID (None이면 워크플로우 종료)
        
        Raises:
            RoutingAmbiguityError: 다중 엣지 존재 시 __next_node 미설정
            InvalidTargetError: 존재하지 않는 노드로 라우팅 시도
            UnauthorizedRoutingError: 금지된 노드로 라우팅 시도
        """
        # Priority 1: 노드가 명시한 타겟 (__next_node)
        if "__next_node" in state:
            next_node = state.pop("__next_node")
            
            # 🛡️ 화이트리스트 검증 (O(1))
            self._validate_target(next_node, current_node_id, "explicit")
            
            logger.info(
                f"[ROUTING] {current_node_id} → {next_node} (explicit)"
            )
            return next_node
        
        # Priority 2: 단일 outgoing edge
        outgoing = [e for e in edges if e.source == current_node_id]
        
        if len(outgoing) == 0:
            # 종료 노드
            logger.info(
                f"[ROUTING] {current_node_id} → END (no outgoing edges)"
            )
            return None
        
        elif len(outgoing) == 1:
            # 단일 흐름
            next_node = outgoing[0].target
            
            # 🛡️ 화이트리스트 검증 (O(1))
            self._validate_target(next_node, current_node_id, "edge")
            
            logger.info(
                f"[ROUTING] {current_node_id} → {next_node} (edge)"
            )
            return next_node
        
        else:
            # ❌ 다중 엣지 존재: 설계 오류
            raise RoutingAmbiguityError(
                f"Node {current_node_id} has {len(outgoing)} outgoing edges "
                f"but did not set __next_node. Use 'route_condition' node to "
                f"make routing decisions explicit."
            )
    
    def _validate_target(
        self,
        target_node: str,
        source_node: str,
        routing_method: str
    ) -> None:
        """
        타겟 노드 화이트리스트 검증
        
        Args:
            target_node: 타겟 노드 ID
            source_node: 소스 노드 ID
            routing_method: 라우팅 방법 ('explicit' | 'edge')
        
        Raises:
            InvalidTargetError: 존재하지 않는 노드
            UnauthorizedRoutingError: 접근 금지된 노드
        """
        # 1. 존재 검증 (O(1) - Set lookup)
        if target_node not in self.context.valid_node_ids:
            logger.error(
                f"[ROUTING_VIOLATION] {source_node} attempted to route to "
                f"non-existent node: {target_node} (method: {routing_method})"
            )
            raise InvalidTargetError(
                f"Invalid routing target: '{target_node}' does not exist in "
                f"current manifest. Valid nodes: {sorted(self.context.valid_node_ids)}"
            )
        
        # 2. 보안 정책 검증 (O(1) - Set lookup)
        if target_node in self.context.restricted_nodes:
            logger.error(
                f"[SECURITY_VIOLATION] {source_node} attempted to route to "
                f"restricted node: {target_node} (Ring {self.context.current_ring_level})"
            )
            raise UnauthorizedRoutingError(
                f"Unauthorized routing: '{target_node}' is restricted for "
                f"Ring {self.context.current_ring_level} nodes. "
                f"Security policy violation detected."
            )
        
        # 3. 검증 통과
        logger.debug(
            f"[ROUTING_VALIDATED] {source_node} → {target_node} "
            f"(method: {routing_method})"
        )
    
    def get_outgoing_edges(
        self,
        node_id: str,
        edges: List[EdgeModel]
    ) -> List[EdgeModel]:
        """
        특정 노드의 outgoing edges 반환
        
        Args:
            node_id: 노드 ID
            edges: 전체 엣지 목록
        
        Returns:
            outgoing edges 목록
        """
        return [e for e in edges if e.source == node_id]
    
    def validate_routing_graph(self, edges: List[EdgeModel]) -> None:
        """
        워크플로우 저장 시 라우팅 그래프 유효성 사전 검증
        
        Args:
            edges: 전체 엣지 목록
        
        Raises:
            InvalidTargetError: 존재하지 않는 노드를 가리키는 엣지 발견
        """
        invalid_edges = []
        
        for edge in edges:
            # Source 검증
            if edge.source not in self.context.valid_node_ids:
                invalid_edges.append(
                    f"Edge {edge.source} → {edge.target}: "
                    f"source '{edge.source}' not found"
                )
            
            # Target 검증
            if edge.target not in self.context.valid_node_ids:
                invalid_edges.append(
                    f"Edge {edge.source} → {edge.target}: "
                    f"target '{edge.target}' not found"
                )
        
        if invalid_edges:
            logger.error(
                f"[ROUTING_GRAPH_INVALID] Found {len(invalid_edges)} invalid edges"
            )
            raise InvalidTargetError(
                f"Invalid routing graph:\n" + "\n".join(invalid_edges)
            )
        
        logger.info(
            f"[ROUTING_GRAPH_VALID] All {len(edges)} edges validated"
        )


class RoutingPolicy:
    """
    라우팅 보안 정책 관리
    
    Ring 레벨별 접근 가능한 노드 타입 정의
    """
    
    # Ring별 접근 금지 노드 타입
    RESTRICTED_NODE_TYPES = {
        3: {  # Ring 3 (Agent): 시스템 노드 접근 금지
            "_kernel_operator",
            "system_admin",
            "governor_control"
        },
        2: {  # Ring 2 (Trusted): 커널 노드 접근 금지
            "_kernel_operator"
        },
        1: set(),  # Ring 1 (Governor): 제한 없음
        0: set()   # Ring 0 (Kernel): 제한 없음
    }
    
    @classmethod
    def get_restricted_nodes(
        cls,
        ring_level: int,
        all_nodes: List[Dict]
    ) -> Set[str]:
        """
        특정 Ring 레벨에서 접근 금지된 노드 ID 목록 반환
        
        Args:
            ring_level: Ring 레벨 (0-3)
            all_nodes: 전체 노드 목록
        
        Returns:
            접근 금지 노드 ID Set
        """
        restricted_types = cls.RESTRICTED_NODE_TYPES.get(ring_level, set())
        
        restricted_ids = {
            node["id"]
            for node in all_nodes
            if node.get("type") in restricted_types or
               node.get("_security_level", 0) < ring_level
        }
        
        logger.info(
            f"[ROUTING_POLICY] Ring {ring_level}: "
            f"{len(restricted_ids)} restricted nodes"
        )
        
        return restricted_ids


# 팩토리 함수
def create_routing_resolver(
    nodes: List[Dict],
    current_ring_level: int
) -> RoutingResolver:
    """
    RoutingResolver 인스턴스 생성 (팩토리)
    
    Args:
        nodes: 현재 매니페스트의 전체 노드 목록
        current_ring_level: 현재 실행 컨텍스트의 Ring 레벨
    
    Returns:
        RoutingResolver 인스턴스
    """
    # 유효한 노드 ID Set 구성 (O(n))
    valid_node_ids = {node["id"] for node in nodes}
    
    # 보안 정책 기반 제한 노드 추출
    restricted_nodes = RoutingPolicy.get_restricted_nodes(
        current_ring_level,
        nodes
    )
    
    # RoutingContext 생성
    context = RoutingContext(
        valid_node_ids=valid_node_ids,
        restricted_nodes=restricted_nodes,
        current_ring_level=current_ring_level
    )
    
    return RoutingResolver(context)
