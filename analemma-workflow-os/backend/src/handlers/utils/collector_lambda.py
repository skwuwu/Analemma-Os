import os
import json
import logging
from typing import Optional, Dict, Any

import boto3
try:
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    class BotoCoreError(Exception):
        pass
    class ClientError(Exception):
        pass

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

# Configurable via env vars so tests can override easily
EVENT_BUS_NAME = os.getenv("EVENT_BUS_NAME", "my-app-event-bus")
EVENT_SOURCE = os.getenv("EVENT_SOURCE", "com.my-app.news-collector")
DETAIL_TYPE = os.getenv("DETAIL_TYPE", "New Article Found")

# Module-level client to allow tests to monkeypatch
events_client: Optional[Any] = None


def get_events_client():
    global events_client
    if events_client is None:
        # Lazy initialize so importing the module doesn't require aws config
        events_client = boto3.client("events")
    return events_client


def build_event(detail: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "EventBusName": EVENT_BUS_NAME,
        "Source": EVENT_SOURCE,
        "DetailType": DETAIL_TYPE,
        "Detail": json.dumps(detail),
    }


def publish(detail: Dict[str, Any]) -> Dict[str, Any]:
    client = get_events_client()
    event = build_event(detail)
    logger.debug("Putting event to EventBridge: %s", event)
    try:
        resp = client.put_events(Entries=[event])
        logger.info("put_events response: %s", resp)
        return resp
    except (BotoCoreError, ClientError) as e:
        logger.exception("Failed to publish event to EventBridge")
        raise


def lambda_handler(event, context):
    """Lambda handler to accept an article payload and publish it to EventBridge.

    Expected input examples:
    - API Gateway proxy: { "body": "{...article json...}" }
    - Direct invoke / test: { ...article json... }
    """
    logger.debug("collector_lambda received event: %s", event)

    # Support API Gateway proxy event where payload is in `body`
    payload = event.get("body") if isinstance(event, dict) and event.get("body") else event

    import os
    if isinstance(payload, str):
        try:
            detail = json.loads(payload)
        except json.JSONDecodeError:
            logger.exception("Invalid JSON payload")
            return {"statusCode": 400, "body": json.dumps({"error": "Invalid JSON payload"})}
    elif isinstance(payload, dict):
        detail = payload
    else:
        logger.error("Unsupported payload type: %s", type(payload))
        return {"statusCode": 400, "body": json.dumps({"error": "Unsupported payload type"})}

    # Basic validation: require at least title or url
    if not detail.get("title") and not detail.get("url"):
        logger.error("Invalid payload: missing title and url")
        return {"statusCode": 400, "body": json.dumps({"error": "Missing title or url"})}

    try:
        resp = publish(detail)
        return {"statusCode": 200, "body": json.dumps({"result": resp})}
    except (BotoCoreError, ClientError) as e:
        logger.exception("Failed to publish event to EventBridge")
        return {"statusCode": 502, "body": json.dumps({"error": "EventBridge publish failed"})}
    except Exception:
        logger.exception("Unexpected error in collector_lambda")
        return {"statusCode": 500, "body": json.dumps({"error": "Internal server error"})}

