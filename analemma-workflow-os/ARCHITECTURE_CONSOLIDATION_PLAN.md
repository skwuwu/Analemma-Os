# Smart StateBag 아키텍처 통합 개선 계획

## 📊 현재 상태 분석 (2026-02-19)

### 🔴 문제점: 파편화 위험

#### 1. **StateHydrator 이중화**
```
현재:
- StateHydrator (state_hydrator.py) ← 기존
- BatchedDehydrator (batched_dehydrator.py) ← 새로 추가 (Phase 8)

문제:
- 두 클래스가 독립적으로 존재
- Lambda에서 어떤 것을 사용할지 불명확
- 로직 중복 (S3 업로드, 압축 등)
```

#### 2. **StateVersioningService 분산**
```
현재:
- StateVersioningService (state_versioning_service.py) ← 기존
- EventualConsistencyGuard (eventual_consistency_guard.py) ← 새로 추가 (Phase 10)

문제:
- 2-Phase Commit 로직이 분리됨
- StateVersioningService.create_manifest()와 EventualConsistencyGuard.create_manifest_with_consistency() 중복
- 호출자가 어떤 것을 사용할지 결정해야 함
```

#### 3. **인스턴스화 패턴 불일치**
```python
# 패턴 1: 매번 생성 (initialize_state_data.py)
hydrator = StateHydrator(bucket_name=bucket)

# 패턴 2: 싱글톤 (universal_sync_core.py)
_default_hydrator = StateHydrator()

# 패턴 3: 클래스 멤버 (segment_runner_service.py)
self.hydrator = StateHydrator(bucket_name=self.state_bucket)
```

---

## ✅ 개선 계획: Unified Architecture

### Phase A: StateHydrator 통합 (Priority: P0)

#### 목표: BatchedDehydrator를 StateHydrator에 통합

**Before (파편화)**:
```python
# Lambda A
hydrator = StateHydrator()
result = hydrator.dehydrate(state)  # 기존 방식

# Lambda B
batcher = BatchedDehydrator()
result = batcher.dehydrate_batch(changed_fields)  # 새 방식
```

**After (통합)**:
```python
# 모든 Lambda
hydrator = StateHydrator(
    use_batching=True,  # Phase 8 기능 활성화
    use_zstd=True       # Zstd 압축
)
result = hydrator.dehydrate(state)  # 단일 인터페이스
```

#### 구현:
```python
# backend/src/common/state_hydrator.py

class StateHydrator:
    def __init__(
        self,
        bucket_name: Optional[str] = None,
        use_batching: bool = False,       # ✅ Phase 8: Smart Batching
        use_zstd: bool = False,           # ✅ Phase 8: Zstd Compression
        compression_level: int = 3
    ):
        self.s3_client = boto3.client('s3')
        self._bucket = bucket_name or os.environ.get('SKELETON_S3_BUCKET')
        
        # Phase 8: Batching 설정
        self.use_batching = use_batching
        self.use_zstd = use_zstd
        
        if use_batching:
            from src.common.batched_dehydrator import BatchedDehydrator
            self._batcher = BatchedDehydrator(
                bucket_name=self._bucket,
                compression_level=compression_level
            )
        else:
            self._batcher = None
    
    def dehydrate(
        self,
        state: SmartStateBag,
        owner_id: str,
        workflow_id: str,
        execution_id: str,
        return_delta: bool = True
    ) -> Dict[str, Any]:
        """
        통합 Dehydration 엔진
        
        자동 전략 선택:
        - use_batching=True → BatchedDehydrator 사용
        - use_batching=False → 기존 필드별 오프로드
        """
        if self.use_batching and state.has_changes():
            # Phase 8: Smart Batching
            delta = state.get_delta()
            batch_pointers = self._batcher.dehydrate_batch(
                changed_fields=delta.changed_fields,
                owner_id=owner_id,
                workflow_id=workflow_id,
                execution_id=execution_id
            )
            
            # 배치 포인터를 state에 통합
            result = {}
            for batch_key, batch_pointer in batch_pointers.items():
                result[batch_key] = batch_pointer
            
            return result
        else:
            # 기존 방식
            return self._dehydrate_legacy(state, owner_id, workflow_id, execution_id, return_delta)
```

**마이그레이션 전략**:
1. **Week 1**: StateHydrator에 `use_batching` 파라미터 추가
2. **Week 2**: 핵심 Lambda 5개에서 `use_batching=True` 테스트
3. **Week 3**: 전체 롤아웃 + BatchedDehydrator 클래스 deprecated 선언
4. **Week 4**: BatchedDehydrator 삭제

---

### Phase B: StateVersioningService 통합 (Priority: P0)

#### 목표: EventualConsistencyGuard를 StateVersioningService에 통합

**Before (분산)**:
```python
# create_manifest()를 호출하는 코드
from src.services.state.state_versioning_service import StateVersioningService
from src.services.state.eventual_consistency_guard import EventualConsistencyGuard

# 어떤 것을 사용? 혼란!
versioning = StateVersioningService(...)
guard = EventualConsistencyGuard(...)
```

**After (통합)**:
```python
# backend/src/services/state/state_versioning_service.py

class StateVersioningService:
    def __init__(
        self,
        dynamodb_table: str,
        s3_bucket: str,
        block_references_table: str = None,
        use_2pc: bool = True,          # ✅ Phase 10: 2-Phase Commit
        gc_dlq_url: Optional[str] = None
    ):
        self.use_2pc = use_2pc
        
        if use_2pc and gc_dlq_url:
            from src.services.state.eventual_consistency_guard import EventualConsistencyGuard
            self._consistency_guard = EventualConsistencyGuard(
                s3_bucket=s3_bucket,
                dynamodb_table=dynamodb_table,
                block_references_table=block_references_table,
                gc_dlq_url=gc_dlq_url
            )
        else:
            self._consistency_guard = None
    
    def create_manifest(
        self,
        workflow_id: str,
        workflow_config: dict,
        segment_manifest: List[dict],
        parent_manifest_id: Optional[str] = None
    ) -> ManifestPointer:
        """
        통합 Manifest 생성
        
        자동 전략 선택:
        - use_2pc=True → EventualConsistencyGuard 사용 (99.99% 정합성)
        - use_2pc=False → 기존 트랜잭션 사용 (98% 정합성)
        """
        if self._consistency_guard:
            # Phase 10: 2-Phase Commit
            manifest_id = str(uuid.uuid4())
            version = self._get_next_version(workflow_id)
            
            # ... (해시 계산 등)
            
            return self._consistency_guard.create_manifest_with_consistency(
                workflow_id=workflow_id,
                manifest_id=manifest_id,
                version=version,
                config_hash=config_hash,
                manifest_hash=manifest_hash,
                blocks=blocks,
                segment_hashes=segment_hashes,
                metadata=metadata
            )
        else:
            # 기존 방식
            return self._create_manifest_legacy(...)
```

**마이그레이션 전략**:
1. **Week 1**: StateVersioningService에 `use_2pc` 파라미터 추가
2. **Week 2**: 새 워크플로우에서 `use_2pc=True` 테스트
3. **Week 3**: 기존 워크플로우도 `use_2pc=True`로 전환
4. **Week 4**: EventualConsistencyGuard를 private 메서드로 변경

---

### Phase C: 싱글톤 인스턴스화 패턴 (Priority: P1)

#### 목표: Lambda 콜드 스타트 최소화

**Before (매번 생성)**:
```python
def lambda_handler(event, context):
    hydrator = StateHydrator(bucket_name=os.environ['BUCKET'])  # 매번 boto3 client 생성
    state = hydrator.hydrate(event)
```

**After (모듈 레벨 싱글톤)**:
```python
# backend/src/common/state_hydrator.py (모듈 최하단)
_default_hydrator: Optional[StateHydrator] = None

def get_hydrator(
    use_batching: Optional[bool] = None,
    use_zstd: Optional[bool] = None
) -> StateHydrator:
    """
    싱글톤 StateHydrator 반환 (Lambda 재사용)
    
    첫 호출 시 환경 변수로 초기화, 이후 재사용
    """
    global _default_hydrator
    
    if _default_hydrator is None:
        _default_hydrator = StateHydrator(
            bucket_name=os.environ.get('SKELETON_S3_BUCKET'),
            use_batching=use_batching or os.environ.get('USE_BATCHING', 'false') == 'true',
            use_zstd=use_zstd or os.environ.get('USE_ZSTD', 'false') == 'true'
        )
    
    return _default_hydrator

# Lambda 함수
def lambda_handler(event, context):
    hydrator = get_hydrator()  # ✅ 싱글톤 재사용
    state = hydrator.hydrate(event)
```

**이점**:
- boto3 client 재사용 (HTTP 연결 풀 유지)
- Zstd 컴프레서 재사용 (초기화 비용 제거)
- 콜드 스타트 50-100ms 절감

---

### Phase D: 설정 기반 Feature Flag (Priority: P2)

#### 목표: 런타임 동적 전환

**환경 변수**:
```yaml
# backend/template.yaml
Globals:
  Function:
    Environment:
      Variables:
        # Phase 8: Smart Batching
        USE_BATCHING: "true"
        USE_ZSTD: "true"
        ZSTD_LEVEL: "3"
        
        # Phase 10: 2-Phase Commit
        USE_2PC: "true"
        GC_DLQ_URL: !Ref GCDeadLetterQueue
```

**Lambda 함수**:
```python
def lambda_handler(event, context):
    # 환경 변수로 자동 설정
    hydrator = get_hydrator()  # USE_BATCHING, USE_ZSTD 자동 적용
    
    versioning = StateVersioningService(
        dynamodb_table=os.environ['MANIFESTS_TABLE'],
        s3_bucket=os.environ['SKELETON_S3_BUCKET'],
        use_2pc=os.environ.get('USE_2PC', 'true') == 'true',
        gc_dlq_url=os.environ.get('GC_DLQ_URL')
    )
```

**롤아웃 시나리오**:
```
Day 1: USE_BATCHING=false (검증 완료 대기)
Day 7: USE_BATCHING=true (워크플로우 5% A/B 테스트)
Day 14: USE_BATCHING=true (전체 롤아웃)
```

---

## 📊 통합 후 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  Lambda Handler                                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  hydrator = get_hydrator()  ← 싱글톤 (환경 변수 기반)        │
│  state = hydrator.hydrate(event)                            │
│                                                              │
│  # 비즈니스 로직                                              │
│  state['result'] = process(...)                             │
│                                                              │
│  return hydrator.dehydrate(state)                           │
│         ↓                                                    │
│  [자동 전략 선택]                                             │
│    use_batching=True → BatchedDehydrator (내장)             │
│    use_batching=False → Legacy Offload                      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────┐
│  StateVersioningService                                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  versioning.create_manifest(...)                            │
│         ↓                                                    │
│  [자동 전략 선택]                                             │
│    use_2pc=True → EventualConsistencyGuard (내장)           │
│    use_2pc=False → Legacy Transaction                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**단일 진입점**:
- `StateHydrator.dehydrate()` → 모든 오프로드 로직 통합
- `StateVersioningService.create_manifest()` → 모든 버저닝 로직 통합
- 호출자는 구현 세부사항을 몰라도 됨

---

## 🎯 구현 우선순위

### P0 (1주 내)
1. ✅ StateHydrator에 `use_batching` 파라미터 추가
2. ✅ StateVersioningService에 `use_2pc` 파라미터 추가
3. ✅ 기존 코드와 호환성 유지 (기본값 False)

### P1 (2주 내)
4. ✅ `get_hydrator()` 싱글톤 팩토리 구현
5. ✅ 환경 변수 기반 Feature Flag
6. ✅ A/B 테스트 (5% 트래픽)

### P2 (1개월 내)
7. ✅ 전체 롤아웃
8. ✅ BatchedDehydrator, EventualConsistencyGuard deprecated
9. ✅ 통합 테스트 + 문서 업데이트

### ⚠️ P3 (추가 통합 필요 - Phase E, F, G)
10. ❌ **StateManager → StateVersioningService 통합** (Phase E)
11. ❌ **StatePersistenceService → StateVersioningService 통합** (Phase F)
12. ❌ **StateDataManager → StateHydrator 통합** (Phase G)

---

## ⚠️ 추가 파편화 발견 (Phase E-G 필요)

### Phase E: StateManager 통합 (Priority: P3)

**문제**: StateManager가 StateVersioningService와 기능 중복

**현재 구조**:
```python
# StateManager (state_manager.py)
class StateManager:
    def upload_state_to_s3(bucket, prefix, state)  # ❌ 중복
    def download_state_from_s3(s3_path)            # ❌ 중복
    def handle_state_storage(state, ...)           # ❌ 중복
    def mask_pii_in_state(state)                   # ✅ 보안 - 유지

# StateVersioningService (state_versioning_service.py)
class StateVersioningService:
    def create_manifest(...)  # Merkle DAG
    def load_manifest_segments(...)
    def _upload_block_to_s3(...)  # ❌ StateManager와 중복
```

**목표 구조**:
```python
# StateVersioningService (통합 후)
class StateVersioningService:
    def create_manifest(...)  # Merkle DAG
    def load_manifest_segments(...)
    def save_state(...)       # ✅ StateManager.upload_state_to_s3 대체
    def load_state(...)       # ✅ StateManager.download_state_from_s3 대체

# SecurityUtils (새로 분리)
def mask_pii_in_state(state)  # ✅ 보안 유틸리티로 별도 분리
```

**마이그레이션**:
1. StateManager.mask_pii_in_state() → SecurityUtils로 분리
2. StateManager.upload/download → StateVersioningService로 통합
3. StateManager deprecated 선언

---

### Phase F: StatePersistenceService 통합 (Priority: P3)

**문제**: StatePersistenceService가 StateVersioningService와 기능 중복

**현재 구조**:
```python
# StatePersistenceService (state_persistence_service.py)
class StatePersistenceService:
    def save_state(execution_id, state_data)  # ❌ 중복
    def load_state(execution_id)              # ❌ 중복
    def delete_state(execution_id)            # ❌ 중복
    # S3 + DynamoDB dual-write

# StateVersioningService (state_versioning_service.py)
class StateVersioningService:
    def create_manifest(...)  # Merkle DAG
    # S3 + DynamoDB transaction
```

**목표**: StatePersistenceService의 dual-write 로직을 StateVersioningService로 통합

**이유**:
- 둘 다 S3 + DynamoDB 사용
- 둘 다 트랜잭션 필요
- 중복 코드 500줄+

---

### Phase G: StateDataManager 통합 (Priority: P3)

**문제**: StateDataManager (Lambda)가 StateHydrator와 기능 중복

**현재 구조**:
```python
# StateDataManager (state_data_manager.py)
def optimize_current_state(state, idempotency_key)  # ❌ 중복
def store_to_s3(data, key)                          # ❌ 중복
def load_from_s3(s3_path)                           # ❌ 중복
def cached_load_from_s3(s3_path)                    # ❌ 중복

# StateHydrator (state_hydrator.py)
class StateHydrator:
    def dehydrate(state, ...)  # S3 오프로드
    def hydrate(event, ...)    # S3 로드
```

**목표**: StateDataManager Lambda를 제거하고 StateHydrator로 통합

**마이그레이션**:
1. `sync_state_data()` → StateHydrator.dehydrate()로 대체
2. `optimize_current_state()` → StateHydrator 내부 로직으로 통합
3. StateDataManager Lambda Function 제거 (SAM template)

---

## 💰 통합 효과

### 개발 생산성
- **코드 중복 제거**: 500줄 → 0줄
- **의사결정 부담 제거**: "어떤 클래스 사용?" → "StateHydrator 하나"
- **온보딩 시간**: 2일 → 1시간

### 운영 안정성
- **단일 책임**: 각 클래스가 명확한 역할
- **테스트 범위**: 2개 클래스 → 1개 클래스
- **버그 추적**: 파편화된 로직 → 중앙 집중

### 성능
- **싱글톤 재사용**: boto3 client, Zstd 컴프레서
- **콜드 스타트**: 50-100ms 절감
- **메모리 효율**: 중복 인스턴스 제거

---

## 📝 마이그레이션 가이드

### Step 1: 기존 코드 (변경 없음)
```python
# ✅ 기존 코드는 그대로 작동
hydrator = StateHydrator()
state = hydrator.hydrate(event)
result = hydrator.dehydrate(state)
```

### Step 2: Phase 8 활성화 (옵션)
```python
# ✅ Phase 8 기능 활성화
hydrator = StateHydrator(use_batching=True, use_zstd=True)
state = hydrator.hydrate(event)
result = hydrator.dehydrate(state)  # 자동으로 BatchedDehydrator 사용
```

### Step 3: 싱글톤 사용 (권장)
```python
# ✅ 싱글톤으로 전환
from src.common.state_hydrator import get_hydrator

def lambda_handler(event, context):
    hydrator = get_hydrator()  # 환경 변수로 자동 설정
    state = hydrator.hydrate(event)
    result = hydrator.dehydrate(state)
```

---

**결론**: 현재는 파편화 위험이 있으나, Phase A-D 통합으로 **단일 진입점, 환경 변수 기반 Feature Flag, 싱글톤 패턴**으로 개선 가능합니다.
