# Workflow Configuration Schema Documentation

## 목차
- [개요](#개요)
- [프론트엔드 → 백엔드 변환 흐름](#프론트엔드--백엔드-변환-흐름)
- [프론트엔드 스키마](#프론트엔드-스키마)
- [백엔드 스키마](#백엔드-스키마)
- [노드 타입별 상세 스키마](#노드-타입별-상세-스키마)
- [엣지 타입별 상세 스키마](#엣지-타입별-상세-스키마)
- [실제 작동 기능](#실제-작동-기능)
- [검증 및 보안](#검증-및-보안)

---

## 개요

Analemma Workflow System은 프론트엔드에서 시각적으로 설계된 워크플로우를 백엔드에서 실행 가능한 형태로 변환하여 처리합니다.

### 핵심 아키텍처
```
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│   Frontend      │         │   Converter     │         │    Backend      │
│  (ReactFlow)    │────────▶│  (TypeScript)   │────────▶│    (Python)     │
│                 │         │                 │         │                 │
│ - UI Node Types │         │ - Type Mapping  │         │ - Execution     │
│ - Visual Editor │         │ - Validation    │         │ - Partitioning  │
│ - JSON Export   │         │ - Normalization │         │ - LLM Calls     │
└─────────────────┘         └─────────────────┘         └─────────────────┘
```

### 데이터 흐름
1. **Frontend**: 사용자가 ReactFlow로 워크플로우 작성
2. **Converter**: `workflowConverter.ts`가 백엔드 형식으로 변환
3. **API**: HTTP POST `/workflow/save` 호출
4. **Backend**: Pydantic 모델로 검증 후 DynamoDB 저장
5. **Execution**: Step Functions + Lambda로 실행

---

## 프론트엔드 → 백엔드 변환 흐름

### 변환 과정

```typescript
// 프론트엔드 (ReactFlow 노드)
{
  id: "node_123",
  type: "aiModel",
  data: {
    label: "GPT-4 분석",
    provider: "openai",
    model: "gpt-4",
    prompt_content: "분석해주세요"
  },
  position: { x: 100, y: 200 }
}

↓ workflowConverter.ts

// 백엔드 (Python 노드)
{
  "id": "node_123",
  "type": "llm_chat",
  "label": "GPT-4 분석",
  "config": {
    "provider": "openai",
    "model": "gpt-4",
    "prompt_content": "분석해주세요",
    "temperature": 0.7,
    "max_tokens": 256
  }
}
```

### 노드 타입 매핑 테이블

| Frontend Type | Backend Type | 설명 | 변환 위치 |
|--------------|-------------|------|----------|
| `aiModel` | `llm_chat` | LLM 대화 노드 | [workflowConverter.ts:94](frontend/apps/web/src/lib/workflowConverter.ts#L94) |
| `operator` | `operator` / `api_call` / `db_query` / `safe_operator` | 데이터 처리 노드 (operatorType에 따라 분기) | [workflowConverter.ts:125](frontend/apps/web/src/lib/workflowConverter.ts#L125) |
| `trigger` | `operator` (with `_frontend_type: 'trigger'`) | 트리거 노드 | [workflowConverter.ts:162](frontend/apps/web/src/lib/workflowConverter.ts#L162) |
| `control` (loop) | `loop` | 순차 반복 노드 | [workflowConverter.ts:193](frontend/apps/web/src/lib/workflowConverter.ts#L193) |
| `control` (for_each) | `for_each` | 병렬 반복 노드 | [workflowConverter.ts:180](frontend/apps/web/src/lib/workflowConverter.ts#L180) |
| `control` (parallel) | `parallel_group` | 병렬 실행 노드 | [workflowConverter.ts:206](frontend/apps/web/src/lib/workflowConverter.ts#L206) |
| `control` (aggregator) | `aggregator` | 집계 노드 | [workflowConverter.ts:213](frontend/apps/web/src/lib/workflowConverter.ts#L213) |
| `control` (conditional) | `route_condition` | 조건부 라우팅 | [workflowConverter.ts:219](frontend/apps/web/src/lib/workflowConverter.ts#L219) |
| `control` (human/branch) | `null` (엣지로 변환) | HITL 또는 분기 처리 | [workflowConverter.ts:227](frontend/apps/web/src/lib/workflowConverter.ts#L227) |
| `group` | `subgraph` | 서브그래프 | [workflowConverter.ts:241](frontend/apps/web/src/lib/workflowConverter.ts#L241) |
| `control_block` | `null` (엣지로 변환) | UI 전용 제어 블록 | [workflowConverter.ts:247](frontend/apps/web/src/lib/workflowConverter.ts#L247) |

---

## 프론트엔드 스키마

### WorkflowNode (TypeScript)

**파일**: [frontend/apps/web/src/types/index.ts:55](frontend/apps/web/src/types/index.ts#L55)

```typescript
export interface WorkflowNode {
  id: string;                           // 고유 노드 ID (UUID)
  type: string;                         // 노드 타입 (aiModel, operator, control 등)
  position: { x: number; y: number };   // 캔버스 상 위치
  data: any;                            // 노드별 설정 데이터
}
```

### WorkflowEdge (TypeScript)

**파일**: [frontend/apps/web/src/types/index.ts:63](frontend/apps/web/src/types/index.ts#L63)

```typescript
export interface WorkflowEdge {
  id: string;         // 고유 엣지 ID
  source: string;     // 시작 노드 ID
  target: string;     // 종료 노드 ID
  type?: string;      // 엣지 타입 (edge, hitp, conditional 등)
}
```

### 프론트엔드 노드 Data 구조 예시

#### AI Model 노드
```typescript
{
  id: "node_ai_1",
  type: "aiModel",
  data: {
    label: "문서 분석",
    provider: "openai" | "anthropic" | "google",
    model: "gpt-4" | "claude-3-opus" | "gemini-1.5-pro",
    prompt_content: "다음 문서를 분석하세요: {{state.document}}",
    system_prompt?: "당신은 전문 분석가입니다",
    temperature?: 0.7,
    max_tokens?: 2048,
    enable_thinking?: false,
    thinking_budget_tokens?: 4096,
    writes_state_key?: "analysis_result",
    tools?: [
      {
        name: "search",
        description: "웹 검색",
        parameters: { query: "string" },
        skill_id?: "skill_123"
      }
    ]
  }
}
```

#### Operator 노드
```typescript
{
  id: "node_op_1",
  type: "operator",
  data: {
    label: "데이터 처리",
    operatorType: "api_call" | "db_query" | "safe_operator",
    // API Call 타입
    url?: "https://api.example.com/data",
    method?: "GET" | "POST" | "PUT" | "DELETE",
    headers?: { "Authorization": "Bearer {{state.token}}" },
    params?: { "limit": 100 },
    body?: { "query": "{{state.search_term}}" },
    timeout?: 10,
    // DB Query 타입
    query?: "SELECT * FROM users WHERE id = {{state.user_id}}",
    connection_string?: "postgresql://...",
    // Safe Operator 타입
    strategy?: "list_filter" | "map" | "reduce",
    input_key?: "state.items",
    params?: { "filter_key": "active" },
    output_key?: "filtered_items"
  }
}
```

#### Control 노드 (Loop/For_Each)
```typescript
{
  id: "node_ctrl_1",
  type: "control",
  data: {
    label: "반복 처리",
    controlType: "loop" | "for_each" | "parallel" | "conditional" | "aggregator",
    // Loop 타입
    condition?: "state.count < 10",
    max_iterations?: 5,
    loop_var?: "loop_index",
    convergence_key?: "state.accuracy",
    target_score?: 0.9,
    sub_workflow?: { nodes: [...] },
    // For_Each 타입
    items_path?: "state.items",
    item_key?: "item",
    output_key?: "for_each_results",
    // Parallel 타입
    branches?: [
      { branch_id: "A", nodes: [...] },
      { branch_id: "B", nodes: [...] }
    ],
    // Conditional 타입
    conditions?: [
      { condition: "state.score > 0.8", target_node: "high_quality" },
      { condition: "state.score > 0.5", target_node: "medium_quality" }
    ],
    default_node?: "low_quality"
  }
}
```

---

## 백엔드 스키마

### WorkflowConfigModel (Pydantic)

**파일**: [backend/src/handlers/core/main.py:337](backend/src/handlers/core/main.py#L337)

```python
class WorkflowConfigModel(BaseModel):
    workflow_name: Optional[constr(min_length=0, max_length=256)] = None
    description: Optional[constr(min_length=0, max_length=512)] = None
    nodes: conlist(NodeModel, min_length=0, max_length=500)
    edges: conlist(EdgeModel, min_length=0, max_length=1000)
    start_node: Optional[constr(min_length=1, max_length=128)] = None
```

### NodeModel (Pydantic)

**파일**: [backend/src/handlers/core/main.py:150-330](backend/src/handlers/core/main.py#L150)

```python
class NodeModel(BaseModel):
    id: constr(min_length=1, max_length=128)
    type: Literal[
        'operator', 'llm', 'llm_chat', 'loop', 'for_each', 
        'parallel_group', 'parallel', 'aggregator', 
        'subgraph', 'api_call', 'db_query', 'safe_operator',
        'operator_official', 'route_condition', 'trigger'
    ]
    label: Optional[constr(min_length=0, max_length=256)] = None
    action: Optional[constr(min_length=0, max_length=128)] = None
    hitp: Optional[bool] = False  # Human-in-the-loop
    config: Optional[Dict[str, Any]] = None
    position: Optional[Dict[str, float]] = None
    branches: Optional[List[Dict[str, Any]]] = None
    resource_policy: Optional[Dict[str, Any]] = None
    subgraph_ref: Optional[str] = None
    subgraph_inline: Optional[Dict[str, Any]] = None
```

### EdgeModel (Pydantic)

**파일**: [backend/src/handlers/core/main.py:298-328](backend/src/handlers/core/main.py#L298)

```python
class EdgeModel(BaseModel):
    type: Literal[
        'edge', 'normal', 'flow', 'if', 
        'hitp', 'human_in_the_loop', 'pause', 
        'conditional_edge', 'start', 'end'
    ]
    source: constr(min_length=1, max_length=128)
    target: constr(min_length=1, max_length=128)
    condition: Optional[Union[str, Dict[str, str]]] = None
    router_func: Optional[str] = None
    mapping: Optional[Dict[str, str]] = None
```

### BackendWorkflow (Python Dict)

**실제 DynamoDB 저장 형식**:

```python
{
    "name": "워크플로우 이름",
    "nodes": [
        {
            "id": "node_1",
            "type": "llm_chat",
            "label": "GPT-4 분석",
            "config": {
                "provider": "openai",
                "model": "gpt-4",
                "prompt_content": "분석해주세요",
                "temperature": 0.7,
                "max_tokens": 256,
                "enable_thinking": False
            }
        }
    ],
    "edges": [
        {
            "type": "edge",
            "source": "node_1",
            "target": "node_2"
        }
    ],
    "secrets": [
        {
            "provider": "secretsmanager",
            "name": "openai-api-key",
            "target": "OPENAI_API_KEY"
        }
    ],
    "subgraphs": {
        "subgraph_1": {
            "id": "subgraph_1",
            "nodes": [...],
            "edges": [...],
            "metadata": {
                "name": "서브그래프 이름",
                "description": "설명"
            }
        }
    }
}
```

---

## 노드 타입별 상세 스키마

### 1. LLM Chat 노드 (`llm_chat`)

**용도**: LLM 모델 호출 (GPT-4, Claude, Gemini 등)

**백엔드 Config 스키마**:
```python
{
    "provider": str,                  # "openai" | "anthropic" | "google" | "bedrock"
    "model": str,                     # "gpt-4" | "claude-3-opus" | "gemini-1.5-pro"
    "prompt_content": str,            # 프롬프트 템플릿 (Jinja2 지원)
    "system_prompt": Optional[str],   # 시스템 프롬프트
    "temperature": float = 0.7,       # 0.0 ~ 2.0
    "max_tokens": int = 256,          # 응답 최대 토큰
    "writes_state_key": Optional[str], # 결과 저장할 state 키
    "enable_thinking": bool = False,   # Extended Thinking 활성화
    "thinking_budget_tokens": int = 4096, # Thinking 토큰 예산
    "tool_definitions": Optional[List[Dict]] # Function Calling 도구
}
```

**실행 엔진**: [backend/src/services/execution/llm_chat_runner.py](backend/src/services/execution/llm_chat_runner.py)

**지원 기능**:
- ✅ Multi-Provider (OpenAI, Anthropic, Google, AWS Bedrock)
- ✅ Function Calling (Tool Use)
- ✅ Extended Thinking Mode (GPT-4o, Claude Sonnet)
- ✅ Streaming 응답
- ✅ Vision 입력 (이미지 분석)
- ✅ Prompt Template (Jinja2)

**실행 로직**:
1. `llm_chat_runner.execute_llm_chat_node()` 호출
2. Provider별 Client 생성 (`openai`, `anthropic`, `google`)
3. Prompt Template 렌더링 (Jinja2 + state context)
4. LLM API 호출
5. 응답을 `state[writes_state_key]`에 저장
6. Token 사용량 추적 (`state.usage`)

---

### 2. Operator 노드 (`operator`)

**용도**: 상태 변환, 데이터 처리, 기본 로직 실행

**백엔드 Config 스키마**:
```python
{
    "sets": Dict[str, Any],           # state에 설정할 키-값 쌍
    "_frontend_type": Optional[str]   # 프론트엔드 타입 추적용
}
```

**실행 엔진**: [backend/src/services/execution/operator_runner.py](backend/src/services/execution/operator_runner.py)

**지원 기능**:
- ✅ State 키-값 설정
- ✅ Jinja2 표현식 평가
- ✅ Python 표현식 실행 (샌드박스)

**실행 로직**:
```python
for key, value in config["sets"].items():
    if isinstance(value, str) and "{{" in value:
        # Jinja2 템플릿 렌더링
        rendered_value = jinja_env.from_string(value).render(state=state)
    else:
        rendered_value = value
    state[key] = rendered_value
```

---

### 3. API Call 노드 (`api_call`)

**용도**: 외부 REST API 호출

**백엔드 Config 스키마**:
```python
{
    "url": str,                       # API 엔드포인트
    "method": str = "GET",            # HTTP 메서드
    "headers": Dict[str, str] = {},   # HTTP 헤더
    "params": Dict[str, Any] = {},    # Query Parameters
    "json": Optional[Dict] = None,    # Request Body (JSON)
    "timeout": int = 10               # 타임아웃 (초)
}
```

**실행 엔진**: [backend/src/services/execution/api_call_runner.py](backend/src/services/execution/api_call_runner.py)

**지원 기능**:
- ✅ GET, POST, PUT, DELETE, PATCH
- ✅ Jinja2 템플릿 (URL, Headers, Params, Body)
- ✅ 자동 재시도 (3회)
- ✅ 응답 캐싱 (선택)

**실행 로직**:
1. URL/Headers/Params 템플릿 렌더링
2. `requests` 라이브러리로 HTTP 요청
3. 응답을 `state.api_response`에 저장
4. 에러 시 재시도 (Exponential Backoff)

---

### 4. Database Query 노드 (`db_query`)

**용도**: SQL 데이터베이스 쿼리 실행

**백엔드 Config 스키마**:
```python
{
    "query": str,                     # SQL 쿼리 (Jinja2 템플릿)
    "connection_string": str          # DB 연결 문자열
}
```

**실행 엔진**: [backend/src/services/execution/db_query_runner.py](backend/src/services/execution/db_query_runner.py)

**지원 기능**:
- ✅ PostgreSQL, MySQL, SQLite
- ✅ Parameterized Queries (SQL Injection 방지)
- ✅ Connection Pooling
- ✅ Transaction 지원

**실행 로직**:
1. Connection Pool에서 연결 획득
2. Jinja2로 쿼리 렌더링
3. `psycopg2` / `pymysql`로 실행
4. 결과를 `state.db_result`에 저장
5. 연결 반환

---

### 5. Safe Operator 노드 (`safe_operator`)

**용도**: 리스트/딕셔너리 데이터 안전 처리

**백엔드 Config 스키마**:
```python
{
    "strategy": str,                  # "list_filter" | "map" | "reduce"
    "input_key": str,                 # 입력 state 키
    "params": Dict[str, Any],         # 전략별 파라미터
    "output_key": str                 # 결과 저장 키
}
```

**실행 엔진**: [backend/src/services/execution/safe_operator_runner.py](backend/src/services/execution/safe_operator_runner.py)

**지원 전략**:
- `list_filter`: 조건에 맞는 항목 필터링
- `map`: 각 항목에 함수 적용
- `reduce`: 항목들을 단일 값으로 축약
- `sort`: 정렬
- `unique`: 중복 제거

**실행 로직**:
```python
input_data = state[config["input_key"]]

if strategy == "list_filter":
    result = [item for item in input_data if eval(params["condition"])]
elif strategy == "map":
    result = [eval(params["expression"]) for item in input_data]
# ...

state[config["output_key"]] = result
```

---

### 6. Loop 노드 (`loop`)

**용도**: 조건 기반 순차 반복 실행

**백엔드 Config 스키마**:
```python
{
    "nodes": List[Dict],              # 루프 내부 노드
    "condition": str,                 # 반복 조건 (Python 표현식)
    "max_iterations": int = 5,        # 최대 반복 횟수
    "loop_var": str = "loop_index",   # 루프 변수명
    "convergence_key": Optional[str], # 수렴 감지 키
    "target_score": float = 0.9       # 목표 점수
}
```

**실행 엔진**: [backend/src/services/execution/loop_runner.py](backend/src/services/execution/loop_runner.py)

**지원 기능**:
- ✅ While 조건 평가
- ✅ 최대 반복 제한 (무한 루프 방지)
- ✅ Convergence Detection (점수 기반 조기 종료)
- ✅ 루프 인덱스 추적

**실행 로직**:
```python
iteration = 0
while eval(condition, {"state": state}) and iteration < max_iterations:
    # 서브 워크플로우 실행
    for node in config["nodes"]:
        execute_node(node, state)
    
    # 수렴 감지
    if convergence_key and state[convergence_key] >= target_score:
        break
    
    iteration += 1
    state[loop_var] = iteration
```

---

### 7. For_Each 노드 (`for_each`)

**용도**: 리스트 항목에 대한 병렬 반복 실행

**백엔드 Config 스키마**:
```python
{
    "items_path": str,                # 리스트 경로 (e.g., "state.items")
    "item_key": str = "item",         # 각 항목 변수명
    "output_key": str = "for_each_results", # 결과 저장 키
    "max_iterations": int = 20,       # 최대 항목 수
    "sub_workflow": Dict              # 각 항목에 실행할 워크플로우
}
```

**실행 엔진**: [backend/src/services/execution/for_each_runner.py](backend/src/services/execution/for_each_runner.py)

**지원 기능**:
- ✅ ThreadPoolExecutor 병렬 실행 (최대 10 스레드)
- ✅ 항목별 독립 State Context
- ✅ 결과 자동 집계
- ✅ 부분 실패 허용 (일부 항목 실패해도 계속)

**실행 로직**:
```python
items = get_nested_value(state, items_path)

with ThreadPoolExecutor(max_workers=10) as executor:
    futures = []
    for item in items[:max_iterations]:
        # 각 항목에 대해 독립 실행
        item_state = state.copy()
        item_state[item_key] = item
        future = executor.submit(execute_sub_workflow, sub_workflow, item_state)
        futures.append(future)
    
    results = [f.result() for f in futures]

state[output_key] = results
```

---

### 8. Parallel Group 노드 (`parallel_group`)

**용도**: 여러 브랜치를 병렬로 실행

**백엔드 Config 스키마**:
```python
{
    "branches": List[Dict],           # 브랜치 목록
    # 각 브랜치:
    # {
    #   "branch_id": str,
    #   "nodes": List[Dict] 또는 "sub_workflow": Dict
    # }
    "resource_policy": Optional[Dict] # 리소스 할당 정책
}
```

**실행 엔진**: [backend/src/services/execution/parallel_runner.py](backend/src/services/execution/parallel_runner.py)

**지원 기능**:
- ✅ Step Functions Map State 활용
- ✅ 브랜치별 독립 실행
- ✅ Bin Packing 자동 그룹화 (리소스 최적화)
- ✅ 브랜치별 타임아웃 설정

**실행 로직**:
```python
# Step Functions Map State로 변환
{
    "Type": "Map",
    "ItemsPath": "$.branches",
    "Iterator": {
        "StartAt": "ExecuteBranch",
        "States": {
            "ExecuteBranch": {
                "Type": "Task",
                "Resource": "arn:aws:lambda:...:SegmentRunnerFunction",
                "End": True
            }
        }
    },
    "ResultPath": "$.parallel_results"
}
```

---

### 9. Aggregator 노드 (`aggregator`)

**용도**: 병렬/반복 실행 결과 집계

**백엔드 Config 스키마**:
```python
{}  # 자동 집계 (설정 불필요)
```

**실행 엔진**: [backend/src/services/execution/aggregator_runner.py](backend/src/services/execution/aggregator_runner.py)

**지원 기능**:
- ✅ 자동 토큰 사용량 합산
- ✅ 결과 리스트 병합
- ✅ 에러 집계

**실행 로직**:
```python
total_tokens = sum(branch.get("usage", {}).get("total_tokens", 0) for branch in branches)
merged_results = [branch.get("result") for branch in branches]

state["aggregated_usage"] = {"total_tokens": total_tokens}
state["aggregated_results"] = merged_results
```

---

### 10. Route Condition 노드 (`route_condition`)

**용도**: 조건부 분기 (동적 라우팅)

**백엔드 Config 스키마**:
```python
{
    "conditions": List[Dict],         # 조건 목록
    # 각 조건:
    # {
    #   "condition": str,              # Python 표현식
    #   "target_node": str             # 목표 노드 ID
    # }
    "default_node": str,              # 기본 목표 노드
    "evaluation_mode": str = "first_match" # "first_match" | "all_match"
}
```

**실행 엔진**: [backend/src/services/execution/route_condition_runner.py](backend/src/services/execution/route_condition_runner.py)

**지원 기능**:
- ✅ 다중 조건 평가
- ✅ First Match / All Match 모드
- ✅ 기본 라우트 설정

**실행 로직**:
```python
for cond in conditions:
    if eval(cond["condition"], {"state": state}):
        state["__next_node"] = cond["target_node"]
        break
else:
    state["__next_node"] = default_node
```

---

## 엣지 타입별 상세 스키마

### 1. Normal Edge (`edge`, `normal`, `flow`)

**용도**: 기본 순차 실행 흐름

**백엔드 스키마**:
```python
{
    "type": "edge",
    "source": "node_1",
    "target": "node_2"
}
```

**실행**: 노드 완료 후 자동으로 다음 노드 실행

---

### 2. Conditional Edge (`if`, `conditional_edge`)

**용도**: 조건부 분기

**백엔드 스키마**:
```python
{
    "type": "if",
    "source": "node_1",
    "target": "node_2",
    "condition": "state.score > 0.8"  # Python 표현식
}
```

**실행**: 조건 평가 후 True일 때만 target 실행

---

### 3. Human-in-the-Loop Edge (`hitp`, `human_in_the_loop`, `pause`)

**용도**: 사람의 승인/입력 대기

**백엔드 스키마**:
```python
{
    "type": "hitp",
    "source": "node_1",
    "target": "node_2"
}
```

**실행**: 
1. Step Functions Task Token 생성
2. DynamoDB에 Task Token 저장
3. 사용자 입력 대기
4. 입력 받으면 `SendTaskSuccess` 호출하여 재개

---

### 4. Conditional Router Edge (`conditional_edge` with `router_func`)

**용도**: 함수 기반 동적 라우팅

**백엔드 스키마**:
```python
{
    "type": "conditional_edge",
    "source": "node_1",
    "router_func": "sentiment_router",  # 라우터 함수명
    "mapping": {
        "positive": "happy_path",
        "negative": "sad_path",
        "neutral": "neutral_path"
    }
}
```

**실행**:
1. 라우터 함수 실행: `result = sentiment_router(state)`
2. Mapping에서 다음 노드 조회: `next_node = mapping[result]`
3. 해당 노드로 이동

---

## 실제 작동 기능

### 1. 워크플로우 생성 & 저장

```
Frontend                Converter               Backend
   │                       │                       │
   │  Save Workflow        │                       │
   │─────────────────────▶ │                       │
   │                       │  Convert Nodes/Edges  │
   │                       │───────────────────────▶│
   │                       │                       │
   │                       │                       │ Validate (Pydantic)
   │                       │                       │ Store in DynamoDB
   │                       │                       │
   │                       │◀───────────────────────│
   │◀─────────────────────│                       │
   │  Success Response     │                       │
```

**코드 경로**:
- Frontend: [frontend/apps/web/src/lib/workflowConverter.ts](frontend/apps/web/src/lib/workflowConverter.ts)
- Backend: [backend/src/handlers/core/main.py:save_workflow_handler](backend/src/handlers/core/main.py)

---

### 2. 워크플로우 실행

```
API Gateway          Lambda              Step Functions        Lambda (SegmentRunner)
   │                   │                       │                       │
   │  POST /execute    │                       │                       │
   │──────────────────▶│                       │                       │
   │                   │  StartExecution       │                       │
   │                   │──────────────────────▶│                       │
   │                   │                       │  InvokeSegment        │
   │                   │                       │──────────────────────▶│
   │                   │                       │                       │ Execute Nodes
   │                   │                       │                       │ Call LLM APIs
   │                   │                       │◀──────────────────────│
   │                   │                       │  Return Results       │
   │                   │◀──────────────────────│                       │
   │◀──────────────────│                       │                       │
   │  Execution ID     │                       │                       │
```

**핵심 단계**:

1. **InitializeStateData** ([backend/src/common/initialize_state_data.py](backend/src/common/initialize_state_data.py))
   - Workflow Config 로드
   - Partition (세그먼트 분할)
   - Merkle Manifest 생성
   - StateBag 초기화

2. **SegmentRunner** ([backend/src/services/execution/segment_runner_service.py](backend/src/services/execution/segment_runner_service.py))
   - Segment Config 로드
   - 노드별 Runner 호출
   - State 업데이트
   - 다음 Segment 결정

3. **노드 실행 (Runner별)**:
   - LLM Chat: [llm_chat_runner.py](backend/src/services/execution/llm_chat_runner.py)
   - Operator: [operator_runner.py](backend/src/services/execution/operator_runner.py)
   - Loop: [loop_runner.py](backend/src/services/execution/loop_runner.py)
   - For_Each: [for_each_runner.py](backend/src/services/execution/for_each_runner.py)

---

### 3. 동적 재파티셔닝 (Dynamic Re-partitioning)

**시나리오**: Agent가 실행 중 새로운 노드 추가 요청

```
SegmentRunner        ManifestRegenerator           StateVersioning
   │                        │                             │
   │  Recovery Segments     │                             │
   │  Detected (3+ new)     │                             │
   │                        │                             │
   │  Invoke Regenerator    │                             │
   │───────────────────────▶│                             │
   │                        │  Load Old Manifest          │
   │                        │─────────────────────────────▶│
   │                        │                             │
   │                        │  Load Workflow Config       │
   │                        │  Merge Modifications        │
   │                        │  Re-partition               │
   │                        │                             │
   │                        │  Create New Manifest        │
   │                        │─────────────────────────────▶│
   │                        │                             │ Atomic Transaction:
   │                        │                             │ - Save Manifest
   │                        │                             │ - Increment Block Refs
   │                        │                             │
   │                        │  Invalidate Old Manifest    │
   │                        │─────────────────────────────▶│
   │                        │                             │ - Mark INVALIDATED
   │                        │                             │ - Decrement Block Refs
   │                        │                             │
   │◀───────────────────────│                             │
   │  New Manifest Pointer  │                             │
```

**코드 경로**:
- Trigger: [backend/src/services/execution/segment_runner_service.py:_inject_recovery_segments](backend/src/services/execution/segment_runner_service.py)
- Regenerator: [backend/src/handlers/core/manifest_regenerator.py](backend/src/handlers/core/manifest_regenerator.py)
- Versioning: [backend/src/services/state/state_versioning_service.py](backend/src/services/state/state_versioning_service.py)

---

## 검증 및 보안

### Pydantic 모델 검증

**파일**: [backend/src/handlers/core/main.py:337](backend/src/handlers/core/main.py#L337)

```python
class WorkflowConfigModel(BaseModel):
    nodes: conlist(NodeModel, min_length=0, max_length=500)  # 최대 500 노드
    edges: conlist(EdgeModel, min_length=0, max_length=1000) # 최대 1000 엣지
    
    # 자동 검증:
    # - 타입 체크
    # - 길이 제한
    # - 필수 필드 확인
```

**검증 단계**:
1. Frontend: TypeScript 타입 체크
2. Converter: 변환 중 필수 필드 확인
3. Backend: Pydantic 모델 자동 검증
4. Security: Reserved Key 차단

---

### 예약 키 보호 (State Pollution Safeguard)

**파일**: [backend/src/handlers/core/main.py:352](backend/src/handlers/core/main.py#L352)

```python
RESERVED_STATE_KEYS = {
    # 시스템 컨텍스트
    "workflowId", "owner_id", "execution_id",
    
    # 흐름 제어
    "loop_counter", "max_loop_iterations", "segment_id",
    
    # 상태 인프라
    "current_state", "final_state", "state_s3_path",
    
    # 텔레메트리
    "step_history", "execution_logs",
    
    # 스케줄링
    "scheduling_metadata", "__scheduling_metadata"
}

def _validate_output_keys(output: Dict, node_id: str) -> Dict:
    """노드 출력에서 예약 키 제거"""
    forbidden_attempts = [k for k in output if k in RESERVED_STATE_KEYS]
    
    if forbidden_attempts:
        logger.warning(f"Node {node_id} tried to overwrite: {forbidden_attempts}")
        return {k: v for k, v in output.items() if k not in RESERVED_STATE_KEYS}
    
    return output
```

**보호 대상**:
- ❌ `loop_counter` 조작 → 무한 루프 방지
- ❌ `segment_id` 변경 → 실행 흐름 보호
- ❌ `__s3_offloaded` 삭제 → 데이터 손실 방지
- ❌ `execution_logs` 수정 → 추적성 보호

---

### Ring-Based Security (Agent Governance)

**파일**: [backend/src/handlers/core/main.py:398](backend/src/handlers/core/main.py#L398)

```python
def _validate_output_keys(output: Dict, node_id: str, ring_level: int = 3):
    """
    Ring 3 (User/Agent): _kernel_* 명령 출력 불가
    Ring 0/1 (Kernel/Governor): _kernel_* 명령 출력 가능
    """
    if ring_level >= 3:  # Ring 3 (Agent)
        kernel_forgery_attempts = [k for k in output if k.startswith("_kernel_")]
        
        if kernel_forgery_attempts:
            logger.error(f"[KERNEL_COMMAND_FORGERY] Node {node_id} attempted: {kernel_forgery_attempts}")
            # Governor에 보고 + Audit Log 기록
            for key in kernel_forgery_attempts:
                output.pop(key, None)
```

**보안 레벨**:
- **Ring 0 (Kernel)**: 모든 권한
- **Ring 1 (Governor)**: 감사 + 제한적 kernel 명령
- **Ring 2 (Trusted)**: 일반 실행
- **Ring 3 (Agent)**: LLM 출력, kernel 명령 금지

---

## 부록: 주요 파일 참조

### 프론트엔드
- [types/index.ts](frontend/apps/web/src/types/index.ts) - TypeScript 타입 정의
- [workflowConverter.ts](frontend/apps/web/src/lib/workflowConverter.ts) - 백엔드 변환 로직
- [nodeFactory.ts](frontend/apps/web/src/lib/nodeFactory.ts) - 노드 생성 팩토리
- [graphAnalysis.ts](frontend/apps/web/src/lib/graphAnalysis.ts) - 그래프 분석 (순환 감지)

### 백엔드
- [main.py](backend/src/handlers/core/main.py) - Pydantic 모델 정의
- [initialize_state_data.py](backend/src/common/initialize_state_data.py) - StateBag 초기화
- [segment_runner_service.py](backend/src/services/execution/segment_runner_service.py) - 세그먼트 실행 엔진
- [state_versioning_service.py](backend/src/services/state/state_versioning_service.py) - Merkle DAG 버전 관리
- [partition_service.py](backend/src/services/partitioning/partition_service.py) - 워크플로우 분할 알고리즘

### 노드 Runner
- [llm_chat_runner.py](backend/src/services/execution/llm_chat_runner.py)
- [operator_runner.py](backend/src/services/execution/operator_runner.py)
- [loop_runner.py](backend/src/services/execution/loop_runner.py)
- [for_each_runner.py](backend/src/services/execution/for_each_runner.py)
- [parallel_runner.py](backend/src/services/execution/parallel_runner.py)
- [aggregator_runner.py](backend/src/services/execution/aggregator_runner.py)
- [route_condition_runner.py](backend/src/services/execution/route_condition_runner.py)
- [api_call_runner.py](backend/src/services/execution/api_call_runner.py)
- [db_query_runner.py](backend/src/services/execution/db_query_runner.py)
- [safe_operator_runner.py](backend/src/services/execution/safe_operator_runner.py)

---

## 변경 이력
- 2026-02-18: 초안 작성 (Dynamic Re-partitioning, Merkle DAG 포함)
