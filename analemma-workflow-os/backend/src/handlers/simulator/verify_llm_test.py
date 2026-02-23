"""
Pipeline Test Verifier Lambda
==============================
LLM Simulator Step Functions에서 호출됩니다.
실제 프로덕션 스키마 워크플로 실행 결과를 검증합니다.

검증 대상 시나리오:
  COMPLETE       — aiModel 노드 포함 Happy Path 파이프라인
  FAIL           — 에러 처리 파이프라인
  MAP_AGGREGATOR — 병렬 처리 + 결과 집계 파이프라인
  LOOP_LIMIT     — 동적 루프 제한 파이프라인
  S3_LARGE       — S3 Offload 파이프라인 (300KB+ 페이로드)
  ASYNC_LLM      — 비동기 LLM 실행 파이프라인

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


# ─── 시나리오별 검증기 ────────────────────────────────────────────────────────

def verify_complete(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    COMPLETE: aiModel 노드 포함 Happy Path 파이프라인 검증
    - SFN status = SUCCEEDED
    - final_state에 TEST_RESULT 키 존재
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
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
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    # 병렬 집계 결과 — aggregated_results 또는 map_results 키 확인
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


def verify_s3_large(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    S3_LARGE: S3 Offload 파이프라인 검증 (실제 Offload 감사)
    - SFN status = SUCCEEDED
    - state_s3_path 키 존재 (실제 Offload 발생)
    - state_size_bytes > 200,000 (256KB 초과 페이로드 확인)
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
    offloaded = "state_s3_path" in final
    size_bytes = final.get("state_size_bytes", 0)
    s3_path = final.get("state_s3_path", "")

    checks.append(_check(
        "S3 Offload occurred (state_s3_path present)",
        offloaded,
        f"state_s3_path={s3_path[:80] if s3_path else 'absent'}"
    ))

    size_ok = size_bytes > S3_OFFLOAD_SIZE_THRESHOLD
    checks.append(_check(
        f"payload size exceeds threshold ({S3_OFFLOAD_SIZE_THRESHOLD:,} bytes)",
        size_ok,
        f"state_size_bytes={size_bytes:,} (threshold={S3_OFFLOAD_SIZE_THRESHOLD:,})"
    ))

    metrics = {"state_size_bytes": size_bytes, "offloaded": offloaded}
    return _result("S3_LARGE", checks, metrics)


def verify_async_llm(test_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    ASYNC_LLM: 비동기 LLM 실행 파이프라인 검증
    - SFN status = SUCCEEDED
    - final_state에 async_result 키 존재
    """
    checks = []
    succeeded = _sfn_succeeded(test_result)
    checks.append(_check(
        "SFN status SUCCEEDED",
        succeeded,
        f"status={test_result.get('status')}"
    ))

    final = _final_state(test_result)
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
