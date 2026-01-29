"""
LLM Simulator Trigger - Orchestrates Test Suite via Step Functions
===================================================================
Triggers the LLM Simulator Step Functions State Machine to execute
multiple test scenarios in parallel.

Role:
- Client / Triggerer Only
- Does NOT run verification logic (Delegated to VerifyLLMTestFunction)
- Does NOT poll individual LLM calls

Flow:
1. User invokes this Lambda (via API or CLI).
2. This Lambda starts 'LLMSimulatorStateMachine'.
3. State Machine executes 'Prepare', 'Run (SFN)', 'Verify' for each scenario.
4. Returns Execution ARN immediately (Async).
"""

import json
import os
import time
import uuid
import boto3
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

# Logger setup
try:
    from aws_lambda_powertools import Logger
    import os
    log_level = os.getenv("LOG_LEVEL", "INFO")
    logger = Logger(
        service=os.getenv("AWS_LAMBDA_FUNCTION_NAME", "analemma-backend"),
        level=log_level,
        child=True
    )
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

# Configuration
LLM_SIMULATOR_STATE_MACHINE_ARN = os.environ.get('LLM_SIMULATOR_STATE_MACHINE_ARN')
DISTRIBUTED_STATE_MACHINE_ARN = os.environ.get('WORKFLOW_DISTRIBUTED_ORCHESTRATOR_ARN')
METRIC_NAMESPACE = os.environ.get('METRIC_NAMESPACE', 'Analemma/LLMSimulator')

_stepfunctions_client = None

def get_stepfunctions_client():
    global _stepfunctions_client
    if _stepfunctions_client is None:
        _stepfunctions_client = boto3.client('stepfunctions')
    return _stepfunctions_client


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Triggers the LLM Simulator Suite.
    
    Event:
    {
        "scenarios": ["STAGE1_BASIC", "STAGE5_HYPER_STRESS"],
        "orchestrator_type": "DISTRIBUTED" | "STANDARD",
        "dry_run": false
    }
    """
    logger.info("=" * 60)
    logger.info("🚀 LLM Simulator Suite Triggered")
    logger.info("=" * 60)
    
    scenarios = event.get('scenarios')
    orchestrator_type = event.get('orchestrator_type', 'DISTRIBUTED')
    dry_run = event.get('dry_run', False)
    
    if not LLM_SIMULATOR_STATE_MACHINE_ARN:
        msg = "CRITICAL: LLM_SIMULATOR_STATE_MACHINE_ARN not configured."
        logger.error(msg)
        return {"status": "ERROR", "error": msg}

    # SFN Input Payload
    execution_id = f"llm-suite-{uuid.uuid4().hex[:8]}"
    sfn_input = {
        "simulator_execution_id": execution_id,
        "scenarios": scenarios, # If None, SFN defaults to ALL
        "orchestrator_type": orchestrator_type,
        "dry_run": dry_run,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    if dry_run:
        logger.info("DRY RUN: Would have started execution with payload:")
        logger.info(json.dumps(sfn_input, indent=2))
        return {
            "status": "DRY_RUN", 
            "message": "Dry run complete", 
            "payload": sfn_input
        }

    # Start Execution
    sfn = get_stepfunctions_client()
    try:
        response = sfn.start_execution(
            stateMachineArn=LLM_SIMULATOR_STATE_MACHINE_ARN,
            name=execution_id,
            input=json.dumps(sfn_input)
        )
        execution_arn = response['executionArn']
        
        logger.info(f"✅ Started LLM Simulator SFN: {execution_arn}")
        return {
            "status": "STARTED",
            "execution_arn": execution_arn,
            "simulator_ui_link": f"https://console.aws.amazon.com/states/home?region={os.environ.get('AWS_REGION')}#/executions/details/{execution_arn}"
        }
        
    except Exception as e:
        logger.exception("Failed to start LLM Simulator SFN")
        return {
            "status": "FAILED",
            "error": str(e)
        }

if __name__ == "__main__":
    # Local Test
    print("Running local test...")
    result = lambda_handler({
        "scenarios": ["STAGE1_BASIC"], 
        "dry_run": True
    }, None)
    print(result)
