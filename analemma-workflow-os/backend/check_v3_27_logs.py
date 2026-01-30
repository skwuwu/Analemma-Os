#!/usr/bin/env python3
import boto3
from datetime import datetime, timedelta
import time

logs = boto3.client('logs', region_name='ap-northeast-2')

# SegmentRunnerFunction log group
log_group = '/aws/lambda/backend-workflow-dev-SegmentRunnerFunction-i2op6tD2ScJf'

# Get logs from last 5 minutes
start_time = int((datetime.now() - timedelta(minutes=5)).timestamp() * 1000)

# Search for v3.27 logs
query = '''
fields @timestamp, @message
| filter @message like /v3.27/ or @message like /Extracted segment_config/ or @message like /Calling run_workflow/ or @message like /llm_raw_output/
| sort @timestamp desc
| limit 50
'''

print('Starting CloudWatch Logs query for v3.27.1 logs...')
response = logs.start_query(
    logGroupName=log_group,
    startTime=start_time,
    endTime=int(datetime.now().timestamp() * 1000),
    queryString=query
)

query_id = response['queryId']
print(f'Query ID: {query_id}')

for i in range(15):
    time.sleep(2)
    result = logs.get_query_results(queryId=query_id)
    status = result['status']
    
    if status == 'Complete':
        print(f'\nFound {len(result["results"])} log entries\n')
        
        if len(result["results"]) == 0:
            print("No logs found! Trying broader search...")
            # Try again with broader filter
            query2 = '''
            fields @timestamp, @message
            | filter @message like /segment/ or @message like /config/
            | sort @timestamp desc
            | limit 20
            '''
            response2 = logs.start_query(
                logGroupName=log_group,
                startTime=start_time,
                endTime=int(datetime.now().timestamp() * 1000),
                queryString=query2
            )
            time.sleep(3)
            result2 = logs.get_query_results(queryId=response2['queryId'])
            
            if result2['status'] == 'Complete':
                print(f'Broader search found {len(result2["results"])} entries:\n')
                for entry in result2['results'][:10]:
                    msg = next((f['value'] for f in entry if f['field'] == '@message'), '')
                    print(f'{msg[:400]}\n---')
        else:
            for entry in result['results']:
                timestamp = next((f['value'] for f in entry if f['field'] == '@timestamp'), '')
                msg = next((f['value'] for f in entry if f['field'] == '@message'), '')
                
                if timestamp:
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    time_str = dt.strftime('%H:%M:%S')
                else:
                    time_str = 'N/A'
                
                print(f'[{time_str}] {msg[:600]}')
                print('---')
        
        break
    
    if status in ['Failed', 'Cancelled']:
        print(f'Query failed: {status}')
        break
    
    if i % 3 == 0:
        print(f'  Waiting... ({status})')

if status == 'Running':
    print('Query timeout')
