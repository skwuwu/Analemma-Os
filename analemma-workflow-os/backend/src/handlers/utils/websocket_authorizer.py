
"""
WebSocket $connect Authorizer Lambda.

[v2.1] 개선사항:
1. 와일드카드 ARN으로 모든 WebSocket 라우트 허용
2. Authorizer 캐싱 전략 문서화
3. CloudWatch 메트릭 + 세분화된 로깅

Caching Strategy (API Gateway 설정 권장):
- TTL: 300초 (5분) 권장
- Identity Source: queryStringParameters.token
- 캐싱 활성화 시 Lambda 호출 비용 90%+ 절감

API Gateway 설정 예시:
  AuthorizerResultTtlInSeconds: 300
  IdentitySource: route.request.querystring.token
"""

import logging
import os
import time
from typing import Any, Dict, Optional

# Import validate_token from src.common.auth_utils (Absolute import for Lambda environment)
try:
    from src.common.auth_utils import validate_token
except ImportError:
    try:
        from src.common.auth_utils import validate_token
    except ImportError:
        # Fallback: provide dummy function if auth_utils not available
        def validate_token(*args, **kwargs):
            raise Exception('Unauthorized: validate_token not available')

# CloudWatch Metrics (optional)
try:
    import boto3
    cloudwatch = boto3.client('cloudwatch')
    METRICS_ENABLED = os.environ.get('ENABLE_AUTH_METRICS', 'false').lower() in ('true', '1', 'yes')
except Exception:
    cloudwatch = None
    METRICS_ENABLED = False

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# =============================================================================
# [v2.1] CloudWatch 메트릭 헬퍼
# =============================================================================

def _emit_metric(metric_name: str, value: float = 1.0, dimensions: Optional[Dict] = None):
    """
    [v2.1] CloudWatch 커스텀 메트릭 발행.
    
    Metrics:
    - WebSocketAuthSuccess: 인증 성공
    - WebSocketAuthFailure: 인증 실패 (세부 원인별)
    - WebSocketAuthLatency: 인증 소요 시간
    """
    if not METRICS_ENABLED or not cloudwatch:
        return
    
    try:
        metric_data = {
            'MetricName': metric_name,
            'Value': value,
            'Unit': 'Count' if metric_name != 'WebSocketAuthLatency' else 'Milliseconds',
            'Dimensions': [
                {'Name': 'Service', 'Value': 'WebSocketAuthorizer'},
                *(
                    [{'Name': k, 'Value': v} for k, v in (dimensions or {}).items()]
                )
            ]
        }
        
        cloudwatch.put_metric_data(
            Namespace='AnalemmaOS/WebSocket',
            MetricData=[metric_data]
        )
    except Exception as e:
        logger.debug(f"Failed to emit metric {metric_name}: {e}")


def _build_wildcard_arn(method_arn: str) -> str:
    """
    [v2.1] Method ARN을 와일드카드 ARN으로 변환.
    
    WebSocket은 $connect 시에만 Authorizer가 실행되지만,
    정책은 연결 세션 동안 캐싱되어 재사용됩니다.
    특정 라우트에 묶인 ARN은 다른 라우트($default, sendMessage 등) 
    호출 시 403 에러를 유발할 수 있습니다.
    
    Input:  arn:aws:execute-api:region:account:api-id/stage/$connect
    Output: arn:aws:execute-api:region:account:api-id/stage/*
    """
    # ARN 형식: arn:aws:execute-api:region:account:api-id/stage/route
    parts = method_arn.rsplit('/', 1)
    if len(parts) == 2:
        return f"{parts[0]}/*"
    return method_arn  # 파싱 실패 시 원본 유지


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    WebSocket $connect Authorizer.
    
    [v2.1] 개선사항:
    - 와일드카드 ARN으로 모든 라우트 허용
    - 세분화된 에러 로깅 (보안 메시지는 유지)
    - CloudWatch 메트릭 발행
    - 캐싱 친화적 정책 반환
    
    Validates JWT token from query string parameter 'token' or 'access_token'.
    Returns an IAM Policy granting access to all WebSocket routes.
    """
    start_time = time.time()
    error_type = None
    
    try:
        method_arn = event['methodArn']
        qs = event.get('queryStringParameters') or {}
        token = qs.get('token') or qs.get('access_token')

        # [v2.1] 세분화된 에러 로깅
        if not token:
            error_type = 'missing_token'
            logger.warning("Authorization failed: Missing token in query string parameters")
            raise Exception('Unauthorized')

        # Validate token using shared logic
        # This checks signature, exp, issuer, etc.
        try:
            claims = validate_token(token)
        except Exception as token_error:
            error_str = str(token_error).lower()
            
            # [v2.1] 토큰 에러 유형 분류
            if 'expired' in error_str or 'exp' in error_str:
                error_type = 'token_expired'
                logger.warning("Authorization failed: Token expired")
            elif 'signature' in error_str or 'invalid' in error_str:
                error_type = 'invalid_signature'
                logger.warning("Authorization failed: Invalid token signature")
            elif 'issuer' in error_str or 'iss' in error_str:
                error_type = 'invalid_issuer'
                logger.warning("Authorization failed: Invalid token issuer")
            else:
                error_type = 'token_validation_failed'
                logger.warning(f"Authorization failed: Token validation error - {token_error}")
            
            raise Exception('Unauthorized')
        
        principal_id = claims.get('sub')

        if not principal_id:
            error_type = 'missing_sub_claim'
            logger.warning("Authorization failed: Token valid but missing 'sub' claim")
            raise Exception('Unauthorized')

        logger.info(f"Authorized user {principal_id} for WebSocket connection")

        # [v2.1] 와일드카드 ARN으로 모든 라우트 허용
        wildcard_arn = _build_wildcard_arn(method_arn)
        
        # Construct IAM Policy
        policy = {
            "principalId": principal_id,
            "policyDocument": {
                "Version": "2012-10-17",
                "Statement": [{
                    "Action": "execute-api:Invoke",
                    "Effect": "Allow",
                    "Resource": wildcard_arn  # [v2.1] 와일드카드 사용
                }]
            },
            # Context is passed to the backend Lambda in event.requestContext.authorizer
            # Values MUST be strings, numbers, or booleans.
            "context": {
                "ownerId": str(principal_id),
                "email": str(claims.get('email', '')),
                # [v2.1] 캐싱 힌트: API Gateway에서 이 값으로 캐시 키 구성 가능
                "tokenHash": str(hash(token) % 1000000),  # 보안상 전체 토큰 노출 방지
                "authTime": str(int(time.time()))
            }
        }
        
        # [v2.1] 성공 메트릭
        latency_ms = (time.time() - start_time) * 1000
        _emit_metric('WebSocketAuthSuccess')
        _emit_metric('WebSocketAuthLatency', latency_ms)
        
        return policy

    except Exception as e:
        # [v2.1] 실패 메트릭 (에러 유형별)
        latency_ms = (time.time() - start_time) * 1000
        _emit_metric('WebSocketAuthFailure', dimensions={'ErrorType': error_type or 'unknown'})
        _emit_metric('WebSocketAuthLatency', latency_ms)
        
        # 클라이언트에게는 표준 보안 메시지만 반환
        # 상세 에러는 이미 위에서 로깅됨
        logger.error(f"Authorization failed: {e} (error_type={error_type})")
        
        # API Gateway expects 'Unauthorized' to return 401
        raise Exception('Unauthorized')
