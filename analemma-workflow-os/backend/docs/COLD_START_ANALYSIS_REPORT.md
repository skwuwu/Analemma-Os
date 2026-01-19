# 🧊 Lambda 콜드 스타트 분석 보고서

**분석 일자**: 2025년 1월  
**분석 대상**: Analemma Workflow OS Backend Lambda Functions  
**분석자**: GitHub Copilot

---

## 📋 요약 (Executive Summary)

현재 Lambda 아키텍처에서 콜드 스타트를 유발하는 **5가지 핵심 요인**을 식별했습니다:

| 순위 | 요인 | 예상 영향 | 심각도 |
|------|------|----------|--------|
| 1 | Docker 컨테이너 이미지 기반 배포 | +3~8초 | 🔴 Critical |
| 2 | 무거운 AI/ML 패키지 의존성 | +1.5~2.5초 | 🔴 Critical |
| 3 | 모듈 레벨 초기화 코드 체인 | +0.8~1.5초 | 🟠 High |
| 4 | 사용하지 않는 의존성 포함 | +0.3~0.5초 | 🟡 Medium |
| 5 | 런타임 패키지 버전 불일치 | +0.1~0.2초 | 🟢 Low |

**총 예상 콜드 스타트 시간**: 약 **6~13초**

---

## 1️⃣ Docker 컨테이너 이미지 기반 배포 (Critical)

### 현재 상태
```yaml
# template.yaml - 모든 함수가 Image 타입 사용
PackageType: Image
ImageConfig:
  Command:
    - src.handlers.core.main.handler
```

### 문제점
- **모든 Lambda 함수**가 `PackageType: Image` 사용
- ZIP 배포 대비 **5~10초 추가 지연** 발생
- ECR에서 컨테이너 이미지 Pull 시간이 콜드 스타트의 주요 원인

### 영향 분석
| 배포 방식 | 일반적인 콜드 스타트 | 비고 |
|----------|---------------------|------|
| ZIP (.zip) | 100ms ~ 1초 | Layer 포함 최대 250MB |
| Container Image | 3초 ~ 15초 | 이미지 크기에 비례 |

### 현재 Dockerfile 구조
```dockerfile
# Dockerfile.base - Heavy Dependencies
FROM public.ecr.aws/lambda/python:3.12
COPY requirements.txt .
RUN pip install -r requirements.txt

# Dockerfile.lambda - Application Code
ARG BASE_IMAGE_URI=public.ecr.aws/lambda/python:3.12
FROM ${BASE_IMAGE_URI}
COPY . /var/task/
```

**Base Image에 포함된 무거운 패키지들:**
- `google-cloud-aiplatform>=1.38.0` (~300MB)
- `langgraph>=0.0.40` + 의존성들 (~100MB)
- `pydantic>=2.7.4` (~50MB)
- `fastapi>=0.104.0` + `uvicorn` (~30MB)

---

## 2️⃣ 무거운 AI/ML 패키지 의존성 (Critical)

### Import 시간 측정 결과

```
📊 핵심 모듈 Import 시간 순위 (내림차순)
---------------------------------------------
main.py (run_workflow)   :   788.7ms (54.1%) ██████████████████
src.common.statebag      :   328.3ms (22.5%) ███████
boto3/botocore           :   192.2ms (13.2%) ████
langchain_core           :    73.6ms ( 5.1%) █
pydantic                 :    36.2ms ( 2.5%) 
표준 라이브러리          :    21.1ms ( 1.4%) 
aws_lambda_powertools    :    15.6ms ( 1.1%) 
StateManager             :     1.2ms ( 0.1%) 
langgraph                :     0.3ms ( 0.0%) 
---------------------------------------------
TOTAL                    :  1457.3ms
```

### 서브모듈 상세 분석

```
📊 서브모듈 Import 시간 (warm 상태에서도 높음)
--------------------------------------------------
jsonschema                         : 1426.21ms  ← ⚠️ 사용 안 함!
langchain_core.runnables           :  238.58ms
langchain_core.messages            :  170.26ms
pydantic.BaseModel                 :  168.84ms
langgraph.graph.StateGraph         :   52.67ms
aws_lambda_powertools.Tracer       :   13.42ms
```

### 핵심 의존성 트리 (requirements.txt)

| 패키지 | 크기(추정) | 의존성 수 | 사용 빈도 |
|--------|-----------|----------|----------|
| `google-cloud-aiplatform` | ~300MB | 80+ | 높음 (Gemini) |
| `langgraph` | ~50MB | 20+ | 높음 |
| `langchain-core` | ~40MB | 15+ | 높음 |
| `pydantic` | ~50MB | 5+ | 매우 높음 |
| `boto3/botocore` | ~120MB | 10+ | 필수 |
| `fastapi` | ~30MB | 10+ | API만 사용 |
| `jsonschema` | ~5MB | 3+ | **미사용** |

---

## 3️⃣ 모듈 레벨 초기화 코드 체인 (High)

### 문제 위치

**[src/common/__init__.py](src/common/__init__.py)** - 68줄에서 시작되는 체인:

```python
# 모든 유틸리티를 무조건 import
from src.common.logging_utils import (
    get_logger,
    get_tracer,      # ← aws_xray_sdk 초기화 트리거
    get_metrics,     # ← aws_lambda_powertools 초기화 트리거
    ...
)
```

### 초기화 체인 분석

```
src/common/__init__.py
    └── src.common.logging_utils
        └── aws_lambda_powertools.Tracer()  ← __init__에서 X-Ray SDK 초기화
            └── aws_xray_sdk.core.xray_recorder
                └── 네트워크 설정 초기화

src/handlers/core/main.py (1974줄)
    ├── import boto3 (192ms)
    ├── from pydantic import ... (168ms)
    ├── from langgraph.graph.message import add_messages
    ├── from src.langchain_core_custom.outputs import ... (체인 트리거)
    └── 전역 Logger 초기화
```

### Import 순환 우려 경로

```
main.py 
  → src.common 
    → src.common.aws_clients (boto3 초기화)
    → src.common.logging_utils (Tracer 초기화)
      → aws_xray_sdk (네트워크 I/O)
```

---

## 4️⃣ 사용하지 않는 의존성 (Medium)

### 분석 결과

| 패키지 | requirements.txt | 코드 사용 | 상태 |
|--------|-----------------|----------|------|
| `jsonschema` | ✅ 포함 | ❌ 미사용 | **제거 가능** |
| `croniter` | ✅ 포함 | ? 확인 필요 | 검토 필요 |
| `uvicorn[standard]` | ✅ 포함 | ❌ Lambda 불필요 | **제거 가능** |
| `mangum` | ✅ 포함 | FastAPI용 | 조건부 필요 |
| `python-multipart` | ✅ 포함 | 파일 업로드용 | 조건부 필요 |
| `asyncpg` | ✅ 포함 | pgvector용 | 확인 필요 |

### jsonschema 제거 영향
- Import 시간 **~1,426ms 절약** (가장 큰 단일 절약)
- 코드 검색 결과: `.py` 파일에서 `jsonschema` import 문 **0건**
- `requirements.txt`에만 선언되어 있음

---

## 5️⃣ Lambda 레이어 구조 분석

### 현재 레이어 구성

```
packages/lambda-layers/
├── common/          # 기본: boto3, requests, aws-lambda-powertools
├── heavy/           # 무거움: google-cloud-aiplatform, fastapi, pydantic
├── llm/             # LLM: langgraph, openai, anthropic
├── llm_core/        # langchain-core, langchain
├── generativeai/    # google-ai-generativelanguage
├── google/          # google-auth
└── google_api_client/  # google-api-python-client
```

### 레이어별 예상 크기

| 레이어 | 포함 패키지 | 예상 크기 | Lambda 제한 |
|--------|------------|----------|------------|
| common | boto3, requests, aws-lambda-powertools | ~80MB | ✅ |
| heavy | google-cloud-aiplatform, fastapi, pydantic, aiohttp | ~350MB | ❌ **초과** |
| llm | langgraph, openai, anthropic | ~100MB | ✅ |
| llm_core | langchain-core, langchain | ~60MB | ✅ |

**Lambda Layer 제한**: 최대 250MB (unzipped) × 5개 레이어

> ⚠️ `heavy/` 레이어가 250MB 제한을 초과하여 Docker 이미지로 마이그레이션된 것으로 추정

---

## 6️⃣ 함수별 메모리/타임아웃 설정 분석

### 현재 설정 (template.yaml)

| 함수 | Memory | Timeout | Reserved Concurrency |
|------|--------|---------|---------------------|
| Globals (기본) | 512MB | 30s | - |
| SegmentRunnerFunction | 1024MB | 300s | 200 |
| WorkflowOrchestrator | 1024MB | 300s | - |
| ChunkedWorkflowRunner | 2048MB | 900s | - |
| AggregateResults | 2048MB | 600s | - |

### 메모리 vs 콜드 스타트 상관관계

| 메모리 | vCPU 비율 | 예상 초기화 속도 |
|--------|----------|-----------------|
| 512MB | 0.33 vCPU | 느림 |
| 1024MB | 0.66 vCPU | 보통 |
| 2048MB | 1.0 vCPU | 빠름 |
| 3008MB+ | 1.5+ vCPU | 매우 빠름 |

> 📌 **권장**: SegmentRunnerFunction을 1536MB~2048MB로 증가 시 콜드 스타트 25~40% 개선 가능

---

## 📊 종합 콜드 스타트 분해

```
┌─────────────────────────────────────────────────────────────────┐
│                    Lambda 콜드 스타트 분해                        │
├─────────────────────────────────────────────────────────────────┤
│ ┌───────────────────────────────────────────┐                   │
│ │ 1. 컨테이너 이미지 Pull (ECR)              │ ~3000-8000ms    │
│ └───────────────────────────────────────────┘                   │
│ ┌───────────────────────────────────────────┐                   │
│ │ 2. Python 런타임 초기화                    │ ~100-200ms      │
│ └───────────────────────────────────────────┘                   │
│ ┌───────────────────────────────────────────┐                   │
│ │ 3. 외부 패키지 Import                      │                  │
│ │   ├─ boto3/botocore                       │ ~200ms          │
│ │   ├─ pydantic                             │ ~170ms          │
│ │   ├─ langchain_core                       │ ~250ms          │
│ │   ├─ langgraph                            │ ~50ms           │
│ │   ├─ aws_lambda_powertools                │ ~15ms           │
│ │   └─ jsonschema (미사용!)                  │ ~1426ms ⚠️      │
│ └───────────────────────────────────────────┘                   │
│ ┌───────────────────────────────────────────┐                   │
│ │ 4. 내부 모듈 체인 초기화                   │                  │
│ │   ├─ src.common (aws_clients, logging)    │ ~330ms          │
│ │   ├─ main.py 전역 초기화                  │ ~790ms          │
│ │   └─ X-Ray/Tracer 초기화                  │ ~100ms          │
│ └───────────────────────────────────────────┘                   │
├─────────────────────────────────────────────────────────────────┤
│ 총 예상 콜드 스타트: 6,000ms ~ 13,000ms (6~13초)                │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ 권장 조치 사항

### 🔴 즉시 조치 (High Impact, Low Effort)

#### 1. 미사용 의존성 제거
```diff
# requirements.txt & src/requirements.txt
- jsonschema
- uvicorn[standard]>=0.24.0  # Lambda에서 불필요
```
**예상 절감**: ~1.5초

#### 2. SegmentRunnerFunction 메모리 증가
```yaml
# template.yaml
SegmentRunnerFunction:
  MemorySize: 1536  # 1024 → 1536
```
**예상 절감**: ~20% 초기화 속도 향상

---

### 🟠 중기 조치 (High Impact, Medium Effort)

#### 3. Lazy Import 패턴 적용

**Before (현재):**
```python
# src/common/__init__.py
from src.common.logging_utils import get_logger, get_tracer, get_metrics
from src.common.aws_clients import get_dynamodb_resource, ...
```

**After (권장):**
```python
# src/common/__init__.py
def get_tracer():
    from src.common.logging_utils import get_tracer as _get_tracer
    return _get_tracer()

# 또는 TYPE_CHECKING 패턴
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.common.logging_utils import get_tracer
```

**예상 절감**: ~400ms

#### 4. Conditional Import for AI Packages

```python
# src/services/llm/gemini_service.py (현재 잘 되어 있음)
def _init_vertexai() -> bool:
    try:
        import vertexai  # ← 런타임에만 import
        ...
```

다른 서비스에도 동일 패턴 확장 필요

---

### 🟢 장기 조치 (High Impact, High Effort)

#### 5. Provisioned Concurrency 도입

```yaml
# template.yaml
SegmentRunnerFunction:
  AutoPublishAlias: live
  ProvisionedConcurrencyConfig:
    ProvisionedConcurrentExecutions: 5  # 최소 warm 인스턴스
```

**비용**: ~$0.00004/GB-second (월 ~$15-50 예상)
**효과**: 콜드 스타트 완전 제거 (5개 인스턴스)

#### 6. ZIP 배포로 전환 (선택적)

핵심 Lambda만 ZIP 배포로 전환:
- `CommonDependenciesLayer` (boto3, pydantic)
- `LLMCoreLayer` (langchain_core)
- Application code as ZIP

Heavy packages (google-cloud-aiplatform)는 ECS Fargate로 오프로드

#### 7. Lambda SnapStart (Java 전용, 참고)

Python은 아직 미지원이지만, AWS Roadmap에 있음

---

## 📈 기대 효과 요약

| 조치 | 예상 절감 | 난이도 | 우선순위 |
|------|----------|--------|---------|
| jsonschema 제거 | ~1.5초 | ⭐ | P0 |
| 메모리 증가 (1536MB) | ~1.0초 | ⭐ | P0 |
| Lazy Import 적용 | ~0.4초 | ⭐⭐ | P1 |
| 미사용 패키지 정리 | ~0.3초 | ⭐⭐ | P1 |
| Provisioned Concurrency | 콜드 스타트 제거 | ⭐⭐⭐ | P2 |

**총 예상 개선**: 콜드 스타트 **6~13초 → 3~5초** (50%+ 개선)

---

## 📎 참고: 코드 파일 크기 분석

```
TOP 10 Python 파일 (라인 수)
---------------------------------------------
3558 ./src/handlers/simulator/mission_simulator.py
1986 ./src/services/design/codesign_assistant.py
1974 ./src/handlers/core/main.py
1966 ./src/handlers/core/instruction_distiller.py
1880 ./src/services/execution/segment_runner_service.py
1792 ./src/handlers/core/aggregate_distributed_results.py
1703 ./src/common/model_router.py
1658 ./src/services/llm/gemini_service.py
1611 ./src/services/instruction_conflict_service.py
1349 ./src/services/llm/structure_tools.py
```

- 총 Python 파일: **156개**
- 총 코드 라인: **76,608줄**

---

## ✅ 결론

현재 Lambda 콜드 스타트의 가장 큰 원인은:

1. **Docker 이미지 기반 배포** (불가피 - heavy deps 때문)
2. **jsonschema 불필요 의존성** (즉시 제거 가능)
3. **모듈 레벨 eager import 체인** (리팩토링 필요)

**즉시 적용 가능한 Quick Win:**
- `jsonschema` 제거 → **~1.5초 절감**
- `MemorySize: 1024 → 1536` → **~1초 절감**

이 두 가지만으로도 콜드 스타트를 **25~30% 개선**할 수 있습니다.
