#!/bin/bash

# Cognito 설정 검증 스크립트
echo "🔍 AWS Cognito 설정 검증 시작..."

# 환경 변수 확인
echo ""
echo "📋 환경 변수 확인:"
echo "VITE_AWS_USER_POOL_ID: ${VITE_AWS_USER_POOL_ID:-❌ 미설정}"
echo "VITE_AWS_USER_POOL_WEB_CLIENT_ID: ${VITE_AWS_USER_POOL_WEB_CLIENT_ID:-❌ 미설정}"
echo "VITE_AWS_REGION: ${VITE_AWS_REGION:-❌ 미설정}"

# .env 파일 확인
echo ""
echo "📄 .env 파일 내용:"
if [ -f ".env" ]; then
  grep -E "^VITE_AWS" .env | while IFS= read -r line; do
    echo "  $line"
  done
else
  echo "  ❌ .env 파일이 존재하지 않습니다"
fi

# User Pool 설정 검증
if [ -n "$VITE_AWS_USER_POOL_ID" ] && [ -n "$VITE_AWS_REGION" ]; then
  expected_prefix="${VITE_AWS_REGION}_"
  if [[ "$VITE_AWS_USER_POOL_ID" == "$expected_prefix"* ]]; then
    echo "✅ User Pool ID가 리전과 일치합니다"
  else
    echo "❌ User Pool ID가 리전($VITE_AWS_REGION)과 일치하지 않습니다"
  fi
fi

# AWS CLI 설치 확인 (선택적)
echo ""
echo "🔧 AWS CLI 확인:"
if command -v aws &> /dev/null; then
  echo "✅ AWS CLI 설치됨"
  
  # User Pool 정보 확인 (자격 증명이 있는 경우)
  if [ -n "$VITE_AWS_USER_POOL_ID" ]; then
    echo "🔍 User Pool 정보 확인 중..."
    aws cognito-idp describe-user-pool --user-pool-id "$VITE_AWS_USER_POOL_ID" --region "$VITE_AWS_REGION" --output table --query 'UserPool.[Id,Name,Status,Policies.PasswordPolicy]' 2>/dev/null || echo "  ℹ️  AWS 자격 증명이 없어 User Pool 정보를 확인할 수 없습니다"
    
    echo "🔍 User Pool Client 정보 확인 중..."
    aws cognito-idp describe-user-pool-client --user-pool-id "$VITE_AWS_USER_POOL_ID" --client-id "$VITE_AWS_USER_POOL_WEB_CLIENT_ID" --region "$VITE_AWS_REGION" --output table --query 'UserPoolClient.[ClientId,ClientName,GenerateSecret,ExplicitAuthFlows]' 2>/dev/null || echo "  ℹ️  AWS 자격 증명이 없어 Client 정보를 확인할 수 없습니다"
  fi
else
  echo "ℹ️  AWS CLI가 설치되어 있지 않습니다 (선택적)"
fi

# DNS 및 네트워크 확인
echo ""
echo "🌐 네트워크 연결 확인:"
cognito_domain="cognito-idp.${VITE_AWS_REGION}.amazonaws.com"
if command -v curl &> /dev/null; then
  if curl -s --max-time 5 "https://$cognito_domain" > /dev/null; then
    echo "✅ $cognito_domain 연결 가능"
  else
    echo "❌ $cognito_domain 연결 실패"
  fi
else
  echo "ℹ️  curl을 사용할 수 없어 네트워크 확인을 건너뜁니다"
fi

echo ""
echo "🔍 검증 완료!"
echo ""
echo "📋 일반적인 400 Bad Request 해결 방법:"
echo "1. User Pool Client ID 확인"
echo "2. 리전 설정 확인"  
echo "3. withAuthenticator 호환성 확인"
echo "4. Amplify v6 설정 형식 확인"
echo "5. 브라우저 캐시/쿠키 초기화"