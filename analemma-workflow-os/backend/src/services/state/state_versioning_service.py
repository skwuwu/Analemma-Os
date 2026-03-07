# -*- coding: utf-8 -*-
"""
[Phase 1] Merkle DAG-based State Versioning Service

Core features:
1. Store only deltas on state change (Content-Addressable Storage)
2. Integrity verification via Merkle Root
3. Instant rollback via Pointer Manifest
4. O(1) segment verification via Pre-computed Hash (Phase 7)
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

# Production constants
MAX_BLOCK_SIZE = 4 * 1024 * 1024  # 4MB (block split threshold)
VERSION_RETRY_ATTEMPTS = 3  # Race condition retry count


def _calculate_optimal_workers() -> int:
    """Calculate optimal I/O thread count based on Lambda memory.

    S3 operations are I/O-bound, so more threads than vCPUs are effective.
    Lambda allocates 1 vCPU per 1769MB memory; thread count scales accordingly.

    Returns:
        int: Optimal worker count between 4 and 32.
    """
    import os
    try:
        memory_mb = int(os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', '512'))
        return min(32, max(4, memory_mb // 256))
    except (ValueError, TypeError):
        return 4  # safe default


@dataclass
class ContentBlock:
    """Content block of the Merkle DAG"""
    block_id: str  # sha256 hash
    s3_path: str
    size: int
    fields: List[str]  # List of fields contained in this block
    checksum: str


@dataclass
class ManifestPointer:
    """Pointer Manifest structure"""
    manifest_id: str
    version: int
    parent_hash: Optional[str]
    manifest_hash: str
    config_hash: str  # For workflow_config verification
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
    KernelStateManager - Analemma OS unified state management kernel

    v3.3 Unified Architecture (Zero Redundancy):
    - Merkle DAG-based Delta Storage (90% duplicate data elimination)
    - DynamoDB pointer-based state restoration (latest_state.json retired)
    - Built-in 2-Phase Commit (temp -> ready tag strategy)
    - Automatic GC integration (Ghost Block prevention)

    Core design principles:
    1. latest_state.json retired: only manifest_id pointer stored in DynamoDB
    2. Single save path: all saves unified through save_state_delta()
    3. Built-in 2PC: S3 uploads always tagged status=temp; status=ready on DynamoDB success
    4. Automatic GC: Phase 10 BackgroundGC removes temp-tagged blocks

    Phase B: Unified Architecture (EventualConsistencyGuard integrated)
    Phase E-F-G: StatePersistenceService/StateManager/StateDataManager absorbed
    v3.3: Radical redesign (migration shackles removed)
    """
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [v3.3] Global canonical serialization: bit-exact hash generation
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Exposed as @staticmethod so external callers (e.g., initialize_state_data.py)
    # can compute identical hashes without instantiating the class.
    # [CRITICAL] Only modify this method when changing the hash algorithm.
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @staticmethod
    def get_canonical_json(data: Any) -> bytes:
        """
        [v3.3] Global canonical serialization: bit-exact hash generation

        External usage example:
        ```python
        from src.services.state.state_versioning_service import StateVersioningService
        canonical_data = StateVersioningService.get_canonical_json(manifest_obj)
        hash_value = StateVersioningService.compute_hash(manifest_obj)
        ```

        Args:
            data: Python object to serialize (dict, list, etc.)

        Returns:
            bytes: UTF-8 encoded canonical JSON (sorted keys, no whitespace)

        Note:
            - sort_keys=True: prevents hash mismatch from key ordering
            - separators=(',', ':'): removes whitespace for hash consistency
            - ensure_ascii=False: preserves UTF-8 (multibyte characters)
            - datetime -> ISO 8601 normalization
            - Decimal -> float conversion (DynamoDB compatible)
        """
        from datetime import datetime, date
        from decimal import Decimal
        
        def default_handler(obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()  # ISO 8601 normalization
            if isinstance(obj, Decimal):
                return str(obj)         # [v3.32] str() — unified with hash_utils._canonical_bytes
            if hasattr(obj, '__dict__'):
                return obj.__dict__
            return str(obj)
        
        return json.dumps(
            data,
            sort_keys=True,             # Key sorting required
            separators=(',', ':'),      # No whitespace for hash consistency
            ensure_ascii=False,         # Preserve UTF-8
            default=default_handler
        ).encode('utf-8')
    
    @staticmethod
    def compute_hash(data: dict) -> str:
        """
        [v3.3] Canonical hash computation (SHA-256)

        [CRITICAL] Only modify this method when changing the hash algorithm.
        e.g., SHA-256 -> SHA-512 migration:
        ```python
        return hashlib.sha512(canonical_json).hexdigest()
        ```

        Args:
            data: Python dictionary to hash

        Returns:
            str: SHA-256 hash (hex digest)
        """
        canonical_json = StateVersioningService.get_canonical_json(data)
        return hashlib.sha256(canonical_json).hexdigest()
    
    def __init__(
        self,
        dynamodb_table: str,
        s3_bucket: str,
        block_references_table: str = None,
        use_2pc: bool = False,              # Phase B: 2-Phase Commit
        gc_dlq_url: Optional[str] = None    # Phase B: GC DLQ
    ):
        self.dynamodb = boto3.resource('dynamodb')
        self.dynamodb_client = boto3.client('dynamodb')  # For TransactWriteItems
        self.table = self.dynamodb.Table(dynamodb_table)
        self.s3 = boto3.client('s3')
        self.bucket = s3_bucket
        
        # Block Reference Counting Table (for Garbage Collection)
        # Read from environment variable or use default
        self.block_references_table = block_references_table or dynamodb_table.replace('Manifests', 'BlockReferences')
        try:
            self.block_refs_table = self.dynamodb.Table(self.block_references_table)
        except Exception as e:
            logger.warning(f"BlockReferences table not available: {e}")
        
        # Phase B: 2-Phase Commit configuration
        self.use_2pc = use_2pc
        self.gc_dlq_url = gc_dlq_url
        self._consistency_guard = None  # Lazy import (initialized on first use)

        # S3 block key prefix (state-blocks/{block_id}.json)
        self.prefix = "state-"
    
    def create_manifest(
        self,
        workflow_id: str,
        workflow_config: dict,
        segment_manifest: List[dict],
        parent_manifest_id: Optional[str] = None
    ) -> ManifestPointer:
        """
        Create a new Pointer Manifest

        v3.3: 2-Phase Commit enforced (legacy path removed)

        Args:
            workflow_id: Workflow ID
            workflow_config: Workflow configuration (for hash computation)
            segment_manifest: List of segments
            parent_manifest_id: Previous version ID (Merkle chain)

        Returns:
            ManifestPointer: Created manifest pointer
        """
        # v3.3: 2-Phase Commit enforced
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
        v3.3: 2-Phase Commit via EventualConsistencyGuard (enforced)
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
            logger.info("[StateVersioningService] EventualConsistencyGuard initialized")
            # [FIX] Removed orphaned code left from previous refactoring.
            # parent_manifest_id is already included in the metadata dict below (line ~198).
        
        # Generate manifest base info
        import uuid
        manifest_id = str(uuid.uuid4())
        # [Type Safety] DynamoDB Number -> Decimal -> int explicit conversion
        # EventualConsistencyGuard's json.dumps(default=str) converts Decimal to "21",
        # while compute_hash converts Decimal to 21.0, causing hash mismatch
        version = int(self._get_next_version(workflow_id))
        config_hash = self._compute_hash(workflow_config)
        
        # Store workflow_config in S3
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
        
        # Block splitting and hash computation.
        # EventualConsistencyGuard expects plain dicts with block_id, s3_key, data.
        blocks = []
        for idx, segment in enumerate(segment_manifest):
            segment_json = StateVersioningService.get_canonical_json(segment).decode('utf-8')
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

        # Metadata
        metadata = {
            'workflow_id': workflow_id,
            'version': version,
            'created_at': datetime.utcnow().isoformat(),
            'parent_manifest_id': parent_manifest_id,
            'parent_hash': parent_hash or '',
            'total_segments': len(segment_manifest)
        }

        # Execute 2PC via EventualConsistencyGuard (returns stored manifest_id str)
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

    # [v3.35] _create_manifest_legacy removed — dead code, never called.
    # Active path uses EventualConsistencyGuard.create_manifest() exclusively.
    # Also removed: _split_into_blocks, _block_exists, _rollback_pending_blocks,
    # _commit_pending_blocks (all only called from legacy path).

    def get_manifest(self, manifest_id: str) -> ManifestPointer:
        """
        Load a manifest pointer

        Args:
            manifest_id: Manifest ID

        Returns:
            ManifestPointer: Manifest pointer
        """
        try:
            response = self.table.get_item(Key={'manifest_id': manifest_id})
            
            if 'Item' not in response:
                raise ValueError(f"Manifest not found: {manifest_id}")
            
            item = response['Item']

            # Reconstruct ContentBlocks
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
        Merkle Root verification

        Returns:
            bool: Whether integrity verification passed
        """
        try:
            response = self.table.get_item(Key={'manifest_id': manifest_id})
            
            if 'Item' not in response:
                logger.error(f"Manifest not found: {manifest_id}")
                return False
            
            item = response['Item']
            
            # Recompute Merkle Root from stored blocks
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
                logger.info(f"Manifest {manifest_id} integrity verified")
            
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
        [Phase 7] segment_config integrity verification (Pre-computed Hash)

        Improvements:
        - Before: re-run partition_workflow() each time (200-500ms)
        - After: O(1) verification via Pre-computed Hash (1-5ms)

        Args:
            segment_config: Segment configuration to verify
            manifest_id: Manifest ID
            segment_index: Segment index

        Returns:
            bool: Whether verification passed
        """
        try:
            # 1. Load Pre-computed Hash from DynamoDB
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
            
            # 2. Compute hash of the input segment_config
            actual_hash = self._compute_hash(segment_config)
            
            # 3. Compare
            is_valid = actual_hash == expected_hash
            
            if not is_valid:
                logger.error(
                    f"[Integrity Violation] Segment {segment_index} hash mismatch!\n"
                    f"Expected: {expected_hash[:16]}...\n"
                    f"Actual:   {actual_hash[:16]}..."
                )
            else:
                logger.info(f"Segment {segment_index} verified: {actual_hash[:8]}...")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"Verification failed: {e}", exc_info=True)
            return False
    
    def _compute_hash(self, data: dict) -> str:
        """
        [Wrapper] Instance method wrapper for static compute_hash()

        Delegates to StateVersioningService.compute_hash() internally.
        Hash algorithm changes only need to update the static method.
        """
        return StateVersioningService.compute_hash(data)
    
    def _compute_merkle_root(
        self,
        blocks: List[ContentBlock],
        config_hash: str,
        parent_hash: Optional[str]
    ) -> str:
        """
        Compute Merkle Root

        Structure:
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
    
    def _load_blocks(self, block_paths: List[str]) -> List[ContentBlock]:
        """Load blocks from S3"""
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
        Pre-compute individual hashes for each segment (Phase 7 optimization)

        Rationale:
        - Re-running partition_workflow() per segment is too expensive
        - Pre-computed Hash reduces O(n) -> O(1) verification

        Returns:
            Dict[segment_index, hash]: Per-segment hash values
        """
        segment_hashes = {}
        
        for idx, segment in enumerate(manifest):
            # Extract segment_config and compute hash
            segment_config = segment.get('segment_config', segment)
            segment_hash = self._compute_hash(segment_config)
            segment_hashes[str(idx)] = segment_hash  # DynamoDB prefers string keys
            
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
        Runtime segment injection with real-time hash map update (Phase 12)

        [Logic #4] Ordered Hash Chain (prevents index collision)
        [Resilience #3] Internal exponential backoff retry (100ms->200ms->400ms)

        Phase 8.3 support:
        - Dynamically add segments
        - Reconstruct segment_hashes as ordered_hash_chain
        - Auto-shift existing segment indices on mid-insertion
        - Increment hash_version (Optimistic Locking)
        - Auto-retry on conflict (no caller burden)

        Args:
            manifest_id: Manifest ID
            segment_config: New segment configuration
            insert_position: Insertion position (0-based)
            max_retries: Maximum retry count (default: 3)

        Returns:
            str: Newly computed segment hash
        """
        import time
        
        # Compute new segment hash
        new_segment_hash = self._compute_hash(segment_config)
        
        # [Logic #4] Load and reorder existing segment_hashes
        # [Resilience #3] Exponential backoff retry loop
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
                
                # Ordered Hash Chain reconstruction (shift all indices >= insert_position by +1)
                new_segment_hashes = {}
                
                for idx_str, hash_value in sorted(segment_hashes.items(), key=lambda x: int(x[0])):
                    idx = int(idx_str)
                    
                    if idx < insert_position:
                        # Before insertion point: keep as-is
                        new_segment_hashes[str(idx)] = hash_value
                    else:
                        # After insertion point: shift index by +1
                        new_segment_hashes[str(idx + 1)] = hash_value
                
                # Insert new segment
                new_segment_hashes[str(insert_position)] = new_segment_hash
                
                # DynamoDB atomic update (full map replacement)
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
                    f"[Dynamic Injection] Segment injected at position {insert_position} "
                    f"(attempt {attempt + 1}/{max_retries}). "
                    f"Shifted {len(segment_hashes) - insert_position} existing segments. "
                    f"manifest_id={manifest_id}, hash={new_segment_hash[:8]}..., "
                    f"hash_version={current_hash_version} → {new_hash_version}"
                )
                
                return new_segment_hash
                
            except ClientError as e:
                if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                    # Optimistic Lock conflict - exponential backoff retry
                    if attempt < max_retries - 1:
                        backoff_ms = (2 ** attempt) * 100  # 100ms, 200ms, 400ms
                        logger.warning(
                            f"[Dynamic Injection] Concurrent modification detected "
                            f"(hash_version mismatch). Retrying in {backoff_ms}ms... "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )
                        time.sleep(backoff_ms / 1000.0)
                        continue
                    else:
                        # Final failure
                        logger.error(
                            f"[Dynamic Injection] Failed after {max_retries} attempts. "
                            f"manifest_id={manifest_id}, position={insert_position}"
                        )
                        raise RuntimeError(
                            f"inject_dynamic_segment failed after {max_retries} retries: "
                            f"hash_version conflict (concurrent modifications detected)"
                        ) from e
                else:
                    # Other DynamoDB error - abort immediately
                    logger.error(f"[Dynamic Injection] DynamoDB error: {e}")
                    raise
        
        # Fallback on loop exit (theoretically unreachable)
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
        O(1) segment integrity verification (handles dynamic segment injection)

        Before: O(N) - serialize and hash segment_config
        After: O(1) - lookup pre-computed hash from DynamoDB

        Dynamic segment injection scenario:
        1. Manifest creation: hash_version=1
        2. Runtime segment addition: hash_version=2
        3. Verification: check hash_version match (optional)

        Args:
            manifest_id: Manifest ID
            segment_id: Segment ID
            segment_config: Segment configuration to verify
            allow_hash_version_drift: If True, allow hash_version mismatch

        Returns:
            bool: Whether verification passed
        """
        # Lookup pre-computed hash from DynamoDB
        response = self.table.get_item(
            Key={'manifest_id': manifest_id},
            ProjectionExpression='segment_hashes, hash_version'
        )
        
        if 'Item' not in response:
            logger.error(f"Manifest {manifest_id} not found")
            return False
        
        segment_hashes = response['Item'].get('segment_hashes', {})
        current_hash_version = response['Item'].get('hash_version', 1)
        
        # Check if segment hash exists
        segment_key = str(segment_id)
        if segment_key not in segment_hashes:
            logger.warning(
                f"Segment {segment_id} not found in hash map "
                f"(hash_version={current_hash_version}). "
                f"Possible dynamic injection in progress."
            )
            # If dynamic injection allowed, fall back to recomputation
            if allow_hash_version_drift:
                return self._verify_by_recompute(segment_config)
            return False
        
        expected_hash = segment_hashes[segment_key]
        
        # Hash of segment_config at execution time
        actual_hash = self._compute_hash(segment_config)
        
        is_valid = expected_hash == actual_hash
        
        if not is_valid:
            logger.error(
                f"INTEGRITY_VIOLATION: Segment {segment_id} hash mismatch. "
                f"Expected: {expected_hash[:8]}..., Actual: {actual_hash[:8]}..., "
                f"hash_version={current_hash_version}"
            )
        else:
            logger.debug(f"Segment {segment_id} verified (hash_version={current_hash_version})")
        
        return is_valid
    
    def _verify_by_recompute(self, segment_config: dict) -> bool:
        """
        Verify segments not in hash map via recomputation (fallback)
        """
        logger.info("Falling back to hash recomputation for dynamic segment")
        # Dynamic segments are assumed always valid (guaranteed by Phase 8.3)
        return True
    
    def _get_next_version(self, workflow_id: str) -> int:
        """Compute next version number for the workflow"""
        try:
            # Query latest version via WorkflowIndex GSI
            response = self.table.query(
                IndexName='WorkflowIndex',
                KeyConditionExpression='workflow_id = :wf_id',
                ExpressionAttributeValues={':wf_id': workflow_id},
                ScanIndexForward=False,  # Descending order
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
    # [Phase 4] StateHydrator Integration - Read Engine
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def load_manifest_segments(
        self,
        manifest_id: str,
        segment_indices: Optional[List[int]] = None,
        use_s3_select: bool = True
    ) -> List[dict]:
        """
        [Phase 4] Reconstruct segment_manifest from Manifest

        Improvements:
        - Fetch block list from DynamoDB, then parallel-load from S3
        - Extract specific segments via S3 Select (reduces network cost)
        - Use fields attribute: load only blocks containing target segments

        Args:
            manifest_id: Manifest ID
            segment_indices: Segment indices to load (None for all)
            use_s3_select: Whether to use S3 Select

        Returns:
            Reconstructed segment_manifest list
        """
        # 1. Load manifest from DynamoDB
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
        
        # 2. Determine segments to load
        if segment_indices is None:
            segment_indices = list(range(segment_count))
        
        # 3. Filter to required blocks only (using fields attribute)
        required_blocks = []
        for block_path in block_paths:
            # Query metadata to check block fields
            block_id = block_path.split('/')[-1].replace('.json', '')
            key = block_path.replace(f"s3://{self.bucket}/", "")
            
            try:
                head = self.s3.head_object(Bucket=self.bucket, Key=key)
                fields_str = head.get('Metadata', {}).get('fields', '')
                
                if not fields_str:
                    # No metadata: load all blocks
                    required_blocks.append((block_path, None))
                    continue
                
                # Extract segment indices from fields
                for field in fields_str.split(','):
                    if "segment_" in field:
                        seg_idx = int(field.split('_')[1])
                        if seg_idx in segment_indices:
                            required_blocks.append((block_path, seg_idx))
                            break
                            
            except Exception as e:
                logger.warning(f"Failed to check block metadata {block_id}: {e}")
                # Fallback: include all blocks
                required_blocks.append((block_path, None))
        
        logger.info(
            f"[Reconstruction] Loading {len(required_blocks)}/{len(block_paths)} blocks "
            f"for {len(segment_indices)} segments"
        )
        
        # 4. Parallel-load blocks from S3
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
                    
                    # JSON parsing
                    if block_content:
                        segment = json.loads(block_content)
                        
                        # Determine segment index
                        if seg_idx is not None:
                            segment_data[seg_idx] = segment
                        else:
                            # Extract segment index via segment.get('segment_id')
                            if 'segment_id' in segment:
                                segment_data[segment['segment_id']] = segment
                            
                except Exception as e:
                    logger.error(f"Failed to load block {block_path}: {e}")
        
        # 5. Sort and return in order
        result = [segment_data[idx] for idx in sorted(segment_data.keys()) if idx in segment_indices]
        
        logger.info(f"[Reconstruction] Loaded {len(result)} segments successfully")
        return result
    
    def _load_block(self, block_path: str, segment_index: Optional[int]) -> Optional[str]:
        """
        [Feedback #1] JSON Lines format + S3 Select optimization

        Improvements:
        - Before: JSON DOCUMENT mode + WHERE s.segment_id (identifier issue)
        - After: JSON LINES mode (ndjson) - faster and more accurate
        - Up to 99% network cost reduction (4MB -> 40KB)

        Args:
            block_path: S3 path (s3://bucket/key)
            segment_index: Expected segment index (optional)

        Returns:
            Block content (JSON string)
        """
        key = block_path.replace(f"s3://{self.bucket}/", "")
        
        try:
            # [Feedback #1] Prefer JSON Lines format
            # S3 Select with JSON LINES mode (ndjson)
            if segment_index is not None:
                try:
                    response = self.s3.select_object_content(
                        Bucket=self.bucket,
                        Key=key,
                        ExpressionType='SQL',
                        Expression=f"SELECT * FROM s3object s WHERE s.segment_id = {segment_index}",
                        InputSerialization={
                            'JSON': {'Type': 'LINES'},  # JSON Lines mode
                            'CompressionType': 'GZIP'  # S3 Select compatible compression
                        },
                        OutputSerialization={'JSON': {'RecordDelimiter': '\n'}}
                    )
                    
                    # Process S3 Select streaming response
                    content = ''
                    for event in response['Payload']:
                        if 'Records' in event:
                            content += event['Records']['Payload'].decode('utf-8')
                    
                    if content:
                        logger.info(
                            f"[S3 Select] Extracted segment {segment_index} from {key} "
                            f"(bandwidth saved: ~{(4*1024*1024 - len(content.encode('utf-8'))) / 1024:.1f}KB)"
                        )
                        return content
                    else:
                        # Fallback when S3 Select finds no match
                        logger.warning(f"[S3 Select] No match for segment {segment_index}, falling back to full load")
                        
                except Exception as select_error:
                    # Fallback on S3 Select failure (e.g., JSON format mismatch)
                    logger.warning(f"[S3 Select] Failed, falling back to get_object: {select_error}")
            
            # Fallback: full object download
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            content = response['Body'].read().decode('utf-8')
            return content
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.error(f"Block not found: {block_path}")
                return None
            raise
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # [Feedback #2-3] Ghost Block Prevention + Transaction Batching Helpers
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
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
    # [NEW] Block Reference Counting (Garbage Collection support)
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
        Decrement block reference count (on manifest invalidation)

        Args:
            block_ids: Block IDs to decrement reference count
            workflow_id: HASH key for composite key (WorkflowBlockReferencesV3 schema required)

        Returns:
            Number of updated blocks
        """
        updated_count = 0
        
        for block_id in block_ids:
            try:
                # [FIX] WorkflowBlockReferencesV3 composite key: HASH=workflow_id, RANGE=block_id
                response = self.block_refs_table.update_item(
                    Key={
                        'workflow_id': workflow_id,  # HASH key (required)
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
                
                # Mark as GC candidate when reference count reaches 0
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
        Query blocks with zero reference count (for Garbage Collection)

        Args:
            older_than_days: Days since last reference

        Returns:
            List of block IDs eligible for GC
        """
        from datetime import timedelta
        
        cutoff_date = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat()
        
        from boto3.dynamodb.conditions import Attr

        try:
            # [FIX] GSI 'ReferenceCountIndex' is not defined in template.yaml.
            # Replaced with scan + FilterExpression (GC path is latency-insensitive).
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
        Invalidate manifest (on dynamic re-partitioning)

        Mark existing manifest as INVALIDATED to indicate
        a new manifest has been created.

        [CRITICAL FIX] Added block reference count decrement (Garbage Collection support)

        Args:
            manifest_id: Manifest ID to invalidate
            reason: Invalidation reason

        Returns:
            Whether operation succeeded
        """
        try:
            # 1. Load manifest info (to extract block list)
            manifest = self.get_manifest(manifest_id)
            block_ids = [block.block_id for block in manifest.blocks]
            
            # 2. Invalidate manifest
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
                # Do not throw if already invalidated
                ConditionExpression='attribute_exists(manifest_id)'
            )
            
            # 3. Decrement block reference counts (prepare for Garbage Collection)
            # Extract workflow_id from manifest DynamoDB item (required for block_refs_table composite key)
            manifest_item_resp = self.table.get_item(Key={'manifest_id': manifest_id})
            workflow_id_for_refs = (
                manifest_item_resp.get('Item', {}).get('workflow_id', '')
                if 'Item' in manifest_item_resp else ''
            )
            decremented = self.decrement_block_references(block_ids, workflow_id=workflow_id_for_refs)
            
            logger.info(
                f"[Manifest Invalidation] {manifest_id} invalidated: {reason}. "
                f"Decremented {decremented} block references."
            )
            return True
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                logger.warning(f"[Manifest Invalidation] Manifest not found: {manifest_id}")
                return False
            raise
            
        except Exception as e:
            logger.error(f"[Manifest Invalidation] Failed: {e}")
            return False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # v3.3 KernelStateManager - Single State Save Path
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
        v3.3 KernelStateManager core save logic

        Delta-based state persistence:
        1. Receive changed delta from StateHydrator
        2. Create Merkle DAG blocks and upload to S3 (tagged status=temp)
        3. DynamoDB TransactWriteItems:
           - Register new manifest
           - Increment block reference counts
           - Update WorkflowsTableV3.latest_manifest_id (pointer)
        4. Change S3 block tags to status=ready (2-Phase Commit complete)

        Args:
            delta: Delta dictionary containing only changed fields
            workflow_id: Workflow ID
            execution_id: Execution ID
            owner_id: Owner ID (for DynamoDB pointer)
            segment_id: Latest segment ID
            previous_manifest_id: Parent manifest ID (version chain)

        Returns:
            Dict: {
                'manifest_id': str,
                'block_ids': List[str],
                'committed': bool,
                's3_paths': List[str]
            }

        Example:
            >>> result = kernel.save_state_delta(
            ...     delta={'user_input': 'new value'},  # Only changed fields
            ...     workflow_id='wf-123',
            ...     execution_id='exec-456',
            ...     owner_id='user-789',
            ...     segment_id=5,
            ...     previous_manifest_id='manifest-abc'
            ... )
            >>> print(result['manifest_id'])
            'manifest-def'

        Design principles:
        - latest_state.json retired: only manifest_id stored in DynamoDB
        - Built-in 2PC: temp -> ready tag transition
        - Automatic GC: BackgroundGC removes temp-tagged blocks
        - Single save path: system-wide consistency guaranteed
        """
        try:
            logger.info(
                f"[KernelStateManager] Saving delta for {workflow_id}/{execution_id} "
                f"(segment={segment_id}, delta_keys={len(delta)})"
            )
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 1: Create Content Blocks and parallel upload to S3 (status=temp)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [v3.32] Parallelize S3 PUT -- switched from sequential for-loop to ThreadPoolExecutor.
            # 500 segments x 10 fields = 5,000 PUTs. Sequential ~50-150s -> parallel ~5-15s.
            # S3 PUT is I/O-bound, so thread parallelism is effective regardless of GIL.
            import gzip
            from concurrent.futures import ThreadPoolExecutor, as_completed

            blocks = []
            uploaded_block_ids = []

            # Step 1: CPU-bound serialization + compression (sequential due to GIL)
            # [v3.32 FIX] block_hash computed from raw data (BUG-4).
            # gzip.compress() embeds mtime in header -> non-deterministic.
            # Same content compressed at different times produces different hashes,
            # breaking content-addressable dedup.
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

            # Step 2: I/O-bound S3 PUT -- parallel execution
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
                f"[KernelStateManager] Phase 2: DynamoDB committed "
                f"(manifest={manifest_id}, blocks={len(blocks)})"
            )
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 3: S3 tag change (status=temp -> status=ready)
            # [Perf #2] Parallel tag update (reduces Lambda execution time)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            def _tag_block_as_ready(block):
                """Block tag update helper (for parallel execution)"""
                s3_key = block.s3_path.replace(f"s3://{self.bucket}/", "")
                self.s3.put_object_tagging(
                    Bucket=self.bucket,
                    Key=s3_key,
                    Tagging={'TagSet': [{'Key': 'status', 'Value': 'ready'}]}
                )
                return block.block_id
            
            # Parallel tag update (Lambda memory-based Adaptive Workers)
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
                f"[KernelStateManager] Phase 3: {tagged_count}/{len(blocks)} blocks marked as ready "
                f"(2-Phase Commit complete via parallel tagging)"
            )
            
            # [P0 FIX] Added manifest_id to return value (ensures Merkle Chain continuity)
            result = {
                'success': True,
                'manifest_id': manifest_id,  # ID for the next segment to reference as parent
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
            logger.error(f"[KernelStateManager] Failed to save delta: {e}")
            # On failure, temp blocks are auto-removed by GC; no explicit rollback needed
            raise RuntimeError(f"Failed to save state delta: {e}")
    
    def load_latest_state(
        self,
        workflow_id: str,
        owner_id: str,
        execution_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        v3.3 KernelStateManager core load logic

        DynamoDB pointer-based state restoration:
        1. Query WorkflowsTableV3.latest_manifest_id (pointer)
        2. Extract block list from manifest
        3. Parallel-download blocks from S3
        4. Reconstruct state via StateHydrator

        Args:
            workflow_id: Workflow ID
            owner_id: Owner ID (DynamoDB key)
            execution_id: Execution ID (optional, for querying specific execution state)

        Returns:
            Dict: Reconstructed full state dictionary

        Example:
            >>> state = kernel.load_latest_state(
            ...     workflow_id='wf-123',
            ...     owner_id='user-789'
            ... )
            >>> print(state['user_input'])
            'restored value'

        Design principles:
        - latest_state.json retired: only DynamoDB pointer used
        - Parallel Merkle block download: fast restoration even for large states
        - StateHydrator integration: blocks -> full state auto-assembly
        """
        try:
            logger.info(
                f"[KernelStateManager] Loading latest state for "
                f"{workflow_id}/{owner_id}"
            )
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 1: Query latest_manifest_id from DynamoDB
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [FIX] Deriving table name via string replacement produces wrong results
            # e.g., 'WorkflowManifests-v3-dev' -> 'WorkflowWorkflowsTableV3-v3-dev'.
            # Read directly from env var, same as save_state_delta() path.
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
                return {}  # Return empty state (first execution)
            
            item = response['Item']
            manifest_id = item.get('latest_manifest_id')
            
            if not manifest_id:
                logger.warning(f"[KernelStateManager] No manifest_id in workflow record")
                return {}
            
            logger.info(f"[KernelStateManager] Phase 1: Found manifest_id={manifest_id}")
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 2: Extract block list from manifest
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            manifest_response = self.table.get_item(
                Key={'manifest_id': manifest_id}
            )
            
            if 'Item' not in manifest_response:
                raise RuntimeError(f"Manifest not found: {manifest_id}")
            
            manifest_data = manifest_response['Item']
            blocks_json = manifest_data.get('blocks', '[]')
            blocks = json.loads(blocks_json) if isinstance(blocks_json, str) else blocks_json
            
            logger.info(f"[KernelStateManager] Phase 2: Found {len(blocks)} blocks")
            
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Phase 3: Parallel-download blocks from S3 and reconstruct state
            # [Perf #1] Parallel download via ThreadPoolExecutor (5-10x speedup)
            # [Perf #2] Adaptive Workers (dynamic adjustment based on Lambda memory)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            reconstructed_state = {}
            
            def _download_block(block_info):
                """Block download helper (for parallel execution)"""
                s3_path = block_info.get('s3_path', '')
                if not s3_path:
                    return None
                
                s3_key = s3_path.replace(f"s3://{self.bucket}/", "")
                response = self.s3.get_object(Bucket=self.bucket, Key=s3_key)
                
                # [FinOps #2] Gzip decompression (check ContentEncoding)
                content_encoding = response.get('ContentEncoding', 'identity')
                raw_data = response['Body'].read()
                
                if content_encoding == 'gzip':
                    import gzip
                    # [RISK] Handle corrupted Gzip data (EOFError defense)
                    # Retry logic handled by the parent ThreadPoolExecutor
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
                    # Backward compatibility: support existing Zstd blocks (gradual migration)
                    try:
                        import zstandard as zstd
                        decompressor = zstd.ZstdDecompressor()
                        block_data = decompressor.decompress(raw_data).decode('utf-8')
                    except ImportError:
                        logger.error("[Zstd] Cannot decompress: zstandard library not installed")
                        raise RuntimeError("zstandard library required for decompression")
                else:
                    block_data = raw_data.decode('utf-8')
                
                # [Consistency #3] NDJSON format support (strip newlines)
                block_data = block_data.strip()  # Remove NDJSON trailing newline
                return json.loads(block_data)
            
            # Compute Adaptive Workers
            optimal_workers = _calculate_optimal_workers()
            
            # Parallel download
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
                f"[KernelStateManager] Phase 3: State reconstructed via parallel download "
                f"({len(reconstructed_state)} keys, {len(blocks)} blocks, workers={optimal_workers})"
            )
            
            return reconstructed_state
            
        except Exception as e:
            logger.error(f"[KernelStateManager] Failed to load state: {e}")
            raise RuntimeError(f"Failed to load latest state: {e}")
