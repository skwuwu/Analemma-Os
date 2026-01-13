import os
import json
import logging
from decimal import Decimal
from typing import Dict, Any
import boto3
from boto3.dynamodb.conditions import Key
try:
    from src.common import statebag
except Exception:
    from src.common import statebag

# 공통 모듈에서 AWS 클라이언트 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource
    dynamodb = get_dynamodb_resource()
except ImportError:
    dynamodb = boto3.resource('dynamodb')

from src.common.constants import DynamoDBConfig

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

WORKFLOWS_TABLE = DynamoDBConfig.WORKFLOWS_TABLE
OWNER_INDEX = DynamoDBConfig.OWNER_ID_NAME_INDEX

table = dynamodb.Table(WORKFLOWS_TABLE)


def _json_default(value):
    if isinstance(value, Decimal):
        if value % 1 == 0:
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def lambda_handler(event: Dict[str, Any], context: object) -> Dict[str, Any]:
    """Return a single workflow by ownerId and name.

    Query params: ownerId (required), name (required)
    """
    # Normalize state-bag inputs if present
    event = statebag.normalize_event(event)

    # Handle OPTIONS request for CORS
    if event.get('httpMethod') == 'OPTIONS' or event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return {"statusCode": 200, "body": ""}
    
    try:
        params = event.get('queryStringParameters') or {}
        # Do NOT trust ownerId from src.query parameters. Owner identity must come
        # from src.the authenticated JWT `sub` claim.
        try:
            owner_id = (event.get('requestContext', {})
                        .get('authorizer', {})
                        .get('jwt', {})
                        .get('claims', {})
                        .get('sub'))
        except Exception:
            owner_id = None

        if not owner_id:
            return {
                'statusCode': 401,
                'body': json.dumps({'error': 'Authentication required'}, default=_json_default)
            }

        name = params.get('name')

        # Note: ownerId is now authenticated from src.JWT (above), not from src.query parameters
        # Name is optional. When name is omitted, return the owner's workflows (list)
        # — this keeps the endpoint useful for clients that want to list workflows 
        # under an owner without a separate call to the list endpoint.

        # Use GSI query only. If `name` is provided we query the composite
        # index (ownerId + name) which should return at most one item. If
        # `name` is omitted, query by ownerId alone and return a list of
        # workflows for that owner.
        # Use boto3's Key condition builder (imported at module top) for
        # constructing KeyConditionExpression. This is safe in Lambda and
        # test environments where boto3 is available.
        if name:
                logger.info(f"Querying composite GSI {OWNER_INDEX} for ownerId={owner_id} and name={name}")
                keycond = Key('ownerId').eq(owner_id) & Key('name').eq(name)
                # Include the 'name' attribute in the projection. Use ExpressionAttributeNames
                # to avoid reserved-keyword issues with the attribute name 'name'.
                resp = table.query(
                    IndexName=OWNER_INDEX,
                    KeyConditionExpression=keycond,
                    ProjectionExpression='workflowId, config, ownerId, #nm, createdAt, updatedAt, is_scheduled, next_run_time',
                    ExpressionAttributeNames={'#nm': 'name'}
                )
                items = resp.get('Items', [])

                if not items:
                    return {
                        'statusCode': 404,
                        'body': json.dumps({'error': 'Workflow not found'}, default=_json_default)
                    }

                it = items[0]
                cfg = it.get('config')
                if isinstance(cfg, str):
                    try:
                        cfg_parsed = json.loads(cfg)
                    except (json.JSONDecodeError, ValueError):
                        cfg_parsed = None
                else:
                    cfg_parsed = cfg if isinstance(cfg, dict) else None

                # Ensure returned config contains edge ids for frontend
                try:
                    from src.common.workflow_utils import ensure_edge_ids
                    if isinstance(cfg_parsed, dict):
                        ensure_edge_ids(cfg_parsed)
                except (ImportError, AttributeError, TypeError):
                    pass

                return {
                    'statusCode': 200,
                    'body': json.dumps({
                        'workflowId': it.get('workflowId'),
                        'name': it.get('name'),
                        'createdAt': it.get('createdAt'),
                        'updatedAt': it.get('updatedAt'),
                        'is_scheduled': it.get('is_scheduled'),
                        'next_run_time': it.get('next_run_time'),
                        'config': cfg_parsed
                    }, default=_json_default)
                }
        else:
            # name is not provided: return a list of workflows for owner
            logger.info(f"Listing workflows for ownerId={owner_id} (no name provided)")
            try:
                keycond = Key('ownerId').eq(owner_id)
                # Use ExpressionAttributeNames for 'name' because it can be a
                # DynamoDB reserved word in ProjectionExpression contexts.
                resp = table.query(
                    IndexName=OWNER_INDEX,
                    KeyConditionExpression=keycond,
                    ProjectionExpression='workflowId, ownerId, #nm, createdAt, updatedAt, is_scheduled, next_run_time',
                    ExpressionAttributeNames={'#nm': 'name'}
                )
                items = resp.get('Items', [])

                if not items:
                    return build_response(404, {'error': 'No workflows found for owner'})

                # Return list of simplified workflow summaries
                workflows = []
                for it in items:
                    workflows.append({
                        'workflowId': it.get('workflowId'),
                        'name': it.get('name'),
                        'createdAt': it.get('createdAt'),
                        'updatedAt': it.get('updatedAt'),
                        'is_scheduled': it.get('is_scheduled'),
                        'next_run_time': it.get('next_run_time')
                    })

                return build_response(200, {'workflows': workflows})
            except Exception as e:
                logger.exception(f"Error querying GSI {OWNER_INDEX}")
                return build_response(500, {'error': str(e)})

    except Exception as e:
        logger.exception('Error in get_workflow_by_name')
        return build_response(500, {'error': str(e)})
