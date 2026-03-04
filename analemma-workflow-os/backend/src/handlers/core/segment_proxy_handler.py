"""
Segment Proxy Handler — E2E Hybrid IoT Core Bridge Lambda

Bridges AWS SFN to a local E2E worker via IoT Core MQTT pub/sub.
Uses the existing waitForTaskToken callback pattern (same as HITP).

Flow:
    1. Receives segment payload from SFN ($.state_data.bag)
    2. Extracts TaskToken from SFN context
    3. Stores token + payload in DynamoDB (TaskTokensTableV3)
    4. If payload >100KB: upload to S3, send pointer via MQTT
    5. Publish task to IoT Core topic: analemma/e2e/{execution_id}/task
    6. SFN pauses (waitForTaskToken) -- no Lambda timeout constraint
    7. Local MQTT subscriber executes and publishes result
    8. IoT Rule triggers IoTCallbackFunction -> sfn.send_task_success()

Environment Variables:
    E2E_IOT_ENDPOINT       : IoT Core Data-ATS endpoint
    TASK_TOKENS_TABLE_NAME : DynamoDB table for task token storage
    WORKFLOW_STATE_BUCKET  : S3 bucket for MQTT payload offloading (>100KB)
    E2E_MODE               : 'true' to enable proxy behavior
"""

import json
import logging
import os
import time
from typing import Any, Dict

import boto3
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
IOT_ENDPOINT = os.environ.get("E2E_IOT_ENDPOINT", "")
TASK_TOKENS_TABLE = os.environ.get("TASK_TOKENS_TABLE_NAME", "TaskTokensTableV3")
WORKFLOW_STATE_BUCKET = os.environ.get("WORKFLOW_STATE_BUCKET", "")
E2E_MODE = os.environ.get("E2E_MODE", "false").lower() == "true"

# MQTT payload threshold (100KB, safety margin for 128KB MQTT limit)
MQTT_PAYLOAD_THRESHOLD = 100_000

# Lazy init for cold start optimization
_dynamodb = None
_table = None
_iot_client = None
_s3_client = None


def _get_table():
    global _dynamodb, _table
    if _table is None:
        _dynamodb = boto3.resource("dynamodb")
        _table = _dynamodb.Table(TASK_TOKENS_TABLE)
    return _table


def _get_iot_client():
    global _iot_client
    if _iot_client is None:
        _iot_client = boto3.client(
            "iot-data",
            endpoint_url=f"https://{IOT_ENDPOINT}" if IOT_ENDPOINT else None,
        )
    return _iot_client


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def _upload_to_s3(payload: dict, s3_key: str) -> str:
    """Upload JSON payload to S3 and return s3:// path."""
    s3 = _get_s3_client()
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    s3.put_object(
        Bucket=WORKFLOW_STATE_BUCKET,
        Key=s3_key,
        Body=body,
        ContentType="application/json",
    )
    s3_path = f"s3://{WORKFLOW_STATE_BUCKET}/{s3_key}"
    logger.info("[E2E Proxy] Uploaded to S3: %s (%d bytes)", s3_path, len(body))
    return s3_path


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    SFN ExecuteSegment proxy: stores TaskToken, publishes task to IoT Core.

    This Lambda is used in E2E testing mode only. In production, SFN uses
    SegmentRunnerFunction directly.
    """
    if not E2E_MODE:
        raise RuntimeError(
            "segment_proxy_handler invoked without E2E_MODE=true. "
            "This handler is for E2E hybrid IoT Core testing only."
        )

    if not IOT_ENDPOINT:
        raise ValueError(
            "E2E_IOT_ENDPOINT environment variable is not set. "
            "Cannot publish payload to IoT Core."
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
        "[E2E Proxy] Received segment payload. execution_id=%s, owner_id=%s, iot_endpoint=%s",
        execution_id, owner_id, IOT_ENDPOINT,
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
        "transport": "iot_core_mqtt",
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

    # Build worker payload
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

    # Serialize and check size against MQTT 128KB limit
    payload_bytes = json.dumps(worker_payload, ensure_ascii=False, default=str).encode("utf-8")
    topic = f"analemma/e2e/{execution_id}/task"

    if len(payload_bytes) > MQTT_PAYLOAD_THRESHOLD:
        # S3 offload — send pointer via MQTT instead of full payload
        s3_key = f"mqtt-payloads/{execution_id}/task_{now}.json"
        s3_path = _upload_to_s3(worker_payload, s3_key)
        mqtt_message = json.dumps({
            "__mqtt_s3_offloaded": True,
            "__mqtt_s3_path": s3_path,
            "execution_id": execution_id,
            "task_token": task_token,
        }).encode("utf-8")
        logger.info(
            "[E2E Proxy] Payload exceeds 100KB (%d bytes), S3-offloaded to %s",
            len(payload_bytes), s3_path,
        )
    else:
        mqtt_message = payload_bytes

    # Publish to IoT Core
    iot_client = _get_iot_client()
    iot_client.publish(
        topic=topic,
        qos=1,
        payload=mqtt_message,
    )
    logger.info(
        "[E2E Proxy] Published to IoT Core: topic=%s, size=%d bytes, execution_id=%s",
        topic, len(mqtt_message), execution_id,
    )

    # No return value needed — SFN is in waitForTaskToken mode
    return {"status": "PUBLISHED", "execution_id": execution_id, "topic": topic}
