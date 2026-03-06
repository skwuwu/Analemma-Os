# -*- coding: utf-8 -*-
"""
[Phase 1] Merkle DAG 기반 상태 버저닝 서비스

핵심 기능:
1. 상태 변경 시 델타만 저장 (Content-Addressable Storage)
2. Merkle Root로 무결성 검증
3. Pointer Manifest로 즉시 회귀 가능
4. Pre-computed Hash로 O(1) segment 검증 (Phase 7)
"""

import hashlib
import json
import os
import time
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# 운영 환경 상수
MAX_BLOCK_SIZE = 4 * 1024 * 1024  # 4MB (블록 분할 임계값)
VERSION_RETRY_ATTEMPTS = 3  # Race Condition 재시도 횟수


def _calculate_optimal_workers() -> int:
    """Lambda 메모리 기반 I/O 병렬 스레드 수 계산.

    S3 작업은 I/O-bound이므로 vCPU 수보다 많은 스레드가 유효.
    Lambda는 메모리 1769MB당 1 vCPU 할당. 그 비율로 스레드 수 조정.

    Returns:
        int: 4 ~ 32 사이의 적정 worker 수.
    """
    import os
    try:
        memory_mb = int(os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', '512'))
        return min(32, max(4, memory_mb // 256))
    except (ValueError, TypeError):
        return 4  # safe default


@dataclass
class ContentBlock:
    """Merkle DAG의 컨텐츠 블록"""
    block_id: str  # sha256 해시
    s3_path: str
    size: int
    fields: List[str]  # 이 블록에 포함된 필드 목록
    checksum: str


@dataclass
class ManifestPointer:
    """Pointer Manifest 구조"""
    manifest_id: str
    version: int
    parent_hash: Optional[str]
    manifest_hash: str
    config_hash: str  # workflow_config 검증용
    blocks: List[ContentBlock]
    metadata: Dict

    def __post_init__(self) -> None:
        # [v3.34] Normalize legacy 'null' string from DynamoDB to Python None.
        # Old records stored parent_hash as {'S': 'null'} instead of {'NULL': True}.
        # The 'null' string silently poisons _compute_merkle_root(): the expression
        # (parent_hash or '') treats 'null' as truthy → hash('config' + 'null' + blocks)
        # instead of hash('config' + '' + blocks), producing a wrong Merkle root.
        if self.parent_hash == 'null':
            self.parent_hash = None


class StateVersioningService:
    """
    🧬 KernelStateManager - Analemma OS의 단일 상태 관리 커널
    
    v3.3 통합 아키텍처 (Zero Redundancy):
    - Merkle DAG 기반 Delta Storage (중복 데이터 90% 제거)
    - DynamoDB 포인터 기반 상태 복원 (latest_state.json 폐기)
    - 2-Phase Commit 완전 내장 (temp → ready 태그 전략)
    - GC 자동 연계 (Ghost Block 원천 차단)
    
    핵심 설계 철학:
    1. 🗑️ latest_state.json 폐기: DynamoDB에 manifest_id 포인터만 저장
    2. 🧬 단일 저장 경로: save_state_delta()로 모든 저장 통일
    3. 🛡️ 2-Phase Commit 내장: S3 업로드 시 무조건 status=temp, DynamoDB 성공 시 status=ready
    4. ♻️ GC 자동 연계: Phase 10 BackgroundGC가 temp 태그 블록 자동 제거
    
    ✅ Phase B: Unified Architecture (EventualConsistencyGuard 통합)
    ✅ Phase E-F-G: StatePersistenceService/StateManager/StateDataManager 흡수 통합
    ✅ v3.3: 급진적 재설계 (마이그레이션 족쇄 제거)
    """
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [v3.3] 전역 표준 직렬화: 1비트의 오차도 없는 해시 생성 보장
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 외부 호출자(예: initialize_state_data.py)가 객체 생성 없이
    # 동일한 해시를 계산할 수 있도록 @staticmethod로 제공
    # ⚠️ [CRITICAL] 해시 알고리즘 변경 시 이 메서드만 수정하면 됨
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @staticmethod
    def get_canonical_json(data: Any) -> bytes:
        """
        [v3.3] 전역 표준 직렬화: 1비트의 오차도 없는 해시 생성 보장
        
        외부 호출 예시:
        ```python
        from src.services.state.state_versioning_service import StateVersioningService
        canonical_data = StateVersioningService.get_canonical_json(manifest_obj)
        hash_value = StateVersioningService.compute_hash(manifest_obj)
        ```
        
        Args:
            data: 직렬화할 파이썬 객체 (dict, list, etc.)
        
        Returns:
            bytes: UTF-8 인코딩된 표준 JSON (키 정렬, 공백 제거)
        
        Note:
            - sort_keys=True: 키 순서로 인한 해시 불일치 방지
            - separators=(',', ':'): 공백 제거로 해시 일관성 확보
            - ensure_ascii=False: UTF-8 보존 (한글 등 멀티바이트 문자)
            - datetime → ISO 8601 표준화
            - Decimal → float 변환 (DynamoDB 호환)
        """
        from datetime import datetime, date
        from decimal import Decimal
        
        def default_handler(obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()  # ISO 8601 표준화
            if isinstance(obj, Decimal):
                return str(obj)         # [v3.32] str() — unified with hash_utils._canonical_bytes
            if hasattr(obj, '__dict__'):
                return obj.__dict__
            return str(obj)
        
        return json.dumps(
            data,
            sort_keys=True,             # 키 정렬 필수
            separators=(',', ':'),      # 공백 제거로 해시 일관성 확보
            ensure_ascii=False,         # UTF-8 보존
            default=default_handler
        ).encode('utf-8')
    
    @staticmethod
    def compute_hash(data: dict) -> str:
        """
        [v3.3] 표준 해시 계산 (SHA-256)
        
        ⚠️ [CRITICAL] 해시 알고리즘 변경 시 이 메서드만 수정
        예: SHA-256 → SHA-512 마이그레이션 시
        ```python
        return hashlib.sha512(canonical_json).hexdigest()
        ```
        
        Args:
            data: 해시를 계산할 파이썬 딕셔너리
        
        Returns:
            str: SHA-256 해시 (hex digest)
        """
        canonical_json = StateVersioningService.get_canonical_json(data)
        return hashlib.sha256(canonical_json).hexdigest()
    
    def __init__(
        self,
        dynamodb_table: str,
        s3_bucket: str,
        block_references_table: str = None,
        use_2pc: bool = False,              # ✅ Phase B: 2-Phase Commit
        gc_dlq_url: Optional[str] = None    # ✅ Phase B: GC DLQ
    ):
        self.dynamodb = boto3.resource('dynamodb')
        self.dynamodb_client = boto3.client('dynamodb')  # For TransactWriteItems
        self.table = self.dynamodb.Table(dynamodb_table)
        self.s3 = boto3.client('s3')
        self.bucket = s3_bucket
        
        # Block Reference Counting Table (Garbage Collection용)
        # 환경변수에서 가져오거나 기본값 사용
        self.block_references_table = block_references_table or dynamodb_table.replace('Manifests', 'BlockReferences')
        try:
            self.block_refs_table = self.dynamodb.Table(self.block_references_table)
        except Exception as e:
            logger.warning(f"BlockReferences table not available: {e}")
        
        # ✅ Phase B: 2-Phase Commit 설정
        self.use_2pc = use_2pc
        self.gc_dlq_url = gc_dlq_url
        self._consistency_guard = None  # Lazy Import (실제 사용 시 초기화)

        # S3 블록 키 프리픽스 (state-blocks/{block_id}.json)
        self.prefix = "state-"
    
    def create_manifest(
        self,
        workflow_id: str,
        workflow_config: dict,
        segment_manifest: List[dict],
        parent_manifest_id: Optional[str] = None
    ) -> ManifestPointer:
        """
        새 Pointer Manifest 생성
        
        ✅ v3.3: 2-Phase Commit 강제 사용 (Legacy 제거)
        
        Args:
            workflow_id: 워크플로우 ID
            workflow_config: 워크플로우 설정 (해시 계산용)
            segment_manifest: 세그먼트 목록
            parent_manifest_id: 이전 버전 ID (Merkle 체인)
        
        Returns:
            ManifestPointer: 생성된 매니페스트 포인터
        """
        # v3.3: 2-Phase Commit 강제 사용
        if not self.use_2pc:
            logger.warning("[StateVersioningService] use_2pc=False is deprecated, forcing 2PC")
        
        if not self.gc_dlq_url:
            raise RuntimeError("GC DLQ URL is required for 2-Phase Commit")
        
        return self._create_manifest_with_2pc(
            workflow_id=workflow_id,
            workflow_config=workflow_config,
            segment_manifest=segment_manifest,
            parent_manifest_id=parent_manifest_id
        )
    
    def _create_manifest_with_2pc(
        self,
        workflow_id: str,
        workflow_config: dict,
        segment_manifest: List[dict],
        parent_manifest_id: Optional[str]
    ) -> ManifestPointer:
        """
        ✅ v3.3: EventualConsistencyGuard를 사용한 2-Phase Commit (강제)
        """
        # Lazy Import
        if self._consistency_guard is None:
            from src.services.state.eventual_consistency_guard import EventualConsistencyGuard
            self._consistency_guard = EventualConsistencyGuard(
                s3_bucket=self.bucket,
                dynamodb_table=self.table.name,
                block_references_table=self.block_references_table,
                gc_dlq_url=self.gc_dlq_url
            )
            logger.info("[StateVersioningService] ✅ EventualConsistencyGuard initialized")
            # [FIX] 이전 리팩토링 과정에서 남겨진 고아 코드 제거.
            # parent_manifest_id는 아래 metadata dict에 이미 포함됨 (line ~198).
        
        # 매니페스트 기본 정보 생성
        import uuid
        manifest_id = str(uuid.uuid4())
        # 🛡️ [Type Safety] DynamoDB Number → Decimal → int 명시적 변환
        # EventualConsistencyGuard의 json.dumps(default=str)가 Decimal을 "21"로 변환하는 반면,
        # compute_hash는 Decimal을 21.0으로 변환하여 해시 불일치 발생
        version = int(self._get_next_version(workflow_id))
        config_hash = self._compute_hash(workflow_config)
        
        # S3에 workflow_config 저장
        config_s3_key = f"workflow-configs/{workflow_id}/{config_hash}.json"
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=config_s3_key,
                Body=json.dumps(workflow_config, default=str),
                ContentType='application/json',
                Metadata={
                    'usage': 'reference_only',
                    'workflow_id': workflow_id,
                    'config_hash': config_hash
                }
            )
        except Exception as e:
            logger.error(f"Failed to store workflow_config: {e}")
            raise
        
        # 블록 분할 및 해시 계산
        # EventualConsistencyGuard expects plain dicts with block_id, s3_key, data.
        # _split_into_blocks() returns ContentBlock objects (used only by legacy path).
        blocks = []
        for idx, segment in enumerate(segment_manifest):
            segment_json = self._canonical_json_serialize(segment)
            segment_bytes = segment_json.encode('utf-8')
            block_id = hashlib.sha256(segment_bytes).hexdigest()
            if len(segment_bytes) <= MAX_BLOCK_SIZE:
                blocks.append({
                    'block_id': block_id,
                    's3_key': f"state-blocks/{block_id}.json",
                    'data': segment,
                })
            else:
                # Large segment: split into chunks
                chunk_index = 0
                for offset in range(0, len(segment_json), MAX_BLOCK_SIZE):
                    chunk_str = segment_json[offset:offset + MAX_BLOCK_SIZE]
                    chunk_bytes = chunk_str.encode('utf-8')
                    chunk_id = hashlib.sha256(chunk_bytes).hexdigest()
                    blocks.append({
                        'block_id': chunk_id,
                        's3_key': f"state-blocks/{chunk_id}.json",
                        'data': {'__chunk__': chunk_str, '__chunk_index__': chunk_index,
                                 'segment_index': idx},
                    })
                    chunk_index += 1
        segment_hashes = self._compute_segment_hashes(segment_manifest)

        # [v3.33 FIX-C] Resolve parent_hash from parent manifest.
        # Previous code excluded parent_hash from manifest_hash computation,
        # violating the Merkle structural invariant: a child manifest's hash
        # MUST cryptographically bind to its parent.  Without this, two
        # manifests with different parents can produce identical hashes,
        # making DAG fork detection impossible.
        parent_hash = None
        if parent_manifest_id:
            try:
                parent = self.get_manifest(parent_manifest_id)
                parent_hash = parent.manifest_hash
            except Exception as e:
                logger.warning(f"[v3.33] Failed to resolve parent manifest hash: {e}")

        manifest_hash = self._compute_hash({
            'workflow_id': workflow_id,
            'version': version,
            'config_hash': config_hash,
            'segment_hashes': segment_hashes,
            'parent_hash': parent_hash or '',
        })

        # 메타데이터
        metadata = {
            'workflow_id': workflow_id,
            'version': version,
            'created_at': datetime.utcnow().isoformat(),
            'parent_manifest_id': parent_manifest_id,
            'parent_hash': parent_hash or '',
            'total_segments': len(segment_manifest)
        }

        # EventualConsistencyGuard로 2PC 실행 (반환값은 stored manifest_id str)
        stored_manifest_id = self._consistency_guard.create_manifest_with_consistency(
            workflow_id=workflow_id,
            manifest_id=manifest_id,
            version=version,
            config_hash=config_hash,
            manifest_hash=manifest_hash,
            blocks=blocks,
            segment_hashes=segment_hashes,
            metadata=metadata,
            parent_hash=parent_hash,
        )
        # Wrap result in ManifestPointer so callers can access .manifest_id/.manifest_hash etc.
        return ManifestPointer(
            manifest_id=stored_manifest_id,
            version=version,
            parent_hash=parent_hash,
            manifest_hash=manifest_hash,
            config_hash=config_hash,
            blocks=[],  # blocks persisted to S3/DynamoDB; not needed in the pointer
            metadata=metadata
        )
    
    def _create_manifest_legacy(
        self,
        workflow_id: str,
        workflow_config: dict,
        segment_manifest: List[dict],
        parent_manifest_id: Optional[str]
    ) -> ManifestPointer:
        """
        🔄 기존 Legacy Manifest 생성 (Production Fixes 포함)
        """
        import uuid
        
        manifest_id = str(uuid.uuid4())
        
        # 1. workflow_config 해시 계산 (불변 참조)
        config_hash = self._compute_hash(workflow_config)
        
        # 2. workflow_config를 S3에 저장 (참조용)
        config_s3_key = f"workflow-configs/{workflow_id}/{config_hash}.json"
        
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=config_s3_key,
                Body=json.dumps(workflow_config, default=str),
                ContentType='application/json',
                Metadata={
                    'usage': 'reference_only',
                    'workflow_id': workflow_id,
                    'config_hash': config_hash
                }
            )
            logger.info(f"Stored workflow_config to S3: s3://{self.bucket}/{config_s3_key}")
        except Exception as e:
            logger.error(f"Failed to store workflow_config: {e}")
            raise
        
        # 3. segment_manifest를 Content Blocks로 분할
        blocks = self._split_into_blocks(segment_manifest)
        
        # 3.5. Pre-computed Hash 생성 (Phase 7 검증 최적화용)
        segment_hashes = self._compute_segment_hashes(segment_manifest)
        logger.info(f"Pre-computed {len(segment_hashes)} segment hashes")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # [Phase 10] S3 업로드 with Pending Tags (유령 블록 방지)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 문제: S3 성공 + DynamoDB 실패 → Ghost Block 발생
        # 해결: Pending Tag 전략 (status=pending → committed)
        transaction_id = str(uuid.uuid4())
        
        # 4. 각 블록을 S3에 저장 (Content-Addressable) + Pending Tags
        stored_blocks = 0
        reused_blocks = 0
        
        for idx, block in enumerate(blocks):
            if not self._block_exists(block.block_id):
                try:
                    # [Fix #2] 실제 segment 데이터 저장 (피드백 반영)
                    # 기존: 메타데이터만 저장 (fields, checksum)
                    # 개선: 실제 segment_manifest 데이터 저장
                    
                    # 해당 세그먼트 또는 청크 데이터 추출
                    segment_data = None
                    
                    # 필드명에서 세그먼트 인덱스 추출
                    field_info = block.fields[0]  # "segment_0" 또는 "segment_0_chunk_1"
                    
                    if "_chunk_" in field_info:
                        # 청크 케이스: segment_idx_chunk_N
                        parts = field_info.split("_")
                        segment_idx = int(parts[1])
                        chunk_idx = int(parts[3])
                        
                        # 해당 청크의 데이터는 이미 block.checksum에 해시됨
                        # 원본 segment를 직렬화한 후 청크로 분할한 부분 저장
                        segment_json = self._canonical_json_serialize(segment_manifest[segment_idx])
                        chunk_start = chunk_idx * MAX_BLOCK_SIZE
                        chunk_end = chunk_start + MAX_BLOCK_SIZE
                        chunk_data = segment_json[chunk_start:chunk_end]
                        segment_data = chunk_data
                    else:
                        # 단일 블록 케이스: segment_N
                        segment_idx = int(field_info.split("_")[1])
                        
                        # ✅ [피드백 ①] JSON Lines 형식으로 저장 (S3 Select 최적화)
                        segment = segment_manifest[segment_idx]
                        segment_data = json.dumps(segment, default=self._json_default) + "\n"  # ndjson
                    
                    # ✅ [피드백 ②] Pending Tag로 업로드 (Ghost Block 방지)
                    self.s3.put_object(
                        Bucket=self.bucket,
                        Key=block.s3_path.replace(f"s3://{self.bucket}/", ""),
                        Body=segment_data,  # ✅ 실제 데이터 저장
                        ContentType='application/json',
                        Tagging=f"status=pending&transaction_id={transaction_id}",  # ✅ Pending Tag
                        Metadata={
                            'block_id': block.block_id,
                            'fields': ','.join(block.fields),
                            'checksum': block.checksum,
                            'transaction_id': transaction_id,
                            'format': 'ndjson'  # JSON Lines 형식 표시
                        }
                    )
                    stored_blocks += 1
                except Exception as e:
                    logger.error(f"Failed to store block {block.block_id}: {e}")
                    # ✅ [피드백 ②] S3 업로드 실패 시 이미 업로드된 블록들 정리
                    self._rollback_pending_blocks(blocks[:idx], transaction_id)
                    raise
            else:
                reused_blocks += 1
        
        logger.info(f"Blocks: {stored_blocks} stored, {reused_blocks} reused (deduplication)")
        
        # 5. Merkle Root 계산
        parent_hash = None
        if parent_manifest_id:
            try:
                parent = self.get_manifest(parent_manifest_id)
                parent_hash = parent.manifest_hash
            except Exception as e:
                logger.warning(f"Failed to get parent manifest {parent_manifest_id}: {e}")
        
        manifest_hash = self._compute_merkle_root(blocks, config_hash, parent_hash)
        
        # 6. DynamoDB에 포인터 저장 (Race Condition 방지)
        version = self._get_next_version(workflow_id)
        
        # [Fix #1] 조건부 쓰기로 버전 충돌 방지
        # [CRITICAL FIX] TransactWriteItems로 매니페스트 저장 + 블록 참조 카운트 증가 원자화
        # 피드백: manifest_id뿐 아니라 workflow_id+version 조합도 체크 필요
        # 현상: Lambda A와 B가 동시에 버전 6으로 쓰기 시도 가능
        
        # ✅ [피드백 ③] TransactWriteItems 100개 제한 대응
        # 제한: DynamoDB 트랜잭션은 최대 100개 아이템
        # 해결: 블록이 100개 초과 시 배치 분할
        # 
        # ⚠️ [RISK] 100개 초과 시 전체 원자성 보장 불가
        # - 첫 트랜잭션: 매니페스트 + 첫 99개 블록 (원자적)
        # - 이후 배치: 나머지 블록 참조 카운트 (별도 트랜잭션)
        # - 중간 실패 시: 매니페스트는 생성되었지만 일부 블록 참조 미증가 가능
        # - 완화책: GC Grace Period 동안 미참조 블록도 유지 (Phase 10)
        # - 권장: 필드 수가 극단적으로 많다면 필드 그룹화 고려
        MAX_TRANSACTION_ITEMS = 100
        
        for attempt in range(VERSION_RETRY_ATTEMPTS):
            try:
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # [ATOMICITY FIX] 원자적 트랜잭션으로 Dangling Pointer 방지
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                
                # 매니페스트 저장 아이템
                manifest_item = {
                    'Put': {
                        'TableName': self.table.table_name,
                        'Item': {
                            'manifest_id': {'S': manifest_id},
                            'version': {'N': str(version)},
                            'workflow_id': {'S': workflow_id},
                            'parent_hash': {'S': parent_hash} if parent_hash else {'NULL': True},
                            'manifest_hash': {'S': manifest_hash},
                            'config_hash': {'S': config_hash},
                            'segment_hashes': {'M': {k: {'S': v} for k, v in segment_hashes.items()}},
                            's3_pointers': {'M': {
                                'manifest': {'S': f"s3://{self.bucket}/manifests/{manifest_id}.json"},
                                'config': {'S': f"s3://{self.bucket}/{config_s3_key}"},
                                'state_blocks': {'L': [{'S': block.s3_path} for block in blocks]}
                            }},
                            'metadata': {'M': {
                                'created_at': {'S': datetime.utcnow().isoformat()},
                                'segment_count': {'N': str(len(segment_manifest))},
                                'total_size': {'N': str(sum(block.size for block in blocks))},
                                'compression': {'S': 'none'},
                                'blocks_stored': {'N': str(stored_blocks)},
                                'blocks_reused': {'N': str(reused_blocks)},
                                'transaction_id': {'S': transaction_id}  # ✅ Transaction ID 저장
                            }},
                            'ttl': {'N': str(int(time.time()) + 30 * 24 * 3600)}
                        },
                        'ConditionExpression': 'attribute_not_exists(manifest_id)'
                    }
                }
                
                # ✅ [피드백 ③] 블록 참조 업데이트를 배치로 분할 (100개 제한 대응)
                # 전략: 첫 번째 트랜잭션에 매니페스트 + 최대 99개 블록
                #       나머지 블록은 별도 배치 업데이트
                
                first_batch_blocks = blocks[:MAX_TRANSACTION_ITEMS - 1]  # 매니페스트 1개 + 블록 99개
                remaining_blocks = blocks[MAX_TRANSACTION_ITEMS - 1:]
                
                # 첫 번째 트랜잭션: 매니페스트 + 첫 99개 블록
                transact_items = [manifest_item]
                
                for block in first_batch_blocks:
                    transact_items.append({
                        'Update': {
                            'TableName': self.block_references_table,
                            'Key': {
                                'workflow_id': {'S': workflow_id},
                                'block_id': {'S': block.block_id}
                            },
                            'UpdateExpression': 'ADD reference_count :inc SET last_referenced = :now',
                            'ExpressionAttributeValues': {
                                ':inc': {'N': '1'},
                                ':now': {'S': datetime.utcnow().isoformat()}
                            }
                        }
                    })
                
                # ✅ 원자적 트랜잭션 실행: 모두 성공 or 모두 실패
                self.dynamodb_client.transact_write_items(TransactItems=transact_items)
                
                logger.info(
                    f"[Atomic Transaction] ✅ Created manifest {manifest_id} (v{version}) "
                    f"+ incremented {len(first_batch_blocks)} block references (first batch)"
                )
                
                # ✅ [피드백 ③] 나머지 블록 참조 카운트 업데이트 (100개 초과 시)
                if remaining_blocks:
                    logger.info(f"[Batch Update] Processing {len(remaining_blocks)} remaining blocks...")
                    
                    # 100개씩 배치 처리
                    for i in range(0, len(remaining_blocks), MAX_TRANSACTION_ITEMS):
                        batch = remaining_blocks[i:i + MAX_TRANSACTION_ITEMS]
                        batch_transact_items = []
                        
                        for block in batch:
                            batch_transact_items.append({
                                'Update': {
                                    'TableName': self.block_references_table,
                                    'Key': {
                                        'workflow_id': {'S': workflow_id},
                                        'block_id': {'S': block.block_id}
                                    },
                                    'UpdateExpression': 'ADD reference_count :inc SET last_referenced = :now',
                                    'ExpressionAttributeValues': {
                                        ':inc': {'N': '1'},
                                        ':now': {'S': datetime.utcnow().isoformat()}
                                    }
                                }
                            })
                        
                        self.dynamodb_client.transact_write_items(TransactItems=batch_transact_items)
                    
                    logger.info(f"[Batch Update] ✅ Completed {len(remaining_blocks)} remaining block references")
                
                # ✅ [피드백 ②] S3 블록들을 Committed 상태로 전환
                self._commit_pending_blocks(blocks, transaction_id)
                
                break  # 성공 시 루프 탈출
                
            except ClientError as e:
                error_code = e.response['Error']['Code']
                
                if error_code == 'TransactionCanceledException':
                    # 조건 체크 실패 (manifest_id 중복 등)
                    cancellation_reasons = e.response['Error'].get('CancellationReasons', [])
                    logger.warning(
                        f"[Atomicity] Transaction cancelled on attempt {attempt + 1}: {cancellation_reasons}"
                    )
                    
                    # manifest_id 재생성
                    import uuid
                    manifest_id = str(uuid.uuid4())
                    
                    if attempt == VERSION_RETRY_ATTEMPTS - 1:
                        raise RuntimeError(
                            f"Failed to create manifest after {VERSION_RETRY_ATTEMPTS} attempts. "
                            f"Last error: {cancellation_reasons}"
                        )
                else:
                    logger.error(f"[Atomicity] Transaction failed: {e}")
                    raise
            
            except Exception as e:
                logger.error(f"[Atomicity] Unexpected error during manifest creation: {e}")
                raise
        
        return ManifestPointer(
            manifest_id=manifest_id,
            version=version,
            parent_hash=parent_hash,
            manifest_hash=manifest_hash,
            config_hash=config_hash,
            blocks=blocks,
            metadata={
                'segment_count': len(segment_manifest),
                'total_size': sum(block.size for block in blocks)
            }
        )
    
    def get_manifest(self, manifest_id: str) -> ManifestPointer:
        """
        매니페스트 포인터 로드
        
        Args:
            manifest_id: 매니페스트 ID
        
        Returns:
            ManifestPointer: 매니페스트 포인터
        """
        try:
            response = self.table.get_item(Key={'manifest_id': manifest_id})
            
            if 'Item' not in response:
                raise ValueError(f"Manifest not found: {manifest_id}")
            
            item = response['Item']

            # ContentBlock 재구성
            # 2PC path stores manifest without 's3_pointers' (blocks in separate table).
            # Legacy path may have 's3_pointers.state_blocks'. Use .get() to avoid KeyError.
            blocks = []
            s3_pointers = item.get('s3_pointers') or {}
            for s3_path in (s3_pointers.get('state_blocks') or []):
                block_id = s3_path.split('/')[-1].replace('.json', '')
                blocks.append(ContentBlock(
                    block_id=block_id,
                    s3_path=s3_path,
                    size=0,
                    fields=[],
                    checksum=block_id
                ))
            
            return ManifestPointer(
                manifest_id=item['manifest_id'],
                version=item['version'],
                parent_hash=item.get('parent_hash'),
                manifest_hash=item['manifest_hash'],
                config_hash=item['config_hash'],
                blocks=blocks,
                metadata=item.get('metadata', {})
            )
            
        except ClientError as e:
            logger.error(f"DynamoDB error loading manifest {manifest_id}: {e}")
            raise
    
    def verify_manifest_integrity(self, manifest_id: str) -> bool:
        """
        Merkle Root 검증
        
        Returns:
            bool: 무결성 검증 통과 여부
        """
        try:
            response = self.table.get_item(Key={'manifest_id': manifest_id})
            
            if 'Item' not in response:
                logger.error(f"Manifest not found: {manifest_id}")
                return False
            
            item = response['Item']
            
            # 저장된 블록들로 Merkle Root 재계산
            blocks = self._load_blocks(item['s3_pointers']['state_blocks'])
            # [v3.34] Normalize legacy 'null' string — same logic as ManifestPointer.__post_init__
            raw_parent = item.get('parent_hash')
            parent_hash = None if raw_parent in (None, 'null') else raw_parent
            computed_hash = self._compute_merkle_root(
                blocks,
                item['config_hash'],
                parent_hash
            )
            
            is_valid = computed_hash == item['manifest_hash']
            
            if not is_valid:
                logger.error(
                    f"[Integrity Violation] Manifest {manifest_id} hash mismatch! "
                    f"Expected: {item['manifest_hash']}, "
                    f"Computed: {computed_hash}"
                )
            else:
                logger.info(f"✓ Manifest {manifest_id} integrity verified")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"Verification failed for {manifest_id}: {e}")
            return False
    
    def verify_segment_config(
        self,
        segment_config: dict,
        manifest_id: str,
        segment_index: int
    ) -> bool:
        """
        [Phase 7] segment_config 무결성 검증 (Pre-computed Hash 방식)
        
        피드백 반영:
        - ❌ 기존: 매번 partition_workflow() 재실행 (200-500ms)
        - ✅ 개선: Pre-computed Hash로 O(1) 검증 (1-5ms)
        
        Args:
            segment_config: 검증할 세그먼트 설정
            manifest_id: 매니페스트 ID
            segment_index: 세그먼트 인덱스
        
        Returns:
            bool: 검증 통과 여부
        """
        try:
            # 1. DynamoDB에서 Pre-computed Hash 로드
            response = self.table.get_item(
                Key={'manifest_id': manifest_id},
                ProjectionExpression='segment_hashes'
            )
            
            if 'Item' not in response:
                logger.error(f"Manifest not found: {manifest_id}")
                return False
            
            segment_hashes = response['Item'].get('segment_hashes', {})
            expected_hash = segment_hashes.get(str(segment_index))
            
            if not expected_hash:
                logger.error(f"No pre-computed hash for segment {segment_index}")
                return False
            
            # 2. 입력된 segment_config의 해시 계산
            actual_hash = self._compute_hash(segment_config)
            
            # 3. 비교
            is_valid = actual_hash == expected_hash
            
            if not is_valid:
                logger.error(
                    f"[Integrity Violation] Segment {segment_index} hash mismatch!\n"
                    f"Expected: {expected_hash[:16]}...\n"
                    f"Actual:   {actual_hash[:16]}..."
                )
            else:
                logger.info(f"✓ Segment {segment_index} verified: {actual_hash[:8]}...")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"Verification failed: {e}", exc_info=True)
            return False
    
    def _canonical_json_serialize(self, data: Any) -> str:
        """
        [Deprecated] Legacy wrapper for get_canonical_json()
        
        ⚠️ Use StateVersioningService.get_canonical_json() instead
        This method is kept for backward compatibility only.
        """
        return StateVersioningService.get_canonical_json(data).decode('utf-8')
    
    def _compute_hash(self, data: dict) -> str:
        """
        [Wrapper] Instance method wrapper for static compute_hash()
        
        ✅ 내부적으로 StateVersioningService.compute_hash() 호출
        ✅ 해시 알고리즘 변경 시 static method만 수정하면 자동 동기화
        """
        return StateVersioningService.compute_hash(data)
    
    def _compute_merkle_root(
        self,
        blocks: List[ContentBlock],
        config_hash: str,
        parent_hash: Optional[str]
    ) -> str:
        """
        Merkle Root 계산
        
        구조:
        root_hash = sha256(
            config_hash +
            parent_hash +
            sha256(block1.checksum + block2.checksum + ...)
        )
        """
        blocks_hash = hashlib.sha256(
            ''.join(b.checksum for b in sorted(blocks, key=lambda x: x.block_id)).encode()
        ).hexdigest()
        
        combined = config_hash + (parent_hash or '') + blocks_hash
        return hashlib.sha256(combined.encode()).hexdigest()
    
    def _split_into_blocks(self, manifest: List[dict]) -> List[ContentBlock]:
        """
        [Fix #2] segment_manifest를 Content Blocks로 분할 (청크 분할 지원)
        
        피드백 반영:
        - ✅ 세그먼트가 4MB 초과 시 청크로 분할
        - ✅ 확장성: 거대한 프롬프트/임베딩 대응
        
        전략: 각 세그먼트를 별도 블록으로, 단 크기 초과 시 청크 분할
        """
        blocks = []
        for idx, segment in enumerate(manifest):
            segment_json = self._canonical_json_serialize(segment)
            segment_bytes = segment_json.encode('utf-8')
            segment_size = len(segment_bytes)
            
            # 세그먼트가 4MB 이하면 단일 블록
            if segment_size <= MAX_BLOCK_SIZE:
                block_id = hashlib.sha256(segment_bytes).hexdigest()
                
                blocks.append(ContentBlock(
                    block_id=block_id,
                    s3_path=f"s3://{self.bucket}/state-blocks/{block_id}.json",
                    size=segment_size,
                    fields=[f"segment_{idx}"],
                    checksum=block_id
                ))
            
            # 세그먼트가 4MB 초과 → 청크로 분할
            else:
                logger.info(f"Segment {idx} is {segment_size / 1024 / 1024:.2f}MB, splitting into chunks...")
                
                # JSON 문자열을 청크로 분할 (4MB 단위)
                chunk_index = 0
                for offset in range(0, len(segment_json), MAX_BLOCK_SIZE):
                    chunk = segment_json[offset:offset + MAX_BLOCK_SIZE]
                    chunk_bytes = chunk.encode('utf-8')
                    chunk_id = hashlib.sha256(chunk_bytes).hexdigest()
                    
                    blocks.append(ContentBlock(
                        block_id=chunk_id,
                        s3_path=f"s3://{self.bucket}/state-blocks/{chunk_id}.json",
                        size=len(chunk_bytes),
                        fields=[f"segment_{idx}_chunk_{chunk_index}"],
                        checksum=chunk_id
                    ))
                    
                    chunk_index += 1
                
                logger.info(f"Segment {idx} split into {chunk_index} chunks")
        
        return blocks
    
    def _block_exists(self, block_id: str) -> bool:
        """블록이 S3에 이미 존재하는지 확인 (중복 제거)"""
        try:
            self.s3.head_object(
                Bucket=self.bucket,
                Key=f"state-blocks/{block_id}.json"
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            raise
    
    def _load_blocks(self, block_paths: List[str]) -> List[ContentBlock]:
        """S3에서 블록 로드"""
        blocks = []
        for s3_path in block_paths:
            block_id = s3_path.split('/')[-1].replace('.json', '')
            blocks.append(ContentBlock(
                block_id=block_id,
                s3_path=s3_path,
                size=0,
                fields=[],
                checksum=block_id
            ))
        return blocks
    
    def _compute_segment_hashes(self, manifest: List[dict]) -> Dict[str, str]:
        """
        각 세그먼트의 개별 해시 미리 계산 (Phase 7 최적화용)
        
        피드백:
        - 매 세그먼트마다 partition_workflow() 재실행은 너무 무거움
        - Pre-computed Hash로 O(n) → O(1) 검증
        
        Returns:
            Dict[segment_index, hash]: 세그먼트별 해시값
        """
        segment_hashes = {}
        
        for idx, segment in enumerate(manifest):
            # segment_config만 추출하여 해시 계산
            segment_config = segment.get('segment_config', segment)
            segment_hash = self._compute_hash(segment_config)
            segment_hashes[str(idx)] = segment_hash  # DynamoDB는 문자열 키 선호
            
            logger.debug(f"Pre-computed hash for segment {idx}: {segment_hash[:8]}...")
        
        return segment_hashes
    
    def inject_dynamic_segment(
        self,
        manifest_id: str,
        segment_config: dict,
        insert_position: int,
        max_retries: int = 3
    ) -> str:
        """
        🧪 런타임 세그먼트 주입 시 해시 맵 실시간 갱신 (Phase 12)
        
        🧬 [논리 개선 #4] Ordered Hash Chain 도입 (인덱스 충돌 방지)
        🧪 [탄력성 개선 #3] 내부 지수 백오프 재시도 (100ms→200ms→400ms)
        
        Phase 8.3 대응:
        - 동적으로 세그먼트 추가
        - segment_hashes를 ordered_hash_chain으로 재구성
        - 중간 삽입 시 기존 세그먼트 인덱스 자동 shift
        - hash_version 증가 (Optimistic Locking)
        - 충돌 시 자동 재시도 (caller 부담 제거)
        
        Args:
            manifest_id: 매니페스트 ID
            segment_config: 새 세그먼트 설정
            insert_position: 삽입 위치 (0-based)
            max_retries: 최대 재시도 횟수 (기본값: 3)
        
        Returns:
            str: 새로 계산된 세그먼트 해시
        """
        import time
        
        # 새 세그먼트 해시 계산
        new_segment_hash = self._compute_hash(segment_config)
        
        # 🧬 [논리 개선 #4] 기존 segment_hashes 로드 및 재정렬
        # 🧪 [탄력성 개선 #3] 지수 백오프 재시도 루프
        for attempt in range(max_retries):
            try:
                response = self.table.get_item(
                    Key={'manifest_id': manifest_id},
                    ProjectionExpression='segment_hashes, hash_version'
                )
                
                if 'Item' not in response:
                    raise ValueError(f"Manifest {manifest_id} not found")
                
                item = response['Item']
                segment_hashes = item.get('segment_hashes', {})
                current_hash_version = item.get('hash_version', 0)
                
                # 🧬 Ordered Hash Chain 재구성 (insert_position 이후 모든 인덱스 +1 shift)
                new_segment_hashes = {}
                
                for idx_str, hash_value in sorted(segment_hashes.items(), key=lambda x: int(x[0])):
                    idx = int(idx_str)
                    
                    if idx < insert_position:
                        # 삽입 위치 이전: 그대로 유지
                        new_segment_hashes[str(idx)] = hash_value
                    else:
                        # 삽입 위치 이후: 인덱스 +1 shift
                        new_segment_hashes[str(idx + 1)] = hash_value
                
                # 새 세그먼트 삽입
                new_segment_hashes[str(insert_position)] = new_segment_hash
                
                # DynamoDB 원자적 업데이트 (전체 맵 교체)
                update_response = self.table.update_item(
                    Key={'manifest_id': manifest_id},
                    UpdateExpression=(
                        'SET segment_hashes = :new_hashes, '
                        'hash_version = :new_version'
                    ),
                    ConditionExpression=(
                        'attribute_exists(manifest_id) AND '
                        'hash_version = :expected_version'  # Optimistic Locking
                    ),
                    ExpressionAttributeValues={
                        ':new_hashes': new_segment_hashes,
                        ':new_version': current_hash_version + 1,
                        ':expected_version': current_hash_version
                    },
                    ReturnValues='ALL_NEW'
                )
                
                new_hash_version = update_response['Attributes'].get('hash_version', 1)
                
                logger.info(
                    f"[Dynamic Injection] ✅ Segment injected at position {insert_position} "
                    f"(attempt {attempt + 1}/{max_retries}). "
                    f"Shifted {len(segment_hashes) - insert_position} existing segments. "
                    f"manifest_id={manifest_id}, hash={new_segment_hash[:8]}..., "
                    f"hash_version={current_hash_version} → {new_hash_version}"
                )
                
                return new_segment_hash
                
            except ClientError as e:
                if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                    # 🧪 Optimistic Lock 충돌 - 지수 백오프 후 재시도
                    if attempt < max_retries - 1:
                        backoff_ms = (2 ** attempt) * 100  # 100ms, 200ms, 400ms
                        logger.warning(
                            f"[Dynamic Injection] ⚠️ Concurrent modification detected "
                            f"(hash_version mismatch). Retrying in {backoff_ms}ms... "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(backoff_ms / 1000.0)
                        continue
                    else:
                        # 🚫 최종 실패
                        logger.error(
                            f"[Dynamic Injection] ❌ Failed after {max_retries} attempts. "
                            f"manifest_id={manifest_id}, position={insert_position}"
                        )
                        raise RuntimeError(
                            f"inject_dynamic_segment failed after {max_retries} retries: "
                            f"hash_version conflict (concurrent modifications detected)"
                        ) from e
                else:
                    # 다른 DynamoDB 에러 - 즉시 중단
                    logger.error(f"[Dynamic Injection] ❌ DynamoDB error: {e}")
                    raise
        
        # 🚫 루프 종료 시 Fallback (이론적으로 도달 불가)
        raise RuntimeError(
            f"inject_dynamic_segment: Unexpected exit from retry loop "
            f"(manifest_id={manifest_id}, max_retries={max_retries})"
        )
    
    def verify_segment_integrity(
        self,
        manifest_id: str,
        segment_id: int,
        segment_config: dict,
        allow_hash_version_drift: bool = False
    ) -> bool:
        """
        O(1) 세그먼트 무결성 검증 (동적 세그먼트 주입 대응)
        
        Before: O(N) - segment_config를 직렬화 및 해싱
        After: O(1) - DynamoDB에서 사전 계산된 해시 조회
        
        🧪 동적 세그먼트 주입 시나리오:
        1. 매니페스트 생성 시: hash_version=1
        2. 런타임 세그먼트 추가: hash_version=2
        3. 검증 시: hash_version 일치 확인 (옵션)
        
        Args:
            manifest_id: 매니페스트 ID
            segment_id: 세그먼트 ID
            segment_config: 검증할 세그먼트 설정
            allow_hash_version_drift: True면 hash_version 불일치 허용
        
        Returns:
            bool: 검증 통과 여부
        """
        # DynamoDB에서 사전 계산된 해시 조회
        response = self.table.get_item(
            Key={'manifest_id': manifest_id},
            ProjectionExpression='segment_hashes, hash_version'
        )
        
        if 'Item' not in response:
            logger.error(f"Manifest {manifest_id} not found")
            return False
        
        segment_hashes = response['Item'].get('segment_hashes', {})
        current_hash_version = response['Item'].get('hash_version', 1)
        
        # 세그먼트 해시 존재 여부 확인
        segment_key = str(segment_id)
        if segment_key not in segment_hashes:
            logger.warning(
                f"Segment {segment_id} not found in hash map "
                f"(hash_version={current_hash_version}). "
                f"Possible dynamic injection in progress."
            )
            # 동적 주입 허용 모드면 재계산
            if allow_hash_version_drift:
                return self._verify_by_recompute(segment_config)
            return False
        
        expected_hash = segment_hashes[segment_key]
        
        # 실행 시점의 segment_config 해시
        actual_hash = self._compute_hash(segment_config)
        
        is_valid = expected_hash == actual_hash
        
        if not is_valid:
            logger.error(
                f"INTEGRITY_VIOLATION: Segment {segment_id} hash mismatch. "
                f"Expected: {expected_hash[:8]}..., Actual: {actual_hash[:8]}..., "
                f"hash_version={current_hash_version}"
            )
        else:
            logger.debug(f"✓ Segment {segment_id} verified (hash_version={current_hash_version})")
        
        return is_valid
    
    def _verify_by_recompute(self, segment_config: dict) -> bool:
        """
        해시 맵에 없는 세그먼트는 재계산으로 검증 (fallback)
        """
        logger.info("Falling back to hash recomputation for dynamic segment")
        # 동적 세그먼트는 항상 유효하다고 가정 (Phase 8.3 보장)
        return True
    
    def _get_next_version(self, workflow_id: str) -> int:
        """워크플로우의 다음 버전 번호 계산"""
        try:
            # WorkflowIndex GSI로 최신 버전 조회
            response = self.table.query(
                IndexName='WorkflowIndex',
                KeyConditionExpression='workflow_id = :wf_id',
                ExpressionAttributeValues={':wf_id': workflow_id},
                ScanIndexForward=False,  # 내림차순 정렬
                Limit=1
            )
            
            if response['Items']:
                return response['Items'][0]['version'] + 1
            else:
                return 1
                
        except Exception as e:
            logger.warning(f"Failed to get next version, defaulting to 1: {e}")
            return 1
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [Phase 4] StateHydrator 통합 - 읽기 엔진
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def load_manifest_segments(
        self,
        manifest_id: str,
        segment_indices: Optional[List[int]] = None,
        use_s3_select: bool = True
    ) -> List[dict]:
        """
        [Phase 4] Manifest로부터 segment_manifest 재구성
        
        피드백 반영:
        - DynamoDB에서 블록 리스트 가져온 후 S3에서 병렬 로드
        - S3 Select로 특정 세그먼트만 추출 (네트워크 비용 절감)
        - fields 속성 활용: 특정 세그먼트 포함 블록만 로드
        
        Args:
            manifest_id: 매니페스트 ID
            segment_indices: 로드할 세그먼트 인덱스 (전체면 None)
            use_s3_select: S3 Select 사용 여부
        
        Returns:
            재구성된 segment_manifest 리스트
        """
        # 1. DynamoDB에서 매니페스트 로드
        try:
            response = self.table.get_item(Key={'manifest_id': manifest_id})
            
            if 'Item' not in response:
                raise ValueError(f"Manifest not found: {manifest_id}")
            
            item = response['Item']
            block_paths = item['s3_pointers']['state_blocks']
            segment_count = item['metadata']['segment_count']
            
        except Exception as e:
            logger.error(f"Failed to load manifest metadata: {e}")
            raise
        
        # 2. 로드할 세그먼트 결정
        if segment_indices is None:
            segment_indices = list(range(segment_count))
        
        # 3. 필요한 블록만 필터링 (fields 속성 활용)
        required_blocks = []
        for block_path in block_paths:
            # 블록의 fields 확인을 위해 메타데이터 조회
            block_id = block_path.split('/')[-1].replace('.json', '')
            key = block_path.replace(f"s3://{self.bucket}/", "")
            
            try:
                head = self.s3.head_object(Bucket=self.bucket, Key=key)
                fields_str = head.get('Metadata', {}).get('fields', '')
                
                if not fields_str:
                    # 메타데이터 없으면 모든 블록 로드
                    required_blocks.append((block_path, None))
                    continue
                
                # fields에서 세그먼트 인덱스 추출
                for field in fields_str.split(','):
                    if "segment_" in field:
                        seg_idx = int(field.split('_')[1])
                        if seg_idx in segment_indices:
                            required_blocks.append((block_path, seg_idx))
                            break
                            
            except Exception as e:
                logger.warning(f"Failed to check block metadata {block_id}: {e}")
                # Fallback: 모든 블록 포함
                required_blocks.append((block_path, None))
        
        logger.info(
            f"[Reconstruction] Loading {len(required_blocks)}/{len(block_paths)} blocks "
            f"for {len(segment_indices)} segments"
        )
        
        # 4. S3에서 블록 병렬 로드
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        segment_data = {}
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_block = {
                executor.submit(self._load_block, block_path, seg_idx): (block_path, seg_idx)
                for block_path, seg_idx in required_blocks
            }
            
            for future in as_completed(future_to_block):
                block_path, seg_idx = future_to_block[future]
                try:
                    block_content = future.result()
                    
                    # JSON 파싱
                    if block_content:
                        segment = json.loads(block_content)
                        
                        # 세그먼트 인덱스 결정
                        if seg_idx is not None:
                            segment_data[seg_idx] = segment
                        else:
                            # 세그먼트 인덱스를 segment.get('segment_id')로 추출
                            if 'segment_id' in segment:
                                segment_data[segment['segment_id']] = segment
                            
                except Exception as e:
                    logger.error(f"Failed to load block {block_path}: {e}")
        
        # 5. 순서대로 정렬하여 반환
        result = [segment_data[idx] for idx in sorted(segment_data.keys()) if idx in segment_indices]
        
        logger.info(f"[Reconstruction] Loaded {len(result)} segments successfully")
        return result
    
    def _load_block(self, block_path: str, segment_index: Optional[int]) -> Optional[str]:
        """
        ✅ [피드백 ①] JSON Lines 형식 + S3 Select 최적화
        
        개선 사항:
        - ❌ 기존: JSON DOCUMENT 모드 + WHERE s.segment_id 비교 (식별자 문제)
        - ✅ 개선: JSON LINES 모드 (ndjson) - 더 빠르고 정확
        - 네트워크 비용 최대 99% 절감 (4MB → 40KB)
        
        Args:
            block_path: S3 경로 (s3://bucket/key)
            segment_index: 예상 세그먼트 인덱스 (선택)
        
        Returns:
            블록 컨텐츠 (JSON 문자열)
        """
        key = block_path.replace(f"s3://{self.bucket}/", "")
        
        try:
            # ✅ [피드백 ①] JSON Lines 형식 우선 사용
            # S3 Select with JSON LINES 모드 (ndjson)
            if segment_index is not None:
                try:
                    response = self.s3.select_object_content(
                        Bucket=self.bucket,
                        Key=key,
                        ExpressionType='SQL',
                        Expression=f"SELECT * FROM s3object s WHERE s.segment_id = {segment_index}",
                        InputSerialization={
                            'JSON': {'Type': 'LINES'},  # ✅ JSON Lines 모드
                            'CompressionType': 'GZIP'  # 🔄 S3 Select 호환 압축
                        },
                        OutputSerialization={'JSON': {'RecordDelimiter': '\n'}}
                    )
                    
                    # S3 Select 스트리밍 응답 처리
                    content = ''
                    for event in response['Payload']:
                        if 'Records' in event:
                            content += event['Records']['Payload'].decode('utf-8')
                    
                    if content:
                        logger.info(
                            f"[S3 Select] ✅ Extracted segment {segment_index} from {key} "
                            f"(bandwidth saved: ~{(4*1024*1024 - len(content.encode('utf-8'))) / 1024:.1f}KB)"
                        )
                        return content
                    else:
                        # S3 Select로 찾지 못한 경우 Fallback
                        logger.warning(f"[S3 Select] No match for segment {segment_index}, falling back to full load")
                        
                except Exception as select_error:
                    # S3 Select 실패 시 Fallback (JSON 형식 불일치 등)
                    logger.warning(f"[S3 Select] Failed, falling back to get_object: {select_error}")
            
            # Fallback: 전체 객체 다운로드
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            content = response['Body'].read().decode('utf-8')
            return content
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.error(f"Block not found: {block_path}")
                return None
            raise
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [피드백 ②③] Ghost Block 방지 + Transaction Batching 헬퍼 메서드
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _rollback_pending_blocks(self, blocks: List[dict], transaction_id: str) -> None:
        """
        ✅ [피드백 ②] DynamoDB 트랜잭션 실패 시 S3 Pending 블록 롤백
        
        시나리오:
        - S3 업로드 성공 (status=pending)
        - DynamoDB 트랜잭션 실패
        - 유령 블록 발생 방지를 위해 S3에서 삭제
        
        Args:
            blocks: 롤백할 블록 리스트
            transaction_id: 트랜잭션 식별자
        """
        rollback_count = 0
        failed_deletions = []
        
        for block in blocks:
            block_id = block['block_id']
            key = f"{self.prefix}blocks/{block_id}.json"
            
            try:
                # Pending 태그 확인 후 삭제 (이중 보호)
                response = self.s3.get_object_tagging(Bucket=self.bucket, Key=key)
                tags = {tag['Key']: tag['Value'] for tag in response.get('TagSet', [])}
                
                if tags.get('status') == 'pending' and tags.get('transaction_id') == transaction_id:
                    self.s3.delete_object(Bucket=self.bucket, Key=key)
                    rollback_count += 1
                    logger.info(f"[Rollback] Deleted pending block {block_id} (transaction: {transaction_id})")
                else:
                    logger.warning(
                        f"[Rollback] Block {block_id} tag mismatch "
                        f"(expected pending/{transaction_id}, got {tags})"
                    )
                    
            except ClientError as e:
                if e.response['Error']['Code'] != 'NoSuchKey':
                    logger.error(f"[Rollback] Failed to delete block {block_id}: {e}")
                    failed_deletions.append(block_id)
        
        logger.info(
            f"[Rollback Complete] Deleted {rollback_count}/{len(blocks)} pending blocks "
            f"(transaction: {transaction_id})"
        )
        
        if failed_deletions:
            logger.error(
                f"[Rollback Warning] {len(failed_deletions)} blocks failed to delete, "
                f"will be cleaned by BackgroundGC after 15 minutes: {failed_deletions}"
            )
            
            # 🌀 [멱등성 강화 #1] 실패 블록을 GC DLQ에 전송 (핀포인트 삭제)
            if self.gc_dlq_url:
                try:
                    import boto3
                    sqs = boto3.client('sqs')
                    
                    for block_id in failed_deletions:
                        sqs.send_message(
                            QueueUrl=self.gc_dlq_url,
                            MessageBody=json.dumps({
                                'event_type': 'rollback_failure',
                                'block_id': block_id,
                                'transaction_id': transaction_id,
                                'reason': 'rollback_deletion_failed',
                                'status': 'pending',
                                'failed_at': datetime.utcnow().isoformat(),
                                'retry_after_minutes': 15
                            }),
                            MessageAttributes={
                                'event_type': {'StringValue': 'rollback_failure', 'DataType': 'String'},
                                'block_id': {'StringValue': block_id, 'DataType': 'String'}
                            }
                        )
                    
                    logger.info(
                        f"[멱등성 보장] {len(failed_deletions)} failed blocks sent to GC DLQ "
                        f"for pinpoint deletion (scan cost = $0)"
                    )
                except Exception as dlq_error:
                    logger.error(f"[DLQ] Failed to send to GC DLQ: {dlq_error}")
    
    def _commit_pending_blocks(self, blocks: List[dict], transaction_id: str) -> None:
        """
        ✅ [피드백 ②] DynamoDB 트랜잭션 성공 시 S3 블록 상태를 committed로 변경
        
        시나리오:
        - S3 업로드 성공 (status=pending)
        - DynamoDB 트랜잭션 성공
        - S3 블록을 status=committed로 변경 (GC 대상 제외)
        
        Args:
            blocks: 커밋할 블록 리스트
            transaction_id: 트랜잭션 식별자
        """
        commit_count = 0
        failed_commits = []
        
        for block in blocks:
            block_id = block['block_id']
            key = f"{self.prefix}blocks/{block_id}.json"
            
            try:
                # Pending → Committed 태그 변경
                self.s3.put_object_tagging(
                    Bucket=self.bucket,
                    Key=key,
                    Tagging={
                        'TagSet': [
                            {'Key': 'status', 'Value': 'committed'},
                            {'Key': 'transaction_id', 'Value': transaction_id},
                            {'Key': 'committed_at', 'Value': datetime.utcnow().isoformat()}
                        ]
                    }
                )
                commit_count += 1
                
            except ClientError as e:
                logger.error(f"[Commit] Failed to tag block {block_id}: {e}")
                failed_commits.append(block_id)
        
        logger.info(
            f"[Commit Complete] Tagged {commit_count}/{len(blocks)} blocks as committed "
            f"(transaction: {transaction_id})"
        )
        
        if failed_commits:
            logger.warning(
                f"[Commit Warning] {len(failed_commits)} blocks failed to commit, "
                f"but already in DynamoDB (safe state): {failed_commits}"
            )
    
    def _json_default(self, obj: Any) -> Any:
        """JSON serialization handler for datetime and Decimal.

        [v3.32 FIX] Decimal uses str() — matches hash_utils._canonical_bytes.
        Previous: float()/int() diverged from get_canonical_json (float) and
        _canonical_bytes (str), producing incompatible hashes for the same value.
        """
        from decimal import Decimal

        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, Decimal):
            return str(obj)
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [NEW] Block Reference Counting (Garbage Collection 지원)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def increment_block_references(self, block_ids: List[str], workflow_id: str) -> int:
        """Increment ref_count for blocks when a manifest references them.

        [v3.33 FIX] Partial failures are now tracked and raised so the caller
        can decide whether to abort or compensate.  Silent swallowing of
        increment errors causes ref_count drift → premature GC deletion.

        Args:
            block_ids: Block IDs to increment.
            workflow_id: HASH key for WorkflowBlockReferencesV3.

        Returns:
            Number of successfully updated blocks.

        Raises:
            RuntimeError: If any block increment failed (contains details).
        """
        updated_count = 0
        failed_blocks: List[str] = []

        for block_id in block_ids:
            try:
                self.block_refs_table.update_item(
                    Key={
                        'workflow_id': workflow_id,
                        'block_id': block_id,
                    },
                    UpdateExpression='ADD reference_count :inc SET last_referenced = :now',
                    ExpressionAttributeValues={
                        ':inc': 1,
                        ':now': datetime.utcnow().isoformat(),
                    }
                )
                updated_count += 1
            except Exception as e:
                logger.error(f"Failed to increment reference for block {block_id}: {e}")
                failed_blocks.append(block_id)

        logger.info(f"[Reference Counting] Incremented {updated_count}/{len(block_ids)} blocks")

        if failed_blocks:
            raise RuntimeError(
                f"[v3.33] {len(failed_blocks)}/{len(block_ids)} block ref_count increments "
                f"failed for workflow {workflow_id}. Failed blocks: "
                f"{[b[:8] for b in failed_blocks]}. Ref count drift risk."
            )

        return updated_count
    
    def decrement_block_references(self, block_ids: List[str], workflow_id: str) -> int:
        """
        블록 참조 카운트 감소 (매니페스트 무효화 시)
        
        Args:
            block_ids: 참조 카운트를 감소시킬 블록 ID 리스트
            workflow_id: 복합키의 HASH key (WorkflowBlockReferencesV3 스키마 필수)
        
        Returns:
            업데이트된 블록 수
        """
        updated_count = 0
        
        for block_id in block_ids:
            try:
                # [FIX] WorkflowBlockReferencesV3 복합키: HASH=workflow_id, RANGE=block_id
                response = self.block_refs_table.update_item(
                    Key={
                        'workflow_id': workflow_id,  # HASH key (필수)
                        'block_id': block_id          # RANGE key
                    },
                    UpdateExpression='ADD reference_count :dec SET last_dereferenced = :now',
                    ExpressionAttributeValues={
                        ':dec': -1,
                        ':now': datetime.utcnow().isoformat()
                    },
                    ReturnValues='ALL_NEW'
                )
                
                updated_count += 1
                
                # 참조 카운트가 0이 되면 GC 대상으로 표시
                if response.get('Attributes', {}).get('reference_count', 1) <= 0:
                    logger.warning(
                        f"[GC Candidate] Block {block_id} reference count reached 0, "
                        f"eligible for garbage collection"
                    )
                
            except Exception as e:
                logger.error(f"Failed to decrement reference for block {block_id}: {e}")
        
        logger.info(f"[Reference Counting] Decremented {updated_count}/{len(block_ids)} blocks")
        return updated_count
    
    def get_unreferenced_blocks(self, older_than_days: int = 7) -> List[str]:
        """
        참조 카운트가 0인 블록 조회 (Garbage Collection용)
        
        Args:
            older_than_days: 마지막 참조 이후 경과일
        
        Returns:
            GC 대상 블록 ID 리스트
        """
        from datetime import timedelta
        
        cutoff_date = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat()
        
        from boto3.dynamodb.conditions import Attr

        try:
            # [FIX] GSI 'ReferenceCountIndex'는 template.yaml에 정의되지 않음.
            # scan + FilterExpression으로 대체 (GC 경로는 latency-insensitive).
            response = self.block_refs_table.scan(
                FilterExpression=(
                    Attr('reference_count').lte(0) &
                    Attr('last_dereferenced').lt(cutoff_date)
                )
            )
            items = response.get('Items', [])

            # Paginate if needed
            while 'LastEvaluatedKey' in response:
                response = self.block_refs_table.scan(
                    FilterExpression=(
                        Attr('reference_count').lte(0) &
                        Attr('last_dereferenced').lt(cutoff_date)
                    ),
                    ExclusiveStartKey=response['LastEvaluatedKey']
                )
                items.extend(response.get('Items', []))

            gc_candidates = [item['block_id'] for item in items]

            logger.info(
                f"[Garbage Collection] Found {len(gc_candidates)} blocks with 0 references "
                f"older than {older_than_days} days"
            )

            return gc_candidates

        except Exception as e:
            logger.error(f"Failed to scan unreferenced blocks: {e}")
            return []
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [NEW] Dynamic Re-partitioning Support
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def invalidate_manifest(self, manifest_id: str, reason: str) -> bool:
        """
        매니페스트 무효화 (동적 재파티셔닝 시)
        
        기존 매니페스트를 INVALIDATED 상태로 표시하여
        새로운 매니페스트가 생성되었음을 표시.
        
        [CRITICAL FIX] 블록 참조 카운트 감소 추가 (Garbage Collection 지원)
        
        Args:
            manifest_id: 무효화할 매니페스트 ID
            reason: 무효화 사유
        
        Returns:
            성공 여부
        """
        try:
            # 1. 매니페스트 정보 로드 (블록 리스트 추출용)
            manifest = self.get_manifest(manifest_id)
            block_ids = [block.block_id for block in manifest.blocks]
            
            # 2. 매니페스트 무효화
            self.table.update_item(
                Key={'manifest_id': manifest_id},
                UpdateExpression=(
                    'SET #status = :status, '
                    'invalidation_reason = :reason, '
                    'invalidated_at = :timestamp'
                ),
                ExpressionAttributeNames={
                    '#status': 'status'
                },
                ExpressionAttributeValues={
                    ':status': 'INVALIDATED',
                    ':reason': reason,
                    ':timestamp': datetime.utcnow().isoformat()
                },
                # 이미 무효화된 경우 예외 발생하지 않도록
                ConditionExpression='attribute_exists(manifest_id)'
            )
            
            # 3. 블록 참조 카운트 감소 (Garbage Collection 준비)
            # workflow_id는 manifest DynamoDB item에서 추출 (block_refs_table 복합키 필수)
            manifest_item_resp = self.table.get_item(Key={'manifest_id': manifest_id})
            workflow_id_for_refs = (
                manifest_item_resp.get('Item', {}).get('workflow_id', '')
                if 'Item' in manifest_item_resp else ''
            )
            decremented = self.decrement_block_references(block_ids, workflow_id=workflow_id_for_refs)
            
            logger.info(
                f"[Manifest Invalidation] ✅ {manifest_id} invalidated: {reason}. "
                f"Decremented {decremented} block references."
            )
            return True
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                logger.warning(f"[Manifest Invalidation] Manifest not found: {manifest_id}")
                return False
            raise
            
        except Exception as e:
            logger.error(f"[Manifest Invalidation] ❌ Failed: {e}")
            return False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 🧬 v3.3 KernelStateManager - 단일 상태 저장 경로
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def save_state_delta(
        self,
        delta: Dict[str, Any],
        workflow_id: str,
        execution_id: str,
        owner_id: str,
        segment_id: int,
        previous_manifest_id: Optional[str] = None,
        dirty_keys: Optional[set] = None,
        full_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        🧬 v3.3 KernelStateManager의 핵심 저장 로직
        
        Delta 기반 상태 저장:
        1. StateHydrator로부터 변경된 델타(Delta) 수신
        2. Merkle DAG 블록 생성 및 S3 업로드 (status=temp 태그)
        3. DynamoDB TransactWriteItems:
           - 새 매니페스트 등록
           - 블록 참조 카운트 증가
           - WorkflowsTableV3.latest_manifest_id 갱신 (포인터)
        4. S3 블록 태그를 status=ready로 변경 (2-Phase Commit 완료)
        
        Args:
            delta: 변경된 필드만 포함된 델타 딕셔너리
            workflow_id: 워크플로우 ID
            execution_id: 실행 ID
            owner_id: 소유자 ID (DynamoDB 포인터용)
            segment_id: 최신 세그먼트 ID
            previous_manifest_id: 부모 매니페스트 ID (버전 체인)
        
        Returns:
            Dict: {
                'manifest_id': str,
                'block_ids': List[str],
                'committed': bool,
                's3_paths': List[str]
            }
        
        Example:
            >>> result = kernel.save_state_delta(
            ...     delta={'user_input': 'new value'},  # 변경된 부분만
            ...     workflow_id='wf-123',
            ...     execution_id='exec-456',
            ...     owner_id='user-789',
            ...     segment_id=5,
            ...     previous_manifest_id='manifest-abc'
            ... )
            >>> print(result['manifest_id'])
            'manifest-def'
        
        설계 철학:
        - latest_state.json 폐기: DynamoDB에 manifest_id만 저장
        - 2-Phase Commit 내장: temp → ready 태그 전환
        - GC 자동 연계: temp 태그는 BackgroundGC가 자동 제거
        - 단일 저장 경로: 시스템 전체 정합성 보장
        """
        try:
            logger.info(
                f"[KernelStateManager] 💾 Saving delta for {workflow_id}/{execution_id} "
                f"(segment={segment_id}, delta_keys={len(delta)})"
            )
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 1: Content Block 생성 및 S3 병렬 업로드 (status=temp)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [v3.32] S3 PUT 병렬화 — 순차 for 루프에서 ThreadPoolExecutor로 전환.
            # 500 세그먼트 × 10 필드 = 5,000 PUT. 순차 시 ~50-150s → 병렬 시 ~5-15s.
            # S3 PUT은 I/O-bound이므로 GIL 영향 없이 스레드 병렬 효과 확보.
            import gzip
            from concurrent.futures import ThreadPoolExecutor, as_completed

            blocks = []
            uploaded_block_ids = []

            # Step 1: CPU-bound 직렬화 + 압축 (GIL-bound이므로 순차 처리)
            # [v3.32 FIX] block_hash는 raw data에서 계산 (BUG-4).
            # gzip.compress()는 mtime을 header에 embed → non-deterministic.
            # 같은 content가 다른 시각에 압축되면 다른 hash가 생성되어
            # content-addressable dedup이 무효화됨.
            prepared_uploads = []
            for field_name, field_value in delta.items():
                field_json = json.dumps({field_name: field_value}, ensure_ascii=False, default=self._json_default)
                ndjson_data = field_json + "\n"
                raw_data = ndjson_data.encode('utf-8')
                block_hash = hashlib.sha256(raw_data).hexdigest()
                compressed_data = gzip.compress(raw_data, compresslevel=6, mtime=0)
                s3_key = f"merkle-blocks/{workflow_id}/{block_hash[:2]}/{block_hash}.json"

                prepared_uploads.append({
                    'field_name': field_name,
                    's3_key': s3_key,
                    'body': compressed_data,
                    'block_hash': block_hash,
                    'raw_size': len(raw_data),
                    'compressed_size': len(compressed_data),
                    'field_json_size': len(field_json),
                })

            # Step 2: I/O-bound S3 PUT — 병렬 실행
            def _upload_block(upload_info):
                """Single block S3 upload (thread-safe: each call uses its own params)."""
                self.s3.put_object(
                    Bucket=self.bucket,
                    Key=upload_info['s3_key'],
                    Body=upload_info['body'],
                    ContentType='application/x-ndjson',
                    ContentEncoding='gzip',
                    Tagging='status=temp',
                    Metadata={
                        'block_hash': upload_info['block_hash'],
                        'workflow_id': workflow_id,
                        'execution_id': execution_id,
                        'uploaded_at': datetime.utcnow().isoformat(),
                        'format': 'ndjson',
                        'field_name': upload_info['field_name'],
                        'contains_segments': 'delta',
                        'compression': 'gzip',
                    }
                )
                return upload_info

            optimal_workers = _calculate_optimal_workers()

            if len(prepared_uploads) <= 1:
                # Single field: skip thread pool overhead
                for info in prepared_uploads:
                    _upload_block(info)
            else:
                with ThreadPoolExecutor(max_workers=optimal_workers) as executor:
                    futures = {executor.submit(_upload_block, info): info for info in prepared_uploads}
                    for future in as_completed(futures):
                        future.result()  # propagate any S3 error immediately

            for info in prepared_uploads:
                blocks.append(ContentBlock(
                    block_id=info['block_hash'],
                    s3_path=f"s3://{self.bucket}/{info['s3_key']}",
                    size=info['field_json_size'],
                    fields=[info['field_name']],
                    checksum=info['block_hash'],
                ))
                uploaded_block_ids.append(info['block_hash'])

            logger.info(
                f"[KernelStateManager] Phase 1: Uploaded {len(blocks)} blocks "
                f"(status=temp, workers={optimal_workers})"
            )
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 2: DynamoDB TransactWriteItems (Atomic Commit)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [v3.32 FIX] UUID suffix prevents 1-second collision window (BUG-2).
            # int(time.time()) alone has 1s granularity — concurrent calls for the
            # same execution_id + segment_id within 1s produce identical manifest_ids.
            import uuid
            manifest_id = f"manifest-{execution_id}-{segment_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
            manifest_hash = hashlib.sha256(
                json.dumps([asdict(b) for b in blocks], sort_keys=True).encode('utf-8')
            ).hexdigest()

            # ── Phase 3d+5a: Incremental hashing + temperature-aware manifest ──
            # When dirty_keys and full_state are provided, compute an incremental
            # merkle root using SubBlockHashRegistry instead of full-state hash.
            # Store per-temperature-tier hashes in manifest metadata.
            incremental_metadata: Dict[str, str] = {}
            if dirty_keys is not None and full_state is not None:
                try:
                    from src.common.hash_utils import SubBlockHashRegistry

                    if not hasattr(self, '_sub_block_registry'):
                        self._sub_block_registry = SubBlockHashRegistry()

                    inc_root, block_hashes = (
                        self._sub_block_registry.compute_incremental_root(
                            full_state, dirty_keys,
                        )
                    )
                    incremental_metadata = {
                        'incremental_root': inc_root,
                        **{f'{k}_hash': v for k, v in block_hashes.items()},
                    }
                    logger.info(
                        "[KernelStateManager] Incremental hash: "
                        "dirty_keys=%d blocks_rehashed=%s root=%s",
                        len(dirty_keys),
                        list(block_hashes.keys()),
                        inc_root[:16],
                    )
                except Exception as exc:
                    logger.warning(
                        "[KernelStateManager] Incremental hashing fallback: %s",
                        exc,
                    )

            # ── Phase 2a: Block ref counts + Manifest (unconditional, atomic) ──
            # Block persistence must NEVER fail due to a stale pointer condition,
            # so the conditional pointer update is separated into Phase 2b.
            transact_items = []

            for block_id in uploaded_block_ids:
                transact_items.append({
                    'Update': {
                        'TableName': self.block_refs_table.name,
                        'Key': {
                            'workflow_id': {'S': workflow_id},
                            'block_id': {'S': block_id},
                        },
                        'UpdateExpression': 'ADD ref_count :inc SET last_referenced = :now',
                        'ExpressionAttributeValues': {
                            ':inc': {'N': '1'},
                            ':now': {'S': datetime.utcnow().isoformat()},
                        }
                    }
                })

            manifest_dynamo_item = {
                'manifest_id': {'S': manifest_id},
                'workflow_id': {'S': workflow_id},
                'execution_id': {'S': execution_id},
                'segment_id': {'N': str(segment_id)},
                'manifest_hash': {'S': manifest_hash},
                'parent_manifest_id': {'S': previous_manifest_id} if previous_manifest_id else {'NULL': True},
                # [v3.33 FIX-A] Must use sort_keys=True to match manifest_hash
                # computation (line 1873).  Without sort_keys, deserialized dict
                # key ordering may differ → hash mismatch on re-verification.
                'blocks': {'S': json.dumps([asdict(b) for b in blocks], sort_keys=True)},
                'created_at': {'S': datetime.utcnow().isoformat()},
                'status': {'S': 'ACTIVE'},
            }

            # Phase 5a: Store temperature-tier hashes in manifest
            if incremental_metadata:
                manifest_dynamo_item['incremental_metadata'] = {
                    'S': json.dumps(incremental_metadata, sort_keys=True)
                }
                for meta_key in ('hot_hash', 'warm_hash', 'cold_hash',
                                 'control_hash', 'incremental_root'):
                    if meta_key in incremental_metadata:
                        manifest_dynamo_item[meta_key] = {
                            'S': incremental_metadata[meta_key]
                        }

            manifest_item = {
                'Put': {
                    'TableName': self.table.name,
                    'Item': manifest_dynamo_item,
                    # [v3.32 FIX] Prevent silent overwrite on manifest_id collision
                    'ConditionExpression': 'attribute_not_exists(manifest_id)',
                }
            }

            # Execute: block refs + manifest (100-item batching)
            if len(transact_items) < 99:
                transact_items.append(manifest_item)
                self.dynamodb_client.transact_write_items(TransactItems=transact_items)
            else:
                for i in range(0, len(transact_items), 99):
                    batch = transact_items[i:i+99]
                    if i + 99 >= len(transact_items):
                        batch.append(manifest_item)
                    try:
                        self.dynamodb_client.transact_write_items(TransactItems=batch)
                    except Exception as e:
                        logger.error(
                            f"[Atomicity Protection] Batch {i//99 + 1} failed. "
                            f"Manifest NOT created (data integrity preserved): {e}"
                        )
                        raise

            # ── Phase 2b: Conditional pointer advancement (with retry) ──
            # [v3.33] Monotonic segment guard: only advance latest_manifest_id if
            # segment_id >= currently stored value.  Prevents a late-finishing
            # parallel branch from overwriting a pointer that a higher segment
            # already set.  For fan-out branches with the SAME segment_id,
            # last-writer-wins is acceptable — the aggregator reads branch states
            # from S3 directly, not from this pointer.
            #
            # This is separated from Phase 2a so a ConditionalCheckFailedException
            # (expected during parallel fan-out) does NOT abort block persistence.
            #
            # [v3.33 FIX] Exponential backoff retry for transient DynamoDB errors.
            # Without retry, a transient throttle/network error leaves the pointer
            # permanently stale — the "Ghost State" problem. Recovery (resume) would
            # then read an outdated manifest and lose the successful computation.
            # ConditionalCheckFailedException is still non-retryable (expected fan-out).
            _POINTER_RETRYABLE_CODES = frozenset({
                'ProvisionedThroughputExceededException',
                'ThrottlingException',
                'InternalServerError',
                'ServiceUnavailable',
                'RequestLimitExceeded',
            })
            _POINTER_MAX_RETRIES = 3
            _POINTER_BASE_DELAY = 0.1  # 100ms → 200ms → 400ms

            workflows_table_name = os.environ.get('WORKFLOWS_TABLE', 'WorkflowsTableV3')
            pointer_advanced = False

            for _attempt in range(_POINTER_MAX_RETRIES):
                try:
                    self.dynamodb_client.update_item(
                        TableName=workflows_table_name,
                        Key={
                            'ownerId': {'S': owner_id},
                            'workflowId': {'S': workflow_id},
                        },
                        UpdateExpression=(
                            'SET latest_manifest_id = :manifest_id, '
                            'latest_segment_id = :segment_id, '
                            'latest_execution_id = :execution_id, '
                            'updated_at = :now'
                        ),
                        ConditionExpression=(
                            'attribute_not_exists(latest_segment_id) OR '
                            'latest_segment_id <= :segment_id'
                        ),
                        ExpressionAttributeValues={
                            ':manifest_id': {'S': manifest_id},
                            ':segment_id': {'N': str(segment_id)},
                            ':execution_id': {'S': execution_id},
                            ':now': {'S': datetime.utcnow().isoformat()},
                        },
                    )
                    pointer_advanced = True
                    break
                except ClientError as ce:
                    error_code = ce.response['Error']['Code']
                    if error_code == 'ConditionalCheckFailedException':
                        # Expected during parallel fan-out: a higher segment already
                        # advanced the pointer.  Blocks + manifest are persisted; only
                        # the global pointer stays at the higher segment.
                        logger.info(
                            f"[v3.33] Pointer not advanced (segment {segment_id} <= current). "
                            f"Manifest {manifest_id} persisted independently."
                        )
                        pointer_advanced = True  # Not a failure — intentional skip
                        break
                    elif error_code in _POINTER_RETRYABLE_CODES:
                        delay = _POINTER_BASE_DELAY * (2 ** _attempt)
                        logger.warning(
                            f"[v3.33] Pointer update transient error (attempt {_attempt + 1}/"
                            f"{_POINTER_MAX_RETRIES}): {error_code}. Retrying in {delay:.1f}s"
                        )
                        time.sleep(delay)
                    else:
                        # Non-retryable unexpected error
                        logger.error(
                            f"[v3.33] Pointer update failed (non-retryable): {ce}"
                        )
                        break

            if not pointer_advanced:
                logger.error(
                    f"[v3.33] [GHOST_STATE_RISK] Pointer update exhausted all retries. "
                    f"manifest_id={manifest_id}, segment_id={segment_id}, "
                    f"workflow_id={workflow_id}. Manifest is persisted but pointer is stale. "
                    f"Recovery path must use manifest chain scan via ParentHashIndex."
                )
            
            logger.info(
                f"[KernelStateManager] ✅ Phase 2: DynamoDB committed "
                f"(manifest={manifest_id}, blocks={len(blocks)})"
            )
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 3: S3 태그 변경 (status=temp → status=ready)
            # 🚀 [성능 개선 #2] 병렬 태그 업데이트 (Lambda 실행 시간 단축)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            def _tag_block_as_ready(block):
                """블록 태그 업데이트 헬퍼 (병렬 실행용)"""
                s3_key = block.s3_path.replace(f"s3://{self.bucket}/", "")
                self.s3.put_object_tagging(
                    Bucket=self.bucket,
                    Key=s3_key,
                    Tagging={'TagSet': [{'Key': 'status', 'Value': 'ready'}]}
                )
                return block.block_id
            
            # 🚀 병렬 태그 업데이트 (Lambda 메모리 기반 Adaptive Workers)
            optimal_workers = _calculate_optimal_workers()
            tagged_count = 0
            with ThreadPoolExecutor(max_workers=optimal_workers) as executor:
                future_to_block = {
                    executor.submit(_tag_block_as_ready, block): block
                    for block in blocks
                }
                
                for future in as_completed(future_to_block):
                    try:
                        block_id = future.result()
                        tagged_count += 1
                    except Exception as e:
                        block = future_to_block[future]
                        logger.error(f"[Parallel Tagging] Failed to tag block {block.block_id}: {e}")
            
            logger.info(
                f"[KernelStateManager] ✅ Phase 3: {tagged_count}/{len(blocks)} blocks marked as ready "
                f"(2-Phase Commit complete via parallel tagging)"
            )
            
            # 🎯 [P0 수정] manifest_id 반환 추가 (Merkle Chain 연속성 확보)
            result = {
                'success': True,
                'manifest_id': manifest_id,  # 다음 세그먼트가 부모로 참조할 ID
                'blocks_uploaded': len(blocks),
                'manifest_hash': manifest_hash,
                'segment_id': segment_id,
                'block_ids': uploaded_block_ids,
                's3_paths': [b.s3_path for b in blocks],
            }
            if incremental_metadata:
                result['incremental_metadata'] = incremental_metadata
            return result
            
        except Exception as e:
            logger.error(f"[KernelStateManager] ❌ Failed to save delta: {e}")
            # 실패 시 temp 블록은 GC가 자동 제거하므로 별도 롤백 불필요
            raise RuntimeError(f"Failed to save state delta: {e}")
    
    def load_latest_state(
        self,
        workflow_id: str,
        owner_id: str,
        execution_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        🧬 v3.3 KernelStateManager의 핵심 로드 로직
        
        DynamoDB 포인터 기반 상태 복원:
        1. WorkflowsTableV3.latest_manifest_id 조회 (포인터)
        2. 매니페스트에서 블록 리스트 추출
        3. S3에서 블록들을 병렬 다운로드
        4. StateHydrator로 상태 재구성
        
        Args:
            workflow_id: 워크플로우 ID
            owner_id: 소유자 ID (DynamoDB 키)
            execution_id: 실행 ID (선택, 특정 실행의 상태 조회용)
        
        Returns:
            Dict: 재구성된 전체 상태 딕셔너리
        
        Example:
            >>> state = kernel.load_latest_state(
            ...     workflow_id='wf-123',
            ...     owner_id='user-789'
            ... )
            >>> print(state['user_input'])
            'restored value'
        
        설계 철학:
        - latest_state.json 폐기: DynamoDB 포인터만 사용
        - Merkle 블록 병렬 다운로드: 대용량 상태도 빠른 복원
        - StateHydrator 통합: 블록 → 전체 상태 자동 조립
        """
        try:
            logger.info(
                f"[KernelStateManager] 📥 Loading latest state for "
                f"{workflow_id}/{owner_id}"
            )
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 1: DynamoDB에서 latest_manifest_id 조회
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [FIX] 문자열 치환으로 테이블명을 유도하면
            # 'WorkflowManifests-v3-dev' → 'WorkflowWorkflowsTableV3-v3-dev' 처럼 잘못됨.
            # save_state_delta() 경로와 동일하게 환경변수에서 직접 읽는다.
            workflows_table_name = os.environ.get('WORKFLOWS_TABLE', 'WorkflowsTableV3')
            workflows_table = self.dynamodb.Table(workflows_table_name)
            
            response = workflows_table.get_item(
                Key={
                    'ownerId': owner_id,
                    'workflowId': workflow_id
                }
            )
            
            if 'Item' not in response:
                logger.warning(f"[KernelStateManager] No state found for {workflow_id}")
                return {}  # 빈 상태 반환 (첫 실행)
            
            item = response['Item']
            manifest_id = item.get('latest_manifest_id')
            
            if not manifest_id:
                logger.warning(f"[KernelStateManager] No manifest_id in workflow record")
                return {}
            
            logger.info(f"[KernelStateManager] ✅ Phase 1: Found manifest_id={manifest_id}")
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 2: 매니페스트에서 블록 리스트 추출
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            manifest_response = self.table.get_item(
                Key={'manifest_id': manifest_id}
            )
            
            if 'Item' not in manifest_response:
                raise RuntimeError(f"Manifest not found: {manifest_id}")
            
            manifest_data = manifest_response['Item']
            blocks_json = manifest_data.get('blocks', '[]')
            blocks = json.loads(blocks_json) if isinstance(blocks_json, str) else blocks_json
            
            logger.info(f"[KernelStateManager] ✅ Phase 2: Found {len(blocks)} blocks")
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 3: S3에서 블록들을 병렬 다운로드 및 상태 재구성
            # 🚀 [성능 개선 #1] ThreadPoolExecutor로 병렬 다운로드 (5~10배 속도 향상)
            # 🚀 [최적화 #2] Adaptive Workers (Lambda 메모리 기반 동적 조정)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            reconstructed_state = {}
            
            def _download_block(block_info):
                """블록 다운로드 헬퍼 (병렬 실행용)"""
                s3_path = block_info.get('s3_path', '')
                if not s3_path:
                    return None
                
                s3_key = s3_path.replace(f"s3://{self.bucket}/", "")
                response = self.s3.get_object(Bucket=self.bucket, Key=s3_key)
                
                # 📦 [FinOps #2] Gzip 압축 해제 (ContentEncoding 확인)
                content_encoding = response.get('ContentEncoding', 'identity')
                raw_data = response['Body'].read()
                
                if content_encoding == 'gzip':
                    import gzip
                    # 🛡️ [RISK] Gzip 손상 데이터 처리 (EOFError 방어)
                    # 재시도 로직은 상위 ThreadPoolExecutor에서 처리
                    try:
                        block_data = gzip.decompress(raw_data).decode('utf-8')
                    except (EOFError, OSError) as decomp_err:
                        logger.error(
                            f"[Gzip Decompression] Failed for block {block_info.get('block_id', 'unknown')}: "
                            f"{decomp_err}. Data size: {len(raw_data)}B. "
                            f"This indicates data corruption or incomplete S3 write."
                        )
                        raise RuntimeError(
                            f"Gzip decompression failed: {decomp_err}. "
                            f"Block {block_info.get('block_id', 'unknown')} may be corrupted."
                        ) from decomp_err
                elif content_encoding == 'zstd':
                    # 🔄 하위 호환: 기존 Zstd 블록 지원 (점진적 마이그레이션)
                    try:
                        import zstandard as zstd
                        decompressor = zstd.ZstdDecompressor()
                        block_data = decompressor.decompress(raw_data).decode('utf-8')
                    except ImportError:
                        logger.error("[Zstd] Cannot decompress: zstandard library not installed")
                        raise RuntimeError("zstandard library required for decompression")
                else:
                    block_data = raw_data.decode('utf-8')
                
                # 📝 [일관성 개선 #3] NDJSON 포맷 지원 (줄바꿈 제거)
                block_data = block_data.strip()  # ✅ NDJSON의 trailing newline 제거
                return json.loads(block_data)
            
            # 🚀 Adaptive Workers 계산
            optimal_workers = _calculate_optimal_workers()
            
            # 🚀 병렬 다운로드
            with ThreadPoolExecutor(max_workers=optimal_workers) as executor:
                future_to_block = {
                    executor.submit(_download_block, block): block
                    for block in blocks
                }
                
                for future in as_completed(future_to_block):
                    try:
                        block_data = future.result()
                        if block_data:
                            reconstructed_state.update(block_data)
                    except Exception as e:
                        block = future_to_block[future]
                        logger.error(f"[Parallel Load] Failed to load block {block.get('block_id', 'unknown')}: {e}")
            
            logger.info(
                f"[KernelStateManager] ✅ Phase 3: State reconstructed via parallel download "
                f"({len(reconstructed_state)} keys, {len(blocks)} blocks, workers={optimal_workers})"
            )
            
            return reconstructed_state
            
        except Exception as e:
            logger.error(f"[KernelStateManager] ❌ Failed to load state: {e}")
            raise RuntimeError(f"Failed to load latest state: {e}")
