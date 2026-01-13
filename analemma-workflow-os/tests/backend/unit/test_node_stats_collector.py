import pytest
import sys
import os
from unittest.mock import patch, MagicMock
from decimal import Decimal
from datetime import datetime, timezone

# backend/src 경로 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../backend/src'))

import src.handlers.utils.node_stats_collector as nsc



class TestNodeStatsCollector:
    """Node Stats Collector Lambda 테스트"""

    def test_infer_node_type_classification(self):
        """분류 노드 타입 추론 테스트"""
        detail = {}
        assert nsc._infer_node_type("ClassifyIntent", detail) == "classification"
        assert nsc._infer_node_type("classify_document", detail) == "classification"

    def test_infer_node_type_generation(self):
        """생성 노드 타입 추론 테스트"""
        detail = {}
        assert nsc._infer_node_type("GenerateResponse", detail) == "generation"
        assert nsc._infer_node_type("write_summary", detail) == "generation"

    def test_infer_node_type_reasoning(self):
        """추론 노드 타입 추론 테스트"""
        detail = {}
        assert nsc._infer_node_type("AnalyzeData", detail) == "reasoning"
        assert nsc._infer_node_type("reason_about_input", detail) == "reasoning"

    def test_infer_node_type_api_call(self):
        """API 호출 노드 타입 추론 테스트"""
        detail = {}
        assert nsc._infer_node_type("CallExternalAPI", detail) == "api_call"
        assert nsc._infer_node_type("http_request", detail) == "api_call"

    def test_infer_node_type_database(self):
        """데이터베이스 노드 타입 추론 테스트"""
        detail = {}
        assert nsc._infer_node_type("QueryDatabase", detail) == "database"
        assert nsc._infer_node_type("dynamo_lookup", detail) == "database"

    def test_infer_node_type_default(self):
        """기본 노드 타입 테스트"""
        detail = {}
        assert nsc._infer_node_type("UnknownState", detail) == "default"
        assert nsc._infer_node_type("SomeTask", detail) == "default"

    @patch('src.handlers.utils.node_stats_collector.table')
    def test_update_node_stats_outlier_filtered(self, mock_table):
        """아웃라이어 필터링 테스트"""
        # 기존 데이터 설정 (평균 10초)
        mock_table.get_item.return_value = {
            'Item': {
                'node_type': 'test_node',
                'avg_duration_seconds': Decimal('10.0'),
                'sample_count': 10,
                'success_count': 8
            }
        }

        # 아웃라이어 (31초 > 10 * 3 = 30초) - 필터링되어야 함
        nsc._update_node_stats('test_node', 31.0, True)

        # update_item이 호출되지 않아야 함
        mock_table.update_item.assert_not_called()

    @patch('src.handlers.utils.node_stats_collector.table')
    def test_update_node_stats_normal_update(self, mock_table):
        """정상 통계 업데이트 테스트"""
        # 기존 데이터 설정
        mock_table.get_item.return_value = {
            'Item': {
                'node_type': 'test_node',
                'avg_duration_seconds': Decimal('10.0'),
                'sample_count': 10,
                'success_count': 8
            }
        }

        # 정상 데이터 (15초 < 10 * 3 = 30초)
        nsc._update_node_stats('test_node', 15.0, True)

        # update_item이 호출되어야 함
        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args

        # 새로운 평균 계산 검증: 10.0 * 0.9 + 15.0 * 0.1 = 10.5
        expected_new_avg = Decimal('10.0') * nsc.DECAY_FACTOR + Decimal('15.0') * nsc.CURRENT_FACTOR
        assert call_args[1]['ExpressionAttributeValues'][':new_avg'] == expected_new_avg

        # 샘플 수 증가 검증
        assert call_args[1]['ExpressionAttributeValues'][':new_sample_count'] == 11

        # 성공 수 증가 검증
        assert call_args[1]['ExpressionAttributeValues'][':new_success_count'] == 9

    @patch('src.handlers.utils.node_stats_collector.table')
    def test_update_node_stats_initial_creation(self, mock_table):
        """초기 데이터 생성 테스트"""
        # 기존 데이터 없음
        mock_table.get_item.return_value = {}

        nsc._update_node_stats('new_node', 5.0, True)

        # update_item이 호출되어야 함
        mock_table.update_item.assert_called_once()
        call_args = mock_table.update_item.call_args

        # 초기 값 검증
        assert call_args[1]['ExpressionAttributeValues'][':new_avg'] == Decimal('5.0')
        assert call_args[1]['ExpressionAttributeValues'][':new_sample_count'] == 1
        assert call_args[1]['ExpressionAttributeValues'][':new_success_count'] == 1
        assert call_args[1]['ExpressionAttributeValues'][':new_success_rate'] == Decimal('1.0')