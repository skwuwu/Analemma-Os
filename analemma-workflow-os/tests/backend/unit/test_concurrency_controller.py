"""
4단계 아키텍처 종합 테스트

1단계: Reserved Concurrency (RC) - template.yaml 설정 확인
2단계: 커널 스케줄링 및 부하 평탄화
3단계: 지능형 품질 및 재시도 제어
4단계: 비용 및 드리프트 모니터링
"""

import pytest
import time
from unittest.mock import Mock, patch


class TestReservedConcurrency:
    """1단계: Reserved Concurrency (RC) 검증"""
    
    def test_template_yaml_has_reserved_concurrency(self):
        """template.yaml에 ReservedConcurrentExecutions 설정 확인"""
        import os
        
        # 프로젝트 루트에서 상대 경로로 접근
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        template_path = os.path.join(base_dir, 'backend', 'template.yaml')
        
        with open(template_path, 'r') as f:
            content = f.read()
        
        # SegmentRunnerFunction에 ReservedConcurrentExecutions 설정 확인
        assert 'ReservedConcurrentExecutions: 200' in content
        assert '# 🛡️ Reserved Concurrency' in content


class TestKernelScheduler:
    """2단계: 커널 스케줄링 및 부하 평탄화"""
    
    def test_load_level_detection(self):
        """부하 레벨 감지"""
        from src.services.quality_kernel.concurrency_controller import (
            KernelTaskScheduler,
            LoadLevel
        )
        
        scheduler = KernelTaskScheduler(reserved_concurrency=100)
        
        # 초기 상태: LOW
        snapshot = scheduler.get_concurrency_snapshot()
        assert snapshot.load_level == LoadLevel.LOW
        assert snapshot.utilization_ratio == 0.0
    
    def test_concurrency_slot_management(self):
        """동시성 슬롯 관리"""
        from src.services.quality_kernel.concurrency_controller import (
            KernelTaskScheduler,
            LoadLevel
        )
        
        scheduler = KernelTaskScheduler(reserved_concurrency=10)
        
        # 슬롯 획득
        for i in range(10):
            assert scheduler.acquire_execution_slot() is True
        
        # 한도 초과
        assert scheduler.acquire_execution_slot() is False
        
        # 슬롯 해제
        scheduler.release_execution_slot()
        
        # 다시 획득 가능
        assert scheduler.acquire_execution_slot() is True
    
    def test_throttling_applies_at_high_load(self):
        """고부하 시 쓰로틀링 적용"""
        from src.services.quality_kernel.concurrency_controller import (
            KernelTaskScheduler,
            LoadLevel
        )
        
        scheduler = KernelTaskScheduler(
            reserved_concurrency=10,
            enable_throttling=True
        )
        
        # 부하 증가 시뮬레이션
        for _ in range(9):  # 90% 사용
            scheduler.acquire_execution_slot()
        
        snapshot = scheduler.get_concurrency_snapshot()
        assert snapshot.load_level == LoadLevel.CRITICAL
        
        # 쓰로틀링 지연 확인
        delay = scheduler.THROTTLE_DELAYS[LoadLevel.CRITICAL]
        assert delay > 0
    
    def test_batching_for_operator_nodes(self):
        """operator 노드 배치 처리 판단"""
        from src.services.quality_kernel.concurrency_controller import (
            KernelTaskScheduler
        )
        
        scheduler = KernelTaskScheduler(enable_batching=True)
        
        # operator 타입: 배치 대상
        operator_config = {
            'type': 'operator',
            'config': {'code': 'x = 1 + 1'}
        }
        assert scheduler.should_batch(operator_config) is True
        
        # LLM 노드: 배치 불가
        llm_config = {
            'type': 'operator',
            'config': {'model': 'gemini-1.5-flash'}
        }
        assert scheduler.should_batch(llm_config) is False
        
        # 긴 코드: 배치 불가
        long_code_config = {
            'type': 'operator',
            'config': {'code': 'x = 1\n' * 200}
        }
        assert scheduler.should_batch(long_code_config) is False


class TestIntelligentRetryController:
    """3단계: 지능형 품질 및 재시도 제어"""
    
    def test_adaptive_threshold_normal_load(self):
        """정상 부하에서 기본 임계값 사용"""
        from src.services.quality_kernel.concurrency_controller import (
            AdaptiveThresholdConfig,
            LoadLevel
        )
        
        config = AdaptiveThresholdConfig(
            base_quality_threshold=0.6,
            min_quality_threshold=0.3
        )
        
        threshold = config.get_effective_threshold(LoadLevel.NORMAL, retry_count=0)
        assert threshold == 0.5  # NORMAL은 1레벨 완화 (0.6 - 0.1)
    
    def test_adaptive_threshold_high_load_reduces(self):
        """고부하 시 임계값 완화"""
        from src.services.quality_kernel.concurrency_controller import (
            AdaptiveThresholdConfig,
            LoadLevel
        )
        
        config = AdaptiveThresholdConfig(
            base_quality_threshold=0.6,
            threshold_reduction_per_level=0.1,
            min_quality_threshold=0.3
        )
        
        # LOW: 0.6 (감소 없음)
        # NORMAL: 0.5 (1레벨 × 0.1)
        # HIGH: 0.4 (2레벨 × 0.1)
        # CRITICAL: 0.3 (3레벨 × 0.1)
        
        assert abs(config.get_effective_threshold(LoadLevel.LOW, 0) - 0.6) < 0.01
        assert abs(config.get_effective_threshold(LoadLevel.HIGH, 0) - 0.4) < 0.01
        assert abs(config.get_effective_threshold(LoadLevel.CRITICAL, 0) - 0.3) < 0.01
    
    def test_adaptive_threshold_retry_reduces(self):
        """재시도 횟수 증가 시 임계값 완화"""
        from src.services.quality_kernel.concurrency_controller import (
            AdaptiveThresholdConfig,
            LoadLevel
        )
        
        config = AdaptiveThresholdConfig(
            base_quality_threshold=0.6,
            threshold_reduction_per_retry=0.1,
            min_quality_threshold=0.3
        )
        
        # 0회: 0.6, 1회: 0.5, 2회: 0.4, 3회: 0.3 (최소값)
        assert abs(config.get_effective_threshold(LoadLevel.LOW, 0) - 0.6) < 0.01
        assert abs(config.get_effective_threshold(LoadLevel.LOW, 1) - 0.5) < 0.01
        assert abs(config.get_effective_threshold(LoadLevel.LOW, 2) - 0.4) < 0.01
        assert abs(config.get_effective_threshold(LoadLevel.LOW, 3) - 0.3) < 0.01
        assert abs(config.get_effective_threshold(LoadLevel.LOW, 5) - 0.3) < 0.01  # 최소값 유지
    
    def test_distill_instead_of_retry_when_max_retries(self):
        """최대 재시도 도달 시 증류 선택"""
        from src.services.quality_kernel.concurrency_controller import (
            IntelligentRetryController
        )
        
        controller = IntelligentRetryController(max_retries=3)
        
        # 3회 재시도 기록
        for _ in range(3):
            controller.record_retry(
                node_id='test_node',
                quality_score=0.4,
                action_taken='RETRY',
                success=False
            )
        
        should_distill, reason = controller.should_distill_instead_of_retry(
            node_id='test_node',
            quality_score=0.4,
            slop_issues=['verbose', 'hedging']
        )
        
        assert should_distill is True
        assert 'Max retries' in reason


class TestBudgetWatchdog:
    """4단계: 비용 서킷 브레이커"""
    
    def test_cost_recording(self):
        """비용 기록"""
        from src.services.quality_kernel.concurrency_controller import (
            BudgetWatchdog,
            BudgetWatchdogConfig
        )
        
        watchdog = BudgetWatchdog(BudgetWatchdogConfig(max_budget_usd=1.0))
        
        result = watchdog.record_cost(
            model='gemini-1.5-flash',
            input_tokens=1000,
            output_tokens=500,
            node_id='test'
        )
        
        assert result['cost_usd'] > 0
        assert result['action'] == 'CONTINUE'
    
    def test_warning_at_70_percent(self):
        """70% 예산 도달 시 경고"""
        from src.services.quality_kernel.concurrency_controller import (
            BudgetWatchdog,
            BudgetWatchdogConfig
        )
        
        watchdog = BudgetWatchdog(BudgetWatchdogConfig(
            max_budget_usd=0.001,  # $0.001로 낮게 설정
            warning_threshold=0.7
        ))
        
        # 비용 기록하여 70% 이상 도달
        result = watchdog.record_cost(
            model='gemini-1.5-flash',
            input_tokens=10000,
            output_tokens=5000,
            node_id='test'
        )
        
        # 총 비용이 경고 임계값을 초과하면 WARNING 또는 그 이상
        assert result['action'] in ['WARNING', 'DOWNGRADE', 'HALT']
    
    def test_downgrade_at_90_percent(self):
        """90% 예산 도달 시 모델 다운그레이드"""
        from src.services.quality_kernel.concurrency_controller import (
            BudgetWatchdog,
            BudgetWatchdogConfig
        )
        
        watchdog = BudgetWatchdog(BudgetWatchdogConfig(
            max_budget_usd=0.0005,
            critical_threshold=0.9
        ))
        
        # 비용 기록하여 90% 이상 도달
        result = watchdog.record_cost(
            model='gemini-1.5-pro',
            input_tokens=1000,
            output_tokens=500,
            node_id='test'
        )
        
        if result['budget_ratio'] >= 0.9:
            assert result['action'] in ['DOWNGRADE', 'HALT']
            if result['action'] == 'DOWNGRADE':
                assert result['new_model'] == 'gemini-1.5-flash-8b'
    
    def test_halt_at_100_percent(self):
        """100% 예산 도달 시 중단"""
        from src.services.quality_kernel.concurrency_controller import (
            BudgetWatchdog,
            BudgetWatchdogConfig
        )
        
        watchdog = BudgetWatchdog(BudgetWatchdogConfig(
            max_budget_usd=0.0001
        ))
        
        # 비용 기록하여 100% 도달
        result = watchdog.record_cost(
            model='gpt-4o',
            input_tokens=10000,
            output_tokens=5000,
            node_id='test'
        )
        
        if result['budget_ratio'] >= 1.0:
            assert result['action'] == 'HALT'
            assert watchdog.is_halted() is True
    
    def test_effective_model_override(self):
        """모델 다운그레이드 후 effective_model 반환"""
        from src.services.quality_kernel.concurrency_controller import (
            BudgetWatchdog,
            BudgetWatchdogConfig
        )
        
        watchdog = BudgetWatchdog(BudgetWatchdogConfig(max_budget_usd=0.0003))
        
        # 초기: 요청 모델 그대로
        assert watchdog.get_effective_model('gemini-1.5-pro') == 'gemini-1.5-pro'
        
        # 비용 초과 시뮬레이션
        watchdog._model_override = 'gemini-1.5-flash-8b'
        
        # 다운그레이드 적용
        assert watchdog.get_effective_model('gemini-1.5-pro') == 'gemini-1.5-flash-8b'


class TestSemanticDriftDetector:
    """4단계: 시맨틱 드리프트 감지"""
    
    def test_no_drift_on_first_output(self):
        """첫 출력은 드리프트 아님"""
        from src.services.quality_kernel.concurrency_controller import (
            SemanticDriftDetector
        )
        
        detector = SemanticDriftDetector()
        
        result = detector.check_drift("This is the first output")
        
        assert result.is_drifting is False
        assert result.consecutive_similar_count == 0
    
    def test_drift_detected_on_repeated_outputs(self):
        """동일 출력 반복 시 드리프트 감지"""
        from src.services.quality_kernel.concurrency_controller import (
            SemanticDriftDetector
        )
        
        detector = SemanticDriftDetector(
            similarity_threshold=0.95,
            max_consecutive_similar=3
        )
        
        same_output = "This is the exact same output every time."
        
        # 4번 반복 (3번 연속 유사 → 드리프트)
        for i in range(4):
            result = detector.check_drift(same_output)
        
        assert result.is_drifting is True
        assert result.consecutive_similar_count >= 3
        assert result.recommendation == 'HALT_FOR_HITL'
    
    def test_no_drift_on_varying_outputs(self):
        """다양한 출력은 드리프트 아님"""
        from src.services.quality_kernel.concurrency_controller import (
            SemanticDriftDetector
        )
        
        detector = SemanticDriftDetector()
        
        outputs = [
            "First unique output about topic A",
            "Second very different output about topic B",
            "Third output discussing something else entirely",
            "Fourth output with new information"
        ]
        
        for output in outputs:
            result = detector.check_drift(output)
        
        assert result.is_drifting is False
        assert result.consecutive_similar_count < 3


class TestConcurrencyControllerV2:
    """통합 컨트롤러 테스트"""
    
    def test_pre_execution_check_passes(self):
        """Pre-execution 체크 통과"""
        from src.services.quality_kernel.concurrency_controller import (
            ConcurrencyControllerV2
        )
        
        controller = ConcurrencyControllerV2(
            workflow_id='test',
            max_budget_usd=10.0
        )
        
        result = controller.pre_execution_check()
        
        assert result['can_proceed'] is True
        assert result['reason'] == 'OK'
    
    def test_pre_execution_check_fails_when_halted(self):
        """예산 소진 시 Pre-execution 체크 실패"""
        from src.services.quality_kernel.concurrency_controller import (
            ConcurrencyControllerV2
        )
        
        controller = ConcurrencyControllerV2(
            workflow_id='test',
            max_budget_usd=0.0001
        )
        
        # 예산 소진 시뮬레이션
        controller.budget_watchdog._halted = True
        
        result = controller.pre_execution_check()
        
        assert result['can_proceed'] is False
        assert 'Budget exhausted' in result['reason']
    
    def test_post_execution_check_detects_drift(self):
        """Post-execution 체크에서 드리프트 감지"""
        from src.services.quality_kernel.concurrency_controller import (
            ConcurrencyControllerV2
        )
        
        controller = ConcurrencyControllerV2(workflow_id='test')
        
        # 동일 출력 반복
        same_output = "Repeated output message here"
        
        for _ in range(4):
            result = controller.post_execution_check(
                output_text=same_output,
                model='gemini-1.5-flash',
                input_tokens=100,
                output_tokens=50,
                node_id='test_node'
            )
        
        # 마지막 결과에서 드리프트 감지
        assert result['drift_result'].is_drifting is True
        assert result['should_halt'] is True
        assert 'drift' in result['halt_reason'].lower()
    
    def test_comprehensive_stats(self):
        """종합 통계 반환"""
        from src.services.quality_kernel.concurrency_controller import (
            ConcurrencyControllerV2
        )
        
        controller = ConcurrencyControllerV2(workflow_id='stats_test')
        
        stats = controller.get_comprehensive_stats()
        
        assert 'workflow_id' in stats
        assert 'scheduler' in stats
        assert 'retry' in stats
        assert 'budget' in stats
        assert stats['workflow_id'] == 'stats_test'


class TestDistributedStateManager:
    """v2.0: 분산 환경 상태 동기화 테스트"""
    
    def test_local_mode_fallback(self):
        """DynamoDB 연결 실패 시 로컬 모드 폴백"""
        from src.services.quality_kernel.concurrency_controller import (
            DistributedStateManager,
            DistributedStateConfig
        )
        
        # 분산 모드 비활성화
        config = DistributedStateConfig(enable_distributed=False)
        manager = DistributedStateManager(config)
        
        # 로컬 모드에서 정상 동작
        count = manager.increment_executions(5)
        assert count == 5
        
        count = manager.decrement_executions(2)
        assert count == 3
        
        state = manager.get_global_state()
        assert state['active_executions'] == 3
        assert state['is_distributed'] is False
    
    def test_local_cost_tracking(self):
        """로컬 모드 비용 추적"""
        from src.services.quality_kernel.concurrency_controller import (
            DistributedStateManager,
            DistributedStateConfig
        )
        
        config = DistributedStateConfig(enable_distributed=False)
        manager = DistributedStateManager(config)
        
        cost1 = manager.add_cost(0.001, "workflow_1")
        assert cost1 == pytest.approx(0.001, rel=1e-6)
        
        cost2 = manager.add_cost(0.002, "workflow_2")
        assert cost2 == pytest.approx(0.003, rel=1e-6)
        
        state = manager.get_global_state()
        assert state['accumulated_cost'] == pytest.approx(0.003, rel=1e-6)
    
    def test_reset_global_state(self):
        """전역 상태 초기화"""
        from src.services.quality_kernel.concurrency_controller import (
            DistributedStateManager,
            DistributedStateConfig
        )
        
        config = DistributedStateConfig(enable_distributed=False)
        manager = DistributedStateManager(config)
        
        manager.increment_executions(10)
        manager.add_cost(0.5, "test")
        
        result = manager.reset_global_state()
        assert result is True
        
        state = manager.get_global_state()
        assert state['active_executions'] == 0
        assert state['accumulated_cost'] == 0.0


class TestFastTrack:
    """v2.0: Fast Track 경로 테스트"""
    
    def test_priority_extraction_from_task_config(self):
        """task_config에서 우선순위 추출"""
        from src.services.quality_kernel.concurrency_controller import (
            KernelTaskScheduler,
            TaskPriority
        )
        
        scheduler = KernelTaskScheduler(enable_distributed_state=False)
        
        # task_config에 priority 지정
        task = {'priority': 'realtime', 'type': 'llm'}
        state = {}
        priority = scheduler._get_task_priority(task, state)
        assert priority == TaskPriority.REALTIME
    
    def test_priority_extraction_from_state(self):
        """state에서 우선순위 추출"""
        from src.services.quality_kernel.concurrency_controller import (
            KernelTaskScheduler,
            TaskPriority
        )
        
        scheduler = KernelTaskScheduler(enable_distributed_state=False)
        
        # state.workflow_priority에서 추출
        task = {'type': 'llm'}
        state = {'workflow_priority': 'high'}
        priority = scheduler._get_task_priority(task, state)
        assert priority == TaskPriority.HIGH
    
    def test_priority_extraction_from_metadata(self):
        """metadata에서 우선순위 추출"""
        from src.services.quality_kernel.concurrency_controller import (
            KernelTaskScheduler,
            TaskPriority
        )
        
        scheduler = KernelTaskScheduler(enable_distributed_state=False)
        
        # state.metadata.priority에서 추출
        task = {'type': 'llm'}
        state = {'metadata': {'priority': 'background'}}
        priority = scheduler._get_task_priority(task, state)
        assert priority == TaskPriority.BACKGROUND
    
    def test_fast_track_bypasses_throttling(self):
        """Fast Track은 쓰로틀링 bypass"""
        from src.services.quality_kernel.concurrency_controller import (
            KernelTaskScheduler,
            TaskPriority,
            LoadLevel
        )
        
        scheduler = KernelTaskScheduler(
            reserved_concurrency=100,
            enable_throttling=True,
            enable_distributed_state=False
        )
        
        # 고부하 상태 시뮬레이션
        for _ in range(90):
            scheduler._active_executions += 1
        
        snapshot = scheduler.get_concurrency_snapshot(use_distributed=False)
        assert snapshot.load_level == LoadLevel.CRITICAL
        
        # REALTIME은 쓰로틀링 bypass (지연 0)
        start = time.time()
        delay = scheduler.apply_throttling(snapshot, TaskPriority.REALTIME)
        elapsed = time.time() - start
        
        assert delay == 0
        assert elapsed < 0.01  # 거의 즉시
    
    def test_fast_track_bypasses_batching(self):
        """Fast Track은 배치 bypass"""
        from src.services.quality_kernel.concurrency_controller import (
            KernelTaskScheduler,
            TaskPriority
        )
        
        scheduler = KernelTaskScheduler(
            enable_batching=True,
            enable_distributed_state=False
        )
        
        # operator 타입 노드 (배치 대상)
        task = {'type': 'operator', 'config': {'code': 'x + 1'}}
        
        # NORMAL은 배치 대상
        assert scheduler.should_batch(task, TaskPriority.NORMAL) is True
        
        # REALTIME은 배치 bypass
        assert scheduler.should_batch(task, TaskPriority.REALTIME) is False
        
        # HIGH도 배치 bypass
        assert scheduler.should_batch(task, TaskPriority.HIGH) is False
    
    def test_fast_track_execution_stats(self):
        """Fast Track 실행 통계"""
        from src.services.quality_kernel.concurrency_controller import (
            KernelTaskScheduler,
            TaskPriority
        )
        
        scheduler = KernelTaskScheduler(enable_distributed_state=False)
        
        def mock_executor(config, state):
            return {'result': 'ok'}
        
        # REALTIME 작업 실행
        task = {'priority': 'realtime', 'type': 'llm'}
        state = {}
        
        result = scheduler.schedule_task('wf_1', task, state, mock_executor)
        
        stats = scheduler.get_stats()
        assert stats['fast_track_executions'] == 1
        assert stats['total_scheduled'] == 1


class TestDistributedBudgetWatchdog:
    """v2.0: 분산 환경 비용 추적 테스트"""
    
    def test_budget_status_includes_distributed_info(self):
        """예산 상태에 분산 정보 포함"""
        from src.services.quality_kernel.concurrency_controller import (
            BudgetWatchdog,
            BudgetWatchdogConfig
        )
        
        # 분산 모드 비활성화 (테스트 환경)
        watchdog = BudgetWatchdog(
            config=BudgetWatchdogConfig(max_budget_usd=10.0),
            enable_distributed=False
        )
        
        watchdog.record_cost('gemini-1.5-flash', 1000, 500, 'node_1')
        
        status = watchdog.get_budget_status()
        
        assert 'local_cost_usd' in status
        assert 'global_cost_usd' in status
        assert 'is_distributed' in status
        assert status['is_distributed'] is False
    
    def test_record_cost_returns_distributed_flag(self):
        """비용 기록 결과에 분산 플래그 포함"""
        from src.services.quality_kernel.concurrency_controller import (
            BudgetWatchdog,
            BudgetWatchdogConfig
        )
        
        watchdog = BudgetWatchdog(
            config=BudgetWatchdogConfig(max_budget_usd=10.0),
            enable_distributed=False
        )
        
        result = watchdog.record_cost('gemini-1.5-pro', 1000, 500, 'node_1')
        
        assert 'global_cost_usd' in result
        assert 'is_distributed' in result
        assert result['is_distributed'] is False


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
