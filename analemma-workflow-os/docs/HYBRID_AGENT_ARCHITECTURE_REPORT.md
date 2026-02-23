# Analemma OS — Hybrid Architecture & Loop Virtualization
## Feasibility Report & Concrete Implementation Plan

**작성일:** 2026-02-22
**최종 수정:** 2026-02-23 (피드백 반영: Polling Scalability, ZKP Storage, Memory Ghosting, Optimistic Commitment, Recovery Instruction, Audit over Score, Reordering Buffer, Hybrid Interceptor, shared_policy.py Single Source of Truth, BridgeRingLevel IntEnum, Redis AuditRegistry TTL, Ring 3 is_optimistic_report 강제, Policy Sync 엔드포인트, ZWS/Homoglyph 정규화, TypeScript Bridge SDK — v1.3.0)
**대상 계획:** Plan A (B2B Hybrid Local+Cloud) / Plan B (Loop Virtualization Bridge SDK)
**문서 범위:** 타당성 평가 · 기술 리스크 분석 · 단계별 구현 방안

---

## Table of Contents

1. [문서 목적 및 범위](#1-문서-목적-및-범위)
2. [제안 요약 비교](#2-제안-요약-비교)
3. [Plan A — B2B Hybrid Architecture](#3-plan-a--b2b-hybrid-architecture)
   - 3.1 적절성 평가
   - 3.2 기술 실현 가능성
   - 3.3 구체적 구현 방안
   - 3.4 비즈니스 모델 설계
   - 3.5 리스크 및 완화
4. [Plan B — Loop Virtualization / Bridge SDK](#4-plan-b--loop-virtualization--bridge-sdk)
   - 4.1 적절성 평가
   - 4.2 기술 실현 가능성
   - 4.3 ABI 명세 고도화
   - 4.4 구체적 구현 방안 (Hybrid Interceptor 포함)
   - 4.5 커널 연동: VirtualSegmentManager (Reordering Buffer 포함)
   - 4.6 리스크 및 완화
5. [통합 아키텍처 — 두 계획의 결합](#5-통합-아키텍처--두-계획의-결합)
6. [구현 로드맵](#6-구현-로드맵)
7. [결론 및 권고사항](#7-결론-및-권고사항)

---

## 1. 문서 목적 및 범위

본 문서는 Analemma OS의 다음 두 가지 확장 방향에 대한 **냉정한 기술 검토**를 수행한다.

| 계획 | 핵심 아이디어 |
|------|---------------|
| **Plan A** | 로컬 엔진(Local Agent)과 AWS SFN 제어 플레인을 분리하여 B2B 하이브리드 배포 모델 구현 |
| **Plan B** | 자율형 에이전트의 비정형 루프(Thought-Action-Observation)를 Analemma 커널의 결정론적 세그먼트로 강제 매핑 |

평가 기준: **기술 실현 가능성(0–10)**, **구현 복잡도(0–10, 높을수록 어려움)**, **비즈니스 가치(0–10)**

---

## 2. 제안 요약 비교

| 항목 | Plan A (Hybrid) | Plan B (Loop Virtualization) |
|------|-----------------|------------------------------|
| 기술 실현 가능성 | **8 / 10** | **7 / 10** |
| 구현 복잡도 | 6 / 10 (중상) | 8 / 10 (상) |
| 비즈니스 가치 | **9 / 10** | 8 / 10 |
| 기존 코드 재사용률 | 높음 (kernel_protocol, USC 그대로 활용) | 중간 (GovernanceEngine, SemanticShield 재사용) |
| 단독 구현 가능 여부 | 가능 | 가능 |
| 권고 우선순위 | **1순위** | 2순위 (Plan A 완료 후) |

**결론 선요약:** Plan A는 즉시 착수 가능한 검증된 패턴(AWS SFN Activity Worker)을 기반으로 하며 단기 수익화 경로가 명확하다. 단, Polling Scalability 한도와 S3 보안 역설(→ ZKP Storage로 해결)에 유의한다. Plan B는 개념적으로 탁월하나 Memory Ghosting(→ State Rehydration으로 해결)과 레이턴시 복리(→ Optimistic Commitment로 완화), Hallucination Loop(→ Recovery Instruction 필수화) 문제가 존재한다. "Plan A=수익, Plan B=권위" 전략으로 병렬 추진.

---

## 3. Plan A — B2B Hybrid Architecture

### 3.1 적절성 평가

#### 시장 적합성

B2B 기업 시장에서 "데이터는 내부, 거버넌스는 클라우드" 모델은 이미 검증된 판매 전략이다.

| 비교 사례 | 구조 |
|-----------|------|
| HashiCorp Vault | 로컬 비밀 저장소 + 클라우드 정책 관리 |
| Datadog Agent | 로컬 메트릭 수집 + 클라우드 분석 |
| GitHub Actions Self-hosted Runner | 로컬 실행 컨테이너 + GitHub 오케스트레이션 |
| **Analemma OS (제안)** | 로컬 에이전트 실행 + AWS SFN 거버넌스 |

Analemma의 제안은 기존 시장에서 검증된 하이브리드 SaaS 패턴이다. 특히 **GDPR/개인정보보호법 규제** 환경에서 민감 데이터가 클라우드로 올라가지 않는다는 보안 보장은 강력한 영업 포인트다.

#### 현재 아키텍처와의 정합성

Analemma의 `open_state_bag` / `seal_state_bag` 프로토콜은 이미 Lambda와 스토리지를 디커플링한 설계다. 이 계약(Contract)을 로컬 프로세스에 동일하게 이식하면 별도의 재설계 없이 로컬 엔진을 구동할 수 있다.

**현재:**
```
Lambda (클라우드) → open_state_bag → [실행] → seal_state_bag → SFN
```

**Plan A 이후:**
```
Lambda (클라우드) → SFN Activity Task Token 발급
                ↓
Local Agent (온프레미스) → open_state_bag → [실행] → seal_state_bag → SendTaskSuccess
                ↓
SFN → 다음 단계 진행
```

코드 변경 최소화: `open_state_bag` / `seal_state_bag` 함수 시그니처는 변경 없이 재사용.

---

### 3.2 기술 실현 가능성

#### 핵심 메커니즘: AWS SFN Activity Worker

AWS Step Functions Activity는 프로덕션 검증된 Long-Poll 기반 분산 워커 패턴이다.

| 항목 | 스펙 |
|------|------|
| Task Token 유효 기간 | 최대 1년 |
| Heartbeat 주기 | 설정 가능 (권장: 30–60초) |
| Polling 지연 | 최대 60초 (Long-Poll, 통상 1–5초 내 응답) |
| 동시 워커 수 | 제한 없음 (ActivityARN 기준 수평 확장 가능) |
| 보안 | IAM Role 기반 인증, 로컬 → AWS 단방향 아웃바운드만 필요 |

**장점:** 로컬 머신에 인바운드 포트 개방 불필요 (방화벽 문제 없음)
**단점:** Polling 오버헤드로 최소 ~1–3초 레이턴시 발생

#### SFN Activity vs HTTP Task 비교

| 항목 | Activity Worker (Polling) | HTTP Task (Push) |
|------|--------------------------|-----------------|
| 로컬 인바운드 포트 | 불필요 ✅ | 필요 (VPN 또는 공개 URL) |
| 레이턴시 | 1–5초 | < 500ms |
| 구현 복잡도 | 낮음 ✅ | 높음 (TLS 인증, ngrok 등) |
| 기업 네트워크 호환성 | 높음 ✅ | 낮음 (방화벽 이슈) |
| 권고 | **B2B 엔터프라이즈 1순위** | 개발자 개인 사용 적합 |

**결론:** B2B 엔터프라이즈 배포는 SFN Activity Worker 방식 채택.

#### ⚠️ 현실적 병목: Polling Scalability

> **핵심 리스크:** 고객사가 수백 대의 로컬 에이전트를 동시에 가동하면 `GetActivityTask` API Rate Limit에 도달할 수 있다.

AWS `GetActivityTask`의 실제 제한치:
- 계정 전체 기준 초당 약 **200 req/s** (us-east-1 기준, 리전별 상이)
- 워커 1대 = 60초 롱폴링 1회 → 100대 워커 = 약 1.67 req/s (문제 없음)
- **워커 1,000대 이상** = 16.7 req/s → 여전히 한계 이하이나, 순간 burst 시 ThrottlingException 발생 가능

**대응 전략 — 2단계 확장 계획:**

| 규모 | 전략 | 구조 |
|------|------|------|
| 워커 < 500대 | SFN Activity 직접 연결 | 기본 구현 그대로 사용 |
| 워커 500–5,000대 | **Analemma Relay (SQS 기반)** | SFN → SQS → 로컬 워커 (Fan-out) |
| 워커 5,000대 이상 | SQS + FIFO Queue per Region | 멀티 리전 분산 |

**Analemma Relay 구조 (SQS 기반 예비 플랜):**

```
SFN (Work distributor)
  → SQS Standard Queue (analemma-work-queue)
      → 로컬 워커 A: SQS ReceiveMessage (Long-Poll, 최대 20초)
      → 로컬 워커 B: SQS ReceiveMessage (Long-Poll)
      → 로컬 워커 N: SQS ReceiveMessage (Long-Poll)
  ← SendTaskSuccess(taskToken, result)  ← 워커가 SFN에 직접 보고
```

SQS는 `GetActivityTask`보다 훨씬 높은 처리량(초당 수만 건)을 지원하므로 대규모 배포에서 병목이 해소된다. 단, 이 구조에서 Task Token은 SQS 메시지 본문에 포함되어 전달되며, 워커가 작업 완료 후 직접 `SendTaskSuccess`를 호출하는 패턴은 동일하게 유지된다.

**구현 원칙:** Phase 0–1에서는 SFN Activity 직접 연결로 시작하고, 실제 고객사 워커 수가 500대를 초과하는 시점에 SQS Relay로 마이그레이션한다. 두 구조는 `LocalAgentWorker` 인터페이스 변경 없이 Transport 레이어만 교체하면 전환 가능하도록 설계한다.

---

### 3.3 구체적 구현 방안

#### Step 1: ASL 수정 — Activity Task 노드 삽입

기존 SFN ASL에 `LOCAL_EXECUTION` 상태를 추가한다.

```json
{
  "LocalExecutionState": {
    "Type": "Task",
    "Resource": "arn:aws:states:us-east-1:ACCOUNT_ID:activity:analemma-local-agent",
    "Parameters": {
      "segment_id.$": "$.state_data.bag.current_segment_id",
      "workflow_id.$": "$.state_data.bag.workflow_id",
      "agent_config.$": "$.state_data.bag.agent_config",
      "state_s3_pointer.$": "$.state_data.bag.state_s3_pointer"
    },
    "HeartbeatSeconds": 60,
    "TimeoutSeconds": 3600,
    "ResultPath": "$.state_data.bag.local_result",
    "Next": "GovernorValidationState"
  }
}
```

**설계 원칙:** 로컬 실행 결과는 S3 포인터 형태로만 SFN에 반환. 민감 데이터는 클라우드에 직접 오르지 않는다.

#### Step 2: Local Agent 워커 구현

```python
# analemma_local_agent/worker.py
import boto3
import json
import threading
import logging
from src.kernel.kernel_protocol import open_state_bag, seal_state_bag

logger = logging.getLogger(__name__)

ACTIVITY_ARN = "arn:aws:states:us-east-1:ACCOUNT_ID:activity:analemma-local-agent"


class LocalAgentWorker:
    """
    AWS SFN Activity Worker — Analemma 로컬 엔진 메인 루프

    흐름:
      1. GetActivityTask (Long-Poll, 최대 60초 대기)
      2. 작업 수신 시 Task Token + Input 획득
      3. 로컬 커널 실행 (open_state_bag → 에이전트 실행 → seal_state_bag)
      4. S3에 결과 업로드 후 포인터만 SendTaskSuccess
      5. Heartbeat 스레드로 타임아웃 방지
    """

    def __init__(self, worker_name: str = "analemma-local-worker"):
        self.sfn = boto3.client("stepfunctions")
        self.s3 = boto3.client("s3")
        self.worker_name = worker_name
        self._shutdown = threading.Event()

    def run(self):
        logger.info(f"[LocalAgent] Worker '{self.worker_name}' started. Polling...")
        while not self._shutdown.is_set():
            self._poll_and_execute()

    def _poll_and_execute(self):
        try:
            response = self.sfn.get_activity_task(
                activityArn=ACTIVITY_ARN,
                workerName=self.worker_name
            )
        except Exception as e:
            logger.error(f"[LocalAgent] Polling error: {e}")
            return

        if "taskToken" not in response or not response["taskToken"]:
            return  # 작업 없음 — 다시 polling

        token = response["taskToken"]
        raw_input = json.loads(response["input"])

        # Heartbeat 스레드 시작 (30초 간격)
        heartbeat = threading.Thread(
            target=self._heartbeat_loop, args=(token,), daemon=True
        )
        heartbeat.start()

        try:
            # 로컬 커널 실행
            state = open_state_bag(raw_input)
            result_delta = self._run_local_agent(state)
            sealed = seal_state_bag(state, result_delta, action="local_execute")

            # 결과를 S3에 오프로드, 포인터만 반환
            s3_pointer = self._upload_result_to_s3(sealed, raw_input["workflow_id"])

            self.sfn.send_task_success(
                taskToken=token,
                output=json.dumps({"state_s3_pointer": s3_pointer, "status": "SUCCESS"})
            )
        except Exception as e:
            logger.error(f"[LocalAgent] Execution failed: {e}")
            self.sfn.send_task_failure(
                taskToken=token,
                error=type(e).__name__,
                cause=str(e)[:256]
            )

    def _heartbeat_loop(self, token: str, interval: int = 30):
        while not self._shutdown.is_set():
            try:
                self.sfn.send_task_heartbeat(taskToken=token)
            except Exception:
                break
            self._shutdown.wait(interval)

    def _upload_result_to_s3(self, sealed_state: dict, workflow_id: str) -> str:
        import hashlib
        content = json.dumps(sealed_state, sort_keys=True).encode()
        key = f"local-results/{workflow_id}/{hashlib.sha256(content).hexdigest()}.json"
        bucket = self._get_state_bucket()
        self.s3.put_object(Bucket=bucket, Key=key, Body=content)
        return f"s3://{bucket}/{key}"

    def _get_state_bucket(self) -> str:
        import os
        return os.environ.get("WORKFLOW_STATE_BUCKET", "analemma-state")

    def _run_local_agent(self, state: dict) -> dict:
        """
        실제 로컬 에이전트 실행 — 파일 시스템, 로컬 프로세스 등 접근 가능
        override 포인트: 고객사별 커스텀 로직 주입
        """
        raise NotImplementedError("Subclass and implement _run_local_agent()")
```

#### Step 3: 로컬 에이전트 패키징 — 3가지 배포 형태

| 형태 | 대상 | 장점 | 단점 |
|------|------|------|------|
| **Docker 이미지** | DevOps팀이 있는 기업 | 환경 격리, 버전 고정 | Docker 설치 필요 |
| **pip 패키지** (`analemma-agent`) | Python 개발자 | `pip install`로 즉시 설치 | Python 환경 의존 |
| **단일 바이너리** (PyInstaller) | 비개발 부서 PC 배포 | 의존성 없음 | 빌드 복잡, 업데이트 어려움 |

**권고:** Docker 우선, pip 패키지 병행.

#### Step 3-b: Zero-Knowledge Storage — MinIO / 온프레미스 스토리지 지원

> **보안 역설 수정:** 기존 설계는 "민감 데이터는 클라우드에 오르지 않는다"고 명시하나, `_upload_result_to_s3()`가 AWS S3를 사용할 경우 결국 데이터가 클라우드로 전송된다. 진정한 하이브리드를 위한 해결책은 **Zero-Knowledge Governance 구조**다.

**목표:** Analemma 커널(AWS)에는 실제 데이터가 전혀 전달되지 않고, 오직 **컨텐츠 해시(SHA-256)** 만 전달된다.

```
로컬 실행 결과
  ↓
고객사 내부 스토리지 (MinIO / NFS / 온프레미스 S3 호환)
  ↓ SHA-256 해시만 추출
AWS SFN SendTaskSuccess:
  { "content_hash": "sha256:abc123...", "size_bytes": 42000, "status": "SUCCESS" }
  (실제 데이터 내용: 전송되지 않음)
  ↓
AWS Governor Lambda:
  - 해시로 무결성 검증 가능 (변조 감지)
  - 데이터 내용 접근 불가 (Zero-Knowledge)
  - DynamoDB Audit Log에 해시만 기록
```

**Storage Provider 추상화 인터페이스:**

```python
# analemma_local_agent/storage.py
from abc import ABC, abstractmethod
import hashlib, json

class StorageProvider(ABC):
    """
    로컬 스토리지 추상화 — MinIO, AWS S3, NFS, Azure Blob 등 구현 가능
    커널에는 항상 hash와 메타데이터만 반환 (데이터 내용 전달 없음)
    """
    @abstractmethod
    def store(self, content: bytes, key: str) -> str:
        """저장 후 스토리지 URI 반환 (로컬 내부용)"""
        ...

    def store_and_hash(self, data: dict, key: str) -> dict:
        """
        데이터를 내부 스토리지에 저장하고
        커널에 전달할 Zero-Knowledge 메타데이터만 반환
        """
        content = json.dumps(data, sort_keys=True).encode()
        content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
        internal_uri = self.store(content, key)
        return {
            "content_hash": content_hash,
            "size_bytes": len(content),
            "internal_uri": internal_uri,   # 로컬 내부 참조용 (클라우드로 전송 안 함)
        }


class MinIOProvider(StorageProvider):
    """MinIO / S3 호환 온프레미스 스토리지"""
    def __init__(self, endpoint: str, bucket: str,
                 access_key: str, secret_key: str):
        from minio import Minio
        self.client = Minio(endpoint, access_key=access_key,
                           secret_key=secret_key, secure=False)
        self.bucket = bucket

    def store(self, content: bytes, key: str) -> str:
        from io import BytesIO
        self.client.put_object(self.bucket, key, BytesIO(content), len(content))
        return f"minio://{self.bucket}/{key}"


class AWSS3Provider(StorageProvider):
    """기존 AWS S3 — Enterprise ZKP 불필요 고객사용"""
    def __init__(self, bucket: str):
        import boto3
        self.s3 = boto3.client("s3")
        self.bucket = bucket

    def store(self, content: bytes, key: str) -> str:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=content)
        return f"s3://{self.bucket}/{key}"
```

**`LocalAgentWorker._upload_result_to_s3()` 교체:**

```python
def _upload_result(self, sealed_state: dict, workflow_id: str) -> dict:
    """
    StorageProvider 추상화로 업로드.
    반환값은 Zero-Knowledge 메타데이터 (해시 + 크기만).
    """
    key = f"local-results/{workflow_id}/{self._generate_key()}.json"
    return self.storage_provider.store_and_hash(sealed_state, key)
```

SFN에 전달되는 페이로드:
```json
{
  "content_hash": "sha256:3f4a91b...",
  "size_bytes": 18432,
  "status": "SUCCESS"
}
```

커널은 해시를 Merkle DAG에 기록하여 **무결성을 검증**하되, 데이터 내용에는 접근하지 않는다. 고객사의 온프레미스 스토리지 접근 권한은 Analemma 클라우드에 존재하지 않는다.

**Enterprise Tier 영업 포인트 업데이트:**

> "저희 클라우드 인프라는 고객사 데이터의 **SHA-256 지문만** 보관합니다. 실제 데이터 열람은 기술적으로 불가능합니다. 이는 단순한 정책이 아닌 아키텍처적 보장입니다."

```dockerfile
# Dockerfile.local-agent
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY analemma_local_agent/ ./analemma_local_agent/
COPY src/kernel/ ./src/kernel/
# open_state_bag, seal_state_bag 등 공통 모듈

ENV ACTIVITY_ARN=""
ENV AWS_REGION="us-east-1"
ENV WORKFLOW_STATE_BUCKET=""

CMD ["python", "-m", "analemma_local_agent.worker"]
```

---

### 3.4 비즈니스 모델 설계

#### 가격 구조 (SaaS + Agent 라이선스 혼합)

```
Tier 1 — Starter (개발자 개인)
  · 클라우드 제어 플레인: 월 $49
  · 로컬 에이전트: 1대 무료
  · 지원: 커뮤니티

Tier 2 — Team (소규모 기업)
  · 클라우드 제어 플레인: 월 $299
  · 로컬 에이전트: 최대 10대 (초과 시 대당 $20/월)
  · 지원: 이메일 48h SLA

Tier 3 — Enterprise (대기업, 금융/의료)
  · 클라우드 제어 플레인: 협상 (보통 월 $2,000+)
  · 로컬 에이전트: 무제한 (사이트 라이선스)
  · On-Prem SFN 대체(AWS GovCloud 또는 자체 Step Functions 호환 엔진) 옵션
  · 지원: 전담 CSM + 4h SLA
```

#### 영업 핵심 메시지

> "에이전트는 고객사 내부 네트워크에서만 실행됩니다. 저희 클라우드로는 **'작업이 성공했는가'와 '보안 정책을 위반했는가'** 두 가지 메타데이터만 전송됩니다. GDPR, K-ISMS, 의료 데이터 규정을 준수하면서도 Analemma의 거버넌스 레이어를 사용할 수 있습니다."

---

### 3.5 리스크 및 완화

| 리스크 | 심각도 | 완화 방안 |
|--------|--------|-----------|
| 로컬 에이전트 버전 관리 어려움 | 중 | 자동 업데이트 매커니즘: 에이전트 기동 시 최신 버전 체크 → Docker pull 또는 pip upgrade |
| AWS 자격증명을 로컬에 보관해야 함 | 상 | AWS IAM Role for EC2/ECS 사용 또는 IAM Identity Center SSO 토큰 발급 (단기 유효 토큰만 사용) |
| Long-Poll 레이턴시 (~3초) | 하 | 대부분의 에이전트 루프(분 단위)에서 무시 가능. 실시간 필요 시 HTTP Task 옵션 제공 |
| 로컬 실행 환경 이기종성 | 중 | Docker 표준화, `requirements.txt` 고정, `python:3.12-slim` 베이스 이미지 |
| 네트워크 단절 시 Heartbeat 실패 → SFN 타임아웃 | 중 | HeartbeatSeconds를 넉넉히 설정 + 재시도 큐 (로컬 SQLite로 체크포인트) |

---

## 4. Plan B — Loop Virtualization / Bridge SDK

### 4.1 적절성 평가

#### 개념의 강점

자율형 에이전트(AutoGPT, CrewAI, LangGraph 등)의 근본 문제는 루프가 **불투명하고 비결정론적**이라는 점이다. Plan B는 이 루프를 Analemma 커널의 세그먼트 단위로 강제 분절함으로써:

1. **관찰 가능성**: 매 TAO(Thought-Action-Observation) 사이클에서 스냅샷 생성
2. **복구 가능성**: 특정 루프 인덱스부터 재시작 가능 (Merkle DAG 연동)
3. **거버넌스**: 각 행동(Action) 전에 커널의 명시적 승인 필요

이 개념은 **에이전트 런타임 계층**에서 실행되므로 에이전트 내부 LLM 프롬프트나 로직을 변경할 필요가 없다는 점에서 실용적이다.

#### 기존 사례와 차별점

| 기존 솔루션 | Analemma Loop Virtualization |
|-------------|------------------------------|
| LangGraph (StateGraph) | 개발자가 명시적으로 그래프를 정의해야 함 |
| AutoGen | 대화 패턴은 정형화되나 커널 수준 거버넌스 없음 |
| CrewAI | Task 단위 분절이나 세그먼트 체크포인트 없음 |
| **Analemma** | 에이전트가 **어떤 프레임워크**를 써도 커널이 가로채서 분절 |

---

### 4.2 기술 실현 가능성

#### 핵심 기술 과제 5가지

**과제 1: Memory Ghosting — 상태 직렬화의 근본 한계**

> **현실 진단:** LangChain Memory, Vector Store, Tool Instance 등 에이전트가 사용하는 외부 라이브러리 객체를 JSON으로 직렬화하면 **Circular Reference** 또는 **Unserializable Object** 에러가 99% 확률로 발생한다. 에이전트 전체 상태를 스냅샷하려는 시도는 실패한다.

**잘못된 접근 (시도하지 말 것):**

| 방식 | 결과 |
|------|------|
| `json.dumps(agent.__dict__)` | `TypeError: Object of type VectorStore is not JSON serializable` |
| `pickle.dumps(agent)` | 보안 취약, Python 버전 의존, 외부 파일 핸들 포함 시 실패 |
| 전체 객체 그래프 재귀 직렬화 | Circular Reference → `RecursionError` |

**올바른 전략 — State Rehydration:**

> 에이전트 상태를 **전부 저장**하는 대신, **핵심 컨텍스트(Core Context)**와 **현재 의도(Next Intent)**만 추출한다. 나머지 무거운 객체(Vector Store, LLM 클라이언트 등)는 에이전트가 재시작될 때 자체적으로 **Rehydration(재로드)** 하도록 설계한다.

```
스냅샷에 포함하는 것 (Lightweight Core Context):
  - short_term_memory: 최근 N개 메시지 (텍스트)
  - current_intent: 현재 루프의 목적 (문자열)
  - progress_markers: retry_count, step_name, found_flags 등 원시값
  - token_usage_total: 누적 토큰 수 (숫자)

스냅샷에서 제외하는 것 (Rehydratable):
  - VectorStore 인스턴스 → 재시작 시 동일 설정으로 재생성
  - LLM 클라이언트 → 환경변수 + API 키로 재연결
  - HTTP 세션, 파일 핸들, DB 커넥션 → 재연결
```

**`StateRehydrationMixin` — 에이전트 기반 클래스에 믹스인:**

```python
# analemma_bridge/python/rehydration.py

class StateRehydrationMixin:
    """
    State Rehydration 전략 구현 믹스인.
    에이전트 클래스가 상속하면 snapshot/restore 자동 지원.

    사용:
        class MyAgent(StateRehydrationMixin, BaseAgent):
            rehydratable_fields = ["vector_store", "llm_client"]
            snapshot_fields = ["retry_count", "current_step", "short_term_memory"]

            def rehydrate(self, snapshot: dict):
                # 재시작 시 무거운 객체 재생성
                self.vector_store = VectorStore.from_config(self.config)
                self.llm_client = LLMClient(api_key=os.environ["LLM_KEY"])
    """

    snapshot_fields: list[str] = []      # 직렬화할 원시값 필드
    rehydratable_fields: list[str] = []  # 재시작 시 재생성할 필드

    def extract_snapshot(self) -> dict:
        """직렬화 가능한 핵심 컨텍스트만 추출"""
        snapshot = {}
        for field in self.snapshot_fields:
            val = getattr(self, field, None)
            try:
                import json
                json.dumps(val)           # 직렬화 테스트
                snapshot[field] = val
            except (TypeError, ValueError):
                snapshot[field] = str(val)   # 폴백: 문자열 변환
        return snapshot

    def restore_from_snapshot(self, snapshot: dict):
        """원시값 복원 후 무거운 객체 재생성 (Rehydration)"""
        for field, value in snapshot.items():
            setattr(self, field, value)
        self.rehydrate(snapshot)          # 재생성 로직 호출

    def rehydrate(self, snapshot: dict):
        """override 포인트: 무거운 객체 재생성 로직"""
        pass   # 기본: 아무것도 하지 않음 (순수 원시값 에이전트용)
```

| 접근 방식 | 실현 가능성 | 비고 |
|-----------|------------|------|
| 전체 객체 직렬화 | **낮음** | Circular Reference, 보안 이슈 |
| `pickle` 직렬화 | 낮음 | 버전 의존, 보안 취약 |
| **State Rehydration (원시값 스냅샷)** | **높음** ✅ | 직렬화 가능 필드만, 무거운 객체는 재생성 |
| S3 오프로드 포인터 | 높음 | 대형 텍스트 컨텍스트는 S3에 저장, 포인터만 전달 |

**권고:** `StateRehydrationMixin` + `snapshot_fields` 명시. `rehydrate()` 메서드로 에이전트가 직접 복구 로직을 선언. 이 패턴은 직렬화 실패를 구조적으로 차단한다.

**과제 2: SFN Execution History 한도**

| 실행 모드 | 최대 이벤트 | 최대 실행 시간 |
|-----------|------------|---------------|
| Standard Workflow | 25,000 이벤트 | 1년 |
| Express Workflow | 100,000 이벤트 | 5분 |

각 세그먼트 = 약 5개 SFN 이벤트. Standard Workflow 기준 최대 **~5,000 세그먼트**. 실제 에이전트 루프 수백 회 기준으로 충분하다. 단, 수만 회 루프가 필요한 장기 작업은 **Nested Execution 패턴** (자식 SFN 실행 체인)으로 처리해야 한다.

**과제 3: 비동기 에이전트 프레임워크 호환성**

LangGraph, CrewAI, AutoGen 등은 모두 `async` 기반으로 동작한다. Bridge SDK가 이 비동기 경계를 올바르게 처리하지 못하면 교착 상태(Deadlock)가 발생할 수 있다.

**해결:** `asyncio.run()` 격리, 별도 이벤트 루프 스레드에서 Bridge I/O 처리.

**과제 4: 행동 인터셉트 시점**

에이전트가 LLM을 호출하거나 도구를 호출하기 "직전"에 브릿지를 호출해야 한다. 이를 위한 인터셉트 방법은 세 가지다:

| 방법 | 침투도 | 신뢰도 |
|------|--------|--------|
| **컨텍스트 매니저** (`with bridge.segment(...)`) | 중 — 코드 수정 필요 | 높음 ✅ |
| **클래스 상속** (`class MyAgent(AnalemmaAgent)`) | 낮 — 최소 수정 | 중간 |
| **monkey-patching** (LLM 호출 함수 교체) | 없음 | 낮음 (프레임워크 내부 변경에 취약) |

**권고:** 컨텍스트 매니저 방식을 Primary로, 상속 방식을 Secondary로 제공.

**과제 5: Commit 순서 보장 — Reordering Buffer**

멀티스레드 에이전트에서 여러 세그먼트가 동시 Propose될 경우 네트워크·스케줄링 지연에 의해 **순서가 뒤섞여 도착**할 수 있다. Merkle DAG의 `parent_manifest_id` 체인이 깨지고, 시퀀스 번호 검증이 실패한다.

**예시 시나리오:**

```
Thread A: loop_42 → PROPOSE 전송 (50ms 지연 네트워크)
Thread B: loop_43 → PROPOSE 전송 (5ms 지연 로컬)
커널 수신 순서: loop_43 → loop_42  ← 역전!
```

**해결 — VirtualSegmentManager의 Reordering Buffer:**

브릿지에 **시퀀스 번호(monotonic counter)** 를 추가하고, 커널 서버(VirtualSegmentManager)에 **Reordering Buffer**를 두어 out-of-order 도착 세그먼트를 `max_wait_ms` 동안 대기시킨 뒤 순서대로 처리한다. 타임아웃 도달 시 Fail-Open(그대로 처리)으로 가용성을 보장한다.

---

### 4.3 ABI 명세 고도화

제안된 ABI를 프로덕션 수준으로 고도화한다.

#### SEGMENT_PROPOSE (에이전트 → 커널)

```json
{
  "protocol_version": "1.0",
  "op": "SEGMENT_PROPOSE",
  "idempotency_key": "wf_123:loop_042:sha256_of_action",
  "segment_context": {
    "workflow_id": "wf_123",
    "parent_segment_id": "seg_041",
    "loop_index": 42,
    "segment_type": "TOOL_CALL",
    "sequence_number": 42,
    "ring_level": 3,
    "estimated_duration_ms": 3000,
    "is_optimistic_report": false
  },
  "payload": {
    "thought": "I need to read the AWS billing report.",
    "action": "s3_get_object",
    "action_params": {
      "bucket": "my-billing",
      "key": "report.json"
    }
  },
  "state_snapshot": {
    "serializable_fields": {
      "retry_count": 0,
      "found_data": false,
      "current_step": "data_collection"
    },
    "large_state_s3_pointer": "s3://bucket/snapshots/loop_042.json",
    "token_usage_total": 1540,
    "elapsed_ms": 12000
  }
}
```

추가된 필드 설명:
- `idempotency_key`: 네트워크 재전송 시 중복 처리 방지
- `segment_type`: `LLM_CALL | TOOL_CALL | MEMORY_UPDATE | FINAL`
- `sequence_number`: 순서 보장용 단조증가 카운터
- `ring_level`: 에이전트 Ring 레벨 (`0`=KERNEL, `1`=DRIVER, `2`=SERVICE, `3`=USER)
- `is_optimistic_report`: `false`=사전 제안(행동 실행 전), `true`=Optimistic 사후 보고. Ring 3에서는 항상 `false`로 강제
- `estimated_duration_ms`: HeartbeatSeconds 동적 조정에 사용
- `large_state_s3_pointer`: 직렬화 불가 객체의 S3 오프로드 포인터

#### SEGMENT_COMMIT (커널 → 에이전트)

```json
{
  "protocol_version": "1.0",
  "op": "SEGMENT_COMMIT",
  "idempotency_key": "wf_123:loop_042:sha256_of_action",
  "status": "APPROVED",
  "security_clearance": "RING_3",
  "checkpoint_id": "cp_sha256_merkle_root",
  "commands": {
    "action_override": null,
    "inject_recovery_instruction": null,
    "modify_action_params": null
  },
  "governance_feedback": {
    "warnings": [],
    "anomaly_score": 0.0,
    "article_violations": []
  }
}
```

`status` 가능 값:
- `APPROVED`: 행동 실행 허가
- `MODIFIED`: `commands.modify_action_params`로 파라미터 수정 후 실행
- `REJECTED`: 이번 루프 건너뜀, `governance_feedback`으로 에이전트 자기수정 유도
- `SOFT_ROLLBACK`: 이전 체크포인트로 되돌아가 재시도
- `SIGKILL`: 에이전트 즉시 종료

---

### 4.4 구체적 구현 방안

#### Python Bridge SDK

```python
# analemma_bridge/python/bridge.py
import asyncio
import hashlib
import json
import threading
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class SegmentResult:
    status: str
    checkpoint_id: str
    action_override: Optional[Dict] = None
    governance_feedback: Optional[Dict] = None

    @property
    def allowed(self) -> bool:
        return self.status in ("APPROVED", "MODIFIED")

    @property
    def should_kill(self) -> bool:
        return self.status == "SIGKILL"


class AnalemmaBridge:
    """
    Analemma Bridge SDK — Python 에이전트용

    사용법:
        bridge = AnalemmaBridge(workflow_id="wf_123", ring_level=3)

        # 컨텍스트 매니저 방식
        with bridge.segment(thought="...", action="s3_get_object", params={...}) as seg:
            if seg.allowed:
                result = execute_action(seg.action_params)
                seg.report_observation(result)
    """

    def __init__(self, workflow_id: str, ring_level: int = 3,
                 kernel_endpoint: str = "http://localhost:8765"):
        self.workflow_id = workflow_id
        self.ring_level = ring_level
        self.kernel_endpoint = kernel_endpoint
        self._loop_index = 0
        self._parent_segment_id: Optional[str] = None
        self._lock = threading.Lock()

    @contextmanager
    def segment(self, thought: str, action: str, params: Dict[str, Any],
                segment_type: str = "TOOL_CALL",
                state_snapshot: Optional[Dict] = None):
        """
        동기 컨텍스트 매니저 — 에이전트 행동 전후 커널 통신
        """
        with self._lock:
            self._loop_index += 1
            loop_index = self._loop_index

        proposal = self._build_proposal(
            thought, action, params, segment_type, loop_index, state_snapshot
        )
        commit = self._send_propose(proposal)
        seg = _SegmentHandle(commit, params)

        try:
            yield seg
            if seg._observation is not None:
                self._send_observation(commit.checkpoint_id, seg._observation)
        except Exception as e:
            self._send_failure(commit.checkpoint_id, str(e))
            raise
        finally:
            self._parent_segment_id = commit.checkpoint_id

    def _build_proposal(self, thought, action, params, segment_type,
                        loop_index, state_snapshot) -> Dict:
        content = f"{self.workflow_id}:loop_{loop_index}:{action}:{json.dumps(params, sort_keys=True)}"
        idempotency_key = hashlib.sha256(content.encode()).hexdigest()[:16]

        return {
            "protocol_version": "1.0",
            "op": "SEGMENT_PROPOSE",
            "idempotency_key": idempotency_key,
            "segment_context": {
                "workflow_id": self.workflow_id,
                "parent_segment_id": self._parent_segment_id,
                "loop_index": loop_index,
                "segment_type": segment_type,
                "sequence_number": loop_index,
            },
            "payload": {
                "thought": thought,
                "action": action,
                "action_params": params,
            },
            "state_snapshot": state_snapshot or {},
        }

    def _send_propose(self, proposal: Dict) -> SegmentResult:
        """커널에 SEGMENT_PROPOSE 전송 → SEGMENT_COMMIT 수신"""
        import requests
        try:
            resp = requests.post(
                f"{self.kernel_endpoint}/v1/segment/propose",
                json=proposal,
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            return SegmentResult(
                status=data["status"],
                checkpoint_id=data["checkpoint_id"],
                action_override=data.get("commands", {}).get("action_override"),
                governance_feedback=data.get("governance_feedback"),
            )
        except Exception as e:
            # 커널 불가 시 Fail-Open (APPROVED) — 가용성 우선
            # 프로덕션에서는 Fail-Closed로 변경 가능
            import logging
            logging.warning(f"[Bridge] Kernel unreachable, fail-open: {e}")
            return SegmentResult(status="APPROVED", checkpoint_id="local_only")

    def _send_observation(self, checkpoint_id: str, observation: Any):
        """행동 결과를 커널에 보고"""
        import requests
        try:
            requests.post(
                f"{self.kernel_endpoint}/v1/segment/observe",
                json={"checkpoint_id": checkpoint_id, "observation": str(observation)},
                timeout=5
            )
        except Exception:
            pass  # 관찰 보고 실패는 치명적이지 않음

    def _send_failure(self, checkpoint_id: str, error: str):
        import requests
        try:
            requests.post(
                f"{self.kernel_endpoint}/v1/segment/fail",
                json={"checkpoint_id": checkpoint_id, "error": error},
                timeout=5
            )
        except Exception:
            pass


class _SegmentHandle:
    """컨텍스트 매니저 내부에서 에이전트에 노출되는 핸들"""

    def __init__(self, commit: SegmentResult, original_params: Dict):
        self._commit = commit
        self._observation = None
        self.action_params = commit.action_override or original_params

    @property
    def allowed(self) -> bool:
        return self._commit.allowed

    @property
    def should_kill(self) -> bool:
        return self._commit.should_kill

    @property
    def checkpoint_id(self) -> str:
        return self._commit.checkpoint_id

    def report_observation(self, observation: Any):
        self._observation = observation
```

#### Optimistic Commitment Mode — 레이턴시 복리 효과 대응

> **핵심 문제:** 매 루프마다 PROPOSE → 커널 왕복 → COMMIT이 일어나면 에이전트 사고 속도가 커널 네트워크 레이턴시에 **직렬 종속**된다. 100ms × 1,000 루프 = 100초 추가 오버헤드.

**두 가지 실행 모드:**

| 모드 | 동작 | 레이턴시 | 보안 강도 |
|------|------|---------|-----------|
| **Strict Mode** (기본) | PROPOSE → 커널 승인 → 실행 | +100–500ms/루프 | 최상 (모든 행동 사전 검증) |
| **Optimistic Mode** | L1 로컬 검사 → 즉시 실행 → 결과 비동기 보고 | +5ms/루프 | 중 (L1 우회 불가, L2/L3는 사후 검증) |

**Optimistic Mode 동작 원리:**

```
[로컬 브릿지] L1 Heuristic Check (동기, ~1ms):
  - 금지 키워드 패턴 (INJECTION_PATTERNS 서브셋)
  - Capability Map 확인 (frozenset 룩업, ~0.01ms)
  - 인젝션 의심 시 → 즉시 BLOCK (로컬, 네트워크 왕복 없음)

L1 통과 시:
  - 에이전트가 즉시 행동 실행
  - 동시에, 비동기로 커널에 보고 (fire-and-forget)

[커널 서버] 비동기 수신 (사후):
  - L2 패턴 매칭 + L3 SemanticShield 검사
  - 위반 감지 시 → 해당 에이전트의 다음 루프에 SOFT_ROLLBACK 주입
  - DynamoDB에 위반 기록 (감사 추적 유지)
```

```python
# AnalemmaBridge에 mode 파라미터 추가

class AnalemmaBridge:
    def __init__(self, workflow_id: str, ring_level: int = 3,
                 kernel_endpoint: str = "http://localhost:8765",
                 mode: str = "strict"):  # "strict" | "optimistic"
        ...
        self.mode = mode
        self._l1_checker = LocalL1Checker()   # 로컬 규칙 엔진

    @contextmanager
    def segment(self, thought: str, action: str, params: dict, ...):
        if self.mode == "optimistic":
            # L1 로컬 검사만 수행 (동기)
            l1_result = self._l1_checker.check(thought, action)
            if not l1_result.allowed:
                raise SecurityViolation(f"L1 blocked: {l1_result.reason}")
            # 즉시 실행 핸들 반환
            seg = _OptimisticHandle(params)
            yield seg
            # 사후 비동기 보고
            self._async_report(thought, action, seg._observation)
        else:
            # Strict Mode: 기존 동기 PROPOSE/COMMIT 흐름
            ...


class LocalL1Checker:
    """
    브릿지 내장 경량 검사기 — 네트워크 없이 즉시 실행
    커널의 SemanticShield Stage 1+2 핵심 패턴 서브셋을 복사 탑재
    """
    _QUICK_PATTERNS = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"이전\s+지시사항\s+무시",
        r"system\s+prompt\s+reveal",
    ]

    def check(self, thought: str, action: str) -> "L1Result":
        import re
        text = f"{thought} {action}".lower()
        for pattern in self._QUICK_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return L1Result(allowed=False, reason=f"Pattern: {pattern}")
        # Capability Map 룩업 (frozenset, O(1))
        if not self._check_capability(action):
            return L1Result(allowed=False, reason=f"Capability denied: {action}")
        return L1Result(allowed=True, reason=None)
```

**모드 선택 가이드:**

| 상황 | 권고 모드 | 이유 |
|------|-----------|------|
| Ring 3 (외부 사용자 에이전트) | Strict | 비신뢰 입력, 사전 검증 필수 |
| Ring 2 (내부 서비스 에이전트) | Optimistic | 신뢰 환경, 레이턴시 최소화 |
| 실시간 대화형 에이전트 | Optimistic | 응답 속도 UX 우선 |
| 재무/의료 데이터 처리 에이전트 | Strict | 규제 준수, 사전 승인 필수 |

**위험 허용 범위:** Optimistic Mode에서 L2/L3 위반이 사후 감지되면 "이미 실행된 행동"을 되돌릴 수 없다. 이 트레이드오프를 고객에게 명시적으로 고지하고, Ring 3 에이전트에는 Optimistic Mode를 비활성화해야 한다.

#### Hybrid Interceptor — Optimistic Mode의 구조적 안전장치

> **핵심 문제:** Optimistic Mode는 L2/L3 검증을 사후에 비동기로 수행한다. 그러나 `rm -rf`, `DROP TABLE`, `filesystem_delete` 등 **파괴적 행동(Destructive Actions)** 은 실행 후 되돌릴 수 없다. 사후 SOFT_ROLLBACK을 받아도 이미 디스크 삭제·DB 드롭이 완료된 상태다.

**Hybrid Interceptor 동작 원리:**

```
[Optimistic Mode segment() 진입]
    ↓
[Hybrid Interceptor._is_destructive(action, thought, params)]
    ↓
 ┌──────────┐         ┌──────────────────────────────────────┐
 │ False    │         │ True                                  │
 │ (안전)   │         │ (파괴적)                               │
 ↓          │         ↓                                       │
L1 로컬 검사 │         effective_mode 강제 전환:               │
즉시 실행   │         "optimistic" → "strict"                 │
비동기 보고 │         ↓                                       │
            │         PROPOSE → 커널 동기 승인 → 실행        │
            └─────────────────────────────────────────────────┘
```

**파괴적 행동 분류 기준:**

| 카테고리 | 해당 action 목록 |
|----------|-----------------|
| 파일시스템 파괴 | `filesystem_write`, `filesystem_delete`, `rm`, `rmdir`, `truncate` |
| 프로세스 실행 | `shell_exec`, `subprocess_call` |
| 데이터베이스 파괴 | `database_delete`, `database_drop` |
| 클라우드 스토리지 삭제 | `s3_delete` |

추가로 `thought` 또는 파라미터 문자열에서 파괴적 패턴(`rm -rf`, `drop table`, `delete from`, `truncate`, `format disk`, `파일삭제` 등)이 감지될 경우에도 강제 Strict 전환된다.

```python
# Hybrid Interceptor 핵심 로직 (python_bridge.py)

DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset({
    "filesystem_write", "filesystem_delete", "shell_exec",
    "subprocess_call", "database_delete", "database_drop",
    "s3_delete", "rm", "rmdir", "truncate",
})

DESTRUCTIVE_PATTERNS: list[str] = [
    r"rm\s+-[rf]+",           # rm -rf, rm -r, rm -f
    r"drop\s+table",          # SQL DROP TABLE
    r"delete\s+from",         # SQL DELETE
    r"truncate\s+table",      # SQL TRUNCATE
    r"format\s+disk",         # 디스크 포맷
    r"파일\s*삭제",            # 한국어 파괴 패턴
    r"데이터베이스\s*삭제",
    r"전체\s*삭제",
    r"mkfs\.",                # 파일시스템 포맷
    r"dd\s+if=.+of=/dev/",   # 디스크 오버라이트
]

@contextmanager
def segment(self, thought: str, action: str, params: dict, ...):
    effective_mode = self.mode

    # Hybrid Interceptor: Optimistic Mode에서 파괴적 행동 감지 시 강제 Strict 전환
    if effective_mode == "optimistic" and self._is_destructive(action, thought, params):
        effective_mode = "strict"
        logger.warning(
            "[HybridInterceptor] Destructive action detected in Optimistic Mode. "
            "Forcing STRICT mode. action=%s, workflow=%s",
            action, self.workflow_id
        )

    if effective_mode == "optimistic":
        # 기존 Optimistic 흐름
        ...
    else:
        # Strict 흐름 (파괴적 행동 포함)
        ...
```

**설계 원칙:**
- Hybrid Interceptor는 **에이전트 코드 수정 없이** 적용된다 — `mode="optimistic"` 설정만으로 자동 활성화
- 파괴적 행동의 Strict 전환은 **로그 경고**를 남겨 감사 추적 유지
- DESTRUCTIVE_ACTIONS frozenset 룩업은 O(1) — 성능 영향 없음
- 패턴 미탐지 위험을 줄이기 위해 `thought` + `action` + `params` 전체를 스캔

#### TypeScript Bridge SDK (`@analemma/bridge-sdk`)

> **v1.3.0 업데이트:** 비동기 팩토리 패턴, `BridgeRingLevel` IntEnum, Policy Sync, Hybrid Interceptor 통합.

```typescript
// analemma-bridge/src/types.ts
export enum BridgeRingLevel { KERNEL=0, DRIVER=1, SERVICE=2, USER=3 }

export const CAPABILITY_MAP: Record<BridgeRingLevel, ReadonlySet<string>> = {
  [BridgeRingLevel.KERNEL]: new Set(["*"]),
  [BridgeRingLevel.DRIVER]: new Set(["filesystem_read","subprocess_call","network_limited",
    "database_write","config_read","network_read","database_query","cache_read",
    "event_publish","basic_query","read_only","s3_get_object","s3_put_object"]),
  [BridgeRingLevel.SERVICE]: new Set(["network_read","database_query","cache_read",
    "event_publish","basic_query","read_only","s3_get_object"]),
  [BridgeRingLevel.USER]: new Set(["basic_query","read_only"]),
};

export const DESTRUCTIVE_ACTIONS: ReadonlySet<string> = new Set([
  "filesystem_write","filesystem_delete","rm","rmdir","truncate",
  "shell_exec","subprocess_call","database_delete","database_drop",
  "s3_delete","s3_delete_objects","format","wipe",
]);

export const DESTRUCTIVE_PATTERNS: RegExp[] = [
  /rm\s+-[rf]+/i, /drop\s+table/i, /delete\s+from/i,
  /truncate\s+(?:table\s+)?\w+/i, /format\s+(?:disk|drive|c:)/i,
  /mkfs\./i, /dd\s+if=.+of=\/dev\//i,
  /파일\s*삭제/, /데이터베이스\s*(?:삭제|드롭)/, /전체\s*삭제/, /모두\s*삭제/,
];
```

```typescript
// analemma-bridge/src/bridge.ts  (핵심 요약)
import { BridgeRingLevel, DESTRUCTIVE_ACTIONS, DESTRUCTIVE_PATTERNS } from "./types";
import { LocalL1Checker } from "./l1_checker";

export interface BridgeConfig {
  workflowId: string;
  ringLevel?: BridgeRingLevel;
  kernelEndpoint?: string;   // 기본: ANALEMMA_KERNEL_ENDPOINT env | "http://localhost:8765"
  mode?: "strict" | "optimistic";
  syncPolicy?: boolean;      // true 시 create() 내에서 /v1/policy/sync 자동 호출
}

export class AnalemmaBridge {
  /** 비동기 팩토리 — syncPolicy=true이면 PolicySync 후 인스턴스 반환 */
  static async create(config: BridgeConfig): Promise<AnalemmaBridge> {
    const resolved = {
      ringLevel: config.ringLevel ?? BridgeRingLevel.USER,
      kernelEndpoint: config.kernelEndpoint
        ?? process.env.ANALEMMA_KERNEL_ENDPOINT ?? "http://localhost:8765",
      mode: config.mode ?? "strict",
      syncPolicy: config.syncPolicy
        ?? (process.env.ANALEMMA_SYNC_POLICY ?? "").trim() === "1",
    };
    const l1Checker = new LocalL1Checker();
    if (resolved.syncPolicy) {
      const synced = await l1Checker.syncFromKernel(resolved.kernelEndpoint);
      if (synced)
        console.info(`[AnalemmaBridge] Policy synced. version=${l1Checker.policyVersion}`);
      else
        console.warn("[AnalemmaBridge] Policy sync failed. Using local defaults.");
    }
    return new AnalemmaBridge(resolved, l1Checker);
  }

  async segment<T>(options: SegmentOptions<T>): Promise<SegmentOutcome<T>> {
    const loopIndex = ++this.loopIndex;

    // Hybrid Interceptor: DESTRUCTIVE_ACTIONS frozenset + 패턴 스캔
    let effectiveMode = this.mode;
    if (effectiveMode === "optimistic" && this.isDestructive(options)) {
      effectiveMode = "strict";
      console.warn(
        `[HybridInterceptor] Destructive → forcing STRICT. action=${options.action}`
      );
    }

    return effectiveMode === "optimistic"
      ? this.optimisticSegment(options, loopIndex)
      : this.strictSegment(options, loopIndex);
  }

  private async strictSegment<T>(options, loopIndex): Promise<SegmentOutcome<T>> {
    const commit = await this.sendPropose(this.buildProposal(options, loopIndex, false));
    const recoveryInstruction = commit.commands.inject_recovery_instruction;

    if (commit.status === "SIGKILL")
      throw new SecurityViolation(`SIGKILL at loop ${loopIndex}. Recovery: ${recoveryInstruction}`);

    if (!["APPROVED","MODIFIED"].includes(commit.status)) {
      await this.sendObservation(commit.checkpoint_id, null, "SKIPPED");
      return { result: null, commit, recoveryInstruction };
    }

    const result = await options.execute(commit.commands.action_override ?? options.params);
    await this.sendObservation(commit.checkpoint_id, result, "SUCCESS");
    this.parentSegmentId = commit.checkpoint_id;
    return { result, commit, recoveryInstruction };
  }

  private async optimisticSegment<T>(options, loopIndex): Promise<SegmentOutcome<T>> {
    // L1 로컬 검사 (ZWS 정규화 + Homoglyph 변환 포함, ~1ms)
    const l1 = this.l1Checker.check(options.thought, options.action, this.ringLevel, options.params);
    if (!l1.allowed) throw new SecurityViolation(`[L1 Blocked] ${l1.reason}`);

    const result = await options.execute(options.params);

    // 사후 비동기 커널 보고 (fire-and-forget)
    this.asyncReport(options, loopIndex, result).catch((e) =>
      console.debug(`[AnalemmaBridge] Async report failed (non-critical): ${e}`)
    );
    return { result, commit: FAKE_APPROVED_COMMIT, recoveryInstruction: null };
  }

  private isDestructive(options: SegmentOptions<unknown>): boolean {
    if (DESTRUCTIVE_ACTIONS.has(options.action.toLowerCase())) return true;
    const text = options.thought + " " + JSON.stringify(options.params ?? {});
    return DESTRUCTIVE_PATTERNS.some((p) => p.test(text));
  }
}
```

```typescript
// analemma-bridge/src/l1_checker.ts  (ZWS + Homoglyph 정규화 포함)
const ZW_REGEX = /[\u200b\u200c\u200d\ufeff\u202e\u202d]/g;
const HOMOGLYPH_MAP: Record<string, string> = {
  "\u0430":"a", "\u0435":"e", "\u043e":"o",
  "\u0440":"p", "\u0441":"c", "\u0445":"x",
  "\u03b1":"a", "\u03bf":"o",
};
const HOMOGLYPH_REGEX = new RegExp(Object.keys(HOMOGLYPH_MAP).join("|"), "g");

function normalize(text: string): string {
  return text
    .replace(ZW_REGEX, "")           // Zero-Width + RTL Override 제거
    .normalize("NFKC")               // Unicode 정규형
    .replace(HOMOGLYPH_REGEX, (ch) => HOMOGLYPH_MAP[ch] ?? ch);  // 키릴 homoglyph 치환
}

export class LocalL1Checker {
  async syncFromKernel(kernelEndpoint: string): Promise<boolean> {
    // GET /v1/policy/sync → version 비교 → inject 패턴·CapabilityMap 업데이트
    const resp = await axios.get(`${kernelEndpoint}/v1/policy/sync`, { timeout: 5_000 });
    if (resp.data.version === this._policyVersion) return true;
    this.injectPatterns(resp.data.injection_patterns, resp.data.capability_map, resp.data.version);
    return true;
  }
}
```

**모듈 패키지 구조:**

```
analemma-bridge/
├── package.json          # @analemma/bridge-sdk, axios peer dep
├── tsconfig.json         # ES2020, CommonJS, strict
└── src/
    ├── types.ts          # BridgeRingLevel, CAPABILITY_MAP, DESTRUCTIVE_ACTIONS, ABI types
    ├── l1_checker.ts     # LocalL1Checker (normalize + Pattern + CapMap + PolicySync)
    ├── bridge.ts         # AnalemmaBridge (Strict / Optimistic / Hybrid Interceptor)
    └── index.ts          # 공개 API re-export
```

---

### 4.5 커널 연동: VirtualSegmentManager

브릿지에서 전송된 `SEGMENT_PROPOSE`를 수신하여 Analemma 커널의 기존 거버넌스 파이프라인과 연동하는 서버 컴포넌트. (v1.3.0)

```
SEGMENT_PROPOSE (HTTP POST /v1/segment/propose)
  │
  ├─ [pre] Ring 3 is_optimistic_report 강제 차단
  │    → ring_level ≥ 3 이고 is_optimistic_report=true → false로 강제 교정 + WARNING 로그
  │
  ├─ [0] ReorderingBuffer.wait_for_turn(workflow_id, sequence_number)
  │    → out-of-order 도착 시 max_wait_ms=200ms 대기 후 순서 보장
  │    → 타임아웃 시 Fail-Open (순서 무시하고 처리)
  │
  ├─ [1] SemanticShield.inspect(payload.thought)
  │    → INJECTION 감지 시 → SIGKILL + Recovery Instruction 반환
  │
  ├─ [2] validate_capability(ring_level, payload.action)
  │    → 미허가 도구 → REJECTED + "사용 가능 대안 도구 목록" Recovery Instruction
  │    → is_optimistic_report=true이면 SOFT_ROLLBACK
  │
  ├─ [3] BudgetWatchdog.check(state_snapshot.token_usage_total)
  │    → 예산 초과 → SOFT_ROLLBACK + "FINAL 세그먼트로 종료" Recovery Instruction
  │
  ├─ [4] GovernanceEngine.verify(thought + action_params)
  │    → CRITICAL Article 위반 → SIGKILL + "즉시 종료" Recovery Instruction
  │    → MEDIUM Article 위반 → REJECTED / SOFT_ROLLBACK + "수정 후 재시도" 안내
  │
  ├─ [5] _AuditRegistry.set(checkpoint_id, _ProposedRecord)
  │    → Redis setex(TTL=1h) / in-memory fallback
  │    → segment_type == "FINAL" 시 ReorderingBuffer 자동 정리
  │
  └─ SEGMENT_COMMIT { status: APPROVED, checkpoint_id,
                       inject_recovery_instruction } 반환

POST /v1/segment/observe  → Observation Audit Trail (정합성 검사)
GET  /v1/policy/sync      → LocalL1Checker 동기화용 패턴·CapabilityMap 제공
DELETE /v1/workflow/{id}  → 수동 Reordering Buffer 정리
GET  /v1/health           → 전체 컴포넌트 상태 + 정책 버전
```

**v1.3 주요 변경사항 요약:**

| 항목 | v1.1 (이전) | v1.3 (현재) |
|------|-------------|-------------|
| 정책 상수 관리 | 인라인 중복 정의 | `shared_policy.py` 단일 출처 임포트 |
| Audit Registry | 인메모리 dict | `_AuditRegistry`: Redis TTL=1h / 인메모리 fallback |
| is_optimistic 보호 | 없음 | Ring 3에서 `true` → `false` 강제 차단 |
| Recovery Instruction | 빈 `null` 반환 | 거부 사유별 자연어 가이드 (`_build_recovery_instruction`) |
| Observation 검증 | 없음 | proposed vs actual action 정합성 검사 |
| Policy Sync | 없음 | `GET /v1/policy/sync` 엔드포인트 |
| FINAL 정리 | 수동 DELETE만 | segment_type="FINAL" 수신 시 자동 정리 |

**Reordering Buffer 상세 설계:**

```
워크플로 wf_123에서 멀티스레드 제안:
  Thread A: seq=42 (50ms 네트워크 지연)
  Thread B: seq=43 (2ms 로컬 처리)

서버 수신 순서: seq=43 → seq=42

ReorderingBuffer.wait_for_turn("wf_123", seq=43):
  expected["wf_123"] = 42  (아직 42를 못 받음)
  → 최대 200ms 대기 (polling 10ms 간격)
  → seq=42가 도착하면 즉시 seq=42 처리 → seq=43 처리
  → 200ms 타임아웃 도달 시: Fail-Open, seq=43 즉시 처리 + 경고 로그

설계 원칙:
  - per-workflow_id 독립 카운터 (서로 다른 워크플로는 순서 독립)
  - asyncio.Lock으로 동시성 보장
  - max_wait_ms=200 기본값 (조정 가능)
  - Fail-Open: 타임아웃 후에도 가용성 우선
```

```python
# backend/src/bridge/virtual_segment_manager.py  — v1.3.0 핵심 스니펫
#
# 전체 구현: backend/src/bridge/virtual_segment_manager.py
# 실행: uvicorn backend.src.bridge.virtual_segment_manager:app --host 0.0.0.0 --port 8765

# ── 공유 정책 상수 (Single Source of Truth) ─────────────────────────────────
# 이전 v1.1에서 _ALLOWED_TOOLS_BY_RING / _POLICY_INJECTION_PATTERNS를
# 인라인 중복 정의했던 방식을 제거. shared_policy.py 단일 출처로 통합.
from .shared_policy import (
    CAPABILITY_MAP_INT as _ALLOWED_TOOLS_BY_RING,   # int-keyed dict
    RING_NAMES as _RING_NAMES,
    INJECTION_PATTERNS as _POLICY_INJECTION_PATTERNS,
)
_POLICY_VERSION: str = hashlib.md5(
    "|".join(sorted(_POLICY_INJECTION_PATTERNS)).encode()
).hexdigest()[:8]


# ── Audit Registry — Redis TTL / in-memory fallback ─────────────────────────
class _AuditRegistry:
    """
    Audit Registry 백엔드 추상화.
    ANALEMMA_REDIS_URL 설정 시 Redis setex(TTL=1h) 사용 (서버 재시작 안전).
    미설정 시 in-memory dict (개발 환경, 단일 프로세스).
    """
    def init(self) -> None:
        if not self._redis_url:
            return
        try:
            import redis as _redis_lib
            self._redis = _redis_lib.from_url(self._redis_url, decode_responses=True)
            self._redis.ping()
            self._use_redis = True
        except Exception as exc:
            logger.warning("[AuditRegistry] Redis unavailable, falling back to in-memory: %s", exc)

    def set(self, key: str, record: _ProposedRecord) -> None:
        if self._use_redis:
            self._redis.setex(f"audit:{key}", self._ttl, json.dumps(dataclasses.asdict(record)))
            return
        if len(self._memory) >= _REGISTRY_MAX_SIZE:
            del self._memory[next(iter(self._memory))]
        self._memory[key] = record

    def pop(self, key: str) -> Optional[_ProposedRecord]:
        if self._use_redis:
            data = self._redis.get(f"audit:{key}")
            if data:
                self._redis.delete(f"audit:{key}")
                return _ProposedRecord(**json.loads(data))
            return None
        return self._memory.pop(key, None)


# ── propose_segment 핵심 로직 (v1.3 추가사항) ───────────────────────────────

@app.post("/v1/segment/propose")
async def propose_segment(req: SegmentProposalRequest):
    ctx = req.segment_context
    ring_level = ctx.ring_level
    is_optimistic = ctx.is_optimistic_report

    # ① Ring 3(USER) is_optimistic_report 강제 차단
    #    비신뢰 에이전트가 is_optimistic_report=True로 사전 검증을 우회하는 것을 방지
    if ring_level >= 3 and is_optimistic:
        is_optimistic = False
        logger.warning(
            "[VirtualSegmentManager] Ring 3 agent attempted is_optimistic_report=True. "
            "Forced to False. workflow=%s", ctx.workflow_id,
        )

    # ... [0]–[4] 파이프라인 (SemanticShield → Capability → Budget → Governance) ...

    # ② FINAL 세그먼트 → ReorderingBuffer 자동 정리 (메모리 누수 방지)
    if ctx.segment_type == "FINAL":
        _reorder_buffer.reset(ctx.workflow_id)
        logger.info("[VirtualSegmentManager] FINAL received. Buffer cleaned: %s", ctx.workflow_id)

    # ③ Audit Registry 등록 (Observation 정합성 검사용)
    _registry.set(checkpoint_id, _ProposedRecord(
        workflow_id=ctx.workflow_id, action=action, action_params=action_params,
        thought=thought, ring_level=ring_level, loop_index=ctx.loop_index,
    ))

    # ④ Recovery Instruction 생성 — 거부 사유별 자연어 가이드
    #    capability 거부 → "사용 가능한 대안 도구 목록 제시"
    #    injection 탐지 → "시스템 프롬프트 조작 시도 금지 안내"
    #    constitutional_critical → "즉시 종료 지시"
    #    budget → "FINAL 세그먼트로 우아한 종료 유도"
    return _commit("APPROVED", checkpoint_id)


# ── Policy Sync 엔드포인트 (/v1/policy/sync) ─────────────────────────────────
# LocalL1Checker.syncFromKernel()이 초기화 시 이 엔드포인트를 호출하여
# 로컬 패턴을 커널과 동기화한다. version 필드로 캐시 유효성 확인 가능.
@app.get("/v1/policy/sync")
async def get_policy_sync():
    return {
        "version": _POLICY_VERSION,
        "injection_patterns": _POLICY_INJECTION_PATTERNS,  # shared_policy.INJECTION_PATTERNS
        "capability_map": {
            str(ring): sorted(tools)
            for ring, tools in _ALLOWED_TOOLS_BY_RING.items()
            if ring != 0  # Ring 0 무제한 — 클라이언트에 노출 불필요
        },
        "audit_registry_backend": _registry.backend_name,
    }


# ── Observation Audit Trail (/v1/segment/observe) ────────────────────────────
# 보고된 actual_action vs. 제안된 proposed_action 정합성 검사.
# 불일치 시 CONSISTENCY_MISMATCH 경고 로그 → 향후 Ring 강제 격하 트리거.
@app.post("/v1/segment/observe")
async def observe_segment(body: Dict[str, Any]):
    proposed = _registry.pop(body.get("checkpoint_id", ""))
    if proposed and body.get("action") and body["action"] != proposed.action:
        logger.warning(
            "[AuditTrail] CONSISTENCY_MISMATCH checkpoint=%s proposed=%s actual=%s",
            body["checkpoint_id"], proposed.action, body["action"],
        )
```

---

### 4.6 리스크 및 완화

| 리스크 | 심각도 | 완화 방안 |
|--------|--------|-----------|
| 커널 서버 불가 시 에이전트 멈춤 | 상 | Fail-Open 정책 + 로컬 캐시 Commit 큐 |
| 상태 직렬화 실패 (복잡 객체) | 중 | `StateRehydrationMixin._safe_serialize()` — max_depth + circular reference 감지 |
| SFN History 한도 초과 (25,000 이벤트) | 중 | Nested Execution: 1,000 루프마다 자식 SFN 실행 생성 |
| 브릿지 레이턴시 오버헤드 (+100ms/루프) | 하 | 로컬 gRPC 또는 Unix Socket으로 HTTP → gRPC 전환 시 ~10ms로 감소 |
| 비동기 프레임워크 교착 상태 | 중 | 별도 이벤트 루프 스레드에서 Bridge I/O 격리 |
| 행동 순서 보장 (멀티스레드 에이전트) | 상 | `ReorderingBuffer` + `sequence_number` 단조증가 카운터 |
| Audit Registry 서버 재시작 시 휘발 | 중 | `_AuditRegistry` Redis TTL=1h 백엔드; 미연결 시 in-memory + 경고 |
| 정책 상수 중복 관리 오류 | 중 | `shared_policy.py` 단일 출처 — VSM·LocalL1Checker 모두 임포트 |
| 인코딩 우회 인젝션 (Homoglyph·ZWS) | 상 | `LocalL1Checker.normalize()` — ZWS 제거 + NFKC + Homoglyph 치환 후 패턴 검사 |
| Optimistic Mode Ring 3 사전검증 우회 시도 | 상 | VSM에서 `ring_level ≥ 3 and is_optimistic_report=True` 서버 강제 차단 |

---

## 5. 통합 아키텍처 — 두 계획의 결합

Plan A와 Plan B는 독립적으로 구현 가능하지만, 함께 배포될 때 시너지가 극대화된다.

```
┌─────────────────────────────────────────────────────────────────┐
│  고객사 내부 네트워크 (On-Premises / Private Cloud)              │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  자율형 에이전트 (Python / TypeScript)                   │    │
│  │                                                          │    │
│  │  bridge.segment(thought, action, params)                 │    │
│  │         ↓                                                │    │
│  │  VirtualSegmentManager (FastAPI, 로컬 실행)              │    │
│  │    [SemanticShield] [CapabilityMap] [GovernanceEngine]   │    │
│  │         ↓                                                │    │
│  │  LocalAgentWorker                                        │    │
│  │    open_state_bag → [실행] → seal_state_bag              │    │
│  │         ↓ (S3 포인터만 외부 전송)                         │    │
│  └─────────────────────────────────────────────────────────┘    │
│                         │                                        │
│                  AWS IAM 자격증명 (아웃바운드 HTTPS만)            │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│  AWS (Control Plane)                                             │
│                                                                  │
│  SFN Activity Task Token ← SendTaskSuccess(s3_pointer)          │
│         ↓                                                        │
│  Governor Lambda (Ring 1)                                        │
│    [governor_runner] [GovernanceEngine] [SemanticShield]         │
│         ↓                                                        │
│  DynamoDB Governance Audit Log (90일 TTL)                        │
│  Merkle DAG (S3 + DynamoDB)                                      │
│  CloudWatch Dashboard                                            │
└─────────────────────────────────────────────────────────────────┘
```

**데이터 흐름 원칙 (Zero-Knowledge 업데이트):**

| 데이터 종류 | 위치 | 이동 방향 |
|-------------|------|-----------|
| 원시 에이전트 출력 (민감 데이터) | 고객사 내부 스토리지 (MinIO/온프레미스) | 외부 전송 안 함 |
| 컨텐츠 해시 (SHA-256) | AWS SFN → DynamoDB | 로컬 → AWS (해시만) |
| 거버넌스 결과 (APPROVED/REJECTED) | AWS DynamoDB | AWS 내부 |
| 메타데이터 (토큰 수, 레이턴시) | AWS CloudWatch | AWS 내부 |

### ⚠️ 결정론의 한계 — Hallucination Loop 위험

> **핵심 문제:** 커널이 REJECTED를 반환했을 때, 에이전트가 "왜 거절당했지?"를 이해하지 못하면 유사한 행동을 반복하는 **Hallucination Loop**에 빠진다. 결정론적 커널이 확률적 에이전트의 내부 상태를 제어하지 못하는 근본적 긴장이다.

**Hallucination Loop 시나리오:**

```
루프 42: action=filesystem_write → 커널 REJECTED (Ring 3 비허가)
루프 43: action=write_file_system → 커널 REJECTED (동일 이유)
루프 44: action=save_to_disk     → 커널 REJECTED (동일 이유)
루프 45: action=filesystem_write → ...  ← 무한 반복
```

**해결: Recovery Instruction 필수화**

`governance_feedback.inject_recovery_instruction`을 단순 경고 필드가 아닌 **에이전트 자기수정의 핵심 메커니즘**으로 격상한다.

REJECTED 시 커널이 반환하는 `SEGMENT_COMMIT` 예시:

```json
{
  "status": "REJECTED",
  "checkpoint_id": "cp_abc123",
  "commands": {
    "action_override": null,
    "inject_recovery_instruction": "Action 'filesystem_write' is not permitted at Ring 3. Use 'basic_query' or escalate to Ring 2 via human approval. Available actions: [basic_query, read_only]."
  },
  "governance_feedback": {
    "warnings": ["Capability denied: filesystem_write at RING_3"],
    "anomaly_score": 0.2,
    "article_violations": [],
    "suggested_alternatives": ["basic_query", "read_only"],
    "escalation_path": "Request HITP human approval to upgrade ring level"
  }
}
```

**브릿지 SDK에서 Recovery Instruction 자동 주입:**

```python
@contextmanager
def segment(self, thought: str, action: str, params: dict, ...):
    ...
    commit = self._send_propose(proposal)
    seg = _SegmentHandle(commit, params)

    # Recovery Instruction이 있으면 에이전트의 다음 thought에 자동 주입
    if commit.recovery_instruction:
        self._inject_to_next_thought(commit.recovery_instruction)

    yield seg
    ...

def _inject_to_next_thought(self, instruction: str):
    """
    에이전트의 다음 루프 시작 전 thought에 커널 지침을 prefix로 삽입.
    에이전트가 왜 거절당했는지 이해하고 대안을 시도하도록 유도.
    """
    self._pending_system_injection = (
        f"[KERNEL GOVERNANCE] {instruction}\n"
        f"This instruction has priority over your original plan.\n"
    )
```

**VirtualSegmentManager에서 Recovery Instruction 생성 로직:**

```python
def _build_recovery_instruction(
    violation_type: str, action: str, ring_level: int
) -> str:
    """
    위반 유형별 구체적인 복구 지침 생성.
    추상적 거부 대신 에이전트가 즉시 실행 가능한 대안 제시.
    """
    instructions = {
        "CAPABILITY_DENIED": (
            f"Action '{action}' requires Ring {ring_level - 1} or higher. "
            f"Available at your current ring: {_get_allowed_actions(ring_level)}. "
            f"To request elevated access, use action 'request_human_approval'."
        ),
        "CONSTITUTIONAL_VIOLATION": (
            f"Your output violated Article 6 (PII in text). "
            f"Remove personal identifiers (email, phone, SSN) before proceeding. "
            f"Use masking: replace 'john@example.com' with '[REDACTED_EMAIL]'."
        ),
        "BUDGET_EXCEEDED": (
            f"Token budget exhausted. Summarize progress and request human approval "
            f"to continue. Current usage: {token_usage} / {max_tokens} tokens."
        ),
    }
    return instructions.get(violation_type, f"Action '{action}' was rejected. Try an alternative approach.")
```

**Loop Guard — 동일 행동 반복 차단:**

Hallucination Loop를 구조적으로 차단하기 위해 VirtualSegmentManager에 반복 탐지를 추가한다.

```python
# 동일 (action, params) 조합이 N회 연속 REJECTED → SIGKILL
LOOP_GUARD_THRESHOLD = 3

if self._consecutive_rejections.get(idempotency_base) >= LOOP_GUARD_THRESHOLD:
    return _commit("SIGKILL", "local_only",
                   warnings=["Hallucination loop detected: same action rejected 3 times"],
                   recovery="Agent is stuck in a rejection loop. Human review required.")
```

---

## 6. 구현 로드맵

### Phase 0 — 기반 (2주)

**목표:** SFN Activity 등록 + 로컬 워커 기본 동작 확인

| 작업 | 난이도 | 예상 기간 |
|------|--------|-----------|
| SFN Activity ARN 생성 및 ASL 수정 | 하 | 1일 |
| `LocalAgentWorker` 기본 구현 | 하 | 2일 |
| `open_state_bag` / `seal_state_bag` 로컬 이식 확인 | 하 | 1일 |
| Docker 이미지 빌드 + 로컬 테스트 | 하 | 2일 |
| **목표 검증:** SFN 콘솔에서 로컬 워커가 Activity Task를 가져와 완료하는 것 확인 | — | — |

### Phase 1 — Plan A 완성 (4주)

**목표:** B2B 배포 가능한 로컬 에이전트 패키징

| 작업 | 난이도 | 예상 기간 |
|------|--------|-----------|
| S3 결과 오프로드 + 포인터 반환 | 하 | 1일 |
| Heartbeat 스레드 안정화 | 중 | 2일 |
| pip 패키지 (`analemma-agent`) 구성 | 중 | 3일 |
| IAM Role 최소 권한 정의 문서화 | 하 | 1일 |
| 자동 업데이트 메커니즘 (버전 체크 API) | 중 | 3일 |
| 고객사 배포 가이드 작성 | 하 | 2일 |
| **목표 검증:** 고객사 VPN 환경에서 방화벽 인바운드 없이 동작 | — | — |

### Phase 2 — Plan B 기반 (6주)

**목표:** Bridge SDK + VirtualSegmentManager 초기 버전

| 작업 | 난이도 | 예상 기간 |
|------|--------|-----------|
| **🔬 Moltbot 브릿지 연동 실험** (Phase 2 첫 작업) | 상 | 3일 |
| `VirtualSegmentManager` FastAPI 서버 | 중 | 4일 |
| Python Bridge SDK (`AnalemmaBridge`) | 중 | 3일 |
| TypeScript Bridge SDK | 중 | 4일 |
| `StateRehydrationMixin` + `LocalL1Checker` 구현 | 중 | 3일 |
| SemanticShield / CapabilityMap 연동 | 하 (기존 코드 재사용) | 2일 |
| GovernanceEngine 연동 + Recovery Instruction 생성 | 중 | 3일 |
| Loop Guard (Hallucination Loop 탐지) | 중 | 2일 |
| **목표 검증:** Moltbot 루프에서 SIGKILL이 즉시 종료하고, REJECTED 시 Recovery Instruction이 다음 thought에 주입되는 것 확인 | — | — |

> **왜 Moltbot 먼저인가:** Moltbot은 현재 TypeScript 기반으로 동작하며, 직렬화 문제(Memory Ghosting)와 비동기 프레임워크 호환성 문제가 가장 먼저 표면화될 대상이다. 이론과 실제의 간극을 조기에 발견하면 Bridge SDK 설계를 현실에 맞게 수정할 수 있다. LangChain 통합 전에 Moltbot으로 전체 흐름을 검증한다.

### Phase 3 — 고도화 (8주+)

| 작업 | 비고 |
|------|------|
| Nested Execution 패턴 (루프 1,000회 이상 대응) | SFN Express Workflow 활용 |
| gRPC Bridge (HTTP → gRPC, 레이턴시 10x 감소) | 선택적 |
| 자동 `@snapshot_fields` 추론 (AST 분석) | 편의성 향상 |
| 대시보드: 루프별 anomaly_score 시각화 | CloudWatch Custom Metrics |
| CrewAI / AutoGen / LangGraph 전용 어댑터 | 생태계 확장 |

---

## 7. 결론 및 권고사항

### 종합 평가

| 기준 | Plan A | Plan B |
|------|--------|--------|
| 기술 리스크 | **낮음** (AWS 검증 패턴) | 중간 (직렬화·순서 보장 난이도) |
| 구현 기간 | **6주 (Phase 0+1)** | 14주 (Phase 0+1+2) |
| 수익화 시점 | **빠름** (Phase 1 완료 후 즉시) | 느림 (에코시스템 채택 필요) |
| 차별화 강도 | 중간 (유사 사례 존재) | **높음** (에이전트 거버넌스 시장 독보적) |
| 기존 Analemma 코드 활용 | **높음** | 중간 |

### 전략적 포지셔닝

**Plan A는 '수익'을 위해, Plan B는 '권위'를 위해.**

Plan A를 빠르게 상용화하여 캐시플로우를 만들고, Plan B를 통해 "Analemma OS가 없으면 자율 에이전트는 통제 불가능하다"는 기술적 담론을 형성한다. 두 계획은 시장에서 각각 다른 역할을 수행한다:

| | Plan A | Plan B |
|---|---|---|
| **시장에서의 역할** | 즉각적 수익원 (제품 판매) | 기술적 권위 형성 (표준 선점) |
| **고객 설득 논리** | "데이터 유출 없이 AI 거버넌스 가능" | "에이전트는 커널 없이 통제 불가" |
| **경쟁 차단 효과** | 중간 (유사 제품 존재) | 높음 (에이전트 거버넌스 레이어는 독보적) |

### 권고

1. **즉시 착수:** Plan A (Phase 0 → Phase 1). 6주 내 B2B 판매 가능한 제품 완성.
2. **Moltbot 조기 실험:** Phase 2 첫 번째 작업으로 Moltbot 브릿지 연동. 직렬화·비동기 문제의 실제 발생 지점을 조기에 확인.
3. **Fail-Open 정책:** 브릿지 서버 장애 시 에이전트를 멈추지 않도록 Fail-Open 기본값 유지. Ring 3 에이전트에는 Strict Mode 강제.
4. **Optimistic Mode는 Ring 2 한정:** Ring 3 에이전트에 Optimistic Mode를 허용하면 사후 감지 위반이 이미 실행된 행동에 대해 롤백 불가. Ring 3는 항상 Strict.
5. **gRPC 전환은 측정 후:** HTTP REST로 먼저 동작을 검증하고, 실제 레이턴시 측정값이 허용치를 초과할 때 gRPC로 전환.
6. **Audit over Score:** Trust Score(삭제된 `trust_score_manager.py`)의 자리에 **Immutable Audit Trail**을 배치한다. B2B 고객은 "에이전트의 신뢰도 점수"보다 "누가, 언제, 어떤 행동을, 왜 했는가"의 불변 증거를 원한다. DynamoDB Governance Audit Log를 이 증거의 단일 진실 공급원으로 강화한다.

### Audit over Score — Immutable Audit Trail 설계

`GovernanceAuditLog` (DynamoDB, 90일 TTL)를 모든 거버넌스 결정의 **법적 증거 수준 기록**으로 확장한다:

| 필드 | 내용 | 용도 |
|------|------|------|
| `agent_id` | 에이전트 식별자 | 주체 추적 |
| `action` | 시도한 도구/행동 | 행동 기록 |
| `decision` | APPROVED / REJECTED / SIGKILL | 결정 기록 |
| `decision_reason` | Article 위반, 권한 부족 등 | **왜** 기록 |
| `recovery_instruction` | 에이전트에 주입된 복구 지침 | 자기수정 증거 |
| `content_hash` | 출력물 SHA-256 | 무결성 증명 |
| `ring_level` | 실행 시 권한 레벨 | 권한 범위 기록 |
| `timestamp` | ISO-8601 UTC | 시간 기록 |
| `immutable` | `true` (DynamoDB 조건부 쓰기) | 소급 수정 불가 |

이 로그를 기반으로 규제 기관 감사 시 "특정 에이전트가 특정 시각에 특정 데이터에 접근을 시도했고, 커널이 이를 거부했으며, 에이전트는 대안 행동을 취했다"를 증명할 수 있다. 이는 Trust Score보다 B2B 규제 환경에서 10배 강력한 가치를 지닌다.

### 핵심 기술 자산 재사용 요약

Analemma OS의 기존 구현체가 두 계획 모두에서 직접 재사용된다.

| 기존 구현체 | Plan A 재사용 | Plan B 재사용 |
|-------------|---------------|---------------|
| `kernel_protocol.py` (open/seal) | ✅ 로컬 실행 계약 | — |
| `universal_sync_core.py` | ✅ 상태 병합 | — |
| `SemanticShield` | — | ✅ Thought 인젝션 검사 |
| `GovernanceEngine` | — | ✅ Constitutional 검증 |
| `CAPABILITY_MAP` | — | ✅ 행동 권한 게이트 |
| `RedisCircuitBreaker` | ✅ 워커 간 CB 공유 | ✅ 루프 재시도 제한 |
| `MerkleDAG` (state_versioning) | ✅ 로컬 결과 체크포인트 | ✅ 루프 체크포인트 |

---

*본 문서는 Analemma OS 아키텍처 설계 결정의 근거 자료로 사용된다.*
*구현 중 발견된 기술적 차이는 이 문서를 직접 갱신한다.*
