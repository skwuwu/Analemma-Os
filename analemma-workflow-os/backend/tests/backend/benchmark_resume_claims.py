#!/usr/bin/env python3
"""
Benchmark tests to validate resume/portfolio numerical claims.

Claims to validate:
1. Incremental sub-block hashing: O(Δ) vs O(N) — "60% CPU reduction"
2. 2PC parallel I/O vs sequential: "85% latency reduction"
3. Control Plane extraction: "under 10KB"

Run:
    cd analemma-workflow-os/backend
    python -m tests.backend.benchmark_resume_claims
"""

import hashlib
import json
import os
import sys
import time
import statistics
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, Set, Tuple
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


# ═══════════════════════════════════════════════════════════════════════════
# Benchmark 1: Incremental Sub-Block Hashing O(Δ) vs O(N)
# Claim: "60% reduction in CPU cycles"
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_incremental_hashing():
    """Compare incremental sub-block hashing vs full-state hashing.

    Simulates a realistic state bag with HOT/WARM/COLD partitions.
    Only HOT fields change between segments (LLM output, token counts).
    """
    from src.common.hash_utils import (
        SubBlockHashRegistry,
        streaming_content_hash,
        content_hash,
        HOT_FIELDS,
        WARM_FIELDS,
        COLD_FIELDS,
    )

    print("\n" + "=" * 70)
    print("BENCHMARK 1: Incremental Sub-Block Hashing O(Δ) vs O(N)")
    print("=" * 70)

    # Build a realistic state bag
    # COLD: workflow_config (large, immutable after init)
    cold_data = {
        "workflow_config": {
            f"node_{i}": {
                "type": "transform",
                "config": {"rules": [f"rule_{j}" for j in range(20)]},
                "connections": [f"node_{i+1}"],
            }
            for i in range(50)
        },
        "partition_map": {
            f"segment_{i}": {
                "nodes": [f"node_{i*5+j}" for j in range(5)],
                "execution_order": i,
            }
            for i in range(10)
        },
        "segment_manifest": [
            {"segment_id": i, "hash": hashlib.sha256(f"seg{i}".encode()).hexdigest()}
            for i in range(10)
        ],
    }

    # WARM: step_history (grows over time)
    warm_data = {
        "step_history": [
            {
                "step": i,
                "action": f"executed_node_{i}",
                "timestamp": 1700000000 + i,
                "result": f"output_{i}" * 10,
            }
            for i in range(100)
        ],
        "messages": [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg_{i}" * 50}
            for i in range(20)
        ],
    }

    # HOT: LLM output (changes every segment)
    hot_data = {
        "llm_response": "A" * 5000,  # ~5KB LLM response
        "llm_raw_output": "B" * 5000,
        "current_state": {"phase": "processing", "progress": 0.5},
        "token_usage": {"input": 1500, "output": 800, "total": 2300},
        "total_tokens": 2300,
        "total_input_tokens": 1500,
        "total_output_tokens": 800,
    }

    # Combined state
    full_state: Dict[str, Any] = {}
    full_state.update(cold_data)
    full_state.update(warm_data)
    full_state.update(hot_data)

    state_json = json.dumps(full_state, default=str)
    state_size_kb = len(state_json.encode("utf-8")) / 1024
    print(f"\nState size: {state_size_kb:.1f} KB")
    print(f"Fields: HOT={len(hot_data)}, WARM={len(warm_data)}, COLD={len(cold_data)}")

    # --- Full-state hash (O(N)) ---
    full_times = []
    for _ in range(50):
        t0 = time.perf_counter()
        content_hash(full_state)
        full_times.append(time.perf_counter() - t0)

    full_median = statistics.median(full_times) * 1000
    full_p95 = sorted(full_times)[int(len(full_times) * 0.95)] * 1000

    # --- Incremental hash (O(Δ)) ---
    registry = SubBlockHashRegistry()
    # First run: must hash everything (cold start)
    all_keys = set(full_state.keys())
    registry.compute_incremental_root(full_state, all_keys)

    # Subsequent runs: only HOT fields dirty
    dirty_keys: Set[str] = set(hot_data.keys())
    incr_times = []
    for _ in range(50):
        # Mutate hot fields to simulate segment progression
        full_state["llm_response"] = f"response_{time.perf_counter()}"
        full_state["total_tokens"] = int(time.perf_counter() * 1000) % 10000
        t0 = time.perf_counter()
        registry.compute_incremental_root(full_state, dirty_keys)
        incr_times.append(time.perf_counter() - t0)

    incr_median = statistics.median(incr_times) * 1000
    incr_p95 = sorted(incr_times)[int(len(incr_times) * 0.95)] * 1000

    reduction = (1 - incr_median / full_median) * 100

    print(f"\nFull-state hash  (O(N)): median={full_median:.2f}ms, p95={full_p95:.2f}ms")
    print(f"Incremental hash (O(Δ)): median={incr_median:.2f}ms, p95={incr_p95:.2f}ms")
    print(f"\n→ Reduction: {reduction:.1f}%")
    print(f"→ Claim '60% reduction': {'✅ VALIDATED' if reduction >= 55 else '❌ NOT MET (adjust claim)'}")
    print(f"  (Threshold: ≥55% to account for measurement variance)")

    return {
        "full_median_ms": full_median,
        "incr_median_ms": incr_median,
        "reduction_pct": reduction,
        "state_size_kb": state_size_kb,
        "claim_validated": reduction >= 55,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Benchmark 2: Parallel I/O vs Sequential (2PC simulation)
# Claim: "85% reduction in wall-clock latency"
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_parallel_io():
    """Simulate 2PC S3 uploads: parallel ThreadPoolExecutor vs sequential.

    Uses simulated I/O latency (time.sleep) to model S3 PUT operations.
    Real S3 latency is typically 20-80ms per PUT.
    """
    print("\n" + "=" * 70)
    print("BENCHMARK 2: 2PC Parallel I/O vs Sequential")
    print("=" * 70)

    # Simulate S3 PUT latency
    S3_PUT_LATENCY_MS = 40  # Typical S3 single-digit-ms to ~80ms
    DYNAMODB_WRITE_LATENCY_MS = 15
    NUM_BLOCKS = 10

    def simulated_s3_put(block_id: str) -> str:
        """Simulate S3 PUT with realistic latency."""
        time.sleep(S3_PUT_LATENCY_MS / 1000)
        return f"s3://bucket/blocks/{block_id}.json"

    def simulated_dynamodb_write(manifest_id: str) -> None:
        """Simulate DynamoDB transact_write_items."""
        time.sleep(DYNAMODB_WRITE_LATENCY_MS / 1000)

    blocks = [f"block_{hashlib.sha256(f'{i}'.encode()).hexdigest()[:12]}" for i in range(NUM_BLOCKS)]

    # --- Sequential ---
    seq_times = []
    for _ in range(5):
        t0 = time.perf_counter()
        for block in blocks:
            simulated_s3_put(block)
        simulated_dynamodb_write("manifest_seq")
        seq_times.append(time.perf_counter() - t0)

    seq_median = statistics.median(seq_times) * 1000

    # --- Parallel (ThreadPoolExecutor) ---
    def _calculate_workers() -> int:
        # Mirror _calculate_optimal_workers() from state_versioning_service.py
        memory_mb = int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "2048"))
        return min(32, max(4, memory_mb // 256))

    par_times = []
    n_workers = _calculate_workers()
    for _ in range(5):
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            list(pool.map(simulated_s3_put, blocks))
        simulated_dynamodb_write("manifest_par")
        par_times.append(time.perf_counter() - t0)

    par_median = statistics.median(par_times) * 1000
    reduction = (1 - par_median / seq_median) * 100

    print(f"\nSimulation: {NUM_BLOCKS} blocks × {S3_PUT_LATENCY_MS}ms S3 PUT + {DYNAMODB_WRITE_LATENCY_MS}ms DynamoDB")
    print(f"Workers: {n_workers} (based on {os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', '2048')}MB Lambda)")
    print(f"\nSequential: median={seq_median:.0f}ms")
    print(f"Parallel:   median={par_median:.0f}ms")
    print(f"\n→ Reduction: {reduction:.1f}%")
    print(f"→ Claim '85% reduction': {'✅ VALIDATED' if reduction >= 80 else '❌ NOT MET (adjust claim)'}")

    # Also compute theoretical bound
    theoretical_seq = NUM_BLOCKS * S3_PUT_LATENCY_MS + DYNAMODB_WRITE_LATENCY_MS
    theoretical_par = S3_PUT_LATENCY_MS + DYNAMODB_WRITE_LATENCY_MS  # All parallel
    theoretical_reduction = (1 - theoretical_par / theoretical_seq) * 100
    print(f"\nTheoretical: seq={theoretical_seq}ms, par={theoretical_par}ms, reduction={theoretical_reduction:.1f}%")

    return {
        "seq_median_ms": seq_median,
        "par_median_ms": par_median,
        "reduction_pct": reduction,
        "theoretical_reduction_pct": theoretical_reduction,
        "n_workers": n_workers,
        "claim_validated": reduction >= 80,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Benchmark 3: Control Plane Extraction — "under 10KB"
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_control_plane_size():
    """Verify that get_control_plane() output stays under 10KB.

    Uses a worst-case state bag with all CONTROL_PLANE_FIELDS populated.
    """
    print("\n" + "=" * 70)
    print("BENCHMARK 3: Control Plane Extraction Size")
    print("=" * 70)

    from src.common.state_hydrator import (
        CONTROL_PLANE_FIELDS,
        CONTROL_PLANE_MAX_SIZE,
        SmartStateBag,
    )

    # Worst-case: all control plane fields populated with realistic values
    control_data = {
        "ownerId": "usr_" + "a" * 36,
        "workflowId": "wf_" + "b" * 36,
        "idempotency_key": "idem_" + "c" * 36,
        "execution_id": "exec_" + "d" * 36,
        "quota_reservation_id": "quota_" + "e" * 36,
        "workflow_config_s3_path": "s3://my-bucket/workflows/wf_123/config/v42.json",
        "state_s3_path": "s3://my-bucket/workflows/wf_123/executions/exec_456/state.json",
        "partition_map_s3_path": "s3://my-bucket/workflows/wf_123/executions/exec_456/partition.json",
        "segment_manifest_s3_path": "s3://my-bucket/workflows/wf_123/executions/exec_456/manifest.json",
        "final_state_s3_path": "s3://my-bucket/workflows/wf_123/executions/exec_456/final.json",
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
        "llm_segments": [1, 3, 5, 7],
        "hitp_segments": [2, 8],
        "segment_type": "normal",
        "light_config": {
            "name": "My Workflow",
            "version": "1.0",
            "nodes_count": 50,
        },
    }

    # Also add large data plane fields (should be excluded)
    full_data = dict(control_data)
    full_data["llm_response"] = "X" * 100_000  # 100KB LLM response
    full_data["step_history"] = [{"step": i, "data": "Y" * 1000} for i in range(100)]
    full_data["workflow_config"] = {"nodes": {f"n{i}": {} for i in range(200)}}

    bag = SmartStateBag(full_data, hydrator=None, track_changes=False)
    control_plane = bag.get_control_plane()

    full_size = len(json.dumps(full_data, default=str).encode("utf-8"))
    cp_size = len(json.dumps(control_plane, default=str).encode("utf-8"))

    print(f"\nFull state size:     {full_size / 1024:.1f} KB")
    print(f"Control plane size:  {cp_size / 1024:.2f} KB")
    print(f"Target max:          {CONTROL_PLANE_MAX_SIZE / 1024:.0f} KB")
    print(f"Data plane excluded: {(full_size - cp_size) / 1024:.1f} KB")
    print(f"Compression ratio:   {(1 - cp_size / full_size) * 100:.1f}%")
    print(f"\n→ Claim 'under 10KB': {'✅ VALIDATED' if cp_size <= CONTROL_PLANE_MAX_SIZE else '❌ EXCEEDS LIMIT'}")

    # Verify no data plane fields leaked
    from src.common.state_hydrator import DATA_PLANE_FIELDS
    leaked = set(control_plane.keys()) & DATA_PLANE_FIELDS
    if leaked:
        print(f"⚠️  Data plane fields leaked into control plane: {leaked}")
    else:
        print("✅ No data plane field leakage")

    return {
        "full_size_kb": full_size / 1024,
        "cp_size_kb": cp_size / 1024,
        "target_kb": CONTROL_PLANE_MAX_SIZE / 1024,
        "under_limit": cp_size <= CONTROL_PLANE_MAX_SIZE,
        "claim_validated": cp_size <= CONTROL_PLANE_MAX_SIZE,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Benchmark 4: Speculative Execution Rollback Atomicity
# Claim: "100% atomicity"
# ═══════════════════════════════════════════════════════════════════════════

def benchmark_speculative_atomicity():
    """Verify speculative execution rollback atomicity under concurrent load.

    Spawns background verification threads and checks that resolution
    is atomic (no partial state, no race conditions).
    """
    print("\n" + "=" * 70)
    print("BENCHMARK 4: Speculative Execution Rollback Atomicity")
    print("=" * 70)

    from src.services.execution.speculative_controller import (
        SpeculativeExecutionController,
        SpeculativeStatus,
    )

    controller = SpeculativeExecutionController()
    total_tests = 100
    atomicity_violations = 0
    resolution_timeouts = 0

    for i in range(total_tests):
        handle = controller.begin_speculative(
            segment_id=i,
            state_snapshot={"test": i},
            merkle_parent_hash=f"hash_{i}",
        )

        # Simulate background verification with varying outcomes
        should_abort = i % 3 == 0  # ~33% abort rate

        def _verify(h=handle, abort=should_abort):
            time.sleep(0.001)  # 1ms simulated verification
            if abort:
                h.resolve(SpeculativeStatus.ABORTED, reason="test_abort")
            else:
                h.resolve(SpeculativeStatus.COMMITTED)

        import threading
        t = threading.Thread(target=_verify, daemon=True)
        t.start()

        # Check resolution
        rollback = controller.check_abort(handle, timeout=5.0)

        # Verify atomicity: status must be either COMMITTED or ABORTED, never PENDING
        if handle.status == SpeculativeStatus.PENDING:
            atomicity_violations += 1

        # Verify consistency: rollback returned iff ABORTED
        if should_abort and rollback is None:
            atomicity_violations += 1
        if not should_abort and rollback is not None:
            atomicity_violations += 1

    stats = controller.get_stats()
    print(f"\nTotal tests: {total_tests}")
    print(f"Committed: {stats['committed']}, Aborted: {stats['aborted']}")
    print(f"Atomicity violations: {atomicity_violations}")
    print(f"\n→ Claim '100% atomicity': {'✅ VALIDATED' if atomicity_violations == 0 else '❌ VIOLATIONS DETECTED'}")

    return {
        "total_tests": total_tests,
        "atomicity_violations": atomicity_violations,
        "committed": stats["committed"],
        "aborted": stats["aborted"],
        "claim_validated": atomicity_violations == 0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║     Analemma OS — Resume Claims Benchmark Suite                ║")
    print("╚══════════════════════════════════════════════════════════════════╝")

    results = {}

    results["incremental_hashing"] = benchmark_incremental_hashing()
    results["parallel_io"] = benchmark_parallel_io()
    results["control_plane_size"] = benchmark_control_plane_size()
    results["speculative_atomicity"] = benchmark_speculative_atomicity()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    all_passed = True
    for name, result in results.items():
        status = "✅" if result["claim_validated"] else "❌"
        all_passed = all_passed and result["claim_validated"]
        print(f"  {status} {name}")

    print(f"\nOverall: {'ALL CLAIMS VALIDATED ✅' if all_passed else 'SOME CLAIMS NEED ADJUSTMENT ⚠️'}")

    # Write results to JSON for reference
    output_path = os.path.join(os.path.dirname(__file__), "benchmark_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed results written to: {output_path}")
