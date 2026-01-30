#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import boto3
import json

# Lambda client
lambda_client = boto3.client('lambda', region_name='ap-northeast-2')

# Test event for Segment 1
test_event = {
    "segment_id": 1,
    "segment_to_run": 1,
    "workflowId": "llm-test-stage1_basic",
    "ownerId": "system",
    "MOCK_MODE": "false",
    "partition_map": [
        {
            "id": 0,
            "segment_id": 0,
            "type": "normal",
            "segment_config": {
                "nodes": [
                    {"id": "start", "type": "operator_official"},
                    {"id": "prepare_input", "type": "operator_official"}
                ],
                "edges": []
            }
        },
        {
            "id": 1,
            "segment_id": 1,
            "type": "llm",
            "segment_config": {
                "nodes": [
                    {
                        "id": "llm_structured_call",
                        "type": "llm_chat",
                        "label": "LLM Structured Output 호출",
                        "config": {
                            "provider": "gemini",
                            "model": "gemini-2.0-flash",
                            "system_prompt": "You are a text summarizer. Always respond with valid JSON matching the provided schema. Be concise and accurate.",
                            "prompt_content": "Summarize this text in structured format:\n\n{{input_text}}\n\nExtract: main topic, key points (max 3), sentiment (positive/neutral/negative), word count estimate.",
                            "max_tokens": 512,
                            "temperature": 0.3,
                            "response_schema": {
                                "type": "object",
                                "properties": {
                                    "main_topic": {"type": "string"},
                                    "key_points": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                                    "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
                                    "word_count_estimate": {"type": "integer"}
                                },
                                "required": ["main_topic", "key_points", "sentiment"]
                            },
                            "output_key": "llm_raw_output"
                        }
                    }
                ],
                "edges": []
            }
        }
    ],
    "current_state": {
        "input_text": "Analemma OS is a revolutionary AI-first workflow automation platform. It enables complex business logic to be defined and executed as DAG-based workflows with LLM-powered nodes and native operators."
    },
    "workflow_config": {
        "workflow_name": "test_llm_stage1_basic",
        "nodes": [],
        "edges": []
    }
}

print("Invoking SegmentRunnerFunction with Segment 1 config...")
print(f"Event keys: {list(test_event.keys())}")
print(f"partition_map[1] keys: {list(test_event['partition_map'][1].keys())}")
print(f"partition_map[1]['segment_config'] has {len(test_event['partition_map'][1]['segment_config']['nodes'])} nodes")
print()

response = lambda_client.invoke(
    FunctionName='backend-workflow-dev-SegmentRunnerFunction-i2op6tD2ScJf',
    InvocationType='RequestResponse',
    Payload=json.dumps(test_event)
)

payload = json.loads(response['Payload'].read())

print("Response received:")
print(f"StatusCode: {response['StatusCode']}")

if 'FunctionError' in response:
    print(f"ERROR: {response['FunctionError']}")
    print(f"Error details: {json.dumps(payload, indent=2)[:1000]}")
else:
    print("Success!")
    
    # Check state_data
    state_data = payload.get('state_data', {})
    print(f"\nstate_data keys: {list(state_data.keys())[:20]}")
    
    # Check current_state
    current_state = state_data.get('current_state', {})
    print(f"current_state keys: {list(current_state.keys())}")
    
    if 'llm_raw_output' in current_state:
        print(f"\n✅ llm_raw_output FOUND!")
        llm_output = current_state['llm_raw_output']
        print(f"Output preview: {str(llm_output)[:300]}")
    else:
        print(f"\n❌ llm_raw_output NOT FOUND in current_state")
    
    # Check execution_result
    exec_result = state_data.get('execution_result', {})
    print(f"\nexecution_result keys: {list(exec_result.keys())[:15]}")
    
    if exec_result:
        exec_final_state = exec_result.get('final_state', {})
        print(f"execution_result.final_state keys: {list(exec_final_state.keys())[:15]}")
        
        if 'llm_raw_output' in exec_final_state:
            print(f"✅ llm_raw_output in execution_result.final_state!")
        else:
            print(f"❌ llm_raw_output NOT in execution_result.final_state")
    else:
        print("⚠️ execution_result is empty!")

print("\n" + "="*80)
print("Full payload preview:")
print(json.dumps(payload, indent=2, ensure_ascii=False)[:2000])
