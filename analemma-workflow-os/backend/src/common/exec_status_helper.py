import json
import logging
from typing import Any, Dict
import copy

import boto3
from botocore.exceptions import ClientError
import time

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

stepfunctions = boto3.client('stepfunctions')


class ExecutionNotFound(Exception):
    pass


class ExecutionForbidden(Exception):
    pass


def _safe_json_load(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
    return raw


def _safe_json_compatible(value: Any) -> Any:
    if value is None:
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        try:
            return json.loads(json.dumps(str(value)))
        except Exception:
            return str(value)


def _iso_or_str(value: Any) -> Any:
    try:
        if hasattr(value, 'isoformat'):
            return value.isoformat()
    except Exception:
        pass
    return str(value) if value is not None else None


def _message_for_status(status: str) -> str | None:
    if status == 'RUNNING':
        return 'Workflow is running.'
    if status == 'SUCCEEDED':
        return 'Workflow completed successfully.'
    if status == 'FAILED':
        return 'Workflow failed.'
    if status == 'TIMED_OUT':
        return 'Workflow timed out.'
    return None


def describe_execution(execution_arn: str) -> Dict[str, Any]:
    try:
        return stepfunctions.describe_execution(executionArn=execution_arn)
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code')
        if code in ('ExecutionDoesNotExist', 'StateMachineDoesNotExist'):
            raise ExecutionNotFound() from src.exc
        logger.exception('DescribeExecution error for %s: %s', execution_arn, exc)
        raise


def build_status_payload(execution_arn: str, owner_id: str) -> Dict[str, Any]:
    desc = describe_execution(execution_arn)
    status = desc.get('status')
    start_date = desc.get('startDate')
    stop_date = desc.get('stopDate')

    input_raw = desc.get('input')
    sfn_input = _safe_json_load(input_raw) or {}
    execution_owner_id = sfn_input.get('ownerId')
    if execution_owner_id and execution_owner_id != owner_id:
        raise ExecutionForbidden()

    inner_payload = {
        'execution_id': execution_arn,
        'status': status,
        'startDate': _iso_or_str(start_date),
        'stopDate': _iso_or_str(stop_date),
        'input': sfn_input
    }

    try:
        current_segment = None
        total_segments = sfn_input.get('total_segments')
        state_data = sfn_input  # Fallback

        if current_segment is None:
            current_segment = (
                sfn_input.get('segment_to_run')
                or sfn_input.get('current_segment')
                or sfn_input.get('segmentIndex')
            )

        # Normalize to int if possible
        try:
            current_segment = int(current_segment) if current_segment is not None else 0
        except Exception:
            current_segment = 0

        # Determine start_time (epoch seconds) from src.state_data or DescribeExecution startDate
        start_time = None
        if isinstance(state_data, dict):
            st = state_data.get('start_time') or state_data.get('startTime')
            try:
                start_time = int(st) if isinstance(st, (int, float)) else None
            except Exception:
                start_time = None

        if start_time is None and start_date is not None:
            try:
                # boto3 DescribeExecution returns datetime for startDate
                start_time = int(start_date.timestamp())
            except Exception:
                try:
                    start_time = int(float(start_date))
                except Exception:
                    start_time = None

        now_ts = int(time.time())
        average_segment_duration = None
        estimated_remaining_seconds = None
        estimated_completion_time = None
        if total_segments and isinstance(total_segments, (int, float)) and total_segments > 1 and current_segment >= 0 and start_time:
            # completed segments are current_segment (segments processed so far)
            completed_segments = current_segment
            if completed_segments > 0:
                total_elapsed = max(0, now_ts - int(start_time))
                average_segment_duration = int(total_elapsed / completed_segments)
                remaining_segments = max(int(total_segments) - completed_segments - 1, 0)
                estimated_remaining_seconds = int(average_segment_duration * remaining_segments)
                estimated_completion_time = now_ts + estimated_remaining_seconds

        # Attach estimation fields (normalize None -> null in JSON)
        inner_payload['current_segment'] = current_segment
        inner_payload['average_segment_duration'] = average_segment_duration
        inner_payload['estimated_remaining_seconds'] = estimated_remaining_seconds
        inner_payload['estimated_completion_time'] = estimated_completion_time
    except Exception:
        # Non-fatal: best-effort estimates only
        logger.exception('Failed to compute ETA/segment estimates for %s', execution_arn)

    return {
        'type': 'workflow_status',
        'payload': inner_payload,
    }