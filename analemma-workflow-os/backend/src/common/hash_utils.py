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
"""

import hashlib
import json
from typing import Any


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
        return str(obj)

    try:
        return json.dumps(
            data,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=False,
            default=_default,
        ).encode('utf-8')
    except (ValueError, RecursionError):
        # Circular reference or excessive nesting — fall back to repr.
        # repr() is deterministic for the same object state, preserving
        # hash stability without crashing the kernel.
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
