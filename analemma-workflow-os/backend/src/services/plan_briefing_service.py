# -*- coding: utf-8 -*-
"""
Plan Briefing Service

워크플로우 실행 전 미리보기를 생성하는 서비스입니다.
워크플로우 설정과 초기 상태를 분석하여 예상 실행 계획과 결과물을 생성합니다.
"""

import json
import os
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

# 모델 임포트
try:
    from src.models.plan_briefing import (
        PlanBriefing, PlanStep, DraftResult, RiskLevel
    )
except ImportError:
    from src.models.plan_briefing import (
        PlanBriefing, PlanStep, DraftResult, RiskLevel
    )

logger = logging.getLogger(__name__)

# LLM 클라이언트 (OpenAI 또는 다른 프로바이더)
try:
    from openai import AsyncOpenAI
    HAS_OPENAI = True
except ImportError:
    try:
        import openai
        HAS_OPENAI = True
    except ImportError:
        HAS_OPENAI = False
    logger.warning("OpenAI not available, using mock briefing generation")


class PlanBriefingService:
    """
    실행 전 계획 브리핑 생성 서비스
    
    워크플로우 실행 직전에 사용자에게 미리보기를 제공합니다.
    - 실행 순서 분석
    - 각 단계 예상 동작
    - 예상 소요 시간
    - 위험 분석
    - 예상 결과물 초안
    """
    
    def __init__(self, openai_api_key: Optional[str] = None):
        """
        Args:
            openai_api_key: OpenAI API 키 (없으면 환경변수에서 로드)
        """
        self.api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        if HAS_OPENAI and self.api_key:
            try:
                # AsyncOpenAI 클라이언트 사용 (v1.0+)
                self.client = AsyncOpenAI(api_key=self.api_key)
            except NameError:
                # 구버전 호환성 유지
                openai.api_key = self.api_key
                self.client = None
    
    SYSTEM_PROMPT = """You are an expert workflow analyst. 
Your job is to analyze a workflow configuration and predict:
1. The exact execution order of nodes
2. What each node will do with the given inputs
3. The expected outputs/results
4. Potential risks or side effects

Be specific and actionable. The user should understand exactly what will happen.
Always respond in the same language as the user's workflow names and descriptions.
If the workflow is in Korean, respond in Korean. If in English, respond in English."""

    # 노드 타입별 기본 소요 시간 (초)
    DEFAULT_DURATIONS = {
        "llm": 5,
        "hitp": 30,  # Human-in-the-loop은 대기 시간이 김
        "api_call": 3,
        "email": 2,
        "condition": 1,
        "transform": 1,
        "aggregator": 2,
        "default": 2
    }
    
    # 외부 영향이 있는 노드 타입
    SIDE_EFFECT_TYPES = {"email", "api_call", "webhook", "notification", "payment"}

    def __init__(self, openai_api_key: Optional[str] = None):
        """
        Args:
            openai_api_key: OpenAI API 키 (없으면 환경변수에서 로드)
        """
        self.api_key = openai_api_key or os.environ.get("OPENAI_API_KEY")
        if HAS_OPENAI and self.api_key:
            openai.api_key = self.api_key

    async def generate_briefing(
        self,
        workflow_config: Dict[str, Any],
        initial_statebag: Dict[str, Any],
        user_context: Optional[Dict[str, Any]] = None,
        use_llm: bool = True
    ) -> PlanBriefing:
        """
        워크플로우 설정과 초기 상태를 분석하여 브리핑 생성
        
        Args:
            workflow_config: 워크플로우 설정 (nodes, edges 포함)
            initial_statebag: 초기 상태 데이터
            user_context: 사용자 컨텍스트 (선택)
            use_llm: LLM을 사용하여 상세 분석할지 여부
            
        Returns:
            PlanBriefing: 생성된 브리핑
        """
        nodes = workflow_config.get('nodes', [])
        edges = workflow_config.get('edges', [])
        workflow_id = workflow_config.get('id', 'unknown')
        workflow_name = workflow_config.get('name', 'Unnamed Workflow')
        
        if use_llm and HAS_OPENAI and self.api_key:
            try:
                return await self._generate_with_llm(
                    workflow_config, initial_statebag, user_context
                )
            except Exception as e:
                logger.warning(f"LLM briefing generation failed, falling back to rule-based: {e}")
        
        # 규칙 기반 브리핑 생성 (폴백 또는 LLM 비활성화 시)
        return self._generate_rule_based(
            workflow_id, workflow_name, nodes, edges, initial_statebag
        )

    async def _generate_with_llm(
        self,
        workflow_config: Dict[str, Any],
        initial_statebag: Dict[str, Any],
        user_context: Optional[Dict[str, Any]]
    ) -> PlanBriefing:
        """LLM을 사용한 상세 브리핑 생성"""
        
        analysis_prompt = self._build_analysis_prompt(
            workflow_config.get('nodes', []),
            workflow_config.get('edges', []),
            initial_statebag,
            user_context
        )
        
        if hasattr(self, 'client') and self.client:
            # AsyncOpenAI 클라이언트 사용 (v1.0+)
            response = await self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": analysis_prompt}
                ],
                response_format={"type": "json_object"},
                max_tokens=2000,
                temperature=0.3  # 일관된 분석을 위해 낮은 temperature
            )
        else:
            # 구버전 호환성 유지
            response = await openai.ChatCompletion.acreate(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": analysis_prompt}
                ],
                response_format={"type": "json_object"},
                max_tokens=2000,
                temperature=0.3  # 일관된 분석을 위해 낮은 temperature
            )
        
        analysis = json.loads(response.choices[0].message.content)
        
        return self._build_briefing_from_analysis(workflow_config, analysis)

    def _build_analysis_prompt(
        self,
        nodes: List[Dict],
        edges: List[Dict],
        statebag: Dict[str, Any],
        context: Optional[Dict]
    ) -> str:
        """LLM 분석을 위한 프롬프트 구성"""
        
        # 민감 정보 마스킹
        safe_statebag = self._mask_sensitive_data(statebag)
        
        nodes_desc = json.dumps(nodes, indent=2, ensure_ascii=False)
        edges_desc = json.dumps(edges, indent=2, ensure_ascii=False)
        statebag_desc = json.dumps(safe_statebag, indent=2, ensure_ascii=False)
        
        return f"""Analyze this workflow and predict its execution:

## Workflow Nodes
{nodes_desc}

## Workflow Edges (Execution Flow)
{edges_desc}

## Initial State (Input Data)
{statebag_desc}

## Analysis Required (respond in JSON):
{{
  "execution_order": ["node_id1", "node_id2", ...],
  "steps": [
    {{
      "node_id": "string",
      "node_name": "string",
      "node_type": "llm|hitp|api_call|email|condition|transform|aggregator",
      "action_description": "What this node will do in plain language",
      "estimated_duration_seconds": number,
      "risk_level": "low|medium|high",
      "risk_description": "Why this risk level (if medium/high)",
      "expected_input": "Summary of input data",
      "expected_output": "Summary of expected output",
      "external_systems": ["system1", "system2"],
      "has_side_effect": boolean,
      "is_conditional": boolean,
      "condition_description": "Condition description if conditional"
    }}
  ],
  "draft_results": [
    {{
      "result_type": "email|document|data|notification|api_call",
      "title": "Title of the result",
      "content_preview": "Preview of the actual content that will be generated",
      "recipients": ["email1@example.com"],
      "warnings": ["Warning message if any"],
      "requires_review": boolean
    }}
  ],
  "overall_risk": "low|medium|high",
  "warnings": ["Warning message 1", ...],
  "requires_confirmation": boolean,
  "confirmation_message": "Message if confirmation required",
  "summary": "1-2 sentence summary of what this workflow will do",
  "confidence": 0.0-1.0
}}"""

    def _generate_rule_based(
        self,
        workflow_id: str,
        workflow_name: str,
        nodes: List[Dict],
        edges: List[Dict],
        statebag: Dict[str, Any]
    ) -> PlanBriefing:
        """규칙 기반 브리핑 생성 (LLM 없이)"""
        
        # 실행 순서 결정 (토폴로지 정렬)
        execution_order = self._determine_execution_order(nodes, edges)
        
        # 각 단계 생성
        steps = []
        total_duration = 0
        max_risk = RiskLevel.LOW
        warnings = []
        has_confirmation_required = False
        
        for i, node_id in enumerate(execution_order):
            node = next((n for n in nodes if n.get('id') == node_id), None)
            if not node:
                continue
            
            node_type = node.get('type', 'default')
            node_name = node.get('data', {}).get('label', node.get('label', node_id))
            
            duration = self.DEFAULT_DURATIONS.get(node_type, self.DEFAULT_DURATIONS['default'])
            has_side_effect = node_type in self.SIDE_EFFECT_TYPES
            
            # 위험 수준 결정
            risk_level = RiskLevel.LOW
            risk_description = None
            if has_side_effect:
                risk_level = RiskLevel.MEDIUM
                risk_description = f"{node_type} 노드가 외부 시스템에 영향을 미칩니다"
                if node_type in {"payment", "email"}:
                    risk_level = RiskLevel.HIGH
                    has_confirmation_required = True
            
            # 최고 위험 수준 추적
            if risk_level == RiskLevel.HIGH:
                max_risk = RiskLevel.HIGH
            elif risk_level == RiskLevel.MEDIUM and max_risk != RiskLevel.HIGH:
                max_risk = RiskLevel.MEDIUM
            
            step = PlanStep(
                step_number=i + 1,
                node_id=node_id,
                node_name=node_name,
                node_type=node_type,
                action_description=self._generate_action_description(node),
                estimated_duration_seconds=duration,
                risk_level=risk_level,
                risk_description=risk_description,
                has_external_side_effect=has_side_effect,
                external_systems=[node_type] if has_side_effect else []
            )
            
            steps.append(step)
            total_duration += duration
        
        # 경고 생성
        if max_risk == RiskLevel.HIGH:
            warnings.append("⚠️ 이 워크플로우는 되돌릴 수 없는 작업을 포함합니다")
        
        # 요약 생성
        summary = self._generate_summary(workflow_name, len(steps), total_duration, max_risk)
        
        return PlanBriefing(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            summary=summary,
            total_steps=len(steps),
            estimated_total_duration_seconds=total_duration,
            steps=steps,
            draft_results=[],  # 규칙 기반에서는 결과물 예측 제한
            overall_risk_level=max_risk,
            warnings=warnings,
            requires_confirmation=has_confirmation_required,
            confirmation_message="외부에 영향을 미치는 작업이 포함되어 있습니다. 계속하시겠습니까?" if has_confirmation_required else None,
            confidence_score=0.6  # 규칙 기반은 낮은 신뢰도
        )

    def _determine_execution_order(
        self,
        nodes: List[Dict],
        edges: List[Dict]
    ) -> List[str]:
        """토폴로지 정렬로 실행 순서 결정"""
        from collections import defaultdict, deque
        
        node_ids = [n.get('id') for n in nodes if n.get('id')]
        
        # 진입 차수 계산
        in_degree = defaultdict(int)
        adjacency = defaultdict(list)
        
        for node_id in node_ids:
            in_degree[node_id] = 0
        
        for edge in edges:
            source = edge.get('source')
            target = edge.get('target')
            if source and target:
                adjacency[source].append(target)
                in_degree[target] += 1
        
        # 진입 차수가 0인 노드부터 시작
        queue = deque([n for n in node_ids if in_degree[n] == 0])
        order = []
        
        while queue:
            node = queue.popleft()
            order.append(node)
            
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        return order

    def _generate_action_description(self, node: Dict) -> str:
        """노드에 대한 동작 설명 생성"""
        node_type = node.get('type', 'default')
        node_data = node.get('data', {})
        label = node_data.get('label', node.get('label', ''))
        
        descriptions = {
            "llm": f"AI가 '{label}'을(를) 처리합니다",
            "hitp": f"사용자 입력을 기다립니다: {label}",
            "api_call": f"외부 API를 호출합니다: {label}",
            "email": f"이메일을 발송합니다: {label}",
            "condition": f"조건을 확인합니다: {label}",
            "transform": f"데이터를 변환합니다: {label}",
            "aggregator": f"결과를 집계합니다: {label}",
        }
        
        return descriptions.get(node_type, f"{label} 노드를 실행합니다")

    def _generate_summary(
        self,
        workflow_name: str,
        step_count: int,
        total_duration: int,
        risk_level: RiskLevel
    ) -> str:
        """브리핑 요약 생성"""
        risk_text = {
            RiskLevel.LOW: "",
            RiskLevel.MEDIUM: " 일부 외부 연동이 포함됩니다.",
            RiskLevel.HIGH: " ⚠️ 되돌릴 수 없는 작업이 포함됩니다."
        }
        
        return f"'{workflow_name}' 워크플로우는 {step_count}단계로 구성되며, 약 {total_duration}초가 소요됩니다.{risk_text[risk_level]}"

    def _build_briefing_from_analysis(
        self,
        workflow_config: Dict[str, Any],
        analysis: Dict[str, Any]
    ) -> PlanBriefing:
        """LLM 분석 결과를 PlanBriefing 모델로 변환"""
        
        steps = [
            PlanStep(
                step_number=i + 1,
                node_id=step['node_id'],
                node_name=step['node_name'],
                node_type=step.get('node_type', 'generic'),
                action_description=step['action_description'],
                estimated_duration_seconds=step['estimated_duration_seconds'],
                risk_level=RiskLevel(step['risk_level']),
                risk_description=step.get('risk_description'),
                expected_input_summary=step.get('expected_input'),
                expected_output_summary=step.get('expected_output'),
                has_external_side_effect=step.get('has_side_effect', False),
                external_systems=step.get('external_systems', []),
                is_conditional=step.get('is_conditional', False),
                condition_description=step.get('condition_description')
            )
            for i, step in enumerate(analysis.get('steps', []))
        ]
        
        draft_results = [
            DraftResult(
                result_type=dr['result_type'],
                title=dr['title'],
                content_preview=dr['content_preview'],
                recipients=dr.get('recipients'),
                warnings=dr.get('warnings', []),
                requires_review=dr.get('requires_review', False)
            )
            for dr in analysis.get('draft_results', [])
        ]
        
        total_duration = sum(s.estimated_duration_seconds for s in steps)
        
        return PlanBriefing(
            workflow_id=workflow_config.get('id', 'unknown'),
            workflow_name=workflow_config.get('name', 'Unnamed Workflow'),
            summary=analysis.get('summary', ''),
            total_steps=len(steps),
            estimated_total_duration_seconds=total_duration,
            steps=steps,
            draft_results=draft_results,
            overall_risk_level=RiskLevel(analysis.get('overall_risk', 'low')),
            warnings=analysis.get('warnings', []),
            requires_confirmation=analysis.get('requires_confirmation', False),
            confirmation_message=analysis.get('confirmation_message'),
            confidence_score=analysis.get('confidence', 0.8)
        )

    def _mask_sensitive_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """민감 정보 마스킹"""
        sensitive_keys = {'password', 'secret', 'api_key', 'token', 'credential', 'ssn', 'credit_card'}
        
        def mask_recursive(obj):
            if isinstance(obj, dict):
                return {
                    k: '***MASKED***' if any(s in k.lower() for s in sensitive_keys) else mask_recursive(v)
                    for k, v in obj.items()
                }
            elif isinstance(obj, list):
                return [mask_recursive(item) for item in obj]
            return obj
        
        return mask_recursive(data)

    async def validate_confirmation_token(
        self,
        token: str,
        workflow_id: str,
        user_id: str
    ) -> bool:
        """
        실행 승인 토큰 검증
        
        Redis/DynamoDB에서 토큰을 조회하여 검증합니다.
        """
        try:
            # 토큰 저장소 연동 구현
            token_service = ConfirmationTokenService()
            is_valid = await token_service.validate_token(
                token=token,
                workflow_id=workflow_id,
                user_id=user_id
            )
            
            if is_valid:
                # 토큰 사용 후 무효화 (일회성)
                await token_service.invalidate_token(token)
                logger.info(f"Confirmation token validated and invalidated for workflow {workflow_id}")
                return True
            else:
                logger.warning(f"Invalid confirmation token for workflow {workflow_id} by user {user_id}")
                return False
                
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            return False

    async def generate_confirmation_token(
        self,
        workflow_id: str,
        user_id: str,
        expires_in_minutes: int = 30
    ) -> str:
        """
        실행 승인 토큰 생성
        
        Args:
            workflow_id: 워크플로우 ID
            user_id: 사용자 ID
            expires_in_minutes: 토큰 만료 시간 (분)
            
        Returns:
            생성된 토큰
        """
        try:
            token_service = ConfirmationTokenService()
            token = await token_service.generate_token(
                workflow_id=workflow_id,
                user_id=user_id,
                expires_in_minutes=expires_in_minutes
            )
            
            logger.info(f"Confirmation token generated for workflow {workflow_id}")
            return token
            
        except Exception as e:
            logger.error(f"Token generation error: {e}")
            raise


class ConfirmationTokenService:
    """
    실행 승인 토큰 관리 서비스
    
    Redis 또는 DynamoDB를 사용하여 토큰을 저장하고 검증합니다.
    """
    
    def __init__(self):
        self.use_redis = os.environ.get('REDIS_URL') is not None
        self.token_table = os.environ.get('CONFIRMATION_TOKENS_TABLE', 'ConfirmationTokens')
        self._redis_client = None
        self._dynamodb_table = None
    
    @property
    def redis_client(self):
        """Redis 클라이언트 지연 초기화"""
        if self._redis_client is None and self.use_redis:
            try:
                import redis.asyncio as redis
                redis_url = os.environ.get('REDIS_URL')
                self._redis_client = redis.from_url(redis_url)
            except ImportError:
                logger.warning("Redis not available, falling back to DynamoDB")
                self.use_redis = False
        return self._redis_client
    
    @property
    def dynamodb_table(self):
        """DynamoDB 테이블 지연 초기화"""
        if self._dynamodb_table is None and not self.use_redis:
            try:
                import boto3
                dynamodb = boto3.resource('dynamodb')
                self._dynamodb_table = dynamodb.Table(self.token_table)
            except Exception as e:
                logger.error(f"DynamoDB initialization failed: {e}")
        return self._dynamodb_table
    
    async def generate_token(
        self,
        workflow_id: str,
        user_id: str,
        expires_in_minutes: int = 30
    ) -> str:
        """토큰 생성 및 저장"""
        import secrets
        import time
        
        # 안전한 랜덤 토큰 생성
        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + (expires_in_minutes * 60)
        
        token_data = {
            'workflow_id': workflow_id,
            'user_id': user_id,
            'created_at': int(time.time()),
            'expires_at': expires_at,
            'is_used': False
        }
        
        if self.use_redis and self.redis_client:
            # Redis에 저장 (TTL 자동 만료)
            await self.redis_client.hset(
                f"confirmation_token:{token}",
                mapping=token_data
            )
            await self.redis_client.expire(
                f"confirmation_token:{token}",
                expires_in_minutes * 60
            )
        else:
            # DynamoDB에 저장
            if self.dynamodb_table:
                self.dynamodb_table.put_item(
                    Item={
                        'token': token,
                        'workflow_id': workflow_id,
                        'user_id': user_id,
                        'created_at': token_data['created_at'],
                        'expires_at': expires_at,
                        'is_used': False,
                        'ttl': expires_at  # DynamoDB TTL
                    }
                )
        
        return token
    
    async def validate_token(
        self,
        token: str,
        workflow_id: str,
        user_id: str
    ) -> bool:
        """토큰 검증"""
        import time
        
        try:
            if self.use_redis and self.redis_client:
                # Redis에서 조회
                token_data = await self.redis_client.hgetall(f"confirmation_token:{token}")
                if not token_data:
                    return False
                
                # 바이트를 문자열로 변환 (Redis 특성)
                token_data = {k.decode(): v.decode() for k, v in token_data.items()}
                
            else:
                # DynamoDB에서 조회
                if not self.dynamodb_table:
                    return False
                
                response = self.dynamodb_table.get_item(Key={'token': token})
                token_data = response.get('Item')
                if not token_data:
                    return False
            
            # 토큰 검증
            current_time = int(time.time())
            
            return (
                token_data.get('workflow_id') == workflow_id and
                token_data.get('user_id') == user_id and
                int(token_data.get('expires_at', 0)) > current_time and
                not token_data.get('is_used', False)
            )
            
        except Exception as e:
            logger.error(f"Token validation error: {e}")
            return False
    
    async def invalidate_token(self, token: str) -> bool:
        """토큰 무효화 (사용 후)"""
        try:
            if self.use_redis and self.redis_client:
                # Redis에서 삭제
                await self.redis_client.delete(f"confirmation_token:{token}")
            else:
                # DynamoDB에서 is_used 플래그 설정
                if self.dynamodb_table:
                    self.dynamodb_table.update_item(
                        Key={'token': token},
                        UpdateExpression='SET is_used = :used',
                        ExpressionAttributeValues={':used': True}
                    )
            
            return True
            
        except Exception as e:
            logger.error(f"Token invalidation error: {e}")
            return False
