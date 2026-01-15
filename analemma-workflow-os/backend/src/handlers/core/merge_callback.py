"""Lambda handler to merge previous_final_state and user_callback_result into new_current_state.

This provides a deterministic merging behavior usable by the Step Functions state machine
after a HITP callback. It prefers user_callback_result values when merging dictionaries,
and for scalar callback results it stores them under the 'user_callback' key while preserving
previous_final_state contents.
Additionally, to preserve the S3 claim-check pattern and avoid large Step Functions payloads,
the merged state will be offloaded to S3 when its serialized size exceeds the configured
STREAM_INLINE_THRESHOLD_BYTES. In that case this Lambda returns a pointer under
"new_state_s3_path" and keeps "new_current_state" small (empty dict).
"""
import copy
import json
from typing import Any, Dict
import os
import time
import re
import boto3
import logging
import uuid

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge dict b into a and return the result (a is copied).
    Values from src.b take precedence over a.
    """
    out = copy.deepcopy(a) if a is not None else {}
    for k, v in (b or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _is_frontend_event(ev: Any) -> bool:
    """Heuristic to detect events that originated from src.an external frontend client.

    We treat events containing request context identifiers (API Gateway/GQL/WebSocket)
    or top-level conversation/execution ids as frontend-origin. These events must
    not be allowed to upload arbitrary state — only natural-language feedback is
    accepted.
    """
    if not isinstance(ev, dict):
        return False
    frontend_keys = (
        "request_context",
        "requestContext",
        "conversation_id",
        "conversationId",
        "execution_id",
        "executionId",
        "websocket",
        "http_method",
    )
    for k in frontend_keys:
        if k in ev:
            return True
    if ev.get("source") in ("frontend", "web_client"):
        return True
    return False


def make_new_current_state(previous_final_state: Any, user_callback_result: Any) -> Any:
    """Create a merged current state given previous_final_state and user_callback_result.

    Rules:
    - If both are dict-like, deep-merge with user_callback_result taking precedence.
    - If previous is dict and callback is scalar, preserve previous and add 'user_callback'.
    - If previous is scalar and callback is dict, return callback merged with {'previous': previous}.
    - If both scalar, return {'previous': previous, 'user_callback': callback}.
    """
    # If user_callback_result appears to be natural-language feedback (string)
    # or a small dict with a 'feedback' key, treat it as a user message and
    # append it to a `messages` list inside the state so LLMs can consume a
    # chat-style history. This repository uses messages-based state for
    # revision workflows.
    feedback_text = None
    if isinstance(user_callback_result, str):
        feedback_text = user_callback_result
    elif isinstance(user_callback_result, dict) and isinstance(user_callback_result.get('feedback'), str):
        feedback_text = user_callback_result.get('feedback')

    # helper: sliding window size for messages
    try:
        MESSAGES_WINDOW = int(os.environ.get('MESSAGES_WINDOW', '20'))
    except Exception:
        MESSAGES_WINDOW = 20

    def _extract_variables_from_text(text: str, system_data: Dict[str, Any]) -> Dict[str, Any]:
        """Lightweight heuristic extraction: budget (예산), user name, project goal."""
        if not isinstance(text, str):
            return system_data
        t = text
        # budget: look for numbers followed by '원' or '만원' or keywords '예산'
        try:
            if '예산' in t or '예산은' in t or 'budget' in t:
                m = re.search(r"([0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)\s*(만원|원)?", t)
                if m:
                    num = m.group(1).replace(',', '')
                    unit = m.group(2)
                    val = int(num)
                    if unit == '만원':
                        val = val * 10000
                    # store under current_budget
                    system_data['current_budget'] = val
        except Exception:
            pass

        # name: 간단한 패턴
        try:
            if '이름' in t:
                # patterns like '제 이름은 홍길동입니다' or '내 이름은 홍길동'
                m = re.search(r"(?:제\s*이름은|내\s*이름은|이름은)\s*([\w가-힣]+)", t)
                if m:
                    system_data['user_name'] = m.group(1)
        except Exception:
            pass

        # project goal: '목표' 관련 간단 추출
        try:
            if '목표' in t or '목적' in t:
                m = re.search(r"(?:목표|목적)\s*(?:은|:)?\s*(.+)$", t)
                if m:
                    system_data['project_goal'] = m.group(1).strip()
        except Exception:
            pass

        return system_data

    if feedback_text is not None:
        # Prefer preserving and mutating previous_final_state if it's a dict
        if isinstance(previous_final_state, dict):
            out = copy.deepcopy(previous_final_state)
            msgs = out.get('messages')
            if not isinstance(msgs, list):
                msgs = []
                # Convert an existing textual draft/result into an assistant message
                prior = (
                    out.get('current_draft')
                    or out.get('result')
                    or out.get('final_text')
                    or out.get('text')
                    or out.get('value')
                )
                if prior is not None:
                    msgs.append({'role': 'assistant', 'content': str(prior)})
                out['messages'] = msgs

            out['messages'].append({'role': 'user', 'content': feedback_text})
            # enforce sliding window
            out['messages'] = out['messages'][-MESSAGES_WINDOW:]

            # ensure system_data exists and try extracting facts
            sysd = out.get('system_data') if isinstance(out.get('system_data'), dict) else {}
            sysd = _extract_variables_from_text(feedback_text, sysd)
            out['system_data'] = sysd

            out['status'] = 'REVISION_REQUESTED'
            out['last_feedback_at'] = int(time.time())
            return out

        # previous_final_state is scalar or None — return a minimal messages-based state
        msgs = []
        if previous_final_state is not None:
            msgs.append({'role': 'assistant', 'content': str(previous_final_state)})
        msgs.append({'role': 'user', 'content': feedback_text})
        sysd = {}
        sysd = _extract_variables_from_text(feedback_text, sysd)
        return {'messages': msgs[-MESSAGES_WINDOW:], 'status': 'REVISION_REQUESTED', 'previous': previous_final_state, 'system_data': sysd}

    # Fallback to original merge semantics for non-feedback payloads
    if isinstance(previous_final_state, dict) and isinstance(user_callback_result, dict):
        return deep_merge(previous_final_state, user_callback_result)

    if isinstance(previous_final_state, dict) and not isinstance(user_callback_result, dict):
        out = copy.deepcopy(previous_final_state)
        # Always overwrite any existing 'user_callback' with the newest value
        out['user_callback'] = user_callback_result
        return out

    if not isinstance(previous_final_state, dict) and isinstance(user_callback_result, dict):
        out = copy.deepcopy(user_callback_result)
        # Ensure we record the previous scalar value under 'previous',
        # overwriting any existing value to avoid stale data.
        out['previous'] = previous_final_state
        return out

    # both scalars or None
    return {
        'previous': previous_final_state,
        'user_callback': user_callback_result,
    }


def _normalize_callback_result(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    [3순위 최적화] Callback 정규화 로직을 Lambda 내부로 흡수.
    NormalizeCallbackResult, PromoteCallbackPayload, PromoteCallbackDirect 상태 제거.
    
    callback_result가 .Payload를 가지면 그 안의 값을 추출하고,
    그렇지 않으면 callback_result 자체를 사용.
    
    🚨 [Critical Fix] workflow_config 등 필수 필드 보존 강화
    """
    callback_result = event.get('callback_result', {})
    
    # 🚨 [Critical Fix] 기본 state_data를 event에서 가져옴 (fallback용)
    default_state_data = event.get('state_data', {})
    
    # Payload가 있으면 Lambda invoke 래핑된 결과
    if isinstance(callback_result, dict) and 'Payload' in callback_result:
        payload = callback_result.get('Payload', {})
        # 🚨 [Critical Fix] state_data를 payload에서 먼저, 없으면 event에서 가져오고 병합
        merged_state_data = _merge_state_data(
            default_state_data, 
            payload.get('state_data') if isinstance(payload, dict) else None
        )
        return {
            'user_callback_result': payload,
            'state_data': merged_state_data,
            'conversation_id': payload.get('conversation_id') if isinstance(payload, dict) else None,
            'workflowId': payload.get('workflowId') if isinstance(payload, dict) else None,
            'ownerId': payload.get('ownerId') if isinstance(payload, dict) else None,
            # 🚨 [Critical Fix] workflow_config 보존
            'workflow_config': (payload.get('workflow_config') if isinstance(payload, dict) else None) or event.get('workflow_config'),
        }
    else:
        # Direct callback result (SendTaskSuccess로 직접 전달된 경우)
        cb_state_data = callback_result.get('state_data') if isinstance(callback_result, dict) else None
        merged_state_data = _merge_state_data(default_state_data, cb_state_data)
        return {
            'user_callback_result': callback_result,
            'state_data': merged_state_data,
            'conversation_id': callback_result.get('conversation_id') if isinstance(callback_result, dict) else None,
            'workflowId': callback_result.get('workflowId') if isinstance(callback_result, dict) else None,
            'ownerId': callback_result.get('ownerId') if isinstance(callback_result, dict) else None,
            # 🚨 [Critical Fix] workflow_config 보존
            'workflow_config': (callback_result.get('workflow_config') if isinstance(callback_result, dict) else None) or event.get('workflow_config'),
        }


def _merge_state_data(base: Any, override: Any) -> Dict[str, Any]:
    """
    🚨 [Critical Fix] state_data 병합 - 필수 필드(workflow_config 등) 보존 보장
    """
    if not isinstance(base, dict):
        base = {}
    if not isinstance(override, dict):
        override = {}
    
    # base를 복사하고 override로 덮어씌움
    result = dict(base)
    for key, value in override.items():
        if value is not None:  # None 값은 기존 값 유지
            result[key] = value
    
    return result


def handler(event, context=None):
    """Lambda entrypoint compatible with Step Functions invocation.

    Expected event keys: previous_final_state, user_callback_result, segment_to_run (optional)
    [3순위 최적화] callback_result 정규화 로직 내장 (ASL에서 3개 상태 제거)
    Returns: { new_current_state: ..., segment_to_run: ... }
    """
    # [3순위 최적화] callback_result가 있으면 정규화 수행
    if 'callback_result' in event and 'user_callback_result' not in event:
        normalized = _normalize_callback_result(event)
        # 정규화된 값으로 event 업데이트
        event['user_callback_result'] = normalized.get('user_callback_result')
        if normalized.get('state_data') and 'state_data' not in event:
            event['state_data'] = normalized.get('state_data')
        if normalized.get('ownerId') and 'ownerId' not in event:
            event['ownerId'] = normalized.get('ownerId')
        if normalized.get('workflowId') and 'workflowId' not in event:
            event['workflowId'] = normalized.get('workflowId')

    # Support state-bag: if event contains state_data, prefer those values but
    # do not overwrite explicit top-level keys.
    if isinstance(event, dict) and isinstance(event.get('state_data'), dict):
        sd = event.get('state_data') or {}
        for k, v in sd.items():
            if k not in event:
                event[k] = v

    prev = event.get('previous_final_state')
    # If caller supplied an S3 pointer instead of inline previous_final_state,
    # fetch it here so the merging logic can operate on a dict.
    prev_s3 = event.get('previous_final_state_s3_path')
    if (prev is None or prev == {}) and prev_s3:
        # Require ownerId/user identification to validate the S3 key belongs
        # to the same authenticated tenant to avoid IDOR.
        owner_id = event.get('ownerId') or event.get('owner_id') or event.get('user_id')
        try:
            if isinstance(prev_s3, dict):
                bucket = prev_s3.get('bucket') or prev_s3.get('Bucket')
                key = prev_s3.get('key') or prev_s3.get('Key')
            elif isinstance(prev_s3, str) and prev_s3.startswith('s3://'):
                rest = prev_s3[5:]
                bucket, key = rest.split('/', 1)
            else:
                bucket = os.environ.get('SKELETON_S3_BUCKET') or os.environ.get('WORKFLOW_STATE_BUCKET')
                key = prev_s3

            if bucket and key:
                # Security validation: ensure the S3 key is scoped to the
                # authenticated owner. If owner_id missing or mismatch, deny.
                if not owner_id:
                    logger.error("Access denied: missing ownerId when attempting to access previous_final_state_s3_path=%s", key)
                    raise PermissionError("Missing ownerId for S3 state access")

                # 🔒 Path traversal attack prevention
                # Block .., %2f, %2F, null bytes, and other dangerous patterns
                dangerous_patterns = ["..", "%2f", "%2F", "%2e", "%2E", "\x00", "//"]
                for pattern in dangerous_patterns:
                    if pattern in key:
                        logger.error(
                            "Security Alert: Path traversal attack detected. Owner='%s', Key='%s', Pattern='%s'",
                            owner_id, key, pattern
                        )
                        raise PermissionError("Forbidden: Invalid path pattern detected")

                expected_prefix = f"workflow-states/{owner_id}/"
                if not key.startswith(expected_prefix):
                    logger.error(
                        "Access Denied: Owner '%s' attempted to access forbidden key: '%s'",
                        owner_id,
                        key,
                    )
                    raise PermissionError("Forbidden: State path does not match authenticated owner")

                s3 = boto3.client('s3')
                resp = s3.get_object(Bucket=bucket, Key=key)
                body = resp.get('Body')
                if body:
                    raw = body.read()
                    try:
                        prev = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        prev = {'raw': raw.decode('utf-8', errors='replace')}
        except Exception as e:
            # Treat S3 load failures as critical: log and re-raise so the
            # Step Functions task fails instead of silently losing state.
            logger.exception("CRITICAL: Failed to download S3 state 's3://%s/%s'", bucket if 'bucket' in locals() else '-', key if 'key' in locals() else '-')
            raise
    cb = event.get('user_callback_result')
    # If this invocation appears to come from src.an external frontend client,
    # do NOT accept arbitrary structured state uploads. Only allow a plain
    # natural-language feedback string (or a dict with 'feedback' string).
    if _is_frontend_event(event):
        feedback_text = None
        # direct string payload
        if isinstance(cb, str):
            feedback_text = cb
        # wrapped feedback dict
        elif isinstance(cb, dict) and isinstance(cb.get('feedback'), str):
            feedback_text = cb.get('feedback')
        # also accept common alternative top-level fields
        else:
            cand = event.get('response') or event.get('feedback') or event.get('user_response')
            if isinstance(cand, str):
                feedback_text = cand

        if feedback_text is not None:
            cb = feedback_text
            logger.info("merge_callback: frontend-origin request — accepting only natural-language feedback")
        else:
            # No usable textual feedback provided; treat as no callback.
            cb = None
            logger.info("merge_callback: frontend-origin request contained no textual feedback; ignoring uploaded state")
    seg = event.get('segment_to_run')
    owner_id = event.get('ownerId') or event.get('owner_id') or event.get('user_id')
    # Best-effort workflow id extraction for S3 prefixing
    workflow_id = (
        event.get('workflow_id')
        or event.get('workflowId')
        or (event.get('workflow_config') or {}).get('id')
        or (event.get('workflow_config') or {}).get('workflowId')
    )

    new_state = make_new_current_state(prev, cb)

    # Offload to S3 when merged state is large
    bucket = os.environ.get('SKELETON_S3_BUCKET') or os.environ.get('WORKFLOW_STATE_BUCKET')
    try:
        STREAM_INLINE_THRESHOLD = int(os.environ.get('STREAM_INLINE_THRESHOLD_BYTES', '250000'))
    except Exception:
        STREAM_INLINE_THRESHOLD = 250000

    result: Dict[str, Any] = {
        'new_current_state': new_state if isinstance(new_state, dict) else {'value': new_state},
        'segment_to_run': seg,
    }

    # Build a compact snapshot of the previous state for history tracking.
    # The snapshot is intentionally small: include timestamp, segment, a few
    # representative fields (status, system_data, last message summary), and
    # S3 pointer if present. This avoids storing full payloads in the inline
    # state while still preserving important prior facts for non-HITP flows.
    def _make_snapshot(prev_obj: Any, prev_s3_path: Any, seg_val: Any) -> Dict[str, Any]:
        snap: Dict[str, Any] = {
            'ts': int(time.time()),
            'segment': seg_val,
        }
        try:
            if isinstance(prev_obj, dict):
                snap['status'] = prev_obj.get('status')
                sd = prev_obj.get('system_data')
                if isinstance(sd, dict):
                    # Only persist a copy of system_data (may be small)
                    snap['system_data'] = sd.copy()
                msgs = prev_obj.get('messages')
                if isinstance(msgs, list) and len(msgs) > 0:
                    # Persist a one-line summary of the last user message
                    last = msgs[-1]
                    snap['last_message_summary'] = (last.get('content')[:240] if isinstance(last.get('content'), str) else str(last))
                # friendly human-readable id if present
                snap['summary_id'] = prev_obj.get('id') or prev_obj.get('conversation_id')
                
                # [NEW] Capture usage stats if present
                if 'usage' in prev_obj:
                    snap['usage'] = prev_obj['usage']
            else:
                snap['previous_scalar'] = prev_obj
        except Exception:
            # best-effort: never fail the merge due to snapshotting
            pass
        if prev_s3_path:
            snap['previous_state_s3'] = prev_s3_path
        return snap

    # Existing history (if caller provided it via state_data) will be respected
    existing_history = []
    try:
        existing_history = (event.get('state_data') or {}).get('state_history') or event.get('state_history') or []
        if not isinstance(existing_history, list):
            existing_history = []
    except Exception:
        existing_history = []

    # Build and append the snapshot
    try:
        snapshot = _make_snapshot(prev if prev is not None else {}, prev_s3, seg)
        history = list(existing_history) + [snapshot]
        # enforce max entries
        try:
            MAX_HISTORY = int(os.environ.get('STATE_HISTORY_MAX_ENTRIES', '10'))
        except Exception:
            MAX_HISTORY = 10
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        result['new_state_history'] = history
    except Exception:
        # non-fatal
        logger.exception("Failed to build state_history snapshot — continuing without it")

    try:
        if bucket and new_state is not None:
            serialized = json.dumps(new_state, ensure_ascii=False)
            if len(serialized.encode('utf-8')) > STREAM_INLINE_THRESHOLD:
                if not owner_id:
                    logger.error("Refusing to offload merged state to S3: missing ownerId")
                    raise PermissionError("Missing ownerId for S3 upload")
                # Build a deterministic, owner-scoped prefix to enable server-side validation
                seg_suffix = str(seg) if seg is not None else "unknown"
                prefix = f"workflow-states/{owner_id}/{workflow_id or 'unknown'}/segments/{seg_suffix}/merged"
                key = f"{prefix.rstrip('/')}/{uuid.uuid4()}.json"
                s3 = boto3.client('s3')
                s3.put_object(Bucket=bucket, Key=key, Body=serialized.encode('utf-8'))
                result['new_state_s3_path'] = f"s3://{bucket}/{key}"
                # Keep inline small to prevent Step Functions payload bloat
                result['new_current_state'] = {}
    except Exception:
        # If offload fails, propagate as an error so the workflow doesn't attempt
        # to pass a too-large inline payload to the next state.
        logger.exception("CRITICAL: Failed to offload merged state to S3")
        raise

    # Best-effort: ensure `conversation_id` is present on the callback result
    # so Step Functions JsonPath references like `$.callback_result.conversation_id`
    # can succeed even if the caller omitted it. Prefer explicit top-level
    # values, then stored state_data, then any field inside the user callback.
    try:
        conv = None
        if isinstance(event, dict):
            conv = event.get('conversation_id') or event.get('conversationId')
            if not conv:
                sd = event.get('state_data') or {}
                if isinstance(sd, dict):
                    conv = sd.get('conversation_id') or sd.get('conversationId')
            if not conv:
                cbv = event.get('user_callback_result')
                if isinstance(cbv, dict):
                    conv = cbv.get('conversation_id') or cbv.get('conversationId')
        if conv:
            result['conversation_id'] = conv
            # also propagate into new_current_state when appropriate
            try:
                ncs = result.get('new_current_state')
                if isinstance(ncs, dict) and 'conversation_id' not in ncs:
                    ncs['conversation_id'] = conv
                    result['new_current_state'] = ncs
            except Exception:
                pass
    except Exception:
        # non-fatal — do not fail the merge for missing conversation id
        logger.debug("merge_callback: failed to derive conversation_id (non-fatal)")

    # Ensure the result always contains the new_state_s3_path key so that
    # Step Functions ResultSelector/Pass mappings that reference
    # `$.Payload.new_state_s3_path` do not fail when no S3 offload occurred.
    if 'new_state_s3_path' not in result:
        result['new_state_s3_path'] = None

    # ASL ResultSelector가 'workflow_config'를 찾을 수 있도록
    # 입력받은 설정값을 그대로 반환값에 포함시킵니다.
    result['workflow_config'] = event.get('workflow_config')

    return result


if __name__ == '__main__':
    # quick local smoke when run as script
    import sys
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            ev = json.load(f)
    else:
        ev = {
            'previous_final_state': {'x': 1, 'y': {'a': 2}},
            'user_callback_result': {'y': {'b': 3}, 'z': 4},
            'segment_to_run': 1,
        }
    print(json.dumps(handler(ev), indent=2))
