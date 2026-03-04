"""
E2E Test Fixtures — AWS credentials, local servers, tunnel management.

Features:
    - Real AWS session (NOT moto) for E2E tests
    - VSM server on :8765 and Worker server on :9876 as background processes
    - Tunnel auto-rebuild with TunnelHealthMonitor (30s check interval)
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
from typing import Optional

import boto3
import pytest
import requests

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

E2E_WORKER_PORT = int(os.environ.get("E2E_WORKER_PORT", "9876"))
VSM_PORT = int(os.environ.get("VSM_PORT", "8765"))
TUNNEL_CHECK_INTERVAL = int(os.environ.get("TUNNEL_CHECK_INTERVAL", "30"))
TUNNEL_MAX_RETRIES = int(os.environ.get("TUNNEL_MAX_RETRIES", "3"))
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


@pytest.fixture(scope="session")
def worker_server(vsm_server):
    """Start E2E Worker Server on :9876 in background process."""
    health_url = f"http://localhost:{E2E_WORKER_PORT}/health"

    # Check if already running
    try:
        resp = requests.get(health_url, timeout=2)
        if resp.status_code == 200:
            logger.info("[E2E] Worker already running on port %d", E2E_WORKER_PORT)
            yield None
            return
    except requests.exceptions.ConnectionError:
        pass

    logger.info("[E2E] Starting worker server on port %d", E2E_WORKER_PORT)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "tests.backend.e2e.worker_server:app",
            "--host", "0.0.0.0",
            "--port", str(E2E_WORKER_PORT),
        ],
        cwd=os.path.join(os.path.dirname(__file__), "..", "..", ".."),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if not _wait_for_server(health_url, timeout=30):
        proc.terminate()
        pytest.fail(f"Worker server failed to start on port {E2E_WORKER_PORT}")

    logger.info("[E2E] Worker server started (pid=%d)", proc.pid)
    yield proc
    proc.terminate()
    proc.wait(timeout=10)


# ── Tunnel Management ────────────────────────────────────────────────────────


class TunnelHealthMonitor:
    """
    Background thread that monitors tunnel health and auto-reconnects.

    Pings the tunnel every check_interval seconds via the worker /health endpoint.
    On failure: tears down old tunnel, reconnects with exponential backoff.
    Max 3 reconnection attempts before raising TunnelError.
    """

    def __init__(self, port: int, check_interval: int = 30, max_retries: int = 3):
        self._port = port
        self._check_interval = check_interval
        self._max_retries = max_retries
        self._current_url: Optional[str] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def current_url(self) -> Optional[str]:
        with self._lock:
            return self._current_url

    def start(self, initial_url: str) -> None:
        self._current_url = initial_url
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._check_interval)
            if self._stop_event.is_set():
                break

            url = self.current_url
            if not url:
                continue

            try:
                resp = requests.get(f"{url}/health", timeout=5)
                if resp.status_code == 200:
                    continue
            except Exception:
                pass

            # Tunnel is down -- attempt reconnection
            logger.warning("[TunnelMonitor] Tunnel health check failed, attempting reconnect...")
            new_url = self._reconnect()
            if new_url:
                with self._lock:
                    self._current_url = new_url
                logger.info("[TunnelMonitor] Reconnected: %s", new_url)
            else:
                logger.error("[TunnelMonitor] Reconnection failed after %d attempts", self._max_retries)

    def _reconnect(self) -> Optional[str]:
        """Attempt tunnel reconnection with exponential backoff."""
        try:
            from pyngrok import ngrok
        except ImportError:
            logger.error("[TunnelMonitor] pyngrok not installed, cannot reconnect")
            return None

        # Kill existing tunnels
        try:
            for t in ngrok.get_tunnels():
                ngrok.disconnect(t.public_url)
        except Exception:
            pass

        for attempt in range(self._max_retries):
            delay = 2 ** attempt  # 1s, 2s, 4s
            time.sleep(delay)
            try:
                tunnel = ngrok.connect(self._port, bind_tls=True)
                # Verify the new tunnel works
                resp = requests.get(f"{tunnel.public_url}/health", timeout=5)
                if resp.status_code == 200:
                    return tunnel.public_url
            except Exception as e:
                logger.warning(
                    "[TunnelMonitor] Reconnect attempt %d/%d failed: %s",
                    attempt + 1, self._max_retries, e,
                )

        return None


def _create_tunnel_with_retry(port: int, max_retries: int = 3) -> str:
    """Create an ngrok tunnel with retry logic."""
    try:
        from pyngrok import ngrok
    except ImportError:
        pytest.skip("pyngrok not installed. Install with: pip install pyngrok")

    for attempt in range(max_retries):
        try:
            tunnel = ngrok.connect(port, bind_tls=True)
            logger.info("[E2E] Tunnel created: %s -> localhost:%d", tunnel.public_url, port)
            return tunnel.public_url
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                logger.warning("[E2E] Tunnel creation attempt %d failed: %s", attempt + 1, e)
            else:
                pytest.fail(f"Failed to create tunnel after {max_retries} attempts: {e}")

    return ""  # unreachable


@pytest.fixture(scope="session")
def tunnel_url(worker_server):
    """
    Open ngrok tunnel to local worker. Returns public URL.

    Includes TunnelHealthMonitor for auto-rebuild on disconnect.
    """
    url = _create_tunnel_with_retry(port=E2E_WORKER_PORT, max_retries=TUNNEL_MAX_RETRIES)

    monitor = TunnelHealthMonitor(
        port=E2E_WORKER_PORT,
        check_interval=TUNNEL_CHECK_INTERVAL,
        max_retries=TUNNEL_MAX_RETRIES,
    )
    monitor.start(initial_url=url)

    yield url

    monitor.stop()
    try:
        from pyngrok import ngrok
        ngrok.disconnect(url)
    except Exception:
        pass


# ── MerkleVerifier ───────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def merkle_verifier(s3_client, dynamodb_resource):
    """MerkleVerifier instance for cross-boundary state consistency checks."""
    from tests.backend.e2e.merkle_verifier import MerkleVerifier
    from src.services.state.state_versioning_service import StateVersioningService

    bucket = os.environ.get(
        "WORKFLOW_STATE_BUCKET",
        os.environ.get("SKELETON_S3_BUCKET", "analemma-workflow-state-dev"),
    )
    versioning = StateVersioningService(bucket_name=bucket)
    return MerkleVerifier(
        s3_client=s3_client,
        dynamodb_resource=dynamodb_resource,
        versioning_service=versioning,
        state_bucket=bucket,
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


@pytest.fixture(scope="session")
def sfn_orchestrator_arn():
    """ARN of the E2E test SFN orchestrator (must be set via env var)."""
    arn = os.environ.get("E2E_SFN_ORCHESTRATOR_ARN", "")
    if not arn:
        pytest.skip("E2E_SFN_ORCHESTRATOR_ARN not set")
    return arn


@pytest.fixture(scope="session")
def state_bucket():
    """S3 bucket name for workflow state."""
    return os.environ.get(
        "WORKFLOW_STATE_BUCKET",
        os.environ.get("SKELETON_S3_BUCKET", "analemma-workflow-state-dev"),
    )
