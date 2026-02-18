"""
Manifest Regenerator - 동적 매니페스트 재생성

자율형 에이전트의 계획 수정(Re-planning)을 지원하기 위한 핵심 Lambda.
실행 중 노드/엣지가 추가/수정되면 partition_map을 재생성하여 Merkle Manifest를 갱신.

Features:
- 동적 수정사항(modifications)을 base config에 병합
- partition_workflow_advanced() 재실행
- 새 Merkle Manifest 생성 (S3 + DynamoDB)
- 기존 manifest 무효화 (INVALIDATED 상태)
"""

import os
import json
import time
import copy
import hashlib
import logging
import boto3
from typing import Dict, Any, List

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# AWS Clients
try:
    from src.common.aws_clients import get_dynamodb_resource, get_s3_client
    dynamodb = get_dynamodb_resource()
    s3_client = get_s3_client()
except ImportError:
    dynamodb = boto3.resource('dynamodb')
    s3_client = boto3.client('s3')

# Services
try:
    from src.services.workflow.partition_service import partition_workflow_advanced
    _HAS_PARTITION = True
except ImportError:
    _HAS_PARTITION = False
    partition_workflow_advanced = None

try:
    from src.services.state.state_versioning_service import StateVersioningService
    _HAS_VERSIONING = True
except ImportError:
    _HAS_VERSIONING = False
    StateVersioningService = None

# Environment Variables
WORKFLOWS_TABLE = os.environ.get('WORKFLOWS_TABLE', 'WorkflowsTableV3')
MANIFESTS_TABLE = os.environ.get('MANIFESTS_TABLE', 'WorkflowManifests-v3-dev')
STATE_BUCKET = os.environ.get('WORKFLOW_STATE_BUCKET', 'analemma-workflow-state-dev')


def _load_manifest_from_s3(manifest_s3_path: str) -> Dict[str, Any]:
    """S3에서 기존 매니페스트 로드"""
    import urllib.parse
    
    # s3://bucket/key 파싱
    if manifest_s3_path.startswith('s3://'):
        manifest_s3_path = manifest_s3_path[5:]
    
    parts = manifest_s3_path.split('/', 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ''
    
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        manifest_json = response['Body'].read().decode('utf-8')
        return json.loads(manifest_json)
    except Exception as e:
        logger.error(f"Failed to load manifest from {manifest_s3_path}: {e}")
        raise


def _load_workflow_config(owner_id: str, workflow_id: str) -> Dict[str, Any]:
    """DynamoDB에서 base workflow config 로드"""
    try:
        table = dynamodb.Table(WORKFLOWS_TABLE)
        response = table.get_item(
            Key={'ownerId': owner_id, 'workflowId': workflow_id}
        )
        
        if 'Item' not in response:
            raise ValueError(f"Workflow not found: {workflow_id}")
        
        item = response['Item']
        
        # config가 S3 참조인 경우
        if 'config_s3_ref' in item:
            s3_ref = item['config_s3_ref']
            logger.info(f"Loading config from S3: {s3_ref}")
            
            if s3_ref.startswith('s3://'):
                s3_ref = s3_ref[5:]
            
            parts = s3_ref.split('/', 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ''
            
            response = s3_client.get_object(Bucket=bucket, Key=key)
            config_json = response['Body'].read().decode('utf-8')
            
            if isinstance(config_json, str):
                return json.loads(config_json)
            return config_json
        
        # config가 DynamoDB에 직접 저장된 경우
        config = item.get('config', {})
        
        if isinstance(config, str):
            return json.loads(config)
        
        return config
        
    except Exception as e:
        logger.error(f"Failed to load workflow config: {e}")
        raise


def _merge_modifications(base_config: Dict[str, Any], modifications: Dict[str, Any]) -> Dict[str, Any]:
    """
    동적 수정사항을 base_config에 병합
    
    Args:
        base_config: 원본 워크플로우 설정
        modifications: {
            'new_nodes': [...],
            'new_edges': [...],
            'modified_nodes': {node_id: {updates}},
            'deleted_nodes': [node_id, ...],
            'deleted_edges': [edge_id, ...]
        }
    
    Returns:
        병합된 워크플로우 설정
    """
    merged = copy.deepcopy(base_config)
    
    # 새 노드 추가
    if 'new_nodes' in modifications:
        new_nodes = modifications['new_nodes']
        logger.info(f"[Merge] Adding {len(new_nodes)} new nodes")
        merged.setdefault('nodes', []).extend(new_nodes)
    
    # 새 엣지 추가
    if 'new_edges' in modifications:
        new_edges = modifications['new_edges']
        logger.info(f"[Merge] Adding {len(new_edges)} new edges")
        merged.setdefault('edges', []).extend(new_edges)
    
    # 기존 노드 수정
    if 'modified_nodes' in modifications:
        node_map = {n['id']: n for n in merged.get('nodes', [])}
        
        for node_id, updates in modifications['modified_nodes'].items():
            if node_id in node_map:
                logger.info(f"[Merge] Updating node {node_id}: {updates.keys()}")
                node_map[node_id].update(updates)
    
    # 노드 삭제
    if 'deleted_nodes' in modifications:
        deleted_ids = set(modifications['deleted_nodes'])
        logger.info(f"[Merge] Deleting {len(deleted_ids)} nodes")
        merged['nodes'] = [
            n for n in merged.get('nodes', []) 
            if n.get('id') not in deleted_ids
        ]
    
    # 엣지 삭제
    if 'deleted_edges' in modifications:
        deleted_edges = modifications['deleted_edges']
        logger.info(f"[Merge] Deleting {len(deleted_edges)} edges")
        
        # edge_id 또는 (source, target) 튜플로 삭제
        merged['edges'] = [
            e for e in merged.get('edges', [])
            if e.get('id') not in deleted_edges and
               (e.get('source'), e.get('target')) not in deleted_edges
        ]
    
    return merged


def lambda_handler(event, context):
    """
    동적 매니페스트 재생성 Lambda Handler
    
    Input Event:
    {
        "manifest_s3_path": "s3://bucket/manifests/abc123.json",
        "workflow_id": "wf_12345",
        "owner_id": "user_abc",
        "modification_type": "RECOVERY_INJECT" | "AGENT_REPLAN",
        "modifications": {
            "new_nodes": [...],
            "new_edges": [...],
            "modified_nodes": {...},
            "reason": "Governor approved additional validation"
        },
        "task_token": "..." (Optional, for Step Functions callback)
    }
    
    Output:
    {
        "new_manifest_id": "manifest_xyz789",
        "new_manifest_s3_path": "s3://.../xyz789.json",
        "new_manifest_hash": "sha256:...",
        "invalidated_manifest_id": "manifest_abc123",
        "total_segments_before": 3,
        "total_segments_after": 5
    }
    """
    logger.info(f"[ManifestRegenerator] Starting: {json.dumps(event, default=str)}")
    
    # Validate inputs
    if not _HAS_PARTITION:
        raise ImportError("partition_workflow_advanced is not available")
    
    if not _HAS_VERSIONING:
        raise ImportError("StateVersioningService is not available")
    
    manifest_s3_path = event.get('manifest_s3_path')
    workflow_id = event.get('workflow_id')
    owner_id = event.get('owner_id')
    modifications = event.get('modifications', {})
    modification_type = event.get('modification_type', 'UNKNOWN')
    task_token = event.get('task_token')
    
    if not all([manifest_s3_path, workflow_id, owner_id]):
        raise ValueError("Missing required fields: manifest_s3_path, workflow_id, owner_id")
    
    try:
        # 1. 기존 매니페스트 로드
        logger.info(f"[Step 1] Loading old manifest from {manifest_s3_path}")
        old_manifest = _load_manifest_from_s3(manifest_s3_path)
        old_manifest_id = old_manifest.get('manifest_id', 'unknown')
        old_total_segments = old_manifest.get('total_segments', 0)
        
        # 2. Base Workflow Config 로드
        logger.info(f"[Step 2] Loading base config for workflow {workflow_id}")
        base_config = _load_workflow_config(owner_id, workflow_id)
        
        # 3. 동적 수정사항 병합
        logger.info(f"[Step 3] Merging modifications: {modification_type}")
        merged_config = _merge_modifications(base_config, modifications)
        
        # 4. Re-partitioning
        logger.info(f"[Step 4] Re-partitioning workflow")
        partition_start = time.time()
        
        partition_result = partition_workflow_advanced(merged_config)
        new_partition_map = partition_result.get('partition_map', [])
        
        partition_time = time.time() - partition_start
        logger.info(f"[Step 4] ✅ Re-partitioning completed in {partition_time:.3f}s: "
                   f"{len(new_partition_map)} segments")
        
        # 5. 새 Merkle Manifest 생성
        logger.info(f"[Step 5] Creating new Merkle manifest")
        versioning_service = StateVersioningService(
            dynamodb_table=MANIFESTS_TABLE,
            s3_bucket=STATE_BUCKET
        )
        
        segment_manifest = []
        for idx, segment in enumerate(new_partition_map):
            segment_manifest.append({
                "segment_id": idx,
                "segment_config": segment,
                "execution_order": idx,
                "dependencies": segment.get("dependencies", []),
                "type": segment.get("type", "normal"),
                # [NEW] 동적 수정 추적
                "is_dynamic": idx >= old_total_segments,
                "modification_reason": modifications.get('reason', modification_type)
            })
        
        # Config Hash 계산
        config_hash = hashlib.sha256(
            json.dumps(merged_config, sort_keys=True).encode()
        ).hexdigest()
        
        new_manifest_pointer = versioning_service.create_manifest(
            workflow_id=workflow_id,
            segment_manifest=segment_manifest,
            config_hash=config_hash
        )
        
        logger.info(f"[Step 5] ✅ New manifest created: {new_manifest_pointer['manifest_id']}")
        
        # 6. 기존 매니페스트 무효화
        logger.info(f"[Step 6] Invalidating old manifest: {old_manifest_id}")
        versioning_service.invalidate_manifest(
            manifest_id=old_manifest_id,
            reason=f"Dynamic re-partitioning: {modification_type}"
        )
        
        # 7. 결과 반환
        result = {
            "status": "success",
            "new_manifest_id": new_manifest_pointer['manifest_id'],
            "new_manifest_s3_path": new_manifest_pointer['manifest_s3_path'],
            "new_manifest_hash": new_manifest_pointer['manifest_hash'],
            "invalidated_manifest_id": old_manifest_id,
            "total_segments_before": old_total_segments,
            "total_segments_after": len(new_partition_map),
            "partition_time_seconds": partition_time,
            "modification_type": modification_type
        }
        
        logger.info(f"[ManifestRegenerator] ✅ Success: {old_total_segments} → {len(new_partition_map)} segments")
        
        # [Optional] Step Functions Task Token 콜백
        if task_token:
            logger.info(f"[Step 7] Sending task success to Step Functions")
            sfn_client = boto3.client('stepfunctions')
            
            sfn_client.send_task_success(
                taskToken=task_token,
                output=json.dumps(result)
            )
        
        return result
        
    except Exception as e:
        logger.error(f"[ManifestRegenerator] ❌ Failed: {e}", exc_info=True)
        
        # Step Functions 콜백 실패
        if task_token:
            try:
                sfn_client = boto3.client('stepfunctions')
                sfn_client.send_task_failure(
                    taskToken=task_token,
                    error=type(e).__name__,
                    cause=str(e)
                )
            except Exception as callback_error:
                logger.error(f"Failed to send task failure: {callback_error}")
        
        return {
            "status": "error",
            "error_type": type(e).__name__,
            "error_message": str(e),
            "modification_type": modification_type
        }
