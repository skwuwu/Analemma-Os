import json
import os
import boto3
import logging
from botocore.exceptions import ClientError
try:
    from src.common import statebag
except Exception:
    from src.common import statebag

from src.common.http_utils import build_response

try:
    from src.common.exec_status_helper import (
        build_status_payload,
        ExecutionForbidden,
        ExecutionNotFound,
    )
except ImportError:
    try:
        from src.common.exec_status_helper import (
            build_status_payload,
            ExecutionForbidden,
            ExecutionNotFound,
        )
    except ImportError:
        # Last resort: define minimal fallbacks
        def build_status_payload(*args, **kwargs):
            return {"error": "exec_status_helper not available"}
        class ExecutionForbidden(Exception):
            pass
        class ExecutionNotFound(Exception):
            pass

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)




def lambda_handler(event, context):
    """
    Secure handler for GET /status/{id} or GET /status?executionArn=...

    - Requires API Gateway JWT authorizer to populate
      requestContext.authorizer.jwt.claims.sub (owner id)
    - Calls Step Functions DescribeExecution and compares execution input.ownerId
      to the authenticated owner id. If mismatch, returns 404 to avoid leaking
      existence of other tenants' executions.
    """
    # Normalize potential state-bag input (non-destructive)
    event = statebag.normalize_event(event)

    if event.get('httpMethod') == 'OPTIONS' or event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {"statusCode": 200,  "body": ""}

    try:
        # --- 1. Get executionArn from src.path or querystring ---
        execution_arn = event.get('pathParameters', {}).get('id')
        if not execution_arn:
            q = event.get('queryStringParameters') or {}
            execution_arn = q.get('executionArn') or q.get('execution_arn')

        if not execution_arn:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Missing executionArn in path or query string'})
            }

        # --- 2. Authentication: require JWT subject (owner id) ---
        try:
            owner_id = (event.get('requestContext', {})
                        .get('authorizer', {})
                        .get('jwt', {})
                        .get('claims', {})
                        .get('sub'))
        except Exception:
            owner_id = None

        if not owner_id:
            return build_response(403, {'error': 'Forbidden: Not authenticated'})

        try:
            response_payload = build_status_payload(execution_arn, owner_id)
        except ExecutionNotFound:
            return build_response(404, {'error': 'Not Found'})
        except ExecutionForbidden:
            logger.warning(
                "Authorization Failure: User %s tried to access execution %s",
                owner_id,
                execution_arn,
            )
            return build_response(404, {'error': 'Not Found'})
        except ClientError as e:
            logger.exception('DescribeExecution error for %s: %s', execution_arn, e)
            return build_response(500, {'error': str(e)})
        except Exception as e:
            logger.exception('Handler error: %s', e)
            return build_response(500, {'error': str(e)})

        return build_response(200, response_payload)

    except Exception as e:
        logger.exception('Handler error: %s', e)
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }
