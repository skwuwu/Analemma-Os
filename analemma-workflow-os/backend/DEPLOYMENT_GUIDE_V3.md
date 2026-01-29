# 🚀 Smart StateBag v3 Deployment Guide

## 📋 배포 전 체크리스트

### 1. 변경 사항 요약
- ✅ `aws_step_functions_v3.json` - 43 states (레거시 63 states에서 31.7% 감소)
- ✅ `aws_step_functions_distributed_v3.json` - 46 states (Race-Condition-Free 병렬 처리)
- ✅ `state_data_manager.py` - 8개 신규 action 추가 (기존 호환성 유지)
- ✅ `template.yaml` - v3 ASL 참조로 업데이트

### 2. 호환성 보장
- ✅ 기존 `update_and_compress` action 완전 보존
- ✅ 타임라인 기능 100% 호환
- ✅ 알림/WebSocket 로직 100% 호환
- ✅ execution_progress_notifier 호환
- ✅ 모든 Lambda 인터페이스 동일

---

## 🔧 배포 단계

### Phase 1: 사전 검증 (필수)

```powershell
# 1. 백엔드 디렉토리로 이동
cd "C:\Users\gimgy\OneDrive\바탕 화면\Analemma-Os\analemma-workflow-os\backend"

# 2. 검증 스크립트 실행
.\scripts\validate_v3_deployment.ps1

# 기대 결과: ✅ ALL CHECKS PASSED
```

**검증 항목:**
- v3 ASL 파일 존재 확인
- JSON 문법 검증
- StateDataManager action 확인
- template.yaml 설정 확인
- 호환성 검증

---

### Phase 2: 백업 (권장)

```powershell
# 레거시 ASL 파일 백업 (롤백용)
mkdir -p src/legacy_asl_backup
Copy-Item src/aws_step_functions.json src/legacy_asl_backup/
Copy-Item src/aws_step_functions_distributed.json src/legacy_asl_backup/

Write-Host "✅ Legacy ASL files backed up" -ForegroundColor Green
```

---

### Phase 3: SAM Build

```powershell
# SAM 빌드 실행
sam build

# 기대 결과:
# Build Succeeded
# Built Artifacts: .aws-sam/build
```

**주의사항:**
- Python 3.12 환경 필요
- requirements.txt 의존성 자동 설치
- Lambda 레이어 포함

---

### Phase 4: SAM Deploy

#### 4-1. 개발 환경 배포 (권장)

```powershell
# 개발 환경에 먼저 배포 (안전)
sam deploy --stack-name analemma-workflow-dev-v3 `
           --parameter-overrides StageName=dev `
           --capabilities CAPABILITY_IAM `
           --resolve-s3 `
           --confirm-changeset

# 변경 사항 확인 후 'y' 입력
```

#### 4-2. 프로덕션 배포 (검증 후)

```powershell
# 개발 환경 검증 완료 후 프로덕션 배포
sam deploy --guided

# 또는 기존 설정 사용
sam deploy --config-env production
```

---

### Phase 5: 배포 후 검증

#### 5-1. State Machine 확인

```powershell
# AWS CLI로 State Machine 확인
aws stepfunctions list-state-machines `
    --query "stateMachines[?contains(name, 'WorkflowOrchestrator')].name"

# 기대 결과:
# - WorkflowOrchestrator-dev (또는 production)
# - WorkflowDistributedOrchestrator-dev
```

#### 5-2. Lambda 함수 확인

```powershell
# StateDataManager 함수 확인
aws lambda get-function --function-name StateDataManager-dev

# 최신 버전 배포 확인
aws lambda list-versions-by-function `
    --function-name StateDataManager-dev `
    --query 'Versions[-1].Version'
```

#### 5-3. 테스트 워크플로우 실행

```powershell
# 간단한 테스트 워크플로우 실행
aws stepfunctions start-execution `
    --state-machine-arn "arn:aws:states:us-east-1:ACCOUNT_ID:stateMachine:WorkflowOrchestrator-dev" `
    --input file://test_input.json
```

---

## 📊 배포 후 모니터링

### 1. CloudWatch Metrics

**확인 항목:**
- StateDataManager 호출 횟수
- action별 분포 (sync, aggregate_branches 등)
- 에러율 (< 0.1% 목표)
- 평균 실행 시간

**대시보드:**
```
Namespace: Workflow/StateDataManager
Metrics:
  - StateDataManagerInvocations
  - ActionDistribution (by action dimension)
  - PayloadSizeKB
  - CompressionRatio
```

### 2. Step Functions Execution History

**모니터링 명령:**
```powershell
# 최근 실행 목록
aws stepfunctions list-executions `
    --state-machine-arn "arn:aws:states:REGION:ACCOUNT:stateMachine:WorkflowOrchestrator-dev" `
    --max-results 10

# 특정 실행 상세
aws stepfunctions describe-execution `
    --execution-arn "EXECUTION_ARN"
```

### 3. 알림 및 타임라인 확인

**프론트엔드 확인:**
1. 웹 앱 접속
2. 워크플로우 실행
3. 실시간 타임라인 업데이트 확인
4. WebSocket 알림 수신 확인

**예상 동작:**
- ✅ 세그먼트 진행 상황 실시간 표시
- ✅ 중복 로그 없음 (P0 최적화)
- ✅ 병렬 브랜치 정상 집계
- ✅ 완료 알림 정상 수신

---

## 🔄 롤백 절차 (문제 발생 시)

### 시나리오 1: v3 ASL 문제

```powershell
# 1. template.yaml 수정 (레거시 ASL로 복원)
# DefinitionUri: src/aws_step_functions.json
# DefinitionUri: src/aws_step_functions_distributed.json

# 2. 재배포
sam build
sam deploy

# 복구 시간: ~5분
```

### 시나리오 2: Lambda 코드 문제

```powershell
# 이전 버전으로 롤백
aws lambda update-function-code `
    --function-name StateDataManager-dev `
    --s3-bucket <backup-bucket> `
    --s3-key lambda/state_data_manager_v2.zip

# 복구 시간: ~2분
```

### 시나리오 3: 전체 스택 롤백

```powershell
# CloudFormation 스택 업데이트 취소
aws cloudformation cancel-update-stack `
    --stack-name analemma-workflow-dev-v3

# 이전 스택 버전으로 복원
aws cloudformation update-stack `
    --stack-name analemma-workflow-dev-v3 `
    --use-previous-template

# 복구 시간: ~10분
```

---

## 🐛 트러블슈팅

### 문제 1: "State not found" 에러

**원인:** v3 ASL 파일에서 상태 이름 변경
**해결:** 
```powershell
# ASL 파일 검증
python -m json.tool src/aws_step_functions_v3.json

# 상태 이름 확인
grep -E '"[A-Za-z]+":' src/aws_step_functions_v3.json | head -20
```

### 문제 2: Lambda 타임아웃

**원인:** StateDataManager 처리 시간 증가
**해결:**
```powershell
# Lambda 타임아웃 증가 (template.yaml)
Timeout: 60  # 기본 30초에서 증가

sam build && sam deploy
```

### 문제 3: 타임라인/알림 미작동

**원인:** EventBridge 이벤트 포맷 불일치
**해결:**
```powershell
# EventBridge 규칙 확인
aws events list-rules --name-prefix "workflow"

# 로그 확인
aws logs tail /aws/lambda/execution-progress-notifier-dev --follow
```

---

## 📈 성능 벤치마크

### 배포 전 기준값 측정

```powershell
# 1. 평균 실행 시간 측정
aws cloudwatch get-metric-statistics `
    --namespace AWS/States `
    --metric-name ExecutionTime `
    --start-time 2026-01-20T00:00:00Z `
    --end-time 2026-01-29T00:00:00Z `
    --period 86400 `
    --statistics Average

# 2. State 전환 횟수
aws cloudwatch get-metric-statistics `
    --namespace AWS/States `
    --metric-name StateTransition `
    --start-time 2026-01-20T00:00:00Z `
    --end-time 2026-01-29T00:00:00Z `
    --period 86400 `
    --statistics Sum
```

### v3 배포 후 비교

**기대 개선 지표:**
- State 수: -31.7% (63 → 43)
- 평균 실행 시간: -10% (예상)
- S3 GET 요청: -30% (캐싱)
- 중복 로그: -100% (P0)
- Event History 크기: -20% (예상)

---

## 📞 지원 및 문의

### 문제 보고
- GitHub Issues: [analemma-workflow-os/issues](https://github.com/...)
- 이메일: support@analemma.com

### 긴급 지원
- Slack: #workflow-support
- 전화: 1-800-ANALEMMA

---

## ✅ 배포 완료 체크리스트

배포 완료 후 다음 항목을 확인하세요:

- [ ] sam deploy 성공
- [ ] State Machine 생성 확인
- [ ] Lambda 함수 업데이트 확인
- [ ] 테스트 워크플로우 실행 성공
- [ ] 타임라인 정상 표시
- [ ] 알림 정상 수신
- [ ] CloudWatch 메트릭 정상
- [ ] 에러율 < 0.1%
- [ ] 롤백 계획 준비 완료
- [ ] 팀 공지 완료

---

**배포 책임자**: _____________
**배포 일시**: _____________
**검증자**: _____________

---

*이 가이드는 Smart StateBag v3.0 배포를 위해 작성되었습니다.*
*최종 업데이트: 2026-01-29*
