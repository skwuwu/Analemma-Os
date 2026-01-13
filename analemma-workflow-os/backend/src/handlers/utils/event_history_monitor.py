import os
import json
import logging
import boto3
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# CloudWatch and Step Functions clients
cloudwatch = boto3.client('cloudwatch')
stepfunctions = boto3.client('stepfunctions')


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Monitor Step Functions Event History usage and publish custom metrics.
    
    This function is triggered periodically to check execution event counts
    and publish CloudWatch metrics for alerting when approaching limits.
    
    Args:
        event: CloudWatch Events trigger or manual invocation
        context: Lambda context
        
    Returns:
        Dict containing monitoring results
    """
    logger.info("Starting Event History monitoring")
    
    # Get configuration
    state_machine_arn = os.environ.get('WORKFLOW_ORCHESTRATOR_ARN')
    if not state_machine_arn:
        raise ValueError("WORKFLOW_ORCHESTRATOR_ARN environment variable required")
    
    # Event History limit (Step Functions limit is 25,000)
    event_history_limit = int(os.environ.get('EVENT_HISTORY_LIMIT', '25000'))
    warning_threshold = int(os.environ.get('EVENT_HISTORY_WARNING_THRESHOLD', '20000'))  # 80%
    critical_threshold = int(os.environ.get('EVENT_HISTORY_CRITICAL_THRESHOLD', '23000'))  # 92%
    
    try:
        # List recent executions (last 24 hours)
        response = stepfunctions.list_executions(
            stateMachineArn=state_machine_arn,
            statusFilter='RUNNING',
            maxResults=100
        )
        
        running_executions = response.get('executions', [])
        logger.info(f"Found {len(running_executions)} running executions")
        
        # Monitor each running execution
        monitoring_results = []
        high_usage_count = 0
        critical_usage_count = 0
        
        for execution in running_executions:
            execution_arn = execution['executionArn']
            execution_name = execution['name']
            
            try:
                # Get execution history
                history_response = stepfunctions.get_execution_history(
                    executionArn=execution_arn,
                    maxResults=1000,  # Just get count, not full history
                    reverseOrder=True
                )
                
                # Count total events (approximate)
                events = history_response.get('events', [])
                event_count = len(events)
                
                # If we got 1000 events, there are likely more
                if len(events) == 1000:
                    # Get more accurate count by paginating
                    next_token = history_response.get('nextToken')
                    while next_token and event_count < event_history_limit:
                        page_response = stepfunctions.get_execution_history(
                            executionArn=execution_arn,
                            nextToken=next_token,
                            maxResults=1000
                        )
                        page_events = page_response.get('events', [])
                        event_count += len(page_events)
                        next_token = page_response.get('nextToken')
                        
                        # Stop counting if we're clearly over the limit
                        if event_count > event_history_limit:
                            break
                
                # Calculate usage percentage
                usage_percentage = (event_count / event_history_limit) * 100
                
                # Categorize usage level
                if event_count >= critical_threshold:
                    usage_level = "CRITICAL"
                    critical_usage_count += 1
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
                
                # Publish individual execution metrics
                _publish_execution_metrics(
                    execution_name=execution_name,
                    event_count=event_count,
                    usage_percentage=usage_percentage,
                    usage_level=usage_level
                )
                
                logger.info(
                    f"Execution {execution_name}: {event_count} events "
                    f"({usage_percentage:.1f}%) - {usage_level}"
                )
                
            except Exception as e:
                logger.error(f"Failed to monitor execution {execution_arn}: {e}")
                continue
        
        # Publish aggregate metrics
        _publish_aggregate_metrics(
            total_executions=len(running_executions),
            high_usage_executions=high_usage_count,
            critical_usage_executions=critical_usage_count
        )
        
        # Generate summary
        summary = {
            "total_running_executions": len(running_executions),
            "high_usage_executions": high_usage_count,
            "critical_usage_executions": critical_usage_count,
            "monitoring_timestamp": context.aws_request_id if context else "unknown",
            "thresholds": {
                "warning": warning_threshold,
                "critical": critical_threshold,
                "limit": event_history_limit
            }
        }
        
        logger.info(f"Event History monitoring completed: {summary}")
        
        return {
            "status": "SUCCESS",
            "summary": summary,
            "executions": monitoring_results
        }
        
    except Exception as e:
        logger.error(f"Event History monitoring failed: {e}")
        
        # Publish error metric
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
                                'Value': state_machine_arn.split(':')[-1]
                            }
                        ]
                    }
                ]
            )
        except Exception:
            pass  # Don't fail on metric publishing error
        
        raise


def _publish_execution_metrics(
    execution_name: str,
    event_count: int,
    usage_percentage: float,
    usage_level: str
) -> None:
    """Publish CloudWatch metrics for individual execution."""
    try:
        cloudwatch.put_metric_data(
            Namespace='WorkflowOrchestrator/EventHistory',
            MetricData=[
                {
                    'MetricName': 'EventCount',
                    'Value': event_count,
                    'Unit': 'Count',
                    'Dimensions': [
                        {
                            'Name': 'ExecutionName',
                            'Value': execution_name
                        }
                    ]
                },
                {
                    'MetricName': 'UsagePercentage',
                    'Value': usage_percentage,
                    'Unit': 'Percent',
                    'Dimensions': [
                        {
                            'Name': 'ExecutionName',
                            'Value': execution_name
                        }
                    ]
                },
                {
                    'MetricName': 'UsageLevel',
                    'Value': 1 if usage_level == "CRITICAL" else 0,
                    'Unit': 'Count',
                    'Dimensions': [
                        {
                            'Name': 'UsageLevel',
                            'Value': usage_level
                        }
                    ]
                }
            ]
        )
    except Exception as e:
        logger.warning(f"Failed to publish execution metrics: {e}")


def _publish_aggregate_metrics(
    total_executions: int,
    high_usage_executions: int,
    critical_usage_executions: int
) -> None:
    """Publish aggregate CloudWatch metrics."""
    try:
        cloudwatch.put_metric_data(
            Namespace='WorkflowOrchestrator/EventHistory',
            MetricData=[
                {
                    'MetricName': 'TotalRunningExecutions',
                    'Value': total_executions,
                    'Unit': 'Count'
                },
                {
                    'MetricName': 'HighUsageExecutions',
                    'Value': high_usage_executions,
                    'Unit': 'Count'
                },
                {
                    'MetricName': 'CriticalUsageExecutions',
                    'Value': critical_usage_executions,
                    'Unit': 'Count'
                }
            ]
        )
    except Exception as e:
        logger.warning(f"Failed to publish aggregate metrics: {e}")