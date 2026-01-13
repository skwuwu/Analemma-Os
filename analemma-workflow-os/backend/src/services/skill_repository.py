"""
Skill Repository - DynamoDB operations for Skills.

This module provides CRUD operations for the Skills table,
following the same pattern as workflow_repository.py.

Table Schema:
- Primary Key: skillId (HASH) + version (RANGE)
- GSI: OwnerIdIndex (ownerId HASH, skillId RANGE)
- GSI: CategoryIndex (category HASH, skillId RANGE)
- GSI: VisibilityIndex (visibility HASH, updated_at RANGE)
      → For marketplace listing of public skills

CloudFormation/SAM template should include:
  VisibilityIndex:
    KeySchema:
      - AttributeName: visibility
        KeyType: HASH
      - AttributeName: updated_at
        KeyType: RANGE
    Projection:
      ProjectionType: ALL
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

import boto3
from src.common.constants import DynamoDBConfig
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

# Common module for AWS clients
try:
    from src.common.aws_clients import get_dynamodb_resource
    _dynamodb = get_dynamodb_resource()
except ImportError:
    _dynamodb = boto3.resource('dynamodb')

logger = logging.getLogger(__name__)

SKILLS_TABLE = os.environ.get('SKILLS_TABLE', 'Skills')


class SkillRepository:
    """
    Repository for Skill CRUD operations in DynamoDB.
    
    Responsibilities:
    - create_skill: Create a new skill
    - get_skill: Get a skill by ID and version
    - get_latest_skill: Get the latest version of a skill
    - list_skills_by_owner: List all skills for a user
    - list_skills_by_category: List skills in a category
    - update_skill: Update an existing skill
    - delete_skill: Delete a skill
    - hydrate_skills: Load multiple skills for context injection
    """

    def __init__(self, dynamodb_resource=None):
        self.dynamodb = dynamodb_resource or _dynamodb
        self.skills_table = self.dynamodb.Table(SKILLS_TABLE)

    def create_skill(self, skill: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new skill in DynamoDB.
        
        Args:
            skill: Skill data conforming to SkillSchema
            
        Returns:
            The created skill with any server-side modifications
            
        Raises:
            ClientError: If a skill with the same ID and version exists
        """
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        
        # Ensure required fields
        if not skill.get('skill_id'):
            raise ValueError("skill_id is required")
        if not skill.get('version'):
            skill['version'] = "1.0.0"
        if not skill.get('owner_id'):
            raise ValueError("owner_id is required")
            
        # Set timestamps
        skill['created_at'] = now
        skill['updated_at'] = now
        
        # Set defaults
        skill.setdefault('status', 'active')
        skill.setdefault('visibility', 'private')
        skill.setdefault('tool_definitions', [])
        skill.setdefault('dependencies', [])
        skill.setdefault('required_api_keys', [])
        skill.setdefault('timeout_seconds', 300)
        
        try:
            # Use condition to prevent overwrite
            self.skills_table.put_item(
                Item={
                    'skillId': skill['skill_id'],
                    'version': skill['version'],
                    'ownerId': skill['owner_id'],
                    **{k: v for k, v in skill.items() 
                       if k not in ('skill_id', 'owner_id')}
                },
                ConditionExpression='attribute_not_exists(skillId) AND attribute_not_exists(version)'
            )
            return skill
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
                raise ValueError(f"Skill {skill['skill_id']}:{skill['version']} already exists")
            logger.exception('DynamoDB create_skill error')
            raise

    def get_skill(self, skill_id: str, version: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific skill by ID and version.
        
        Args:
            skill_id: The skill identifier
            version: The version string
            
        Returns:
            Skill data or None if not found
        """
        try:
            resp = self.skills_table.get_item(
                Key={'skillId': skill_id, 'version': version}
            )
            item = resp.get('Item')
            if item:
                return self._normalize_item(item)
            return None
        except ClientError:
            logger.exception('DynamoDB get_skill error')
            raise

    def get_latest_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the latest version of a skill.
        
        Uses a query with ScanIndexForward=False to get the highest version.
        Note: This assumes versions are sortable strings (e.g., semantic versioning).
        
        Args:
            skill_id: The skill identifier
            
        Returns:
            Latest skill version or None if not found
        """
        try:
            resp = self.skills_table.query(
                KeyConditionExpression=Key('skillId').eq(skill_id),
                ScanIndexForward=False,  # Descending order
                Limit=1
            )
            items = resp.get('Items', [])
            if items:
                return self._normalize_item(items[0])
            return None
        except ClientError:
            logger.exception('DynamoDB get_latest_skill error')
            raise

    def list_skills_by_owner(
        self, 
        owner_id: str, 
        limit: int = 50,
        last_key: Optional[Dict] = None,
        status_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        List all skills owned by a user.
        
        Args:
            owner_id: The owner's user ID
            limit: Maximum number of results
            last_key: Pagination key from src.previous query
            status_filter: Optional filter for skill status
            
        Returns:
            Dict with 'items' list and optional 'last_key' for pagination
        """
        try:
            query_kwargs = {
                'IndexName': DynamoDBConfig.OWNER_ID_INDEX,
                'KeyConditionExpression': Key('ownerId').eq(owner_id),
                'Limit': limit
            }
            
            if last_key:
                query_kwargs['ExclusiveStartKey'] = last_key
                
            if status_filter:
                query_kwargs['FilterExpression'] = Attr('status').eq(status_filter)
            
            resp = self.skills_table.query(**query_kwargs)
            
            return {
                'items': [self._normalize_item(item) for item in resp.get('Items', [])],
                'last_key': resp.get('LastEvaluatedKey')
            }
        except ClientError:
            logger.exception('DynamoDB list_skills_by_owner error')
            raise

    def list_skills_by_category(
        self, 
        category: str, 
        limit: int = 50,
        last_key: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        List skills in a specific category.
        
        Args:
            category: The category to filter by
            limit: Maximum number of results
            last_key: Pagination key
            
        Returns:
            Dict with 'items' list and optional 'last_key' for pagination
        """
        try:
            query_kwargs = {
                'IndexName': DynamoDBConfig.CATEGORY_INDEX,
                'KeyConditionExpression': Key('category').eq(category),
                'Limit': limit
            }
            
            if last_key:
                query_kwargs['ExclusiveStartKey'] = last_key
            
            resp = self.skills_table.query(**query_kwargs)
            
            return {
                'items': [self._normalize_item(item) for item in resp.get('Items', [])],
                'last_key': resp.get('LastEvaluatedKey')
            }
        except ClientError:
            logger.exception('DynamoDB list_skills_by_category error')
            raise

    def list_public_skills(
        self,
        limit: int = 50,
        last_key: Optional[Dict] = None,
        category_filter: Optional[str] = None,
        sort_order: str = 'desc'
    ) -> Dict[str, Any]:
        """
        List public skills for the marketplace.
        
        Uses VisibilityIndex GSI to efficiently query public skills
        without a full table scan. Results are sorted by updated_at.
        
        Args:
            limit: Maximum number of results
            last_key: Pagination key from src.previous query
            category_filter: Optional filter by category
            sort_order: 'asc' or 'desc' (default: desc for newest first)
            
        Returns:
            Dict with 'items' list and optional 'last_key' for pagination
            
        Note:
            Requires VisibilityIndex GSI with:
            - Partition Key: visibility (String)
            - Sort Key: updated_at (String, ISO 8601)
        """
        try:
            query_kwargs = {
                'IndexName': DynamoDBConfig.VISIBILITY_INDEX,
                'KeyConditionExpression': Key('visibility').eq('public'),
                'ScanIndexForward': sort_order.lower() == 'asc',
                'Limit': limit
            }
            
            if last_key:
                query_kwargs['ExclusiveStartKey'] = last_key
            
            # Apply category filter if provided
            filter_expr = Attr('status').eq('active')
            if category_filter:
                filter_expr = filter_expr & Attr('category').eq(category_filter)
            query_kwargs['FilterExpression'] = filter_expr
            
            resp = self.skills_table.query(**query_kwargs)
            
            return {
                'items': [self._normalize_item(item) for item in resp.get('Items', [])],
                'last_key': resp.get('LastEvaluatedKey')
            }
        except ClientError as e:
            # Check if GSI doesn't exist (for graceful degradation)
            error_code = e.response.get('Error', {}).get('Code', '')
            error_msg = str(e)
            if error_code == 'ValidationException' and 'VisibilityIndex' in error_msg:
                logger.warning('VisibilityIndex GSI not found, falling back to scan')
                return self._list_public_skills_fallback(limit, last_key, category_filter)
            logger.exception('DynamoDB list_public_skills error')
            raise

    def _list_public_skills_fallback(
        self,
        limit: int = 50,
        last_key: Optional[Dict] = None,
        category_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fallback method when VisibilityIndex GSI is not available.
        
        Uses Scan with filter expressions. This is less efficient but
        allows the system to work before GSI is deployed.
        
        WARNING: This should only be used temporarily during migration.
        """
        logger.warning('Using scan fallback for public skills - deploy VisibilityIndex GSI for production')
        
        try:
            filter_expr = Attr('visibility').eq('public') & Attr('status').eq('active')
            
            if category_filter:
                filter_expr = filter_expr & Attr('category').eq(category_filter)
            
            scan_kwargs = {
                'FilterExpression': filter_expr,
                'Limit': limit * 3  # Over-fetch to compensate for filtering
            }
            
            if last_key:
                scan_kwargs['ExclusiveStartKey'] = last_key
            
            resp = self.skills_table.scan(**scan_kwargs)
            items = [self._normalize_item(item) for item in resp.get('Items', [])]
            
            # Sort by updated_at descending
            items.sort(key=lambda x: x.get('updated_at', ''), reverse=True)
            
            # Trim to limit
            items = items[:limit]
            
            return {
                'items': items,
                'last_key': resp.get('LastEvaluatedKey')
            }
        except ClientError:
            logger.exception('DynamoDB scan fallback error')
            raise

    def update_skill(
        self, 
        skill_id: str, 
        version: str, 
        updates: Dict[str, Any],
        owner_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update an existing skill.
        
        Args:
            skill_id: The skill identifier
            version: The version to update
            updates: Dict of fields to update
            owner_id: If provided, validates ownership before update
            
        Returns:
            The updated skill data
        """
        # Build update expression
        update_parts = []
        expr_attr_values = {}
        expr_attr_names = {}
        
        # Always update timestamp
        updates['updated_at'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        
        for key, value in updates.items():
            # Skip primary key fields
            if key in ('skill_id', 'version', 'skillId'):
                continue
            
            # Handle reserved words
            attr_name = f"#{key}"
            attr_value = f":{key}"
            update_parts.append(f"{attr_name} = {attr_value}")
            expr_attr_names[attr_name] = key
            expr_attr_values[attr_value] = value
        
        if not update_parts:
            # Nothing to update
            return self.get_skill(skill_id, version)
        
        update_expression = "SET " + ", ".join(update_parts)
        
        try:
            condition = "attribute_exists(skillId)"
            if owner_id:
                condition += " AND ownerId = :owner_id"
                expr_attr_values[':owner_id'] = owner_id
            
            resp = self.skills_table.update_item(
                Key={'skillId': skill_id, 'version': version},
                UpdateExpression=update_expression,
                ConditionExpression=condition,
                ExpressionAttributeNames=expr_attr_names,
                ExpressionAttributeValues=expr_attr_values,
                ReturnValues='ALL_NEW'
            )
            return self._normalize_item(resp.get('Attributes', {}))
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
                raise ValueError(f"Skill not found or access denied: {skill_id}:{version}")
            logger.exception('DynamoDB update_skill error')
            raise

    def delete_skill(
        self, 
        skill_id: str, 
        version: str,
        owner_id: Optional[str] = None
    ) -> bool:
        """
        Delete a skill.
        
        Args:
            skill_id: The skill identifier
            version: The version to delete
            owner_id: If provided, validates ownership before delete
            
        Returns:
            True if deleted, False if not found
        """
        try:
            condition_kwargs = {}
            if owner_id:
                condition_kwargs['ConditionExpression'] = 'ownerId = :owner_id'
                condition_kwargs['ExpressionAttributeValues'] = {':owner_id': owner_id}
            
            self.skills_table.delete_item(
                Key={'skillId': skill_id, 'version': version},
                **condition_kwargs
            )
            return True
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
                return False
            logger.exception('DynamoDB delete_skill error')
            raise

    def _batch_get_skills(self, keys: List[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
        """
        Batch get skills by their keys using BatchGetItem.
        
        This is more efficient than individual get_item calls when loading multiple skills.
        BatchGetItem supports up to 100 items per request.
        
        Args:
            keys: List of {'skillId': ..., 'version': ...} dicts
            
        Returns:
            Dict mapping "skillId:version" to normalized skill data
        """
        if not keys:
            return {}
            
        result = {}
        # BatchGetItem supports max 100 keys per request
        batch_size = 100
        
        for i in range(0, len(keys), batch_size):
            batch_keys = keys[i:i + batch_size]
            
            try:
                resp = self.dynamodb.batch_get_item(
                    RequestItems={
                        SKILLS_TABLE: {
                            'Keys': batch_keys
                        }
                    }
                )
                
                items = resp.get('Responses', {}).get(SKILLS_TABLE, [])
                for item in items:
                    skill_id = item.get('skillId')
                    version = item.get('version')
                    key = f"{skill_id}:{version}"
                    result[key] = self._normalize_item(item)
                    
                # Handle unprocessed keys (throttling)
                unprocessed = resp.get('UnprocessedKeys', {}).get(SKILLS_TABLE, {}).get('Keys', [])
                if unprocessed:
                    logger.warning(f"BatchGetItem returned {len(unprocessed)} unprocessed keys")
                    # Retry unprocessed keys individually
                    for key_dict in unprocessed:
                        skill = self.get_skill(key_dict['skillId'], key_dict['version'])
                        if skill:
                            result[f"{key_dict['skillId']}:{key_dict['version']}"] = skill
                            
            except ClientError:
                logger.exception('BatchGetItem error, falling back to individual gets')
                for key_dict in batch_keys:
                    skill = self.get_skill(key_dict['skillId'], key_dict['version'])
                    if skill:
                        result[f"{key_dict['skillId']}:{key_dict['version']}"] = skill
                        
        return result

    def hydrate_skills(
        self, 
        skill_refs: List[str],
        _visited: Optional[set] = None
    ) -> Dict[str, Dict[str, Any]]:
        """
        Load multiple skills for Context Hydration.
        
        This method efficiently loads multiple skills and resolves their dependencies.
        Uses BatchGetItem for versioned skills to reduce DynamoDB calls.
        Used during workflow execution to inject skill context.
        
        Args:
            skill_refs: List of skill IDs (optionally with version: "skill_id:version")
            _visited: Internal parameter for circular dependency detection
            
        Returns:
            Dict mapping skill_id to hydrated skill data
            
        Raises:
            ValueError: If circular dependency is detected
        """
        # Initialize visited set for circular dependency detection
        if _visited is None:
            _visited = set()
        
        result = {}
        versioned_refs = []  # For BatchGetItem
        unversioned_refs = []  # Need individual queries
        
        # Parse and categorize skill refs
        for ref in skill_refs:
            if ':' in ref:
                skill_id, version = ref.split(':', 1)
                if version:  # Has actual version
                    versioned_refs.append((skill_id, version, ref))
                else:  # Empty version after colon
                    unversioned_refs.append((skill_id, ref))
            else:
                unversioned_refs.append((ref, ref))
        
        # Check all refs for circular dependencies first
        for skill_id, version, ref_key in versioned_refs:
            if ref_key in _visited:
                logger.warning(f"Circular dependency detected: {ref_key}")
                raise ValueError(f"Circular skill dependency detected: {ref_key}")
            _visited.add(ref_key)
            
        for skill_id, ref_key in unversioned_refs:
            if ref_key in _visited:
                logger.warning(f"Circular dependency detected: {ref_key}")
                raise ValueError(f"Circular skill dependency detected: {ref_key}")
            _visited.add(ref_key)
        
        # Batch get versioned skills
        if versioned_refs:
            batch_keys = [
                {'skillId': skill_id, 'version': version}
                for skill_id, version, _ in versioned_refs
            ]
            batch_results = self._batch_get_skills(batch_keys)
            
            for skill_id, version, ref_key in versioned_refs:
                skill = batch_results.get(f"{skill_id}:{version}")
                if skill:
                    result[skill_id] = self._build_hydrated_skill(skill, skill_id, _visited)
        
        # Load unversioned skills individually (need Query for latest)
        for skill_id, ref_key in unversioned_refs:
            skill = self.get_latest_skill(skill_id)
            if skill:
                result[skill_id] = self._build_hydrated_skill(skill, skill_id, _visited)
        
        return result
    
    def _build_hydrated_skill(
        self, 
        skill: Dict[str, Any], 
        skill_id: str, 
        _visited: set
    ) -> Dict[str, Any]:
        """Build a hydrated skill dict with resolved dependencies."""
        hydrated = {
            'skill_id': skill['skill_id'],
            'version': skill['version'],
            'name': skill.get('name', skill_id),
            'tool_definitions': skill.get('tool_definitions', []),
            'system_instructions': skill.get('system_instructions', ''),
            'resolved_dependencies': {}
        }
        
        # Recursively load dependencies with visited tracking
        deps = skill.get('dependencies', [])
        if deps:
            dep_refs = [
                f"{d['skill_id']}:{d.get('version', '')}" if d.get('version') 
                else d['skill_id']
                for d in deps
            ]
            # Pass original visited set to detect cycles (not a copy!)
            dep_skills = self.hydrate_skills(dep_refs, _visited)
            hydrated['resolved_dependencies'] = dep_skills
            
        return hydrated

    # -------------------------------------------------------------------------
    # Subgraph-based Skills
    # -------------------------------------------------------------------------
    
    def save_subgraph_as_skill(
        self,
        subgraph_def: Dict[str, Any],
        owner_id: str,
        name: str,
        description: str = "",
        input_schema: Optional[Dict[str, Any]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        category: str = "custom",
        tags: Optional[List[str]] = None,
        visibility: str = "private"
    ) -> Dict[str, Any]:
        """
        Save a subgraph definition as a reusable Skill.
        
        This allows users to package grouped nodes as a skill that can be:
        - Reused across multiple workflows
        - Shared with other users (if visibility is public)
        - Versioned and updated independently
        
        Args:
            subgraph_def: The subgraph definition (nodes, edges, subgraphs)
            owner_id: The owner's user ID
            name: Human-readable skill name
            description: Skill description
            input_schema: Expected input fields
            output_schema: Output fields produced
            category: Skill category for organization
            tags: Searchable tags
            visibility: "private", "public", or "organization"
            
        Returns:
            The created skill data
        """
        from src.models.skill_models import create_skill_id
        from datetime import datetime, timezone
        
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        skill_data = {
            'skill_id': create_skill_id(name),
            'version': '1.0.0',
            'owner_id': owner_id,
            'visibility': visibility,
            'name': name,
            'description': description,
            'category': category,
            'tags': tags or [],
            
            # Subgraph-specific fields
            'skill_type': 'subgraph_based',
            'subgraph_config': subgraph_def,
            'input_schema': input_schema or {},
            'output_schema': output_schema or {},
            
            # Empty tool_definitions (not used for subgraph skills)
            'tool_definitions': [],
            'system_instructions': '',
            
            # Defaults
            'dependencies': [],
            'required_api_keys': [],
            'required_permissions': [],
            'timeout_seconds': 300,
            'retry_config': {'max_retries': 3, 'backoff_multiplier': 2},
            'created_at': now,
            'updated_at': now,
            'status': 'active',
        }
        
        return self.create_skill(skill_data)
    
    def get_subgraph_config(self, skill_id: str, version: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Get the subgraph configuration from src.a skill.
        
        Args:
            skill_id: The skill identifier
            version: Optional version (defaults to latest)
            
        Returns:
            The subgraph_config if the skill is subgraph_based, None otherwise
        """
        if version:
            skill = self.get_skill(skill_id, version)
        else:
            skill = self.get_latest_skill(skill_id)
        
        if not skill:
            return None
        
        if skill.get('skill_type') != 'subgraph_based':
            logger.warning(f"Skill {skill_id} is not subgraph_based (type: {skill.get('skill_type')})")
            return None
        
        return skill.get('subgraph_config')
    
    def list_subgraph_skills(
        self,
        owner_id: str,
        limit: int = 50,
        include_public: bool = False
    ) -> List[Dict[str, Any]]:
        """
        List subgraph-based skills for a user.
        
        Args:
            owner_id: The owner's user ID
            limit: Maximum results
            include_public: Whether to include public skills
            
        Returns:
            List of subgraph skills (metadata only, not full subgraph_config)
        """
        result = self.list_skills_by_owner(owner_id, limit=limit)
        skills = result.get('items', [])
        
        # Filter to subgraph_based skills
        subgraph_skills = [
            {
                'skill_id': s['skill_id'],
                'version': s['version'],
                'name': s.get('name', ''),
                'description': s.get('description', ''),
                'category': s.get('category', ''),
                'input_schema': s.get('input_schema', {}),
                'output_schema': s.get('output_schema', {}),
                'visibility': s.get('visibility', 'private'),
                'created_at': s.get('created_at', ''),
            }
            for s in skills
            if s.get('skill_type') == 'subgraph_based'
        ]
        
        return subgraph_skills

    def _normalize_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize DynamoDB item to match SkillSchema field names.
        
        DynamoDB uses 'skillId' and 'ownerId', but our schema uses
        'skill_id' and 'owner_id' for consistency.
        """
        if not item:
            return item
            
        # Map DynamoDB field names to schema field names
        mapping = {
            'skillId': 'skill_id',
            'ownerId': 'owner_id',
        }
        
        result = {}
        for key, value in item.items():
            new_key = mapping.get(key, key)
            result[new_key] = value
            
        return result


# Singleton instance for convenience
_repository: Optional[SkillRepository] = None


def get_skill_repository() -> SkillRepository:
    """Get or create the singleton SkillRepository instance."""
    global _repository
    if _repository is None:
        _repository = SkillRepository()
    return _repository


__all__ = ['SkillRepository', 'get_skill_repository']
