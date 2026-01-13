# 📦 Fargate 비동기 LLM 설정 (비활성화 상태)

## 📍 현재 상태: ⏸️ **비활성화**

이 폴더에는 Lambda 15분 제한을 우회하여 대용량 LLM 처리를 위한 모든 Fargate 설정이 준비되어 있습니다.

## 🎯 활성화 조건
- ✅ 기본 워크플로우 엔진 테스트 완료
- ✅ Step Functions 통합 테스트 완료  
- ✅ HITP (Human-in-the-Loop) 기능 검증 완료

## 🚀 활성화 방법
**`ACTIVATION_GUIDE.md`** 파일을 참조하여 단계별로 활성화할 수 있습니다.

## 📋 포함된 설정
- ECS Task Definition (보안 강화된 버전)
- IAM 역할 분리 (Execution Role + Task Role)
- Secrets Manager 통합
- 자동화된 배포 스크립트
- GitHub Actions 워크플로우

**준비 완료 상태**: 언제든 5분 내 활성화 가능 🚀