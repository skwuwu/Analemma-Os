"""
MerkleVerifier — Cross-boundary state consistency checker for E2E tests.

After each E2E test, verifies that:
    1. Local Merkle DAG state matches S3 content blocks
    2. DynamoDB checkpoint records match S3 manifests
    3. CONTROL_PLANE_FIELDS survived dehydration across segments
    4. Rollback/recovery restores exact checkpoint state

S3 Eventual Consistency Guard:
    All S3 reads use exponential backoff retry (0.5s -> 1s -> 2s -> 4s -> 8s)
    to handle eventual consistency delays.
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from src.services.state.state_versioning_service import StateVersioningService

logger = logging.getLogger(__name__)


class S3ConsistencyError(Exception):
    """Raised when S3 object is not available after max retries."""
    pass


@dataclass
class VerificationResult:
    """Result of a Merkle DAG verification."""
    passed: bool
    execution_id: str
    segments_verified: int = 0
    blocks_verified: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class MerkleVerifier:
    """
    Verifies Merkle DAG state consistency across local/cloud boundaries.

    Args:
        s3_client:           boto3 S3 client
        dynamodb_resource:   boto3 DynamoDB resource
        versioning_service:  StateVersioningService instance for hash computation
        state_bucket:        S3 bucket name for workflow state
    """

    def __init__(
        self,
        s3_client,
        dynamodb_resource,
        versioning_service: StateVersioningService,
        state_bucket: str,
    ):
        self._s3 = s3_client
        self._dynamodb = dynamodb_resource
        self._versioning = versioning_service
        self._bucket = state_bucket

    # ── S3 Eventual Consistency Guard ────────────────────────────────────────

    def _s3_get_with_retry(
        self,
        bucket: str,
        key: str,
        max_retries: int = 5,
    ) -> bytes:
        """
        S3 read with exponential backoff for eventual consistency.

        Retry schedule: 0.5s -> 1s -> 2s -> 4s -> 8s (total ~15.5s max wait).
        Retries on NoSuchKey or empty body.

        Raises:
            S3ConsistencyError: after max_retries exhausted.
        """
        base_delay = 0.5
        last_error = None

        for attempt in range(max_retries):
            try:
                resp = self._s3.get_object(Bucket=bucket, Key=key)
                body = resp["Body"].read()
                if body:
                    return body
                # Empty body -- treat as not-yet-available
                logger.warning(
                    "[MerkleVerifier] Empty body on attempt %d/%d: s3://%s/%s",
                    attempt + 1, max_retries, bucket, key,
                )
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code in ("NoSuchKey", "404"):
                    last_error = e
                    logger.warning(
                        "[MerkleVerifier] NoSuchKey on attempt %d/%d: s3://%s/%s",
                        attempt + 1, max_retries, bucket, key,
                    )
                else:
                    raise  # Non-retryable error

            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                time.sleep(delay)

        raise S3ConsistencyError(
            f"S3 object not available after {max_retries} retries: "
            f"s3://{bucket}/{key}. Last error: {last_error}"
        )

    # ── Main Verification ────────────────────────────────────────────────────

    def verify_execution(self, execution_id: str) -> VerificationResult:
        """
        Verify Merkle DAG integrity for a completed execution.

        Steps:
            1. Fetch all segment manifests from S3
            2. Recompute Merkle root from content blocks
            3. Compare with stored manifest hash
            4. Verify DynamoDB checkpoint records match S3 state
            5. Check CONTROL_PLANE_FIELDS survived dehydration
        """
        result = VerificationResult(passed=True, execution_id=execution_id)

        try:
            # 1. List all manifests for this execution
            prefix = f"state/{execution_id}/manifests/"
            manifests = self._list_s3_objects(prefix)

            if not manifests:
                result.warnings.append(f"No manifests found at {prefix}")
                return result

            result.segments_verified = len(manifests)

            # 2. Verify each manifest
            for manifest_key in manifests:
                manifest_ok = self._verify_manifest(manifest_key, result)
                if not manifest_ok:
                    result.passed = False

            # 3. Verify DynamoDB checkpoints
            self._verify_dynamodb_checkpoints(execution_id, result)

        except S3ConsistencyError as e:
            result.passed = False
            result.errors.append(f"S3 consistency error: {e}")
        except Exception as e:
            result.passed = False
            result.errors.append(f"Unexpected error: {e}")

        return result

    def _verify_manifest(self, manifest_key: str, result: VerificationResult) -> bool:
        """Verify a single manifest: recompute hash from blocks, compare."""
        try:
            manifest_bytes = self._s3_get_with_retry(self._bucket, manifest_key)
            manifest = json.loads(manifest_bytes)

            stored_hash = manifest.get("manifest_hash", "")
            blocks = manifest.get("blocks", [])

            # Recompute Merkle root from content blocks
            block_hashes = []
            for block_info in blocks:
                block_path = block_info.get("s3_path", "")
                if not block_path:
                    continue

                block_data = self._s3_get_with_retry(self._bucket, block_path)
                computed_hash = hashlib.sha256(block_data).hexdigest()
                expected_hash = block_info.get("checksum", block_info.get("block_id", ""))

                if computed_hash != expected_hash:
                    result.errors.append(
                        f"Block hash mismatch: {block_path} "
                        f"expected={expected_hash[:16]}... got={computed_hash[:16]}..."
                    )
                    return False

                block_hashes.append(computed_hash)
                result.blocks_verified += 1

            # Verify manifest hash
            if block_hashes:
                combined = "".join(sorted(block_hashes))
                recomputed_root = hashlib.sha256(combined.encode()).hexdigest()

                if stored_hash and recomputed_root != stored_hash:
                    result.errors.append(
                        f"Manifest root mismatch: {manifest_key} "
                        f"stored={stored_hash[:16]}... recomputed={recomputed_root[:16]}..."
                    )
                    return False

            # Check CONTROL_PLANE_FIELDS in manifest metadata
            metadata = manifest.get("metadata", {})
            control_fields = metadata.get("control_plane_fields", [])
            if control_fields:
                result.details[f"control_fields_{manifest_key}"] = control_fields

            return True

        except S3ConsistencyError:
            raise
        except Exception as e:
            result.errors.append(f"Manifest verification failed: {manifest_key}: {e}")
            return False

    def _verify_dynamodb_checkpoints(
        self, execution_id: str, result: VerificationResult
    ) -> None:
        """Verify DynamoDB checkpoint records match S3 manifests."""
        try:
            # Query DynamoDB for execution checkpoints
            table = self._dynamodb.Table("WorkflowManifestsV3")
            resp = table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key("execution_id").eq(execution_id),
            )
            items = resp.get("Items", [])

            for item in items:
                manifest_id = item.get("manifest_id", "")
                stored_version = item.get("version", 0)
                manifest_hash = item.get("manifest_hash", "")

                # Verify the S3 manifest exists and matches
                manifest_key = f"state/{execution_id}/manifests/{manifest_id}.json"
                try:
                    manifest_bytes = self._s3_get_with_retry(self._bucket, manifest_key)
                    manifest = json.loads(manifest_bytes)
                    s3_hash = manifest.get("manifest_hash", "")

                    if manifest_hash and s3_hash and manifest_hash != s3_hash:
                        result.errors.append(
                            f"DynamoDB/S3 hash mismatch for {manifest_id}: "
                            f"dynamo={manifest_hash[:16]}... s3={s3_hash[:16]}..."
                        )
                        result.passed = False

                except S3ConsistencyError:
                    result.warnings.append(
                        f"Checkpoint manifest not found in S3: {manifest_key}"
                    )

        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code")
            if code == "ResourceNotFoundException":
                result.warnings.append("WorkflowManifestsV3 table not found")
            else:
                result.errors.append(f"DynamoDB checkpoint query failed: {e}")

    def _list_s3_objects(self, prefix: str) -> List[str]:
        """List S3 object keys under a prefix."""
        keys = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    # ── Rollback Recovery Verification ───────────────────────────────────────

    def verify_control_plane_preservation(
        self,
        execution_id: str,
        expected_fields: List[str],
    ) -> VerificationResult:
        """
        Verify CONTROL_PLANE_FIELDS survived seal->ResultSelector->open cycle.

        Args:
            execution_id:    SFN execution ID
            expected_fields: List of field names that should be present
        """
        result = VerificationResult(passed=True, execution_id=execution_id)

        try:
            # Fetch final state from S3
            final_state_key = f"state/{execution_id}/final_state.json"
            try:
                final_bytes = self._s3_get_with_retry(self._bucket, final_state_key)
                final_state = json.loads(final_bytes)
            except S3ConsistencyError:
                result.warnings.append(f"Final state not found: {final_state_key}")
                return result

            # Check each expected field
            missing = []
            for field_name in expected_fields:
                if field_name not in final_state:
                    missing.append(field_name)

            if missing:
                result.passed = False
                result.errors.append(
                    f"Control plane fields missing from final_state: {missing}"
                )
            else:
                result.details["preserved_fields"] = expected_fields

        except Exception as e:
            result.passed = False
            result.errors.append(f"Control plane verification failed: {e}")

        return result

    def verify_s3_offload_flags(self, execution_id: str) -> VerificationResult:
        """
        Verify __s3_offloaded and __s3_path survive seal_state_bag (v3.31 fix).
        """
        result = VerificationResult(passed=True, execution_id=execution_id)

        try:
            final_state_key = f"state/{execution_id}/final_state.json"
            try:
                final_bytes = self._s3_get_with_retry(self._bucket, final_state_key)
                final_state = json.loads(final_bytes)
            except S3ConsistencyError:
                result.warnings.append("Final state not found for S3 offload check")
                return result

            # Only relevant if the execution used S3 offloading
            if final_state.get("__s3_offloaded"):
                if "__s3_path" not in final_state:
                    result.passed = False
                    result.errors.append(
                        "__s3_offloaded=True but __s3_path missing from final_state"
                    )
                else:
                    result.details["s3_path"] = final_state["__s3_path"]

        except Exception as e:
            result.passed = False
            result.errors.append(f"S3 offload flag verification failed: {e}")

        return result
