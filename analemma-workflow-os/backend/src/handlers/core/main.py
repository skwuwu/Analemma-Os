import json
import time
import re
import os
import copy
import operator
import concurrent.futures
import logging
from typing import TypedDict, Dict, Any, List, Optional, Annotated, Union, Callable
from functools import partial
import socket
import ipaddress
from urllib.parse import urlparse

from pydantic import BaseModel, Field, conlist, constr, ValidationError

import boto3
from botocore.config import Config
from botocore.exceptions import ReadTimeoutError
from src.langchain_core_custom.outputs import LLMResult, Generation

# -----------------------------------------------------------------------------
# 1. Imports & Constants
# -----------------------------------------------------------------------------

# LangGraph imports for state management
try:
    from langgraph.graph.message import add_messages
except ImportError:
    # Fallback logic mainly for basic testing without full deps
    def add_messages(left, right):
        if not isinstance(left, list): left = [left] if left else []
        if not isinstance(right, list): right = [right] if right else []
        return left + right

# 커스텀 예외: Step Functions가 Error 필드로 쉽게 감지 가능 (비동기 처리용)
class AsyncLLMRequiredException(Exception):
    """Exception raised when async LLM processing is required"""
    pass

# HITP (Human in the Loop) 엣지 타입들
HITP_EDGE_TYPES = {"hitp", "human_in_the_loop", "pause"}

# Configure basic logging
logger = logging.getLogger("workflow")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Check for LangGraph availability and warn if using fallback
try:
    from langgraph.graph.message import add_messages
except ImportError:
    logger.warning("LangGraph not available, using fallback message reducer. This may cause issues with complex workflows.")

# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# 2. State Schema Definition
# -----------------------------------------------------------------------------

# Minimal WorkflowState - flexible dict-based state used at runtime
# Annotated is CRITICAL for history accumulation (reducer)
class WorkflowState(TypedDict, total=False):
    user_query: str
    user_api_keys: Dict[str, str]
    step_history: List[str]
    # Messages list that accumulates instead of overwriting
    messages: Annotated[List[Dict[str, Any]], add_messages]
    # Allow dynamic keys via loose typing mechanism in LangGraph (implicit)
    # Common dynamic fields
    item: Any  # For for_each operations
    result: Any  # General result storage
    
    # --- Skills Integration ---
    # List of skill IDs to inject into context at runtime
    injected_skills: List[str]
    # Reference to active context (for large skill payloads offloaded to S3)
    active_context_ref: str
    # Hydrated skill data loaded during Context Hydration phase
    active_skills: Dict[str, Any]  # skill_id -> HydratedSkill
    # Accumulated execution logs for skill invocations
    skill_execution_log: Annotated[List[Dict[str, Any]], operator.add]


# -----------------------------------------------------------------------------
# 3. Core Helper Functions (Template, S3 Check, Bedrock)
# -----------------------------------------------------------------------------

# --- Pydantic Schemas for workflow config validation ---
class EdgeModel(BaseModel):
    source: constr(min_length=1, max_length=128)
    target: constr(min_length=1, max_length=128)
    type: constr(min_length=1, max_length=64) = "edge"


class NodeModel(BaseModel):
    id: constr(min_length=1, max_length=128)
    type: constr(min_length=1, max_length=64)
    label: Optional[constr(min_length=0, max_length=256)] = None
    action: Optional[constr(min_length=0, max_length=256)] = None
    hitp: Optional[bool] = None
    config: Optional[Dict[str, Any]] = None


class WorkflowConfigModel(BaseModel):
    workflow_name: Optional[constr(min_length=0, max_length=256)] = None
    description: Optional[constr(min_length=0, max_length=512)] = None
    nodes: conlist(NodeModel, min_length=1, max_length=500)
    edges: conlist(EdgeModel, min_length=0, max_length=1000)
    start_node: Optional[constr(min_length=1, max_length=128)] = None


# -----------------------------------------------------------------------------
# PII Masking Helpers for Glass-Box logging
# -----------------------------------------------------------------------------
PII_REGEX_PATTERNS = [
    (r"\bsk-[a-zA-Z0-9]{20,}\b", "[API_KEY_REDACTED]"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "[EMAIL_REDACTED]"),
    (r"\d{3}-\d{3,4}-\d{4}", "[PHONE_REDACTED]"),
]


def mask_pii(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    masked = text
    for pattern, repl in PII_REGEX_PATTERNS:
        masked = re.sub(pattern, repl, masked)
    return masked


def _get_nested_value(state: Dict[str, Any], path: str, default: Any = "") -> Any:
    """Retrieve nested value from src.state using dot-separated path."""
    if not path: return default
    parts = path.split('.')
    cur: Any = state
    try:
        for p in parts:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return default
        return cur
    except Exception:
        return default


def _render_template(template: Any, state: Dict[str, Any]) -> Any:
    """Render {{variable}} templates against the provided state."""
    if template is None: return None
    if isinstance(template, str):
        def _repl(m):
            key = m.group(1).strip()
            if key == "__state_json":
                try: return json.dumps(state, ensure_ascii=False)
                except: return str(state)
            val = _get_nested_value(state, key, "")
            if isinstance(val, (dict, list)):
                try: return json.dumps(val)
                except: return str(val)
            return str(val)
        return re.sub(r"\{\{\s*([\w\.]+)\s*\}\}", _repl, template)
    if isinstance(template, dict):
        return {k: _render_template(v, state) for k, v in template.items()}
    if isinstance(template, list):
        return [_render_template(v, state) for v in template]
    return template


# --- Bedrock & LLM Helpers ---
_bedrock_client = None
# Base retry/timeout config for reuse (reduces cold-start overhead)
_bedrock_base_config = Config(
    retries={"max_attempts": 3, "mode": "standard"},
    read_timeout=int(os.environ.get("BEDROCK_READ_TIMEOUT_SECONDS", "60")),
    connect_timeout=int(os.environ.get("BEDROCK_CONNECT_TIMEOUT_SECONDS", "5")),
)

def get_bedrock_client():
    global _bedrock_client
    if _bedrock_client:
        return _bedrock_client
    try:
        region = os.environ.get("AWS_REGION", "us-east-1")
        _bedrock_client = boto3.client("bedrock-runtime", region_name=region, config=_bedrock_base_config)
        return _bedrock_client
    except Exception:
        logger.warning("Failed to create Bedrock client")
        return None

def _is_mock_mode() -> bool:
    return os.getenv("MOCK_MODE", "false").strip().lower() in {"true", "1", "yes", "on"}

def _build_mock_llm_text(model_id: str, prompt: str) -> str:
    return f"[MOCK_MODE] Response from {model_id}. Prompt: {prompt[:50]}..."

def invoke_bedrock_model(model_id: str, system_prompt: str | None, user_prompt: str, max_tokens: int | None = None, temperature: float | None = None, read_timeout_seconds: int | None = None) -> Any:
    if _is_mock_mode():
        logger.info(f"MOCK_MODE: Skipping Bedrock call for {model_id}")
        return {"content": [{"text": _build_mock_llm_text(model_id, user_prompt)}]}

    try:
        # Prefer shared client; only create ad-hoc client when caller explicitly needs longer timeout
        if read_timeout_seconds and read_timeout_seconds != _bedrock_base_config.read_timeout:
            client = boto3.client("bedrock-runtime", config=_bedrock_base_config.merge(Config(read_timeout=read_timeout_seconds)))
        else:
            client = get_bedrock_client()

        if not client:
            # Fallback if client creation failed
            return {"content": [{"text": "[Error] Bedrock client unavailable"}]}

        messages = [{"role": "user", "content": [{"type": "text", "text": user_prompt}]}]
        payload = {"messages": messages}
        
        if "gemini" not in (model_id or "").lower():
            payload["anthropic_version"] = "bedrock-2023-05-31"
        if system_prompt: payload["system"] = system_prompt
        if max_tokens: payload["max_tokens"] = int(max_tokens)
        if temperature: payload["temperature"] = float(temperature)

        resp = client.invoke_model(body=json.dumps(payload), modelId=model_id)
        return json.loads(resp['body'].read())

    except ReadTimeoutError:
        logger.warning(f"Bedrock read timeout for {model_id}")
        raise AsyncLLMRequiredException("SDK read timeout")
    except Exception as e:
        logger.exception(f"Bedrock invocation failed for {model_id}")
        raise e

def extract_text_from_bedrock_response(resp: Any) -> str:
    """Extract text from src.standard Bedrock response format."""
    try:
        if isinstance(resp, dict):
            c = resp.get("content")
            if isinstance(c, list) and c:
                if "text" in c[0]: return c[0]["text"]
        return str(resp)
    except Exception:
        return str(resp)

# Async processing threshold (configurable via environment variable)
ASYNC_TOKEN_THRESHOLD = int(os.getenv('ASYNC_TOKEN_THRESHOLD', '2000'))

def should_use_async_llm(config: Dict[str, Any]) -> bool:
    """Heuristic to check if async processing is needed."""
    max_tokens = config.get("max_tokens", 0)
    model = config.get("model", "")
    force_async = config.get("force_async", False)
    
    high_token_count = max_tokens > ASYNC_TOKEN_THRESHOLD
    heavy_model = "claude-3-opus" in model
    
    if high_token_count or heavy_model or force_async:
        logger.info(f"Async required: tokens={max_tokens}, model={model}, force={force_async}")
        return True
    return False


# -----------------------------------------------------------------------------
# 4. Node Runners Implementation
# -----------------------------------------------------------------------------

def llm_chat_runner(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Standard LLM Chat Runner with Async detection."""
    prompt_template = config.get("prompt_content", "")
    prompt = _render_template(prompt_template, state)
    
    # 2. Check Async Conditions
    node_id = config.get("id", "llm")
    model = config.get("model") or "gpt-3.5-turbo"
    max_tokens = config.get("max_tokens", 1024)
    
    if should_use_async_llm(config):
        logger.warning(f"🚨 Async required by heuristic for node {node_id}")
        raise AsyncLLMRequiredException("Resource-intensive processing required")

    # 3. Invoke
    meta = {"model": model, "max_tokens": max_tokens}
    
    # [Fix] Manually trigger callbacks since we are using Boto3 directly
    callbacks = config.get("callbacks", [])
    run_manager = None
    if callbacks:
        # We need to manually call on_llm_start
        # Note: In a real LangChain node, this is handled by the framework.
        # Here we simulate it for our StateHistoryCallback.
        for cb in callbacks:
            if hasattr(cb, 'on_llm_start'):
                try:
                    cb.on_llm_start(serialized={"name": node_id}, prompts=[prompt])
                except Exception:
                    pass

    try:
        resp = invoke_bedrock_model(
            model_id=model,
            system_prompt=config.get("system_prompt"),
            user_prompt=prompt,
            max_tokens=max_tokens,
            temperature=config.get("temperature"),
            read_timeout_seconds=90 # Adaptive timeout
        )
        text = extract_text_from_bedrock_response(resp)
        
        # Extract usage stats if available
        usage = {}
        if isinstance(resp, dict) and "usage" in resp:
            usage = resp["usage"]
        
        # [Fix] Manually trigger on_llm_end
        if callbacks:
            llm_result = LLMResult(generations=[[Generation(text=text)]], llm_output={"usage": usage})
            for cb in callbacks:
                if hasattr(cb, 'on_llm_end'):
                    try:
                        cb.on_llm_end(response=llm_result)
                    except Exception:
                        pass
        
        # Update history
        current_history = state.get("step_history", [])
        new_history = current_history + [f"{node_id}:llm_call"]
        
        out_key = config.get("writes_state_key") or f"{node_id}_output"
        return {out_key: text, f"{node_id}_meta": meta, "step_history": new_history, "usage": usage}
        
    except Exception as e:
        # [Fix] Manually trigger on_llm_error
        if callbacks:
            for cb in callbacks:
                if hasattr(cb, 'on_llm_error'):
                    try:
                        cb.on_llm_error(error=e)
                    except Exception:
                        pass

        if isinstance(e, AsyncLLMRequiredException):
            # Bubble up to let orchestrator pause
            raise
        logger.exception(f"LLM execution failed for node {node_id}")
        raise

def operator_runner(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Runs arbitrary python code (sandboxed)."""
    node_id = config.get("id", "operator")
    code = config.get("code") or (config.get("config") or {}).get("code")
    result_updates = {}
    mock_mode_enabled = _is_mock_mode()
    
    # 1. 'sets' shorthand
    sets = config.get("sets")
    if isinstance(sets, dict):
        result_updates.update(sets)

    # 2. Execute code with security restrictions
    if code:
        # Only allow exec in MOCK_MODE to avoid RCE in production paths
        if not mock_mode_enabled:
            raise PermissionError(f"Operator code execution is disabled outside MOCK_MODE (node={node_id})")
        try:
            # Security: Restrict builtins to prevent dangerous operations
            safe_builtins = {
                "print": print,
                "len": len,
                "list": list,
                "dict": dict,
                "range": range,
                "str": str,
                "int": int,
                "float": float,
                "bool": bool,
                "sum": sum,
                "min": min,
                "max": max,
                "abs": abs,
                "round": round,
                "enumerate": enumerate,
                "zip": zip,
                "sorted": sorted,
                "reversed": reversed,
                # [FIX] Add missing built-ins for test workflow support
                "all": all,
                "any": any,
                "filter": filter,
                "map": map,
                "isinstance": isinstance,
                "type": type,
                "tuple": tuple,
                "set": set,
                "frozenset": frozenset,
                # Exception classes
                "Exception": Exception,
                "ValueError": ValueError,
                "TypeError": TypeError,
                "KeyError": KeyError,
                "RuntimeError": RuntimeError,
                "IndexError": IndexError,
                "AttributeError": AttributeError,
                "StopIteration": StopIteration,
                "True": True,
                "False": False,
                "None": None,
            }
            
            # Create a copy of state for execution
            exec_state = dict(state)
            local_vars = {"state": exec_state, "result": None}
            # Execute with restricted builtins
            exec(code, {"__builtins__": safe_builtins}, local_vars)
            code_result = local_vars.get("result")
            
            # Check if state was modified during execution
            if exec_state != state:
                # State was modified, merge changes (but be careful with security)
                for key, value in exec_state.items():
                    if key not in state or state[key] != value:
                        result_updates[key] = value
            
            if code_result is not None:
                if isinstance(code_result, dict): result_updates.update(code_result)
                else: result_updates[f"{node_id}_result"] = code_result
                    
        except Exception as e:
            logger.exception(f"Operator {node_id} failed")
            raise e
    
    # 3. Handle output_key if specified (for backward compatibility)
    output_key = config.get("output_key")
    if output_key and output_key in result_updates:
        # output_key is already handled above
        pass
            
    if not result_updates:
        result_updates = {f"{node_id}_status": "ok"}
        
    return result_updates

def api_call_runner(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    node_id = config.get("id", "api_call")
    url_template = config.get("url")
    if not url_template: raise ValueError("api_call requires 'url'")

    def _validate_outbound_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Only http/https schemes are allowed")
        host = parsed.hostname
        if not host:
            raise ValueError("URL must include hostname")
        # Restrict ports to common web ports to avoid hitting infra/admin ports
        if parsed.port not in (None, 80, 443):
            raise ValueError(f"Port {parsed.port} is not allowed (only 80/443)")
        try:
            infos = socket.getaddrinfo(host, parsed.port or 80, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            raise ValueError("Invalid hostname")
        for info in infos:
            ip_str = info[4][0]
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved or ip_obj.is_multicast:
                raise ValueError(f"Access to internal/private IP is blocked ({ip_str})")
        return None

    url = _render_template(url_template, state)
    method = (_render_template(config.get("method") or "GET", state)).upper()
    headers = _render_template(config.get("headers"), state) or {}
    params = _render_template(config.get("params"), state)
    json_body = _render_template(config.get("json"), state)
    timeout = _render_template(config.get("timeout", 10), state)

    allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    if method not in allowed_methods:
        raise ValueError(f"Method {method} not allowed; allowed: {sorted(allowed_methods)}")

    try:
        _validate_outbound_url(url)
    except ValueError as ve:
        return {f"{node_id}_status": "error", f"{node_id}_error": str(ve)}

    # Clamp timeout to a safe upper bound
    try:
        timeout_val = float(timeout) if timeout is not None else 10.0
    except Exception:
        timeout_val = 10.0
    timeout_val = max(1.0, min(timeout_val, 30.0))

    try:
        import requests
        resp = requests.request(method, url, headers=headers, params=params, json=json_body, timeout=timeout_val, allow_redirects=False)
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        return {f"{node_id}_status": resp.status_code, f"{node_id}_response": body}
    except requests.exceptions.Timeout:
        return {f"{node_id}_status": "error", f"{node_id}_error": f"Request timed out (limit: {timeout_val}s)"}
    except requests.exceptions.ConnectionError:
        return {f"{node_id}_status": "error", f"{node_id}_error": "Connection refused or DNS failure"}
    except Exception as e:
        return {f"{node_id}_status": "error", f"{node_id}_error": str(e)}

def db_query_runner(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a database query.
    
    SECURITY NOTE: This runner is disabled in production environments.
    Only allowed when ALLOW_DB_QUERY=true environment variable is set.
    """
    node_id = config.get("id", "db_query")
    
    # Security: Block db_query in production unless explicitly allowed
    allow_db_query = os.getenv("ALLOW_DB_QUERY", "false").lower() in ("true", "1", "yes")
    is_production = os.getenv("APP_ENV", "").lower() == "production"
    
    if is_production and not allow_db_query:
        logger.warning(f"db_query node {node_id} blocked in production environment")
        return {f"{node_id}_error": "db_query is not allowed in production"}
    
    query = config.get("query")
    conn_str = _render_template(config.get("connection_string"), state)
    if not query or not conn_str: raise ValueError("db_query requires 'query' and 'connection_string'")

    try:
        from src.sqlalchemy import create_engine, text
        engine = create_engine(conn_str)
        with engine.connect() as conn:
            result = conn.execute(text(query), config.get("params", {}))
            fetch = config.get("fetch", "all")
            if fetch == "one":
                row = result.fetchone()
                res = dict(row) if row else None
            elif fetch == "all":
                res = [dict(r) for r in result.fetchall()]
            else:
                res = result.rowcount
            return {f"{node_id}_result": res}
    except Exception as e:
        return {f"{node_id}_error": str(e)}


# -----------------------------------------------------------------------------
# Skill Executor Runner - Execute skills from src.active_skills context
# -----------------------------------------------------------------------------

def skill_executor_runner(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Execute a skill's tool from src.the hydrated active_skills context.
    
    This runner:
    1. Looks up the skill from src.state['active_skills'] by skill_ref
    2. Finds the specified tool within the skill's tool_definitions
    3. Dispatches to the appropriate handler (llm_chat, operator, api_call, etc.)
    4. Logs execution to skill_execution_log
    
    Config schema (SkillExecutorNodeConfig):
        skill_ref: str - Skill ID to reference
        skill_version: Optional[str] - Specific version (ignored if already hydrated)
        tool_call: str - Which tool to invoke from src.the skill
        input_mapping: Dict[str, str] - Template mappings for tool inputs
        output_key: str - State key to store result
        error_handling: str - "fail", "skip", "retry"
    
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
    import time
    from datetime import datetime, timezone
    
    node_id = config.get("id", "skill_executor")
    skill_ref = config.get("skill_ref")
    tool_call = config.get("tool_call")
    input_mapping = config.get("input_mapping", {})
    output_key = config.get("output_key", f"{node_id}_result")
    error_handling = config.get("error_handling", "fail")
    
    # Validate required config
    if not skill_ref:
        raise ValueError(f"skill_executor node '{node_id}' requires 'skill_ref'")
    if not tool_call:
        raise ValueError(f"skill_executor node '{node_id}' requires 'tool_call'")
    
    # Get active skills from src.state
    active_skills = state.get("active_skills", {})
    
    # Look up the skill
    skill = active_skills.get(skill_ref)
    if not skill:
        error_msg = f"Skill '{skill_ref}' not found in active_skills. Available: {list(active_skills.keys())}"
        if error_handling == "skip":
            logger.warning(f"[skill_executor] {error_msg} - Skipping")
            return {output_key: None, f"{node_id}_skipped": True}
        raise ValueError(error_msg)
    
    # Find the tool definition
    tool_definitions = skill.get("tool_definitions", [])
    tool_def = None
    for td in tool_definitions:
        if td.get("name") == tool_call:
            tool_def = td
            break
    
    if not tool_def:
        error_msg = f"Tool '{tool_call}' not found in skill '{skill_ref}'. Available: {[t.get('name') for t in tool_definitions]}"
        if error_handling == "skip":
            logger.warning(f"[skill_executor] {error_msg} - Skipping")
            return {output_key: None, f"{node_id}_skipped": True}
        raise ValueError(error_msg)
    
    # Render input mappings
    rendered_inputs = {}
    for key, template in input_mapping.items():
        rendered_inputs[key] = _render_template(template, state)
    
    # Determine handler type and dispatch
    handler_type = tool_def.get("handler_type", "operator")
    handler_config = tool_def.get("handler_config", {})
    
    # Merge skill system instructions if this is an LLM call
    if handler_type == "llm_chat" and skill.get("system_instructions"):
        handler_config = {**handler_config}
        existing_system = handler_config.get("system_prompt", "")
        skill_instructions = skill.get("system_instructions", "")
        handler_config["system_prompt"] = f"{skill_instructions}\n\n{existing_system}".strip()
    
    # Build execution config
    exec_config = {
        "id": f"{node_id}_{tool_call}",
        **handler_config,
        **rendered_inputs,
        "callbacks": config.get("callbacks", [])
    }
    
    # Track execution time
    start_time = time.time()
    result = {}
    error = None
    
    try:
        # Dispatch to appropriate handler
        handler = NODE_REGISTRY.get(handler_type)
        if not handler:
            raise ValueError(f"Unknown handler_type '{handler_type}' for tool '{tool_call}'")
        
        result = handler(state, exec_config)
        
    except Exception as e:
        error = str(e)
        logger.exception(f"[skill_executor] Tool '{tool_call}' in skill '{skill_ref}' failed")
        
        if error_handling == "skip":
            result = {output_key: None, f"{node_id}_error": error}
        elif error_handling == "retry":
            # For now, just fail on retry - could implement actual retry logic
            raise
        else:  # fail
            raise
    
    execution_time_ms = int((time.time() - start_time) * 1000)
    
    # Build execution log entry
    log_entry = {
        "skill_id": skill_ref,
        "node_id": node_id,
        "tool_name": tool_call,
        "input_params": rendered_inputs,
        "execution_time_ms": execution_time_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if error:
        log_entry["error"] = error
    
    # Prepare output
    output = {output_key: result}
    
    # Append to skill execution log (uses Annotated accumulator)
    current_log = state.get("skill_execution_log", [])
    output["skill_execution_log"] = current_log + [log_entry]
    
    # Update step history
    current_history = state.get("step_history", [])
    output["step_history"] = current_history + [f"{node_id}:skill_executor:{skill_ref}.{tool_call}"]
    
    return output

def for_each_runner(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Executes sub-node for each item in list concurrently."""
    input_list_key = config.get("input_list_key")
    sub_node_config = config.get("sub_node_config")
    output_key = config.get("output_key")
    max_iterations = config.get("max_iterations", 10)
    
    if not all([input_list_key, sub_node_config, output_key]):
        raise ValueError("for_each requires input_list_key, sub_node_config, output_key")

    input_list = _get_nested_value(state, input_list_key, [])
    if len(input_list) > max_iterations:
        logger.warning(f"for_each truncated {len(input_list)} -> {max_iterations}")
        input_list = input_list[:max_iterations]
    
    sub_node_type = sub_node_config.get("type")
    sub_node_func = NODE_REGISTRY.get(sub_node_type)
    if not sub_node_func: raise ValueError(f"Unknown sub-node: {sub_node_type}")

    def worker(item):
        # Create a shallow copy of state to avoid race conditions, but minimize memory overhead
        # Deep copy only the mutable parts that might be modified
        item_state = dict(state)  # Shallow copy
        item_state["item"] = item
        # Deep copy only the messages list if it exists (to avoid reducer conflicts)
        if "messages" in item_state and isinstance(item_state["messages"], list):
            item_state["messages"] = item_state["messages"].copy()
        rendered_sub = _render_template(sub_node_config, item_state)
        return sub_node_func(item_state, rendered_sub)

    # Handle empty list early
    if not input_list:
        return {output_key: []}
    
    # Dynamic worker count based on CPU cores and list size
    cpu_count = os.cpu_count() or 2
    max_workers = min(len(input_list), max(1, cpu_count // 2))  # Conservative: half of CPU cores
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(worker, input_list))
    
    return {output_key: results}

def route_draft_quality(state: Dict[str, Any]) -> str:
    draft = state.get("gemini_draft")
    if not isinstance(draft, dict) or not draft.get("is_complete"):
        return "reviser"
    return "send_email"


def parallel_group_runner(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Executes branches in parallel and merges results."""
    node_id = config.get("id", "parallel_group")
    branches = config.get("config", {}).get("branches", [])
    
    if not branches:
        return {}

    def run_branch(branch):
        branch_id = branch.get("branch_id", "unknown")
        nodes = branch.get("nodes", [])
        
        # Branch execution uses a copy of the state
        branch_state = state.copy()
        branch_updates = {}
        
        for node_def in nodes:
            node_type = node_def.get("type")
            handler = NODE_REGISTRY.get(node_type)
            if not handler:
                logger.error(f"Unknown node type in branch {branch_id}: {node_type}")
                continue
                
            # Execute node
            try:
                # Note: handlers usually return a dict of updates, not the full state
                updates = handler(branch_state, node_def)
                if isinstance(updates, dict):
                    branch_state.update(updates)
                    branch_updates.update(updates)
            except Exception as e:
                logger.error(f"Node execution failed in branch {branch_id}: {e}")
                raise e
                
        return branch_id, branch_updates

    # Execute branches in parallel
    combined_updates = {}
    branch_results = {}  # [Fix] Track branch results explicitly
    
    # Use ThreadPoolExecutor for concurrency
    # Note: Be careful with state conflicts if branches write to same keys
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_branch = {executor.submit(run_branch, b): b for b in branches}
        for future in concurrent.futures.as_completed(future_to_branch):
            branch = future_to_branch[future]
            try:
                branch_id, updates = future.result()
                # [Fix] Store branch results explicitly for verification
                branch_results[branch_id] = updates
                # Flatten results directly into state, also keep namespaced copy for reference
                combined_updates[branch_id] = updates  # Keep namespaced for debugging
                if isinstance(updates, dict):
                    combined_updates.update(updates)  # Also flatten for easy access
            except Exception as e:
                logger.error(f"Branch execution failed: {e}")
                raise e
    
    # [Fix] Add explicit branch execution markers for test verification
    for branch_id in branch_results.keys():
        combined_updates[f"{branch_id}_executed"] = True
                
    return combined_updates


# -----------------------------------------------------------------------------
# 5. Registry & Orchestration
# -----------------------------------------------------------------------------

NODE_REGISTRY: Dict[str, Callable] = {}

def register_node(name: str, func: Callable) -> None:
    NODE_REGISTRY[name] = func

# Register Nodes
register_node("operator", operator_runner)
register_node("operator_custom", operator_runner)  # 사용자 정의 코드/sets 전용 (MOCK_MODE에서만 exec 허용)
# Placeholder for curated/safe official operator integrations (e.g., Gmail/GDrive templates)
def operator_official_runner(state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("operator_official is reserved for curated official integrations (e.g., Gmail/GDrive) and is not yet implemented.")
register_node("operator_official", operator_official_runner)
register_node("llm_chat", llm_chat_runner)
register_node("aiModel", llm_chat_runner)  # aiModel은 llm_chat과 동일하게 처리
register_node("api_call", api_call_runner)
register_node("db_query", db_query_runner)
register_node("for_each", for_each_runner)
register_node("route_draft_quality", route_draft_quality)
register_node("parallel_group", parallel_group_runner)
register_node("aggregator", operator_runner) # Aggregator uses same logic as operator
register_node("skill_executor", skill_executor_runner)  # Skills integration

def _get_mock_config(mock_behavior: str) -> Dict[str, Any]:
    """Returns test configurations for mock behaviors."""
    if mock_behavior == "E2E_S3_LARGE_DATA":
        return {
            "nodes": [{
                "id": "large_data_generator", "type": "operator",
                "config": { "code": "state['res'] = 'X'*300000", "output_key": "res" }
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
    # Add other mock configs as needed (FAIL, PAUSE, etc.)
    return {"nodes": [], "edges": []} # Default empty


def run_workflow(config_json: str | Dict[str, Any], initial_state: Dict[str, Any] | None = None, 
                 user_api_keys: Dict[str, str] | None = None, 
                 use_cache: bool = True, 
                 conversation_id: str | None = None, 
                 ddb_table_name: str | None = None,
                 run_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Main entry point using Dynamic Builder architecture.
    Supports both Real and Mock execution paths.
    """
    initial_state = dict(initial_state) if initial_state else {}
    
    # 1. Check for Mock/Test Request
    mock_behavior = initial_state.get("mock_behavior")
    if mock_behavior:
        logger.info(f"🧪 Mock behavior detected: {mock_behavior}")
        mock_config = _get_mock_config(mock_behavior)
        config_json = json.dumps(mock_config)
        
        # Mock Response Simulation for HITP
        if mock_behavior == "PAUSED_FOR_HITP":
            return {"status": "PAUSED_FOR_HITP", "next_segment_to_run": 1}

    # 2. Config Validation (JSON parse + Pydantic schema)
    if isinstance(config_json, dict):
        raw_config = config_json
    else:
        try:
            raw_config = json.loads(config_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON config: {str(e)}. Please check the config_json format.")
        except Exception as e:
            raise ValueError(f"Failed to parse workflow config: {str(e)}")

    try:
        validated_config = WorkflowConfigModel.model_validate(raw_config)
        # Use validated dict for downstream (keeps types constrained)
        workflow_config = validated_config.model_dump()
    except ValidationError as ve:
        raise ValueError(f"Invalid workflow config: {ve}") from ve

    # 3. Dynamic Build (No S3, No Pickle)
    # Lazy import to avoid circular ref with NODE_REGISTRY
    from src.services.workflow.builder import DynamicWorkflowBuilder
    
    logger.info("🏗️ Building workflow dynamically...")
    builder = DynamicWorkflowBuilder(workflow_config)
    app = builder.build()

    # 4. Apply Checkpointer if needed
    if ddb_table_name:
        try:
            from langgraph_checkpoint_dynamodb import DynamoDBSaver
            saver = DynamoDBSaver(table_name=ddb_table_name)
            app = app.with_checkpointer(saver)
        except ImportError:
            logger.warning("DynamoDBSaver not found, skipping persistence")

    # 5. Execution
    # [수정] run_config가 없으면 빈 딕셔너리로 초기화
    final_config = run_config.copy() if run_config else {}
    
    # configurable이 없으면 생성
    if "configurable" not in final_config:
        final_config["configurable"] = {}
    
    # thread_id 및 conversation_id 보정
    configurable = final_config["configurable"]
    if not configurable.get("thread_id"):
        configurable["thread_id"] = conversation_id or "default_thread"
    if conversation_id and not configurable.get("conversation_id"):
        configurable["conversation_id"] = conversation_id

    # Setup API Keys
    if user_api_keys:
        initial_state.setdefault("user_api_keys", {}).update(user_api_keys)
    initial_state.setdefault("step_history", [])

    # 6. Setup Callbacks (Glass Box)
    from src.langchain_core_custom.callbacks import BaseCallbackHandler
    import uuid
    
    class StateHistoryCallback(BaseCallbackHandler):
        """
        Capture AI thoughts and tool usage with PII masking for Glass-Box UI.
        """
        def __init__(self):
            self.logs = []
            
        def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any) -> None:
            pass
            
        def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any) -> None:
            prompt_safe = mask_pii(prompts[0]) if prompts else ""
            self.logs.append({
                "id": str(uuid.uuid4()),
                "type": "ai_thought",
                "name": serialized.get("name", "LLM Thinking"),
                "node_id": serialized.get("name", "llm_node"),
                "content": "Thinking...",
                "timestamp": int(time.time()),
                "status": "RUNNING",
                "details": {
                    "prompts": [prompt_safe] if prompt_safe else []
                }
            })
            
        def on_llm_end(self, response: Any, **kwargs: Any) -> None:
            if self.logs and self.logs[-1]["type"] == "ai_thought":
                self.logs[-1]["status"] = "COMPLETED"
                
                if getattr(response, "llm_output", None) and "usage" in response.llm_output:
                    self.logs[-1]["usage"] = response.llm_output["usage"]
                
                try:
                    text = response.generations[0][0].text
                    self.logs[-1]["content"] = mask_pii(text)
                except Exception:
                    pass
                    
        def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
            if self.logs and self.logs[-1]["type"] == "ai_thought":
                self.logs[-1]["status"] = "FAILED"
                self.logs[-1]["error"] = {
                    "message": str(error),
                    "type": type(error).__name__
                }

        def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> None:
            self.logs.append({
                "id": str(uuid.uuid4()),
                "type": "tool_usage",
                "name": serialized.get("name", "Tool"),
                "node_id": serialized.get("name", "tool_node"),
                "status": "RUNNING",
                "timestamp": int(time.time()),
                "input": mask_pii(input_str)
            })

        def on_tool_end(self, output: str, **kwargs: Any) -> None:
            if self.logs and self.logs[-1]["type"] == "tool_usage":
                self.logs[-1]["status"] = "COMPLETED"
                self.logs[-1]["output"] = mask_pii(str(output))

        def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
            if self.logs and self.logs[-1]["type"] == "tool_usage":
                self.logs[-1]["status"] = "FAILED"
                self.logs[-1]["error"] = str(error)

    history_callback = StateHistoryCallback()
    final_config.setdefault("callbacks", []).append(history_callback)

    # Run!
    logger.info("🚀 Invoking workflow...")
    try:
        # [수정] config 전체를 넘겨야 metadata 등이 함께 전달됨
        result = app.invoke(initial_state, config=final_config) 
        
        # [NEW] Attach collected logs to the result (if result is a dict)
        if isinstance(result, dict):
            # Legacy field (kept for backward compatibility)
            result["__new_history_logs"] = history_callback.logs
            # Glass-box logs for UI
            prev_logs = result.get("execution_logs", [])
            result["execution_logs"] = prev_logs + history_callback.logs
        return result
    except AsyncLLMRequiredException:
        # Signal orchestrator to pause for async LLM / HITP handling
        return {"status": "PAUSED_FOR_ASYNC_LLM"}
    except Exception as e:
        logger.exception("Workflow execution failed")
        raise e


# -----------------------------------------------------------------------------
# Partition Workflow Functions (for Lambda compatibility)
# -----------------------------------------------------------------------------

def partition_workflow(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    워크플로우를 세그먼트로 분할하는 함수.
    partition_workflow_advanced의 alias로, Lambda 호환성을 위해 유지.
    """
    from src.services.workflow.partition_service import partition_workflow_advanced
    
    # partition_workflow_advanced는 {"partition_map": [...], ...} 형태로 반환하므로
    # partition_map 리스트만 추출
    result = partition_workflow_advanced(config)
    return result.get("partition_map", [])


def _build_segment_config(segment: Dict[str, Any]) -> Dict[str, Any]:
    """
    세그먼트 객체를 실행 가능한 워크플로우 config로 변환.
    
    세그먼트는 {"id": str, "nodes": [...], "edges": [...], "type": str, "node_ids": [...]} 형태.
    이를 run_workflow에 전달할 수 있는 {"nodes": [...], "edges": [...]} 형태로 변환.
    """
    return {
        "nodes": segment.get("nodes", []),
        "edges": segment.get("edges", [])
    }


def run_workflow_from_dynamodb(table_name: str, key_name: str, key_value: str, initial_state: Optional[Dict[str, Any]] = None, user_api_keys: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    DynamoDB에서 워크플로우 config를 가져와서 실행.
    
    Args:
        table_name: DynamoDB 테이블 이름
        key_name: 파티션 키 이름
        key_value: 파티션 키 값
        initial_state: 초기 상태 (옵션)
        user_api_keys: 사용자 API 키 (옵션)
    
    Returns:
        워크플로우 실행 결과
    """
    # DynamoDB에서 config 가져오기 - 리전을 환경변수에서 가져옴
    region = os.environ.get('AWS_REGION', 'ap-northeast-2')
    dynamodb = boto3.resource('dynamodb', region_name=region)
    table = dynamodb.Table(table_name)
    
    response = table.get_item(Key={key_name: key_value})
    
    if 'Item' not in response:
        raise ValueError(f"Workflow config not found in DynamoDB table {table_name} with key {key_name}={key_value}")
    
    item = response['Item']
    config_json = item.get('config_json')
    
    if not config_json:
        raise ValueError(f"No config_json found in DynamoDB item")
    
    # JSON 파싱 (필요한 경우)
    if isinstance(config_json, str):
        config_json = json.loads(config_json)
    
    # 워크플로우 실행
    return run_workflow(config_json, initial_state, user_api_keys)
