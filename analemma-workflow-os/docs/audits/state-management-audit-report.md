# Analemma OS — Comprehensive State Management Audit Report

**Date**: 2026-02-20
**Scope**: Full backend workflow execution pipeline (v3.3 architecture)
**Auditor**: Claude Code (Sonnet 4.6)
**Reference Commit**: `a27b491` (docs: comprehensive v3.3 technical documentation)

---

## Table of Contents

1. [Audit Scope and Methodology](#1-audit-scope-and-methodology)
2. [Architecture Summary (Current)](#2-architecture-summary-current)
3. [Discovered Issues](#3-discovered-issues)
   - [CRITICAL — Immediate Fix Required](#31-critical--immediate-fix-required)
   - [HIGH — Fix Before Production](#32-high--fix-before-production)
   - [MODERATE — Short-Term Improvement Needed](#33-moderate--short-term-improvement-needed)
   - [LOW — Code Quality Improvement](#34-low--code-quality-improvement)
4. [SFN Field and State Value Conformance Review](#4-sfn-field-and-state-value-conformance-review)
5. [Log Snapshot Pipeline Review](#5-log-snapshot-pipeline-review)
6. [Fix Priority and Recommendations](#6-fix-priority-and-recommendations)

---

## 1. Audit Scope and Methodology

### Files Audited

| File | Role |
|------|------|
| `src/common/initialize_state_data.py` | State initialization / Merkle DAG creation |
| `src/handlers/core/run_workflow.py` | SFN trigger / config partitioning |
| `src/handlers/core/segment_runner_handler.py` | SFN Task entry point |
| `src/services/execution/segment_runner_service.py` | Core segment execution logic |
| `src/handlers/utils/universal_sync_core.py` | State merge pipeline (USC) |
| `src/common/kernel_protocol.py` | Lambda <-> ASL communication protocol |
| `src/handlers/core/execution_progress_notifier.py` | Execution logs / WebSocket / DB persistence |
| `src/services/state/state_versioning_service.py` | Merkle DAG state versioning |
| `src/common/state_hydrator.py` | S3 pointer hydration/dehydration |
| `backend/template.yaml` | Environment variables and Lambda configuration |

### Methodology

- Static source code analysis (function-level tracing, data flow tracing)
- Environment variable declaration vs. actual usage mismatch comparison (`template.yaml` cross-validation)
- Path inconsistency detection before and after v3.13 Kernel Protocol introduction
- Lambda return format conformance check against SFN ASL contracts (`ResultSelector`, `ResultPath`)

---

## 2. Architecture Summary (Current)

```
Frontend  ->  run_workflow.py  ->  [SFN Start]
                                      |
                                InitializeStateData  (initialize_state_data.py)
                                  |  Merkle manifest creation (StateVersioningService)
                                  |  SmartStateBag construction -> S3 offload (StateHydrator)
                                  |  seal_state_bag -> {state_data, next_action}
                                      |
                              [SFN Loop: segment_to_run < total_segments]
                                      |
                                SegmentRunner  (segment_runner_handler.py)
                                  |  open_state_bag(event) -> flat bag
                                  |  SegmentRunnerService.execute_segment()
                                  |  _finalize_response() -> seal_state_bag()
                                  |    USC: flatten_result -> merge_logic -> optimize_and_offload
                                  |    save_state_delta() (Merkle versioning)
                                  |  {state_data: flat_state, next_action}
                                      |
                              ExecutionProgressNotifier  (execution_progress_notifier.py)
                                  |  WebSocket delivery (DynamoDB GSI -> connectionId lookup)
                                  |  _update_execution_status() -> DynamoDB + S3 snapshot
                                      |
                              [Choice: next_action == COMPLETE? -> Exit]
```

**Core Data Path (v3.13 Kernel Protocol)**:
```
Lambda return:   { "state_data": flat_state,  "next_action": "CONTINUE" }
ASL ResultSelector: { "bag.$": "$.Payload.state_data", "next_action.$": "$.Payload.next_action" }
ASL ResultPath "$.state_data":
  -> SFN state: { "state_data": { "bag": flat_state, "next_action": "..." } }
  -> Next Lambda input: event.state_data.bag = flat_state
```

---

## 3. Discovered Issues

---

### 3.1 CRITICAL — Immediate Fix Required

---

#### BUG-01: `initialize_state_data.py:538` — Hard Failure Without Fallback

**File**: [initialize_state_data.py:527-543](../backend/src/common/initialize_state_data.py#L527-L543)

**Symptom**:
```python
try:
    # Merkle Manifest creation
    manifest_pointer = versioning_service.create_manifest(...)
    manifest_id = manifest_pointer.manifest_id
    ...
except Exception as e:
    logger.error(f"Failed to create Merkle manifest: {e}", exc_info=True)
    # Fallback to legacy mode   <- comment
    manifest_id = None           <- assigned None

# State Bag Construction
bag = SmartStateBag({}, hydrator=hydrator)

if not manifest_id:
    raise RuntimeError(          <- unconditional exception (no fallback)
        "Failed to create Merkle DAG manifest. ..."
    )
```

**Problem**: If `create_manifest()` fails for **any reason** — network error, transient DynamoDB failure, S3 access error — `manifest_id = None` leads to immediate `RuntimeError`. The code comment "Fallback to legacy mode" completely contradicts the actual behavior.

**Impact**: Any transient failure in Merkle-related AWS resources causes **all workflow executions to halt entirely**. Infrastructure failures unrelated to the workflow itself block user execution.

**Recommendation**: Implement an actual fallback to the legacy path (direct storage via partition_map). The `_HAS_VERSIONING` flag exists but is effectively neutralized during state initialization.

---

#### BUG-02: `segment_runner_service.py:3067` — Bag Nested Path Error (Merkle Chain Break)

**File**: [segment_runner_service.py:3035-3079](../backend/src/services/execution/segment_runner_service.py#L3035-L3079)

**Symptom**:
```python
# seal_state_bag return structure:
# sealed_result = {
#   "state_data": flat_merged_state,   <- no 'bag' key (at Lambda return time)
#   "next_action": "CONTINUE"
# }
# ASL adds the bag wrapping after Lambda returns

sealed_result = seal_state_bag(
    base_state=base_state,
    result_delta={'execution_result': execution_result},
    action='sync',
    context=seal_context
)

# ...after save_state_delta() call...
if new_manifest_id:
    sealed_result['state_data']['bag']['current_manifest_id'] = new_manifest_id
    # ^ KeyError: 'bag' <- state_data is a flat dict, no 'bag' key
```

**Problem**: `seal_state_bag -> USC` returns `{state_data: flat_state}`. `state_data['bag']` is a structure added by the ASL `ResultSelector` after Lambda returns, so **`state_data['bag']` is not accessible within Lambda code**.

This line triggers a KeyError inside `except Exception as e` (line 3077), which is caught, so the **workflow continues but** `current_manifest_id` is not propagated, breaking the Merkle Chain linkage at every segment.

**Impact**:
- `save_state_delta(previous_manifest_id=None)` — all deltas branch from the ROOT manifest
- Merkle integrity chain cannot form -> history tracking and rollback functionality invalidated
- Error logs should appear instead of "current_manifest_id set successfully", but the workflow itself proceeds -> silent failure

**Recommendation**:
```python
# Fix: recognize that state_data is a flat dict and insert directly at top level
if new_manifest_id:
    sealed_result['state_data']['current_manifest_id'] = new_manifest_id
```

---

#### BUG-03: `execution_progress_notifier.py:595` — `new_history_logs` Path Mismatch (History Loss)

**File**: [execution_progress_notifier.py:593-624](../backend/src/handlers/core/execution_progress_notifier.py#L593-L624)

**Symptom**:
USC's `merge_logic` (universal_sync_core.py:748-752) converts the `new_history_logs` key to `state_history` upon receipt:
```python
# universal_sync_core.py merge_logic
if key == 'new_history_logs':
    existing = updated_state.get('state_history', [])
    updated_state['state_history'] = _merge_list_field(existing, value, strategy)
    continue  # <- new_history_logs key itself does not remain in state_data
```

After USC processing, only `state_history` exists in the flat state; the `new_history_logs` key is removed.

```python
# execution_progress_notifier.py _update_execution_status
new_logs = notification_payload.get('new_history_logs')  # -> None
          or inner.get('new_history_logs')                # -> None (key absent in inner_payload)
```

`new_logs` is always `None`, so execution falls into the `else` branch of `_merge_history_logs`, **rewriting the existing S3 history as-is without adding new logs**.

**Impact**:
- Segment execution logs are not accumulated in DynamoDB/S3 history
- Frontend `CheckpointTimeline` and `ExecutionHistoryInline` components do not reflect execution history
- Even after execution completes, history appears empty

**Recommendation**:
```python
# When constructing inner_payload before calling _update_execution_status,
# either separate new_history_logs from state_history and pass explicitly,
# or fix the full_state lookup path to match Kernel Protocol structure:
bag = state_data.get('bag', state_data)  # prefer bag key, fall back to flat
state_history = bag.get('state_history', [])
```

---

#### BUG-04: `execution_progress_notifier.py:812` — `state_history` Path Error Inside `state_data`

**File**: [execution_progress_notifier.py:810-812](../backend/src/handlers/core/execution_progress_notifier.py#L810-L812)

**Symptom**:
```python
# Inside lambda_handler
state_data = payload.get('state_data') or {}
# Per Kernel Protocol: state_data = {bag: flat_state, next_action: "..."}
# state_history is inside bag

inner_payload = {
    ...
    'state_history': payload.get('new_history_logs') or state_data.get('state_history', []),
    # ^ state_data is {bag: {...}} structure, so state_history key doesn't exist -> always []
}
```

**Problem**: `state_data.get('state_history', [])` searches for `state_history` in a `{bag: flat_state}` dictionary, always returning an empty list. The correct path is `state_data.get('bag', {}).get('state_history', [])`.

**Impact**: `inner_payload.state_history` delivered via WebSocket is always an empty array -> frontend timeline/checkpoint view is always empty.

**Related to BUG-03**: BUG-03 affects the DB persistence path; BUG-04 affects the WebSocket delivery path with the same underlying issue.

---

### 3.2 HIGH — Fix Before Production

---

#### BUG-05: MANIFESTS_TABLE Environment Variable 3-Way Split

**File**: Multiple files

| File | Environment Variable Used | Default Value |
|------|--------------------------|---------------|
| `initialize_state_data.py:76` | `MANIFESTS_TABLE` | `WorkflowManifests-v3-dev` ✓ |
| `manifest_regenerator.py:52` | `MANIFESTS_TABLE` | `WorkflowManifests-v3-dev` ✓ |
| `segment_runner_service.py:3045` | `MANIFESTS_TABLE` | `StateManifestsV3` ✗ differs |
| `save_latest_state.py:96` | `MANIFESTS_TABLE` | `StateManifestsV3` ✗ differs |
| `load_latest_state.py:96` | `MANIFESTS_TABLE` | `StateManifestsV3` ✗ differs |
| `segment_runner_service.py:1210` | **`WORKFLOW_MANIFESTS_TABLE`** | `WorkflowManifestsV3` ✗ different var name |
| `segment_runner_service.py:3481` | **`WORKFLOW_MANIFESTS_TABLE`** | `WorkflowManifestsV3` ✗ different var name |
| `merkle_gc_service.py:457` | **`WORKFLOW_MANIFESTS_TABLE`** | `WorkflowManifests-v3-dev` |
| `template.yaml:629` | `MANIFESTS_TABLE: !Ref WorkflowManifestsV3` | -> `WorkflowManifests-v3-{stage}` |

**Problem**:
- Production: The `MANIFESTS_TABLE` env var is set, so files using `MANIFESTS_TABLE` work correctly
- However, the three locations using `WORKFLOW_MANIFESTS_TABLE` (segment_runner_service:1210, :3481, merkle_gc_service) **always use hardcoded defaults** (`WorkflowManifestsV3`, `WorkflowManifests-v3-dev`) since this variable is absent from template.yaml
- If the actual table name is `WorkflowManifests-v3-prod`, these three paths access the wrong table

**Impact**:
- `segment_runner_service.py:1210` — manifest loading fails -> segment config lookup impossible
- `segment_runner_service.py:3481` — manifest regeneration fails -> recovery impossible
- `merkle_gc_service.py` — GC accesses the wrong table -> potential deletion of valid blocks

**Recommendation**: Unify `WORKFLOW_MANIFESTS_TABLE` references to `MANIFESTS_TABLE`, or add `WORKFLOW_MANIFESTS_TABLE` to `template.yaml`.

---

#### BUG-06: `should_update_database()` — Kernel Protocol Structure Not Reflected

**File**: [execution_progress_notifier.py:253-302](../backend/src/handlers/core/execution_progress_notifier.py#L253-L302)

**Symptom**:
```python
def should_update_database(payload: dict, state_data: dict) -> bool:
    current_status = payload.get('status', '').upper()
    # state_data has {bag: flat_state, next_action: ...} structure
    last_db_update = state_data.get('last_db_update_time', 0)
    # ^ last_db_update_time is inside state_data.bag -> always returns 0
```

**Problem**: Despite `state_data` having the `{bag: flat_state}` structure after Kernel Protocol, `state_data.get('last_db_update_time')` directly queries the wrapper -> always returns `0`.

**Impact**: The `DB_UPDATE_INTERVAL` time condition (`current_time - last_db_update >= 30`) is always `True` -> **DynamoDB writes occur on every notifier invocation** even under the SELECTIVE strategy (excessive WCU consumption).

---

#### BUG-07: S3 Bucket Environment Variable Mismatch (`segment_runner_service.py:3041`)

**File**: [segment_runner_service.py:3040-3042](../backend/src/services/execution/segment_runner_service.py#L3040-L3042)

**Symptom**:
```python
# Inside segment_runner_service.py _finalize_response
s3_bucket = os.environ.get('S3_BUCKET') or os.environ.get('SKELETON_S3_BUCKET')
```

`template.yaml` SegmentRunnerHandler environment variables:
```yaml
WORKFLOW_STATE_BUCKET: !If [CreateWorkflowStateBucket, !Ref ...]
SKELETON_S3_BUCKET: !If [CreateWorkflowStateBucket, !Ref ...]  # Inherited from Globals
```

USC (`universal_sync_core.py:74-80`) bucket lookup order:
```python
_S3_BUCKET = (
    os.environ.get('WORKFLOW_STATE_BUCKET') or
    os.environ.get('S3_BUCKET') or
    os.environ.get('STATE_STORAGE_BUCKET') or ''
)
```

**Problem**: `segment_runner_service.py` queries `S3_BUCKET` first, but this variable is not declared in template.yaml Globals or SegmentRunnerHandler environment variables. Since `SKELETON_S3_BUCKET` is inherited from Globals, it ultimately works correctly, but the `S3_BUCKET` priority lookup is dead code and causes confusion.

**Additionally**: `initialize_state_data.py:353` uses a 3-tier fallback of `WORKFLOW_STATE_BUCKET` -> `S3_BUCKET` -> `SKELETON_S3_BUCKET`. Different lookup patterns across Lambdas make environment variable misconfiguration debugging difficult.

---

### 3.3 MODERATE — Short-Term Improvement Needed

---

#### BUG-08: `initialize_state_data.py:369` — execution_id and SFN executionArn Mismatch

**File**: [initialize_state_data.py:368-373](../backend/src/common/initialize_state_data.py#L368-L373)

**Symptom**:
```python
# initialize_state_data.py
execution_id = raw_input.get('idempotency_key') or raw_input.get('execution_id')
if not execution_id:
    execution_id = f"init-{workflow_id}-{int(time.time())}-{str(uuid.uuid4())[:8]}"
    # e.g.: "init-wf-abc123-1708425600-f3a9c7b2"
```

Actual SFN executionArn:
```
arn:aws:states:ap-northeast-2:123456789012:execution:WorkflowOrchestrator:abc-def-123
```

**Problem**: The Merkle manifest is created with an `init-*` format ID, but there is no link to the `executionArn`-based DynamoDB record tracked by `execution_progress_notifier`.

**Impact**: The StateVersioningService delta history is not linked to the DynamoDB execution record created by `run_workflow.py`, making Merkle history lookup impossible during execution tracking and rollback.

---

#### BUG-09: `universal_sync_core.py:1003-1004` — Missing `segment_to_run` Increment Condition

**File**: [universal_sync_core.py:1002-1004](../backend/src/handlers/utils/universal_sync_core.py#L1002-L1004)

**Symptom**:
```python
# universal_sync_core.py universal_sync_core
if normalized_delta.get('_increment_segment', False):
    updated_state['segment_to_run'] = int(updated_state.get('segment_to_run', 0)) + 1
```

The `_increment_segment` flag is only set in `flatten_result` when `action == 'merge_callback'` or `action == 'merge_async'`. For regular `sync` actions, `next_segment_to_run` is directly substituted into `segment_to_run`.

**Problem**: If `next_segment_to_run` is returned as `None` (on completion) and the `_increment_segment` flag is absent, `segment_to_run` retains its current value -> in `_compute_next_action`'s COMPLETE check, the `delta.get('segment_to_run') is None` condition must be satisfied, but if `segment_to_run` is missing from the delta, the flow falls through to comparing the value from `updated_state` (previous segment ID) against `total_segments`.

The actual COMPLETE determination relies on the segment runner directly returning `status: 'COMPLETE'`, with USC's numeric comparison as a secondary fallback. Whether this path always works correctly **lacks end-to-end scenario tests**.

---

#### BUG-10: `prevent_pointer_bloat` — Dependency on Potentially Absent `state_data_manager`

**File**: [universal_sync_core.py:798](../backend/src/handlers/utils/universal_sync_core.py#L798)

**Symptom**:
```python
def prevent_pointer_bloat(state, idempotency_key):
    if 'failed_segments' in state:
        if len(failed) > 5:
            from .state_data_manager import store_to_s3, generate_s3_key  # lazy import
            try:
                s3_path = store_to_s3(failed, s3_key)
                ...
            except Exception as e:
                logger.warning(...)  # failure silently ignored
```

**Problem**: `state_data_manager.py` imports `from .universal_sync_core import universal_sync_core` at module level. `universal_sync_core.py` also lazy-imports `state_data_manager` inside a function. This lazy pattern is meant to prevent circular references, but there is a **potential ImportError if circular initialization is incomplete at the time of function call**.

---

### 3.4 LOW — Code Quality Improvement

---

#### BUG-11: `run_workflow.py:188-218` — Redundant Request Body Double-Parsing

**File**: [run_workflow.py:186-218](../backend/src/handlers/core/run_workflow.py#L186-L218)

**Symptom**:
```python
# First parse (line 188-198)
parsed_body = None
if event.get('body'):
    try:
        parsed_body = json.loads(event['body'])
        if mock_mode == 'true' and 'test_workflow_config' in parsed_body:
            test_config_to_inject = parsed_body['test_workflow_config']
    except json.JSONDecodeError:
        pass

# Second parse (line 203-210) — reset parsed_body and re-parse
parsed_body = None   # <- reset
input_data = {}
raw_body = event.get('body')
if raw_body:
    try:
        parsed_body = json.loads(raw_body)  # same body re-parsed
    except ...:
        parsed_body = None
```

**Problem**: No functional bug, but the `mock_mode` check (`os.environ.get('MOCK_MODE', 'false').lower()`) occurs before the second parse, so the first parse's `mock_mode == 'true'` condition and the `mock_mode_enabled` condition after the second parse use different expressions. The unnecessary double-parsing wastes performance and hurts MOCK_MODE logic readability.

---

#### BUG-12: `segment_runner_service.py:51` — Comment vs. Actual Import Mismatch on Circular Import Risk

**File**: [segment_runner_service.py:51](../backend/src/services/execution/segment_runner_service.py#L51)

**Symptom**:
```python
# Using generic imports from main handler file as source of truth
from src.handlers.core.main import run_workflow, partition_workflow as _partition_workflow_dynamically, _build_segment_config
```

A comment near the bottom of the file (lines 199-212) explicitly flags this import pattern as a "Circular Import risk" and recommends removal, but **the module-level import at the top of the file (line 51) remains**:

```python
# --- Legacy Helper Imports REMOVED (v3.3) ---
# [WARNING] The imports below have been removed due to Circular Import risk.
# REMOVED:
#   from src.handlers.core.main import run_workflow, ...
```

The bottom comment says "removed" but the import still exists at the top (line 51). Documentation and code are inconsistent.

---

## 4. SFN Field and State Value Conformance Review

### 4.1 next_action State Values

Mapping between values returned by USC `_compute_next_action` and values expected by ASL Choice states:

| USC Return | ASL Expected State | Conformance |
|-----------|-------------------|-------------|
| `STARTED` | InitialState -> SegmentLoop entry | OK |
| `CONTINUE` | LoopCheck -> SegmentRunner re-execution | OK |
| `COMPLETE` | LoopCheck -> completion branch | OK |
| `PAUSED_FOR_HITP` | WaitForHITP Task | OK |
| `FAILED` | Failure handling branch | OK |
| `HALTED` | Need to verify separate ASL branch | Warning |
| `SIGKILL` | Need to verify separate ASL branch | Warning |
| `PARALLEL_GROUP` | Parallel branch execution path | OK |

**`HALTED`, `SIGKILL` handling**: USC returns these values, but verification is needed to confirm whether the actual ASL Choice state handles them as separate branches.

### 4.2 Required Field Guarantees (SFN 256KB Limit Mitigation)

Current protection layers:
1. `initialize_state_data.py` — force_offload applied at initialization (`workflow_config`, `partition_map`, `current_state`, `input`)
2. `USC optimize_and_offload` — S3 offload for fields exceeding 30KB
3. `seal_state_bag` — size verification logging after USC pass
4. `segment_runner_handler.py` — response size logging (error log if exceeding 250KB)

**`CONTROL_FIELDS_NEVER_OFFLOAD` verification**: Fields that USC never offloads:
```
execution_id, segment_to_run, segment_id, loop_counter, next_action,
status, idempotency_key, state_s3_path, pre_snapshot_s3_path,
post_snapshot_s3_path, last_update_time, payload_size_kb
```
Recommended to verify in template.yaml whether these fields are directly referenced in ASL Choice conditions.

### 4.3 Partition Map Access Path

At initialization, `partition_map` is stored in the Merkle manifest, and only `segment_manifest_pointers` are kept in the bag. The segment runner must load segment_config from S3 using `manifest_id + segment_index`, but **separate verification is needed for whether the actual manifest loading is implemented in `segment_runner_service.py`**.

---

## 5. Log Snapshot Pipeline Review

### 5.1 Current Snapshot Flow

```
Segment execution -> _finalize_response()
  -> execution_result.new_history_logs = [...]
  -> seal_state_bag({execution_result: ...})
  -> USC flatten_result(action='sync')
      -> payload.get('execution_result').get('new_history_logs') -> delta.new_history_logs
  -> USC merge_logic
      -> new_history_logs -> state_history (dedupe_append)
      -> new_history_logs key is removed from state_data
  -> ASL ResultPath: stored in state_data.bag.state_history

ExecutionProgressNotifier invocation
  -> payload = SFN event (state_data.bag structure)
  -> _update_execution_status(notification_payload)
      -> new_logs = notification_payload.get('new_history_logs')  # None
              or inner.get('new_history_logs')                      # None
      -> else: current_history = full_state.get('state_history', [])
              full_state = inner.get('state_data')  # None -> {}
      -> full_state.get('state_history', []) -> []
      -> merged_history = []  (history lost)
```

### 5.2 Glass-Box Recovery Path (Partially Working)

"Light Hydration" logic at `execution_progress_notifier.py:836-903`:
```python
if target_s3_path and not has_inline_data:
    hydrated_data = s3_client.get_object(...)
    logs = hydrated_data.get('new_history_logs') or hydrated_data.get('state_history')
    if logs:
        inner_payload['new_history_logs'] = logs[-10:]
```

This retrieves `final_state` or `state_s3_path` from S3 and extracts `new_history_logs` or `state_history`. However, this path requires:
- `target_s3_path` to be correctly passed (`payload.final_state_s3_path`, etc.)
- `has_inline_data` condition to be False

If USC offloading sent `final_state` to S3, this path may work, but **the Light Hydration result is not reflected in `_update_execution_status`'s DB persistence path** (it is reflected in inner_payload, but `_update_execution_status` uses a separate `db_payload`).

### 5.3 History Maximum Entry Limit

`execution_progress_notifier.py:598`: `MAX_HISTORY = int(os.environ.get('STATE_HISTORY_MAX_ENTRIES', '50'))`

With a 50-entry limit, long-running workflows (50+ segments) lose early history. Eviction policy: FIFO (oldest entries removed first, line 533-534).

---

## 6. Fix Priority and Recommendations

### Priority Table

| # | Severity | File | Location | Impact | Estimated Fix Difficulty |
|---|----------|------|----------|--------|--------------------------|
| BUG-01 | CRITICAL | `initialize_state_data.py` | L538-543 | All workflow initialization halted | Medium (fallback path implementation) |
| BUG-02 | CRITICAL | `segment_runner_service.py` | L3067 | Merkle Chain break (silent failure) | Low (1-line path fix) |
| BUG-03 | CRITICAL | `execution_progress_notifier.py` | L595 | Execution history DB persistence lost | Medium (flow redesign) |
| BUG-04 | CRITICAL | `execution_progress_notifier.py` | L812 | WebSocket history always empty array | Low (1-line path fix) |
| BUG-05 | HIGH | Multiple files | - | Wrong DynamoDB table access | Low (variable name unification) |
| BUG-06 | HIGH | `execution_progress_notifier.py` | L288 | Excessive WCU consumption (DB strategy nullified) | Low (path fix) |
| BUG-07 | HIGH | `segment_runner_service.py` | L3041 | S3 bucket lookup confusion | Low (lookup order unification) |
| BUG-08 | MODERATE | `initialize_state_data.py` | L369 | Merkle history and execution record disconnected | Medium |
| BUG-09 | MODERATE | `universal_sync_core.py` | L1003 | COMPLETE determination needs E2E verification | Medium (testing) |
| BUG-10 | MODERATE | `universal_sync_core.py` | L798 | Potential circular import risk | Medium |
| BUG-11 | LOW | `run_workflow.py` | L188-218 | Readability, redundant double-parsing | Low |
| BUG-12 | LOW | `segment_runner_service.py` | L51 | Comment and code inconsistency | Low |

### Recommendations

#### Phase 1 — Immediate (BUG-01, 02, 04)
1. **BUG-02 first**: Single-line fix directly impacting Merkle Chain continuity
   ```python
   # Before
   sealed_result['state_data']['bag']['current_manifest_id'] = new_manifest_id
   # After
   sealed_result['state_data']['current_manifest_id'] = new_manifest_id
   ```

2. **BUG-04**: `state_data.get('state_history', [])` -> `state_data.get('bag', state_data).get('state_history', [])`

3. **BUG-01**: Replace `RuntimeError` in the `if not manifest_id:` block with actual legacy path execution

#### Phase 2 — Short-Term (BUG-03, 05, 06)
4. **BUG-03**: Refactor to explicitly pass `new_history_logs` when calling `_update_execution_status`. Pass original logs through a separate channel before USC converts them to `state_history`.

5. **BUG-05**: Unify `WORKFLOW_MANIFESTS_TABLE` -> `MANIFESTS_TABLE` or add the variable to template.yaml

6. **BUG-06**: `should_update_database`'s `state_data.get(...)` -> `state_data.get('bag', state_data).get(...)`

#### Phase 3 — Medium-Term (BUG-07, 08, 09, 10)
7. Introduce environment variable access helper functions to establish single lookup points for bucket/table names
8. Define `execution_id` lifecycle: mechanism to back-link executionArn to Merkle history after SFN start

---

*This report was produced based on static analysis. Parallel verification in a live AWS environment is recommended.*
