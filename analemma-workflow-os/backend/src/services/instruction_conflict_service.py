"""
지능형 지침 증류기 - 지침 충돌 감지 및 해결 서비스

개선사항 #2: metadata_signature를 활용한 충돌 방지 로직
"""

import boto3
import logging
import json
import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from ..models.correction_log import (
    DistilledInstruction, 
    CorrectionType, 
    ConflictResolver,
    TaskCategory
)

logger = logging.getLogger(__name__)

class InstructionConflictService:
    """지침 충돌 감지 및 해결 서비스"""
    
    def __init__(self):
        self.ddb = boto3.resource('dynamodb')
        self.ddb_client = boto3.client('dynamodb')  # 트랜잭션용 클라이언트 추가
        self.instruction_table = self.ddb.Table(
            os.environ.get('DISTILLED_INSTRUCTIONS_TABLE', 'distilled-instructions')
        )
        self.conflict_resolver = ConflictResolver()
        self.semantic_validator = None  # LLM 기반 의미적 검증기 (지연 초기화)
    
    async def create_instruction_with_conflict_check(
        self,
        user_id: str,
        category: CorrectionType,
        context_scope: str,
        instruction_text: str,
        metadata_signature: Dict[str, str],
        source_correction_ids: List[str],
        applicable_task_categories: List[TaskCategory] = None,
        applicable_node_types: List[str] = None,
        conflict_resolution_strategy: str = "ask_user",
        enable_semantic_validation: bool = False
    ) -> Tuple[Optional[DistilledInstruction], List[Dict[str, Any]]]:
        """
        충돌 검사를 포함한 새 지침 생성
        
        개선사항:
        1. 원자적 트랜잭션으로 다중 아이템 업데이트
        2. 모든 충돌 항목을 수집하여 일괄 처리
        3. 선택적 의미적 충돌 검증
        
        Returns:
            (생성된_지침, 충돌_정보_리스트)
        """
        try:
            # 기존 지침들 조회
            existing_instructions = await self.get_active_instructions(
                user_id, category, context_scope
            )
            
            # 1차 충돌 감지 (메타데이터 시그니처 기반)
            signature_conflicts = self.conflict_resolver.detect_conflicts(
                existing_instructions,
                metadata_signature,
                category,
                context_scope
            )
            
            # 2차 의미적 충돌 검증 (선택적)
            semantic_conflicts = []
            if enable_semantic_validation and not signature_conflicts:
                semantic_conflicts = await self._detect_semantic_conflicts(
                    instruction_text, existing_instructions
                )
            
            # 모든 충돌 수집
            all_conflicts = signature_conflicts + semantic_conflicts
            
            if not all_conflicts:
                # 충돌 없음 - 새 지침 생성
                new_instruction = DistilledInstruction(
                    pk=f"user#{user_id}",
                    user_id=user_id,
                    category=category,
                    context_scope=context_scope,
                    instruction=instruction_text,
                    confidence=0.8,
                    source_correction_ids=source_correction_ids,
                    pattern_description=f"Generated from {len(source_correction_ids)} corrections",
                    metadata_signature=metadata_signature,
                    applicable_task_categories=applicable_task_categories or [],
                    applicable_node_types=applicable_node_types or []
                )
                
                await self.save_instruction(new_instruction)
                
                logger.info(f"New instruction created without conflicts: {new_instruction.sk}")
                return new_instruction, []
            
            else:
                # 충돌 발생 - 모든 충돌 항목 처리
                conflict_info = []
                
                # 모든 충돌에 대한 해결 전략 수집
                for conflicting_instruction in all_conflicts:
                    resolution = self.conflict_resolver.resolve_conflict_strategy(
                        conflicting_instruction,
                        metadata_signature,
                        conflict_resolution_strategy
                    )
                    
                    conflict_info.append({
                        "conflicting_instruction_id": conflicting_instruction.sk,
                        "conflicting_instruction_text": conflicting_instruction.instruction,
                        "resolution": resolution,
                        "metadata_conflicts": resolution["conflicts"],
                        "conflict_type": "semantic" if conflicting_instruction in semantic_conflicts else "signature"
                    })
                
                # 자동 해결 가능한 경우 - 원자적 트랜잭션으로 처리
                if conflict_resolution_strategy == "override":
                    new_instruction = await self._resolve_all_conflicts_atomically(
                        user_id, instruction_text, metadata_signature, 
                        category, context_scope, source_correction_ids,
                        all_conflicts, applicable_task_categories, applicable_node_types
                    )
                    
                    if new_instruction:
                        logger.info(f"All conflicts resolved atomically by override: {new_instruction.sk}")
                        return new_instruction, conflict_info
                
                # 사용자 확인이 필요한 경우
                logger.info(f"Conflicts detected, user confirmation required: {len(all_conflicts)} conflicts")
                return None, conflict_info
                
        except Exception as e:
            logger.error(f"Failed to create instruction with conflict check: {str(e)}")
            raise
    
    async def resolve_conflict_manually(
        self,
        user_id: str,
        conflicting_instruction_ids: List[str],  # 다중 충돌 ID 지원
        resolution_action: str,  # "override" | "keep_existing" | "merge"
        new_instruction_text: str,
        new_metadata_signature: Dict[str, str]
    ) -> Optional[DistilledInstruction]:
        """
        사용자가 수동으로 다중 충돌을 원자적으로 해결
        
        개선사항: 여러 충돌 지침을 한 번에 처리하는 원자적 트랜잭션
        """
        try:
            if not conflicting_instruction_ids:
                raise ValueError("No conflicting instruction IDs provided")
            
            # 모든 충돌하는 지침들 조회
            conflicting_instructions = []
            for instruction_id in conflicting_instruction_ids:
                instruction = await self.get_instruction_by_id(user_id, instruction_id)
                if instruction:
                    conflicting_instructions.append(instruction)
                else:
                    logger.warning(f"Conflicting instruction not found: {instruction_id}")
            
            if not conflicting_instructions:
                raise ValueError("No valid conflicting instructions found")
            
            if resolution_action == "keep_existing":
                # 첫 번째 기존 지침 유지
                logger.info(f"User chose to keep existing instruction: {conflicting_instructions[0].sk}")
                return conflicting_instructions[0]
            
            elif resolution_action in ["override", "merge"]:
                # 새 지침 생성 및 모든 기존 지침 비활성화를 원자적으로 처리
                return await self._resolve_multiple_conflicts_atomically(
                    user_id, new_instruction_text, new_metadata_signature,
                    conflicting_instructions, resolution_action
                )
            
            else:
                raise ValueError(f"Invalid resolution action: {resolution_action}")
                
        except Exception as e:
            logger.error(f"Failed to resolve conflicts manually: {str(e)}")
            raise
    
    async def get_active_instructions(
        self,
        user_id: str,
        category: CorrectionType = None,
        context_scope: str = None
    ) -> List[DistilledInstruction]:
        """활성 지침들 조회"""
        try:
            query_params = {
                'KeyConditionExpression': 'pk = :pk',
                'FilterExpression': 'is_active = :active',
                'ExpressionAttributeValues': {
                    ':pk': f'user#{user_id}',
                    ':active': True
                }
            }
            
            # 추가 필터 조건
            if category:
                query_params['FilterExpression'] += ' AND category = :category'
                query_params['ExpressionAttributeValues'][':category'] = category.value
            
            if context_scope:
                query_params['FilterExpression'] += ' AND context_scope = :scope'
                query_params['ExpressionAttributeValues'][':scope'] = context_scope
            
            response = self.instruction_table.query(**query_params)
            
            instructions = []
            for item in response.get('Items', []):
                instructions.append(DistilledInstruction(**item))
            
            return instructions
            
        except Exception as e:
            logger.error(f"Failed to get active instructions: {str(e)}")
            return []
    
    async def get_instruction_by_id(
        self,
        user_id: str,
        instruction_id: str
    ) -> Optional[DistilledInstruction]:
        """특정 지침 조회"""
        try:
            response = self.instruction_table.get_item(
                Key={
                    'pk': f'user#{user_id}',
                    'sk': instruction_id
                }
            )
            
            item = response.get('Item')
            if item:
                return DistilledInstruction(**item)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get instruction by id: {str(e)}")
            return None
    
    async def save_instruction(self, instruction: DistilledInstruction) -> bool:
        """지침 저장"""
        try:
            item = instruction.dict()
            
            # datetime을 ISO string으로 변환
            item['created_at'] = instruction.created_at.isoformat()
            item['updated_at'] = instruction.updated_at.isoformat()
            
            self.instruction_table.put_item(Item=item)
            
            logger.info(f"Instruction saved: {instruction.sk}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save instruction: {str(e)}")
            return False
    
    async def update_instruction(self, instruction: DistilledInstruction) -> bool:
        """지침 업데이트"""
        try:
            self.instruction_table.update_item(
                Key={
                    'pk': instruction.pk,
                    'sk': instruction.sk
                },
                UpdateExpression='SET is_active = :active, superseded_by = :superseded, updated_at = :updated',
                ExpressionAttributeValues={
                    ':active': instruction.is_active,
                    ':superseded': instruction.superseded_by,
                    ':updated': datetime.now(timezone.utc).isoformat()
                }
            )
            
            logger.info(f"Instruction updated: {instruction.sk}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update instruction: {str(e)}")
            return False
    
    async def get_conflict_history(
        self,
        user_id: str,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """충돌 해결 이력 조회"""
        try:
            response = self.instruction_table.query(
                KeyConditionExpression='pk = :pk',
                FilterExpression='attribute_exists(superseded_by)',
                ExpressionAttributeValues={
                    ':pk': f'user#{user_id}'
                },
                Limit=limit,
                ScanIndexForward=False  # 최신순
            )
            
            history = []
            for item in response.get('Items', []):
                instruction = DistilledInstruction(**item)
                history.append({
                    "instruction_id": instruction.sk,
                    "instruction_text": instruction.instruction,
                    "superseded_by": instruction.superseded_by,
                    "metadata_signature": instruction.metadata_signature,
                    "created_at": instruction.created_at.isoformat(),
                    "updated_at": instruction.updated_at.isoformat()
                })
            
            return history
            
        except Exception as e:
            logger.error(f"Failed to get conflict history: {str(e)}")
            return []
    
    async def _resolve_all_conflicts_atomically(
        self,
        user_id: str,
        instruction_text: str,
        metadata_signature: Dict[str, str],
        category: CorrectionType,
        context_scope: str,
        source_correction_ids: List[str],
        conflicting_instructions: List[DistilledInstruction],
        applicable_task_categories: List[TaskCategory] = None,
        applicable_node_types: List[str] = None
    ) -> Optional[DistilledInstruction]:
        """
        모든 충돌을 원자적 트랜잭션으로 해결
        
        개선사항:
        1. DynamoDB 100개 아이템 제한 처리
        2. 정교한 에러 핸들링 및 사용자 피드백
        3. 트랜잭션 실패 시 상세 정보 제공
        """
        try:
            # DynamoDB 트랜잭션 제한 검증 (100개 아이템)
            max_conflicts = 99  # 새 지침 1개 + 기존 지침 99개
            if len(conflicting_instructions) > max_conflicts:
                logger.warning(f"Too many conflicts ({len(conflicting_instructions)}), limiting to {max_conflicts}")
                conflicting_instructions = conflicting_instructions[:max_conflicts]
            
            # 새 지침 생성
            new_instruction = DistilledInstruction(
                pk=f"user#{user_id}",
                user_id=user_id,
                category=category,
                context_scope=context_scope,
                instruction=instruction_text,
                confidence=0.8,
                source_correction_ids=source_correction_ids,
                pattern_description=f"Override resolution from {len(conflicting_instructions)} conflicts",
                metadata_signature=metadata_signature,
                applicable_task_categories=applicable_task_categories or [],
                applicable_node_types=applicable_node_types or []
            )
            
            # 트랜잭션 아이템들 준비
            transact_items = []
            
            # 1. 새 지침 생성 (조건: 동일 SK가 없어야 함)
            new_item = self._serialize_instruction(new_instruction)
            transact_items.append({
                'Put': {
                    'TableName': self.instruction_table.table_name,
                    'Item': new_item,
                    'ConditionExpression': 'attribute_not_exists(sk)'
                }
            })
            
            # 2. 모든 충돌 지침들 비활성화 (조건: 현재 활성 상태여야 함)
            for conflicting_instruction in conflicting_instructions:
                conflicting_instruction.is_active = False
                conflicting_instruction.superseded_by = new_instruction.sk
                conflicting_instruction.updated_at = datetime.now(timezone.utc)
                
                transact_items.append({
                    'Update': {
                        'TableName': self.instruction_table.table_name,
                        'Key': {
                            'pk': {'S': conflicting_instruction.pk},
                            'sk': {'S': conflicting_instruction.sk}
                        },
                        'UpdateExpression': 'SET is_active = :inactive, superseded_by = :superseded, updated_at = :updated',
                        'ConditionExpression': 'is_active = :active',  # 현재 활성 상태인지 확인
                        'ExpressionAttributeValues': {
                            ':inactive': {'BOOL': False},
                            ':active': {'BOOL': True},
                            ':superseded': {'S': new_instruction.sk},
                            ':updated': {'S': datetime.now(timezone.utc).isoformat()}
                        }
                    }
                })
            
            # 원자적 트랜잭션 실행
            self.ddb_client.transact_write_items(TransactItems=transact_items)
            
            logger.info(f"Atomic conflict resolution completed: {new_instruction.sk} (resolved {len(conflicting_instructions)} conflicts)")
            return new_instruction
            
        except ClientError as e:
            return self._handle_transaction_error(e, conflicting_instructions, "conflict resolution")
        except Exception as e:
            logger.error(f"Failed to resolve conflicts atomically: {str(e)}")
            raise
    
    def _handle_transaction_error(
        self,
        error: ClientError,
        conflicting_instructions: List[DistilledInstruction],
        operation_name: str
    ) -> None:
        """
        트랜잭션 에러 정교한 처리 및 사용자 피드백
        
        개선사항: CancellationReasons 파싱으로 구체적인 실패 원인 제공
        """
        if error.response['Error']['Code'] == 'TransactionCanceledException':
            logger.error(f"Transaction cancelled during {operation_name}: {error}")
            
            # 트랜잭션 실패 상세 분석
            cancellation_reasons = error.response.get('CancellationReasons', [])
            concurrent_update_detected = False
            condition_failures = []
            
            for i, reason in enumerate(cancellation_reasons):
                reason_code = reason.get('Code', 'None')
                reason_message = reason.get('Message', '')
                
                if reason_code != 'None':
                    logger.error(f"Transaction item {i} failed: {reason_code} - {reason_message}")
                    
                    if reason_code == 'ConditionalCheckFailed':
                        if i == 0:
                            # 새 지침 생성 실패 - 이미 동일한 지침 존재
                            condition_failures.append({
                                'type': 'duplicate_instruction',
                                'message': '동일한 지침이 이미 존재합니다'
                            })
                        else:
                            # 기존 지침 업데이트 실패 - 다른 프로세스에 의해 이미 수정됨
                            instruction_index = i - 1
                            if instruction_index < len(conflicting_instructions):
                                failed_instruction = conflicting_instructions[instruction_index]
                                condition_failures.append({
                                    'type': 'concurrent_update',
                                    'instruction_id': failed_instruction.sk,
                                    'message': f'지침 {failed_instruction.sk}이(가) 다른 프로세스에 의해 이미 업데이트되었습니다'
                                })
                                concurrent_update_detected = True
            
            # 사용자 친화적 에러 메시지 생성
            if concurrent_update_detected:
                user_message = (
                    "충돌 해결 중 일부 지침이 다른 프로세스에 의해 이미 수정되었습니다. "
                    "최신 상태를 다시 확인한 후 재시도해주세요."
                )
            elif condition_failures:
                user_message = "지침 생성 조건을 만족하지 않습니다. 중복된 지침이 이미 존재할 수 있습니다."
            else:
                user_message = f"트랜잭션 실행 중 오류가 발생했습니다: {operation_name}"
            
            # 구조화된 에러 정보와 함께 예외 재발생
            enhanced_error = ClientError(
                error_response={
                    **error.response,
                    'UserMessage': user_message,
                    'ConditionFailures': condition_failures,
                    'ConcurrentUpdateDetected': concurrent_update_detected
                },
                operation_name=error.operation_name
            )
            raise enhanced_error
        else:
            # 다른 타입의 ClientError는 그대로 재발생
            raise error
    
    async def _resolve_multiple_conflicts_atomically(
        self,
        user_id: str,
        instruction_text: str,
        metadata_signature: Dict[str, str],
        conflicting_instructions: List[DistilledInstruction],
        resolution_action: str
    ) -> Optional[DistilledInstruction]:
        """
        다중 충돌을 원자적으로 해결 (수동 해결용)
        
        개선사항: 트랜잭션 제한 및 에러 핸들링 적용
        """
        try:
            # DynamoDB 트랜잭션 제한 검증
            max_conflicts = 99
            if len(conflicting_instructions) > max_conflicts:
                logger.warning(f"Too many conflicts for manual resolution ({len(conflicting_instructions)}), limiting to {max_conflicts}")
                conflicting_instructions = conflicting_instructions[:max_conflicts]
            
            # 첫 번째 충돌 지침을 기반으로 새 지침 생성
            base_instruction = conflicting_instructions[0]
            
            if resolution_action == "merge":
                # 모든 충돌 지침들의 메타데이터 병합
                merged_signature = {}
                for instruction in conflicting_instructions:
                    merged_signature.update(instruction.metadata_signature)
                merged_signature.update(metadata_signature)
                final_signature = merged_signature
            else:  # override
                final_signature = metadata_signature
            
            new_instruction = base_instruction.create_override_version(
                instruction_text,
                final_signature
            )
            
            # 트랜잭션 아이템들 준비
            transact_items = []
            
            # 1. 새 지침 생성
            new_item = self._serialize_instruction(new_instruction)
            transact_items.append({
                'Put': {
                    'TableName': self.instruction_table.table_name,
                    'Item': new_item,
                    'ConditionExpression': 'attribute_not_exists(sk)'
                }
            })
            
            # 2. 모든 충돌 지침들 비활성화
            for conflicting_instruction in conflicting_instructions:
                conflicting_instruction.is_active = False
                conflicting_instruction.superseded_by = new_instruction.sk
                conflicting_instruction.updated_at = datetime.now(timezone.utc)
                
                transact_items.append({
                    'Update': {
                        'TableName': self.instruction_table.table_name,
                        'Key': {
                            'pk': {'S': conflicting_instruction.pk},
                            'sk': {'S': conflicting_instruction.sk}
                        },
                        'UpdateExpression': 'SET is_active = :inactive, superseded_by = :superseded, updated_at = :updated',
                        'ConditionExpression': 'is_active = :active',
                        'ExpressionAttributeValues': {
                            ':inactive': {'BOOL': False},
                            ':active': {'BOOL': True},
                            ':superseded': {'S': new_instruction.sk},
                            ':updated': {'S': datetime.now(timezone.utc).isoformat()}
                        }
                    }
                })
            
            # 원자적 트랜잭션 실행
            self.ddb_client.transact_write_items(TransactItems=transact_items)
            
            logger.info(f"Manual conflict resolution completed atomically: {new_instruction.sk}")
            return new_instruction
            
        except ClientError as e:
            return self._handle_transaction_error(e, conflicting_instructions, "manual conflict resolution")
        except Exception as e:
            logger.error(f"Failed to resolve multiple conflicts atomically: {str(e)}")
            raise
    
    def _serialize_instruction(self, instruction: DistilledInstruction) -> Dict[str, Any]:
        """
        DistilledInstruction을 DynamoDB 트랜잭션용 형태로 직렬화
        
        개선사항: String Set(SS) 빈 값 제약 처리
        """
        item_dict = instruction.dict()
        
        # DynamoDB 트랜잭션용 타입 지정 형태로 변환
        serialized = {}
        
        for key, value in item_dict.items():
            if value is None:
                continue  # None 값은 제외
            elif isinstance(value, str):
                if value.strip():  # 빈 문자열 제외
                    serialized[key] = {'S': value}
            elif isinstance(value, bool):
                serialized[key] = {'BOOL': value}
            elif isinstance(value, int):
                serialized[key] = {'N': str(value)}
            elif isinstance(value, float):
                serialized[key] = {'N': str(value)}
            elif isinstance(value, list):
                if value:  # 빈 리스트가 아닌 경우만
                    # String Set용 필터링: 빈 문자열 제거 및 중복 제거
                    filtered_items = []
                    seen = set()
                    for item in value:
                        str_item = str(item).strip()
                        if str_item and str_item not in seen:  # 빈 문자열과 중복 제거
                            filtered_items.append(str_item)
                            seen.add(str_item)
                    
                    if filtered_items:  # 필터링 후에도 항목이 있는 경우만
                        serialized[key] = {'SS': filtered_items}
            elif isinstance(value, dict):
                if value:  # 빈 딕셔너리가 아닌 경우만
                    serialized[key] = {'S': json.dumps(value, ensure_ascii=False)}
            elif isinstance(value, datetime):
                serialized[key] = {'S': value.isoformat()}
            elif hasattr(value, 'value'):  # Enum
                enum_value = str(value.value).strip()
                if enum_value:  # 빈 값이 아닌 경우만
                    serialized[key] = {'S': enum_value}
            else:
                str_value = str(value).strip()
                if str_value:  # 빈 값이 아닌 경우만
                    serialized[key] = {'S': str_value}
        
        return serialized
    
    async def _detect_semantic_conflicts(
        self,
        new_instruction_text: str,
        existing_instructions: List[DistilledInstruction]
    ) -> List[DistilledInstruction]:
        """
        LLM 기반 의미적 충돌 감지 (배치 최적화)
        
        개선사항:
        1. 우선순위 필터링으로 LLM 호출 대상 최소화
        2. 배치 검증으로 N번 호출을 1번으로 축소
        3. 비용 효율성 극대화
        """
        try:
            if not existing_instructions:
                return []
            
            # 의미적 검증기 지연 초기화
            if self.semantic_validator is None:
                self.semantic_validator = await self._initialize_semantic_validator()
            
            if not self.semantic_validator:
                logger.warning("Semantic validator not available, skipping semantic conflict detection")
                return []
            
            # 1단계: 우선순위 필터링 (메타데이터 유사성 기반)
            priority_candidates = self._filter_semantic_candidates(
                new_instruction_text, existing_instructions
            )
            
            if not priority_candidates:
                logger.info("No semantic validation candidates after priority filtering")
                return []
            
            # 2단계: 배치 의미적 검증 (1회 LLM 호출)
            semantic_conflicts = await self._batch_semantic_validation(
                new_instruction_text, priority_candidates
            )
            
            logger.info(f"Semantic validation: {len(priority_candidates)} candidates -> {len(semantic_conflicts)} conflicts")
            return semantic_conflicts
            
        except Exception as e:
            logger.error(f"Failed to detect semantic conflicts: {str(e)}")
            return []  # 실패 시 빈 리스트 반환 (의미적 검증은 선택적)
    
    def _filter_semantic_candidates(
        self,
        new_instruction_text: str,
        existing_instructions: List[DistilledInstruction]
    ) -> List[DistilledInstruction]:
        """
        의미적 검증 우선순위 필터링
        
        메타데이터 유사성과 카테고리 기반으로 LLM 검증 대상을 선별하여
        불필요한 호출을 최소화합니다.
        """
        candidates = []
        new_text_lower = new_instruction_text.lower()
        
        # 키워드 기반 우선순위 매핑
        semantic_domains = {
            'length': ['길게', '짧게', '자세히', '요약', '간단히', '상세히', 'long', 'short', 'detailed', 'brief'],
            'tone': ['정중하게', '직설적', '공손하게', '친근하게', 'formal', 'casual', 'polite', 'direct'],
            'style': ['기술적', '전문적', '쉽게', '일반인', 'technical', 'simple', 'professional', 'basic'],
            'format': ['구조화', '자유형식', '목록', '문단', 'structured', 'freeform', 'bullet', 'paragraph']
        }
        
        # 새 지침의 의미적 도메인 식별
        new_domains = set()
        for domain, keywords in semantic_domains.items():
            if any(keyword in new_text_lower for keyword in keywords):
                new_domains.add(domain)
        
        for instruction in existing_instructions:
            should_check = False
            
            # 1. 동일 카테고리는 항상 검사
            if hasattr(instruction, 'category') and hasattr(instruction.category, 'value'):
                should_check = True
            
            # 2. 의미적 도메인 겹치는 경우 우선 검사
            instruction_text_lower = instruction.instruction.lower()
            for domain in new_domains:
                if any(keyword in instruction_text_lower for keyword in semantic_domains[domain]):
                    should_check = True
                    break
            
            # 3. 메타데이터 시그니처에 공통 키가 있는 경우
            if instruction.metadata_signature:
                # 새 지침과 공통 메타데이터 키가 있으면 검사 대상
                # (값이 다르더라도 의미적 충돌 가능성 있음)
                should_check = True
            
            if should_check:
                candidates.append(instruction)
        
        # 최대 10개로 제한 (비용 효율성)
        return candidates[:10]
    
    async def _batch_semantic_validation(
        self,
        new_instruction_text: str,
        candidate_instructions: List[DistilledInstruction]
    ) -> List[DistilledInstruction]:
        """
        배치 의미적 검증 (1회 LLM 호출로 모든 후보 검사)
        
        기존: N번의 개별 LLM 호출
        개선: 1번의 배치 LLM 호출
        """
        try:
            if not candidate_instructions:
                return []
            
            # 배치 검증 실행
            batch_result = await self.semantic_validator.batch_check_conflicts(
                new_instruction_text, candidate_instructions
            )
            
            # 충돌로 판정된 지침들만 반환
            conflicting_instructions = []
            conflicting_indices = batch_result.get('conflicting_indices', [])
            
            for index in conflicting_indices:
                if 0 <= index < len(candidate_instructions):
                    conflicting_instructions.append(candidate_instructions[index])
            
            return conflicting_instructions
            
        except Exception as e:
            logger.error(f"Batch semantic validation failed: {str(e)}")
            # 폴백: 개별 검증 (최대 3개만)
            return await self._fallback_individual_validation(
                new_instruction_text, candidate_instructions[:3]
            )
    
    async def _fallback_individual_validation(
        self,
        new_instruction_text: str,
        candidate_instructions: List[DistilledInstruction]
    ) -> List[DistilledInstruction]:
        """배치 검증 실패 시 폴백 개별 검증 (제한적)"""
        conflicts = []
        
        for instruction in candidate_instructions:
            try:
                is_conflicting = await self._check_semantic_conflict(
                    new_instruction_text, instruction.instruction
                )
                if is_conflicting:
                    conflicts.append(instruction)
            except Exception as e:
                logger.warning(f"Individual semantic check failed for {instruction.sk}: {e}")
                continue
        
        return conflicts
    
    async def _initialize_semantic_validator(self):
        """의미적 검증기 초기화 (LLM 클라이언트)"""
        try:
            # 환경에 따라 적절한 LLM 클라이언트 초기화
            # 예: OpenAI, Anthropic, 또는 로컬 모델
            
            # 여기서는 간단한 모의 구현
            # 실제로는 model_router나 다른 LLM 서비스를 사용
            return MockSemanticValidator()
            
        except Exception as e:
            logger.error(f"Failed to initialize semantic validator: {str(e)}")
            return None
    
    async def _check_semantic_conflict(
        self,
        instruction1: str,
        instruction2: str
    ) -> bool:
        """
        두 지침 간의 의미적 충돌 여부 확인
        
        Returns:
            True if conflicting, False otherwise
        """
        try:
            if not self.semantic_validator:
                return False
            
            # LLM을 사용한 의미적 충돌 검사
            conflict_result = await self.semantic_validator.check_conflict(
                instruction1, instruction2
            )
            
            return conflict_result.get('is_conflicting', False)
            
        except Exception as e:
            logger.error(f"Failed to check semantic conflict: {str(e)}")
            return False  # 에러 시 충돌 없음으로 처리


class MockSemanticValidator:
    """
    의미적 검증기 모의 구현
    
    실제 구현에서는 LLM API를 호출하여 의미적 충돌을 감지합니다.
    배치 검증 지원으로 비용 효율성 극대화.
    """
    
    async def batch_check_conflicts(
        self, 
        new_instruction: str, 
        candidate_instructions: List[DistilledInstruction]
    ) -> Dict[str, Any]:
        """
        배치 의미적 충돌 검사 (1회 LLM 호출)
        
        실제 구현에서는 다음과 같은 프롬프트를 사용할 수 있습니다:
        
        "새 지침: {new_instruction}
        
        기존 지침들:
        1. {instruction1}
        2. {instruction2}
        ...
        
        새 지침과 의미적으로 충돌하는 기존 지침의 번호를 모두 나열해주세요.
        충돌 이유도 함께 설명해주세요."
        """
        
        conflicting_indices = []
        conflict_reasons = []
        
        # 간단한 키워드 기반 충돌 감지 (실제로는 LLM 사용)
        conflicting_pairs = [
            (['길게', '자세히', '상세히'], ['짧게', '요약', '간단히']),
            (['정중하게', '공손하게'], ['직설적으로', '단도직입적으로']),
            (['기술적으로', '전문적으로'], ['쉽게', '일반인도']),
        ]
        
        new_instruction_lower = new_instruction.lower()
        
        for i, candidate in enumerate(candidate_instructions):
            candidate_text_lower = candidate.instruction.lower()
            
            for positive_keywords, negative_keywords in conflicting_pairs:
                has_positive_new = any(keyword in new_instruction_lower for keyword in positive_keywords)
                has_negative_candidate = any(keyword in candidate_text_lower for keyword in negative_keywords)
                
                has_negative_new = any(keyword in new_instruction_lower for keyword in negative_keywords)
                has_positive_candidate = any(keyword in candidate_text_lower for keyword in positive_keywords)
                
                if (has_positive_new and has_negative_candidate) or (has_negative_new and has_positive_candidate):
                    conflicting_indices.append(i)
                    conflict_reasons.append(f"Semantic conflict between opposing concepts")
                    break
        
        return {
            'conflicting_indices': conflicting_indices,
            'conflict_reasons': conflict_reasons,
            'total_checked': len(candidate_instructions),
            'confidence': 0.85
        }
    
    async def check_conflict(self, instruction1: str, instruction2: str) -> Dict[str, Any]:
        """
        두 지침의 의미적 충돌 여부를 확인 (개별 검증용 - 폴백)
        """
        
        # 간단한 키워드 기반 충돌 감지 (실제로는 LLM 사용)
        conflicting_pairs = [
            (['길게', '자세히', '상세히'], ['짧게', '요약', '간단히']),
            (['정중하게', '공손하게'], ['직설적으로', '단도직입적으로']),
            (['기술적으로', '전문적으로'], ['쉽게', '일반인도']),
        ]
        
        instruction1_lower = instruction1.lower()
        instruction2_lower = instruction2.lower()
        
        for positive_keywords, negative_keywords in conflicting_pairs:
            has_positive_1 = any(keyword in instruction1_lower for keyword in positive_keywords)
            has_negative_2 = any(keyword in instruction2_lower for keyword in negative_keywords)
            
            has_positive_2 = any(keyword in instruction2_lower for keyword in positive_keywords)
            has_negative_1 = any(keyword in instruction1_lower for keyword in negative_keywords)
            
            if (has_positive_1 and has_negative_2) or (has_positive_2 and has_negative_1):
                return {
                    'is_conflicting': True,
                    'reason': f'Semantic conflict detected between opposing concepts',
                    'confidence': 0.8
                }
        
        return {
            'is_conflicting': False,
            'reason': 'No semantic conflict detected',
            'confidence': 0.9
        }