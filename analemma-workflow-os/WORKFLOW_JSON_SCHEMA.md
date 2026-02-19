# Analemma-OS 워크플로우 JSON 스키마 문서

**작성일**: 2026-02-19  
**버전**: v3.27  
**목적**: 워크플로우 정의 JSON 구조 및 지원 기능 명세

---

## 📋 목차

1. [워크플로우 루트 스키마](#1-워크플로우-루트-스키마)
2. [노드 타입 및 설정](#2-노드-타입-및-설정)
3. [엣지 타입 및 설정](#3-엣지-타입-및-설정)
4. [고급 기능](#4-고급-기능)
5. [보안 및 제약사항](#5-보안-및-제약사항)
6. [예제 워크플로우](#6-예제-워크플로우)

---

## 1. 워크플로우 루트 스키마

### 1.1 기본 구조

```json
{
  "workflow_name": "string (optional, max 256)",
  "description": "string (optional, max 512)",
  "version": "string (optional)",
  "nodes": [NodeModel],
  "edges": [EdgeModel],
  "start_node": "string (optional, max 128)",
  "initial_state": {
    "key": "value"
  }
}
```

### 1.2 필드 설명

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `workflow_name` | string | ❌ | 워크플로우 이름 (최대 256자) |
| `description` | string | ❌ | 워크플로우 설명 (최대 512자) |
| `version` | string | ❌ | 워크플로우 버전 (의미론적 버전 권장) |
| `nodes` | array | ✅ | 노드 목록 (최소 0개, 최대 500개) |
| `edges` | array | ✅ | 엣지 목록 (최소 0개, 최대 1000개) |
| `start_node` | string | ❌ | 시작 노드 ID (지정하지 않으면 위상 정렬로 자동 결정) |
| `initial_state` | object | ❌ | 초기 상태 데이터 |

---

## 2. 노드 타입 및 설정

### 2.1 NodeModel 스키마

```json
{
  "id": "string (required, 1-128 chars)",
  "type": "string (required, 1-64 chars)",
  "label": "string (optional, max 256)",
  "action": "string (optional, max 256)",
  "hitp": "boolean (optional)",
  "config": {
    "key": "value"
  },
  "branches": [
    {
      "branch_id": "string",
      "sub_workflow": {
        "nodes": []
      }
    }
  ],
  "resource_policy": {},
  "subgraph_ref": "string (optional)",
  "subgraph_inline": {}
}
```

### 2.2 지원 노드 타입

#### 2.2.1 Core Execution Types

| 타입 | 설명 | Config 필수 필드 |
|------|------|------------------|
| `operator` | Python 코드 실행 (MOCK_MODE에서만) | `code` 또는 `sets` |
| `operator_custom` | 사용자 정의 연산자 | `strategy`, `params` |
| `operator_official` | 공식 연산자 | `strategy`, `params` |
| `llm_chat` | LLM 호출 (Gemini/Bedrock) | `model`, `prompt_content` |

**operator 예시**:
```json
{
  "id": "calculate",
  "type": "operator",
  "config": {
    "code": "state['result'] = state['a'] + state['b']"
  }
}
```

**llm_chat 예시**:
```json
{
  "id": "llm_call",
  "type": "llm_chat",
  "config": {
    "provider": "gemini",
    "model": "gemini-2.0-flash-exp",
    "system_prompt": "You are a helpful assistant.",
    "prompt_content": "{{user_input}}",
    "max_tokens": 1024,
    "temperature": 0.7,
    "output_key": "llm_response"
  }
}
```

#### 2.2.2 Flow Control Types

| 타입 | 설명 | Config 필수 필드 |
|------|------|------------------|
| `route_condition` | **조건부 라우팅** (v3.27 신규) | `branches`, `default_target` |
| `dynamic_router` | LLM 기반 동적 라우팅 | - |
| `parallel_group` | 병렬 실행 그룹 | `branches` |
| `aggregator` | 병렬/반복 결과 집계 | - |
| `for_each` | 리스트 항목별 반복 | `items_path`, `sub_workflow` |
| `nested_for_each` | 중첩 반복 | `outer_items`, `inner_items` |
| `loop` | While 루프 | `condition`, `max_iterations` |

**route_condition 예시** (v3.27 - 라우팅 주권 일원화):
```json
{
  "id": "quality_check",
  "type": "route_condition",
  "config": {
    "branches": [
      {
        "condition": "score > 0.9",
        "target": "high_quality_path",
        "label": "High Quality"
      },
      {
        "condition": "score <= 0.9",
        "target": "low_quality_path",
        "label": "Low Quality"
      }
    ],
    "default_target": "fallback_node"
  }
}
```

**parallel_group 예시**:
```json
{
  "id": "parallel_tasks",
  "type": "parallel_group",
  "config": {
    "branches": [
      {
        "branch_id": "branch_0",
        "sub_workflow": {
          "nodes": [
            {
              "id": "task_a",
              "type": "operator",
              "config": {
                "sets": {"result_a": "Task A complete"}
              }
            }
          ]
        }
      },
      {
        "branch_id": "branch_1",
        "sub_workflow": {
          "nodes": [
            {
              "id": "task_b",
              "type": "operator",
              "config": {
                "sets": {"result_b": "Task B complete"}
              }
            }
          ]
        }
      }
    ]
  }
}
```

#### 2.2.3 Subgraph & Reusability

| 타입 | 설명 | Config 필수 필드 |
|------|------|------------------|
| `subgraph` | 재귀적 워크플로우 실행 | `subgraph_ref` 또는 `subgraph_inline` |

**subgraph 예시**:
```json
{
  "id": "data_processing",
  "type": "subgraph",
  "subgraph_ref": "preprocessing_workflow_v2"
}
```

#### 2.2.4 Infrastructure & Data

| 타입 | 설명 | Config 필수 필드 |
|------|------|------------------|
| `api_call` | HTTP API 호출 | `url`, `method` |
| `db_query` | 데이터베이스 쿼리 | `connection`, `query` |

#### 2.2.5 Multimodal & Skills

| 타입 | 설명 | Config 필수 필드 |
|------|------|------------------|
| `vision` | 이미지 분석 (Vision API) | `image_inputs`, `prompt_content` |
| `video_chunker` | 비디오 청킹 | `video_uri`, `chunk_duration` |
| `skill_executor` | 스킬 실행 | `skill_name`, `params` |

#### 2.2.6 UI Marker Types (실행되지 않음)

| 타입 | 설명 |
|------|------|
| `input` | 입력 마커 |
| `output` | 출력 마커 |
| `start` | 시작 마커 |
| `end` | 종료 마커 |
| `trigger` | 트리거 마커 (API request → start로 매핑) |

#### 2.2.7 노드 타입 Alias (별칭)

다음 타입들은 자동으로 정규 타입으로 변환됩니다:

| Alias | 정규 타입 |
|-------|-----------|
| `code` | `operator` |
| `aimodel`, `llm`, `chat`, `genai`, `gpt`, `claude`, `gemini` | `llm_chat` |

---

## 3. 엣지 타입 및 설정

### 3.1 EdgeModel 스키마 (v3.27 리팩토링)

```json
{
  "source": "string (required, 1-128 chars)",
  "target": "string (required, 1-128 chars)",
  "type": "string (default: 'edge', 1-64 chars)"
}
```

**❌ 제거된 필드 (라우팅 주권 일원화)**:
- `router_func`: 라우터 함수명 → `route_condition` 노드 사용
- `mapping`: 라우터 반환값 매핑 → `route_condition` 노드 사용
- `condition`: 조건 표현식 → `route_condition` 노드 사용

### 3.2 지원 엣지 타입

| 타입 | 설명 | 사용 사례 |
|------|------|-----------|
| `edge` | 기본 엣지 (순차 흐름) | 대부분의 노드 연결 |
| `normal` | edge의 별칭 | - |
| `flow` | edge의 별칭 | - |
| `hitp` | **Human-in-the-Loop** (세그먼트 경계) | 인간 승인 대기 |
| `human_in_the_loop` | hitp의 별칭 | - |
| `pause` | 일시정지 (hitp와 유사) | 워크플로우 중단 |
| `start` | 시작점 지정 | Entry point 명시 |
| `end` | 종료점 지정 | Exit point 명시 |

**엣지 예시**:
```json
{
  "edges": [
    {
      "source": "node_a",
      "target": "node_b",
      "type": "edge"
    },
    {
      "source": "approval_check",
      "target": "execute_action",
      "type": "hitp"
    }
  ]
}
```

### 3.3 HITP Edge 특성

**HITP는 조건부 분기가 아닙니다**:
- ❌ 라우팅 결정 (A 또는 B로 이동)
- ✅ 워크플로우 일시정지 → 인간 승인 대기 → 재개
- ✅ 세그먼트 경계 마커 (Lambda 종료점)
- ✅ 상태 저장 후 DynamoDB에 PAUSED_FOR_HITP 기록

**Edge와 Node 양쪽 지원**:
- **Edge `type="hitp"`**: 세그먼트 간 경계 표현
- **Node `hitp=True`**: 세그먼트 내부 노드가 HITP 역할

---

## 4. 고급 기능

### 4.1 템플릿 변수 (Jinja2)

모든 문자열 필드에서 템플릿 변수 사용 가능:

```json
{
  "prompt_content": "User input: {{user_message}}\nContext: {{context}}"
}
```

**지원 함수**:
- `{{variable}}`: State 변수 참조
- `{{variable | default('fallback')}}`: 기본값 설정
- `{{variable | upper}}`: 대문자 변환

### 4.2 S3 Offloading (자동)

대용량 데이터는 자동으로 S3에 저장되고 포인터로 변환됩니다:

**기준**:
- 단일 필드 > 200KB → S3 offload
- 전체 State > 256KB → S3 offload

**포인터 형식**:
```json
{
  "large_data": {
    "__s3_offloaded": true,
    "__s3_path": "s3://bucket/path/to/data.json",
    "__summary": "Large dataset (5.2 MB)"
  }
}
```

### 4.3 State Hydration (자동)

S3 포인터는 실행 시 자동으로 다운로드됩니다:

**수동 제어**:
```json
{
  "config": {
    "input_variables": ["large_data"],
    "lazy_load": true
  }
}
```

### 4.4 Retry & Error Handling

```json
{
  "config": {
    "retry_config": {
      "max_retries": 3,
      "base_delay": 2.0,
      "exponential_backoff": true
    }
  }
}
```

### 4.5 Multimodal Inputs

```json
{
  "type": "llm_chat",
  "config": {
    "vision_enabled": true,
    "image_inputs": ["s3://bucket/image.jpg", "{{uploaded_image}}"],
    "video_inputs": ["s3://bucket/video.mp4"],
    "prompt_content": "Describe what you see in the images and video."
  }
}
```

---

## 5. 보안 및 제약사항

### 5.1 예약 State 키 (오염 방지)

다음 키들은 사용자 코드에서 **수정 불가**:

**System Context**:
- `workflowId`, `workflow_id`, `owner_id`, `execution_id`, `user_id`, `idempotency_key`

**Flow Control**:
- `loop_counter`, `max_loop_iterations`, `segment_id`, `segment_to_run`, `total_segments`, `segment_type`

**State & Infrastructure**:
- `current_state`, `final_state`, `state_s3_path`, `final_state_s3_path`, `partition_map`, `__s3_offloaded`, `__s3_path`

**Telemetry & Logs**:
- `step_history`, `execution_logs`, `__new_history_logs`, `skill_execution_log`, `__kernel_actions`

**Credentials**:
- `user_api_keys`, `aws_credentials`

### 5.2 노드 실행 제약

| 노드 타입 | 제약사항 |
|-----------|----------|
| `operator` | **MOCK_MODE에서만 실행** (RCE 방지) |
| `llm_chat` | 최대 토큰: 8192 (Gemini 2.0), 4096 (Bedrock) |
| `parallel_group` | 최대 브랜치: 10개 |
| `for_each` | 최대 반복: 100회 (설정 가능) |
| `loop` | 최대 반복: 10회 (무한 루프 방지) |

### 5.3 Operator 코드 실행 (샌드박싱)

**허용된 Built-in**:
- 기본 타입: `str`, `int`, `float`, `bool`, `list`, `dict`, `tuple`, `set`
- 함수: `len`, `sum`, `min`, `max`, `abs`, `round`, `range`, `enumerate`, `zip`, `sorted`
- 예외: `Exception`, `ValueError`, `TypeError`, `KeyError`

**허용된 모듈**:
- `time`, `json`, `re`, `uuid`, `math`, `random`, `datetime`, `collections`, `sys`

**차단된 기능**:
- ❌ `open()`, `eval()`, `exec()`, `compile()`
- ❌ `__import__()` (SAFE_MODULES만 허용)
- ❌ 파일 시스템 접근
- ❌ 네트워크 접근 (os.system, subprocess 등)

### 5.4 route_condition 보안 (Safe Eval)

**허용된 Context**:
- State 키 (non-private)
- 안전한 함수: `len`, `str`, `int`, `float`, `bool`
- 비교 연산자: `==`, `!=`, `>`, `<`, `>=`, `<=`
- 논리 연산자: `and`, `or`, `not`

**차단**:
- ❌ `__import__`, `exec`, `eval`
- ❌ 속성 접근 (`__builtins__`)
- ❌ 파일/네트워크 접근

---

## 6. 예제 워크플로우

### 6.1 기본 LLM 호출

```json
{
  "workflow_name": "simple_llm_call",
  "nodes": [
    {
      "id": "llm_node",
      "type": "llm_chat",
      "config": {
        "provider": "gemini",
        "model": "gemini-2.0-flash-exp",
        "system_prompt": "You are a helpful assistant.",
        "prompt_content": "{{user_input}}",
        "output_key": "response"
      }
    }
  ],
  "edges": [],
  "initial_state": {
    "user_input": "Tell me a joke about AI."
  }
}
```

### 6.2 조건부 분기 (route_condition)

```json
{
  "workflow_name": "quality_control",
  "nodes": [
    {
      "id": "analyze",
      "type": "llm_chat",
      "config": {
        "model": "gemini-2.0-flash-exp",
        "prompt_content": "Rate the quality of this text: {{text}}. Return only a score from 0.0 to 1.0.",
        "output_key": "quality_score"
      }
    },
    {
      "id": "route_by_quality",
      "type": "route_condition",
      "config": {
        "branches": [
          {
            "condition": "float(quality_score) > 0.9",
            "target": "high_quality_handler",
            "label": "High Quality"
          },
          {
            "condition": "float(quality_score) <= 0.9",
            "target": "low_quality_handler",
            "label": "Low Quality"
          }
        ]
      }
    },
    {
      "id": "high_quality_handler",
      "type": "operator",
      "config": {
        "sets": {"result": "Approved"}
      }
    },
    {
      "id": "low_quality_handler",
      "type": "operator",
      "config": {
        "sets": {"result": "Rejected"}
      }
    }
  ],
  "edges": [
    {"source": "analyze", "target": "route_by_quality"},
    {"source": "route_by_quality", "target": "high_quality_handler"},
    {"source": "route_by_quality", "target": "low_quality_handler"}
  ],
  "initial_state": {
    "text": "This is a sample text to analyze."
  }
}
```

### 6.3 병렬 실행

```json
{
  "workflow_name": "parallel_processing",
  "nodes": [
    {
      "id": "parallel_tasks",
      "type": "parallel_group",
      "config": {
        "branches": [
          {
            "branch_id": "sentiment_analysis",
            "sub_workflow": {
              "nodes": [
                {
                  "id": "sentiment_llm",
                  "type": "llm_chat",
                  "config": {
                    "model": "gemini-2.0-flash-exp",
                    "prompt_content": "Analyze sentiment: {{text}}",
                    "output_key": "sentiment"
                  }
                }
              ]
            }
          },
          {
            "branch_id": "entity_extraction",
            "sub_workflow": {
              "nodes": [
                {
                  "id": "entity_llm",
                  "type": "llm_chat",
                  "config": {
                    "model": "gemini-2.0-flash-exp",
                    "prompt_content": "Extract entities: {{text}}",
                    "output_key": "entities"
                  }
                }
              ]
            }
          }
        ]
      }
    },
    {
      "id": "aggregate",
      "type": "aggregator"
    }
  ],
  "edges": [
    {"source": "parallel_tasks", "target": "aggregate"}
  ],
  "initial_state": {
    "text": "Apple announced a new iPhone in California."
  }
}
```

### 6.4 HITP (Human-in-the-Loop)

```json
{
  "workflow_name": "approval_workflow",
  "nodes": [
    {
      "id": "generate_draft",
      "type": "llm_chat",
      "config": {
        "model": "gemini-2.0-flash-exp",
        "prompt_content": "Generate a draft email for: {{topic}}",
        "output_key": "draft"
      }
    },
    {
      "id": "send_email",
      "type": "api_call",
      "config": {
        "url": "https://api.example.com/send",
        "method": "POST",
        "body": {
          "email": "{{draft}}"
        }
      }
    }
  ],
  "edges": [
    {
      "source": "generate_draft",
      "target": "send_email",
      "type": "hitp"
    }
  ],
  "initial_state": {
    "topic": "Project update"
  }
}
```

### 6.5 For Each 반복

```json
{
  "workflow_name": "batch_processing",
  "nodes": [
    {
      "id": "process_items",
      "type": "for_each",
      "config": {
        "items_path": "items",
        "item_key": "current_item",
        "output_key": "results",
        "max_iterations": 100,
        "sub_workflow": {
          "nodes": [
            {
              "id": "process_one",
              "type": "llm_chat",
              "config": {
                "model": "gemini-2.0-flash-exp",
                "prompt_content": "Process: {{current_item}}",
                "output_key": "processed"
              }
            }
          ]
        }
      }
    }
  ],
  "edges": [],
  "initial_state": {
    "items": ["item1", "item2", "item3"]
  }
}
```

---

## 📊 요약 통계

| 구분 | 개수 |
|------|------|
| **노드 타입** | 22개 (Core: 4, Flow: 7, Infra: 2, Multimodal: 3, UI: 5, Subgraph: 1) |
| **엣지 타입** | 8개 (edge, normal, flow, hitp, human_in_the_loop, pause, start, end) |
| **노드 Alias** | 9개 |
| **예약 State 키** | 30개 |
| **최대 노드 수** | 500개 |
| **최대 엣지 수** | 1000개 |

---

## 🔄 변경 이력

### v3.27 (2026-02-19)
- ✅ `route_condition` 노드 추가 (라우팅 주권 일원화)
- ❌ EdgeModel에서 `router_func`, `mapping`, `condition` 제거
- ✅ `dynamic_router` 노드 추가 (LLM 기반 라우팅)

### v3.20
- ✅ StateViewContext (메모리 78% 절감)
- ✅ S3 lazy hydration

### v3.8
- ✅ Loop 노드 convergence 지원

### v3.0
- ✅ Multimodal inputs (image_inputs, video_inputs)
- ✅ Explicit media specification

---

**문서 작성자**: Analemma-OS Architecture Team  
**라이선스**: MIT License
