#!/bin/bash
set -e

STACK_NAME=$1
REGION=$2
# 이름 규칙: executions-stream-manual-{STAGE_NAME}
# 예: STACK_NAME이 'dev'이면 executions-stream-manual-dev
STREAM_NAME="executions-stream-manual-${STACK_NAME}"

echo "Checking if Kinesis stream '$STREAM_NAME' exists in region '$REGION'..."

# 1. 스트림 존재 여부 확인
if aws kinesis describe-stream --stream-name "$STREAM_NAME" --region "$REGION" > /dev/null 2>&1; then
    echo "✅ Stream '$STREAM_NAME' exists."
else
    echo "⚠️ Stream '$STREAM_NAME' not found. Creating..."
    # 2. 없으면 생성 (On-Demand)
    aws kinesis create-stream \
        --stream-name "$STREAM_NAME" \
        --stream-mode-details StreamMode=ON_DEMAND \
        --region "$REGION"
    
    echo "⏳ Waiting for stream to become ACTIVE..."
    aws kinesis wait stream-exists --stream-name "$STREAM_NAME" --region "$REGION"
    echo "✅ Stream '$STREAM_NAME' created successfully."
fi

# 3. ARN 추출 및 출력
STREAM_ARN=$(aws kinesis describe-stream --stream-name "$STREAM_NAME" --region "$REGION" --query 'StreamDescription.StreamARN' --output text)
echo "Stream ARN: $STREAM_ARN"

# GitHub Actions 환경변수로 내보내기 (다음 스텝에서 사용)
# GITHUB_ENV가 존재할 때만 쓰기 (로컬 테스트 호환성)
if [ -n "$GITHUB_ENV" ]; then
    echo "KINESIS_STREAM_ARN=$STREAM_ARN" >> $GITHUB_ENV
fi
