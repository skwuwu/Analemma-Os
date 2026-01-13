import os
import json
import boto3

from backend.workflow_repository import WorkflowRepository

bedrock = boto3.client('bedrock')

OUTPUT_BUCKET = os.environ.get('BEDROCK_OUTPUT_BUCKET')

def lambda_handler(event, context):
    task_token = event.get('TaskToken') or event.get('task_token')
    payload = event.get('input') if isinstance(event.get('input'), dict) else event
    model = payload.get('model')
    prompt = payload.get('prompt')

    # Kick off Bedrock async job
    resp = bedrock.invoke_model_async(
        ModelId=model,
        Body=json.dumps({'prompt': prompt}),
        OutputConfig={
            'S3Destination': {
                'Bucket': OUTPUT_BUCKET,
                'Prefix': 'bedrock-output/'
            }
        }
    )

    job_id = resp['JobId']

    # store mapping jobId -> TaskToken via repository
    repo = WorkflowRepository()
    repo.put_job_mapping(job_id, task_token)

    return {'status': 'started', 'jobId': job_id}
