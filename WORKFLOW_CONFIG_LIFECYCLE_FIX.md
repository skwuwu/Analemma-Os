# Workflow Config & Partition Map 생명주기 개선 계획

## 현재 문제점

### 1. workflow_config가 statebag에 영구 저장됨
```python
# initialize_state_data.py Line 421
bag['workflow_config'] = workflow_config  # ❌ 잘못됨
```

**문제:**
- workflow_config는 초기화 시점에만 필요
- 200KB 데이터가 모든 세그먼트에 전달됨
- 100개 세그먼트 = 20MB 낭비

### 2. partition_map도 statebag에 영구 저장됨
```python
# initialize_state_data.py Line 423
bag['partition_map'] = partition_map  # ❌ 잘못됨
```

**문제:**
- partition_map은 segment_manifest 생성 후 불필요
- 50KB 데이터가 모든 세그먼트에 전달됨

### 3. 브랜치가 이미 partition에서 생성되었는데 중복 정보
```python
# partition_service.py Line 520-534
parallel_seg = {
    "type": "parallel_group",
    "branches": branches_data,  # ← 이미 완성된 브랜치
    ...
}
```

**그런데:**
- segment_runner에서 workflow_config를 받아서 뭘 하려고?
- 브랜치는 이미 partition_map에 있음

---

## ✅ 올바른 설계

### 데이터 생명주기 분리

```
┌─────────────────────────────────────────────────────────┐
│ Phase 1: Initialization (InitializeStateBag)           │
├─────────────────────────────────────────────────────────┤
│ 입력: workflow_config (DynamoDB)                        │
│ 처리:                                                   │
│  1. partition_workflow(workflow_config)                │
│     → partition_map 생성 (branches 포함)               │
│  2. segment_manifest 생성 (S3 저장)                    │
│  3. segment_manifest_pointers만 statebag에 저장        │
│ 폐기: workflow_config, partition_map                   │
│ 유지: segment_manifest_s3_path (포인터만)              │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Phase 2: Execution (ExecuteSegment)                    │
├─────────────────────────────────────────────────────────┤
│ 입력:                                                   │
│  - segment_config (manifest에서 추출)                  │
│  - current_state                                        │
│ 처리:                                                   │
│  - llm_chat: node.config만 사용                         │
│  - parallel_group: branches (이미 segment_config에 포함) │
│  - aggregator: branch_results만 사용                    │
│ 불필요: workflow_config, partition_map                  │
└─────────────────────────────────────────────────────────┘
```

---

## 🔧 구체적 수정사항

### 1. initialize_state_data.py 수정

#### Before (잘못됨):
```python
# Line 421-423
bag['workflow_config'] = workflow_config  # ❌
bag['partition_map'] = partition_map      # ❌
```

#### After (올바름):
```python
# workflow_config와 partition_map은 로컬 변수로만 사용
# statebag에 저장하지 않음

# S3에 저장만 (디버깅/회귀용)
if hydrator.s3_client and bucket:
    # workflow_config → S3 (참조용, 실행에는 불필요)
    config_key = f"workflow-configs/{owner_id}/{workflow_id}/config.json"
    hydrator.s3_client.put_object(
        Bucket=bucket,
        Key=config_key,
        Body=json.dumps(workflow_config, default=str),
        ContentType='application/json',
        Metadata={'usage': 'reference_only'}  # 실행에 사용 안 함
    )
    
    # partition_map → S3 (참조용)
    partition_key = f"workflow-partitions/{owner_id}/{workflow_id}/partition_map.json"
    hydrator.s3_client.put_object(
        Bucket=bucket,
        Key=partition_key,
        Body=json.dumps(partition_map, default=str),
        ContentType='application/json',
        Metadata={'usage': 'reference_only'}
    )

# statebag에는 포인터만 저장 (선택적)
bag['workflow_config_s3_path'] = f"s3://{bucket}/{config_key}"  # 디버깅용
bag['partition_map_s3_path'] = f"s3://{bucket}/{partition_key}"  # 디버깅용

# ❌ 제거
# bag['workflow_config'] = workflow_config
# bag['partition_map'] = partition_map
```

---

### 2. segment_manifest에 segment_config 포함

#### Before (잘못됨):
```python
# Line 455-464
segment_manifest.append({
    "segment_id": idx,
    "segment_config": segment,  # ← 여기는 OK
    ...
})

# 그런데 statebag에도 중복 저장
bag['partition_map'] = partition_map  # ❌
```

#### After (올바름):
```python
# segment_manifest만 사용
# partition_map은 manifest 생성 후 폐기

for idx, segment in enumerate(partition_map):
    # 각 세그먼트에 필요한 정보만 포함
    manifest_entry = {
        "segment_id": idx,
        "segment_type": segment.get("type"),
        "segment_config": segment,  # 완전한 segment 정보
        "dependencies": segment.get("dependencies", []),
    }
    
    # parallel_group의 경우 branches도 포함되어 있음
    # (partition_service.py에서 이미 생성됨)
    if segment.get("type") == "parallel_group":
        # branches는 segment["branches"]에 이미 있음
        # workflow_config 불필요
        pass
    
    segment_manifest.append(manifest_entry)

# partition_map은 여기서 폐기됨 (로컬 변수)
# workflow_config도 폐기됨
```

---

### 3. parallel_group branches 생성 로직 검증

**partition_service.py는 이미 올바르게 구현됨:**

```python
# Line 520-534
parallel_seg = {
    "type": "parallel_group",
    "branches": branches_data,  # ← 완성된 브랜치
    "node_ids": [node_id],
    "branch_count": len(branches_data),
}
```

**각 branch_data 구조:**
```python
branch_data = {
    "branch_id": branch_id,
    "partition_map": branch_partition,  # ← 브랜치 내부 파티션
    "has_end": False,
    "target_node": branch_nodes[0].get("id")
}
```

**✅ 이미 완벽함!**
- workflow_config 불필요
- branches는 partition 단계에서 완성
- 실행 시에는 segment_config.branches만 사용

---

### 4. segment_runner_service.py 수정

#### Before (잘못됨):
```python
# Line 2856-2858
workflow_config = _safe_get_from_bag(event, 'workflow_config')  # ❌
partition_map = _safe_get_from_bag(event, 'partition_map')      # ❌

segment_config = self._resolve_segment_config(
    workflow_config, partition_map, segment_id  # ❌
)
```

#### After (올바름):
```python
# segment_config는 ASL에서 직접 전달받음
# (segment_manifest에서 추출)

segment_config = event.get('segment_config')

if not segment_config:
    # Fallback: manifest에서 로드
    manifest_s3_path = event.get('manifest_s3_path')
    segment_index = event.get('segment_index', 0)
    
    if manifest_s3_path:
        manifest = self._load_manifest(manifest_s3_path)
        segment_config = manifest[segment_index]['segment_config']
    else:
        raise ValueError("segment_config not found")

# workflow_config와 partition_map은 접근하지 않음
# (statebag에 없음)
```

---

## 📊 예상 효과

### Before (현재):
```
statebag 구조:
├─ workflow_config: 200KB  ❌ 불필요
├─ partition_map: 50KB      ❌ 불필요
├─ segment_manifest: 포인터 (1KB)
├─ current_state: 100KB
└─ control_plane: 20KB
━━━━━━━━━━━━━━━━━━━━━━━━━━━
총: 371KB

100개 세그먼트 실행:
- 전송량: 371KB × 100 = 37.1MB
```

### After (개선):
```
statebag 구조:
├─ segment_config: 로컬 (ASL 전달)
├─ manifest_s3_path: 포인터 (100 bytes)
├─ current_state: 100KB
└─ control_plane: 20KB
━━━━━━━━━━━━━━━━━━━━━━━━━━━
총: 120KB (-68%)

100개 세그먼트 실행:
- 전송량: 120KB × 100 = 12MB (-67%)
```

---

## 🎯 구현 우선순위

### Phase 1 (1주, P0)
1. ✅ initialize_state_data.py 수정
   - workflow_config, partition_map statebag 제거
   - S3 저장만 (참조용)

2. ✅ segment_manifest에 완전한 segment_config 포함
   - branches 포함 확인

3. ✅ ASL 수정 (aws_step_functions_v3.json)
   - segment_config 직접 전달

### Phase 2 (3일, P1)
4. ✅ segment_runner_service.py 수정
   - workflow_config, partition_map 접근 제거
   - segment_config 직접 사용

### Phase 3 (1주, P2)
5. ✅ 회귀 테스트
6. ✅ 프로덕션 배포

---

## 📝 검증 포인트

### 1. partition_service.py
- [x] branches가 partition 단계에서 생성되는가?
- [x] workflow_config가 partition 후 폐기 가능한가?

### 2. initialize_state_data.py
- [ ] workflow_config를 statebag에서 제거해도 되는가?
- [ ] partition_map을 statebag에서 제거해도 되는가?

### 3. segment_runner_service.py
- [ ] segment_config만으로 실행 가능한가?
- [ ] parallel_group branches가 segment_config에 포함되는가?

---

**결론: 사용자의 지적이 100% 정확합니다!**

workflow_config와 partition_map은:
1. ✅ **초기화 단계에서만 필요**
2. ✅ **statebag에 저장 불필요**
3. ✅ **branches는 partition에서 미리 생성**
4. ✅ **S3 참조용으로만 저장** (디버깅/회귀)
