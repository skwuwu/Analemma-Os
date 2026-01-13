"""
AuditService - Workflow Audit and Validation Service

Extracted from `logical_auditor.py` handler.
Provides comprehensive workflow validation:
- Structural integrity checks
- Cycle detection
- Data flow analysis
- Execution simulation
"""

import logging
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple, Optional

logger = logging.getLogger(__name__)


class AuditService:
    """
    Workflow logical validation and simulation service.
    
    Checks:
    - Orphan nodes (disconnected)
    - Cycles (infinite loops)
    - Unreachable nodes
    - Dead ends
    - Missing configurations
    - Data flow issues
    - Duplicate connections
    """
    
    # Node types that are expected to be endpoints
    END_NODE_TYPES = {"end", "terminate", "return", "exit"}
    
    # Node types that intentionally contain loops
    LOOP_NODE_TYPES = {"for_each", "while", "loop"}
    
    # Required config fields by node type
    REQUIRED_CONFIGS = {
        "llm_chat": ["prompt_content"],
        "api_call": ["url"],
        "db_query": ["query", "connection_string"],
    }

    def __init__(self, workflow: Dict[str, Any]):
        self.workflow = workflow
        self.nodes: Dict[str, Dict[str, Any]] = {
            n.get("id"): n for n in workflow.get("nodes", [])
        }
        self.edges: List[Dict[str, Any]] = workflow.get("edges", [])
        
        # Build adjacency lists
        self.outgoing: Dict[str, List[str]] = defaultdict(list)
        self.incoming: Dict[str, List[str]] = defaultdict(list)
        
        for edge in self.edges:
            src = edge.get("source")
            tgt = edge.get("target")
            if src and tgt:
                self.outgoing[src].append(tgt)
                self.incoming[tgt].append(src)
        
        # Identify start node
        self.start_node = workflow.get("start_node")
        if not self.start_node:
            # Heuristic: node with no incoming edges
            for node_id in self.nodes:
                if node_id not in self.incoming:
                    self.start_node = node_id
                    break

    def audit(self) -> List[Dict[str, Any]]:
        """
        Perform full validation.
        
        Returns:
            List of issues with level (error/warning/info), type, and message
        """
        issues = []
        issues.extend(self._check_orphan_nodes())
        issues.extend(self._check_cycles())
        issues.extend(self._check_unreachable_nodes())
        issues.extend(self._check_dead_ends())
        issues.extend(self._check_missing_configs())
        issues.extend(self._check_data_flow())
        issues.extend(self._check_duplicate_connections())
        return issues

    def _check_orphan_nodes(self) -> List[Dict[str, Any]]:
        """Detect nodes with no connections."""
        issues = []
        for node_id in self.nodes:
            if node_id not in self.outgoing and node_id not in self.incoming:
                issues.append({
                    "level": "warning",
                    "type": "orphan_node",
                    "node_id": node_id,
                    "message": f"Node '{node_id}' has no connections"
                })
        return issues

    def _check_cycles(self) -> List[Dict[str, Any]]:
        """Detect infinite loops using DFS."""
        issues = []
        visited: Set[str] = set()
        rec_stack: Set[str] = set()
        
        def dfs(node: str, path: List[str]) -> Optional[List[str]]:
            if node in rec_stack:
                cycle_start = path.index(node)
                return path[cycle_start:]
            
            if node in visited:
                return None
            
            # Skip intentional loop nodes
            node_data = self.nodes.get(node, {})
            if node_data.get("type") in self.LOOP_NODE_TYPES:
                visited.add(node)
                return None
            
            visited.add(node)
            rec_stack.add(node)
            
            for neighbor in self.outgoing.get(node, []):
                cycle = dfs(neighbor, path + [neighbor])
                if cycle:
                    return cycle
            
            rec_stack.remove(node)
            return None
        
        for node_id in self.nodes:
            if node_id not in visited:
                cycle = dfs(node_id, [node_id])
                if cycle:
                    issues.append({
                        "level": "error",
                        "type": "cycle_detected",
                        "nodes": cycle,
                        "message": f"Cycle detected: {' → '.join(cycle)}"
                    })
        
        return issues

    def _check_unreachable_nodes(self) -> List[Dict[str, Any]]:
        """Detect nodes unreachable from start."""
        issues = []
        if not self.start_node:
            return issues
        
        reachable: Set[str] = set()
        queue = [self.start_node]
        
        while queue:
            node = queue.pop(0)
            if node in reachable:
                continue
            reachable.add(node)
            queue.extend(self.outgoing.get(node, []))
        
        for node_id in self.nodes:
            if node_id not in reachable:
                issues.append({
                    "level": "warning",
                    "type": "unreachable_node",
                    "node_id": node_id,
                    "message": f"Node '{node_id}' is unreachable from start"
                })
        
        return issues

    def _check_dead_ends(self) -> List[Dict[str, Any]]:
        """Detect nodes that are not end nodes but have no outgoing edges."""
        issues = []
        for node_id, node_data in self.nodes.items():
            node_type = node_data.get("type", "").lower()
            
            if node_type not in self.END_NODE_TYPES:
                if node_id not in self.outgoing or not self.outgoing[node_id]:
                    issues.append({
                        "level": "warning",
                        "type": "dead_end",
                        "node_id": node_id,
                        "message": f"Node '{node_id}' has no outgoing edges (dead end)"
                    })
        
        return issues

    def _check_missing_configs(self) -> List[Dict[str, Any]]:
        """Detect nodes with missing required configuration."""
        issues = []
        for node_id, node_data in self.nodes.items():
            node_type = node_data.get("type")
            required = self.REQUIRED_CONFIGS.get(node_type, [])
            config = node_data.get("config", {})
            
            for field in required:
                if field not in config or not config[field]:
                    issues.append({
                        "level": "error",
                        "type": "missing_config",
                        "node_id": node_id,
                        "field": field,
                        "message": f"Node '{node_id}' ({node_type}) missing required config: {field}"
                    })
        
        return issues

    def _check_data_flow(self) -> List[Dict[str, Any]]:
        """Simple heuristic data flow analysis."""
        issues = []
        # Check for template variables that reference non-existent outputs
        # This is a simplified check
        
        for node_id, node_data in self.nodes.items():
            config = node_data.get("config", {})
            prompt = config.get("prompt_content", "")
            
            if isinstance(prompt, str) and "{{" in prompt:
                # Extract variable references
                import re
                refs = re.findall(r"\{\{(\w+)\}\}", prompt)
                
                # Check if upstream nodes produce these outputs
                # (Simplified: just warn if references exist)
                if refs:
                    issues.append({
                        "level": "info",
                        "type": "data_flow_reference",
                        "node_id": node_id,
                        "references": refs,
                        "message": f"Node '{node_id}' references: {', '.join(refs)}"
                    })
        
        return issues

    def _check_duplicate_connections(self) -> List[Dict[str, Any]]:
        """Detect duplicate edges."""
        issues = []
        seen: Set[Tuple[str, str]] = set()
        
        for edge in self.edges:
            pair = (edge.get("source"), edge.get("target"))
            if pair in seen:
                issues.append({
                    "level": "warning",
                    "type": "duplicate_edge",
                    "source": pair[0],
                    "target": pair[1],
                    "message": f"Duplicate edge: {pair[0]} → {pair[1]}"
                })
            seen.add(pair)
        
        return issues

    def simulate(self, mock_inputs: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Simulate workflow execution without actual API/LLM calls.
        
        Returns:
            {
                "success": bool,
                "steps": [...],
                "errors": [...],
                "visited_nodes": [...],
                "coverage": float
            }
        """
        state = dict(mock_inputs or {})
        visited = []
        steps = []
        errors = []
        
        current = self.start_node
        max_iterations = len(self.nodes) * 2  # Safety limit
        iterations = 0
        
        while current and iterations < max_iterations:
            iterations += 1
            visited.append(current)
            
            node = self.nodes.get(current)
            if not node:
                errors.append(f"Node '{current}' not found")
                break
            
            step_result = self._simulate_node(node, state)
            steps.append({"node_id": current, **step_result})
            
            if step_result.get("error"):
                errors.append(step_result["error"])
            
            # Move to next node
            next_nodes = self.outgoing.get(current, [])
            current = next_nodes[0] if next_nodes else None
        
        coverage = len(set(visited)) / len(self.nodes) if self.nodes else 0.0
        
        return {
            "success": len(errors) == 0,
            "steps": steps,
            "errors": errors,
            "visited_nodes": visited,
            "coverage": round(coverage, 2)
        }

    def _simulate_node(self, node: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate a single node execution."""
        node_type = node.get("type", "operator")
        node_id = node.get("id")
        
        if node_type == "llm_chat":
            # Mock LLM response
            state[f"{node_id}_output"] = "[SIMULATED LLM RESPONSE]"
            return {"type": "llm_chat", "output": "simulated"}
        
        elif node_type == "api_call":
            # Mock API response
            state[f"{node_id}_response"] = {"status": 200, "data": "simulated"}
            return {"type": "api_call", "status": 200}
        
        elif node_type == "operator":
            # Simulate operator
            return {"type": "operator", "status": "ok"}
        
        return {"type": node_type, "status": "simulated"}

    def get_execution_order(self) -> List[str]:
        """Return topologically sorted execution order."""
        in_degree = {node_id: 0 for node_id in self.nodes}
        for node_id in self.nodes:
            for neighbor in self.outgoing.get(node_id, []):
                if neighbor in in_degree:
                    in_degree[neighbor] += 1
        
        queue = [n for n, d in in_degree.items() if d == 0]
        order = []
        
        while queue:
            node = queue.pop(0)
            order.append(node)
            
            for neighbor in self.outgoing.get(node, []):
                if neighbor in in_degree:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)
        
        return order

    def get_affected_downstream(self, node_id: str) -> List[str]:
        """Get all nodes affected by changes to a specific node."""
        affected = []
        queue = list(self.outgoing.get(node_id, []))
        seen = set()
        
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            affected.append(current)
            queue.extend(self.outgoing.get(current, []))
        
        return affected


# API Functions

def audit_workflow(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Audit a workflow and return issues."""
    auditor = AuditService(workflow)
    return auditor.audit()


def simulate_workflow(
    workflow: Dict[str, Any],
    mock_inputs: Dict[str, Any] = None
) -> Dict[str, Any]:
    """Simulate a workflow execution."""
    auditor = AuditService(workflow)
    return auditor.simulate(mock_inputs)


def get_workflow_analysis(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Get comprehensive workflow analysis."""
    auditor = AuditService(workflow)
    
    issues = auditor.audit()
    simulation = auditor.simulate()
    
    return {
        "node_count": len(auditor.nodes),
        "edge_count": len(auditor.edges),
        "start_node": auditor.start_node,
        "execution_order": auditor.get_execution_order(),
        "issues": issues,
        "issue_summary": {
            "errors": len([i for i in issues if i["level"] == "error"]),
            "warnings": len([i for i in issues if i["level"] == "warning"]),
            "info": len([i for i in issues if i["level"] == "info"])
        },
        "simulation": simulation
    }
