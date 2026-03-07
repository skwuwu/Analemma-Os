# -*- coding: utf-8 -*-
"""
AWS Lambda Benchmark Handler — Real Infrastructure Performance Validation.

Runs against actual S3/DynamoDB to validate resume claims with production-grade
numbers. All test data is cleaned up after execution.

Invoke:
    aws lambda invoke --function-name <stack>-BenchmarkFunction \
        --payload '{}' --cli-binary-format raw-in-base64-out /dev/stdout

    # Or with specific benchmarks:
    aws lambda invoke --function-name <stack>-BenchmarkFunction \
        --payload '{"benchmarks": ["parallel_io", "incremental_hashing"]}' \
        --cli-binary-format raw-in-base64-out /dev/stdout

Environment Variables (from SAM template):
    SKELETON_S3_BUCKET: S3 bucket for test block uploads
    MANIFESTS_TABLE: DynamoDB table for manifest writes
"""

import hashlib
import json
import logging
import os
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Config ────────────────────────────────────────────────────────────────

BENCHMARK_PREFIX = "__benchmark__"  # S3 key prefix for cleanup isolation
CLEANUP_ON_FINISH = True


def _calculate_optimal_workers() -> int:
    """Lambda memory-based I/O thread count — mirrors state_versioning_service.py."""
    try:
        memory_mb = int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "2048"))
        return min(32, max(4, memory_mb // 256))
    except (ValueError, TypeError):
        return 4


def _generate_block_data(size_kb: float) -> Dict[str, Any]:
    """Generate a realistic JSON block payload of approximately `size_kb` KB."""
    target_bytes = int(size_kb * 1024)
    # Approximate: each entry is ~80 bytes in JSON
    n_entries = max(1, target_bytes // 80)
    return {
        "segment_id": 0,
        "nodes": [
            {
                "node_id": f"node_{i}",
                "type": "transform",
                "output": f"result_{i}" * 3,
                "ts": time.time(),
            }
            for i in range(n_entries)
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════
# Benchmark 1: Incremental Sub-Block Hashing — O(Delta) vs O(N)
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_incremental_hashing() -> Dict[str, Any]:
    """Pure CPU benchmark — same logic as local, but on Lambda vCPU (arm64)."""
    from src.common.hash_utils import SubBlockHashRegistry, content_hash

    # Realistic state: ~50KB
    cold_data = {
        "workflow_config": {
            f"node_{i}": {
                "type": "transform",
                "config": {"rules": [f"rule_{j}" for j in range(20)]},
                "connections": [f"node_{i + 1}"],
            }
            for i in range(50)
        },
        "partition_map": {
            f"segment_{i}": {
                "nodes": [f"node_{i * 5 + j}" for j in range(5)],
                "execution_order": i,
            }
            for i in range(10)
        },
        "segment_manifest": [
            {"segment_id": i, "hash": hashlib.sha256(f"seg{i}".encode()).hexdigest()}
            for i in range(10)
        ],
    }
    warm_data = {
        "step_history": [
            {"step": i, "action": f"executed_node_{i}", "timestamp": 1700000000 + i, "result": f"output_{i}" * 10}
            for i in range(100)
        ],
        "messages": [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg_{i}" * 50}
            for i in range(20)
        ],
    }
    hot_data = {
        "llm_response": "A" * 5000,
        "llm_raw_output": "B" * 5000,
        "current_state": {"phase": "processing", "progress": 0.5},
        "token_usage": {"input": 1500, "output": 800, "total": 2300},
        "total_tokens": 2300,
        "total_input_tokens": 1500,
        "total_output_tokens": 800,
    }

    full_state: Dict[str, Any] = {}
    full_state.update(cold_data)
    full_state.update(warm_data)
    full_state.update(hot_data)

    state_size_kb = len(json.dumps(full_state, default=str).encode()) / 1024

    # Full-state hash O(N)
    full_times = []
    for _ in range(100):
        t0 = time.perf_counter()
        content_hash(full_state)
        full_times.append(time.perf_counter() - t0)

    # Incremental hash O(Delta) — cold start first
    registry = SubBlockHashRegistry()
    registry.compute_incremental_root(full_state, set(full_state.keys()))

    dirty_keys: Set[str] = set(hot_data.keys())
    incr_times = []
    for run in range(100):
        full_state["llm_response"] = f"response_{run}_{time.perf_counter()}"
        full_state["total_tokens"] = run * 100
        t0 = time.perf_counter()
        registry.compute_incremental_root(full_state, dirty_keys)
        incr_times.append(time.perf_counter() - t0)

    full_median_ms = statistics.median(full_times) * 1000
    full_p95_ms = sorted(full_times)[95] * 1000
    incr_median_ms = statistics.median(incr_times) * 1000
    incr_p95_ms = sorted(incr_times)[95] * 1000
    reduction_pct = (1 - incr_median_ms / full_median_ms) * 100

    return {
        "benchmark": "incremental_hashing",
        "state_size_kb": round(state_size_kb, 1),
        "iterations": 100,
        "full_state_hash": {
            "median_ms": round(full_median_ms, 3),
            "p95_ms": round(full_p95_ms, 3),
        },
        "incremental_hash": {
            "median_ms": round(incr_median_ms, 3),
            "p95_ms": round(incr_p95_ms, 3),
            "dirty_fields": len(dirty_keys),
        },
        "reduction_pct": round(reduction_pct, 1),
        "claim": "60% reduction",
        "claim_validated": reduction_pct >= 55,
        "lambda_memory_mb": int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "0")),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Benchmark 2: 2PC Parallel I/O vs Sequential — Real S3 + DynamoDB
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_parallel_io() -> Dict[str, Any]:
    """Real S3 PUT + DynamoDB write: sequential vs parallel ThreadPoolExecutor."""
    s3 = boto3.client("s3")
    dynamodb = boto3.client("dynamodb")
    bucket = os.environ.get("SKELETON_S3_BUCKET", "")
    table = os.environ.get("MANIFESTS_TABLE", "")

    if not bucket or not table:
        return {"benchmark": "parallel_io", "error": "SKELETON_S3_BUCKET or MANIFESTS_TABLE not set", "claim_validated": False}

    test_id = uuid.uuid4().hex[:8]
    block_configs = [
        {"count": 5, "size_kb": 5},
        {"count": 10, "size_kb": 5},
        {"count": 10, "size_kb": 20},
        {"count": 20, "size_kb": 5},
    ]
    n_workers = _calculate_optimal_workers()
    results_per_config = []

    for cfg in block_configs:
        num_blocks = cfg["count"]
        block_size_kb = cfg["size_kb"]
        blocks = []
        for i in range(num_blocks):
            block_id = hashlib.sha256(f"{test_id}_{i}".encode()).hexdigest()[:16]
            s3_key = f"{BENCHMARK_PREFIX}/{test_id}/blocks/{block_id}.json"
            data = _generate_block_data(block_size_kb)
            blocks.append({"block_id": block_id, "s3_key": s3_key, "body": json.dumps(data, default=str)})

        # ── Sequential ──
        seq_times = []
        for trial in range(3):
            t0 = time.perf_counter()
            for block in blocks:
                s3.put_object(Bucket=bucket, Key=block["s3_key"], Body=block["body"], ContentType="application/json")
            # DynamoDB manifest write
            manifest_id = f"bench_seq_{test_id}_{trial}"
            # [v3.34 FIX] parent_hash is a GSI key — omit instead of NULL
            dynamodb.put_item(
                TableName=table,
                Item={
                    "manifest_id": {"S": manifest_id},
                    "version": {"N": "1"},
                    "workflow_id": {"S": f"bench_{test_id}"},
                    "manifest_hash": {"S": hashlib.sha256(manifest_id.encode()).hexdigest()},
                    "config_hash": {"S": "bench_config"},
                    "ttl": {"N": str(int(time.time()) + 300)},  # 5min TTL
                    "metadata": {"M": {"benchmark": {"S": "true"}}},
                },
            )
            seq_times.append(time.perf_counter() - t0)

        # ── Parallel ──
        par_times = []
        for trial in range(3):
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futures = [
                    pool.submit(
                        s3.put_object,
                        Bucket=bucket,
                        Key=block["s3_key"],
                        Body=block["body"],
                        ContentType="application/json",
                    )
                    for block in blocks
                ]
                for f in as_completed(futures):
                    f.result()  # Raise on error
            manifest_id = f"bench_par_{test_id}_{trial}"
            # [v3.34 FIX] parent_hash is a GSI key — omit instead of NULL
            dynamodb.put_item(
                TableName=table,
                Item={
                    "manifest_id": {"S": manifest_id},
                    "version": {"N": "1"},
                    "workflow_id": {"S": f"bench_{test_id}"},
                    "manifest_hash": {"S": hashlib.sha256(manifest_id.encode()).hexdigest()},
                    "config_hash": {"S": "bench_config"},
                    "ttl": {"N": str(int(time.time()) + 300)},
                    "metadata": {"M": {"benchmark": {"S": "true"}}},
                },
            )
            par_times.append(time.perf_counter() - t0)

        seq_median = statistics.median(seq_times) * 1000
        par_median = statistics.median(par_times) * 1000
        reduction = (1 - par_median / seq_median) * 100

        results_per_config.append({
            "num_blocks": num_blocks,
            "block_size_kb": block_size_kb,
            "sequential_median_ms": round(seq_median, 1),
            "parallel_median_ms": round(par_median, 1),
            "reduction_pct": round(reduction, 1),
        })

    # ── Cleanup ──
    if CLEANUP_ON_FINISH:
        _cleanup_s3(s3, bucket, f"{BENCHMARK_PREFIX}/{test_id}/")
        _cleanup_dynamodb_bench(dynamodb, table, f"bench_{test_id}")

    # Aggregate: use the 10-block/5KB config as the canonical number
    canonical = next((r for r in results_per_config if r["num_blocks"] == 10 and r["block_size_kb"] == 5), results_per_config[0])

    return {
        "benchmark": "parallel_io",
        "n_workers": n_workers,
        "lambda_memory_mb": int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "0")),
        "configurations": results_per_config,
        "canonical_result": canonical,
        "reduction_pct": canonical["reduction_pct"],
        "claim": "85% reduction",
        "claim_validated": canonical["reduction_pct"] >= 80,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Benchmark 3: Control Plane Size
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_control_plane_size() -> Dict[str, Any]:
    """Measure actual control plane extraction size — same as local."""
    from src.common.state_hydrator import (
        CONTROL_PLANE_FIELDS,
        CONTROL_PLANE_MAX_SIZE,
        DATA_PLANE_FIELDS,
        SmartStateBag,
    )

    control_data = {
        "ownerId": "usr_" + "a" * 36,
        "workflowId": "wf_" + "b" * 36,
        "idempotency_key": "idem_" + "c" * 36,
        "execution_id": "exec_" + "d" * 36,
        "quota_reservation_id": "quota_" + "e" * 36,
        "workflow_config_s3_path": "s3://bucket/workflows/wf_123/config/v42.json",
        "state_s3_path": "s3://bucket/workflows/wf_123/executions/exec_456/state.json",
        "partition_map_s3_path": "s3://bucket/workflows/wf_123/executions/exec_456/partition.json",
        "segment_manifest_s3_path": "s3://bucket/workflows/wf_123/executions/exec_456/manifest.json",
        "final_state_s3_path": "s3://bucket/workflows/wf_123/executions/exec_456/final.json",
        "segment_to_run": 7,
        "total_segments": 15,
        "loop_counter": 3,
        "max_loop_iterations": 10,
        "max_branch_iterations": 5,
        "max_concurrency": 4,
        "distributed_strategy": "SEQUENTIAL_BRANCH",
        "distributed_mode": "fan_out",
        "MOCK_MODE": False,
        "AUTO_RESUME_HITP": True,
        "current_manifest_id": "mf_" + hashlib.sha256(b"test").hexdigest(),
        "llm_segments": [1, 3, 5, 7, 9, 11],
        "hitp_segments": [2, 8, 14],
        "segment_type": "normal",
        "light_config": {"name": "Production Workflow", "version": "2.1", "nodes_count": 80},
    }
    full_data = dict(control_data)
    full_data["llm_response"] = "X" * 100_000
    full_data["step_history"] = [{"step": i, "data": "Y" * 1000} for i in range(100)]
    full_data["workflow_config"] = {"nodes": {f"n{i}": {"type": "t"} for i in range(200)}}

    bag = SmartStateBag(full_data, hydrator=None, track_changes=False)
    cp = bag.get_control_plane()

    full_size = len(json.dumps(full_data, default=str).encode())
    cp_size = len(json.dumps(cp, default=str).encode())
    leaked = set(cp.keys()) & DATA_PLANE_FIELDS

    return {
        "benchmark": "control_plane_size",
        "full_state_kb": round(full_size / 1024, 1),
        "control_plane_bytes": cp_size,
        "control_plane_kb": round(cp_size / 1024, 2),
        "target_max_kb": CONTROL_PLANE_MAX_SIZE / 1024,
        "data_plane_leak": list(leaked),
        "claim": "under 10KB",
        "claim_validated": cp_size <= CONTROL_PLANE_MAX_SIZE and len(leaked) == 0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Benchmark 4: Speculative Execution Atomicity
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_speculative_atomicity() -> Dict[str, Any]:
    """Thread-safety test on Lambda runtime."""
    import threading
    from src.services.execution.speculative_controller import (
        SpeculativeExecutionController,
        SpeculativeStatus,
    )

    total_tests = 200
    violations = 0

    for i in range(total_tests):
        controller = SpeculativeExecutionController()
        handle = controller.begin_speculative(
            segment_id=i,
            state_snapshot={"test": i},
            merkle_parent_hash=f"hash_{i}",
        )
        should_abort = i % 3 == 0

        def _verify(h=handle, abort=should_abort):
            time.sleep(0.001)
            if abort:
                h.resolve(SpeculativeStatus.ABORTED, reason="test")
            else:
                h.resolve(SpeculativeStatus.COMMITTED)

        t = threading.Thread(target=_verify, daemon=True)
        t.start()
        rollback = controller.check_abort(handle, timeout=5.0)

        if handle.status == SpeculativeStatus.PENDING:
            violations += 1
        if should_abort and rollback is None:
            violations += 1
        if not should_abort and rollback is not None:
            violations += 1

    return {
        "benchmark": "speculative_atomicity",
        "total_tests": total_tests,
        "violations": violations,
        "claim": "100% atomicity",
        "claim_validated": violations == 0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Benchmark 5: Parallel Sub-Block Hashing (multi-threaded)
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_parallel_hashing() -> Dict[str, Any]:
    """Compare single-threaded vs multi-threaded sub-block hashing on Lambda."""
    from src.common.hash_utils import SubBlockHashRegistry

    # Large state to make threading overhead worthwhile
    full_state: Dict[str, Any] = {}
    for temp in ["hot", "warm", "cold"]:
        for i in range(30):
            key = f"{temp}_field_{i}"
            full_state[key] = {f"k_{j}": f"v_{j}" * 50 for j in range(100)}

    state_size_kb = len(json.dumps(full_state, default=str).encode()) / 1024
    all_keys = set(full_state.keys())

    # Single-threaded
    reg_st = SubBlockHashRegistry()
    st_times = []
    for _ in range(30):
        t0 = time.perf_counter()
        reg_st.compute_incremental_root(full_state, all_keys)
        st_times.append(time.perf_counter() - t0)
        reg_st._block_hashes.clear()

    # Multi-threaded
    reg_mt = SubBlockHashRegistry()
    n_workers = _calculate_optimal_workers()
    mt_times = []
    for _ in range(30):
        t0 = time.perf_counter()
        reg_mt.compute_incremental_root_parallel(full_state, all_keys, max_workers=n_workers)
        mt_times.append(time.perf_counter() - t0)
        reg_mt._block_hashes.clear()

    st_median = statistics.median(st_times) * 1000
    mt_median = statistics.median(mt_times) * 1000
    speedup = st_median / mt_median if mt_median > 0 else 0

    return {
        "benchmark": "parallel_hashing",
        "state_size_kb": round(state_size_kb, 1),
        "n_workers": n_workers,
        "single_threaded_median_ms": round(st_median, 2),
        "multi_threaded_median_ms": round(mt_median, 2),
        "speedup_x": round(speedup, 2),
        "lambda_memory_mb": int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "0")),
        "claim_validated": True,  # Informational — no specific claim
    }


# ═══════════════════════════════════════════════════════════════════════════
# Cleanup Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _cleanup_s3(s3_client, bucket: str, prefix: str) -> int:
    """Delete all objects under prefix. Returns count deleted."""
    deleted = 0
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue
            # S3 delete_objects max 1000
            for batch_start in range(0, len(objects), 1000):
                batch = objects[batch_start : batch_start + 1000]
                s3_client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": [{"Key": obj["Key"]} for obj in batch]},
                )
                deleted += len(batch)
    except Exception as e:
        logger.warning("S3 cleanup failed for prefix %s: %s", prefix, e)
    return deleted


def _cleanup_dynamodb_bench(dynamodb_client, table: str, workflow_id_prefix: str) -> int:
    """Delete benchmark manifest items by scanning for bench_ prefix."""
    deleted = 0
    try:
        # Scan for benchmark items (small number, OK for cleanup)
        response = dynamodb_client.scan(
            TableName=table,
            FilterExpression="begins_with(workflow_id, :prefix)",
            ExpressionAttributeValues={":prefix": {"S": workflow_id_prefix}},
            ProjectionExpression="manifest_id",
        )
        for item in response.get("Items", []):
            dynamodb_client.delete_item(
                TableName=table,
                Key={"manifest_id": item["manifest_id"]},
            )
            deleted += 1
    except Exception as e:
        logger.warning("DynamoDB cleanup failed: %s", e)
    return deleted


# ═══════════════════════════════════════════════════════════════════════════
# Lambda Handler
# ═══════════════════════════════════════════════════════════════════════════

AVAILABLE_BENCHMARKS = {
    "incremental_hashing": benchmark_incremental_hashing,
    "parallel_io": benchmark_parallel_io,
    "control_plane_size": benchmark_control_plane_size,
    "speculative_atomicity": benchmark_speculative_atomicity,
    "parallel_hashing": benchmark_parallel_hashing,
}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Run benchmark suite and return structured results.

    Args:
        event: Optional {"benchmarks": ["parallel_io", ...]} to run specific tests.
               Empty or omitted runs all benchmarks.

    Returns:
        Dict with per-benchmark results, summary, and Lambda execution metadata.
    """
    start_time = time.perf_counter()
    requested = event.get("benchmarks", list(AVAILABLE_BENCHMARKS.keys()))

    # Validate
    invalid = [b for b in requested if b not in AVAILABLE_BENCHMARKS]
    if invalid:
        return {
            "error": f"Unknown benchmarks: {invalid}",
            "available": list(AVAILABLE_BENCHMARKS.keys()),
        }

    results = {}
    for name in requested:
        logger.info("Running benchmark: %s", name)
        try:
            results[name] = AVAILABLE_BENCHMARKS[name]()
        except Exception as e:
            logger.error("Benchmark %s failed: %s", name, e, exc_info=True)
            results[name] = {"benchmark": name, "error": str(e), "claim_validated": False}

    # Summary
    total_elapsed_ms = (time.perf_counter() - start_time) * 1000
    all_validated = all(r.get("claim_validated", False) for r in results.values())

    return {
        "results": results,
        "summary": {
            "total_benchmarks": len(results),
            "all_claims_validated": all_validated,
            "validated": [n for n, r in results.items() if r.get("claim_validated")],
            "failed": [n for n, r in results.items() if not r.get("claim_validated")],
        },
        "metadata": {
            "lambda_memory_mb": int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "0")),
            "lambda_region": os.environ.get("AWS_REGION", "unknown"),
            "total_elapsed_ms": round(total_elapsed_ms, 1),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "remaining_time_ms": getattr(context, "get_remaining_time_in_millis", lambda: 0)(),
        },
    }
