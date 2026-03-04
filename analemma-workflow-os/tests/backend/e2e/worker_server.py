"""
E2E Worker Server — Local FastAPI worker for Hybrid Tunnel Testing

Receives forwarded SFN tasks via tunnel, runs ReactExecutor with real VSM
governance, and returns results to SFN via SendTaskSuccess.

Runs at: localhost:9876

Features:
    - DynamoDB-based idempotency (prevents duplicate task execution)
    - Real AnalemmaBridge -> real VirtualSegmentManager on :8765
    - Hard cap on max_iterations (50) to prevent infinite loops
    - Execution trace returned for test assertions
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Kernel protocol
from src.common.kernel_protocol import open_state_bag, seal_state_bag

# Bridge + ReactExecutor
from src.bridge.python_bridge import AnalemmaBridge
from src.bridge.react_executor import ReactExecutor

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Configuration ────────────────────────────────────────────────────────────

VSM_ENDPOINT = os.environ.get("ANALEMMA_KERNEL_ENDPOINT", "http://localhost:8765")
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
IDEMPOTENCY_TABLE = os.environ.get("E2E_IDEMPOTENCY_TABLE", "E2EWorkerIdempotency")
MAX_ITERATIONS_HARD_CAP = 50

app = FastAPI(title="Analemma E2E Worker", version="1.0.0")

# ── Pydantic Models ──────────────────────────────────────────────────────────


class TaskExecuteRequest(BaseModel):
    task_token: str
    execution_id: str
    owner_id: str = "e2e_test_owner"
    payload: Dict[str, Any] = Field(default_factory=dict)
    bag: Dict[str, Any] = Field(default_factory=dict)
    max_iterations: int = Field(default=10, ge=1, le=MAX_ITERATIONS_HARD_CAP)
    segment_to_run: Optional[int] = None
    total_segments: Optional[int] = None
    workflow_config: Optional[Dict[str, Any]] = None


class TaskExecuteResponse(BaseModel):
    status: str
    execution_id: str
    iterations: int = 0
    stop_reason: str = ""
    final_answer: str = ""
    segments: list = Field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    idempotency_status: str = "NEW"


# ── Idempotency Layer ────────────────────────────────────────────────────────

_dynamodb = None


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    return _dynamodb


def _idempotency_key(task_token: str) -> str:
    """Compute idempotency key from task_token hash."""
    return hashlib.sha256(task_token.encode("utf-8")).hexdigest()[:32]


def _check_idempotency(idem_key: str) -> Optional[Dict[str, Any]]:
    """
    Check DynamoDB for prior execution.

    Returns:
        None if new task, cached result dict if already completed,
        raises HTTPException(409) if in-progress.
    """
    try:
        table = _get_dynamodb().Table(IDEMPOTENCY_TABLE)
        resp = table.get_item(Key={"idempotency_key": idem_key})
        item = resp.get("Item")

        if item is None:
            return None

        status = item.get("status", "UNKNOWN")
        if status == "COMPLETED":
            logger.info("[Idempotency] Cache hit: key=%s, returning cached result", idem_key)
            return item.get("cached_result", {})
        elif status == "PENDING":
            logger.warning("[Idempotency] Task in-progress: key=%s", idem_key)
            raise HTTPException(
                status_code=409,
                detail=f"Task already in-progress: idempotency_key={idem_key}",
            )
        return None  # FAILED or unknown status -> allow retry

    except HTTPException:
        raise
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code")
        if code == "ResourceNotFoundException":
            logger.warning("[Idempotency] Table %s not found, skipping check", IDEMPOTENCY_TABLE)
            return None
        raise


def _write_idempotency(idem_key: str, status: str, cached_result: Optional[Dict] = None) -> None:
    """Write idempotency record to DynamoDB."""
    try:
        table = _get_dynamodb().Table(IDEMPOTENCY_TABLE)
        item = {
            "idempotency_key": idem_key,
            "status": status,
            "updated_at": int(time.time()),
            "ttl": int(time.time()) + 3600,  # 1 hour TTL
        }
        if cached_result:
            # Trim large fields to fit DynamoDB 400KB item limit
            item["cached_result"] = {
                "status": cached_result.get("status", ""),
                "stop_reason": cached_result.get("stop_reason", ""),
                "iterations": cached_result.get("iterations", 0),
                "execution_id": cached_result.get("execution_id", ""),
            }
        table.put_item(Item=item)
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code")
        if code == "ResourceNotFoundException":
            logger.warning("[Idempotency] Table %s not found, skipping write", IDEMPOTENCY_TABLE)
        else:
            logger.error("[Idempotency] Failed to write: %s", e)


# ── SFN Callback ─────────────────────────────────────────────────────────────

_sfn_client = None


def _get_sfn_client():
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions", region_name=AWS_REGION)
    return _sfn_client


def _send_task_success(task_token: str, output: Dict[str, Any]) -> None:
    """Call SFN SendTaskSuccess with sealed state bag."""
    client = _get_sfn_client()
    output_json = json.dumps(output, ensure_ascii=False, default=str)

    # SFN output limit is 256KB
    if len(output_json.encode("utf-8")) > 256_000:
        logger.warning(
            "[Worker] Output exceeds 256KB (%d bytes). seal_state_bag should have S3-offloaded.",
            len(output_json.encode("utf-8")),
        )

    client.send_task_success(taskToken=task_token, output=output_json)
    logger.info("[Worker] SendTaskSuccess completed for token=%s...", task_token[:20])


def _send_task_failure(task_token: str, error: str, cause: str) -> None:
    """Call SFN SendTaskFailure."""
    client = _get_sfn_client()
    client.send_task_failure(
        taskToken=task_token,
        error=error[:256],
        cause=cause[:32768],
    )
    logger.info("[Worker] SendTaskFailure: error=%s", error[:100])


# ── Default Tool for E2E ─────────────────────────────────────────────────────

def _echo_handler(params: Dict[str, Any]) -> str:
    """Default E2E tool: echoes input text."""
    return params.get("text", "echo: no text provided")


def _compute_handler(params: Dict[str, Any]) -> Dict[str, Any]:
    """Default E2E tool: simple arithmetic."""
    a = params.get("a", 0)
    b = params.get("b", 0)
    op = params.get("operation", "add")
    ops = {"add": a + b, "subtract": a - b, "multiply": a * b}
    return {"result": ops.get(op, a + b), "operation": op}


# ── Main Endpoint ────────────────────────────────────────────────────────────

@app.post("/task/execute", response_model=TaskExecuteResponse)
async def execute_task(request: TaskExecuteRequest):
    """
    Execute a segment task with ReactExecutor under full VSM governance.

    Idempotency: checks DynamoDB before processing, records completion after.
    """
    idem_key = _idempotency_key(request.task_token)

    # Step 0: Idempotency check
    cached = _check_idempotency(idem_key)
    if cached is not None:
        return TaskExecuteResponse(
            status="CACHED",
            execution_id=request.execution_id,
            iterations=cached.get("iterations", 0),
            stop_reason=cached.get("stop_reason", "cached"),
            idempotency_status="CACHED",
        )

    # Mark as PENDING
    _write_idempotency(idem_key, "PENDING")

    # Enforce hard cap on iterations
    max_iter = min(request.max_iterations, MAX_ITERATIONS_HARD_CAP)

    try:
        # Step 1: Extract state from bag
        bag = request.bag or open_state_bag(request.payload)
        task_prompt = bag.get("task_prompt", bag.get("prompt", "Execute the workflow segment."))

        # Step 2: Initialize AnalemmaBridge with real VSM
        bridge = AnalemmaBridge(
            workflow_id=f"e2e_{request.execution_id}",
            ring_level=2,  # SERVICE level for E2E
            kernel_endpoint=VSM_ENDPOINT,
            mode="strict",
        )

        # Step 3: Initialize ReactExecutor
        executor = ReactExecutor(
            bridge=bridge,
            max_iterations=max_iter,
            token_budget=500_000,
        )

        # Step 4: Register default E2E tools
        executor.add_tool(
            name="read_only",
            description="Echo text back (read-only test tool)",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Text to echo"}},
                "required": ["text"],
            },
            handler=_echo_handler,
            bridge_action="read_only",
        )
        executor.add_tool(
            name="basic_query",
            description="Perform simple arithmetic (a op b)",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                    "operation": {"type": "string", "enum": ["add", "subtract", "multiply"]},
                },
                "required": ["a", "b"],
            },
            handler=_compute_handler,
            bridge_action="basic_query",
        )

        # Register additional tools from workflow_config if provided
        if request.workflow_config and "tools" in request.workflow_config:
            for tool_def in request.workflow_config["tools"]:
                executor.add_tool(
                    name=tool_def["name"],
                    description=tool_def.get("description", ""),
                    input_schema=tool_def.get("input_schema", {"type": "object", "properties": {}}),
                    handler=lambda p, desc=tool_def.get("description", ""): f"[E2E stub] {desc}: {p}",
                    bridge_action=tool_def.get("bridge_action", tool_def["name"]),
                )

        # Step 5: Execute ReAct loop
        logger.info(
            "[Worker] Starting ReactExecutor: execution_id=%s, max_iter=%d, task=%s",
            request.execution_id, max_iter, task_prompt[:100],
        )
        result = executor.run(task_prompt)

        # Step 6: Seal state bag
        result_delta = {
            "status": "COMPLETE" if result.stop_reason == "end_turn" else result.stop_reason.upper(),
            "_status": "COMPLETE" if result.stop_reason == "end_turn" else result.stop_reason.upper(),
            "react_result": {
                "final_answer": result.final_answer,
                "iterations": result.iterations,
                "stop_reason": result.stop_reason,
                "segments": result.segments,
                "total_input_tokens": result.total_input_tokens,
                "total_output_tokens": result.total_output_tokens,
            },
            "llm_raw_output": result.final_answer,
            "total_tokens": result.total_input_tokens + result.total_output_tokens,
        }

        sealed = seal_state_bag(
            base_state=bag,
            result_delta=result_delta,
            action="sync",
            context={"segment_type": "E2E_REACT"},
        )

        # Step 7: SendTaskSuccess to SFN
        _send_task_success(request.task_token, sealed)

        # Step 8: Mark completed in idempotency table
        response_data = {
            "status": "SUCCESS",
            "execution_id": request.execution_id,
            "iterations": result.iterations,
            "stop_reason": result.stop_reason,
        }
        _write_idempotency(idem_key, "COMPLETED", cached_result=response_data)

        return TaskExecuteResponse(
            status="SUCCESS",
            execution_id=request.execution_id,
            iterations=result.iterations,
            stop_reason=result.stop_reason,
            final_answer=result.final_answer[:10000],  # Truncate for response
            segments=result.segments,
            total_input_tokens=result.total_input_tokens,
            total_output_tokens=result.total_output_tokens,
            idempotency_status="NEW",
        )

    except Exception as e:
        logger.exception("[Worker] Task execution failed: %s", e)
        _write_idempotency(idem_key, "FAILED")

        # Notify SFN of failure
        try:
            _send_task_failure(
                request.task_token,
                error=type(e).__name__,
                cause=str(e),
            )
        except Exception as sfn_err:
            logger.error("[Worker] Failed to send task failure to SFN: %s", sfn_err)

        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    """Health check endpoint."""
    vsm_connected = False
    try:
        import requests as req
        resp = req.get(f"{VSM_ENDPOINT}/v1/health", timeout=3)
        vsm_connected = resp.status_code == 200
    except Exception:
        pass

    return {
        "status": "ready",
        "vsm_endpoint": VSM_ENDPOINT,
        "vsm_connected": vsm_connected,
        "max_iterations_cap": MAX_ITERATIONS_HARD_CAP,
        "idempotency_table": IDEMPOTENCY_TABLE,
    }
