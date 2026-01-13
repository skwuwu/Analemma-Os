"""
인증 관련 공통 유틸리티 함수들
JWT 토큰 검증 및 사용자 ID 추출 로직을 통합하여 재사용성 향상
"""

import os
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# JWKS 캐시 전역 변수들
_JWKS_CACHE: Optional[Dict[str, Any]] = None
_JWKS_CACHE_FETCHED_AT: float = 0.0
_JWKS_CACHE_TTL = int(os.getenv("JWKS_CACHE_TTL_SECONDS", "3600"))


def _fetch_jwks(jwks_url: str) -> Dict[str, Any]:
    """
    JWKS를 주어진 URL에서 가져옵니다. (urllib.request 사용으로 의존성 최소화)

    Args:
        jwks_url: JWKS 엔드포인트 URL

    Returns:
        JWKS 데이터

    Raises:
        RuntimeError: HTTP 요청 실패 시
    """
    try:
        import urllib.request
        import json

        with urllib.request.urlopen(jwks_url, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data
    except Exception as e:
        logger.error(f"Failed to fetch JWKS from {jwks_url}: {e}")
        raise RuntimeError(f"Failed to fetch JWKS: {e}")


def _get_cached_jwks(jwks_url: str) -> Dict[str, Any]:
    """
    캐시된 JWKS를 반환하고 TTL이 만료되면 새로고침합니다.

    Args:
        jwks_url: JWKS 엔드포인트 URL

    Returns:
        캐시된 JWKS 데이터 (kid->key 매핑)
    """
    global _JWKS_CACHE, _JWKS_CACHE_FETCHED_AT
    now = time.time()
    if _JWKS_CACHE is None or (now - _JWKS_CACHE_FETCHED_AT) > _JWKS_CACHE_TTL:
        jwks = _fetch_jwks(jwks_url)
        # kid로 인덱싱하여 빠른 조회 가능하도록 함
        key_map: Dict[str, Any] = {}
        for k in jwks.get("keys", []):
            kid = k.get("kid")
            if kid:
                key_map[kid] = k
        _JWKS_CACHE = {"keys": key_map}
        _JWKS_CACHE_FETCHED_AT = now
    return _JWKS_CACHE


def validate_token(token: str) -> Dict[str, Any]:
    """
    Cognito JWKS를 사용하여 JWT 토큰을 검증합니다. (PyJWT 사용으로 경량화)

    Args:
        token: 검증할 JWT 토큰

    Returns:
        디코딩된 토큰 클레임

    Raises:
        RuntimeError: 필수 라이브러리나 환경변수가 없을 경우
        ValueError: 토큰 검증 실패 시
    """
    try:
        import jwt
        from jwt.algorithms import RSAAlgorithm
    except Exception:
        raise RuntimeError("'PyJWT' is required for JWT validation. Please add 'PyJWT' to your Lambda dependencies.")

    # 발급자 URL 결정 (환경변수 우선, 없으면 리전과 풀 ID로 조립)
    issuer_url = os.environ.get("COGNITO_ISSUER_URL")

    if issuer_url:
        # 환경변수에 URL이 있으면 그대로 사용 (가장 안전함)
        issuer = issuer_url.rstrip('/')
    else:
        # URL이 없으면 Region과 Pool ID로 직접 조립
        region = os.environ.get("COGNITO_REGION")
        user_pool = os.environ.get("USER_POOL_ID")

        if not region or not user_pool:
            raise RuntimeError("COGNITO_ISSUER_URL or (COGNITO_REGION + USER_POOL_ID) must be set.")

        issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool}"

    app_client = os.environ.get("APP_CLIENT_ID")
    if not app_client:
        raise RuntimeError("APP_CLIENT_ID environment variable must be set.")

    jwks_url = f"{issuer}/.well-known/jwks.json"

    # 토큰 헤더에서 kid 추출 (PyJWT에서는 get_unverified_header)
    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
    except Exception as e:
        raise ValueError(f"Invalid token header: {e}")

    # JWKS 조회 (캐시 사용)
    jwks = _get_cached_jwks(jwks_url)
    key_map = jwks.get("keys", {})
    key_data = key_map.get(kid)

    if not key_data:
        # 캐시 새로고침 후 재시도 (1회)
        global _JWKS_CACHE_FETCHED_AT
        _JWKS_CACHE_FETCHED_AT = 0
        jwks = _get_cached_jwks(jwks_url)
        key_map = jwks.get("keys", {})
        key_data = key_map.get(kid)
        if not key_data:
            raise ValueError("Public key not found in JWKS for kid: %s" % kid)

    # PyJWT에서는 RSA public key를 직접 구성해야 함
    try:
        # RSA public key 생성
        public_key = RSAAlgorithm.from_jwk(key_data)

        # 토큰 검증 및 디코딩
        decoded = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=app_client,
        )
        return decoded
    except Exception as e:
        raise ValueError(f"Token validation failed: {e}")


def extract_owner_id_from_token(token: str) -> str:
    """
    JWT 토큰에서 owner_id를 추출합니다.

    Args:
        token: JWT 토큰

    Returns:
        사용자 ID (sub 클레임)

    Raises:
        ValueError: 토큰이 유효하지 않거나 sub 클레임이 없을 경우
    """
    token_claims = validate_token(token)
    owner_id = token_claims.get('sub')
    if not owner_id:
        raise ValueError("Token does not contain required 'sub' claim")
    return owner_id


def extract_owner_id_from_event(event: Dict[str, Any]) -> Optional[str]:
    """
    API Gateway 이벤트에서 owner_id를 추출합니다.
    
    1. requestContext의 authorizer claims (API Gateway가 이미 검증함)를 최우선으로 사용
    2. 없을 경우 Authorization 헤더를 파싱하여 검증 (Fallback)

    Args:
        event: API Gateway 이벤트

    Returns:
        사용자 ID 또는 None (인증 실패 시)
    """
    # 1. Fast Path: API Gateway가 이미 검증한 Claims 사용
    try:
        request_context = event.get("requestContext", {})
        authorizer = request_context.get("authorizer", {})
        
        # HTTP API (JWT Authorizer) structure: authorizer -> jwt -> claims -> sub
        jwt_auth = authorizer.get("jwt", {})
        unique_id = jwt_auth.get("claims", {}).get("sub")
        
        # REST API (Cognito Authorizer) or simple Lambda Authorizer structure: authorizer -> claims -> sub
        if not unique_id:
            claims = authorizer.get("claims", {})
            unique_id = claims.get("sub")
            
        # Direct authorizer output (Lambda Authorizer returning context)
        if not unique_id:
            unique_id = authorizer.get("sub") or authorizer.get("principalId")

        if unique_id:
            # logger.debug(f"Using owner_id from requestContext: {unique_id}")
            return unique_id

    except Exception as e:
        logger.warning(f"Failed to extract owner_id from requestContext: {e}")
        # Fallback continues below

    # 2. Slow Path: 헤더에서 직접 파싱 및 서명 검증 (로컬 테스트 호환성 등)
    try:
        headers = event.get("headers") or {}
        # 헤더 이름 소문자화하여 다양한 변형 수용
        headers_low = {k.lower(): v for k, v in headers.items()} if isinstance(headers, dict) else {}
        auth_header = headers_low.get("authorization")

        if not auth_header or not isinstance(auth_header, str) or not auth_header.lower().startswith("bearer "):
            logger.warning("Missing or invalid Authorization header")
            return None

        token = auth_header.split()[1]
        return extract_owner_id_from_token(token)
    except Exception as e:
        logger.error(f"Failed to extract owner_id from src.event headers: {e}")
        return None


def require_authentication(event: Dict[str, Any]) -> str:
    """
    API Gateway 이벤트에서 인증을 요구하고 owner_id를 반환합니다.
    인증 실패 시 예외를 발생시킵니다.

    Args:
        event: API Gateway 이벤트

    Returns:
        사용자 ID

    Raises:
        ValueError: 인증 실패 시
    """
    owner_id = extract_owner_id_from_event(event)
    if not owner_id:
        raise ValueError("Authentication required: Missing or invalid Authorization header")
    return owner_id