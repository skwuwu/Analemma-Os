"""
Segment Proxy Handler — E2E Hybrid Tunnel Bridge Lambda

Bridges AWS SFN to a local E2E worker via HTTP tunnel (ngrok/Cloudflare).
Uses the existing waitForTaskToken callback pattern (same as HITP).

Flow:
    1. Receives segment payload from SFN ($.state_data.bag)
    2. Extracts TaskToken from SFN context
    3. Stores token + payload in DynamoDB (TaskTokensTableV3)
    4. HTTP POST payload to tunnel_url (env: E2E_WORKER_TUNNEL_URL)
    5. SFN pauses (waitForTaskToken) -- no Lambda timeout constraint
    6. Local worker calls sfn.send_task_success(token, result) to resume

Environment Variables:
    E2E_WORKER_TUNNEL_URL  : ngrok/Cloudflare Tunnel URL -> local :9876
    TASK_TOKENS_TABLE_NAME : DynamoDB table for task token storage
    E2E_MODE               : 'true' to enable proxy behavior
"""

import json
import logging
import os
import time
from typing import Any, Dict

import boto3
import requests
from botocore.exceptions import ClientError

try:
    from src.common.kernel_protocol import open_state_bag
except ImportError:
    try:
        from common.kernel_protocol import open_state_bag
    except ImportError:
        def open_state_bag(event: dict) -> dict:
            if not isinstance(event, dict):
                return {}
            state_data = event.get("state_data", {})
            if isinstance(state_data, dict):
                bag = state_data.get("bag")
                if isinstance(bag, dict):
                    return bag
                return state_data
            return event

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Environment configuration
TUNNEL_URL = os.environ.get("E2E_WORKER_TUNNEL_URL", "")
TASK_TOKENS_TABLE = os.environ.get("TASK_TOKENS_TABLE_NAME", "TaskTokensTableV3")
E2E_MODE = os.environ.get("E2E_MODE", "false").lower() == "true"

# DynamoDB resource (lazy init for cold start optimization)
_dynamodb = None
_table = None


def _get_table():
    global _dynamodb, _table
    if _table is None:
        _dynamodb = boto3.resource("dynamodb")
        _table = _dynamodb.Table(TASK_TOKENS_TABLE)
    return _table


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    SFN ExecuteSegment proxy: stores TaskToken, forwards payload to local worker.

    This Lambda is used in E2E testing mode only. In production, SFN uses
    SegmentRunnerFunction directly.
    """
    if not E2E_MODE:
        raise RuntimeError(
            "segment_proxy_handler invoked without E2E_MODE=true. "
            "This handler is for E2E hybrid tunnel testing only."
        )

    if not TUNNEL_URL:
        raise ValueError(
            "E2E_WORKER_TUNNEL_URL environment variable is not set. "
            "Cannot forward payload to local worker."
        )

    # Extract TaskToken (injected by SFN for waitForTaskToken states)
    task_token = event.get("TaskToken") or event.get("taskToken")
    if not task_token:
        logger.error("No TaskToken found in event. Is ExecuteSegment configured with 'Resource: arn:aws:states:::lambda:invoke.waitForTaskToken'?")
        raise ValueError("Missing TaskToken in event payload")

    # Extract state bag using kernel protocol
    bag = open_state_bag(event)
    execution_id = (
        event.get("execution_id")
        or event.get("executionId")
        or bag.get("execution_id")
        or bag.get("executionId")
        or "unknown"
    )
    owner_id = (
        event.get("ownerId")
        or event.get("owner_id")
        or bag.get("ownerId")
        or bag.get("owner_id")
        or "e2e_test_owner"
    )

    logger.info(
        "[E2E Proxy] Received segment payload. execution_id=%s, owner_id=%s, tunnel=%s",
        execution_id, owner_id, TUNNEL_URL,
    )

    # Store TaskToken in DynamoDB (idempotent write)
    now = int(time.time())
    table = _get_table()
    item = {
        "ownerId": owner_id,
        "conversation_id": execution_id,
        "execution_id": execution_id,
        "taskToken": task_token,
        "createdAt": now,
        "ttl": now + 3600,  # 1 hour TTL for E2E tests
        "source": "segment_proxy_handler",
        "tunnel_url": TUNNEL_URL,
    }

    try:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(conversation_id)",
        )
        logger.info("[E2E Proxy] TaskToken stored: execution_id=%s", execution_id)
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code")
        if code == "ConditionalCheckFailedException":
            logger.info("[E2E Proxy] TaskToken already stored (retry): execution_id=%s", execution_id)
        else:
            raise

    # Forward payload to local worker via tunnel
    worker_payload = {
        "task_token": task_token,
        "execution_id": execution_id,
        "owner_id": owner_id,
        "payload": event,
        "bag": bag,
        "max_iterations": bag.get("max_iterations", 10),
        "segment_to_run": bag.get("segment_to_run"),
        "total_segments": bag.get("total_segments"),
        "workflow_config": bag.get("workflow_config"),
    }

    try:
        resp = requests.post(
            f"{TUNNEL_URL.rstrip('/')}/task/execute",
            json=worker_payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        logger.info(
            "[E2E Proxy] Worker acknowledged: status=%d, execution_id=%s",
            resp.status_code, execution_id,
        )
        # Worker will call sfn.send_task_success() asynchronously.
        # This Lambda returns nothing -- SFN waits for the callback.
    except requests.exceptions.ConnectionError as e:
        logger.error("[E2E Proxy] Tunnel unreachable: %s", e)
        # SFN will wait for HeartbeatSeconds timeout, then Catch -> NotifyAndFail
        raise
    except requests.exceptions.Timeout as e:
        logger.warning("[E2E Proxy] Worker response timeout (non-fatal, worker may still be running): %s", e)
        # Worker might still be processing -- SFN waits for SendTaskSuccess

    # No return value -- SFN is in waitForTaskToken mode
    return {"status": "PROXIED", "execution_id": execution_id}
