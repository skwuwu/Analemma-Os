"""
E2E MQTT Client — Local subscriber for AWS IoT Core task dispatch.

Replaces the ngrok tunnel approach: connects to IoT Core via
WebSocket+SigV4 (outbound 443 only, no inbound ports needed),
subscribes to task topics, executes segments via ReactExecutor,
and publishes results back.

Features:
    - SigV4 WebSocket auth via awscrt (IAM credentials from env)
    - Auto-reconnect on connection interruption [Feedback ②]
    - QoS 1 with DynamoDB idempotency [Feedback ①]
    - S3 offload for payloads >100KB (MQTT 128KB limit)
    - Thread-safe execution with background processing

Dependencies:
    pip install awsiotsdk
"""

import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

import boto3

logger = logging.getLogger(__name__)

# MQTT payload threshold (100KB safety margin for 128KB MQTT limit)
MQTT_PAYLOAD_THRESHOLD = 100_000

# ── S3 Helpers ───────────────────────────────────────────────────────────────

_s3_client = None


def _get_s3_client():
    global _s3_client
    if _s3_client is None:
        region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
        _s3_client = boto3.client("s3", region_name=region)
    return _s3_client


def _download_from_s3(s3_path: str) -> dict:
    """Download JSON payload from S3 path (s3://bucket/key)."""
    if s3_path.startswith("s3://"):
        parts = s3_path[5:].split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
    else:
        bucket = os.environ.get("WORKFLOW_STATE_BUCKET", "")
        key = s3_path

    s3 = _get_s3_client()
    resp = s3.get_object(Bucket=bucket, Key=key)
    body = resp["Body"].read().decode("utf-8")
    return json.loads(body)


def _upload_to_s3(payload: dict, bucket: str, s3_key: str) -> str:
    """Upload JSON payload to S3 and return s3:// path."""
    s3 = _get_s3_client()
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key=s3_key,
        Body=body,
        ContentType="application/json",
    )
    return f"s3://{bucket}/{s3_key}"


# ── MQTT Client ──────────────────────────────────────────────────────────────


class E2EMqttClient:
    """
    Local MQTT client for E2E hybrid testing via AWS IoT Core.

    Connects via WebSocket+SigV4, subscribes to task topics,
    dispatches to ReactExecutor, publishes results back.
    """

    TASK_TOPIC_FILTER = "analemma/e2e/+/task"
    RESULT_TOPIC_TEMPLATE = "analemma/e2e/{execution_id}/result"

    def __init__(
        self,
        iot_endpoint: str,
        region: str,
        state_bucket: str = "",
    ):
        self._iot_endpoint = iot_endpoint
        self._region = region
        self._state_bucket = state_bucket or os.environ.get(
            "WORKFLOW_STATE_BUCKET",
            os.environ.get("SKELETON_S3_BUCKET", "analemma-workflow-state-dev"),
        )
        self._connection = None
        self._connected = False
        self._running = False
        self._client_id = f"analemma-e2e-{uuid.uuid4().hex[:8]}"
        self._execution_log: list = []
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def execution_log(self) -> list:
        with self._lock:
            return list(self._execution_log)

    def connect(self) -> None:
        """Establish MQTT connection using SigV4 WebSocket auth."""
        try:
            from awscrt import mqtt
            from awscrt.auth import AwsCredentialsProvider
            from awsiot import mqtt_connection_builder
        except ImportError:
            raise ImportError(
                "awsiotsdk not installed. Install with: pip install awsiotsdk"
            )

        logger.info(
            "[MqttClient] Connecting to %s as %s (region=%s)",
            self._iot_endpoint, self._client_id, self._region,
        )

        # awscrt default chain doesn't support SSO/credential_process.
        # Resolve credentials via boto3 (which supports all AWS credential sources)
        # and pass them as static credentials to awscrt.
        import boto3 as _boto3
        _session = _boto3.Session(region_name=self._region)
        _creds = _session.get_credentials().get_frozen_credentials()
        credentials_provider = AwsCredentialsProvider.new_static(
            access_key_id=_creds.access_key,
            secret_access_key=_creds.secret_key,
            session_token=_creds.token or "",
        )

        self._connection = mqtt_connection_builder.websockets_with_default_aws_signing(
            endpoint=self._iot_endpoint,
            region=self._region,
            credentials_provider=credentials_provider,
            client_id=self._client_id,
            clean_session=True,
            keep_alive_secs=30,
            on_connection_interrupted=self._on_connection_interrupted,
            on_connection_resumed=self._on_connection_resumed,
        )

        connect_future = self._connection.connect()
        connect_future.result(timeout=30)
        self._connected = True
        logger.info("[MqttClient] Connected to IoT Core: %s", self._iot_endpoint)

    def _on_connection_interrupted(self, connection, error, **kwargs):
        """[Feedback ②] Auto-reconnect callback — awscrt handles reconnection."""
        self._connected = False
        logger.warning(
            "[MqttClient] Connection interrupted: %s. awscrt will auto-reconnect.",
            error,
        )

    def _on_connection_resumed(self, connection, return_code, session_present, **kwargs):
        """[Feedback ②] Re-subscribe after reconnection."""
        self._connected = True
        logger.info(
            "[MqttClient] Connection resumed (return_code=%s, session_present=%s). Re-subscribing...",
            return_code, session_present,
        )
        if not session_present and self._running:
            self._subscribe()

    def _subscribe(self) -> None:
        """Subscribe to task topic filter."""
        from awscrt import mqtt

        subscribe_future, _ = self._connection.subscribe(
            topic=self.TASK_TOPIC_FILTER,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=self._on_task_message,
        )
        subscribe_future.result(timeout=10)
        logger.info("[MqttClient] Subscribed to: %s", self.TASK_TOPIC_FILTER)

    def subscribe_and_run(self) -> None:
        """Subscribe to task topics and start processing messages."""
        self._running = True
        self._subscribe()

    def _on_task_message(self, topic: str, payload: bytes, **kwargs) -> None:
        """
        Handle incoming task message from IoT Core.

        Runs execution in a background thread to avoid blocking the MQTT
        event loop. Uses DynamoDB idempotency to prevent duplicate execution.
        """
        thread = threading.Thread(
            target=self._process_task,
            args=(topic, payload),
            daemon=True,
        )
        thread.start()

    def _process_task(self, topic: str, payload: bytes) -> None:
        """Process a single task message (runs in background thread)."""
        try:
            message = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.error("[MqttClient] Invalid JSON payload on %s: %s", topic, e)
            return

        execution_id = message.get("execution_id", "unknown")
        task_token = message.get("task_token", "")

        logger.info(
            "[MqttClient] Received task: topic=%s, execution_id=%s, s3_offloaded=%s",
            topic, execution_id, message.get("__mqtt_s3_offloaded", False),
        )

        # S3 rehydration if needed
        if message.get("__mqtt_s3_offloaded"):
            s3_path = message.get("__mqtt_s3_path", "")
            if not s3_path:
                logger.error("[MqttClient] __mqtt_s3_offloaded=true but no __mqtt_s3_path")
                return
            message = _download_from_s3(s3_path)
            task_token = message.get("task_token", task_token)
            execution_id = message.get("execution_id", execution_id)

        if not task_token:
            logger.error("[MqttClient] No task_token in message")
            return

        # [Feedback ①] Idempotency check — prevent duplicate execution from QoS 1
        from worker_server import (
            _idempotency_key,
            check_idempotency_simple,
            _write_idempotency,
        )

        idem_key = _idempotency_key(task_token)
        cached = check_idempotency_simple(idem_key)
        if cached is not None:
            if cached == "PENDING":
                logger.info("[MqttClient] Task already in-progress, skipping: %s", execution_id)
            else:
                logger.info("[MqttClient] Task already completed (cached), skipping: %s", execution_id)
            return

        # Mark as PENDING
        _write_idempotency(idem_key, "PENDING")

        # Execute segment task
        try:
            from worker_server import execute_segment_task
            from src.common.kernel_protocol import open_state_bag

            bag = message.get("bag") or open_state_bag(message.get("payload", {}))
            max_iterations = message.get("max_iterations", 10)
            owner_id = message.get("owner_id", "e2e_test_owner")
            workflow_config = message.get("workflow_config")

            sealed, response_summary = execute_segment_task(
                task_token=task_token,
                execution_id=execution_id,
                owner_id=owner_id,
                bag=bag,
                max_iterations=max_iterations,
                workflow_config=workflow_config,
            )

            # E2E test flag: force CONTINUE to test SFN loop limit behavior.
            # Check multiple locations since _force_continue may not survive
            # through InitializeStateBag if the Lambda hasn't been redeployed.
            # Fallback: detect "looplimit" in execution_id (set by test name_prefix).
            force_continue = (
                message.get("_force_continue")
                or bag.get("_force_continue")
                or message.get("payload", {}).get("_force_continue")
                or "looplimit" in execution_id
            )
            if force_continue:
                sealed["next_action"] = "CONTINUE"
                # Re-inject _force_continue into state_data so it survives
                # dehydration and is available on subsequent SFN loop iterations.
                if isinstance(sealed.get("state_data"), dict):
                    sealed["state_data"]["_force_continue"] = True
                logger.info("[MqttClient] _force_continue override: next_action=CONTINUE")

            # seal_state_bag returns {"state_data": {...}, "next_action": "COMPLETE/CONTINUE/..."}
            # This matches SFN ResultSelector: {"bag.$": "$.state_data", "next_action.$": "$.next_action"}
            # Send directly without additional wrapping.
            self._send_task_success(task_token, execution_id, sealed)

            # Mark completed
            _write_idempotency(idem_key, "COMPLETED", cached_result=response_summary)

            with self._lock:
                self._execution_log.append({
                    "execution_id": execution_id,
                    "status": "SUCCESS",
                    "stop_reason": response_summary.get("stop_reason", ""),
                    "iterations": response_summary.get("iterations", 0),
                    "timestamp": time.time(),
                })

            logger.info(
                "[MqttClient] Task completed: execution_id=%s, stop_reason=%s",
                execution_id, response_summary.get("stop_reason", ""),
            )

        except Exception as e:
            logger.exception("[MqttClient] Task execution failed: %s", e)
            _write_idempotency(idem_key, "FAILED")

            # Send failure directly via SFN API
            self._send_task_failure(task_token, execution_id, e)

            with self._lock:
                self._execution_log.append({
                    "execution_id": execution_id,
                    "status": "FAILED",
                    "error": str(e),
                    "timestamp": time.time(),
                })

    def _send_task_success(
        self, task_token: str, execution_id: str, sealed_result: dict
    ) -> None:
        """Call sfn.send_task_success() directly (bypasses IoT Callback Lambda)."""
        import boto3 as _boto3
        sfn = _boto3.client("stepfunctions", region_name=self._region)
        output = json.dumps(sealed_result, default=str, ensure_ascii=False)

        # SFN output limit is 256KB — if larger, the output was already S3-offloaded
        # by seal_state_bag, so this should fit.
        try:
            sfn.send_task_success(taskToken=task_token, output=output)
            logger.info(
                "[MqttClient] send_task_success: execution_id=%s, output_size=%d",
                execution_id, len(output),
            )
        except (sfn.exceptions.InvalidToken, sfn.exceptions.TaskTimedOut) as e:
            logger.warning("[MqttClient] Duplicate or expired token (idempotent): %s", e)

    def _send_task_failure(
        self, task_token: str, execution_id: str, error: Exception
    ) -> None:
        """Call sfn.send_task_failure() directly."""
        import boto3 as _boto3
        sfn = _boto3.client("stepfunctions", region_name=self._region)
        try:
            sfn.send_task_failure(
                taskToken=task_token,
                error=type(error).__name__,
                cause=str(error)[:32768],
            )
            logger.info("[MqttClient] send_task_failure: execution_id=%s", execution_id)
        except (sfn.exceptions.InvalidToken, sfn.exceptions.TaskTimedOut) as e:
            logger.warning("[MqttClient] Duplicate or expired token (idempotent): %s", e)

    def _publish_result(
        self, execution_id: str, task_token: str, sealed_result: dict
    ) -> None:
        """Publish result to IoT Core. S3-offload if >100KB. (Unused — kept for IoT Callback path.)"""
        result_topic = self.RESULT_TOPIC_TEMPLATE.format(execution_id=execution_id)

        mqtt_message = {
            "task_token": task_token,
            "execution_id": execution_id,
            "result_payload": sealed_result,
        }

        payload_bytes = json.dumps(mqtt_message, ensure_ascii=False, default=str).encode("utf-8")

        if len(payload_bytes) > MQTT_PAYLOAD_THRESHOLD:
            # S3 offload
            s3_key = f"mqtt-payloads/{execution_id}/result_{int(time.time())}.json"
            s3_path = _upload_to_s3(sealed_result, self._state_bucket, s3_key)
            mqtt_message = {
                "__mqtt_s3_offloaded": True,
                "__mqtt_s3_path": s3_path,
                "task_token": task_token,
                "execution_id": execution_id,
            }
            payload_bytes = json.dumps(mqtt_message).encode("utf-8")
            logger.info(
                "[MqttClient] Result S3-offloaded to %s (original %d bytes)",
                s3_path, len(payload_bytes),
            )

        from awscrt import mqtt
        pub_future, _ = self._connection.publish(
            topic=result_topic,
            payload=payload_bytes,
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )
        pub_future.result(timeout=30)
        logger.info("[MqttClient] Published result: topic=%s, size=%d bytes", result_topic, len(payload_bytes))

    def _publish_error(
        self, execution_id: str, task_token: str, error: Exception
    ) -> None:
        """Publish error result to IoT Core."""
        result_topic = self.RESULT_TOPIC_TEMPLATE.format(execution_id=execution_id)

        mqtt_message = {
            "task_token": task_token,
            "execution_id": execution_id,
            "error": type(error).__name__,
            "cause": str(error)[:32768],
        }

        payload_bytes = json.dumps(mqtt_message).encode("utf-8")

        from awscrt import mqtt
        pub_future, _ = self._connection.publish(
            topic=result_topic,
            payload=payload_bytes,
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )
        pub_future.result(timeout=30)
        logger.info("[MqttClient] Published error: topic=%s", result_topic)

    def disconnect(self) -> None:
        """Disconnect from IoT Core."""
        self._running = False
        if self._connection:
            try:
                disconnect_future = self._connection.disconnect()
                disconnect_future.result(timeout=10)
                logger.info("[MqttClient] Disconnected from IoT Core")
            except Exception as e:
                logger.warning("[MqttClient] Disconnect error: %s", e)
        self._connected = False
