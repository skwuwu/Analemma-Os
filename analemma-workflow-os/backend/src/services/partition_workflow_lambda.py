import os
import json
import logging
from typing import Dict, Any, List, Set, Optional
from src.services.workflow_repository import WorkflowRepository

logger = logging.getLogger(__name__)

# LLM 노드 타입들 - 이 타입들을 만날 때마다 세그먼트를 분할합니다
LLM_NODE_TYPES = {
    "llm_chat",
    "openai_chat", 
    "anthropic_chat",
    "bedrock_chat",
    "claude_chat",
    "gpt_chat",
    "aiModel"  # 범용 AI 모델 노드 타입 추가
}

# HITP (Human in the Loop) 엣지 타입들
HITP_EDGE_TYPES = {"hitp", "human_in_the_loop", "pause"}

# 세그먼트 타입들
SEGMENT_TYPES = {"normal", "llm", "hitp", "isolated", "complete", "parallel_group", "aggregator"}


def partition_workflow_advanced(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    고급 워크플로우 분할: HITP 엣지와 LLM 노드 기반으로 세그먼트를 생성합니다.
    
    개선된 알고리즘:
    - 병합 지점(Merge Point) 감지 및 처리
    - 병렬 그룹(Parallel Group) 생성 및 재귀적 파티셔닝
    - Convergence Node 찾기 및 브랜치 제한
    - 재귀적 Node-to-Segment 매핑
    """
    nodes = {n["id"]: n for n in config.get("nodes", [])}
    edges = config.get("edges", []) if config.get("edges") else []
    
    # 엣지 맵 생성
    incoming_edges: Dict[str, List[Dict[str, Any]]] = {}
    outgoing_edges: Dict[str, List[Dict[str, Any]]] = {}
    
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target") 
        if source:
            outgoing_edges.setdefault(source, []).append(edge)
        if target:
            incoming_edges.setdefault(target, []).append(edge)
    
    # ID 생성기 (0-based 정수 반환으로 수정 - 리스트 인덱스와 일치)
    class IdGenerator:
        def __init__(self): 
            self.val = -1
        def next(self): 
            self.val += 1
            return self.val  # 정수 반환 (0, 1, 2...)
    
    seg_id_gen = IdGenerator()
    stats = {"llm": 0, "hitp": 0}
    
    # --- Helper: 합류 지점(Convergence Node) 찾기 ---
    def find_convergence_node(start_nodes: List[str]) -> Optional[str]:
        """
        브랜치들이 공통으로 도달하는 첫 번째 Merge Point를 찾습니다.
        in-degree > 1인 노드를 후보로 봅니다.
        """
        queue = list(start_nodes)
        seen = set(queue)
        
        while queue:
            node_id = queue.pop(0)
            # Merge Point 후보 확인
            if len(incoming_edges.get(node_id, [])) > 1:
                if node_id not in start_nodes:
                    return node_id
            
            for out_edge in outgoing_edges.get(node_id, []):
                target = out_edge.get("target")
                if target and target not in seen:
                    seen.add(target)
                    queue.append(target)
        return None
    
    # --- Segment 생성 헬퍼 ---
    def create_segment(nodes_map, edges_list, s_type="normal", override_id=None, config=None):
        # 세그먼트 내부 엣지 추가
        if config:
            all_edges = config.get("edges", [])
            for edge in all_edges:
                source = edge.get("source")
                target = edge.get("target")
                if source in nodes_map and target in nodes_map:
                    if edge not in edges_list:  # 중복 방지
                        edges_list.append(edge)
        
        final_type = s_type
        if s_type == "normal":
            if any(n.get("hitp") in [True, "true"] for n in nodes_map.values()):
                final_type = "hitp"
        
        if final_type == "llm": 
            stats["llm"] += 1
        elif final_type == "hitp": 
            stats["hitp"] += 1
            
        return {
            "id": override_id if override_id is not None else seg_id_gen.next(),
            "nodes": list(nodes_map.values()),
            "edges": list(edges_list),
            "type": final_type,
            "node_ids": list(nodes_map.keys())
        }
    
    # --- 재귀적 파티셔닝 로직 ---
    visited_nodes: Set[str] = set()
    
    def run_partitioning(start_node_ids: List[str], stop_at_nodes: Set[str] = None, config=None) -> List[Dict[str, Any]]:
        local_segments = []
        local_current_nodes = {}
        local_current_edges = []
        queue = list(start_node_ids)
        
        def flush_local(seg_type="normal"):
            nonlocal local_current_nodes, local_current_edges
            if local_current_nodes or local_current_edges:
                seg = create_segment(local_current_nodes, local_current_edges, seg_type, config=config)
                local_segments.append(seg)
                local_current_nodes = {}
                local_current_edges = []
        
        while queue:
            node_id = queue.pop(0)
            
            # Stop Condition
            if node_id in visited_nodes: 
                continue
            if stop_at_nodes and node_id in stop_at_nodes: 
                continue
            
            node = nodes[node_id]
            
            # 트리거 조건 계산
            in_edges = incoming_edges.get(node_id, [])
            non_hitp_in = [e for e in in_edges if e.get("type") not in HITP_EDGE_TYPES]
            
            is_hitp_start = any(e.get("type") in HITP_EDGE_TYPES for e in in_edges)
            is_llm = node.get("type") in LLM_NODE_TYPES
            is_merge = len(non_hitp_in) > 1
            is_branch = len(outgoing_edges.get(node_id, [])) > 1
            
            # 세그먼트 분할 트리거
            if (is_hitp_start or is_llm or is_merge or is_branch) and local_current_nodes:
                if node_id not in local_current_nodes:
                    flush_local("normal")
            
            # 병렬 그룹 처리
            if is_branch:
                flush_local("normal")  # 현재까지 저장
                
                # 분기점 노드 처리
                seg = create_segment({node_id: node}, [], "normal", config=config) 
                local_segments.append(seg)
                visited_nodes.add(node_id)
                
                out_edges = outgoing_edges.get(node_id, [])
                branch_targets = [e.get("target") for e in out_edges if e.get("target")]
                
                # 합류점 찾기
                convergence_node = find_convergence_node(branch_targets)
                stop_set = {convergence_node} if convergence_node else set()
                
                # 각 브랜치 실행
                branches_data = []
                for i, target in enumerate(branch_targets):
                    if target:
                        branch_segs = run_partitioning([target], stop_at_nodes=stop_set, config=config)
                        if branch_segs:
                            branches_data.append({
                                "branch_id": f"B{i}",
                                "partition_map": branch_segs
                            })
                
                # Parallel Group 생성
                if branches_data:
                    p_seg_id = seg_id_gen.next()
                    parallel_seg = {
                        "id": p_seg_id,
                        "type": "parallel_group",
                        "branches": branches_data,
                        "node_ids": []
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
                        "convergence_node": convergence_node  # 합류 노드 저장
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
            
            # 엣지 추가 제거 - 세그먼트 내부 엣지만 create_segment에서 추가
            
            # 특수 타입 처리
            if is_llm:
                flush_local("llm")
            elif is_hitp_start:
                flush_local("hitp")
            
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
    def process_links_recursive(seg_list):
        # Aggregator 세그먼트들의 ID 집합을 미리 파악
        aggregator_ids = {s["id"] for s in seg_list if s.get("type") == "aggregator"}
        
        for seg in seg_list:
            if seg["type"] == "parallel_group":
                for branch in seg["branches"]:
                    process_links_recursive(branch["partition_map"])
                continue
            
            # Aggregator의 경우 convergence_node를 사용해 다음 세그먼트 연결
            if seg.get("type") == "aggregator":
                convergence_node = seg.get("convergence_node")
                if convergence_node and convergence_node in node_to_seg_map:
                    next_seg_id = node_to_seg_map[convergence_node]
                    seg["next_mode"] = "default"
                    seg["default_next"] = next_seg_id
                else:
                    # 합류 노드가 맵에 없다는 것은 의도된 종료일 수도 있지만, 
                    # 복잡한 그래프에서는 로직 오류일 수도 있으므로 경고 로그 추가
                    if convergence_node:
                        logger.warning(f"Aggregator {seg['id']} has convergence node {convergence_node} but it is not mapped to any segment. Treating as workflow end.")
                    
                    seg["next_mode"] = "end"
                    seg["default_next"] = None
                continue
            
            exit_edges = []
            for nid in seg["node_ids"]:
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
                seg["next_mode"] = "end"
                seg["default_next"] = None
            elif len(exit_edges) == 1:
                seg["next_mode"] = "default"
                seg["default_next"] = exit_edges[0]["target_segment"]
            else:
                seg["next_mode"] = "conditional"
                seg["branches"] = [
                    {"condition": e["edge"].get("condition", "default"), "next": e["target_segment"]}
                    for e in exit_edges
                ]
    
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
    
    total_segments = count_segments_recursive(segments)
    
    return {
        "partition_map": segments,
        "total_segments": total_segments, 
        "llm_segments": stats["llm"],
        "hitp_segments": stats["hitp"]
    }


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    PartitionWorkflow Lambda: 워크플로우를 지능적으로 분할합니다.
    
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
            "Partitioned workflow for owner=%s: %d total segments (%d LLM, %d HITP)", 
            owner_id,
            partition_result["total_segments"],
            partition_result["llm_segments"], 
            partition_result["hitp_segments"]
        )
        
        return {
            "status": "success",
            "partition_result": partition_result
        }
        
    except Exception as e:
        logger.exception("Failed to partition workflow")
        return {
            "status": "error", 
            "error_message": str(e)
        }
