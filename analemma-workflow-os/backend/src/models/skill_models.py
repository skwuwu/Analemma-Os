"""
Skill Models - Data structures for the Skills feature.

Skills are reusable, modular units of functionality that can be:
- Injected into workflow context at runtime
- Referenced by skill_executor nodes
- Composed hierarchically (skills can depend on other skills)

Architecture follows the design in the Skills integration plan:
- Each skill contains tool_definitions, system_instructions, and dependencies
- Skills are loaded via Context Hydration and stored in WorkflowState
- skill_executor nodes reference skills by ID and execute them
"""

from typing import TypedDict, List, Dict, Any, Optional
from datetime import datetime, timezone


# -----------------------------------------------------------------------------
# Core Skill Schema
# -----------------------------------------------------------------------------

class ToolDefinition(TypedDict, total=False):
    """Definition of a tool that a skill can use."""
    name: str  # Tool identifier
    description: str  # Human-readable description
    parameters: Dict[str, Any]  # JSON Schema for parameters
    required_api_keys: List[str]  # e.g., ["OPENAI_API_KEY"]
    handler_type: str  # "llm_chat", "api_call", "operator", etc.
    handler_config: Dict[str, Any]  # Handler-specific configuration


class SkillDependency(TypedDict, total=False):
    """Reference to a dependent skill."""
    skill_id: str  # ID of the dependent skill
    version: Optional[str]  # Specific version, or None for latest
    alias: Optional[str]  # Optional alias for namespacing in context


class SkillSchema(TypedDict, total=False):
    """
    Complete Skill definition stored in DynamoDB.
    
    Primary Key: skillId (HASH) + version (RANGE)
    GSI: OwnerIdIndex for user-scoped queries
    """
    # Primary identifiers
    skill_id: str  # Unique skill identifier (UUID or human-readable)
    version: str  # Semantic version (e.g., "1.0.0") or "latest"
    
    # Ownership & access
    owner_id: str  # User ID who owns/created this skill
    visibility: str  # "private", "public", "organization"
    
    # Metadata
    name: str  # Human-readable name
    description: str  # Detailed description
    category: str  # Categorization (e.g., "data_processing", "llm_tools")
    tags: List[str]  # Searchable tags
    
    # Core skill content
    tool_definitions: List[ToolDefinition]  # Available tools in this skill
    system_instructions: str  # Instructions injected into LLM context
    
    # Dependencies - other skills this skill depends on
    dependencies: List[SkillDependency]
    
    # Resource requirements
    required_api_keys: List[str]  # API keys needed at runtime
    required_permissions: List[str]  # AWS/system permissions needed
    
    # Execution hints
    timeout_seconds: int  # Default timeout for skill execution
    retry_config: Dict[str, Any]  # Retry policy configuration
    
    # Timestamps
    created_at: str  # ISO 8601 timestamp
    updated_at: str  # ISO 8601 timestamp
    
    # Status
    status: str  # "active", "deprecated", "archived"


# -----------------------------------------------------------------------------
# Runtime Skill Context (hydrated into WorkflowState)
# -----------------------------------------------------------------------------

class HydratedSkill(TypedDict, total=False):
    """
    Skill data after Context Hydration - ready for execution.
    This is what gets stored in WorkflowState.active_skills
    """
    skill_id: str
    version: str
    name: str
    tool_definitions: List[ToolDefinition]
    system_instructions: str
    # Flattened dependencies (already resolved)
    resolved_dependencies: Dict[str, "HydratedSkill"]


class SkillExecutionLog(TypedDict, total=False):
    """Log entry for skill execution tracking."""
    skill_id: str
    node_id: str  # Node that executed this skill
    tool_name: str  # Which tool was invoked
    input_params: Dict[str, Any]
    output: Any
    execution_time_ms: int
    timestamp: str  # ISO 8601


# -----------------------------------------------------------------------------
# Skill Node Configuration (in workflow JSON)
# -----------------------------------------------------------------------------

class SkillExecutorNodeConfig(TypedDict, total=False):
    """
    Configuration for a skill_executor node in workflow JSON.
    
    Example workflow node:
    {
        "id": "process_data",
        "type": "skill_executor",
        "skill_ref": "data-processor-v1",
        "tool_call": "transform_json",
        "input_mapping": {"source": "{{prev_result}}"},
        "output_key": "transformed_data"
    }
    """
    skill_ref: str  # Skill ID to reference
    skill_version: Optional[str]  # Optional specific version
    tool_call: str  # Which tool to invoke from src.the skill
    input_mapping: Dict[str, str]  # Template mappings for tool inputs
    output_key: str  # State key to store result
    error_handling: str  # "fail", "skip", "retry"


# -----------------------------------------------------------------------------
# Sub-graph Abstraction Schema (Hierarchical Workflow Support)
# -----------------------------------------------------------------------------

class SchemaField(TypedDict, total=False):
    """Schema definition for input/output fields."""
    type: str  # "string", "number", "object", "array", "any"
    description: str
    required: bool
    default: Any


class SubgraphMetadata(TypedDict, total=False):
    """UI metadata for subgraph visualization."""
    name: str  # Display name (e.g., "Data Refiner")
    description: str
    icon: str  # Emoji or icon name
    color: str  # Hex color for UI
    collapsed: bool  # UI collapse state


class SubgraphDefinition(TypedDict, total=False):
    """
    Definition of a sub-graph (nested workflow).
    
    Sub-graphs can be:
    - Defined inline within a workflow
    - Referenced from src.a saved Skill
    - Nested recursively (sub-graphs within sub-graphs)
    """
    # Internal structure
    nodes: List[Dict[str, Any]]  # Standard node definitions
    edges: List[Dict[str, Any]]  # Standard edge definitions
    
    # Nested sub-graphs (recursive)
    subgraphs: Optional[Dict[str, "SubgraphDefinition"]]
    
    # Input/Output schema for state mapping
    input_schema: Dict[str, SchemaField]  # Fields required from src.parent
    output_schema: Dict[str, SchemaField]  # Fields returned to parent
    
    # Metadata
    metadata: SubgraphMetadata


class SubgraphNodeConfig(TypedDict, total=False):
    """
    Configuration for a subgraph node in workflow JSON.
    
    Example:
    {
        "id": "data_processing_group",
        "type": "subgraph",
        "subgraph_ref": "sg-12345",  // Reference to subgraphs dict
        "input_mapping": {"raw_data": "{{user_input}}"},
        "output_mapping": {"processed_result": "final_output"},
        "metadata": {"name": "Data Processor", "icon": "⚙️"}
    }
    """
    # Sub-graph reference (mutually exclusive)
    subgraph_ref: Optional[str]  # Reference to subgraphs[ref]
    subgraph_inline: Optional[SubgraphDefinition]  # Or inline definition
    skill_ref: Optional[str]  # Or reference to a saved Skill
    
    # State mapping between parent and child
    input_mapping: Dict[str, str]  # parent_key -> child_key (with templates)
    output_mapping: Dict[str, str]  # child_key -> parent_key
    
    # UI metadata
    metadata: SubgraphMetadata
    
    # Execution options
    timeout_seconds: int
    error_handling: str  # "fail", "skip", "isolate"


class ExtendedWorkflowConfig(TypedDict, total=False):
    """
    Extended workflow configuration with sub-graph support.
    
    This extends the standard workflow config to support hierarchical structures.
    """
    # Standard workflow fields
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    
    # Sub-graph definitions (referenced by subgraph_ref)
    subgraphs: Dict[str, SubgraphDefinition]
    
    # Workflow metadata
    metadata: Dict[str, Any]


# -----------------------------------------------------------------------------
# Extended Skill Schema (Subgraph-based Skills)
# -----------------------------------------------------------------------------

class SubgraphSkillSchema(SkillSchema, total=False):
    """
    Extended Skill schema for subgraph-based skills.
    
    A skill can be either:
    - tool_based: Uses tool_definitions for discrete tools
    - subgraph_based: Contains a complete subgraph workflow
    """
    skill_type: str  # "tool_based" | "subgraph_based"
    
    # For subgraph_based skills
    subgraph_config: Optional[SubgraphDefinition]
    
    # Explicit input/output schema (used instead of tool_definitions)
    input_schema: Dict[str, SchemaField]
    output_schema: Dict[str, SchemaField]


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def create_skill_id(name: str) -> str:
    """Generate a skill ID from src.a name."""
    import uuid
    # Normalize name to lowercase, replace spaces with hyphens
    normalized = name.lower().replace(" ", "-").replace("_", "-")
    # Add short UUID suffix for uniqueness
    short_uuid = str(uuid.uuid4())[:8]
    return f"{normalized}-{short_uuid}"


def create_default_skill(
    name: str,
    owner_id: str,
    description: str = "",
    tool_definitions: Optional[List[ToolDefinition]] = None,
    system_instructions: str = "",
) -> SkillSchema:
    """Create a new skill with default values."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return SkillSchema(
        skill_id=create_skill_id(name),
        version="1.0.0",
        owner_id=owner_id,
        visibility="private",
        name=name,
        description=description,
        category="general",
        tags=[],
        tool_definitions=tool_definitions or [],
        system_instructions=system_instructions,
        dependencies=[],
        required_api_keys=[],
        required_permissions=[],
        timeout_seconds=300,
        retry_config={"max_retries": 3, "backoff_multiplier": 2},
        created_at=now,
        updated_at=now,
        status="active",
    )


def validate_skill(skill: SkillSchema) -> List[str]:
    """
    Validate a skill schema and return list of errors (empty if valid).
    
    Supports both tool_based (default) and subgraph_based skills:
    - tool_based: Requires tool_definitions with valid handlers
    - subgraph_based: Requires subgraph_config with nodes/edges
    """
    errors = []
    
    # Required fields
    if not skill.get("skill_id"):
        errors.append("skill_id is required")
    if not skill.get("version"):
        errors.append("version is required")
    if not skill.get("owner_id"):
        errors.append("owner_id is required")
    if not skill.get("name"):
        errors.append("name is required")
    
    # Determine skill type (default: tool_based)
    skill_type = skill.get("skill_type", "tool_based")
    valid_skill_types = ["tool_based", "subgraph_based"]
    if skill_type not in valid_skill_types:
        errors.append(f"skill_type must be one of: {valid_skill_types}")
        return errors  # Early return for invalid type
    
    # Type-specific validation
    if skill_type == "subgraph_based":
        errors.extend(_validate_subgraph_skill(skill))
    else:
        errors.extend(_validate_tool_based_skill(skill))
    
    # Validate visibility
    valid_visibility = ["private", "public", "organization"]
    if skill.get("visibility") and skill["visibility"] not in valid_visibility:
        errors.append(f"visibility must be one of: {valid_visibility}")
    
    # Validate status
    valid_status = ["active", "deprecated", "archived"]
    if skill.get("status") and skill["status"] not in valid_status:
        errors.append(f"status must be one of: {valid_status}")
    
    return errors


def _validate_tool_based_skill(skill: SkillSchema) -> List[str]:
    """Validate a tool_based skill."""
    errors = []
    
    tool_definitions = skill.get("tool_definitions", [])
    
    # tool_based skills should have at least one tool definition
    # (warning, not error - empty skills might be valid placeholders)
    if not tool_definitions:
        # This is a warning logged, not an error
        pass
    
    for i, tool in enumerate(tool_definitions):
        if not tool.get("name"):
            errors.append(f"tool_definitions[{i}].name is required")
        if not tool.get("handler_type"):
            errors.append(f"tool_definitions[{i}].handler_type is required")
        
        # Validate handler_type values
        valid_handlers = ["llm_chat", "api_call", "operator", "python", "javascript"]
        if tool.get("handler_type") and tool["handler_type"] not in valid_handlers:
            errors.append(
                f"tool_definitions[{i}].handler_type must be one of: {valid_handlers}"
            )
    
    return errors


def _validate_subgraph_skill(skill: SkillSchema) -> List[str]:
    """
    Validate a subgraph_based skill.
    
    Subgraph skills must have:
    - subgraph_config with nodes and edges
    - input_schema and output_schema for state mapping
    """
    errors = []
    
    subgraph_config = skill.get("subgraph_config")
    
    # subgraph_config is required for subgraph_based skills
    if not subgraph_config:
        errors.append("subgraph_config is required for skill_type='subgraph_based'")
        return errors
    
    if not isinstance(subgraph_config, dict):
        errors.append("subgraph_config must be a dictionary")
        return errors
    
    # Validate nodes
    nodes = subgraph_config.get("nodes", [])
    if not nodes:
        errors.append("subgraph_config.nodes is required and cannot be empty")
    else:
        if not isinstance(nodes, list):
            errors.append("subgraph_config.nodes must be a list")
        else:
            node_ids = set()
            for i, node in enumerate(nodes):
                if not isinstance(node, dict):
                    errors.append(f"subgraph_config.nodes[{i}] must be a dictionary")
                    continue
                
                node_id = node.get("id")
                if not node_id:
                    errors.append(f"subgraph_config.nodes[{i}].id is required")
                elif node_id in node_ids:
                    errors.append(f"Duplicate node id: {node_id}")
                else:
                    node_ids.add(node_id)
                
                if not node.get("type"):
                    errors.append(f"subgraph_config.nodes[{i}].type is required")
    
    # Validate edges
    edges = subgraph_config.get("edges", [])
    if not isinstance(edges, list):
        errors.append("subgraph_config.edges must be a list")
    else:
        for i, edge in enumerate(edges):
            if not isinstance(edge, dict):
                errors.append(f"subgraph_config.edges[{i}] must be a dictionary")
                continue
            
            if not edge.get("source"):
                errors.append(f"subgraph_config.edges[{i}].source is required")
            if not edge.get("target"):
                errors.append(f"subgraph_config.edges[{i}].target is required")
    
    # Validate input_schema (optional but recommended)
    input_schema = skill.get("input_schema")
    if input_schema and not isinstance(input_schema, dict):
        errors.append("input_schema must be a dictionary")
    
    # Validate output_schema (optional but recommended)
    output_schema = skill.get("output_schema")
    if output_schema and not isinstance(output_schema, dict):
        errors.append("output_schema must be a dictionary")
    
    # tool_definitions should be empty for subgraph_based skills
    if skill.get("tool_definitions"):
        errors.append(
            "tool_definitions should be empty for skill_type='subgraph_based'; "
            "use subgraph_config instead"
        )
    
    return errors
