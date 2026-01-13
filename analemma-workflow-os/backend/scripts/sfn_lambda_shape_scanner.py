#!/usr/bin/env python3
"""Scan aws_step_functions.json and local lambda handlers for return-shape mismatches.

Heuristics:
- Map CloudFormation variable names like ${StoreTaskTokenFunction.Arn} -> handler file
  by stripping 'Function' and converting CamelCase -> snake_case and trying common suffixes.
- For each Task state with a ResultPath (e.g. $.callback_result) and any references
  to that result's `.Payload` later in the state machine, check the mapped handler
  source file for presence of expected keys (e.g. 'state_data', 'conversation_id').

Usage: run from repo root: `python3 scripts/sfn_lambda_shape_scanner.py`
"""
import json
import os
import re
from typing import Dict, List, Optional


ROOT = os.path.dirname(os.path.dirname(__file__))
SFN_PATH = os.path.join(ROOT, 'backend', 'aws_step_functions.json')
HANDLER_DIR = os.path.join(ROOT, 'backend')


def camel_to_snake(name: str) -> str:
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def candidate_handler_names(varname: str) -> List[str]:
    # strip ${ and .Arn if present
    v = varname
    v = re.sub(r"\$\{?|\}?", '', v)
    v = re.sub(r"\.Arn$", '', v)
    # common pattern: SomethingFunction -> something
    if v.endswith('Function'):
        base = v[:-8]
    else:
        base = v
    snake = camel_to_snake(base)
    candidates = [f"{snake}.py", f"{snake}_lambda.py", f"{snake}_handler.py", f"{snake}.py"]
    # also try some known mapping rules
    alt = snake.replace('lambda_', '')
    if alt != snake:
        candidates.append(f"{alt}.py")
    return list(dict.fromkeys(candidates))


def find_handler_file(varname: str) -> Optional[str]:
    for cand in candidate_handler_names(varname):
        path = os.path.join(HANDLER_DIR, cand)
        if os.path.exists(path):
            return path
    # try fuzz: look for files containing base words
    base = re.sub(r"Function$", '', re.sub(r"\$\{|\}|\.Arn", '', varname))
    base_snake = camel_to_snake(base)
    for fname in os.listdir(HANDLER_DIR):
        if fname.endswith('.py') and base_snake in fname:
            return os.path.join(HANDLER_DIR, fname)
    return None


def extract_literal_keys_from_file(path: str) -> List[str]:
    try:
        txt = open(path, 'r', encoding='utf-8').read()
    except Exception:
        return []
    # find literal dict key strings like 'state_data' or "state_data"
    keys = set(re.findall(r"['\"]([a-zA-Z0-9_]+)['\"]\s*:\s*", txt))
    # also look for occurrences of result['key'] or result.get('key')
    keys.update(re.findall(r"\.get\(\s*['\"]([a-zA-Z0-9_]+)['\"]", txt))
    keys.update(re.findall(r"\[\s*['\"]([a-zA-Z0-9_]+)['\"]\s*\]", txt))
    return sorted(keys)


def load_sfn(path: str) -> Dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    if not os.path.exists(SFN_PATH):
        print('Cannot find aws_step_functions.json at', SFN_PATH)
        return 1
    sfn = load_sfn(SFN_PATH)
    states = sfn.get('States', {})

    # build map of state result names -> state node
    resultpaths = {}
    for name, node in states.items():
        rp = node.get('ResultPath')
        if isinstance(rp, str) and rp.startswith('$.'):
            resultpaths[rp[2:]] = name

    findings = []

    # scan for Task nodes with FunctionName
    for name, node in states.items():
        if node.get('Type') != 'Task':
            continue
        resource = node.get('Resource', '')
        params = node.get('Parameters', {})
        fn = params.get('FunctionName') or params.get('Function') or params.get('FunctionName.$')
        # Normalize if FnName.$ present
        if isinstance(fn, str) and fn.startswith('${'):
            fnvar = fn
        elif isinstance(fn, str) and fn.startswith('arn:'):
            fnvar = fn
        else:
            fnvar = fn

        result_path = node.get('ResultPath')
        result_name = result_path[2:] if isinstance(result_path, str) and result_path.startswith('$.') else None
        is_wait = 'waitForTaskToken' in resource

        # find downstream references to this result's Payload
        uses_payload_refs = []
        if result_name:
            # search entire JSON text for references to this result's Payload
            sfn_text = json.dumps(sfn)
            payload_patterns = [f"$.{result_name}.Payload", f"$.{result_name}.Payload.state_data", f"$.{result_name}.Payload.conversation_id"]
            for p in payload_patterns:
                if p in sfn_text:
                    uses_payload_refs.append(p)

        handler_file = None
        if isinstance(fnvar, str) and fnvar.startswith('${'):
            handler_file = find_handler_file(fnvar)

        keys = []
        if handler_file:
            keys = extract_literal_keys_from_file(handler_file)

        findings.append({
            'state': name,
            'function': fnvar,
            'resource': resource,
            'result_name': result_name,
            'is_waitForTaskToken': is_wait,
            'uses_payload_refs': uses_payload_refs,
            'handler_file': handler_file,
            'handler_keys': keys,
        })

    # Print report
    print('\nSFN -> Lambda shape scanner report\n')
    for f in findings:
        print('State:', f['state'])
        print('  Resource:', f['resource'])
        print('  Function:', f['function'])
        print('  ResultPath:', f['result_name'])
        print('  waitForTaskToken:', f['is_waitForTaskToken'])
        if f['uses_payload_refs']:
            print('  Uses payload refs:', ', '.join(f['uses_payload_refs']))
        if f['handler_file']:
            print('  Mapped handler:', f['handler_file'])
            print('  Detected keys in handler:', ', '.join(f['handler_keys'][:30]) or '(none)')
            # quick checks
            for expect in ('state_data', 'workflow_config', 'conversation_id', 'new_current_state', 'new_state_s3_path', 'final_state', 'final_state_s3_path', 'next_segment_to_run'):
                if any(k == expect for k in f['handler_keys']):
                    print(f"    - provides: {expect}")
        else:
            print('  Mapped handler: (not found heuristically)')
        # simple heuristic warning
        if f['is_waitForTaskToken'] and f['uses_payload_refs'] and f['handler_file']:
            # if payload refs present but handler doesn't contain 'state_data' warn
            if not any(k == 'state_data' for k in f['handler_keys']):
                print('  WARNING: waitForTaskToken result is later referenced as .Payload.state_data but handler does not appear to return state_data')
        print('')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
