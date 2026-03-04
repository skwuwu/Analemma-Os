"""
E2E Test Fixtures — AWS credentials, local servers, IoT Core MQTT.

Features:
    - Real AWS session (NOT moto) for E2E tests
    - VSM server on :8765 as background process
    - IoT Core MQTT worker (replaces ngrok tunnel — outbound 443 only)
    - MerkleVerifier fixture for state consistency checks
    - Hard timeout (120s) per test
    - Idempotency table creation/verification
"""

import os
import signal
import subprocess
import sys
import threading
import time
import logging
from pathlib import Path
from typing import Optional

# Ensure this directory is on sys.path so sibling modules (mqtt_client, etc.) can be imported
_E2E_DIR = str(Path(__file__).parent)
if _E2E_DIR not in sys.path:
    sys.path.insert(0, _E2E_DIR)

import boto3
import pytest
import requests

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

VSM_PORT = int(os.environ.get("VSM_PORT", "8765"))
E2E_TEST_TIMEOUT = int(os.environ.get("E2E_TEST_TIMEOUT", "120"))

# ── Pytest Markers ───────────────────────────────────────────────────────────


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: E2E tests requiring real AWS infrastructure")
    config.addinivalue_line("markers", "hybrid: Tests using local worker + real SFN")
    config.addinivalue_line("markers", "cloud: Tests running entirely in AWS")
    config.addinivalue_line("markers", "chaos: Chaos engineering scenarios")


# ── AWS Session ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def aws_session():
    """Real AWS session (NOT moto). Requires AWS_PROFILE or credentials."""
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    session = boto3.Session(region_name=region)
    # Verify credentials work
    sts = session.client("sts")
    try:
        identity = sts.get_caller_identity()
        logger.info("[E2E] AWS identity: %s", identity.get("Arn", "unknown"))
    except Exception as e:
        pytest.skip(f"AWS credentials not available: {e}")
    return session


# ── SFN Client ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def sfn_client(aws_session):
    """Step Functions client."""
    return aws_session.client("stepfunctions")


# ── S3 Client ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def s3_client(aws_session):
    """S3 client."""
    return aws_session.client("s3")


# ── DynamoDB ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def dynamodb_resource(aws_session):
    """DynamoDB resource."""
    return aws_session.resource("dynamodb")


# ── Idempotency Table ────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def idempotency_table(dynamodb_resource):
    """Ensure E2EWorkerIdempotency DynamoDB table exists for dedup."""
    table_name = os.environ.get("E2E_IDEMPOTENCY_TABLE", "E2EWorkerIdempotency")
    try:
        table = dynamodb_resource.Table(table_name)
        table.load()  # Check if table exists
        logger.info("[E2E] Idempotency table exists: %s", table_name)
    except dynamodb_resource.meta.client.exceptions.ResourceNotFoundException:
        logger.info("[E2E] Creating idempotency table: %s", table_name)
        table = dynamodb_resource.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "idempotency_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "idempotency_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        # Enable TTL
        dynamodb_resource.meta.client.update_time_to_live(
            TableName=table_name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
        )
        logger.info("[E2E] Idempotency table created with TTL: %s", table_name)
    return table


# ── Local Server Management ─────────────────────────────────────────────────


def _wait_for_server(url: str, timeout: int = 30) -> bool:
    """Wait for a server to become available."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=2)
            if resp.status_code == 200:
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
    return False


@pytest.fixture(scope="session")
def vsm_server():
    """Start VirtualSegmentManager on :8765 in background process."""
    health_url = f"http://localhost:{VSM_PORT}/v1/health"

    # Check if already running
    try:
        resp = requests.get(health_url, timeout=2)
        if resp.status_code == 200:
            logger.info("[E2E] VSM already running on port %d", VSM_PORT)
            yield None
            return
    except requests.exceptions.ConnectionError:
        pass

    logger.info("[E2E] Starting VSM server on port %d", VSM_PORT)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "src.bridge.virtual_segment_manager:app",
            "--host", "0.0.0.0",
            "--port", str(VSM_PORT),
        ],
        cwd=os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if not _wait_for_server(health_url, timeout=30):
        proc.terminate()
        pytest.fail(f"VSM server failed to start on port {VSM_PORT}")

    logger.info("[E2E] VSM server started (pid=%d)", proc.pid)
    yield proc
    proc.terminate()
    proc.wait(timeout=10)


# ── IoT Core MQTT Worker ────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def iot_endpoint(aws_session):
    """Discover IoT Core Data-ATS endpoint for the account."""
    iot_client = aws_session.client("iot")
    response = iot_client.describe_endpoint(endpointType="iot:Data-ATS")
    endpoint = response["endpointAddress"]
    logger.info("[E2E] IoT Core endpoint: %s", endpoint)
    return endpoint


@pytest.fixture(scope="session", autouse=True)
def mqtt_worker(vsm_server, iot_endpoint, aws_session, idempotency_table, state_bucket):
    """
    Start local MQTT worker that subscribes to E2E task topics.

    [Feedback ⑤] Depends on vsm_server fixture — pytest guarantees VSM
    is healthy before this fixture runs. Additionally, explicit health
    check before MQTT connect ensures governance pipeline is ready.
    """
    from mqtt_client import E2EMqttClient

    # [Feedback ⑤] Verify VSM is healthy before starting MQTT worker
    vsm_url = f"http://localhost:{VSM_PORT}/v1/health"
    try:
        resp = requests.get(vsm_url, timeout=5)
        assert resp.status_code == 200, f"VSM not healthy: {resp.text}"
    except requests.exceptions.ConnectionError:
        pytest.fail(f"VSM not running on port {VSM_PORT}. Cannot start MQTT worker.")

    client = E2EMqttClient(
        iot_endpoint=iot_endpoint,
        region=aws_session.region_name,
        state_bucket=state_bucket,
    )
    client.connect()
    client.subscribe_and_run()

    logger.info("[E2E] MQTT worker connected and subscribed (client_id=%s)", client._client_id)
    yield client

    client.disconnect()


# ── MerkleVerifier ───────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def merkle_verifier(s3_client, dynamodb_resource, state_bucket):
    """MerkleVerifier instance for cross-boundary state consistency checks."""
    from merkle_verifier import MerkleVerifier
    from src.services.state.state_versioning_service import StateVersioningService

    table = os.environ.get("MANIFESTS_TABLE", "WorkflowManifests-v3-dev")
    versioning = StateVersioningService(dynamodb_table=table, s3_bucket=state_bucket)
    return MerkleVerifier(
        s3_client=s3_client,
        dynamodb_resource=dynamodb_resource,
        versioning_service=versioning,
        state_bucket=state_bucket,
    )


# ── Test Timeout ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def e2e_timeout():
    """Hard timeout per test to prevent runaway loops."""
    # signal.alarm is Unix-only; use threading timer on Windows
    if sys.platform == "win32":
        timer = threading.Timer(E2E_TEST_TIMEOUT, _timeout_handler)
        timer.daemon = True
        timer.start()
        yield
        timer.cancel()
    else:
        old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(E2E_TEST_TIMEOUT)
        yield
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _alarm_handler(signum, frame):
    raise TimeoutError(f"E2E test exceeded {E2E_TEST_TIMEOUT}s timeout")


def _timeout_handler():
    raise TimeoutError(f"E2E test exceeded {E2E_TEST_TIMEOUT}s timeout")


# ── SFN Execution Helpers ────────────────────────────────────────────────────


@pytest.fixture
def start_sfn_execution(sfn_client):
    """Helper fixture to start SFN executions with E2E cost control."""
    import uuid

    def _start(state_machine_arn: str, input_data: dict, name_prefix: str = "e2e"):
        execution_name = f"{name_prefix}_{uuid.uuid4().hex[:12]}"
        response = sfn_client.start_execution(
            stateMachineArn=state_machine_arn,
            name=execution_name,
            input=__import__("json").dumps(input_data, default=str),
        )
        return response["executionArn"], execution_name

    return _start


@pytest.fixture
def wait_for_sfn_completion(sfn_client):
    """Helper fixture to poll SFN execution until completion or timeout."""

    def _wait(execution_arn: str, timeout: int = 600, poll_interval: int = 5):
        """
        Wait for SFN execution to reach terminal state.

        Args:
            execution_arn: SFN execution ARN
            timeout: Max wait time in seconds (default 600s = 10 min cost control)
            poll_interval: Seconds between polls

        Returns:
            dict: describe_execution response
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = sfn_client.describe_execution(executionArn=execution_arn)
            status = resp["status"]
            if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
                return resp
            time.sleep(poll_interval)
        raise TimeoutError(f"SFN execution did not complete within {timeout}s: {execution_arn}")

    return _wait


# ── E2E SFN ARN Fixtures ────────────────────────────────────────────────────


# ── Dehydration-Aware Output Helpers ─────────────────────────────────────────


def _extract_bag(output: dict) -> dict:
    """
    Extract the bag dict from SFN output, handling both direct and distributed formats.

    The distributed SFN wraps results in final_state after seal_state_bag. This
    navigates through the common nesting patterns:
        output -> state_data -> bag -> final_state
    """
    state_data = output.get("state_data", output)
    bag = state_data.get("bag", state_data)
    # Distributed SFN wraps in final_state
    final_state = bag.get("final_state", bag)
    return final_state


def _has_batch_pointers(bag: dict) -> bool:
    """
    Check if seal_state_bag dehydrated data into S3 batch pointers.

    When SmartStateBag.seal_state_bag runs, non-control-plane fields are
    batched into __hot_batch__ and __cold_batch__ S3 pointers. The presence
    of these pointers confirms that seal_state_bag executed and data was
    properly offloaded.
    """
    hot = bag.get("__hot_batch__", {})
    cold = bag.get("__cold_batch__", {})
    return (
        (isinstance(hot, dict) and hot.get("__batch_pointer__"))
        or (isinstance(cold, dict) and cold.get("__batch_pointer__"))
    )


def _assert_react_evidence(bag: dict, context: str = ""):
    """
    Assert that either react_result/llm_raw_output are present (pre-dehydration)
    or batch pointers exist (post-dehydration via seal_state_bag).

    Args:
        bag: The extracted bag/final_state dict.
        context: Optional description for assertion error messages.
    """
    has_direct_keys = bag.get("react_result") or bag.get("llm_raw_output")
    has_dehydrated = _has_batch_pointers(bag)
    prefix = f"[{context}] " if context else ""
    assert has_direct_keys or has_dehydrated, (
        f"{prefix}No ReactResult evidence found. Expected react_result/llm_raw_output "
        f"in bag or __hot_batch__/__cold_batch__ pointers from seal_state_bag. "
        f"Keys present: {list(bag.keys())}"
    )


def _get_react_result(bag: dict) -> dict:
    """
    Safely extract react_result from bag. Returns empty dict if dehydrated.

    When seal_state_bag dehydrates the bag, react_result is offloaded to S3
    and no longer directly accessible. Callers should gate assertions on the
    return value being non-empty.
    """
    result = bag.get("react_result", {})
    if isinstance(result, dict) and not result.get("__batch_pointer__"):
        return result
    return {}


@pytest.fixture(scope="session")
def sfn_orchestrator_arn():
    """ARN of the E2E test SFN orchestrator (must be set via env var)."""
    arn = os.environ.get("E2E_SFN_ORCHESTRATOR_ARN", "")
    if not arn:
        pytest.skip("E2E_SFN_ORCHESTRATOR_ARN not set")
    return arn


@pytest.fixture(scope="session")
def state_bucket(aws_session):
    """S3 bucket name for workflow state.

    Auto-discovers from Lambda env if not set via WORKFLOW_STATE_BUCKET.
    """
    bucket = os.environ.get(
        "WORKFLOW_STATE_BUCKET",
        os.environ.get("SKELETON_S3_BUCKET", ""),
    )
    if bucket:
        return bucket

    # Auto-discover from deployed Lambda configuration
    try:
        lambda_client = aws_session.client("lambda")
        paginator = lambda_client.get_paginator("list_functions")
        for page in paginator.paginate():
            for fn in page.get("Functions", []):
                if "InitializeStateData" in fn["FunctionName"]:
                    resp = lambda_client.get_function_configuration(
                        FunctionName=fn["FunctionName"],
                    )
                    bucket = resp.get("Environment", {}).get("Variables", {}).get(
                        "WORKFLOW_STATE_BUCKET", ""
                    )
                    if bucket:
                        logger.info("[E2E] Auto-discovered state bucket: %s", bucket)
                        return bucket
    except Exception as e:
        logger.warning("[E2E] Could not auto-discover state bucket: %s", e)

    return "analemma-workflow-state-dev"
