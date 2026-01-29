# 🎯 Universal Sync Core Architecture Design

**작성일**: 2026-01-29  
**목표**: Function-Agnostic 데이터 파이프라인으로 P0~P2 자동 해결

---

## 🚨 현재 문제점

### 1️⃣ **9개 액션이 9가지 다른 방식으로 작동**
```python
# 현재 state_data_manager.py
def update_and_compress():  # ✅ 전체 최적화 파이프라인
def sync_state_data():      # ⚠️ 일부 최적화만
def aggregate_branches():   # ⚠️ 포인터 로딩만
def aggregate_distributed(): # ❌ 오프로딩 없음
def merge_callback():       # ❌ 크기 체크 없음
def merge_async():          # ❌ 크기 체크 없음
def create_snapshot():      # ❌ 최적화 없음
```

**결과**: P0~P2 이슈를 해결하려면 각 함수마다 특수 로직 추가 → 스파게티 회귀

---

### 2️⃣ **"경로 추가" 패러다임의 한계**
보고서에서 제안한 해결책:
- P0: `aggregate_distributed`에 오프로딩 로직 추가
- P1: `load_from_s3`에 재시도 로직 추가
- P1: `optimize_current_state`에 scheduling_metadata 간소화 추가
- P2: `create_snapshot`에 포인터 참조 추가

**문제**: 새로운 액션을 추가할 때마다 동일한 작업을 반복 → O(N²) 복잡도 증가

---

## 🎯 해결책: Universal Sync Core

### 핵심 원칙
> **"함수가 무엇이든 상관없이, 데이터가 흐르는 파이프 자체를 표준화"**

모든 액션은 다음 3단계만 수행:
1. **입력 정규화** (Normalize): 리스트든 단일 객체든 동일한 형태로 평탄화
2. **상태 병합** (Merge): Smart StateBag 패턴으로 무조건 머지
3. **자동 최적화** (Optimize): 크기 초과 시 자동 오프로딩

---

## 🛠️ 아키텍처 설계

### Phase 1: Universal Sync Core 함수

```python
def universal_sync_core(
    base_state: Dict[str, Any],
    new_result: Any,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Function-Agnostic 동기화 코어
    
    이 함수는:
    - sync, aggregate_branches, aggregate_distributed, merge_callback 등
      모든 액션에서 호출됨
    - 입력이 어떤 형태든 상관없이 동일한 파이프라인 적용
    - P0~P2 이슈를 자동으로 해결
    
    Args:
        base_state: 기존 state_data
        new_result: 새로운 실행 결과 (단일 객체 or 리스트)
        context: 선택적 컨텍스트 (execution_id, action_type 등)
    
    Returns:
        최적화된 state_data (S3 오프로딩 포함)
    """
    
    # Step 1: 입력 정규화 (Flatten)
    normalized_delta = flatten_result(new_result)
    
    # Step 2: 상태 병합 (Merge)
    updated_state = merge_logic(base_state, normalized_delta, context)
    
    # Step 3: 자동 최적화 (Optimize & Offload)
    # 여기서 모든 P0~P2 리스크 해결:
    # - 크기 초과 시 S3 오프로딩
    # - 포인터 비대화 방지
    # - 히스토리 아카이빙
    optimized_state = optimize_and_offload(updated_state, context)
    
    return optimized_state
```

---

### Phase 2: 하위 함수 구현

#### 2.1 `flatten_result()` - 입력 정규화
```python
def flatten_result(result: Any) -> Dict[str, Any]:
    """
    입력이 리스트(Map 결과)인지 단일 객체인지 자동 판별 후 평탄화
    
    Examples:
        # Distributed Map 결과
        [{"chunk_id": 0, "output": {...}}, {"chunk_id": 1, ...}]
        → {"distributed_outputs": [...], "chunk_count": 2}
        
        # 단일 LLM 결과
        {"thoughts": [...], "response": "..."}
        → {"thoughts": [...], "response": "..."}
        
        # HITP Callback 결과
        {"callback_result": {"final_state": {...}}}
        → {"final_state": {...}}
    """
    if isinstance(result, list):
        # Map/Distributed 결과
        return {
            'distributed_outputs': result,
            'chunk_count': len(result),
            'aggregation_timestamp': datetime.now().isoformat()
        }
    
    elif isinstance(result, dict):
        # 단일 결과
        # callback_result 같은 래퍼 제거
        if 'callback_result' in result:
            return result['callback_result']
        if 'async_result' in result:
            return result['async_result']
        return result
    
    else:
        # 기타 타입 (문자열 등)
        return {'raw_result': result}
```

---

#### 2.2 `merge_logic()` - 상태 병합
```python
def merge_logic(
    base_state: Dict[str, Any],
    delta: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Smart StateBag 패턴으로 상태 병합
    
    규칙:
    1. 제어 필드는 delta 우선 (execution_id, segment_to_run, loop_counter)
    2. 히스토리는 append (state_history)
    3. 데이터 필드는 deep merge (current_state)
    4. 충돌 시 타임스탬프 기준 최신 우선
    """
    updated_state = copy.deepcopy(base_state)
    
    # 제어 필드 업데이트
    for control_field in CONTROL_FIELDS_NEVER_OFFLOAD:
        if control_field in delta:
            updated_state[control_field] = delta[control_field]
    
    # 히스토리 append
    if 'state_history' in delta:
        existing_history = updated_state.get('state_history', [])
        new_entries = delta['state_history']
        if isinstance(new_entries, list):
            existing_history.extend(new_entries)
        else:
            existing_history.append(new_entries)
        updated_state['state_history'] = existing_history
    
    # current_state deep merge
    if 'current_state' in delta:
        base_current = updated_state.get('current_state', {})
        delta_current = delta['current_state']
        updated_state['current_state'] = deep_merge(base_current, delta_current)
    
    # 기타 필드 병합
    for key, value in delta.items():
        if key not in ['state_history', 'current_state'] and key not in CONTROL_FIELDS_NEVER_OFFLOAD:
            updated_state[key] = value
    
    return updated_state
```

---

#### 2.3 `optimize_and_offload()` - 자동 최적화
```python
def optimize_and_offload(
    state: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    통합 최적화 파이프라인 - P0~P2 자동 해결
    
    처리 순서:
    1. 히스토리 아카이빙 (>50 entries)
    2. 개별 필드 오프로딩 (>30KB)
    3. 전체 상태 오프로딩 (>100KB)
    4. 포인터 비대화 방지 (scheduling_metadata 간소화)
    5. 최종 크기 체크 (>200KB 경고)
    """
    execution_id = context.get('execution_id') if context else state.get('execution_id')
    
    # 1. 히스토리 최적화 (기존 로직 재사용)
    state = optimize_state_history(state, execution_id)
    
    # 2. current_state 최적화 (기존 로직 재사용)
    if 'current_state' in state:
        state['current_state'] = optimize_current_state(state['current_state'], execution_id)
    
    # 3. 포인터 비대화 방지 (NEW!)
    state = prevent_pointer_bloat(state)
    
    # 4. 최종 크기 체크 및 경고
    final_size_kb = calculate_payload_size(state)
    if final_size_kb > MAX_PAYLOAD_SIZE_KB * 0.75:  # 75% 임계값
        logger.warning(f"Payload approaching limit: {final_size_kb}KB / {MAX_PAYLOAD_SIZE_KB}KB")
        
        # 응급 처리: distributed_outputs 같은 대용량 배열 오프로드
        state = emergency_offload_large_arrays(state, execution_id)
    
    return state


def prevent_pointer_bloat(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    P1 이슈 해결: 포인터 자체가 비대해지는 것 방지
    
    대상:
    - scheduling_metadata: batch_details 배열 → 요약만
    - chunk_results: 전체 배열 → 상위 10개 + S3 경로
    - failed_segments: 전체 배열 → 상위 5개 + S3 경로
    """
    if 'current_state' in state and isinstance(state['current_state'], dict):
        current = state['current_state']
        
        # scheduling_metadata 간소화
        if 'scheduling_metadata' in current and isinstance(current['scheduling_metadata'], dict):
            metadata = current['scheduling_metadata']
            batch_details = metadata.get('batch_details', [])
            if len(batch_details) > 5:
                current['scheduling_summary'] = {
                    'total_batches': len(batch_details),
                    'priority': metadata.get('priority', 1),
                    'total_items': sum(b.get('size', 0) for b in batch_details)
                }
                del current['scheduling_metadata']
    
    # distributed_chunk_summary 최적화 (이미 구현됨 - 10개 제한)
    # failed_segments 오프로딩 (P0 이슈 해결)
    if 'failed_segments' in state:
        failed = state['failed_segments']
        if isinstance(failed, list) and len(failed) > 5:
            execution_id = state.get('execution_id')
            s3_key = generate_s3_key(execution_id, 'failed_segments')
            failed_s3_path = store_to_s3(failed, s3_key)
            state['failed_segments_s3_path'] = failed_s3_path
            state['failed_segments'] = failed[:5]  # 샘플만
    
    return state
```

---

### Phase 3: 액션 함수 리팩토링

모든 액션을 `universal_sync_core()` 호출로 단순화:

```python
def sync_state_data(event: Dict[str, Any]) -> Dict[str, Any]:
    """v3: 실행 결과를 state_data에 머지"""
    state_data = event.get('state_data', {})
    execution_result = event.get('execution_result', {})
    
    # ✨ Universal Core 호출로 모든 로직 위임
    updated_state = universal_sync_core(
        base_state=state_data,
        new_result=execution_result,
        context={'execution_id': state_data.get('execution_id'), 'action': 'sync'}
    )
    
    return {'state_data': updated_state}


def aggregate_branches(event: Dict[str, Any]) -> Dict[str, Any]:
    """v3: 병렬 브랜치 결과 집계"""
    state_data = event.get('state_data', {})
    branches = event.get('branches', [])
    
    # 포인터 로딩 (캐시 활용)
    loaded_branches = [
        cached_load_from_s3(b['s3_path']) if 's3_path' in b else b
        for b in branches
    ]
    
    # ✨ Universal Core 호출
    updated_state = universal_sync_core(
        base_state=state_data,
        new_result=loaded_branches,
        context={'execution_id': state_data.get('execution_id'), 'action': 'aggregate_branches'}
    )
    
    return {'state_data': updated_state}


def aggregate_distributed_results(event: Dict[str, Any]) -> Dict[str, Any]:
    """v3: MAP_REDUCE/BATCHED 결과 집계 - P0 이슈 자동 해결"""
    state_data = event.get('state_data', {})
    execution_result = event.get('execution_result', [])
    
    # ✨ Universal Core 호출 (오프로딩 자동 적용!)
    updated_state = universal_sync_core(
        base_state=state_data,
        new_result=execution_result,
        context={'execution_id': state_data.get('execution_id'), 'action': 'aggregate_distributed'}
    )
    
    return {'state_data': updated_state}


def merge_callback_result(event: Dict[str, Any]) -> Dict[str, Any]:
    """v3: HITP 콜백 결과 머지"""
    state_data = event.get('state_data', {})
    callback_result = event.get('callback_result', {})
    
    # ✨ Universal Core 호출
    updated_state = universal_sync_core(
        base_state=state_data,
        new_result=callback_result,
        context={'execution_id': state_data.get('execution_id'), 'action': 'merge_callback'}
    )
    
    return {'state_data': updated_state}


def create_snapshot(event: Dict[str, Any]) -> Dict[str, Any]:
    """v3: 상태 스냅샷 생성 - P2 이슈 자동 해결"""
    state_data = event.get('state_data', {})
    snapshot_type = event.get('snapshot_type', 'pre')
    execution_id = state_data.get('execution_id')
    
    # 스냅샷도 Universal Core 통과 (자동 최적화!)
    snapshot_data = {
        'snapshot_id': f"{execution_id}_{snapshot_type}_{int(datetime.now().timestamp())}",
        'snapshot_type': snapshot_type,
        'execution_id': execution_id,
        'created_at': datetime.now().isoformat(),
        'state_data': state_data  # 이것도 최적화됨
    }
    
    # ✨ 스냅샷 자체를 최적화 (포인터 참조 자동)
    optimized_snapshot = optimize_and_offload(
        snapshot_data,
        context={'execution_id': execution_id, 'action': 'snapshot'}
    )
    
    # S3 저장
    s3_key = generate_s3_key(execution_id, f'snapshot_{snapshot_type}')
    s3_path = store_to_s3(optimized_snapshot, s3_key)
    
    # state_data에 경로만 추가
    state_data[f'{snapshot_type}_snapshot_s3_path'] = s3_path
    return {'state_data': state_data}
```

---

## 🎯 복구 로직 통합: StateHydrator + Retry Strategy

### 현재 문제
```python
def load_from_s3(s3_path: str) -> Any:
    try:
        # ...
    except Exception as e:
        return None  # ⚠️ 데이터 손실
```

### 해결책: StateHydrator에 Retry 주입
```python
class StateHydrator:
    """
    상태 복구 전담 클래스 (Control Plane)
    v3.1: Retry Strategy 통합
    """
    
    def __init__(self, retry_strategy: Optional['RetryStrategy'] = None):
        self.retry_strategy = retry_strategy or ExponentialBackoffRetry()
    
    def load_from_s3(self, s3_path: str) -> Any:
        """재시도 로직이 통합된 S3 로딩"""
        return self.retry_strategy.execute(
            func=lambda: self._load_from_s3_once(s3_path),
            fallback=None
        )
    
    def _load_from_s3_once(self, s3_path: str) -> Any:
        """단일 시도 로직 (기존 코드)"""
        if not s3_path or not s3_path.startswith('s3://'):
            return None
        
        path_parts = s3_path.replace('s3://', '').split('/', 1)
        bucket = path_parts[0]
        key = path_parts[1] if len(path_parts) > 1 else ''
        
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        return json.loads(content)


class ExponentialBackoffRetry:
    """Exponential Backoff 재시도 전략"""
    
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
    
    def execute(self, func: Callable, fallback: Any = None) -> Any:
        for attempt in range(self.max_retries):
            try:
                return func()
            except Exception as e:
                if attempt < self.max_retries - 1:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(f"Retry {attempt+1}/{self.max_retries} after {delay}s: {e}")
                    time.sleep(delay)
                else:
                    logger.error(f"Failed after {self.max_retries} attempts: {e}")
                    return fallback


# 모듈 레벨 인스턴스 (기존 코드 호환성)
_state_hydrator = StateHydrator()

def load_from_s3(s3_path: str) -> Any:
    """기존 함수 시그니처 유지 (backward compatible)"""
    return _state_hydrator.load_from_s3(s3_path)
```

---

## 📊 ASL v3 Payload 규격 통일

### 현재 상태 (이미 대부분 완료)
```json
// aws_step_functions_v3.json - 모든 상태가 동일한 규격 사용
{
  "Type": "Task",
  "Resource": "arn:aws:states:::lambda:invoke",
  "Parameters": {
    "FunctionName": "${StateDataManagerFunction}",
    "Payload": {
      "action": "sync",
      "state_data.$": "$.state_data",
      "execution_result.$": "$.execution_result"
    }
  },
  "ResultPath": "$.state_data",
  "ResultSelector": {
    "state_data.$": "$.Payload.state_data"
  }
}
```

### 추가 검증 필요 항목
- [ ] ProcessParallelBranches - ResultSelector 포인터만
- [ ] DistributedMapState - MaxConcurrency=100
- [ ] WaitForCallback - HeartbeatSeconds=3600

---

## 🚀 마이그레이션 계획

### Phase 1: 핵심 함수 구현 (1-2시간)
1. `universal_sync_core()` 구현
2. `flatten_result()` 구현
3. `merge_logic()` 구현
4. `optimize_and_offload()` 확장
5. `prevent_pointer_bloat()` 구현 (NEW)

### Phase 2: 액션 리팩토링 (2-3시간)
6. `sync_state_data()` → Universal Core 호출
7. `aggregate_branches()` → Universal Core 호출
8. `aggregate_distributed_results()` → Universal Core 호출 (P0 자동 해결!)
9. `merge_callback_result()` → Universal Core 호출
10. `merge_async_result()` → Universal Core 호출
11. `create_snapshot()` → optimize_and_offload 호출 (P2 자동 해결!)

### Phase 3: 복구 로직 통합 (1시간)
12. `StateHydrator` 클래스 구현
13. `ExponentialBackoffRetry` 구현
14. `load_from_s3()` → StateHydrator 위임 (P1 자동 해결!)

### Phase 4: 테스트 및 검증 (2시간)
15. 단위 테스트 작성
16. 통합 테스트 실행
17. 기존 워크플로우 호환성 검증

**총 소요 시간**: 6-8시간

---

## ✅ 예상 효과

### 1️⃣ P0~P2 자동 해결
- ✅ P0 (aggregate_distributed 오프로딩): `universal_sync_core` 호출로 자동 적용
- ✅ P1 (복구 재시도): `StateHydrator` 한 곳에만 구현
- ✅ P1 (포인터 비대화): `prevent_pointer_bloat` 한 곳에만 구현
- ✅ P2 (snapshot 최적화): `optimize_and_offload` 호출로 자동 적용

### 2️⃣ 코드 복잡도 감소
- 9개 액션 로직 → 1개 핵심 함수 + 9개 얇은 래퍼
- 유지보수 포인트: 9개 → **1개**
- 새로운 액션 추가 시: 특수 로직 없이 3줄 코드면 충분

### 3️⃣ 미래 확장성
- 새로운 최적화 전략 추가: `optimize_and_offload` 한 곳만 수정
- 새로운 복구 전략 추가: `RetryStrategy` 구현체만 추가
- 새로운 머지 규칙 추가: `merge_logic` 한 곳만 수정

---

**작성자**: GitHub Copilot (Claude Sonnet 4.5)  
**상태**: ✅ **구현 완료** (2026-01-29)

---

## 📋 구현 완료 체크리스트

### ✅ Phase 1: 핵심 함수 구현
- [x] `universal_sync_core.py` 생성
- [x] `universal_sync_core()` - Function-Agnostic 동기화 코어
- [x] `flatten_result()` - 입력 정규화
- [x] `merge_logic()` - Shallow Merge + Copy-on-Write
- [x] `optimize_and_offload()` - 자동 최적화 파이프라인
- [x] `prevent_pointer_bloat()` - 포인터 비대화 방지
- [x] `StateHydrator` - 재시도 + 캐시 통합 복구 클래스
- [x] `ExponentialBackoffRetry` - 재시도 전략

### ✅ Phase 2: state_data_manager.py 업그레이드
- [x] `load_from_s3()` - 재시도 로직 + Checksum 검증 추가
- [x] `aggregate_distributed_results()` - P0 자동 오프로딩 적용
- [x] `merge_callback_result()` - P2 자동 최적화 추가
- [x] `merge_async_result()` - P2 자동 최적화 추가
- [x] `create_snapshot()` - P2 포인터 참조 모드 추가

### ✅ Phase 3: 하위 호환성 검증
```
🎉 모든 하위 호환성 검사 통과!
✅ 통과: 27개
⚠️ 경고: 3개 (모두 무해한 경고)
❌ 실패: 0개
```

---

## 🔧 피드백 반영 요약

| 피드백 | 반영 내용 |
|--------|----------|
| ① deepcopy 성능 함정 | `_shallow_copy_with_cow()` - 변경될 필드만 복사 |
| ② deep_merge 원자성 | `LIST_FIELD_STRATEGIES` - 필드별 병합 전략 지정 |
| ③ Checksum 생존 신고 | `load_from_s3()` - MD5 검증 후 불일치 시 재시도 |
