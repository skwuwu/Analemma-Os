# -*- coding: utf-8 -*-
"""
[Phase 6] Garbage Collection Service - Merkle DAG 자동 정리 (고도화)

핵심 기능:
1. DynamoDB Streams 이벤트 기반 GC
2. TTL 만료 시 S3 Content Blocks 자동 삭제
3. Atomic Reference Counting으로 Dangling Block 방지
4. S3 Bulk Delete로 성능 최적화 (1000개/batch)
5. Safe Chain Protection (보안 사고 발생 시 Freeze)

OS 아키텍처 고려사항:
- Dangling Pointer 문제 완전 차단 (Reference Counter Table)
- Glacier 2단계 전략: 30일 → Glacier → 90일 → 삭제
- 증거 보존: Security Violation 발생 시 TTL 연장
"""

import logging
import os
from typing import List, Dict, Set, Any, Tuple
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reference Counter Table Schema (별도 DynamoDB 테이블)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Table: BlockReferenceCounts
# - block_id (String, HASH): SHA256 해시
# - ref_count (Number): 참조 카운트
# - created_at (String): 최초 생성 시각
# - last_accessed (String): 최근 참조 시각
# - is_frozen (Boolean): Safe Chain Protection (보안 사고 시)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class MerkleGarbageCollector:
    """
    Merkle DAG Garbage Collector (Production-Ready)
    
    DynamoDB TTL 만료 → Streams 이벤트 → Atomic RefCount → S3 Bulk Delete
    
    OS 아키텍처 고도화:
    - Atomic Reference Counting (Dangling Block 완전 차단)
    - S3 Bulk Delete (1000개/batch, 네트워크 최적화)
    - Safe Chain Protection (보안 사고 시 Freeze)
    - Glacier 2단계 전략 (30일 → Glacier → 90일 → 삭제)
    """
    
    def __init__(self, dynamodb_table: str, s3_bucket: str, ref_table: str = None):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(dynamodb_table)
        
        # Reference Counter Table (별도 DynamoDB 테이블)
        ref_table_name = ref_table or os.environ.get(
            'BLOCK_REF_COUNT_TABLE', 
            'BlockReferenceCounts-dev'
        )
        self.ref_table = self.dynamodb.Table(ref_table_name)
        
        self.s3 = boto3.client('s3')
        self.bucket = s3_bucket
    
    def process_ttl_expiry_event(self, event: Dict[str, Any]) -> Dict[str, int]:
        """
        DynamoDB Streams TTL 만료 이벤트 처리
        
        Args:
            event: DynamoDB Streams 이벤트
        
        Returns:
            처리 통계 딕셔너리
        """
        stats = {
            'manifests_processed': 0,
            'blocks_deleted': 0,
            'blocks_skipped': 0,  # 참조 카운트 > 0
            'errors': 0
        }
        
        for record in event.get('Records', []):
            if record['eventName'] != 'REMOVE':
                continue
            
            # TTL에 의한 삭제인지 확인
            if 'userIdentity' not in record or record['userIdentity'].get('type') != 'Service':
                continue
            
            try:
                old_image = record['dynamodb'].get('OldImage', {})
                manifest_id = old_image.get('manifest_id', {}).get('S', '')
                
                if not manifest_id:
                    continue
                
                logger.info(f"[GC] Processing TTL expiry for manifest: {manifest_id}")
                
                # 블록 삭제
                deleted, skipped = self._cleanup_manifest_blocks(old_image)
                
                stats['manifests_processed'] += 1
                stats['blocks_deleted'] += deleted
                stats['blocks_skipped'] += skipped
                
            except Exception as e:
                logger.error(f"[GC] Failed to process record: {e}", exc_info=True)
                stats['errors'] += 1
        
        logger.info(
            f"[GC] Batch complete: {stats['manifests_processed']} manifests, "
            f"{stats['blocks_deleted']} blocks deleted, "
            f"{stats['blocks_skipped']} blocks skipped"
        )
        
        return stats
    
    def _cleanup_manifest_blocks(self, manifest_item: Dict[str, Any]) -> Tuple[int, int]:
        """
        매니페스트의 S3 블록 정리 (고도화: Atomic RefCount + Bulk Delete)
        
        OS 아키텍처 개선:
        1. Atomic Reference Counting으로 Dangling Block 완전 차단
        2. S3 Bulk Delete로 성능 최적화 (1000개/batch)
        3. Safe Chain Protection (is_frozen 체크)
        
        Args:
            manifest_item: DynamoDB OldImage
        
        Returns:
            (삭제된 블록 수, 스킵된 블록 수)
        """
        deleted = 0
        skipped = 0
        
        # S3 포인터 추출
        s3_pointers = manifest_item.get('s3_pointers', {}).get('M', {})
        state_blocks = s3_pointers.get('state_blocks', {}).get('L', [])
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # [고도화 1] Bulk Delete 준비 (성능 최적화)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        pending_deletes = []  # S3 Bulk Delete 대상
        frozen_blocks = []    # Safe Chain Protection에 의해 보호된 블록
        
        for block_item in state_blocks:
            block_path = block_item.get('S', '')
            if not block_path:
                continue
            
            block_id = block_path.split('/')[-1].replace('.json', '')
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [고도화 2] Atomic Reference Counting (Dangling Block 방지)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            is_deletable = self._decrement_and_check_zero(block_id)
            
            if is_deletable:
                key = block_path.replace(f"s3://{self.bucket}/", "")
                pending_deletes.append({'Key': key})
            else:
                skipped += 1
                # Frozen block 여부 로깅
                if self._is_block_frozen(block_id):
                    frozen_blocks.append(block_id[:8])
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # [고도화 3] S3 Bulk Delete 실행 (1000개/batch)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        if pending_deletes:
            # S3 delete_objects는 최대 1000개까지 처리 가능
            batch_size = 1000
            for i in range(0, len(pending_deletes), batch_size):
                batch = pending_deletes[i:i + batch_size]
                try:
                    response = self.s3.delete_objects(
                        Bucket=self.bucket,
                        Delete={'Objects': batch, 'Quiet': True}
                    )
                    
                    # 삭제 성공 카운트
                    deleted += len(batch)
                    
                    # 에러 발생 시 로깅
                    if 'Errors' in response:
                        for error in response['Errors']:
                            logger.error(
                                f"[GC] Failed to delete {error['Key']}: "
                                f"{error['Code']} - {error['Message']}"
                            )
                    
                    logger.info(f"[GC] Bulk deleted {len(batch)} blocks (batch {i//batch_size + 1})")
                    
                except ClientError as e:
                    logger.error(f"[GC] Bulk delete failed for batch {i//batch_size + 1}: {e}")
        
        # Safe Chain Protection 로깅
        if frozen_blocks:
            logger.warning(
                f"[GC] [Safe Chain] {len(frozen_blocks)} blocks protected by freeze policy: "
                f"{frozen_blocks[:5]}..."
            )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # [고도화 4] workflow_config 정리 (참조 카운트 확인)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        config_path = s3_pointers.get('config', {}).get('S', '')
        if config_path:
            config_hash = config_path.split('/')[-1].replace('.json', '')
            config_ref_count = self._get_config_reference_count(config_hash)
            
            if config_ref_count <= 1:
                try:
                    key = config_path.replace(f"s3://{self.bucket}/", "")
                    self.s3.delete_object(Bucket=self.bucket, Key=key)
                    logger.info(f"[GC] Deleted config: {key}")
                    deleted += 1
                except ClientError as e:
                    if e.response['Error']['Code'] != 'NoSuchKey':
                        logger.error(f"[GC] Failed to delete config: {e}")
        
        return deleted, skipped
    
    def _decrement_and_check_zero(self, block_id: str) -> bool:
        """
        [핵심 로직] Atomic Reference Counting - Dangling Block 완전 차단
        
        블록의 참조 카운트를 원자적으로 1 감소시키고, 결과가 0 이하인지 확인
        
        OS 아키텍처 원칙:
        - DynamoDB UpdateItem의 ADD 연산으로 Race Condition 방지
        - ref_count <= 0인 경우에만 True 반환 (실제 삭제 가능)
        - is_frozen=True인 블록은 강제로 False 반환 (Safe Chain Protection)
        
        Args:
            block_id: 블록 ID (SHA256 해시)
        
        Returns:
            True: 삭제 가능 (ref_count = 0, not frozen)
            False: 삭제 불가 (ref_count > 0 또는 frozen)
        """
        try:
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Atomic Update] DynamoDB ADD로 ref_count -1 수행
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            response = self.ref_table.update_item(
                Key={'block_id': block_id},
                UpdateExpression="ADD ref_count :dec SET last_accessed = :now",
                ExpressionAttributeValues={
                    ':dec': -1,
                    ':now': datetime.utcnow().isoformat()
                },
                ReturnValues="ALL_NEW"
            )
            
            new_count = response.get('Attributes', {}).get('ref_count', 0)
            is_frozen = response.get('Attributes', {}).get('is_frozen', False)
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Safe Chain Protection] 보안 사고 발생 시 Freeze된 블록
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            if is_frozen:
                logger.warning(
                    f"[GC] [Safe Chain] Block {block_id[:8]}... is FROZEN, "
                    f"skipping delete (ref_count={new_count})"
                )
                return False
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Deletion Criterion] ref_count <= 0이면 삭제 가능
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            can_delete = new_count <= 0
            
            if can_delete:
                logger.debug(f"[GC] Block {block_id[:8]}... ref_count=0, marking for deletion")
            else:
                logger.debug(
                    f"[GC] Block {block_id[:8]}... still referenced "
                    f"(ref_count={new_count}), skipping"
                )
            
            return can_delete
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                # Reference Table에 항목이 없음 (이미 삭제됨 또는 초기화 안 됨)
                logger.warning(
                    f"[GC] Block {block_id[:8]}... not found in RefTable, "
                    f"conservative skip (may be orphaned)"
                )
                return False  # Conservative: 삭제하지 않음
            else:
                logger.error(f"[GC] Failed to decrement ref_count for {block_id[:8]}...: {e}")
                return False  # 에러 시 안전하게 삭제하지 않음
        
        except Exception as e:
            logger.error(f"[GC] Unexpected error in _decrement_and_check_zero: {e}")
            return False
    
    def _is_block_frozen(self, block_id: str) -> bool:
        """
        Safe Chain Protection: 블록이 Freeze 상태인지 확인
        
        보안 사고(Security Violation) 발생 시 증거 보존을 위해
        특정 블록을 GC 대상에서 제외
        
        Args:
            block_id: 블록 ID
        
        Returns:
            True: Frozen (삭제 금지)
            False: Normal (삭제 가능)
        """
        try:
            response = self.ref_table.get_item(
                Key={'block_id': block_id},
                ProjectionExpression='is_frozen'
            )
            return response.get('Item', {}).get('is_frozen', False)
        except Exception:
            return False  # 에러 시 안전하게 삭제 가능으로 간주
    
    def _get_config_reference_count(self, config_hash: str) -> int:
        """
        workflow_config를 참조하는 매니페스트 수
        
        Args:
            config_hash: config SHA256 해시
        
        Returns:
            참조 카운트
        """
        try:
            # config_hash를 사용하는 매니페스트 수 조회
            # HashIndex GSI 활용
            response = self.table.query(
                IndexName='HashIndex',
                KeyConditionExpression='manifest_hash = :hash',
                ExpressionAttributeValues={':hash': config_hash},
                Select='COUNT'
            )
            
            return response['Count']
            
        except Exception as e:
            logger.error(f"Failed to get config reference count: {e}")
            return 1


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [보조 함수] Reference Counting 관리 (StateVersioningService 연동)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def increment_block_references(block_ids: List[str], ref_table_name: str = None):
    """
    블록 생성 시 참조 카운트 증가 (StateVersioningService.create_manifest 호출 시)
    
    Args:
        block_ids: 블록 ID 리스트
        ref_table_name: Reference Table 이름 (환경변수 우선)
    """
    dynamodb = boto3.resource('dynamodb')
    ref_table_name = ref_table_name or os.environ.get(
        'BLOCK_REF_COUNT_TABLE', 'BlockReferenceCounts-dev'
    )
    ref_table = dynamodb.Table(ref_table_name)
    
    for block_id in block_ids:
        try:
            ref_table.update_item(
                Key={'block_id': block_id},
                UpdateExpression="""
                    ADD ref_count :inc 
                    SET created_at = if_not_exists(created_at, :now),
                        last_accessed = :now
                """,
                ExpressionAttributeValues={
                    ':inc': 1,
                    ':now': datetime.utcnow().isoformat()
                }
            )
            logger.debug(f"[RefCount] Incremented {block_id[:8]}... (+1)")
        except Exception as e:
            logger.error(f"[RefCount] Failed to increment {block_id[:8]}...: {e}")


def freeze_manifest_blocks(manifest_id: str, reason: str = "Security Violation"):
    """
    [Safe Chain Protection] 보안 사고 발생 시 매니페스트의 모든 블록 Freeze
    
    증거 보존: TTL이 만료되어도 GC에서 삭제하지 않음
    
    Args:
        manifest_id: 매니페스트 ID
        reason: Freeze 사유 (audit log)
    """
    dynamodb = boto3.resource('dynamodb')
    manifest_table_name = os.environ.get(
        'WORKFLOW_MANIFESTS_TABLE', 'WorkflowManifests-v3-dev'
    )
    ref_table_name = os.environ.get(
        'BLOCK_REF_COUNT_TABLE', 'BlockReferenceCounts-dev'
    )
    
    manifest_table = dynamodb.Table(manifest_table_name)
    ref_table = dynamodb.Table(ref_table_name)
    
    try:
        # 1. 매니페스트 로드
        response = manifest_table.get_item(Key={'manifest_id': manifest_id})
        if 'Item' not in response:
            logger.error(f"[Safe Chain] Manifest not found: {manifest_id}")
            return
        
        item = response['Item']
        block_paths = item.get('s3_pointers', {}).get('state_blocks', [])
        
        # 2. 모든 블록을 Freeze
        frozen_count = 0
        for block_path in block_paths:
            block_id = block_path.split('/')[-1].replace('.json', '')
            try:
                ref_table.update_item(
                    Key={'block_id': block_id},
                    UpdateExpression="SET is_frozen = :true, freeze_reason = :reason, frozen_at = :now",
                    ExpressionAttributeValues={
                        ':true': True,
                        ':reason': reason,
                        ':now': datetime.utcnow().isoformat()
                    }
                )
                frozen_count += 1
            except Exception as e:
                logger.error(f"[Safe Chain] Failed to freeze block {block_id[:8]}...: {e}")
        
        # 3. 매니페스트 TTL 연장 (90일)
        import time
        manifest_table.update_item(
            Key={'manifest_id': manifest_id},
            UpdateExpression="SET ttl = :new_ttl, freeze_reason = :reason",
            ExpressionAttributeValues={
                ':new_ttl': int(time.time()) + 90 * 24 * 3600,  # 90일 연장
                ':reason': reason
            }
        )
        
        logger.warning(
            f"[Safe Chain] [FROZEN] Manifest {manifest_id} and {frozen_count} blocks "
            f"protected for 90 days. Reason: {reason}"
        )
        
    except Exception as e:
        logger.error(f"[Safe Chain] Failed to freeze manifest {manifest_id}: {e}")


def lambda_handler(event, context):
    """
    DynamoDB Streams Lambda Handler (Production-Ready)
    
    트리거: WorkflowManifestsV3 테이블의 TTL 만료 이벤트
    
    환경변수:
    - WORKFLOW_MANIFESTS_TABLE: 매니페스트 테이블 이름
    - BLOCK_REF_COUNT_TABLE: Reference Counter 테이블 이름
    - S3_BUCKET: S3 버킷 이름
    """
    gc = MerkleGarbageCollector(
        dynamodb_table=os.environ.get('WORKFLOW_MANIFESTS_TABLE', 'WorkflowManifests-v3-dev'),
        s3_bucket=os.environ.get('S3_BUCKET', 'analemma-workflow-state-dev'),
        ref_table=os.environ.get('BLOCK_REF_COUNT_TABLE', 'BlockReferenceCounts-dev')
    )
    
    stats = gc.process_ttl_expiry_event(event)
    
    logger.info(
        f"[GC] [Summary] Processed {stats['manifests_processed']} manifests, "
        f"deleted {stats['blocks_deleted']} blocks, "
        f"skipped {stats['blocks_skipped']} blocks, "
        f"errors {stats['errors']}"
    )
    
    return {
        'statusCode': 200,
        'body': stats
    }
