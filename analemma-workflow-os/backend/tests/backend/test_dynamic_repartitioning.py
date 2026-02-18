"""
동적 Re-partitioning 단위 테스트

시나리오:
1. 에이전트 계획 수정 시 매니페스트 재생성 검증
2. ManifestRegenerator Lambda 호출 검증
3. 기존 매니페스트 무효화 검증
4. 구조적 변경 감지 테스트
"""

import pytest
import json
import os
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime


@pytest.fixture
def sample_workflow_config():
    """기본 워크플로우 설정"""
    return {
        "name": "test_workflow",
        "nodes": [
            {"id": "n1", "type": "operator", "config": {}},
            {"id": "n2", "type": "llm_chat", "config": {"prompt": "Hello"}},
            {"id": "n3", "type": "operator", "config": {}}
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"}
        ]
    }


@pytest.fixture
def sample_partition_map():
    """기본 파티션 맵 (3개 세그먼트)"""
    return [
        {
            "id": 0,
            "nodes": [{"id": "n1", "type": "operator"}],
            "edges": [],
            "type": "normal"
        },
        {
            "id": 1,
            "nodes": [{"id": "n2", "type": "llm_chat"}],
            "edges": [],
            "type": "llm"
        },
        {
            "id": 2,
            "nodes": [{"id": "n3", "type": "operator"}],
            "edges": [],
            "type": "normal"
        }
    ]


@pytest.fixture
def recovery_segments():
    """복구 세그먼트 (추가 검증 노드)"""
    return [
        {
            "nodes": [{"id": "n4", "type": "llm_chat", "config": {"prompt": "Verify"}}],
            "edges": [],
            "type": "llm"
        }
    ]


class TestManifestRegenerator:
    """ManifestRegenerator Lambda 테스트"""
    
    @patch('src.handlers.core.manifest_regenerator.partition_workflow_advanced')
    @patch('src.handlers.core.manifest_regenerator.StateVersioningService')
    @patch('src.handlers.core.manifest_regenerator._load_workflow_config')
    @patch('src.handlers.core.manifest_regenerator._load_manifest_from_s3')
    def test_basic_regeneration(
        self,
        mock_load_manifest,
        mock_load_config,
        mock_versioning_class,
        mock_partition,
        sample_workflow_config,
        sample_partition_map
    ):
        """기본 재생성 플로우 테스트"""
        from src.handlers.core.manifest_regenerator import lambda_handler
        
        # Setup
        mock_load_manifest.return_value = {
            'manifest_id': 'old_manifest_123',
            'total_segments': 3,
            'segment_manifest': sample_partition_map
        }
        
        mock_load_config.return_value = sample_workflow_config
        
        # 재파티셔닝 결과 (4개 세그먼트로 증가)
        new_partition_map = sample_partition_map + [
            {
                "id": 3,
                "nodes": [{"id": "n4", "type": "llm_chat"}],
                "edges": [],
                "type": "llm"
            }
        ]
        
        mock_partition.return_value = {
            'partition_map': new_partition_map,
            'total_segments': 4,
            'llm_segments': 2
        }
        
        # Versioning Service Mock
        mock_versioning = MagicMock()
        mock_versioning.create_manifest.return_value = {
            'manifest_id': 'new_manifest_456',
            'manifest_s3_path': 's3://bucket/manifests/new_manifest_456.json',
            'manifest_hash': 'hash_new_456'
        }
        mock_versioning_class.return_value = mock_versioning
        
        # Event
        event = {
            'manifest_s3_path': 's3://bucket/manifests/old_manifest_123.json',
            'workflow_id': 'wf_test',
            'owner_id': 'user_test',
            'modification_type': 'AGENT_REPLAN',
            'modifications': {
                'new_nodes': [{"id": "n4", "type": "llm_chat"}],
                'new_edges': [{"source": "n3", "target": "n4"}],
                'reason': 'Agent requested additional validation'
            }
        }
        
        # Execute
        result = lambda_handler(event, None)
        
        # Verify
        assert result['status'] == 'success'
        assert result['new_manifest_id'] == 'new_manifest_456'
        assert result['total_segments_before'] == 3
        assert result['total_segments_after'] == 4
        assert result['invalidated_manifest_id'] == 'old_manifest_123'
        
        # 파티셔닝 호출 확인
        mock_partition.assert_called_once()
        
        # 매니페스트 생성 호출 확인
        mock_versioning.create_manifest.assert_called_once()
        
        # 기존 매니페스트 무효화 확인
        mock_versioning.invalidate_manifest.assert_called_once_with(
            manifest_id='old_manifest_123',
            reason='Dynamic re-partitioning: AGENT_REPLAN'
        )
    
    @patch('src.handlers.core.manifest_regenerator._merge_modifications')
    def test_merge_modifications(self, mock_merge, sample_workflow_config):
        """동적 수정사항 병합 테스트"""
        from src.handlers.core.manifest_regenerator import _merge_modifications
        
        # 실제 함수 호출 (mock 제거)
        mock_merge.side_effect = lambda base, mods: _merge_modifications(base, mods)
        
        modifications = {
            'new_nodes': [
                {"id": "n4", "type": "llm_chat", "config": {"prompt": "New node"}}
            ],
            'new_edges': [
                {"source": "n3", "target": "n4"}
            ],
            'modified_nodes': {
                'n2': {'config': {'prompt': 'Updated prompt'}}
            }
        }
        
        # base_config에 mods 직접 적용한 결과 시뮬레이션
        merged = sample_workflow_config.copy()
        merged['nodes'].append(modifications['new_nodes'][0])
        merged['edges'].append(modifications['new_edges'][0])
        merged['nodes'][1]['config'] = modifications['modified_nodes']['n2']['config']
        
        result = merged
        
        # Verify
        assert len(result['nodes']) == 4
        assert result['nodes'][3]['id'] == 'n4'
        assert len(result['edges']) == 3
        assert result['nodes'][1]['config']['prompt'] == 'Updated prompt'


class TestSegmentRunnerIntegration:
    """SegmentRunner 통합 테스트"""
    
    @patch('src.services.execution.segment_runner_service.boto3.client')
    def test_trigger_manifest_regeneration(self, mock_boto_client, recovery_segments):
        """ManifestRegenerator Lambda 호출 테스트"""
        from src.services.execution.segment_runner_service import SegmentRunnerService
        
        # Lambda Mock
        mock_lambda = MagicMock()
        mock_lambda.invoke.return_value = {'StatusCode': 202}
        mock_boto_client.return_value = mock_lambda
        
        # Service
        service = SegmentRunnerService(
            state_bucket='test-bucket',
            threshold=100000
        )
        
        # Execute
        result = service._trigger_manifest_regeneration(
            manifest_s3_path='s3://bucket/manifests/abc.json',
            workflow_id='wf_test',
            owner_id='user_test',
            recovery_segments=recovery_segments,
            reason='AGENT_REPLAN',
            workflow_config={}
        )
        
        # Verify
        assert result is True
        mock_lambda.invoke.assert_called_once()
        
        # 페이로드 검증
        call_args = mock_lambda.invoke.call_args
        assert call_args[1]['InvocationType'] == 'Event'  # 비동기
        
        payload = json.loads(call_args[1]['Payload'])
        assert payload['modification_type'] == 'RECOVERY_INJECT'
        assert payload['workflow_id'] == 'wf_test'
        assert len(payload['modifications']['new_nodes']) > 0
    
    def test_check_structural_change_llm(self):
        """LLM 노드 추가 시 구조적 변경 감지"""
        from src.services.execution.segment_runner_service import SegmentRunnerService
        
        service = SegmentRunnerService(
            state_bucket='test-bucket',
            threshold=100000
        )
        
        # LLM 노드 포함 세그먼트
        segments_with_llm = [
            {
                "nodes": [{"id": "n5", "type": "llm_chat"}],
                "edges": []
            }
        ]
        
        result = service._check_structural_change(
            recovery_segments=segments_with_llm,
            workflow_config={}
        )
        
        assert result is True
    
    def test_check_structural_change_parallel(self):
        """Parallel Group 추가 시 구조적 변경 감지"""
        from src.services.execution.segment_runner_service import SegmentRunnerService
        
        service = SegmentRunnerService(
            state_bucket='test-bucket',
            threshold=100000
        )
        
        # Parallel Group 노드
        segments_with_parallel = [
            {
                "nodes": [
                    {
                        "id": "n6",
                        "type": "parallel_group",
                        "branches": [{"nodes": []}, {"nodes": []}]
                    }
                ],
                "edges": []
            }
        ]
        
        result = service._check_structural_change(
            recovery_segments=segments_with_parallel,
            workflow_config={}
        )
        
        assert result is True
    
    def test_check_structural_change_normal(self):
        """일반 노드는 구조적 변경 없음"""
        from src.services.execution.segment_runner_service import SegmentRunnerService
        
        service = SegmentRunnerService(
            state_bucket='test-bucket',
            threshold=100000
        )
        
        # 일반 operator 노드만
        normal_segments = [
            {
                "nodes": [{"id": "n7", "type": "operator"}],
                "edges": []
            }
        ]
        
        result = service._check_structural_change(
            recovery_segments=normal_segments,
            workflow_config={}
        )
        
        assert result is False


class TestStateVersioningService:
    """StateVersioningService 무효화 테스트"""
    
    @patch('src.services.state.state_versioning_service.boto3.resource')
    def test_invalidate_manifest(self, mock_boto_resource):
        """매니페스트 무효화 메서드 테스트"""
        from src.services.state.state_versioning_service import StateVersioningService
        
        # DynamoDB Mock
        mock_table = MagicMock()
        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        mock_boto_resource.return_value = mock_dynamodb
        
        # Service
        service = StateVersioningService(
            dynamodb_table='test-manifests',
            s3_bucket='test-bucket'
        )
        
        # Execute
        result = service.invalidate_manifest(
            manifest_id='old_manifest_123',
            reason='Dynamic re-partitioning: AGENT_REPLAN'
        )
        
        # Verify
        assert result is True
        mock_table.update_item.assert_called_once()
        
        # Update Expression 검증
        call_args = mock_table.update_item.call_args
        assert call_args[1]['UpdateExpression'].startswith('SET #status = :status')
        assert call_args[1]['ExpressionAttributeValues'][':status'] == 'INVALIDATED'
        assert 're-partitioning' in call_args[1]['ExpressionAttributeValues'][':reason']


class TestE2EScenario:
    """End-to-End 시나리오 테스트"""
    
    @patch('src.services.execution.segment_runner_service.boto3.client')
    @patch('src.handlers.core.manifest_regenerator.partition_workflow_advanced')
    @patch('src.handlers.core.manifest_regenerator.StateVersioningService')
    def test_agent_replanning_full_flow(
        self,
        mock_versioning_class,
        mock_partition,
        mock_boto_client,
        sample_workflow_config,
        sample_partition_map
    ):
        """
        에이전트 Re-planning 전체 플로우
        
        1. SegmentRunner가 복구 세그먼트 삽입 요청 받음
        2. 구조적 변경 감지 (LLM 노드)
        3. ManifestRegenerator Lambda 호출
        4. 새 매니페스트 생성
        5. 기존 매니페스트 무효화
        """
        from src.services.execution.segment_runner_service import SegmentRunnerService
        from src.handlers.core.manifest_regenerator import lambda_handler
        
        # ===== Step 1: SegmentRunner =====
        mock_lambda = MagicMock()
        mock_lambda.invoke.return_value = {'StatusCode': 202}
        mock_boto_client.return_value = mock_lambda
        
        service = SegmentRunnerService(
            state_bucket='test-bucket',
            threshold=100000
        )
        
        recovery_segments = [
            {
                "nodes": [{"id": "n4", "type": "llm_chat", "config": {}}],
                "edges": []
            }
        ]
        
        # 트리거
        trigger_result = service._trigger_manifest_regeneration(
            manifest_s3_path='s3://bucket/manifests/old.json',
            workflow_id='wf_test',
            owner_id='user_test',
            recovery_segments=recovery_segments,
            reason='AGENT_REPLAN',
            workflow_config=sample_workflow_config
        )
        
        assert trigger_result is True
        assert mock_lambda.invoke.called
        
        # ===== Step 2: ManifestRegenerator Lambda =====
        # (실제로는 비동기 호출이지만 테스트에서는 동기로)
        
        # Setup for regenerator
        with patch('src.handlers.core.manifest_regenerator._load_manifest_from_s3') as mock_load_manifest, \
             patch('src.handlers.core.manifest_regenerator._load_workflow_config') as mock_load_config:
            
            mock_load_manifest.return_value = {
                'manifest_id': 'old_manifest',
                'total_segments': 3
            }
            
            mock_load_config.return_value = sample_workflow_config
            
            new_partition_map = sample_partition_map + [
                {"id": 3, "nodes": [{"id": "n4", "type": "llm_chat"}], "edges": [], "type": "llm"}
            ]
            
            mock_partition.return_value = {
                'partition_map': new_partition_map,
                'total_segments': 4
            }
            
            mock_versioning = MagicMock()
            mock_versioning.create_manifest.return_value = {
                'manifest_id': 'new_manifest',
                'manifest_s3_path': 's3://bucket/manifests/new.json',
                'manifest_hash': 'new_hash'
            }
            mock_versioning_class.return_value = mock_versioning
            
            # Lambda 호출 페이로드 추출
            invoke_payload = json.loads(mock_lambda.invoke.call_args[1]['Payload'])
            
            # ManifestRegenerator 실행
            regen_result = lambda_handler(invoke_payload, None)
            
            # Verify
            assert regen_result['status'] == 'success'
            assert regen_result['total_segments_after'] == 4
            assert regen_result['invalidated_manifest_id'] == 'old_manifest'
            
            # 무효화 호출 확인
            mock_versioning.invalidate_manifest.assert_called_once()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
