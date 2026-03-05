# -*- coding: utf-8 -*-
"""
Background Distiller Lambda — EventBridge-triggered content distillation.

Triggered by ``BackgroundDistillationRequested`` events published by
KernelMiddlewareInterceptor when quality is acceptable but low-entropy
segments could be improved asynchronously.

Flow:
  1. EventBridge delivers ``BackgroundDistillationRequested`` event
  2. This handler calls ``_distill_segments()`` using Gemini Flash
  3. Distilled text stored to DynamoDB
  4. ``DistillationCompleted`` event emitted for websocket notification

Kernel Level: RING_1_QUALITY (background worker)
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ── Environment ──────────────────────────────────────────────────────────────

WORKFLOW_EVENT_BUS_NAME = os.environ.get("WORKFLOW_EVENT_BUS_NAME", "default")
DISTILLED_RESULTS_TABLE = os.environ.get(
    "DISTILLED_RESULTS_TABLE",
    os.environ.get("DISTILLED_INSTRUCTIONS_TABLE", "DistilledInstructionsTable"),
)


# ── Gemini Client (lazy singleton) ───────────────────────────────────────────

_gemini_client = None


def _get_gemini_client():
    """Lazy-init Gemini client (same pattern as instruction_distiller.py)."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    try:
        import google.generativeai as genai

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            try:
                from src.common.secrets_utils import get_gemini_api_key
                api_key = get_gemini_api_key() or ""
            except ImportError:
                pass

        if api_key:
            genai.configure(api_key=api_key)
            _gemini_client = genai
            return _gemini_client
    except ImportError:
        logger.warning("[BackgroundDistiller] google-generativeai not available")

    return None


# ── Core Distillation ────────────────────────────────────────────────────────

DISTILLATION_SYSTEM_PROMPT = (
    "You are a technical writer specializing in information density. "
    "Rewrite the given text segment to increase specificity, remove "
    "hedging language, and replace vague statements with concrete data "
    "or examples. Keep approximately the same length."
)


def _distill_segments(
    original_text: str,
    targets: List[Dict[str, Any]],
) -> str:
    """Distill low-entropy segments using Gemini Flash.

    Falls back to returning original_text unmodified if Gemini is
    unavailable or all distillation attempts fail.
    """
    genai = _get_gemini_client()
    if not genai:
        logger.warning(
            "[BackgroundDistiller] No Gemini client — returning original text"
        )
        return original_text

    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        system_instruction=DISTILLATION_SYSTEM_PROMPT,
    )

    result_text = original_text
    segments_improved = 0

    for target in targets:
        segment_text = target.get("segment_text", "")
        prompt = target.get("distillation_prompt", "")
        if not segment_text or not prompt:
            continue

        try:
            response = model.generate_content(
                prompt,
                generation_config={"max_output_tokens": 300, "temperature": 0.3},
            )
            improved = response.text.strip() if response.text else ""

            if improved and segment_text in result_text:
                result_text = result_text.replace(segment_text, improved, 1)
                segments_improved += 1
        except Exception as exc:
            logger.warning(
                "[BackgroundDistiller] Segment distillation failed: %s", exc
            )

    logger.info(
        "[BackgroundDistiller] Distilled %d/%d segments",
        segments_improved, len(targets),
    )
    return result_text


# ── EventBridge Emission ─────────────────────────────────────────────────────

def _emit_completion_event(
    task_id: str,
    workflow_id: str,
    node_id: str,
    original_length: int,
    distilled_length: int,
    segments_improved: int,
    websocket_channel: Optional[str],
) -> None:
    """Emit ``DistillationCompleted`` event for websocket notification."""
    try:
        import boto3

        detail = {
            "task_id": task_id,
            "workflow_id": workflow_id,
            "node_id": node_id,
            "original_length": original_length,
            "distilled_length": distilled_length,
            "segments_improved": segments_improved,
            "websocket_channel": websocket_channel or "",
        }

        boto3.client("events").put_events(Entries=[{
            "Source": "backend-workflow.kernel",
            "DetailType": "DistillationCompleted",
            "Detail": json.dumps(detail, default=str),
            "EventBusName": WORKFLOW_EVENT_BUS_NAME,
        }])
    except Exception as exc:
        logger.warning(
            "[BackgroundDistiller] Failed to emit completion event: %s", exc
        )


# ── Lambda Handler ───────────────────────────────────────────────────────────

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Handle ``BackgroundDistillationRequested`` EventBridge events.

    Event structure (from KernelMiddlewareInterceptor._publish_distillation_event):
    {
        "detail-type": "BackgroundDistillationRequested",
        "source": "backend-workflow.kernel",
        "detail": {
            "task_id": "distill-abc12345",
            "workflow_id": "wf-123",
            "node_id": "generate_report",
            "original_text": "...",
            "distillation_targets": [...],
            "priority": 2,
            "created_at": "2026-...",
            "websocket_channel": "ws://wf-123/generate_report/distillation"
        }
    }
    """
    start = time.time()
    logger.info(
        "[BackgroundDistiller] Received event: %s",
        json.dumps(event, default=str)[:500],
    )

    detail = event.get("detail", {})
    # EventBridge wraps detail as string when coming from certain sources
    if isinstance(detail, str):
        detail = json.loads(detail)

    task_id = detail.get("task_id", "unknown")
    workflow_id = detail.get("workflow_id", "unknown")
    node_id = detail.get("node_id", "unknown")
    original_text = detail.get("original_text", "")
    targets = detail.get("distillation_targets", [])
    websocket_channel = detail.get("websocket_channel")

    if not original_text or not targets:
        logger.warning(
            "[BackgroundDistiller] Empty text or targets for task=%s", task_id
        )
        return {"statusCode": 400, "body": "Missing original_text or targets"}

    # Run distillation
    distilled_text = _distill_segments(original_text, targets)

    elapsed_ms = (time.time() - start) * 1000
    logger.info(
        "[BackgroundDistiller] task=%s completed in %.0fms "
        "(original=%d distilled=%d)",
        task_id, elapsed_ms, len(original_text), len(distilled_text),
    )

    # Emit completion event for downstream consumers (websocket notifier, etc.)
    segments_improved = sum(
        1 for t in targets
        if t.get("segment_text", "") not in distilled_text
    )
    _emit_completion_event(
        task_id=task_id,
        workflow_id=workflow_id,
        node_id=node_id,
        original_length=len(original_text),
        distilled_length=len(distilled_text),
        segments_improved=segments_improved,
        websocket_channel=websocket_channel,
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "task_id": task_id,
            "distilled_length": len(distilled_text),
            "segments_improved": segments_improved,
            "elapsed_ms": round(elapsed_ms, 1),
        }),
    }
