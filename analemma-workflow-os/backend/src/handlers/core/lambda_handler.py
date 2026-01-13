"""
This module provides a minimal synchronous `lambda_handler` used for
local testing and debugging only. It intentionally runs workflows
directly (calling into `main.run_workflow`) and is NOT the runtime
entrypoint used by the Step Functions orchestration.

Operational Lambdas invoked by the state machine (for example,
`segment_runner_handler.lambda_handler` and `store_task_token.lambda_handler`)
should be used in production. Keep this handler for tests and local
invocation convenience; do not wire API Gateway endpoints to it in
production `serverless.yml`.
"""

import json
from typing import Any, Dict
import os

from src.handlers.core.main import run_workflow, run_workflow_from_dynamodb, partition_workflow, _build_segment_config

# Support the new state-bag contract for local testing handlers. If the
# repository has `statebag.normalize_event`, use it to flatten `state_data`
# into the top-level keys so existing test callers keep working.
try:
    from src.common.statebag import normalize_event  # type: ignore
except Exception:
    try:
        from src.common.statebag import normalize_event  # type: ignore
    except Exception:
        def normalize_event(e: Dict[str, Any]) -> Dict[str, Any]:
            # no-op fallback when the helper isn't available
            return e


def lambda_handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Minimal Lambda handler that expects a JSON body containing:
      - config_json: str (the workflow configuration JSON)
      - initial_state: dict (optional)
      - user_api_keys: dict (optional)

    Returns a dict suitable for API Gateway/Lambda proxy integration.
    """
    # Normalize state-bag inputs (if present) so callers may provide
    # either legacy top-level fields or the new `state_data` bag.
    try:
        event = normalize_event(event)
    except Exception:
        pass

    body = event.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return {"statusCode": 400, "body": json.dumps({"error": "invalid body"})}

    # If request supplies a DynamoDB pointer, fetch the config there
    if body and all(k in body for k in ("ddb_table", "ddb_key_name", "ddb_key_value")):
        try:
            final = run_workflow_from_dynamodb(body["ddb_table"], body["ddb_key_name"], body["ddb_key_value"], body.get("initial_state"), body.get("user_api_keys"))
            return {"statusCode": 200, "body": json.dumps({"final_state": final})}
        except Exception as e:
            # Map CacheMissError to a 503 so callers can retry later or surface
            # a clear 'cache not ready' message.
            try:
                from src.handlers.core.main import CacheMissError
            except Exception:
                try:
                    from src.handlers.core.main import CacheMissError
                except Exception:
                    CacheMissError = None
            if CacheMissError is not None and isinstance(e, CacheMissError):
                return {"statusCode": 503, "body": json.dumps({"error": str(e)})}
            return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

    if not body or "config_json" not in body:
        return {"statusCode": 400, "body": json.dumps({"error": "missing config_json"})}

    try:
        # If the caller provides 'segment_to_run' and a full workflow_config,
        # partition the workflow and execute only the requested segment.
        if body.get("segment_to_run") is not None and body.get("workflow_config") is not None:
            seg_id = int(body.get("segment_to_run"))
            workflow_cfg = body.get("workflow_config")
            segments = partition_workflow(workflow_cfg)
            if seg_id < 0 or seg_id >= len(segments):
                return {"statusCode": 400, "body": json.dumps({"error": "invalid segment_to_run"})}
            segment = segments[seg_id]
            segment_cfg_json = _build_segment_config(segment)
            ddb_table = os.environ.get("LANGGRAPH_DDB_TABLE")
            final = run_workflow(segment_cfg_json, body.get("current_state"), body.get("user_api_keys"), ddb_table_name=ddb_table)
            # Determine status: if this segment ends at a hitp boundary the
            # caller likely expects to pause. Since segments are split at
            # hitp edges, if there is a next segment, we signal PAUSED.
            status = "COMPLETE"
            if seg_id + 1 < len(segments):
                status = "PAUSED_FOR_HITP"
            return {"statusCode": 200, "body": json.dumps({"status": status, "final_state": final})}

        ddb_table = os.environ.get("LANGGRAPH_DDB_TABLE")
        final = run_workflow(body["config_json"], body.get("initial_state"), body.get("user_api_keys"), ddb_table_name=ddb_table)
        return {"statusCode": 200, "body": json.dumps({"final_state": final})}
    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
# Note: Mangum (ASGI->Lambda adapter) is intentionally omitted here to keep
# this minimal handler free of ASGI dependencies for unit tests. If you need
# to expose the FastAPI app via Lambda/API Gateway, add Mangum in the
# deployment image and wire `handler = Mangum(app)` there.
