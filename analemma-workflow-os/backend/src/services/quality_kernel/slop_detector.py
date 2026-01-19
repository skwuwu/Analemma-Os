"""
Slop Detector - 워크슬롭 패턴 탐지기
====================================

LLM이 생성하는 저품질 "슬롭(Slop)" 패턴을 탐지합니다.

슬롭의 특징:
    1. 상투적 문구 (Boilerplate phrases)
    2. 과도한 헤징 (Excessive hedging)
    3. 빈약한 내용의 장황한 표현 (Verbose emptiness)
    4. 반복적 구조 (Repetitive structures)
    5. 메타 언급 (Self-referential meta statements)

탐지 방법:
    - 패턴 매칭 (정규식 기반)
    - N-gram 빈도 분석
    - 문장 구조 유사도
    - 정보 밀도 측정
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Set, Tuple, Optional
from enum import Enum


class SlopCategory(Enum):
    """슬롭 카테고리 분류"""
    BOILERPLATE = "boilerplate"           # 상투적 문구
    HEDGING = "hedging"                    # 과도한 헤징
    VERBOSE_EMPTINESS = "verbose_emptiness"  # 장황한 공허함
    REPETITION = "repetition"              # 반복
    META_STATEMENT = "meta_statement"      # 메타 언급
    FILLER = "filler"                      # 채우기 표현
    FALSE_DEPTH = "false_depth"            # 거짓 깊이
    EMOJI_OVERLOAD = "emoji_overload"      # 과도한 이모티콘 사용


@dataclass
class SlopPattern:
    """슬롭 패턴 정의"""
    pattern: str
    category: SlopCategory
    severity: float  # 0.0 ~ 1.0
    description: str
    language: str = "en"  # en, ko, universal
    whitelist_domains: List[str] = field(default_factory=list)  # 이 도메인에서는 심각도 감소


@dataclass
class EmojiAnalysisResult:
    """이모티콘 분석 결과"""
    emoji_count: int = 0
    emoji_ratio: float = 0.0  # 단어 대비 비율
    consecutive_emoji_count: int = 0  # 연속 이모티콘 수
    penalty: float = 0.0
    is_overload: bool = False
    
    def to_dict(self) -> Dict:
        return {
            'emoji_count': self.emoji_count,
            'emoji_ratio': round(self.emoji_ratio, 4),
            'consecutive_emoji_count': self.consecutive_emoji_count,
            'penalty': round(self.penalty, 4),
            'is_overload': self.is_overload
        }


@dataclass
class SlopDetectionResult:
    """슬롭 탐지 결과"""
    is_slop: bool
    slop_score: float  # 0.0 (clean) ~ 1.0 (pure slop)
    detected_patterns: List[Dict]
    category_breakdown: Dict[str, int]
    recommendation: str
    requires_llm_verification: bool
    emoji_analysis: Optional[EmojiAnalysisResult] = None
    domain_adjustments: Dict[str, float] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        result = {
            'is_slop': self.is_slop,
            'slop_score': round(self.slop_score, 4),
            'detected_patterns': self.detected_patterns[:10],
            'category_breakdown': self.category_breakdown,
            'recommendation': self.recommendation,
            'requires_llm_verification': self.requires_llm_verification
        }
        if self.emoji_analysis:
            result['emoji_analysis'] = self.emoji_analysis.to_dict()
        if self.domain_adjustments:
            result['domain_adjustments'] = self.domain_adjustments
        return result


class SlopDetector:
    """
    워크슬롭 패턴 탐지기
    
    Kernel Level: RING_1_QUALITY
    Cost: $0 (로컬 패턴 매칭)
    
    Usage:
        detector = SlopDetector()
        result = detector.detect("In conclusion, it is important to note that...")
        
        if result.is_slop:
            # 품질 게이트에서 차단 또는 LLM 검증 요청
            pass
    """
    
    # ========================================
    # 영어 슬롭 패턴
    # ========================================
    # 도메인별 이모티콘 허용 정책
    # ========================================
    EMOJI_POLICY = {
        'TECHNICAL_REPORT': {'max_ratio': 0.0, 'severity_multiplier': 2.0},
        'CODE_DOCUMENTATION': {'max_ratio': 0.0, 'severity_multiplier': 2.0},
        'LEGAL_DOCUMENT': {'max_ratio': 0.0, 'severity_multiplier': 2.5},
        'FINANCIAL_REPORT': {'max_ratio': 0.0, 'severity_multiplier': 2.0},
        'MARKETING_COPY': {'max_ratio': 0.05, 'severity_multiplier': 0.5},
        'SOCIAL_MEDIA': {'max_ratio': 0.1, 'severity_multiplier': 0.3},
        'GENERAL_CHAT': {'max_ratio': 0.15, 'severity_multiplier': 0.2},
        'GENERAL_TEXT': {'max_ratio': 0.03, 'severity_multiplier': 1.0},
    }
    
    # ========================================
    # 도메인별 화이트리스트
    # ========================================
    DOMAIN_WHITELIST = {
        'CODE_DOCUMENTATION': [
            r"\b(note that|in this function|returns|parameters)\b",
            r"\b(for example|e\.g\.|i\.e\.)\b",
        ],
        'TECHNICAL_REPORT': [
            r"\b(in summary|to summarize)\b",  # 기술 리포트에서는 요약 허용
            r"\b(it is important to note)\b",
        ],
        'ACADEMIC_PAPER': [
            r"\b(in conclusion|to summarize|furthermore|moreover)\b",
            r"\b(as discussed|as mentioned)\b",
        ],
    }
    
    # ========================================
    # 영어 슬롭 패턴 (화이트리스트 도메인 포함)
    # ========================================
    ENGLISH_SLOP_PATTERNS: List[SlopPattern] = [
        # Boilerplate - 상투적 도입/결론
        SlopPattern(
            r"\b(in conclusion|to summarize|in summary|to conclude)\b",
            SlopCategory.BOILERPLATE, 0.6,
            "Generic conclusion opener",
            whitelist_domains=['ACADEMIC_PAPER', 'TECHNICAL_REPORT']
        ),
        SlopPattern(
            r"\bit is important to (note|remember|understand|consider) that\b",
            SlopCategory.BOILERPLATE, 0.7,
            "Importance hedging pattern",
            whitelist_domains=['CODE_DOCUMENTATION', 'TECHNICAL_REPORT']
        ),
        SlopPattern(
            r"\b(as (we|I) (can see|have seen|discussed|mentioned))\b",
            SlopCategory.BOILERPLATE, 0.5,
            "Self-referential recap",
            whitelist_domains=['ACADEMIC_PAPER']
        ),
        SlopPattern(
            r"\b(first and foremost|last but not least|at the end of the day)\b",
            SlopCategory.BOILERPLATE, 0.6,
            "Cliché transition phrase"
        ),
        SlopPattern(
            r"\b(it goes without saying|needless to say)\b",
            SlopCategory.BOILERPLATE, 0.7,
            "Unnecessary meta statement"
        ),
        
        # Hedging - 과도한 모호화
        SlopPattern(
            r"\b(may or may not|could potentially|might possibly)\b",
            SlopCategory.HEDGING, 0.8,
            "Excessive hedging"
        ),
        SlopPattern(
            r"\b(to some extent|in some ways|in a sense|somewhat)\b",
            SlopCategory.HEDGING, 0.4,
            "Vague qualification"
        ),
        SlopPattern(
            r"\b(it (depends|varies|can vary)|there are (many|various) factors)\b",
            SlopCategory.HEDGING, 0.5,
            "Non-committal response"
        ),
        
        # Verbose Emptiness - 장황한 공허함
        SlopPattern(
            r"\b(in terms of|with regard to|with respect to|in relation to)\b",
            SlopCategory.VERBOSE_EMPTINESS, 0.3,
            "Bureaucratic filler"
        ),
        SlopPattern(
            r"\b(the fact that|due to the fact that|despite the fact that)\b",
            SlopCategory.VERBOSE_EMPTINESS, 0.4,
            "Wordy fact reference"
        ),
        SlopPattern(
            r"\b(at this point in time|at the present time|in today's world)\b",
            SlopCategory.VERBOSE_EMPTINESS, 0.5,
            "Temporal padding"
        ),
        
        # Meta Statements - AI 자기 언급
        SlopPattern(
            r"\b(as an AI|as a language model|I don't have personal)\b",
            SlopCategory.META_STATEMENT, 0.9,
            "AI self-reference (should be filtered at Ring 0)"
        ),
        SlopPattern(
            r"\b(I (cannot|can't|am unable to) (provide|give|offer))\b",
            SlopCategory.META_STATEMENT, 0.7,
            "Capability disclaimer"
        ),
        SlopPattern(
            r"\b(based on (my|the) (training|knowledge|information))\b",
            SlopCategory.META_STATEMENT, 0.6,
            "Training data reference"
        ),
        
        # Filler - 채우기 표현
        SlopPattern(
            r"\b(basically|essentially|fundamentally|ultimately)\b",
            SlopCategory.FILLER, 0.3,
            "Emphasis filler"
        ),
        SlopPattern(
            r"\b(very|really|quite|rather|fairly|pretty much)\b",
            SlopCategory.FILLER, 0.2,
            "Intensity modifier (low severity)"
        ),
        
        # False Depth - 거짓 깊이
        SlopPattern(
            r"\b(it's (worth|important to) (noting|mentioning|considering))\b",
            SlopCategory.FALSE_DEPTH, 0.6,
            "False importance signaling"
        ),
        SlopPattern(
            r"\b(this (raises|brings up|highlights) (important|interesting|key))\b",
            SlopCategory.FALSE_DEPTH, 0.5,
            "Pseudo-analytical phrase"
        ),
    ]
    
    # ========================================
    # 한국어 슬롭 패턴
    # ========================================
    KOREAN_SLOP_PATTERNS: List[SlopPattern] = [
        # Boilerplate - 상투적 문구
        SlopPattern(
            r"(결론적으로|요약하자면|정리하면|마무리하자면)",
            SlopCategory.BOILERPLATE, 0.6,
            "일반적인 결론 시작어", "ko"
        ),
        SlopPattern(
            r"(주목할 (필요가|점이|만한|가치가) 있)",
            SlopCategory.BOILERPLATE, 0.5,
            "주목 강조 패턴", "ko"
        ),
        SlopPattern(
            r"(말씀드린 (바와|것처럼|대로))",
            SlopCategory.BOILERPLATE, 0.5,
            "자기 참조 반복", "ko"
        ),
        
        # Hedging - 과도한 헤징
        SlopPattern(
            r"(일 수도 있고 아닐 수도|경우에 따라 다를 수)",
            SlopCategory.HEDGING, 0.8,
            "과도한 모호화", "ko"
        ),
        SlopPattern(
            r"(어느 정도|다소|약간|일부)",
            SlopCategory.HEDGING, 0.3,
            "모호한 수식어", "ko"
        ),
        
        # Meta Statements
        SlopPattern(
            r"(AI로서|언어 모델로서|제가 학습한)",
            SlopCategory.META_STATEMENT, 0.9,
            "AI 자기 언급", "ko"
        ),
        SlopPattern(
            r"(저는 (할 수 없|드리기 어려|제공하기 어려))",
            SlopCategory.META_STATEMENT, 0.7,
            "능력 부인", "ko"
        ),
        
        # Filler
        SlopPattern(
            r"(기본적으로|본질적으로|궁극적으로|결국)",
            SlopCategory.FILLER, 0.3,
            "채우기 표현", "ko"
        ),
        
        # False Depth
        SlopPattern(
            r"(중요한 (점은|것은|사실은))",
            SlopCategory.FALSE_DEPTH, 0.5,
            "거짓 중요성 신호", "ko"
        ),
        SlopPattern(
            r"(흥미로운 (점|부분|사실))",
            SlopCategory.FALSE_DEPTH, 0.4,
            "의사 분석 표현", "ko"
        ),
    ]
    
    # ========================================
    # 이모티콘 슬롭 패턴
    # ========================================
    EMOJI_SLOP_PATTERNS: List[SlopPattern] = [
        # 연속된 이모티콘 (2개 이상)
        SlopPattern(
            r"[\u2600-\u27BF\U0001f300-\U0001faff]{2,}",
            SlopCategory.EMOJI_OVERLOAD, 0.7,
            "Consecutive emoji usage"
        ),
        # 스파클 이모티콘 남용 (AI 슬롭의 대표적 신호)
        SlopPattern(
            r"[✨🌟💫⭐]{2,}",
            SlopCategory.EMOJI_OVERLOAD, 0.8,
            "Sparkle emoji overload (AI signature)"
        ),
        # 글머리 기호 + 이모티콘 조합 (AI 리스트 패턴)
        SlopPattern(
            r"^[\-\*•]\s*[\u2600-\u27BF\U0001f300-\U0001faff]",
            SlopCategory.EMOJI_OVERLOAD, 0.6,
            "Bullet + emoji combo (AI list pattern)"
        ),
        # 로켓/화재 이모티콘 남용
        SlopPattern(
            r"[🚀🔥💥]{2,}",
            SlopCategory.EMOJI_OVERLOAD, 0.7,
            "Hype emoji overload"
        ),
    ]
    
    def __init__(
        self,
        slop_threshold: float = 0.5,
        enable_korean: bool = True,
        enable_emoji_detection: bool = True,
        domain: str = "GENERAL_TEXT",
        custom_patterns: Optional[List[SlopPattern]] = None
    ):
        """
        Args:
            slop_threshold: 슬롭 판정 임계값 (0.0 ~ 1.0)
            enable_korean: 한국어 패턴 활성화 여부
            enable_emoji_detection: 이모티콘 탐지 활성화 여부
            domain: 콘텐츠 도메인 (화이트리스트 및 이모티콘 정책 적용)
            custom_patterns: 추가 커스텀 패턴
        """
        self.slop_threshold = slop_threshold
        self.domain = domain.upper()
        self.enable_emoji_detection = enable_emoji_detection
        
        # 도메인별 이모티콘 정책
        self.emoji_policy = self.EMOJI_POLICY.get(
            self.domain,
            self.EMOJI_POLICY['GENERAL_TEXT']
        )
        
        # 도메인별 화이트리스트 패턴 컴파일
        self.whitelist_patterns: List[re.Pattern] = []
        if self.domain in self.DOMAIN_WHITELIST:
            for pattern_str in self.DOMAIN_WHITELIST[self.domain]:
                self.whitelist_patterns.append(
                    re.compile(pattern_str, re.IGNORECASE)
                )
        
        # 패턴 컴파일
        self.patterns: List[Tuple[re.Pattern, SlopPattern]] = []
        
        for pattern in self.ENGLISH_SLOP_PATTERNS:
            self.patterns.append((
                re.compile(pattern.pattern, re.IGNORECASE),
                pattern
            ))
        
        if enable_korean:
            for pattern in self.KOREAN_SLOP_PATTERNS:
                self.patterns.append((
                    re.compile(pattern.pattern),
                    pattern
                ))
        
        if enable_emoji_detection:
            for pattern in self.EMOJI_SLOP_PATTERNS:
                self.patterns.append((
                    re.compile(pattern.pattern, re.MULTILINE),
                    pattern
                ))
        
        if custom_patterns:
            for pattern in custom_patterns:
                self.patterns.append((
                    re.compile(pattern.pattern, re.IGNORECASE if pattern.language == "en" else 0),
                    pattern
                ))
    
    def detect(self, text: str) -> SlopDetectionResult:
        """
        텍스트에서 슬롭 패턴 탐지
        
        Args:
            text: 분석할 텍스트
            
        Returns:
            SlopDetectionResult: 탐지 결과
        """
        if not text or len(text.strip()) < 20:
            return self._clean_result()
        
        detected_patterns: List[Dict] = []
        category_counts: Dict[str, int] = {}
        total_severity = 0.0
        domain_adjustments: Dict[str, float] = {}
        
        for compiled_pattern, slop_pattern in self.patterns:
            matches = compiled_pattern.findall(text)
            
            if matches:
                # 도메인 화이트리스트 체크 - 심각도 조정
                severity = slop_pattern.severity
                is_whitelisted = self.domain in slop_pattern.whitelist_domains
                
                if is_whitelisted:
                    # 화이트리스트 도메인에서는 심각도 70% 감소
                    severity *= 0.3
                    domain_adjustments[slop_pattern.pattern[:30]] = -0.7
                
                for match in matches:
                    detected_patterns.append({
                        'pattern': slop_pattern.pattern[:50],
                        'matched': match if isinstance(match, str) else match[0],
                        'category': slop_pattern.category.value,
                        'severity': severity,
                        'original_severity': slop_pattern.severity,
                        'description': slop_pattern.description,
                        'whitelisted': is_whitelisted
                    })
                    
                    total_severity += severity * len(matches)
                    
                    cat = slop_pattern.category.value
                    category_counts[cat] = category_counts.get(cat, 0) + len(matches)
        
        # 슬롭 점수 계산 (정규화)
        text_length_factor = max(1, len(text) / 500)  # 500자 기준 정규화
        slop_score = min(1.0, total_severity / (text_length_factor * 5))
        
        # 반복 구조 분석으로 추가 점수
        repetition_penalty = self._analyze_sentence_repetition(text)
        slop_score = min(1.0, slop_score + repetition_penalty)
        
        # ========================================
        # 이모티콘 밀도 분석 (도메인별 정책 적용)
        # ========================================
        emoji_result = None
        if self.enable_emoji_detection:
            emoji_result = self._analyze_emoji_density(text)
            if emoji_result.penalty > 0:
                slop_score = min(1.0, slop_score + emoji_result.penalty)
                if emoji_result.is_overload:
                    category_counts[SlopCategory.EMOJI_OVERLOAD.value] = emoji_result.emoji_count
        
        is_slop = slop_score >= self.slop_threshold
        
        # 권장 사항 결정
        if slop_score < 0.3:
            recommendation = "PASS: Content appears to have good information density"
        elif slop_score < 0.5:
            recommendation = "REVIEW: Some slop patterns detected, consider LLM verification"
        elif slop_score < 0.7:
            recommendation = "WARN: Significant slop detected, LLM verification recommended"
        else:
            recommendation = "REJECT: High slop concentration, content should be regenerated"
        
        # LLM 검증 필요 여부 (0.3 ~ 0.7 구간은 불확실)
        requires_llm = 0.3 <= slop_score <= 0.7
        
        return SlopDetectionResult(
            is_slop=is_slop,
            slop_score=slop_score,
            detected_patterns=detected_patterns,
            category_breakdown=category_counts,
            recommendation=recommendation,
            requires_llm_verification=requires_llm,
            emoji_analysis=emoji_result,
            domain_adjustments=domain_adjustments
        )
    
    def _analyze_emoji_density(self, text: str) -> EmojiAnalysisResult:
        """
        이모티콘 밀도 분석 및 페널티 계산
        
        도메인별 정책 적용:
        - TECHNICAL_REPORT: 0개 권장 (매우 엄격)
        - MARKETING_COPY: 5% 허용
        - GENERAL_CHAT: 15% 허용
        """
        # 모든 이모티콘 추출 (유니코드 범위)
        emoji_pattern = r"[\u2600-\u27BF\U0001f300-\U0001faff\U0001f600-\U0001f64f\U0001f680-\U0001f6ff]"
        emojis = re.findall(emoji_pattern, text)
        
        if not emojis:
            return EmojiAnalysisResult()
        
        emoji_count = len(emojis)
        word_count = len(text.split())
        
        # 연속 이모티콘 탐지
        consecutive_pattern = r"[\u2600-\u27BF\U0001f300-\U0001faff\U0001f600-\U0001f64f\U0001f680-\U0001f6ff]{2,}"
        consecutive_matches = re.findall(consecutive_pattern, text)
        consecutive_count = sum(len(m) for m in consecutive_matches)
        
        # 이모티콘 비율 계산
        emoji_ratio = emoji_count / word_count if word_count > 0 else 0
        
        # 도메인 정책 적용
        max_ratio = self.emoji_policy['max_ratio']
        severity_multiplier = self.emoji_policy['severity_multiplier']
        
        # 페널티 계산
        penalty = 0.0
        is_overload = False
        
        if emoji_ratio > max_ratio:
            excess_ratio = emoji_ratio - max_ratio
            
            if excess_ratio > 0.15:  # 매우 심각 (허용치 + 15% 초과)
                penalty = 0.4 * severity_multiplier
                is_overload = True
            elif excess_ratio > 0.08:  # 심각
                penalty = 0.25 * severity_multiplier
                is_overload = True
            elif excess_ratio > 0.03:  # 주의
                penalty = 0.15 * severity_multiplier
            else:  # 경미
                penalty = 0.05 * severity_multiplier
        
        # 연속 이모티콘 추가 페널티
        if consecutive_count >= 3:
            penalty += 0.1 * severity_multiplier
            is_overload = True
        
        penalty = min(0.5, penalty)  # 최대 0.5
        
        return EmojiAnalysisResult(
            emoji_count=emoji_count,
            emoji_ratio=emoji_ratio,
            consecutive_emoji_count=consecutive_count,
            penalty=penalty,
            is_overload=is_overload
        )
    
    def _analyze_sentence_repetition(self, text: str) -> float:
        """문장 구조 반복 분석"""
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 10]
        
        if len(sentences) < 3:
            return 0.0
        
        # 문장 시작 패턴 분석
        starters = [s.split()[0].lower() if s.split() else "" for s in sentences]
        starter_counts = {}
        for starter in starters:
            starter_counts[starter] = starter_counts.get(starter, 0) + 1
        
        # 같은 시작어가 30% 이상이면 페널티
        max_repetition = max(starter_counts.values()) / len(starters)
        
        if max_repetition > 0.5:
            return 0.2
        elif max_repetition > 0.3:
            return 0.1
        
        return 0.0
    
    def _clean_result(self) -> SlopDetectionResult:
        """깨끗한 결과 (슬롭 없음)"""
        return SlopDetectionResult(
            is_slop=False,
            slop_score=0.0,
            detected_patterns=[],
            category_breakdown={},
            recommendation="PASS: Text too short to analyze or clean",
            requires_llm_verification=False
        )
    
    @staticmethod
    def quick_slop_check(text: str) -> bool:
        """
        빠른 슬롭 체크 (간략화된 버전)
        
        Cost: $0
        Latency: < 1ms
        
        Returns:
            bool: True if likely contains slop
        """
        if not text or len(text) < 50:
            return False
        
        # 핵심 슬롭 패턴만 체크
        critical_patterns = [
            r"\bit is important to note that\b",
            r"\bin conclusion\b",
            r"\bas an AI\b",
            r"\bto summarize\b",
            r"\bfirst and foremost\b",
            r"결론적으로",
            r"AI로서",
        ]
        
        text_lower = text.lower()
        matches = sum(1 for p in critical_patterns if re.search(p, text_lower, re.IGNORECASE))
        
        return matches >= 2  # 2개 이상 매칭 시 슬롭 의심
