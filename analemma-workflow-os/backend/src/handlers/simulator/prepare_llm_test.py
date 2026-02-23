"""
LLM Pipeline Test Preparation Lambda
=====================================
LLM Simulator Step Functions에서 호출됩니다.
실제 프로덕션 스키마(nodes/edges/aiModel)의 워크플로 파일을 로드하고,
MOCK_MODE=false로 분산 오케스트레이터에 실행을 위임합니다.

실행 파이프라인 점검 시나리오 (tests/backend/workflows/ 사용):
  COMPLETE       — aiModel 노드 포함 Happy Path 파이프라인
  FAIL           — 에러 처리 파이프라인
  MAP_AGGREGATOR — 병렬 처리 + 결과 집계 파이프라인
  LOOP_LIMIT     — 동적 루프 제한 파이프라인
  S3_LARGE       — S3 Offload 파이프라인 (300KB+ 페이로드)
  ASYNC_LLM      — 비동기 LLM 실행 파이프라인
"""

import json
import os
import uuid
import logging
from typing import Dict, Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Orchestrator ARNs
DISTRIBUTED_ORCHESTRATOR_ARN = os.environ.get('WORKFLOW_DISTRIBUTED_ORCHESTRATOR_ARN')
MOCK_MODE = 'false'       # 파이프라인 점검은 항상 실제 LLM 호출
AUTO_RESUME_HITP = 'true' # HITP 자동 승인 (무한대기 방지)

# ─── 파이프라인 테스트 시나리오 매핑 ───────────────────────────────────────────
# 실제 프로덕션 스키마 워크플로 파일 (tests/backend/workflows/*.json)

PIPELINE_TEST_MAPPINGS: Dict[str, str] = {
    'COMPLETE':       'test_complete_workflow',
    'FAIL':           'test_fail_workflow',
    'MAP_AGGREGATOR': 'test_map_aggregator_workflow',
    'LOOP_LIMIT':     'test_loop_limit_dynamic_workflow',
    'S3_LARGE':       'test_s3_large_workflow',
    'ASYNC_LLM':      'test_async_llm_workflow',
}

# 시나리오별 추가 initial_state 주입값
PIPELINE_SCENARIO_INPUT: Dict[str, Dict[str, Any]] = {
    'COMPLETE': {
        'pipeline_test_enabled': True,
    },
    'FAIL': {
        'pipeline_test_enabled': True,
        'expected_failure': True,
    },
    'MAP_AGGREGATOR': {
        'pipeline_test_enabled': True,
        'verify_aggregation': True,
    },
    'LOOP_LIMIT': {
        'pipeline_test_enabled': True,
        'verify_loop_count': True,
    },
    'S3_LARGE': {
        'pipeline_test_enabled': True,
        'verify_s3_offload': True,
        'expected_payload_size_kb': 300,
    },
    'ASYNC_LLM': {
        'pipeline_test_enabled': True,
        'verify_async_result': True,
    },
}


def _find_workflow_path(scenario_name: str) -> str:
    """
    시나리오에 해당하는 워크플로 파일 경로를 반환합니다.

    탐색 순서:
      1. Lambda 배포 환경: /var/task/tests/backend/workflows/<file>.json
      2. 로컬 개발 환경: 핸들러 위치에서 4단계 상위 = 프로젝트 루트

    Raises:
        KeyError: 알 수 없는 시나리오 이름
        FileNotFoundError: 워크플로 파일을 찾을 수 없을 때
    """
    workflow_id = PIPELINE_TEST_MAPPINGS[scenario_name]  # KeyError on unknown scenario
    filename = f"{workflow_id}.json"

    # 1. Lambda 배포 환경 (/var/task 고정)
    lambda_path = os.path.join("/var/task/tests/backend/workflows", filename)
    if os.path.exists(lambda_path):
        return lambda_path

    # 2. 로컬 개발 환경 (handlers/simulator → ../../../../ = 프로젝트 루트)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(current_dir, "../../../../tests/backend/workflows", filename)
    if os.path.exists(local_path):
        return os.path.abspath(local_path)

    raise FileNotFoundError(
        f"Workflow file '{filename}' not found.\n"
        f"  Searched: {lambda_path}\n"
        f"  Searched: {os.path.abspath(local_path)}"
    )


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    LLM 파이프라인 테스트 실행을 준비합니다.

    Input:  {"scenario": "COMPLETE", "simulator_execution_id": "..."}
    Output: {"targetArn": "...", "name": "...", "input": {...}, "scenario": "..."}
    """
    scenario_key = event.get('scenario', 'COMPLETE')
    sim_exec_id = event.get('simulator_execution_id', 'unknown-sim')

    logger.info(f"Preparing pipeline test: scenario={scenario_key}")

    if not DISTRIBUTED_ORCHESTRATOR_ARN:
        raise ValueError("WORKFLOW_DISTRIBUTED_ORCHESTRATOR_ARN not configured")

    # 워크플로 파일 로드
    try:
        workflow_path = _find_workflow_path(scenario_key)
    except KeyError:
        raise ValueError(
            f"Unknown scenario: '{scenario_key}'. "
            f"Valid scenarios: {list(PIPELINE_TEST_MAPPINGS.keys())}"
        )

    with open(workflow_path, 'r', encoding='utf-8') as f:
        workflow_config = json.load(f)

    logger.info(f"Loaded workflow: {workflow_path}")

    # 실행 이름 생성 (SFN 80자 제한)
    safe_scenario = scenario_key.replace('_', '-')[:30]
    short_sim_id = sim_exec_id.split(':')[-1][-12:]
    random_suffix = uuid.uuid4().hex[:4]
    execution_name = f"llm-{short_sim_id}-{safe_scenario}-{random_suffix}"

    # 시나리오별 input_data
    input_data = PIPELINE_SCENARIO_INPUT.get(scenario_key, {})

    payload = {
        'workflowId': f'llm-pipeline-{scenario_key.lower().replace("_", "-")}',
        'ownerId': 'system',
        'user_id': 'system',
        'MOCK_MODE': MOCK_MODE,                      # false — 실제 LLM 호출
        'AUTO_RESUME_HITP': AUTO_RESUME_HITP,        # HITP 자동 승인
        'initial_state': {
            'llm_pipeline_scenario': scenario_key,
            'llm_execution_id': execution_name,
            **input_data,
        },
        'idempotency_key': f"llm-pipeline#{scenario_key}#{execution_name}",
        'ALLOW_UNSAFE_EXECUTION': True,
        'test_workflow_config': workflow_config,     # 실제 스키마 워크플로 주입
    }

    logger.info(
        f"Pipeline test ready: scenario={scenario_key} "
        f"workflow_nodes={len(workflow_config.get('nodes', []))} "
        f"MOCK_MODE={MOCK_MODE}"
    )

    return {
        "targetArn": DISTRIBUTED_ORCHESTRATOR_ARN,
        "name": execution_name,
        "input": payload,
        "scenario": scenario_key,
    }
