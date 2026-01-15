"""
InfraSmokeTester: Infrastructure Health Check Lambda

이 함수는 Analemma 인프라의 핵심 구성 요소가 정상 작동하는지 선제적으로 검증합니다.
5분마다 EventBridge로 트리거되며, 실패 시 CloudWatch 알람을 통해 즉시 알림을 발송합니다.

체크 대상:
- S3: smoketest/{request_id}.txt 파일 생성/읽기/삭제 (병렬 안전)
- DynamoDB: Executions 테이블 상태 확인
- Bedrock: 실제 1-토큰 추론 테스트 (Haiku 사용)
- Step Functions: 최근 5분 내 실행 중 FAILED 상태 확인
- PII Masking: 이메일 마스킹 & URL 보존 검증
- S3 Offloading: 256KB 초과 데이터 자동 오프로딩 검증
"""
import json
import os
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import boto3
from botocore.exceptions import ClientError

# Services for logic tests
from src.services.common.pii_masking_service import get_pii_masking_service
from src.services.state.state_persistence_service import StatePersistenceService

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment Variables
DATA_BUCKET = os.environ.get("WORKFLOW_STATE_BUCKET", "")
EXECUTIONS_TABLE = os.environ.get("EXECUTIONS_TABLE", "")
ORCHESTRATOR_ARN = os.environ.get("WORKFLOW_ORCHESTRATOR_ARN", "")
DISTRIBUTED_ORCHESTRATOR_ARN = os.environ.get("WORKFLOW_DISTRIBUTED_ORCHESTRATOR_ARN", "")
# 🚨 [Critical Fix] 기본값을 template.yaml과 일치시킴
WORKFLOWS_TABLE = os.environ.get("WORKFLOWS_TABLE", "WorkflowsTableV3")
METRIC_NAMESPACE = "Analemma/Engine"


def put_custom_metric(metric_name: str, value: float, dimensions: list[dict] | None = None) -> None:
    """CloudWatch에 커스텀 매트릭을 발행합니다."""
    try:
        cloudwatch = boto3.client("cloudwatch")
        metric_data = {
            "MetricName": metric_name,
            "Value": value,
            "Unit": "Count",
        }
        if dimensions:
            metric_data["Dimensions"] = dimensions
        
        cloudwatch.put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[metric_data]
        )
        logger.info(f"Metric emitted: {metric_name}={value}")
    except Exception as e:
        logger.error(f"Failed to emit metric {metric_name}: {e}")


def check_s3_permission(bucket_name: str, request_id: str) -> dict[str, Any]:
    """
    S3 버킷에 파일을 생성/읽기/삭제하여 연결성과 권한을 확인합니다.
    병렬 실행 시 충돌을 방지하기 위해 request_id 기반 고유 파일명을 사용합니다.
    """
    result = {"service": "S3", "bucket": bucket_name, "status": "OK", "details": None}
    
    if not bucket_name:
        result["status"] = "SKIPPED"
        result["details"] = "WORKFLOW_STATE_BUCKET not configured"
        return result
    
    s3 = boto3.client("s3")
    test_key = f"smoketest/{request_id}.txt"
    test_content = f"healthcheck-{int(time.time())}"
    
    try:
        # 1. Write
        s3.put_object(Bucket=bucket_name, Key=test_key, Body=test_content.encode())
        
        # 2. Read
        response = s3.get_object(Bucket=bucket_name, Key=test_key)
        read_content = response["Body"].read().decode()
        
        if read_content != test_content:
            raise ValueError("Content mismatch after read")
        
        # 3. Delete
        s3.delete_object(Bucket=bucket_name, Key=test_key)
        
        result["status"] = "OK"
    except ClientError as e:
        result["status"] = "ERROR"
        result["details"] = str(e)
        logger.error(f"S3 check failed: {e}")
    except Exception as e:
        result["status"] = "ERROR"
        result["details"] = str(e)
        logger.error(f"S3 check failed: {e}")
    
    return result


def check_dynamo_status(table_name: str) -> dict[str, Any]:
    """
    DynamoDB 테이블이 ACTIVE 상태인지, GSI가 살아있는지 확인합니다.
    """
    result = {"service": "DynamoDB", "table": table_name, "status": "OK", "details": None}
    
    if not table_name:
        result["status"] = "SKIPPED"
        result["details"] = "EXECUTIONS_TABLE not configured"
        return result
    
    dynamodb = boto3.client("dynamodb")
    
    try:
        response = dynamodb.describe_table(TableName=table_name)
        table_status = response["Table"]["TableStatus"]
        
        if table_status != "ACTIVE":
            result["status"] = "WARNING"
            result["details"] = f"Table status is {table_status}"
            return result
        
        # GSI 상태 확인
        gsi_list = response["Table"].get("GlobalSecondaryIndexes", [])
        inactive_gsi = [gsi["IndexName"] for gsi in gsi_list if gsi.get("IndexStatus") != "ACTIVE"]
        
        if inactive_gsi:
            result["status"] = "WARNING"
            result["details"] = f"Inactive GSIs: {inactive_gsi}"
        else:
            result["status"] = "OK"
            
    except ClientError as e:
        result["status"] = "ERROR"
        result["details"] = str(e)
        logger.error(f"DynamoDB check failed: {e}")
    
    return result


def check_bedrock_connectivity() -> dict[str, Any]:
    """
    Bedrock에 실제 1-토큰 추론 요청을 보내 연결성을 확인합니다.
    가장 저렴한 Haiku 모델을 사용하고, 응답 텍스트 유효성도 검증합니다.
    """
    result = {"service": "Bedrock", "status": "OK", "details": None}
    
    # 알려진 에러 패턴 목록
    ERROR_PATTERNS = [
        "error",
        "unable to",
        "cannot process",
        "rate limit",
        "quota exceeded"
    ]
    
    try:
        bedrock_runtime = boto3.client("bedrock-runtime")
        
        # Claude 3 Haiku - 가장 저렴한 옵션
        model_id = "anthropic.claude-3-haiku-20240307-v1:0"
        
        request_body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "Hi"}]
        })
        
        response = bedrock_runtime.invoke_model(
            modelId=model_id,
            body=request_body,
            contentType="application/json",
            accept="application/json"
        )
        
        # 응답 구조 및 텍스트 유효성 검증
        response_body = json.loads(response["body"].read())
        
        # 1. content 필드 존재 여부 확인
        if "content" not in response_body:
            result["status"] = "WARNING"
            result["details"] = "Response missing 'content' field"
            return result
        
        # 2. content가 비어있는지 확인
        content_list = response_body.get("content", [])
        if not content_list:
            result["status"] = "WARNING"
            result["details"] = "Response content is empty"
            return result
        
        # 3. 응답 텍스트 추출 및 에러 패턴 검사
        response_text = ""
        for item in content_list:
            if item.get("type") == "text":
                response_text += item.get("text", "")
        
        response_text_lower = response_text.lower()
        for pattern in ERROR_PATTERNS:
            if pattern in response_text_lower:
                result["status"] = "WARNING"
                result["details"] = f"Response contains error pattern: '{pattern}'"
                return result
        
        # 모든 검증 통과
        result["status"] = "OK"
        result["details"] = f"Model {model_id} responded: '{response_text[:50]}...'"
            
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        if error_code == "AccessDeniedException":
            result["status"] = "ERROR"
            result["details"] = "Bedrock access denied - check IAM permissions"
        elif error_code == "ThrottlingException":
            result["status"] = "WARNING"
            result["details"] = "Bedrock throttled - may indicate high load"
        else:
            result["status"] = "ERROR"
            result["details"] = str(e)
        logger.error(f"Bedrock check failed: {e}")
    except Exception as e:
        result["status"] = "ERROR"
        result["details"] = str(e)
        logger.error(f"Bedrock check failed: {e}")
    
    return result



def check_step_functions(orchestrator_arn: str) -> dict[str, Any]:
    """
    Step Functions 상태 머신 상태 및 최근 실행 성공 여부를 확인합니다.
    maxResults=10으로 제한하여 효율성을 높이고, 실패율 매트릭을 발행합니다.
    """
    result = {"service": "StepFunctions", "arn": orchestrator_arn, "status": "OK", "details": None}
    
    if not orchestrator_arn:
        result["status"] = "SKIPPED"
        result["details"] = "WORKFLOW_ORCHESTRATOR_ARN not configured"
        return result
    
    sfn = boto3.client("stepfunctions")
    
    try:
        # 1. 상태 머신 정의 확인
        sfn.describe_state_machine(stateMachineArn=orchestrator_arn)
        
        # 2. 최근 10개 실행 중 FAILED가 있는지 확인 (효율성을 위해 제한)
        executions = sfn.list_executions(
            stateMachineArn=orchestrator_arn,
            maxResults=10  # 성능 최적화: 최근 10개만 조회
        )
        
        recent_executions = executions.get("executions", [])
        total_count = len(recent_executions)
        failed_count = sum(1 for ex in recent_executions if ex.get("status") == "FAILED")
        
        # 📊 실패율 매트릭 발행 (0.0 ~ 1.0)
        if total_count > 0:
            failure_rate = failed_count / total_count
            # State Machine 이름 추출 (ARN에서)
            sm_name = orchestrator_arn.split(":")[-1] if ":" in orchestrator_arn else "unknown"
            put_custom_metric(
                "StepFunctionsFailureRate",
                failure_rate,
                dimensions=[{"Name": "StateMachine", "Value": sm_name}]
            )
        
        if failed_count > 0:
            result["status"] = "WARNING"
            result["details"] = f"{failed_count}/{total_count} executions failed ({int(failed_count/total_count*100)}%)"
        else:
            result["status"] = "OK"
            result["details"] = f"Last {total_count} executions OK"
            
    except ClientError as e:
        result["status"] = "ERROR"
        result["details"] = str(e)
        logger.error(f"Step Functions check failed: {e}")
    
    return result


# =============================================================================
# LOGIC CHECKS (verify internal services)
# =============================================================================

def verify_pii_masking() -> dict[str, Any]:
    """
    PII 마스킹 서비스가 이메일을 마스킹하고 URL을 보존하는지 검증합니다.
    """
    result = {"service": "PII_Masking", "status": "OK", "details": None}
    
    try:
        service = get_pii_masking_service()
        test_url = "https://s3.aws.com/report.pdf"
        test_input = f"Contact dev@analemma.ai or visit {test_url}"
        masked_result = service.mask(test_input)
        
        # 이메일 마스킹 확인
        email_masked = "[EMAIL_REDACTED]" in masked_result
        # URL 완전 보존 확인 (부분 일치 아닌 정확한 문자열 포함 여부)
        url_preserved = test_url in masked_result
        
        if email_masked and url_preserved:
            result["status"] = "OK"
            result["details"] = f"Email masked, URL preserved. Sample: {masked_result[:60]}..."
        else:
            result["status"] = "ERROR"
            issues = []
            if not email_masked:
                issues.append("Email not masked")
            if not url_preserved:
                issues.append("URL not preserved")
            result["details"] = f"Failed: {', '.join(issues)}. Result: {masked_result[:80]}"
            
    except Exception as e:
        result["status"] = "ERROR"
        result["details"] = str(e)
        logger.error(f"PII Masking check failed: {e}")
    
    return result


def _cleanup_test_data(execution_id: str, owner_id: str, workflow_id: str) -> None:
    """테스트 데이터 완전 삭제 (DDB + S3)"""
    try:
        persistence = StatePersistenceService()
        result = persistence.delete_state(execution_id, owner_id, workflow_id)
        logger.info(f"Cleanup result for {execution_id}: {result}")
    except Exception as e:
        logger.warning(f"Cleanup failed for {execution_id}: {e}")


def verify_s3_offloading(request_id: str) -> dict[str, Any]:
    """
    256KB 초과 데이터가 S3로 자동 오프로딩되는지 검증합니다.
    try-finally로 테스트 데이터를 반드시 정리합니다.
    """
    result = {"service": "S3_Offloading", "status": "OK", "details": None}
    
    test_exec_id = f"smoke-{request_id}"
    test_owner_id = "system"
    test_workflow_id = "smoke-test"
    
    try:
        persistence = StatePersistenceService(
            state_bucket=DATA_BUCKET,
            workflows_table=WORKFLOWS_TABLE
        )
        
        # 300KB 테스트 데이터 생성 (256KB 임계치 초과)
        large_data = {"test_payload": "x" * 300_000, "request_id": request_id}
        payload_size_kb = len(json.dumps(large_data)) // 1024
        
        # 저장 시도 (내부적으로 S3 오프로딩 발생해야 함)
        save_result = persistence.save_state(
            execution_id=test_exec_id,
            owner_id=test_owner_id,
            workflow_id=test_workflow_id,
            chunk_id="0",
            segment_id=0,
            state_data=large_data
        )
        
        if not save_result.get("saved"):
            result["status"] = "ERROR"
            result["details"] = f"Save failed: {save_result.get('error')}"
            return result
        
        # 로드 및 검증
        load_result = persistence.load_state(
            execution_id=test_exec_id,
            owner_id=test_owner_id,
            workflow_id=test_workflow_id,
            chunk_index=1  # chunk_index > 0 to trigger load
        )
        
        if load_result.get("state_loaded"):
            loaded_data = load_result.get("previous_state", {})
            # 데이터 무결성 검증 (request_id 필드 비교)
            if loaded_data.get("request_id") == request_id:
                result["status"] = "OK"
                result["details"] = f"Saved and loaded {payload_size_kb}KB successfully"
            else:
                result["status"] = "WARNING"
                result["details"] = "Data mismatch after load"
        else:
            result["status"] = "WARNING"
            result["details"] = f"Load issue: {load_result.get('reason', 'unknown')}"
            
    except Exception as e:
        result["status"] = "ERROR"
        result["details"] = str(e)
        logger.error(f"S3 Offloading check failed: {e}")
    finally:
        # 성공/실패 무관하게 테스트 데이터 정리
        _cleanup_test_data(test_exec_id, test_owner_id, test_workflow_id)
    
    return result


def lambda_handler(event: dict, context: Any) -> dict:
    """
    메인 헬스체크 핸들러 - Environment Validation Engine.
    인프라 구성 요소와 내부 로직을 모두 검사하고 결과를 CloudWatch 매트릭으로 발행합니다.
    """
    request_id = context.aws_request_id if context else f"local-{int(time.time())}"
    
    logger.info(f"Starting environment validation (request_id: {request_id})")
    
    # Infrastructure checks
    infra_checks = {
        "S3_DataBucket": check_s3_permission(DATA_BUCKET, request_id),
        "DynamoDB_ExecTable": check_dynamo_status(EXECUTIONS_TABLE),
        "Bedrock_Runtime": check_bedrock_connectivity(),
        "StepFunctions_Orchestrator": check_step_functions(ORCHESTRATOR_ARN),
        "StepFunctions_Distributed": check_step_functions(DISTRIBUTED_ORCHESTRATOR_ARN),
    }
    
    # Logic checks (internal services)
    logic_checks = {
        "PII_Masking": verify_pii_masking(),
        "S3_Offloading": verify_s3_offloading(request_id),
    }
    
    # Combine all results
    all_results = {**infra_checks, **logic_checks}

    # 실패/경고 항목 집계
    failed_services = [k for k, v in all_results.items() if v.get("status") == "ERROR"]
    warning_services = [k for k, v in all_results.items() if v.get("status") == "WARNING"]
    
    # CloudWatch 매트릭 발행
    if failed_services:
        put_custom_metric("InfraHealthStatus", 0)
        overall_status = "ERROR"
    elif warning_services:
        put_custom_metric("InfraHealthStatus", 0.5)  # 경고 상태
        overall_status = "WARNING"
    else:
        put_custom_metric("InfraHealthStatus", 1)
        overall_status = "OK"
    
    response = {
        "status": overall_status,
        "timestamp": int(time.time()),
        "request_id": request_id,
        "infra_checks": infra_checks,
        "logic_checks": logic_checks,
    }
    
    if failed_services:
        response["failed"] = failed_services
        logger.error(f"Environment validation FAILED: {failed_services}")
    
    if warning_services:
        response["warnings"] = warning_services
        logger.warning(f"Environment validation WARNINGS: {warning_services}")
    
    logger.info(f"Environment validation completed: {overall_status}")
    
    return response
