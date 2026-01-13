#!/bin/bash

# ECS Async Worker용 IAM 역할 생성 스크립트
# 실행: ./scripts/setup-ecs-iam-roles.sh

set -e

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="ap-northeast-2"

echo "🔐 ECS Async Worker IAM 역할 설정 시작..."
echo "📊 계정 ID: ${ACCOUNT_ID}"
echo "🌏 리전: ${REGION}"

# 1. ECS Task Execution Role 생성
echo "📋 ECS Task Execution Role 생성 중..."

EXECUTION_TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}'

# Execution Role 생성
aws iam create-role \
    --role-name ecsAsyncLLMExecutionRole \
    --assume-role-policy-document "$EXECUTION_TRUST_POLICY" \
    --description "ECS Task Execution Role for Async LLM Worker" \
    --tags Key=Purpose,Value=AsyncLLMWorker Key=Type,Value=ExecutionRole \
    2>/dev/null || echo "⚠️ ecsAsyncLLMExecutionRole already exists"

# Execution Role에 정책 연결
aws iam put-role-policy \
    --role-name ecsAsyncLLMExecutionRole \
    --policy-name AsyncLLMExecutionPolicy \
    --policy-document file://ecs-execution-role-policy.json

echo "✅ ECS Task Execution Role 설정 완료"

# 2. ECS Task Role 생성
echo "📋 ECS Task Role 생성 중..."

TASK_TRUST_POLICY='{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}'

# Task Role 생성
aws iam create-role \
    --role-name ecsAsyncLLMTaskRole \
    --assume-role-policy-document "$TASK_TRUST_POLICY" \
    --description "ECS Task Role for Async LLM Worker Application Logic" \
    --tags Key=Purpose,Value=AsyncLLMWorker Key=Type,Value=TaskRole \
    2>/dev/null || echo "⚠️ ecsAsyncLLMTaskRole already exists"

# Task Role에 정책 연결
aws iam put-role-policy \
    --role-name ecsAsyncLLMTaskRole \
    --policy-name AsyncLLMTaskPolicy \
    --policy-document file://ecs-task-role-policy.json

echo "✅ ECS Task Role 설정 완료"

# 3. Secrets Manager에 API 키 생성 (예시)
echo "🔑 Secrets Manager 시크릿 생성 안내..."
echo ""
echo "다음 명령어로 API 키들을 Secrets Manager에 저장하세요:"
echo ""
echo "aws secretsmanager create-secret \\"
echo "    --name openai-api-key \\"
echo "    --description 'OpenAI API Key for Async LLM Worker' \\"
echo "    --secret-string 'sk-proj-your-openai-key-here'"
echo ""
echo "aws secretsmanager create-secret \\"
echo "    --name anthropic-api-key \\"
echo "    --description 'Anthropic API Key for Async LLM Worker' \\"
echo "    --secret-string 'sk-ant-your-anthropic-key-here'"
echo ""
echo "aws secretsmanager create-secret \\"
echo "    --name google-api-key \\"
echo "    --description 'Google API Key for Async LLM Worker' \\"
echo "    --secret-string 'your-google-api-key-here'"
echo ""

# 4. 역할 ARN 출력
EXECUTION_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/ecsAsyncLLMExecutionRole"
TASK_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/ecsAsyncLLMTaskRole"

echo "🎉 IAM 역할 설정 완료!"
echo ""
echo "📋 생성된 역할들:"
echo "   Execution Role: ${EXECUTION_ROLE_ARN}"
echo "   Task Role: ${TASK_ROLE_ARN}"
echo ""
echo "⚠️ Task Definition 파일이 이미 업데이트되었습니다."
echo "   다음 단계: Secrets Manager에 API 키들을 저장하세요."