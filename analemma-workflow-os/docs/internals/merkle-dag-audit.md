# Merkle DAG & Hash Generation

> Analemma OS uses a **Merkle DAG (Directed Acyclic Graph)** for state versioning: every state mutation produces a content-addressed block, and blocks are chained via parent hashes into an append-only history graph. This enables O(1) integrity verification, delta-only storage (90%+ dedup), and instant rollback to any prior state.

---

## 1. Core Data Structures

### ContentBlock
```python
@dataclass
class ContentBlock:
    block_id: str       # SHA-256 hash of content
    s3_path: str        # s3://<bucket>/merkle-blocks/<wf_id>/<hash[:2]>/<hash>.json
    size: int           # raw byte size
    fields: List[str]   # field names contained in this block
    checksum: str       # == block_id (self-referencing CAS)
```

Each block is **Content-Addressable**: `block_id = SHA-256(canonical_json(content))`. Two blocks with identical content always produce the same hash, enabling automatic deduplication.

### ManifestPointer
```python
@dataclass
class ManifestPointer:
    manifest_id: str              # manifest-{execution_id}-{segment_id}-{ts}-{uuid[:8]}
    version: int                  # monotonically increasing (via WorkflowIndex GSI)
    parent_hash: Optional[str]    # previous manifest's hash (DAG chain)
    manifest_hash: str            # Merkle Root of this version
    config_hash: str              # SHA-256(workflow_config)
    blocks: List[ContentBlock]
    metadata: Dict
```

Stored in DynamoDB (`WorkflowManifests-v3`). The `parent_hash` field creates the DAG chain — each manifest points to its predecessor.

---

## 2. Hash Generation Pipeline

### 2.1 Canonical Serialization (Single Source of Truth)

**Primary**: `hash_utils.py` — `canonical_bytes(data)`
**Delegated**: `state_versioning_service.py` — `get_canonical_json()` (v3.32 aligned)

```python
json.dumps(
    data,
    sort_keys=True,            # deterministic key ordering
    separators=(',', ':'),     # no whitespace
    ensure_ascii=False,        # preserve UTF-8 (Korean, etc.)
    default=default_handler    # datetime → ISO 8601, Decimal → str()
).encode('utf-8')
```

**Critical invariant**: All hash computations throughout the system MUST use this single serialization method. Any deviation (different key order, extra whitespace, different Decimal handling) produces a different hash.

**v3.34 Change**: `hash_utils.canonical_bytes()` now raises `TypeError` on unsupported types (fail-fast) instead of silently falling back to `repr()`. Private keys (prefix `_`) are filtered out.

### 2.2 Hash Algorithms

| Hash Type | Algorithm | Location | Purpose |
|-----------|-----------|----------|---------|
| `compute_hash()` | **SHA-256** | `StateVersioningService.compute_hash()` | Standard hash for all state data |
| `content_hash()` | **SHA-256** | `hash_utils.content_hash()` | Public API: SHA-256 of canonical JSON |
| `content_hash_md5()` | **MD5** | `hash_utils.content_hash_md5()` | Non-security contexts (cache keys) |
| `quick_id()` | SHA-256 truncated 12 | `hash_utils.quick_id()` | Short identifiers |
| `block_id` | SHA-256 | `save_state_delta()` | Content-Addressable block identity |
| `config_hash` | SHA-256 | `create_manifest()` | workflow_config fingerprint |
| `manifest_hash` | SHA-256 | `_compute_merkle_root()` | Merkle Root (see below) |
| `segment_hash` | SHA-256 | `_compute_segment_hashes()` | Per-segment O(1) verification |
| `streaming_content_hash` | SHA-256 | `hash_utils.SubBlockHashRegistry` | Streaming hash (no full string alloc) |

**Single change point**: To migrate from SHA-256 to SHA-512, only `StateVersioningService.compute_hash()` needs modification. All other hash computations delegate to this method.

### 2.3 Merkle Root Calculation

**File**: `state_versioning_service.py` — `_compute_merkle_root()`

```
blocks_hash = SHA-256(
    concat(b.checksum for b in sorted(blocks, key=block_id))
)

manifest_hash = SHA-256(
    config_hash + parent_hash (or '') + blocks_hash
)
```

Three inputs compose the root:
1. **config_hash** — the workflow_config's SHA-256
2. **parent_hash** — previous manifest's hash (creates the DAG chain) [v3.33 FIX-C: now included]
3. **blocks_hash** — SHA-256 of all block checksums concatenated (sorted by block_id for determinism)

This means changing ANY block, ANY config field, or linking to a different parent produces a completely different Merkle Root.

**v3.33 Change**: `parent_hash` is now resolved from the parent manifest and included in the hash computation. Previously it was excluded, breaking the DAG tamper-evidence chain.

### 2.4 Incremental Hashing (SubBlockHashRegistry)

**File**: `hash_utils.py` — `SubBlockHashRegistry`

For state updates, only dirty fields are rehashed:

```python
compute_incremental_root(full_state, dirty_keys) -> (root, block_hashes)
```

- Fields are classified by temperature tier: HOT / WARM / COLD / CONTROL_PLANE
- Only blocks containing dirty_keys are recomputed
- Unchanged blocks reuse previous hash (CPU pipeline analogy: instruction cache)
- Complexity: O(changed_data) vs O(total_state)

**Parallel variant**: `compute_incremental_root_parallel(full_state, dirty_keys, max_workers=4)`
- Multi-threaded sub-block hashing (hashlib.sha256.update() releases GIL)
- Effective for CPU-bound hashing of independent blocks

Temperature tier classification:
```python
HOT_FIELDS = {'llm_response', 'llm_raw_output', 'current_state', 'token_usage', ...}
WARM_FIELDS = {'step_history', 'messages', 'query_results', ...}
COLD_FIELDS = {'workflow_config', 'partition_map', 'segment_manifest', ...}
CONTROL_PLANE_FIELDS = (imported from state_hydrator)
```

---

## 3. Storage Architecture

### 3.1 Block Storage (S3 — Content-Addressable)

**Path A: Segment Manifest Blocks** (`create_manifest`)
```
s3://<bucket>/state-blocks/<block_id>.json
```
- Raw JSON, no compression
- Blocks > 4MB are automatically chunked with `__chunk__` marker

**Path B: Delta Blocks** (`save_state_delta`) [v3.3+]
```
s3://<bucket>/merkle-blocks/<workflow_id>/<hash[:2]>/<hash>.json
```
- NDJSON format, Gzip compressed (level 6, mtime=0 for determinism)
- **v3.32 BIX-4**: Hash computed on **raw** data (not compressed) for deterministic cross-platform hashing
- 2-Phase Commit: uploaded with `status=temp` tag, changed to `status=committed` after DynamoDB commit

**Path C: Config Blocks**
```
s3://<bucket>/workflow-configs/<workflow_id>/<config_hash>.json
```
- Immutable config storage (CAS — Content-Addressable)

**Path D: Manifest Envelope** (Phase 2.5)
```
s3://<bucket>/manifests/<manifest_id>.json
```
- Contains full manifest metadata for pre-flight verification
- Includes `parent_hash` for DAG reconstruction [v3.33 FIX-D]

### 3.2 Manifest Storage (DynamoDB)

```
Table: WorkflowManifests-v3
├── manifest_id (HASH key)
├── version (Number)
├── workflow_id (String)
├── parent_hash → previous manifest's hash (DAG chain link)
├── manifest_hash → Merkle Root
├── config_hash → SHA-256 of workflow_config
├── segment_hashes → {seg_idx: hash} (pre-computed, Phase 7)
├── hash_version → optimistic lock for dynamic segment injection
├── s3_pointers
│   ├── manifest → S3 path to manifest JSON
│   ├── config → S3 path to workflow_config
│   └── state_blocks → [S3 paths to ContentBlocks]
├── metadata (created_at, segment_count, total_size, ...)
├── transaction_id → 2PC transaction reference
├── status → 'ACTIVE' | 'INVALIDATED'
└── ttl → 30-day TTL for GC

GSIs:
  - WorkflowIndex: workflow_id (HASH), version (RANGE)
  - HashIndex: manifest_hash (HASH) — for GC config reference counting
  - ParentHashIndex: parent_hash (HASH) — for orphan traversal in rollback GC
```

### 3.3 Pointer Table (WorkflowsTableV3)

```
Table: WorkflowsTableV3
├── ownerId (HASH key)
├── workflowId (RANGE key)
├── latest_manifest_id → current active manifest
├── latest_segment_id → monotonic segment guard
├── latest_execution_id
└── updated_at
```

Pointer advancement rule: Only advance if `segment_id >= current_latest_segment_id` (prevents stale overwrites from parallel fan-out).

---

## 4. Two-Phase Commit (2PC) Protocol

**File**: `eventual_consistency_guard.py` — `EventualConsistencyGuard`

2PC is **enforced** (no opt-out since v3.3). The protocol ensures atomicity between S3 block uploads and DynamoDB manifest commits.

### Phase 1: Prepare (S3 Block Upload)
```python
s3.put_object(
    Bucket=bucket, Key=s3_key,
    Body=compressed_data,
    Tagging=f"status=pending&transaction_id={txn_id}&upload_nonce={nonce}",
    Metadata={'block_id', 'transaction_id', 'workflow_id'}
)
```
- Unique `upload_nonce` per block (TOCTOU race condition prevention)
- Parallel upload: workers = memory / 256, capped 4–32
- Rollback on Phase 1 failure: S3 delete objects

### Phase 2: Commit (DynamoDB TransactWriteItems)
- Block reference count increments (batched, 99-item chunks — DynamoDB limit is 100)
- Manifest Put with `attribute_not_exists(manifest_id)` condition (idempotent)
- Retryable errors: ProvisionedThroughputExceededException, ThrottlingException, InternalServerError
- Non-retryable: ValidationError, ConditionalCheckFailed
- Max retries: 3 with exponential backoff (100ms → 200ms → 400ms)

### Phase 2.5: Manifest S3 Envelope
```json
{
  "manifest_id": "...",
  "version": int,
  "workflow_id": "...",
  "parent_hash": "...",
  "manifest_hash": "...",
  "config_hash": "...",
  "segment_hashes": {...},
  "transaction_id": "...",
  "committed": true,
  "committed_at": "ISO timestamp",
  "segments": [...]
}
```
- Non-fatal if fails (DynamoDB is source of truth)

### Phase 3: Confirm (S3 Tag Update)
- Tags: `status=pending` → `status=committed`
- Non-fatal failure (Background GC cleans uncommitted blocks)

### Ghost State Risk
If Phase 2 pointer advancement exhausts all retries, the manifest is persisted but the pointer is stale. The `__merkle_save_failed` flag is injected into state for next-segment detection (v3.34, segment_runner_service.py:3474).

### GC DLQ Handling
When Phase 2 fails:
- SQS DLQ message per block (batched, 10/message, 5-minute delay)
- `process_dlq_gc_message()` has **two conditions** for safe deletion:
  1. S3 tag `status != 'committed'`
  2. S3 tag `upload_nonce == message upload_nonce`
- Condition 2 prevents: txn-A fails → txn-B re-uploads same block → GC deletes txn-B's block

---

## 5. Integrity Verification Pipeline

### 5.1 Initialization: Pre-flight Check (Phase 8.1)

**File**: `initialize_state_data.py`, line ~740

After creating the Merkle manifest:
1. Read back the manifest from S3
2. Extract INVARIANT fields: `{workflow_id, version, config_hash, segment_hashes}`
3. Recompute hash via `StateVersioningService.compute_hash()`
4. Compare with stored `manifest_hash`
5. If mismatch → log corruption warning (but don't halt initialization)

### 5.2 Execution: Trust Chain Gatekeeper (Phase 8.4)

**File**: `segment_runner_service.py`, line ~3977

Before executing any segment, Zero Trust verification:
1. Load pre-computed `segment_hashes` from DynamoDB manifest
2. Compute SHA-256 of current `segment_config`
3. Compare with stored hash for this segment index
4. **If mismatch → KERNEL PANIC**: halt execution, fire CloudWatch alarm

```
[KERNEL PANIC] [SECURITY ALERT]
segment_config INTEGRITY VIOLATION DETECTED!
Severity: CRITICAL
Recommended: INVESTIGATE_IMMEDIATELY
```

Security incident log includes: manifest_id, segment_index, execution_id, workflow_id, owner_id. Possible causes: MITM attack, S3 tampering, manifest corruption.

### 5.3 Full Manifest Verification

**File**: `state_versioning_service.py` — `verify_manifest_integrity()`

1. Load manifest from DynamoDB
2. Reconstruct blocks from `s3_pointers.state_blocks`
3. Recompute `_compute_merkle_root(blocks, config_hash, parent_hash)`
4. **v3.34**: Normalize legacy `'null'` string → `None` in parent_hash before comparison
5. Compare with stored `manifest_hash`
6. Returns `bool`

### 5.4 Segment-Level Verification

**File**: `state_versioning_service.py` — `verify_segment_config()`

- **Phase 7 Optimization**: O(1) pre-computed hash lookup (vs O(N) full recompute)
- Loads `segment_hashes` from manifest
- Computes hash of input segment_config
- Handles dynamic segment injection (allows `hash_version` drift with flag)

---

## 6. Garbage Collection (Merkle GC)

**File**: `merkle_gc_service.py` — `MerkleGarbageCollector`

### 6.1 Reference Counting

```
Table: BlockReferenceCounts
├── block_id (HASH key) — SHA-256 hash
├── workflow_id (String)
├── ref_count (Number) — atomic increment/decrement
├── is_frozen (Boolean) — Safe Chain Protection
├── zero_reached_at (ISO timestamp) — graceful wait start
├── freeze_reason (String)
├── frozen_at (String)
├── rollback_orphaned (Boolean) — Agent Governance tag
├── orphaned_manifest_id (String)
├── last_accessed (String)
└── transaction_id (String)
```

#### Three-Layer GC Safety (v3.33)

| Layer | Mechanism | Protects Against |
|-------|-----------|-----------------|
| 1 | Atomic Reference Counting (DynamoDB ADD) | Concurrent writes |
| 2 | Delete-time Reachability Re-check (TOCTOU) | Race condition between check and delete |
| 3 | Safe Chain Protection (frozen blocks) | Evidence destruction after security violation |

**`_decrement_and_check_zero(block_id, graceful_wait_seconds=300)`**
```sql
UPDATE SET ref_count = if_not_exists(ref_count, 0) - 1,
           last_accessed = :now,
           zero_reached_at = if_not_exists(zero_reached_at, :null)
CONDITION ref_count > 0  -- prevents negative counts
```
Returns True only if: ref_count = 0 AND not frozen AND graceful_wait elapsed

**`_verify_still_unreachable(block_id)`** — Layer 2 TOCTOU defense:
- Conditional delete of ref_count entry
- Condition: `ref_count <= 0 AND (is_frozen NOT EXISTS OR is_frozen = false)`
- Re-verified at deletion time (not at check time)

### 6.2 Block Lifecycle

```
Block Created (save_state_delta)
  │ status=pending → status=committed (2PC Phase 3)
  │ ref_count++ (2PC Phase 2)
  │
Manifest TTL Expires (30 days)
  │ DynamoDB Streams REMOVE event
  │
  ├── ref_count-- (atomic decrement)
  │   ├── ref_count > 0 → keep block (other manifests reference it)
  │   └── ref_count = 0 → wait 5 minutes (graceful period)
  │       └── TOCTOU re-check → S3 Bulk Delete (1000/batch)
  │
  └── is_frozen = true → NEVER delete (evidence preservation)
```

### 6.3 Safe Chain Protection

On security violations, `freeze_manifest_blocks()`:
- Sets `is_frozen=True` on all blocks in the manifest
- Extends manifest TTL by 90 days
- Records `freeze_reason` and `frozen_at` timestamp
- Frozen blocks are **never deleted** by GC (evidence preservation for audit trail)

### 6.4 Rollback Orphan Detection (Agent Governance v2.1)

**File**: `merkle_gc_service.py` — `mark_rollback_orphans()`

When Governor detects anomaly (anomaly_score > OPTIMISTIC_ROLLBACK_THRESHOLD):
1. **DFS traversal** via `ParentHashIndex` GSI — finds all orphaned manifests
2. Tags orphaned blocks as `rollback_orphaned=True`
3. Sets 30-day TTL for cleanup (7-day for HARD_ROLLBACK — data corruption risk)
4. Returns: `{orphaned_manifests, orphaned_blocks, grace_period_expires_at}`

**v3.35 Change**: Rollback budget (`MAX_ROLLBACKS_PER_EXECUTION=5`) prevents infinite GC flooding from repeated SOFT_ROLLBACK.

### 6.5 Config Block Protection

Config blocks (stored at `workflow-configs/...`) are only deleted when:
- Reference count via `HashIndex` GSI query ≤ 1
- Prevents deleting shared configs used across multiple manifest versions

### 6.6 Glacier Lifecycle Policy

| Age | Action |
|-----|--------|
| 0–30 days | S3 Standard (hot access) |
| 30–90 days | Glacier Instant Retrieval |
| 90+ days | Deletion |
| Frozen blocks | Extended to 90 days post-freeze, no auto-delete |

---

## 7. DAG Structure Visualization

```
Manifest v1 (root)
├── config_hash: SHA-256(workflow_config)
├── parent_hash: null
├── blocks: [block_A, block_B]
└── manifest_hash: SHA-256(config + '' + SHA-256(A.checksum + B.checksum))
         │
         ▼ parent_hash = v1.manifest_hash
Manifest v2 (delta)
├── config_hash: (same if config unchanged)
├── parent_hash: v1.manifest_hash
├── blocks: [block_B, block_C]  ← block_B reused (dedup), block_C new
└── manifest_hash: SHA-256(config + v1.hash + SHA-256(B+C))
         │
         ▼ parent_hash = v2.manifest_hash
Manifest v3 (delta)
├── ...
└── manifest_hash: SHA-256(config + v2.hash + ...)
```

Key property: **changing any ancestor manifest invalidates all descendant hashes** (tamper-evident chain).

---

## 8. Dynamic Segment Injection (Phase 12)

**File**: `state_versioning_service.py` — `inject_dynamic_segment()`

When segments are added at runtime:
1. Compute hash of new segment_config
2. Load existing `segment_hashes` from DynamoDB
3. **Ordered Hash Chain**: Shift all segment indices ≥ insert_position by +1
4. Insert new hash at target position
5. Increment `hash_version` (optimistic locking)
6. Conditional DynamoDB update (retry on version conflict)
7. Exponential backoff: 100ms → 200ms → 400ms (max 3 retries)

Returns: newly computed segment hash

---

## 9. Async Commit Verification

**File**: `async_commit_service.py` — `AsyncCommitService`

Post-rollback S3 consistency verification:

```python
verify_commit_with_retry(execution_id, s3_bucket, s3_key) -> CommitStatus
```

- Redis check: `commit:{execution_id}` status
- S3 existence check: relies on S3 Strong Consistency (2020+)
- Retry strategy: 3 attempts, 0.1s → 0.4s with ±10% jitter (Thundering Herd mitigation)
- Returns:
  ```python
  @dataclass
  class CommitStatus:
      is_committed: bool
      s3_available: bool
      redis_status: Optional[str]
      retry_count: int
      total_wait_ms: float
  ```

---

## 10. save_state_delta — Full Flow

**File**: `state_versioning_service.py` — `save_state_delta()`

The primary state persistence path during segment execution:

```python
save_state_delta(
    delta: Dict,           # changed fields only
    workflow_id: str,
    execution_id: str,
    owner_id: str,
    segment_id: int,
    previous_manifest_id: Optional[str],
    dirty_keys: Optional[Set[str]],
    full_state: Optional[Dict]
) -> Dict[str, Any]
```

**Step 1**: Content block creation
- Each field → NDJSON format
- Gzip compression (mtime=0 for deterministic hash)
- Hash computed from RAW data [v3.32 BIX-4]
- S3 key: `merkle-blocks/{workflow_id}/{hash[:2]}/{hash}.json`

**Step 2**: Parallel S3 upload
- Workers = memory / 256, capped 4–32
- Tagging: `status=temp` (2PC Phase 1)

**Step 3**: DynamoDB TransactWriteItems (2PC Phase 2)
- Block ref count increments (batched, 99-item chunks)
- Manifest Put with `attribute_not_exists(manifest_id)`
- Manifest ID: `manifest-{execution_id}-{segment_id}-{timestamp}-{uuid[:8]}` [v3.32 BIX-2]
- Manifest hash: SHA-256 of block list (`sort_keys=True` required [v3.33 FIX-A])
- Incremental metadata: Per-temperature-tier hashes (hot/warm/cold/control)

**Step 4**: Pointer advancement (WorkflowsTableV3)
- Monotonic segment guard: only advance if segment_id ≥ current
- Retryable (3 attempts with exponential backoff)
- ConditionalCheckFailedException: non-retryable (expected during parallel fan-out)

**Step 5**: S3 tag confirmation (2PC Phase 3)
- `status=temp` → `status=ready` (parallel update)

**Returns**:
```python
{
    'manifest_id': str,
    'block_ids': List[str],
    'committed': bool,
    's3_paths': List[str]
}
```

---

## 11. Key Design Principles

| Principle | Implementation |
|-----------|----------------|
| **Content-Addressable** | block_id = SHA-256(content) → identical content = identical key |
| **Append-Only** | Manifests are never modified, only new versions created |
| **Tamper-Evident** | Merkle Root changes if any block, config, or parent is altered |
| **Zero Trust** | Every segment_config is hash-verified before execution (Kernel Panic on mismatch) |
| **Delta Storage** | Only changed fields are stored as new blocks; unchanged blocks are reused |
| **2-Phase Commit** | S3 blocks: `temp` → `committed` tag; GC cleans orphaned `temp` blocks |
| **Atomic Reference Counting** | DynamoDB ADD operation prevents dangling pointers during concurrent GC |
| **TOCTOU Defense** | upload_nonce + two-condition delete guard in GC |
| **Safe Chain Freeze** | Evidence blocks preserved on security violations (90-day hold) |
| **Incremental Hashing** | SubBlockHashRegistry only rehashes dirty temperature-tier blocks |

---

## 12. Version History (Critical Fixes)

| Version | Fix | Impact |
|---------|-----|--------|
| v3.32 BIX-2 | UUID suffix on manifest_id | Prevents 1-second collision window |
| v3.32 BIX-4 | Block hash from raw data | Deterministic cross-platform hashing |
| v3.33 FIX-A | sort_keys=True for manifest hash | Fixes hash mismatch on DynamoDB reads |
| v3.33 FIX-B | parent_hash persisted | Merkle chain continuity |
| v3.33 FIX-C | parent_hash in manifest_hash | DAG tamper-evidence restored |
| v3.33 FIX-D | parent_hash in S3 envelope | DAG reconstruction without DynamoDB |
| v3.34 | 'null' → None normalization | Legacy manifest compatibility |
| v3.34 | Decimal → int type safety | DynamoDB Decimal handling |
| v3.34 | Fail-fast on unsupported types | Prevent silent hash divergence |
| v3.35 | Rollback budget (5/execution) | Prevent GC flooding from infinite SOFT_ROLLBACK |
