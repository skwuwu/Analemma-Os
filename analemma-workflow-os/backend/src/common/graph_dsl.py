"""
Graph DSL: 워크플로우 JSON 스키마 정의 및 검증

Co-design Assistant의 핵심 모듈로, 워크플로우 구조를 공식화합니다.
"""
import json
from typing import Any, Dict, List, Literal, Optional

# Pydantic이 없을 경우를 위한 fallback
try:
    from pydantic import BaseModel, Field, ValidationError, field_validator
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    
    class BaseModel:
        """Pydantic fallback"""
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
        
        def model_dump(self) -> Dict[str, Any]:
            return self.__dict__.copy()
    
    class ValidationError(Exception):
        pass
    
    def Field(*args, **kwargs):
        return kwargs.get("default")
    
    def field_validator(*args, **kwargs):
        def decorator(func):
            return func
        return decorator


class Position(BaseModel):
    """노드 위치 정보"""
    x: float
    y: float


class NodeConfig(BaseModel):
    """유연한 노드 설정 (노드 타입별 다양한 필드)"""
    # operator
    code: Optional[str] = None
    sets: Optional[Dict[str, Any]] = None
    
    # llm_chat
    prompt_content: Optional[str] = None
    model: Optional[str] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    system_prompt: Optional[str] = None
    
    # api_call
    url: Optional[str] = None
    method: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    params: Optional[Dict[str, Any]] = None
    json_body: Optional[Dict[str, Any]] = Field(default=None, alias="json")
    timeout: Optional[int] = None
    
    # db_query
    query: Optional[str] = None
    connection_string: Optional[str] = None
    
    # for_each
    items: Optional[List[Any]] = None
    item_key: Optional[str] = None
    
    # route_draft_quality
    threshold: Optional[float] = None
    
    # group
    subgraph_id: Optional[str] = None


class WorkflowNode(BaseModel):
    """워크플로우 노드 정의"""
    id: str
    type: Literal["operator", "llm_chat", "api_call", "db_query", "for_each", "route_draft_quality", "group", "aiModel"]
    label: Optional[str] = None
    position: Position
    config: Optional[NodeConfig] = None
    data: Optional[Dict[str, Any]] = None  # 프론트엔드 호환용
    
    # 서브그래프 관계
    subgraph_id: Optional[str] = None  # group 노드용 (내부 서브그래프 참조)
    parent_id: Optional[str] = None    # 그룹 내 노드용 (부모 그룹 참조)


class WorkflowEdge(BaseModel):
    """워크플로우 엣지 정의"""
    id: str
    source: str
    target: str
    source_handle: Optional[str] = None
    target_handle: Optional[str] = None
    label: Optional[str] = None
    condition: Optional[str] = None  # 조건부 분기용
    type: Optional[str] = None  # edge 타입 (예: "smoothstep")


class SubgraphMetadata(BaseModel):
    """서브그래프(그룹) 메타데이터"""
    id: str
    name: str
    description: Optional[str] = None
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    input_schema: Optional[Dict[str, Any]] = None
    output_schema: Optional[Dict[str, Any]] = None


class Workflow(BaseModel):
    """Graph DSL 루트 스키마"""
    name: Optional[str] = None
    description: Optional[str] = None
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    subgraphs: Optional[Dict[str, SubgraphMetadata]] = None
    metadata: Optional[Dict[str, Any]] = None


# ──────────────────────────────────────────────────────────────
# 스키마 검증 함수
# ──────────────────────────────────────────────────────────────

def validate_workflow(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    워크플로우 구조 검증
    
    Args:
        workflow: 워크플로우 JSON 딕셔너리
        
    Returns:
        에러 목록 [{"level": "error|warning", "message": "...", "node_id": "..."}]
    """
    errors: List[Dict[str, Any]] = []
    
    # 1. 기본 구조 검증 (Pydantic 모델 사용)
    if PYDANTIC_AVAILABLE:
        try:
            # 노드/엣지 배열 추출
            nodes = workflow.get("nodes", [])
            edges = workflow.get("edges", [])
            
            # 각 노드의 position 필드가 올바른 형식인지 확인
            for i, node in enumerate(nodes):
                if "position" not in node:
                    errors.append({
                        "level": "error",
                        "message": f"노드 #{i}에 position 필드가 없습니다.",
                        "node_id": node.get("id")
                    })
                elif not isinstance(node["position"], dict):
                    errors.append({
                        "level": "error",
                        "message": f"노드 '{node.get('id')}'의 position이 객체가 아닙니다.",
                        "node_id": node.get("id")
                    })
                    
        except Exception as e:
            errors.append({
                "level": "error",
                "message": f"워크플로우 구조 검증 실패: {str(e)}",
                "node_id": None
            })
    
    # 기본 필드 존재 확인
    if "nodes" not in workflow:
        errors.append({
            "level": "error",
            "message": "워크플로우에 'nodes' 필드가 없습니다.",
            "node_id": None
        })
        return errors
        
    if "edges" not in workflow:
        errors.append({
            "level": "error",
            "message": "워크플로우에 'edges' 필드가 없습니다.",
            "node_id": None
        })
        return errors
    
    nodes = workflow.get("nodes", [])
    edges = workflow.get("edges", [])
    
    # 2. 노드 ID 중복 검사
    node_ids = [n.get("id") for n in nodes if n.get("id")]
    seen_ids = set()
    for node_id in node_ids:
        if node_id in seen_ids:
            errors.append({
                "level": "error",
                "message": f"중복된 노드 ID: {node_id}",
                "node_id": node_id
            })
        seen_ids.add(node_id)
    
    # 3. 엣지 참조 무결성
    node_id_set = set(node_ids)
    for edge in edges:
        edge_id = edge.get("id", "unknown")
        source = edge.get("source")
        target = edge.get("target")
        
        if source and source not in node_id_set:
            errors.append({
                "level": "error",
                "message": f"엣지 '{edge_id}': 존재하지 않는 source 노드 '{source}'",
                "node_id": source
            })
        if target and target not in node_id_set:
            errors.append({
                "level": "error",
                "message": f"엣지 '{edge_id}': 존재하지 않는 target 노드 '{target}'",
                "node_id": target
            })
    
    # 4. 엣지 ID 중복 검사
    edge_ids = [e.get("id") for e in edges if e.get("id")]
    seen_edge_ids = set()
    for edge_id in edge_ids:
        if edge_id in seen_edge_ids:
            errors.append({
                "level": "warning",
                "message": f"중복된 엣지 ID: {edge_id}",
                "node_id": None
            })
        seen_edge_ids.add(edge_id)
    
    return errors


def normalize_workflow(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """
    워크플로우 정규화 (프론트엔드 ↔ 백엔드 변환)
    
    - position 필드 보장
    - data 필드와 config 필드 동기화
    - 기본값 설정
    """
    normalized = {
        "name": workflow.get("name"),
        "description": workflow.get("description"),
        "nodes": [],
        "edges": [],
        "subgraphs": workflow.get("subgraphs"),
        "metadata": workflow.get("metadata")
    }
    
    for node in workflow.get("nodes", []):
        normalized_node = {
            "id": node.get("id"),
            "type": node.get("type", "operator"),
            "label": node.get("label") or (node.get("data", {}) or {}).get("label"),
            "position": node.get("position", {"x": 150, "y": 50}),
            "config": node.get("config"),
            "data": node.get("data"),
            "subgraph_id": node.get("subgraph_id"),
            "parent_id": node.get("parent_id")
        }
        
        # data에서 config 동기화
        if node.get("data") and not node.get("config"):
            data = node["data"]
            normalized_node["config"] = {
                k: v for k, v in data.items() 
                if k not in ["label", "blockId", "id"]
            }
        
        normalized["nodes"].append(normalized_node)
    
    for edge in workflow.get("edges", []):
        normalized_edge = {
            "id": edge.get("id"),
            "source": edge.get("source"),
            "target": edge.get("target"),
            "source_handle": edge.get("source_handle") or edge.get("sourceHandle"),
            "target_handle": edge.get("target_handle") or edge.get("targetHandle"),
            "label": edge.get("label"),
            "condition": edge.get("condition"),
            "type": edge.get("type")
        }
        normalized["edges"].append(normalized_edge)
    
    return normalized


def workflow_to_frontend_format(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """
    백엔드 워크플로우를 프론트엔드 React Flow 형식으로 변환
    """
    frontend_nodes = []
    frontend_edges = []
    
    for node in workflow.get("nodes", []):
        frontend_node = {
            "id": node.get("id"),
            "type": node.get("type"),
            "position": node.get("position", {"x": 150, "y": 50}),
            "data": {
                "label": node.get("label") or node.get("id"),
                "blockId": node.get("id"),
                **(node.get("config") or {}),
                **(node.get("data") or {})
            }
        }
        frontend_nodes.append(frontend_node)
    
    for edge in workflow.get("edges", []):
        frontend_edge = {
            "id": edge.get("id"),
            "source": edge.get("source"),
            "target": edge.get("target"),
            "sourceHandle": edge.get("source_handle"),
            "targetHandle": edge.get("target_handle"),
            "label": edge.get("label"),
            "animated": True,
            "style": {"stroke": "hsl(263 70% 60%)", "strokeWidth": 2}
        }
        if edge.get("type"):
            frontend_edge["type"] = edge["type"]
        frontend_edges.append(frontend_edge)
    
    return {
        "nodes": frontend_nodes,
        "edges": frontend_edges
    }


def workflow_from_frontend_format(frontend_workflow: Dict[str, Any]) -> Dict[str, Any]:
    """
    프론트엔드 React Flow 형식을 백엔드 워크플로우로 변환
    """
    backend_nodes = []
    backend_edges = []
    
    for node in frontend_workflow.get("nodes", []):
        data = node.get("data", {}) or {}
        backend_node = {
            "id": node.get("id"),
            "type": node.get("type"),
            "label": data.get("label"),
            "position": node.get("position", {"x": 150, "y": 50}),
            "config": {
                k: v for k, v in data.items()
                if k not in ["label", "blockId", "id"]
            },
            "data": data
        }
        backend_nodes.append(backend_node)
    
    for edge in frontend_workflow.get("edges", []):
        backend_edge = {
            "id": edge.get("id"),
            "source": edge.get("source"),
            "target": edge.get("target"),
            "source_handle": edge.get("sourceHandle"),
            "target_handle": edge.get("targetHandle"),
            "label": edge.get("label"),
            "type": edge.get("type")
        }
        backend_edges.append(backend_edge)
    
    return {
        "nodes": backend_nodes,
        "edges": backend_edges
    }


# ──────────────────────────────────────────────────────────────
# JSON 스키마 정의 (외부 도구 호환용)
# ──────────────────────────────────────────────────────────────

WORKFLOW_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Workflow",
    "type": "object",
    "required": ["nodes", "edges"],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type", "position"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["operator", "llm_chat", "api_call", "db_query", "for_each", "route_draft_quality", "group", "aiModel"]
                    },
                    "label": {"type": "string"},
                    "position": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"}
                        },
                        "required": ["x", "y"]
                    },
                    "config": {"type": "object"},
                    "data": {"type": "object"}
                }
            }
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "source", "target"],
                "properties": {
                    "id": {"type": "string"},
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "label": {"type": "string"},
                    "condition": {"type": "string"}
                }
            }
        },
        "subgraphs": {"type": "object"},
        "metadata": {"type": "object"}
    }
}
