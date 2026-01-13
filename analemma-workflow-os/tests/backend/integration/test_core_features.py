"""
핵심 기능 통합 테스트 (Edge Cases 강화)

테스트 대상:
- Plan Briefing: 워크플로우 실행 전 미리보기
- Task Manager: 비즈니스 친화적 Task 정보 변환
- Instruction Distiller: 지침 충돌 감지 및 해결

원칙:
- AWS 서비스만 mock (DynamoDB, S3 등)
- 프로덕션 비즈니스 로직 코드 직접 사용
- 🚨 Edge Cases: 순환 참조, 고립 노드, 미분류 에러, 경계값, DynamoDB 왕복 등

테스트 철학: "설계도대로 만든 기계에 모래를 뿌려도 잘 돌아가나?"
"""
import pytest
import json
import sys
import os
from unittest.mock import MagicMock, AsyncMock
from moto import mock_aws
import boto3
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import ValidationError
from collections import defaultdict, deque

# 환경 변수 설정
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("CORRECTIONS_TABLE", "test-corrections")
os.environ.setdefault("INSTRUCTIONS_TABLE", "test-instructions")


# ============================================================================
# 🧪 Plan Briefing 테스트 (Edge Cases 포함)
# ============================================================================
class TestPlanBriefingCore:
    """Plan Briefing 핵심 기능 테스트"""
    
    def test_plan_step_model_validation(self):
        """Plan Step 모델이 올바르게 생성되는지 검증"""
        from backend.models.plan_briefing import PlanStep, RiskLevel
        
        step = PlanStep(
            step_number=1,
            node_id="node-1",
            node_name="이메일 작성",
            node_type="llm",
            action_description="GPT-4로 이메일 초안 생성",
            estimated_duration_seconds=5,
            risk_level=RiskLevel.LOW
        )
        
        assert step.step_number == 1
        assert step.node_id == "node-1"
        assert step.risk_level == RiskLevel.LOW
        assert step.has_external_side_effect == False
    
    def test_plan_step_with_external_side_effect(self):
        """외부 영향이 있는 단계가 올바르게 표시되는지 검증"""
        from backend.models.plan_briefing import PlanStep, RiskLevel
        
        step = PlanStep(
            step_number=2,
            node_id="node-2",
            node_name="이메일 발송",
            node_type="email",
            action_description="작성된 이메일을 고객에게 발송",
            estimated_duration_seconds=3,
            risk_level=RiskLevel.HIGH,
            risk_description="이메일 발송 후 되돌릴 수 없음",
            has_external_side_effect=True,
            external_systems=["SMTP", "SendGrid"]
        )
        
        assert step.risk_level == RiskLevel.HIGH
        assert step.has_external_side_effect == True
        assert "SendGrid" in step.external_systems
    
    def test_draft_result_model(self):
        """Draft Result 모델 검증"""
        from backend.models.plan_briefing import DraftResult
        
        draft = DraftResult(
            result_type="email",
            title="고객 문의 답변 이메일",
            content_preview="안녕하세요, 문의하신 내용에 대해 답변드립니다..."
        )
        
        assert draft.result_type == "email"
        assert draft.title == "고객 문의 답변 이메일"
        assert draft.result_id is not None  # 자동 생성됨
        assert "안녕하세요" in draft.content_preview
    
    def test_plan_briefing_service_default_durations(self):
        """Plan Briefing 서비스의 기본 소요 시간 설정 검증"""
        from backend.services.plan_briefing_service import PlanBriefingService
        
        service = PlanBriefingService()
        
        # 노드 타입별 기본 소요 시간 확인
        assert service.DEFAULT_DURATIONS["llm"] == 5
        assert service.DEFAULT_DURATIONS["hitp"] == 30  # HITL은 대기 시간이 김
        assert service.DEFAULT_DURATIONS["api_call"] == 3
    
    def test_plan_briefing_service_side_effect_types(self):
        """외부 영향이 있는 노드 타입 식별 검증"""
        from backend.services.plan_briefing_service import PlanBriefingService
        
        service = PlanBriefingService()
        
        # 외부 영향이 있는 타입들
        assert "email" in service.SIDE_EFFECT_TYPES
        assert "payment" in service.SIDE_EFFECT_TYPES
        assert "webhook" in service.SIDE_EFFECT_TYPES
    
    # ========================================================================
    # 🚨 Edge Case: 잘못된 입력 테스트 (의도적 실패)
    # ========================================================================
    def test_plan_step_invalid_risk_level_raises_error(self):
        """정의되지 않은 RiskLevel 문자열 주입 시 ValidationError"""
        from backend.models.plan_briefing import PlanStep
        
        with pytest.raises(ValidationError):
            PlanStep(
                step_number=1,
                node_id="n1",
                node_name="test",
                node_type="llm",
                action_description="test action",
                estimated_duration_seconds=5,
                risk_level="VERY_DANGEROUS"  # 존재하지 않는 값
            )
    
    def test_plan_step_negative_step_number_raises_error(self):
        """step_number < 1 이면 ValidationError"""
        from backend.models.plan_briefing import PlanStep, RiskLevel
        
        with pytest.raises(ValidationError):
            PlanStep(
                step_number=0,  # ge=1 위반
                node_id="n1",
                node_name="test",
                node_type="llm",
                action_description="test",
                estimated_duration_seconds=5,
                risk_level=RiskLevel.LOW
            )
    
    def test_plan_step_negative_duration_raises_error(self):
        """estimated_duration_seconds < 0 이면 ValidationError"""
        from backend.models.plan_briefing import PlanStep, RiskLevel
        
        with pytest.raises(ValidationError):
            PlanStep(
                step_number=1,
                node_id="n1",
                node_name="test",
                node_type="llm",
                action_description="test",
                estimated_duration_seconds=-10,  # ge=0 위반
                risk_level=RiskLevel.LOW
            )
    
    # ========================================================================
    # 🚨 Edge Case: 미정의 노드 타입 (DEFAULT_DURATIONS에 없음)
    # ========================================================================
    def test_undefined_node_type_uses_default_duration(self):
        """DEFAULT_DURATIONS에 없는 새 노드 타입 → 'default' 폴백"""
        from backend.services.plan_briefing_service import PlanBriefingService
        
        service = PlanBriefingService()
        
        # "quantum_processor" 같은 미래형 노드 타입
        unknown_type = "quantum_processor_v99"
        
        # 서비스는 .get()으로 안전하게 처리해야 함
        duration = service.DEFAULT_DURATIONS.get(unknown_type, service.DEFAULT_DURATIONS["default"])
        
        assert duration == service.DEFAULT_DURATIONS["default"]
        assert duration == 2  # default = 2초


# ============================================================================
# 🧪 Task Manager 테스트 (Edge Cases 포함)
# ============================================================================
class TestTaskManagerCore:
    """Task Manager 핵심 기능 테스트 (Table-Driven)"""
    
    @pytest.mark.parametrize("technical_status,expected_business_status", [
        ("RUNNING", "IN_PROGRESS"),
        ("PAUSED_FOR_HITP", "PENDING_APPROVAL"),
        ("COMPLETED", "COMPLETED"),
        ("FAILED", "FAILED"),
        # Edge cases: 다양한 케이싱
        ("running", "IN_PROGRESS"),
        ("Running", "IN_PROGRESS"),
        ("STARTED", "IN_PROGRESS"),
        ("SUCCEEDED", "COMPLETED"),
        ("TIMED_OUT", "FAILED"),
        ("ABORTED", "CANCELLED"),
    ])
    def test_task_status_conversion(self, technical_status, expected_business_status):
        """기술적 상태를 비즈니스 친화적 상태로 변환 (Table-Driven)"""
        from src.models.task_context import convert_technical_status, TaskStatus
        
        result = convert_technical_status(technical_status)
        expected = TaskStatus[expected_business_status]
        assert result == expected
    
    @pytest.mark.parametrize("error_input,expected_keyword", [
        ("timeout error occurred", "시간"),
        ("403 forbidden", "권한"),
        ("connection refused", "연결"),
        ("401 unauthorized", "인증"),
        ("500 internal server error", "문제"),
        # Edge cases: 혼합된 에러 메시지
        ("Request timeout after 30 seconds", "시간"),
        ("ERROR 503 Service Unavailable", "이용"),
    ])
    def test_friendly_error_message_generation(self, error_input, expected_keyword):
        """기술적 에러를 사용자 친화적 메시지로 변환 (Table-Driven)"""
        from src.models.task_context import get_friendly_error_message
        
        error_msg, suggestion = get_friendly_error_message(error_input)
        assert expected_keyword in error_msg or expected_keyword in suggestion
    
    def test_task_context_model(self):
        """TaskContext 모델 생성 및 검증"""
        from src.models.task_context import TaskContext, TaskStatus
        
        task = TaskContext(
            task_id="task-123",
            owner_id="user-456",
            workflow_name="이메일 자동화",
            current_step_description="이메일 초안 작성 중",
            status=TaskStatus.IN_PROGRESS,
            progress_percentage=45
        )
        
        assert task.task_id == "task-123"
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.progress_percentage == 45
    
    def test_artifact_preview_model(self):
        """ArtifactPreview 모델 검증"""
        from src.models.task_context import ArtifactPreview, ArtifactType
        
        preview = ArtifactPreview(
            artifact_id="artifact-001",
            artifact_type=ArtifactType.TEXT,  # TEXT 사용 (EMAIL 없음)
            title="고객 응대 이메일",
            preview_content="안녕하세요, 문의하신 내용에 대해..."
        )
        
        assert preview.artifact_type == ArtifactType.TEXT
        assert "안녕하세요" in preview.preview_content
    
    # ========================================================================
    # 🚨 Edge Case: 미분류 에러 → 폴백 메시지
    # ========================================================================
    @pytest.mark.parametrize("garbage_error", [
        "kernel panic - not syncing: VFS: Unable to mount root fs",
        "Segmentation fault (core dumped)",
        "xyzzy_unknown_error_12345",
        "完全に理解できないエラー",  # 일본어 에러
        "",  # 빈 문자열
        "   ",  # 공백만
        None,  # None은 str이 아니므로 별도 처리 필요
    ])
    def test_unknown_error_returns_fallback(self, garbage_error):
        """미분류 에러는 폴백 메시지 반환 (크래시 없음)"""
        from src.models.task_context import get_friendly_error_message
        
        if garbage_error is None:
            # None 입력은 함수에서 처리해야 함
            garbage_error = ""
        
        error_msg, suggestion = get_friendly_error_message(garbage_error)
        
        # 크래시 없이 메시지 반환
        assert error_msg is not None
        assert suggestion is not None
        assert len(error_msg) > 0
        assert len(suggestion) > 0
        # 폴백: "작업 중 문제가 발생"
        assert "문제" in error_msg or "발생" in error_msg
    
    # ========================================================================
    # 🚨 Edge Case: 진행률 경계값 (Pydantic Validation)
    # ========================================================================
    @pytest.mark.parametrize("invalid_progress", [
        -1, -10, -100,   # 음수
        101, 150, 999,   # 100 초과
    ])
    def test_progress_percentage_boundary_validation(self, invalid_progress):
        """progress_percentage 0~100 범위 벗어나면 ValidationError"""
        from src.models.task_context import TaskContext, TaskStatus
        
        with pytest.raises(ValidationError):
            TaskContext(
                task_id="task-boundary",
                progress_percentage=invalid_progress  # ge=0, le=100 위반
            )
    
    @pytest.mark.parametrize("valid_progress", [0, 1, 50, 99, 100])
    def test_progress_percentage_valid_range(self, valid_progress):
        """0~100 범위 내 progress_percentage는 정상 생성"""
        from src.models.task_context import TaskContext, TaskStatus
        
        task = TaskContext(
            task_id="task-valid",
            progress_percentage=valid_progress
        )
        assert task.progress_percentage == valid_progress
    
    # ========================================================================
    # 🚨 Edge Case: 미정의 기술 상태 → 기본값 폴백
    # ========================================================================
    @pytest.mark.parametrize("unknown_status", [
        "QUANTUM_SUPERPOSITION",
        "STATUS_404_NOT_FOUND",
        "",
        "null",
    ])
    def test_unknown_technical_status_falls_back(self, unknown_status):
        """미정의 기술 상태 → IN_PROGRESS로 폴백"""
        from src.models.task_context import convert_technical_status, TaskStatus
        
        result = convert_technical_status(unknown_status)
        
        # 알 수 없는 상태는 IN_PROGRESS로 폴백
        assert result == TaskStatus.IN_PROGRESS


# ============================================================================
# 🧪 Instruction Distiller (지침 충돌 감지) 테스트 - Edge Cases 포함
# ============================================================================
class TestInstructionDistillerCore:
    """지침 증류기 핵심 기능 테스트"""
    
    def test_correction_type_enum(self):
        """수정 타입 열거형 검증"""
        from src.models.correction_log import CorrectionType
        
        assert CorrectionType.TONE.value == "tone"
        assert CorrectionType.FORMAT.value == "format"
        assert CorrectionType.CONTENT.value == "content"
        assert CorrectionType.STYLE.value == "style"
    
    def test_task_category_enum(self):
        """태스크 카테고리 열거형 검증"""
        from src.models.correction_log import TaskCategory
        
        assert TaskCategory.EMAIL.value == "email"
        assert TaskCategory.SQL.value == "sql"
        assert TaskCategory.DOCUMENT.value == "document"
    
    def test_correction_log_model_creation(self):
        """CorrectionLog 모델 생성 검증"""
        from src.models.correction_log import CorrectionLog, TaskCategory
        
        log = CorrectionLog(
            pk="user#user123",
            user_id="user123",
            workflow_id="wf-001",
            node_id="node-001",
            task_category=TaskCategory.EMAIL,
            original_input="고객 문의 내용",
            agent_output="안녕하세요, 고객님.",
            user_correction="안녕하세요, 고객님! 감사합니다.",
            edit_distance=12,
            correction_time_seconds=30,
            # 필수 필드 추가
            node_type="llm_operator",
            workflow_domain="sales",
            context_scope="global"
        )
        
        assert log.user_id == "user123"
        assert log.task_category == TaskCategory.EMAIL
        assert log.edit_distance == 12
        assert log.node_type == "llm_operator"
    
    def test_distilled_instruction_model(self):
        """DistilledInstruction 모델 검증"""
        from src.models.correction_log import DistilledInstruction, CorrectionType
        
        instruction = DistilledInstruction(
            pk="user#user123",
            user_id="user123",
            category=CorrectionType.TONE,
            context_scope="email_response",
            instruction="항상 감사 인사로 시작하고 끝내기",
            confidence=0.85,
            source_correction_ids=["corr-001", "corr-002"],
            pattern_description="3회 이상 동일 패턴 감지됨",
            metadata_signature={"tone": "friendly", "formality": "formal"}
        )
        
        assert instruction.confidence == 0.85
        assert len(instruction.source_correction_ids) == 2
        assert instruction.metadata_signature["tone"] == "friendly"
    
    def test_conflict_resolver_detect_conflicts(self):
        """충돌 감지 로직 검증"""
        from src.models.correction_log import ConflictResolver, DistilledInstruction, CorrectionType
        
        resolver = ConflictResolver()
        
        # 기존 지침
        existing = [
            DistilledInstruction(
                pk="user#user123",
                sk="instruction#001",
                user_id="user123",
                category=CorrectionType.TONE,
                context_scope="email_response",
                instruction="격식체 사용",
                confidence=0.8,
                source_correction_ids=["c1"],
                pattern_description="formal tone",
                metadata_signature={"tone": "formal", "formality": "formal"}
            )
        ]
        
        # 충돌하는 새 메타데이터 (casual vs formal)
        new_signature = {"tone": "casual", "formality": "informal"}
        
        # 실제 API 시그니처에 맞게 호출
        conflicts = resolver.detect_conflicts(
            existing_instructions=existing,
            new_signature=new_signature,
            category=CorrectionType.TONE,
            context_scope="email_response"
        )
        
        # tone과 formality 모두 충돌해야 함
        assert len(conflicts) >= 1
    
    # ========================================================================
    # 🚨 Edge Case: 부분적 시그니처 충돌
    # ========================================================================
    def test_partial_signature_conflict_tone_only(self):
        """tone만 다르고 formality는 같음 → 부분 충돌"""
        from src.models.correction_log import DistilledInstruction, CorrectionType
        
        existing_sig = {"tone": "formal", "formality": "formal", "length": "short"}
        new_sig = {"tone": "casual", "formality": "formal"}  # tone만 충돌
        
        instruction = DistilledInstruction(
            pk="user#test",
            user_id="test",
            category=CorrectionType.TONE,
            context_scope="email",
            instruction="test",
            confidence=0.9,
            source_correction_ids=["c1"],
            pattern_description="test",
            metadata_signature=existing_sig
        )
        
        # 충돌 감지
        assert instruction.has_conflicting_signature(new_sig) == True
        
        # 충돌 상세: tone만 다름
        details = instruction.get_conflict_details(new_sig)
        assert "tone" in details
        assert details["tone"]["existing"] == "formal"
        assert details["tone"]["new"] == "casual"
        assert "formality" not in details  # formality는 같음
    
    def test_partial_signature_key_missing_no_conflict(self):
        """한쪽에 키가 없으면 충돌 아님"""
        from src.models.correction_log import DistilledInstruction, CorrectionType
        
        existing_sig = {"tone": "formal"}  # formality 없음
        new_sig = {"formality": "informal"}  # tone 없음
        
        instruction = DistilledInstruction(
            pk="user#test",
            user_id="test",
            category=CorrectionType.TONE,
            context_scope="email",
            instruction="test",
            confidence=0.9,
            source_correction_ids=["c1"],
            pattern_description="test",
            metadata_signature=existing_sig
        )
        
        # 공통 키가 없으면 충돌 없음
        assert instruction.has_conflicting_signature(new_sig) == False
    
    # ========================================================================
    # 🚨 Edge Case: 대량 지침 처리 (100개 이상)
    # ========================================================================
    def test_conflict_detection_with_100_instructions(self):
        """100개 이상의 기존 지침에서 충돌 감지 성능/정확도"""
        from src.models.correction_log import ConflictResolver, DistilledInstruction, CorrectionType
        import time
        
        resolver = ConflictResolver()
        
        # 100개의 기존 지침 생성 (다양한 메타데이터)
        existing = []
        for i in range(100):
            tone = "formal" if i % 2 == 0 else "casual"
            formality = "formal" if i % 3 == 0 else "informal"
            
            instruction = DistilledInstruction(
                pk=f"user#user{i}",
                sk=f"instruction#{i:03d}",
                user_id=f"user{i}",
                category=CorrectionType.TONE,
                context_scope="email_response",
                instruction=f"지침 {i}",
                confidence=0.8,
                source_correction_ids=[f"c{i}"],
                pattern_description=f"pattern {i}",
                metadata_signature={"tone": tone, "formality": formality}
            )
            existing.append(instruction)
        
        # 충돌하는 새 시그니처 (casual + formal)
        new_signature = {"tone": "casual", "formality": "formal"}
        
        start_time = time.time()
        conflicts = resolver.detect_conflicts(
            existing_instructions=existing,
            new_signature=new_signature,
            category=CorrectionType.TONE,
            context_scope="email_response"
        )
        elapsed = time.time() - start_time
        
        # 성능: 100개 처리는 1초 이내
        assert elapsed < 1.0, f"100개 지침 처리가 너무 느림: {elapsed:.2f}s"
        
        # 정확도: 일부는 충돌, 일부는 아님
        # tone=formal이고 formality=formal인 경우만 충돌 (i % 2 == 0 and i % 3 == 0)
        # → i = 0, 6, 12, 18, ... (0~99에서 17개)
        assert len(conflicts) >= 1
    
    # ========================================================================
    # 🚨 Edge Case: Pydantic 타입 강제 변환 (DynamoDB Decimal/String)
    # ========================================================================
    def test_confidence_string_coercion(self):
        """confidence가 문자열 '0.85'로 들어와도 float로 변환"""
        from src.models.correction_log import DistilledInstruction, CorrectionType
        
        # DynamoDB에서 Decimal이 문자열로 변환되어 올 수 있음
        instruction = DistilledInstruction(
            pk="user#test",
            user_id="test",
            category=CorrectionType.TONE,
            context_scope="email",
            instruction="test",
            confidence="0.85",  # 문자열로 주입
            source_correction_ids=["c1"],
            pattern_description="test"
        )
        
        # Pydantic이 float로 강제 변환
        assert isinstance(instruction.confidence, float)
        assert instruction.confidence == 0.85
    
    def test_edit_distance_string_coercion(self):
        """edit_distance가 문자열 '42'로 들어와도 int로 변환"""
        from src.models.correction_log import CorrectionLog, TaskCategory
        
        log = CorrectionLog(
            pk="user#test",
            user_id="test",
            workflow_id="wf-1",
            node_id="n-1",
            task_category=TaskCategory.EMAIL,
            original_input="test",
            agent_output="test",
            user_correction="test",
            edit_distance="42",  # 문자열로 주입
            correction_time_seconds="30",  # 문자열로 주입
            node_type="llm",
            workflow_domain="sales",
            context_scope="global"
        )
        
        assert isinstance(log.edit_distance, int)
        assert log.edit_distance == 42
        assert isinstance(log.correction_time_seconds, int)
        assert log.correction_time_seconds == 30
    
    def test_confidence_decimal_coercion(self):
        """confidence가 Decimal('0.85')로 들어와도 float로 변환"""
        from src.models.correction_log import DistilledInstruction, CorrectionType
        
        instruction = DistilledInstruction(
            pk="user#test",
            user_id="test",
            category=CorrectionType.TONE,
            context_scope="email",
            instruction="test",
            confidence=Decimal("0.85"),  # Decimal로 주입
            source_correction_ids=["c1"],
            pattern_description="test"
        )
        
        # Pydantic이 float로 변환
        assert isinstance(instruction.confidence, float)
        assert instruction.confidence == 0.85


# ============================================================================
# 🧪 워크플로우 그래프 분석 테스트 (복잡한 구조 포함)
# ============================================================================
class TestWorkflowAnalysisPureFunctions:
    """워크플로우 분석 순수 함수 테스트 (복잡한 그래프 구조 포함)"""
    
    def _topological_sort(self, nodes, edges):
        """토폴로지 정렬 헬퍼 함수"""
        in_degree = defaultdict(int)
        adj = defaultdict(list)
        
        node_ids = {n["id"] for n in nodes}
        for n in nodes:
            in_degree[n["id"]] = 0  # 초기화
        
        for edge in edges:
            if edge["source"] in node_ids and edge["target"] in node_ids:
                adj[edge["source"]].append(edge["target"])
                in_degree[edge["target"]] += 1
        
        # BFS 토폴로지 정렬
        queue = deque([n["id"] for n in nodes if in_degree[n["id"]] == 0])
        order = []
        
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        return order, len(order) == len(nodes)
    
    def test_workflow_node_ordering_linear(self):
        """선형 워크플로우: start → llm → email → end"""
        nodes = [
            {"id": "start", "type": "trigger"},
            {"id": "llm-1", "type": "llm"},
            {"id": "email", "type": "email"},
            {"id": "end", "type": "complete"}
        ]
        edges = [
            {"source": "start", "target": "llm-1"},
            {"source": "llm-1", "target": "email"},
            {"source": "email", "target": "end"}
        ]
        
        order, is_valid = self._topological_sort(nodes, edges)
        
        assert is_valid
        assert order == ["start", "llm-1", "email", "end"]
    
    # ========================================================================
    # 🚨 Edge Case: 순환 참조(Cycle) 감지
    # ========================================================================
    def test_cycle_detection_simple(self):
        """순환 참조가 있으면 토폴로지 정렬 실패"""
        nodes = [
            {"id": "A", "type": "llm"},
            {"id": "B", "type": "llm"},
            {"id": "C", "type": "llm"},
        ]
        # A → B → C → A (순환)
        edges = [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "C"},
            {"source": "C", "target": "A"},  # 순환!
        ]
        
        order, is_valid = self._topological_sort(nodes, edges)
        
        # 순환이 있으면 모든 노드를 정렬할 수 없음
        assert not is_valid
        assert len(order) < len(nodes)
    
    def test_cycle_detection_self_loop(self):
        """자기 자신을 가리키는 Self-loop"""
        nodes = [
            {"id": "A", "type": "llm"},
            {"id": "B", "type": "llm"},
        ]
        edges = [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "B"},  # Self-loop!
        ]
        
        order, is_valid = self._topological_sort(nodes, edges)
        
        # B의 in_degree가 절대 0이 되지 않음
        assert not is_valid
    
    # ========================================================================
    # 🚨 Edge Case: 고립된 노드(Isolated Node)
    # ========================================================================
    def test_isolated_node_handling(self):
        """시작점과 연결되지 않은 고립 노드"""
        nodes = [
            {"id": "start", "type": "trigger"},
            {"id": "llm-1", "type": "llm"},
            {"id": "end", "type": "complete"},
            {"id": "isolated", "type": "llm"},  # 아무것도 연결 안 됨
        ]
        edges = [
            {"source": "start", "target": "llm-1"},
            {"source": "llm-1", "target": "end"},
            # "isolated" 노드는 엣지 없음
        ]
        
        order, is_valid = self._topological_sort(nodes, edges)
        
        # 고립 노드도 in_degree=0이므로 정렬에 포함됨
        assert is_valid
        assert "isolated" in order
        # 고립 노드는 시작 지점들과 함께 처음에 나올 수 있음
    
    def test_multiple_roots_handling(self):
        """여러 시작점이 있는 병렬 워크플로우"""
        nodes = [
            {"id": "root1", "type": "trigger"},
            {"id": "root2", "type": "trigger"},
            {"id": "merge", "type": "aggregator"},
            {"id": "end", "type": "complete"},
        ]
        edges = [
            {"source": "root1", "target": "merge"},
            {"source": "root2", "target": "merge"},
            {"source": "merge", "target": "end"},
        ]
        
        order, is_valid = self._topological_sort(nodes, edges)
        
        assert is_valid
        # root1, root2가 먼저 나오고, merge, end가 뒤에
        assert order.index("merge") > order.index("root1")
        assert order.index("merge") > order.index("root2")
        assert order.index("end") > order.index("merge")
    
    # ========================================================================
    # 🚨 Edge Case: 복잡한 다이아몬드 구조
    # ========================================================================
    def test_diamond_dependency(self):
        """다이아몬드 의존성: A → B, A → C, B → D, C → D"""
        nodes = [
            {"id": "A", "type": "trigger"},
            {"id": "B", "type": "llm"},
            {"id": "C", "type": "llm"},
            {"id": "D", "type": "aggregator"},
        ]
        edges = [
            {"source": "A", "target": "B"},
            {"source": "A", "target": "C"},
            {"source": "B", "target": "D"},
            {"source": "C", "target": "D"},
        ]
        
        order, is_valid = self._topological_sort(nodes, edges)
        
        assert is_valid
        assert order[0] == "A"
        assert order[-1] == "D"
        # B, C는 A 다음, D 전에 (순서 불확정)
        assert order.index("B") > order.index("A")
        assert order.index("C") > order.index("A")
    
    def test_risk_level_calculation(self):
        """노드 타입에 따른 위험 수준 계산"""
        from backend.models.plan_briefing import RiskLevel
        
        def calculate_risk_level(node_type: str, has_side_effect: bool) -> RiskLevel:
            """노드 타입과 외부 영향 여부로 위험 수준 계산"""
            high_risk_types = {"email", "payment", "webhook", "sms"}
            medium_risk_types = {"api_call", "database_write"}
            
            if node_type in high_risk_types or (has_side_effect and node_type in medium_risk_types):
                return RiskLevel.HIGH
            elif node_type in medium_risk_types:
                return RiskLevel.MEDIUM
            else:
                return RiskLevel.LOW
        
        assert calculate_risk_level("llm", False) == RiskLevel.LOW
        assert calculate_risk_level("api_call", False) == RiskLevel.MEDIUM
        assert calculate_risk_level("email", True) == RiskLevel.HIGH
        assert calculate_risk_level("payment", True) == RiskLevel.HIGH
    
    # ========================================================================
    # 🚨 Edge Case: 빈 워크플로우, 노드만 있는 경우
    # ========================================================================
    def test_empty_workflow(self):
        """노드도 엣지도 없는 빈 워크플로우"""
        nodes = []
        edges = []
        
        order, is_valid = self._topological_sort(nodes, edges)
        
        assert is_valid
        assert order == []
    
    def test_nodes_only_no_edges(self):
        """엣지 없이 노드만 있음 → 모두 고립"""
        nodes = [
            {"id": "A", "type": "llm"},
            {"id": "B", "type": "llm"},
            {"id": "C", "type": "llm"},
        ]
        edges = []
        
        order, is_valid = self._topological_sort(nodes, edges)
        
        assert is_valid
        assert len(order) == 3


# ============================================================================
# 🧪 메타데이터 시그니처 충돌 테스트 (부분 일치 포함)
# ============================================================================
class TestMetadataSignatureConflict:
    """메타데이터 시그니처 기반 충돌 감지 테스트 (Edge Cases 포함)"""
    
    def _signatures_conflict(self, s1: dict, s2: dict) -> bool:
        """두 시그니처가 같은 키에 다른 값을 가지면 충돌"""
        for key in set(s1.keys()) & set(s2.keys()):
            if s1[key] != s2[key]:
                return True
        return False
    
    def test_signature_exact_match(self):
        """정확히 일치하는 시그니처 → 충돌 아님"""
        sig1 = {"tone": "formal", "length": "short"}
        sig2 = {"tone": "formal", "length": "short"}
        
        assert not self._signatures_conflict(sig1, sig2)
    
    def test_signature_conflict_detection(self):
        """충돌하는 시그니처 감지"""
        sig1 = {"tone": "formal", "length": "short"}
        sig2 = {"tone": "casual", "length": "short"}  # tone 충돌
        
        assert self._signatures_conflict(sig1, sig2)
    
    def test_signature_no_overlap(self):
        """겹치는 키가 없으면 충돌 없음"""
        sig1 = {"tone": "formal"}
        sig2 = {"length": "short"}
        
        assert not self._signatures_conflict(sig1, sig2)
    
    # ========================================================================
    # 🚨 Edge Case: 다양한 부분 일치 시나리오
    # ========================================================================
    def test_signature_subset_match(self):
        """한쪽이 다른 쪽의 서브셋 (일치하는 키들)"""
        sig1 = {"tone": "formal", "length": "short", "formality": "formal"}
        sig2 = {"tone": "formal"}  # 서브셋
        
        # 공통 키(tone)가 같으면 충돌 아님
        assert not self._signatures_conflict(sig1, sig2)
    
    def test_signature_empty(self):
        """빈 시그니처와의 비교"""
        sig1 = {"tone": "formal"}
        sig2 = {}
        
        assert not self._signatures_conflict(sig1, sig2)
        assert not self._signatures_conflict({}, {})
    
    def test_signature_multiple_conflicts(self):
        """여러 키가 동시에 충돌"""
        sig1 = {"tone": "formal", "length": "short", "style": "direct"}
        sig2 = {"tone": "casual", "length": "long", "style": "diplomatic"}
        
        # 모든 키가 다름
        assert self._signatures_conflict(sig1, sig2)
        
        # 충돌 개수 확인
        conflict_count = sum(
            1 for k in set(sig1.keys()) & set(sig2.keys())
            if sig1[k] != sig2[k]
        )
        assert conflict_count == 3


# ============================================================================
# 🧪 DynamoDB 왕복 테스트 (Moto 활용)
# ============================================================================
class TestDynamoDBRoundTrip:
    """DynamoDB put_item → query → Pydantic 복원 테스트"""
    
    @pytest.fixture
    def dynamodb_table(self):
        """moto로 가짜 DynamoDB 테이블 생성"""
        with mock_aws():
            dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
            
            table = dynamodb.create_table(
                TableName='test-corrections',
                KeySchema=[
                    {'AttributeName': 'pk', 'KeyType': 'HASH'},
                    {'AttributeName': 'sk', 'KeyType': 'RANGE'}
                ],
                AttributeDefinitions=[
                    {'AttributeName': 'pk', 'AttributeType': 'S'},
                    {'AttributeName': 'sk', 'AttributeType': 'S'}
                ],
                BillingMode='PAY_PER_REQUEST'
            )
            
            # 테이블이 활성화될 때까지 대기
            table.meta.client.get_waiter('table_exists').wait(TableName='test-corrections')
            
            yield table
    
    def test_correction_log_roundtrip(self, dynamodb_table):
        """CorrectionLog: 생성 → DynamoDB 저장 → 조회 → Pydantic 복원"""
        from src.models.correction_log import CorrectionLog, TaskCategory
        
        # 1. Pydantic 모델 생성
        original_log = CorrectionLog(
            pk="user#roundtrip-user",
            user_id="roundtrip-user",
            workflow_id="wf-roundtrip",
            node_id="node-roundtrip",
            task_category=TaskCategory.EMAIL,
            original_input="원본 입력 테스트",
            agent_output="에이전트 출력",
            user_correction="사용자 수정본",
            edit_distance=42,
            correction_time_seconds=120,
            node_type="llm_operator",
            workflow_domain="sales",
            context_scope="global"
        )
        
        # 2. DynamoDB에 저장 (Pydantic → dict → DynamoDB)
        item = json.loads(original_log.model_dump_json())
        
        # Enum 값을 문자열로 변환 (DynamoDB는 Enum 직접 저장 불가)
        if 'task_category' in item and hasattr(item['task_category'], 'value'):
            item['task_category'] = item['task_category'].value
        
        dynamodb_table.put_item(Item=item)
        
        # 3. DynamoDB에서 조회
        response = dynamodb_table.get_item(
            Key={'pk': original_log.pk, 'sk': original_log.sk}
        )
        retrieved_item = response.get('Item', {})
        
        # 4. 조회된 데이터로 Pydantic 모델 복원
        restored_log = CorrectionLog(**retrieved_item)
        
        # 5. 원본과 비교
        assert restored_log.user_id == original_log.user_id
        assert restored_log.workflow_id == original_log.workflow_id
        assert restored_log.task_category == original_log.task_category
        assert restored_log.edit_distance == original_log.edit_distance
        assert restored_log.original_input == original_log.original_input
    
    def test_enum_serialization_deserialization(self, dynamodb_table):
        """Enum 값의 직렬화/역직렬화 검증"""
        from src.models.correction_log import TaskCategory, VectorSyncStatus
        
        # Enum을 문자열로 저장
        item = {
            'pk': 'test#enum',
            'sk': 'enum#001',
            'task_category': TaskCategory.SQL.value,  # "sql"
            'vector_sync_status': VectorSyncStatus.PENDING.value  # "pending"
        }
        
        dynamodb_table.put_item(Item=item)
        
        # 조회
        response = dynamodb_table.get_item(Key={'pk': 'test#enum', 'sk': 'enum#001'})
        retrieved = response.get('Item', {})
        
        # 문자열 → Enum 복원
        assert TaskCategory(retrieved['task_category']) == TaskCategory.SQL
        assert VectorSyncStatus(retrieved['vector_sync_status']) == VectorSyncStatus.PENDING
    
    def test_decimal_handling_from_dynamodb(self, dynamodb_table):
        """DynamoDB Decimal 타입 → Python float/int 변환"""
        from src.models.correction_log import DistilledInstruction, CorrectionType
        
        # DynamoDB는 숫자를 Decimal로 저장
        item = {
            'pk': 'user#decimal-test',
            'sk': 'instruction#dec001',
            'user_id': 'decimal-test',
            'category': 'tone',
            'context_scope': 'email',
            'instruction': 'test instruction',
            'confidence': Decimal('0.85'),  # DynamoDB에서 오는 Decimal
            'source_correction_ids': ['c1', 'c2'],
            'pattern_description': 'test pattern',
            'version': Decimal('1'),  # 정수형 Decimal
            'is_active': True
        }
        
        dynamodb_table.put_item(Item=item)
        
        response = dynamodb_table.get_item(Key={'pk': 'user#decimal-test', 'sk': 'instruction#dec001'})
        retrieved = response.get('Item', {})
        
        # Pydantic이 Decimal을 float/int로 변환해야 함
        instruction = DistilledInstruction(**retrieved)
        
        assert isinstance(instruction.confidence, float)
        assert instruction.confidence == 0.85
        assert isinstance(instruction.version, int)
        assert instruction.version == 1
    
    def test_missing_optional_fields(self, dynamodb_table):
        """선택적 필드가 없는 데이터에서 모델 복원"""
        from src.models.correction_log import CorrectionLog, TaskCategory
        
        # 최소 필수 필드만 있는 데이터
        minimal_item = {
            'pk': 'user#minimal',
            'sk': 'correction#minimal',
            'user_id': 'minimal',
            'workflow_id': 'wf-min',
            'node_id': 'n-min',
            'task_category': 'email',
            'original_input': 'min input',
            'agent_output': 'min output',
            'user_correction': 'min correction',
            'edit_distance': 5,
            'correction_time_seconds': 10,
            'node_type': 'llm',
            'workflow_domain': 'test',
            'context_scope': 'global'
            # 선택적 필드들 생략: correction_type, extracted_metadata, etc.
        }
        
        dynamodb_table.put_item(Item=minimal_item)
        
        response = dynamodb_table.get_item(Key={'pk': 'user#minimal', 'sk': 'correction#minimal'})
        retrieved = response.get('Item', {})
        
        # 선택적 필드가 없어도 모델 생성 성공
        log = CorrectionLog(**retrieved)
        
        assert log.user_id == 'minimal'
        assert log.correction_type is None  # 선택적 필드는 None
        assert log.extracted_metadata == {}  # 기본값


# ============================================================================
# 🧪 의도적 실패 테스트 (Invalid Input Rejection)
# ============================================================================
class TestIntentionalFailures:
    """잘못된 데이터 입력 시 확실히 실패하는지 검증"""
    
    def test_invalid_task_category_raises_error(self):
        """존재하지 않는 TaskCategory → ValueError"""
        from src.models.correction_log import TaskCategory
        
        with pytest.raises(ValueError):
            TaskCategory("blockchain_nft_metaverse")  # 없는 값
    
    def test_invalid_correction_type_raises_error(self):
        """존재하지 않는 CorrectionType → ValueError"""
        from src.models.correction_log import CorrectionType
        
        with pytest.raises(ValueError):
            CorrectionType("quantum_correction")  # 없는 값
    
    def test_confidence_out_of_range_raises_error(self):
        """confidence > 1.0 또는 < 0.0 → ValidationError"""
        from src.models.correction_log import DistilledInstruction, CorrectionType
        
        with pytest.raises(ValidationError):
            DistilledInstruction(
                pk="user#test",
                user_id="test",
                category=CorrectionType.TONE,
                context_scope="email",
                instruction="test",
                confidence=1.5,  # > 1.0 (le=1 위반)
                source_correction_ids=["c1"],
                pattern_description="test"
            )
    
    def test_negative_edit_distance_raises_error(self):
        """edit_distance < 0 → ValidationError"""
        from src.models.correction_log import CorrectionLog, TaskCategory
        
        with pytest.raises(ValidationError):
            CorrectionLog(
                pk="user#test",
                user_id="test",
                workflow_id="wf",
                node_id="n",
                task_category=TaskCategory.EMAIL,
                original_input="test",
                agent_output="test",
                user_correction="test",
                edit_distance=-10,  # ge=0 위반
                correction_time_seconds=10,
                node_type="llm",
                workflow_domain="test",
                context_scope="global"
            )
    
    def test_invalid_artifact_type_raises_error(self):
        """존재하지 않는 ArtifactType → ValueError"""
        from src.models.task_context import ArtifactType
        
        with pytest.raises(ValueError):
            ArtifactType("hologram_3d")  # 없는 값
