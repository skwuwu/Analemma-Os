export const BUILD_TIME_TEST_KEYWORDS = [
    // Basic Status Tests
    'FAIL',
    'PAUSED_FOR_HITP',
    'COMPLETE',
    'CONTINUE',
    // S3 Tests
    'E2E_S3_LARGE_DATA',
    'E2E_S3_MIXED_DATA', 
    'E2E_S3_PROGRESSIVE',
    'S3_INIT_TEST',
    // Async Tests
    'ASYNC_LLM_TEST',
    'ASYNC_HEAVY_PROMPT',
    'ASYNC_S3_HEAVY_FILE',
    // Edge Cases
    'NULL_FINAL_STATE',
    // Map State Tests
    'MAP_AGGREGATOR_TEST',
    'MAP_AGGREGATOR_HITP_TEST',
    // Step Functions Improvements Tests (NEW)
    'IDEMPOTENCY_DUPLICATE',
    'PAYLOAD_COMPRESSION',
    'PAYLOAD_S3_OFFLOAD',
    'PARALLEL_COMPLEX_5',
    'LOOP_LIMIT_DYNAMIC'
];

export const TEST_KEYWORD_DESCRIPTIONS: Record<string, string> = {
    // Basic Status Tests
    'FAIL': '의도적 실패 테스트',
    'PAUSED_FOR_HITP': 'Human-in-the-loop 일시정지 테스트',
    'COMPLETE': '정상 완료 테스트 (LLM 포함)',
    'CONTINUE': '단계별 진행 테스트',
    // S3 Tests
    'E2E_S3_LARGE_DATA': '대용량 S3 데이터 테스트 (300KB+)',
    'E2E_S3_MIXED_DATA': 'S3 혼합 데이터 처리 테스트',
    'E2E_S3_PROGRESSIVE': '점진적 S3 데이터 증가 테스트',
    'S3_INIT_TEST': 'S3 초기화 테스트',
    // Async Tests
    'ASYNC_LLM_TEST': '비동기 LLM 처리 테스트',
    'ASYNC_HEAVY_PROMPT': '비동기 무거운 프롬프트 테스트',
    'ASYNC_S3_HEAVY_FILE': '비동기 S3 대용량 파일 테스트',
    // Edge Cases
    'NULL_FINAL_STATE': 'NULL 최종 상태 처리 테스트',
    // Map State Tests
    'MAP_AGGREGATOR_TEST': 'Map State 집계 테스트',
    'MAP_AGGREGATOR_HITP_TEST': 'Map State + HITP 조합 테스트',
    // Step Functions Improvements Tests (NEW)
    'IDEMPOTENCY_DUPLICATE': '멱등성 중복 실행 차단 테스트',
    'PAYLOAD_COMPRESSION': 'StateDataManager 압축 테스트',
    'PAYLOAD_S3_OFFLOAD': '페이로드 S3 오프로딩 테스트',
    'PARALLEL_COMPLEX_5': '복잡한 5개 브랜치 병렬 테스트',
    'LOOP_LIMIT_DYNAMIC': '동적 루프 제한 테스트'
};

// Pattern to match exact keywords from the list
// Capture group 1 matches the keyword
export const TEST_KEYWORD_PATTERN = new RegExp(`^(${BUILD_TIME_TEST_KEYWORDS.join('|')})$`, 'i');

// Generic pattern: uppercase words with underscores
export const GENERIC_TEST_PATTERN = /^[A-Z][A-Z0-9_]+$/;

// Legacy pattern: MOCK_BEHAVIOR_ prefix
export const LEGACY_MOCK_PATTERN = /^MOCK_BEHAVIOR_([A-Z0-9_]+)$/i;
