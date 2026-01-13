#!/usr/bin/env bash
set -euo pipefail

# scripts/fetch-api-url.sh
# AWS Systems Manager Parameter Store에서 API URL을 읽어 VITE_API_BASE_URL 환경변수로 export하거나
# .env.local 파일에 기록합니다. CI 파이프라인에서 사용하도록 설계됨.

PARAM_NAME_DEFAULT="/my-app/dev/api-url"
ENV_FILE=".env.local"

# Parameter name for websocket URL (optional). If present, will write VITE_WS_URL to ${ENV_FILE}
# Default SSM parameter name for WebSocket URL. Change to project-specific naming.
# Default: /my-app/dev/websocket-url (can be overridden via PARAM_NAME_WS env)
PARAM_NAME_WS=${PARAM_NAME_WS:-/my-app/dev/websocket-url}

# Allow override via env
PARAM_NAME_OVERRIDE=${PARAM_NAME_OVERRIDE:-${PARAM_NAME_DEFAULT}}
AWS_REGION_OVERRIDE=${AWS_REGION:-ap-northeast-2}

echo "Fetching API URL from SSM parameter: ${PARAM_NAME_OVERRIDE}"
if [ -n "${AWS_REGION_OVERRIDE}" ]; then
  echo "Using AWS region: ${AWS_REGION_OVERRIDE}"
fi

# Helpful debug: show caller identity if possible
echo "Attempting to show caller identity (for debugging)"
aws sts get-caller-identity --output json --region "${AWS_REGION_OVERRIDE}" 2>/dev/null || echo "(no caller identity available or not configured)"

# Try get-parameter with decryption first, capture stderr for diagnostics
API_URL=$(aws ssm get-parameter --name "${PARAM_NAME_OVERRIDE}" --with-decryption --query "Parameter.Value" --output text --region "${AWS_REGION_OVERRIDE}" 2>&1) || API_URL="__FAILED__:$?::$API_URL"

if [[ "${API_URL}" == __FAILED__* ]]; then
  echo "SSM get-parameter command failed. Raw output:" >&2
  echo "${API_URL}" >&2
  API_URL=""
fi

if [ -z "${API_URL}" ]; then
  echo "Error: Could not retrieve parameter ${PARAM_NAME_OVERRIDE} from SSM."
  echo "If you are running locally without AWS credentials, create ${ENV_FILE} with VITE_API_BASE_URL or export VITE_API_BASE_URL before running build."
  echo "CI: ensure the runner has AWS credentials and ssm:GetParameter permission for ${PARAM_NAME_OVERRIDE}."

  # Allow bypass for special cases by setting SKIP_SSM=1 (not recommended for CI)
  if [ "${SKIP_SSM:-0}" = "1" ]; then
    echo "SKIP_SSM=1 detected, continuing without setting VITE_API_BASE_URL."
    exit 0
  fi

  # Fail the build when parameter is missing to avoid deploying a build with wrong API endpoint
  echo "Failing build because SSM parameter is missing. Set SKIP_SSM=1 to override (not recommended)."
  exit 1
fi

# Export for immediate subprocesses
export VITE_API_BASE_URL="${API_URL}"

# Persist into .env.local for Vite (preferred in local dev or CI build step)
# Overwrite existing entry if present
if [ -f "${ENV_FILE}" ]; then
  # Remove any existing VITE_API_BASE_URL lines using grep (cross-platform compatible)
  grep -v "^VITE_API_BASE_URL=" "${ENV_FILE}" > "${ENV_FILE}.tmp" || true
  mv "${ENV_FILE}.tmp" "${ENV_FILE}"
fi

echo "VITE_API_BASE_URL=${API_URL}" >> "${ENV_FILE}"

# --- LFU related parameters (optional) ---
# PARAM names can be supplied via env vars: PARAM_NAME_LFU_FUNCTION, PARAM_NAME_LFU_HTTPAPI
PARAM_NAME_LFU_FUNCTION=${PARAM_NAME_LFU_FUNCTION:-/my-app/dev/lfu-function-url}
PARAM_NAME_LFU_HTTPAPI=${PARAM_NAME_LFU_HTTPAPI:-/my-app/dev/lfu-httpapi-url}
PARAM_NAME_LFU_USE_FUNCTION=${PARAM_NAME_LFU_USE_FUNCTION:-/my-app/dev/lfu-use-function}

echo "Attempting to fetch LFU function URL from: ${PARAM_NAME_LFU_FUNCTION}"
LFU_FUNC_URL=$(aws ssm get-parameter --name "${PARAM_NAME_LFU_FUNCTION}" --with-decryption --query "Parameter.Value" --output text --region "${AWS_REGION_OVERRIDE}" 2>/dev/null || true)
if [ -n "${LFU_FUNC_URL}" ] && [ "${LFU_FUNC_URL}" != "None" ]; then
  export VITE_LFU_FUNCTION_URL="${LFU_FUNC_URL}"
  # remove existing line and append
  if [ -f "${ENV_FILE}" ]; then
    grep -v "^VITE_LFU_FUNCTION_URL=" "${ENV_FILE}" > "${ENV_FILE}.tmp" || true
    mv "${ENV_FILE}.tmp" "${ENV_FILE}"
  fi
  echo "VITE_LFU_FUNCTION_URL=${LFU_FUNC_URL}" >> "${ENV_FILE}"
  echo "Wrote VITE_LFU_FUNCTION_URL to ${ENV_FILE}"
fi

echo "Attempting to fetch LFU httpApi URL from: ${PARAM_NAME_LFU_HTTPAPI}"
LFU_HTTPAPI_URL=$(aws ssm get-parameter --name "${PARAM_NAME_LFU_HTTPAPI}" --with-decryption --query "Parameter.Value" --output text --region "${AWS_REGION_OVERRIDE}" 2>/dev/null || true)
if [ -n "${LFU_HTTPAPI_URL}" ] && [ "${LFU_HTTPAPI_URL}" != "None" ]; then
  export VITE_LFU_HTTPAPI_URL="${LFU_HTTPAPI_URL}"
  if [ -f "${ENV_FILE}" ]; then
    grep -v "^VITE_LFU_HTTPAPI_URL=" "${ENV_FILE}" > "${ENV_FILE}.tmp" || true
    mv "${ENV_FILE}.tmp" "${ENV_FILE}"
  fi
  echo "VITE_LFU_HTTPAPI_URL=${LFU_HTTPAPI_URL}" >> "${ENV_FILE}"
  echo "Wrote VITE_LFU_HTTPAPI_URL to ${ENV_FILE}"
fi

# Optionally fetch a flag to prefer function URL (true/false)
LFU_USE_FUNCTION=$(aws ssm get-parameter --name "${PARAM_NAME_LFU_USE_FUNCTION}" --with-decryption --query "Parameter.Value" --output text --region "${AWS_REGION_OVERRIDE}" 2>/dev/null || true)
if [ -n "${LFU_USE_FUNCTION}" ] && [ "${LFU_USE_FUNCTION}" != "None" ]; then
  export VITE_LFU_USE_FUNCTION_URL="${LFU_USE_FUNCTION}"
  if [ -f "${ENV_FILE}" ]; then
    grep -v "^VITE_LFU_USE_FUNCTION_URL=" "${ENV_FILE}" > "${ENV_FILE}.tmp" || true
    mv "${ENV_FILE}.tmp" "${ENV_FILE}"
  fi
  echo "VITE_LFU_USE_FUNCTION_URL=${LFU_USE_FUNCTION}" >> "${ENV_FILE}"
  echo "Wrote VITE_LFU_USE_FUNCTION_URL to ${ENV_FILE}"
fi

echo "Wrote ${ENV_FILE} with VITE_API_BASE_URL"

# --- WebSocket URL (optional) ---
echo "DEBUG: PARAM_NAME_WS environment variable value: '${PARAM_NAME_WS}'"
echo "DEBUG: PARAM_NAME_WS variable is set via: ${PARAM_NAME_WS:+FROM_ENV} ${PARAM_NAME_WS:-DEFAULT_VALUE}"

# Check if VITE_WS_URL is already set in .env.local (from previous CI step)
if [ -f "${ENV_FILE}" ] && grep -q "^VITE_WS_URL=" "${ENV_FILE}"; then
  echo "VITE_WS_URL already exists in ${ENV_FILE}, skipping SSM fetch"
  WS_URL_FROM_FILE=$(grep "^VITE_WS_URL=" "${ENV_FILE}" | cut -d'=' -f2-)
  export VITE_WS_URL="${WS_URL_FROM_FILE}"
  echo "Using existing VITE_WS_URL from ${ENV_FILE}"
else
  echo "Attempting to fetch WebSocket URL from: ${PARAM_NAME_WS}"
  WS_URL=$(aws ssm get-parameter --name "${PARAM_NAME_WS}" --with-decryption --query "Parameter.Value" --output text --region "${AWS_REGION_OVERRIDE}" 2>/dev/null || true)
  if [ -n "${WS_URL}" ] && [ "${WS_URL}" != "None" ]; then
    export VITE_WS_URL="${WS_URL}"
    if [ -f "${ENV_FILE}" ]; then
      grep -v "^VITE_WS_URL=" "${ENV_FILE}" > "${ENV_FILE}.tmp" || true
      mv "${ENV_FILE}.tmp" "${ENV_FILE}"
    fi
    echo "VITE_WS_URL=${WS_URL}" >> "${ENV_FILE}"
    echo "Wrote VITE_WS_URL to ${ENV_FILE}"
  else
    echo "No WebSocket URL found in SSM for ${PARAM_NAME_WS}; VITE_WS_URL will not be set."
    # In CI environments we consider missing WS URL a fatal error to avoid deploying a build
    # without the required websocket endpoint. GitHub Actions sets CI=true in the environment.
    if [ "${CI:-false}" = "true" ] || [ "${FAIL_ON_MISSING_WS:-0}" = "1" ]; then
      echo "ERROR: VITE_WS_URL not found in SSM (${PARAM_NAME_WS}) while running in CI. Failing build." >&2
      exit 1
    fi
  fi
fi

# Print exported value for CI logs (redacted partially for safety)
SHORT=$(echo "${API_URL}" | sed -E 's#^(https?://[^/]+)(/.*)?#\1#')
echo "Fetched API URL host: ${SHORT}"

exit 0
