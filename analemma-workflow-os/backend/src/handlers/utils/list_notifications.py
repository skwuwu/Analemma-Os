"""
알림 센터 (Notification Inbox) API

사용자가 웹사이트 방문 시 미확인 알림 목록을 가져오는 Pull 방식 API
프론트엔드: GET /notifications?status=unread

[변경] Sparse Index 패턴 적용
- Active Workflows: ExecutionsTable의 OwnerIdStatusIndex에서 조회
- Completed Workflows (Not Dismissed): ExecutionsTable의 NotificationsIndex GSI에서 조회
- 두 결과를 병합하여 반환
"""

import json
import os
import logging
import boto3
import base64
from decimal import Decimal
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key, Attr
from datetime import datetime

# 안전한 임포트 시도
try:
    from src.common import statebag
except ImportError:
    try:
        from src.common import statebag
    except ImportError:
        statebag = None

# 공통 모듈에서 AWS 클라이언트 및 유틸리티 가져오기
try:
    from src.common.aws_clients import get_dynamodb_resource
    from src.common.json_utils import DecimalEncoder
    from src.common.http_utils import JSON_HEADERS
    dynamodb = get_dynamodb_resource()
    _USE_COMMON_UTILS = True
except ImportError:
    dynamodb = boto3.resource('dynamodb')
    _USE_COMMON_UTILS = False

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

EXECUTIONS_TABLE = os.environ.get('EXECUTIONS_TABLE')
# 🚨 [Critical Fix] NotificationsIndex GSI 기본값 추가
NOTIFICATIONS_INDEX = os.environ.get('NOTIFICATIONS_INDEX', 'NotificationsIndex')

# Fallback definitions if common modules not available
if not _USE_COMMON_UTILS:
    JSON_HEADERS = {"Content-Type": "application/json"}
    
    class DecimalEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return int(obj) if obj % 1 == 0 else float(obj)
            if isinstance(obj, set):
                return list(obj)
            try:
                return super(DecimalEncoder, self).default(obj)
            except TypeError:
                return str(obj)


# =============================================================================
# [v2.3] Smart Grouping: 알림 우선순위 정의
# PAUSED_FOR_HITP (사용자 승인 대기)를 최상단에 배치하여 업무 병목 즉시 해결
# =============================================================================
class NotificationPriority:
    """알림 우선순위 (숫자가 낮을수록 높은 우선순위)"""
    HITP_PAUSE = 0      # 사용자 승인 대기 - 최우선
    RUNNING = 1         # 실행 중
    COMPLETED = 2       # 완료됨
    FAILED = 3          # 실패
    DEFAULT = 10        # 기타


def _get_priority(action: str, status: str) -> int:
    """액션/상태 기반 우선순위 계산."""
    if action == "hitp_pause" or status == "PAUSED_FOR_HITP":
        return NotificationPriority.HITP_PAUSE
    elif action == "execution_progress" or status in ("RUNNING", "STARTED"):
        return NotificationPriority.RUNNING
    elif status in ("SUCCEEDED", "COMPLETED"):
        return NotificationPriority.COMPLETED
    elif status == "FAILED":
        return NotificationPriority.FAILED
    return NotificationPriority.DEFAULT


def _extract_timestamp_safe(item: dict) -> int:
    """
    [v2.3] 안정적인 타임스탬프 추출 (UI 플리커링 방지).
    
    폴백 순서:
    1. notificationTime
    2. startDate
    3. updated_at
    4. 0 (리스트 하단에 위치)
    
    datetime.now() 사용 금지 - 새로고침마다 순서 변경 방지
    """
    # 1. notificationTime 시도
    notification_time = item.get('notificationTime')
    if notification_time:
        try:
            dt = datetime.fromisoformat(str(notification_time).replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        except (ValueError, AttributeError):
            pass
    
    # 2. startDate 폴백 (boto3 datetime 또는 epoch ms)
    start_date = item.get('startDate')
    if start_date:
        try:
            if isinstance(start_date, datetime):
                return int(start_date.timestamp() * 1000)
            elif isinstance(start_date, (int, float)):
                # epoch ms로 가정
                return int(start_date) if start_date > 1e12 else int(start_date * 1000)
            elif isinstance(start_date, str):
                dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                return int(dt.timestamp() * 1000)
        except (ValueError, AttributeError):
            pass
    
    # 3. updated_at 폴백
    updated_at = item.get('updated_at') or item.get('updatedAt')
    if updated_at:
        try:
            if isinstance(updated_at, str):
                dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                return int(dt.timestamp() * 1000)
        except (ValueError, AttributeError):
            pass
    
    # 4. 타임스탬프 없음 - 0으로 처리하여 항상 하단 배치
    return 0


def map_execution_to_notification(item):
    """
    ExecutionsTable 아이템을 NotificationItem 형식으로 변환.
    
    [v2.3] 개선사항:
    - 안정적인 타임스탬프 폴백 (UI 플리커링 방지)
    - priority 필드 추가 (Smart Grouping)
    """
    # [v2.3] 안정적인 타임스탬프 추출
    ts = _extract_timestamp_safe(item)

    # Determine action based on status for frontend filtering
    status = item.get('status')
    action = "workflow_status"
    if status in ['RUNNING', 'STARTED']:
        action = "execution_progress"
    elif status == 'PAUSED_FOR_HITP':
        action = "hitp_pause"

    # [v2.3] Smart Grouping용 우선순위
    priority = _get_priority(action, status)

    return {
        "notificationId": item.get('executionArn'), # Use ARN as ID
        "type": "workflow_status",
        "action": action, # Top-level action for useNotifications hook
        "status": "sent", # Dismiss되지 않았으므로 '보여질' 상태
        "timestamp": ts,
        "priority": priority,  # [v2.3] Smart Grouping
        "notification": {
            "type": "workflow_status",
            "payload": {
                "action": action, # Payload-level action for consistency
                "execution_id": item.get('executionArn'),
                "status": status,
                "workflowId": item.get('workflowId'),
                "start_time": item.get('startDate'),
                "stop_time": item.get('stopDate'),
                "message": f"Workflow {status}",
                # 필요한 경우 output 등 추가
            }
        }
    }

def lambda_handler(event, context):
    """
    알림 센터 API Lambda 핸들러.
    
    [v2.3] 개선사항:
    1. Active 항목에도 Limit 적용 (메모리 보호)
    2. Smart Grouping: priority + timestamp 기반 정렬
    3. 복합 페이지네이션 토큰 (Active lastId + Completed LEK)
    """
    # 1. Auth & Setup
    try:
        owner_id = (event.get('requestContext', {})
                         .get('authorizer', {})
                         .get('jwt', {})
                         .get('claims', {})
                         .get('sub'))
    except Exception:
        owner_id = None
    
    if not owner_id:
        return {'statusCode': 401, 'headers': JSON_HEADERS, 'body': json.dumps({'error': 'Unauthorized'})}
    
    query_params = event.get('queryStringParameters') or {}
    
    # [v2.3] 안전한 limit 파싱
    try:
        limit = min(int(query_params.get('limit', 50)), 100)
    except (ValueError, TypeError):
        limit = 50
    
    next_token_in = query_params.get('nextToken')
    
    # [v2.3] 복합 토큰 파싱
    active_last_id = None
    completed_lek = None
    if next_token_in:
        try:
            token_data = json.loads(base64.b64decode(next_token_in).decode('utf-8'))
            if isinstance(token_data, dict) and 'type' in token_data:
                # 복합 토큰 형식
                active_last_id = token_data.get('active_last_id')
                completed_lek = token_data.get('completed_lek')
            else:
                # 레거시 형식 (Completed LEK만)
                completed_lek = token_data
        except Exception:
            pass

    try:
        # 2. Fetch Active Notifications (ExecutionsTable OwnerIdStatusIndex)
        # [v2.3] Active 항목에도 Limit 적용하여 메모리 보호
        active_items = []
        active_overflow = False  # 더 많은 Active 항목 존재 여부
        
        if EXECUTIONS_TABLE and os.environ.get('STATUS_INDEX'):
            status_index = os.environ.get('STATUS_INDEX')
            exec_table = dynamodb.Table(EXECUTIONS_TABLE)
            
            # [v2.3] PAUSED_FOR_HITP를 우선 조회 (Smart Grouping)
            priority_statuses = ['PAUSED_FOR_HITP', 'RUNNING', 'STARTED']
            active_limit_per_status = max(limit // len(priority_statuses), 10)
            
            for status in priority_statuses:
                if len(active_items) >= limit:
                    active_overflow = True
                    break
                    
                try:
                    resp = exec_table.query(
                        IndexName=status_index,
                        KeyConditionExpression=Key('ownerId').eq(owner_id) & Key('status').eq(status),
                        Limit=active_limit_per_status,  # [v2.3] 상태별 Limit
                        ScanIndexForward=False  # 최신순
                    )
                    items = resp.get('Items', [])
                    mapped_items = [map_execution_to_notification(item) for item in items]
                    active_items.extend(mapped_items)
                    
                    if resp.get('LastEvaluatedKey'):
                        active_overflow = True
                        
                except Exception as e:
                    logger.error(f"Failed to query status {status}: {e}")
        
        # 3. Fetch Completed Notifications (ExecutionsTable GSI)
        completed_items = []
        completed_lek_out = None
        
        if EXECUTIONS_TABLE and NOTIFICATIONS_INDEX:
            exec_table = dynamodb.Table(EXECUTIONS_TABLE)
            
            # [v2.3] Completed는 Active가 차지한 만큼 제외하고 조회
            completed_limit = max(limit - len(active_items), 10)
            
            query_kwargs = {
                'IndexName': NOTIFICATIONS_INDEX,
                'KeyConditionExpression': Key('ownerId').eq(owner_id),
                'ScanIndexForward': False,  # 최신순
                'Limit': completed_limit
            }
            
            if completed_lek:
                query_kwargs['ExclusiveStartKey'] = completed_lek

            exec_resp = exec_table.query(**query_kwargs)
            raw_completed = exec_resp.get('Items', [])
            completed_items = [map_execution_to_notification(item) for item in raw_completed]
            
            if exec_resp.get('LastEvaluatedKey'):
                completed_lek_out = exec_resp['LastEvaluatedKey']

        # 4. Merge & Deduplicate
        final_list = []
        seen_ids = set()
        
        # Active 먼저 추가 (우선순위 높음)
        for item in active_items:
            nid = item['notificationId']
            if nid not in seen_ids:
                final_list.append(item)
                seen_ids.add(nid)

        # Completed 추가
        for item in completed_items:
            nid = item['notificationId']
            if nid not in seen_ids:
                final_list.append(item)
                seen_ids.add(nid)

        # [v2.3] Smart Grouping: priority 우선, 같은 우선순위 내에서 timestamp 내림차순
        final_list.sort(key=lambda x: (x.get('priority', 10), -x.get('timestamp', 0)))
        
        # Slice to limit
        final_list = final_list[:limit]
        
        # [v2.3] 복합 페이지네이션 토큰 생성
        next_token_out = None
        has_more = active_overflow or completed_lek_out is not None
        
        if has_more:
            # 마지막 Active ID 추출 (다음 페이지에서 중복 방지용)
            last_active_id = None
            if final_list:
                active_in_result = [n for n in final_list if n.get('action') in ('execution_progress', 'hitp_pause')]
                if active_in_result:
                    last_active_id = active_in_result[-1].get('notificationId')
            
            composite_token = {
                'type': 'composite_v2',
                'active_last_id': last_active_id,
                'completed_lek': completed_lek_out
            }
            token_json = json.dumps(composite_token, cls=DecimalEncoder)
            next_token_out = base64.b64encode(token_json.encode('utf-8')).decode('utf-8')

        result = {
            'notifications': final_list,
            'count': len(final_list),
            'nextToken': next_token_out,
            'hasMore': has_more  # [v2.3] 추가 데이터 존재 여부
        }

        return {
            'statusCode': 200,
            'headers': JSON_HEADERS,
            'body': json.dumps(result, cls=DecimalEncoder)
        }

    except Exception as e:
        logger.exception(f"Error fetching notifications: {e}")
        return {'statusCode': 500, 'headers': JSON_HEADERS, 'body': json.dumps({'error': str(e)})}
