"""
Pipeline Test Verifier Lambda
==============================
LLM Simulator Step Functions에서 호출됩니다.
실제 프로덕션 스키마 워크플로 실행 결과를 검증합니다.

검증 대상 시나리오:
  COMPLETE            — aiModel 노드 포함 Happy Path 파이프라인
  FAIL                — 에러 처리 파이프라인
  MAP_AGGREGATOR      — 병렬 처리 + 결과 집계 파이프라인
  LOOP_LIMIT          — 동적 루프 제한 파이프라인
  LOOP_BRANCH_STRESS  — 루프 + 분기 + 상태 축적 + S3 오프로드 복합 테스트
  STRESS              — 극한 스트레스 (루프 내부 HITL + 병렬 데이터 레이스)
  VISION              — 멀티모달 비전 (메모리 추정, 인젝션 방어, 상태 오프로드)
  HITP_RECOVERY       — HITP 후 정상 복구 로직 테스트
  ASYNC_LLM           — 비동기 LLM 실행 파이프라인

반환 스키마 (모든 verify_* 함수 공통):
  {
      "passed": bool,
      "scenario": str,
      "checks": [{"name": str, "passed": bool, "details": str}],
      "metrics": {"duration_ms": int, "segments_executed": int}  # 선택
  }
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# S3_LARGE 실제 Offload 임계값 (bytes)
S3_OFFLOAD_SIZE_THRESHOLD = 200_000


# ─── 헬퍼 ────────────────────────────────────────────────────────────────────

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
    """SFN 실행 output에서 final_state 추출"""
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
    🔍 실제 LLM 호출 증거 검증
    
    Returns:
        (has_evidence, details): LLM 호출 증거 여부와 상세 정보
    """
    evidence = []
    
    # 1. llm_raw_output 체크 (가장 확실한 증거)
    if "llm_raw_output" in final_state:
        raw_output = final_state["llm_raw_output"]
        if raw_output and isinstance(raw_output, str) and len(raw_output) > 0:
            evidence.append(f"llm_raw_output present ({len(raw_output)} chars)")
    
    # 2. 토큰 사용량 체크
    total_tokens = final_state.get("total_tokens", 0)
    if total_tokens and total_tokens > 0:
        evidence.append(f"total_tokens={total_tokens}")
    
    # 3. usage 객체 체크
    usage = final_state.get("usage")
    if isinstance(usage, dict):
        input_tok = usage.get("input_tokens", 0)
        output_tok = usage.get("output_tokens", 0)
        if input_tok > 0 or output_tok > 0:
            evidence.append(f"usage={{input:{input_tok},output:{output_tok}}}")
    
    # 4. 노드별 LLM 출력 키 검색 (node_id + "_output" 패턴)
    llm_output_keys = [k for k in final_state.keys() 
                       if k.endswith("_output") or k.endswith("_llm") or k.endswith("_response")]
    if llm_output_keys:
        evidence.append(f"llm_output_keys={llm_output_keys[:3]}")
    
    has_evidence = len(evidence) > 0
    details = "; ".join(evidence) if evidence else "No LLM invocation evidence found"
    
    return has_evidence, details


def _is_mock_mode(final_state: Dict[str, Any]) -> bool:
    """🎭 MOCK_MODE 감지 (테스트 무효화)"""
    return final_state.get("MOCK_MODE") is True or final_state.get("_mock_execution") is True


# ─── 시나리오별 검증기 ────────────────────────────────────────────────────────

def verify_complete(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    COMPLETE: aiModel 노드 포함 Happy Path 파이프라인 검증
    - SFN status = SUCCEEDED
    - final_state에 TEST_RESULT 키 존재
    - 🔍 [강화] 실제 LLM 호출 증거 확인
    - 🎭 [강화] MOCK_MODE 거부
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # 🎭 MOCK_MODE 감지
    is_mock = _is_mock_mode(final)
    checks.append(_check(
        "Not MOCK_MODE (real execution)",
        not is_mock,
        f"MOCK_MODE={is_mock}"
    ))
    
    # 🔍 실제 LLM 호출 증거 검증
    has_llm, llm_details = _has_llm_evidence(final)
    checks.append(_check(
        "LLM invocation evidence detected",
        has_llm,
        llm_details
    ))
    
    # 기존 검증: TEST_RESULT 키 존재
    has_result = "TEST_RESULT" in final
    checks.append(_check(
        "TEST_RESULT key in final_state",
        has_result,
        f"keys={list(final.keys())[:10]}"
    ))

    return _result("COMPLETE", checks)


def verify_fail(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    FAIL: 에러 처리 파이프라인 검증
    - SFN status = FAILED (에러 파이프라인이므로 FAILED가 정상)
    - error_info 전파 확인
    """
    checks = []
    status = test_result.get("status", "")
    failed_as_expected = status == "FAILED"
    checks.append(_check(
        "SFN status FAILED (expected)",
        failed_as_expected,
        f"status={status}"
    ))

    # error_info는 SFN cause/error 필드에 전파됨
    has_error_info = bool(test_result.get("error") or test_result.get("cause"))
    checks.append(_check(
        "error_info propagated",
        has_error_info,
        f"error={test_result.get('error')}, cause={str(test_result.get('cause', ''))[:80]}"
    ))

    return _result("FAIL", checks)


def verify_map_aggregator(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    MAP_AGGREGATOR: 병렬 처리 + 결과 집계 파이프라인 검증
    - SFN status = SUCCEEDED
    - final_state에 병렬 브랜치 집계 결과 키 존재
    - 🔍 [강화] 실제 LLM 호출 증거 확인 (브랜치에 aiModel 포함 시)
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # 🎭 MOCK_MODE 감지 (optional - map 테스트는 LLM 없을 수도 있음)
    is_mock = _is_mock_mode(final)
    if is_mock:
        checks.append(_check(
            "MOCK_MODE status (informational)",
            True,
            f"MOCK_MODE={is_mock} - aggregation test may not require LLM"
        ))
    
    # 기존 검증: 집계 결과 키 존재
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
    LOOP_LIMIT: 동적 루프 제한 파이프라인 검증
    - SFN status = SUCCEEDED
    - loop_count ≤ max_loop_iterations
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
    LOOP_BRANCH_STRESS: Loop + Branch + State Accumulation + S3 Offload Integration Test
    - SFN status = SUCCEEDED
    - Loop completed 5 iterations (loop_counter = 5)
    - Parallel/Sequential branch executions (branch_execution_count = 5)
    - State accumulation exceeds 100KB (accumulated_size_kb > 100)
    - S3 offload triggered (offload_triggered_count > 0)
    - HITP approval and validation passed
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # 루프 완료 검증
    loop_counter = final.get("loop_counter", 0)
    loop_ok = loop_counter == 5
    checks.append(_check(
        "Loop completed (5 iterations)",
        loop_ok,
        f"loop_counter={loop_counter}"
    ))
    
    # 분기 실행 검증
    branch_count = final.get("branch_execution_count", 0)
    branch_ok = branch_count == 5
    checks.append(_check(
        "Branch executions (5 times)",
        branch_ok,
        f"branch_execution_count={branch_count}"
    ))
    
    # 상태 축적 검증 (100KB 초과)
    accumulated_size = final.get("accumulated_size_kb", 0)
    size_ok = accumulated_size > 100
    checks.append(_check(
        "State accumulation exceeded 100KB",
        size_ok,
        f"accumulated_size_kb={accumulated_size:.2f}"
    ))
    
    # S3 오프로드 검증
    offload_count = final.get("offload_triggered_count", 0)
    offload_ok = offload_count > 0
    checks.append(_check(
        "S3 offload triggered",
        offload_ok,
        f"offload_triggered_count={offload_count}"
    ))
    
    # HITP 승인 검증
    hitp_passed = "hitp_checkpoint" in final
    checks.append(_check(
        "HITP checkpoint passed",
        hitp_passed,
        f"hitp_checkpoint present: {hitp_passed}"
    ))
    
    # TEST_RESULT 검증
    test_result_key = "TEST_RESULT" in final
    test_passed = "✅" in str(final.get("TEST_RESULT", ""))
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
    STRESS: 극한 스트레스 테스트 (루프 내부 HITL + 병렬 데이터 레이스)
    - SFN status = SUCCEEDED
    - 중첩 루프 + HITL 복구      verify_complete,
    "FAIL":                verify_fail,
    "MAP_AGGREGATOR":      verify_map_aggregator,
    "LOOP_LIMIT":          verify_loop_limit,
    "LOOP_BRANCH_STRESS":  verify_loop_branch_stress,
    "STRESS":              verify_stress,
    "VISION":              verify_vision,
    "HITP_RECOVERY":       verify_hitp_recovery,
    "S3_LARGE":            verify_s3_large,  # Deprecated, kept for compatibility
    "ASYNC_LLM":     _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # 스트레스 메트릭 추출
    stress_metrics = final.get("stress_metrics", {})
    
    # 루프 포인터 복구 검증
    loop_recoveries = stress_metrics.get("loop_pointer_recoveries", 0)
    recovery_ok = loop_recoveries > 0
    checks.append(_check(
        "Loop pointer recovery after HITL",
        recovery_ok,
        f"loop_pointer_recoveries={loop_recoveries}"
    ))
    
    # 메모리 격리 위반 검증 (없어야 함)
    isolation_violations = stress_metrics.get("isolation_violations", [])
    isolation_ok = len(isolation_violations) == 0
    checks.append(_check(
        "Branch isolation maintained (no violations)",
        isolation_ok,
        f"isolation_violations={len(isolation_violations)}"
    ))
    
    # HITL 내부 루프 실행 확인
    hitl_count = stress_metrics.get("hitl_inside_loop_count", 0)
    hitl_ok = hitl_count > 0
    checks.append(_check(
        "HITL triggered inside loop",
        hitl_ok,
        f"hitl_inside_loop_count={hitl_count}"
    ))
    
    # TEST_RESULT 검증
    test_result_key = "TEST_RESULT" in final
    test_passed = "✅" in str(final.get("TEST_RESULT", ""))
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
    VISION: Multimodal Vision Test
    - SFN status = SUCCEEDED (or SIGKILL if injection detected)
    - Memory estimation engine execution check
    - Visual injection defense check (SIGKILL on detection is normal)
    - State offloading logic check
    """
    checks = []
    status = test_result.get("status", "")
    
    # Vision test may terminate with SIGKILL when injection detected
    # Test expects this, so both SUCCEEDED or FAILED are acceptable
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
    
    # Memory estimation validation
    memory_estimated = validation_checks.get("memory_estimation_executed", False)
    checks.append(_check(
        "Memory estimation engine executed",
        memory_estimated,
        f"memory_estimation_executed={memory_estimated}"
    ))
    
    # Security guard 실행 검증
    security_executed = validation_checks.get("security_guard_executed", False)
    checks.append(_check(
        "Security guard (injection defense) executed",
        security_executed,
        f"security_guard_executed={security_executed}"
    ))
    
    # Visual injection 감지 확인
    injection_detected = validation_checks.get("injection_detected", False)
    sigkill_triggered = validation_checks.get("sigkill_on_injection", False)
    checks.append(_check(
        "Visual injection detected and SIGKILL triggered",
        injection_detected and sigkill_triggered,
        f"injection_detected={injection_detected}, sigkill={sigkill_triggered}"
    ))
    
    # State offloading 로직 실행 확인
    offload_executed = validation_checks.get("offloading_logic_executed", False)
    checks.append(_check(
        "State offloading logic executed",
        offload_executed,
        f"offloading_logic_executed={offload_executed}"
    ))
    
    # 전체 테스트 통과 여부
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
    HITP_RECOVERY: HITP Recovery Logic Test
    - SFN status = SUCCEEDED
    - HITP node execution check
    - Workflow resumption after HITL approval
    - TEST_RESULT presence check
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # HITP 준비 확인
    hitp_prepared = final.get("hitp_prepared", False)
    checks.append(_check(
        "HITP preparation completed",
        hitp_prepared,
        f"hitp_prepared={hitp_prepared}"
    ))
    
    # 승인 결과 확인
    approval_result = final.get("approval_result")
    has_approval = approval_result is not None
    checks.append(_check(
        "HITP approval result present",
        has_approval,
        f"approval_result={approval_result}"
    ))
    
    # TEST_RESULT 검증
    test_result_key = "TEST_RESULT" in final
    test_passed = "✅" in str(final.get("TEST_RESULT", ""))
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
    
    StateBag architecture already auto-offloads data, making simple size tests unnecessary.
    Replaced by LOOP_BRANCH_STRESS which tests loop + branch + state accumulation combination.
    
    This function is kept for backward compatibility and recommends redirecting to LOOP_BRANCH_STRESS.
    """
    checks = []
    checks.append(_check(
        "DEPRECATED SCENARIO",
        False,
        "S3_LARGE is deprecated. Use LOOP_BRANCH_STRESS instead for comprehensive S3 offload testing."
    ))
    return _result("S3_LARGE", checks)


def verify_async_llm(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    ASYNC_LLM: 비동기 LLM 실행 파이프라인 검증
    - SFN status = SUCCEEDED
    - final_state에 async_result 키 존재
    - 🔍 [강화] 실제 LLM 호출 증거 확인
    - ⚠️ [참고] Fargate 비활성화로 동기 폴백 가능
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    
    # 🎭 MOCK_MODE 감지
    is_mock = _is_mock_mode(final)
    checks.append(_check(
        "Not MOCK_MODE (real execution)",
        not is_mock,
        f"MOCK_MODE={is_mock}"
    ))
    
    # 🔍 실제 LLM 호출 증거 검증
    has_llm, llm_details = _has_llm_evidence(final)
    checks.append(_check(
        "LLM invocation evidence detected",
        has_llm,
        llm_details
    ))
    
    # 기존 검증: async 결과 키 존재
    async_keys = {"async_result", "async_llm_result", "llm_result", "TEST_RESULT"}
    found_key = next((k for k in async_keys if k in final), None)
    checks.append(_check(
        "async result key in final_state",
        found_key is not None,
        f"found={found_key}, keys={list(final.keys())[:10]}"
    ))

    return _result("ASYNC_LLM", checks)


# ─── 라우터 ──────────────────────────────────────────────────────────────────

_VERIFIERS = {
    "COMPLETE":       verify_complete,
    "FAIL":           verify_fail,
    "MAP_AGGREGATOR": verify_map_aggregator,
    "LOOP_LIMIT":     verify_loop_limit,
    "S3_LARGE":       verify_s3_large,
    "ASYNC_LLM":      verify_async_llm,
}


# ─── Lambda 핸들러 ────────────────────────────────────────────────────────────

def lambda_handler(event: Dict[str, Any], _context) -> Dict[str, Any]:
    """
    파이프라인 테스트 실행 결과를 검증합니다.

    Input:
        {
            "scenario":    "COMPLETE",
            "test_result": {
                "status":  "SUCCEEDED" | "FAILED",
                "output":  {...},          # SFN ExecutionResult
                "error":   "...",          # FAILED 시
                "cause":   "..."           # FAILED 시
            }
        }

    Output (표준 스키마):
        {
            "passed":   bool,
            "scenario": str,
            "checks":   [{"name": str, "passed": bool, "details": str}],
            "metrics":  {...}   # 선택
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
        f"Verification done: scenario={scenario} passed={result['passed']} "
        f"checks={[c['name'] for c in result['checks'] if not c['passed']]}"
    )

    return result
