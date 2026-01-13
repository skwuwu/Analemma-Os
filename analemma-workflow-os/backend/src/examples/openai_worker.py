import os
import json
import boto3
import openai

openai.api_key = os.environ.get('OPENAI_API_KEY')
ddb = boto3.resource('dynamodb')

CHECKPOINT_TABLE = os.environ.get('CHECKPOINT_TABLE')

def lambda_handler(event, context):
    # event: { 'model': 'gpt-4o', 'prompt': '...', 'checkpoint_key': 'user123-cp-1' }
    model = event.get('model')
    prompt = event.get('prompt')
    checkpoint_key = event.get('checkpoint_key')

    # load checkpoint if present
    cp = {}
    if checkpoint_key and CHECKPOINT_TABLE:
        table = ddb.Table(CHECKPOINT_TABLE)
        res = table.get_item(Key={'cp_key': checkpoint_key})
        cp = res.get('Item', {}).get('payload') or {}

    # merge checkpoint into prompt/state as needed
    # (consumer decides how to use cp)

    resp = openai.ChatCompletion.create(
        model=model or 'gpt-4o-mini',
        messages=[{'role': 'user', 'content': prompt}],
        timeout=600
    )

    result_text = resp['choices'][0]['message']['content']

    # persist updated checkpoint if requested
    if checkpoint_key and CHECKPOINT_TABLE:
        table.put_item(Item={'cp_key': checkpoint_key, 'payload': {'last_output': result_text}})

    return {'output': result_text}
