#!/bin/bash

# AWS OIDC Setup Script for GitHub Actions
# This script sets up AWS OpenID Connect Provider and IAM Role for secure GitHub Actions deployment

set -e

# Configuration
GITHUB_OWNER="skwuwu"  # GitHub username
GITHUB_REPO="analemma-fullstack"  # Repository name
AWS_REGION="ap-northeast-2"
ROLE_NAME="GitHubActions-ServerlessDeployRole"
POLICY_NAME="GitHubActions-ServerlessDeployPolicy"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Setting up AWS OIDC for GitHub Actions...${NC}"

# Get AWS Account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo -e "${YELLOW}📋 AWS Account ID: ${AWS_ACCOUNT_ID}${NC}"

# 1. Create GitHub OIDC Identity Provider (if not exists)
echo -e "${YELLOW}🔧 Creating GitHub OIDC Identity Provider...${NC}"

OIDC_PROVIDER_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"

# Check if OIDC provider already exists
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_PROVIDER_ARN" >/dev/null 2>&1; then
    echo -e "${GREEN}✅ OIDC Provider already exists: ${OIDC_PROVIDER_ARN}${NC}"
else
    aws iam create-open-id-connect-provider \
        --url https://token.actions.githubusercontent.com \
        --client-id-list sts.amazonaws.com \
        --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
    
    echo -e "${GREEN}✅ Created OIDC Provider: ${OIDC_PROVIDER_ARN}${NC}"
fi

# 2. Create Trust Policy Document
echo -e "${YELLOW}📝 Creating trust policy...${NC}"

TRUST_POLICY=$(cat <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "Federated": "${OIDC_PROVIDER_ARN}"
            },
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Condition": {
                "StringEquals": {
                    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                },
                "StringLike": {
                    "token.actions.githubusercontent.com:sub": "repo:${GITHUB_OWNER}/${GITHUB_REPO}:*"
                }
            }
        }
    ]
}
EOF
)

# 3. Create IAM Role
echo -e "${YELLOW}👤 Creating IAM Role...${NC}"

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    echo -e "${GREEN}✅ IAM Role already exists: ${ROLE_NAME}${NC}"
    
    # Update trust policy
    echo "$TRUST_POLICY" > /tmp/trust-policy.json
    aws iam update-assume-role-policy \
        --role-name "$ROLE_NAME" \
        --policy-document file:///tmp/trust-policy.json
    echo -e "${GREEN}✅ Updated trust policy for role: ${ROLE_NAME}${NC}"
else
    echo "$TRUST_POLICY" > /tmp/trust-policy.json
    aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document file:///tmp/trust-policy.json \
        --description "Role for GitHub Actions to deploy serverless applications"
    
    echo -e "${GREEN}✅ Created IAM Role: ${ROLE_NAME}${NC}"
fi

# 4. Create IAM Policy for Serverless Deployment
echo -e "${YELLOW}📋 Creating IAM Policy...${NC}"

POLICY_DOCUMENT=$(cat <<'EOF'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "cloudformation:*",
                "lambda:*",
                "dynamodb:*",
                "states:*",
                "sqs:*",
                "iam:*",
                "apigateway:*",
                "logs:*",
                "cloudwatch:*",
                "s3:*",
                "events:*",
                "sns:*",
                "ssm:*",
                "secretsmanager:GetSecretValue",
                "secretsmanager:DescribeSecret",
                "secretsmanager:CreateSecret",
                "secretsmanager:PutSecretValue",
                "secretsmanager:UpdateSecret",
                "secretsmanager:TagResource",
                "ecr:*"
            ],
            "Resource": "*"
        }
    ]
}
EOF
)
EOF
)

# Check if policy exists
POLICY_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:policy/${POLICY_NAME}"

if aws iam get-policy --policy-arn "$POLICY_ARN" >/dev/null 2>&1; then
    echo -e "${GREEN}✅ IAM Policy already exists: ${POLICY_NAME}${NC}"
    
    # Create new policy version
    echo "$POLICY_DOCUMENT" > /tmp/policy.json
    aws iam create-policy-version \
        --policy-arn "$POLICY_ARN" \
        --policy-document file:///tmp/policy.json \
        --set-as-default
    echo -e "${GREEN}✅ Updated IAM Policy: ${POLICY_NAME}${NC}"
else
    echo "$POLICY_DOCUMENT" > /tmp/policy.json
    aws iam create-policy \
        --policy-name "$POLICY_NAME" \
        --policy-document file:///tmp/policy.json \
        --description "Policy for serverless deployment via GitHub Actions"
    
    echo -e "${GREEN}✅ Created IAM Policy: ${POLICY_NAME}${NC}"
fi

# 5. Attach Policy to Role
echo -e "${YELLOW}🔗 Attaching policy to role...${NC}"

aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn "$POLICY_ARN"

echo -e "${GREEN}✅ Attached policy to role${NC}"

# 6. Clean up temporary files
rm -f /tmp/trust-policy.json /tmp/policy.json

# 7. Output configuration details
ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"

echo -e "${GREEN}🎉 AWS OIDC Setup Complete!${NC}"
echo ""
echo -e "${YELLOW}📋 Configuration Summary:${NC}"
echo -e "   AWS Account ID: ${AWS_ACCOUNT_ID}"
echo -e "   AWS Region: ${AWS_REGION}"
echo -e "   Role ARN: ${ROLE_ARN}"
echo -e "   OIDC Provider: ${OIDC_PROVIDER_ARN}"
echo ""
echo -e "${YELLOW}🔐 GitHub Secrets to Configure:${NC}"
echo -e "   AWS_ROLE_ARN: ${ROLE_ARN}"
echo -e "   AWS_REGION: ${AWS_REGION}"
echo ""
echo -e "${YELLOW}📚 Next Steps:${NC}"
echo -e "   1. Update GITHUB_OWNER and GITHUB_REPO in this script"
echo -e "   2. Set GitHub Secrets in your repository"
echo -e "   3. Add LLM API keys to GitHub Secrets"
echo -e "   4. Test deployment with a commit to main branch"
echo ""
echo -e "${GREEN}✨ Ready for secure GitHub Actions deployment!${NC}"