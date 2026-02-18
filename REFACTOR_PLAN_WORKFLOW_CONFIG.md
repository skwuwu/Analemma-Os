# Workflow Config & Partition Map 제거 리팩토링 계획

## 🎯 목표

**현재 (문제):**
```
Initialize → StateBag
├─ workflow_config: 200KB  ← 모든 세그먼트로 전달
├─ partition_map: 50KB     ← 모든 세그먼트로 전달
├─ current_state: 100KB
└─ segment_to_run: 0

Execute Segment 0
├─ workflow_config: 200KB  ← 불필요
├─ partition_map: 50KB     ← 불필요
└─ segment_config: segment_runner._resolve_segment_config() 동적 생성

Execute Segment 1
├─ workflow_config: 200KB  ← 불필요
├─ partition_map: 50KB     ← 불필요
...
```

**개선 (목표):**
```
Initialize → StateBag
├─ segment_manifest_s3_path: "s3://bucket/manifest.json"  ← 포인터만
├─ current_state: 100KB
└─ segment_to_run: 0

Execute Segment 0
├─ segment_config: manifest[0].segment_config  ← ASL에서 직접 전달
└─ current_state: 100KB

Execute Segment 1
├─ segment_config: manifest[1].segment_config  ← ASL에서 직접 전달
└─ current_state: 100KB
```

---

## 📋 Phase 1: initialize_state_data.py 수정 (P0)

### 현재 코드:
```python
# Line 421-423
bag['workflow_config'] = workflow_config  # ❌ 제거 대상
bag['partition_map'] = partition_map      # ❌ 제거 대상
```

### 수정 후:
```python
# workflow_config와 partition_map은 로컬 변수로만 사용
# statebag에 저장하지 않음

# (Optional) S3에 참조용으로만 저장 (디버깅/회귀 분석용)
if hydrator.s3_client and bucket:
    # workflow_config → S3 (metadata only)
    config_s3_key = f"workflow-metadata/{owner_id}/{workflow_id}/config.json"
    hydrator.s3_client.put_object(
        Bucket=bucket,
        Key=config_s3_key,
        Body=json.dumps(workflow_config, default=str),
        ContentType='application/json',
        Metadata={
            'usage': 'debugging_only',  # 실행에는 사용 안 함
            'workflow_id': workflow_id
        }
    )
    logger.info(f"Stored workflow_config to S3 for reference: s3://{bucket}/{config_s3_key}")

# statebag에는 manifest path만 저장 (이미 Line 457에서 저장됨)
# ✓ bag['segment_manifest_s3_path'] = manifest_s3_path (already exists)

# ❌ 제거
# bag['workflow_config'] = workflow_config
# bag['partition_map'] = partition_map
```

**변경 파일:** [initialize_state_data.py](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\common\\initialize_state_data.py#L421-L423)

---

## 📋 Phase 2: ASL 수정 - segment_config 직접 전달 (P0)

### 현재 ASL:
```json
{
  "ExecuteSegment": {
    "Type": "Task",
    "Resource": "arn:aws:lambda:...:function:ExecuteSegment",
    "Parameters": {
      "state_data.$": "$.state_data",  // ← workflow_config, partition_map 포함
      "segment_index.$": "$.segment_index"
    }
  }
}
```

### 수정 후 ASL:
```json
{
  "ExecuteSegment": {
    "Type": "Task",
    "Resource": "arn:aws:lambda:...:function:ExecuteSegment",
    "Parameters": {
      "state_data.$": "$.state_data",
      "segment_index.$": "$.segment_index",
      
      // ✅ segment_config 직접 전달
      "segment_config.$": "States.ArrayGetItem(
        States.JsonToArray(
          States.StringToJson(
            States.ArrayGetItem(
              States.JsonToArray($.segment_manifest),
              $.segment_index
            )
          )
        ),
        'segment_config'
      )"
    }
  }
}
```

**또는 더 간단하게:**
```json
{
  "ExecuteSegment": {
    "Type": "Task",
    "Resource": "arn:aws:lambda:...:function:ExecuteSegment",
    "Parameters": {
      "state_data.$": "$.state_data",
      "segment_index.$": "$.segment_index",
      
      // Lambda에서 manifest를 로드하도록 경로만 전달
      "segment_manifest_s3_path.$": "$.segment_manifest_s3_path"
    },
    "ResultPath": "$.segment_result"
  }
}
```

**변경 파일:** 
- [aws_step_functions_v3.json](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\aws_step_functions_v3.json)
- [aws_step_functions_distributed_v3.json](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\aws_step_functions_distributed_v3.json)

---

## 📋 Phase 3: segment_runner_service.py 수정 (P0)

### 3.1 execute_segment() 메서드 수정

**현재 코드 (Line 2856-2919):**
```python
# ❌ workflow_config와 partition_map 추출
workflow_config = _safe_get_from_bag(event, 'workflow_config')
partition_map = _safe_get_from_bag(event, 'partition_map')

# ❌ _resolve_segment_config() 동적 호출
segment_config = self._resolve_segment_config(workflow_config, partition_map, segment_id)
```

**수정 후:**
```python
# ✅ segment_config 직접 사용
segment_config = event.get('segment_config')

if not segment_config:
    # Fallback: manifest에서 로드
    manifest_s3_path = event.get('segment_manifest_s3_path')
    segment_index = event.get('segment_index', segment_id)
    
    if manifest_s3_path:
        segment_config = self._load_segment_config_from_manifest(
            manifest_s3_path,
            segment_index
        )
    else:
        raise ValueError(f"segment_config not provided and no manifest path available")

# workflow_config와 partition_map은 접근하지 않음
# (statebag에 없음)
```

### 3.2 새 메서드 추가: `_load_segment_config_from_manifest()`

```python
def _load_segment_config_from_manifest(self, manifest_s3_path: str, segment_index: int) -> dict:
    """
    S3에서 segment_manifest를 로드하고 특정 segment_config를 추출
    
    Args:
        manifest_s3_path: s3://bucket/path/to/manifest.json
        segment_index: 세그먼트 인덱스
        
    Returns:
        segment_config: {nodes: [], edges: [], type: "sequential", ...}
    """
    try:
        import boto3
        s3 = boto3.client('s3')
        
        # S3 경로 파싱
        bucket_name = manifest_s3_path.replace("s3://", "").split("/")[0]
        key_name = "/".join(manifest_s3_path.replace("s3://", "").split("/")[1:])
        
        logger.info(f"Loading segment_manifest from S3: {manifest_s3_path}")
        
        # S3에서 manifest 로드
        obj = s3.get_object(Bucket=bucket_name, Key=key_name)
        content = obj['Body'].read().decode('utf-8')
        manifest = self._safe_json_load(content)
        
        # segment_config 추출
        if not isinstance(manifest, list):
            raise ValueError(f"Invalid manifest format: expected list, got {type(manifest)}")
        
        if not (0 <= segment_index < len(manifest)):
            raise ValueError(f"segment_index {segment_index} out of range (manifest has {len(manifest)} segments)")
        
        segment_entry = manifest[segment_index]
        
        # segment_config 추출 (nested 구조)
        if 'segment_config' in segment_entry:
            segment_config = segment_entry['segment_config']
        else:
            # Fallback: segment_entry 자체가 config
            segment_config = segment_entry
        
        logger.info(f"Loaded segment_config for segment {segment_index}: "
                   f"type={segment_config.get('type')}, "
                   f"nodes={len(segment_config.get('nodes', []))}")
        
        return segment_config
        
    except Exception as e:
        logger.error(f"Failed to load segment_config from manifest: {e}", exc_info=True)
        raise
```

### 3.3 `_resolve_segment_config()` 메서드 제거

**현재 (Line 3739-3827):**
```python
def _resolve_segment_config(self, workflow_config, partition_map, segment_id):
    # ❌ 이 메서드 전체 제거 또는 deprecated 표시
    pass
```

**이유:**
- partition_map이 이미 완성된 segment_config를 가지고 있음
- workflow_config는 partition 단계에서만 필요
- 실행 단계에서는 segment_config만 필요

**변경 파일:** [segment_runner_service.py](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\services\\execution\\segment_runner_service.py#L2856-L2919)

---

## 📋 Phase 4: parallel_group branches 검증 (P1)

### partition_service.py 확인

**현재 코드는 이미 올바름 (Line 520-534):**
```python
parallel_seg = {
    "type": "parallel_group",
    "branches": branches_data,  # ← 완성된 브랜치
    "node_ids": [node_id],
    "branch_count": len(branches_data),
}

branches_data = [
    {
        "branch_id": branch_id,
        "partition_map": branch_partition,  # ← 브랜치 내부 파티션
        "has_end": False,
        "target_node": branch_nodes[0].get("id")
    }
    for branch in branches_data
]
```

**✅ 이미 완벽함!**
- workflow_config 없이도 branches 생성 가능
- partition 단계에서 이미 완성
- 실행 단계에서는 segment_config.branches만 사용

**변경 불필요:** partition_service.py는 이미 올바르게 구현됨

---

## 📋 Phase 5: 회귀 테스트 (P1)

### 테스트 케이스

1. **기본 워크플로우 (sequential)**
   ```python
   workflow = {
       "nodes": [
           {"id": "node1", "type": "llm_chat", ...},
           {"id": "node2", "type": "llm_chat", ...}
       ],
       "edges": [{"source": "node1", "target": "node2"}]
   }
   ```
   - ✅ segment_config가 ASL에서 전달되는지
   - ✅ workflow_config가 statebag에 없는지
   - ✅ 실행 성공

2. **parallel_group 워크플로우**
   ```python
   workflow = {
       "nodes": [
           {"id": "parallel1", "type": "parallel_group", "branches": [...]}
       ]
   }
   ```
   - ✅ branches가 segment_config에 포함되는지
   - ✅ workflow_config 없이 실행되는지
   - ✅ 브랜치 결과 aggregation 성공

3. **대용량 워크플로우 (100+ nodes)**
   - ✅ 페이로드 크기 감소 확인 (400KB → 130KB)
   - ✅ Lambda 메모리 사용량 감소

---

## 📊 예상 효과

### Before (현재):
```
Initialize:
├─ workflow_config: 200KB → S3
├─ partition_map: 50KB → S3
├─ segment_manifest: 50KB → S3
└─ statebag: 370KB
    ├─ workflow_config: 200KB  ❌
    ├─ partition_map: 50KB     ❌
    ├─ current_state: 100KB
    └─ control_plane: 20KB

Segment 0:
└─ statebag: 370KB  ← 불필요한 250KB 포함

Segment 1:
└─ statebag: 370KB  ← 불필요한 250KB 포함

...
Segment 99:
└─ statebag: 370KB  ← 불필요한 250KB 포함

━━━━━━━━━━━━━━━━━━━━━━━━━━━
총 전송량: 370KB × 100 = 37MB
```

### After (개선):
```
Initialize:
├─ workflow_config: 200KB → S3 (참조용)
├─ partition_map: 50KB → (로컬 폐기)
├─ segment_manifest: 50KB → S3
└─ statebag: 120KB
    ├─ segment_manifest_s3_path: 0.1KB  ✅
    ├─ current_state: 100KB
    └─ control_plane: 20KB

Segment 0:
├─ segment_config: 10KB  ← ASL 직접 전달
└─ statebag: 120KB

Segment 1:
├─ segment_config: 10KB  ← ASL 직접 전달
└─ statebag: 120KB

...
Segment 99:
├─ segment_config: 10KB  ← ASL 직접 전달
└─ statebag: 120KB

━━━━━━━━━━━━━━━━━━━━━━━━━━━
총 전송량: 130KB × 100 = 13MB (-65%)
```

---

## 🚀 구현 우선순위

### Week 1 (P0 - Critical)
- [ ] Day 1-2: Phase 1 - initialize_state_data.py 수정
  - [ ] workflow_config, partition_map statebag 제거
  - [ ] S3 참조 저장 추가 (선택)
  
- [ ] Day 3-4: Phase 2 - ASL 수정
  - [ ] segment_config 직접 전달 방식 구현
  - [ ] 또는 manifest_s3_path 전달 방식 구현
  
- [ ] Day 5: Phase 3 - segment_runner_service.py 수정
  - [ ] _load_segment_config_from_manifest() 추가
  - [ ] execute_segment() 로직 변경
  - [ ] _resolve_segment_config() deprecated 표시

### Week 2 (P1 - Validation)
- [ ] Day 1-3: Phase 5 - 회귀 테스트
  - [ ] 기본 워크플로우 테스트
  - [ ] parallel_group 테스트
  - [ ] 대용량 워크플로우 테스트
  
- [ ] Day 4-5: 성능 측정 및 모니터링
  - [ ] CloudWatch 메트릭 확인
  - [ ] Lambda 메모리 사용량 분석
  - [ ] 페이로드 크기 검증

### Week 3 (P2 - Production)
- [ ] Day 1-2: Canary 배포
- [ ] Day 3-5: 프로덕션 배포 및 모니터링

---

## ⚠️ 리스크 및 완화 방안

### 1. ASL 변경 실패 시
- **리스크:** ASL에서 segment_config 추출 실패
- **완화:** Fallback으로 manifest_s3_path 사용

### 2. 기존 실행 중인 워크플로우
- **리스크:** statebag에 workflow_config가 없어서 실패
- **완화:** 
  - Phase 3에서 fallback 로직 유지
  - 점진적 마이그레이션 (새 워크플로우만 적용)

### 3. parallel_group 브랜치 실행
- **리스크:** branches 정보 부족
- **완화:** partition_service.py는 이미 올바르게 구현됨

---

## ✅ 완료 체크리스트

### Code Changes
- [ ] initialize_state_data.py Line 421-423 수정
- [ ] segment_runner_service.py Line 2856-2919 수정
- [ ] segment_runner_service.py _load_segment_config_from_manifest() 추가
- [ ] ASL segment_config 전달 로직 추가

### Testing
- [ ] Unit tests 작성
- [ ] Integration tests 실행
- [ ] Performance benchmarks 측정

### Documentation
- [ ] 아키텍처 문서 업데이트
- [ ] API 문서 업데이트
- [ ] Migration guide 작성

### Deployment
- [ ] Dev 환경 배포
- [ ] Staging 환경 검증
- [ ] Production canary 배포
- [ ] Production full 배포

---

## 📝 참고 자료

- [WORKFLOW_CONFIG_LIFECYCLE_FIX.md](WORKFLOW_CONFIG_LIFECYCLE_FIX.md)
- [SEGMENT_PAYLOAD_OPTIMIZATION.md](SEGMENT_PAYLOAD_OPTIMIZATION.md)
- [partition_service.py Line 520-534](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\services\\execution\\partition_service.py#L520-L534)
- [initialize_state_data.py Line 421-423](c:\\Users\\gimgy\\OneDrive\\바탕%20화면\\Analemma-Os\\analemma-workflow-os\\backend\\src\\common\\initialize_state_data.py#L421-L423)
