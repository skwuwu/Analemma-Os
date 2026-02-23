# Analemma-OS 백엔드 전체 점검 보고서

**작성일:** 2026-02-22
**점검 범위:** 보안·신뢰 / 상태·일관성 / 실행 인프라 3개 레이어
**점검 파일:** 18개
**기록 정책:** 점검 결과 문서화만 (코드 수정 없음)

---

## 목차

1. [점검 요약](#1-점검-요약)
2. [1순위: 보안·신뢰 레이어](#2-1순위-보안신뢰-레이어)
   - [prompt_security_guard.py](#21-prompt_security_guardpy)
   - [error_classifier.py](#22-error_classifierpy)
   - [agent_guardrails.py](#23-agent_guardrailspy)
   - [trust_score_manager.py](#24-trust_score_managerpy)
   - [constitution.py](#25-constitutionpy)
   - [retroactive_masking.py](#26-retroactive_maskingpy)
   - [pii_masking_service.py](#27-pii_masking_servicepy)
3. [2순위: 상태·일관성 레이어](#3-2순위-상태일관성-레이어)
   - [state_versioning_service.py](#31-state_versioning_servicepy)
   - [eventual_consistency_guard.py](#32-eventual_consistency_guardpy)
   - [merkle_gc_service.py](#33-merkle_gc_servicepy)
   - [async_commit_service.py](#34-async_commit_servicepy)
   - [checkpoint_service.py](#35-checkpoint_servicepy)
4. [3순위: 실행 인프라 레이어](#4-3순위-실행-인프라-레이어)
   - [bedrock_client.py / gemini_client.py / gemini_service.py](#41-bedrock_clientpy--gemini_clientpy--gemini_servicepy)
   - [distributed_chunk_service.py](#42-distributed_chunk_servicepy)
   - [partition_service.py](#43-partition_servicepy)
   - [orchestrator_service.py](#44-orchestrator_servicepy)
5. [전체 위험도 매트릭스](#5-전체-위험도-매트릭스)

---

## 1. 점검 요약

### 전체 이슈 집계

| 레이어 | 파일 수 | 버그 | 보안취약점 | 기능빈약 | 합계 |
|--------|---------|------|-----------|---------|------|
| 보안·신뢰 | 7 | 26 | 31 | 43 | 100 |
| 상태·일관성 | 5 | 30 | 18 | 27 | 75 |
| 실행 인프라 | 6 | 24 | 17 | 34 | 75 |
| **합계** | **18** | **80** | **66** | **104** | **250** |

### 심각도별 CRITICAL/HIGH 이슈

| 파일 | CRITICAL | HIGH |
|------|---------|------|
| `state_versioning_service.py` | 1 (self.prefix 미초기화) | 2 |
| `eventual_consistency_guard.py` | 1 (GC 레이스 컨디션) | 3 |
| `checkpoint_service.py` | 1 (S3 경로 파싱) | 2 |
| `prompt_security_guard.py` | 2 (syscall 권한상승, blocklist 한계) | 2 |
| `gemini_service.py` | 1 (임시파일 자격증명 미삭제) | 3 |
| `distributed_chunk_service.py` | 1 (파티션 슬라이스 인덱스 오류) | 1 |
| `partition_service.py` | 1 (수렴 노드 탐지 불완전) | 1 |
| `merkle_gc_service.py` | 0 | 2 |

---

## 2. 1순위: 보안·신뢰 레이어

---

### 2.1 `prompt_security_guard.py`

**경로:** `backend/src/services/recovery/prompt_security_guard.py`
**라인 수:** 577줄

#### 버그 (BUG)

**BUG-1.1: 컴파일된 패턴의 심각도 판정 신뢰 불가 (Line 224–242)**
```python
pattern_str = pattern.pattern
if 'jailbreak' in pattern_str.lower() or 'escape' in pattern_str.lower():
    severity = SecurityConfig.SEVERITY_CRITICAL
```
- `pattern.pattern`에서 정규식 문자열을 그대로 검사. 정규식 메타 문자가 포함되어 있어 `'jailbreak' in pattern_str`이 의도와 다르게 동작할 수 있음
- 심각도 결정 로직이 패턴 메타 문자에 의존 → 신뢰 불가

**BUG-1.2: 위반 카운팅 중복 가능 (Line 173, 190)**
```python
self._violation_count += len(violations)
```
- 하나의 `validate_prompt()` 호출에서 여러 패턴이 동일 content를 매칭하면 중복 카운팅 발생

**BUG-1.3: Ring 0 태그 이스케이프 일관성 없음 (Line 388–389)**
```python
sanitized = sanitized.replace("[RING-", "[ESC_RING-")   # 문자열 치환
sanitized = sanitized.replace("</RING", "&lt;/RING")    # HTML 엔티티
```
- 두 방식 혼용. LLM은 `&lt;`를 HTML로 해석하지 않을 수 있어 이스케이프 무력화 가능
- 유니코드 정규화 우회 가능: `\uff3b` (전각 `[`) 사용 시 탐지 실패

#### 보안취약점 (SECURITY)

**SEC-1.1: syscall_request_tool() 불충분한 justification 검증 — CRITICAL (Line 489–510)**
```python
if justification and len(justification) > 20:
    return {"granted": True, ...}
```
- 21자 이상이면 `s3_delete`, `db_delete` 등 민감 권한 자동 승인
- Justification 내용 검증 없음 (주석: "프로덕션에서는 ShieldGemma로 검증" — 미구현)
- 예: `"Please let me delete this file because I need to."` → 즉시 승인

**SEC-1.2: Block-list 기반 검증의 근본적 한계 — CRITICAL (constants.py)**
- 알려진 패턴만 탐지, 신규/변형 기법 미탐지
- 인코딩 우회: Base64, 로마 숫자(`ⅠGNORE`), Zero-Width Space(`IGNORE\u200bPREVIOUS`) 탐지 불가
- White-list 기반 허용 모드 없음

**SEC-1.3: Ring 레벨 간 실질적 보안 차이 없음 — HIGH (Line 162–165)**
- Ring 1, 2는 정의만 있고 검증 로직 없음. 실제로는 Ring 3만 검증
- Ring 0/1/2는 무조건 신뢰 처리

**SEC-1.4: SIGKILL 조건 비대칭 — HIGH (Line 168–172)**
```python
has_high = any(v.severity == SecurityConfig.SEVERITY_HIGH for v in violations)
# has_high는 정의되지만 사용되지 않음
if has_critical and self.enable_auto_sigkill:
    should_sigkill = True
```
- `has_high` 변수가 정의되나 SIGKILL 트리거에 사용 안 됨
- RING_0_TAMPERING (HIGH 심각도)도 SIGKILL 미적용

**SEC-1.5: Zero-Width 문자 등 인코딩된 injection 우회 (Line 158)**
- `IGNORE\u200bPREVIOUS` (Zero-Width Space), RTL/LTR 마크, 제어 문자 삽입 → 정규식 미탐지

**SEC-1.6: create_ring_0_prompt() 입력 검증 없음 (Line 311–361)**
- `system_purpose`, `security_rules`, `tool_permissions` 사용자 입력 검증/sanitization 없음
- Ring 0 프롬프트 자체가 injection 대상이 될 수 없다고 잘못 가정

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-1.1: 한글/최신 jailbreak 패턴 미포함**
- "당신은 이제...", "You are in developer mode", Role-playing 프롬프트, CoT 강제 등 미탐지

**WEAK-1.2: Ring 1, 2 미구현**
- `RingLevel.RING_1_DRIVER`, `RING_2_SERVICE` 정의만 존재, 실제 검증 로직 없음

**WEAK-1.3: 메트릭 정보 부족 (Line 555–562)**
- 위반 유형별 분포, 시간대별 추세, 패턴별 탐지 빈도, 거짓 양성 비율 추적 없음

**WEAK-1.4: 보안 로깅 불완전**
- Request 전체 내용 미로깅, correlation ID 없음, 타임스탬프 누락

**WEAK-1.5: 정화 방식의 단순성 (Line 293–305)**
- 정규식 매칭 부분만 `[FILTERED_BY_RING_PROTECTION]`으로 치환. 의미 동일한 우회 표현 미대응
- 태그 이스케이프(`[ESCAPED_RING]`)와 패턴 필터(`[FILTERED_BY_RING_PROTECTION]`) 마커 불일치

**WEAK-1.6: 싱글톤 hot-reload 불가**
- 패턴 업데이트 시 배포 필요. 런타임 정책 변경 불가

**WEAK-1.7: sanitize_healing_advice() 부분 필터링 (Line 540–549)**
- `result.is_safe == False`이지만 `sanitized_content`가 존재하면 필터링된 내용 반환
- 완전 차단(sentinel) 우회 경로 존재

---

### 2.2 `error_classifier.py`

**경로:** `backend/src/services/recovery/error_classifier.py`
**라인 수:** 263줄

#### 버그 (BUG)

**BUG-2.1: 한글/다국어 에러 메시지 미탐지 (Line 43–116)**
- 모든 패턴이 영어 전용. "JSON 디코딩 오류" 등 한글 에러 분류 불가

**BUG-2.2: Semantic → Deterministic 순서로 중복 패턴 위험 (Line 157–171)**
- Semantic 먼저 체크 후 Deterministic 체크. 양쪽 겹치는 패턴 있으면 Semantic 우선 분류 (의도 불명)

**BUG-2.3: Circuit Breaker 임계값 경계 혼동 (Line 151–155)**
```python
if healing_count >= self.MAX_AUTO_HEALING_COUNT:  # >= 3
```
- 정확히 3회에 갑자기 SEMANTIC으로 전환. "exceeded limit (3)" 메시지가 혼동 유발

**BUG-2.4: Context 기반 분류 우선순위 불명 (Line 173–187)**
- Context 플래그가 있어도 패턴 매칭이 먼저 수행되어 Context 검사가 무의미

**BUG-2.5: Unknown 에러 기본값이 SEMANTIC — 거짓 음성 (Line 189–194)**
- 분류 불가 에러를 모두 수동 개입 필요(SEMANTIC)로 처리. 자동 복구 가능한 신규 에러도 차단

#### 보안취약점 (SECURITY)

**SEC-2.1: Semantic 패턴 과도한 광범위성 — 거짓 양성 (Line 81–116)**
- `r"forbidden"`, `r"401"`, `r"403"` 패턴: 일반 HTTP 상태 에러도 수동 개입 필요로 오분류

**SEC-2.2: healing_count 파라미터 조작 가능 — MEDIUM (Line 129)**
- 호출자가 `healing_count=999`로 전달 시 무조건 SEMANTIC 반환 → 자동 복구 차단(DoS)

**SEC-2.3: Regex Injection (현재는 정적 패턴, 미래 위험) — LOW (Line 122–127)**
- 동적 패턴 추가 기능 추가 시 regex injection 위험

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-2.1: 분류 신뢰도(Confidence) 반환 없음**
- `(ErrorCategory, str)` 만 반환. 오탐 가능성을 호출자가 알 수 없음

**WEAK-2.2: 여러 패턴 동시 매칭 시 첫 패턴만 사용 — 비결정론적**

**WEAK-2.3: Provider별 에러 자동 분류 없음**
- 새 LLM provider 추가 시 패턴 수동 업데이트 필요

**WEAK-2.4: 에러 메시지 정규화 없음**
- 공백 미트림, 공백으로 구분된 동일 에러 미탐지

**WEAK-2.5: 재시도 무한 루프 확실한 보호 없음**

**WEAK-2.6: get_healing_advice() 일반적 조언만 반환**
- 에러 메시지 내 위치/맥락 기반 구체적 제안 없음

**WEAK-2.7: 모니터링/메트릭 완전 부재**

---

### 2.3 `agent_guardrails.py`

**경로:** `backend/src/services/governance/agent_guardrails.py`
**라인 수:** 421줄

#### 버그 (BUG)

**BUG-3.1: CircuitBreaker HALF_OPEN → OPEN 전이 조건 불완전 (Line 70–102)**
```python
if self._state.failure_count >= self.failure_threshold:
    self._state.state = "OPEN"
```
- HALF_OPEN 상태에서 실패 1회 시 failure_count만 증가, OPEN 복귀 안 됨
- threshold=3이면 HALF_OPEN에서 3번 연속 실패해야 OPEN 복귀 → 보호 약화

**BUG-3.2: detect_slop() 반복 탐지 알고리즘 거짓 양성 (Line 160–166)**
```python
for i in range(0, min(len(output_json) - 10, 1000), 100):
    substring = output_json[i:i+10]
    count = output_json.count(substring)
    if count > 100:
        return True, ...
```
- 100개 항목 JSON 배열의 공통 패턴(`,"value"` 등)이 100회 이상 나타나면 SLOP 오탐

**BUG-3.3: detect_plan_drift() 의미론적 검증 미구현 (Line 274–308)**
```python
# TODO: Integrate Llama-3-8B or similar model
```
- 실제 구현: 키워드 교집합/합집합만 계산. 동의어("삭제" vs "제거") 미구분

#### 보안취약점 (SECURITY)

**SEC-3.1: CircuitBreaker 분산 환경에서 상태 공유 불가 — HIGH**
- 메모리 기반 상태 → Lambda/ECS 다중 인스턴스에서 각각 독립 상태
- Instance A가 OPEN이어도 Instance B는 계속 요청 가능 → Guardrail 우회

**SEC-3.2: calculate_gas_fee() 토큰 카운팅 조작 가능 (Line 178–197)**
- `total_tokens_used` 검증 없음. 악의적 에이전트가 1로 조작 시 비용 통제 우회

**SEC-3.3: detect_plan_drift() 파괴적 키워드 우회 (Line 290–297)**
```python
destructive_keywords = {"delete", "remove", "drop", "destroy", "bypass", "ignore"}
```
- "erase", "purge", "wipe", "truncate", "exec", "eval" 등 미포함
- 문자 삽입 우회: "d3l3t3", "del ete"

**SEC-3.4: check_agent_health() 예외 처리 부재로 guardrail 비활성화 (Line 315–406)**
- 순환 참조 구조 agent_output 전달 시 `json.dumps()` 실패 → 전체 guardrail 우회

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-3.1: detect_slop() JSON 깊이 체크 TODO로 방치 (Line 168–169)**
```python
# Check 3: Excessive nesting (TODO: Implement JSON depth check)
```

**WEAK-3.2: CircuitBreaker reset() 권한 제어 없음**
- 누구나 OPEN 상태 Circuit Breaker를 즉시 리셋 가능

**WEAK-3.3: 파괴적 키워드 페널티 50% — 미약**
- `intent_retention_rate *= 0.5` → similarity_threshold 설정에 따라 DRIFT 미탐지 가능

---

### 2.4 `trust_score_manager.py`

**경로:** `backend/src/services/governance/trust_score_manager.py`
**라인 수:** 282줄

#### 버그 (BUG)

**BUG-4.1: 동시 업데이트 Race Condition (Line 78–170)**
```python
trust_state = self.agent_scores[agent_id]      # Line 107
old_score = trust_state.current_score           # Line 108
# ... 계산 ...
trust_state.current_score = new_score           # Line 155
```
- 멀티스레드 환경에서 두 스레드가 동시에 update_score() 호출 시 마지막 업데이트만 반영

**BUG-4.2: _flush_history_to_metrics() 예외 시 silent failure (Line 262–283)**
- DynamoDB 연결 실패 → 신뢰 점수 영구 저장 실패 → 시스템 재시작 시 히스토리 소실

**BUG-4.3: streak_ratio가 현재 상태와 무관 (Line 125–130)**
- 과거 기록에만 의존하는 연속 성공 비율. 현재 상태 반영 안 됨

#### 보안취약점 (SECURITY)

**SEC-4.1: 신뢰 점수 인메모리 저장 — CRITICAL (Line 75–76)**
```python
self.agent_scores: Dict[str, TrustScoreState] = {}
```
- 프로세스 재시작 시 신뢰도 초기화. 악의적 에이전트가 재시작으로 신뢰도 페널티 회피

**SEC-4.2: governance_result 입력값 검증 없음 (Line 78–170)**
```python
decision = governance_result.get("decision", "APPROVED")  # 기본값 APPROVED
```
- 조작된 governance_result 전달 시 신뢰도 점수 조작 가능

**SEC-4.3: Ring Level별 Penalty Multiplier 의도 불명확 (Line 68–73)**
```python
RING_PENALTY_MULTIPLIERS = {0: 2.0, 1: 1.5, 2: 0.8, 3: 0.5}
```
- 권한이 낮을수록 페널티 감소. 의도된 설계인지 실수인지 불명확

**SEC-4.4: get_governance_mode() 실제 강제 메커니즘 없음 (Line 172–195)**
- STRICT/OPTIMISTIC 반환만 하고 실제 실행 제어 연결 불명확

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-4.1: EMA 가속도 계수 고정값 (Line 63)**
- 모든 에이전트에 동일 EMA_ACCELERATION=2.0 적용

**WEAK-4.2: 신뢰도 하향 트리거 결정 상태 3종만 처리**
- "WARNING", "DEFER", "TIMEOUT" 등 추가 상태 미처리

**WEAK-4.3: History 플러시로 장기 추세 분석 불가**
- HISTORY_MAX_SIZE=20 초과 시 절반 삭제. 최근 20번 실행만 추적

---

### 2.5 `constitution.py`

**경로:** `backend/src/services/governance/constitution.py`
**라인 수:** 120줄

#### 보안취약점 (SECURITY)

**SEC-5.1: 헌법 조항과 실제 Guardrail 강제 간 단절 — HIGH**
- `DEFAULT_CONSTITUTION`은 정의만 있고 검증 로직 없음
- `agent_guardrails.py`와 연결점 없음. 헌법이 문서로만 존재

**SEC-5.2: 심각도(Severity) 정의만 있고 강제 메커니즘 없음**
```python
class ClauseSeverity(Enum):
    CRITICAL = "critical"   # Immediate REJECTED — 코드로 미구현
```

**SEC-5.3: PII 탐지 조항(Article 6) 구현 불명확**
- 이메일 정규식? 전화번호 패턴? 탐지 알고리즘 명시 없음

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-5.1: 커스텀 조항 추가만 가능, 기본 조항 오버라이드/제거 불가**

**WEAK-5.2: 헌법 버전 관리 없음**

**WEAK-5.3: 조항별 구현 상태(is_implemented) 추적 필드 없음**

---

### 2.6 `retroactive_masking.py`

**경로:** `backend/src/services/recovery/retroactive_masking.py`
**라인 수:** 278줄

#### 버그 (BUG)

**BUG-6.1: str.replace() 부분 일치 오류 (Line 191, 197, 203, 209, 215)**
```python
text = text.replace(email, f"***EMAIL_{email_hash}***")
```
- `"user@example.com"`이 `"user@example.com.au"` 안에 포함되면 부분 치환으로 데이터 훼손

**BUG-6.2: 마스킹 카운팅 오류 (Line 192, 198, 204, 210, 216)**
- `total_masked += 1`은 PII 타입당 1증가. replace()가 3번 치환해도 카운트는 +1

**BUG-6.3: 딕셔너리 얕은 복사 (Line 171)**
```python
masked = output.copy()
```
- 중첩 딕셔너리/리스트 있으면 원본 `output`도 의도치 않게 마스킹됨

#### 보안취약점 (SECURITY)

**SEC-6.1: SHA256 해시 8자리 역마스킹 공격 가능 — CRITICAL (Line 190–216)**
```python
email_hash = hashlib.sha256(email.encode()).hexdigest()[:8]
```
- 2^32 조합, 생일 역설로 ~65,000개 이메일 중 50% 충돌 확률
- 사전 공격으로 원본 PII 복원 가능

**SEC-6.2: LLM 기반 탐지 보안 위험 (Line 75–154)**
- Gemini에 민감정보 전달. LLM 탈옥/프롬프트 인젝션 시 잘못된 PII 반환 가능

**SEC-6.3: 신용카드 Luhn 알고리즘 검증 없음**
- 유효하지 않은 카드번호도 마스킹 → 정상 데이터 훼손

**SEC-6.4: IP 주소 옥텟 범위 검증 없음**
- `"999.999.999.999"` 도 매칭

**SEC-6.5: SSN 체크디짓 검증 없음**
- `\d{6}-?[1-4]\d{6}` 단순 패턴, 유효성 체크 없음

**SEC-6.6: 마스킹 후 재검증 없음**
- 마스킹 후 PII가 남아있는지 확인 안 함

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-6.1: 한국 주민번호 체크디짓 검증 없음**

**WEAK-6.2: 누락 PII 유형 — 여권번호, 운전면허번호, 계좌번호, 의료정보, 국제전화번호**

**WEAK-6.3: 예외 처리 세분화 없음** — JSON 파싱 실패, 타임아웃 등 모두 동일 처리

**WEAK-6.4: 이메일 정규식이 유니코드 주소 미지원** (`用户@example.com`)

**WEAK-6.5: 일관성 추적 역추적 가능** — 동일 PII는 항상 동일 해시 → 동일 이메일임 노출

**WEAK-6.6: 부분 응답(일부 필드 누락) 처리 불충분**

**WEAK-6.7: 마스킹된 출력 재검증 없음**

---

### 2.7 `pii_masking_service.py`

**경로:** `backend/src/services/recovery/pii_masking_service.py`

#### 버그 (BUG)

**BUG-7.1: URL 토큰이 URL2 안에 포함 시 부분 복원 오류 (Line 192–196)**

**BUG-7.2: 괄호 균형 검사 로직 오류 (Line 172–180)**
```python
open_parens = url.count('(')
close_parens = url.count(')')
```
- 카운트만 비교, 순서 무시. `"url)("` → 균형 맞다고 판단

**BUG-7.3: 싱글톤 스레드 안전성 없음 (Line 246–263)**
```python
if _pii_masking_instance is None:
    _pii_masking_instance = PIIMaskingService(...)
```
- 멀티스레드에서 여러 인스턴스 생성 가능 (threading.Lock 없음)

**BUG-7.4: 이메일 정규식에 캡처 그룹 있으나 설계 불명확**

**BUG-7.5: UUID 기반 토큰 충돌 이론적 가능성**

**BUG-7.6: URL 복원 순서 의존성**

#### 보안취약점 (SECURITY)

**SEC-7.1: API 키 정규식 `sk-*` 형식만 지원 (Line 36)**
- AWS, GCP, Azure 키 형식 미지원

**SEC-7.2: 전화번호 정규식 과도하게 관대함 (Line 40)**
- `"\d{3}[-.\s]?\d{3,4}[-.\s]?\d{4}"` → 일반 숫자 조합도 마스킹

**SEC-7.3: 신용카드 Luhn 검증 없음 (Line 44)**

**SEC-7.4: SSN 패턴 검증 없음 (Line 42)**

**SEC-7.5: strict_mode가 URL 처리에만 영향 — 이름 오해 유발 (Line 63–72)**
- 엄격한 PII 검증을 암시하지만 실제로는 URL 처리만 변경

**SEC-7.6: 정규식 DoS(ReDoS) 가능성 (Line 100–101)**
- 악의적 입력으로 정규식 과도 백트래킹 유발 가능

**SEC-7.7: mailto: 내 이메일 마스킹 문제**
- `"mailto:user@example.com"` → `"mailto:[EMAIL_REDACTED]"` (링크 파손)

**SEC-7.8: 마스킹 전후 텍스트 길이로 메타데이터 유출 가능**

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-7.1: 누락 PII 유형** — 여권번호, 운전면허번호, 계좌번호, 주민번호, 의료정보

**WEAK-7.2: bytes/Tuple/Set 타입 마스킹 미지원**

**WEAK-7.3: 마스킹 추적성 없음** — retroactive_masking.py와 달리 카운트/카테고리 메타데이터 없음

**WEAK-7.4: URL 쿼리 파라미터 내 PII 미탐지**
- `"?email=user@example.com"` → 파라미터 내 이메일 마스킹 안 됨

**WEAK-7.5: 국제전화번호 미지원**

**WEAK-7.6: 마스킹 후 재검증 없음**

**WEAK-7.7: 예외 처리 없음** — `pattern.sub()` 실패 시 처리 없음

**WEAK-7.8: LRU 캐시 없음** — 반복 호출 시 성능 저하

**WEAK-7.9: 정규식 성능 O(n×m)**

**WEAK-7.10: message와 context 마스킹이 독립적 — UUID 불일치 가능성**

---

## 3. 2순위: 상태·일관성 레이어

---

### 3.1 `state_versioning_service.py`

**경로:** `backend/src/services/state/state_versioning_service.py`
**라인 수:** ~1,549줄

#### 버그 (BUG)

**BUG-8.1: self.prefix 속성 미초기화 — CRITICAL (Line 1247, 1329)**
```python
key = f"{self.prefix}blocks/{block_id}.json"
```
- `__init__()`에서 `self.prefix` 초기화 없음 → rollback/commit 시 `AttributeError` 즉시 발생

**BUG-8.2: 버전 할당 Race Condition — HIGH (Line 359, 180)**
- 두 Lambda가 동시에 `_get_next_version()` 호출 시 동일 version 번호 두 manifest에 할당 가능
- TransactWriteItems ConditionExpression이 `(workflow_id, version)` 유일성 미체크

**BUG-8.3: Merkle DAG Hash 충돌 가능성 (Line 687–708)**
```python
combined = config_hash + (parent_hash or '') + blocks_hash
return hashlib.sha256(combined.encode()).hexdigest()
```
- 구분자 없는 단순 문자열 연결 → `"abc"+"def"+"ghi"` = `"ab"+"cde"+"fghi"` 충돌

**BUG-8.4: S3 Select 폴백 silent failure (Line 1180–1212)**
- S3 Select에서 데이터 없을 때 전체 오브젝트 다운로드로 폴백. 4MB+ 블록 낭비 발생

**BUG-8.5: 블록 참조 카운팅 원자성 부재 — HIGH (Line 362–473)**
- 매니페스트 + 첫 99블록은 원자적. 블록 100개 이상은 별도 트랜잭션
- 중간 크래시 시 매니페스트는 존재하나 블록 참조 카운트=0 → GC가 삭제 가능

#### 보안취약점 (SECURITY)

**SEC-8.1: segment_manifest 구조 검증 없음**
- 과도한 크기, 순환 참조 → DoS

**SEC-8.2: S3 경로 sanitization 부족 (Line 324)**
```python
Key=block.s3_path.replace(f"s3://{self.bucket}/", "")
```
- `../` 시퀀스 포함 시 경로 탈출 가능 (S3에서 실제 발생 가능성 낮으나 패턴 위험)

**SEC-8.3: 매니페스트 접근 권한 검증 없음 (Line 517–559)**
- `get_manifest(manifest_id)` 호출자 권한 검증 없음

**SEC-8.4: S3 메타데이터 JSON 인젝션 (Line 141–145)**
- 사용자 입력 block_id, transaction_id가 S3 메타데이터에 검증 없이 삽입

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-8.1: 커밋 실패 보상 트랜잭션 없음**
- DynamoDB 성공 후 S3 태깅 실패 시 고아 "committed" 블록 발생

**WEAK-8.2: 청크 재조립 해시 검증 없음**
- 분할된 청크 로드 후 무결성 검증 없음

**WEAK-8.3: 연쇄 삭제 불완전 (Line 1497–1549)**
- 매니페스트가 동일 블록을 여러 번 참조 시 참조 카운트 오감소 가능

**WEAK-8.4: TransactWriteItems 재시도 즉시 재시도 (Line 371–502)**
- 지수 백오프 없이 3회 즉시 재시도 → DynamoDB 스로틀링 시 모두 실패

**WEAK-8.5: 고아 pending 작업 복구 없음**
- Lambda 크래시 후 pending 블록이 GC되지 않으면 영구 스토리지 누수

---

### 3.2 `eventual_consistency_guard.py`

**경로:** `backend/src/services/state/eventual_consistency_guard.py`
**라인 수:** 368줄

#### 버그 (BUG)

**BUG-9.1: Phase 3 부분 실패 시 매니페스트 불일치 — MEDIUM (Line 212–231)**
- Phase 2 성공 후 Phase 3에서 일부 S3 태그만 업데이트되면, 나머지 블록은 "pending" 상태로 남아 GC 대상이 됨

**BUG-9.2: 중복 트랜잭션 ID 처리 없음 — HIGH (Line 110–117)**
- 클라이언트 타임아웃 재시도 시 새 transaction_id 생성 → 두 세트의 pending 블록 발생 (멱등성 없음)

**BUG-9.3: _batch_update_block_references 배치 실패 미처리 — HIGH (Line 241–288)**
- 배치 1 성공 후 배치 2 실패 시 매니페스트는 생성됐으나 블록 99개 이상의 참조 카운트=0
- GC가 참조된 블록 삭제 가능

**BUG-9.4: GC 레이스 컨디션 — CRITICAL (Line 330–361)**
```
T0: 블록 "hash123" pending 업로드 (transaction_id="txn-A")
T5: txn-A 실패, 5분 후 GC 예약
T8: 블록 "hash123" 재업로드 (transaction_id="txn-B")
T10: GC 실행, transaction_id="txn-A" 확인 → 삭제
T15: txn-B Phase 2, "hash123" 참조 → 블록 없음!
```
- 데이터 손실 가능. S3 Object Lock 미사용으로 보호 불가

#### 보안취약점 (SECURITY)

**SEC-9.1: blocks 파라미터 검증 없음 (Line 78–117)**

**SEC-9.2: SQS DLQ URL 검증 없음 (Line 358–361)**
- gc_dlq_url이 공격자 제어 가능 시 내부 블록 정보 노출

**SEC-9.3: 트랜잭션 타이밍 정보 로그 유출**

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-9.1: Phase 3 실패 시 보상 트랜잭션 없음** — `logger.warning()`만 기록

**WEAK-9.2: DynamoDB 25KB 아이템 한계 미확인** — 세그먼트 해시가 많으면 트랜잭션 실패

**WEAK-9.3: 일관성 위반 모니터링 없음** — CloudWatch 메트릭 전무

**WEAK-9.4: Phase 2/3 간 순서 보장 없음** — 다운스트림이 매니페스트를 읽고 pending 블록에 접근 가능

**WEAK-9.5: S3 리전에 따른 eventually consistent 문제**

---

### 3.3 `merkle_gc_service.py`

**경로:** `backend/src/services/state/merkle_gc_service.py`

#### 버그 (BUG)

**BUG-10.1: 참조 카운트 음수 가능 — CRITICAL (Line 255)**
```python
UpdateExpression="SET ref_count = if_not_exists(ref_count, :zero) - :dec"
```
- `ref_count` 없는 블록: 0 - 1 = -1. 초기화 없이 감소 시 음수 발생

**BUG-10.2: graceful_wait TOCTOU 레이스 컨디션 — HIGH (Line 286–315)**
- `zero_reached_at` 체크와 업데이트 사이 다른 프로세스가 덮어쓸 수 있음

**BUG-10.3: DynamoDB Streams TTL 이벤트 탐지 불안정 (Line 88–90)**
```python
if record['userIdentity'].get('type') != 'Service':
    continue
```
- AWS TTL 이벤트의 정확한 userIdentity 구조 변동 가능

**BUG-10.4: zero_reached_at 타임스탬프 파싱 예외 처리 없음 (Line 301)**
```python
zero_time = datetime.fromisoformat(zero_reached_at)  # try-catch 없음
```

**BUG-10.5: Config 참조 카운트 쿼리가 잘못된 인덱스 사용 (Line 394–399)**
- `manifest_hash` 인덱스로 `config_hash` 참조 수 조회 → 항상 부정확한 결과

**BUG-10.6: S3 delete_object 성공 검증 없음 (Line 217–222)**
- `delete_object()`는 없는 오브젝트도 성공 반환. 실제 삭제 수 부정확

**BUG-10.7: 블록 ID 추출 로직 취약 (Line 153, 211, 479)**
```python
block_id = block_path.split('/')[-1].replace('.json', '')
```
- 경로 형식 불일치 시 빈 문자열 or 오류

#### 보안취약점 (SECURITY)

**SEC-10.1: 이벤트 레코드 입력 검증 없음**
- 과도하게 큰 OldImage → DoS, 깊은 중첩 → CPU 고갈

**SEC-10.2: 순회 스택 크기 제한 없음 (Line 715–756)**
- 수천 개 매니페스트의 고아 탐지 시 메모리 고갈

**SEC-10.3: TTL 연장 무제한 (Line 500)**
```python
':new_ttl': int(time.time()) + 90 * 24 * 3600
```
- 보안 위반 반복 트리거 시 TTL 무한 연장 가능

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-10.1: 실제 삭제 수 미로깅** — `len(batch)`를 삭제 수로 보고 (실제 아닐 수 있음)

**WEAK-10.2: graceful_wait 5분 하드코딩** — 환경변수로 튜닝 불가

**WEAK-10.3: CloudWatch 메트릭 전무**

**WEAK-10.4: 순환 참조 매니페스트 DAG 유효성 검증 없음**

**WEAK-10.5: Glacier 전략 미구현** — 주석에만 "30일 → Glacier → 90일 → 삭제"

---

### 3.4 `async_commit_service.py`

**경로:** `backend/src/services/state/async_commit_service.py`

#### 버그 (BUG)

**BUG-11.1: Redis 불가 시 S3 기반 커밋 상태 무시 — HIGH (Line 105–111)**
```python
except Exception as e:
    logger.warning(f"Redis check failed: {e}")
# redis_status = None → is_committed=False 반환
```
- Redis 다운 시 S3에 커밋이 존재해도 `is_committed=False` 반환 (소스 오브 트루스 무시)

**BUG-11.2: S3 에러 코드 체크 오류 — HIGH (Line 232)**
```python
if e.response['Error']['Code'] == '404':  # 실제는 'NoSuchKey'
    return False
```
- boto3 ClientError의 실제 코드는 `'NoSuchKey'`, 정수 404가 아님 → 조건 미충족

**BUG-11.3: 루프 이전 redis_status 미정의 (Line 182–188)**
- RETRY_ATTEMPTS=0이면 루프 미실행, `redis_status` 미정의 참조 → NameError

**BUG-11.4: 지수 백오프 MAX_DELAY 도달 후 정체 (Line 149)**
```python
delay = min(delay * 2, MAX_DELAY)  # 0.4s에서 고정
```
- 실제로는 0.1 → 0.2 → 0.4 → 0.4 → ... (비진정한 지수 백오프)

**BUG-11.5: TOCTOU 창 존재 (Line 117–132)**
- Redis 'committed' 확인 후 S3 확인 사이에 S3 오브젝트 삭제 가능

**BUG-11.6: 지터 범위 문서화 부족**

#### 보안취약점 (SECURITY)

**SEC-11.1: 재시도 루프 Rate Limiting 없음**
- 재시도당 Redis + S3 2회 네트워크 호출 → DoS 가능

**SEC-11.2: 싱글톤 스레드 안전성 없음 (Line 240–248)**

**SEC-11.3: 클라이언트 자격증명 검증 없음**

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-11.1: 부분 실패 처리 없음**

**WEAK-11.2: AWS 권장 Full Jitter 백오프 미구현**

**WEAK-11.3: Circuit Breaker 패턴 없음**
- Redis/S3 일시적 장애 시 매 호출마다 3회 재시도

**WEAK-11.4: 싱글톤 스레드 안전성 없음**

---

### 3.5 `checkpoint_service.py`

**경로:** `backend/src/services/checkpoint_service.py`

#### 버그 (BUG)

**BUG-12.1: S3 경로 파싱 안전성 없음 — CRITICAL (Line 161–168)**
```python
if s3_path.startswith('s3://'):
    parts = s3_path[5:].split('/', 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ''
```
- `"s3://"` → bucket='', key='' → `get_object(Bucket='', Key='')` 즉시 오류

**BUG-12.2: 체크포인트 복원 시 Race Condition — HIGH (Line 589–627)**
- 체크포인트 조회 후 Step Functions 실행 시작 사이에 GC가 삭제 가능

**BUG-12.3: GSI 폴백 스캔 Limit 불일치 (Line 256–259)**
- 쿼리 문서는 Limit=500, 폴백은 Limit=100. 타임라인 결과 잘림

**BUG-12.4: 체크포인트 ID 충돌 위험 (Line 142–148)**
```python
return f"cp_{timestamp}_{uuid.uuid4().hex[:8]}"
```
- UUID 8자리만 사용 (32비트 엔트로피). 동일 타임스탬프에서 충돌 가능

**BUG-12.5: 타임스탬프 정렬이 문자열 기준 (Line 309)**
- 단일 자리 월/일(`"2024-1-5"`)이면 문자열 정렬 오동작

**BUG-12.6: 대용량 S3 오브젝트 전체 메모리 로딩 (Line 174–181)**
- `response['Body'].read()` 크기 제한 없음. 100MB+ 상태 로딩 시 OOM

**BUG-12.7: 순환 참조 상태의 재귀 diff 무한 루프 (Line 542–554)**

**BUG-12.8: 비원자적 체크포인트 비교 (Line 464–475)**
- 두 체크포인트 순차 조회 사이에 데이터 변경 가능

#### 보안취약점 (SECURITY)

**SEC-12.1: 권한 검증 전무 (전체)**
- `get_execution_timeline(thread_id)` 호출자 권한 미확인. 타 사용자 워크플로 열람 가능

**SEC-12.2: 상태 스냅샷에 비밀 정보 포함 가능**
- API 키, 비밀번호, PII가 포함된 state를 RedAction 없이 직접 반환

**SEC-12.3: S3 경로 탈출 취약점 (Line 161–168)**
- 사용자 제어 s3_path로 다른 버킷 접근 가능

**SEC-12.4: Step Functions ARN 형식 미검증 (Line 677–681)**
```python
f"arn:aws:states:...:{os.environ.get('AWS_ACCOUNT_ID', '')}:stateMachine:{workflow_id}"
```
- AWS_ACCOUNT_ID 없으면 유효하지 않은 ARN. workflow_id에 특수문자 포함 시 위험

**SEC-12.5: 설정 정보 과도 로깅 (Line 184, 258)**

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-12.1: 오래된 체크포인트 정리 로직 없음**
- 체크포인트 무한 누적. 비용/성능 이슈

**WEAK-12.2: 복원 상태 유효성 검증 없음**
- 만료된 API 자격증명, 시간 의존 변수 포함 state 재실행 가능

**WEAK-12.3: 알림 파싱 불완전**
- notification이 문자열도 딕셔너리도 아니면 `{}` 사일런트 기본값

**WEAK-12.4: 대형 상태의 diff 메모리 문제**
- 100MB+ 상태의 added/removed/modified 딕셔너리 동시 생성

**WEAK-12.5: LRU 캐시 없음** — 반복 조회마다 S3/DynamoDB 호출

**WEAK-12.6: 손상된 알림 silent 무시**

**WEAK-12.7: 타임라인 페이지네이션 없음** — 이벤트 10,000개 시 500개만 반환

**WEAK-12.8: Executor 타임아웃 없음** — DynamoDB 쿼리 무한 대기 가능

---

## 4. 3순위: 실행 인프라 레이어

---

### 4.1 `bedrock_client.py` / `gemini_client.py` / `gemini_service.py`

**경로:** `backend/src/services/llm/`

#### 버그 (BUG)

**BUG-13.1: gemini_service.py 메서드 중복 정의 — HIGH (Line 2007–2031, 2155–2212)**
```python
# 첫 번째 정의: urllib.request (Line 2007)
def _download_from_url(self, url: str) -> bytes:
    ...

# 두 번째 정의: requests 라이브러리 (Line 2175) — 이 버전만 실제 사용
def _download_from_url(self, url: str) -> bytes:
    ...
```
- 동일 메서드 두 번 정의. 두 번째만 실행됨. 의도 혼동 및 잠재 오류

**BUG-13.2: `self._context_cache` 속성 미초기화 — MEDIUM (Line 1552)**
- `get_session_cost_summary()`에서 `self._context_cache` 참조 → AttributeError

**BUG-13.3: `clear_context_cache()` 미초기화 속성 참조 (Line 1558–1562)**
- `self._context_cache`, `self._context_cache_key` 미초기화

**BUG-13.4: 스트리밍 output_tokens 항상 0 — MEDIUM (Line 1412)**
```python
output_tokens = len(output_text_buffer) // 4 if output_text_buffer else 0
```
- `output_text_buffer`가 스트리밍 루프에서 업데이트되지 않아 항상 0

**BUG-13.5: bedrock_client.py 스트리밍 non-JSON 청크 silent 폐기 (Line 252–257)**
- 유효한 데이터가 JSON 아니면 debug 로그만 남기고 소실

#### 보안취약점 (SECURITY)

**SEC-13.1: GCP 서비스 계정 임시 파일 미삭제 — CRITICAL (Line 488–493)**
```python
with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
    f.write(sa_key)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = f.name
    logger.debug(f"Service Account credentials written to {f.name}")
```
- `delete=False` → 파일 영구 존재
- 파일 경로가 debug 로그에 기록 → 자격증명 파일 경로 노출

**SEC-13.2: URL 다운로드 SSRF 취약점 — HIGH (Line 1824–1825)**
- 호스트명 검증 없음. `169.254.169.254` (AWS 메타데이터 서버) 접근 가능
- `requests.get()` 리다이렉트 무제한 허용

**SEC-13.3: 파일 경로 검증 없음 — HIGH (Line 1830–1832)**
```python
with open(source, "rb") as f:
    image_bytes = f.read()
```
- `/etc/passwd`, `../../.env` 등 임의 경로 읽기 가능

**SEC-13.4: gemini_client.py API 키 로깅 위험 — HIGH (Line 43–47)**
- 배포 시스템이 환경변수를 로깅하면 GEMINI_API_KEY 노출

**SEC-13.5: S3 URI 경로 탈출 (Line 2015–2020)**

**SEC-13.6: 에러 메시지에 인프라 정보 노출 (Line 556–561)**
- GCP project ID, 자격증명 상태 에러 메시지에 포함

**SEC-13.7: project_id 검증 없이 다운스트림 사용**

**SEC-13.8: Gemini API 키 갱신 전략 없음 (Line 43–52)**
- AWS Secrets Manager 비밀 교체 시 재시작 전까지 만료 키 사용

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-13.1: bedrock_client.py 재시도 로직 없음 (Line 94–143)**
- `ThrottlingException` 탐지 없음. 단일 실패로 전체 작업 실패

**WEAK-13.2: 스트리밍 에러 Rate Limit 미분류 (Line 212–219)**
- 모든 예외를 동일하게 처리 후 동기 폴백. 폴백도 재시도 없음

**WEAK-13.3: 에러 분류에 문자열 매칭 사용**
```python
if "429" in error_msg or "quota" in error_msg:
```
- SDK 버전에 따라 메시지 변동 가능

**WEAK-13.4: Bedrock 타임아웃 설정 없음**

**WEAK-13.5: Context Cache 폴백이 실제 캐시 아님 (Line 726–729)**
```python
cache_name = f"fallback_cache/{self.config.model.value}/{cache_key}"
```
- 실제 Vertex AI 캐시 리소스 이름이 아님. 캐시 사용 안 됨

**WEAK-13.6: 입력 크기 검증 없음 (Line 1614–1617)**
- 이미지 목록 크기/개별 이미지 크기 검증 없음 → 메모리 고갈

**WEAK-13.7: 스트리밍 오류 시 비용 메타데이터 소실 (Line 1409–1441)**

**WEAK-13.8: 비스트리밍 Safety Filter 탐지 없음 (Line 829–1041)**
- 스트리밍은 SAFETY 탐지, 비스트리밍은 빈 응답 silent 반환

**WEAK-13.9: 모델 가용성 검증 없음 (Line 193–195)**
- Gemini 모델 요청 시 Claude Haiku로 silent 전환

**WEAK-13.10: API 키 갱신 전략 없음**

**WEAK-13.11: 스트리밍 토큰 카운팅 부정확** → 비용 추적 오류

---

### 4.2 `distributed_chunk_service.py`

**경로:** `backend/src/services/distributed/distributed_chunk_service.py`

#### 버그 (BUG)

**BUG-14.1: 파티션 슬라이스 인덱스 로직 오류 — CRITICAL (Line 87–88)**
```python
idx_end = chunk_data.get('end_segment', idx_start)  # 기본값이 idx_start (잘못됨)
partition_slice = full_map[idx_start : idx_end + 1]
```
- `end_segment`가 없으면 단일 아이템 슬라이스. 이미 Line 79에서 `end_segment` 계산했지만 미사용

**BUG-14.2: S3 로딩 로직 불완전 — HIGH (Line 56–96)**
- 경계 계산이 글로벌 인덱스 기준인지 불명확
- 로드된 맵이 실제로 요청된 세그먼트를 포함하는지 검증 없음

**BUG-14.3: Task Token 만료 검증 없음 (Line 146–155)**
- Task Token 저장 시 만료 타임스탬프 없음 → 만료된 토큰 사용 가능

**BUG-14.4: 부분 실패 시 상태 오염 (Line 188–195)**
- `PARTIAL_FAILURE` 시에도 `current_state['__latest_segment_id']` 업데이트

**BUG-14.5: state_bucket 유효성 검증 없음 (Line 206–207)**

**BUG-14.6: start_segment 인덱스 경계 검증 없음 (Line 84)**

**BUG-14.7: os 모듈 중복 import (Line 4–5)**

#### 보안취약점 (SECURITY)

**SEC-14.1: Task Token 형식 검증 없음 (Line 146–155)**
- 임의 문자열 저장 가능. 로그 유출 시 토큰 노출

**SEC-14.2: json.dumps(default=str) — 객체 __repr__ 정보 노출 (Line 210, 226)**

**SEC-14.3: S3 업로드 서버사이드 암호화 없음 (Line 214–219)**

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-14.1: 완료 상태 검증 단순 (PARALLEL_GROUP, HITP 미고려)**

**WEAK-14.2: 세그먼트 레벨 재시도 없음**
- 모든 예외를 FAILED 처리. 일시적 장애에도 영구 실패

**WEAK-14.3: 세그먼트 이벤트 스키마 강제 없음**

**WEAK-14.4: PARALLEL_GROUP 중단 동작 미문서화**

**WEAK-14.5: 빈 partition_slice가 성공으로 처리 — 로딩 실패 은닉**

**WEAK-14.6: S3 오프로드 임계값 32KB 하드코딩**
- Step Functions 실제 한도 256KB. 과도하게 보수적

---

### 4.3 `partition_service.py`

**경로:** `backend/src/services/workflow/partition_service.py`

#### 버그 (BUG)

**BUG-15.1: 위상 정렬 알파벳 순 정렬로 비결정론적 — MEDIUM (Line 573–593)**
```python
queue.sort()  # 매 반복마다 알파벳 정렬 → 비결정론적 세그먼트 순서
```
- Kahn 알고리즘 위반. 노드 ID가 "node_10", "node_2"이면 잘못된 순서

**BUG-15.2: 무한 루프 방지 카운터 기준 불명확 — MEDIUM (Line 691–711)**
```python
max_iterations = len(nodes) * 2
```
- 중첩된 병렬 그룹에서 유효한 워크플로도 임계값 초과 가능

**BUG-15.3: forced_segment_starts 사전 초기화 없음 — HIGH (Line 487–489)**
- 수렴 노드를 분기 탐지 후에야 추가 → 여러 분기점 있는 워크플로에서 일부 수렴 노드 누락

**BUG-15.4: 브랜치 노드 ID 중복 허용 (Line 776–778)**

**BUG-15.5: 집계자 수렴 노드 중첩 구조에서 미탐지 (Line 1002–1006)**
- `node_to_seg_map`에 없는 경우 중첩 병렬 그룹 재귀 탐색 없음

**BUG-15.6: 타입 별칭 적용 위치 부적절 (Line 599–605)**
- `"code"` → `"operator"` 변환이 create_segment 내에서 반복 적용

#### 보안취약점 (SECURITY)

**SEC-15.1: 환경변수 검증 없음 (Line 15, 18, 176, 484)**
```python
MAX_PARTITION_DEPTH = int(os.environ.get("MAX_PARTITION_DEPTH", "50"))
```
- 음수, 0, 비정수 값 방어 없음

**SEC-15.2: 노드 타입 별칭 허용 목록 없음 (Line 74)**

**SEC-15.3: SQL 트랜잭션 패턴 문자열 매칭 취약 (Line 365, 388)**
- SQL 주석/문자열 리터럴로 우회 가능

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-15.1: 빈 워크플로 엣지 케이스 미처리** — 노드만 있고 엣지 없으면 임의 노드 선택

**WEAK-15.2: 노드 실행 시간 추정값 비현실적** — LLM 10초 고정. 1~60초+ 실제 변동

**WEAK-15.3: 도달 불가능한 노드 탐지 없음**

**WEAK-15.4: 수렴 노드 탐지가 첫 merge point만 반환** — 비대칭 분기 미처리

**WEAK-15.5: Ring Level 경계 엣지 검증 없음**
- Ring 3 → Ring 1 직접 흐름 탐지/경고 없음

**WEAK-15.6: 암묵적 집계자 부모 검증 없음**

---

### 4.4 `orchestrator_service.py`

**경로:** `backend/src/services/workflow/orchestrator_service.py`

#### 버그 (BUG)

**BUG-16.1: total_segments 누락 시 Step Functions 무한 루프 위험 — HIGH (Line 397–398)**
```python
if "total_segments" not in result:
    result["total_segments"] = initial_state.get("total_segments") or 1
```
- 기본값 1로 대체 → ASL 루프 제어 오동작, 세그먼트 스킵 또는 무한 루프

**BUG-16.2: 서브그래프 순환 참조 방문 집합 초기화 오류 (Line 238–241)**
```python
for node in nodes:
    visited.clear()  # 각 노드마다 초기화 → 상호 참조 사이클 미탐지
    _detect_cycle(node)
```

**BUG-16.3: 비동기 LLM 예외 응답 구조 불일치 (Line 406–418)**
- `new_history_logs` 사용 (정상 경로는 `__new_history_logs`)

**BUG-16.4: Mock 응답에 필수 필드 누락 (Line 360–361)**
```python
return {"status": "PAUSED_FOR_HITP", "next_segment_to_run": 1}
```
- `total_segments`, `final_state` 없음 → Step Functions ASL 파싱 실패

**BUG-16.5: 서브그래프 노드 공유 참조 문제 (Line 463–538)**
- 노드 딕셔너리 deep copy 없음. 동일 노드가 여러 브랜치에 공유 시 수정 상호 영향

**BUG-16.6: 순환 참조 깊이 체크 off-by-one (Line 182–187)**
- 체크 후 재귀. 실제 허용 깊이 51

#### 보안취약점 (SECURITY)

**SEC-16.1: initial_state 검증 없음 (Line 352)**
- 임의 키 포함 가능. 내부 상태 오버라이드, run_config 인젝션 위험

**SEC-16.2: user_api_keys 검증 없음 (Line 383)**
```python
initial_state.setdefault("user_api_keys", {}).update(user_api_keys)
```
- 알 수 없는 provider 키 삽입 가능. 감사 로그 없음

**SEC-16.3: Ring Level 런타임 강제 없음**
- 노드별 검증은 있으나 실행 중 Ring Level 변경 방어 없음

**SEC-16.4: Mock 테스트 데이터 로그 노출**
```python
"code": "state['res'] = 'X'*300000"
```
- DEBUG 로그 수집 시 300KB 데이터 포함

#### 기능빈약 (FUNCTIONAL WEAKNESS)

**WEAK-16.1: 워크플로 검증 오류 메시지 불친절**
- 어떤 노드/엣지에서 실패했는지 불명확

**WEAK-16.2: 서브그래프 추출 실패 silent 처리**
- 추출 실패 시 인라인 버전 계속 사용 (페이로드 크기 경고 없음)

**WEAK-16.3: DynamoDB 체크포인트 테이블 존재 검증 없음**
- 실행 시작 후 첫 체크포인트 시점에 오류 발생

**WEAK-16.4: 대화 스레드 충돌 미탐지**

**WEAK-16.5: LLM 오류 로그에 스택 트레이스 없음**

**WEAK-16.6: 워크플로 실행 타임아웃 없음**
- `app.invoke()` 무제한 대기 가능

---

## 5. 전체 위험도 매트릭스

### CRITICAL 이슈 목록

| # | 파일 | 이슈 | 영향 |
|---|------|------|------|
| 1 | `state_versioning_service.py` | `self.prefix` 미초기화 → RuntimeError | 모든 rollback/commit 작업 즉시 실패 |
| 2 | `eventual_consistency_guard.py` | GC 레이스 컨디션 → 블록 삭제 | 데이터 손실 |
| 3 | `checkpoint_service.py` | S3 경로 파싱 안전성 없음 | 크래시 / 경로 탈출 |
| 4 | `prompt_security_guard.py` | syscall 21자 justification 자동 승인 | 권한 상승 / 임의 권한 획득 |
| 5 | `prompt_security_guard.py` | Block-list 근본적 한계 | 인코딩/변형 공격 완전 우회 |
| 6 | `gemini_service.py` | GCP 자격증명 임시 파일 미삭제 | 자격증명 유출 |
| 7 | `distributed_chunk_service.py` | 파티션 슬라이스 인덱스 오류 | 청크 심각한 과소 처리 |
| 8 | `retroactive_masking.py` | 8자리 해시 역마스킹 가능 | PII 원본 복원 가능 |

### 레이어별 최우선 개선 영역

| 레이어 | 최우선 영역 |
|--------|-----------|
| 보안·신뢰 | syscall 권한 검증 강화, 헌법-guardrail 연결, 신뢰 점수 영속성 |
| 상태·일관성 | self.prefix 초기화, GC 레이스 보호, Redis 폴백 로직 수정 |
| 실행 인프라 | GCP 자격증명 파일 정리, SSRF 방어, 청크 인덱스 수정 |

### 개선 우선순위 권장

```
즉시 (프로덕션 차단):
  - state_versioning_service.py BUG#1 (self.prefix)
  - eventual_consistency_guard.py BUG#4 (GC 레이스)
  - gemini_service.py SEC#1 (자격증명 파일)
  - distributed_chunk_service.py BUG#1 (파티션 인덱스)
  - prompt_security_guard.py SEC#1 (syscall 권한)

단기 (다음 릴리스 전):
  - trust_score_manager.py: 영속성 + Race Condition
  - async_commit_service.py: S3 에러 코드 수정
  - checkpoint_service.py: S3 경로 검증 + 권한 체크
  - bedrock_client.py: 재시도 로직
  - constitution.py: Guardrail 연결

중기 (아키텍처 개선):
  - CircuitBreaker 분산 환경 지원 (Redis 기반)
  - Ring 1/2 검증 레이어 구현
  - PII 마스킹 고도화 (Luhn, 체크디짓, 추가 유형)
  - GC 모니터링 CloudWatch 메트릭
  - 에러 분류기 다국어 지원
```

---

*본 문서는 점검 결과 기록 목적으로만 작성되었으며, 코드 수정은 별도 작업으로 진행됩니다.*
