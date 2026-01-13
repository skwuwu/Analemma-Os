import json
import boto3
import os
import logging
import concurrent.futures
from typing import Dict, Any, List
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

METRIC_NAMESPACE = os.environ.get('METRIC_NAMESPACE', 'Analemma/MissionSimulator')

def _extract_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Lambda invoke 응답에서 실제 Payload를 추출합니다.
    
    Map 상태의 결과는 Lambda invoke 응답 형식일 수 있음:
    { "ExecutedVersion": "$LATEST", "Payload": {...}, "StatusCode": 200 }
    
    또는 직접 결과일 수 있음:
    { "passed": true, "scenario": "...", ... }
    """
    if not isinstance(result, dict):
        return {}
    
    # Lambda invoke 응답 형식인 경우 Payload 추출
    if 'Payload' in result and 'StatusCode' in result:
        payload = result.get('Payload', {})
        # Payload가 문자열인 경우 (JSON)
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                return {}
        return payload if isinstance(payload, dict) else {}
    
    # 이미 직접 결과 형식인 경우
    return result


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Aggregates results from all parallel test executions.
    Input: { "results": [ {"Payload": {"passed": True, ...}, ...}, ... ], "simulator_execution_id": "..." }
    
    Note: Map 상태에서 Lambda invoke를 사용하면 각 결과가 Lambda 응답 형식으로 래핑됨.
    """
    raw_results = event.get('results', [])
    sim_id = event.get('simulator_execution_id')
    
    # Lambda invoke 응답에서 실제 Payload 추출
    results = [_extract_payload(r) for r in raw_results]
    
    total = len(results)
    passed_count = sum(1 for r in results if r.get('passed'))
    failed_count = total - passed_count
    
    overall_status = "SUCCESS" if failed_count == 0 else "FAILURE"
    
    logger.info(f"Aggregating {total} scenarios for {sim_id}. Passed: {passed_count}, Failed: {failed_count}")
    
    # [Optimization] Parallel S3 Download for Reports
    failures = [r for r in results if not r.get('passed')]
    failed_reasons = {}

    if failures:
        logger.error("❌ Mission Failed Scenarios - Fetching Details...")
        
        def _fetch_reason(failure_record):
            s3_path = failure_record.get('report_s3_path', '')
            scenario = failure_record.get('scenario', 'UNKNOWN')
            if not s3_path or not s3_path.startswith('s3://'):
                return scenario, "No S3 report path"

            try:
                # s3://bucket/key
                parts = s3_path.replace("s3://", "").split("/", 1)
                bucket, key = parts[0], parts[1]
                s3 = boto3.client('s3')
                obj = s3.get_object(Bucket=bucket, Key=key)
                data = json.loads(obj['Body'].read())
                # Extract reason from verification_result checks or error field
                checks = data.get('verification_result', {}).get('checks', [])
                failed_checks = [c['name'] for c in checks if not c['passed']]
                details = [c.get('details', '') for c in checks if not c['passed']]
                return scenario, f"Failed Checks: {failed_checks}. Details: {details}"
            except Exception as e:
                return scenario, f"Failed to fetch report: {str(e)}"

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_scenario = {executor.submit(_fetch_reason, f): f for f in failures}
            for future in concurrent.futures.as_completed(future_to_scenario):
                scen, reason = future.result()
                failed_reasons[scen] = reason
                logger.error(f" - {scen}: {reason}")

    # 1. Publish CloudWatch Metrics
    _publish_metrics(results, passed_count, total)
            
    return {
        "status": overall_status,
        "total": total,
        "passed": passed_count,
        "failed": failed_count,
        "failed_scenarios": [f.get('scenario') for f in failures],
        "failure_details": failed_reasons,
        "metrics_published": True
    }

def _chunk_metrics(data, size=20):
    for i in range(0, len(data), size):
        yield data[i:i + size]

def _publish_metrics(results: List[Dict], passed_count: int, total: int):
    if total == 0: return
    
    try:
        cw = boto3.client('cloudwatch')
        metric_data = []
        
        # Per-scenario results
        for r in results:
            scenario = r.get('scenario', 'UNKNOWN')
            is_success = r.get('passed', False)
            
            # [Optimization] Use SuccessCount/FailureCount for easier aggregation
            metric_data.append({
                'MetricName': 'SuccessCount',
                'Dimensions': [{'Name': 'Scenario', 'Value': scenario}],
                'Value': 1 if is_success else 0,
                'Unit': 'Count'
            })
            metric_data.append({
                'MetricName': 'FailureCount',
                'Dimensions': [{'Name': 'Scenario', 'Value': scenario}],
                'Value': 0 if is_success else 1,
                'Unit': 'Count'
            })
            
        # Overall Success Rate
        success_rate = (passed_count / total) * 100
        metric_data.append({
            'MetricName': 'OverallSuccessRate',
            'Value': success_rate,
            'Unit': 'Percent'
        })
        
        # [Optimization] Generator-based Chunking
        for chunk in _chunk_metrics(metric_data):
            cw.put_metric_data(
                Namespace=METRIC_NAMESPACE,
                MetricData=chunk
            )
            
        logger.info(f"Published {len(metric_data)} metrics in chunks.")
            
    except Exception as e:
        logger.error(f"Failed to publish metrics: {e}")
