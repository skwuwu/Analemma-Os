import pytest
from src.services.workflow.builder import DynamicWorkflowBuilder

def test_builder_graceful_handling_none_config():
    """
    Validation: DynamicWorkflowBuilder should not crash when initialized with None.
    It should log an error and use an empty fallback config.
    """
    builder = DynamicWorkflowBuilder(None)
    assert builder.config == {"nodes": [], "edges": [], "type": "error_fallback"}
    
    # Verify build() doesn't crash and returns fallback
    try:
        app = builder.build()
        assert app is not None
        print("Build with None config successful (Fallback Graph created)")
        
        # Verify Fallback behavior
        result = app.invoke({})
        assert result.get("status") == "FAILED"
        assert "error" in result
        print(f"Fallback Execution Result: {result}")
        
    except Exception as e:
        pytest.fail(f"Builder crashed with None config: {e}")

def test_builder_handling_empty_nodes_list():
    """
    Validation: DynamicWorkflowBuilder should handle empty nodes list without error by returning fallback.
    """
    config = {"nodes": [], "edges": []}
    builder = DynamicWorkflowBuilder(config)
    
    try:
        app = builder.build()
        assert app is not None
        result = app.invoke({})
        assert result.get("status") == "FAILED"
    except Exception as e:
        pytest.fail(f"Builder crashed with empty nodes: {e}")
