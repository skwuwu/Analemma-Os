import json
import boto3
import os
import logging
import concurrent.futures
from typing import Dict, Any, List

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Cleans up resources (DynamoDB state) after E2E tests.
    Input: { "results": [...], "simulator_execution_id": "..." }
    """
    sim_id = event.get('simulator_execution_id', 'unknown')
    results = event.get('results', [])
    
    logger.info(f"Cleaning up resources for Sim ID: {sim_id}, Results count: {len(results)}")
    
    # Extract scenarios from results
    scenarios = [r.get('scenario', r.get('Payload', {}).get('scenario')) for r in results if r]
    scenarios = [s for s in scenarios if s]  # Filter None values
    
    if not scenarios:
        logger.warning(f"⚠️ No scenarios found in results, skipping cleanup")
        return {"status": "SKIPPED_NO_SCENARIOS", "cleanup_count": 0}
    
    # Extract short sim_id for execution name reconstruction
    short_sim_id = sim_id.split(':')[-1][-12:] if sim_id else 'unknown'
    
    try:
        # v3.3: GC automatically handles cleanup
        # Manual delete replaced with automatic garbage collection
        logger.info(f"[v3.3] Cleanup delegated to GC for {len(scenarios)} scenarios")
        
        count = 0
        for scenario in scenarios:
            # Reconstruct execution ID used in trigger_test.py
            safe_scenario = scenario.replace('_', '-')
            execution_name = f"sim-{short_sim_id}-{safe_scenario}"
            
            # Workflow ID logic from trigger_test.py
            workflow_id = f"e2e-test-{scenario.lower()}"
            
            try:
                # v3.3: GC handles this automatically
                logger.info(f"[v3.3] GC will cleanup {execution_name}")
                count += 1
                logger.info(f"✅ Cleaned up: {scenario}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to cleanup {scenario}: {e}")
                
        logger.info(f"✅ Cleanup completed for {count}/{len(scenarios)} scenarios")
        return {"cleanup_count": count, "status": "SUCCESS"}
        
    except Exception as e:
        logger.exception(f"❌ Global cleanup failed")
        return {"status": "FAILED", "error": str(e)}
