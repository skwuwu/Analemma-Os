import json
with open('temp_config.json', 'r', encoding='utf-8') as f:
    config_str = f.read().strip()
    if config_str.startswith('"') and config_str.endswith('"'):
        config_str = config_str[1:-1]
    config = json.loads(config_str)
    
    for node in config.get('nodes', []):
        if node.get('type') in ('llm_chat', 'aiModel'):
            print('LLM Node found:')
            print(json.dumps(node, indent=2, ensure_ascii=False))
            break