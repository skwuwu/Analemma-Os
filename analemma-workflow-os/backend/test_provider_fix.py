from src.handlers.utils.save_workflow import _validate_workflow_config

# Test config with incorrect provider
test_config = {
    'nodes': [
        {
            'id': 'test-llm-node',
            'type': 'llm_chat',
            'provider': 'openai',  # Wrong provider
            'model': 'gemini',     # Correct model
            'prompt_content': 'Test prompt',
            'temperature': 0.7,
            'max_tokens': 2000
        }
    ]
}

result, error = _validate_workflow_config(test_config)
if error:
    print(f'Validation error: {error}')
else:
    print('Validation successful')
    llm_node = None
    for node in result['nodes']:
        if node.get('type') == 'llm_chat':
            llm_node = node
            break
    
    if llm_node:
        print(f'LLM Node provider: {llm_node.get("provider")}')
        print(f'LLM Node model: {llm_node.get("model")}')