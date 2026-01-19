"""
Entropy Analyzer - Shannon Entropy 기반 텍스트 품질 측정
========================================================

텍스트의 정보 밀도를 수학적으로 측정하여 워크슬롭 탐지의 1차 휴리스틱으로 활용.

Shannon Entropy Formula:
    H(X) = -Σ P(x_i) * log₂(P(x_i))

Domain-Specific Thresholds:
    - Technical Report: H(X) >= 4.5
    - Creative Writing: H(X) >= 5.0
    - Code Documentation: H(X) >= 4.0
    - General Text: H(X) >= 4.2

Length-based Normalization:
    엔트로피는 텍스트 길이에 비례하여 증가하는 경향이 있음.
    짧은 문장에서도 밀도 있는 정보(예: 핵심 수치)를 가진 경우 억울하게 탈락하지 않도록
    로그 스케일 가중치를 적용:
    
    Normalized_H(X) = H(X) * (1 + α * log₂(1 + N/N_ref))
    
    Where:
    - N: 실제 단어 수
    - N_ref: 기준 단어 수 (default: 50)
    - α: 보정 계수 (default: 0.15)
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from enum import Enum


class ContentDomain(Enum):
    """콘텐츠 도메인 분류"""
    TECHNICAL_REPORT = "technical_report"
    CREATIVE_WRITING = "creative_writing"
    CODE_DOCUMENTATION = "code_documentation"
    GENERAL_TEXT = "general_text"
    API_RESPONSE = "api_response"
    WORKFLOW_OUTPUT = "workflow_output"


@dataclass
class EntropyThresholds:
    """도메인별 엔트로피 임계값"""
    min_word_entropy: float = 4.2
    min_char_entropy: float = 3.8
    max_repetition_ratio: float = 0.15
    min_vocabulary_richness: float = 0.4
    
    # Length-based Normalization 파라미터
    length_normalization_alpha: float = 0.15  # 보정 계수
    reference_word_count: int = 50  # 기준 단어 수
    short_text_bonus: float = 0.3  # 짧은 텍스트 보너스 (정보 밀도 높은 경우)
    
    @classmethod
    def for_domain(cls, domain: ContentDomain) -> 'EntropyThresholds':
        """도메인별 맞춤 임계값 반환"""
        thresholds = {
            ContentDomain.TECHNICAL_REPORT: cls(
                min_word_entropy=4.5,
                min_char_entropy=4.0,
                max_repetition_ratio=0.12,
                min_vocabulary_richness=0.45
            ),
            ContentDomain.CREATIVE_WRITING: cls(
                min_word_entropy=5.0,
                min_char_entropy=4.2,
                max_repetition_ratio=0.10,
                min_vocabulary_richness=0.55
            ),
            ContentDomain.CODE_DOCUMENTATION: cls(
                min_word_entropy=4.0,
                min_char_entropy=3.5,
                max_repetition_ratio=0.18,
                min_vocabulary_richness=0.35
            ),
            ContentDomain.API_RESPONSE: cls(
                min_word_entropy=3.8,
                min_char_entropy=3.5,
                max_repetition_ratio=0.20,
                min_vocabulary_richness=0.30
            ),
            ContentDomain.WORKFLOW_OUTPUT: cls(
                min_word_entropy=4.3,
                min_char_entropy=3.8,
                max_repetition_ratio=0.15,
                min_vocabulary_richness=0.40
            ),
        }
        return thresholds.get(domain, cls())


@dataclass
class EntropyAnalysisResult:
    """엔트로피 분석 결과"""
    # 핵심 메트릭
    word_entropy: float
    char_entropy: float
    vocabulary_richness: float  # unique_words / total_words
    
    # N-gram 분석 (기본값 없는 필드는 위에)
    bigram_entropy: float
    trigram_entropy: float
    repetition_ratio: float
    
    # 통계
    total_words: int
    unique_words: int
    total_chars: int
    unique_chars: int
    
    # 판정
    passes_threshold: bool
    domain: ContentDomain
    thresholds_used: EntropyThresholds
    
    # Length-normalized 엔트로피 (짧은 텍스트 보정) - 기본값 있는 필드
    normalized_word_entropy: float = 0.0
    length_adjustment_factor: float = 1.0
    
    # 상세 분석
    low_entropy_segments: List[str] = field(default_factory=list)
    high_frequency_ngrams: Dict[str, int] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        """직렬화용 딕셔너리 변환"""
        return {
            'metrics': {
                'word_entropy': round(self.word_entropy, 4),
                'char_entropy': round(self.char_entropy, 4),
                'normalized_word_entropy': round(self.normalized_word_entropy, 4),
                'length_adjustment_factor': round(self.length_adjustment_factor, 4),
                'vocabulary_richness': round(self.vocabulary_richness, 4),
                'bigram_entropy': round(self.bigram_entropy, 4),
                'trigram_entropy': round(self.trigram_entropy, 4),
                'repetition_ratio': round(self.repetition_ratio, 4),
            },
            'statistics': {
                'total_words': self.total_words,
                'unique_words': self.unique_words,
                'total_chars': self.total_chars,
                'unique_chars': self.unique_chars,
            },
            'verdict': {
                'passes_threshold': self.passes_threshold,
                'domain': self.domain.value,
            },
            'issues': {
                'low_entropy_segments': self.low_entropy_segments[:5],
                'high_frequency_ngrams': dict(list(self.high_frequency_ngrams.items())[:10]),
            }
        }


class EntropyAnalyzer:
    """
    Shannon Entropy 기반 텍스트 품질 분석기
    
    Kernel Level: RING_1_QUALITY
    Cost: $0 (로컬 계산)
    
    Usage:
        analyzer = EntropyAnalyzer(domain=ContentDomain.TECHNICAL_REPORT)
        result = analyzer.analyze("Your text here...")
        
        if not result.passes_threshold:
            # Stage 2: LLM 검증으로 에스컬레이션
            pass
    """
    
    def __init__(
        self, 
        domain: ContentDomain = ContentDomain.GENERAL_TEXT,
        custom_thresholds: Optional[EntropyThresholds] = None
    ):
        self.domain = domain
        self.thresholds = custom_thresholds or EntropyThresholds.for_domain(domain)
        
        # 전처리용 정규식
        self._word_pattern = re.compile(r'\b[a-zA-Z가-힣]+\b')
        self._sentence_pattern = re.compile(r'[.!?]+')
    
    def analyze(self, text: str) -> EntropyAnalysisResult:
        """
        텍스트의 엔트로피 분석 수행
        
        Args:
            text: 분석할 텍스트
            
        Returns:
            EntropyAnalysisResult: 상세 분석 결과
        """
        if not text or len(text.strip()) < 10:
            return self._empty_result()
        
        # 토큰화
        words = self._tokenize_words(text)
        chars = list(text)
        
        if len(words) < 3:
            return self._empty_result()
        
        # 핵심 엔트로피 계산
        word_entropy = self._calculate_entropy(words)
        char_entropy = self._calculate_entropy(chars)
        
        # N-gram 엔트로피
        bigrams = self._get_ngrams(words, 2)
        trigrams = self._get_ngrams(words, 3)
        bigram_entropy = self._calculate_entropy(bigrams) if bigrams else 0.0
        trigram_entropy = self._calculate_entropy(trigrams) if trigrams else 0.0
        
        # 어휘 풍부성
        unique_words = set(words)
        vocabulary_richness = len(unique_words) / len(words) if words else 0.0
        
        # 반복성 분석
        repetition_ratio, high_freq_ngrams = self._analyze_repetition(words)
        
        # 저엔트로피 구간 탐지
        low_entropy_segments = self._find_low_entropy_segments(text)
        
        # ============================================================
        # Length-based Normalization (짧은 텍스트 보정)
        # Normalized_H(X) = H(X) * (1 + α * log₂(1 + N/N_ref))
        # ============================================================
        normalized_entropy, length_factor = self._apply_length_normalization(
            word_entropy=word_entropy,
            word_count=len(words),
            vocabulary_richness=vocabulary_richness
        )
        
        # 임계값 검사 (정규화된 엔트로피 사용)
        passes = self._check_thresholds(
            word_entropy=normalized_entropy,  # 정규화된 값 사용
            char_entropy=char_entropy,
            vocabulary_richness=vocabulary_richness,
            repetition_ratio=repetition_ratio
        )
        
        return EntropyAnalysisResult(
            word_entropy=word_entropy,
            char_entropy=char_entropy,
            vocabulary_richness=vocabulary_richness,
            normalized_word_entropy=normalized_entropy,
            length_adjustment_factor=length_factor,
            bigram_entropy=bigram_entropy,
            trigram_entropy=trigram_entropy,
            repetition_ratio=repetition_ratio,
            total_words=len(words),
            unique_words=len(unique_words),
            total_chars=len(chars),
            unique_chars=len(set(chars)),
            passes_threshold=passes,
            domain=self.domain,
            thresholds_used=self.thresholds,
            low_entropy_segments=low_entropy_segments,
            high_frequency_ngrams=high_freq_ngrams
        )
    
    def _calculate_entropy(self, tokens: List[str]) -> float:
        """
        Shannon Entropy 계산
        
        H(X) = -Σ P(x_i) * log₂(P(x_i))
        """
        if not tokens:
            return 0.0
        
        # 빈도 계산
        counter = Counter(tokens)
        total = len(tokens)
        
        # 엔트로피 계산
        entropy = 0.0
        for count in counter.values():
            probability = count / total
            if probability > 0:
                entropy -= probability * math.log2(probability)
        
        return entropy
    
    def _tokenize_words(self, text: str) -> List[str]:
        """단어 토큰화 (영어/한국어 지원)"""
        words = self._word_pattern.findall(text.lower())
        return words
    
    def _get_ngrams(self, tokens: List[str], n: int) -> List[str]:
        """N-gram 생성"""
        if len(tokens) < n:
            return []
        return [' '.join(tokens[i:i+n]) for i in range(len(tokens) - n + 1)]
    
    def _analyze_repetition(self, words: List[str]) -> Tuple[float, Dict[str, int]]:
        """반복성 분석"""
        if len(words) < 6:
            return 0.0, {}
        
        # 트라이그램 반복 분석
        trigrams = self._get_ngrams(words, 3)
        trigram_counts = Counter(trigrams)
        
        # 2회 이상 등장하는 트라이그램
        repeated = {k: v for k, v in trigram_counts.items() if v >= 2}
        repeated_count = sum(repeated.values()) - len(repeated)  # 초과 등장 횟수
        
        repetition_ratio = repeated_count / len(trigrams) if trigrams else 0.0
        
        # 고빈도 n-gram (상위 10개)
        high_freq = dict(trigram_counts.most_common(10))
        
        return repetition_ratio, high_freq
    
    def _apply_length_normalization(
        self,
        word_entropy: float,
        word_count: int,
        vocabulary_richness: float
    ) -> Tuple[float, float]:
        """
        길이 기반 엔트로피 정규화
        
        짧은 텍스트에서도 밀도 있는 정보(예: 핵심 수치, API 응답)를 가진 경우
        억울하게 탈락하지 않도록 로그 스케일 가중치 적용.
        
        Formula:
            Normalized_H(X) = H(X) * adjustment_factor
            
            For short text (N < N_ref):
                adjustment_factor = 1 + bonus * (vocabulary_richness - 0.5) * (1 - N/N_ref)
                
            For normal/long text:
                adjustment_factor = 1 + α * log₂(1 + N/N_ref)
        
        Args:
            word_entropy: 원본 단어 엔트로피
            word_count: 단어 수
            vocabulary_richness: 어휘 풍부성 (unique/total)
            
        Returns:
            Tuple[normalized_entropy, adjustment_factor]
        """
        alpha = self.thresholds.length_normalization_alpha
        n_ref = self.thresholds.reference_word_count
        bonus = self.thresholds.short_text_bonus
        
        if word_count < n_ref:
            # 짧은 텍스트: 어휘 풍부성이 높으면 보너스
            # (핵심 수치, API 응답 등 정보 밀도 높은 짧은 텍스트 보호)
            richness_factor = max(0, vocabulary_richness - 0.5) * 2  # 0~1 스케일
            length_penalty = 1 - (word_count / n_ref)  # 짧을수록 큰 값
            
            # 어휘 풍부성이 높으면 보너스, 낮으면 패널티 없음
            adjustment = 1.0 + (bonus * richness_factor * length_penalty)
        else:
            # 일반/긴 텍스트: 로그 스케일 정규화
            # 길이가 증가해도 엔트로피 기대치가 과도하게 높아지지 않도록 조정
            adjustment = 1.0 + alpha * math.log2(1 + word_count / n_ref)
        
        normalized_entropy = word_entropy * adjustment
        
        return normalized_entropy, adjustment
    
    def _find_low_entropy_segments(self, text: str, segment_size: int = 50) -> List[str]:
        """저엔트로피 구간 탐지 (정보 증류 트리거용)"""
        sentences = self._sentence_pattern.split(text)
        low_entropy_segments = []
        
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 20:
                continue
            
            words = self._tokenize_words(sentence)
            if len(words) < 5:
                continue
            
            entropy = self._calculate_entropy(words)
            
            # 문장 레벨 엔트로피가 낮으면 기록
            if entropy < self.thresholds.min_word_entropy * 0.6:
                low_entropy_segments.append(sentence[:100])
        
        return low_entropy_segments[:5]  # 최대 5개
    
    def _check_thresholds(
        self,
        word_entropy: float,
        char_entropy: float,
        vocabulary_richness: float,
        repetition_ratio: float
    ) -> bool:
        """임계값 검사"""
        checks = [
            word_entropy >= self.thresholds.min_word_entropy,
            char_entropy >= self.thresholds.min_char_entropy,
            vocabulary_richness >= self.thresholds.min_vocabulary_richness,
            repetition_ratio <= self.thresholds.max_repetition_ratio
        ]
        
        # 모든 조건 충족 시 통과
        return all(checks)
    
    def _empty_result(self) -> EntropyAnalysisResult:
        """빈 결과 반환 (텍스트 부족 시)"""
        return EntropyAnalysisResult(
            word_entropy=0.0,
            char_entropy=0.0,
            vocabulary_richness=0.0,
            normalized_word_entropy=0.0,
            length_adjustment_factor=1.0,
            bigram_entropy=0.0,
            trigram_entropy=0.0,
            repetition_ratio=1.0,
            total_words=0,
            unique_words=0,
            total_chars=0,
            unique_chars=0,
            passes_threshold=False,
            domain=self.domain,
            thresholds_used=self.thresholds,
            low_entropy_segments=[],
            high_frequency_ngrams={}
        )
    
    @staticmethod
    def quick_entropy_check(text: str, min_threshold: float = 4.0) -> bool:
        """
        빠른 엔트로피 체크 (간략화된 버전)
        
        Cost: $0
        Latency: < 1ms
        
        Returns:
            bool: True if passes minimum entropy threshold
        """
        if not text or len(text) < 50:
            return True  # 짧은 텍스트는 통과
        
        words = re.findall(r'\b\w+\b', text.lower())
        if len(words) < 10:
            return True
        
        counter = Counter(words)
        total = len(words)
        
        entropy = 0.0
        for count in counter.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        
        return entropy >= min_threshold
