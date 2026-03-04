"""
IoT Callback Handler — Receives E2E task results from IoT Rule, calls SFN SendTaskSuccess.

Flow:
    1. IoT Rule on 'analemma/e2e/+/result' triggers this Lambda
    2. If __mqtt_s3_offloaded: download full result from S3
    3. Call sfn.send_task_success(token, result) or send_task_failure

Security:
    - QoS 1 dedup: try/except on InvalidToken guards duplicate send_task_success
    - S3 pointer rehydration for payloads >100KB

Environment Variables:
    WORKFLOW_STATE_BUCKET : S3 bucket for MQTT payload offloading
"""

import json
import logging
import os
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Environment
WORKFLOW_STATE_BUCKET = os.environ.get("WORKFLOW_STATE_BUCKET", "")

# Lazy init
_sfn_client = None
_s3_client = None


def _get_sfn_client():
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions")
    return _sfn_client


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def _download_from_s3(s3_path: str) -> dict:
    """
    Download JSON payload from S3.

    Args:
        s3_path: 's3://bucket/key' or just 'key' (uses WORKFLOW_STATE_BUCKET)

    Returns:
        Parsed JSON dict.
    """
    if s3_path.startswith("s3://"):
        # Parse s3://bucket/key
        parts = s3_path[5:].split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
    else:
        bucket = WORKFLOW_STATE_BUCKET
        key = s3_path

    logger.info("[IoTCallback] Downloading from S3: bucket=%s, key=%s", bucket, key)
    s3 = _get_s3_client()
    resp = s3.get_object(Bucket=bucket, Key=key)
    body = resp["Body"].read().decode("utf-8")
    return json.loads(body)


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    IoT Rule → Lambda: receives E2E task result, calls sfn.send_task_success().

    Event shape (from IoT Rule SQL: SELECT * FROM 'analemma/e2e/+/result'):
    {
        "task_token": "...",
        "execution_id": "...",
        "result_payload": { ... sealed state bag ... },
        "__mqtt_s3_offloaded": false,
        "__mqtt_s3_path": "s3://bucket/key"  (only if offloaded)
    }
    """
    task_token = event.get("task_token")
    execution_id = event.get("execution_id", "unknown")

    if not task_token:
        logger.error("[IoTCallback] No task_token in event: %s", json.dumps(event)[:500])
        return {"status": "ERROR", "reason": "missing_task_token"}

    logger.info(
        "[IoTCallback] Received result: execution_id=%s, s3_offloaded=%s",
        execution_id, event.get("__mqtt_s3_offloaded", False),
    )

    # Rehydrate S3-offloaded payload if needed
    if event.get("__mqtt_s3_offloaded"):
        s3_path = event.get("__mqtt_s3_path", "")
        if not s3_path:
            logger.error("[IoTCallback] __mqtt_s3_offloaded=true but no __mqtt_s3_path")
            return {"status": "ERROR", "reason": "missing_s3_path"}
        result_payload = _download_from_s3(s3_path)
    else:
        result_payload = event.get("result_payload", {})

    sfn = _get_sfn_client()

    # Error path
    if event.get("error"):
        error_msg = str(event["error"])[:256]
        cause = str(event.get("cause", ""))[:32768]
        try:
            sfn.send_task_failure(
                taskToken=task_token,
                error=error_msg,
                cause=cause,
            )
            logger.info("[IoTCallback] SendTaskFailure: execution_id=%s, error=%s", execution_id, error_msg)
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code", "")
            if code in ("InvalidToken", "TaskTimedOut"):
                logger.warning("[IoTCallback] Token invalid/expired on failure (idempotent): %s", code)
            else:
                raise
        return {"status": "FAILURE_SENT", "execution_id": execution_id}

    # Success path — guard against duplicate send_task_success (QoS 1 dedup)
    output_json = json.dumps(result_payload, ensure_ascii=False, default=str)

    # SFN output limit is 256KB — log warning if exceeded
    output_size = len(output_json.encode("utf-8"))
    if output_size > 256_000:
        logger.warning(
            "[IoTCallback] Output exceeds 256KB (%d bytes). "
            "seal_state_bag should have S3-offloaded this.",
            output_size,
        )

    try:
        sfn.send_task_success(taskToken=task_token, output=output_json)
        logger.info(
            "[IoTCallback] SendTaskSuccess: execution_id=%s, output_size=%d bytes",
            execution_id, output_size,
        )
    except ClientError as e:
        code = (e.response.get("Error") or {}).get("Code", "")
        if code in ("InvalidToken", "TaskTimedOut"):
            # QoS 1 duplicate delivery or token already consumed — safe to ignore
            logger.warning("[IoTCallback] Duplicate or expired token (idempotent): %s", code)
        else:
            raise

    return {"status": "SUCCESS_SENT", "execution_id": execution_id}
