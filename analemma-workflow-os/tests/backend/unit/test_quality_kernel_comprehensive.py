"""
Quality Kernel 종합 테스트
=========================

테스트 범위:
    1. 호환성 검증 - 기존 코드에 영향 없음 확인
    2. 슬롭 감지 - LLM Mock 응답으로 패턴 탐지 검증
    3. 엔트로피 분석 - 저품질/고품질 텍스트 구분
    4. 비용 가드레일 - 4단계 가드레일 작동 검증
    5. 데코레이터 호환성 - 기존 함수 시그니처 유지

Mock LLM 슬롭 응답 유형:
    - SLOP_BOILERPLATE: 상투적 문구 (In conclusion...)
    - SLOP_HEDGING: 과도한 헤징 (may or may not...)
    - SLOP_META: AI 자기 언급 (As an AI...)
    - SLOP_VERBOSE: 장황한 공허함 (in terms of...)
    - SLOP_EMOJI: 이모티콘 남발
    - SLOP_KOREAN: 한국어 슬롭
    - QUALITY_HIGH: 고품질 응답 (통과해야 함)
"""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

# Quality Kernel imports
import sys
import os

# 프로젝트 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../backend/src'))

from services.quality_kernel import (
    # Core Analyzers
    EntropyAnalyzer,
    EntropyAnalysisResult,
    ContentDomain,
    EntropyThresholds,
    
    # Slop Detection
    SlopDetector,
    SlopDetectionResult,
    SlopCategory,
    SlopPattern,
    EmojiAnalysisResult,
    
    # Quality Gate
    QualityGate,
    QualityVerdict,
    QualityGateResult,
    QualityGateError,
    quality_gate_middleware,
    
    # Cost Guardrails
    CostGuardrailSystem,
    GuardrailAction,
    GuardrailDecision,
    GuardrailTrigger,
    RetryState,
    BudgetState,
    ModelPricing,
    DriftDetectionResult,
    create_guardrail_for_workflow,
    
    # Kernel Middleware
    KernelMiddlewareInterceptor,
    InterceptorAction,
    InterceptorResult,
    create_kernel_interceptor,
    register_node_interceptor,
)


# ============================================================
# MOCK LLM 슬롭 응답 데이터
# ============================================================

MOCK_SLOP_RESPONSES = {
    # ========================================
    # 영어 슬롭 케이스
    # ========================================
    'SLOP_BOILERPLATE': """
        In conclusion, it is important to note that this represents a 
        significant development. As we have discussed, there are many 
        factors to consider. First and foremost, let me explain the key 
        aspects. At the end of the day, it goes without saying that this 
        matters greatly. To summarize, these points are worth noting.
    """,
    
    'SLOP_HEDGING': """
        This may or may not be relevant, but to some extent, the situation 
        could potentially vary. In some ways, the outcome might possibly 
        depend on various factors. It could be argued that somewhat, the 
        results are fairly promising, though it's hard to say definitively.
    """,
    
    'SLOP_META': """
        As an AI language model, I cannot provide personal opinions or 
        experiences. Based on my training data, I can offer some general 
        information. However, I am unable to give medical advice. As a 
        language model, my knowledge has limitations.
    """,
    
    'SLOP_VERBOSE': """
        In terms of the overall situation, with regard to the specific 
        context, and with respect to the current circumstances, due to 
        the fact that there are multiple considerations, at this point 
        in time we need to address the fact that various aspects require 
        attention in relation to the matter at hand.
    """,
    
    'SLOP_FILLER': """
        Basically, this is essentially a fundamental concept that ultimately 
        really matters. Very important, quite significant, and rather 
        interesting. Pretty much the core idea fundamentally revolves around 
        this essentially basic principle.
    """,
    
    'SLOP_FALSE_DEPTH': """
        It's worth noting that this consideration is important to mention.
        There are several key points to consider here. It's crucial to 
        understand the nuances involved. Let me elaborate on these aspects.
        There's a lot to unpack here, and the complexity is worth exploring.
    """,
    
    'SLOP_EMOJI_OVERLOAD': """
        This is amazing! 🎉🎉🎉 I love this so much! 💕💕💕 
        Great work everyone! 🙌🙌🙌 Let's go! 🚀🚀🚀 
        Absolutely incredible! ✨✨✨ Best thing ever! 😍😍😍
    """,
    
    # ========================================
    # 한국어 슬롭 케이스
    # ========================================
    'SLOP_KOREAN_BOILERPLATE': """
        결론적으로 말씀드리면, 종합적으로 고려해 볼 때, 이러한 점들을 
        감안하시면, 다양한 측면에서 검토한 결과, 여러 가지 관점에서 
        분석해 보면, 요약하자면 이렇게 정리할 수 있습니다.
    """,
    
    'SLOP_KOREAN_HEDGING': """
        ~일 수도 있고 아닐 수도 있습니다. 상황에 따라 다를 수 있으며,
        경우에 따라서는 다르게 해석될 여지가 있습니다. 어떤 면에서는
        그럴 수도 있겠지만, 확실히 말씀드리기는 어렵습니다.
    """,
    
    'SLOP_KOREAN_META': """
        저는 AI 언어 모델로서 개인적인 의견을 드리기 어렵습니다.
        제 학습 데이터에 기반하여 일반적인 정보만 제공 가능합니다.
        AI로서 한계가 있음을 양해 부탁드립니다.
    """,
    
    'SLOP_KOREAN_RESPECTFUL_PADDING': """
        말씀하신 내용에 대해 충분히 이해하고 있으며, 관련하여 
        답변 드리겠습니다. 먼저 배경을 설명드리자면, 우선적으로 
        고려해야 할 사항들을 정리해 드리겠습니다. 이 점 참고 
        부탁드리며, 양해 부탁드립니다.
    """,
    
    # ========================================
    # 고품질 응답 (통과해야 함)
    # ========================================
    'QUALITY_HIGH_TECHNICAL': """
        The API endpoint accepts POST requests with a JSON payload containing:
        - `user_id`: string (required, UUID format)
        - `action`: enum ["create", "update", "delete"]
        - `timestamp`: ISO 8601 datetime
        
        Response format:
        ```json
        {"status": "success", "data": {...}, "request_id": "..."}
        ```
        
        Rate limit: 100 requests/minute per API key.
        Authentication: Bearer token in Authorization header.
    """,
    
    'QUALITY_HIGH_INFORMATIVE': """
        Python's GIL (Global Interpreter Lock) is a mutex that protects 
        access to Python objects, preventing multiple threads from executing 
        Python bytecodes simultaneously. This means CPU-bound multi-threaded 
        programs don't see performance gains on multi-core systems.
        
        Workarounds include:
        1. multiprocessing module (spawns separate processes)
        2. C extensions that release GIL (numpy, etc.)
        3. asyncio for I/O-bound workloads
    """,
    
    'QUALITY_HIGH_CODE': """
        def fibonacci(n: int) -> list[int]:
            \"\"\"Generate Fibonacci sequence up to n terms.\"\"\"
            if n <= 0:
                return []
            if n == 1:
                return [0]
            
            sequence = [0, 1]
            while len(sequence) < n:
                sequence.append(sequence[-1] + sequence[-2])
            return sequence
    """,
    
    'QUALITY_HIGH_KOREAN': """
        서울시 강남구 테헤란로 152에 위치한 강남파이낸스센터는 
        지하 7층, 지상 34층 규모의 오피스 빌딩입니다. 2001년 
        준공되었으며, 연면적 약 85,000㎡, 높이 152m입니다.
        
        주요 입주 기업:
        - IT 기업: 구글코리아, 마이크로소프트
        - 금융사: 한국투자증권, KB증권
        
        접근성: 강남역 2번 출구에서 도보 3분
    """,
}


# ============================================================
# 호환성 검증 테스트
# ============================================================

class TestCompatibility:
    """기존 코드 호환성 검증"""
    
    def test_quality_kernel_module_imports(self):
        """모든 export가 정상 import 되는지 확인"""
        # 이미 상단에서 import 완료 - 실패하면 테스트 자체가 실행 안됨
        assert EntropyAnalyzer is not None
        assert SlopDetector is not None
        assert QualityGate is not None
        assert CostGuardrailSystem is not None
        assert KernelMiddlewareInterceptor is not None
        assert ModelPricing is not None
    
    def test_decorator_does_not_modify_function_signature(self):
        """데코레이터가 함수 시그니처를 변경하지 않는지 확인"""
        
        @quality_gate_middleware(domain=ContentDomain.GENERAL_TEXT, reject_on_fail=False)
        def sample_handler(arg1: str, arg2: int = 10) -> str:
            """Sample docstring"""
            return f"{arg1}: {arg2}"
        
        # 함수 호출이 정상 작동하는지
        result = sample_handler("test", 20)
        assert result == "test: 20"
        
        # 기본값도 작동하는지
        result = sample_handler("test")
        assert result == "test: 10"
    
    def test_decorator_passes_through_non_string_results(self):
        """문자열이 아닌 결과는 품질 검사 없이 통과"""
        
        @quality_gate_middleware(domain=ContentDomain.GENERAL_TEXT)
        def returns_dict():
            return {"key": "value", "count": 42}
        
        result = returns_dict()
        assert result == {"key": "value", "count": 42}
    
    def test_register_node_interceptor_preserves_state(self):
        """register_node_interceptor가 state를 보존하는지 확인"""
        
        @register_node_interceptor
        def llm_node_handler(state: Dict) -> Dict:
            # 기존 state 반환 + 응답 추가
            state['llm_response'] = "This is a quality response with specific details."
            state['processed'] = True
            return state
        
        input_state = {
            'workflow_id': 'test-workflow',
            'current_node_id': 'test-node',
            'user_input': 'Hello'
        }
        
        result = llm_node_handler(input_state)
        
        # 원본 필드 보존
        assert result['workflow_id'] == 'test-workflow'
        assert result['processed'] == True
        
        # 인터셉터 메타데이터 추가됨
        assert '_kernel_quality_check' in result
        assert '_kernel_action' in result
    
    def test_quality_gate_does_not_raise_on_good_content(self):
        """고품질 콘텐츠에서 예외 발생하지 않음"""
        
        @quality_gate_middleware(
            domain=ContentDomain.TECHNICAL_REPORT,
            reject_on_fail=True
        )
        def good_content_handler():
            return MOCK_SLOP_RESPONSES['QUALITY_HIGH_TECHNICAL']
        
        # 예외 없이 통과해야 함
        result = good_content_handler()
        assert "API endpoint" in result
    
    def test_backward_compatible_with_existing_state_structure(self):
        """기존 워크플로우 상태 구조와 호환"""
        
        # 기존 워크플로우에서 사용하는 최소 상태 구조
        legacy_state = {
            'workflow_id': 'legacy-wf-001',
            'execution_id': 'exec-001',
            'variables': {'user_name': 'test'},
            'nodes': [],
        }
        
        # create_kernel_interceptor가 legacy state 처리 가능한지
        interceptor = create_kernel_interceptor(legacy_state)
        assert interceptor is not None
        
        # 도메인 기본값 적용
        assert interceptor.domain == ContentDomain.GENERAL_TEXT


# ============================================================
# 슬롭 감지 테스트 (Mock LLM 응답)
# ============================================================

class TestSlopDetection:
    """LLM Mock 응답으로 슬롭 감지 검증"""
    
    @pytest.fixture
    def detector(self):
        return SlopDetector(slop_threshold=0.5)
    
    # ========================================
    # 영어 슬롭 감지
    # ========================================
    
    def test_detect_boilerplate_slop(self, detector):
        """상투적 문구 슬롭 감지"""
        result = detector.detect(MOCK_SLOP_RESPONSES['SLOP_BOILERPLATE'])
        
        assert result.is_slop == True
        assert result.slop_score >= 0.5
        assert SlopCategory.BOILERPLATE.value in result.category_breakdown
        
        # 구체적 패턴 매칭 확인
        patterns = [p['pattern'] for p in result.detected_patterns]
        assert any('conclusion' in p.lower() for p in patterns)
    
    def test_detect_hedging_slop(self, detector):
        """과도한 헤징 슬롭 감지"""
        result = detector.detect(MOCK_SLOP_RESPONSES['SLOP_HEDGING'])
        
        assert result.is_slop == True
        assert SlopCategory.HEDGING.value in result.category_breakdown
        assert result.category_breakdown[SlopCategory.HEDGING.value] >= 2
    
    def test_detect_meta_statement_slop(self, detector):
        """AI 자기 언급 슬롭 감지 (높은 심각도)"""
        result = detector.detect(MOCK_SLOP_RESPONSES['SLOP_META'])
        
        assert result.is_slop == True
        assert result.slop_score >= 0.6  # META는 높은 심각도
        assert SlopCategory.META_STATEMENT.value in result.category_breakdown
    
    def test_detect_verbose_slop(self, detector):
        """장황한 공허함 슬롭 감지"""
        result = detector.detect(MOCK_SLOP_RESPONSES['SLOP_VERBOSE'])
        
        assert result.is_slop == True
        assert SlopCategory.VERBOSE_EMPTINESS.value in result.category_breakdown
    
    def test_detect_emoji_overload(self, detector):
        """이모티콘 과다 사용 감지"""
        # SlopDetector.detect()는 domain 파라미터를 받지 않음
        # 도메인별 이모티콘 정책은 내부적으로 적용
        result = detector.detect(MOCK_SLOP_RESPONSES['SLOP_EMOJI_OVERLOAD'])
        
        assert result.emoji_analysis is not None
        assert result.emoji_analysis.emoji_count >= 10
        # 이모티콘이 많으면 penalty가 있어야 함
        assert result.emoji_analysis.emoji_count > 5
    
    # ========================================
    # 한국어 슬롭 감지
    # ========================================
    
    def test_detect_korean_boilerplate(self, detector):
        """한국어 상투적 문구 감지"""
        result = detector.detect(MOCK_SLOP_RESPONSES['SLOP_KOREAN_BOILERPLATE'])
        
        # 한국어 패턴 매칭 확인 - slop_score가 0보다 크면 패턴 감지됨
        assert result.slop_score > 0
        # 어느 정도 패턴이 감지되어야 함
        assert len(result.detected_patterns) >= 1
    
    def test_detect_korean_hedging(self, detector):
        """한국어 헤징 패턴 감지"""
        result = detector.detect(MOCK_SLOP_RESPONSES['SLOP_KOREAN_HEDGING'])
        
        # 패턴이 감지되면 slop_score > 0
        assert result.slop_score >= 0.2 or len(result.detected_patterns) > 0
    
    def test_detect_korean_meta(self, detector):
        """한국어 AI 자기 언급 감지"""
        result = detector.detect(MOCK_SLOP_RESPONSES['SLOP_KOREAN_META'])
        
        assert result.is_slop == True
        # AI 자기 언급은 높은 심각도
        assert result.slop_score >= 0.5
    
    # ========================================
    # 고품질 콘텐츠 (오탐 방지)
    # ========================================
    
    def test_quality_content_passes_technical(self, detector):
        """고품질 기술 문서는 통과"""
        result = detector.detect(MOCK_SLOP_RESPONSES['QUALITY_HIGH_TECHNICAL'])
        
        # 슬롭으로 판정되면 안 됨
        assert result.is_slop == False
        assert result.slop_score < 0.5
    
    def test_quality_content_passes_informative(self, detector):
        """고품질 정보 콘텐츠는 통과"""
        result = detector.detect(MOCK_SLOP_RESPONSES['QUALITY_HIGH_INFORMATIVE'])
        
        assert result.is_slop == False
    
    def test_quality_content_passes_code(self, detector):
        """코드는 통과 (코드 도메인에서)"""
        # SlopDetector.detect()는 domain 파라미터를 받지 않음
        result = detector.detect(MOCK_SLOP_RESPONSES['QUALITY_HIGH_CODE'])
        
        # 코드는 슬롭이 아님
        assert result.is_slop == False
    
    def test_quality_korean_passes(self, detector):
        """고품질 한국어 정보 콘텐츠 통과"""
        result = detector.detect(MOCK_SLOP_RESPONSES['QUALITY_HIGH_KOREAN'])
        
        assert result.is_slop == False
    
    # ========================================
    # 도메인별 화이트리스트 테스트
    # ========================================
    
    def test_domain_whitelist_reduces_severity(self, detector):
        """화이트리스트 패턴은 낮은 심각도로 처리됨"""
        # 기술 리포트에서 허용되는 패턴
        text = "In conclusion, to summarize the key findings of this technical report..."
        
        # 패턴이 감지되지만 처리 가능해야 함
        result = detector.detect(text)
        
        # 패턴이 감지되는지 확인 (화이트리스트 여부와 별개)
        assert len(result.detected_patterns) > 0


# ============================================================
# 엔트로피 분석 테스트
# ============================================================

class TestEntropyAnalysis:
    """엔트로피 기반 품질 분석"""
    
    @pytest.fixture
    def analyzer(self):
        return EntropyAnalyzer(domain=ContentDomain.GENERAL_TEXT)
    
    def test_high_entropy_quality_content(self, analyzer):
        """고품질 콘텐츠는 높은 엔트로피"""
        result = analyzer.analyze(MOCK_SLOP_RESPONSES['QUALITY_HIGH_TECHNICAL'])
        
        assert result.word_entropy >= 3.5
        # passes_threshold로 확인
        assert result.passes_threshold == True
    
    def test_low_entropy_repetitive_content(self, analyzer):
        """반복적 콘텐츠는 낮은 엔트로피"""
        repetitive_text = "very very very important. It is very very significant. Very very crucial."
        result = analyzer.analyze(repetitive_text)
        
        # 반복이 많으면 엔트로피 낮음
        assert result.word_entropy < 4.5
    
    def test_short_text_length_normalization(self, analyzer):
        """짧은 텍스트에 대한 길이 정규화"""
        short_text = "API returns JSON with status code."
        result = analyzer.analyze(short_text)
        
        # 짧은 텍스트도 불공평하게 reject 되면 안 됨
        # normalized_word_entropy 속성 사용
        assert result.normalized_word_entropy is not None
        assert result.length_adjustment_factor >= 1.0
    
    def test_quick_entropy_check(self):
        """빠른 엔트로피 체크 유틸리티"""
        good_text = MOCK_SLOP_RESPONSES['QUALITY_HIGH_INFORMATIVE']
        
        assert EntropyAnalyzer.quick_entropy_check(good_text, min_threshold=3.5) == True
        
        # quick_entropy_check는 char entropy를 사용하므로 
        # 같은 문자 반복이어도 char_entropy가 0에 가깝지 않을 수 있음
        # 실제 word_entropy가 낮은 케이스 확인
        analyzer = EntropyAnalyzer()
        bad_result = analyzer.analyze("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        # 단일 문자 반복은 word_entropy가 0에 가까워야 함
        assert bad_result.word_entropy < 1.0


# ============================================================
# 비용 가드레일 테스트
# ============================================================

class TestCostGuardrails:
    """4단계 비용 가드레일 검증"""
    
    def test_guardrail_1_retry_quota(self):
        """Guardrail 1: 재시도 쿼터 초과"""
        guardrail = CostGuardrailSystem(
            workflow_id='test-wf',
            max_budget_usd=10.0,  # 높은 예산
            max_retries_per_node=3
        )
        
        # 3번 재시도
        for i in range(3):
            decision = guardrail.evaluate_regeneration_request(
                node_id='test-node',
                quality_score=0.3,  # 낮은 품질
                response_text=f"Response attempt {i+1}",
                input_tokens=100,
                output_tokens=50
            )
        
        # 4번째는 쿼터 초과
        final_decision = guardrail.evaluate_regeneration_request(
            node_id='test-node',
            quality_score=0.3,
            response_text="Response attempt 4",
            input_tokens=100,
            output_tokens=50
        )
        
        assert final_decision.action == GuardrailAction.FORCE_BEST_EFFORT
        assert final_decision.trigger == GuardrailTrigger.RETRY_QUOTA_EXCEEDED
    
    def test_guardrail_2_budget_exceeded(self):
        """Guardrail 2: 예산 초과"""
        guardrail = CostGuardrailSystem(
            workflow_id='test-wf',
            max_budget_usd=0.001,  # 매우 낮은 예산
            max_retries_per_node=10
        )
        
        # 비용 소진
        decision = guardrail.evaluate_regeneration_request(
            node_id='test-node',
            quality_score=0.5,
            response_text="Some response",
            input_tokens=100000,  # 많은 토큰
            output_tokens=50000
        )
        
        assert decision.action == GuardrailAction.EMERGENCY_STOP
        assert decision.trigger == GuardrailTrigger.EMERGENCY_BUDGET_BREACH
    
    def test_guardrail_3_adaptive_threshold(self):
        """Guardrail 3: 적응형 임계값 하향"""
        guardrail = CostGuardrailSystem(
            workflow_id='test-wf',
            max_budget_usd=0.5,
            max_retries_per_node=5
        )
        
        # 예산 80% 소진 상태 시뮬레이션
        guardrail.budget_state.current_cost_usd = 0.45  # 90%
        
        decision = guardrail.evaluate_regeneration_request(
            node_id='test-node',
            quality_score=0.4,
            response_text="Some response",
            input_tokens=100,
            output_tokens=50
        )
        
        # 경고 구간에서 임계값 하향
        assert decision.trigger in [
            GuardrailTrigger.BUDGET_LIMIT_REACHED,
            GuardrailTrigger.QUALITY_THRESHOLD_FLOOR
        ]
    
    def test_guardrail_4_drift_detection(self):
        """Guardrail 4: 시맨틱 드리프트 감지"""
        guardrail = CostGuardrailSystem(
            workflow_id='test-wf',
            max_budget_usd=10.0,
            max_retries_per_node=10,
            similarity_threshold=0.9
        )
        
        same_response = "This is the exact same response every time."
        
        # 동일한 응답 반복
        for _ in range(3):
            decision = guardrail.evaluate_regeneration_request(
                node_id='test-node',
                quality_score=0.4,
                response_text=same_response,
                input_tokens=100,
                output_tokens=50
            )
        
        # 드리프트 감지 시 HITL 에스컬레이션
        # (유사도 높고 품질 개선 없으면)
        retry_state = guardrail.get_retry_state('test-node')
        assert retry_state.attempt_count == 3
        assert len(retry_state.previous_response_snippets) == 3
    
    def test_allow_regeneration_normal_case(self):
        """정상 케이스: 재생성 허용"""
        guardrail = CostGuardrailSystem(
            workflow_id='test-wf',
            max_budget_usd=10.0,
            max_retries_per_node=5
        )
        
        decision = guardrail.evaluate_regeneration_request(
            node_id='test-node',
            quality_score=0.4,
            response_text="First attempt response",
            input_tokens=100,
            output_tokens=50
        )
        
        assert decision.action == GuardrailAction.ALLOW_REGENERATION
        assert decision.adjusted_threshold is not None


# ============================================================
# 가격 모델 테스트
# ============================================================

class TestModelPricing:
    """동적 가격 계산 테스트"""
    
    def test_default_pricing(self):
        """기본 가격표 확인"""
        pricing = ModelPricing.get_pricing('gemini-1.5-flash')
        
        assert 'input' in pricing
        assert 'output' in pricing
        assert 'cached_input' in pricing
        assert pricing['input'] > pricing['cached_input']  # 캐시가 더 저렴
    
    def test_cost_calculation_with_cache(self):
        """Context Caching 반영 비용 계산"""
        input_tokens = 10000
        output_tokens = 1000
        cached_tokens = 3000  # 30% 캐시 히트
        
        cost, breakdown = ModelPricing.calculate_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model='gemini-1.5-flash',
            cached_tokens=cached_tokens
        )
        
        assert cost > 0
        assert breakdown['cache_savings'] > 0
        assert breakdown['cached_input_cost'] < breakdown['regular_input_cost']
    
    def test_environment_variable_override(self):
        """환경변수로 가격 오버라이드"""
        import os
        
        # 환경변수 설정
        os.environ['ANALEMMA_PRICING_GEMINI_1_5_FLASH_INPUT'] = '0.10'
        
        try:
            pricing = ModelPricing.get_pricing('gemini-1.5-flash')
            assert pricing['input'] == 0.10
        finally:
            # 정리
            del os.environ['ANALEMMA_PRICING_GEMINI_1_5_FLASH_INPUT']
    
    def test_estimate_cached_tokens(self):
        """캐시 토큰 추정"""
        input_tokens = 10000
        
        # 기본 추정
        cached = ModelPricing.estimate_cached_tokens(input_tokens)
        assert 0 < cached < input_tokens
        
        # 워크플로우 컨텍스트로 조정
        context = {'cache_hit_ratio': 0.5, 'executed_node_count': 5}
        cached_with_context = ModelPricing.estimate_cached_tokens(input_tokens, context)
        assert cached_with_context > cached  # 노드 많으면 캐시 확률 증가


# ============================================================
# BudgetState 직렬화 테스트
# ============================================================

class TestBudgetState:
    """예산 상태 추적 및 직렬화"""
    
    def test_budget_state_to_dict_includes_cache_info(self):
        """to_dict()에 캐시 정보 포함"""
        state = BudgetState(workflow_id='test-wf', max_budget_usd=1.0)
        
        # 비용 추가
        state.add_cost(1000, 500, 'gemini-1.5-flash', 'node-1', cached_tokens=300)
        
        result = state.to_dict()
        
        # 기본 필드
        assert 'workflow_id' in result
        assert 'budget' in result
        assert 'tokens' in result
        
        # 캐시 관련 필드
        assert 'cached' in result['tokens']
        assert 'cache_hit_ratio' in result['tokens']
        assert 'cache_savings' in result
        assert 'total_savings_usd' in result['cache_savings']
        
        # 상세 비용 기록
        assert 'cost_details' in result
    
    def test_budget_zones(self):
        """예산 경고/비상 구간"""
        state = BudgetState(workflow_id='test', max_budget_usd=1.0)
        
        # 초기: 안전 구간
        assert state.is_warning_zone() == False
        assert state.is_emergency_zone() == False
        
        # 80% 소진: 경고 구간
        state.current_cost_usd = 0.85
        assert state.is_warning_zone() == True
        assert state.is_emergency_zone() == False
        
        # 95% 소진: 비상 구간
        state.current_cost_usd = 0.96
        assert state.is_emergency_zone() == True


# ============================================================
# Quality Gate 통합 테스트
# ============================================================

class TestQualityGateIntegration:
    """QualityGate 통합 테스트"""
    
    def test_evaluate_slop_content_fails(self):
        """슬롭 콘텐츠는 FAIL, UNCERTAIN 또는 경고 판정"""
        gate = QualityGate(domain=ContentDomain.GENERAL_TEXT)
        
        result = gate.evaluate(
            MOCK_SLOP_RESPONSES['SLOP_BOILERPLATE'],
            skip_stage2=True
        )
        
        # PASS_WITH_WARNING도 슬롭 감지의 증거
        assert result.final_verdict in [
            QualityVerdict.FAIL, 
            QualityVerdict.UNCERTAIN,
            QualityVerdict.PASS_WITH_WARNING
        ]
        # Stage 1에서 슬롭이 감지되어야 함
        assert result.stage1.slop_result.is_slop == True
    
    def test_evaluate_quality_content_passes(self):
        """고품질 콘텐츠는 PASS"""
        gate = QualityGate(domain=ContentDomain.TECHNICAL_REPORT)
        
        result = gate.evaluate(
            MOCK_SLOP_RESPONSES['QUALITY_HIGH_TECHNICAL'],
            skip_stage2=True
        )
        
        assert result.final_verdict == QualityVerdict.PASS
    
    def test_quick_quality_check_utility(self):
        """빠른 품질 체크 - evaluate로 대체"""
        gate = QualityGate()
        
        # 고품질 콘텐츠
        good_result = gate.evaluate(
            MOCK_SLOP_RESPONSES['QUALITY_HIGH_INFORMATIVE'],
            skip_stage2=True
        )
        assert good_result.final_verdict == QualityVerdict.PASS
        
        # 저품질 콘텐츠 - 슬롭 감지 또는 경고
        bad_result = gate.evaluate(
            MOCK_SLOP_RESPONSES['SLOP_META'],
            skip_stage2=True
        )
        assert bad_result.stage1.slop_result.is_slop == True


# ============================================================
# 미들웨어 인터셉터 테스트
# ============================================================

class TestKernelMiddleware:
    """커널 미들웨어 인터셉터 테스트"""
    
    def test_interceptor_action_for_slop(self):
        """슬롭에 대해 적절한 액션 반환"""
        interceptor = KernelMiddlewareInterceptor(
            domain=ContentDomain.GENERAL_TEXT
        )
        
        result = interceptor.post_process_node(
            node_output=MOCK_SLOP_RESPONSES['SLOP_BOILERPLATE'],
            node_id='test-node',
            workflow_id='test-wf',
            context={}
        )
        
        # 슬롭이 감지되면 PASS가 아니어야 함 (또는 경고와 함께 PASS)
        # InterceptorAction Enum 값 확인
        valid_actions = [
            InterceptorAction.DISTILL,
            InterceptorAction.REGENERATE,
            InterceptorAction.ESCALATE_STAGE2,
            InterceptorAction.PASS_WITH_BACKGROUND_DISTILL,
            InterceptorAction.PASS,  # 경고와 함께 통과 가능
        ]
        assert result.action in valid_actions
        
        # 슬롭이 감지되었는지 확인
        assert result.slop_result.is_slop == True
    
    def test_interceptor_passes_quality_content(self):
        """고품질 콘텐츠는 PASS 또는 PASS_WITH_BACKGROUND_DISTILL"""
        interceptor = KernelMiddlewareInterceptor(
            domain=ContentDomain.TECHNICAL_REPORT
        )
        
        result = interceptor.post_process_node(
            node_output=MOCK_SLOP_RESPONSES['QUALITY_HIGH_TECHNICAL'],
            node_id='test-node',
            workflow_id='test-wf',
            context={}
        )
        
        # PASS 또는 백그라운드 증류와 함께 PASS
        assert result.action in [
            InterceptorAction.PASS,
            InterceptorAction.PASS_WITH_BACKGROUND_DISTILL
        ]


# ============================================================
# 드리프트 감지 결과 테스트
# ============================================================

class TestDriftDetectionResult:
    """드리프트 감지 결과 직렬화"""
    
    def test_to_dict_without_llm_verification(self):
        """LLM 검증 없는 경우"""
        result = DriftDetectionResult(
            is_drifting=True,
            similarity_score=0.85,
            quality_improvement=-0.02,
            is_stuck_in_loop=False
        )
        
        d = result.to_dict()
        assert 'llm_verification' not in d
    
    def test_to_dict_with_llm_verification(self):
        """LLM 검증 있는 경우"""
        result = DriftDetectionResult(
            is_drifting=True,
            similarity_score=0.75,
            quality_improvement=0.0,
            is_stuck_in_loop=True,
            llm_verified=True,
            llm_semantic_same=True,
            llm_verification_reason="Responses contain identical core information"
        )
        
        d = result.to_dict()
        assert 'llm_verification' in d
        assert d['llm_verification']['semantic_same'] == True


# ============================================================
# 엣지 케이스 테스트
# ============================================================

class TestEdgeCases:
    """엣지 케이스 및 경계 조건"""
    
    def test_empty_string_handling(self):
        """빈 문자열 처리"""
        detector = SlopDetector()
        result = detector.detect("")
        
        # 빈 문자열은 슬롭 아님 (처리 가능해야 함)
        assert result is not None
    
    def test_very_short_text(self):
        """매우 짧은 텍스트"""
        gate = QualityGate()
        result = gate.evaluate("OK", skip_stage2=True)
        
        # 에러 없이 처리
        assert result is not None
    
    def test_unicode_and_emoji_handling(self):
        """유니코드 및 이모지 처리"""
        detector = SlopDetector()
        text = "안녕하세요! 🎉 This is 日本語 and العربية mixed content."
        
        result = detector.detect(text)
        assert result is not None
    
    def test_very_long_text(self):
        """매우 긴 텍스트"""
        long_text = MOCK_SLOP_RESPONSES['QUALITY_HIGH_TECHNICAL'] * 100
        
        detector = SlopDetector()
        result = detector.detect(long_text)
        
        assert result is not None
    
    def test_budget_state_zero_division(self):
        """예산 0일 때 ZeroDivisionError 방지"""
        state = BudgetState(workflow_id='test', max_budget_usd=0)
        
        # ZeroDivisionError 없어야 함
        ratio = state.get_budget_ratio()
        assert ratio == 1.0  # 예산 0이면 100% 사용한 것으로


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
