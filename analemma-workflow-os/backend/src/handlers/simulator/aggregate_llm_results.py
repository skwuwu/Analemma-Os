"""
LLM Test Results Aggregation Lambda
여러 LLM 테스트 결과를 집계하고 CloudWatch 메트릭을 발행합니다.
"""

import json
import logging
import boto3
import os
from typing import Dict, Any, List
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

METRIC_NAMESPACE = os.environ.get('METRIC_NAMESPACE', 'Analemma/LLMSimulator')


def _extract_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lambda invoke 응답에서 실제 Payload를 추출합니다.
    
    Map 상태의 결과는 Lambda invoke 응답 형식일 수 있음:
    { "ExecutedVersion": "$LATEST", "Payload": {...}, "StatusCode": 200 }
    """
    if not isinstance(result, dict):
        return {}
    
    # Lambda invoke 응답 형식인 경우 Payload 추출
    if 'Payload' in result and 'StatusCode' in result:
        payload = result.get('Payload', {})
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                return {}
        return payload if isinstance(payload, dict) else {}
    
    # 이미 직접 결과 형식인 경우
    return result


def _extract_thinking_logs(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    테스트 결과에서 Thinking Mode 로그를 추출합니다.
    
    Args:
        result: 테스트 결과 딕셔너리
    
    Returns:
        Thinking 로그 리스트 [{"step": 1, "thought": "...", "reasoning": "..."}]
    """
    thinking_logs = []
    
    try:
        test_result = result.get('test_result', {})
        final_state = test_result.get('output', test_result)
        
        if isinstance(final_state, str):
            try:
                final_state = json.loads(final_state)
            except:
                final_state = {}
        
        # State에서 thinking 관련 키 찾기
        # 패턴: {node_id}_thinking, thinking_output, llm_thinking 등
        for key, value in final_state.items():
            if 'thinking' in key.lower() and isinstance(value, (list, dict)):
                if isinstance(value, list):
                    thinking_logs.extend(value)
                elif isinstance(value, dict):
                    thinking_logs.append(value)
        
        # metadata에서도 확인
        if isinstance(final_state, dict):
            for key in final_state.keys():
                if key.endswith('_meta'):
                    meta = final_state[key]
                    if isinstance(meta, dict) and 'thinking' in meta:
                        thinking_data = meta['thinking']
                        if isinstance(thinking_data, list):
                            thinking_logs.extend(thinking_data)
                        elif isinstance(thinking_data, dict):
                            thinking_logs.append(thinking_data)
    
    except Exception as e:
        logger.warning(f"Failed to extract thinking logs: {e}")
    
    return thinking_logs


def _extract_context_info(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    테스트 결과에서 Context 정보를 추출합니다.
    
    Args:
        result: 테스트 결과 딕셔너리
    
    Returns:
        Context 정보 {"size_tokens": int, "cached_tokens": int, "cache_hit_rate": float}
    """
    context_info = {
        "size_tokens": 0,
        "cached_tokens": 0,
        "cache_hit_rate": 0.0,
        "estimated_size_kb": 0
    }
    
    try:
        test_result = result.get('test_result', {})
        final_state = test_result.get('output', test_result)
        
        if isinstance(final_state, str):
            try:
                final_state = json.loads(final_state)
            except:
                final_state = {}
        
        # Usage에서 토큰 정보 추출
        usage = final_state.get('usage') or final_state.get('final_state', {}).get('usage', {})
        
        input_tokens = usage.get('input_tokens', 0)
        cached_tokens = usage.get('cached_tokens', 0)
        
        context_info['size_tokens'] = input_tokens
        context_info['cached_tokens'] = cached_tokens
        
        if input_tokens > 0:
            context_info['cache_hit_rate'] = round((cached_tokens / input_tokens) * 100, 2)
        
        # 대략적인 크기 추정 (1 token ≈ 4 bytes)
        context_info['estimated_size_kb'] = round((input_tokens * 4) / 1024, 2)
    
    except Exception as e:
        logger.warning(f"Failed to extract context info: {e}")
    
    return context_info


def _generate_comprehensive_report(
    results: List[Dict[str, Any]],
    scenarios: Dict[str, Any]
) -> Dict[str, Any]:
    """
    종합 리포트 생성: 시나리오별 usage, context, thinking 로그를 집계합니다.
    
    Args:
        results: 전체 테스트 결과 리스트
        scenarios: 시나리오별 결과 딕셔너리
    
    Returns:
        종합 리포트 딕셔너리
    """
    comprehensive_report = {
        "scenario_details": {},
        "aggregate_statistics": {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cached_tokens": 0,
            "total_cost_usd": 0.0,
            "total_cost_saved_usd": 0.0,
            "average_cache_hit_rate": 0.0,
            "total_thinking_steps": 0,
            # 🛡️ [Payload Optimization] 검증 결과 요약 추가
            "total_passed": 0,
            "total_failed": 0
        },
        # 🛡️ [Payload Optimization] thinking_logs 전체 저장 제거 - count만 유지
        "thinking_summary": {},  # {scenario: count} only
        "context_analysis": {}
    }
    
    total_scenarios_with_cache = 0
    total_cache_hit_rate = 0.0
    
    for result in results:
        scenario = result.get('scenario', 'unknown')
        
        # Thinking 로그 추출 - count만 저장
        thinking_logs = _extract_thinking_logs(result)
        if thinking_logs:
            # 🛡️ [Payload Optimization] logs 전체 대신 count만 저장
            comprehensive_report['thinking_summary'][scenario] = len(thinking_logs)
            comprehensive_report['aggregate_statistics']['total_thinking_steps'] += len(thinking_logs)
        
        # Context 정보 추출
        context_info = _extract_context_info(result)
        comprehensive_report['context_analysis'][scenario] = context_info
        
        # Usage 통계 집계
        scenario_data = scenarios.get(scenario, {})
        usage = scenario_data.get('usage', {})
        
        comprehensive_report['aggregate_statistics']['total_input_tokens'] += usage.get('input_tokens', 0)
        comprehensive_report['aggregate_statistics']['total_output_tokens'] += usage.get('output_tokens', 0)
        comprehensive_report['aggregate_statistics']['total_cached_tokens'] += usage.get('cached_tokens', 0)
        comprehensive_report['aggregate_statistics']['total_cost_usd'] += usage.get('estimated_cost_usd', 0.0)
        comprehensive_report['aggregate_statistics']['total_cost_saved_usd'] += usage.get('cost_saved_usd', 0.0)
        
        # 🛡️ [Payload Optimization] 검증 결과 집계
        status = scenario_data.get('status', 'UNKNOWN')
        if status == 'PASSED':
            comprehensive_report['aggregate_statistics']['total_passed'] += 1
        elif status == 'FAILED':
            comprehensive_report['aggregate_statistics']['total_failed'] += 1
        
        # Cache hit rate 평균 계산
        if context_info['cache_hit_rate'] > 0:
            total_cache_hit_rate += context_info['cache_hit_rate']
            total_scenarios_with_cache += 1
        
        # 🛡️ [Payload Optimization] 시나리오별 상세 - 검증 결과 통합, 중복 제거
        verification_summary = scenario_data.get('verification_summary', {})
        comprehensive_report['scenario_details'][scenario] = {
            "status": status,
            "passed": verification_summary.get('passed', status == 'PASSED'),
            "checks": verification_summary.get('checks', []),
            "failure_reason": verification_summary.get('failure_reason'),
            "usage": {
                "input_tokens": usage.get('input_tokens', 0),
                "output_tokens": usage.get('output_tokens', 0),
                "cached_tokens": usage.get('cached_tokens', 0),
                "estimated_cost_usd": usage.get('estimated_cost_usd', 0.0)
            },
            "cache_hit_rate": context_info.get('cache_hit_rate', 0.0),
            "thinking_steps": len(thinking_logs) if thinking_logs else 0,
            "provider": usage.get('provider', 'unknown'),
            "outcome_url": scenario_data.get('outcome_url')
        }
    
    # 평균 cache hit rate 계산
    if total_scenarios_with_cache > 0:
        comprehensive_report['aggregate_statistics']['average_cache_hit_rate'] = round(
            total_cache_hit_rate / total_scenarios_with_cache, 2
        )
    
    # 비용 절감률 계산
    total_cost = comprehensive_report['aggregate_statistics']['total_cost_usd']
    total_saved = comprehensive_report['aggregate_statistics']['total_cost_saved_usd']
    if total_cost > 0:
        comprehensive_report['aggregate_statistics']['cost_reduction_percentage'] = round(
            (total_saved / (total_cost + total_saved)) * 100, 2
        )
    else:
        comprehensive_report['aggregate_statistics']['cost_reduction_percentage'] = 0.0
    
    return comprehensive_report


def _publish_metrics(results: List[Dict[str, Any]], passed_count: int, total: int):
    """CloudWatch 메트릭을 발행합니다."""
    try:
        cw = boto3.client('cloudwatch')
        
        metrics = [
            {
                'MetricName': 'LLMTestsPassed',
                'Value': passed_count,
                'Unit': 'Count',
                'Timestamp': datetime.now(timezone.utc)
            },
            {
                'MetricName': 'LLMTestsFailed',
                'Value': total - passed_count,
                'Unit': 'Count',
                'Timestamp': datetime.now(timezone.utc)
            },
            {
                'MetricName': 'LLMTestsTotal',
                'Value': total,
                'Unit': 'Count',
                'Timestamp': datetime.now(timezone.utc)
            },
            {
                'MetricName': 'LLMTestPassRate',
                'Value': (passed_count / total * 100) if total > 0 else 0,
                'Unit': 'Percent',
                'Timestamp': datetime.now(timezone.utc)
            }
        ]
        
        # 시나리오별 메트릭
        for result in results:
            scenario = result.get('scenario', 'unknown')
            verification = result.get('verification_result', {}).get('verification', {})
            status = verification.get('status', 'UNKNOWN')
            
            metrics.append({
                'MetricName': f'LLMTest_{scenario}',
                'Value': 1 if status == 'PASSED' else 0,
                'Unit': 'Count',
                'Timestamp': datetime.now(timezone.utc),
                'Dimensions': [
                    {'Name': 'Scenario', 'Value': scenario},
                    {'Name': 'Status', 'Value': status}
                ]
            })
        
        cw.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=metrics
        )
        
        logger.info(f"✅ Published {len(metrics)} CloudWatch metrics")
        
    except Exception as e:
        logger.warning(f"Failed to publish CloudWatch metrics: {e}")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    LLM 테스트 결과를 집계하고 종합 리포트를 생성합니다.
    
    Input: {
        "test_results": [...],
        "simulator_execution_id": "...",
        "start_time": "..."
    }
    """
    raw_results = event.get('test_results', [])
    sim_exec_id = event.get('simulator_execution_id', 'unknown')
    start_time = event.get('start_time', '')
    
    # Lambda invoke 응답에서 실제 Payload 추출
    results = [_extract_payload(r) for r in raw_results]
    
    total = len(results)
    passed_count = 0
    failed_count = 0
    skipped_count = 0
    
    logger.info(f"🧠 Aggregating {total} LLM test results for {sim_exec_id}")
    
    scenarios = {}
    failed_scenarios = []
    
    for result in results:
        scenario = result.get('scenario', 'unknown')
        verification = result.get('verification_result', {}).get('verification', {})
        
        # [Fix] verify_llm_test.py는 'verified' boolean을 반환하므로 'status' 문자열로 변환
        # 기존: status = verification.get('status', 'UNKNOWN')
        if 'status' in verification:
            status = verification.get('status')
        elif 'verified' in verification:
            status = 'PASSED' if verification.get('verified') else 'FAILED'
        else:
            status = 'UNKNOWN'
        
        if status == 'PASSED':
            passed_count += 1
        elif status == 'FAILED':
            failed_count += 1
            failed_scenarios.append(scenario)
        else:
            skipped_count += 1
        
        # Extract provider information from test result
        # Structure: result -> test_result -> output (final_state) -> usage -> provider
        test_result = result.get('test_result', {})
        
        # Handle both direct output and nested structure
        if isinstance(test_result, dict):
            # Check for 'output' key (Step Functions format)
            final_state = test_result.get('output', test_result)
            
            # Handle case where output is a JSON string
            if isinstance(final_state, str):
                try:
                    final_state = json.loads(final_state)
                except:
                    final_state = {}
            
            # Extract usage from final_state or nested final_state
            usage = final_state.get('usage') or final_state.get('final_state', {}).get('usage', {})
            provider = usage.get('provider', 'unknown')
        else:
            usage = {}
            provider = 'unknown'
        
        # Log provider info for debugging
        if provider != 'unknown':
            logger.info(f"Scenario {scenario}: provider={provider}")
        else:
            logger.warning(f"Scenario {scenario}: provider not found in result")
        
        # [v3.3] Outcome Manager Link Generation
        # Extract execution ID to enable accessing Detailed Outcome Report
        execution_id = final_state.get('llm_execution_id') or final_state.get('execution_id') or test_result.get('executionArn')
        
        outcome_url = None
        if execution_id:
            # Assuming standard API path, can be used by frontend or CLI
            outcome_url = f"/tasks/{execution_id}/outcomes"

        scenarios[scenario] = {
            'status': status,
            'message': verification.get('message', ''),
            # 🛡️ [Payload Optimization] test_result 전체 저장 제거 - 256KB 제한 방지
            # 'test_result': test_result,  # REMOVED: 중복 데이터, 페이로드 폭발 원인
            'verification_summary': {
                'passed': status == 'PASSED',
                'checks': verification.get('checks', []),  # 검증 조건 목록만
                'failure_reason': verification.get('failure_reason') if status == 'FAILED' else None
            },
            'provider': provider,
            'usage': usage,
            'execution_id': execution_id,
            'outcome_url': outcome_url
        }
    
    overall_status = 'SUCCESS' if failed_count == 0 else 'FAILURE'
    
    # CloudWatch 메트릭 발행
    _publish_metrics(results, passed_count, total)
    
    # 종합 리포트 생성
    comprehensive_report = _generate_comprehensive_report(results, scenarios)
    
    # 🛡️ [Payload Optimization] 통합 리포트 - scenarios 중복 제거
    # comprehensive_report.scenario_details에 검증 결과가 이미 포함됨
    report = {
        'simulator_execution_id': sim_exec_id,
        'start_time': start_time,
        'end_time': datetime.now(timezone.utc).isoformat(),
        'overall_status': overall_status,
        'summary': {
            'total': total,
            'passed': passed_count,
            'failed': failed_count,
            'skipped': skipped_count,
            'pass_rate': round((passed_count / total * 100) if total > 0 else 0, 2)
        },
        # 🛡️ scenarios 제거 - comprehensive_report.scenario_details로 통합
        'failed_scenarios': failed_scenarios,
        'mock_mode': 'false',
        # 종합 리포트 (scenarios + 검증 결과 + usage 통합)
        'comprehensive_report': comprehensive_report
    }
    
    if failed_count > 0:
        logger.error(f"❌ LLM Simulator FAILED: {failed_count}/{total} scenarios failed")
        logger.error(f"Failed scenarios: {failed_scenarios}")
    else:
        logger.info(f"✅ LLM Simulator SUCCESS: All {total} scenarios passed")
    
    # 종합 통계 로깅
    agg_stats = comprehensive_report['aggregate_statistics']
    logger.info(f"Pass rate: {report['summary']['pass_rate']}%")
    logger.info(f"📊 Aggregate Statistics:")
    logger.info(f"  - Total tokens: {agg_stats['total_input_tokens'] + agg_stats['total_output_tokens']:,}")
    logger.info(f"  - Cached tokens: {agg_stats['total_cached_tokens']:,}")
    logger.info(f"  - Cache hit rate: {agg_stats['average_cache_hit_rate']}%")
    logger.info(f"  - Total cost: ${agg_stats['total_cost_usd']:.6f}")
    logger.info(f"  - Cost saved: ${agg_stats['total_cost_saved_usd']:.6f} ({agg_stats.get('cost_reduction_percentage', 0)}%)")
    logger.info(f"  - Thinking steps: {agg_stats['total_thinking_steps']}")
    
    # Thinking 로그가 있는 시나리오 로깅
    thinking_scenarios = list(comprehensive_report.get('thinking_summary', {}).keys())
    if thinking_scenarios:
        logger.info(f"🧠 Scenarios with Thinking Mode: {', '.join(thinking_scenarios)}")
    
    return report
