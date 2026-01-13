"""
S3 정리 서비스 - 실패한 워크플로우의 S3 파일 정리

테스트 계획 요구사항:
- 실패한 실행의 S3 데이터 정리
- Lifecycle 정책 적용 확인
- 비용 누수 방지
"""

import os
import boto3
import logging
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# S3 클라이언트 초기화
try:
    from common.aws_clients import get_s3_client
    s3_client = get_s3_client()
except ImportError:
    s3_client = boto3.client('s3')


def cleanup_failed_execution_s3_files(
    execution_arn: str, 
    owner_id: str, 
    workflow_id: str,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    실패한 워크플로우 실행의 S3 파일들을 정리합니다.
    
    Args:
        execution_arn: Step Functions 실행 ARN
        owner_id: 사용자 ID (테넌트 격리)
        workflow_id: 워크플로우 ID
        dry_run: True면 실제 삭제하지 않고 목록만 반환
        
    Returns:
        dict: 정리 결과 정보
    """
    cleanup_result = {
        'execution_arn': execution_arn,
        'owner_id': owner_id,
        'workflow_id': workflow_id,
        'files_found': 0,
        'files_deleted': 0,
        'bytes_freed': 0,
        'errors': [],
        'dry_run': dry_run,
        'cleanup_timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    try:
        bucket = os.environ.get('WORKFLOW_STATE_BUCKET') or os.environ.get('SKELETON_S3_BUCKET')
        if not bucket:
            cleanup_result['errors'].append("No S3 bucket configured for cleanup")
            return cleanup_result
        
        # 실행 ID 추출 (ARN에서)
        execution_id = execution_arn.split(':')[-1] if execution_arn else 'unknown'
        
        # 정리할 S3 경로 패턴들
        cleanup_patterns = [
            f"workflow-states/{owner_id}/{workflow_id}/",
            f"workflow-manifests/{owner_id}/{workflow_id}/",
            f"workflow-partitions/{owner_id}/{workflow_id}/",
            f"segment-outputs/{owner_id}/{workflow_id}/",
            f"execution-inputs/{owner_id}/{workflow_id}/",
            f"merge-states/{owner_id}/{workflow_id}/",
            f"temp-states/{owner_id}/{execution_id}/",
        ]
        
        total_files = 0
        total_bytes = 0
        deleted_files = 0
        
        for pattern in cleanup_patterns:
            try:
                # S3 객체 목록 조회
                paginator = s3_client.get_paginator('list_objects_v2')
                pages = paginator.paginate(Bucket=bucket, Prefix=pattern)
                
                for page in pages:
                    objects = page.get('Contents', [])
                    
                    for obj in objects:
                        key = obj['Key']
                        size = obj['Size']
                        last_modified = obj['LastModified']
                        
                        total_files += 1
                        total_bytes += size
                        
                        logger.info(f"Found S3 object: s3://{bucket}/{key} ({size} bytes, modified: {last_modified})")
                        
                        # 실제 삭제 (dry_run이 아닌 경우)
                        if not dry_run:
                            try:
                                s3_client.delete_object(Bucket=bucket, Key=key)
                                deleted_files += 1
                                logger.info(f"✅ Deleted: s3://{bucket}/{key}")
                            except ClientError as e:
                                error_msg = f"Failed to delete s3://{bucket}/{key}: {e}"
                                cleanup_result['errors'].append(error_msg)
                                logger.error(error_msg)
                        else:
                            logger.info(f"🔍 Would delete: s3://{bucket}/{key} (dry run)")
                            
            except ClientError as e:
                error_msg = f"Failed to list objects with pattern {pattern}: {e}"
                cleanup_result['errors'].append(error_msg)
                logger.error(error_msg)
        
        cleanup_result.update({
            'files_found': total_files,
            'files_deleted': deleted_files if not dry_run else 0,
            'bytes_freed': total_bytes if not dry_run else 0
        })
        
        if total_files > 0:
            logger.info(f"🧹 S3 cleanup completed: {deleted_files}/{total_files} files deleted, {total_bytes:,} bytes freed")
        else:
            logger.info("🔍 No S3 files found for cleanup")
            
    except Exception as e:
        error_msg = f"S3 cleanup failed: {e}"
        cleanup_result['errors'].append(error_msg)
        logger.error(error_msg)
    
    return cleanup_result


def cleanup_old_workflow_files(
    owner_id: str,
    days_old: int = 7,
    dry_run: bool = False
) -> Dict[str, Any]:
    """
    지정된 기간보다 오래된 워크플로우 파일들을 정리합니다.
    
    Args:
        owner_id: 사용자 ID (테넌트 격리)
        days_old: 삭제할 파일의 최소 나이 (일)
        dry_run: True면 실제 삭제하지 않고 목록만 반환
        
    Returns:
        dict: 정리 결과 정보
    """
    cleanup_result = {
        'owner_id': owner_id,
        'days_old': days_old,
        'files_found': 0,
        'files_deleted': 0,
        'bytes_freed': 0,
        'errors': [],
        'dry_run': dry_run,
        'cleanup_timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    try:
        bucket = os.environ.get('WORKFLOW_STATE_BUCKET') or os.environ.get('SKELETON_S3_BUCKET')
        if not bucket:
            cleanup_result['errors'].append("No S3 bucket configured for cleanup")
            return cleanup_result
        
        # 기준 날짜 계산
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_old)
        
        # 사용자별 경로 패턴
        user_patterns = [
            f"workflow-states/{owner_id}/",
            f"workflow-manifests/{owner_id}/",
            f"workflow-partitions/{owner_id}/",
            f"segment-outputs/{owner_id}/",
            f"execution-inputs/{owner_id}/",
            f"merge-states/{owner_id}/",
            f"temp-states/{owner_id}/",
        ]
        
        total_files = 0
        total_bytes = 0
        deleted_files = 0
        
        for pattern in user_patterns:
            try:
                paginator = s3_client.get_paginator('list_objects_v2')
                pages = paginator.paginate(Bucket=bucket, Prefix=pattern)
                
                for page in pages:
                    objects = page.get('Contents', [])
                    
                    for obj in objects:
                        key = obj['Key']
                        size = obj['Size']
                        last_modified = obj['LastModified']
                        
                        # 날짜 비교 (timezone-aware)
                        if last_modified.replace(tzinfo=timezone.utc) < cutoff_date:
                            total_files += 1
                            total_bytes += size
                            
                            logger.info(f"Found old S3 object: s3://{bucket}/{key} ({size} bytes, age: {(datetime.now(timezone.utc) - last_modified.replace(tzinfo=timezone.utc)).days} days)")
                            
                            if not dry_run:
                                try:
                                    s3_client.delete_object(Bucket=bucket, Key=key)
                                    deleted_files += 1
                                    logger.info(f"✅ Deleted old file: s3://{bucket}/{key}")
                                except ClientError as e:
                                    error_msg = f"Failed to delete s3://{bucket}/{key}: {e}"
                                    cleanup_result['errors'].append(error_msg)
                                    logger.error(error_msg)
                            else:
                                logger.info(f"🔍 Would delete old file: s3://{bucket}/{key} (dry run)")
                                
            except ClientError as e:
                error_msg = f"Failed to list objects with pattern {pattern}: {e}"
                cleanup_result['errors'].append(error_msg)
                logger.error(error_msg)
        
        cleanup_result.update({
            'files_found': total_files,
            'files_deleted': deleted_files if not dry_run else 0,
            'bytes_freed': total_bytes if not dry_run else 0
        })
        
        if total_files > 0:
            logger.info(f"🧹 Old files cleanup completed: {deleted_files}/{total_files} files deleted, {total_bytes:,} bytes freed")
        else:
            logger.info("🔍 No old files found for cleanup")
            
    except Exception as e:
        error_msg = f"Old files cleanup failed: {e}"
        cleanup_result['errors'].append(error_msg)
        logger.error(error_msg)
    
    return cleanup_result


def apply_s3_lifecycle_policy(bucket_name: str) -> Dict[str, Any]:
    """
    S3 버킷에 Lifecycle 정책을 적용하여 자동 정리를 설정합니다.
    
    Args:
        bucket_name: S3 버킷 이름
        
    Returns:
        dict: 정책 적용 결과
    """
    policy_result = {
        'bucket_name': bucket_name,
        'policy_applied': False,
        'errors': [],
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    try:
        # Lifecycle 정책 정의
        lifecycle_policy = {
            'Rules': [
                {
                    'ID': 'WorkflowTempFilesCleanup',
                    'Status': 'Enabled',
                    'Filter': {
                        'Prefix': 'temp-states/'
                    },
                    'Expiration': {
                        'Days': 1  # 임시 파일은 1일 후 삭제
                    }
                },
                {
                    'ID': 'WorkflowStateFilesCleanup',
                    'Status': 'Enabled',
                    'Filter': {
                        'Prefix': 'workflow-states/'
                    },
                    'Expiration': {
                        'Days': 30  # 워크플로우 상태는 30일 후 삭제
                    }
                },
                {
                    'ID': 'ExecutionInputsCleanup',
                    'Status': 'Enabled',
                    'Filter': {
                        'Prefix': 'execution-inputs/'
                    },
                    'Expiration': {
                        'Days': 7  # 실행 입력은 7일 후 삭제
                    }
                },
                {
                    'ID': 'SegmentOutputsCleanup',
                    'Status': 'Enabled',
                    'Filter': {
                        'Prefix': 'segment-outputs/'
                    },
                    'Expiration': {
                        'Days': 14  # 세그먼트 출력은 14일 후 삭제
                    }
                }
            ]
        }
        
        # 정책 적용
        s3_client.put_bucket_lifecycle_configuration(
            Bucket=bucket_name,
            LifecycleConfiguration=lifecycle_policy
        )
        
        policy_result['policy_applied'] = True
        logger.info(f"✅ S3 Lifecycle policy applied to bucket: {bucket_name}")
        
    except ClientError as e:
        error_msg = f"Failed to apply lifecycle policy to {bucket_name}: {e}"
        policy_result['errors'].append(error_msg)
        logger.error(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error applying lifecycle policy: {e}"
        policy_result['errors'].append(error_msg)
        logger.error(error_msg)
    
    return policy_result


def lambda_handler(event, context):
    """
    S3 정리 Lambda 핸들러
    
    EventBridge에서 호출되어 실패한 워크플로우의 S3 파일을 정리합니다.
    """
    logger.info("S3 cleanup service started")
    
    try:
        # EventBridge 이벤트에서 정보 추출
        detail = event.get('detail', {})
        execution_arn = detail.get('executionArn')
        execution_status = detail.get('status')
        
        # 실행 입력에서 owner_id, workflow_id 추출
        input_raw = detail.get('input')
        if isinstance(input_raw, str):
            try:
                input_obj = json.loads(input_raw)
            except json.JSONDecodeError:
                input_obj = {}
        else:
            input_obj = input_raw or {}
        
        owner_id = input_obj.get('ownerId')
        workflow_id = input_obj.get('workflowId')
        
        # 실패한 실행만 정리
        if execution_status in ['FAILED', 'TIMED_OUT', 'ABORTED']:
            if execution_arn and owner_id and workflow_id:
                cleanup_result = cleanup_failed_execution_s3_files(
                    execution_arn=execution_arn,
                    owner_id=owner_id,
                    workflow_id=workflow_id,
                    dry_run=False
                )
                
                logger.info(f"S3 cleanup completed for failed execution: {cleanup_result}")
                
                return {
                    'statusCode': 200,
                    'body': json.dumps(cleanup_result)
                }
            else:
                logger.warning(f"Missing required fields for cleanup: execution_arn={bool(execution_arn)}, owner_id={bool(owner_id)}, workflow_id={bool(workflow_id)}")
        else:
            logger.info(f"Execution status '{execution_status}' does not require cleanup")
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'No cleanup required'})
        }
        
    except Exception as e:
        logger.error(f"S3 cleanup service failed: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }