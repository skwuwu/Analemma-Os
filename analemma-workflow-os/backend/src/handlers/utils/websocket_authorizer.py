
import logging
import os
from typing import Any, Dict

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


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    WebSocket $connect Authorizer.
    Validates JWT token from src.query string parameter 'token' or 'access_token'.
    Returns an IAM Policy granting access to the connect route.
    """
    try:
        method_arn = event['methodArn']
        qs = event.get('queryStringParameters') or {}
        token = qs.get('token') or qs.get('access_token')

        if not token:
            logger.warning("Missing token in query string parameters")
            raise Exception('Unauthorized')

        # Validate token using shared logic
        # This checks signature, exp, issuer, etc.
        claims = validate_token(token)
        principal_id = claims.get('sub')

        if not principal_id:
            logger.warning("Token valid but missing 'sub' claim")
            raise Exception('Unauthorized')

        logger.info(f"Authorized user {principal_id} for WebSocket connection")

        # Construct IAM Policy
        policy = {
            "principalId": principal_id,
            "policyDocument": {
                "Version": "2012-10-17",
                "Statement": [{
                    "Action": "execute-api:Invoke",
                    "Effect": "Allow",
                    "Resource": method_arn
                }]
            },
            # Context is passed to the backend Lambda in event.requestContext.authorizer
            # Values MUST be strings, numbers, or booleans.
            "context": {
                "ownerId": str(principal_id),
                "email": str(claims.get('email', ''))
            }
        }
        return policy


    except Exception as e:
        logger.error(f"Authorization failed: {e}")
        # API Gateway expects 'Unauthorized' to return 401
        raise Exception('Unauthorized')
