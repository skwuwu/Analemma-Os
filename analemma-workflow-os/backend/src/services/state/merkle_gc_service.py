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
    
    def _decrement_and_check_zero(self, block_id: str, graceful_wait_seconds: int = 300) -> bool:
        """
        [핵심 로직] Atomic Reference Counting - Dangling Block 완전 차단
        
        블록의 참조 카운트를 원자적으로 1 감소시키고, 결과가 0 이하인지 확인
        
        OS 아키텍처 원칙:
        - DynamoDB UpdateItem의 ADD 연산으로 Race Condition 방지
        - ref_count <= 0인 경우에만 True 반환 (실제 삭제 가능)
        - is_frozen=True인 블록은 강제로 False 반환 (Safe Chain Protection)
        - [v2.1.1] ConditionExpression으로 음수 카운트 방지
        - [v2.1.1] graceful_wait_seconds로 생성/삭제 Race Condition 방지
        
        Args:
            block_id: 블록 ID (SHA256 해시)
            graceful_wait_seconds: 카운트 0 도달 후 대기 시간 (기본 5분)
        
        Returns:
            True: 삭제 가능 (ref_count = 0, not frozen, graceful_wait 경과)
            False: 삭제 불가 (ref_count > 0 또는 frozen 또는 대기 중)
        """
        try:
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [Atomic Update] DynamoDB ADD로 ref_count -1 수행 (음수 방지)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            import time
            response = self.ref_table.update_item(
                Key={'block_id': block_id},
                UpdateExpression="""
                    SET ref_count = if_not_exists(ref_count, :zero) - :dec,
                        last_accessed = :now,
                        zero_reached_at = if_not_exists(zero_reached_at, :null)
                """,
                ConditionExpression="ref_count > :zero",  # attribute_not_exists 제거: 항목 없을 때도 통과하면 0-1=-1 음수 발생
                ExpressionAttributeValues={
                    ':dec': 1,
                    ':zero': 0,
                    ':now': datetime.utcnow().isoformat(),
                    ':null': None
                },
                ReturnValues="ALL_NEW"
            )
            
            new_count = response.get('Attributes', {}).get('ref_count', 0)
            is_frozen = response.get('Attributes', {}).get('is_frozen', False)
            zero_reached_at = response.get('Attributes', {}).get('zero_reached_at')
            
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
            # [Deletion Criterion] ref_count = 0 AND graceful_wait 경과
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            if new_count == 0:
                # 카운트가 0이 된 시점 기록 (첫 번째 도달 시만)
                if not zero_reached_at:
                    self.ref_table.update_item(
                        Key={'block_id': block_id},
                        UpdateExpression="SET zero_reached_at = :now",
                        ExpressionAttributeValues={':now': datetime.utcnow().isoformat()}
                    )
                    logger.debug(
                        f"[GC] Block {block_id[:8]}... ref_count=0 (first time), "
                        f"entering graceful_wait ({graceful_wait_seconds}s)"
                    )
                    return False  # 아직 삭제하지 않음 (graceful_wait 시작)
                
                # graceful_wait 경과 확인
                try:
                    zero_time = datetime.fromisoformat(zero_reached_at)
                except (ValueError, TypeError) as parse_err:
                    logger.warning(
                        f"[GC] Block {block_id[:8]}... invalid zero_reached_at format "
                        f"'{zero_reached_at}': {parse_err} — skipping"
                    )
                    return False
                elapsed = (datetime.utcnow() - zero_time).total_seconds()
                
                if elapsed >= graceful_wait_seconds:
                    logger.debug(
                        f"[GC] Block {block_id[:8]}... ref_count=0, graceful_wait elapsed "
                        f"({elapsed:.0f}s), marking for deletion"
                    )
                    return True  # 삭제 가능
                else:
                    logger.debug(
                        f"[GC] Block {block_id[:8]}... ref_count=0, graceful_wait in progress "
                        f"({elapsed:.0f}s / {graceful_wait_seconds}s)"
                    )
                    return False  # 아직 대기 중
            else:
                logger.debug(
                    f"[GC] Block {block_id[:8]}... still referenced "
                    f"(ref_count={new_count}), skipping"
                )
                return False
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                # ref_count가 이미 0이거나 존재하지 않음 (음수 방지 조건 실패)
                logger.debug(
                    f"[GC] Block {block_id[:8]}... already at ref_count=0, "
                    f"checking graceful_wait eligibility"
                )
                # 현재 상태 조회하여 graceful_wait 확인
                try:
                    item_response = self.ref_table.get_item(Key={'block_id': block_id})
                    item = item_response.get('Item', {})
                    zero_reached_at = item.get('zero_reached_at')
                    
                    if zero_reached_at:
                        try:
                            zero_time = datetime.fromisoformat(zero_reached_at)
                        except (ValueError, TypeError) as parse_err:
                            logger.warning(
                                f"[GC] Block {block_id[:8]}... invalid zero_reached_at "
                                f"'{zero_reached_at}': {parse_err} — treating as not ready"
                            )
                            return False
                        elapsed = (datetime.utcnow() - zero_time).total_seconds()
                        return elapsed >= graceful_wait_seconds
                except Exception:
                    pass
                return False
            elif e.response['Error']['Code'] == 'ResourceNotFoundException':
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [v2.1] Rollback Orphaned Blocks Detection (Agent Governance)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def mark_rollback_orphans(
    rollback_manifest_id: str,
    abandoned_branch_root: str,
    grace_period_days: int = 30
) -> Dict[str, Any]:
    """
    [Agent Governance v2.1] Optimistic Rollback 시 버려진 상태 블록 탐지
    
    Optimistic Rollback Policy 연동:
    - Governor가 AnomalyScore > 0.5 감지 시 _kernel_rollback_to_manifest 실행
    - Rollback으로 버려진 브랜치의 모든 블록을 "rollback_orphaned" 태그
    - 30일 grace period 후 자동 삭제 (TTL 설정)
    
    동작 원리:
    1. rollback_manifest_id부터 parent_hash 체인 역추적
    2. abandoned_branch_root부터 시작된 분기점 찾기
    3. 분기된 브랜치의 모든 매니페스트 및 블록 태그
    4. 30일 후 TTL 만료로 자동 GC
    
    Args:
        rollback_manifest_id: Rollback 대상 매니페스트 ID (Safe Manifest)
        abandoned_branch_root: 버려진 브랜치의 시작 매니페스트 ID
        grace_period_days: 삭제 유예 기간 (기본 30일)
    
    Returns:
        처리 통계 딕셔너리
        {
            'orphaned_manifests': int,
            'orphaned_blocks': int,
            'grace_period_expires_at': str (ISO timestamp)
        }
    """
    import time
    
    dynamodb = boto3.resource('dynamodb')
    manifest_table_name = os.environ.get(
        'WORKFLOW_MANIFESTS_TABLE', 'WorkflowManifests-v3-dev'
    )
    ref_table_name = os.environ.get(
        'BLOCK_REF_COUNT_TABLE', 'BlockReferenceCounts-dev'
    )
    
    manifest_table = dynamodb.Table(manifest_table_name)
    ref_table = dynamodb.Table(ref_table_name)
    
    stats = {
        'orphaned_manifests': 0,
        'orphaned_blocks': 0,
        'grace_period_expires_at': None
    }
    
    try:
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 1. 버려진 브랜치 탐색 (DFS로 parent_hash 체인 추적)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        orphaned_manifests = _traverse_orphaned_branch(
            manifest_table,
            abandoned_branch_root,
            rollback_manifest_id
        )
        
        if not orphaned_manifests:
            logger.info(
                f"[GC] [Rollback Orphans] No orphaned manifests found "
                f"(branch_root={abandoned_branch_root[:8]}...)"
            )
            return stats
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 2. TTL 설정 (grace_period_days 후 자동 삭제)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        expiry_timestamp = int(time.time()) + (grace_period_days * 24 * 3600)
        expiry_iso = datetime.fromtimestamp(expiry_timestamp).isoformat()
        stats['grace_period_expires_at'] = expiry_iso
        
        orphaned_blocks_set = set()
        
        for manifest_id in orphaned_manifests:
            try:
                # 매니페스트 태그 및 TTL 설정
                response = manifest_table.update_item(
                    Key={'manifest_id': manifest_id},
                    UpdateExpression="""
                        SET rollback_orphaned = :true,
                            rollback_reason = :reason,
                            orphaned_at = :now,
                            ttl = :ttl
                    """,
                    ExpressionAttributeValues={
                        ':true': True,
                        ':reason': f"Optimistic Rollback to {rollback_manifest_id[:8]}...",
                        ':now': datetime.utcnow().isoformat(),
                        ':ttl': expiry_timestamp
                    },
                    ReturnValues='ALL_NEW'
                )
                
                stats['orphaned_manifests'] += 1
                
                # 블록 ID 추출 및 태그
                item = response.get('Attributes', {})
                block_paths = item.get('s3_pointers', {}).get('state_blocks', [])
                
                for block_path in block_paths:
                    block_id = block_path.split('/')[-1].replace('.json', '')
                    orphaned_blocks_set.add(block_id)
                    
                    # Reference Table에 orphaned 태그
                    ref_table.update_item(
                        Key={'block_id': block_id},
                        UpdateExpression="""
                            SET rollback_orphaned = :true,
                                orphaned_manifest_id = :manifest_id,
                                orphaned_at = :now
                        """,
                        ExpressionAttributeValues={
                            ':true': True,
                            ':manifest_id': manifest_id,
                            ':now': datetime.utcnow().isoformat()
                        }
                    )
                
            except Exception as e:
                logger.error(
                    f"[GC] [Rollback Orphans] Failed to tag manifest {manifest_id[:8]}...: {e}"
                )
        
        stats['orphaned_blocks'] = len(orphaned_blocks_set)
        
        logger.warning(
            f"🗑️ [GC] [Rollback Orphans] Marked {stats['orphaned_manifests']} manifests "
            f"and {stats['orphaned_blocks']} blocks for deletion. "
            f"Grace period: {grace_period_days} days (expires {expiry_iso})"
        )
        
    except Exception as e:
        logger.error(f"[GC] [Rollback Orphans] Failed to mark orphans: {e}", exc_info=True)
    
    return stats


def _traverse_orphaned_branch(
    manifest_table,
    branch_root: str,
    safe_manifest: str
) -> List[str]:
    """
    버려진 브랜치의 모든 매니페스트 탐색 (DFS with ParentHashIndex GSI)
    
    [v2.1.1] Performance: O(Depth) instead of O(N) full table scan
    - ParentHashIndex GSI를 사용하여 parent_hash로 자식 매니페스트 조회
    - DFS로 버려진 브랜치 전체를 재귀적으로 탐색
    - 수만 개의 매니페스트가 있어도 빠르게 처리 가능
    
    Args:
        manifest_table: DynamoDB 매니페스트 테이블
        branch_root: 버려진 브랜치의 시작점
        safe_manifest: Rollback 대상 (안전한 매니페스트)
    
    Returns:
        버려진 매니페스트 ID 리스트
    """
    orphaned = []
    visited = set()
    stack = [branch_root]
    
    while stack:
        manifest_id = stack.pop()
        
        if manifest_id in visited or manifest_id == safe_manifest:
            continue
        
        visited.add(manifest_id)
        
        try:
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 1. 현재 매니페스트 로드
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            response = manifest_table.get_item(Key={'manifest_id': manifest_id})
            
            if 'Item' not in response:
                continue
            
            item = response['Item']
            orphaned.append(manifest_id)
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 2. ParentHashIndex GSI로 자식 매니페스트 조회 (O(1) query)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            current_hash = item.get('manifest_hash', '')
            
            if not current_hash:
                continue
            
            try:
                # ParentHashIndex GSI를 사용하여 현재 매니페스트를 부모로 하는 자식들 찾기
                children_response = manifest_table.query(
                    IndexName='ParentHashIndex',
                    KeyConditionExpression='parent_hash = :hash',
                    ExpressionAttributeValues={':hash': current_hash},
                    ProjectionExpression='manifest_id'
                )
                
                # 자식 매니페스트들을 스택에 추가 (DFS 계속)
                for child_item in children_response.get('Items', []):
                    child_id = child_item.get('manifest_id')
                    if child_id and child_id not in visited:
                        stack.append(child_id)
                        logger.debug(
                            f"[GC] [Orphan Traversal] Found child {child_id[:8]}... "
                            f"of parent {manifest_id[:8]}..."
                        )
            
            except ClientError as gsi_error:
                # GSI가 아직 생성되지 않았거나 에러 발생 시 로깅만
                logger.warning(
                    f"[GC] [Orphan Traversal] ParentHashIndex query failed for "
                    f"{manifest_id[:8]}...: {gsi_error}. "
                    f"GSI may not be deployed yet."
                )
                # 자식 탐색 실패 시에도 현재 매니페스트는 orphaned 리스트에 추가됨
            
        except Exception as e:
            logger.error(f"[GC] Failed to traverse manifest {manifest_id[:8]}...: {e}")
    
    return orphaned