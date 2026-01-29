# 🚀 Quick Deployment Commands

## ✅ 검증 완료!
모든 v3 파일이 준비되었으며, 배포 준비가 완료되었습니다.

---

## 📦 배포 명령어

### 1. 검증 (이미 완료)
```powershell
.\scripts\validate_v3_deployment.ps1
# ✅ ALL CHECKS PASSED
```

### 2. 빌드
```powershell
sam build
```

### 3. 배포 - 개발 환경 (권장)
```powershell
sam deploy --stack-name analemma-workflow-dev-v3 `
           --parameter-overrides StageName=dev `
           --capabilities CAPABILITY_IAM `
           --resolve-s3 `
           --confirm-changeset
```

### 4. 배포 - 프로덕션 (dev 검증 후)
```powershell
sam deploy --guided
```

---

## 📊 변경 사항 요약

### ASL 파일
- ✅ Standard: `aws_step_functions_v3.json` (51 → 33 states, -35.3%)
- ✅ Distributed: `aws_step_functions_distributed_v3.json` (36 → 34 states, -5.6%)

### Lambda 함수
- ✅ StateDataManager: 8개 action (기존 호환성 유지)
  - update_and_compress (레거시 지원)
  - sync, sync_branch, aggregate_branches
  - merge_callback, merge_async
  - aggregate_distributed, create_snapshot

### 신규 기능
- ✅ P0: 중복 로그 자동 제거
- ✅ P1: State Snapshot (복구/디버깅)
- ✅ P2: 경량 에러 알림
- ✅ 최적화: S3 캐싱 (5분 TTL)

### 호환성
- ✅ 타임라인: 100% 호환 (+ 중복 제거)
- ✅ 알림: 100% 호환
- ✅ WebSocket: 100% 호환
- ✅ 기존 워크플로우: 영향 없음

---

## 🔄 롤백 (문제 발생 시)

### 빠른 롤백
```powershell
# 1. template.yaml 수정
# DefinitionUri: src/aws_step_functions.json
# DefinitionUri: src/aws_step_functions_distributed.json

# 2. 재배포
sam build && sam deploy

# 복구 시간: ~5분
```

---

## 📈 성능 개선 (예상)

| 지표 | 개선율 |
|------|--------|
| State 수 | -35.3% |
| 실행 시간 | -10% |
| S3 요청 | -30% |
| 중복 로그 | -100% |
| Event History | -20% |

---

## 📞 문제 발생 시

1. **검증 스크립트 재실행**
   ```powershell
   .\scripts\validate_v3_deployment.ps1
   ```

2. **상세 가이드 참조**
   - [DEPLOYMENT_GUIDE_V3.md](DEPLOYMENT_GUIDE_V3.md)
   - [COMPATIBILITY_REPORT.md](COMPATIBILITY_REPORT.md)

3. **로그 확인**
   ```powershell
   sam logs -n StateDataManagerFunction --tail
   ```

---

**준비 완료!** 🎉
위 명령어를 순서대로 실행하세요.
