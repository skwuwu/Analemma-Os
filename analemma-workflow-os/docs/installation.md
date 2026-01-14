# 🚀 Installation Guide

> [← Back to Main README](../README.md)

This document provides step-by-step instructions for deploying Analemma OS to AWS, including local development setup, environment configuration, and production deployment.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Quick Start](#2-quick-start)
3. [Local Development Setup](#3-local-development-setup)
4. [AWS Deployment](#4-aws-deployment)
5. [Environment Configuration](#5-environment-configuration)
6. [Database Setup](#6-database-setup)
7. [Authentication Setup (Cognito)](#7-authentication-setup-cognito)
8. [Monitoring & Logging](#8-monitoring--logging)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Prerequisites

### 1.1 Required Tools

| Tool | Version | Purpose |
|------|---------|---------|
| **Python** | 3.12+ | Backend runtime |
| **Node.js** | 18+ | Frontend build tools |
| **AWS CLI** | 2.x | AWS resource management |
| **AWS SAM CLI** | 1.100+ | Serverless deployment |
| **Docker** | 20+ | Lambda container builds |

### 1.2 AWS Account Requirements

- AWS Account with administrative access
- IAM user with programmatic access
- Cognito User Pool configured
- Sufficient service quotas:
  - Lambda concurrent executions: 1000+
  - Step Functions state transitions: 25000/day
  - DynamoDB RCU/WCU: 500+ each

### 1.3 API Keys

| Service | Required | Purpose |
|---------|----------|---------|
| **Google AI (Gemini)** | ✅ Yes | Primary LLM provider |
| **AWS Bedrock** | Optional | Fallback LLM (Claude) |
| **OpenAI** | Optional | Alternative LLM |
| **Anthropic** | Optional | Direct Claude access |

---

## 2. Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/skwuwu/Analemma-Os.git
cd Analemma-Os/analemma-workflow-os/backend

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure AWS credentials
aws configure
# Enter: Access Key, Secret Key, Region (e.g., us-east-1)

# 5. Deploy to AWS
sam build
sam deploy --guided
```

---

## 3. Local Development Setup

### 3.1 Python Environment

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install -r requirements-dev.txt  # If available
```

### 3.2 Environment Variables

Create a `.env` file in the `backend/` directory:

```bash
# .env file for local development

# AWS Configuration
AWS_REGION=us-east-1
AWS_PROFILE=default

# DynamoDB Tables (local or remote)
WORKFLOWS_TABLE=WorkflowsTableV3-dev
EXECUTIONS_TABLE=ExecutionsTableV3-dev
TASK_TOKENS_TABLE_NAME=TaskTokensTableV3-dev
USERS_TABLE=UsersTableV3-dev
CHECKPOINT_TABLE=CheckpointsTableV3-dev

# S3 Bucket
SKELETON_S3_BUCKET=analemma-state-dev

# Cognito
COGNITO_ISSUER_URL=https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXX
APP_CLIENT_ID=your-app-client-id

# LLM API Keys
GOOGLE_API_KEY=your-gemini-api-key
OPENAI_API_KEY=your-openai-api-key  # Optional
ANTHROPIC_API_KEY=your-anthropic-api-key  # Optional

# Development Mode
MOCK_MODE=true  # Use mock LLM responses
LOG_LEVEL=DEBUG
```

### 3.3 Running Locally with SAM

```bash
# Start local API Gateway
sam local start-api --env-vars env.json

# Invoke specific function
sam local invoke RunWorkflowFunction --event events/run_workflow.json

# Start with Docker network for DynamoDB Local
sam local start-api --docker-network analemma-network
```

### 3.4 Using DynamoDB Local

```bash
# Start DynamoDB Local container
docker run -d -p 8000:8000 amazon/dynamodb-local

# Create tables
aws dynamodb create-table \
  --table-name WorkflowsTableV3-dev \
  --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --endpoint-url http://localhost:8000
```

---

## 4. AWS Deployment

### 4.1 SAM Configuration

The `samconfig.toml` file contains deployment configuration:

```toml
# samconfig.toml

version = 0.1
[default.deploy.parameters]
stack_name = "analemma-workflow-backend"
resolve_s3 = true
s3_prefix = "analemma-workflow-backend"
region = "us-east-1"
confirm_changeset = true
capabilities = "CAPABILITY_IAM CAPABILITY_AUTO_EXPAND"
disable_rollback = false
image_repositories = []

[default.build.parameters]
use_container = true
```

### 4.2 Deployment Commands

```bash
# Build the application
sam build

# Deploy with guided prompts (first time)
sam deploy --guided

# Deploy with existing configuration
sam deploy

# Deploy to specific stage
sam deploy --config-env prod --parameter-overrides "StageName=prod"
```

### 4.3 Required Parameters

During `sam deploy --guided`, provide these parameters:

| Parameter | Description | Example |
|-----------|-------------|---------|
| `StageName` | Deployment stage | `dev`, `prod` |
| `CognitoIssuerUrl` | Cognito issuer URL | `https://cognito-idp.us-east-1...` |
| `CognitoAudience` | App client ID | `abc123def456` |
| `GoogleApiKey` | Gemini API key | `AIza...` |
| `AllowedOrigins` | CORS origins | `https://app.analemma.io` |

### 4.4 CI/CD Deployment (GitHub Actions)

```yaml
# .github/workflows/deploy.yml

name: Deploy to AWS

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - uses: aws-actions/setup-sam@v2
      
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-1
      
      - run: sam build
      
      - run: |
          sam deploy \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset \
            --parameter-overrides \
              "GoogleApiKey=${{ secrets.GOOGLE_API_KEY }}" \
              "CognitoIssuerUrl=${{ secrets.COGNITO_ISSUER_URL }}" \
              "CognitoAudience=${{ secrets.COGNITO_AUDIENCE }}"
```

---

## 5. Environment Configuration

### 5.1 Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `WORKFLOWS_TABLE` | Yes | DynamoDB workflows table name |
| `EXECUTIONS_TABLE` | Yes | DynamoDB executions table name |
| `TASK_TOKENS_TABLE_NAME` | Yes | HITP task tokens table |
| `SKELETON_S3_BUCKET` | Yes | S3 bucket for state offload |
| `COGNITO_ISSUER_URL` | Yes | Cognito User Pool issuer |
| `APP_CLIENT_ID` | Yes | Cognito App Client ID |
| `GOOGLE_API_KEY` | Yes | Gemini API key |
| `MOCK_MODE` | No | Enable mock LLM responses |
| `LOG_LEVEL` | No | Logging level (DEBUG, INFO, etc.) |
| `STREAMING_UI_DELAY_MS` | No | UI delay for streaming (default: 50) |

### 5.2 Secrets Management

API keys are stored in AWS Secrets Manager:

```bash
# Create secret
aws secretsmanager create-secret \
  --name "backend-workflow-prod-gemini_api_key" \
  --secret-string '{"api_key":"AIza..."}'

# Retrieve secret in Lambda
import boto3
client = boto3.client('secretsmanager')
secret = client.get_secret_value(SecretId='backend-workflow-prod-gemini_api_key')
```

### 5.3 Stage-Specific Configuration

```yaml
# template.yaml - Parameter defaults by stage

Parameters:
  StageName:
    Type: String
    Default: dev
    AllowedValues: [dev, staging, prod]
  
  MockMode:
    Type: String
    Default: "true"  # Enabled for dev, disabled for prod
```

---

## 6. Database Setup

### 6.1 DynamoDB Tables

The SAM template automatically creates these tables:

| Table | Purpose | Key Schema |
|-------|---------|------------|
| `WorkflowsTableV3` | Workflow definitions | `pk` (owner_id), `sk` (workflow_id) |
| `ExecutionsTableV3` | Execution records | `pk` (owner_id), `sk` (execution_id) |
| `TaskTokensTableV3` | HITP task tokens | `pk` (execution_id), `sk` (segment_id) |
| `UsersTableV3` | User profiles | `pk` (user_id) |
| `CheckpointsTableV3` | Time Machine checkpoints | `pk` (thread_id), `sk` (checkpoint_id) |
| `SkillsTableV3` | Skill repository | `pk` (skill_id), `sk` (version) |

### 6.2 Global Secondary Indexes (GSI)

| Table | GSI Name | Purpose |
|-------|----------|---------|
| `WorkflowsTableV3` | `OwnerIdNameIndex` | List workflows by owner |
| `WorkflowsTableV3` | `CategoryIndex` | Filter by category |
| `ExecutionsTableV3` | `OwnerIdStartDateIndex` | List executions by date |
| `ExecutionsTableV3` | `OwnerIdStatusIndex` | Filter by status |

### 6.3 TTL Configuration

All tables use TTL for automatic cleanup:

| Table | TTL Field | Default TTL |
|-------|-----------|-------------|
| `TaskTokensTableV3` | `ttl` | 24 hours |
| `ExecutionsTableV3` | `ttl` | 90 days |
| `CheckpointsTableV3` | `ttl` | 30 days |

---

## 7. Authentication Setup (Cognito)

### 7.1 Create User Pool

```bash
# Create User Pool
aws cognito-idp create-user-pool \
  --pool-name "analemma-users" \
  --auto-verified-attributes email \
  --username-attributes email \
  --policies '{"PasswordPolicy":{"MinimumLength":8,"RequireUppercase":true,"RequireLowercase":true,"RequireNumbers":true,"RequireSymbols":false}}'

# Create App Client
aws cognito-idp create-user-pool-client \
  --user-pool-id <POOL_ID> \
  --client-name "analemma-web" \
  --generate-secret false \
  --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH
```

### 7.2 Configure Hosted UI

1. Go to AWS Console → Cognito → User Pools
2. Select your pool → App Integration
3. Configure callback URLs:
   - `https://your-domain.com/callback`
   - `http://localhost:3000/callback` (development)

### 7.3 Get Configuration Values

```bash
# Get Issuer URL
echo "https://cognito-idp.${AWS_REGION}.amazonaws.com/${USER_POOL_ID}"

# Get App Client ID
aws cognito-idp list-user-pool-clients --user-pool-id <POOL_ID>
```

---

## 8. Monitoring & Logging

### 8.1 CloudWatch Logs

Lambda logs are automatically sent to CloudWatch:

```
Log Groups:
├── /aws/lambda/RunWorkflowFunction-{stage}
├── /aws/lambda/SegmentRunnerFunction-{stage}
├── /aws/lambda/AgenticDesignerFunction-{stage}
└── /aws/lambda/CodesignApiFunction-{stage}
```

### 8.2 X-Ray Tracing

X-Ray is enabled by default in `template.yaml`:

```yaml
Globals:
  Function:
    Tracing: Active
```

View traces in AWS Console → X-Ray → Service Map

### 8.3 CloudWatch Dashboards

Create a custom dashboard:

```bash
aws cloudwatch put-dashboard \
  --dashboard-name "Analemma-Operations" \
  --dashboard-body file://dashboard.json
```

### 8.4 Alarms Setup

```bash
# Create high error rate alarm
aws cloudwatch put-metric-alarm \
  --alarm-name "Analemma-HighErrorRate" \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --statistic Sum \
  --period 300 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --alarm-actions arn:aws:sns:us-east-1:123456789:alerts
```

---

## 9. Troubleshooting

### 9.1 Common Issues

#### Lambda Cold Start Timeout

**Symptom:** First request times out after 30s

**Solution:**
```yaml
# Increase timeout in template.yaml
Globals:
  Function:
    Timeout: 60  # Increase from 30
```

#### DynamoDB Throttling

**Symptom:** `ProvisionedThroughputExceededException`

**Solution:**
1. Enable on-demand billing: `BillingMode: PAY_PER_REQUEST`
2. Or increase provisioned capacity

#### CORS Errors

**Symptom:** `Access-Control-Allow-Origin` errors in browser

**Solution:**
```yaml
# Ensure AllowedOrigins includes your frontend URL
AllowedOrigins: "https://your-app.com,http://localhost:3000"
```

#### Cognito Token Validation Fails

**Symptom:** 401 Unauthorized responses

**Solution:**
1. Verify `CognitoIssuerUrl` is correct
2. Check token hasn't expired
3. Ensure `APP_CLIENT_ID` matches

### 9.2 Debugging Commands

```bash
# View Lambda logs
sam logs -n RunWorkflowFunction --tail

# Check Step Functions execution
aws stepfunctions describe-execution \
  --execution-arn <EXECUTION_ARN>

# Test Lambda locally
sam local invoke RunWorkflowFunction \
  --event events/test.json \
  --debug

# Validate SAM template
sam validate

# Check deployed resources
aws cloudformation describe-stack-resources \
  --stack-name analemma-workflow-backend
```

### 9.3 Health Check Endpoints

```bash
# API Health
curl https://api.analemma.io/health

# Expected response
{"status": "healthy", "version": "1.0.0", "region": "us-east-1"}
```

---

## Appendix: Infrastructure Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            AWS Infrastructure                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────────────────────┐   │
│  │  CloudFront │────>│ API Gateway │────>│     Lambda Functions        │   │
│  │   (CDN)     │     │  (REST/WS)  │     │  ├─ RunWorkflow             │   │
│  └─────────────┘     └─────────────┘     │  ├─ SegmentRunner           │   │
│                            │             │  ├─ AgenticDesigner         │   │
│                            │             │  ├─ CodesignAPI             │   │
│                            ▼             │  └─ WebSocket handlers      │   │
│                      ┌───────────┐       └─────────────────────────────┘   │
│                      │  Cognito  │                    │                     │
│                      │User Pool  │                    │                     │
│                      └───────────┘                    ▼                     │
│                                          ┌─────────────────────────────┐   │
│                                          │      Step Functions         │   │
│                                          │  ├─ Standard Workflow       │   │
│                                          │  └─ Distributed Map         │   │
│                                          └─────────────────────────────┘   │
│                                                       │                     │
│         ┌─────────────────────────────────────────────┼─────────────┐      │
│         │                                             │             │      │
│         ▼                                             ▼             ▼      │
│  ┌─────────────┐                             ┌─────────────┐  ┌─────────┐ │
│  │  DynamoDB   │                             │     S3      │  │ Secrets │ │
│  │  ├─ Workflows                             │ State Bucket│  │ Manager │ │
│  │  ├─ Executions                            └─────────────┘  └─────────┘ │
│  │  ├─ TaskTokens                                                         │
│  │  ├─ Users                                                              │
│  │  └─ Checkpoints                                                        │
│  └─────────────┘                                                          │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

---

> [← Back to Main README](../README.md)
