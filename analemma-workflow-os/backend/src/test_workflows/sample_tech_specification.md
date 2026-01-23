# Analemma OS Technical Specification
## Version 2.5.0 - Internal Architecture Document

---

## 1. System Overview

Analemma OS는 AI-First 워크플로우 자동화 플랫폼입니다. 
복잡한 비즈니스 로직을 LLM 기반 노드와 네이티브 오퍼레이터로 구성된 
DAG(Directed Acyclic Graph)로 정의하고 실행합니다.

### 1.1 Core Components

```
┌─────────────────────────────────────────────────────────────┐
│                    Analemma OS Architecture                  │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────┐   │
│  │   Frontend  │   │   API GW    │   │  Step Functions  │   │
│  │   (React)   │◄──┤  (Lambda)   │◄──┤  (Distributed)  │   │
│  └─────────────┘   └─────────────┘   └─────────────────┘   │
│         │                 │                   │             │
│         ▼                 ▼                   ▼             │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────┐   │
│  │  WebSocket  │   │   DynamoDB  │   │   S3 (State)    │   │
│  └─────────────┘   └─────────────┘   └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Security Architecture

### 2.1 Authentication Flow

현재 인증은 API Gateway의 Lambda Authorizer를 통해 처리됩니다.

**[보안 검토 필요]** 현재 구현에는 다음 문제가 있습니다:

1. **JWT 토큰 검증 미비**: `api_authorizer.py`에서 토큰 만료 검증이 
   클라이언트 시간에 의존합니다.
   
   ```python
   # 현재 코드 (취약)
   if token.exp < client_timestamp:
       return unauthorized()
   ```

2. **SQL Injection 가능성**: `workflow_search.py`의 검색 쿼리에서 
   사용자 입력이 직접 쿼리에 삽입됩니다.
   
   ```python
   # 위험한 패턴
   query = f"SELECT * FROM workflows WHERE name LIKE '%{user_input}%'"
   ```

3. **CORS 정책 과도한 허용**: `template.yaml`에서 모든 Origin을 허용합니다.
   ```yaml
   Cors:
     AllowOrigin: "'*'"  # 프로덕션에서 제한 필요
   ```

### 2.2 Data Encryption

- **전송 중 암호화**: TLS 1.3 사용 (AWS ALB)
- **저장 시 암호화**: S3 SSE-KMS, DynamoDB 암호화 활성화
- **[취약점]** Lambda 환경변수에 API 키가 평문으로 저장됨

---

## 3. Operator System

### 3.1 operator_runner (Legacy)

기존 `operator_runner`는 `exec()`를 사용하여 임의의 Python 코드를 실행합니다.

**[심각한 보안 취약점]**
- Remote Code Execution (RCE) 취약점
- MOCK_MODE에서만 허용되나, 환경변수 우회 가능성 존재
- 샌드박스가 완벽하지 않음 (`__subclasses__` 공격 가능)

```python
# 취약한 코드 패턴
exec(code, {"__builtins__": safe_builtins}, local_vars)
```

### 3.2 operator_official (신규)

Built-in Strategy 패턴으로 구현된 안전한 오퍼레이터입니다.

**지원 전략:**
- `json_parse`, `json_stringify`
- `list_filter`, `list_map`, `list_reduce`
- `string_template`, `regex_extract`
- `if_else`, `switch_case`

**[최적화 제안]** 
- `list_filter` 조건 평가 시 캐싱 적용 가능
- 대용량 리스트 처리 시 청크 분할 필요

---

## 4. LLM Integration

### 4.1 Provider Configuration

```python
PROVIDER_CONFIG = {
    "gemini": {
        "model": "gemini-2.0-flash",
        "max_tokens": 8192,
        "temperature": 0.7
    },
    "bedrock": {
        "model": "anthropic.claude-3-sonnet",
        "max_tokens": 4096
    }
}
```

**[취약점]** Bedrock Fallback 시 모델 매핑 누락됨
- Gemini 모델명이 그대로 Bedrock에 전달될 수 있음

### 4.2 Context Caching

Vertex AI의 Context Caching을 활용하여 비용을 절감합니다.

**[최적화 제안]**
- 32K 토큰 이상의 컨텍스트에서만 캐싱 활성화
- 캐시 TTL 동적 조정 필요 (현재 고정 1시간)

---

## 5. Step Functions Integration

### 5.1 Distributed Mode

대규모 워크플로우는 Distributed Map을 통해 병렬 처리됩니다.

```json
{
  "Type": "Map",
  "ItemsPath": "$.items",
  "MaxConcurrency": 40,
  "ItemProcessor": {
    "ProcessorConfig": {
      "Mode": "DISTRIBUTED"
    }
  }
}
```

**[성능 최적화 제안]**
1. `MaxConcurrency` 동적 조정 (현재 하드코딩)
2. S3 매니페스트 기반 입력으로 10MB 제한 우회
3. 청크 크기 최적화 (현재 고정 1000건)

### 5.2 Error Handling

**[취약점]** Catch 블록에서 원본 에러 컨텍스트 손실
```json
{
  "Catch": [{
    "ErrorEquals": ["States.ALL"],
    "ResultPath": "$.error"  // 원본 상태 덮어쓰기
  }]
}
```

---

## 6. Database Schema

### 6.1 DynamoDB Tables

| Table | PK | SK | GSI |
|-------|----|----|-----|
| Workflows | workflow_id | version | owner_id-index |
| Executions | execution_id | - | status-index |
| Tasks | task_id | segment_index | workflow_id-index |

**[최적화 제안]**
- Hot Partition 방지를 위한 샤딩 키 추가
- DAX 캐싱 적용 검토

---

## 7. Monitoring & Observability

### 7.1 Metrics

현재 수집 중인 메트릭:
- `workflow.execution.duration`
- `llm.token.usage`
- `operator.execution.count`

**[개선 필요]**
- 분산 트레이싱 (X-Ray) 연동 불완전
- 비용 메트릭 실시간 집계 누락

---

## 8. Known Issues (As of 2026-01-23)

| ID | Priority | Description | Status |
|----|----------|-------------|--------|
| SEC-001 | Critical | SQL Injection in search | Open |
| SEC-002 | High | JWT validation weakness | In Progress |
| SEC-003 | High | CORS policy too permissive | Open |
| SEC-004 | Critical | RCE via operator_runner | Mitigated |
| OPT-001 | Medium | DynamoDB hot partition | Open |
| OPT-002 | High | LLM cost not optimized | Open |
| OPT-003 | Medium | Step Functions retry storm | Open |

---

## Appendix A: API Endpoints

```
POST /api/v1/workflows
GET  /api/v1/workflows/{id}
POST /api/v1/workflows/{id}/execute
GET  /api/v1/executions/{id}/status
POST /api/v1/skills
```

## Appendix B: Environment Variables

```bash
MOCK_MODE=false
AWS_REGION=ap-northeast-2
GCP_PROJECT_ID=analemma-prod
GEMINI_API_KEY=sk-xxx  # [취약] 평문 저장
```

---

*This document is confidential and intended for internal use only.*
*Last updated: 2026-01-23*
