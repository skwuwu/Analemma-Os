# Analemma OS 종합 리팩토링 마스터플랜
## "Fat State → Merkle DAG + Lean Manifest" 전환

> **목표:** 현대적 분산 OS의 정석을 따르는 가버넌스 런타임 구축  
> **기간:** 8-12주 (Phase 0-7)  
> **예상 효과:** 페이로드 67% 절감, 데이터 중복 90% → 10%, 회귀 속도 즉시 전환

---

## 📊 현재 상태 진단

### 아키텍처 문제점
```
현재 (V2 - Fat State):
┌─────────────────────────────────────────────────────┐
│ Initialize Lambda                                   │
│ ├─ workflow_config: 200KB (전체 그래프)            │
│ ├─ partition_map: 50KB (전체 세그먼트)             │
│ └─ StateBag에 영구 저장 ❌                         │
└─────────────────────────────────────────────────────┘
              ↓ 모든 세그먼트로 전달
┌─────────────────────────────────────────────────────┐
│ Execute Segment 0                                   │
│ ├─ workflow_config: 200KB ← 불필요                 │
│ ├─ partition_map: 50KB ← 불필요                    │
│ ├─ current_state: 100KB                            │
│ └─ segment_config: 동적 생성 (느림)                │
└─────────────────────────────────────────────────────┘
              ↓ 100개 세그먼트 = 37MB 낭비

문제 1: 초기화 데이터와 런타임 상태의 혼재
문제 2: 세그먼트가 전체 워크플로우 구조를 알 수 있음 (보안 위반)
문제 3: 상태 변경 시 전체 복사 (데이터 중복 90%)
문제 4: 회귀(Rollback) 시 정확한 시점 재현 불가
문제 5: S3 256KB 제한 우회 불가능
```

### 목표 아키텍처
```
목표 (V3 - Merkle DAG + Lean Manifest):
┌─────────────────────────────────────────────────────┐
│ Initialize Lambda                                   │
│ ├─ workflow_config: 200KB → S3 (참조용)            │
│ │  └─ config_hash: sha256(...) → Manifest Root     │
│ ├─ partition_map: 로컬 변수 (폐기)                 │
│ ├─ segment_manifest: S3 저장                       │
│ │  └─ manifest_id: uuid → DynamoDB Pointer         │
│ └─ StateBag: manifest_id + hash만 저장 ✅          │
└─────────────────────────────────────────────────────┘
              ↓ 포인터만 전달 (100 bytes)
┌─────────────────────────────────────────────────────┐
│ Execute Segment 0                                   │
│ ├─ segment_config: ASL 직접 주입 (10KB)            │
│ │  └─ 또는 manifest[0] Lazy Load                   │
│ ├─ current_state: S3 Select (필요한 필드만)        │
│ └─ manifest_hash: 검증 후 실행 ✅                  │
└─────────────────────────────────────────────────────┘
              ↓ 100개 세그먼트 = 13MB (-65%)

해결 1: 초기화 데이터는 S3 참조, 런타임은 포인터만
해결 2: 세그먼트는 자신의 config만 알 수 있음 (최소 권한)
해결 3: Merkle DAG로 델타만 저장 (10% 중복)
해결 4: Pointer Manifest로 즉시 회귀 가능
해결 5: S3 Select로 필드별 선택적 로드
```

---

## 🎯 Phase 0: 사전 준비 (Week 1, P0 - Critical)

> **핵심:** 기존 시스템을 깨지 않고 새로운 로딩 메커니즘 먼저 배포  
> **순서가 중요:** Fallback 로직 배포 → 데이터 제거 → ASL 최적화

### 0.1 Fallback 로딩 메커니즘 구현

**파일:** [segment_runner_service.py](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\services\\execution\\segment_runner_service.py)

```python
def _load_segment_config_from_manifest(
    self, 
    manifest_s3_path: str, 
    segment_index: int,
    cache_ttl: int = 300  # 5분 캐시
) -> dict:
    """
    S3에서 segment_manifest를 로드하고 특정 segment_config를 추출
    
    새 기능:
    - Size-based routing: 작은 manifest는 전체 로드, 큰 것은 S3 Select
    - In-memory cache: 같은 manifest 재사용
    - Checksum verification: manifest_hash 검증
    """
    cache_key = f"{manifest_s3_path}:{segment_index}"
    
    # 1. 캐시 확인 (Lambda warm start 시 재사용)
    if hasattr(self, '_manifest_cache'):
        cached = self._manifest_cache.get(cache_key)
        if cached and time.time() - cached['timestamp'] < cache_ttl:
            logger.info(f"Cache hit for segment_config: {cache_key}")
            return cached['config']
    
    # 2. S3 경로 파싱
    bucket_name = manifest_s3_path.replace("s3://", "").split("/")[0]
    key_name = "/".join(manifest_s3_path.replace("s3://", "").split("/")[1:])
    
    # 3. Size-based routing (피드백 반영)
    s3 = boto3.client('s3')
    head_obj = s3.head_object(Bucket=bucket_name, Key=key_name)
    object_size = head_obj['ContentLength']
    
    if object_size < 10 * 1024:  # 10KB 미만
        # 전체 로드가 더 효율적
        logger.info(f"Small manifest ({object_size}B), using GetObject")
        obj = s3.get_object(Bucket=bucket_name, Key=key_name)
        content = obj['Body'].read().decode('utf-8')
        manifest = self._safe_json_load(content)
    else:
        # S3 Select로 특정 세그먼트만 추출
        logger.info(f"Large manifest ({object_size}B), using S3 Select")
        response = s3.select_object_content(
            Bucket=bucket_name,
            Key=key_name,
            ExpressionType='SQL',
            Expression=f"SELECT * FROM s3object[*][{segment_index}]",
            InputSerialization={'JSON': {'Type': 'DOCUMENT'}},
            OutputSerialization={'JSON': {}}
        )
        # S3 Select 응답 파싱
        result = []
        for event in response['Payload']:
            if 'Records' in event:
                result.append(event['Records']['Payload'].decode('utf-8'))
        segment_entry = json.loads(''.join(result))
    
    # 4. segment_config 추출
    if object_size < 10 * 1024:
        if not isinstance(manifest, list):
            raise ValueError(f"Invalid manifest: expected list, got {type(manifest)}")
        if not (0 <= segment_index < len(manifest)):
            raise ValueError(f"Index {segment_index} out of range (manifest has {len(manifest)} segments)")
        segment_entry = manifest[segment_index]
    
    # 5. Nested 구조 처리
    if 'segment_config' in segment_entry:
        segment_config = segment_entry['segment_config']
    else:
        segment_config = segment_entry
    
    # 6. 캐시 저장
    if not hasattr(self, '_manifest_cache'):
        self._manifest_cache = {}
    self._manifest_cache[cache_key] = {
        'config': segment_config,
        'timestamp': time.time()
    }
    
    logger.info(f"Loaded segment_config: type={segment_config.get('type')}, "
               f"nodes={len(segment_config.get('nodes', []))}")
    
    return segment_config
```

**배포 우선순위:** ⚠️ **CRITICAL - Phase 0의 최우선 작업**  
이 메서드를 먼저 배포하면 기존 코드와 호환되면서 새 경로도 지원

**성능 최적화 (피드백 반영):**
- **Lambda 캐싱이 실제 주 경로**: ASL Direct Injection은 256KB 제약으로 전체 트래픽의 20% 미만만 처리
- **Warm Start 최적화**: `_manifest_cache`를 Lambda 인스턴스 레벨에서 유지하여 재사용
- **예상 캐시 히트율**: 80% 이상 (같은 워크플로우의 연속 세그먼트 실행 시)

```python
# Lambda 초기화 시 캐시 크기 제한 설정
if not hasattr(self, '_manifest_cache'):
    self._manifest_cache = {}  # LRU로 교체 권장 (최대 100개 항목)
```

---

### 0.2 Hybrid Loading 로직 (ASL 256KB 대응)

**파일:** [segment_runner_service.py](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\services\\execution\\segment_runner_service.py) Line 2856-2919

```python
# ✅ Hybrid Loading: ASL 직접 주입 또는 Fallback
segment_config = event.get('segment_config')  # ASL에서 주입 (작은 manifest)

if not segment_config:
    # Fallback: Lambda가 S3에서 직접 로드 (큰 manifest)
    manifest_s3_path = event.get('segment_manifest_s3_path')
    segment_index = event.get('segment_index', segment_id)
    
    if manifest_s3_path:
        segment_config = self._load_segment_config_from_manifest(
            manifest_s3_path,
            segment_index
        )
    else:
        # Legacy fallback: workflow_config + partition_map (호환성)
        workflow_config = _safe_get_from_bag(event, 'workflow_config')
        partition_map = _safe_get_from_bag(event, 'partition_map')
        
        if workflow_config or partition_map:
            logger.warning("[Legacy Mode] Using workflow_config/partition_map fallback")
            segment_config = self._resolve_segment_config(
                workflow_config, partition_map, segment_id
            )
        else:
            raise ValueError("No segment_config source available")

# workflow_config와 partition_map은 더 이상 직접 사용 안 함
# (statebag에서 제거 예정)
```

**배포 효과:** 3단계 Fallback으로 점진적 마이그레이션 가능

---

## 🎯 Phase 1: Merkle DAG 인프라 구축 (Week 2-3, P0)

### 1.1 DynamoDB 테이블 생성

**테이블:** `WorkflowManifestsV3`

```python
{
    "TableName": "WorkflowManifestsV3",
    "KeySchema": [
        {"AttributeName": "manifest_id", "KeyType": "HASH"},
        {"AttributeName": "version", "KeyType": "RANGE"}
    ],
    "AttributeDefinitions": [
        {"AttributeName": "manifest_id", "AttributeType": "S"},
        {"AttributeName": "version", "AttributeType": "N"},
        {"AttributeName": "workflow_id", "AttributeType": "S"},
        {"AttributeName": "parent_hash", "AttributeType": "S"}
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "WorkflowIndex",
            "KeySchema": [
                {"AttributeName": "workflow_id", "KeyType": "HASH"},
                {"AttributeName": "version", "KeyType": "RANGE"}
            ],
            "Projection": {"ProjectionType": "ALL"}
        },
        {
            "IndexName": "ParentHashIndex",
            "KeySchema": [
                {"AttributeName": "parent_hash", "KeyType": "HASH"}
            ],
            "Projection": {"ProjectionType": "KEYS_ONLY"}
        }
    ],
    "StreamSpecification": {
        "StreamEnabled": true,
        "StreamViewType": "NEW_AND_OLD_IMAGES"
    }
}
```

**항목 구조:**
```python
{
    "manifest_id": "uuid",
    "version": 1,
    "workflow_id": "workflow_123",
    "parent_hash": "sha256(...)",  # 이전 버전의 해시 (Merkle 체인)
    "manifest_hash": "sha256(...)",  # 현재 매니페스트의 해시
    "config_hash": "sha256(...)",    # workflow_config의 해시 (불변 참조)
    "s3_pointers": {
        "manifest": "s3://bucket/manifests/uuid.json",
        "config": "s3://bucket/configs/workflow_123.json",  # 참조용
        "state_blocks": [
            "s3://bucket/states/block_abc.json",  # 델타 블록
            "s3://bucket/states/block_def.json"
        ]
    },
    "metadata": {
        "created_at": "2026-02-18T10:00:00Z",
        "segment_count": 10,
        "total_size": 150000,
        "compression": "gzip"
    },
    "ttl": 1708678800  # 30일 후 삭제 (GC용)
}
```

---

### 1.2 StateVersioningService 구현

**파일:** `backend/src/services/state/state_versioning_service.py` (NEW)

```python
import hashlib
import json
from typing import Dict, List, Optional
from dataclasses import dataclass
import boto3

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

class StateVersioningService:
    """
    Merkle DAG 기반 상태 버저닝 서비스
    
    핵심 기능:
    1. 상태 변경 시 델타만 저장 (Content-Addressable Storage)
    2. Merkle Root로 무결성 검증
    3. Pointer Manifest로 즉시 회귀 가능
    """
    
    def __init__(self, dynamodb_table: str, s3_bucket: str):
        self.dynamodb = boto3.resource('dynamodb')
        self.table = self.dynamodb.Table(dynamodb_table)
        self.s3 = boto3.client('s3')
        self.bucket = s3_bucket
    
    def create_manifest(
        self,
        workflow_id: str,
        workflow_config: dict,
        segment_manifest: List[dict],
        parent_manifest_id: Optional[str] = None
    ) -> ManifestPointer:
        """
        새 Pointer Manifest 생성
        
        Args:
            workflow_id: 워크플로우 ID
            workflow_config: 워크플로우 설정 (해시 계산용)
            segment_manifest: 세그먼트 목록
            parent_manifest_id: 이전 버전 ID (Merkle 체인)
        
        Returns:
            ManifestPointer: 생성된 매니페스트 포인터
        """
        import uuid
        
        manifest_id = str(uuid.uuid4())
        
        # 1. workflow_config 해시 계산 (불변 참조)
        config_hash = self._compute_hash(workflow_config)
        
        # 2. workflow_config를 S3에 저장 (참조용)
        config_s3_key = f"workflow-configs/{workflow_id}/{config_hash}.json"
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
        
        # 3. segment_manifest를 Content Blocks로 분할
        blocks = self._split_into_blocks(segment_manifest)
        
        # 3.5. Pre-computed Hash 생성 (Phase 7 검증 최적화용)
        segment_hashes = self._compute_segment_hashes(segment_manifest)
        
        # 4. 각 블록을 S3에 저장 (Content-Addressable)
        for block in blocks:
            if not self._block_exists(block.block_id):
                self.s3.put_object(
                    Bucket=self.bucket,
                    Key=block.s3_path.replace(f"s3://{self.bucket}/", ""),
                    Body=json.dumps({
                        'fields': block.fields,
                        'data': segment_manifest  # 실제로는 해당 필드만
                    }),
                    ContentType='application/json',
                    Metadata={'block_id': block.block_id}
                )
        
        # 5. Merkle Root 계산
        parent_hash = None
        if parent_manifest_id:
            parent = self.get_manifest(parent_manifest_id)
            parent_hash = parent.manifest_hash
        
        manifest_hash = self._compute_merkle_root(blocks, config_hash, parent_hash)
        
        # 6. DynamoDB에 포인터 저장
        version = self._get_next_version(workflow_id)
        
        self.table.put_item(Item={
            'manifest_id': manifest_id,
            'version': version,
            'workflow_id': workflow_id,
            'parent_hash': parent_hash,
            'manifest_hash': manifest_hash,
            'config_hash': config_hash,
            'segment_hashes': segment_hashes,  # ✅ Pre-computed Hash 저장
            's3_pointers': {
                'manifest': f"s3://{self.bucket}/manifests/{manifest_id}.json",
                'config': f"s3://{self.bucket}/{config_s3_key}",
                'state_blocks': [block.s3_path for block in blocks]
            },
            'metadata': {
                'created_at': datetime.utcnow().isoformat(),
                'segment_count': len(segment_manifest),
                'total_size': sum(block.size for block in blocks),
                'compression': 'none'
            },
            'ttl': int(time.time()) + 30 * 24 * 3600  # 30일 후 GC
        })
        
        return ManifestPointer(
            manifest_id=manifest_id,
            version=version,
            parent_hash=parent_hash,
            manifest_hash=manifest_hash,
            config_hash=config_hash,
            blocks=blocks,
            metadata={}
        )
    
    def verify_manifest_integrity(self, manifest_id: str) -> bool:
        """
        Merkle Root 검증
        
        Returns:
            bool: 무결성 검증 통과 여부
        """
        item = self.table.get_item(Key={'manifest_id': manifest_id})['Item']
        
        # 저장된 블록들로 Merkle Root 재계산
        blocks = self._load_blocks(item['s3_pointers']['state_blocks'])
        computed_hash = self._compute_merkle_root(
            blocks,
            item['config_hash'],
            item.get('parent_hash')
        )
        
        is_valid = computed_hash == item['manifest_hash']
        
        if not is_valid:
            logger.error(f"[Integrity Violation] Manifest {manifest_id} hash mismatch! "
                        f"Expected: {item['manifest_hash']}, "
                        f"Computed: {computed_hash}")
        
        return is_valid
    
    def _compute_hash(self, data: dict) -> str:
        """JSON 데이터의 SHA256 해시 계산"""
        json_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()
    
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
        segment_manifest를 Content Blocks로 분할
        
        전략: 각 세그먼트를 별도 블록으로
        """
        blocks = []
        for idx, segment in enumerate(manifest):
            block_data = json.dumps(segment, default=str)
            block_id = hashlib.sha256(block_data.encode()).hexdigest()
            
            blocks.append(ContentBlock(
                block_id=block_id,
                s3_path=f"s3://{self.bucket}/state-blocks/{block_id}.json",
                size=len(block_data.encode()),
                fields=[f"segment_{idx}"],
                checksum=block_id
            ))
        
        return blocks
    
    def _block_exists(self, block_id: str) -> bool:
        """블록이 S3에 이미 존재하는지 확인 (중복 제거)"""
        try:
            self.s3.head_object(
                Bucket=self.bucket,
                Key=f"state-blocks/{block_id}.json"
            )
            return True
        except:
            return False
    
    def _compute_segment_hashes(self, manifest: List[dict]) -> Dict[int, str]:
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
            segment_hashes[idx] = segment_hash
            
            logger.debug(f"Pre-computed hash for segment {idx}: {segment_hash[:8]}...")
        
        return segment_hashes
```

---

## 🎯 Phase 2: workflow_config/partition_map 제거 (Week 4, P0)

### 2.1 initialize_state_data.py 수정

**파일:** [initialize_state_data.py](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\common\\initialize_state_data.py) Line 421-423

**Before:**
```python
bag['workflow_config'] = workflow_config  # ❌
bag['partition_map'] = partition_map      # ❌
```

**After:**
```python
# StateVersioningService를 통해 Merkle Manifest 생성
versioning_service = StateVersioningService(
    dynamodb_table=os.environ['MANIFESTS_TABLE'],
    s3_bucket=bucket
)

manifest_pointer = versioning_service.create_manifest(
    workflow_id=workflow_id,
    workflow_config=workflow_config,  # 해시 계산 후 S3 저장
    segment_manifest=segment_manifest,
    parent_manifest_id=None  # 첫 실행
)

# StateBag에는 포인터만 저장
bag['manifest_id'] = manifest_pointer.manifest_id
bag['manifest_hash'] = manifest_pointer.manifest_hash
bag['config_hash'] = manifest_pointer.config_hash  # 검증용

# ❌ 제거
# bag['workflow_config'] = workflow_config
# bag['partition_map'] = partition_map

logger.info(f"Created Merkle Manifest: {manifest_pointer.manifest_id}, "
           f"hash={manifest_pointer.manifest_hash[:8]}..., "
           f"blocks={len(manifest_pointer.blocks)}")
```

**보안 강화 (피드백 반영):**
```python
# workflow_config 해시를 statebag에 저장하여
# 실행 중인 segment_config가 원본 설계도에서 유래했음을 보장
bag['config_hash'] = manifest_pointer.config_hash

# 각 세그먼트 실행 시 검증:
# if segment_config_hash != bag['config_hash']:
#     raise SecurityError("Segment config does not match original workflow!")
```

---

### 2.2 ASL 수정 - Threshold-based Loading

**파일:** [aws_step_functions_v3.json](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\aws_step_functions_v3.json)

```json
{
  "Comment": "Analemma OS V3 - Merkle DAG + Lean Manifest",
  "StartAt": "CheckManifestSize",
  
  "States": {
    "CheckManifestSize": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.manifest_size",
          "NumericLessThan": 256000,
          "Comment": "256KB 미만: ASL에서 직접 처리",
          "Next": "ExecuteWithDirectInjection"
        }
      ],
      "Default": "ExecuteWithS3Loading"
    },
    
    "ExecuteWithDirectInjection": {
      "Comment": "작은 manifest: ASL에서 segment_config 직접 주입",
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:function:ExecuteSegment",
      "Parameters": {
        "state_data.$": "$.state_data",
        "segment_index.$": "$.segment_index",
        
        "segment_config.$": "States.JsonToString($.segment_manifest[$.segment_index].segment_config)",
        
        "manifest_hash.$": "$.manifest_hash",
        "config_hash.$": "$.config_hash"
      },
      "Next": "CheckCompletion"
    },
    
    "ExecuteWithS3Loading": {
      "Comment": "큰 manifest: Lambda가 S3에서 직접 로드",
      "Type": "Task",
      "Resource": "arn:aws:lambda:...:function:ExecuteSegment",
      "Parameters": {
        "state_data.$": "$.state_data",
        "segment_index.$": "$.segment_index",
        
        "segment_manifest_s3_path.$": "$.segment_manifest_s3_path",
        "manifest_id.$": "$.manifest_id",
        
        "manifest_hash.$": "$.manifest_hash",
        "config_hash.$": "$.config_hash"
      },
      "Next": "CheckCompletion"
    }
  }
}
```

**피드백 반영:**
- 256KB 기준으로 Direct Injection vs S3 Loading 선택
- manifest_hash와 config_hash를 모든 세그먼트에 전달하여 무결성 검증

**⚠️ 실무 경고 (ASL의 함정):**
- ASL의 `States.ArrayGetItem`과 `States.JsonToString` 조합은 문법이 까다로움
- 동적 인덱스(`$.segment_index`) 사용 시 실수 빈번
- **실제 운영 예상**: Direct Injection은 전체의 20% 미만, 나머지 80%는 Lambda Fallback 처리
- **전략**: ASL은 "빠른 경로(Fast Path)"로만 사용, Lambda는 "안정 경로(Stable Path)"

```json
// ASL 인트린직 함수 사용 시 주의사항
// ❌ 작동 안 함: "segment_config.$": "$.segment_manifest[$.segment_index]"
// ✅ 올바른 방법: "segment_config.$": "States.ArrayGetItem($.segment_manifest, $.segment_index)"
```

---

## 🎯 Phase 3: SegmentFieldOptimizer 통합 (Week 5, P1)

### 3.1 Capability-based Filtering 추가 (피드백 반영)

**파일:** `backend/src/services/execution/segment_field_optimizer.py`

**Before (정적 필터링):**
```python
NODE_REQUIRED_FIELDS = {
    "llm_chat": ["node", "config"],
    "parallel_group": ["branches", "node"],
    # ...
}
```

**After (동적 필터링):**
```python
class SegmentFieldOptimizer:
    """
    세그먼트 페이로드 최적화
    
    새 기능:
    - Capability-based Filtering: 노드의 의도(Intent)에 따라 필드 동적 해제
    - Security Ring 통합: Ring 3 노드는 더 많은 필드 제한
    """
    
    ALWAYS_EXCLUDE_FIELDS = [
        'workflow_config',   # Phase 2에서 제거됨
        'partition_map',     # Phase 2에서 제거됨
        'debug_info',
        'internal_cache'
    ]
    
    # 기본 필수 필드
    BASE_REQUIRED_FIELDS = {
        "llm_chat": ["node", "config"],
        "parallel_group": ["branches", "node"],
        "aggregator": ["branch_results"],
        "trigger": ["event_config"],
        "code_interpreter": ["code", "node"],
        "http_request": ["request_config", "node"],
    }
    
    # Capability 기반 추가 필드 (자율형 에이전트용)
    CAPABILITY_FIELDS = {
        "tool_use": ["tools", "tool_schemas"],  # 도구 사용 시 필요
        "memory_access": ["memory_context"],     # 메모리 접근 시 필요
        "state_mutation": ["state_schema"],      # 상태 변경 시 필요
    }
    
    # Security Ring별 제약
    RING_RESTRICTIONS = {
        "ring_0": [],  # 제약 없음 (신뢰된 코드)
        "ring_1": ["internal_state"],
        "ring_2": ["internal_state", "credentials"],
        "ring_3": ["internal_state", "credentials", "workflow_metadata"]  # 최소 권한
    }
    
    def filter_event_payload(
        self,
        event: dict,
        segment_config: dict,
        security_ring: str = "ring_3"  # 기본값: 최소 권한
    ) -> dict:
        """
        동적 필드 필터링
        
        Args:
            event: 원본 이벤트
            segment_config: 세그먼트 설정
            security_ring: 보안 링 레벨
        
        Returns:
            dict: 최적화된 이벤트
        """
        nodes = segment_config.get('nodes', [])
        if not nodes:
            return event
        
        # 1. 노드 타입별 필수 필드
        required_fields = set()
        for node in nodes:
            node_type = node.get('type')
            base_fields = self.BASE_REQUIRED_FIELDS.get(node_type, [])
            required_fields.update(base_fields)
            
            # 2. Capability 기반 추가 필드
            node_capabilities = node.get('capabilities', [])
            for cap in node_capabilities:
                cap_fields = self.CAPABILITY_FIELDS.get(cap, [])
                required_fields.update(cap_fields)
        
        # 3. Security Ring 제약 적용
        restricted_fields = set(self.RING_RESTRICTIONS.get(security_ring, []))
        
        # 4. 필터링
        filtered = {}
        for key, value in event.items():
            # 항상 제외
            if key in self.ALWAYS_EXCLUDE_FIELDS:
                continue
            
            # Ring 제약으로 제외
            if key in restricted_fields:
                logger.info(f"Field '{key}' excluded by {security_ring} restriction")
                continue
            
            # 필수 필드 또는 메타데이터는 포함
            if key in required_fields or key.startswith('_'):
                filtered[key] = value
        
        # 5. 로깅
        original_size = len(json.dumps(event, default=str))
        filtered_size = len(json.dumps(filtered, default=str))
        reduction = (1 - filtered_size / original_size) * 100
        
        logger.info(f"Payload optimized: {original_size}B → {filtered_size}B "
                   f"(-{reduction:.1f}%), ring={security_ring}")
        
        return filtered
```

**사용 예시:**
```python
# segment_runner_service.py에서
optimizer = SegmentFieldOptimizer()

# 자율형 에이전트 (Manus): Ring 3 + tool_use capability
filtered_event = optimizer.filter_event_payload(
    event,
    segment_config,
    security_ring="ring_3"
)

# 신뢰된 시스템 노드: Ring 0
filtered_event = optimizer.filter_event_payload(
    event,
    segment_config,
    security_ring="ring_0"
)
```

---

## 🎯 Phase 4: S3 Select 최적화 (Week 6, P1)

### 4.1 StateHydrator 개선 - Size-based Routing

**파일:** [state_hydrator.py](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\services\\state\\state_hydrator.py)

```python
class StateHydrator:
    """
    상태 직렬화/역직렬화 with S3 Select 최적화
    
    새 기능:
    - CloudWatch 기반 동적 threshold 튜닝 (레이턴시 지터 대응)
    """
    
    # ⚠️ 초기값: 운영 중 CloudWatch Metric 기반으로 조정
    FIELD_SIZE_THRESHOLD = 10 * 1024  # 10KB (동적 조정 가능)
    S3_SELECT_OVERHEAD = 100  # ms (실제 측정값으로 교체 필요)
    GET_OBJECT_OVERHEAD = 50  # ms (실제 측정값으로 교체 필요)
    
    # CloudWatch 기반 동적 최적화
    _threshold_cache = None
    _threshold_last_update = 0
    THRESHOLD_UPDATE_INTERVAL = 3600  # 1시간마다 갱신
    
    def load_fields_selective(
        self,
        s3_path: str,
        field_names: List[str],
        auto_routing: bool = True
    ) -> dict:
        """
        S3 Select를 이용한 선택적 필드 로딩
        
        새 기능:
        - Size-based routing: 작은 객체는 GetObject, 큰 것은 Select
        - Cost optimization: 필드 크기 기반 최적 경로 선택
        
        Args:
            s3_path: S3 경로
            field_names: 로드할 필드 목록
            auto_routing: 자동 경로 선택 활성화
        
        Returns:
            dict: 로드된 필드
        """
        bucket, key = self._parse_s3_path(s3_path)
        
        # 1. 객체 크기 확인
        head = self.s3_client.head_object(Bucket=bucket, Key=key)
        object_size = head['ContentLength']
        
        # 2. Size-based routing (피드백 반영)
        if auto_routing:
            use_select = self._should_use_select(
                object_size,
                len(field_names),
                field_names
            )
        else:
            use_select = object_size >= self.FIELD_SIZE_THRESHOLD
        
        # 3. 로딩
        if not use_select:
            # GetObject: 전체 로드 후 필터링
            logger.info(f"Using GetObject for {object_size}B object")
            obj = self.s3_client.get_object(Bucket=bucket, Key=key)
            data = json.loads(obj['Body'].read().decode('utf-8'))
            return {k: v for k, v in data.items() if k in field_names}
        else:
            # S3 Select: SQL 쿼리로 필드 선택
            logger.info(f"Using S3 Select for {object_size}B object, "
                       f"fields={field_names}")
            
            sql_fields = ', '.join(f's.{f}' for f in field_names)
            expression = f"SELECT {sql_fields} FROM s3object s"
            
            response = self.s3_client.select_object_content(
                Bucket=bucket,
                Key=key,
                ExpressionType='SQL',
                Expression=expression,
                InputSerialization={'JSON': {'Type': 'DOCUMENT'}},
                OutputSerialization={'JSON': {}}
            )
            
            # 응답 파싱
            result = []
            for event in response['Payload']:
                if 'Records' in event:
                    result.append(event['Records']['Payload'].decode('utf-8'))
            
            return json.loads(''.join(result))
    
    def _get_dynamic_threshold(self) -> int:
        """
        CloudWatch 기반 동적 threshold (레이턴시 지터 대응)
        
        피드백 반영:
        - S3 Select의 쿼리 파싱 오버헤드는 데이터 양에 따라 변동
        - 실제 레이턴시를 측정하여 threshold 자동 조정
        """
        import time
        
        # 캐시 확인 (1시간 유효)
        if (self._threshold_cache and 
            time.time() - self._threshold_last_update < self.THRESHOLD_UPDATE_INTERVAL):
            return self._threshold_cache
        
        try:
            # CloudWatch에서 최근 1일간 레이턴시 메트릭 조회
            cloudwatch = boto3.client('cloudwatch')
            
            # S3 Select 평균 레이턴시
            select_latency = cloudwatch.get_metric_statistics(
                Namespace='Analemma/StateHydrator',
                MetricName='S3SelectLatency',
                StartTime=datetime.utcnow() - timedelta(days=1),
                EndTime=datetime.utcnow(),
                Period=3600,
                Statistics=['Average']
            )
            
            # GetObject 평균 레이턴시
            get_latency = cloudwatch.get_metric_statistics(
                Namespace='Analemma/StateHydrator',
                MetricName='GetObjectLatency',
                StartTime=datetime.utcnow() - timedelta(days=1),
                EndTime=datetime.utcnow(),
                Period=3600,
                Statistics=['Average']
            )
            
            # 레이턴시 비교하여 최적 threshold 계산
            if select_latency['Datapoints'] and get_latency['Datapoints']:
                select_avg = select_latency['Datapoints'][0]['Average']
                get_avg = get_latency['Datapoints'][0]['Average']
                
                # Select가 Get보다 2배 이상 느리면 threshold 증가
                if select_avg > get_avg * 2:
                    new_threshold = 50 * 1024  # 50KB로 증가
                    logger.warning(f"S3 Select latency high, increasing threshold to {new_threshold}B")
                else:
                    new_threshold = 10 * 1024  # 기본값 유지
                
                self._threshold_cache = new_threshold
                self._threshold_last_update = time.time()
                return new_threshold
        
        except Exception as e:
            logger.warning(f"Failed to get dynamic threshold from CloudWatch: {e}")
        
        # Fallback: 기본값
        return self.FIELD_SIZE_THRESHOLD
    
    def _should_use_select(
        self,
        object_size: int,
        field_count: int,
        field_names: List[str]
    ) -> bool:
        """
        S3 Select vs GetObject 비용/성능 비교
        
        Decision Tree:
        - 객체 < dynamic_threshold: GetObject (레이턴시 지터 대응)
        - 필드 > 80%: GetObject (대부분 필요하면 전체 로드가 효율적)
        - 필드 < 20%: S3 Select (대부분 불필요하면 선택적 로드)
        - 나머지: 객체 크기 기반 (> 50KB면 Select)
        """
        # 동적 threshold 사용
        dynamic_threshold = self._get_dynamic_threshold()
        
        if object_size < dynamic_threshold:
            return False  # 작은 객체는 GetObject
        
        # 전체 필드 수 추정 (메타데이터에서 가져오거나 기본값)
        total_fields = head.get('Metadata', {}).get('field_count', 10)
        field_ratio = field_count / total_fields
        
        if field_ratio > 0.8:
            return False  # 대부분 필요하면 GetObject
        
        if field_ratio < 0.2:
            return True  # 일부만 필요하면 Select
        
        # 중간 영역: 크기 기반
        return object_size >= 50 * 1024  # 50KB 이상
```

---

## 🎯 Phase 5: 비동기 커밋 + Redis 캐시 (Week 7-8, P2)

### 5.1 Read-After-Write Consistency 보장 (피드백 반영)

**파일:** `backend/src/services/state/async_state_checkpointer.py` (NEW)

```python
class AsyncStateCheckpointer:
    """
    비동기 상태 체크포인트 with Read-After-Write 일관성 보장
    
    핵심:
    - 동기식 버전 토큰 발행
    - 비동기 S3/DynamoDB 기록
    - 읽기 시 버전 토큰 대기
    """
    
    def __init__(self, sns_topic_arn: str, redis_host: str):
        self.sns = boto3.client('sns')
        self.topic_arn = sns_topic_arn
        self.redis = redis.Redis(host=redis_host, decode_responses=True)
    
    def checkpoint_async(
        self,
        manifest_id: str,
        state_delta: dict,
        wait_for_commit: bool = False
    ) -> str:
        """
        비동기 체크포인트 with 버전 토큰
        
        Args:
            manifest_id: 현재 매니페스트 ID
            state_delta: 변경된 상태
            wait_for_commit: 커밋 완료까지 대기 (HITL 전용)
        
        Returns:
            str: 버전 토큰 (manifest_id:version)
        """
        # 1. 버전 토큰 발행 (동기)
        version = self._get_next_version(manifest_id)
        version_token = f"{manifest_id}:{version}"
        
        # 2. Redis에 "pending" 상태 기록 (동기)
        self.redis.setex(
            f"version:{version_token}",
            300,  # 5분 TTL
            "pending"
        )
        
        # 3. SNS로 비동기 커밋 요청
        self.sns.publish(
            TopicArn=self.topic_arn,
            Message=json.dumps({
                'version_token': version_token,
                'manifest_id': manifest_id,
                'state_delta': state_delta,
                'timestamp': datetime.utcnow().isoformat()
            })
        )
        
        logger.info(f"Async checkpoint initiated: {version_token}")
        
        # 4. 대기 모드 (HITL 전용)
        if wait_for_commit:
            self._wait_for_commit(version_token, timeout=30)
        
        return version_token
    
    def load_state_with_consistency(
        self,
        version_token: str,
        timeout: int = 10
    ) -> dict:
        """
        버전 토큰을 기다리며 상태 로드 (Read-After-Write 일관성)
        
        피드백 반영:
        - TOCTOU 리스크 완화: S3 Eventual Consistency 대응
        - Exponential Backoff 재시도 추가
        
        Args:
            version_token: 기다릴 버전 (예: "uuid:5")
            timeout: 최대 대기 시간 (초)
        
        Returns:
            dict: 로드된 상태
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Redis에서 버전 상태 확인
            status = self.redis.get(f"version:{version_token}")
            
            if status == "committed":
                # 커밋 완료: S3에서 로드 (TOCTOU 대응)
                logger.info(f"Version {version_token} committed, loading from S3")
                return self._load_from_s3_with_retry(version_token)
            
            elif status == "pending":
                # 아직 커밋 중: 대기
                logger.debug(f"Version {version_token} pending, waiting...")
                time.sleep(0.1)
            
            else:
                # Redis에 없음: 아직 발행되지 않았거나 TTL 초과
                raise ConsistencyError(f"Version {version_token} not found")
        
        # Timeout
        raise TimeoutError(f"Version {version_token} not committed within {timeout}s")
    
    def _load_from_s3_with_retry(
        self,
        version_token: str,
        max_retries: int = 5,
        base_delay: float = 0.1
    ) -> dict:
        """
        S3에서 상태 로드 with Exponential Backoff (TOCTOU 완화)
        
        피드백:
        - Redis 상태가 'committed'로 바뀐 직후 S3 객체가 아직 가용하지 않을 수 있음
        - S3 Eventual Consistency로 인한 짧은 갭 존재
        
        Args:
            version_token: 버전 토큰
            max_retries: 최대 재시도 횟수
            base_delay: 초기 대기 시간 (초)
        
        Returns:
            dict: 로드된 상태
        """
        manifest_id, version = version_token.split(':')
        s3_key = f"manifests/{manifest_id}/v{version}.json"
        
        for attempt in range(max_retries):
            try:
                obj = self.s3.get_object(
                    Bucket=self.bucket,
                    Key=s3_key
                )
                data = json.loads(obj['Body'].read().decode('utf-8'))
                logger.info(f"Successfully loaded state from S3: {version_token}")
                return data
            
            except self.s3.exceptions.NoSuchKey:
                # S3 Eventual Consistency 대기
                delay = base_delay * (2 ** attempt)  # Exponential backoff
                logger.warning(
                    f"S3 object not yet available (attempt {attempt+1}/{max_retries}), "
                    f"retrying in {delay}s... (TOCTOU gap)"
                )
                time.sleep(delay)
            
            except Exception as e:
                logger.error(f"Unexpected error loading from S3: {e}")
                raise
        
        # 재시도 실패
        raise ConsistencyError(
            f"S3 object not available after {max_retries} retries. "
            f"TOCTOU gap exceeded expected window. version_token={version_token}"
        )
    
    def _wait_for_commit(self, version_token: str, timeout: int):
        """커밋 완료까지 대기"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            status = self.redis.get(f"version:{version_token}")
            if status == "committed":
                logger.info(f"Commit completed: {version_token}")
                return
            time.sleep(0.5)
        
        raise TimeoutError(f"Commit timeout: {version_token}")
```

**Lambda Handler (비동기 커밋 워커):**
```python
def async_commit_handler(event, context):
    """
    SNS에서 트리거되는 비동기 커밋 Lambda
    """
    for record in event['Records']:
        message = json.loads(record['Sns']['Message'])
        version_token = message['version_token']
        
        try:
            # 1. S3/DynamoDB에 기록
            versioning_service.commit_checkpoint(
                manifest_id=message['manifest_id'],
                state_delta=message['state_delta']
            )
            
            # 2. Redis 상태 업데이트
            redis_client.setex(
                f"version:{version_token}",
                300,
                "committed"
            )
            
            logger.info(f"Async commit completed: {version_token}")
            
        except Exception as e:
            logger.error(f"Async commit failed: {version_token}, error={e}")
            redis_client.setex(
                f"version:{version_token}",
                300,
                f"failed:{str(e)}"
            )
```

**사용 전략 (피드백 반영):**
```python
# segment_runner_service.py

# 1. 자율형 루프 내부: 동기 커밋 (일관성 보장)
if execution_context == "autonomous_loop":
    # 동기식: 다음 세그먼트가 즉시 읽을 수 있어야 함
    versioning_service.commit_checkpoint_sync(
        manifest_id=manifest_id,
        state_delta=state_delta
    )

# 2. HITL 대기 전: 비동기 커밋 + 대기
elif execution_context == "before_hitl":
    # 비동기 커밋하되, 완료까지 대기
    version_token = checkpointer.checkpoint_async(
        manifest_id=manifest_id,
        state_delta=state_delta,
        wait_for_commit=True  # HITL 전에는 반드시 커밋 완료
    )
    
    # HITL 재개 시 버전 토큰 전달
    task_token_metadata['version_token'] = version_token

# 3. 루프 완료 후: 완전 비동기 (Snapshot)
elif execution_context == "loop_completed":
    # 완전 비동기: 재개 시 대기하면 됨
    version_token = checkpointer.checkpoint_async(
        manifest_id=manifest_id,
        state_delta=state_delta,
        wait_for_commit=False
    )
```

---

## 🎯 Phase 6: Garbage Collection (Week 9, P2)

### 6.1 S3 Lifecycle Policy + DynamoDB TTL

**파일:** `infrastructure/s3_lifecycle_policy.json` (NEW)

```json
{
  "Rules": [
    {
      "Id": "ArchiveOldStateBlocks",
      "Status": "Enabled",
      "Filter": {
        "Prefix": "state-blocks/"
      },
      "Transitions": [
        {
          "Days": 30,
          "StorageClass": "GLACIER"
        }
      ],
      "Expiration": {
        "Days": 90
      }
    },
    {
      "Id": "DeleteOldConfigs",
      "Status": "Enabled",
      "Filter": {
        "Prefix": "workflow-configs/"
      },
      "Expiration": {
        "Days": 365
      }
    }
  ]
}
```

**DynamoDB TTL (이미 설정됨):**
```python
# WorkflowManifestsV3 테이블
{
    "ttl": int(time.time()) + 30 * 24 * 3600  # 30일 후 자동 삭제
}
```

### 6.2 Garbage Collector Lambda

**파일:** `backend/src/handlers/garbage_collector.py` (NEW)

```python
def gc_handler(event, context):
    """
    일정 기간 참조되지 않은 블록 정리
    
    트리거: CloudWatch Events (매일 새벽 2시)
    """
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['MANIFESTS_TABLE'])
    s3 = boto3.client('s3')
    bucket = os.environ['STATE_BUCKET']
    
    # 1. 참조되는 모든 블록 ID 수집
    referenced_blocks = set()
    
    scan_kwargs = {}
    while True:
        response = table.scan(**scan_kwargs)
        
        for item in response['Items']:
            # TTL이 아직 유효한 항목만
            if item.get('ttl', 0) > time.time():
                blocks = item.get('s3_pointers', {}).get('state_blocks', [])
                for block_path in blocks:
                    block_id = block_path.split('/')[-1].replace('.json', '')
                    referenced_blocks.add(block_id)
        
        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    
    logger.info(f"Found {len(referenced_blocks)} referenced blocks")
    
    # 2. S3에서 모든 블록 나열
    all_blocks = set()
    paginator = s3.get_paginator('list_objects_v2')
    
    for page in paginator.paginate(Bucket=bucket, Prefix='state-blocks/'):
        for obj in page.get('Contents', []):
            block_id = obj['Key'].split('/')[-1].replace('.json', '')
            all_blocks.add(block_id)
    
    logger.info(f"Found {len(all_blocks)} total blocks in S3")
    
    # 3. 참조되지 않는 블록 삭제
    orphaned_blocks = all_blocks - referenced_blocks
    
    if orphaned_blocks:
        logger.info(f"Deleting {len(orphaned_blocks)} orphaned blocks")
        
        # 배치 삭제 (최대 1000개씩)
        for i in range(0, len(orphaned_blocks), 1000):
            batch = list(orphaned_blocks)[i:i+1000]
            s3.delete_objects(
                Bucket=bucket,
                Delete={
                    'Objects': [{'Key': f'state-blocks/{bid}.json'} for bid in batch]
                }
            )
        
        logger.info(f"GC completed: deleted {len(orphaned_blocks)} blocks")
    else:
        logger.info("GC completed: no orphaned blocks found")
    
    return {
        'referenced': len(referenced_blocks),
        'total': len(all_blocks),
        'deleted': len(orphaned_blocks)
    }
```

---

## 🎯 Phase 7: 보안 강화 (Week 10, P1)

### 7.1 Merkle Hash 검증 강제

**파일:** [segment_runner_service.py](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\services\\execution\\segment_runner_service.py)

```python
def execute_segment(self, event, context):
    """
    세그먼트 실행 with Pre-computed Hash 검증 (최적화)
    """
    # 1. manifest_hash 검증
    manifest_id = event.get('manifest_id')
    segment_index = event.get('segment_index')
    
    if not manifest_id:
        raise SecurityError("Missing manifest_id - execution blocked")
    
    # 2. segment_config 로드
    segment_config = event.get('segment_config')
    
    # 3. Pre-computed Hash로 무결성 검증 (1-5ms, 기존 200-500ms 대비 100배 빠름)
    versioning_service = StateVersioningService(
        dynamodb_table=os.environ['MANIFESTS_TABLE'],
        s3_bucket=os.environ['STATE_BUCKET']
    )
    
    is_valid = versioning_service.verify_segment_config(
        segment_config=segment_config,
        manifest_id=manifest_id,
        segment_index=segment_index
    )
    
    if not is_valid:
        raise SecurityError(
            f"Segment config integrity violation! "
            f"manifest_id={manifest_id}, segment={segment_index}"
        )
    
    logger.info(f"✓ Segment {segment_index} verified (pre-computed hash)")
    
    # 4. 실행
    # ...
```

**StateVersioningService에 추가 (Pre-computed Hash Verification):**
```python
def verify_segment_config(
    self,
    segment_config: dict,
    manifest_id: str,
    segment_index: int
) -> bool:
    """
    segment_config 무결성 검증 (Pre-computed Hash 방식)
    
    피드백 반영:
    - ❌ 기존: 매번 partition_workflow() 재실행 (너무 무거움)
    - ✅ 개선: Pre-computed Hash로 O(1) 검증
    
    방법:
    1. DynamoDB에서 manifest의 segment_hashes 로드
    2. 입력된 segment_config의 해시 계산
    3. Pre-computed Hash와 비교
    
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
```

**성능 비교:**
```
기존 (partition_workflow 재실행):
- 시간: 200-500ms (workflow 크기에 따라)
- CPU: 높음
- 메모리: 높음 (전체 graph 재구성)

개선 (Pre-computed Hash):
- 시간: 1-5ms (해시 계산만)
- CPU: 낮음
- 메모리: 낮음 (segment_config만)

→ 100배 이상 성능 향상
```

---

## 📊 최종 아키텍처 비교

### Before (V2 - Fat State):
```
데이터 흐름:
Initialize
├─ workflow_config: 200KB → StateBag ❌
├─ partition_map: 50KB → StateBag ❌
└─ StateBag: 370KB

Segment 0-99
├─ workflow_config: 200KB × 100 = 20MB ❌
├─ partition_map: 50KB × 100 = 5MB ❌
└─ 총 전송: 37MB

보안:
- 세그먼트가 전체 워크플로우 구조 접근 가능 ❌
- 상태 조작 감지 불가 ❌
- 회귀 시 정확한 시점 재현 불가 ❌

비용:
- S3 저장: 높음 (중복 90%)
- Lambda 메모리: 높음
- 네트워크: 높음
```

### After (V3 - Merkle DAG + Lean Manifest):
```
데이터 흐름:
Initialize
├─ workflow_config: 200KB → S3 (참조용, hash 저장) ✅
├─ partition_map: 로컬 폐기 ✅
├─ Merkle Manifest: DynamoDB Pointer ✅
└─ StateBag: 120KB (-68%)

Segment 0-99
├─ segment_config: 10KB (ASL 직접 주입 또는 S3 Select) ✅
├─ manifest_hash: 검증 ✅
└─ 총 전송: 13MB (-65%)

보안:
- 세그먼트는 자신의 config만 접근 (최소 권한) ✅
- Merkle Root로 1바이트 조작도 감지 ✅
- Pointer Manifest로 즉시 회귀 가능 ✅

비용:
- S3 저장: 낮음 (중복 10%, GC 자동화)
- Lambda 메모리: 낮음 (-68%)
- 네트워크: 낮음 (-65%)
```

---

## 📋 Implementation Checklist

### Phase 0: 사전 준비 (Week 1)
- [ ] `_load_segment_config_from_manifest()` 구현
- [ ] Size-based routing 로직 추가
- [ ] Hybrid loading 배포 (호환성 확보)
- [ ] 기존 워크플로우 정상 동작 확인

### Phase 1: Merkle DAG 인프라 (Week 2-3)
- [ ] WorkflowManifestsV3 DynamoDB 테이블 생성
- [ ] StateVersioningService 구현
- [ ] Merkle Root 계산 로직 검증
- [ ] Content-Addressable Storage 테스트

### Phase 2: workflow_config 제거 (Week 4)
- [ ] initialize_state_data.py 수정
- [ ] ASL Threshold-based Loading 구현
- [ ] manifest_hash, config_hash 전달 로직
- [ ] 회귀 테스트 (기본/parallel_group)

### Phase 3: SegmentFieldOptimizer (Week 5)
- [ ] Capability-based Filtering 구현
- [ ] Security Ring 통합
- [ ] segment_runner_service.py 통합
- [ ] 페이로드 절감 검증 (67%)

### Phase 4: S3 Select 최적화 (Week 6)
- [ ] StateHydrator Size-based routing
- [ ] Cost optimization 로직
- [ ] S3 Select 성능 벤치마크

### Phase 5: 비동기 커밋 (Week 7-8)
- [ ] AsyncStateCheckpointer 구현
- [ ] Redis 캐시 레이어 추가
- [ ] Read-After-Write 일관성 테스트
- [ ] SNS + Lambda 비동기 워커 배포

### Phase 6: Garbage Collection (Week 9)
- [ ] S3 Lifecycle Policy 적용
- [ ] GC Lambda 구현
- [ ] CloudWatch Events 트리거 설정
- [ ] 30일 후 자동 삭제 검증

### Phase 7: 보안 강화 (Week 10)
- [ ] Merkle Hash 검증 강제
- [ ] segment_config 무결성 검사
- [ ] Security audit 완료
- [ ] Penetration testing

### 운영 준비
- [ ] CloudWatch 메트릭 대시보드
- [ ] 알람 설정 (일관성 위반, GC 실패)
- [ ] 문서화 (API, Architecture, Migration)
- [ ] 팀 교육

---

## ⚠️ 리스크 및 완화 전략

### 1. Read-After-Write Consistency 위반
**리스크:** 비동기 커밋 중 다음 세그먼트가 과거 상태 읽음  
**완화:**
- 자율형 루프 내부는 동기 커밋 유지
- HITL 전 wait_for_commit=True
- Redis 버전 토큰으로 대기

### 2. S3 Select 비용 증가
**리스크:** 작은 객체에도 Select 사용 시 비용 상승  
**완화:**
- 10KB 미만은 GetObject 강제
- 필드 비율 80% 이상이면 GetObject
- CloudWatch Cost Explorer 모니터링

### 3. Merkle DAG 복잡도
**리스크:** 디버깅 어려움, 개발자 학습 곡선  
**완화:**
- 명확한 문서화
- 디버깅 도구 제공 (manifest 시각화)
- 점진적 마이그레이션 (V2 fallback 유지)

### 4. GC 오작동
**리스크:** 참조 중인 블록 삭제  
**완화:**
- Dry-run 모드 먼저 테스트
- 삭제 전 30일 Glacier 보관
- 수동 복구 절차 문서화

### 5. S3 Select 레이턴시 지터
**리스크:** 쿼리 파싱 오버헤드로 작은 요청에서 GetObject보다 느림  
**완화:**
- CloudWatch 기반 동적 threshold 튜닝 (Phase 4.1)
- 10KB 미만 강제 GetObject
- 실제 레이턴시 측정하여 1시간마다 threshold 자동 조정

### 6. Redis TOCTOU (Time-of-Check to Time-of-Use)
**리스크:** Redis 'committed' 상태와 S3 실제 가용성 간 시간차  
**완화:**
- S3 로드 시 Exponential Backoff 재시도 (Phase 5.1)
- 최대 5회, 0.1초부터 시작하여 지수 증가
- S3 Eventual Consistency 대응

### 7. ASL 복잡도
**리스크:** States.ArrayGetItem 문법 오류, 동적 인덱스 처리 실패  
**완화:**
- Lambda Fallback을 주 경로로 설계 (80% 처리)
- ASL Direct Injection은 "빠른 경로"로만 활용 (20%)
- Lambda 캐싱 로직을 Phase 0에서 최우선 구현

---

## 📈 성능 지표 목표

| 지표 | 현재 (V2) | 목표 (V3) | 개선율 |
|------|-----------|-----------|--------|
| 평균 페이로드 크기 | 400KB | 130KB | **-67%** |
| 100 세그먼트 전송량 | 37MB | 13MB | **-65%** |
| 데이터 중복률 | 90% | 10% | **-89%** |
| 회귀 속도 | 느림 (전체 복구) | 즉시 (포인터 전환) | **100배↑** |
| S3 저장 비용 | 기준 | 30% | **-70%** |
| Lambda 메모리 | 512MB | 256MB | **-50%** |
| 무결성 검증 | 불가능 | Merkle Root | **100%** |

---

## 🎓 팀 교육 자료

### 개발자 가이드
1. **Merkle DAG 개념**
   - Git과 동일한 Content-Addressable Storage
   - 상태 변경 = 새 해시 블록 생성
   - 과거 버전은 포인터만 바꾸면 즉시 접근

2. **디버깅 방법**
   ```bash
   # manifest_id로 상태 추적
   aws dynamodb get-item \
     --table-name WorkflowManifestsV3 \
     --key '{"manifest_id": {"S": "uuid"}}'
   
   # 특정 블록 내용 확인
   aws s3 cp s3://bucket/state-blocks/hash.json -
   ```

3. **회귀 방법**
   ```python
   # 특정 버전으로 롤백
   versioning_service.rollback_to_version(
       workflow_id="workflow_123",
       version=5  # 5번 버전으로
   )
   ```

---

## 📝 참고 문서

- [WORKFLOW_CONFIG_LIFECYCLE_FIX.md](WORKFLOW_CONFIG_LIFECYCLE_FIX.md) - workflow_config 제거 상세
- [SEGMENT_PAYLOAD_OPTIMIZATION.md](SEGMENT_PAYLOAD_OPTIMIZATION.md) - 페이로드 최적화 분석
- [Git Internals - Objects](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects) - Merkle DAG 참고
- [S3 Select Documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/selecting-content-from-objects.html)

---

**마지막 업데이트:** 2026-02-18  
**문서 버전:** 1.0  
**승인자:** Architecture Review Board  
**다음 검토:** Phase 2 완료 후
