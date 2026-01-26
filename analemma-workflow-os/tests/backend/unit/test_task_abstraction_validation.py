"""
Task Manager Abstraction Layer Validation Tests
================================================
verify_llm_test.py의 Task Abstraction 검증 함수들에 대한 단위 테스트.

테스트 대상:
1. Provider Cross-validation (교차 검증)
2. Thought Memory Compaction (10개 한정 로직)
3. QuickFix Dynamic Generation (동적 생성)
"""

import pytest
from unittest.mock import patch, MagicMock

# 테스트 대상 모듈 import
from src.handlers.simulator.verify_llm_test import (
    verify_provider_cross_validation,
    verify_thought_memory_compaction,
    verify_quick_fix_dynamic_generation,
    verify_task_abstraction,
    _safe_get_nested,
    _simulate_task_context_from_result,
    _simulate_task_context_with_thoughts,
    PROVIDER_SERVICE_NAME_MAP,
)
from src.models.task_context import (
    TaskContext, TaskStatus, SubStatus, CostCategory, CostLineItem, CostDetail,
    QuickFix, QuickFixType, THOUGHT_HISTORY_MAX_LENGTH,
)


class TestSafeGetNested:
    """_safe_get_nested 유틸리티 함수 테스트."""
    
    def test_basic_nested_access(self):
        """기본 중첩 접근 테스트."""
        data = {'a': {'b': {'c': 'value'}}}
        assert _safe_get_nested(data, 'a', 'b', 'c') == 'value'
    
    def test_missing_key_returns_default(self):
        """누락된 키에 대해 default 반환."""
        data = {'a': {'b': 1}}
        assert _safe_get_nested(data, 'a', 'c', default='default') == 'default'
    
    def test_none_value_returns_default(self):
        """None 값에 대해 default 반환."""
        data = {'a': {'b': None}}
        assert _safe_get_nested(data, 'a', 'b', default='default') == 'default'
    
    def test_fallback_to_final_state(self):
        """final_state 내부에서 fallback 탐색."""
        data = {
            'final_state': {
                'usage': {'provider': 'gemini'}
            }
        }
        # 'usage'가 최상위에 없으면 final_state 내부에서 찾아야 함
        result = _safe_get_nested(data, 'usage', 'provider')
        assert result == 'gemini'


class TestProviderCrossValidation:
    """Provider 교차 검증 테스트."""
    
    def test_gemini_provider_match(self):
        """Gemini provider와 service_name 일치 검증."""
        runner_log = {
            'usage': {'provider': 'gemini'}
        }
        task_context_data = {
            'cost_detail': {
                'line_items': [
                    {'category': 'llm', 'service_name': 'Gemini 2.0 Flash'}
                ]
            }
        }
        
        passed, msg, checks = verify_provider_cross_validation(
            runner_log, task_context_data, {}
        )
        
        assert passed is True
        assert 'gemini' in msg.lower()
    
    def test_bedrock_provider_match(self):
        """Bedrock provider와 service_name 일치 검증."""
        runner_log = {
            'usage': {'provider': 'bedrock'}
        }
        task_context_data = {
            'cost_detail': {
                'line_items': [
                    {'category': 'llm', 'service_name': 'Bedrock Claude 3.5 Sonnet'}
                ]
            }
        }
        
        passed, msg, checks = verify_provider_cross_validation(
            runner_log, task_context_data, {}
        )
        
        assert passed is True
        assert 'bedrock' in msg.lower()
    
    def test_provider_mismatch_detected(self):
        """ASSERT_ERROR: Provider 불일치 감지 테스트.
        
        핵심 테스트: 엔진은 Gemini인데 라벨은 Bedrock인 상태를 잡아냄.
        """
        runner_log = {
            'usage': {'provider': 'gemini'}  # 실제 호출은 Gemini
        }
        task_context_data = {
            'cost_detail': {
                'line_items': [
                    {'category': 'llm', 'service_name': 'Bedrock Claude'}  # 라벨은 Bedrock
                ]
            }
        }
        
        passed, msg, checks = verify_provider_cross_validation(
            runner_log, task_context_data, {}
        )
        
        assert passed is False
        assert 'ASSERT_ERROR' in msg or '불일치' in msg
    
    def test_missing_provider_data(self):
        """Provider 데이터 누락 시 처리."""
        runner_log = {}  # provider 없음
        task_context_data = {}  # service_name 없음
        
        passed, msg, checks = verify_provider_cross_validation(
            runner_log, task_context_data, {}
        )
        
        assert passed is False
        assert '누락' in msg or 'INCOMPLETE' in msg
    
    def test_nested_final_state_provider_extraction(self):
        """final_state 내부의 provider 추출."""
        runner_log = {
            'final_state': {
                'usage': {'provider': 'gemini'}
            }
        }
        task_context_data = {
            'cost_detail': {
                'line_items': [
                    {'category': 'llm', 'service_name': 'Gemini Flash'}
                ]
            }
        }
        
        passed, msg, checks = verify_provider_cross_validation(
            runner_log, task_context_data, {}
        )
        
        assert passed is True


class TestThoughtMemoryCompaction:
    """Thought Memory Compaction (10개 한정) 테스트."""
    
    def test_under_limit_no_overflow(self):
        """10개 미만: 모두 메모리에 유지."""
        task = TaskContext(task_id="test-1")
        for i in range(5):
            task.add_thought(f"Thought {i}")
        
        task_data = task.model_dump()
        
        passed, msg, checks = verify_thought_memory_compaction(
            task_context_data=task_data,
            total_thoughts_added=5,
            test_config={}
        )
        
        assert passed is True
        assert '5' in msg
    
    def test_exactly_10_thoughts(self):
        """정확히 10개: 경계 조건 테스트."""
        task = TaskContext(task_id="test-1")
        for i in range(10):
            task.add_thought(f"Thought {i}")
        
        task_data = task.model_dump()
        
        passed, msg, checks = verify_thought_memory_compaction(
            task_context_data=task_data,
            total_thoughts_added=10,
            test_config={}
        )
        
        assert passed is True
        assert len(task.thought_history) == 10
    
    def test_15_thoughts_compaction(self):
        """15개 추가 시 10개만 메모리 유지, 나머지 S3 참조.
        
        핵심 테스트: 메모리 압착 로직이 올바르게 작동하는지 확인.
        """
        task = TaskContext(task_id="test-1")
        for i in range(15):
            task.add_thought(f"Thought {i}")
        
        # S3 참조 시뮬레이션 (실제 환경에서는 add_thought에서 설정)
        task.full_thought_trace_ref = "s3://analemma-traces/test/thoughts.json"
        
        task_data = task.model_dump()
        
        passed, msg, checks = verify_thought_memory_compaction(
            task_context_data=task_data,
            total_thoughts_added=15,
            test_config={}
        )
        
        assert passed is True
        # 메모리에는 10개만 유지
        assert len(task.thought_history) == 10
        # 전체 카운트는 15
        assert task.total_thought_count == 15
        # S3 참조 존재
        assert task.full_thought_trace_ref is not None
    
    def test_missing_s3_ref_on_overflow(self):
        """초과분이 있는데 S3 참조가 없으면 실패."""
        task_data = {
            'thought_history': [{'message': f'T{i}'} for i in range(10)],
            'total_thought_count': 15,
            'full_thought_trace_ref': None  # S3 참조 누락
        }
        
        passed, msg, checks = verify_thought_memory_compaction(
            task_context_data=task_data,
            total_thoughts_added=15,
            test_config={}
        )
        
        assert passed is False
        assert 'S3' in msg or '압착' in msg
    
    def test_total_thought_count_tracking(self):
        """total_thought_count가 올바르게 추적되는지 확인."""
        task = TaskContext(task_id="test-1")
        for i in range(20):
            task.add_thought(f"Thought {i}")
        
        task.full_thought_trace_ref = "s3://test/ref"
        task_data = task.model_dump()
        
        passed, msg, checks = verify_thought_memory_compaction(
            task_context_data=task_data,
            total_thoughts_added=20,
            test_config={}
        )
        
        # total_thought_count 체크 찾기
        count_check = next((c for c in checks if 'total_thought_count' in c['name']), None)
        assert count_check is not None
        assert count_check['passed'] is True


class TestQuickFixDynamicGeneration:
    """QuickFix 동적 생성 검증 테스트."""
    
    def test_429_rate_limit_wait_retry(self):
        """429 Rate Limit → Wait and Retry 액션.
        
        핵심 테스트: 에러 문맥에 맞는 QuickFix가 생성되는지 확인.
        """
        error_context = {
            'error_code': '429',
            'error_message': 'Rate limit exceeded'
        }
        quick_fix = {
            'fix_type': 'RETRY',
            'action_id': 'delayed_retry',
            'context': {'delay_seconds': 60}
        }
        
        passed, msg, checks = verify_quick_fix_dynamic_generation(
            error_context, quick_fix, {}
        )
        
        assert passed is True
        assert '429' in msg or 'RETRY' in msg
    
    def test_401_auth_redirect(self):
        """401 Auth Error → Redirect to Login."""
        error_context = {
            'error_code': '401',
            'error_message': 'Unauthorized'
        }
        quick_fix = {
            'fix_type': 'REDIRECT',
            'action_id': 'auth_redirect',
            'context': {'redirect_url': '/login'}
        }
        
        passed, msg, checks = verify_quick_fix_dynamic_generation(
            error_context, quick_fix, {}
        )
        
        assert passed is True
    
    def test_500_server_error_retry(self):
        """500 Server Error → Retry."""
        error_context = {
            'error_code': '500',
            'error_message': 'Internal Server Error'
        }
        quick_fix = {
            'fix_type': 'RETRY',
            'action_id': 'lambda_retry',
            'context': {}
        }
        
        passed, msg, checks = verify_quick_fix_dynamic_generation(
            error_context, quick_fix, {}
        )
        
        assert passed is True
    
    def test_validation_error_input_required(self):
        """Validation Error → Request Input."""
        error_context = {
            'error_code': 'validation',
            'error_message': 'Missing required field',
            'error_type': 'validation'
        }
        quick_fix = {
            'fix_type': 'INPUT',
            'action_id': 'request_input',
            'context': {}
        }
        
        passed, msg, checks = verify_quick_fix_dynamic_generation(
            error_context, quick_fix, {}
        )
        
        assert passed is True
    
    def test_missing_quickfix_on_error(self):
        """에러 발생 시 QuickFix 미생성 → 실패."""
        error_context = {
            'error_code': '500',
            'error_message': 'Server Error'
        }
        quick_fix = None  # QuickFix 미생성
        
        passed, msg, checks = verify_quick_fix_dynamic_generation(
            error_context, quick_fix, {}
        )
        
        assert passed is False
        assert 'QuickFix' in msg
    
    def test_wrong_quickfix_for_error_type(self):
        """잘못된 QuickFix 타입 → 실패.
        
        예: 401 인증 에러인데 RETRY 제공.
        """
        error_context = {
            'error_code': '401',
            'error_message': 'Unauthorized'
        }
        quick_fix = {
            'fix_type': 'RETRY',  # 401은 REDIRECT 필요
            'action_id': 'lambda_retry',
            'context': {}
        }
        
        passed, msg, checks = verify_quick_fix_dynamic_generation(
            error_context, quick_fix, {}
        )
        
        assert passed is False
    
    def test_missing_context_keys(self):
        """필수 context key 누락 검증.
        
        예: 429에서 delay_seconds 누락.
        """
        error_context = {
            'error_code': '429',
            'error_message': 'Rate limit'
        }
        quick_fix = {
            'fix_type': 'RETRY',
            'action_id': 'delayed_retry',
            'context': {}  # delay_seconds 누락
        }
        
        passed, msg, checks = verify_quick_fix_dynamic_generation(
            error_context, quick_fix, {}
        )
        
        # delay_seconds 누락 체크
        context_check = next((c for c in checks if 'Context Keys' in c['name']), None)
        if context_check:
            assert 'delay_seconds' in context_check.get('missing', [])


class TestSimulateTaskContext:
    """TaskContext 시뮬레이션 함수 테스트."""
    
    def test_simulate_from_gemini_result(self):
        """Gemini 결과에서 TaskContext 시뮬레이션."""
        test_result = {
            'execution_id': 'exec-123',
            'success': True,
            'final_state': {
                'usage': {
                    'provider': 'gemini',
                    'input_tokens': 100,
                    'output_tokens': 50,
                    'cost': 0.001
                }
            }
        }
        
        simulated = _simulate_task_context_from_result(test_result)
        
        assert simulated['task_id'] == 'exec-123'
        assert simulated['status'] == 'COMPLETED'
        assert 'Gemini' in simulated['cost_detail']['line_items'][0]['service_name']
    
    def test_simulate_from_bedrock_result(self):
        """Bedrock 결과에서 TaskContext 시뮬레이션."""
        test_result = {
            'execution_id': 'exec-456',
            'success': True,
            'output': {
                'usage': {
                    'provider': 'bedrock',
                    'input_tokens': 200,
                    'output_tokens': 100,
                    'cost': 0.005
                }
            }
        }
        
        simulated = _simulate_task_context_from_result(test_result)
        
        assert 'Bedrock' in simulated['cost_detail']['line_items'][0]['service_name']
    
    def test_simulate_with_thoughts(self):
        """Thought가 포함된 TaskContext 시뮬레이션."""
        test_result = {'execution_id': 'test-789', 'success': True}
        
        simulated = _simulate_task_context_with_thoughts(test_result, thought_count=15)
        
        # 메모리에는 10개만
        assert len(simulated['thought_history']) == 10
        # 전체 카운트는 15
        assert simulated['total_thought_count'] == 15
        # S3 참조 존재
        assert simulated['full_thought_trace_ref'] is not None


class TestVerifyTaskAbstractionIntegrated:
    """verify_task_abstraction 통합 테스트."""
    
    def test_full_verification_success(self):
        """전체 검증 성공 시나리오."""
        test_result = {
            'execution_id': 'exec-full-1',
            'success': True,
            'final_state': {
                'usage': {
                    'provider': 'gemini',
                    'input_tokens': 100,
                    'output_tokens': 50,
                    'cost': 0.001
                }
            }
        }
        test_config = {
            'verify_provider': True,
            'verify_thought_compaction': False
        }
        
        result = verify_task_abstraction(test_result, test_config)
        
        assert result['task_abstraction_verified'] is True
        assert 'provider_cross_validation' in result['verification_results']
    
    def test_verification_with_thought_compaction(self):
        """Thought Compaction 포함 검증."""
        test_result = {
            'execution_id': 'exec-thoughts-1',
            'success': True,
            'final_state': {
                'usage': {'provider': 'gemini', 'input_tokens': 50, 'output_tokens': 25, 'cost': 0.0005}
            }
        }
        test_config = {
            'verify_provider': True,
            'verify_thought_compaction': True,
            'simulated_thought_count': 15
        }
        
        result = verify_task_abstraction(test_result, test_config)
        
        assert 'thought_memory_compaction' in result['verification_results']
    
    def test_verification_with_error_quickfix(self):
        """에러 발생 시 QuickFix 검증 포함."""
        test_result = {
            'execution_id': 'exec-error-1',
            'success': False,
            'error': {
                'code': '500',
                'message': 'Internal Server Error'
            },
            'final_state': {
                'usage': {'provider': 'gemini', 'input_tokens': 10, 'output_tokens': 0, 'cost': 0}
            }
        }
        test_config = {'verify_provider': True}
        
        result = verify_task_abstraction(test_result, test_config)
        
        assert 'quick_fix_generation' in result['verification_results']
