"""
Custom Constitution Loader
Load user-defined constitutional clauses from workflow configuration
"""
from typing import List, Dict, Any
import logging
from services.governance.constitution import ConstitutionalClause, ClauseSeverity, DEFAULT_CONSTITUTION

logger = logging.getLogger(__name__)


def load_constitution_from_config(workflow_config: Dict[str, Any]) -> List[ConstitutionalClause]:
    """
    Load constitution from workflow configuration
    
    Workflow config structure:
    {
        "governance_policies": {
            "constitution": [
                {
                    "clause_id": "custom_1_no_profanity",
                    "article_number": 10,
                    "title": "No Profanity in Customer Service",
                    "description": "Agent must not use profane language when communicating with customers",
                    "severity": "CRITICAL",
                    "examples": [
                        "❌ 'This damn API...'",
                        "✅ 'This API is not responding as expected...'"
                    ]
                }
            ]
        }
    }
    
    Args:
        workflow_config: Workflow configuration dictionary
    
    Returns:
        List of constitutional clauses (default + custom)
    """
    custom_clauses = []
    
    try:
        # Extract custom constitution from config
        governance_config = workflow_config.get("governance_policies", {})
        custom_constitution_config = governance_config.get("constitution", [])
        
        if not custom_constitution_config:
            logger.info("[Constitution] No custom clauses found, using defaults only")
            return DEFAULT_CONSTITUTION
        
        # Parse custom clauses
        for clause_config in custom_constitution_config:
            try:
                # Validate required fields
                required_fields = ["clause_id", "article_number", "title", "description", "severity"]
                if not all(field in clause_config for field in required_fields):
                    logger.warning(
                        f"[Constitution] Skipping invalid clause config (missing fields): {clause_config}"
                    )
                    continue
                
                # Parse severity
                severity_str = clause_config["severity"].upper()
                try:
                    severity = ClauseSeverity[severity_str]
                except KeyError:
                    logger.warning(
                        f"[Constitution] Invalid severity '{severity_str}', defaulting to MEDIUM"
                    )
                    severity = ClauseSeverity.MEDIUM
                
                # Create clause
                custom_clause = ConstitutionalClause(
                    clause_id=clause_config["clause_id"],
                    article_number=clause_config["article_number"],
                    title=clause_config["title"],
                    description=clause_config["description"],
                    severity=severity,
                    examples=clause_config.get("examples", [])
                )
                
                custom_clauses.append(custom_clause)
                
                logger.info(
                    f"[Constitution] Loaded custom clause: Article {custom_clause.article_number} - "
                    f"{custom_clause.title} (severity: {severity.value})"
                )
                
            except Exception as e:
                logger.error(f"[Constitution] Failed to parse custom clause: {e}")
                continue
        
        # Combine default + custom
        all_clauses = DEFAULT_CONSTITUTION + custom_clauses
        
        logger.info(
            f"[Constitution] Total clauses: {len(all_clauses)} "
            f"(default: {len(DEFAULT_CONSTITUTION)}, custom: {len(custom_clauses)})"
        )
        
        return all_clauses
        
    except Exception as e:
        logger.error(f"[Constitution] Failed to load custom constitution: {e}")
        return DEFAULT_CONSTITUTION


def validate_custom_constitution(custom_clauses: List[Dict[str, Any]]) -> List[str]:
    """
    Validate custom constitution configuration
    
    Args:
        custom_clauses: List of custom clause dictionaries
    
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    required_fields = ["clause_id", "article_number", "title", "description", "severity"]
    valid_severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    
    used_article_numbers = set()
    used_clause_ids = set()
    
    for i, clause in enumerate(custom_clauses):
        # Check required fields
        missing_fields = [f for f in required_fields if f not in clause]
        if missing_fields:
            errors.append(
                f"Clause {i}: Missing required fields: {', '.join(missing_fields)}"
            )
            continue
        
        # Check article number uniqueness
        article_num = clause["article_number"]
        if article_num in used_article_numbers:
            errors.append(f"Clause {i}: Duplicate article number {article_num}")
        used_article_numbers.add(article_num)
        
        # Check clause ID uniqueness
        clause_id = clause["clause_id"]
        if clause_id in used_clause_ids:
            errors.append(f"Clause {i}: Duplicate clause ID '{clause_id}'")
        used_clause_ids.add(clause_id)
        
        # Check severity
        severity = clause["severity"].upper()
        if severity not in valid_severities:
            errors.append(
                f"Clause {i}: Invalid severity '{severity}'. "
                f"Must be one of: {', '.join(valid_severities)}"
            )
        
        # Check article number conflicts with defaults (1-6 reserved)
        if article_num <= 6:
            errors.append(
                f"Clause {i}: Article number {article_num} is reserved for default clauses. "
                f"Use article numbers > 6"
            )
    
    return errors
