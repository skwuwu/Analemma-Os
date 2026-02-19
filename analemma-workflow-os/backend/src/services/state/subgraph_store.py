"""
Subgraph Store Service

서브그래프 중복 제거 저장소 (Content-Addressable Storage)
- Lazy Loading: 필요할 때만 서브그래프 로딩
- Deduplication: 동일한 서브그래프는 한 번만 저장
- Hash Verification: 무결성 검증
"""

import os
import json
import hashlib
import logging
from typing import Dict, Any, Optional
from datetime import datetime
import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class SubgraphNotFoundError(Exception):
    """서브그래프를 찾을 수 없을 때 발생"""
    pass


class SubgraphCorruptionError(Exception):
    """서브그래프 해시 불일치 (무결성 오류)"""
    pass


class SubgraphStore:
    """
    서브그래프 중복 제거 저장소
    
    특징:
    - Content-Addressable Storage: 내용 기반 해시로 주소 지정
    - S3: 실제 서브그래프 데이터 저장
    - DynamoDB: 메타데이터 및 빠른 조회
    - Deduplication: 동일 내용은 한 번만 저장
    """
    
    def __init__(
        self,
        s3_client=None,
        dynamodb_client=None,
        bucket: Optional[str] = None,
        table_name: Optional[str] = None
    ):
        """
        Args:
            s3_client: boto3 S3 client (None이면 자동 생성)
            dynamodb_client: boto3 DynamoDB client (None이면 자동 생성)
            bucket: S3 버킷명 (None이면 환경 변수에서 읽기)
            table_name: DynamoDB 테이블명 (None이면 환경 변수에서 읽기)
        """
        self.s3 = s3_client or boto3.client('s3')
        self.dynamodb = dynamodb_client or boto3.client('dynamodb')
        
        self.bucket = bucket or os.environ.get(
            'SUBGRAPH_STORE_BUCKET',
            os.environ.get('WORKFLOW_STATE_BUCKET')
        )
        
        self.table_name = table_name or os.environ.get(
            'SUBGRAPH_METADATA_TABLE',
            'WorkflowSubgraphMetadata'
        )
        
        if not self.bucket:
            raise ValueError("S3 bucket not specified. Set SUBGRAPH_STORE_BUCKET env var.")
        
        logger.info(
            f"[SUBGRAPH_STORE] Initialized: bucket={self.bucket}, "
            f"table={self.table_name}"
        )
    
    def save_subgraph(self, subgraph: Dict[str, Any]) -> str:
        """
        서브그래프를 저장하고 해시 포인터 반환
        
        Args:
            subgraph: 서브그래프 정의 (nodes, edges 포함)
        
        Returns:
            subgraph_ref: "sha256:abc123..." 형식의 해시 포인터
        
        Raises:
            ValueError: 서브그래프 구조가 잘못된 경우
        """
        # 1. 검증
        if "nodes" not in subgraph:
            raise ValueError("Subgraph must contain 'nodes' field")
        
        # 2. 정규화 (deterministic serialization)
        normalized = self._normalize(subgraph)
        
        # 3. 해시 계산
        content_bytes = json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode('utf-8')
        hash_value = hashlib.sha256(content_bytes).hexdigest()
        subgraph_ref = f"sha256:{hash_value}"
        
        # 4. 중복 체크 (이미 존재하면 바로 반환)
        if self._exists(subgraph_ref):
            logger.info(
                f"[SUBGRAPH_STORE] Deduplication: {subgraph_ref[:20]}... "
                f"already exists ({len(content_bytes)} bytes)"
            )
            return subgraph_ref
        
        # 5. S3 저장
        s3_key = f"subgraphs/{hash_value}.json"
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=content_bytes,
                ContentType="application/json",
                Metadata={
                    'node_count': str(len(normalized.get("nodes", []))),
                    'edge_count': str(len(normalized.get("edges", []))),
                    'created_at': datetime.utcnow().isoformat()
                }
            )
            logger.debug(f"[SUBGRAPH_STORE] S3 upload: s3://{self.bucket}/{s3_key}")
        except ClientError as e:
            logger.error(f"[SUBGRAPH_STORE] S3 upload failed: {e}")
            raise
        
        # 6. DynamoDB 메타데이터 저장
        try:
            self.dynamodb.put_item(
                TableName=self.table_name,
                Item={
                    'subgraph_ref': {'S': subgraph_ref},
                    's3_key': {'S': s3_key},
                    's3_bucket': {'S': self.bucket},
                    'node_count': {'N': str(len(normalized.get("nodes", [])))},
                    'edge_count': {'N': str(len(normalized.get("edges", [])))},
                    'size_bytes': {'N': str(len(content_bytes))},
                    'created_at': {'S': datetime.utcnow().isoformat()},
                    'hash_algorithm': {'S': 'sha256'}
                }
            )
            logger.debug(f"[SUBGRAPH_STORE] DynamoDB metadata saved: {subgraph_ref[:20]}...")
        except ClientError as e:
            logger.error(f"[SUBGRAPH_STORE] DynamoDB save failed: {e}")
            # S3는 저장됐으므로 계속 진행 (메타데이터는 선택적)
        
        logger.info(
            f"[SUBGRAPH_STORE] Saved {subgraph_ref[:20]}... "
            f"({len(content_bytes)} bytes, {len(normalized.get('nodes', []))} nodes)"
        )
        
        return subgraph_ref
    
    def load_subgraph(self, subgraph_ref: str) -> Dict[str, Any]:
        """
        해시 포인터로 서브그래프 로딩
        
        Args:
            subgraph_ref: "sha256:abc123..." 형식의 해시 포인터
        
        Returns:
            서브그래프 Dict (nodes, edges 포함)
        
        Raises:
            SubgraphNotFoundError: 서브그래프를 찾을 수 없는 경우
            SubgraphCorruptionError: 해시 불일치 (무결성 오류)
        """
        # 1. 해시 추출
        if not subgraph_ref.startswith("sha256:"):
            raise ValueError(f"Invalid subgraph_ref format: {subgraph_ref}")
        
        expected_hash = subgraph_ref.split(":")[-1]
        
        # 2. DynamoDB에서 메타데이터 조회 (S3 키 얻기)
        s3_key = None
        try:
            response = self.dynamodb.get_item(
                TableName=self.table_name,
                Key={'subgraph_ref': {'S': subgraph_ref}}
            )
            
            if 'Item' in response:
                s3_key = response['Item']['s3_key']['S']
                logger.debug(
                    f"[SUBGRAPH_STORE] Metadata found: {subgraph_ref[:20]}... → {s3_key}"
                )
        except ClientError as e:
            logger.warning(f"[SUBGRAPH_STORE] DynamoDB query failed: {e}")
        
        # 3. S3 키 결정 (메타데이터 없으면 해시로 직접 생성)
        if not s3_key:
            s3_key = f"subgraphs/{expected_hash}.json"
            logger.debug(
                f"[SUBGRAPH_STORE] No metadata, using direct key: {s3_key}"
            )
        
        # 4. S3에서 로딩
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=s3_key)
            content = obj['Body'].read()
            
            logger.debug(
                f"[SUBGRAPH_STORE] S3 download: s3://{self.bucket}/{s3_key} "
                f"({len(content)} bytes)"
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                raise SubgraphNotFoundError(
                    f"Subgraph {subgraph_ref} not found in S3: {s3_key}"
                )
            raise
        
        # 5. 해시 검증 (무결성 체크)
        actual_hash = hashlib.sha256(content).hexdigest()
        
        if actual_hash != expected_hash:
            logger.error(
                f"[SUBGRAPH_CORRUPTION] Hash mismatch: "
                f"expected={expected_hash}, actual={actual_hash}"
            )
            raise SubgraphCorruptionError(
                f"Subgraph hash mismatch for {subgraph_ref}. "
                f"Expected: {expected_hash}, Got: {actual_hash}. "
                f"Data may be corrupted."
            )
        
        # 6. JSON 파싱
        try:
            subgraph = json.loads(content.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise SubgraphCorruptionError(
                f"Failed to parse subgraph {subgraph_ref}: {e}"
            )
        
        logger.info(
            f"[SUBGRAPH_STORE] Loaded {subgraph_ref[:20]}... "
            f"({len(subgraph.get('nodes', []))} nodes)"
        )
        
        return subgraph
    
    def _normalize(self, subgraph: Dict[str, Any]) -> Dict[str, Any]:
        """
        서브그래프 정규화 (결정적 해시를 위해)
        
        - 노드 ID 기준 정렬
        - 엣지 (source, target) 기준 정렬
        - UI 전용 필드 제거 (position 등)
        - 빈 필드 제거
        
        Args:
            subgraph: 원본 서브그래프
        
        Returns:
            정규화된 서브그래프
        """
        normalized = {
            "nodes": sorted(
                [self._normalize_node(n) for n in subgraph.get("nodes", [])],
                key=lambda x: x["id"]
            ),
            "edges": sorted(
                [self._normalize_edge(e) for e in subgraph.get("edges", [])],
                key=lambda x: (x.get("source", ""), x.get("target", ""))
            )
        }
        
        # 메타데이터 포함 (선택적)
        if "metadata" in subgraph and subgraph["metadata"]:
            normalized["metadata"] = subgraph["metadata"]
        
        return normalized
    
    def _normalize_node(self, node: Dict[str, Any]) -> Dict[str, Any]:
        """노드에서 non-deterministic 필드 제거"""
        normalized = {
            "id": node["id"],
            "type": node["type"]
        }
        
        # 선택적 필드 (값이 있을 때만 포함)
        for field in ["label", "action", "hitp", "config", "branches", "resource_policy", 
                      "subgraph_ref", "subgraph_inline"]:
            if field in node and node[field] is not None:
                normalized[field] = node[field]
        
        # ❌ 제거: position (UI 전용)
        # ❌ 제거: selected, dragging 등 UI 상태
        
        return normalized
    
    def _normalize_edge(self, edge: Dict[str, Any]) -> Dict[str, Any]:
        """엣지 정규화"""
        normalized = {
            "source": edge.get("source"),
            "target": edge.get("target"),
            "type": edge.get("type", "edge")
        }
        
        # id 제거 (재생성 가능)
        # condition, router_func, mapping 제거 (라우팅 주권 일원화로 제거됨)
        
        return normalized
    
    def _exists(self, subgraph_ref: str) -> bool:
        """
        서브그래프 존재 여부 확인
        
        Args:
            subgraph_ref: "sha256:abc123..." 형식
        
        Returns:
            True if exists, False otherwise
        """
        try:
            response = self.dynamodb.get_item(
                TableName=self.table_name,
                Key={'subgraph_ref': {'S': subgraph_ref}},
                ProjectionExpression='subgraph_ref'
            )
            return 'Item' in response
        except ClientError as e:
            logger.warning(f"[SUBGRAPH_STORE] Existence check failed: {e}")
            # DynamoDB 실패 시 S3 직접 확인
            hash_value = subgraph_ref.split(":")[-1]
            s3_key = f"subgraphs/{hash_value}.json"
            try:
                self.s3.head_object(Bucket=self.bucket, Key=s3_key)
                return True
            except ClientError:
                return False
    
    def delete_subgraph(self, subgraph_ref: str) -> bool:
        """
        서브그래프 삭제 (주의: 참조 카운팅 없이 삭제)
        
        Args:
            subgraph_ref: "sha256:abc123..." 형식
        
        Returns:
            True if deleted, False if not found
        """
        if not self._exists(subgraph_ref):
            return False
        
        hash_value = subgraph_ref.split(":")[-1]
        s3_key = f"subgraphs/{hash_value}.json"
        
        # S3 삭제
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=s3_key)
            logger.info(f"[SUBGRAPH_STORE] Deleted from S3: {s3_key}")
        except ClientError as e:
            logger.error(f"[SUBGRAPH_STORE] S3 delete failed: {e}")
        
        # DynamoDB 메타데이터 삭제
        try:
            self.dynamodb.delete_item(
                TableName=self.table_name,
                Key={'subgraph_ref': {'S': subgraph_ref}}
            )
            logger.info(f"[SUBGRAPH_STORE] Deleted metadata: {subgraph_ref[:20]}...")
        except ClientError as e:
            logger.error(f"[SUBGRAPH_STORE] DynamoDB delete failed: {e}")
        
        return True


# 팩토리 함수
def create_subgraph_store(
    bucket: Optional[str] = None,
    table_name: Optional[str] = None
) -> SubgraphStore:
    """
    SubgraphStore 인스턴스 생성 (팩토리)
    
    Args:
        bucket: S3 버킷명 (None이면 환경 변수)
        table_name: DynamoDB 테이블명 (None이면 환경 변수)
    
    Returns:
        SubgraphStore 인스턴스
    """
    return SubgraphStore(bucket=bucket, table_name=table_name)
