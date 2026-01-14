
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add backend root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../backend')))

from src.services.execution.segment_runner_service import SegmentRunnerService
from src.handlers.core.aggregate_distributed_results import lambda_handler as aggregate_handler

class TestAsyncChildTrigger(unittest.TestCase):
    def setUp(self):
        self.service = SegmentRunnerService()
        os.environ['WORKFLOW_ORCHESTRATOR_ARN'] = 'arn:aws:states:us-east-1:123456789012:stateMachine:StandardOrchestrator'
        os.environ['FORCE_CHILD_WORKFLOW'] = 'true'

    def test_trigger_child_workflow(self):
        # Create a mock for boto3 that will be injected into sys.modules
        mock_boto3 = MagicMock()
        mock_sfn = MagicMock()
        mock_boto3.client.return_value = mock_sfn
        
        mock_sfn.start_execution.return_value = {
            'executionArn': 'arn:aws:states:exec:123',
            'startDate': MagicMock(isoformat=lambda: '2026-01-10T12:00:00Z')
        }

        # Patch boto3 in sys.modules so the local import gets our mock
        with patch.dict(sys.modules, {'boto3': mock_boto3}):
            with patch('src.services.execution.segment_runner_service.normalize_inplace'):
                # Sub-test 1: Execute Segment with Branch Config
                event = {
                    'workflowId': 'wf-123',
                    'idempotency_key': 'parent-exec-1',
                    'ownerId': 'user-1',
                    'workflow_config': {'id': 'parent'},
                    'branch_config': {
                        'id': 'branch-A',
                        'nodes': [{'id': 'n1'}] # Small branch but FORCE_CHILD_WORKFLOW=true
                    },
                    'quota_reservation_id': 'quota-1',
                    'state_data': {'some': 'state'} # Top level in Distributed Map payload
                }

                result = self.service.execute_segment(event)

                # Verify Result
                self.assertEqual(result['status'], 'ASYNC_CHILD_WORKFLOW_STARTED')
                self.assertEqual(result['executionArn'], 'arn:aws:states:exec:123')
                self.assertEqual(result['executionName'], 'parent-exec-1_branch-A')

                # Verify SFN Call
                mock_sfn.start_execution.assert_called_once()
                call_args = mock_sfn.start_execution.call_args[1]
                self.assertEqual(call_args['stateMachineArn'], 'arn:aws:states:us-east-1:123456789012:stateMachine:StandardOrchestrator')
                self.assertEqual(call_args['name'], 'parent-exec-1_branch-A')
                
                import json
                payload = json.loads(call_args['input'])
                self.assertEqual(payload['idempotency_key'], 'parent-exec-1_branch-A')
                self.assertEqual(payload['parent_workflow_id'], 'wf-123')
                # Check payload structure (full input injection)
                self.assertEqual(payload['quota_reservation_id'], 'quota-1')
                self.assertEqual(payload['workflow_config']['id'], 'branch-A')

    def test_aggregate_async_results(self):
        # Sub-test 2: Aggregate Distributed Results
        event = {
            'distributed_results': [
                {
                    'status': 'ASYNC_CHILD_WORKFLOW_STARTED',
                    'executionArn': 'arn:1',
                    'executionName': 'exec-1'
                },
                {
                    'status': 'COMPLETED',
                    'final_state': {}
                }
            ],
            'state_data': {'workflowId': 'wf-main'},
            'use_s3_results': False
        }

        # Mock S3/Dynamo loading inside aggregator (if needed)
        with patch('src.handlers.core.aggregate_distributed_results._load_latest_state') as mock_load:
            mock_load.return_value = {}
            with patch('src.handlers.core.aggregate_distributed_results._save_final_state'):
                 with patch('src.handlers.core.aggregate_distributed_results._cleanup_intermediate_states'):
                    result = aggregate_handler(event)

        # Verify Aggregation
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(result['successful_chunks'], 2) # 1 async + 1 completed
        self.assertEqual(result['failed_chunks'], 0)
        
        # Verify execution summary contains trace of async
        # We need to inspect logs or summary in result
        # The aggregation handler returns 'all_results' which are logs.
        # It doesn't explicitly return 'execution_summary' in the top level dict, but inside result.
    def test_child_failure_simulation(self):
        # Sub-test 3: Simulate "Parent Completed, Child Failed"
        # This confirms that in "Fire and Forget" mode, the parent status is NOT affected by the child failure
        # unless we explicitly implement a callback mechanism (which we haven't yet).
        
        # 1. Parent Aggregation (Success)
        event = {
            'distributed_results': [
                {'status': 'ASYNC_CHILD_WORKFLOW_STARTED', 'executionName': 'exec-1'}
            ],
            'state_data': {'workflowId': 'wf-parent'},
        }
        
        with patch('src.handlers.core.aggregate_distributed_results._load_latest_state', return_value={}):
            with patch('src.handlers.core.aggregate_distributed_results._save_final_state') as mock_save:
                 with patch('src.handlers.core.aggregate_distributed_results._cleanup_intermediate_states'):
                    result = aggregate_handler(event)
                    
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertEqual(result['successful_chunks'], 1)
        
        # 2. Simulate Child Failure (Conceptually)
        # If the child 'exec-1' fails later, the parent 'wf-parent' DB record remains 'COMPLETED'
        # This test documents that gap. To fix this, we would need EventBridge rules on 'Step Functions Execution Status Change'.
        print("\n[Simulation] Parent marked as COMPLETED. If child 'exec-1' fails later, parent status in DB remains COMPLETED (As-Is Behavior).")

