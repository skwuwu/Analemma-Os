"""
WorkflowOrchestratorService - Workflow Execution Engine

Extracted from `main.py` to separate orchestration logic from handler.
Handles:
- Workflow validation and config parsing
- Dynamic workflow building  
- Execution with callbacks
- Glass-Box logging
"""

import json
import os
import time
import uuid
import logging
from typing import Dict, Any, List, Optional, Union

from pydantic import BaseModel, Field, conlist, constr, ValidationError, field_validator

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Schema Definitions
# -----------------------------------------------------------------------------

# � Import constants from main.py (single source of truth)
from src.handlers.core.main import (
    ALLOWED_NODE_TYPES,
    EDGE_HANDLED_TYPES,
    UI_MARKER_TYPES,
    TRIGGER_TYPE_MAPPING,
    NODE_TYPE_ALIASES
)

class EdgeModel(BaseModel):
    source: constr(min_length=1, max_length=128)
    target: constr(min_length=1, max_length=128)
    type: constr(min_length=1, max_length=64) = "edge"
    
    # ❌ REMOVED: conditional_edge 필드 제거 (라우팅 주권 일원화)
    # router_func, mapping, condition 제거


class NodeModel(BaseModel):
    id: constr(min_length=1, max_length=128)
    type: constr(min_length=1, max_length=64)
    ring_level: int = Field(ge=0, le=3, default=2)  # ✅ [v3.28] Ring Level (0-3, Required)
    alias: Optional[constr(min_length=1, max_length=128)] = None  # ✅ [v3.28] Node alias for routing
    tags: Optional[List[constr(min_length=1, max_length=64)]] = None  # ✅ [v3.28] Functional tags
    label: Optional[constr(min_length=0, max_length=256)] = None
    action: Optional[constr(min_length=0, max_length=256)] = None
    hitp: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None
    # [Fix] parallel_group support - branches와 resource_policy 필드 추가
    branches: Optional[List[Dict[str, Any]]] = None
    resource_policy: Optional[Dict[str, Any]] = None
    # [Fix] subgraph support
    subgraph_ref: Optional[str] = None
    subgraph_inline: Optional[Dict[str, Any]] = None
    
    @field_validator('type', mode='before')
    @classmethod
    def alias_and_validate_node_type(cls, v):
        """
        🛡️ [P2] Validate and alias node types.
        """
        if not isinstance(v, str):
            raise ValueError(f"Node type must be string, got {type(v).__name__}")
        
        v = v.strip().lower()
        
        # Apply alias mapping first
        if v in NODE_TYPE_ALIASES:
            return NODE_TYPE_ALIASES[v]
        
        # 🛡️ All accepted types: executable nodes + UI markers (passthrough)
        all_valid_types = ALLOWED_NODE_TYPES | UI_MARKER_TYPES
        
        if v not in all_valid_types:
            raise ValueError(
                f"Unknown node type: '{v}'. "
                f"Allowed types: {sorted(all_valid_types)}. "
                f"Aliases: {NODE_TYPE_ALIASES}"
            )
        
        return v
    
    @field_validator('ring_level', mode='before')
    @classmethod
    def auto_assign_and_validate_ring_level(cls, v, info):
        """
        ✅ [v3.28] Auto-assign ring_level based on node type if not provided.
        Validate minimum ring level for each node type.
        """
        # Default Ring Levels by node type
        DEFAULT_RING_LEVELS = {
            # Ring 3: Untrusted (LLM)
            "llm_chat": 3,
            "vision": 3,
            "dynamic_router": 3,
            # Ring 2: User (State modification)
            "operator": 2,
            "operator_custom": 2,
            "operator_official": 2,
            "for_each": 2,
            "nested_for_each": 2,
            "parallel_group": 2,
            "aggregator": 2,
            "loop": 2,
            "route_condition": 2,
            "video_chunker": 2,
            # Ring 1: System (Infrastructure)
            "db_query": 1,
            "api_call": 1,
            "skill_executor": 1,
            # Ring 2: Subgraph (default)
            "subgraph": 2,
        }
        
        node_type = info.data.get('type', '').lower()
        
        # Auto-assign if not provided
        if v is None:
            return DEFAULT_RING_LEVELS.get(node_type, 2)
        
        # Validate minimum ring level
        min_level = DEFAULT_RING_LEVELS.get(node_type, 0)
        if v < min_level:
            raise ValueError(
                f"Node type '{node_type}' requires ring_level >= {min_level}, got {v}"
            )
        
        return v


class WorkflowConfigModel(BaseModel):
    workflow_name: Optional[constr(min_length=0, max_length=256)] = None
    description: Optional[constr(min_length=0, max_length=512)] = None
    nodes: conlist(NodeModel, min_length=1, max_length=500)
    edges: conlist(EdgeModel, min_length=0, max_length=1000)
    start_node: Optional[constr(min_length=1, max_length=128)] = None


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------

class AsyncLLMRequiredException(Exception):
    """Exception raised when async LLM processing is required."""
    pass


class CircularReferenceException(Exception):
    """Exception raised when circular subgraph reference is detected."""
    pass


# -----------------------------------------------------------------------------
# ✅ [v3.28] Subgraph Circular Reference Detection
# -----------------------------------------------------------------------------

def validate_subgraph_dependencies(
    workflow_config: Dict[str, Any],
    subgraph_loader: Optional[callable] = None
) -> None:
    """
    🧬 [v3.28] Validate subgraph dependency tree to prevent circular references.
    
    Args:
        workflow_config: Workflow configuration dictionary
        subgraph_loader: Optional function to load subgraph manifest by ref
                        Signature: (subgraph_ref: str) -> Dict[str, Any]
    
    Raises:
        CircularReferenceException: If circular reference detected
    """
    visited = set()
    path = []
    
    def _detect_cycle(node: Dict[str, Any], depth: int = 0) -> None:
        """Recursively detect circular references in subgraph tree."""
        if depth > 50:  # Maximum depth guard
            raise CircularReferenceException(
                f"Subgraph depth exceeded 50 levels. Possible infinite recursion. "
                f"Path: {' -> '.join(path)}"
            )
        
        node_id = node.get("id")
        node_type = node.get("type")
        
        if node_type != "subgraph":
            return
        
        subgraph_ref = node.get("subgraph_ref")
        subgraph_inline = node.get("subgraph_inline")
        
        # Check for inline subgraph (deprecated)
        if subgraph_inline:
            logger.warning(
                f"⚠️ [Deprecated] Node '{node_id}' uses subgraph_inline. "
                f"This will be removed in v3.30. Use subgraph_ref instead."
            )
            # Validate inline subgraph recursively
            inline_nodes = subgraph_inline.get("nodes", [])
            for inline_node in inline_nodes:
                _detect_cycle(inline_node, depth + 1)
            return
        
        if not subgraph_ref:
            raise ValueError(f"Node '{node_id}': subgraph type requires subgraph_ref")
        
        # Check for circular reference
        if subgraph_ref in visited:
            raise CircularReferenceException(
                f"🚨 Circular subgraph reference detected: {' -> '.join(path)} -> {subgraph_ref}"
            )
        
        visited.add(subgraph_ref)
        path.append(subgraph_ref)
        
        # Load subgraph manifest and validate recursively
        if subgraph_loader:
            try:
                subgraph_manifest = subgraph_loader(subgraph_ref)
                subgraph_nodes = subgraph_manifest.get("nodes", [])
                for subgraph_node in subgraph_nodes:
                    _detect_cycle(subgraph_node, depth + 1)
            except Exception as e:
                logger.error(f"Failed to load subgraph '{subgraph_ref}': {e}")
                # Continue validation without loading
        
        path.pop()
        visited.remove(subgraph_ref)
    
    # Validate all nodes in the workflow
    nodes = workflow_config.get("nodes", [])
    for node in nodes:
        visited.clear()
        path.clear()
        _detect_cycle(node)


# -----------------------------------------------------------------------------
# Glass-Box Callback
# -----------------------------------------------------------------------------

class StateHistoryCallback:
    """Capture AI thoughts and tool usage with PII masking for Glass-Box UI."""
    
    def __init__(self, mask_pii_fn=None):
        self.logs = []
        self.mask_pii = mask_pii_fn or (lambda x: x)
    
    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs) -> None:
        pass
    
    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs) -> None:
        prompt_safe = self.mask_pii(prompts[0]) if prompts else ""
        self.logs.append({
            "id": str(uuid.uuid4()),
            "type": "ai_thought",
            "name": serialized.get("name", "LLM Thinking"),
            "node_id": serialized.get("name", "llm_node"),
            "content": "Thinking...",
            "timestamp": int(time.time()),
            "status": "RUNNING",
            "details": {"prompts": [prompt_safe] if prompt_safe else []}
        })
    
    def on_llm_end(self, response: Any, **kwargs) -> None:
        if self.logs and self.logs[-1]["type"] == "ai_thought":
            self.logs[-1]["status"] = "COMPLETED"
            if getattr(response, "llm_output", None) and "usage" in response.llm_output:
                self.logs[-1]["usage"] = response.llm_output["usage"]
            try:
                text = response.generations[0][0].text
                self.logs[-1]["content"] = self.mask_pii(text)
            except Exception:
                pass
    
    def on_llm_error(self, error: BaseException, **kwargs) -> None:
        if self.logs and self.logs[-1]["type"] == "ai_thought":
            self.logs[-1]["status"] = "FAILED"
            self.logs[-1]["error"] = {"message": str(error), "type": type(error).__name__}
    
    def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs) -> None:
        self.logs.append({
            "id": str(uuid.uuid4()),
            "type": "tool_usage",
            "name": serialized.get("name", "Tool"),
            "node_id": serialized.get("name", "tool_node"),
            "status": "RUNNING",
            "timestamp": int(time.time()),
            "input": self.mask_pii(input_str)
        })
    
    def on_tool_end(self, output: str, **kwargs) -> None:
        if self.logs and self.logs[-1]["type"] == "tool_usage":
            self.logs[-1]["status"] = "COMPLETED"
            self.logs[-1]["output"] = self.mask_pii(str(output))
    
    def on_tool_error(self, error: BaseException, **kwargs) -> None:
        if self.logs and self.logs[-1]["type"] == "tool_usage":
            self.logs[-1]["status"] = "FAILED"
            self.logs[-1]["error"] = str(error)


# -----------------------------------------------------------------------------
# Orchestrator Service
# -----------------------------------------------------------------------------

class WorkflowOrchestratorService:
    """
    Workflow execution orchestrator.
    
    Responsibilities:
    - Config validation (Pydantic schema)
    - Dynamic workflow building
    - Execution with DynamoDB checkpointing
    - Glass-Box callback integration
    """
    
    def __init__(self, mask_pii_fn=None):
        self._mask_pii = mask_pii_fn
    
    def run_workflow(
        self,
        config_json: Union[str, Dict[str, Any]],
        initial_state: Optional[Dict[str, Any]] = None,
        user_api_keys: Optional[Dict[str, str]] = None,
        use_cache: bool = True,
        conversation_id: Optional[str] = None,
        ddb_table_name: Optional[str] = None,
        run_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Main entry point for workflow execution.
        
        Args:
            config_json: Workflow configuration (JSON string or dict)
            initial_state: Initial state for the workflow
            user_api_keys: User-provided API keys
            use_cache: Whether to use caching
            conversation_id: Conversation thread ID
            ddb_table_name: DynamoDB table for checkpointing
            run_config: Additional runtime configuration
            
        Returns:
            Workflow execution result
        """
        initial_state = dict(initial_state) if initial_state else {}
        
        # 1. Check for Mock/Test Request
        mock_behavior = initial_state.get("mock_behavior")
        if mock_behavior:
            logger.info(f"Mock behavior detected: {mock_behavior}")
            config_json = json.dumps(self._get_mock_config(mock_behavior))
            
            if mock_behavior == "PAUSED_FOR_HITP":
                return {"status": "PAUSED_FOR_HITP", "next_segment_to_run": 1}
        
        # 2. Config Validation
        raw_config = self._parse_config(config_json)
        workflow_config = self._validate_config(raw_config)
        
        # 3. Dynamic Build
        from src.services.workflow.builder import DynamicWorkflowBuilder
        
        logger.info("Building workflow dynamically...")
        builder = DynamicWorkflowBuilder(workflow_config)
        app = builder.build()
        
        # 4. Apply Checkpointer
        if ddb_table_name:
            app = self._apply_checkpointer(app, ddb_table_name)
        
        # 5. Prepare Execution Config
        final_config = self._prepare_run_config(run_config, conversation_id)
        
        # 6. Setup State
        if user_api_keys:
            initial_state.setdefault("user_api_keys", {}).update(user_api_keys)
        initial_state.setdefault("step_history", [])
        
        # 7. Setup Glass-Box Callback
        history_callback = StateHistoryCallback(mask_pii_fn=self._mask_pii)
        final_config.setdefault("callbacks", []).append(history_callback)
        
        # 8. Execute
        logger.info("Invoking workflow...")
        try:
            result = app.invoke(initial_state, config=final_config)
            
            if isinstance(result, dict):
                # 🛡️ total_segments 주입 - NoneType 에러 원천 차단
                if "total_segments" not in result:
                    result["total_segments"] = initial_state.get("total_segments") or 1
                
                result["__new_history_logs"] = history_callback.logs
                prev_logs = result.get("execution_logs", [])
                result["execution_logs"] = prev_logs + history_callback.logs
            
            return result
            
        except AsyncLLMRequiredException:
            # 🛡️ [P1 Fix] Step Functions가 필요로 하는 모든 필드 포함
            return {
                "status": "PAUSED_FOR_ASYNC_LLM",
                "total_segments": initial_state.get("total_segments") or 1,
                "final_state": initial_state,
                "final_state_s3_path": None,
                "next_segment_to_run": None,
                "new_history_logs": [],
                "error_info": None,
                "branches": None,
                "segment_type": "async_pause"
            }
        except Exception as e:
            logger.exception("Workflow execution failed")
            raise
    
    def partition_workflow(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Partition workflow into segments for distributed execution."""
        from src.services.workflow.partition_service import partition_workflow_advanced
        result = partition_workflow_advanced(config)
        return result.get("partition_map", [])
    
    def build_segment_config(self, segment: Dict[str, Any]) -> Dict[str, Any]:
        """Convert segment object to executable workflow config."""
        return {
            "nodes": segment.get("nodes", []),
            "edges": segment.get("edges", [])
        }
    
    def extract_and_save_subgraphs(
        self, 
        config: Dict[str, Any], 
        threshold: int = 100
    ) -> Dict[str, Any]:
        """
        워크플로우에서 서브그래프를 자동 추출하여 참조로 변환
        
        규칙:
        - loop/for_each 노드의 subgraph_inline이 threshold 노드 이상이면 추출
        - parallel_group의 각 branch도 동일 규칙 적용
        - 동일 내용 서브그래프는 중복 제거 (Content-Addressable Storage)
        
        Args:
            config: 워크플로우 설정
            threshold: 서브그래프 추출 임계값 (기본 100 노드)
        
        Returns:
            수정된 워크플로우 설정 (subgraph_inline → subgraph_ref)
        """
        from src.services.state.subgraph_store import create_subgraph_store
        
        subgraph_store = create_subgraph_store()
        modified_nodes = []
        
        nodes = config.get("nodes", [])
        
        for node in nodes:
            modified_node = node.copy()
            node_type = node.get("type")
            node_id = node.get("id")
            
            # loop/for_each 노드의 subgraph_inline 검사
            if node_type in ["loop", "for_each"]:
                node_config = node.get("config", {})
                
                if "subgraph_inline" in node_config:
                    subgraph_def = node_config["subgraph_inline"]
                    node_count = len(subgraph_def.get("nodes", []))
                    
                    # 임계값 이상이면 서브그래프로 추출
                    if node_count >= threshold:
                        try:
                            subgraph_ref = subgraph_store.save_subgraph(subgraph_def)
                            
                            # 참조로 변환
                            modified_node["config"] = node_config.copy()
                            modified_node["config"]["subgraph_ref"] = subgraph_ref
                            del modified_node["config"]["subgraph_inline"]
                            
                            logger.info(
                                f"[SUBGRAPH_EXTRACT] {node_id}: "
                                f"{node_count} nodes → {subgraph_ref[:20]}..."
                            )
                        except Exception as e:
                            logger.error(f"[SUBGRAPH_EXTRACT] Failed for {node_id}: {e}")
            
            # parallel_group의 branches 검사
            elif node_type == "parallel_group":
                branches = node.get("branches", [])
                modified_branches = []
                
                for idx, branch in enumerate(branches):
                    if not isinstance(branch, dict):
                        modified_branches.append(branch)
                        continue
                    
                    branch_nodes = branch.get("nodes", [])
                    
                    # 브랜치가 임계값 이상이면 서브그래프로 추출
                    if len(branch_nodes) >= threshold:
                        try:
                            subgraph_def = {
                                "nodes": branch_nodes,
                                "edges": branch.get("edges", [])
                            }
                            subgraph_ref = subgraph_store.save_subgraph(subgraph_def)
                            
                            # 참조로 변환
                            modified_branch = {
                                k: v for k, v in branch.items() 
                                if k not in ["nodes", "edges"]
                            }
                            modified_branch["subgraph_ref"] = subgraph_ref
                            
                            logger.info(
                                f"[SUBGRAPH_EXTRACT] {node_id}.branch[{idx}]: "
                                f"{len(branch_nodes)} nodes → {subgraph_ref[:20]}..."
                            )
                            
                            modified_branches.append(modified_branch)
                        except Exception as e:
                            logger.error(
                                f"[SUBGRAPH_EXTRACT] Failed for {node_id}.branch[{idx}]: {e}"
                            )
                            modified_branches.append(branch)
                    else:
                        modified_branches.append(branch)
                
                if modified_branches:
                    modified_node["branches"] = modified_branches
            
            modified_nodes.append(modified_node)
        
        return {
            **config,
            "nodes": modified_nodes
        }
    
    # =========================================================================
    # Private Helpers
    # =========================================================================
    
    def _parse_config(self, config_json: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Parse JSON config."""
        if isinstance(config_json, dict):
            return config_json
        try:
            return json.loads(config_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON config: {e}")
    
    def _validate_config(self, raw_config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate config against Pydantic schema."""
        try:
            validated = WorkflowConfigModel.model_validate(raw_config)
            return validated.model_dump()
        except ValidationError as ve:
            raise ValueError(f"Invalid workflow config: {ve}")
    
    def _apply_checkpointer(self, app, ddb_table_name: str):
        """Apply DynamoDB checkpointer if available."""
        try:
            from langgraph_checkpoint_dynamodb import DynamoDBSaver
            saver = DynamoDBSaver(table_name=ddb_table_name)
            return app.with_checkpointer(saver)
        except ImportError:
            logger.warning("DynamoDBSaver not found, skipping persistence")
            return app
    
    def _prepare_run_config(
        self, 
        run_config: Optional[Dict[str, Any]], 
        conversation_id: Optional[str]
    ) -> Dict[str, Any]:
        """Prepare runtime configuration."""
        final_config = dict(run_config) if run_config else {}
        
        if "configurable" not in final_config:
            final_config["configurable"] = {}
        
        configurable = final_config["configurable"]
        if not configurable.get("thread_id"):
            configurable["thread_id"] = conversation_id or "default_thread"
        if conversation_id and not configurable.get("conversation_id"):
            configurable["conversation_id"] = conversation_id
        
        return final_config
    
    def _get_mock_config(self, mock_behavior: str) -> Dict[str, Any]:
        """Return mock configurations for testing."""
        if mock_behavior == "E2E_S3_LARGE_DATA":
            return {
                "nodes": [{
                    "id": "large_data_generator", "type": "operator",
                    "config": {"code": "state['res'] = 'X'*300000", "output_key": "res"}
                }],
                "edges": [], "start_node": "large_data_generator"
            }
        elif mock_behavior == "CONTINUE":
            return {
                "nodes": [
                    {"id": "step1", "type": "operator", "config": {"sets": {"step": 1}}},
                    {"id": "step2", "type": "operator", "config": {"sets": {"step": 2}}}
                ],
                "edges": [{"source": "step1", "target": "step2"}], "start_node": "step1"
            }
        return {"nodes": [], "edges": []}


# Singleton
_orchestrator_instance = None

def get_workflow_orchestrator() -> WorkflowOrchestratorService:
    global _orchestrator_instance
    if _orchestrator_instance is None:
        # Import mask_pii from template renderer
        try:
            from src.services.common.template_renderer import get_template_renderer
            renderer = get_template_renderer()
            _orchestrator_instance = WorkflowOrchestratorService(mask_pii_fn=renderer.mask_pii)
        except ImportError:
            _orchestrator_instance = WorkflowOrchestratorService()
    return _orchestrator_instance
