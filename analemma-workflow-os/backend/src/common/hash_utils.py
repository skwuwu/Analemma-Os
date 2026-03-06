# -*- coding: utf-8 -*-
"""
Unified hash computation utilities for Analemma OS.

All hash operations across the codebase should use these functions
to ensure consistent serialization and algorithm selection.

Usage:
    from src.common.hash_utils import content_hash, content_hash_md5, quick_id

    h = content_hash({"key": "value"})           # SHA-256, full hex
    h = content_hash(data, truncate=16)           # SHA-256, first 16 chars
    h = content_hash_md5(data, truncate=16)       # MD5, first 16 chars
    h = quick_id(data)                            # SHA-256, first 12 chars (short ID)

Incremental Hashing (Phase 3):
    from src.common.hash_utils import SubBlockHashRegistry

    registry = SubBlockHashRegistry()
    root, block_hashes = registry.compute_incremental_root(state, dirty_keys)
"""

import hashlib
import json
import logging
from typing import Any, Dict, FrozenSet, Literal, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def _canonical_bytes(data: Any) -> bytes:
    """Convert data to canonical JSON bytes for deterministic hashing.

    Matches StateVersioningService.get_canonical_json() contract:
    - sort_keys=True for deterministic key order
    - separators=(',', ':') for no whitespace
    - ensure_ascii=False for UTF-8 preservation

    Safety:
    - Decimal → str (preserves precision; float conversion causes hash drift)
    - __dict__ → filtered (private keys excluded, to_dict() preferred)
    - Circular references → caught and replaced with safe repr fallback
    """
    from datetime import datetime, date
    from decimal import Decimal

    def _default(obj: Any) -> Any:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            # str preserves exact precision — float(Decimal("0.1") + Decimal("0.2"))
            # yields 0.30000000000000004, causing false Merkle violations.
            return str(obj)
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return sorted(str(item) for item in obj)
        # Explicit serialization contract takes precedence
        if hasattr(obj, 'to_dict') and callable(obj.to_dict):
            return obj.to_dict()
        # __dict__ fallback — filter private/dunder keys to prevent
        # leaking internal state (API keys, credentials, etc.)
        if hasattr(obj, '__dict__'):
            return {
                k: v for k, v in obj.__dict__.items()
                if not k.startswith('_')
            }
        # [v3.34] Fail-fast instead of silent hash divergence.
        # str(obj) can produce non-deterministic output (e.g., memory addresses
        # in default __repr__) causing identical state to hash differently
        # across Lambda invocations.  Raising TypeError forces callers to add
        # explicit serialization (to_dict() or type registration) rather than
        # silently producing wrong Merkle hashes.
        raise TypeError(
            f"Object of type {type(obj).__qualname__} is not canonical-serializable. "
            f"Add a to_dict() method or convert to a supported type before hashing."
        )

    try:
        return json.dumps(
            data,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=False,
            default=_default,
        ).encode('utf-8')
    except (ValueError, TypeError, RecursionError) as exc:
        # [v3.34] TypeError added: unsupported type in _default handler.
        # Circular reference, excessive nesting, or unsupported type — fall back
        # to repr().  repr() is deterministic for the same object state, preserving
        # hash stability without crashing the kernel.
        logger.warning(
            "[hash_utils] Canonical serialization failed (%s: %s) — "
            "using repr() fallback. Hash may be less stable.",
            type(exc).__name__, exc,
        )
        return repr(data).encode('utf-8')


def canonical_json(data: Any) -> str:
    """Canonical JSON string for deterministic, type-safe serialization.

    Same contract as ``_canonical_bytes`` but returns ``str`` instead of bytes.
    Use for tool results, bridge payloads, and any cross-boundary serialization
    where deterministic output is required.

    Unlike ``json.dumps(default=str)`` which silently stringifies unknown types,
    this function uses explicit type-aware conversion via ``_canonical_bytes``.
    """
    return _canonical_bytes(data).decode('utf-8')


def content_hash(data: Any, *, truncate: int = 0) -> str:
    """Compute SHA-256 hash of *data* using canonical JSON serialization.

    Args:
        data: Any JSON-serializable object.
        truncate: If > 0, return only the first *truncate* hex characters.

    Returns:
        Hex-encoded SHA-256 digest (64 chars) or truncated prefix.
    """
    digest = hashlib.sha256(_canonical_bytes(data)).hexdigest()
    return digest[:truncate] if truncate else digest


def content_hash_md5(data: Any, *, truncate: int = 0) -> str:
    """Compute MD5 hash of *data* using canonical JSON serialization.

    Prefer ``content_hash`` (SHA-256) for security-sensitive contexts.
    MD5 is acceptable for cache keys and non-security identifiers.

    Args:
        data: Any JSON-serializable object.
        truncate: If > 0, return only the first *truncate* hex characters.

    Returns:
        Hex-encoded MD5 digest (32 chars) or truncated prefix.
    """
    digest = hashlib.md5(_canonical_bytes(data)).hexdigest()
    return digest[:truncate] if truncate else digest


def quick_id(data: Any) -> str:
    """Short content-based identifier (SHA-256, first 12 chars).

    Useful for log messages and human-readable references.
    """
    return content_hash(data, truncate=12)


def raw_sha256(data: bytes) -> str:
    """SHA-256 of raw bytes (no JSON serialization)."""
    return hashlib.sha256(data).hexdigest()


def raw_md5(data: bytes) -> str:
    """MD5 of raw bytes (no JSON serialization)."""
    return hashlib.md5(data).hexdigest()


# ── Streaming Serialization (Phase 3b) ──────────────────────────────────────

def streaming_content_hash(data: Dict[str, Any]) -> str:
    """Streaming SHA-256 — feeds sorted keys directly into hashlib.update().

    Avoids allocating the full canonical JSON string in memory.
    hashlib releases the GIL during update(), enabling effective
    multi-threaded sub-block hashing on Lambda's multi-core resources.

    Memory: O(1) per key-value pair (vs O(N) for json.dumps of entire dict)
    CPU: Same total work, but no intermediate string concatenation overhead

    Note:
        This is NOT a drop-in replacement for ``content_hash()``.
        The wire format differs (key:value, delimiters vs canonical JSON),
        so digests are intentionally incompatible. Used exclusively by
        ``SubBlockHashRegistry`` for incremental merkle roots.
    """
    hasher = hashlib.sha256()
    for key in sorted(data.keys()):
        hasher.update(key.encode('utf-8'))
        hasher.update(b':')
        value_bytes = _canonical_bytes(data[key])
        hasher.update(value_bytes)
        hasher.update(b',')
    return hasher.hexdigest()


# ── Temperature-Aligned Sub-Block Definitions (Phase 3a) ────────────────────

# Aligned with BatchedDehydrator (batched_dehydrator.py) FieldTemperature.
# Changes here MUST be mirrored in SmartStateBag._dirty_blocks tracking.

HOT_FIELDS: FrozenSet[str] = frozenset({
    'llm_response', 'llm_raw_output', 'current_state', 'token_usage',
    'thought_signature', 'callback_result', 'react_result',
    'total_tokens', 'total_input_tokens', 'total_output_tokens',
    'usage',
})

WARM_FIELDS: FrozenSet[str] = frozenset({
    'step_history', 'messages', 'query_results',
    'parallel_results', 'branch_results', 'state_history',
})

COLD_FIELDS: FrozenSet[str] = frozenset({
    'workflow_config', 'partition_map', 'segment_manifest',
    'final_state',
})

# CONTROL_PLANE_FIELDS imported from state_hydrator at runtime to avoid
# circular import. Kept as a string constant for block classification.
_BLOCK_NAMES = ("hot", "warm", "cold", "control", "unclassified")

# Mapping for O(1) field → block lookup (built lazily)
_FIELD_TO_BLOCK: Optional[Dict[str, str]] = None


def _get_field_to_block_map() -> Dict[str, str]:
    """Lazy-build the field → block classification map."""
    global _FIELD_TO_BLOCK
    if _FIELD_TO_BLOCK is not None:
        return _FIELD_TO_BLOCK

    mapping: Dict[str, str] = {}
    for f in HOT_FIELDS:
        mapping[f] = "hot"
    for f in WARM_FIELDS:
        mapping[f] = "warm"
    for f in COLD_FIELDS:
        mapping[f] = "cold"

    # Import CONTROL_PLANE_FIELDS here to avoid circular import
    try:
        from src.common.state_hydrator import CONTROL_PLANE_FIELDS
        for f in CONTROL_PLANE_FIELDS:
            if f not in mapping:  # Temperature classification takes precedence
                mapping[f] = "control"
    except ImportError:
        pass

    _FIELD_TO_BLOCK = mapping
    return mapping


def classify_field_block(field_name: str) -> Literal["hot", "warm", "cold", "control", "unclassified"]:
    """Classify a field name into its temperature block.

    Returns one of: "hot", "warm", "cold", "control", "unclassified".
    Unclassified fields are treated as WARM (default temperature).
    """
    return _get_field_to_block_map().get(field_name, "unclassified")


# ── Sub-Block Hash Registry (Phase 3a) ──────────────────────────────────────

class SubBlockHashRegistry:
    """Incremental merkle hashing via dirty-key tracking.

    Divides state into sub-blocks aligned with BatchedDehydrator temperature:
    - HOT block: llm_response, current_state, token_usage, etc.
    - WARM block: step_history, messages, query_results, etc.
    - COLD block: workflow_config, partition_map, segment_manifest, etc.
    - CONTROL block: CONTROL_PLANE_FIELDS (ownerId, workflowId, etc.)
    - UNCLASSIFIED: treated as WARM (default)

    Only re-hashes blocks containing dirty keys. Unchanged blocks
    reuse their previous block_hash for merkle root computation.

    Complexity: O(changed_data) instead of O(total_state).

    CPU pipelining analogy:
        Like a CPU instruction cache — unchanged pipeline stages
        (COLD block hash) are reused from the previous cycle,
        while only modified stages (HOT block) are recomputed.
    """

    def __init__(self) -> None:
        self._block_hashes: Dict[str, str] = {}

    def compute_incremental_root(
        self,
        full_state: Dict[str, Any],
        dirty_keys: Set[str],
    ) -> Tuple[str, Dict[str, str]]:
        """Compute merkle root using incremental sub-block hashing.

        Args:
            full_state: The complete state dictionary.
            dirty_keys: Set of field names that changed since last hash.

        Returns:
            Tuple of (merkle_root_hash, {block_name: block_hash}).
        """
        dirty_blocks = self._classify_dirty_blocks(dirty_keys)

        for block_name in dirty_blocks:
            # Cold block skip: immutable after workflow init
            if block_name == "cold" and "cold" in self._block_hashes:
                continue
            block_fields = self._extract_block_fields(full_state, block_name)
            if block_fields:
                self._block_hashes[block_name] = streaming_content_hash(block_fields)

        # Initialize any blocks not yet hashed (first run)
        for block_name in _BLOCK_NAMES:
            if block_name not in self._block_hashes:
                block_fields = self._extract_block_fields(full_state, block_name)
                if block_fields:
                    self._block_hashes[block_name] = streaming_content_hash(block_fields)

        # Merkle root from sorted block hashes
        combined = "|".join(
            f"{name}:{h}"
            for name, h in sorted(self._block_hashes.items())
        )
        root = raw_sha256(combined.encode('utf-8'))
        return root, dict(self._block_hashes)

    def compute_incremental_root_parallel(
        self,
        full_state: Dict[str, Any],
        dirty_keys: Set[str],
        max_workers: int = 4,
    ) -> Tuple[str, Dict[str, str]]:
        """Multi-threaded sub-block hashing.

        hashlib.sha256.update() releases the GIL, so threading is effective
        for CPU-bound hashing of independent sub-blocks.

        Lambda 2048MB ~ 1 vCPU; 2-4 threads optimal for I/O overlap.
        """
        from concurrent.futures import ThreadPoolExecutor

        dirty_blocks = self._classify_dirty_blocks(dirty_keys)

        # Determine which blocks need (re-)hashing
        blocks_to_hash: list = []
        for block_name in dirty_blocks:
            if block_name == "cold" and "cold" in self._block_hashes:
                continue
            blocks_to_hash.append(block_name)

        # First run: hash all blocks that have no previous hash
        for block_name in _BLOCK_NAMES:
            if block_name not in self._block_hashes and block_name not in blocks_to_hash:
                blocks_to_hash.append(block_name)

        def _hash_block(block_name: str) -> Tuple[str, Optional[str]]:
            block_fields = self._extract_block_fields(full_state, block_name)
            if block_fields:
                return block_name, streaming_content_hash(block_fields)
            return block_name, None

        if blocks_to_hash:
            n_workers = min(max_workers, len(blocks_to_hash))
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                for name, h in pool.map(_hash_block, blocks_to_hash):
                    if h is not None:
                        self._block_hashes[name] = h

        combined = "|".join(
            f"{name}:{h}"
            for name, h in sorted(self._block_hashes.items())
        )
        root = raw_sha256(combined.encode('utf-8'))
        return root, dict(self._block_hashes)

    # ── Internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _classify_dirty_blocks(dirty_keys: Set[str]) -> Set[str]:
        """Map dirty field names to the set of blocks that need re-hashing."""
        blocks: Set[str] = set()
        for key in dirty_keys:
            block = classify_field_block(key)
            # "unclassified" fields are hashed with the WARM block
            blocks.add("warm" if block == "unclassified" else block)
        return blocks

    @staticmethod
    def _extract_block_fields(
        full_state: Dict[str, Any],
        block_name: str,
    ) -> Dict[str, Any]:
        """Extract fields belonging to a specific block from the full state."""
        field_map = _get_field_to_block_map()

        if block_name == "unclassified":
            # Gather all fields not mapped to any known block
            return {
                k: v for k, v in full_state.items()
                if k not in field_map
            }

        return {
            k: v for k, v in full_state.items()
            if field_map.get(k) == block_name
            or (block_name == "warm" and k not in field_map)
        }
