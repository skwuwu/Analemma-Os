"""
지능형 지침 증류기 - 수정 로그 수집 서비스
"""

import boto3
import json
import uuid
import time
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from botocore.exceptions import ClientError
from ..models.correction_log import CorrectionLog, TaskCategory, CorrectionType
from .metadata_extractor import MetadataExtractor
from src.common.constants import DynamoDBConfig

logger = logging.getLogger(__name__)

class CorrectionService:
    """사용자 수정 로그 수집 및 관리 서비스"""
    
    def __init__(self):
        self.ddb = boto3.resource('dynamodb')
        self.correction_table = self.ddb.Table(
            os.environ.get('CORRECTION_LOGS_TABLE', 'correction-logs')
        )
        self.metadata_extractor = MetadataExtractor()
    
    async def log_correction(
        self,
        user_id: str,
        workflow_id: str,
        node_id: str,
        original_input: str,
        agent_output: str,
        user_correction: str,
        task_category: str,
        node_type: str = "llm_operator",
        workflow_domain: str = "general",
        correction_time_seconds: int = 0,
        user_confirmed_valuable: Optional[bool] = None,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """수정 로그 저장 및 메타데이터 추출"""
        
        try:
            # 편집 거리 계산
            edit_distance = self.metadata_extractor.calculate_edit_distance(
                agent_output, user_correction
            )
            
            # 메타데이터 추출
            extracted_metadata = await self.metadata_extractor.extract_metadata(
                agent_output, user_correction
            )
            
            # 수정 타입 분류
            correction_type = await self.metadata_extractor.classify_correction_type(
                original_input, user_correction, extracted_metadata
            )
            
            # 컨텍스트 스코프 결정
            context_scope = self._determine_context_scope(
                task_category, node_type, workflow_domain
            )
            
            # CorrectionLog 객체 생성 (개선된 멱등성 SK 생성 적용)
            correction_log = CorrectionLog(
                pk=f"user#{user_id}",
                user_id=user_id,
                workflow_id=workflow_id,
                node_id=node_id,
                task_category=TaskCategory(task_category),
                original_input=original_input,
                agent_output=agent_output,
                user_correction=user_correction,
                edit_distance=edit_distance,
                correction_time_seconds=correction_time_seconds,
                user_confirmed_valuable=user_confirmed_valuable,
                correction_type=CorrectionType(correction_type) if correction_type in [e.value for e in CorrectionType] else None,
                extracted_metadata=extracted_metadata,
                node_type=node_type,
                workflow_domain=workflow_domain,
                context_scope=context_scope,
                applicable_contexts=self._get_applicable_contexts(
                    context_scope, task_category, node_type, workflow_domain
                )
            )
            
            # 중복 확인 없이 바로 원자적 저장 (멱등성 보장)
            # ConditionExpression을 사용하여 동일 SK가 없을 때만 저장
            item = self._serialize_correction_log(correction_log)
            
            try:
                self.correction_table.put_item(
                    Item=item,
                    ConditionExpression='attribute_not_exists(sk)'
                )
                
                logger.info(f"Correction logged: {correction_log.sk} for user {user_id}")
                return correction_log.sk
                
            except ClientError as e:
                if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                    # 이미 존재하는 경우 - 멱등성 보장됨
                    logger.info(f"Duplicate correction detected (idempotent): {correction_log.sk}")
                    return correction_log.sk
                else:
                    # 다른 에러는 재발생
                    raise
            
        except Exception as e:
            logger.error(f"Failed to log correction: {str(e)}")
            raise
    
    def _serialize_correction_log(self, correction_log: CorrectionLog) -> Dict[str, Any]:
        """
        CorrectionLog를 DynamoDB 호환 형태로 직렬화
        
        Enum과 datetime 객체를 적절히 변환하여 DynamoDB 저장 시 
        TypeError를 방지합니다.
        """
        # Pydantic의 dict() 메서드로 기본 변환
        item = correction_log.dict()
        
        # Enum 값들을 문자열로 변환
        if isinstance(item.get('task_category'), TaskCategory):
            item['task_category'] = item['task_category'].value
        elif hasattr(correction_log.task_category, 'value'):
            item['task_category'] = correction_log.task_category.value
        
        if item.get('correction_type') and isinstance(item['correction_type'], CorrectionType):
            item['correction_type'] = item['correction_type'].value
        elif correction_log.correction_type and hasattr(correction_log.correction_type, 'value'):
            item['correction_type'] = correction_log.correction_type.value
        
        if item.get('vector_sync_status'):
            if hasattr(item['vector_sync_status'], 'value'):
                item['vector_sync_status'] = item['vector_sync_status'].value
            elif hasattr(correction_log.vector_sync_status, 'value'):
                item['vector_sync_status'] = correction_log.vector_sync_status.value
        
        # datetime 객체들을 ISO 문자열로 변환
        datetime_fields = ['created_at', 'updated_at', 'last_vector_sync_attempt']
        for field in datetime_fields:
            if item.get(field):
                if isinstance(item[field], datetime):
                    item[field] = item[field].isoformat()
                elif hasattr(correction_log, field):
                    field_value = getattr(correction_log, field)
                    if field_value and isinstance(field_value, datetime):
                        item[field] = field_value.isoformat()
        
        # None 값 제거 (DynamoDB는 None을 지원하지 않음)
        item = {k: v for k, v in item.items() if v is not None}
        
        return item
    
    async def _check_duplicate_correction(self, correction_log: CorrectionLog) -> Optional[Dict[str, Any]]:
        """
        멱등성 보장을 위한 중복 수정 로그 확인
        
        동일한 SK를 가진 레코드가 이미 존재하는지 확인
        """
        try:
            response = self.correction_table.get_item(
                Key={
                    'pk': correction_log.pk,
                    'sk': correction_log.sk
                }
            )
            
            return response.get('Item')
            
        except Exception as e:
            logger.error(f"Failed to check duplicate correction: {str(e)}")
            return None
    
    async def get_pending_vector_sync_corrections(
        self,
        user_id: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        벡터 동기화가 필요한 수정 로그들 조회
        
        개선사항 #3: 벡터 검색 인덱싱 필드 활용
        """
        try:
            # 사용자의 모든 수정 로그 조회
            response = self.correction_table.query(
                KeyConditionExpression='pk = :pk',
                FilterExpression='vector_sync_status IN (:pending, :failed, :retry)',
                ExpressionAttributeValues={
                    ':pk': f'user#{user_id}',
                    ':pending': 'pending',
                    ':failed': 'failed',
                    ':retry': 'retry'
                },
                Limit=limit
            )
            
            items = response.get('Items', [])
            
            # VectorSyncManager를 사용하여 재시도 필요한 항목만 필터링
            from ..models.correction_log import VectorSyncManager, CorrectionLog
            
            corrections = []
            for item in items:
                # DynamoDB 아이템을 CorrectionLog 객체로 변환
                correction = CorrectionLog(**item)
                if VectorSyncManager.should_retry_sync(correction):
                    corrections.append(item)
            
            return corrections
            
        except Exception as e:
            logger.error(f"Failed to get pending vector sync corrections: {str(e)}")
            return []
    
    def _determine_context_scope(
        self,
        task_category: str,
        node_type: str,
        workflow_domain: str
    ) -> str:
        """컨텍스트 스코프 결정 로직"""
        
        # 특정 노드 타입은 node 스코프
        if node_type in ["sql_generator", "api_formatter"]:
            return "node"
        
        # 특정 태스크는 task 스코프
        elif task_category in ["email", "document"]:
            return "task"
        
        # 도메인별 특화는 domain 스코프
        elif workflow_domain in ["sales", "marketing", "support"]:
            return "domain"
        
        # 기본은 global 스코프
        else:
            return "global"
    
    def _get_applicable_contexts(
        self,
        context_scope: str,
        task_category: str,
        node_type: str,
        workflow_domain: str
    ) -> List[str]:
        """적용 가능한 컨텍스트 목록 생성"""
        
        contexts = []
        
        if context_scope == "node":
            contexts.append(f"node#{node_type}")
        elif context_scope == "task":
            contexts.extend([
                f"task#{task_category}",
                f"node#{node_type}"
            ])
        elif context_scope == "domain":
            contexts.extend([
                f"domain#{workflow_domain}",
                f"task#{task_category}",
                f"node#{node_type}"
            ])
        else:  # global
            contexts.extend([
                "global",
                f"domain#{workflow_domain}",
                f"task#{task_category}",
                f"node#{node_type}"
            ])
        
        return contexts
    
    async def get_recent_corrections(
        self,
        user_id: str,
        task_category: Optional[str] = None,
        hours: int = 24,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """최근 수정 로그 조회"""
        
        try:
            # 시간 범위 계산
            cutoff_time = datetime.now(timezone.utc).timestamp() - (hours * 3600)
            cutoff_iso = datetime.fromtimestamp(cutoff_time).isoformat()
            
            if task_category:
                # 태스크별 조회 (GSI 사용)
                response = self.correction_table.query(
                    IndexName=DynamoDBConfig.TASK_CATEGORY_INDEX,
                    KeyConditionExpression='gsi1_pk = :task_pk AND gsi1_sk > :cutoff',
                    ExpressionAttributeValues={
                        ':task_pk': f'task#{task_category}',
                        ':cutoff': f'user#{user_id}#{cutoff_iso}'
                    },
                    Limit=limit,
                    ScanIndexForward=False  # 최신순
                )
            else:
                # 사용자별 전체 조회
                response = self.correction_table.query(
                    IndexName=DynamoDBConfig.USER_RECENT_INDEX,
                    KeyConditionExpression='gsi2_pk = :user_pk AND gsi2_sk > :cutoff',
                    ExpressionAttributeValues={
                        ':user_pk': f'user#{user_id}',
                        ':cutoff': f'timestamp#{cutoff_iso}'
                    },
                    Limit=limit,
                    ScanIndexForward=False  # 최신순
                )
            
            return response.get('Items', [])
            
        except Exception as e:
            logger.error(f"Failed to get recent corrections: {str(e)}")
            return []
    
    async def get_corrections_by_pattern(
        self,
        user_id: str,
        metadata_pattern: Dict[str, str],
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """메타데이터 패턴으로 수정 로그 검색"""
        
        try:
            # 사용자의 모든 수정 로그 조회
            response = self.correction_table.query(
                KeyConditionExpression='pk = :pk',
                ExpressionAttributeValues={
                    ':pk': f'user#{user_id}'
                },
                Limit=100,  # 최근 100개만
                ScanIndexForward=False
            )
            
            items = response.get('Items', [])
            
            # 메타데이터 패턴 매칭
            matching_items = []
            for item in items:
                extracted_metadata = item.get('extracted_metadata', {})
                
                # 패턴 매칭 (모든 키-값이 일치해야 함)
                if all(
                    extracted_metadata.get(key) == value 
                    for key, value in metadata_pattern.items()
                ):
                    matching_items.append(item)
                    
                    if len(matching_items) >= limit:
                        break
            
            return matching_items
            
        except Exception as e:
            logger.error(f"Failed to get corrections by pattern: {str(e)}")
            return []
    
    async def update_correction_quality(
        self,
        correction_sk: str,
        user_id: str,
        is_valuable: bool,
        confidence: float,
        reason: str
    ) -> bool:
        """수정 로그의 품질 평가 결과 업데이트"""
        
        try:
            self.correction_table.update_item(
                Key={
                    'pk': f'user#{user_id}',
                    'sk': correction_sk
                },
                UpdateExpression='SET is_valuable = :valuable, quality_confidence = :confidence, quality_reason = :reason, updated_at = :updated',
                ExpressionAttributeValues={
                    ':valuable': is_valuable,
                    ':confidence': confidence,
                    ':reason': reason,
                    ':updated': datetime.now(timezone.utc).isoformat()
                }
            )
            
            logger.info(f"Updated correction quality: {correction_sk}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update correction quality: {str(e)}")
            return False
    
    async def update_vector_sync_status(
        self,
        correction_sk: str,
        user_id: str,
        status: str,
        embedding_id: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """벡터 동기화 상태 업데이트 (원자적 처리)"""
        
        try:
            update_expression = 'SET vector_sync_status = :status, updated_at = :updated, last_vector_sync_attempt = :attempt'
            expression_values = {
                ':status': status,
                ':updated': datetime.utcnow().isoformat(),
                ':attempt': datetime.utcnow().isoformat()
            }
            
            if embedding_id:
                update_expression += ', embedding_id = :embedding_id'
                expression_values[':embedding_id'] = embedding_id
            
            if error_message:
                update_expression += ', vector_sync_error = :error'
                expression_values[':error'] = error_message
            
            # 시도 횟수 증가 (실패인 경우)
            if status in ['failed', 'retry']:
                update_expression += ' ADD vector_sync_attempts :increment'
                expression_values[':increment'] = 1
            
            self.correction_table.update_item(
                Key={
                    'pk': f'user#{user_id}',
                    'sk': correction_sk
                },
                UpdateExpression=update_expression,
                ExpressionAttributeValues=expression_values
            )
            
            logger.info(f"Updated vector sync status: {correction_sk} -> {status}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update vector sync status: {str(e)}")
            return False