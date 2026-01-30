#!/usr/bin/env python3
"""
Test Segment 1 LLM execution locally
"""
import json
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from handlers.core.main import run_workflow

# Segment 1 config (single LLM node)
segment_config = {
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
                        "key_points": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 3
                        },
                        "sentiment": {
                            "type": "string",
                            "enum": ["positive", "neutral", "negative"]
                        },
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

initial_state = {
    "input_text": "Analemma OS is a revolutionary AI-first workflow automation platform. It enables complex business logic to be defined and executed as DAG-based workflows with LLM-powered nodes and native operators. Key features include Gemini Thinking Mode, Safe Operators with 62+ strategies, recursive StateBag for state isolation, and seamless Step Functions integration. The platform prioritizes security with Ring Protection and provides real-time observability through WebSocket notifications.",
    "MOCK_MODE": "false"  # Force real execution
}

print("=" * 80)
print("Testing Segment 1 LLM Execution")
print("=" * 80)
print()
print("Input state keys:", list(initial_state.keys()))
print("Input text length:", len(initial_state["input_text"]))
print()

try:
    result = run_workflow(
        config_json=segment_config,
        initial_state=initial_state,
        user_api_keys={},
        run_config={"user_id": "test"}
    )
    
    print("SUCCESS!")
    print()
    print("Result keys:", list(result.keys()))
    print()
    
    # Check for LLM output
    llm_raw = result.get('llm_raw_output')
    if llm_raw:
        print("✅ llm_raw_output FOUND:")
        print(json.dumps(llm_raw, indent=2, ensure_ascii=False)[:500])
    else:
        print("❌ llm_raw_output NOT FOUND")
        print()
        print("Full result:")
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str)[:2000])
        
except Exception as e:
    print("FAILED:", e)
    import traceback
    traceback.print_exc()
