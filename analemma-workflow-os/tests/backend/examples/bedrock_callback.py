s3 = boto3.client('s3')
import os
import json
import boto3
from urllib.parse import unquote_plus

from backend.workflow_repository import WorkflowRepository

ddb = boto3.client('dynamodb')
s3 = boto3.client('s3')
step = boto3.client('stepfunctions')

JOB_TABLE = os.environ.get('BEDROCK_JOB_TABLE')

def lambda_handler(event, context):
    repo = WorkflowRepository()
    for rec in event.get('Records', []):
        s3_info = rec['s3']
        bucket = s3_info['bucket']['name']
        key = unquote_plus(s3_info['object']['key'])

        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj['Body'].read()
        try:
            data = json.loads(body)
        except Exception:
            # If not JSON, wrap raw bytes
            data = {'raw': body.decode('utf-8', errors='replace')}

        job_id = data.get('jobId') or data.get('job_id') or key.split('/')[-1]

        task_token = repo.pop_job_mapping(job_id)
        if not task_token:
            # no mapping found; skip
            continue

        output = {'jobId': job_id, 'result': data}

        step.send_task_success(taskToken=task_token, output=json.dumps(output))

    return {'status': 'ok'}
