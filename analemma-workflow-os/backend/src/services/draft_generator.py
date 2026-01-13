# -*- coding: utf-8 -*-
"""
Draft Result Generator Service

실제 실행 없이 예상 결과물의 상세 초안을 생성하는 서비스입니다.
사용자가 "자세히 보기"를 클릭했을 때 호출됩니다.
"""

import json
import os
import re
import logging
from typing import Dict, Any, List, Optional

try:
    from src.models.plan_briefing import DraftResult
except ImportError:
    from src.models.plan_briefing import DraftResult

logger = logging.getLogger(__name__)

# LLM 클라이언트
try:
    from openai import AsyncOpenAI
    HAS_OPENAI = True
except ImportError:
    try:
        import openai
        HAS_OPENAI = True
    except ImportError:
        HAS_OPENAI = False


class DraftResultGenerator:
    """
    실제 실행 없이 예상 결과물의 상세 초안 생성
    
    노드 타입별로 최적화된 초안 생성 로직을 제공합니다.
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

    async def generate_detailed_draft(
        self,
        node_config: Dict[str, Any],
        input_data: Dict[str, Any],
        output_type: str
    ) -> Dict[str, Any]:
        """
        특정 노드의 상세 출력 초안 생성
        
        Args:
            node_config: 노드 설정
            input_data: 입력 데이터
            output_type: 출력 타입 (email, document, slack_message, api_call 등)
            
        Returns:
            생성된 초안 및 경고 정보
        """
        generators = {
            "email": self._generate_email_draft,
            "document": self._generate_document_draft,
            "slack_message": self._generate_slack_draft,
            "notification": self._generate_notification_draft,
            "api_call": self._generate_api_draft,
            "sms": self._generate_sms_draft,
        }
        
        generator = generators.get(output_type, self._generate_generic_draft)
        return await generator(node_config, input_data)

    async def _generate_email_draft(
        self,
        node_config: Dict[str, Any],
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """이메일 초안 생성"""
        
        template = node_config.get('template', '')
        variables = input_data
        
        # LLM 사용 가능한 경우
        if HAS_OPENAI and self.api_key:
            try:
                if hasattr(self, 'client') and self.client:
                    # AsyncOpenAI 클라이언트 사용 (v1.0+)
                    response = await self.client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{
                            "role": "system",
                            "content": """Generate a realistic email draft based on the template and variables. 
Output JSON with: subject, body, recipients (array).
The email should be professional and complete.
Respond in the same language as the template."""
                        }, {
                            "role": "user",
                            "content": f"Template: {template}\nVariables: {json.dumps(variables, ensure_ascii=False)}"
                        }],
                        response_format={"type": "json_object"},
                        max_tokens=1000,
                        temperature=0.5
                    )
                else:
                    # 구버전 호환성 유지
                    response = await openai.ChatCompletion.acreate(
                        model="gpt-4o-mini",
                        messages=[{
                            "role": "system",
                            "content": """Generate a realistic email draft based on the template and variables. 
Output JSON with: subject, body, recipients (array).
The email should be professional and complete.
Respond in the same language as the template."""
                        }, {
                            "role": "user",
                            "content": f"Template: {template}\nVariables: {json.dumps(variables, ensure_ascii=False)}"
                        }],
                        response_format={"type": "json_object"},
                        max_tokens=1000,
                        temperature=0.5
                    )
                
                draft = json.loads(response.choices[0].message.content)
            except Exception as e:
                logger.warning(f"LLM email draft generation failed: {e}")
                draft = self._generate_email_fallback(node_config, input_data)
        else:
            draft = self._generate_email_fallback(node_config, input_data)
        
        # 경고 체크
        warnings = self._check_email_warnings(draft)
        
        return {
            "type": "email",
            "draft": {
                "to": draft.get('recipients', []),
                "subject": draft.get('subject', ''),
                "body": draft.get('body', ''),
                "is_preview": True
            },
            "warnings": warnings,
            "can_edit": True
        }

    def _generate_email_fallback(
        self,
        node_config: Dict[str, Any],
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """이메일 초안 폴백 (LLM 없이)"""
        template = node_config.get('template', '')
        
        # 간단한 변수 치환
        body = template
        for key, value in input_data.items():
            body = body.replace(f'{{{{{key}}}}}', str(value))
            body = body.replace(f'${{{key}}}', str(value))
        
        recipients = node_config.get('recipients', [])
        if isinstance(recipients, str):
            recipients = [r.strip() for r in recipients.split(',')]
        
        # 입력 데이터에서 수신자 찾기
        if not recipients:
            for key in ['email', 'to', 'recipient', 'customer_email']:
                if key in input_data:
                    recipients = [input_data[key]]
                    break
        
        return {
            "recipients": recipients,
            "subject": node_config.get('subject', f"[Preview] {node_config.get('label', 'Email')}"),
            "body": body or f"[Preview content based on template]\n\nInput data:\n{json.dumps(input_data, indent=2, ensure_ascii=False)}"
        }

    def _check_email_warnings(self, draft: Dict) -> List[str]:
        """이메일 초안의 잠재적 문제점 체크"""
        warnings = []
        
        body = draft.get('body', '')
        subject = draft.get('subject', '')
        recipients = draft.get('recipients', [])
        
        # 본문 길이 체크
        if len(body) > 5000:
            warnings.append("⚠️ 이메일 본문이 매우 깁니다 (5000자 초과)")
        
        # 수신자 수 체크
        if len(recipients) > 10:
            warnings.append(f"📧 다수의 수신자에게 발송됩니다 ({len(recipients)}명)")
        
        # 민감 정보 패턴 체크
        patterns = [
            (r'\b\d{3}-\d{2}-\d{4}\b', "SSN 패턴"),
            (r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b', "신용카드 번호 패턴"),
            (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', None),  # 이메일은 경고 안 함
        ]
        
        for pattern, desc in patterns:
            if desc and re.search(pattern, body):
                warnings.append(f"⚠️ 민감한 개인정보({desc})가 포함되어 있을 수 있습니다")
        
        # 제목 없음 체크
        if not subject.strip():
            warnings.append("📝 이메일 제목이 비어 있습니다")
        
        # 빈 본문 체크
        if not body.strip():
            warnings.append("📝 이메일 본문이 비어 있습니다")
        
        return warnings

    async def _generate_document_draft(
        self,
        node_config: Dict[str, Any],
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """문서 초안 생성"""
        
        template = node_config.get('template', '')
        doc_type = node_config.get('document_type', 'general')
        
        if HAS_OPENAI and self.api_key:
            try:
                if hasattr(self, 'client') and self.client:
                    # AsyncOpenAI 클라이언트 사용 (v1.0+)
                    response = await self.client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{
                            "role": "system",
                            "content": f"""Generate a {doc_type} document draft based on the template and data.
Output JSON with: title, content, sections (array of {{heading, body}}).
Make it realistic and complete."""
                        }, {
                            "role": "user",
                            "content": f"Template: {template}\nData: {json.dumps(input_data, ensure_ascii=False)}"
                        }],
                        response_format={"type": "json_object"},
                        max_tokens=1500
                    )
                else:
                    # 구버전 호환성 유지
                    response = await openai.ChatCompletion.acreate(
                        model="gpt-4o-mini",
                        messages=[{
                            "role": "system",
                            "content": f"""Generate a {doc_type} document draft based on the template and data.
Output JSON with: title, content, sections (array of {{heading, body}}).
Make it realistic and complete."""
                        }, {
                            "role": "user",
                            "content": f"Template: {template}\nData: {json.dumps(input_data, ensure_ascii=False)}"
                        }],
                        response_format={"type": "json_object"},
                        max_tokens=1500
                    )
                
                draft = json.loads(response.choices[0].message.content)
            except Exception as e:
                logger.warning(f"LLM document draft generation failed: {e}")
                draft = {
                    "title": node_config.get('label', 'Document'),
                    "content": f"[Preview document content]\n\n{template}",
                    "sections": []
                }
        else:
            draft = {
                "title": node_config.get('label', 'Document'),
                "content": f"[Preview document content]\n\n{template}",
                "sections": []
            }
        
        return {
            "type": "document",
            "draft": draft,
            "warnings": [],
            "can_edit": True
        }

    async def _generate_slack_draft(
        self,
        node_config: Dict[str, Any],
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Slack 메시지 초안 생성"""
        
        template = node_config.get('template', node_config.get('message', ''))
        channel = node_config.get('channel', '#general')
        
        # 변수 치환
        message = template
        for key, value in input_data.items():
            message = message.replace(f'{{{{{key}}}}}', str(value))
            message = message.replace(f'${{{key}}}', str(value))
        
        warnings = []
        if '@channel' in message or '@here' in message:
            warnings.append("📢 채널 전체 알림이 포함되어 있습니다")
        
        return {
            "type": "slack_message",
            "draft": {
                "channel": channel,
                "message": message or f"[Preview Slack message]\n\nData: {json.dumps(input_data, ensure_ascii=False)[:500]}",
                "is_preview": True
            },
            "warnings": warnings,
            "can_edit": True
        }

    async def _generate_notification_draft(
        self,
        node_config: Dict[str, Any],
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """알림 초안 생성"""
        
        title = node_config.get('title', 'Notification')
        body_template = node_config.get('body', node_config.get('message', ''))
        
        # 변수 치환
        body = body_template
        for key, value in input_data.items():
            body = body.replace(f'{{{{{key}}}}}', str(value))
            body = body.replace(f'${{{key}}}', str(value))
        
        return {
            "type": "notification",
            "draft": {
                "title": title,
                "body": body or "[Preview notification content]",
                "is_preview": True
            },
            "warnings": [],
            "can_edit": True
        }

    async def _generate_api_draft(
        self,
        node_config: Dict[str, Any],
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """API 호출 초안 생성"""
        
        method = node_config.get('method', 'POST')
        url = node_config.get('url', node_config.get('endpoint', ''))
        headers = node_config.get('headers', {})
        body_template = node_config.get('body', {})
        
        # 변수 치환
        if isinstance(body_template, str):
            body = body_template
            for key, value in input_data.items():
                body = body.replace(f'{{{{{key}}}}}', str(value))
        else:
            body = json.dumps(body_template, indent=2, ensure_ascii=False)
        
        warnings = []
        
        # 프로덕션 URL 경고
        if 'prod' in url.lower() or 'production' in url.lower():
            warnings.append("⚠️ 프로덕션 환경 API를 호출합니다")
        
        # 결제 관련 경고
        if any(kw in url.lower() for kw in ['payment', 'charge', 'billing', 'invoice']):
            warnings.append("💳 결제 관련 API입니다. 실제 청구가 발생할 수 있습니다")
        
        return {
            "type": "api_call",
            "draft": {
                "method": method,
                "url": url,
                "headers": {k: v if 'key' not in k.lower() and 'secret' not in k.lower() else '***' 
                          for k, v in headers.items()},
                "body": body,
                "is_preview": True
            },
            "warnings": warnings,
            "can_edit": False  # API 호출은 직접 수정 불가
        }

    async def _generate_sms_draft(
        self,
        node_config: Dict[str, Any],
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """SMS 초안 생성"""
        
        template = node_config.get('template', node_config.get('message', ''))
        phone = node_config.get('phone', input_data.get('phone', input_data.get('phone_number', '')))
        
        # 변수 치환
        message = template
        for key, value in input_data.items():
            message = message.replace(f'{{{{{key}}}}}', str(value))
            message = message.replace(f'${{{key}}}', str(value))
        
        warnings = []
        if len(message) > 160:
            warnings.append(f"📱 메시지가 160자를 초과합니다 ({len(message)}자). 여러 SMS로 분할될 수 있습니다")
        
        return {
            "type": "sms",
            "draft": {
                "to": phone,
                "message": message or "[Preview SMS content]",
                "character_count": len(message),
                "is_preview": True
            },
            "warnings": warnings,
            "can_edit": True
        }

    async def _generate_generic_draft(
        self,
        node_config: Dict[str, Any],
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """일반 노드 출력 초안 생성"""
        
        return {
            "type": "generic",
            "draft": {
                "node_label": node_config.get('label', node_config.get('id', 'Unknown')),
                "description": f"이 노드는 입력 데이터를 처리하고 결과를 반환합니다.",
                "input_preview": json.dumps(input_data, indent=2, ensure_ascii=False)[:500],
                "is_preview": True
            },
            "warnings": [],
            "can_edit": False
        }

    async def generate_multiple_drafts(
        self,
        nodes: List[Dict[str, Any]],
        statebag: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        여러 노드의 초안을 한 번에 생성
        
        Args:
            nodes: 노드 설정 목록
            statebag: 현재 상태 데이터
            
        Returns:
            각 노드의 초안 목록
        """
        drafts = []
        
        for node in nodes:
            node_id = node.get('id')
            node_type = node.get('type', 'generic')
            node_config = node.get('data', node)
            
            # 노드 타입을 출력 타입으로 매핑
            output_type_map = {
                'email': 'email',
                'sendEmail': 'email',
                'slack': 'slack_message',
                'notification': 'notification',
                'apiCall': 'api_call',
                'http': 'api_call',
                'sms': 'sms',
                'document': 'document',
            }
            output_type = output_type_map.get(node_type, 'generic')
            
            try:
                draft = await self.generate_detailed_draft(
                    node_config=node_config,
                    input_data=statebag,
                    output_type=output_type
                )
                draft['node_id'] = node_id
                drafts.append(draft)
            except Exception as e:
                logger.error(f"Failed to generate draft for node {node_id}: {e}")
                drafts.append({
                    "node_id": node_id,
                    "type": "error",
                    "error": str(e)
                })
        
        return drafts
