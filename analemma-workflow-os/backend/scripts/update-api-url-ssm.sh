#!/bin/bash
set -e

STACK_NAME="$1"  # 첫 번째 인자로 스택 이름 받음
AWS_REGION="$2"  # 두 번째 인자로 리전 받음

# 환경에 따라 PARAM_NAME 설정
if [[ "$STACK_NAME" == *"prod"* ]]; then
    PARAM_NAME="/my-app/prod/api-url"
else
    PARAM_NAME="/my-app/dev/api-url"
fi

#!/bin/bash
set -e

STACK_NAME="$1"  # 첫 번째 인자로 스택 이름 받음
AWS_REGION="$2"  # 두 번째 인자로 리전 받음

# 환경에 따라 PARAM_NAME 설정 (항상 dev로 고정)
API_PARAM_NAME="/my-app/dev/api-url"
WS_PARAM_NAME="/my-app/dev/websocket-url"

# CloudFormation Outputs에서 ApiEndpoint와 WebsocketApiUrl 값 추출
API_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" --output text)
WS_URL=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" --query "Stacks[0].Outputs[?OutputKey=='WebsocketApiUrl'].OutputValue" --output text)

# SSM Parameter Store에 최신 API URL 저장
aws ssm put-parameter --name "$API_PARAM_NAME" --value "$API_URL" --type String --overwrite --region "$AWS_REGION"
aws ssm put-parameter --name "$WS_PARAM_NAME" --value "$WS_URL" --type String --overwrite --region "$AWS_REGION"

echo "✅ 최신 API Gateway URL이 SSM Parameter Store에 저장되었습니다: $API_URL"
echo "✅ 최신 WebSocket URL이 SSM Parameter Store에 저장되었습니다: $WS_URL"
