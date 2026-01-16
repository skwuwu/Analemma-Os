"""
Event History Monitor Lambda (v2.0)

Step Functions Event History 사용량을 모니터링하고 25,000 이벤트 한도 도달 전
사전 대응 조치를 수행합니다.

Improvements (v2.0):
- API 효율화: maxResults=1 + reverseOrder로 마지막 이벤트 ID만 조회 (기존 대비 ~99% API 호출 감소)
- 사전 방지: CRITICAL 도달 시 자동 마이그레이션 트리거 인터페이스
- 비용 최적화: ExecutionName 차원 제거 → StateMachine 집계 + 로그 상세

CloudWatch Alarm 연동:
- Namespace: WorkflowOrchestrator/EventHistory
- Metric: MaxUsagePercentage (StateMachine 레벨 집계)
"""
import os
import json
import logging
import boto3
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Global scope for Lambda cold start optimization
cloudwatch = boto3.client('cloudwatch')
stepfunctions = boto3.client('stepfunctions')

# Configuration constants
MAX_CONCURRENT_API_CALLS = 10  # Rate limit 방지를 위한 동시 호출 제한
EVENT_ID_EXTRACTION_TIMEOUT = 5  # 개별 실행당 타임아웃 (초)


class MigrationAction:
    """
    CRITICAL 도달 시 수행할 마이그레이션 액션 정의.
    실제 StopExecution + 새 실행 생성은 별도 Lambda에서 처리 권장.
    """
    NOTIFY_ONLY = "notify_only"  # 알림만 (기본값)
    TRIGGER_MIGRATION = "trigger_migration"  # 마이그레이션 Lambda 호출
    AUTO_STOP = "auto_stop"  # 자동 중단 (위험 - 데이터 유실 가능)


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Monitor Step Functions Event History usage with optimized API calls.
    
    v2.0 개선사항:
    - maxResults=1 + reverseOrder=True로 마지막 이벤트 ID만 조회
    - 이벤트 ID가 순차 증가한다는 점을 활용하여 전체 카운트 추정
    - 병렬 조회로 100개 실행도 빠르게 처리
    - CRITICAL 시 마이그레이션 트리거 지원
    
    Args:
        event: CloudWatch Events trigger or manual invocation
            - action: MigrationAction (optional, default: NOTIFY_ONLY)
        context: Lambda context
        
    Returns:
        Dict containing monitoring results and any migration actions taken
    """
    logger.info("Starting Event History monitoring (v2.0 - optimized)")
    
    # Get configuration
    state_machine_arn = os.environ.get('WORKFLOW_ORCHESTRATOR_ARN')
    if not state_machine_arn:
        raise ValueError("WORKFLOW_ORCHESTRATOR_ARN environment variable required")
    
    state_machine_name = state_machine_arn.split(':')[-1]
    
    # Event History limit (Step Functions limit is 25,000)
    event_history_limit = int(os.environ.get('EVENT_HISTORY_LIMIT', '25000'))
    warning_threshold = int(os.environ.get('EVENT_HISTORY_WARNING_THRESHOLD', '20000'))  # 80%
    critical_threshold = int(os.environ.get('EVENT_HISTORY_CRITICAL_THRESHOLD', '23000'))  # 92%
    
    # Migration action configuration
    migration_action = event.get('action', MigrationAction.NOTIFY_ONLY)
    migration_lambda_arn = os.environ.get('MIGRATION_LAMBDA_ARN')  # Optional
    
    try:
        # List recent executions
        response = stepfunctions.list_executions(
            stateMachineArn=state_machine_arn,
            statusFilter='RUNNING',
            maxResults=100
        )
        
        running_executions = response.get('executions', [])
        logger.info(f"Found {len(running_executions)} running executions")
        
        # ============================================================
        # [Fix #1] 최적화된 이벤트 카운트 조회
        # 기존: maxResults=1000으로 페이징 → API 호출 폭증
        # 개선: maxResults=1 + reverseOrder=True → 마지막 이벤트 ID만 확인
        # Step Functions 이벤트 ID는 1부터 순차 증가하므로, 마지막 ID = 총 이벤트 수
        # ============================================================
        monitoring_results = []
        high_usage_count = 0
        critical_usage_count = 0
        critical_executions = []  # 마이그레이션 대상
        
        # 병렬 조회로 API 호출 시간 단축
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_API_CALLS) as executor:
            future_to_execution = {
                executor.submit(
                    _get_event_count_optimized,
                    execution['executionArn']
                ): execution
                for execution in running_executions
            }
            
            for future in as_completed(future_to_execution):
                execution = future_to_execution[future]
                execution_arn = execution['executionArn']
                execution_name = execution['name']
                
                try:
                    event_count = future.result(timeout=EVENT_ID_EXTRACTION_TIMEOUT)
                    
                    if event_count is None:
                        logger.warning(f"Could not determine event count for {execution_name}")
                        continue
                    
                    # Calculate usage percentage
                    usage_percentage = (event_count / event_history_limit) * 100
                    
                    # Categorize usage level
                    if event_count >= critical_threshold:
                        usage_level = "CRITICAL"
                        critical_usage_count += 1
                        critical_executions.append({
                            'arn': execution_arn,
                            'name': execution_name,
                            'event_count': event_count
                        })
                    elif event_count >= warning_threshold:
                        usage_level = "WARNING"
                        high_usage_count += 1
                    else:
                        usage_level = "NORMAL"
                    
                    execution_result = {
                        "execution_arn": execution_arn,
                        "execution_name": execution_name,
                        "event_count": event_count,
                        "usage_percentage": round(usage_percentage, 2),
                        "usage_level": usage_level,
                        "started_at": execution['startDate'].isoformat()
                    }
                    
                    monitoring_results.append(execution_result)
                    
                    # ============================================================
                    # [Fix #3] 개별 실행 메트릭 대신 구조화된 로그로 기록
                    # CloudWatch Logs Insights로 분석 가능, 메트릭 비용 절감
                    # ============================================================
                    _log_execution_details(
                        execution_name=execution_name,
                        event_count=event_count,
                        usage_percentage=usage_percentage,
                        usage_level=usage_level,
                        state_machine_name=state_machine_name
                    )
                    
                except Exception as e:
                    logger.error(f"Failed to monitor execution {execution_arn}: {e}")
                    continue
        
        # ============================================================
        # [Fix #2] CRITICAL 도달 시 사전 방지 로직
        # ============================================================
        migration_results = []
        if critical_executions and migration_action != MigrationAction.NOTIFY_ONLY:
            migration_results = _handle_critical_executions(
                critical_executions=critical_executions,
                action=migration_action,
                migration_lambda_arn=migration_lambda_arn
            )
        
        # [Fix #3] StateMachine 레벨 집계 메트릭만 발행 (비용 최적화)
        max_usage_percentage = max(
            (r['usage_percentage'] for r in monitoring_results),
            default=0
        )
        _publish_aggregate_metrics(
            state_machine_name=state_machine_name,
            total_executions=len(running_executions),
            high_usage_executions=high_usage_count,
            critical_usage_executions=critical_usage_count,
            max_usage_percentage=max_usage_percentage
        )
        
        # Generate summary
        summary = {
            "total_running_executions": len(running_executions),
            "high_usage_executions": high_usage_count,
            "critical_usage_executions": critical_usage_count,
            "max_usage_percentage": max_usage_percentage,
            "monitoring_timestamp": context.aws_request_id if context else "unknown",
            "thresholds": {
                "warning": warning_threshold,
                "critical": critical_threshold,
                "limit": event_history_limit
            },
            "migration_action": migration_action,
            "migrations_triggered": len(migration_results)
        }
        
        logger.info(f"Event History monitoring completed: {json.dumps(summary)}")
        
        return {
            "status": "SUCCESS",
            "summary": summary,
            "executions": monitoring_results,
            "migrations": migration_results
        }
        
    except Exception as e:
        logger.error(f"Event History monitoring failed: {e}")
        
        # Publish error metric (StateMachine 레벨만)
        try:
            cloudwatch.put_metric_data(
                Namespace='WorkflowOrchestrator/EventHistory',
                MetricData=[
                    {
                        'MetricName': 'MonitoringErrors',
                        'Value': 1,
                        'Unit': 'Count',
                        'Dimensions': [
                            {
                                'Name': 'StateMachine',
                                'Value': state_machine_name
                            }
                        ]
                    }
                ]
            )
        except Exception:
            pass  # Don't fail on metric publishing error
        
        raise


# ============================================================================
# [Fix #1] 최적화된 이벤트 카운트 조회 함수
# ============================================================================
def _get_event_count_optimized(execution_arn: str) -> Optional[int]:
    """
    최적화된 이벤트 카운트 조회.
    
    Step Functions Event ID는 1부터 시작하여 순차 증가하므로,
    마지막 이벤트의 ID = 총 이벤트 수입니다.
    
    기존 방식: maxResults=1000 페이징 → 2만 이벤트면 20회 API 호출
    개선 방식: maxResults=1 + reverseOrder → 1회 API 호출
    
    Args:
        execution_arn: Step Functions 실행 ARN
        
    Returns:
        이벤트 총 개수 (None if failed)
    """
    try:
        # reverseOrder=True로 마지막 이벤트만 가져옴
        response = stepfunctions.get_execution_history(
            executionArn=execution_arn,
            maxResults=1,
            reverseOrder=True
        )
        
        events = response.get('events', [])
        if not events:
            return 0
        
        # Step Functions 이벤트 ID는 1-indexed sequential
        # 마지막 이벤트의 'id' 필드가 곧 총 이벤트 수
        last_event = events[0]
        event_count = last_event.get('id', 0)
        
        return event_count
        
    except Exception as e:
        logger.warning(f"Failed to get event count for {execution_arn}: {e}")
        return None


# ============================================================================
# [Fix #2] CRITICAL 도달 시 사전 방지 로직
# ============================================================================
def _handle_critical_executions(
    critical_executions: List[Dict[str, Any]],
    action: str,
    migration_lambda_arn: Optional[str]
) -> List[Dict[str, Any]]:
    """
    CRITICAL 임계치 도달 실행에 대한 사전 방지 조치.
    
    마이그레이션 전략:
    1. 현재 상태를 체크포인트에 저장
    2. 기존 실행을 안전하게 중단 (HITP 상태가 아닌 경우)
    3. 새로운 실행을 시작하며 체크포인트에서 복구
    
    Args:
        critical_executions: CRITICAL 상태 실행 목록
        action: MigrationAction 값
        migration_lambda_arn: 마이그레이션 처리 Lambda ARN
        
    Returns:
        마이그레이션 결과 목록
    """
    results = []
    lambda_client = boto3.client('lambda')
    
    for execution in critical_executions:
        execution_arn = execution['arn']
        execution_name = execution['name']
        event_count = execution['event_count']
        
        try:
            if action == MigrationAction.AUTO_STOP:
                # ⚠️ 위험: 진행 중인 작업이 손실될 수 있음
                # HITP 상태 확인 없이 중단하면 데이터 유실 가능
                logger.warning(
                    f"AUTO_STOP requested for {execution_name} "
                    f"({event_count} events) - checking HITP status first"
                )
                
                # 실행 상태 확인
                exec_response = stepfunctions.describe_execution(
                    executionArn=execution_arn
                )
                
                # HITP 상태면 중단하지 않음 (사용자 응답 대기 중)
                if exec_response.get('status') == 'RUNNING':
                    # TODO: Task Token 존재 여부로 HITP 판단 필요
                    # 현재는 안전을 위해 StopExecution 호출하지 않음
                    logger.info(
                        f"Skipping AUTO_STOP for {execution_name} - "
                        "manual migration recommended"
                    )
                    results.append({
                        'execution_arn': execution_arn,
                        'action': 'SKIPPED',
                        'reason': 'auto_stop_disabled_for_safety'
                    })
                    continue
                    
            elif action == MigrationAction.TRIGGER_MIGRATION:
                if not migration_lambda_arn:
                    logger.error("MIGRATION_LAMBDA_ARN not configured")
                    results.append({
                        'execution_arn': execution_arn,
                        'action': 'FAILED',
                        'reason': 'migration_lambda_not_configured'
                    })
                    continue
                
                # 마이그레이션 Lambda 비동기 호출
                payload = {
                    'execution_arn': execution_arn,
                    'execution_name': execution_name,
                    'event_count': event_count,
                    'trigger': 'event_history_critical'
                }
                
                lambda_client.invoke(
                    FunctionName=migration_lambda_arn,
                    InvocationType='Event',  # 비동기
                    Payload=json.dumps(payload)
                )
                
                logger.info(f"Migration triggered for {execution_name}")
                results.append({
                    'execution_arn': execution_arn,
                    'action': 'MIGRATION_TRIGGERED',
                    'reason': f'event_count={event_count}'
                })
                
        except Exception as e:
            logger.error(f"Failed to handle critical execution {execution_arn}: {e}")
            results.append({
                'execution_arn': execution_arn,
                'action': 'ERROR',
                'reason': str(e)
            })
    
    return results


# ============================================================================
# [Fix #3] 구조화된 로그로 개별 실행 정보 기록 (메트릭 비용 절감)
# ============================================================================
def _log_execution_details(
    execution_name: str,
    event_count: int,
    usage_percentage: float,
    usage_level: str,
    state_machine_name: str
) -> None:
    """
    CloudWatch Logs에 구조화된 로그로 개별 실행 정보 기록.
    
    CloudWatch Logs Insights 쿼리 예시:
    ```
    fields @timestamp, execution_name, event_count, usage_level
    | filter usage_level = "CRITICAL"
    | sort @timestamp desc
    | limit 100
    ```
    
    장점:
    - Custom Metric 비용 없음 (로그 저장 비용만)
    - 더 상세한 정보 저장 가능
    - Logs Insights로 유연한 분석
    """
    # 구조화된 로그 (JSON 형태로 출력하면 Logs Insights에서 파싱 가능)
    log_entry = {
        "event_type": "execution_monitoring",
        "state_machine": state_machine_name,
        "execution_name": execution_name,
        "event_count": event_count,
        "usage_percentage": round(usage_percentage, 2),
        "usage_level": usage_level,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # usage_level에 따라 로그 레벨 조정
    if usage_level == "CRITICAL":
        logger.error(json.dumps(log_entry))
    elif usage_level == "WARNING":
        logger.warning(json.dumps(log_entry))
    else:
        logger.info(json.dumps(log_entry))


def _publish_aggregate_metrics(
    state_machine_name: str,
    total_executions: int,
    high_usage_executions: int,
    critical_usage_executions: int,
    max_usage_percentage: float
) -> None:
    """
    StateMachine 레벨 집계 메트릭만 발행.
    
    [Fix #3] 비용 최적화:
    - ExecutionName 차원 제거 → 무한 증가하는 Custom Metric 방지
    - StateMachine 레벨 집계로 알람에 필요한 메트릭만 유지
    - 개별 실행 정보는 로그로 기록 (_log_execution_details)
    
    CloudWatch Alarm 설정 예시:
    ```
    Namespace: WorkflowOrchestrator/EventHistory
    MetricName: MaxUsagePercentage
    Threshold: 80 (WARNING), 92 (CRITICAL)
    ```
    """
    try:
        cloudwatch.put_metric_data(
            Namespace='WorkflowOrchestrator/EventHistory',
            MetricData=[
                {
                    'MetricName': 'TotalRunningExecutions',
                    'Value': total_executions,
                    'Unit': 'Count',
                    'Dimensions': [
                        {
                            'Name': 'StateMachine',
                            'Value': state_machine_name
                        }
                    ]
                },
                {
                    'MetricName': 'HighUsageExecutions',
                    'Value': high_usage_executions,
                    'Unit': 'Count',
                    'Dimensions': [
                        {
                            'Name': 'StateMachine',
                            'Value': state_machine_name
                        }
                    ]
                },
                {
                    'MetricName': 'CriticalUsageExecutions',
                    'Value': critical_usage_executions,
                    'Unit': 'Count',
                    'Dimensions': [
                        {
                            'Name': 'StateMachine',
                            'Value': state_machine_name
                        }
                    ]
                },
                # 가장 중요한 메트릭: 전체 실행 중 최대 사용률
                # 이 메트릭으로 알람 설정 권장
                {
                    'MetricName': 'MaxUsagePercentage',
                    'Value': max_usage_percentage,
                    'Unit': 'Percent',
                    'Dimensions': [
                        {
                            'Name': 'StateMachine',
                            'Value': state_machine_name
                        }
                    ]
                }
            ]
        )
    except Exception as e:
        logger.warning(f"Failed to publish aggregate metrics: {e}")