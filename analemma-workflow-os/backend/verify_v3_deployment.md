# v3.3 Unified Pipe 배포 검증 체크리스트

## ✅ GitHub Actions 배포 파이프라인 검증

### 1. ASL 파일 배포 경로 확인
```yaml
# template.yaml Line 2413-2414
StepFunctionOrchestrator:
  DefinitionUri: src/aws_step_functions_v3.json  ✅

# template.yaml Line 2462-2463  
StepFunctionDistributedOrchestrator:
  DefinitionUri: src/aws_step_functions_distributed_v3.json  ✅
```

**배포 시 파일 위치:**
- `backend/src/aws_step_functions_v3.json` → CloudFormation DefinitionUri
- `backend/src/aws_step_functions_distributed_v3.json` → CloudFormation DefinitionUri

### 2. Lambda 함수 - USC 참조 확인

#### ✅ InitializeStateDataFunction
```python
# src/common/initialize_state_data.py Line 27
from src.handlers.utils.universal_sync_core import universal_sync_core

# Line 809-816: USC 호출
usc_result = universal_sync_core(
    base_state={},  
    new_result=initial_payload,
    context={'action': 'init', ...}
)
```

**템플릿 정의:** Line 556
```yaml
InitializeStateDataFunction:
  PackageType: Image
  ImageConfig:
    Command: ["src.common.initialize_state_data.lambda_handler"]
```

#### ✅ StateDataManagerFunction
```python
# src/handlers/utils/state_data_manager.py Line 38
from .universal_sync_core import universal_sync_core, get_default_hydrator

# Line 643, 683, 701, 717, 744, 793: 6개 액션에서 USC 호출
return universal_sync_core(
    base_state=base_state,
    new_result=new_result,
    context={'action': 'sync', ...}
)
```

**템플릿 정의:** Line 1938
```yaml
StateDataManagerFunction:
  PackageType: Image
  ImageConfig:
    Command: ["src.handlers.utils.state_data_manager.lambda_handler"]
```

#### ✅ SegmentRunnerFunction
```yaml
# template.yaml Line 583
SegmentRunnerFunction:
  PackageType: Image
  ImageConfig:
    Command: ["src.handlers.core.segment_runner_handler.lambda_handler"]
```

### 3. Docker 이미지 빌드 검증

#### Base Image (Line 132-149)
```yaml
- name: Build and Push LLM Base Image
  uses: docker/build-push-action@v5
  with:
    context: ./analemma-workflow-os/backend
    file: ./analemma-workflow-os/backend/Dockerfile.base
    platforms: linux/arm64  # Graviton2
    tags:
      - backend-llm-base:latest
      - backend-llm-base:${{ hash }}
```

#### Lambda Image (Line 165-184)
```yaml
- name: Build and Push Final Lambda Image
  with:
    context: ./analemma-workflow-os/backend/src  # ← USC 포함됨
    file: ./analemma-workflow-os/backend/src/Dockerfile.lambda
    platforms: linux/arm64
    tags:
      - backend-lambda-function:latest
      - backend-lambda-function:${{ github.sha }}
    no-cache: true  # Always fresh build
```

#### Dockerfile.lambda 검증
```dockerfile
# Line 7: 전체 src/ 디렉토리를 복사
COPY . /var/task/src/

# 이미지에 포함되는 USC 경로:
# /var/task/src/handlers/utils/universal_sync_core.py ✅
```

### 4. ASL → Lambda ARN 매핑 검증

#### StepFunctionOrchestrator (v3)
```yaml
# template.yaml Line 2416-2425
DefinitionSubstitutions:
  InitializeStateDataArn: !GetAtt InitializeStateDataFunction.Arn  ✅
  ExecuteSegmentArn: !GetAtt SegmentRunnerFunction.Arn             ✅
  SegmentRunnerArn: !GetAtt SegmentRunnerFunction.Arn              ✅
  StateDataManagerArn: !GetAtt StateDataManagerFunction.Arn        ✅
  MergeCallbackArn: !GetAtt MergeCallbackFunction.Arn              ✅
  AsyncLLMHandlerArn: !GetAtt AsyncLLMHandlerFunction.Arn          ✅
  AggregateResultsArn: !GetAtt AggregateDistributedResultsFunction.Arn ✅
```

#### StepFunctionDistributedOrchestrator (v3)
```yaml
# template.yaml Line 2465-2480
DefinitionSubstitutions:
  InitializeStateDataArn: !GetAtt InitializeStateDataFunction.Arn  ✅
  ExecuteSegmentArn: !GetAtt SegmentRunnerFunction.Arn             ✅
  StateDataManagerArn: !GetAtt StateDataManagerFunction.Arn        ✅
  PrepareDistributedExecutionArn: !GetAtt PrepareDistributedExecutionFunction.Arn ✅
  ProcessSegmentChunkArn: !GetAtt ProcessSegmentChunkFunction.Arn  ✅
  LoadLatestStateArn: !GetAtt LoadLatestStateFunction.Arn          ✅
  SaveLatestStateArn: !GetAtt SaveLatestStateFunction.Arn          ✅
  AggregateDistributedResultsArn: !GetAtt AggregateDistributedResultsFunction.Arn ✅
```

### 5. SAM Deploy 파라미터 검증

```bash
# backend-deploy.yml Line 207-213
sam deploy \
  --stack-name "backend-workflow-dev" \
  --region "${AWS_REGION}" \
  --resolve-s3 \
  --image-repository "${IMAGE_REPO_URI}" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameter-overrides ${PARAM_OVERRIDES}
```

**주입되는 파라미터:**
- `BackendLambdaImageUri`: ECR의 최신 이미지 (SHA 태그) ✅
- `StageName`: dev
- `MockMode`: false (프로덕션 LLM 호출)
- `CognitoIssuerUrl`, `CognitoAudience`: JWT 인증
- `OpenAiApiKey`, `AnthropicApiKey`, `GoogleApiKey`: LLM API 키
- `WorkflowStateBucket`: S3 상태 저장 버킷

---

## 🔍 배포 후 검증 명령어

### 1. Step Functions ASL 업데이트 확인
```bash
aws stepfunctions describe-state-machine \
  --state-machine-arn "arn:aws:states:${AWS_REGION}:${ACCOUNT_ID}:stateMachine:WorkflowOrchestrator-dev" \
  --query 'definition' \
  --output json | jq '.Comment'
# Expected: "Analemma OS v3.0 - Smart StateBag Orchestrator with Standardized Interfaces"
```

### 2. Lambda 이미지 URI 확인
```bash
aws lambda get-function \
  --function-name "backend-workflow-dev-InitializeStateDataFunction-XXX" \
  --query 'Code.ImageUri'
# Expected: ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/backend-lambda-function:${SHA}
```

### 3. USC 포함 여부 확인 (Lambda 컨테이너 내부)
```bash
aws lambda invoke \
  --function-name "backend-workflow-dev-InitializeStateDataFunction-XXX" \
  --payload '{"test": "module_check"}' \
  /tmp/response.json

# 또는 CloudWatch Logs에서 USC import 에러 확인
aws logs tail /aws/lambda/backend-workflow-dev-InitializeStateDataFunction-XXX --follow
```

### 4. ASL → Lambda ARN 매핑 검증
```bash
aws stepfunctions describe-state-machine \
  --state-machine-arn "arn:aws:states:${AWS_REGION}:${ACCOUNT_ID}:stateMachine:WorkflowOrchestrator-dev" \
  --query 'definition' \
  --output json | jq '.States.InitializeStateBag.Parameters.FunctionName'
# Expected: "${InitializeStateDataArn}"가 실제 ARN으로 치환되어 있음
```

### 5. 테스트 실행으로 E2E 검증
```bash
# backend/tests/ 디렉토리에서
pytest tests/backend/integration/test_workflow_execution.py -v

# 또는 Simulator 직접 실행
python -m src.handlers.simulator.mission_simulator \
  --workflow-file src/test_workflows/simple_llm_test.json \
  --stage dev
```

---

## 🚨 배포 실패 시 트러블슈팅

### 1. ASL 파일을 찾을 수 없음
**에러:** `DefinitionUri: src/aws_step_functions_v3.json not found`

**해결:**
```bash
# backend/ 디렉토리 구조 확인
ls -la backend/src/aws_step_functions*.json

# 파일이 없으면 Git 추적 확인
git ls-files backend/src/aws_step_functions*.json

# .gitignore 확인
cat backend/.gitignore | grep "aws_step_functions"
```

### 2. Lambda에서 USC import 실패
**에러:** `ModuleNotFoundError: No module named 'src.handlers.utils.universal_sync_core'`

**해결:**
```bash
# Docker 이미지 재빌드 (캐시 무효화)
# backend-deploy.yml Line 182
no-cache: true  # 이미 설정됨

# 또는 로컬에서 이미지 빌드 테스트
cd backend/src
docker build -f Dockerfile.lambda \
  --build-arg BASE_IMAGE_URI=${BASE_IMAGE_URI} \
  -t test-lambda .

# 컨테이너 내부 파일 확인
docker run --rm test-lambda ls -la /var/task/src/handlers/utils/
```

### 3. DefinitionSubstitutions 치환 실패
**에러:** Step Functions 실행 시 `Lambda function not found: ${InitializeStateDataArn}`

**해결:**
```bash
# CloudFormation 스택 이벤트 확인
aws cloudformation describe-stack-events \
  --stack-name backend-workflow-dev \
  --max-items 20

# Lambda 함수 생성 확인
aws lambda list-functions | grep InitializeStateData

# Step Functions 정의 확인 (치환된 ARN 확인)
aws stepfunctions describe-state-machine \
  --state-machine-arn "..." \
  --query 'definition' | jq '.States.InitializeStateBag'
```

### 4. GitHub Actions 빌드 실패
**에러:** `Disk space quota exceeded`

**해결:** 이미 구현됨 (Line 60-73)
```yaml
- name: Maximize disk space for Docker builds
  run: |
    sudo rm -rf /usr/share/dotnet
    sudo rm -rf /usr/local/lib/android
    docker system prune -af --volumes
```

---

## ✅ v3.3 배포 최종 체크리스트

- [ ] `aws_step_functions_v3.json` 파일이 `backend/src/`에 존재
- [ ] `aws_step_functions_distributed_v3.json` 파일이 `backend/src/`에 존재
- [ ] `universal_sync_core.py`가 `backend/src/handlers/utils/`에 존재
- [ ] `initialize_state_data.py`가 USC import 포함 (Line 27)
- [ ] `state_data_manager.py`가 USC import 포함 (Line 38)
- [ ] `template.yaml` DefinitionUri 경로 확인 (Line 2413, 2462)
- [ ] `template.yaml` DefinitionSubstitutions 매핑 확인 (Line 2416-2480)
- [ ] `Dockerfile.lambda` COPY 명령어 확인 (Line 7: `COPY . /var/task/src/`)
- [ ] GitHub Actions workflow 파일 존재 (`.github/workflows/backend-deploy.yml`)
- [ ] SAM build/deploy 명령어에 `--image-repository` 포함 (Line 207)
- [ ] 배포 후 Step Functions 정의 업데이트 확인
- [ ] 배포 후 Lambda 이미지 URI 최신 SHA 확인
- [ ] 테스트 워크플로우 실행으로 E2E 검증

---

## 📦 배포 트리거 방법

### 자동 배포 (Main 브랜치 Push)
```bash
cd analemma-workflow-os/backend
git add .
git commit -m "v3.3 Unified Pipe: USC integration complete"
git push origin main
```

### 수동 배포 (GitHub UI)
1. GitHub → Actions 탭
2. "Backend Deploy" workflow 선택
3. "Run workflow" 버튼 클릭
4. Branch: `main` 선택
5. "Run workflow" 실행

### 로컬 SAM 배포 (테스트용)
```bash
cd backend

# SAM 빌드
sam build --parameter-overrides StageName=dev

# SAM 배포
sam deploy \
  --stack-name backend-workflow-dev \
  --region us-east-1 \
  --resolve-s3 \
  --image-repository ${ACCOUNT_ID}.dkr.ecr.us-east-1.amazonaws.com/backend-lambda-function \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
  --parameter-overrides StageName=dev BackendLambdaImageUri=${IMAGE_URI}
```

---

## 🎯 v3.3 배포 성공 기준

1. **ASL 업데이트 완료**
   - CloudFormation에서 Step Functions 리소스가 UPDATE_COMPLETE 상태
   - ASL Comment에 "v3.0" 포함

2. **Lambda USC 통합 완료**
   - InitializeStateData 함수에서 USC import 성공
   - StateDataManager 함수에서 6개 액션 모두 USC 호출

3. **E2E 테스트 통과**
   - Simulator로 간단한 워크플로우 실행 성공
   - CloudWatch Logs에 "🎯 [Day-Zero Sync]" 로그 확인
   - CloudWatch Logs에 USC 관련 에러 없음

4. **성능 검증**
   - InitializeStateData 응답 < 250KB (256KB 한도 안전 마진)
   - StateDataManager 응답 < 200KB
   - Cold Start < 3초 (2048MB 메모리 설정 효과 확인)

---

**배포 완료 후 이 체크리스트를 다시 확인하십시오!**
