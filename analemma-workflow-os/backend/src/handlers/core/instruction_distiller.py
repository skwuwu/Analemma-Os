# -*- coding: utf-8 -*-
"""
Instruction Distiller Lambda

HITL 단계에서 사용자가 수정한 결과물을 분석하여 
암묵적 지침을 추출하는 비동기 증류 파이프라인입니다.

트리거: HITL 승인 완료 이벤트 (EventBridge 또는 SNS)
프로세스:
  1. S3에서 original_output과 user_corrected_output 로드
  2. LLM(Haiku)이 두 텍스트의 차이점(diff) 분석
  3. 추출된 지침을 DistilledInstructions 테이블에 저장
  4. 다음 실행부터 해당 지침 자동 반영
"""

import os
import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from decimal import Decimal

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 환경 변수
DISTILLED_INSTRUCTIONS_TABLE = os.environ.get("DISTILLED_INSTRUCTIONS_TABLE", "DistilledInstructions")
S3_BUCKET = os.environ.get("WORKFLOW_STATE_BUCKET", "")
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")
HAIKU_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"

# 지침 가중치 설정
DEFAULT_INSTRUCTION_WEIGHT = Decimal("1.0")
MIN_INSTRUCTION_WEIGHT = Decimal("0.1")
WEIGHT_DECAY_ON_REWORK = Decimal("0.3")  # 재수정 시 가중치 감소량
MAX_INSTRUCTIONS_PER_NODE = 10  # 노드당 최대 지침 수

# 지침 구조
INSTRUCTION_SCHEMA = {
    "text": str,  # 지침 텍스트
    "weight": Decimal,  # 가중치 (1.0 ~ 0.1)
    "created_at": str,  # 생성 시간
    "last_used": str,  # 마지막 사용 시간
    "usage_count": int,  # 사용 횟수
    "is_active": bool  # 활성화 여부
}

# AWS 클라이언트
dynamodb = boto3.resource("dynamodb")
instructions_table = dynamodb.Table(DISTILLED_INSTRUCTIONS_TABLE)
s3_client = boto3.client("s3")

# Bedrock 클라이언트
bedrock_config = Config(
    retries={"max_attempts": 2, "mode": "standard"},
    read_timeout=30,
    connect_timeout=5,
)
bedrock_client = boto3.client(
    "bedrock-runtime",
    region_name=BEDROCK_REGION,
    config=bedrock_config
)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    HITL 승인 이벤트를 처리하여 지침을 증류합니다.
    
    이벤트 구조:
    {
        "detail-type": "HITL Approval Completed",
        "detail": {
            "execution_id": "exec-123",
            "node_id": "generate_email",
            "workflow_id": "wf-456",
            "owner_id": "user-789",
            "original_output_ref": "s3://bucket/original.json",
            "corrected_output_ref": "s3://bucket/corrected.json",
            "approval_timestamp": "2026-01-04T12:00:00Z"
        }
    }
    """
    try:
        logger.info(f"Received event: {json.dumps(event, default=str)[:500]}")
        
        detail = event.get("detail", {})
        
        # 필수 필드 검증
        required_fields = ["execution_id", "node_id", "original_output_ref", "corrected_output_ref"]
        for field in required_fields:
            if field not in detail:
                logger.warning(f"Missing required field: {field}")
                return {"statusCode": 400, "body": f"Missing: {field}"}
        
        execution_id = detail["execution_id"]
        node_id = detail["node_id"]
        workflow_id = detail.get("workflow_id", "unknown")
        owner_id = detail.get("owner_id", "unknown")
        
        # S3에서 원본 및 수정본 로드
        original_output = _load_from_s3_ref(detail["original_output_ref"])
        corrected_output = _load_from_s3_ref(detail["corrected_output_ref"])
        
        if not original_output or not corrected_output:
            logger.warning("Failed to load outputs from S3")
            return {"statusCode": 400, "body": "Failed to load outputs"}
        
        # 차이점 분석 및 지침 추출
        distilled_instructions = _distill_instructions(
            original_output=original_output,
            corrected_output=corrected_output,
            node_id=node_id,
            workflow_id=workflow_id,
            owner_id=owner_id
        )
        
        if distilled_instructions:
            # DynamoDB에 저장
            _save_distilled_instructions(
                workflow_id=workflow_id,
                node_id=node_id,
                owner_id=owner_id,
                instructions=distilled_instructions,
                execution_id=execution_id
            )
            
            logger.info(f"Distilled {len(distilled_instructions)} instructions for {node_id}")
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "distilled_count": len(distilled_instructions),
                    "instructions": distilled_instructions
                })
            }
        
        return {"statusCode": 200, "body": "No significant differences found"}
        
    except Exception as e:
        logger.error(f"Error in instruction distillation: {e}", exc_info=True)
        return {"statusCode": 500, "body": str(e)}


def _load_from_s3_ref(s3_ref: str) -> Optional[str]:
    """
    S3 참조에서 콘텐츠 로드
    s3://bucket/key 형식 지원
    """
    try:
        if s3_ref.startswith("s3://"):
            parts = s3_ref[5:].split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""
        else:
            bucket = S3_BUCKET
            key = s3_ref
        
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read().decode("utf-8")
        
        # JSON인 경우 파싱 후 문자열로 변환
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                # output 필드가 있으면 추출
                return data.get("output", json.dumps(data, ensure_ascii=False))
            return str(data)
        except json.JSONDecodeError:
            return content
            
    except Exception as e:
        logger.error(f"Failed to load from S3: {s3_ref}, error: {e}")
        return None


def _extract_new_instructions(
    original_output: str,
    corrected_output: str,
    node_id: str,
    workflow_id: str
) -> List[str]:
    """
    LLM을 사용하여 원본과 수정본의 차이에서 신규 지침 추출
    """
    # 프롬프트 구성
    prompt = f"""당신은 사용자의 수정 패턴을 분석하여 향후 AI 응답 개선을 위한 지침을 추출하는 전문가입니다.

## 원본 AI 출력:
{original_output[:2000]}

## 사용자 수정본:
{corrected_output[:2000]}

## 분석 대상 노드: {node_id}
## 워크플로우: {workflow_id}

위 두 텍스트를 비교하여 사용자가 원하는 스타일/내용의 차이점을 추출해주세요.
각 지침은 구체적이고 실행 가능해야 합니다.

중요 보안 지침:
- 특정 인명, 주소, 연락처 등 개인정보(PII)는 일반적인 규칙으로 추상화하십시오.
- 예: "홍길동에게 메일 써줘" → "수신자의 이름을 본문에 포함할 것"

다음 형식으로 JSON 배열만 출력하세요 (설명 없이):
["지침1", "지침2", "지침3"]

예시:
["정중하고 격식체를 사용할 것", "구체적인 수치와 날짜를 포함할 것", "인사말을 먼저 작성할 것"]

JSON 배열:"""
    
    try:
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 500,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        
        response = bedrock_client.invoke_model(
            body=json.dumps(payload),
            modelId=HAIKU_MODEL_ID
        )
        
        result = json.loads(response["body"].read())
        if "content" in result and result["content"]:
            response_text = result["content"][0].get("text", "").strip()
            
            # JSON 배열 파싱
            try:
                # ```json ... ``` 형식 처리
                if "```" in response_text:
                    json_match = response_text.split("```")[1]
                    if json_match.startswith("json"):
                        json_match = json_match[4:]
                    response_text = json_match.strip()
                
                instructions = json.loads(response_text)
                if isinstance(instructions, list):
                    # 유효한 문자열만 필터링
                    return [
                        inst.strip() for inst in instructions 
                        if isinstance(inst, str) and len(inst.strip()) > 5
                    ][:MAX_INSTRUCTIONS_PER_NODE]
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse instructions JSON: {response_text[:200]}")
                
    except ClientError as e:
        logger.error(f"Bedrock invocation failed: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in distillation: {e}")
    
    return []


def _consolidate_instructions(
    existing_instructions: List[str],
    new_raw_instructions: List[str],
    node_id: str
) -> List[str]:
    """
    기존 지침과 신규 지침을 분석하여 최적의 통합 지침 생성
    """
    if not new_raw_instructions:
        return existing_instructions
    
    if not existing_instructions:
        return new_raw_instructions
    
    prompt = f"""당신은 AI 가이드라인 최적화 전문가입니다. 
기존 지침과 사용자의 새로운 요구사항을 통합하여 하나의 정교한 지침 목록을 만들어주세요.

## 기존 지침:
{json.dumps(existing_instructions, ensure_ascii=False)}

## 신규 추가 사항:
{json.dumps(new_raw_instructions, ensure_ascii=False)}

## 제약 사항:
1. 서로 상충되는 내용이 있다면 '신규 추가 사항'을 우선하십시오.
2. 특정 인명, 주소, 연락처 등 개인정보(PII)는 일반적인 규칙으로 추상화하십시오.
3. 최대 {MAX_INSTRUCTIONS_PER_NODE}개 이내의 불렛포인트로 작성하십시오.
4. 출력은 반드시 JSON 문자열 배열 형식이어야 합니다.

통합 지침:"""
    
    try:
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 600,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        
        response = bedrock_client.invoke_model(
            body=json.dumps(payload),
            modelId=HAIKU_MODEL_ID
        )
        
        result = json.loads(response["body"].read())
        if "content" in result and result["content"]:
            response_text = result["content"][0].get("text", "").strip()
            
            # JSON 배열 파싱
            try:
                # ```json ... ``` 형식 처리
                if "```" in response_text:
                    json_match = response_text.split("```")[1]
                    if json_match.startswith("json"):
                        json_match = json_match[4:]
                    response_text = json_match.strip()
                
                instructions = json.loads(response_text)
                if isinstance(instructions, list):
                    # 유효한 문자열만 필터링
                    return [
                        inst.strip() for inst in instructions 
                        if isinstance(inst, str) and len(inst.strip()) > 5
                    ][:MAX_INSTRUCTIONS_PER_NODE]
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse consolidated instructions JSON: {response_text[:200]}")
                # 파싱 실패 시 기존 + 신규 단순 결합
                combined = existing_instructions + new_raw_instructions
                return list(set(combined))[:MAX_INSTRUCTIONS_PER_NODE]
                
    except ClientError as e:
        logger.error(f"Bedrock invocation failed in consolidation: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in consolidation: {e}")
    
    # 실패 시 기존 지침 유지
    return existing_instructions


def _save_distilled_instructions(
    workflow_id: str,
    node_id: str,
    owner_id: str,
    instructions: List[str],
    execution_id: str
) -> None:
    """
    증류된 지침을 DynamoDB에 저장 (가중치 시스템 적용)
    
    기존 지침의 가중치를 decay시키고, 통합된 새 지침을 저장합니다.
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d%H%M%S")
    
    pk = f"{owner_id}#{workflow_id}"
    sk = f"{node_id}#{timestamp}"
    
    try:
        # 기존 지침 조회 및 가중치 decay 적용
        existing_weighted_instructions = _get_weighted_instructions(pk, node_id)
        
        # Decay 적용: 재수정 시 기존 지침 가중치 감소
        decayed_instructions = []
        for inst in existing_weighted_instructions:
            new_weight = Decimal(str(inst["weight"])) - WEIGHT_DECAY_ON_REWORK
            if new_weight >= MIN_INSTRUCTION_WEIGHT:
                inst["weight"] = new_weight
                decayed_instructions.append(inst)
            # MIN_INSTRUCTION_WEIGHT 이하이면 제거 (is_active = False)
        
        # 통합 지침을 구조화된 형태로 변환
        structured_instructions = []
        for inst_text in instructions:
            structured_instructions.append({
                "text": inst_text,
                "weight": DEFAULT_INSTRUCTION_WEIGHT,
                "created_at": now.isoformat(),
                "last_used": now.isoformat(),
                "usage_count": 0,
                "is_active": True
            })
        
        # 새 지침 저장 (decay된 기존 + 새 통합)
        final_instructions = decayed_instructions + structured_instructions
        
        instructions_table.put_item(
            Item={
                "pk": pk,
                "sk": sk,
                "node_id": node_id,
                "workflow_id": workflow_id,
                "owner_id": owner_id,
                "instructions": [inst["text"] for inst in final_instructions],  # 레거시 호환
                "weighted_instructions": _convert_to_dynamodb_format(final_instructions),
                "source_execution_id": execution_id,
                "created_at": now.isoformat(),
                "is_active": True,
                "version": 1,
                "usage_count": 0,
            }
        )
        
        # 해당 노드의 최신 활성 지침 인덱스 업데이트
        _update_latest_instruction_index(pk, node_id, sk)
        
        logger.info(f"Saved distilled instructions with weights: pk={pk}, sk={sk}, count={len(final_instructions)}")
        
    except ClientError as e:
        logger.error(f"DynamoDB error saving instructions: {e}")
        raise


def _merge_and_deduplicate_instructions(
    existing: List[dict], 
    new_instructions: List[str]
) -> List[str]:
    """
    기존 지침과 새 지침을 병합하고 의미적 중복을 제거합니다.
    """
    existing_texts = {inst.get("text", "").strip().lower() for inst in existing}
    
    merged = [inst.get("text", "") for inst in existing]
    
    for new_inst in new_instructions:
        normalized = new_inst.strip().lower()
        # 간단한 중복 검사 (정확 일치)
        if normalized not in existing_texts:
            merged.append(new_inst)
            existing_texts.add(normalized)
    
    return merged


def _deduplicate_weighted_instructions(instructions: List[dict]) -> List[dict]:
    """가중치 기반 지침 중복 제거 (높은 가중치 우선)"""
    seen = {}
    
    for inst in instructions:
        text = inst.get("text", "").strip().lower()
        if text not in seen:
            seen[text] = inst
        elif inst.get("weight", 0) > seen[text].get("weight", 0):
            seen[text] = inst
    
    # 가중치 순으로 정렬
    return sorted(seen.values(), key=lambda x: x.get("weight", 0), reverse=True)


def _convert_to_dynamodb_format(instructions: List[dict]) -> List[dict]:
    """DynamoDB 저장을 위해 Decimal 변환"""
    result = []
    for inst in instructions:
        item = dict(inst)
        if "weight" in item:
            item["weight"] = Decimal(str(item["weight"]))
        result.append(item)
    return result



def _get_weighted_instructions(pk: str, node_id: str) -> List[Dict[str, Any]]:
    """
    특정 노드의 최신 가중치 기반 지침 목록 조회
    (내부 로직용 - Decay 계산 시 사용)
    """
    try:
        # 최신 지침 인덱스 조회
        response = instructions_table.get_item(
            Key={"pk": pk, "sk": f"LATEST#{node_id}"}
        )
        
        if "Item" not in response:
            return []
        
        latest_sk = response["Item"].get("latest_instruction_sk")
        if not latest_sk:
            return []
        
        # 실제 지침 조회
        response = instructions_table.get_item(
            Key={"pk": pk, "sk": latest_sk}
        )
        
        if "Item" in response:
            return response["Item"].get("weighted_instructions", [])
            
    except ClientError as e:
        logger.warning(f"Failed to get weighted instructions: {e}")
    
    return []

def _update_latest_instruction_index(pk: str, node_id: str, latest_sk: str) -> None:
    """
    노드별 최신 지침 인덱스 업데이트
    """
    try:
        instructions_table.put_item(
            Item={
                "pk": pk,
                "sk": f"LATEST#{node_id}",
                "latest_instruction_sk": latest_sk,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except ClientError as e:
        logger.warning(f"Failed to update latest index: {e}")


def get_active_instructions(
    owner_id: str,
    workflow_id: str,
    node_id: str
) -> List[str]:
    """
    특정 노드에 대한 활성 지침 조회 (외부 호출용)
    가중치가 MIN_INSTRUCTION_WEIGHT 이상인 지침만 반환합니다.
    
    Returns:
        활성화된 지침 목록 (가중치 순)
    """
    pk = f"{owner_id}#{workflow_id}"
    
    try:
        # 최신 지침 인덱스 조회
        response = instructions_table.get_item(
            Key={"pk": pk, "sk": f"LATEST#{node_id}"}
        )
        
        if "Item" not in response:
            return []
        
        latest_sk = response["Item"].get("latest_instruction_sk")
        if not latest_sk:
            return []
        
        # 실제 지침 조회
        response = instructions_table.get_item(
            Key={"pk": pk, "sk": latest_sk}
        )
        
        if "Item" in response and response["Item"].get("is_active"):
            # 가중치 기반 필터링
            weighted = response["Item"].get("weighted_instructions", [])
            
            if weighted:
                # 가중치가 충분한 지침만 선택
                valid_instructions = [
                    inst["text"] for inst in weighted
                    if Decimal(str(inst.get("weight", 0))) >= MIN_INSTRUCTION_WEIGHT
                ]
                
                # 사용 횟수 증가 (비동기)
                _increment_usage_count(pk, latest_sk)
                
                return valid_instructions
            
            # 레거시 형식 호환
            instructions = response["Item"].get("instructions", [])
            _increment_usage_count(pk, latest_sk)
            return instructions
            
    except ClientError as e:
        logger.error(f"Error getting active instructions: {e}")
    
    return []


def _increment_usage_count(pk: str, sk: str) -> None:
    """사용 횟수 증가 (비동기, 실패 무시)"""
    try:
        instructions_table.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="SET usage_count = usage_count + :inc, total_applications = total_applications + :inc",
            ExpressionAttributeValues={":inc": 1}
        )
    except Exception:
        pass


def record_instruction_feedback(
    owner_id: str,
    workflow_id: str,
    node_id: str,
    is_positive: bool,
    instruction_text: Optional[str] = None
) -> None:
    """
    지침 적용 결과에 대한 피드백을 기록하고 가중치를 조정합니다.
    
    - is_positive=True: 지침이 효과적이었음 (가중치 유지 또는 증가)
    - is_positive=False: 사용자가 다시 수정함 (가중치 감소)
    
    Args:
        owner_id: 사용자 ID
        workflow_id: 워크플로우 ID
        node_id: 노드 ID
        is_positive: 긍정적 피드백 여부
        instruction_text: 특정 지침 텍스트 (없으면 전체 적용)
    """
    pk = f"{owner_id}#{workflow_id}"
    
    try:
        response = instructions_table.get_item(
            Key={"pk": pk, "sk": f"LATEST#{node_id}"}
        )
        
        if "Item" not in response:
            return
        
        latest_sk = response["Item"].get("latest_instruction_sk")
        if not latest_sk:
            return
        
        response = instructions_table.get_item(
            Key={"pk": pk, "sk": latest_sk}
        )
        
        if "Item" not in response:
            return
        
        item = response["Item"]
        weighted = item.get("weighted_instructions", [])
        
        if not weighted:
            return
        
        updated = False
        for inst in weighted:
            # 특정 지침만 업데이트하거나, 전체 업데이트
            if instruction_text and inst.get("text") != instruction_text:
                continue
            
            current_weight = Decimal(str(inst.get("weight", DEFAULT_INSTRUCTION_WEIGHT)))
            
            if is_positive:
                # 긍정적 피드백: 가중치 소폭 증가 (최대 1.5)
                new_weight = min(current_weight + Decimal("0.1"), Decimal("1.5"))
            else:
                # 부정적 피드백: 가중치 감소
                inst["rework_count"] = inst.get("rework_count", 0) + 1
                new_weight = max(current_weight - WEIGHT_DECAY_ON_REWORK, MIN_INSTRUCTION_WEIGHT)
            
            inst["weight"] = new_weight
            updated = True
        
        if updated:
            # 성공률 업데이트
            total = item.get("total_applications", 1)
            current_success = item.get("success_rate", Decimal("0"))
            
            if is_positive:
                new_success_rate = ((current_success * (total - 1)) + 1) / total
            else:
                new_success_rate = (current_success * (total - 1)) / total
            
            instructions_table.update_item(
                Key={"pk": pk, "sk": latest_sk},
                UpdateExpression="SET weighted_instructions = :wi, success_rate = :sr",
                ExpressionAttributeValues={
                    ":wi": weighted,
                    ":sr": Decimal(str(round(new_success_rate, 2)))
                }
            )
            
            logger.info(f"Updated instruction weights for {node_id}: positive={is_positive}")
            
    except ClientError as e:
        logger.error(f"Error recording instruction feedback: {e}")


def merge_instructions_into_prompt(
    base_prompt: str,
    owner_id: str,
    workflow_id: str,
    node_id: str,
    injection_strategy: str = "system"
) -> str:
    """
    기본 프롬프트에 증류된 지침을 구조화하여 병합
    
    XML 태그로 지침의 경계를 명시하여 모델이 엄격하게 인지하도록 합니다.
    """
    instructions = get_active_instructions(owner_id, workflow_id, node_id)
    
    if not instructions:
        return base_prompt
    
    # XML 태그로 지침 구조화 (Claude 최적화)
    instruction_block = "\n".join([f"  <rule>{inst}</rule>" for inst in instructions])
    
    if injection_strategy == "system":
        enhanced_prompt = f"""<user_preferences>
{instruction_block}
</user_preferences>

{base_prompt}

위의 <user_preferences> 내의 규칙을 최우선으로 준수하여 답변하십시오."""
    
    elif injection_strategy == "prefix":
        enhanced_prompt = f"""<user_preferences>
{instruction_block}
</user_preferences>

---

{base_prompt}"""
    
    else:  # suffix (기존 방식, 레거시 호환)
        enhanced_prompt = f"""{base_prompt}

## 사용자 맞춤 지침 (자동 적용):
다음 지침을 반드시 따라주세요:
{instruction_block}
"""
    
    return enhanced_prompt


def _format_instructions_block(instructions: List[str]) -> str:
    """
    지침을 구조화된 블록으로 포맷팅합니다.
    XML 태그와 번호 매김을 사용하여 LLM의 이행률을 높입니다.
    """
    formatted_lines = []
    formatted_lines.append("당신은 다음의 사용자 맞춤 지침을 반드시 따라야 합니다:")
    formatted_lines.append("")
    
    for i, inst in enumerate(instructions, 1):
        formatted_lines.append(f"{i}. {inst}")
    
    formatted_lines.append("")
    formatted_lines.append("위 지침을 모든 응답에 적용하세요.")
    
    return "\n".join(formatted_lines)
