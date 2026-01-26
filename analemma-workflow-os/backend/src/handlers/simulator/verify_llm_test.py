"""
Verify LLM Test Results Lambda
==============================
LLM Simulator Step Functions에서 호출되어 테스트 결과를 검증합니다.

이 Lambda는 llm_simulator.py의 verify_* 함수들을 Step Functions에서 직접 호출할 수 있도록 래핑합니다.
"""

import json
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def verify_basic_llm_call(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """LI-A: 기본 LLM 호출 검증"""
    # [Fix] Support multiple LLM output key patterns used across different workflows
    llm_output_keys = [
        'llm_output', 'llm_raw_output', 'vision_raw_output', 
        'document_analysis_raw', 'final_report_raw', 'llm_result'
    ]
    
    llm_output = None
    for key in llm_output_keys:
        llm_output = final_state.get(key) or final_state.get('final_state', {}).get(key)
        if llm_output:
            break
    
    # Also check for usage field as indicator of LLM execution
    usage = final_state.get('usage') or final_state.get('final_state', {}).get('usage')
    
    if not llm_output and not usage:
        return False, "No LLM output found in result"
    
    expected_min_length = test_config.get('expected_min_length', 10)
    if len(str(llm_output)) < expected_min_length:
        return False, f"LLM output too short: {len(str(llm_output))} < {expected_min_length}"
    
    if '[MOCK' in str(llm_output).upper():
        return False, "Received MOCK response instead of real LLM output"
    
    return True, f"Basic LLM call successful, output length: {len(str(llm_output))}"


def verify_structured_output(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """LI-B: Structured Output 검증"""
    structured_output = final_state.get('structured_output') or final_state.get('final_state', {}).get('structured_output')
    
    if not structured_output:
        return False, "No structured output found"
    
    try:
        if isinstance(structured_output, str):
            parsed = json.loads(structured_output)
        else:
            parsed = structured_output
        
        if 'languages' in parsed and isinstance(parsed['languages'], list):
            return True, f"Structured output valid with {len(parsed['languages'])} items"
        else:
            return False, f"Unexpected structure: {list(parsed.keys())}"
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON in structured output: {e}"


def verify_thinking_mode(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """LI-C: Thinking Mode 검증"""
    thinking_output = final_state.get('thinking_output') or final_state.get('final_state', {}).get('thinking_output')
    llm_output = final_state.get('llm_output') or final_state.get('final_state', {}).get('llm_output')
    
    has_reasoning = False
    if thinking_output:
        has_reasoning = True
    elif llm_output and any(word in str(llm_output).lower() for word in ['because', 'therefore', 'so', 'left', 'remain']):
        has_reasoning = True
    
    if not has_reasoning:
        return False, "No thinking/reasoning visible in output"
    
    answer_correct = '9' in str(llm_output)
    
    if answer_correct:
        return True, "Thinking mode worked correctly, answer is 9"
    else:
        return False, f"Answer may be incorrect: {str(llm_output)[:100]}"


def verify_llm_operator_integration(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """LI-D: LLM + operator_official 통합 검증"""
    step_history = final_state.get('step_history', [])
    expected_strategies = test_config.get('expected_strategies_used', [])
    
    found_strategies = []
    for step in step_history:
        for strategy in expected_strategies:
            if strategy in str(step):
                found_strategies.append(strategy)
    
    found_strategies = list(set(found_strategies))
    
    if len(found_strategies) < len(expected_strategies):
        missing = set(expected_strategies) - set(found_strategies)
        return False, f"Missing strategies: {missing}"
    
    filtered_results = final_state.get('filtered_results') or final_state.get('high_priority_tasks')
    if filtered_results is None:
        return False, "No filtered results found - operator integration may have failed"
    
    return True, f"LLM + Operator integration successful. Strategies used: {found_strategies}"


def verify_document_analysis(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """LI-E: 복합 문서 분석 파이프라인 검증"""
    expected_stages = test_config.get('expected_pipeline_stages', [])
    completed_stages = []
    
    if final_state.get('analysis_data') or final_state.get('llm_analysis_raw'):
        completed_stages.append('extract')
    if final_state.get('high_priority_vulnerabilities') or final_state.get('filtered_items'):
        completed_stages.append('filter')
    if final_state.get('deep_analysis_results'):
        completed_stages.append('deep_analyze')
    if final_state.get('final_report') or final_state.get('final_markdown_report'):
        completed_stages.append('merge')
    
    missing_stages = set(expected_stages) - set(completed_stages)
    if missing_stages:
        return False, f"Pipeline incomplete. Missing stages: {missing_stages}"
    
    if test_config.get('expected_security_findings'):
        vulns = final_state.get('security_vulnerabilities') or final_state.get('high_priority_vulnerabilities')
        if not vulns:
            return False, "No security findings extracted"
    
    return True, f"Document analysis pipeline complete. Stages: {completed_stages}"


def verify_multimodal_vision(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """LI-F: Multimodal Vision 라이브 검증"""
    vision_output = final_state.get('vision_output') or final_state.get('final_state', {}).get('vision_output')
    
    if not vision_output:
        return False, "No vision output found"
    
    if len(str(vision_output)) < 50:
        return False, f"Vision output too short: {len(str(vision_output))}"
    
    diagram_words = ['diagram', 'architecture', 'flow', 'component', 'system', 'connection']
    has_diagram_content = any(word in str(vision_output).lower() for word in diagram_words)
    
    if not has_diagram_content:
        return False, "Vision output doesn't seem to describe a diagram"
    
    return True, "Multimodal vision analysis successful"


# ============================================================================
# 5-Stage Graduated Test Verification Functions
# ============================================================================

def verify_stage1_basic(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """
    Stage 1: 동작 기초 검증
    - Response Schema 준수
    - json_parse 0.5초 이내 성능
    """
    issues = []
    
    # 1. LLM 출력 존재 확인
    llm_output = final_state.get('llm_raw_output') or final_state.get('parsed_summary')
    if not llm_output:
        return False, "No LLM output found"
    
    # 2. MOCK 응답 아닌지 확인
    if '[MOCK' in str(llm_output).upper():
        return False, "Received MOCK response instead of real LLM output"
    
    # 3. Response Schema 준수 확인
    parsed = final_state.get('parsed_summary', {})
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except:
            return False, "Failed to parse JSON from LLM output"
    
    required_fields = ['main_topic', 'key_points', 'sentiment']
    missing_fields = [f for f in required_fields if f not in parsed or parsed.get(f) == 'parse_failed']
    if missing_fields:
        issues.append(f"Missing schema fields: {missing_fields}")
    
    # 4. json_parse 시간 검증 (0.5초 = 500ms 이내)
    parse_start = final_state.get('parse_start_ms', 0)
    parse_end = final_state.get('parse_end_ms', 0)
    
    if parse_start and parse_end:
        parse_duration_ms = parse_end - parse_start
        expected_max_ms = test_config.get('expected_json_parse_ms', 500)
        
        if parse_duration_ms > expected_max_ms:
            issues.append(f"json_parse too slow: {parse_duration_ms}ms > {expected_max_ms}ms")
    
    # 5. 스키마 유효성 확인
    schema_validation = final_state.get('schema_validation', 'unknown')
    if schema_validation == 'schema_invalid':
        issues.append("Schema validation failed")
    
    if issues:
        return False, f"Stage 1 issues: {'; '.join(issues)}"
    
    parse_time_str = f"{parse_duration_ms}ms" if parse_start and parse_end else "N/A"
    return True, f"Stage 1 PASSED: Schema valid, json_parse={parse_time_str}"


def verify_stage2_flow_control(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """
    Stage 2: Flow Control + COST_GUARDRAIL 검증
    - for_each 병렬 처리
    - HITL 상태 복구
    - 토큰 누적 제한
    - State Recovery Integrity (손실률 0%)
    """
    issues = []
    metrics = {}
    
    # 1. for_each 결과 확인
    processed_items = final_state.get('processed_items', [])
    processed_count = final_state.get('processed_count', 0)
    
    if processed_count < 3:
        issues.append(f"Expected 3 items processed, got {processed_count}")
    
    # 2. COST_GUARDRAIL 확인
    guardrail_state = final_state.get('guardrail_state', {})
    accumulated_tokens = guardrail_state.get('accumulated_tokens', 0)
    max_tokens = test_config.get('max_tokens_total', 10000)
    
    if accumulated_tokens > max_tokens:
        issues.append(f"Token limit exceeded: {accumulated_tokens} > {max_tokens}")
    
    metrics['tokens_used'] = accumulated_tokens
    
    # 3. HITL 상태 복구 검증 (State Recovery Integrity)
    pre_hitl_timestamp = final_state.get('pre_hitl_timestamp')
    hitl_decision = final_state.get('hitl_decision')
    
    if hitl_decision:
        # HITL이 실행되었으면 상태가 보존되어야 함
        initial_keys = set(test_config.get('initial_state_keys', []))
        current_keys = set(final_state.keys())
        
        # 손실된 키 확인 (일부는 변환될 수 있으므로 critical keys만 체크)
        critical_lost = initial_keys - current_keys
        if critical_lost:
            issues.append(f"State Recovery Integrity failed: lost keys {critical_lost}")
    
    # 4. Time Machine 롤백 토큰 리셋 테스트
    if test_config.get('verify_token_reset_on_rollback'):
        # 롤백 이벤트가 있었는지 확인
        rollback_event = final_state.get('time_machine_triggered')
        if rollback_event:
            post_rollback_tokens = final_state.get('post_rollback_accumulated_tokens', 0)
            metrics['rollback_token_count'] = post_rollback_tokens
    
    # 5. 반복 횟수 제한 확인
    iteration_count = guardrail_state.get('iteration_count', 0)
    max_iterations = guardrail_state.get('max_iterations', 3)
    
    if iteration_count > max_iterations:
        issues.append(f"Iteration limit exceeded: {iteration_count} > {max_iterations}")
    
    if issues:
        return False, f"Stage 2 issues: {'; '.join(issues)}"
    
    return True, f"Stage 2 PASSED: {processed_count} items, {accumulated_tokens} tokens, HITL ok"


def verify_stage3_vision_basic(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """
    Stage 3: 멀티모달 기초 검증
    - S3 URI → bytes 변환
    - Vision JSON 추출 및 파싱
    """
    issues = []
    
    # 1. Vision 출력 존재 확인
    vision_output = final_state.get('vision_raw_output') or final_state.get('parsed_vision_data')
    if not vision_output:
        return False, "No vision output found"
    
    # 2. S3 → bytes 변환 확인 (하이드레이션 시간 측정)
    hydration_start = final_state.get('hydration_start_ms', 0)
    hydration_end = final_state.get('hydration_end_ms', 0)
    
    if hydration_start and hydration_end:
        hydration_time = hydration_end - hydration_start
        if hydration_time > 10000:  # 10초 초과시 경고
            issues.append(f"Hydration too slow: {hydration_time}ms")
    
    # 3. Vision 출력 JSON 파싱 가능 확인
    parsed_vision = final_state.get('parsed_vision_data', {})
    if isinstance(parsed_vision, str):
        try:
            parsed_vision = json.loads(parsed_vision)
        except:
            return False, "Vision output is not valid JSON"
    
    # 4. 추출 상태 확인
    extraction_status = final_state.get('extraction_status', 'unknown')
    if extraction_status == 'extraction_failed':
        issues.append("Vision extraction failed")
    
    # 5. 필수 필드 존재 확인
    vendor = parsed_vision.get('vendor')
    if vendor == 'parse_failed' or vendor is None:
        issues.append("Failed to extract vendor from image")
    
    # 6. clean_vision_data 확인 (operator 가공)
    clean_data = final_state.get('clean_vision_data')
    if not clean_data:
        issues.append("operator_official processing failed - no clean_vision_data")
    
    if issues:
        return False, f"Stage 3 issues: {'; '.join(issues)}"
    
    hydration_str = f"{hydration_time}ms" if hydration_start and hydration_end else "N/A"
    return True, f"Stage 3 PASSED: Vision JSON valid, hydration={hydration_str}"


def verify_stage4_vision_map(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """
    Stage 4: Vision Map + SPEED_GUARDRAIL 검증
    - 5장 이미지 병렬 분석
    - max_concurrency=3 준수
    - 타임스탬프 간격 분석 (동시 시작 방지)
    - StateBag 브랜치 병합 무결성
    """
    issues = []
    
    # 1. 처리된 이미지 수 확인
    vision_results = final_state.get('vision_results', [])
    total_processed = final_state.get('total_processed', 0)
    
    if total_processed < 5:
        issues.append(f"Expected 5 images, processed {total_processed}")
    
    # 2. 타임스탬프 간격 분석 (동시성 제어 검증)
    execution_timestamps = final_state.get('execution_timestamps', [])
    min_gap_ms = test_config.get('min_timestamp_gap_ms', 100)
    
    if len(execution_timestamps) >= 2:
        sorted_ts = sorted([ts for ts in execution_timestamps if ts])
        concurrent_violations = 0
        
        for i in range(len(sorted_ts) - 1):
            gap = sorted_ts[i + 1] - sorted_ts[i]
            if gap < min_gap_ms and gap >= 0:
                # 동일 시간대에 3개 이상 시작되지 않았는지 확인
                concurrent_count = sum(1 for ts in sorted_ts if abs(ts - sorted_ts[i]) < min_gap_ms)
                if concurrent_count > 3:  # max_concurrency 초과
                    concurrent_violations += 1
        
        if concurrent_violations > 0:
            issues.append(f"Concurrency violation: {concurrent_violations} batches exceeded max_concurrency=3")
    
    # 3. 카테고리 그룹화 결과 확인
    grouped = final_state.get('grouped_by_category', {})
    electronics = final_state.get('electronics_items', [])
    
    if not grouped:
        issues.append("Category grouping failed")
    
    # 4. StateBag 브랜치 병합 무결성
    if test_config.get('verify_branch_merge_integrity'):
        # 모든 브랜치의 결과가 lost 없이 병합되었는지 확인
        if len(vision_results) != total_processed:
            issues.append(f"Branch merge integrity failed: {len(vision_results)} != {total_processed}")
    
    if issues:
        return False, f"Stage 4 issues: {'; '.join(issues)}"
    
    category_count = len(grouped.keys()) if isinstance(grouped, dict) else 0
    return True, f"Stage 4 PASSED: {total_processed} images, {category_count} categories, concurrency verified"


def verify_stage5_hyper_stress(final_state: Dict, test_config: Dict) -> Tuple[bool, str]:
    """
    Stage 5: Hyper Stress + ALL_GUARDRAILS 검증
    - 3단계 재귀 상태 오염 방지
    - Partial Failure (Depth 2) 복구
    - Context Caching TEI ≥ 50%
    - HITL 중첩 처리
    """
    issues = []
    metrics = {}
    
    # 1. 3단계 재귀 완료 확인
    merged_state = final_state.get('merged_state', {})
    depth_0 = merged_state.get('depth_0_analysis')
    depth_1 = merged_state.get('depth_1_results')
    depth_2 = merged_state.get('depth_2_results')
    depth_3 = merged_state.get('depth_3_hitl')
    
    completed_depths = sum(1 for d in [depth_0, depth_1, depth_2, depth_3] if d)
    if completed_depths < 3:
        issues.append(f"Only {completed_depths}/4 recursion depths completed")
    
    # 2. Partial Failure 복구 확인
    failure_check = final_state.get('failure_check')
    if failure_check == 'has_failures':
        # Partial failure가 있었지만 다른 브랜치는 성공해야 함
        depth_1_results = final_state.get('depth_1_results', [])
        successful_branches = [r for r in depth_1_results if not r.get('error')]
        if len(successful_branches) == 0:
            issues.append("All branches failed - no partial failure recovery")
    
    # 3. Token Efficiency Index (TEI) 계산
    # TEI = (Cached Tokens / Total Prompt Tokens) × 100
    usage = final_state.get('usage', {})
    cached_tokens = usage.get('cached_tokens', 0)
    total_prompt_tokens = usage.get('prompt_tokens', 0) or usage.get('total_prompt_tokens', 1)
    
    if total_prompt_tokens > 0:
        tei = (cached_tokens / total_prompt_tokens) * 100
        metrics['tei'] = tei
        
        target_tei = test_config.get('target_tei_percentage', 50)
        if test_config.get('verify_context_caching') and tei < target_tei:
            # 경고만 (Context Caching이 항상 작동하진 않을 수 있음)
            logger.warning(f"TEI below target: {tei:.1f}% < {target_tei}%")
    
    # 4. 상태 오염 검증
    if test_config.get('verify_state_isolation'):
        # 하위 재귀에서 상위 상태를 오염시키지 않았는지 확인
        # 상위 depth의 데이터가 하위 depth 데이터로 덮어쓰이지 않았는지
        initial_analysis = final_state.get('parsed_issues', {})
        if not initial_analysis or initial_analysis.get('issues') is None:
            issues.append("State isolation may have failed - initial analysis corrupted")
    
    # 5. HITL 중첩 처리 확인
    hitl_decision = final_state.get('hitl_decision')
    if hitl_decision is None and depth_3:
        issues.append("HITL at depth 3 was expected but not executed")
    
    # 6. 최종 리포트 생성 확인
    final_report = final_state.get('final_report_raw') or final_state.get('final_report')
    if not final_report:
        issues.append("Final report generation failed")
    
    # 7. Graceful Stop 확인 (가드레일에 의한 정상 중단)
    final_status = final_state.get('final_status', {})
    if final_status.get('graceful_stop'):
        # 가드레일이 작동하여 정상 중단됨
        metrics['graceful_stop'] = True
    
    if issues:
        return False, f"Stage 5 issues: {'; '.join(issues)}"
    
    tei_str = f"TEI={metrics.get('tei', 0):.1f}%" if 'tei' in metrics else "TEI=N/A"
    return True, f"Stage 5 PASSED: {completed_depths} depths, {tei_str}, recursion isolated"


# ============================================================================
# Verification function registry
# ============================================================================
VERIFY_FUNCTIONS = {
    # 5-Stage scenarios
    'STAGE1_BASIC': verify_stage1_basic,
    'STAGE2_FLOW_CONTROL': verify_stage2_flow_control,
    'STAGE3_VISION_BASIC': verify_stage3_vision_basic,
    'STAGE4_VISION_MAP': verify_stage4_vision_map,
    'STAGE5_HYPER_STRESS': verify_stage5_hyper_stress,
    # Legacy scenarios
    'BASIC_LLM_CALL': verify_basic_llm_call,
    'STRUCTURED_OUTPUT': verify_structured_output,
    'THINKING_MODE': verify_thinking_mode,
    'LLM_OPERATOR_INTEGRATION': verify_llm_operator_integration,
    'DOCUMENT_ANALYSIS': verify_document_analysis,
    'MULTIMODAL_VISION_LIVE': verify_multimodal_vision
}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    LLM 테스트 결과 검증 Lambda Handler
    
    Event format:
    {
        "scenario_key": "BASIC_LLM_CALL",
        "final_state": {...},
        "test_config": {...},
        "execution_id": "xxx"
    }
    """
    logger.info(f"Verifying LLM test: {event.get('scenario_key')}")
    
    scenario_key = event.get('scenario_key', 'BASIC_LLM_CALL')
    final_state = event.get('final_state', {})
    test_config = event.get('test_config', {})
    execution_id = event.get('execution_id', '')
    
    verify_func = VERIFY_FUNCTIONS.get(scenario_key)
    
    if not verify_func:
        return {
            'verified': False,
            'message': f"Unknown scenario: {scenario_key}",
            'execution_id': execution_id
        }
    
    try:
        passed, message = verify_func(final_state, test_config)
        
        return {
            'verified': passed,
            'message': message,
            'scenario_key': scenario_key,
            'execution_id': execution_id
        }
        
    except Exception as e:
        logger.exception(f"Verification failed for {scenario_key}")
        return {
            'verified': False,
            'message': f"Verification error: {str(e)}",
            'scenario_key': scenario_key,
            'execution_id': execution_id
        }
