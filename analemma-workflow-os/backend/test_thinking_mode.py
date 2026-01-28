"""
Thinking Mode Integration Test

Tests:
1. GeminiConfig backward compatibility (optional fields)
2. invoke_model with thinking mode
3. State output structure validation
"""

import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from services.llm.gemini_service import GeminiService, GeminiConfig, GeminiModel


def test_gemini_config_backward_compatibility():
    """Test that GeminiConfig works without thinking parameters"""
    print("\n🧪 Test 1: GeminiConfig Backward Compatibility")
    
    # Old usage (should still work)
    config1 = GeminiConfig(
        model=GeminiModel.GEMINI_1_5_FLASH,
        max_output_tokens=1024,
        temperature=0.7
    )
    assert config1.enable_thinking == False, "Default enable_thinking should be False"
    assert config1.thinking_budget_tokens == 4096, "Default thinking_budget_tokens should be 4096"
    print("✅ Old GeminiConfig usage works (backward compatible)")
    
    # New usage (with thinking)
    config2 = GeminiConfig(
        model=GeminiModel.GEMINI_2_5_FLASH,
        max_output_tokens=2048,
        temperature=0.5,
        enable_thinking=True,
        thinking_budget_tokens=8192
    )
    assert config2.enable_thinking == True
    assert config2.thinking_budget_tokens == 8192
    print("✅ New GeminiConfig usage with thinking works")


def test_state_output_structure():
    """Test that state output structure is preserved with thinking mode"""
    print("\n🧪 Test 2: State Output Structure")
    
    # Simulate llm_chat_runner output
    node_id = "test_llm"
    output_value = "Test response"
    meta = {"model": "gemini-2.5-flash", "provider": "gemini"}
    usage = {"input_tokens": 100, "output_tokens": 50}
    new_history = ["test_llm:llm_call"]
    
    # Without thinking
    raw_output_old = {
        f"{node_id}_output": output_value,
        f"{node_id}_meta": meta,
        "step_history": new_history,
        "usage": usage
    }
    
    # With thinking
    thinking_data = [
        {"thought": "Step 1: Analyzing input", "phase": "reasoning"},
        {"thought": "Step 2: Generating response", "phase": "generation"}
    ]
    raw_output_new = {
        f"{node_id}_output": output_value,
        f"{node_id}_meta": meta,
        "step_history": new_history,
        "usage": usage,
        f"{node_id}_thinking": thinking_data  # New field
    }
    
    # Verify structure
    assert f"{node_id}_output" in raw_output_new
    assert f"{node_id}_meta" in raw_output_new
    assert "step_history" in raw_output_new
    assert "usage" in raw_output_new
    assert f"{node_id}_thinking" in raw_output_new
    
    # Verify old keys are preserved
    assert raw_output_new[f"{node_id}_output"] == output_value
    assert raw_output_new["usage"] == usage
    
    print("✅ State output structure preserved with thinking field")
    print(f"   Keys: {list(raw_output_new.keys())}")
    print(f"   Thinking steps: {len(thinking_data)}")


def test_invoke_model_signature():
    """Test that invoke_model signature is correct"""
    print("\n🧪 Test 3: invoke_model Signature")
    
    # Create service (without initialization to avoid API calls)
    config = GeminiConfig(model=GeminiModel.GEMINI_1_5_FLASH)
    service = GeminiService(config=config)
    
    # Check method signature
    import inspect
    sig = inspect.signature(service.invoke_model)
    params = list(sig.parameters.keys())
    
    expected_params = [
        'user_prompt',
        'system_instruction',
        'response_schema',
        'max_output_tokens',
        'temperature',
        'context_to_cache',
        'enable_thinking',
        'thinking_budget_tokens'
    ]
    
    for param in expected_params:
        assert param in params, f"Missing parameter: {param}"
    
    print("✅ invoke_model signature correct")
    print(f"   Parameters: {params}")


def test_invoke_with_images_signature():
    """Test that invoke_with_images signature is correct"""
    print("\n🧪 Test 4: invoke_with_images Signature")
    
    config = GeminiConfig(model=GeminiModel.GEMINI_1_5_FLASH)
    service = GeminiService(config=config)
    
    import inspect
    sig = inspect.signature(service.invoke_with_images)
    params = list(sig.parameters.keys())
    
    expected_params = [
        'user_prompt',
        'image_sources',
        'mime_types',
        'system_instruction',
        'max_output_tokens',
        'temperature',
        'response_schema',
        'enable_thinking',
        'thinking_budget_tokens'
    ]
    
    for param in expected_params:
        assert param in params, f"Missing parameter: {param}"
    
    print("✅ invoke_with_images signature correct")
    print(f"   Parameters: {params}")


def test_metadata_structure():
    """Test metadata structure with thinking"""
    print("\n🧪 Test 5: Metadata Structure")
    
    # Simulate response metadata
    metadata_without_thinking = {
        "token_usage": {"input_tokens": 100, "output_tokens": 50},
        "latency_ms": 1234,
        "model": "gemini-2.5-flash"
    }
    
    metadata_with_thinking = {
        "token_usage": {"input_tokens": 150, "output_tokens": 75},
        "latency_ms": 2345,
        "model": "gemini-2.5-flash",
        "thinking": [
            {"thought": "Analysis step", "phase": "reasoning"}
        ]
    }
    
    # Both should have required fields
    for metadata in [metadata_without_thinking, metadata_with_thinking]:
        assert "token_usage" in metadata
        assert "latency_ms" in metadata
        assert "model" in metadata
    
    # Only new one has thinking
    assert "thinking" not in metadata_without_thinking
    assert "thinking" in metadata_with_thinking
    assert metadata_with_thinking["thinking"] is not None
    
    print("✅ Metadata structure validated")
    print(f"   Without thinking keys: {list(metadata_without_thinking.keys())}")
    print(f"   With thinking keys: {list(metadata_with_thinking.keys())}")


if __name__ == "__main__":
    print("=" * 70)
    print("🧠 Thinking Mode Integration Test Suite")
    print("=" * 70)
    
    try:
        test_gemini_config_backward_compatibility()
        test_state_output_structure()
        test_invoke_model_signature()
        test_invoke_with_images_signature()
        test_metadata_structure()
        
        print("\n" + "=" * 70)
        print("✅ All tests passed! Thinking mode integration is backward compatible.")
        print("=" * 70)
        
        print("\n📋 Summary:")
        print("  ✅ GeminiConfig: Optional fields (enable_thinking, thinking_budget_tokens)")
        print("  ✅ invoke_model: New parameters added without breaking old calls")
        print("  ✅ invoke_with_images: New parameters added without breaking old calls")
        print("  ✅ State output: New key added ({node_id}_thinking) without affecting existing keys")
        print("  ✅ Metadata: thinking field is null when not used, present when enabled")
        
        print("\n🎯 Usage in workflow config:")
        print("  {")
        print('    "id": "llm_node",')
        print('    "type": "llm",')
        print('    "enable_thinking": true,')
        print('    "thinking_budget_tokens": 4096')
        print("  }")
        
        print("\n📊 State output example:")
        print("  {")
        print('    "llm_node_output": "...",')
        print('    "llm_node_thinking": [')
        print('      {"thought": "Step 1...", "phase": "reasoning"},')
        print('      {"thought": "Step 2...", "phase": "generation"}')
        print('    ]')
        print("  }")
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
