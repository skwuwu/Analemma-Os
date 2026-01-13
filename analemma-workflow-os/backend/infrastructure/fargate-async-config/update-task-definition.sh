#!/bin/bash

# ECS Task Definition 동적 업데이트 스크립트
# 사용법: ./scripts/update-task-definition.sh <IMAGE_TAG>

set -e

# 파라미터 검증
if [ -z "$1" ]; then
    echo "❌ 사용법: $0 <IMAGE_TAG>"
    echo "예시: $0 abc123def456"
    exit 1
fi

IMAGE_TAG="$1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="ap-northeast-2"
TASK_DEFINITION_FILE="async-llm-worker-task-definition.json"
TASK_FAMILY="async-llm-worker"

echo "🚀 ECS Task Definition 업데이트 시작..."
echo "📦 이미지 태그: ${IMAGE_TAG}"
echo "🏷️ 계정 ID: ${ACCOUNT_ID}"

# Task Definition 파일에서 이미지 태그 치환
TEMP_FILE=$(mktemp)
sed "s/\${IMAGE_TAG:-latest}/${IMAGE_TAG}/g" "${TASK_DEFINITION_FILE}" > "${TEMP_FILE}"

echo "📋 Task Definition 등록 중..."

# Task Definition 등록
TASK_DEF_ARN=$(aws ecs register-task-definition \
    --cli-input-json "file://${TEMP_FILE}" \
    --region "${REGION}" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text)

if [ -z "$TASK_DEF_ARN" ]; then
    echo "❌ Task Definition 등록 실패"
    rm "${TEMP_FILE}"
    exit 1
fi

echo "✅ Task Definition 등록 완료: ${TASK_DEF_ARN}"

# 임시 파일 정리
rm "${TEMP_FILE}"

# GitHub Actions 출력 (optional)
if [ -n "$GITHUB_OUTPUT" ]; then
    echo "task-definition-arn=${TASK_DEF_ARN}" >> "$GITHUB_OUTPUT"
fi

echo "🎉 Task Definition 업데이트 완료!"
echo "📝 새로운 ARN: ${TASK_DEF_ARN}"