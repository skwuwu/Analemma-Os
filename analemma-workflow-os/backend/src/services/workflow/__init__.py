# -*- coding: utf-8 -*-
"""
Workflow Services Package

This package contains all workflow-related services:
- builder: Dynamic workflow construction from JSON definitions
- repository: Workflow CRUD operations with DynamoDB
- cache_manager: Workflow configuration caching
- orchestrator_service: Workflow execution orchestration
- orchestrator_selector: Smart orchestrator selection (Standard vs Distributed Map)
- partition_service: Workflow partitioning into executable segments

NOTE: Heavy imports (builder, orchestrator_service) are lazy-loaded to avoid
import failures in Lambda functions that don't need langgraph.
"""

# Light imports - no heavy dependencies
from src.services.workflow.repository import WorkflowRepository
from src.services.workflow.cache_manager import cached_get_workflow_config
from src.services.workflow.orchestrator_selector import (
    select_orchestrator,
    get_orchestrator_selection_summary,
)
from src.services.workflow.partition_service import partition_workflow_advanced

# Lazy imports for heavy dependencies (langgraph required)
def __getattr__(name):
    """Lazy load heavy modules only when accessed."""
    if name == "DynamicWorkflowBuilder":
        from src.services.workflow.builder import DynamicWorkflowBuilder
        return DynamicWorkflowBuilder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "DynamicWorkflowBuilder",
    "WorkflowRepository",
    "cached_get_workflow_config",
    "select_orchestrator",
    "get_orchestrator_selection_summary",
    "partition_workflow_advanced",
]
