"""
ReactExecutor — Bridge-governed ReAct loop using Claude's native tool_use protocol.

Wraps every agent tool call through AnalemmaBridge.segment() for governance,
then injects tool results back into the conversation for the next LLM turn.

Governance model:
  - Every tool call passes through bridge.segment() BEFORE handler execution
  - Parallel tool calls use atomic governance: if ANY tool in a batch receives
    SIGKILL, ALL tools in the batch are aborted (no partial execution)
  - Token budget is checked at 3 points: pre-loop, post-LLM, post-tool

Execution flow:
  [1] Build messages (system + user task)
  [2] Call Claude via AnthropicBedrock with tool definitions
  [3] Post-LLM budget gate — stop before tool execution if budget blown
  [4] Atomic governance: probe bridge.segment() for ALL tools in batch
  [5] Kill check: if ANY SIGKILL → abort entire batch
  [6] Execute approved handlers, return errors for rejected
  [7] Post-tool budget estimate — stop if next LLM call will exceed budget
  [8] Loop until end_turn / max_iterations / budget_exceeded / sigkill

Usage:
    bridge = AnalemmaBridge(workflow_id="agent_001", ring_level=2, mode="optimistic")
    executor = ReactExecutor(bridge=bridge)
    executor.add_tool("read_only", "Echo text", {...schema...}, lambda p: p["text"])
    result = executor.run("Use read_only to echo 'hello'")
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from .python_bridge import AnalemmaBridge, SecurityViolation

logger = logging.getLogger(__name__)


# ─── Canonical Serialization ────────────────────────────────────────────────
# Contract matches src.common.hash_utils._canonical_bytes exactly:
#   sort_keys=True, separators=(',', ':'), ensure_ascii=False, typed _default.
# Defined locally to avoid cross-package import fragility.
# For hashing, use hash_utils.content_hash() which shares the same contract.

def _canonical_json(data: Any) -> str:
    """Canonical JSON string for deterministic, type-safe serialization.

    Matches the contract of common.hash_utils._canonical_bytes:
    - sort_keys=True for deterministic key ordering
    - separators=(',', ':') for compact representation (no whitespace)
    - ensure_ascii=False for UTF-8 preservation
    - Typed default handler for datetime, Decimal, bytes, set, and object types

    Safety:
    - Decimal → str (preserves precision; float causes hash drift)
    - __dict__ → filtered (private keys excluded, to_dict() preferred)
    - Circular references → caught, falls back to repr()
    """

    def _default(obj: Any) -> Any:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            # str preserves exact precision — float(Decimal("0.1") + Decimal("0.2"))
            # yields 0.30000000000000004, causing false hash violations.
            return str(obj)
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return sorted(str(item) for item in obj)
        # Explicit serialization contract takes precedence
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict()
        # __dict__ fallback — filter private/dunder keys to prevent
        # leaking internal state (API keys, credentials, etc.)
        if hasattr(obj, '__dict__'):
            return {
                k: v for k, v in obj.__dict__.items()
                if not k.startswith('_')
            }
        return str(obj)

    try:
        return json.dumps(
            data,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=False,
            default=_default,
        )
    except (ValueError, RecursionError):
        # Circular reference or excessive nesting — fall back to repr.
        # repr() is deterministic for the same object state, preserving
        # output stability without crashing the ReAct loop.
        logger.warning("[_canonical_json] Circular reference detected, using repr fallback")
        return repr(data)


# ─── Result Dataclass ───────────────────────────────────────────────────────

@dataclass
class ReactResult:
    """Final result of a ReAct execution."""
    final_answer: str
    messages: List[Dict[str, Any]]
    iterations: int
    total_input_tokens: int
    total_output_tokens: int
    segments: List[str]
    stop_reason: str  # "end_turn" | "max_iterations" | "budget_exceeded" | "sigkill" | "wall_clock_timeout" | "max_rejections"


# ─── Tool Registration ──────────────────────────────────────────────────────

@dataclass
class _RegisteredTool:
    """Internal representation of a registered tool."""
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: Callable[[Dict[str, Any]], Any]
    bridge_action: str  # Maps to CAPABILITY_MAP action name


# ─── Constants ───────────────────────────────────────────────────────────────

_LLM_MAX_RETRIES = 3
_LLM_BASE_DELAY = 1.0  # seconds
_MAX_CONSECUTIVE_REJECTIONS = 3
_TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4  # Conservative estimate for budget projection


# ─── ReactExecutor ───────────────────────────────────────────────────────────

class ReactExecutor:
    """
    Bridge-governed ReAct loop executor with atomic governance.

    Composes with AnalemmaBridge — does NOT subclass it. The executor owns
    the LLM call loop; every tool invocation is delegated through bridge.segment().

    Governance model:
      - Every tool call passes through bridge.segment() BEFORE handler execution
      - Parallel tool calls use atomic governance: if ANY tool in a batch receives
        SIGKILL, ALL tools in the batch are aborted (no partial execution)
      - Budget is checked at 3 points: pre-loop, post-LLM, post-tool

    Args:
        bridge:              AnalemmaBridge instance (externally owned lifecycle).
        model_id:            Bedrock model ID for Claude.
        max_iterations:      Safety limit on loop iterations.
        max_tokens_per_turn: Max tokens per LLM response.
        temperature:         LLM temperature.
        token_budget:        Total token budget (aligns with VSM BudgetWatchdog 500K).
        system_prompt:       Optional system prompt for the agent.
        token_counter:       Optional callable(str) -> int for accurate token estimation.
                             Defaults to len(text) // 4. Plug in tiktoken or Anthropic's
                             token counter for production-grade budget precision.
        wall_clock_timeout:  Hard wall-clock limit in seconds. None = no limit.
                             For Lambda (300s timeout), use 240s to reserve 60s for
                             seal_state_bag + S3 offload + response serialization.
        tool_timeout:        Per-tool execution timeout in seconds. Prevents a single
                             slow tool handler from consuming the entire budget.
    """

    def __init__(
        self,
        bridge: AnalemmaBridge,
        *,
        model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0",
        max_iterations: int = 25,
        max_tokens_per_turn: int = 4096,
        temperature: float = 0.0,
        token_budget: int = 500_000,
        system_prompt: Optional[str] = None,
        token_counter: Optional[Callable[[str], int]] = None,
        wall_clock_timeout: Optional[float] = None,
        tool_timeout: float = 30.0,
    ):
        self._bridge = bridge
        self._model_id = model_id
        self._max_iterations = max_iterations
        self._max_tokens_per_turn = max_tokens_per_turn
        self._temperature = temperature
        self._token_budget = token_budget
        self._system_prompt = system_prompt or "You are a helpful assistant."
        self._tools: Dict[str, _RegisteredTool] = {}
        self._client = None  # Lazy-initialized
        self._token_counter = token_counter or self._default_token_estimate
        self._wall_clock_timeout = wall_clock_timeout
        self._tool_timeout = tool_timeout
        self._start_time: Optional[float] = None

    @staticmethod
    def _default_token_estimate(text: str) -> int:
        """Rough token estimate: ~4 chars per token. Override via token_counter."""
        return len(text) // _TOKEN_ESTIMATE_CHARS_PER_TOKEN

    def _check_wall_clock(self) -> bool:
        """Return True if wall-clock budget is exceeded."""
        if self._wall_clock_timeout is None or self._start_time is None:
            return False
        elapsed = time.time() - self._start_time
        if elapsed >= self._wall_clock_timeout:
            logger.warning(
                "[ReactExecutor] Wall-clock timeout: %.1fs >= %.1fs",
                elapsed, self._wall_clock_timeout,
            )
            return True
        return False

    def _remaining_wall_clock(self) -> Optional[float]:
        """Return remaining wall-clock seconds, or None if no limit."""
        if self._wall_clock_timeout is None or self._start_time is None:
            return None
        return max(0.0, self._wall_clock_timeout - (time.time() - self._start_time))

    # ── Public API ────────────────────────────────────────────────────────────

    def add_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        handler: Callable[[Dict[str, Any]], Any],
        *,
        bridge_action: Optional[str] = None,
    ) -> None:
        """
        Register a tool for the ReAct loop.

        Args:
            name:          Tool name (must match what Claude will call).
            description:   Human-readable description for Claude.
            input_schema:  JSON Schema for tool input parameters.
            handler:       Callable that executes the tool. Receives params dict, returns result.
            bridge_action: Maps to CAPABILITY_MAP action name. Defaults to ``name``.
        """
        schema = input_schema
        if schema.get("type") != "object":
            schema = {
                "type": "object",
                "properties": schema.get("properties", schema),
                "required": schema.get("required", []),
            }

        self._tools[name] = _RegisteredTool(
            name=name,
            description=description,
            input_schema=schema,
            handler=handler,
            bridge_action=bridge_action or name,
        )

    def get_anthropic_tools(self) -> List[Dict[str, Any]]:
        """Return tool definitions in Anthropic Messages API format."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def run(self, task: str) -> ReactResult:
        """
        Execute the full ReAct loop.

        Args:
            task: The user's task description.

        Returns:
            ReactResult with final answer, conversation history, and metadata.
        """
        messages: List[Dict[str, Any]] = [{"role": "user", "content": task}]
        tools = self.get_anthropic_tools()
        segments: List[str] = []

        total_input_tokens = 0
        total_output_tokens = 0
        rejection_counter: Dict[str, int] = {}
        self._start_time = time.time()

        for iteration in range(self._max_iterations):
            # ── Budget gate 1/3: pre-loop ──
            total_tokens_used = total_input_tokens + total_output_tokens
            if total_tokens_used >= self._token_budget:
                logger.warning(
                    "[ReactExecutor] Budget exceeded (pre-loop): %d >= %d",
                    total_tokens_used, self._token_budget,
                )
                return self._budget_exceeded_result(
                    messages, iteration,
                    total_input_tokens, total_output_tokens, segments,
                )

            # ── Wall-clock gate: pre-LLM ──
            if self._check_wall_clock():
                return ReactResult(
                    final_answer="",
                    messages=messages,
                    iterations=iteration,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    segments=segments,
                    stop_reason="wall_clock_timeout",
                )

            # ── LLM call ──
            response = self._call_llm(messages, tools)

            # Accumulate usage
            usage = response.usage
            total_input_tokens += usage.input_tokens
            total_output_tokens += usage.output_tokens

            # ── Budget gate 2/3: post-LLM ──
            total_tokens_used = total_input_tokens + total_output_tokens
            if total_tokens_used >= self._token_budget:
                logger.warning(
                    "[ReactExecutor] Budget exceeded (post-LLM): %d >= %d",
                    total_tokens_used, self._token_budget,
                )
                # If LLM returned a final answer, honor it despite budget
                if response.stop_reason == "end_turn":
                    final_text = self._extract_text(response)
                    self._send_final_segment(final_text, segments)
                    return ReactResult(
                        final_answer=final_text,
                        messages=messages,
                        iterations=iteration + 1,
                        total_input_tokens=total_input_tokens,
                        total_output_tokens=total_output_tokens,
                        segments=segments,
                        stop_reason="end_turn",
                    )
                return self._budget_exceeded_result(
                    messages, iteration + 1,
                    total_input_tokens, total_output_tokens, segments,
                )

            # ── Final answer (no tool_use) ──
            has_tool_use = any(
                block.type == "tool_use" for block in response.content
            )

            if not has_tool_use and response.stop_reason == "end_turn":
                final_text = self._extract_text(response)
                self._send_final_segment(final_text, segments)
                return ReactResult(
                    final_answer=final_text,
                    messages=messages,
                    iterations=iteration + 1,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    segments=segments,
                    stop_reason="end_turn",
                )

            # ── Build assistant message for history ──
            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            messages.append({"role": "assistant", "content": assistant_content})

            # ── Extract thought for bridge context ──
            thought = self._extract_text(response) or f"[Iteration {iteration + 1}]"

            # ── Atomic governance: process tool batch ──
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            tool_results, batch_segments, was_killed = self._process_tool_batch(
                tool_use_blocks=tool_use_blocks,
                thought=thought,
                iteration=iteration,
                total_tokens=total_input_tokens + total_output_tokens,
                rejection_counter=rejection_counter,
            )
            segments.extend(batch_segments)
            messages.append({"role": "user", "content": tool_results})

            if was_killed:
                return ReactResult(
                    final_answer="",
                    messages=messages,
                    iterations=iteration + 1,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    segments=segments,
                    stop_reason="sigkill",
                )

            # ── Wall-clock gate: post-tool ──
            if self._check_wall_clock():
                return ReactResult(
                    final_answer="",
                    messages=messages,
                    iterations=iteration + 1,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    segments=segments,
                    stop_reason="wall_clock_timeout",
                )

            # ── Budget gate 3/3: post-tool estimate ──
            tool_result_text = "".join(
                str(r.get("content", "")) for r in tool_results
            )
            estimated_next_input = self._token_counter(tool_result_text)
            if total_tokens_used + estimated_next_input >= self._token_budget:
                logger.warning(
                    "[ReactExecutor] Budget likely exceeded (post-tool estimate): "
                    "%d actual + ~%d projected >= %d",
                    total_tokens_used, estimated_next_input, self._token_budget,
                )
                return self._budget_exceeded_result(
                    messages, iteration + 1,
                    total_input_tokens, total_output_tokens, segments,
                )

            # ── Consecutive rejection limit ──
            for tool_name, count in rejection_counter.items():
                if count >= _MAX_CONSECUTIVE_REJECTIONS:
                    logger.warning(
                        "[ReactExecutor] Tool '%s' rejected %d times consecutively.",
                        tool_name, count,
                    )
                    return ReactResult(
                        final_answer="",
                        messages=messages,
                        iterations=iteration + 1,
                        total_input_tokens=total_input_tokens,
                        total_output_tokens=total_output_tokens,
                        segments=segments,
                        stop_reason="max_rejections",
                    )

        # Loop exhausted
        logger.warning(
            "[ReactExecutor] Max iterations (%d) reached.", self._max_iterations,
        )
        return ReactResult(
            final_answer="",
            messages=messages,
            iterations=self._max_iterations,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            segments=segments,
            stop_reason="max_iterations",
        )

    # ── Private: LLM Calling ─────────────────────────────────────────────────

    def _get_client(self):
        """Lazy-initialize the Anthropic client."""
        if self._client is None:
            from anthropic import AnthropicBedrock
            self._client = AnthropicBedrock(
                aws_region=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")),
            )
        return self._client

    def _call_llm(self, messages: List[Dict], tools: List[Dict]):
        """
        Call Claude via AnthropicBedrock with retry logic.

        Timeout is capped by remaining wall-clock budget to prevent a single
        slow Bedrock response from consuming the entire Lambda timeout.

        Returns the raw anthropic Message response object.
        """
        client = self._get_client()
        last_error = None

        # Cap LLM timeout to remaining wall-clock (min 10s, max 120s)
        remaining = self._remaining_wall_clock()
        if remaining is not None:
            llm_timeout = min(max(remaining - 5.0, 10.0), 120.0)
        else:
            llm_timeout = 120.0

        for attempt in range(_LLM_MAX_RETRIES):
            try:
                kwargs: Dict[str, Any] = {
                    "model": self._model_id,
                    "max_tokens": self._max_tokens_per_turn,
                    "system": self._system_prompt,
                    "messages": messages,
                    "timeout": llm_timeout,
                }
                if self._temperature > 0:
                    kwargs["temperature"] = self._temperature
                if tools:
                    kwargs["tools"] = tools

                return client.messages.create(**kwargs)

            except Exception as e:
                last_error = e
                error_str = str(e).lower()
                is_retryable = any(
                    kw in error_str
                    for kw in ("throttl", "timeout", "too many", "rate limit", "overloaded")
                )
                if is_retryable and attempt < _LLM_MAX_RETRIES - 1:
                    delay = _LLM_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "[ReactExecutor] LLM call retry %d/%d after %.1fs: %s",
                        attempt + 1, _LLM_MAX_RETRIES, delay, e,
                    )
                    time.sleep(delay)
                else:
                    raise

        raise last_error  # Should not reach here, but safety net

    # ── Private: Atomic Governance ───────────────────────────────────────────

    def _process_tool_batch(
        self,
        tool_use_blocks: List,
        thought: str,
        iteration: int,
        total_tokens: int,
        rejection_counter: Dict[str, int],
    ) -> tuple:
        """
        Process a batch of parallel tool calls with atomic governance.

        Phase 1 — Governance: probe bridge.segment() for ALL tools before
                  executing any handler. Each probe opens a segment, reads the
                  governance decision, and closes it.
        Phase 2 — Kill check: if ANY tool received SIGKILL, abort the entire
                  batch. ALL tools get an ABORTED error result (no partial exec).
        Phase 3 — Execution: run approved handlers via _execute_tool_handler.
                  Rejected tools get an error result with recovery instruction.

        Returns:
            (tool_results, segment_ids, was_killed)
        """
        # Phase 1: Governance decisions
        decisions = []
        for block in tool_use_blocks:
            decision = self._check_tool_governance(
                tool_name=block.name,
                tool_input=block.input,
                tool_use_id=block.id,
                thought=thought,
                iteration=iteration,
                total_tokens=total_tokens,
            )
            decisions.append(decision)

        all_segments = [
            d["checkpoint_id"] for d in decisions if d.get("checkpoint_id")
        ]

        # Phase 2: Atomic kill check — one SIGKILL aborts all
        kill_decision = next(
            (d for d in decisions if d.get("was_killed")), None
        )
        if kill_decision:
            logger.error(
                "[ReactExecutor] SIGKILL in batch — aborting all %d tools. reason=%s",
                len(tool_use_blocks), kill_decision.get("reason"),
            )
            tool_results = []
            for block in tool_use_blocks:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "is_error": True,
                    "content": (
                        f"ABORTED: Atomic governance violation in batch — "
                        f"{kill_decision.get('reason', 'Terminated by kernel')}"
                    ),
                })
            return tool_results, all_segments, True

        # Phase 3: Execute approved, error for rejected
        tool_results = []
        for block, decision in zip(tool_use_blocks, decisions):
            if decision.get("should_rollback"):
                rejection_counter[block.name] = (
                    rejection_counter.get(block.name, 0) + 1
                )
                logger.warning(
                    "[ReactExecutor] SOFT_ROLLBACK: tool=%s (%d consecutive). reason=%s",
                    block.name,
                    rejection_counter[block.name],
                    decision.get("reason"),
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "is_error": True,
                    "content": (
                        f"REJECTED by governance kernel: "
                        f"{decision.get('reason', 'Action not allowed')}"
                    ),
                })
            elif decision.get("allowed"):
                rejection_counter.pop(block.name, None)
                action_params = decision.get("action_params", block.input)
                tool_result = self._execute_tool_handler(
                    tool_name=block.name,
                    tool_input=action_params,
                    tool_use_id=block.id,
                )
                tool_results.append(tool_result)
            else:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "is_error": True,
                    "content": decision.get(
                        "reason",
                        f"Unexpected governance state for '{block.name}'",
                    ),
                })

        return tool_results, all_segments, False

    def _check_tool_governance(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: str,
        thought: str,
        iteration: int,
        total_tokens: int,
    ) -> Dict[str, Any]:
        """
        Probe governance for a single tool call via bridge.segment().

        Opens a segment, reads the governance decision, reports a governance
        probe observation, and closes the segment. No handler execution occurs.

        Returns:
            Dict with keys: allowed, was_killed, should_rollback,
            checkpoint_id, reason, action_params
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return {
                "allowed": False,
                "was_killed": False,
                "should_rollback": False,
                "checkpoint_id": None,
                "reason": (
                    f"Unknown tool: '{tool_name}'. "
                    f"Available: {list(self._tools.keys())}"
                ),
            }

        state_snapshot = {
            "token_usage_total": total_tokens,
            "react_iteration": iteration,
        }

        try:
            with self._bridge.segment(
                thought=thought,
                action=tool.bridge_action,
                params=tool_input,
                segment_type="TOOL_CALL",
                state_snapshot=state_snapshot,
            ) as seg:
                checkpoint_id = seg.checkpoint_id

                if seg.should_kill:
                    return {
                        "allowed": False,
                        "was_killed": True,
                        "should_rollback": False,
                        "checkpoint_id": checkpoint_id,
                        "reason": seg.recovery_instruction or "Terminated by kernel",
                        "action_params": getattr(seg, "action_params", tool_input),
                    }

                if seg.should_rollback:
                    return {
                        "allowed": False,
                        "was_killed": False,
                        "should_rollback": True,
                        "checkpoint_id": checkpoint_id,
                        "reason": seg.recovery_instruction or "Action not allowed",
                        "action_params": getattr(seg, "action_params", tool_input),
                    }

                if seg.allowed:
                    seg.report_observation({"status": "governance_approved"})
                    return {
                        "allowed": True,
                        "was_killed": False,
                        "should_rollback": False,
                        "checkpoint_id": checkpoint_id,
                        "reason": None,
                        "action_params": getattr(seg, "action_params", tool_input),
                    }

                # Fallback: unexpected status
                return {
                    "allowed": False,
                    "was_killed": False,
                    "should_rollback": False,
                    "checkpoint_id": checkpoint_id,
                    "reason": f"Unexpected bridge status for '{tool_name}'",
                }

        except SecurityViolation as sv:
            logger.warning(
                "[ReactExecutor] SecurityViolation: tool=%s error=%s",
                tool_name, sv,
            )
            return {
                "allowed": False,
                "was_killed": False,
                "should_rollback": True,
                "checkpoint_id": None,
                "reason": f"Security violation: {sv}",
            }

        except (ConnectionError, TimeoutError, OSError) as net_err:
            # Bridge unreachable — fail-safe: deny execution.
            # Treat as rollback so the LLM can retry or choose a different path.
            logger.error(
                "[ReactExecutor] Bridge connection failure: tool=%s error=%s",
                tool_name, net_err,
            )
            return {
                "allowed": False,
                "was_killed": False,
                "should_rollback": True,
                "checkpoint_id": None,
                "reason": f"Bridge connection failure: {net_err}",
            }

        except Exception as unexpected_err:
            # Catch-all for unexpected bridge errors — prevents kernel crash.
            # Fail-safe: deny execution.
            logger.error(
                "[ReactExecutor] Unexpected bridge error: tool=%s error=%s",
                tool_name, unexpected_err,
            )
            return {
                "allowed": False,
                "was_killed": False,
                "should_rollback": True,
                "checkpoint_id": None,
                "reason": f"Bridge error: {unexpected_err}",
            }

    def _execute_tool_handler(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_use_id: str,
    ) -> Dict[str, Any]:
        """
        Execute a tool handler after governance approval.

        Pre-validates required fields from input_schema before calling the handler.
        Serializes the result using canonical JSON for deterministic output.

        Returns:
            tool_result content block for the Messages API.
        """
        tool = self._tools[tool_name]

        # Schema pre-validation — catch LLM hallucination (missing required fields)
        schema_error = self._validate_tool_input(tool, tool_input)
        if schema_error:
            logger.warning(
                "[ReactExecutor] Schema violation: tool=%s error=%s",
                tool_name, schema_error,
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "is_error": True,
                "content": (
                    f"Schema violation for '{tool_name}': {schema_error}. "
                    f"Expected schema: {tool.input_schema}"
                ),
            }

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(tool.handler, tool_input)
                result = future.result(timeout=self._tool_timeout)

            # Canonical serialization — typed, deterministic, no silent str() fallback
            if isinstance(result, str):
                result_str = result
            else:
                result_str = _canonical_json(result)

            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result_str,
            }

        except concurrent.futures.TimeoutError:
            logger.warning(
                "[ReactExecutor] Tool timeout: tool=%s after %.1fs",
                tool_name, self._tool_timeout,
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "is_error": True,
                "content": f"Tool '{tool_name}' timed out after {self._tool_timeout}s",
            }

        except Exception as exec_err:
            logger.warning(
                "[ReactExecutor] Tool execution error: tool=%s error=%s",
                tool_name, exec_err,
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "is_error": True,
                "content": f"Tool execution failed: {exec_err}",
            }

    # ── Private: Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _validate_tool_input(
        tool: _RegisteredTool, tool_input: Dict[str, Any],
    ) -> Optional[str]:
        """Validate tool input against schema. Returns error message or None.

        Checks:
          1. Required fields are present.
          2. Tool input is a dict (not a string or other type from LLM hallucination).
        """
        if not isinstance(tool_input, dict):
            return f"Expected dict input, got {type(tool_input).__name__}"

        required = tool.input_schema.get("required", [])
        missing = [f for f in required if f not in tool_input]
        if missing:
            return f"Missing required fields: {missing}"

        # Type check for declared properties (lightweight — no full JSON Schema)
        properties = tool.input_schema.get("properties", {})
        type_map = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
        for field_name, field_schema in properties.items():
            if field_name not in tool_input:
                continue
            expected_type = type_map.get(field_schema.get("type"))
            if expected_type and not isinstance(tool_input[field_name], expected_type):
                return (
                    f"Field '{field_name}' expected {field_schema['type']}, "
                    f"got {type(tool_input[field_name]).__name__}"
                )

        return None

    @staticmethod
    def _extract_text(response) -> str:
        """Extract concatenated text from response content blocks."""
        return "".join(
            block.text for block in response.content
            if block.type == "text"
        )

    def _budget_exceeded_result(
        self,
        messages: List[Dict],
        iterations: int,
        total_input_tokens: int,
        total_output_tokens: int,
        segments: List[str],
    ) -> ReactResult:
        """Build a ReactResult for budget exceeded."""
        return ReactResult(
            final_answer="",
            messages=messages,
            iterations=iterations,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            segments=segments,
            stop_reason="budget_exceeded",
        )

    def _send_final_segment(self, final_answer: str, segments: List[str]) -> None:
        """Send a FINAL segment through the bridge to signal completion."""
        try:
            with self._bridge.segment(
                thought=f"Task complete. Final answer: {final_answer[:200]}",
                action="read_only",
                params={"final_answer_length": len(final_answer)},
                segment_type="FINAL",
            ) as seg:
                if seg.allowed:
                    seg.report_observation({"status": "completed"})
                segments.append(seg.checkpoint_id)
        except Exception as e:
            # Non-critical — FINAL segment failure doesn't affect the result
            logger.debug("[ReactExecutor] FINAL segment failed (non-critical): %s", e)
