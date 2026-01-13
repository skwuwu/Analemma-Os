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
NOTIFICATIONS_INDEX = os.environ.get('NOTIFICATIONS_INDEX')

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

def map_execution_to_notification(item):
    """ExecutionsTable 아이템을 NotificationItem 형식으로 변환"""
    # notificationTime은 ISO String이므로 timestamp(ms)로 변환
    ts = 0
    try:
        dt = datetime.fromisoformat(item.get('notificationTime').replace('Z', '+00:00'))
        ts = int(dt.timestamp() * 1000)
    except:
        ts = int(datetime.now().timestamp() * 1000)

    # Determine action based on status for frontend filtering
    status = item.get('status')
    action = "workflow_status"
    if status in ['RUNNING', 'STARTED']:
        action = "execution_progress"
    elif status == 'PAUSED_FOR_HITP':
        action = "hitp_pause"

    return {
        "notificationId": item.get('executionArn'), # Use ARN as ID
        "type": "workflow_status",
        "action": action, # Top-level action for useNotifications hook
        "status": "sent", # Dismiss되지 않았으므로 '보여질' 상태
        "timestamp": ts,
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
    # 1. Auth & Setup
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
    limit = min(int(query_params.get('limit', 50)), 100)
    next_token_in = query_params.get('nextToken')
    
    notifications = []
    next_token_out = None

    try:
        # 2. Fetch Active Notifications (ExecutionsTable OwnerIdStatusIndex)
        active_items = []
        if EXECUTIONS_TABLE and os.environ.get('STATUS_INDEX'):
            status_index = os.environ.get('STATUS_INDEX')
            exec_table = dynamodb.Table(EXECUTIONS_TABLE)
            # Query for known active statuses
            active_statuses = ['RUNNING', 'STARTED', 'PAUSED_FOR_HITP']
            
            for status in active_statuses:
                try:
                    resp = exec_table.query(
                        IndexName=status_index,
                        KeyConditionExpression=Key('ownerId').eq(owner_id) & Key('status').eq(status)
                    )
                    items = resp.get('Items', [])
                    # Map to notification format if needed, or use as is (ExecutionsTable item structure)
                    # Active items in ExecutionsTable now have the same structure as Completed ones, 
                    # but we need to ensure they look like 'notifications' for the frontend.
                    # Frontend expects 'notification' object inside?
                    # map_execution_to_notification handles 'notification' object creation.
                    mapped_items = [map_execution_to_notification(item) for item in items]
                    active_items.extend(mapped_items)
                except Exception as e:
                    logger.error(f"Failed to query status {status}: {e}")
        
        # 3. Fetch Completed Notifications (ExecutionsTable GSI)
        completed_items = []
        if EXECUTIONS_TABLE and NOTIFICATIONS_INDEX:
            exec_table = dynamodb.Table(EXECUTIONS_TABLE)
            
            query_kwargs = {
                'IndexName': NOTIFICATIONS_INDEX,
                'KeyConditionExpression': Key('ownerId').eq(owner_id),
                'ScanIndexForward': False, # 최신순
                'Limit': limit
            }
            
            if next_token_in:
                try:
                    query_kwargs['ExclusiveStartKey'] = json.loads(base64.b64decode(next_token_in).decode('utf-8'))
                except:
                    pass

            exec_resp = exec_table.query(**query_kwargs)
            raw_completed = exec_resp.get('Items', [])
            completed_items = [map_execution_to_notification(item) for item in raw_completed]
            
            if exec_resp.get('LastEvaluatedKey'):
                lek_json = json.dumps(exec_resp['LastEvaluatedKey'], cls=DecimalEncoder)
                next_token_out = base64.b64encode(lek_json.encode('utf-8')).decode('utf-8')

        # 4. Merge & Deduplicate
        final_list = []
        seen_ids = set()
        
        # Add Active first (usually more important)
        for item in active_items:
            nid = item['notificationId']
            if nid not in seen_ids:
                final_list.append(item)
                seen_ids.add(nid)

        # Add Completed
        for item in completed_items:
            nid = item['notificationId']
            if nid not in seen_ids:
                final_list.append(item)
                seen_ids.add(nid)

        # Sort combined list by timestamp desc
        final_list.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
        
        # Slice to limit
        final_list = final_list[:limit]

        result = {
            'notifications': final_list,
            'count': len(final_list),
            'nextToken': next_token_out # Only for Completed pagination
        }

        return {
            'statusCode': 200,
            'headers': JSON_HEADERS,
            'body': json.dumps(result, cls=DecimalEncoder)
        }

    except Exception as e:
        logger.exception(f"Error fetching notifications: {e}")
        return {'statusCode': 500, 'headers': JSON_HEADERS, 'body': json.dumps({'error': str(e)})}
