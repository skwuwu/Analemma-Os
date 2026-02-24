"""
Pipeline Test Verifier Lambda
==============================
Invoked by LLM Simulator Step Functions to validate workflow execution results.
Tests production schema workflows with actual LLM invocations.

Test Scenarios:
  COMPLETE            - Happy path pipeline with aiModel nodes
  FAIL                - Error handling pipeline
  MAP_AGGREGATOR      - Parallel processing + result aggregation
  LOOP_LIMIT          - Dynamic loop limit enforcement
  LOOP_BRANCH_STRESS  - Loop + branch + state accumulation + S3 offload integration
  STRESS              - Extreme stress (HITL inside loop + parallel data races)
  VISION              - Multimodal vision (memory estimation, injection defense, state offload)
  HITP_RECOVERY       - HITP recovery logic after human approval
  ASYNC_LLM           - Async LLM execution pipeline

Return Schema (all verify_* functions):
  {
      "passed": bool,
      "scenario": str,
      "checks": [{"name": str, "passed": bool, "details": str}],
      "metrics": {...}  # optional
  }
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# S3 offload size threshold (bytes)
S3_OFFLOAD_SIZE_THRESHOLD = 200_000


# Helper Functions

def _check(name: str, passed: bool, details: str) -> Dict[str, Any]:
    return {"name": name, "passed": passed, "details": details}


def _result(scenario: str, checks: List[Dict], metrics: Dict = None) -> Dict[str, Any]:
    passed = all(c["passed"] for c in checks)
    out = {"passed": passed, "scenario": scenario, "checks": checks}
    if metrics:
        out["metrics"] = metrics
    return out


def _sfn_succeeded(test_result: Dict[str, Any]) -> bool:
    return test_result.get("status") == "SUCCEEDED"


def _final_state(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract final_state from SFN execution output"""
    output = test_result.get("output", {})
    if isinstance(output, str):
        import json
        try:
            output = json.loads(output)
        except Exception:
            return {}
    return output.get("final_state", output) if isinstance(output, dict) else {}


def _has_llm_evidence(final_state: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validate actual LLM invocation evidence.
    
    LLM Simulator always performs real LLM calls - never mocked.
    This function detects multiple evidence types to prevent false positives.
    
    Returns:
        (has_evidence, details): Tuple of evidence presence and detailed information
    """
    evidence = []
    
    # 1. llm_raw_output check (strongest evidence)
    if "llm_raw_output" in final_state:
        raw_output = final_state["llm_raw_output"]
        if raw_output and isinstance(raw_output, str) and len(raw_output) > 0:
            evidence.append(f"llm_raw_output present ({len(raw_output)} chars)")
    
    # 2. Token usage check
    total_tokens = final_state.get("total_tokens", 0)
    if total_tokens and total_tokens > 0:
        evidence.append(f"total_tokens={total_tokens}")
    
    # 3. usage object check
    usage = final_state.get("usage")
    if isinstance(usage, dict):
        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        if input_tok > 0 or output_tok > 0:
            evidence.append(f"usage={{input:{input_tok},output:{output_tok}}}")
    
    # 4. LLM output key patterns (node_id + "_output", etc.)
    llm_output_keys = [k for k in final_state.keys() 
                       if k.endswith("_output") or k.endswith("_llm") or k.endswith("_response")]
    if llm_output_keys:
        evidence.append(f"llm_output_keys={llm_output_keys[:3]}")
    
    has_evidence = len(evidence) > 0
    details = "; ".join(evidence) if evidence else "No LLM invocation evidence found"
    
    return has_evidence, details


# Scenario Verifiers

def verify_complete(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    COMPLETE: Happy path pipeline with aiModel nodes
    - SFN status = SUCCEEDED
    - TEST_RESULT key exists in final_state
    - Actual LLM invocation evidence detected
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # Validate actual LLM invocation evidence
    has_llm, llm_details = _has_llm_evidence(final)
    checks.append(_check(
        "LLM invocation evidence detected",
        has_llm,
        llm_details
    ))
    
    # Validate TEST_RESULT key presence
    has_result = "TEST_RESULT" in final
    checks.append(_check(
        "TEST_RESULT key in final_state",
        has_result,
        f"keys={list(final.keys())[:10]}"
    ))

    return _result("COMPLETE", checks)


def verify_fail(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    FAIL: Error handling pipeline validation
    - SFN status = FAILED (expected for error pipeline)
    - error_info propagated to output
    """
    checks = []
    status = test_result.get("status", "")
    failed_as_expected = status == "FAILED"
    checks.append(_check(
        "SFN status FAILED (expected)",
        failed_as_expected,
        f"status={status}"
    ))

    # error_info propagated to SFN cause/error fields
    has_error_info = bool(test_result.get("error") or test_result.get("cause"))
    checks.append(_check(
        "error_info propagated",
        has_error_info,
        f"error={test_result.get('error')}, cause={str(test_result.get('cause', ''))[:80]}"
    ))

    return _result("FAIL", checks)


def verify_map_aggregator(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    MAP_AGGREGATOR: Parallel processing + result aggregation pipeline
    - SFN status = SUCCEEDED
    - Aggregation result key exists in final_state
    - Note: May not have LLM invocation if no aiModel nodes in branches
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # Validate aggregation result key presence
    aggregation_keys = {"aggregated_results", "map_results", "parallel_results", "TEST_RESULT"}
    found_key = next((k for k in aggregation_keys if k in final), None)
    checks.append(_check(
        "aggregation result key in final_state",
        found_key is not None,
        f"found={found_key}, keys={list(final.keys())[:10]}"
    ))

    return _result("MAP_AGGREGATOR", checks)


def verify_loop_limit(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    LOOP_LIMIT: Dynamic loop limit enforcement
    - SFN status = SUCCEEDED
    - loop_count <= max_loop_iterations
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    loop_count = final.get("loop_count", 0)
    max_iterations = final.get("max_loop_iterations", final.get("loop_limit", 10))
    within_limit = loop_count <= max_iterations
    checks.append(_check(
        "loop_count within limit",
        within_limit,
        f"loop_count={loop_count}, max={max_iterations}"
    ))

    metrics = {"loop_count": loop_count, "max_iterations": max_iterations}
    return _result("LOOP_LIMIT", checks, metrics)


def verify_loop_branch_stress(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    LOOP_BRANCH_STRESS: Loop + Branch + State Accumulation + S3 Offload
    Comprehensive integration test combining:
    - Loop iterations (5 expected)
    - Parallel/Sequential branch executions (5 expected)
    - State accumulation exceeding 100KB
    - S3 offload trigger validation
    - HITP checkpoint approval
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # Validate loop completion (5 iterations)
    loop_counter = final.get("loop_counter", 0)
    loop_ok = loop_counter == 5
    checks.append(_check(
        "Loop completed (5 iterations)",
        loop_ok,
        f"loop_counter={loop_counter}"
    ))
    
    # Validate branch executions (5 times)
    branch_count = final.get("branch_execution_count", 0)
    branch_ok = branch_count == 5
    checks.append(_check(
        "Branch executions (5 times)",
        branch_ok,
        f"branch_execution_count={branch_count}"
    ))
    
    # Validate state accumulation exceeds 100KB
    accumulated_size = final.get("accumulated_size_kb", 0)
    size_ok = accumulated_size > 100
    checks.append(_check(
        "State accumulation exceeded 100KB",
        size_ok,
        f"accumulated_size_kb={accumulated_size:.2f}"
    ))
    
    # Validate S3 offload triggered
    offload_count = final.get("offload_triggered_count", 0)
    offload_ok = offload_count > 0
    checks.append(_check(
        "S3 offload triggered",
        offload_ok,
        f"offload_triggered_count={offload_count}"
    ))
    
    # Validate HITP checkpoint passed
    hitp_passed = "hitp_checkpoint" in final
    checks.append(_check(
        "HITP checkpoint passed",
        hitp_passed,
        f"hitp_checkpoint present: {hitp_passed}"
    ))
    
    # Validate TEST_RESULT indicates success
    test_result_key = "TEST_RESULT" in final
    test_passed = final.get("TEST_RESULT", "").find("PASS") >= 0 or final.get("TEST_RESULT", "").find("SUCCESS") >= 0
    checks.append(_check(
        "TEST_RESULT indicates success",
        test_result_key and test_passed,
        f"TEST_RESULT={final.get('TEST_RESULT', 'absent')[:100]}"
    ))
    
    metrics = {
        "loop_iterations": loop_counter,
        "branch_executions": branch_count,
        "accumulated_size_kb": accumulated_size,
        "offload_count": offload_count
    }
    return _result("LOOP_BRANCH_STRESS", checks, metrics)


def verify_stress(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    STRESS: Extreme stress test (HITL inside loop + parallel data races)
    - SFN status = SUCCEEDED
    - Nested loop + HITL recovery
    - Loop pointer recovery after HITL completion
    - Branch isolation maintained (no data races)
    - HITL triggered inside loop at least once
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # Extract stress metrics
    stress_metrics = final.get("stress_metrics", {})
    
    # Validate loop pointer recovery after HITL
    loop_recoveries = stress_metrics.get("loop_pointer_recoveries", 0)
    recovery_ok = loop_recoveries > 0
    checks.append(_check(
        "Loop pointer recovery after HITL",
        recovery_ok,
        f"loop_pointer_recoveries={loop_recoveries}"
    ))
    
    # Validate branch isolation (no memory violations)
    isolation_violations = stress_metrics.get("isolation_violations", [])
    isolation_ok = len(isolation_violations) == 0
    checks.append(_check(
        "Branch isolation maintained (no violations)",
        isolation_ok,
        f"isolation_violations={len(isolation_violations)}"
    ))
    
    # Validate HITL triggered inside loop
    hitl_count = stress_metrics.get("hitl_inside_loop_count", 0)
    hitl_ok = hitl_count > 0
    checks.append(_check(
        "HITL triggered inside loop",
        hitl_ok,
        f"hitl_inside_loop_count={hitl_count}"
    ))
    
    # Validate TEST_RESULT indicates success
    test_result_key = "TEST_RESULT" in final
    test_passed = final.get("TEST_RESULT", "").find("PASS") >= 0 or final.get("TEST_RESULT", "").find("SUCCESS") >= 0
    checks.append(_check(
        "TEST_RESULT indicates success",
        test_result_key and test_passed,
        f"TEST_RESULT={final.get('TEST_RESULT', 'absent')[:100]}"
    ))
    
    metrics = {
        "loop_pointer_recoveries": loop_recoveries,
        "isolation_violations_count": len(isolation_violations),
        "hitl_inside_loop_count": hitl_count
    }
    return _result("STRESS", checks, metrics)


def verify_vision(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    VISION: Multimodal vision test
    - SFN status = SUCCEEDED or FAILED (SIGKILL on injection is normal)
    - Memory estimation engine execution
    - Visual injection defense (SIGKILL trigger)
    - State offloading logic execution
    """
    checks = []
    status = test_result.get("status", "")
    
    # Vision test may terminate with SIGKILL when injection detected
    # Both SUCCEEDED and FAILED are acceptable outcomes
    succeeded = status in ["SUCCEEDED", "FAILED"]
    checks.append(_check(
        "SFN status (SUCCEEDED or FAILED due to SIGKILL)",
        succeeded,
        f"status={status}"
    ))

    final = _final_state(test_result)
    
    # Extract vision test results
    vision_result = final.get("vision_os_test_result", {})
    validation_checks = vision_result.get("validation_checks", {})
    
    # Validate memory estimation engine execution
    memory_estimated = validation_checks.get("memory_estimation_executed", False)
    checks.append(_check(
        "Memory estimation engine executed",
        memory_estimated,
        f"memory_estimation_executed={memory_estimated}"
    ))
    
    # Validate security guard (injection defense) execution
    security_executed = validation_checks.get("security_guard_executed", False)
    checks.append(_check(
        "Security guard (injection defense) executed",
        security_executed,
        f"security_guard_executed={security_executed}"
    ))
    
    # Validate visual injection detection and SIGKILL trigger
    injection_detected = validation_checks.get("injection_detected", False)
    sigkill_triggered = validation_checks.get("sigkill_on_injection", False)
    checks.append(_check(
        "Visual injection detected and SIGKILL triggered",
        injection_detected and sigkill_triggered,
        f"injection_detected={injection_detected}, sigkill={sigkill_triggered}"
    ))
    
    # Validate state offloading logic execution
    offload_executed = validation_checks.get("offloading_logic_executed", False)
    checks.append(_check(
        "State offloading logic executed",
        offload_executed,
        f"offloading_logic_executed={offload_executed}"
    ))
    
    # Validate overall vision OS test passed
    test_passed = vision_result.get("test_passed", False)
    checks.append(_check(
        "Overall vision OS test passed",
        test_passed,
        f"test_passed={test_passed}"
    ))
    
    metrics = {
        "memory_estimation": memory_estimated,
        "injection_detected": injection_detected,
        "sigkill_triggered": sigkill_triggered,
        "offload_executed": offload_executed
    }
    return _result("VISION", checks, metrics)


def verify_hitp_recovery(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    HITP_RECOVERY: HITP recovery logic test
    - SFN status = SUCCEEDED
    - HITP node execution validated
    - Workflow resumption after HITL approval
    - TEST_RESULT presence validated
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # Validate HITP preparation completed
    hitp_prepared = final.get("hitp_prepared", False)
    checks.append(_check(
        "HITP preparation completed",
        hitp_prepared,
        f"hitp_prepared={hitp_prepared}"
    ))
    
    # Validate approval result present
    approval_result = final.get("approval_result")
    has_approval = approval_result is not None
    checks.append(_check(
        "HITP approval result present",
        has_approval,
        f"approval_result={approval_result}"
    ))
    
    # Validate TEST_RESULT indicates success
    test_result_key = "TEST_RESULT" in final
    test_passed = final.get("TEST_RESULT", "").find("PASS") >= 0 or final.get("TEST_RESULT", "").find("SUCCESS") >= 0
    checks.append(_check(
        "TEST_RESULT indicates success",
        test_result_key and test_passed,
        f"TEST_RESULT={final.get('TEST_RESULT', 'absent')[:100]}"
    ))
    
    metrics = {
        "hitp_prepared": hitp_prepared,
        "has_approval_result": has_approval
    }
    return _result("HITP_RECOVERY", checks, metrics)


def verify_s3_large(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    DEPRECATED: S3_LARGE is replaced by LOOP_BRANCH_STRESS
    
    StateBag architecture automatically offloads large data to S3,
    making simple size tests unnecessary. Use LOOP_BRANCH_STRESS instead
    for comprehensive testing of loop + branch + state accumulation + S3 offload.
    
    This function is kept for backward compatibility only.
    """
    checks = []
    checks.append(_check(
        "DEPRECATED SCENARIO",
        False,
        "S3_LARGE is deprecated. Use LOOP_BRANCH_STRESS for comprehensive S3 offload testing."
    ))
    return _result("S3_LARGE", checks)


def verify_async_llm(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    ASYNC_LLM: Async LLM execution pipeline validation
    - SFN status = SUCCEEDED
    - async_result key exists in final_state
    - Actual LLM invocation evidence detected
    - Note: Fargate may be disabled, sync fallback is acceptable
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # Validate actual LLM invocation evidence
    has_llm, llm_details = _has_llm_evidence(final)
    checks.append(_check(
        "LLM invocation evidence detected",
        has_llm,
        llm_details
    ))
    
    # Validate async result key exists
    async_keys = {"async_result", "async_llm_result", "llm_result", "TEST_RESULT"}
    found_key = next((k for k in async_keys if k in final), None)
    checks.append(_check(
        "async result key in final_state",
        found_key is not None,
        f"found={found_key}, keys={list(final.keys())[:10]}"
    ))

    return _result("ASYNC_LLM", checks)


# Verifier Router

_VERIFIERS = {
    "COMPLETE":            verify_complete,
    "FAIL":                verify_fail,
    "MAP_AGGREGATOR":      verify_map_aggregator,
    "LOOP_LIMIT":          verify_loop_limit,
    "LOOP_BRANCH_STRESS":  verify_loop_branch_stress,
    "STRESS":              verify_stress,
    "VISION":              verify_vision,
    "HITP_RECOVERY":       verify_hitp_recovery,
    "S3_LARGE":            verify_s3_large,  # Deprecated
    "ASYNC_LLM":           verify_async_llm,
}


# Lambda Handler

def lambda_handler(event: Dict[str, Any], _context) -> Dict[str, Any]:
    """
    Validate pipeline test execution results.

    Input Schema:
        {
            "scenario":    "COMPLETE",
            "test_result": {
                "status":  "SUCCEEDED" | "FAILED",
                "output":  {...},          # SFN ExecutionResult
                "error":   "...",          # when FAILED
                "cause":   "..."           # when FAILED
            }
        }

    Output Schema:
        {
            "passed":   bool,
            "scenario": str,
            "checks":   [{"name": str, "passed": bool, "details": str}],
            "metrics":  {...}   # optional
        }
    """
    scenario = event.get("scenario", "COMPLETE")
    test_result = event.get("test_result", {})

    logger.info(f"Verifying pipeline test: scenario={scenario}, status={test_result.get('status')}")

    verifier = _VERIFIERS.get(scenario)
    if verifier is None:
        return {
            "passed": False,
            "scenario": scenario,
            "checks": [_check(
                "known scenario",
                False,
                f"Unknown scenario '{scenario}'. Valid: {list(_VERIFIERS.keys())}"
            )],
        }

    result = verifier(test_result)

    logger.info(
        f"Verification completed: scenario={scenario} passed={result['passed']} "
        f"failed_checks={[c['name'] for c in result['checks'] if not c['passed']]}"
    )

    return result
