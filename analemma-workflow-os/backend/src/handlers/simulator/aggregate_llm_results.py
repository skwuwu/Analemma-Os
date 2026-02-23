"""
Pipeline Test Results Aggregation Lambda
==========================================
LLM Simulator Step Functions에서 호출됩니다.
verify_llm_test.py의 표준 스키마 결과를 집계하고
pipeline_matrix를 생성합니다.

표준 입력 스키마 (verify_llm_test 출력):
    {
        "passed":   bool,
        "scenario": str,
        "checks":   [{"name": str, "passed": bool, "details": str}],
        "metrics":  {...}   # 선택
    }
"""

import logging
import os
import boto3
from typing import Dict, Any, List
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

METRIC_NAMESPACE = os.environ.get('METRIC_NAMESPACE', 'Analemma/LLMSimulator')

# 파이프라인 시나리오 순서 (matrix 표시용)
PIPELINE_SCENARIOS = [
    "COMPLETE",
    "FAIL",
    "MAP_AGGREGATOR",
    "LOOP_LIMIT",
    "S3_LARGE",
    "ASYNC_LLM",
]


# ─── 표준 스키마 처리 ──────────────────────────────────────────────────────────

def _process_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    verify_llm_test 표준 스키마를 그대로 사용하는 단순 처리 함수.
    Lambda invoke 래핑이나 중첩 Payload 추출 없이 직접 접근.
    """
    return {
        "passed":   result.get("passed", False),
        "scenario": result.get("scenario", "UNKNOWN"),
        "checks":   result.get("checks", []),
        "metrics":  result.get("metrics", {}),
    }


def _build_pipeline_matrix(processed: List[Dict[str, Any]]) -> Dict[str, str]:
    """
    시나리오별 PASS/FAIL 한눈에 보기 매트릭스.

    Returns:
        {"COMPLETE": "✅ PASS", "FAIL": "❌ FAIL", ...}
    """
    by_scenario = {r["scenario"]: r["passed"] for r in processed}
    matrix = {}
    for scenario in PIPELINE_SCENARIOS:
        if scenario in by_scenario:
            matrix[scenario] = "✅ PASS" if by_scenario[scenario] else "❌ FAIL"
        else:
            matrix[scenario] = "⬜ SKIP"
    return matrix


# ─── CloudWatch 메트릭 ─────────────────────────────────────────────────────────

def _publish_metrics(processed: List[Dict[str, Any]], passed_count: int, total: int):
    """파이프라인 테스트 결과를 CloudWatch에 발행합니다."""
    try:
        cw = boto3.client('cloudwatch')
        now = datetime.now(timezone.utc)

        metrics = [
            {'MetricName': 'PipelineTestsPassed',  'Value': passed_count,          'Unit': 'Count',   'Timestamp': now},
            {'MetricName': 'PipelineTestsFailed',  'Value': total - passed_count,  'Unit': 'Count',   'Timestamp': now},
            {'MetricName': 'PipelineTestsTotal',   'Value': total,                 'Unit': 'Count',   'Timestamp': now},
            {
                'MetricName': 'PipelineTestPassRate',
                'Value': (passed_count / total * 100) if total > 0 else 0,
                'Unit': 'Percent',
                'Timestamp': now,
            },
        ]

        for r in processed:
            metrics.append({
                'MetricName': f'Pipeline_{r["scenario"]}',
                'Value': 1 if r['passed'] else 0,
                'Unit': 'Count',
                'Timestamp': now,
                'Dimensions': [
                    {'Name': 'Scenario', 'Value': r['scenario']},
                    {'Name': 'Status',   'Value': 'PASSED' if r['passed'] else 'FAILED'},
                ],
            })

        cw.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=metrics)
        logger.info(f"Published {len(metrics)} CloudWatch metrics")

    except Exception as e:
        logger.warning(f"Failed to publish CloudWatch metrics: {e}")


# ─── Lambda 핸들러 ────────────────────────────────────────────────────────────

def lambda_handler(event: Dict[str, Any], _context) -> Dict[str, Any]:
    """
    파이프라인 테스트 결과를 집계합니다.

    Input:
        {
            "test_results": [...],            # verify_llm_test 표준 스키마 목록
            "simulator_execution_id": "...",
            "start_time": "..."
        }

    Output:
        {
            "overall_status": "SUCCESS" | "FAILURE",
            "pipeline_matrix": {"COMPLETE": "✅ PASS", ...},
            "summary": {"total": 6, "passed": 5, "failed": 1, "pass_rate": 83.33},
            "scenario_details": {...},
            "failed_scenarios": [...],
            "simulator_execution_id": "...",
            "start_time": "...",
            "end_time": "..."
        }
    """
    raw_results = event.get('test_results', [])
    sim_exec_id = event.get('simulator_execution_id', 'unknown')
    start_time  = event.get('start_time', '')

    # 표준 스키마로 처리 (래핑 없음)
    processed = [_process_result(r) for r in raw_results]

    total        = len(processed)
    passed_count = sum(1 for r in processed if r['passed'])
    failed_count = total - passed_count

    logger.info(f"Aggregating {total} pipeline test results for {sim_exec_id}")

    # 실패 시나리오 목록
    failed_scenarios = [r['scenario'] for r in processed if not r['passed']]

    # 시나리오별 상세 (checks + metrics 보존)
    scenario_details = {
        r['scenario']: {
            'passed':  r['passed'],
            'status':  'PASSED' if r['passed'] else 'FAILED',
            'checks':  r['checks'],
            'metrics': r['metrics'],
        }
        for r in processed
    }

    overall_status = 'SUCCESS' if failed_count == 0 else 'FAILURE'
    pipeline_matrix = _build_pipeline_matrix(processed)

    # CloudWatch 메트릭 발행
    _publish_metrics(processed, passed_count, total)

    pass_rate = round((passed_count / total * 100) if total > 0 else 0, 2)

    if failed_count > 0:
        logger.error(f"Pipeline FAILED: {failed_count}/{total} scenarios failed — {failed_scenarios}")
    else:
        logger.info(f"Pipeline SUCCESS: All {total} scenarios passed")

    logger.info(f"Pipeline matrix: {pipeline_matrix}")

    return {
        'overall_status':         overall_status,
        'pipeline_matrix':        pipeline_matrix,
        'summary': {
            'total':     total,
            'passed':    passed_count,
            'failed':    failed_count,
            'pass_rate': pass_rate,
        },
        'scenario_details':       scenario_details,
        'failed_scenarios':       failed_scenarios,
        'simulator_execution_id': sim_exec_id,
        'start_time':             start_time,
        'end_time':               datetime.now(timezone.utc).isoformat(),
    }
