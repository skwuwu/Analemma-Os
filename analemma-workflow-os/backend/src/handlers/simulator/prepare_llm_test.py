"""
LLM Pipeline Test Preparation Lambda
=====================================
LLM Simulator Step Functions에서 호출됩니다.
실제 프로덕션 스키마(nodes/edges/aiModel)의 워크플로 파일을 로드하고,
MOCK_MODE=false로 분산 오케스트레이터에 실행을 위임합니다.

실행 파이프라인 점검 시나리오 (tests/backend/workflows/ 사용):
  COMPLETE            — aiModel 노드 포함 Happy Path 파이프라인
  FAIL                — 에러 처리 파이프라인
  MAP_AGGREGATOR      — 병렬 처리 + 결과 집계 파이프라인
  LOOP_LIMIT          — 동적 루프 제한 파이프라인
  LOOP_BRANCH_STRESS  — 루프 + 분기 + 상태 축적 + S3 오프로드 복합 테스트
  STRESS              — 극한 스트레스 (루프 내부 HITL + 병렬 데이터 레이스)
  VISION              — 멀티모달 비전 (메모리 추정, 인젝션 방어, 상태 오프로드)
  HITP_RECOVERY       — HITP 후 정상 복구 로직 테스트
  ASYNC_LLM           — 비동기 LLM 실행 파이프라인
LLM Stage 시리즈 (점진적 토합 스테이지 테스트):
  LLM_STAGE1          — LLM 기초: Response Schema 준수 + json_parse 성능
  LLM_STAGE2          — ForEach 병렬 LLM + COST_GUARDRAIL + HITP 승인
  LLM_STAGE3          — 멀티모달 기초: S3 이미지 하이드레이션 + Vision JSON 추출
  LLM_STAGE4          — 5장 이미지 병렬 분석 + SPEED_GUARDRAIL 동시성 제어
  LLM_STAGE5          — 3단계 재귀 + Partial Failure + Context Caching + ALL_GUARDRAILS
  LLM_STAGE6          — 분산 MAP_REDUCE + Loop + HITL 통합
  LLM_STAGE7          — 동시 다중 LLM 호출 + StateBag 병합 검증
  LLM_STAGE8          — 쉘렛 탐지 & 품질 게이트 (_kernel_quality_check)"""

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
    # 파이프라인 기본 시나리오
    'COMPLETE':            'test_complete_workflow',
    'FAIL':                'test_fail_workflow',
    'MAP_AGGREGATOR':      'test_map_aggregator_workflow',
    'LOOP_LIMIT':          'test_loop_limit_dynamic_workflow',
    'LOOP_BRANCH_STRESS':  'test_loop_branch_stress_workflow',
    'STRESS':              'test_hyper_stress_workflow',
    'VISION':              'test_vision_workflow',
    'HITP_RECOVERY':       'test_hitp_workflow',
    'ASYNC_LLM':           'test_async_llm_workflow',
    # LLM Stage 시리즈 (점진적 토합 스테이지 테스트)
    'LLM_STAGE1':          'test_llm_stage1_basic',
    'LLM_STAGE2':          'test_llm_stage2_flow_control',
    'LLM_STAGE3':          'test_llm_stage3_vision_basic',
    'LLM_STAGE4':          'test_llm_stage4_vision_map',
    'LLM_STAGE5':          'test_llm_stage5_hyper_stress',
    'LLM_STAGE6':          'test_llm_stage6_distributed_map_reduce',
    'LLM_STAGE7':          'test_llm_stage7_parallel_multi_llm',
    'LLM_STAGE8':          'test_llm_stage8_slop_detection',
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
    'LOOP_BRANCH_STRESS': {
        'pipeline_test_enabled': True,
        'verify_loop_branch_integration': True,
        'verify_s3_offload': True,
        'expected_outer_count': 5,
    },
    'STRESS': {
        'pipeline_test_enabled': True,
        'verify_extreme_stress': True,
        'expected_outer_count': 4,
    },
    'VISION': {
        'pipeline_test_enabled': True,
        'verify_multimodal': True,
        'product_image': 's3://test-bucket/large_product_8mb.jpg',
    },
    'HITP_RECOVERY': {
        'pipeline_test_enabled': True,
        'verify_hitp_recovery': True,
    },
    'ASYNC_LLM': {
        'pipeline_test_enabled': True,
        'verify_async_result': True,
    },
    # LLM Stage 시리즈
    'LLM_STAGE1': {
        'pipeline_test_enabled': True,
        'input_text': 'Artificial intelligence is transforming the software industry by automating complex tasks, enabling natural language interfaces, and accelerating development workflows.',
    },
    'LLM_STAGE2': {
        'pipeline_test_enabled': True,
        'verify_for_each_parallel': True,
        'verify_hitl': True,
        'hitl_decision': 'approve',
    },
    'LLM_STAGE3': {
        'pipeline_test_enabled': True,
        'image_uri': 's3://analemma-test-assets/sample_receipt.jpg',
        'verify_vision_extraction': True,
    },
    'LLM_STAGE4': {
        'pipeline_test_enabled': True,
        'image_uri_1': 's3://analemma-test-assets/product_electronics_1.jpg',
        'image_uri_2': 's3://analemma-test-assets/product_clothing_1.jpg',
        'image_uri_3': 's3://analemma-test-assets/product_food_1.jpg',
        'image_uri_4': 's3://analemma-test-assets/product_electronics_2.jpg',
        'image_uri_5': 's3://analemma-test-assets/product_furniture_1.jpg',
        'verify_concurrency_limit': True,
    },
    'LLM_STAGE5': {
        'pipeline_test_enabled': True,
        'document_content': 'Project Alpha Q4 Report: Critical infrastructure vulnerabilities detected in authentication module (image evidence required). Performance degradation observed in payment processing (screenshot needed).',
        'verify_partial_failure_recovery': True,
        'verify_context_caching': True,
    },
    'LLM_STAGE6': {
        'pipeline_test_enabled': True,
        'partition_map': [
            {'partition_id': 0, 'items': [{'content': 'Cloud scalability enables elastic workloads.'}, {'content': 'Serverless reduces operational overhead.'}]},
            {'partition_id': 1, 'items': [{'content': 'Distributed systems require careful consistency management.'}, {'content': 'Event sourcing provides complete audit trails.'}]},
            {'partition_id': 2, 'items': [{'content': 'Microservices decouple business capabilities.'}, {'content': 'API gateways centralize cross-cutting concerns.'}]},
        ],
        'loop_convergence_threshold': 0.8,
        'max_loop_per_partition': 2,
        'verify_distributed_reduce': True,
    },
    'LLM_STAGE7': {
        'pipeline_test_enabled': True,
        'verify_statebag_merge': True,
        'verify_state_isolation': True,
        'max_loop_per_branch': 2,
        'hitl_decision': 'approve',
    },
    'LLM_STAGE8': {
        'pipeline_test_enabled': True,
        'test_cases': [
            {'case_id': 'CASE_001', 'domain': 'TECHNICAL_WRITING', 'system_prompt': 'You are a technical writer.', 'prompt': 'Explain what a REST API is.', 'expected_is_slop': False, 'inject_slop': False},
            {'case_id': 'CASE_002', 'domain': 'CREATIVE', 'system_prompt': 'Be very verbose and use filler phrases.', 'prompt': 'Describe the sunset.', 'expected_is_slop': True, 'inject_slop': True},
            {'case_id': 'CASE_003', 'domain': 'CODE_REVIEW', 'system_prompt': 'You are a code reviewer.', 'prompt': 'Review this Python function: def add(a, b): return a + b', 'expected_is_slop': False, 'inject_slop': False},
        ],
        'verify_slop_detection_accuracy': True,
    },
}


def _find_workflow_path(scenario_name: str) -> str:
    """
    시나리오에 해당하는 워크플로 파일 경로를 반환합니다.

    워크플로 파일 위치: backend/src/test_workflows/<file>.json
    Lambda 배포 시 Docker 이미지에 포함됨 (/var/task/src/test_workflows/)

    탐색 순서:
      1. Lambda 배포 환경: /var/task/src/test_workflows/<file>.json
      2. 로컬 개발 환경: 핸들러 위치에서 2단계 상위 = backend/src/

    Raises:
        KeyError: 알 수 없는 시나리오 이름
        FileNotFoundError: 워크플로 파일을 찾을 수 없을 때
    """
    workflow_id = PIPELINE_TEST_MAPPINGS[scenario_name]  # KeyError on unknown scenario
    filename = f"{workflow_id}.json"

    # 1. Lambda 배포 환경 (/var/task/src/test_workflows/ — Docker 이미지에 포함)
    lambda_path = os.path.join("/var/task/src/test_workflows", filename)
    if os.path.exists(lambda_path):
        return lambda_path

    # 2. 로컬 개발 환경 (handlers/simulator → ../../ = backend/src/)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    local_path = os.path.join(current_dir, "../../test_workflows", filename)
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

    with open(workflow_path, 'r', encoding='utf-8-sig') as f:
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
