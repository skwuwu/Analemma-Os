import json

def lambda_handler(event, context):
    # Minimal shim that other Lambdas could invoke if you choose to centralize
    # heavy Google API client calls in an image-based function. Returns 200 OK.
    return {"statusCode": 200, "body": json.dumps({"ok": True, "received": event})}
