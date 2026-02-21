# Analemma OS — 상태 관리 종합 점검 보고서

**작성일**: 2026-02-20
**점검 범위**: 백엔드 워크플로우 실행 파이프라인 전반 (v3.3 아키텍처)
**점검자**: Claude Code (Sonnet 4.6)
**기준 커밋**: `a27b491` (docs: comprehensive v3.3 technical documentation)

---

## 목차

1. [점검 범위 및 방법](#1-점검-범위-및-방법)
2. [아키텍처 요약 (현행)](#2-아키텍처-요약-현행)
3. [발견된 이슈 목록](#3-발견된-이슈-목록)
   - [🔴 CRITICAL — 즉시 수정 필요](#31-critical--즉시-수정-필요)
   - [🟠 HIGH — 운영 전 수정 필요](#32-high--운영-전-수정-필요)
   - [🟡 MODERATE — 단기 내 개선 필요](#33-moderate--단기-내-개선-필요)
   - [🔵 LOW — 코드 품질 개선](#34-low--코드-품질-개선)
4. [SFN 필드·상태값 적합성 검토](#4-sfn-필드상태값-적합성-검토)
5. [로그 스냅샷 파이프라인 검토](#5-로그-스냅샷-파이프라인-검토)
6. [수정 우선순위 및 권고사항](#6-수정-우선순위-및-권고사항)

---

## 1. 점검 범위 및 방법

### 점검 대상 파일

| 파일 | 역할 |
|------|------|
| `src/common/initialize_state_data.py` | 상태 초기화 / Merkle DAG 생성 |
| `src/handlers/core/run_workflow.py` | SFN 트리거 / 설정 분할 |
| `src/handlers/core/segment_runner_handler.py` | SFN Task 진입점 |
| `src/services/execution/segment_runner_service.py` | 세그먼트 실행 핵심 로직 |
| `src/handlers/utils/universal_sync_core.py` | 상태 병합 파이프라인 (USC) |
| `src/common/kernel_protocol.py` | Lambda ↔ ASL 통신 규약 |
| `src/handlers/core/execution_progress_notifier.py` | 실행 로그 / WebSocket / DB 저장 |
| `src/services/state/state_versioning_service.py` | Merkle DAG 상태 버저닝 |
| `src/common/state_hydrator.py` | S3 포인터 수화·탈수 |
| `backend/template.yaml` | 환경변수 및 Lambda 구성 |

### 점검 방법

- 소스코드 정적 분석 (함수 단위 추적, 데이터 흐름 추적)
- 환경변수 선언 vs 실제 사용 간 불일치 비교 (`template.yaml` 교차 검증)
- v3.13 Kernel Protocol 도입 전후 경로 불일치 탐지
- SFN ASL 계약(`ResultSelector`, `ResultPath`) 대비 Lambda 반환 형식 정합성 확인

---

## 2. 아키텍처 요약 (현행)

```
Frontend  →  run_workflow.py  →  [SFN Start]
                                      │
                                InitializeStateData  (initialize_state_data.py)
                                  │  Merkle manifest 생성 (StateVersioningService)
                                  │  SmartStateBag 구성 → S3 오프로드 (StateHydrator)
                                  │  seal_state_bag → {state_data, next_action}
                                      │
                              [SFN Loop: segment_to_run < total_segments]
                                      │
                                SegmentRunner  (segment_runner_handler.py)
                                  │  open_state_bag(event) → flat bag
                                  │  SegmentRunnerService.execute_segment()
                                  │  _finalize_response() → seal_state_bag()
                                  │    USC: flatten_result → merge_logic → optimize_and_offload
                                  │    save_state_delta() (Merkle 버저닝)
                                  │  {state_data: flat_state, next_action}
                                      │
                              ExecutionProgressNotifier  (execution_progress_notifier.py)
                                  │  WebSocket 전송 (DynamoDB GSI → connectionId 조회)
                                  │  _update_execution_status() → DynamoDB + S3 스냅샷
                                      │
                              [Choice: next_action == COMPLETE? → 종료]
```

**핵심 데이터 경로 (v3.13 Kernel Protocol)**:
```
Lambda 반환:   { "state_data": flat_state,  "next_action": "CONTINUE" }
ASL ResultSelector: { "bag.$": "$.Payload.state_data", "next_action.$": "$.Payload.next_action" }
ASL ResultPath "$.state_data":
  → SFN 상태: { "state_data": { "bag": flat_state, "next_action": "..." } }
  → 다음 Lambda 입력: event.state_data.bag = flat_state
```

---

## 3. 발견된 이슈 목록

---

### 3.1 CRITICAL — 즉시 수정 필요

---

#### BUG-01: `initialize_state_data.py:538` — 폴백 없는 하드 실패

**파일**: [initialize_state_data.py:527–543](../backend/src/common/initialize_state_data.py#L527-L543)

**현상**:
```python
try:
    # Merkle Manifest 생성
    manifest_pointer = versioning_service.create_manifest(...)
    manifest_id = manifest_pointer.manifest_id
    ...
except Exception as e:
    logger.error(f"Failed to create Merkle manifest: {e}", exc_info=True)
    # Fallback to legacy mode   ← 주석
    manifest_id = None           ← None 할당

# State Bag Construction
bag = SmartStateBag({}, hydrator=hydrator)

if not manifest_id:
    raise RuntimeError(          ← 무조건 예외 발생 (폴백 없음)
        "Failed to create Merkle DAG manifest. ..."
    )
```

**문제**: `create_manifest()`가 네트워크 오류, DynamoDB 일시적 장애, S3 접근 오류 등 **어떤 이유로든 실패하면** `manifest_id = None` → 즉시 `RuntimeError`. 코드 주석의 "Fallback to legacy mode"와 실제 동작이 완전히 다름.

**영향**: Merkle 관련 AWS 리소스에 일시적 장애가 발생하면 **모든 워크플로우 실행이 완전히 중단**됨. 워크플로우 자체와 무관한 인프라 장애가 사용자 실행을 막음.

**권고**: Legacy 경로(partition_map 기반 직접 저장)로의 실제 폴백 구현이 필요. `_HAS_VERSIONING` 플래그가 존재하지만 상태 초기화에서 실질적으로 무력화되어 있음.

---

#### BUG-02: `segment_runner_service.py:3067` — Bag 중첩 경로 오류 (Merkle Chain 断絶)

**파일**: [segment_runner_service.py:3035–3079](../backend/src/services/execution/segment_runner_service.py#L3035-L3079)

**현상**:
```python
# seal_state_bag 반환 구조:
# sealed_result = {
#   "state_data": flat_merged_state,   ← bag 키 없음 (Lambda 반환 시점)
#   "next_action": "CONTINUE"
# }
# ASL이 Lambda 반환 이후 bag 래핑을 추가함

sealed_result = seal_state_bag(
    base_state=base_state,
    result_delta={'execution_result': execution_result},
    action='sync',
    context=seal_context
)

# ...save_state_delta() 호출 후...
if new_manifest_id:
    sealed_result['state_data']['bag']['current_manifest_id'] = new_manifest_id
    # ↑ KeyError: 'bag' ← state_data는 flat dict, 'bag' 키 없음
```

**문제**: `seal_state_bag → USC`는 `{state_data: flat_state}` 를 반환. `state_data['bag']`는 Lambda가 반환한 이후 ASL `ResultSelector`가 추가하는 구조이므로, **Lambda 코드 내에서는 `state_data['bag']`에 접근할 수 없음**.

이 라인은 `except Exception as e` (line 3077) 내부에서 KeyError가 발생하고 catch되어 **워크플로우는 계속되지만**, `current_manifest_id`가 전파되지 않아 매 세그먼트마다 Merkle Chain 연결이 끊어짐.

**영향**:
- `save_state_delta(previous_manifest_id=None)` — 모든 델타가 ROOT 매니페스트에서 분기됨
- Merkle 무결성 체인 형성 불가 → 이력 추적, 롤백 기능 무효화
- 로그에는 `current_manifest_id 설정 성공`이 아닌 에러 로그가 남아야 하지만, 워크플로우 진행 자체는 됨 → 무음 실패(silent failure)

**권고**:
```python
# 수정 방향: state_data가 flat dict임을 인지하고 최상위에 직접 삽입
if new_manifest_id:
    sealed_result['state_data']['current_manifest_id'] = new_manifest_id
```

---

#### BUG-03: `execution_progress_notifier.py:595` — `new_history_logs` 경로 불일치 (히스토리 소실)

**파일**: [execution_progress_notifier.py:593–624](../backend/src/handlers/core/execution_progress_notifier.py#L593-L624)

**현상**:
USC의 `merge_logic`(universal_sync_core.py:748–752)은 `new_history_logs` 키를 수신하면 이를 `state_history`로 변환하여 저장함:
```python
# universal_sync_core.py merge_logic
if key == 'new_history_logs':
    existing = updated_state.get('state_history', [])
    updated_state['state_history'] = _merge_list_field(existing, value, strategy)
    continue  # ← new_history_logs 키 자체는 state_data에 남지 않음
```

USC 처리 후 flat state에는 `state_history`만 있고 `new_history_logs` 키는 제거됨.

```python
# execution_progress_notifier.py _update_execution_status
new_logs = notification_payload.get('new_history_logs')  # → None
          or inner.get('new_history_logs')                # → None (inner_payload에 이 키 없음)
```

`new_logs`가 항상 `None`이므로 `_merge_history_logs`의 `else` 분기로 빠져 **새 로그 추가 없이** 기존 S3 히스토리를 그대로 재기록.

**영향**:
- 세그먼트 실행 로그가 DynamoDB/S3 히스토리에 누적되지 않음
- 프론트엔드 `CheckpointTimeline`, `ExecutionHistoryInline` 컴포넌트에 실행 이력 미반영
- 실행이 완료되어도 히스토리가 비어있는 것처럼 보임

**권고**:
```python
# _update_execution_status 호출 전, inner_payload를 구성할 때
# state_history에서 new_history_logs를 분리하여 명시적으로 전달하거나,
# full_state 조회 경로를 Kernel Protocol 구조에 맞게 수정:
bag = state_data.get('bag', state_data)  # bag 키 우선, 없으면 flat
state_history = bag.get('state_history', [])
```

---

#### BUG-04: `execution_progress_notifier.py:812` — `state_data` 내부 `state_history` 경로 오류

**파일**: [execution_progress_notifier.py:810–812](../backend/src/handlers/core/execution_progress_notifier.py#L810-L812)

**현상**:
```python
# lambda_handler 내
state_data = payload.get('state_data') or {}
# Kernel Protocol 기준: state_data = {bag: flat_state, next_action: "..."}
# bag 안에 state_history가 있음

inner_payload = {
    ...
    'state_history': payload.get('new_history_logs') or state_data.get('state_history', []),
    # ↑ state_data는 {bag: {...}} 구조이므로 state_history 키 없음 → 항상 []
}
```

**문제**: `state_data.get('state_history', [])` 는 `{bag: flat_state}` 딕셔너리에서 `state_history`를 찾으므로 항상 빈 리스트 반환. 올바른 경로는 `state_data.get('bag', {}).get('state_history', [])`.

**영향**: WebSocket으로 전달되는 `inner_payload.state_history`가 항상 빈 배열 → 프론트엔드 타임라인/체크포인트 뷰가 항상 비어 있음.

**BUG-03과 연계**: BUG-03은 DB 저장 경로, BUG-04는 WebSocket 전송 경로에서 동일한 문제가 발생.

---

### 3.2 HIGH — 운영 전 수정 필요

---

#### BUG-05: MANIFESTS_TABLE 환경변수 3종 분열

**파일**: 다수

| 파일 | 사용 환경변수 | 기본값 |
|------|-------------|--------|
| `initialize_state_data.py:76` | `MANIFESTS_TABLE` | `WorkflowManifests-v3-dev` ✓ |
| `manifest_regenerator.py:52` | `MANIFESTS_TABLE` | `WorkflowManifests-v3-dev` ✓ |
| `segment_runner_service.py:3045` | `MANIFESTS_TABLE` | `StateManifestsV3` ✗ 다름 |
| `save_latest_state.py:96` | `MANIFESTS_TABLE` | `StateManifestsV3` ✗ 다름 |
| `load_latest_state.py:96` | `MANIFESTS_TABLE` | `StateManifestsV3` ✗ 다름 |
| `segment_runner_service.py:1210` | **`WORKFLOW_MANIFESTS_TABLE`** | `WorkflowManifestsV3` ✗ 다른 변수명 |
| `segment_runner_service.py:3481` | **`WORKFLOW_MANIFESTS_TABLE`** | `WorkflowManifestsV3` ✗ 다른 변수명 |
| `merkle_gc_service.py:457` | **`WORKFLOW_MANIFESTS_TABLE`** | `WorkflowManifests-v3-dev` |
| `template.yaml:629` | `MANIFESTS_TABLE: !Ref WorkflowManifestsV3` | → `WorkflowManifests-v3-{stage}` |

**문제**:
- 프로덕션: `MANIFESTS_TABLE` env var가 설정되므로 `MANIFESTS_TABLE`을 쓰는 파일들은 정상
- 그러나 `WORKFLOW_MANIFESTS_TABLE`을 쓰는 세 곳(segment_runner_service:1210, :3481, merkle_gc_service)은 **template.yaml에 이 변수가 없으므로** 항상 하드코딩 기본값(`WorkflowManifestsV3`, `WorkflowManifests-v3-dev`) 사용
- 실제 테이블명이 `WorkflowManifests-v3-prod`라면 이 세 경로는 잘못된 테이블에 접근

**영향**:
- `segment_runner_service.py:1210` — 매니페스트 로딩 실패 → 세그먼트 설정 조회 불가
- `segment_runner_service.py:3481` — 매니페스트 재생성 실패 → 복구 불가
- `merkle_gc_service.py` — GC가 잘못된 테이블에 접근 → 유효 블록 삭제 가능성

**권고**: `WORKFLOW_MANIFESTS_TABLE` 참조를 `MANIFESTS_TABLE`로 통일하거나, `template.yaml`에 `WORKFLOW_MANIFESTS_TABLE` 변수를 추가.

---

#### BUG-06: `should_update_database()` — Kernel Protocol 구조 미반영

**파일**: [execution_progress_notifier.py:253–302](../backend/src/handlers/core/execution_progress_notifier.py#L253-L302)

**현상**:
```python
def should_update_database(payload: dict, state_data: dict) -> bool:
    current_status = payload.get('status', '').upper()
    # state_data는 {bag: flat_state, next_action: ...} 구조
    last_db_update = state_data.get('last_db_update_time', 0)
    # ↑ last_db_update_time은 state_data.bag 안에 있음 → 항상 0 반환
```

**문제**: `state_data`가 Kernel Protocol 이후 `{bag: flat_state}` 구조임에도, `state_data.get('last_db_update_time')` 직접 조회 → 항상 `0` 반환.

**영향**: `DB_UPDATE_INTERVAL` 기반 시간 조건(`current_time - last_db_update >= 30`)이 항상 `True` → SELECTIVE 전략임에도 **매 노티파이어 호출 시 DynamoDB write 발생** (WCU 과다 소비).

---

#### BUG-07: S3 버킷 환경변수 불일치 (`segment_runner_service.py:3041`)

**파일**: [segment_runner_service.py:3040–3042](../backend/src/services/execution/segment_runner_service.py#L3040-L3042)

**현상**:
```python
# segment_runner_service.py _finalize_response 내부
s3_bucket = os.environ.get('S3_BUCKET') or os.environ.get('SKELETON_S3_BUCKET')
```

`template.yaml` SegmentRunnerHandler 환경변수:
```yaml
WORKFLOW_STATE_BUCKET: !If [CreateWorkflowStateBucket, !Ref ...]
SKELETON_S3_BUCKET: !If [CreateWorkflowStateBucket, !Ref ...]  # Globals에서 상속
```

USC(`universal_sync_core.py:74–80`)의 버킷 조회 순서:
```python
_S3_BUCKET = (
    os.environ.get('WORKFLOW_STATE_BUCKET') or
    os.environ.get('S3_BUCKET') or
    os.environ.get('STATE_STORAGE_BUCKET') or ''
)
```

**문제**: `segment_runner_service.py`는 `S3_BUCKET`를 먼저 조회하지만 이 변수는 template.yaml Globals나 SegmentRunnerHandler 환경변수에 명시되어 있지 않음. `SKELETON_S3_BUCKET`은 Globals에서 상속되므로 결국 정상 동작하나, `S3_BUCKET` 우선 조회는 의미없는 코드이며 혼란을 야기.

**추가**: `initialize_state_data.py:353` 는 `WORKFLOW_STATE_BUCKET` → `S3_BUCKET` → `SKELETON_S3_BUCKET` 순서로 3단계 폴백을 사용하는데, 각 Lambda마다 다른 조회 패턴을 사용하면 환경변수 설정 오류 디버깅이 어려움.

---

### 3.3 MODERATE — 단기 내 개선 필요

---

#### BUG-08: `initialize_state_data.py:369` — execution_id와 SFN executionArn 불일치

**파일**: [initialize_state_data.py:368–373](../backend/src/common/initialize_state_data.py#L368-L373)

**현상**:
```python
# initialize_state_data.py
execution_id = raw_input.get('idempotency_key') or raw_input.get('execution_id')
if not execution_id:
    execution_id = f"init-{workflow_id}-{int(time.time())}-{str(uuid.uuid4())[:8]}"
    # 예: "init-wf-abc123-1708425600-f3a9c7b2"
```

실제 SFN executionArn:
```
arn:aws:states:ap-northeast-2:123456789012:execution:WorkflowOrchestrator:abc-def-123
```

**문제**: Merkle manifest가 `init-*` 형식 ID로 생성되지만, `execution_progress_notifier`가 추적하는 `executionArn` 기반 DynamoDB 레코드와 연결고리가 없음.

**영향**: StateVersioningService의 델타 저장 이력이 `run_workflow.py`가 생성한 DynamoDB execution 레코드와 연결되지 않아, 실행 추적 및 롤백 시 Merkle 이력 조회 불가.

---

#### BUG-09: `universal_sync_core.py:1003–1004` — `segment_to_run` 증분 조건 부재

**파일**: [universal_sync_core.py:1002–1004](../backend/src/handlers/utils/universal_sync_core.py#L1002-L1004)

**현상**:
```python
# universal_sync_core.py universal_sync_core
if normalized_delta.get('_increment_segment', False):
    updated_state['segment_to_run'] = int(updated_state.get('segment_to_run', 0)) + 1
```

`_increment_segment` 플래그는 `flatten_result`에서 `action == 'merge_callback'` 또는 `action == 'merge_async'`일 때만 설정됨. 일반 `sync` 액션에서는 `next_segment_to_run` → `segment_to_run`으로 직접 치환.

**문제**: `next_segment_to_run`이 `None`으로 반환되고(완료 시) `_increment_segment` 플래그가 없으면, `segment_to_run`이 현재 값을 유지 → `_compute_next_action`의 COMPLETE 체크에서 `delta.get('segment_to_run') is None` 조건을 만족해야 하지만, delta에서 `segment_to_run`이 없으면 `updated_state`에서의 값(이전 세그먼트 ID)과 `total_segments` 비교로 흐름.

실제 COMPLETE 판정은 세그먼트 러너가 `status: 'COMPLETE'`를 직접 반환하는 것에 의존하며, USC의 숫자 비교는 세컨더리 폴백. 이 경로가 항상 올바르게 작동하는지 **end-to-end 시나리오 테스트가 부재**.

---

#### BUG-10: `prevent_pointer_bloat` — 존재하지 않을 수 있는 `state_data_manager` 의존

**파일**: [universal_sync_core.py:798](../backend/src/handlers/utils/universal_sync_core.py#L798)

**현상**:
```python
def prevent_pointer_bloat(state, idempotency_key):
    if 'failed_segments' in state:
        if len(failed) > 5:
            from .state_data_manager import store_to_s3, generate_s3_key  # lazy import
            try:
                s3_path = store_to_s3(failed, s3_key)
                ...
            except Exception as e:
                logger.warning(...)  # 실패해도 무시
```

**문제**: `state_data_manager.py`는 상단에서 `from .universal_sync_core import universal_sync_core`를 모듈 레벨에서 import. `universal_sync_core.py`도 `state_data_manager`를 함수 내부에서 lazy import. 순환 참조 방지를 위한 lazy 패턴이나, **함수 호출 시점에 순환 초기화가 완료되지 않은 경우** ImportError 발생 가능성 잠재.

---

### 3.4 LOW — 코드 품질 개선

---

#### BUG-11: `run_workflow.py:188–218` — Request Body 이중 파싱

**파일**: [run_workflow.py:186–218](../backend/src/handlers/core/run_workflow.py#L186-L218)

**현상**:
```python
# 첫 번째 파싱 (line 188-198)
parsed_body = None
if event.get('body'):
    try:
        parsed_body = json.loads(event['body'])
        if mock_mode == 'true' and 'test_workflow_config' in parsed_body:
            test_config_to_inject = parsed_body['test_workflow_config']
    except json.JSONDecodeError:
        pass

# ↓ 두 번째 파싱 (line 203-210) — parsed_body 초기화 후 재파싱
parsed_body = None   # ← 리셋
input_data = {}
raw_body = event.get('body')
if raw_body:
    try:
        parsed_body = json.loads(raw_body)  # 동일 body 재파싱
    except ...:
        parsed_body = None
```

**문제**: 기능적 버그는 없으나 `mock_mode` 체크(`os.environ.get('MOCK_MODE', 'false').lower()`)가 두 번째 파싱 전 체크이므로, 첫 번째 파싱의 `mock_mode == 'true'` 조건과 두 번째 파싱 이후의 `mock_mode_enabled` 조건이 서로 다른 표현식을 사용. 불필요한 이중 파싱은 성능 낭비이며 MOCK_MODE 로직의 가독성을 해침.

---

#### BUG-12: `segment_runner_service.py:51` — Circular Import 위험 주석 대비 실제 Import

**파일**: [segment_runner_service.py:51](../backend/src/services/execution/segment_runner_service.py#L51)

**현상**:
```python
# Using generic imports from main handler file as source of truth
from src.handlers.core.main import run_workflow, partition_workflow as _partition_workflow_dynamically, _build_segment_config
```

파일 하단(line 199–212)의 주석에서 이 import 패턴을 "Circular Import 위험"으로 명시하고 제거를 권고하고 있지만, **파일 상단의 모듈 레벨 import는 유지**되어 있음:

```python
# --- Legacy Helper Imports REMOVED (v3.3) ---
# 🚨 [WARNING] 아래 임포트는 Circular Import 위험으로 제거되었습니다.
# REMOVED:
#   from src.handlers.core.main import run_workflow, ...
```

하단 주석은 "제거됨"이라고 하지만 실제로는 상단(line 51)에서 여전히 import되고 있음. 문서와 코드 불일치.

---

## 4. SFN 필드·상태값 적합성 검토

### 4.1 next_action 상태값

USC `_compute_next_action`이 반환하는 값과 ASL Choice 상태에서 기대하는 값의 매핑:

| USC 반환 | ASL 기대 상태 | 적합성 |
|---------|------------|-------|
| `STARTED` | InitialState → SegmentLoop 진입 | ✅ |
| `CONTINUE` | LoopCheck → SegmentRunner 재실행 | ✅ |
| `COMPLETE` | LoopCheck → 완료 분기 | ✅ |
| `PAUSED_FOR_HITP` | WaitForHITP Task | ✅ |
| `FAILED` | 실패 처리 분기 | ✅ |
| `HALTED` | ASL에 별도 분기 필요 여부 확인 필요 | ⚠️ |
| `SIGKILL` | ASL에 별도 분기 필요 여부 확인 필요 | ⚠️ |
| `PARALLEL_GROUP` | 병렬 브랜치 실행 분기 | ✅ |

**`HALTED`, `SIGKILL` 처리**: USC는 이를 반환하나, 실제 ASL Choice 상태에서 이 값을 별도 분기로 처리하는지 확인 필요.

### 4.2 필수 필드 보장 (SFN 256KB 제한 대응)

현행 보호 레이어:
1. `initialize_state_data.py` — 초기화 시 force_offload 적용 (`workflow_config`, `partition_map`, `current_state`, `input`)
2. `USC optimize_and_offload` — 30KB 초과 필드 S3 오프로드
3. `seal_state_bag` — USC 통과 후 크기 검증 로그
4. `segment_runner_handler.py` — 응답 크기 로깅 (250KB 초과 시 에러 로그)

**`CONTROL_FIELDS_NEVER_OFFLOAD` 검증**: USC에서 절대 오프로드하지 않는 필드:
```
execution_id, segment_to_run, segment_id, loop_counter, next_action,
status, idempotency_key, state_s3_path, pre_snapshot_s3_path,
post_snapshot_s3_path, last_update_time, payload_size_kb
```
이 필드들이 ASL Choice 조건에서 직접 참조되는지 template.yaml에서 확인 권고.

### 4.3 Partition Map 접근 경로

초기화 시 `partition_map`은 Merkle manifest에 저장되고, `segment_manifest_pointers`만 bag에 유지됨. 세그먼트 러너는 `manifest_id + segment_index`로 S3에서 segment_config를 로드해야 하나, **`segment_runner_service.py` 에서 실제 manifest 로딩 구현 여부 별도 검증 필요**.

---

## 5. 로그 스냅샷 파이프라인 검토

### 5.1 현행 스냅샷 흐름

```
세그먼트 실행 → _finalize_response()
  → execution_result.new_history_logs = [...]
  → seal_state_bag({execution_result: ...})
  → USC flatten_result(action='sync')
      └→ payload.get('execution_result').get('new_history_logs') → delta.new_history_logs
  → USC merge_logic
      └→ new_history_logs → state_history (dedupe_append)
      └→ new_history_logs 키는 state_data에서 소멸
  → ASL ResultPath: state_data.bag.state_history 에 저장됨

ExecutionProgressNotifier 호출
  → payload = SFN event (state_data.bag 구조)
  → _update_execution_status(notification_payload)
      └→ new_logs = notification_payload.get('new_history_logs')  # None
              or inner.get('new_history_logs')                      # None
      └→ else: current_history = full_state.get('state_history', [])
              full_state = inner.get('state_data')  # None → {}
      └→ full_state.get('state_history', []) → []
      └→ merged_history = []  (히스토리 누락)
```

### 5.2 Glass-Box 복구 경로 (부분적 작동)

`execution_progress_notifier.py:836–903`의 "Light Hydration" 로직:
```python
if target_s3_path and not has_inline_data:
    hydrated_data = s3_client.get_object(...)
    logs = hydrated_data.get('new_history_logs') or hydrated_data.get('state_history')
    if logs:
        inner_payload['new_history_logs'] = logs[-10:]
```

S3에서 `final_state` 또는 `state_s3_path`를 찾아 `new_history_logs` 또는 `state_history`를 추출. 단, 이 경로는:
- `target_s3_path`가 올바르게 전달되어야 함 (`payload.final_state_s3_path` 등)
- `has_inline_data` 조건이 False여야 함

USC 오프로딩이 `final_state`를 S3로 보냈다면 이 경로가 작동할 수 있으나, **`_update_execution_status`의 DB 저장 경로에는 이 Light Hydration 결과가 반영되지 않음** (inner_payload에는 반영되나 `_update_execution_status`는 별도 `db_payload` 사용).

### 5.3 히스토리 최대 항목 수 제한

`execution_progress_notifier.py:598`: `MAX_HISTORY = int(os.environ.get('STATE_HISTORY_MAX_ENTRIES', '50'))`

50개 제한이 있어 장기 실행 워크플로우(50+ 세그먼트)에서는 초기 히스토리가 소실됨. 소실 기준: FIFO (가장 오래된 항목 제거, line 533–534).

---

## 6. 수정 우선순위 및 권고사항

### 우선순위 테이블

| # | 심각도 | 파일 | 위치 | 영향 | 예상 수정 난이도 |
|---|--------|------|------|------|----------------|
| BUG-01 | 🔴 CRITICAL | `initialize_state_data.py` | L538–543 | 모든 워크플로우 초기화 중단 | 중 (폴백 경로 구현) |
| BUG-02 | 🔴 CRITICAL | `segment_runner_service.py` | L3067 | Merkle Chain 断絶 (무음 실패) | 하 (경로 수정 1줄) |
| BUG-03 | 🔴 CRITICAL | `execution_progress_notifier.py` | L595 | 실행 히스토리 DB 저장 소실 | 중 (흐름 재설계) |
| BUG-04 | 🔴 CRITICAL | `execution_progress_notifier.py` | L812 | WebSocket 히스토리 항상 빈 배열 | 하 (경로 수정 1줄) |
| BUG-05 | 🟠 HIGH | 다수 파일 | - | 잘못된 DynamoDB 테이블 접근 | 하 (변수명 통일) |
| BUG-06 | 🟠 HIGH | `execution_progress_notifier.py` | L288 | WCU 과다 소비 (DB 전략 무효) | 하 (경로 수정) |
| BUG-07 | 🟠 HIGH | `segment_runner_service.py` | L3041 | S3 버킷 조회 혼란 | 하 (조회 순서 통일) |
| BUG-08 | 🟡 MODERATE | `initialize_state_data.py` | L369 | Merkle 이력과 실행 레코드 미연결 | 중 |
| BUG-09 | 🟡 MODERATE | `universal_sync_core.py` | L1003 | COMPLETE 판정 E2E 검증 필요 | 중 (테스트) |
| BUG-10 | 🟡 MODERATE | `universal_sync_core.py` | L798 | 순환 import 잠재 위험 | 중 |
| BUG-11 | 🔵 LOW | `run_workflow.py` | L188–218 | 가독성, 이중 파싱 낭비 | 하 |
| BUG-12 | 🔵 LOW | `segment_runner_service.py` | L51 | 주석과 코드 불일치 | 하 |

### 권고사항

#### Phase 1 — 즉시 (BUG-01, 02, 04)
1. **BUG-02 먼저**: 단 한 줄 수정이며 Merkle Chain 연속성에 직결됨
   ```python
   # Before
   sealed_result['state_data']['bag']['current_manifest_id'] = new_manifest_id
   # After
   sealed_result['state_data']['current_manifest_id'] = new_manifest_id
   ```

2. **BUG-04**: `state_data.get('state_history', [])` → `state_data.get('bag', state_data).get('state_history', [])`

3. **BUG-01**: `if not manifest_id:` 블록에서 RuntimeError 대신 실제 legacy 경로 실행

#### Phase 2 — 단기 (BUG-03, 05, 06)
4. **BUG-03**: `_update_execution_status` 호출 시 `new_history_logs`를 명시적으로 전달하는 방식으로 리팩토링. USC가 `state_history`로 변환하기 전 원본 로그를 별도 채널로 전달.

5. **BUG-05**: `WORKFLOW_MANIFESTS_TABLE` → `MANIFESTS_TABLE` 통일 or template.yaml에 변수 추가

6. **BUG-06**: `should_update_database`의 `state_data.get(...)` → `state_data.get('bag', state_data).get(...)`

#### Phase 3 — 중기 (BUG-07, 08, 09, 10)
7. 환경변수 접근 헬퍼 함수 도입으로 버킷/테이블 이름 단일 조회 지점 확보
8. `execution_id` 생명주기 정의: SFN start 후 executionArn을 Merkle 이력에 역연결하는 메커니즘

---

*본 보고서는 정적 분석 기반으로 작성되었습니다. 동적 실행 환경(실제 AWS 환경)에서의 검증을 병행하기를 권고합니다.*
