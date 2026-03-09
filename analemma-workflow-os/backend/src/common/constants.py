"""
Centralized management of constants and configuration values used across the backend
Remove magic numbers and centralized configuration management

Usage:
    from src.common.constants import TTLConfig, QuotaLimits, ModelPricing
    
    # TTL settings
    ttl = int(time.time()) + TTLConfig.WEBSOCKET_CONNECTION
    
    # Quota limits
    limit = QuotaLimits.get_workflow_limit(subscription_plan, stage_name)
"""

import os
from decimal import Decimal
from typing import ClassVar, Dict, Any, FrozenSet, List, Optional
from enum import Enum


class TTLConfig:
    """TTL (Time To Live) constants"""

    # WebSocket connection TTL (2 hours)
    WEBSOCKET_CONNECTION: ClassVar[int] = 7200

    # Task Token TTL (1 day)
    TASK_TOKEN_DEFAULT: ClassVar[int] = 86400

    # Pending Notification TTL (30 days)
    PENDING_NOTIFICATION: ClassVar[int] = 2592000

    # Execution Record TTL (90 days)
    EXECUTION_RECORD: ClassVar[int] = 90 * 24 * 3600

    # Pricing Cache TTL (1 hour)
    PRICING_CACHE: ClassVar[int] = 3600


class QuotaLimits:
    """Usage quota limit constants"""

    # Free tier limits
    FREE_TIER_DEV: ClassVar[int] = 10000
    FREE_TIER_PROD: ClassVar[int] = 50

    # Premium tier limit
    PREMIUM_TIER: ClassVar[int] = 10**9

    # Sampling limits
    USAGE_COLLECTION_SAMPLE_SIZE: ClassVar[int] = 50
    USAGE_COLLECTION_MAX_DEPTH: ClassVar[int] = 10

    # Max output size (1MB)
    MAX_OUTPUT_SIZE_BYTES: ClassVar[int] = 1024 * 1024
    
    @classmethod
    def get_workflow_limit(cls, subscription_plan: str, stage_name: str) -> int:
        """Return workflow limit based on subscription plan and stage."""
        if subscription_plan == 'free':
            return cls.FREE_TIER_DEV if stage_name == 'dev' else cls.FREE_TIER_PROD
        else:
            return cls.PREMIUM_TIER


class ModelPricing:
    """LLM model pricing defaults (overridable via Parameter Store)"""

    DEFAULT_MODELS: ClassVar[Dict[str, Dict[str, Decimal]]] = {
        "gpt-4": {
            "input_per_1k": Decimal("0.03"),
            "output_per_1k": Decimal("0.06")
        },
        "gpt-4-turbo": {
            "input_per_1k": Decimal("0.01"),
            "output_per_1k": Decimal("0.03")
        },
        "gpt-3.5-turbo": {
            "input_per_1k": Decimal("0.002"),
            "output_per_1k": Decimal("0.002")
        },
        "claude-3": {
            "input_per_1k": Decimal("0.015"),
            "output_per_1k": Decimal("0.075")
        },
        "claude-2": {
            "input_per_1k": Decimal("0.008"),
            "output_per_1k": Decimal("0.024")
        },
        "gemini-pro": {
            "input_per_1k": Decimal("0.001"),
            "output_per_1k": Decimal("0.002")
        }
    }

    # Fallback model (used for unknown models)
    DEFAULT_MODEL: ClassVar[str] = "gpt-3.5-turbo"

    # Token cost calculation base unit
    TOKENS_PER_THOUSAND: ClassVar[Decimal] = Decimal("1000")

    # Cost rounding precision (microcent granularity)
    COST_PRECISION: ClassVar[Decimal] = Decimal("0.000001")


class LLMModels:
    """
    Consolidated LLM model ID constants.

    Usage:
        from src.common.constants import LLMModels

        model_id = LLMModels.CLAUDE_3_HAIKU
    """

    # AWS Bedrock - Claude models
    CLAUDE_3_HAIKU: ClassVar[str] = os.getenv("HAIKU_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
    CLAUDE_3_SONNET: ClassVar[str] = os.getenv("SONNET_MODEL_ID", "anthropic.claude-3-sonnet-20240229-v1:0")
    CLAUDE_3_OPUS: ClassVar[str] = os.getenv("OPUS_MODEL_ID", "anthropic.claude-3-opus-20240229-v1:0")

    # Google Gemini models
    GEMINI_2_0_FLASH: ClassVar[str] = os.getenv("GEMINI_FLASH_2_MODEL_ID", "gemini-2.0-flash")
    GEMINI_1_5_PRO: ClassVar[str] = os.getenv("GEMINI_PRO_MODEL_ID", "gemini-1.5-pro-latest")
    GEMINI_1_5_FLASH: ClassVar[str] = os.getenv("GEMINI_FLASH_MODEL_ID", "gemini-1.5-flash")
    GEMINI_1_5_FLASH_8B: ClassVar[str] = os.getenv("GEMINI_FLASH_8B_MODEL_ID", "gemini-1.5-flash-8b")

    # Default model aliases
    DEFAULT_ANALYSIS: ClassVar[str] = CLAUDE_3_HAIKU  # Fast analysis
    DEFAULT_REASONING: ClassVar[str] = GEMINI_1_5_PRO  # Complex reasoning
    DEFAULT_REALTIME: ClassVar[str] = GEMINI_1_5_FLASH  # Real-time collaboration


class PayloadLimits:
    """AWS Step Functions / Lambda payload size limits.

    All threshold constants related to 256KB SFN payload limit should
    reference this class instead of using scattered magic numbers.
    """

    # Hard limit imposed by AWS Step Functions
    SFN_MAX_BYTES: ClassVar[int] = 256 * 1024  # 256 KB

    # Safe thresholds (leave margin for AWS wrapper overhead ~15-76 KB)
    SAFE_THRESHOLD_BYTES: ClassVar[int] = 180 * 1024   # 180 KB — general segment runner
    SAFE_THRESHOLD_KB: ClassVar[int] = 180

    AGGREGATOR_SAFE_BYTES: ClassVar[int] = 120 * 1024  # 120 KB — aggregator path (extra margin)

    # S3 offload trigger (state_data_manager default)
    OFFLOAD_THRESHOLD_KB: ClassVar[int] = int(os.environ.get('MAX_PAYLOAD_SIZE_KB', '200'))

    # EventBridge payload limit (same as SFN)
    EVENTBRIDGE_MAX_BYTES: ClassVar[int] = 256 * 1024


class HTTPStatusCodes:
    """HTTP status code constants"""

    # Success
    OK: ClassVar[int] = 200
    CREATED: ClassVar[int] = 201

    # Client errors
    BAD_REQUEST: ClassVar[int] = 400
    UNAUTHORIZED: ClassVar[int] = 401
    FORBIDDEN: ClassVar[int] = 403
    NOT_FOUND: ClassVar[int] = 404
    CONFLICT: ClassVar[int] = 409
    TOO_MANY_REQUESTS: ClassVar[int] = 429

    # Server errors
    INTERNAL_SERVER_ERROR: ClassVar[int] = 500
    BAD_GATEWAY: ClassVar[int] = 502
    SERVICE_UNAVAILABLE: ClassVar[int] = 503


class RetryConfig:
    """Retry configuration constants"""

    # Default retry interval (seconds)
    DEFAULT_RETRY_AFTER: ClassVar[int] = 5

    # DynamoDB throttling retry interval
    DYNAMODB_THROTTLE_RETRY: ClassVar[int] = 5
    DYNAMODB_THROUGHPUT_RETRY: ClassVar[int] = 10

    # S3 retry interval
    S3_SLOWDOWN_RETRY: ClassVar[int] = 5

    # LLM API retry interval
    LLM_RATE_LIMIT_RETRY: ClassVar[int] = 60


class WorkflowConfig:
    """Workflow configuration constants"""

    # Workflow ID hash length
    WORKFLOW_ID_HASH_LENGTH: ClassVar[int] = 32

    # Workflow name salt
    WORKFLOW_NAME_SALT: ClassVar[str] = "analemma_workflow_v1"

    # S3 state offload threshold (default 250KB)
    DEFAULT_INLINE_THRESHOLD: ClassVar[int] = 250000

    # Message window size
    DEFAULT_MESSAGES_WINDOW: ClassVar[int] = 20


class LoggingConfig:
    """Logging configuration constants"""

    # Default log level
    DEFAULT_LOG_LEVEL: ClassVar[str] = "INFO"

    # Debug log max length
    DEBUG_LOG_MAX_LENGTH: ClassVar[int] = 2000

    # Service name
    DEFAULT_SERVICE_NAME: ClassVar[str] = "analemma-backend"


class SecurityConfig:
    """Security configuration constants"""

    # JWT claim key
    OWNER_ID_CLAIM: ClassVar[str] = "sub"

    # API Gateway policy version
    POLICY_VERSION: ClassVar[str] = "2012-10-17"

    # WebSocket auth query parameter
    WEBSOCKET_TOKEN_PARAM: ClassVar[str] = "token"
    
    # ═══════════════════════════════════════════════════════════════════════════
    # Ring Protection: OS-level Privilege Isolation for AI Agents
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Ring level definitions (modeled after CPU Ring Model)
    RING_0_KERNEL: ClassVar[int] = 0      # Kernel: immutable system purpose, security policies
    RING_1_DRIVER: ClassVar[int] = 1      # Driver: internal system tools (future expansion)
    RING_2_SERVICE: ClassVar[int] = 2     # Service: restricted external APIs (future expansion)
    RING_3_USER: ClassVar[int] = 3        # User: untrusted external input

    # Ring 0 (Kernel) protected prompt prefix -- must never be overridden
    RING_0_PREFIX: ClassVar[str] = "[RING-0:IMMUTABLE]"
    RING_3_PREFIX: ClassVar[str] = "[RING-3:UNTRUSTED]"
    
    # Dangerous tools (not directly accessible from Ring 3)
    DANGEROUS_TOOLS: ClassVar[FrozenSet[str]] = frozenset({
        's3_delete', 's3_write', 's3_put_object',
        'db_delete', 'db_write', 'db_update', 'dynamodb_delete',
        'execute_shell', 'run_command', 'exec',
        'send_email', 'send_sms', 'send_notification',
        'payment_process', 'transfer_funds',
        'delete_user', 'admin_action',
    })
    
    # Safe tools (directly accessible from Ring 3)
    SAFE_TOOLS: ClassVar[FrozenSet[str]] = frozenset({
        's3_read', 's3_get_object', 's3_list',
        'db_read', 'db_query', 'db_scan',
        'api_get', 'http_get',
        'llm_chat', 'llm_complete',
        'log', 'print', 'format',
    })
    
    # Prompt injection patterns (detect Ring 3 -> Ring 0 escape attempts)
    INJECTION_PATTERNS: ClassVar[List[str]] = [
        r'(?i)ignore\s+(all\s+)?previous\s+instructions?',
        r'(?i)disregard\s+(all\s+)?(above|previous)',
        r'(?i)forget\s+(all\s+)?(previous|above)',
        r'(?i)override\s+(system|all|security)',
        r'(?i)you\s+are\s+now\s+(a|an|the)',
        r'(?i)new\s+(role|instructions?|persona)\s*:',
        r'(?i)system\s*:\s*you\s+are',
        r'(?i)\[RING-0',  # Ring 0 tag forgery attempt
        r'(?i)</?(RING|KERNEL|SYSTEM)[-_]',
        r'(?i)jailbreak|bypass|escape\s+mode',
    ]
    
    # Security violation severity levels
    SEVERITY_CRITICAL: ClassVar[str] = "CRITICAL"    # Immediate SIGKILL
    SEVERITY_HIGH: ClassVar[str] = "HIGH"            # Warning + segment abort
    SEVERITY_MEDIUM: ClassVar[str] = "MEDIUM"        # Warning + filter then proceed
    SEVERITY_LOW: ClassVar[str] = "LOW"              # Logging only

    # Enable Ring Protection
    ENABLE_RING_PROTECTION: ClassVar[bool] = os.environ.get('ENABLE_RING_PROTECTION', 'true').lower() == 'true'

    # Enable auto SIGKILL on security violation
    ENABLE_AUTO_SIGKILL: ClassVar[bool] = os.environ.get('ENABLE_AUTO_SIGKILL', 'true').lower() == 'true'
    
    # ═══════════════════════════════════════════════════════════════════════════
    # Kernel Control Interface (v2.1 - Agent Governance)
    # ═══════════════════════════════════════════════════════════════════════════
    
    # Reserved _kernel commands (Ring 0/1 only)
    # Ring 3 agents attempting to output these keys will trigger SecurityViolation
    KERNEL_CONTROL_KEYS: ClassVar[FrozenSet[str]] = frozenset({
        "_kernel_skip_segments",
        "_kernel_skip_reason",
        "_kernel_inject_recovery",
        "_kernel_rollback_to_manifest",
        "_kernel_rollback_reason",
        "_kernel_rollback_type",
        "_kernel_modify_parallelism",
        "_kernel_request_human_approval"
    })
    
    # Governance Mode Selection (Ring-based)
    GOVERNANCE_MODE: ClassVar[Dict[int, str]] = {
        RING_0_KERNEL: "STRICT",      # Kernel: Always synchronous validation
        RING_1_DRIVER: "STRICT",      # Governor: Synchronous validation
        RING_2_SERVICE: "OPTIMISTIC",  # Trusted: Async validation + rollback
        RING_3_USER: "OPTIMISTIC"      # Agents: Async validation + rollback
    }

    # Optimistic Rollback Trigger Threshold
    # If violations exceed this score in OPTIMISTIC mode, trigger rollback
    OPTIMISTIC_ROLLBACK_THRESHOLD: ClassVar[float] = 0.5  # 0.0 (safe) ~ 1.0 (critical)


class EnvironmentVariables:
    """Environment variable key constants"""

    # Table names
    WORKFLOWS_TABLE: ClassVar[str] = "WORKFLOWS_TABLE"
    EXECUTIONS_TABLE: ClassVar[str] = "EXECUTIONS_TABLE"
    USERS_TABLE: ClassVar[str] = "USERS_TABLE"
    TASK_TOKENS_TABLE: ClassVar[str] = "TASK_TOKENS_TABLE_NAME"
    IDEMPOTENCY_TABLE: ClassVar[str] = "IDEMPOTENCY_TABLE"
    WEBSOCKET_CONNECTIONS_TABLE: ClassVar[str] = "WEBSOCKET_CONNECTIONS_TABLE"
    USER_USAGE_TABLE: ClassVar[str] = "USER_USAGE_TABLE"

    # S3 bucket
    SKELETON_S3_BUCKET: ClassVar[str] = "SKELETON_S3_BUCKET"

    # Step Functions
    WORKFLOW_ORCHESTRATOR_ARN: ClassVar[str] = "WORKFLOW_ORCHESTRATOR_ARN"

    # WebSocket
    WEBSOCKET_ENDPOINT_URL: ClassVar[str] = "WEBSOCKET_ENDPOINT_URL"

    # Configuration
    MOCK_MODE: ClassVar[str] = "MOCK_MODE"
    LOG_LEVEL: ClassVar[str] = "LOG_LEVEL"
    STAGE_NAME: ClassVar[str] = "STAGE_NAME"

    # API keys (Secrets Manager references)
    OPENAI_API_KEY: ClassVar[str] = "OPENAI_API_KEY"
    ANTHROPIC_API_KEY: ClassVar[str] = "ANTHROPIC_API_KEY"
    GOOGLE_API_KEY: ClassVar[str] = "GOOGLE_API_KEY"

    # Pricing configuration
    PRICING_CONFIG_PARAM: ClassVar[str] = "PRICING_CONFIG_PARAM"

    # TTL configuration
    TASK_TOKEN_TTL_SECONDS: ClassVar[str] = "TASK_TOKEN_TTL_SECONDS"
    RETENTION_DAYS: ClassVar[str] = "RETENTION_DAYS"


class DynamoDBConfig:
    """DynamoDB configuration constants.

    [Critical] All defaults must match actual resource names in template.yaml.
    - Table names: logical name of the TableName property (e.g., WorkflowsTableV3)
    - GSI names: must exactly match the IndexName property in template.yaml
    """
    
    # ═══════════════════════════════════════════════════════════════════════════
    # Table names (loaded from environment variables)
    # Defaults: same format as template.yaml !Ref logical resource names
    # ═══════════════════════════════════════════════════════════════════════════
    WORKFLOWS_TABLE: ClassVar[str] = os.environ.get('WORKFLOWS_TABLE', 'WorkflowsTableV3')  # env: WORKFLOWS_TABLE
    EXECUTIONS_TABLE: ClassVar[str] = os.environ.get('EXECUTIONS_TABLE', 'ExecutionsTableV3')  # env: EXECUTIONS_TABLE
    PENDING_NOTIFICATIONS_TABLE: ClassVar[str] = os.environ.get('PENDING_NOTIFICATIONS_TABLE', 'PendingNotificationsTableV3')  # env: PENDING_NOTIFICATIONS_TABLE
    # [Critical Fix] Unified env var: prefer TASK_TOKENS_TABLE_NAME (matches template.yaml)
    TASK_TOKENS_TABLE: ClassVar[str] = os.environ.get('TASK_TOKENS_TABLE_NAME', os.environ.get('TASK_TOKENS_TABLE', 'TaskTokensTableV3'))  # env: TASK_TOKENS_TABLE_NAME
    WEBSOCKET_CONNECTIONS_TABLE: ClassVar[str] = os.environ.get('WEBSOCKET_CONNECTIONS_TABLE', 'WebsocketConnectionsTableV3')  # env: WEBSOCKET_CONNECTIONS_TABLE
    USERS_TABLE: ClassVar[str] = os.environ.get('USERS_TABLE', 'UsersTableV3')  # env: USERS_TABLE
    IDEMPOTENCY_TABLE: ClassVar[str] = os.environ.get('IDEMPOTENCY_TABLE', 'IdempotencyTableV3')  # env: IDEMPOTENCY_TABLE
    USER_USAGE_TABLE: ClassVar[str] = os.environ.get('USER_USAGE_TABLE', 'UserUsageTableV3')  # env: USER_USAGE_TABLE
    BEDROCK_JOB_TABLE: ClassVar[str] = os.environ.get('BEDROCK_JOB_TABLE', 'BedrockJobTableV3')  # env: BEDROCK_JOB_TABLE
    CHECKPOINTS_TABLE: ClassVar[str] = os.environ.get('CHECKPOINTS_TABLE', 'CheckpointsTableV3')  # env: CHECKPOINTS_TABLE
    SKILLS_TABLE: ClassVar[str] = os.environ.get('SKILLS_TABLE', 'SkillsTableV3')  # env: SKILLS_TABLE
    CORRECTION_LOGS_TABLE: ClassVar[str] = os.environ.get('CORRECTION_LOGS_TABLE', 'CorrectionLogsTable')  # env: CORRECTION_LOGS_TABLE
    DISTILLED_INSTRUCTIONS_TABLE: ClassVar[str] = os.environ.get('DISTILLED_INSTRUCTIONS_TABLE', 'DistilledInstructionsTable')  # env: DISTILLED_INSTRUCTIONS_TABLE
    WORKFLOW_BRANCHES_TABLE: ClassVar[str] = os.environ.get('WORKFLOW_BRANCHES_TABLE', 'WorkflowBranchesTable')  # env: WORKFLOW_BRANCHES_TABLE
    CONFIRMATION_TOKENS_TABLE: ClassVar[str] = os.environ.get('CONFIRMATION_TOKENS_TABLE', 'ConfirmationTokensTable')  # env: CONFIRMATION_TOKENS_TABLE
    NODE_STATS_TABLE: ClassVar[str] = os.environ.get('NODE_STATS_TABLE', 'NodeStatsTable')  # env: NODE_STATS_TABLE
    TASK_EVENTS_TABLE: ClassVar[str] = os.environ.get('TASK_EVENTS_TABLE', 'TaskEventsTable')  # env: TASK_EVENTS_TABLE

    # ═══════════════════════════════════════════════════════════════════════════
    # GSI names (must exactly match template.yaml GlobalSecondaryIndexes.IndexName)
    # Note: V2 suffix removed -- template.yaml does not use V2
    # ═══════════════════════════════════════════════════════════════════════════
    # WorkflowsTableV3 GSI
    # Fixed: Use actual index name from DynamoDB table
    OWNER_ID_NAME_INDEX: ClassVar[str] = os.environ.get('OWNER_ID_NAME_INDEX', 'OwnerIdNameIndex')
    SCHEDULED_WORKFLOWS_INDEX: ClassVar[str] = os.environ.get('SCHEDULED_WORKFLOWS_INDEX', 'ScheduledWorkflowsIndex')

    # ExecutionsTableV3 GSI
    OWNER_ID_START_DATE_INDEX: ClassVar[str] = os.environ.get('OWNER_ID_START_DATE_INDEX', 'OwnerIdStartDateIndex')
    OWNER_ID_STATUS_INDEX: ClassVar[str] = os.environ.get('OWNER_ID_STATUS_INDEX', 'OwnerIdStatusIndex')
    NOTIFICATIONS_INDEX: ClassVar[str] = os.environ.get('NOTIFICATIONS_INDEX', 'NotificationsIndex')

    # WebsocketConnectionsTableV3 GSI
    WEBSOCKET_OWNER_ID_GSI: ClassVar[str] = os.environ.get('WEBSOCKET_OWNER_ID_GSI', 'OwnerIdConnectionIndex')

    # TaskTokensTableV3 / PendingNotificationsTableV3 GSI
    EXECUTION_ID_INDEX: ClassVar[str] = os.environ.get('EXECUTION_ID_INDEX', 'ExecutionIdIndex')

    # CheckpointsTableV3 GSI
    TIME_INDEX: ClassVar[str] = os.environ.get('TIME_INDEX', 'TimeIndex')

    # SkillsTableV3 GSI
    OWNER_ID_INDEX: ClassVar[str] = os.environ.get('OWNER_ID_INDEX', 'OwnerIdIndex')
    CATEGORY_INDEX: ClassVar[str] = os.environ.get('CATEGORY_INDEX', 'CategoryIndex')
    VISIBILITY_INDEX: ClassVar[str] = os.environ.get('VISIBILITY_INDEX', 'VisibilityIndex')

    # CorrectionLogsTable GSI
    TASK_CATEGORY_INDEX: ClassVar[str] = os.environ.get('TASK_CATEGORY_INDEX', 'task-category-index-v2')
    USER_RECENT_INDEX: ClassVar[str] = os.environ.get('USER_RECENT_INDEX', 'user-recent-index-v2')

    # WorkflowBranchesTable GSI
    ROOT_THREAD_INDEX: ClassVar[str] = os.environ.get('ROOT_THREAD_INDEX', 'root-thread-index')

    # ConfirmationTokensTable GSI
    USER_ID_INDEX: ClassVar[str] = os.environ.get('USER_ID_INDEX', 'UserIdIndex')

    # TaskEventsTable GSI
    OWNER_ID_TIMESTAMP_INDEX: ClassVar[str] = os.environ.get('OWNER_ID_TIMESTAMP_INDEX', 'OwnerIdTimestampIndex')

    # Batch size
    BATCH_WRITE_SIZE: ClassVar[int] = 25

    # Query limits
    DEFAULT_QUERY_LIMIT: ClassVar[int] = 100
    MAX_QUERY_LIMIT: ClassVar[int] = 100


def get_env_var(key: str, default: Any = None, required: bool = False) -> Any:
    """
    Safely retrieve an environment variable.

    Args:
        key: Environment variable key
        default: Default value
        required: Whether the variable is required

    Returns:
        Environment variable value or default

    Raises:
        ValueError: If required=True and the variable is not set
    """
    value = os.environ.get(key, default)
    
    if required and value is None:
        raise ValueError(f"Required environment variable '{key}' is not set")
    
    return value


def get_table_name(table_key: str) -> str:
    """
    Retrieve a table name from environment variables.

    Args:
        table_key: Environment variable key (e.g., "WORKFLOWS_TABLE")

    Returns:
        Table name

    Raises:
        ValueError: If the table name is not set
    """
    return get_env_var(table_key, required=True)


def is_mock_mode() -> bool:
    """Check MOCK_MODE environment variable."""
    return get_env_var(EnvironmentVariables.MOCK_MODE, "false").lower() in {"true", "1", "yes", "on"}


def get_stage_name() -> str:
    """Return the stage name."""
    return get_env_var(EnvironmentVariables.STAGE_NAME, "dev")


def get_log_level() -> str:
    """Return the log level."""
    return get_env_var(EnvironmentVariables.LOG_LEVEL, LoggingConfig.DEFAULT_LOG_LEVEL)


def get_inline_threshold() -> int:
    """Return the S3 offload threshold."""
    try:
        return int(get_env_var("STREAM_INLINE_THRESHOLD_BYTES", WorkflowConfig.DEFAULT_INLINE_THRESHOLD))
    except (ValueError, TypeError):
        return WorkflowConfig.DEFAULT_INLINE_THRESHOLD


def get_messages_window() -> int:
    """Return the message window size."""
    try:
        return int(get_env_var("MESSAGES_WINDOW", WorkflowConfig.DEFAULT_MESSAGES_WINDOW))
    except (ValueError, TypeError):
        return WorkflowConfig.DEFAULT_MESSAGES_WINDOW