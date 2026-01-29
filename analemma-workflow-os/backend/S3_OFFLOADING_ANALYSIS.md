# 🔍 S3 Offloading & Recovery Analysis Report

**작성일**: 2026-01-29
**분석 범위**: state_data_manager.py의 모든 경로

---

## 📊 Executive Summary

### ✅ 구현 완료 항목
- S3 오프로딩: 5개 경로에서 구현
- 복구 로직: 3개 함수 (load_from_s3, cached_load_from_s3, decompress_data)
- 포인터 최적화: 적용됨

### ⚠️ 개선 필요 항목
- **P0**: aggregate_distributed_results에 S3 오프로딩 미적용
- **P1**: 포인터 비대화 리스크 (일부 경로)
- **P2**: 복구 실패 시 Fallback 로직 부재

---

## 1️⃣ S3 오프로딩 적용 현황

### 1.1 적용된 경로

| 경로 | 함수 | 오프로딩 대상 | 트리거 조건 | 상태 |
|------|------|-------------|------------|------|
| **레거시 경로** | `update_and_compress_state_data()` | state_history, current_state, workflow_config, partition_map | payload > 200KB | ✅ 완전 구현 |
| **v3: Sync** | `sync_state_data()` | state_history, partition_map | payload > MAX_PAYLOAD_SIZE_KB | ✅ 완전 구현 |
| **v3: Aggregate Branches** | `aggregate_branches()` | - (포인터만 사용) | load_from_s3=True | ✅ 완전 구현 |
| **Internal** | `optimize_state_history()` | old_history (50개 이상) | len > 50 | ✅ 완전 구현 |
| **Internal** | `optimize_current_state()` | 개별 필드 (30KB 이상), full_state (100KB 이상) | 필드별 크기 초과 | ✅ 완전 구현 |

**적용률**: 5/5 주요 경로 (100%)

---

### 1.2 미적용 경로 (⚠️ 리스크)

| 경로 | 함수 | 리스크 | 우선순위 |
|------|------|--------|---------|
| **v3: Aggregate Distributed** | `aggregate_distributed_results()` | chunk_results 누적 시 256KB 초과 가능 | **P0** |
| **v3: Merge Callback** | `merge_callback_result()` | callback_result 크기 제한 없음 | P2 |
| **v3: Merge Async** | `merge_async_result()` | async_result 크기 제한 없음 | P2 |
| **v3: Snapshot** | `create_snapshot()` | snapshot_data 전체를 그대로 저장 | P1 |

---

## 2️⃣ 복구(Recovery) 로직 분석

### 2.1 복구 함수 구현 현황

#### ✅ `load_from_s3(s3_path: str)` - Lines 102-130
```python
def load_from_s3(s3_path: str) -> Any:
    if not s3_path or not s3_path.startswith('s3://'):
        return None
    
    try:
        path_parts = s3_path.replace('s3://', '').split('/', 1)
        bucket = path_parts[0]
        key = path_parts[1] if len(path_parts) > 1 else ''
        
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)
    except Exception as e:
        logger.error(f"Failed to load from S3 {s3_path}: {e}")
        return None  # ⚠️ 복구 실패 시 None 반환 (데이터 손실)
```

**이슈**:
- ✅ 기본 복구 로직 구현
- ⚠️ **실패 시 None 반환** → 데이터 손실
- ⚠️ 재시도 로직 없음
- ⚠️ Fallback 메커니즘 없음

---

#### ✅ `cached_load_from_s3(s3_path: str)` - Lines 210-249 (New!)
```python
def cached_load_from_s3(s3_path: str) -> Any:
    # Lambda Cold Start 동안 캐시 유지 (5분 TTL)
    # 최대 20개 항목 캐시
    
    if s3_path in _s3_cache:
        cache_time = _cache_timestamps.get(s3_path, 0)
        if current_time - cache_time < CACHE_TTL_SECONDS:
            logger.debug(f"Cache hit for {s3_path}")
            return _s3_cache[s3_path]
    
    data = load_from_s3(s3_path)
    
    if data is not None:
        # 캐시에 저장 (최대 20개)
        _s3_cache[s3_path] = data
```

**장점**:
- ✅ S3 GET 요청 30% 감소 예상
- ✅ Lambda 메모리 효율적 (최대 20개)
- ✅ TTL로 stale data 방지

**이슈**:
- ⚠️ load_from_s3 실패 시 캐시도 실패

---

#### ✅ `decompress_data(compressed_str: str)` - Lines 68-77
```python
def decompress_data(compressed_str: str) -> Any:
    try:
        compressed = base64.b64decode(compressed_str.encode('utf-8'))
        decompressed = gzip.decompress(compressed)
        return json.loads(decompressed.decode('utf-8'))
    except Exception as e:
        logger.error(f"Failed to decompress data: {e}")
        raise  # ⚠️ 예외 전파 (워크플로우 실패)
```

**이슈**:
- ✅ 압축 해제 기본 구현
- ⚠️ **실패 시 예외 전파** → 워크플로우 중단

---

### 2.2 복구 로직 사용 위치

| 위치 | 복구 대상 | 함수 | Fallback |
|------|----------|------|----------|
| `aggregate_branches()` | branch_data (S3) | `cached_load_from_s3()` | ❌ None 반환 시 로그 누락 |
| `update_and_compress()` | - (저장만 수행) | - | N/A |
| `sync_state_data()` | - (저장만 수행) | - | N/A |
| ExecuteSegment Lambda | current_state (S3) | `load_from_s3()` 추정 | ⚠️ 미확인 |

**결론**: **복구 로직은 존재하나 Fallback 메커니즘 부재**

---

## 3️⃣ 포인터 크기 분석

### 3.1 S3 포인터 구조

#### 개별 필드 오프로딩 (optimize_current_state)
```python
{
    "type": "s3_reference",
    "s3_path": "s3://bucket/workflow-state/key/state_field_20260129.json",
    "size_kb": 45,
    "stored_at": "2026-01-29T10:30:45.123456Z"
}
```

**크기**: ~150 bytes

---

#### 전체 상태 오프로딩 (full_state)
```python
{
    "__s3_offloaded": True,
    "__s3_path": "s3://bucket/workflow-state/key/full_state_20260129.json",
    "__original_size_kb": 120,
    "guardrail_verified": True,
    "batch_count_actual": 5,
    "scheduling_metadata": {...},  # ⚠️ 이것도 커질 수 있음
    "__scheduling_metadata": {...},
    "__guardrail_verified": True,
    "__batch_count_actual": 5
}
```

**크기**: ~300-500 bytes (scheduling_metadata에 따라)

**⚠️ 리스크**: `scheduling_metadata`가 크면 포인터도 비대해짐

---

#### 히스토리 아카이브 참조
```python
{
    "type": "history_archive",
    "s3_path": "s3://bucket/workflow-state/key/history_archive_20260129.json",
    "entry_count": 100,
    "archived_at": "2026-01-29T10:30:45.123456Z"
}
```

**크기**: ~150 bytes

---

#### Map ResultSelector 포인터 (v3 ASL)
```python
{
    "branch_id": "branch_0",
    "status": "COMPLETE",
    "s3_path": "s3://bucket/final_state_path.json"
}
```

**크기**: ~100 bytes per branch

**⚠️ 리스크**: 20+ 브랜치 시 2KB+, 100+ 브랜치 시 10KB+

---

### 3.2 포인터 비대화 리스크 시나리오

#### 🚨 **Scenario 1: Distributed Map with 100 Chunks**
```python
# aggregate_distributed_results에서 생성
'distributed_chunk_summary': {
    'total': 100,
    'succeeded': 95,
    'failed': 5,
    'chunk_results': [
        {'chunk_id': 0, 'status': 'COMPLETE', 's3_path': '...', 'execution_order': 0},
        {'chunk_id': 1, 'status': 'COMPLETE', 's3_path': '...', 'execution_order': 1},
        # ... 최대 10개만 저장 (현재 구현)
    ]
}
```

**현재 크기**: ~1.5KB (10개 제한)
**⚠️ 만약 제한 없으면**: ~15KB (100개 전체)

**결론**: ✅ **현재는 안전** (10개 제한)

---

#### 🚨 **Scenario 2: Parallel Branches with 50 Branches**
```python
# ProcessParallelBranches ResultSelector (v3 ASL)
"branches": [
    {"branch_id": "branch_0", "status": "COMPLETE", "s3_path": "..."},
    {"branch_id": "branch_1", "status": "COMPLETE", "s3_path": "..."},
    # ... 50개
]
```

**포인터 크기**: 50 × 100 bytes = **5KB**

**⚠️ 리스크**: 브랜치 수 증가 시 선형 증가

---

#### 🚨 **Scenario 3: Full State Offload with Large Metadata**
```python
{
    "__s3_offloaded": True,
    "__s3_path": "s3://...",
    "scheduling_metadata": {
        "batch_details": [
            {"batch_id": 0, "size": 100, "priority": 1, ...},
            {"batch_id": 1, "size": 100, "priority": 2, ...},
            # ... 20개 배치
        ]
    }
}
```

**포인터 크기**: ~10-20KB

**⚠️ 리스크**: **포인터 자체가 비대해져 256KB에 근접**

---

### 3.3 제어 필드 제외 목록

```python
# Lines 190-203
CONTROL_FIELDS_NEVER_OFFLOAD = {
    'execution_id',
    'segment_to_run',
    'loop_counter',
    'next_action',
    'status',
    'idempotency_key',
    'state_s3_path',
    'pre_snapshot_s3_path',
    'post_snapshot_s3_path',
    'last_update_time',
    'payload_size_kb'
}
```

**✅ 장점**: 제어 흐름에 필수적인 필드는 오프로딩 안 함

**⚠️ 누락**: `scheduling_metadata`, `batch_count_actual`, `guardrail_verified` 등은 **제외 목록에 없음** → 오프로딩 가능

---

## 4️⃣ 발견된 이슈 및 권장 사항

### 🚨 P0: aggregate_distributed_results에 오프로딩 미적용

**문제**:
```python
# Lines 845-897 (현재 구현)
updated_state['distributed_chunk_summary'] = {
    'total': len(execution_result),
    'succeeded': len(all_outputs),
    'failed': len(failed_segments),
    'chunk_results': chunk_results[:10]  # 처음 10개만
}
```

**리스크**: 
- 실패한 청크 정보(`failed_segments`)가 많으면 256KB 초과 가능
- 전체 `execution_result` 배열이 여전히 메모리에 있음

**해결책**:
```python
# failed_segments도 S3로 오프로드
if len(failed_segments) > 5:
    failed_s3_path = store_to_s3(failed_segments, 
        generate_s3_key(execution_id, 'failed_segments'))
    updated_state['failed_segments_s3_path'] = failed_s3_path
    updated_state['failed_segments'] = failed_segments[:5]  # 샘플만
```

---

### ⚠️ P1: 복구 실패 시 Fallback 부재

**문제**:
```python
def load_from_s3(s3_path: str) -> Any:
    # ...
    except Exception as e:
        logger.error(f"Failed to load from S3 {s3_path}: {e}")
        return None  # ⚠️ 데이터 손실
```

**리스크**:
- S3 일시적 장애 시 워크플로우 데이터 손실
- aggregate_branches에서 None 반환 시 브랜치 결과 누락

**해결책**:
```python
def load_from_s3(s3_path: str, max_retries: int = 3) -> Any:
    for attempt in range(max_retries):
        try:
            # ... 기존 로직
            return json.loads(content)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
                continue
            logger.error(f"Failed to load after {max_retries} attempts: {e}")
            return None
```

---

### ⚠️ P1: 포인터 비대화 (Scenario 3)

**문제**:
```python
# optimize_current_state - Lines 366-377
wrapper = {
    "__s3_offloaded": True,
    "__s3_path": s3_path,
    "__original_size_kb": final_size_kb,
    "guardrail_verified": optimized_state.get('guardrail_verified', False),
    "batch_count_actual": optimized_state.get('batch_count_actual', 1),
    "scheduling_metadata": optimized_state.get('scheduling_metadata', {}),  # ⚠️
}
```

**리스크**: `scheduling_metadata` 자체가 크면 포인터도 비대해짐

**해결책**:
```python
# scheduling_metadata도 간소화
wrapper = {
    "__s3_offloaded": True,
    "__s3_path": s3_path,
    "__original_size_kb": final_size_kb,
    # 제어 필드만 보존
    "guardrail_verified": optimized_state.get('guardrail_verified', False),
    "batch_count_actual": optimized_state.get('batch_count_actual', 1),
    # scheduling_metadata는 요약만
    "scheduling_summary": {
        "total_batches": len(optimized_state.get('scheduling_metadata', {}).get('batch_details', [])),
        "priority": optimized_state.get('scheduling_metadata', {}).get('priority', 1)
    }
}
```

---

### ⚠️ P2: create_snapshot 최적화 부재

**문제**:
```python
# Lines 958-1012
snapshot_data = {
    'snapshot_id': snapshot_id,
    'snapshot_type': snapshot_type,
    'execution_id': execution_id,
    'created_at': datetime.now().isoformat(),
    'state_data': state_data,  # ⚠️ 전체 state_data 저장
    'segment_to_run': state_data.get('segment_to_run', 0),
    'loop_counter': state_data.get('loop_counter', 0)
}
```

**리스크**: state_data가 크면 스냅샷도 커짐 (불필요한 복제)

**해결책**:
```python
# state_data가 이미 S3에 있으면 경로만 참조
snapshot_data = {
    'snapshot_id': snapshot_id,
    'snapshot_type': snapshot_type,
    'execution_id': execution_id,
    'created_at': datetime.now().isoformat(),
    'state_s3_path': state_data.get('state_s3_path'),  # 포인터만
    'segment_to_run': state_data.get('segment_to_run', 0),
    'loop_counter': state_data.get('loop_counter', 0)
}
```

---

## 5️⃣ 최종 평가

### ✅ 강점
1. **오프로딩 커버리지**: 주요 5개 경로 모두 구현
2. **캐싱 최적화**: cached_load_from_s3로 S3 비용 절감
3. **계층적 오프로딩**: 개별 필드 → 전체 상태 → 압축
4. **포인터 크기 관리**: chunk_results 10개 제한 등

### ⚠️ 약점
1. **복구 Fallback 부재**: 재시도 없음, 실패 시 None
2. **일부 경로 미적용**: aggregate_distributed, create_snapshot
3. **포인터 비대화 리스크**: scheduling_metadata 제외 안 됨
4. **에러 처리 불완전**: decompress_data는 예외 전파

### 📊 점수

| 항목 | 점수 | 상세 |
|------|------|------|
| **오프로딩 적용률** | 85/100 | 주요 경로 구현, 일부 경로 미적용 |
| **복구 로직** | 70/100 | 기본 구현, Fallback 부재 |
| **포인터 최적화** | 80/100 | 대부분 적절, 일부 리스크 |
| **에러 처리** | 65/100 | 로깅은 충분, 재시도 부재 |
| **전체 안정성** | 75/100 | 프로덕션 사용 가능, 개선 필요 |

---

## 6️⃣ 권장 개선 사항 (우선순위별)

### 🔴 P0 (즉시 수정)
1. **aggregate_distributed에 오프로딩 추가**
2. **load_from_s3에 재시도 로직 추가**

### 🟡 P1 (단기)
3. **포인터 비대화 방지**: scheduling_metadata 간소화
4. **create_snapshot 최적화**: state_s3_path 참조만 저장

### 🟢 P2 (중기)
5. **decompress_data Fallback**: 실패 시 예외 대신 원본 반환
6. **merge_callback/merge_async 크기 체크**: 대용량 방어

---

**작성자**: GitHub Copilot (Claude Sonnet 4.5)
**다음 단계**: P0 이슈 수정 PR 생성
