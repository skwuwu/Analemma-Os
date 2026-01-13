"""
Skills API Lambda Handler - CRUD operations for Skills.

Endpoints:
- POST   /skills           - Create a new skill
- GET    /skills           - List skills for authenticated user
- GET    /skills/{id}      - Get a specific skill
- PUT    /skills/{id}      - Update a skill
- DELETE /skills/{id}      - Delete a skill
- GET    /skills/public    - List public/shared skills (marketplace)

All endpoints require authentication via JWT (Cognito).
"""

import json
import os
import logging
from typing import Optional, Dict, Any, Tuple

# Import repository
try:
    from src.services.skill_repository import SkillRepository, get_skill_repository
    from src.models.skill_models import validate_skill, create_default_skill
except ImportError:
    # Fallback for local testing
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from src.services.skill_repository import SkillRepository, get_skill_repository
    from src.models.skill_models import validate_skill, create_default_skill

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _get_owner_id(event: Dict) -> Optional[str]:
    """Extract owner ID from src.JWT claims."""
    try:
        return (event.get('requestContext', {})
                .get('authorizer', {})
                .get('jwt', {})
                .get('claims', {})
                .get('sub'))
    except Exception:
        return None


def _response(status_code: int, body: Any, headers: Dict = None) -> Dict:
    """Build Lambda proxy response."""
    resp = {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
            'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
            **(headers or {})
        },
        'body': json.dumps(body, ensure_ascii=False) if body else ''
    }
    return resp


def _parse_body(event: Dict) -> Tuple[Optional[Dict], Optional[str]]:
    """Parse request body, returning (parsed_body, error_message)."""
    raw_body = event.get('body')
    if not raw_body:
        return {}, None
    
    try:
        parsed = json.loads(raw_body)
        if not isinstance(parsed, dict):
            return None, 'Request body must be a JSON object'
        return parsed, None
    except json.JSONDecodeError as e:
        return None, f'Invalid JSON: {str(e)}'


def lambda_handler(event, context):
    """Main Lambda handler for Skills API."""
    # Handle CORS preflight
    http_method = event.get('httpMethod') or event.get('requestContext', {}).get('http', {}).get('method')
    if http_method == 'OPTIONS':
        return _response(200, None)
    
    # Extract path and method
    path = event.get('path') or event.get('rawPath', '')
    path_params = event.get('pathParameters') or {}
    skill_id = path_params.get('id')
    
    logger.info("Skills API: %s %s (skill_id=%s)", http_method, path, skill_id)
    
    # Get authenticated user
    owner_id = _get_owner_id(event)
    if not owner_id:
        return _response(401, {'error': 'Authentication required'})
    
    # Get repository
    try:
        repo = get_skill_repository()
    except Exception as e:
        logger.exception("Failed to initialize SkillRepository")
        return _response(500, {'error': 'Service initialization failed'})
    
    # Route to appropriate handler
    try:
        # Check for special routes first
        if path.endswith('/public'):
            return handle_list_public(repo, event)
        
        if skill_id:
            # Single skill operations
            if http_method == 'GET':
                return handle_get_skill(repo, owner_id, skill_id, event)
            elif http_method == 'PUT':
                return handle_update_skill(repo, owner_id, skill_id, event)
            elif http_method == 'DELETE':
                return handle_delete_skill(repo, owner_id, skill_id, event)
            else:
                return _response(405, {'error': f'Method {http_method} not allowed'})
        else:
            # Collection operations
            if http_method == 'GET':
                return handle_list_skills(repo, owner_id, event)
            elif http_method == 'POST':
                return handle_create_skill(repo, owner_id, event)
            else:
                return _response(405, {'error': f'Method {http_method} not allowed'})
                
    except Exception as e:
        logger.exception("Skills API error")
        return _response(500, {'error': str(e)})


def handle_create_skill(repo: SkillRepository, owner_id: str, event: Dict) -> Dict:
    """
    Create a new skill.
    
    Supports two skill types:
    - tool_based (default): Uses tool_definitions for discrete tools
    - subgraph_based: Contains a complete subgraph workflow (for canvas abstractions)
    """
    body, error = _parse_body(event)
    if error:
        return _response(400, {'error': error})
    
    # Validate required fields
    if not body.get('name'):
        return _response(400, {'error': 'name is required'})
    
    # Determine skill type
    skill_type = body.get('skill_type', 'tool_based')
    
    if skill_type == 'subgraph_based':
        # Create subgraph-based skill (from src.canvas abstraction)
        skill_data = _create_subgraph_skill_data(body, owner_id)
    else:
        # Create tool-based skill (default)
        skill_data = create_default_skill(
            name=body['name'],
            owner_id=owner_id,
            description=body.get('description', ''),
            tool_definitions=body.get('tool_definitions'),
            system_instructions=body.get('system_instructions', '')
        )
    
    # Override with any additional fields from src.body
    for key in ['category', 'tags', 'dependencies', 'required_api_keys', 
                'timeout_seconds', 'visibility']:
        if key in body:
            skill_data[key] = body[key]
    
    # Validate
    errors = validate_skill(skill_data)
    if errors:
        return _response(400, {'error': 'Validation failed', 'details': errors})
    
    # Create in DynamoDB
    try:
        created = repo.create_skill(skill_data)
        logger.info("Created skill: %s (type=%s, owner=%s)", 
                   created['skill_id'], skill_type, owner_id)
        return _response(201, created)
    except ValueError as e:
        return _response(409, {'error': str(e)})


def _create_subgraph_skill_data(body: Dict, owner_id: str) -> Dict:
    """
    Create skill data for a subgraph-based skill.
    
    This is called when users abstract canvas nodes into a reusable skill.
    """
    from src.models.skill_models import create_skill_id
    from datetime import datetime, timezone
    
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    
    # Validate subgraph_config is provided
    subgraph_config = body.get('subgraph_config')
    if not subgraph_config:
        raise ValueError("subgraph_config is required for skill_type='subgraph_based'")
    
    return {
        'skill_id': create_skill_id(body['name']),
        'version': body.get('version', '1.0.0'),
        'owner_id': owner_id,
        'visibility': body.get('visibility', 'private'),
        'name': body['name'],
        'description': body.get('description', ''),
        'category': body.get('category', 'custom'),
        'tags': body.get('tags', []),
        
        # Subgraph-specific fields
        'skill_type': 'subgraph_based',
        'subgraph_config': subgraph_config,
        'input_schema': body.get('input_schema', {}),
        'output_schema': body.get('output_schema', {}),
        
        # Empty tool_definitions (not used for subgraph skills)
        'tool_definitions': [],
        'system_instructions': '',
        
        # Defaults
        'dependencies': body.get('dependencies', []),
        'required_api_keys': body.get('required_api_keys', []),
        'required_permissions': body.get('required_permissions', []),
        'timeout_seconds': body.get('timeout_seconds', 300),
        'retry_config': body.get('retry_config', {'max_retries': 3, 'backoff_multiplier': 2}),
        'created_at': now,
        'updated_at': now,
        'status': 'active',
    }


def handle_get_skill(repo: SkillRepository, owner_id: str, skill_id: str, event: Dict) -> Dict:
    """Get a specific skill by ID."""
    # Check for version query param
    query_params = event.get('queryStringParameters') or {}
    version = query_params.get('version')
    
    if version:
        skill = repo.get_skill(skill_id, version)
    else:
        skill = repo.get_latest_skill(skill_id)
    
    if not skill:
        return _response(404, {'error': 'Skill not found'})
    
    # Check access: owner or public visibility
    if skill.get('owner_id') != owner_id and skill.get('visibility') != 'public':
        return _response(403, {'error': 'Access denied'})
    
    return _response(200, skill)


def handle_list_skills(repo: SkillRepository, owner_id: str, event: Dict) -> Dict:
    """List skills for the authenticated user."""
    query_params = event.get('queryStringParameters') or {}
    
    # Safe parsing of limit with validation
    try:
        limit = int(query_params.get('limit', 50))
        limit = max(1, min(limit, 100))  # Clamp to 1-100 range
    except (TypeError, ValueError):
        limit = 50
    
    status_filter = query_params.get('status')
    
    # Parse pagination key
    last_key = None
    if query_params.get('nextToken'):
        try:
            last_key = json.loads(query_params['nextToken'])
        except json.JSONDecodeError:
            pass
    
    result = repo.list_skills_by_owner(
        owner_id=owner_id,
        limit=limit,
        last_key=last_key,
        status_filter=status_filter
    )
    
    response_body = {
        'items': result['items'],
        'count': len(result['items'])
    }
    
    if result.get('last_key'):
        response_body['nextToken'] = json.dumps(result['last_key'])
    
    return _response(200, response_body)


def handle_update_skill(repo: SkillRepository, owner_id: str, skill_id: str, event: Dict) -> Dict:
    """Update an existing skill."""
    body, error = _parse_body(event)
    if error:
        return _response(400, {'error': error})
    
    # Get version from src.body or query params
    query_params = event.get('queryStringParameters') or {}
    version = body.get('version') or query_params.get('version')
    
    if not version:
        # Get latest version
        existing = repo.get_latest_skill(skill_id)
        if not existing:
            return _response(404, {'error': 'Skill not found'})
        version = existing['version']
    
    # Fields that can be updated
    allowed_updates = [
        'name', 'description', 'category', 'tags',
        'tool_definitions', 'system_instructions',
        'dependencies', 'required_api_keys', 'required_permissions',
        'timeout_seconds', 'retry_config', 'visibility', 'status'
    ]
    
    updates = {k: v for k, v in body.items() if k in allowed_updates}
    
    if not updates:
        return _response(400, {'error': 'No valid fields to update'})
    
    try:
        updated = repo.update_skill(
            skill_id=skill_id,
            version=version,
            updates=updates,
            owner_id=owner_id  # Validates ownership
        )
        logger.info("Updated skill: %s (owner=%s)", skill_id, owner_id)
        return _response(200, updated)
    except ValueError as e:
        return _response(404, {'error': str(e)})


def handle_delete_skill(repo: SkillRepository, owner_id: str, skill_id: str, event: Dict) -> Dict:
    """Delete a skill."""
    query_params = event.get('queryStringParameters') or {}
    version = query_params.get('version')
    
    if not version:
        # Get latest version
        existing = repo.get_latest_skill(skill_id)
        if not existing:
            return _response(404, {'error': 'Skill not found'})
        version = existing['version']
    
    success = repo.delete_skill(
        skill_id=skill_id,
        version=version,
        owner_id=owner_id  # Validates ownership
    )
    
    if success:
        logger.info("Deleted skill: %s:%s (owner=%s)", skill_id, version, owner_id)
        return _response(204, None)
    else:
        return _response(404, {'error': 'Skill not found or access denied'})


def handle_list_public(repo: SkillRepository, event: Dict) -> Dict:
    """
    List public/shared skills (marketplace).
    
    Uses VisibilityIndex GSI for efficient querying of public skills.
    Supports filtering by category and pagination.
    """
    query_params = event.get('queryStringParameters') or {}
    
    category = query_params.get('category')
    
    # Safe parsing of limit with validation
    try:
        limit = int(query_params.get('limit', 50))
        limit = max(1, min(limit, 100))  # Clamp to 1-100 range
    except (TypeError, ValueError):
        limit = 50
    
    sort_order = query_params.get('sort', 'desc')  # newest first by default
    
    # Parse pagination key
    last_key = None
    if query_params.get('nextToken'):
        try:
            last_key = json.loads(query_params['nextToken'])
        except json.JSONDecodeError:
            pass
    
    # Use the new GSI-based method
    result = repo.list_public_skills(
        limit=limit,
        last_key=last_key,
        category_filter=category,
        sort_order=sort_order
    )
    
    response_body = {
        'items': result['items'],
        'count': len(result['items'])
    }
    
    if result.get('last_key'):
        response_body['nextToken'] = json.dumps(result['last_key'])
    
    return _response(200, response_body)
