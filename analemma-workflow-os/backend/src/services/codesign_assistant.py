"""
Co-design Assistant: 양방향 협업 워크플로우 설계 엔진

기존 agentic_designer.py를 확장하여 인간-AI 협업을 지원합니다.
- NL → JSON: 자연어를 워크플로우로 변환
- JSON → NL: 워크플로우를 자연어로 설명
- 실시간 제안(Suggestion) 생성
- 검증(Audit) 결과 통합
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Generator, Iterator, List, Optional

# 기존 agentic_designer 모듈에서 필요한 함수 import
from .agentic_designer import (
    invoke_bedrock_model,
    invoke_bedrock_model_stream,
    invoke_claude,
    MODEL_HAIKU,
    MODEL_SONNET,
    _broadcast_to_connections,
    _is_mock_mode,
)
from .graph_dsl import validate_workflow, normalize_workflow
from .logical_auditor import audit_workflow, LogicalAuditor

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


# ──────────────────────────────────────────────────────────────
# 시스템 프롬프트 정의
# ──────────────────────────────────────────────────────────────

NODE_TYPE_SPECS = """
[사용 가능한 노드 타입]
1. "operator": Python 코드 실행 노드
   - config.code: 실행할 Python 코드 (문자열)
   - config.sets: 간단한 키-값 설정 (객체, 선택사항)

2. "llm_chat": LLM 채팅 노드
   - config.prompt_content: 프롬프트 템플릿 (문자열, 필수)
   - config.model: 모델 ID (문자열, 선택사항)
   - config.max_tokens: 최대 토큰 수 (숫자, 선택사항)
   - config.temperature: 온도 설정 (숫자, 선택사항)
   - config.system_prompt: 시스템 프롬프트 (문자열, 선택사항)

3. "api_call": HTTP API 호출 노드
   - config.url: API 엔드포인트 URL (문자열, 필수)
   - config.method: HTTP 메소드 (문자열, 기본값: "GET")
   - config.headers: HTTP 헤더 (객체, 선택사항)
   - config.json: JSON 바디 (객체, 선택사항)

4. "db_query": 데이터베이스 쿼리 노드
   - config.query: SQL 쿼리 (문자열, 필수)
   - config.connection_string: DB 연결 문자열 (문자열, 필수)

5. "for_each": 반복 처리 노드
   - config.items: 반복할 아이템 목록 (배열, 필수)
   - config.item_key: 각 아이템을 저장할 상태 키 (문자열, 선택사항)

6. "route_draft_quality": 품질 라우팅 노드
   - config.threshold: 품질 임계값 (숫자, 필수)

7. "group": 서브그래프 노드
   - config.subgraph_id: 서브그래프 ID (문자열)
"""

CODESIGN_SYSTEM_PROMPT = """
당신은 Co-design Assistant입니다. 사용자와 협업하여 워크플로우를 설계합니다.

[역할]
1. 자연어 요청을 워크플로우 JSON으로 변환
2. 사용자의 UI 변경 사항을 이해하고 보완 제안
3. 워크플로우의 논리적 오류 감지 및 수정 제안

[출력 형식]
모든 응답은 JSONL (JSON Lines) 형식입니다. 각 라인은 완전한 JSON 객체여야 합니다.
허용되는 타입:
- 노드 추가: {{"type": "node", "data": {{...}}}}
- 엣지 추가: {{"type": "edge", "data": {{...}}}}
- 제안: {{"type": "suggestion", "data": {{"id": "sug_X", "action": "...", "reason": "...", "affected_nodes": [...], "proposed_change": {{...}}, "confidence": 0.0~1.0}}}}
- 검증 경고: {{"type": "audit", "data": {{"level": "warning|error|info", "message": "...", "affected_nodes": [...]}}}}
- 텍스트 응답: {{"type": "text", "data": "..."}}
- 완료: {{"type": "status", "data": "done"}}

[제안(Suggestion) action 타입]
- "group": 노드 그룹화 제안
- "add_node": 노드 추가 제안
- "modify": 노드 수정 제안
- "delete": 노드 삭제 제안
- "reorder": 노드 순서 변경 제안
- "connect": 엣지 추가 제안
- "optimize": 성능 최적화 제안

{node_type_specs}

[레이아웃 규칙]
- X좌표: 150 고정
- Y좌표: 첫 노드 50, 이후 100씩 증가

[컨텍스트]
현재 워크플로우:
{current_workflow}

사용자 최근 변경:
{user_changes}

[중요]
- 인간용 설명 텍스트 없이 오직 JSONL만 출력
- 각 라인은 완전한 JSON 객체여야 함
- 완료 시 반드시 {{"type": "status", "data": "done"}} 출력
"""

EXPLAIN_SYSTEM_PROMPT = """
당신은 워크플로우 분석 전문가입니다. 주어진 워크플로우 JSON을 분석하고 자연어로 설명합니다.

[출력 형식]
반드시 아래 JSON 형식으로만 응답하세요:
{{
    "summary": "워크플로우 전체 요약 (1-2문장)",
    "steps": [
        {{"node_id": "...", "description": "노드 설명", "role": "시작|처리|분기|종료"}},
        ...
    ],
    "data_flow": "데이터가 어떻게 흐르는지 설명",
    "issues": ["잠재적 문제점 목록"],
    "suggestions": ["최적화 제안 목록"]
}}
"""

SUGGESTION_SYSTEM_PROMPT = """
당신은 워크플로우 최적화 전문가입니다. 현재 워크플로우를 분석하고 개선 제안을 생성합니다.

[분석 관점]
1. 중복 제거: 유사한 기능을 하는 노드들을 그룹화할 수 있는지
2. 효율성: 불필요한 노드나 연결이 있는지
3. 가독성: 노드 배치가 논리적 흐름을 잘 표현하는지
4. 모범 사례: 일반적인 워크플로우 패턴을 따르는지

[출력 형식]
JSONL 형식으로 각 제안을 한 줄씩 출력:
{{"type": "suggestion", "data": {{"id": "sug_1", "action": "group", "reason": "이 3개 노드는 '데이터 전처리' 기능을 수행하므로 그룹화하면 좋습니다", "affected_nodes": ["node1", "node2", "node3"], "proposed_change": {{}}, "confidence": 0.85}}}}
{{"type": "suggestion", "data": {{"id": "sug_2", "action": "add_node", "reason": "에러 핸들링 노드를 추가하면 안정성이 향상됩니다", "affected_nodes": ["api_call_1"], "proposed_change": {{"new_node": {{}}}}, "confidence": 0.7}}}}
{{"type": "status", "data": "done"}}
"""


# ──────────────────────────────────────────────────────────────
# 컨텍스트 관리
# ──────────────────────────────────────────────────────────────

class CodesignContext:
    """세션별 컨텍스트 관리"""
    
    def __init__(self, session_id: str = None):
        self.session_id = session_id or str(uuid.uuid4())
        self.current_workflow: Dict[str, Any] = {"nodes": [], "edges": []}
        self.change_history: List[Dict[str, Any]] = []
        self.pending_suggestions: Dict[str, Dict[str, Any]] = {}
        self.conversation_history: List[Dict[str, str]] = []
    
    def update_workflow(self, workflow: Dict[str, Any]):
        """현재 워크플로우 업데이트"""
        self.current_workflow = workflow
    
    def record_user_change(self, change_type: str, data: Dict[str, Any]):
        """
        사용자 UI 변경 기록
        
        Args:
            change_type: "add_node", "move_node", "delete_node", 
                        "update_node", "add_edge", "delete_edge", 
                        "group_nodes", "ungroup_nodes"
            data: 변경 세부 정보
        """
        self.change_history.append({
            "timestamp": time.time(),
            "type": change_type,
            "data": data
        })
        # 최근 20개만 유지
        self.change_history = self.change_history[-20:]
    
    def get_recent_changes_summary(self) -> str:
        """최근 변경 요약 (LLM 컨텍스트용)"""
        if not self.change_history:
            return "없음"
        
        recent = self.change_history[-5:]
        summaries = []
        
        change_descriptions = {
            "add_node": lambda d: f"노드 '{d.get('id', '?')}' ({d.get('type', '?')}) 추가",
            "move_node": lambda d: f"노드 '{d.get('id', '?')}' 위치 변경",
            "delete_node": lambda d: f"노드 '{d.get('id', '?')}' 삭제",
            "update_node": lambda d: f"노드 '{d.get('id', '?')}' 설정 변경",
            "add_edge": lambda d: f"'{d.get('source', '?')}'→'{d.get('target', '?')}' 연결 추가",
            "delete_edge": lambda d: f"엣지 '{d.get('id', '?')}' 삭제",
            "group_nodes": lambda d: f"노드 {d.get('node_ids', [])} 그룹화",
            "ungroup_nodes": lambda d: f"그룹 '{d.get('group_id', '?')}' 해제"
        }
        
        for ch in recent:
            change_type = ch.get("type", "unknown")
            data = ch.get("data", {})
            
            if change_type in change_descriptions:
                summaries.append(change_descriptions[change_type](data))
            else:
                summaries.append(f"{change_type}: {data.get('id', '?')}")
        
        return ", ".join(summaries)
    
    def add_suggestion(self, suggestion: Dict[str, Any]):
        """제안 추가"""
        suggestion_id = suggestion.get("id", str(uuid.uuid4()))
        self.pending_suggestions[suggestion_id] = {
            **suggestion,
            "status": "pending",
            "created_at": time.time()
        }
    
    def resolve_suggestion(self, suggestion_id: str, accepted: bool):
        """제안 해결"""
        if suggestion_id in self.pending_suggestions:
            self.pending_suggestions[suggestion_id]["status"] = "accepted" if accepted else "rejected"
            self.pending_suggestions[suggestion_id]["resolved_at"] = time.time()
    
    def add_message(self, role: str, content: str):
        """대화 기록 추가"""
        self.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": time.time()
        })
        # 최근 10개만 유지
        self.conversation_history = self.conversation_history[-10:]


# 세션 스토어 (인메모리, 프로덕션에서는 Redis/DynamoDB 사용)
_session_contexts: Dict[str, CodesignContext] = {}


def get_or_create_context(session_id: str = None) -> CodesignContext:
    """세션 컨텍스트 가져오기 또는 생성"""
    if not session_id:
        return CodesignContext()
    
    if session_id not in _session_contexts:
        _session_contexts[session_id] = CodesignContext(session_id)
    
    return _session_contexts[session_id]


# ──────────────────────────────────────────────────────────────
# 핵심 기능: 스트리밍 응답 생성
# ──────────────────────────────────────────────────────────────

def stream_codesign_response(
    user_request: str,
    current_workflow: Dict[str, Any],
    recent_changes: List[Dict[str, Any]] = None,
    session_id: str = None,
    connection_ids: List[str] = None
) -> Generator[str, None, None]:
    """
    Co-design 스트리밍 응답 생성
    
    Args:
        user_request: 사용자 요청
        current_workflow: 현재 워크플로우 JSON
        recent_changes: 최근 사용자 변경 목록
        session_id: 세션 ID
        connection_ids: WebSocket 연결 ID 목록
        
    Yields:
        JSONL 형식의 응답 청크
    """
    context = get_or_create_context(session_id)
    context.update_workflow(current_workflow)
    
    # 변경 이력 기록
    for change in (recent_changes or []):
        context.record_user_change(
            change.get("type", "unknown"),
            change.get("data", {})
        )
    
    context.add_message("user", user_request)
    
    # 워크플로우 요약 (토큰 절약을 위해 축약)
    workflow_summary = _summarize_workflow(current_workflow)
    changes_summary = context.get_recent_changes_summary()
    
    # 시스템 프롬프트 구성
    system_prompt = CODESIGN_SYSTEM_PROMPT.format(
        node_type_specs=NODE_TYPE_SPECS,
        current_workflow=workflow_summary,
        user_changes=changes_summary
    )
    
    # Mock 모드 처리
    if _is_mock_mode():
        yield from src._mock_codesign_response(user_request, current_workflow)
        return
    
    # Bedrock 스트리밍 호출
    try:
        for chunk in invoke_bedrock_model_stream(system_prompt, user_request):
            # 각 청크 처리
            chunk = chunk.strip()
            if not chunk:
                continue
            
            try:
                obj = json.loads(chunk)
                
                # 제안인 경우 컨텍스트에 저장
                if obj.get("type") == "suggestion":
                    context.add_suggestion(obj.get("data", {}))
                
                # WebSocket 브로드캐스트
                if connection_ids:
                    _broadcast_to_connections(connection_ids, obj)
                
                yield chunk + "\n"
                
            except json.JSONDecodeError:
                logger.debug(f"Skipping non-JSON chunk: {chunk[:50]}")
                continue
                
    except Exception as e:
        logger.exception(f"Codesign streaming error: {e}")
        error_obj = {"type": "error", "data": str(e)}
        yield json.dumps(error_obj) + "\n"
    
    # 워크플로우 검증 실행
    audit_issues = audit_workflow(current_workflow)
    for issue in audit_issues[:5]:  # 최대 5개 이슈만 전송
        audit_obj = {
            "type": "audit",
            "data": {
                "level": issue.get("level", "info"),
                "message": issue.get("message", ""),
                "affected_nodes": issue.get("affected_nodes", []),
                "suggestion": issue.get("suggestion")
            }
        }
        yield json.dumps(audit_obj) + "\n"
        
        if connection_ids:
            _broadcast_to_connections(connection_ids, audit_obj)
    
    # 완료 신호
    done_obj = {"type": "status", "data": "done"}
    yield json.dumps(done_obj) + "\n"
    
    if connection_ids:
        _broadcast_to_connections(connection_ids, done_obj)


def _sanitize_for_prompt(text: str, max_length: int = 100) -> str:
    """
    프롬프트 인젝션 방어를 위한 텍스트 sanitize
    
    - 제어 문자 제거
    - 길이 제한
    - 잠재적 인젝션 패턴 제거
    """
    if not text:
        return ""
    
    # 문자열이 아닌 경우 변환
    if not isinstance(text, str):
        text = str(text)
    
    # 제어 문자 및 특수 유니코드 제거
    import re
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    
    # 잠재적 프롬프트 인젝션 패턴 제거/무력화
    # (예: "Ignore previous instructions", "System:", "Human:", "Assistant:" 등)
    injection_patterns = [
        r'(?i)ignore\s+(all\s+)?previous\s+instructions?',
        r'(?i)system\s*:',
        r'(?i)human\s*:',
        r'(?i)assistant\s*:',
        r'(?i)^you\s+are\s+now',
        r'(?i)new\s+instructions?\s*:',
    ]
    for pattern in injection_patterns:
        text = re.sub(pattern, '[FILTERED]', text)
    
    # 길이 제한
    if len(text) > max_length:
        text = text[:max_length] + "..."
    
    return text


def _summarize_workflow(workflow: Dict[str, Any], max_nodes: int = 10) -> str:
    """
    워크플로우를 LLM 컨텍스트용으로 요약
    
    노드가 많을 경우 주요 정보만 추출하여 토큰 절약
    프롬프트 인젝션 방어를 위해 노드 라벨을 sanitize
    """
    nodes = workflow.get("nodes", [])
    edges = workflow.get("edges", [])
    
    if len(nodes) <= max_nodes:
        # 노드가 적으면 전체 반환하되, 라벨은 sanitize
        safe_workflow = {
            "nodes": [
                {
                    **{k: v for k, v in n.items() if k not in ("label", "data")},
                    "label": _sanitize_for_prompt(
                        n.get("label") or (n.get("data", {}) or {}).get("label", ""),
                        max_length=50
                    )
                }
                for n in nodes
            ],
            "edges": edges
        }
        return json.dumps(safe_workflow, ensure_ascii=False, indent=2)[:2000]
    
    # 노드 요약 (sanitized)
    summary = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": [
            {
                "id": n.get("id"),
                "type": n.get("type"),
                "label": _sanitize_for_prompt(
                    n.get("label") or (n.get("data", {}) or {}).get("label", ""),
                    max_length=50
                )
            }
            for n in nodes[:max_nodes]
        ],
        "edges": [
            {"source": e.get("source"), "target": e.get("target")}
            for e in edges[:20]
        ]
    }
    
    if len(nodes) > max_nodes:
        summary["truncated"] = True
        summary["remaining_nodes"] = len(nodes) - max_nodes
    
    return json.dumps(summary, ensure_ascii=False)


def _mock_codesign_response(
    user_request: str, 
    current_workflow: Dict[str, Any]
) -> Generator[str, None, None]:
    """Mock 모드에서의 응답 생성"""
    ui_delay = float(os.environ.get("STREAMING_UI_DELAY", "0.1"))
    
    # 기본 응답 노드 생성
    existing_nodes = len(current_workflow.get("nodes", []))
    base_y = 50 + existing_nodes * 100
    
    # 텍스트 응답
    text_obj = {
        "type": "text",
        "data": f"[Mock] 요청을 분석했습니다: {user_request[:50]}..."
    }
    yield json.dumps(text_obj) + "\n"
    time.sleep(ui_delay)
    
    # 새 노드 추가 (mock)
    new_node = {
        "type": "node",
        "data": {
            "id": f"mock_node_{int(time.time())}",
            "type": "llm_chat",
            "position": {"x": 150, "y": base_y},
            "config": {
                "prompt_content": f"Mock prompt for: {user_request[:30]}"
            }
        }
    }
    yield json.dumps(new_node) + "\n"
    time.sleep(ui_delay)
    
    # 제안 생성 (mock)
    if existing_nodes >= 3:
        suggestion = {
            "type": "suggestion",
            "data": {
                "id": f"sug_{int(time.time())}",
                "action": "group",
                "reason": "이 노드들은 유사한 기능을 수행하므로 그룹화를 권장합니다.",
                "affected_nodes": [n.get("id") for n in current_workflow.get("nodes", [])[:3]],
                "proposed_change": {},
                "confidence": 0.75
            }
        }
        yield json.dumps(suggestion) + "\n"
        time.sleep(ui_delay)
    
    # 완료
    yield json.dumps({"type": "status", "data": "done"}) + "\n"


# ──────────────────────────────────────────────────────────────
# JSON → NL: 워크플로우 설명 생성
# ──────────────────────────────────────────────────────────────

def explain_workflow(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """
    워크플로우를 자연어로 설명
    
    Args:
        workflow: 워크플로우 JSON
        
    Returns:
        설명 객체 {"summary": "...", "steps": [...], "issues": [...], ...}
    """
    if _is_mock_mode():
        return _mock_explain_workflow(workflow)
    
    workflow_json = json.dumps(workflow, ensure_ascii=False, indent=2)
    
    prompt = f"""다음 워크플로우를 분석하고 설명하세요:

{workflow_json[:3000]}

반드시 아래 JSON 형식으로만 응답하세요:
{{
    "summary": "워크플로우 전체 요약",
    "steps": [...],
    "data_flow": "데이터 흐름 설명",
    "issues": [...],
    "suggestions": [...]
}}"""
    
    try:
        response = invoke_claude(MODEL_HAIKU, EXPLAIN_SYSTEM_PROMPT, prompt)
        
        # 응답 파싱
        if isinstance(response, dict) and "content" in response:
            blocks = response.get("content", [])
            if blocks:
                text = blocks[0].get("text", "") if isinstance(blocks[0], dict) else str(blocks[0])
                return json.loads(text)
        
        return {"summary": "워크플로우 분석 완료", "steps": [], "issues": [], "suggestions": []}
        
    except Exception as e:
        logger.exception(f"Workflow explanation failed: {e}")
        return {
            "summary": "분석 중 오류 발생",
            "steps": [],
            "issues": [str(e)],
            "suggestions": []
        }


def _mock_explain_workflow(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """Mock 워크플로우 설명"""
    nodes = workflow.get("nodes", [])
    
    steps = []
    for i, node in enumerate(nodes[:10]):
        node_type = node.get("type", "unknown")
        node_id = node.get("id", f"node_{i}")
        label = node.get("label") or (node.get("data", {}) or {}).get("label", node_id)
        
        role = "시작" if i == 0 else ("종료" if i == len(nodes) - 1 else "처리")
        
        steps.append({
            "node_id": node_id,
            "description": f"[{node_type}] {label}",
            "role": role
        })
    
    return {
        "summary": f"이 워크플로우는 {len(nodes)}개의 노드로 구성되어 있습니다.",
        "steps": steps,
        "data_flow": "데이터가 순차적으로 각 노드를 통해 흐릅니다.",
        "issues": [],
        "suggestions": ["노드가 많아질 경우 그룹화를 고려하세요."]
    }


# ──────────────────────────────────────────────────────────────
# 제안 생성
# ──────────────────────────────────────────────────────────────

def generate_suggestions(
    workflow: Dict[str, Any],
    max_suggestions: int = 5
) -> Generator[Dict[str, Any], None, None]:
    """
    워크플로우 최적화 제안 생성
    
    Args:
        workflow: 워크플로우 JSON
        max_suggestions: 최대 제안 수
        
    Yields:
        제안 객체
    """
    # 1. 규칙 기반 제안 (즉시 생성)
    yield from src._generate_rule_based_suggestions(workflow)
    
    # 2. LLM 기반 제안 (Mock 모드에서는 스킵)
    if not _is_mock_mode():
        try:
            yield from src._generate_llm_suggestions(workflow, max_suggestions)
        except Exception as e:
            logger.warning(f"LLM suggestion generation failed: {e}")


def _generate_rule_based_suggestions(
    workflow: Dict[str, Any]
) -> Generator[Dict[str, Any], None, None]:
    """규칙 기반 제안 생성"""
    nodes = workflow.get("nodes", [])
    edges = workflow.get("edges", [])
    
    # 1. 연속된 동일 타입 노드 그룹화 제안
    type_sequences: Dict[str, List[str]] = {}
    for node in nodes:
        node_type = node.get("type")
        node_id = node.get("id")
        
        if node_type not in type_sequences:
            type_sequences[node_type] = []
        type_sequences[node_type].append(node_id)
    
    for node_type, node_ids in type_sequences.items():
        if len(node_ids) >= 3:
            yield {
                "id": f"sug_group_{node_type}",
                "action": "group",
                "reason": f"동일한 타입({node_type})의 노드가 {len(node_ids)}개 있습니다. 그룹화를 고려해보세요.",
                "affected_nodes": node_ids[:5],
                "proposed_change": {},
                "confidence": 0.6
            }
    
    # 2. 연결되지 않은 노드 연결 제안
    auditor = LogicalAuditor(workflow)
    issues = auditor.audit()
    
    for issue in issues:
        if issue.get("type") == "orphan_node":
            yield {
                "id": f"sug_connect_{issue.get('affected_nodes', ['?'])[0]}",
                "action": "connect",
                "reason": issue.get("message"),
                "affected_nodes": issue.get("affected_nodes", []),
                "proposed_change": {},
                "confidence": 0.9
            }


def _generate_llm_suggestions(
    workflow: Dict[str, Any],
    max_suggestions: int
) -> Generator[Dict[str, Any], None, None]:
    """LLM 기반 제안 생성"""
    workflow_json = json.dumps(workflow, ensure_ascii=False)[:2000]
    
    prompt = f"""다음 워크플로우를 분석하고 최대 {max_suggestions}개의 개선 제안을 생성하세요:

{workflow_json}

각 제안을 JSONL 형식으로 출력하세요."""
    
    for chunk in invoke_bedrock_model_stream(SUGGESTION_SYSTEM_PROMPT, prompt):
        chunk = chunk.strip()
        if not chunk:
            continue
            
        try:
            obj = json.loads(chunk)
            if obj.get("type") == "suggestion":
                yield obj.get("data", {})
        except json.JSONDecodeError:
            continue


# ──────────────────────────────────────────────────────────────
# 제안 적용
# ──────────────────────────────────────────────────────────────

def apply_suggestion(
    workflow: Dict[str, Any],
    suggestion: Dict[str, Any]
) -> Dict[str, Any]:
    """
    제안을 워크플로우에 적용
    
    Args:
        workflow: 현재 워크플로우
        suggestion: 적용할 제안
        
    Returns:
        수정된 워크플로우
    """
    action = suggestion.get("action")
    affected_nodes = suggestion.get("affected_nodes", [])
    proposed_change = suggestion.get("proposed_change", {})
    
    # 워크플로우 복사
    new_workflow = {
        "nodes": list(workflow.get("nodes", [])),
        "edges": list(workflow.get("edges", [])),
        "subgraphs": dict(workflow.get("subgraphs", {}))
    }
    
    if action == "group":
        # 노드 그룹화 (프론트엔드 groupNodes 로직과 동기화)
        logger.info(f"Grouping nodes: {affected_nodes}")
        
        group_name = proposed_change.get("group_name", f"Group {len(new_workflow.get('subgraphs', {})) + 1}")
        nodes_to_group = [n for n in new_workflow["nodes"] if n.get("id") in affected_nodes]
        
        if len(nodes_to_group) < 2:
            logger.warning("Cannot group less than 2 nodes")
            return new_workflow
        
        # 그룹화할 노드들 간의 내부 엣지 찾기
        internal_edges = [
            e for e in new_workflow["edges"]
            if e.get("source") in affected_nodes and e.get("target") in affected_nodes
        ]
        
        # 외부에서 들어오는/나가는 엣지 찾기
        external_edges = [
            e for e in new_workflow["edges"]
            if (e.get("source") in affected_nodes and e.get("target") not in affected_nodes) or
               (e.get("source") not in affected_nodes and e.get("target") in affected_nodes)
        ]
        
        # 그룹 노드의 위치 계산 (묶인 노드들의 중심점)
        avg_x = sum(n.get("position", {}).get("x", 0) for n in nodes_to_group) / len(nodes_to_group)
        avg_y = sum(n.get("position", {}).get("y", 0) for n in nodes_to_group) / len(nodes_to_group)
        
        # 서브그래프 ID 생성
        import time
        import random
        subgraph_id = f"subgraph-{int(time.time())}-{random.randint(1000, 9999)}"
        
        # 서브그래프 정의 생성
        subgraph = {
            "id": subgraph_id,
            "nodes": [
                {
                    **n,
                    "position": {
                        "x": n.get("position", {}).get("x", 0) - avg_x + 200,
                        "y": n.get("position", {}).get("y", 0) - avg_y + 200
                    }
                }
                for n in nodes_to_group
            ],
            "edges": internal_edges,
            "metadata": {
                "name": group_name,
                "createdAt": datetime.now().isoformat()
            }
        }
        
        # 그룹 노드 생성
        group_node = {
            "id": subgraph_id,
            "type": "group",
            "position": {"x": avg_x, "y": avg_y},
            "data": {
                "label": group_name,
                "subgraphId": subgraph_id,
                "nodeCount": len(nodes_to_group)
            }
        }
        
        # 외부 엣지를 그룹 노드에 연결하도록 업데이트
        updated_external_edges = []
        for edge in external_edges:
            updated_edge = dict(edge)
            if edge.get("source") in affected_nodes:
                updated_edge["source"] = subgraph_id
            if edge.get("target") in affected_nodes:
                updated_edge["target"] = subgraph_id
            updated_external_edges.append(updated_edge)
        
        # 기존 노드/엣지 제거 및 그룹 노드 추가
        remaining_nodes = [n for n in new_workflow["nodes"] if n.get("id") not in affected_nodes]
        remaining_edges = [
            e for e in new_workflow["edges"]
            if e.get("source") not in affected_nodes and e.get("target") not in affected_nodes
        ]
        
        new_workflow["nodes"] = remaining_nodes + [group_node]
        new_workflow["edges"] = remaining_edges + updated_external_edges
        new_workflow["subgraphs"][subgraph_id] = subgraph
        
    elif action == "add_node":
        # 노드 추가
        new_node = proposed_change.get("new_node")
        if new_node:
            new_workflow["nodes"].append(new_node)
            
    elif action == "delete":
        # 노드 삭제
        new_workflow["nodes"] = [
            n for n in new_workflow["nodes"]
            if n.get("id") not in affected_nodes
        ]
        new_workflow["edges"] = [
            e for e in new_workflow["edges"]
            if e.get("source") not in affected_nodes 
            and e.get("target") not in affected_nodes
        ]
        
    elif action == "connect":
        # 엣지 추가
        new_edge = proposed_change.get("new_edge")
        if new_edge:
            new_workflow["edges"].append(new_edge)
    
    return new_workflow
