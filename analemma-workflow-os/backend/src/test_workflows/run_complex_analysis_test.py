"""
Integration Test Runner for Complex Document Analysis Workflow

이 스크립트는 다음을 검증합니다:
1. Gemini 2.0 Thinking Mode 동작
2. operator_official 전략들 (list_filter, json_parse, merge_objects 등)
3. for_each 서브그래프
4. 전체 워크플로우 파이프라인

Usage:
    # MOCK_MODE로 실행 (LLM 호출 없이)
    python run_complex_analysis_test.py --mock

    # 실제 LLM 호출로 실행
    python run_complex_analysis_test.py --live

    # 특정 노드만 테스트
    python run_complex_analysis_test.py --node extract_issues
"""

import os
import sys
import json
import argparse
from pathlib import Path

# Add necessary paths for imports
backend_root = str(Path(__file__).parent.parent.parent)  # backend/
src_root = str(Path(__file__).parent.parent)  # backend/src/

# Add both paths to support different import styles
sys.path.insert(0, backend_root)  # For 'from src.xxx import'
sys.path.insert(0, src_root)       # For 'from services.xxx import'

def load_workflow():
    """Load the test workflow JSON"""
    workflow_path = Path(__file__).parent / "test_complex_document_analysis_workflow.json"
    with open(workflow_path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_sample_document():
    """Load the sample technical specification"""
    doc_path = Path(__file__).parent / "sample_tech_specification.md"
    with open(doc_path, "r", encoding="utf-8") as f:
        return f.read()

def test_operator_strategies():
    """Test individual operator strategies"""
    from services.operators.operator_strategies import execute_strategy, get_available_strategies
    
    print("\n" + "=" * 60)
    print("Testing Operator Strategies")
    print("=" * 60)
    
    # Test data simulating LLM output
    analysis_data = {
        "security_vulnerabilities": [
            {
                "id": "SEC-001",
                "title": "SQL Injection in search",
                "description": "User input directly concatenated to SQL query",
                "priority": "Critical",
                "category": "Injection",
                "affected_component": "workflow_search.py",
                "recommendation": "Use parameterized queries",
                "discovered_at": "2026-01-23"
            },
            {
                "id": "SEC-002",
                "title": "JWT validation weakness",
                "description": "Token expiry checked against client time",
                "priority": "High",
                "category": "Authentication",
                "affected_component": "api_authorizer.py",
                "recommendation": "Use server-side UTC time",
                "discovered_at": "Jan 23, 2026"
            },
            {
                "id": "SEC-003",
                "title": "CORS too permissive",
                "description": "AllowOrigin set to *",
                "priority": "Medium",
                "category": "Configuration",
                "affected_component": "template.yaml",
                "recommendation": "Restrict to specific domains",
                "discovered_at": "2026/01/23"
            }
        ],
        "optimization_suggestions": [
            {
                "id": "OPT-001",
                "title": "DynamoDB hot partition",
                "description": "Uneven key distribution",
                "priority": "High",
                "category": "Performance",
                "estimated_impact": "50% latency reduction",
                "implementation_effort": "Medium",
                "discovered_at": "2026-01-23"
            }
        ],
        "summary": {
            "total_vulnerabilities": 3,
            "total_suggestions": 1,
            "critical_count": 1,
            "high_count": 2
        }
    }
    
    # Test 1: list_filter - High priority vulnerabilities
    print("\n[Test 1] list_filter - High priority vulnerabilities")
    result = execute_strategy(
        "list_filter",
        analysis_data["security_vulnerabilities"],
        {"condition": "$.priority in ['Critical', 'High']"}
    )
    print(f"  Input: {len(analysis_data['security_vulnerabilities'])} items")
    print(f"  Output: {len(result)} high priority items")
    assert len(result) == 2, f"Expected 2, got {len(result)}"
    print("  ✅ PASSED")
    
    # Test 2: list_map - Extract titles
    print("\n[Test 2] list_map - Extract titles")
    result = execute_strategy(
        "list_map",
        analysis_data["security_vulnerabilities"],
        {"field": "title"}
    )
    print(f"  Output: {result}")
    assert len(result) == 3
    print("  ✅ PASSED")
    
    # Test 3: list_reduce - Count
    print("\n[Test 3] list_reduce - Count")
    result = execute_strategy(
        "list_reduce",
        analysis_data["security_vulnerabilities"],
        {"operation": "count"}
    )
    print(f"  Output: {result}")
    assert result == 3
    print("  ✅ PASSED")
    
    # Test 4: merge_objects
    print("\n[Test 4] merge_objects - Combine metadata")
    result = execute_strategy(
        "merge_objects",
        {"base": "data"},
        {
            "objects": [
                {"status": "COMPLETE"},
                {"version": "1.0"}
            ]
        }
    )
    print(f"  Output: {result}")
    assert result.get("status") == "COMPLETE"
    print("  ✅ PASSED")
    
    # Test 5: json_parse
    print("\n[Test 5] json_parse - Parse JSON string")
    json_str = json.dumps(analysis_data)
    result = execute_strategy("json_parse", json_str, {})
    print(f"  Parsed {len(json_str)} bytes")
    assert result["summary"]["total_vulnerabilities"] == 3
    print("  ✅ PASSED")
    
    # Test 6: timestamp
    print("\n[Test 6] timestamp - Generate ISO timestamp")
    result = execute_strategy("timestamp", None, {"format": "iso"})
    print(f"  Output: {result}")
    assert "T" in result
    print("  ✅ PASSED")
    
    # Test 7: uuid_generate
    print("\n[Test 7] uuid_generate")
    result = execute_strategy("uuid_generate", None, {})
    print(f"  Output: {result}")
    assert "-" in result
    print("  ✅ PASSED")
    
    print("\n" + "=" * 60)
    print("All operator strategy tests PASSED!")
    print("=" * 60)

def test_llm_runner(mock_mode: bool = True):
    """Test LLM runner with the document"""
    print("\n" + "=" * 60)
    print(f"Testing LLM Runner (MOCK_MODE={mock_mode})")
    print("=" * 60)
    
    # Set environment
    if mock_mode:
        os.environ["MOCK_MODE"] = "true"
    else:
        os.environ["MOCK_MODE"] = "false"
    
    # Import after setting env
    from handlers.core.main import llm_chat_runner
    
    # Load sample document
    document = load_sample_document()
    print(f"\nLoaded document: {len(document)} characters")
    
    # Prepare state
    state = {
        "hydrated_document": document,
        "step_history": []
    }
    
    # Prepare config (from workflow)
    workflow = load_workflow()
    extract_node = next(n for n in workflow["nodes"] if n["id"] == "extract_issues")
    config = extract_node["config"].copy()
    config["id"] = "extract_issues"
    
    print(f"\nExecuting LLM node: {extract_node['label']}")
    print(f"  Provider: {config.get('provider', 'gemini')}")
    print(f"  Model: {config.get('model', 'N/A')}")
    print(f"  Thinking Mode: {config.get('enable_thinking', False)}")
    
    try:
        result = llm_chat_runner(state, config)
        output_key = config.get("output_key", "llm_analysis_raw")
        
        print(f"\n  Result key: {output_key}")
        print(f"  Output preview: {str(result.get(output_key, ''))[:200]}...")
        
        if "usage" in result:
            usage = result["usage"]
            print(f"\n  Token Usage:")
            print(f"    Input: {usage.get('input_tokens', 'N/A')}")
            print(f"    Output: {usage.get('output_tokens', 'N/A')}")
        
        print("\n  ✅ LLM Runner test PASSED")
        return result
        
    except Exception as e:
        print(f"\n  ❌ LLM Runner test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return None

def test_full_pipeline(mock_mode: bool = True):
    """Test the full workflow pipeline"""
    print("\n" + "=" * 60)
    print(f"Testing Full Pipeline (MOCK_MODE={mock_mode})")
    print("=" * 60)
    
    # Set environment
    os.environ["MOCK_MODE"] = "true" if mock_mode else "false"
    
    # Import runners
    from handlers.core.main import NODE_REGISTRY
    from services.operators.operator_strategies import execute_strategy
    
    # Load workflow
    workflow = load_workflow()
    document = load_sample_document()
    
    # Initialize state
    state = {
        "document_content": document,
        "step_history": []
    }
    
    print(f"\nWorkflow: {workflow.get('workflow_name')}")
    print(f"Nodes: {len(workflow['nodes'])}")
    print(f"Edges: {len(workflow['edges'])}")
    
    # Execute nodes in order (simplified - doesn't follow edges exactly)
    executed_nodes = []
    for node in workflow["nodes"]:
        node_id = node["id"]
        node_type = node["type"]
        config = node.get("config", {}).copy()
        config["id"] = node_id
        
        print(f"\n[{len(executed_nodes)+1}/{len(workflow['nodes'])}] Executing: {node_id} ({node_type})")
        
        try:
            if node_type == "operator_official":
                strategy = config.get("strategy")
                input_key = config.get("input_key")
                input_data = state.get(input_key) if input_key else config.get("input")
                params = config.get("params", {})
                
                result = execute_strategy(strategy, input_data, params)
                output_key = config.get("output_key", f"{node_id}_output")
                state[output_key] = result
                print(f"  Strategy: {strategy} -> {output_key}")
                
            elif node_type == "llm_chat" and mock_mode:
                # Mock LLM response
                output_key = config.get("output_key", f"{node_id}_output")
                state[output_key] = json.dumps({
                    "security_vulnerabilities": [
                        {"id": "SEC-001", "title": "SQL Injection", "priority": "Critical", "category": "Injection"},
                        {"id": "SEC-002", "title": "JWT weakness", "priority": "High", "category": "Auth"}
                    ],
                    "optimization_suggestions": [
                        {"id": "OPT-001", "title": "Cache optimization", "priority": "High", "category": "Performance"}
                    ],
                    "summary": {"total_vulnerabilities": 2, "total_suggestions": 1, "critical_count": 1, "high_count": 2}
                })
                print(f"  [MOCK] Generated mock LLM response")
                
            elif node_type == "llm_chat":
                runner = NODE_REGISTRY.get("llm_chat")
                if runner:
                    result = runner(state, config)
                    state.update(result)
                    print(f"  [LIVE] LLM executed")
                    
            elif node_type == "for_each":
                # Simplified for_each mock
                input_list = state.get(config.get("input_list_key", []), [])
                output_key = config.get("output_key", "for_each_results")
                state[output_key] = [f"Analyzed item {i}" for i in range(len(input_list) if input_list else 2)]
                print(f"  [MOCK] for_each processed {len(state[output_key])} items")
            
            executed_nodes.append(node_id)
            
        except Exception as e:
            print(f"  ⚠️  Error: {e}")
    
    print("\n" + "=" * 60)
    print(f"Pipeline Execution Complete")
    print(f"  Nodes executed: {len(executed_nodes)}/{len(workflow['nodes'])}")
    print(f"  Final state keys: {list(state.keys())}")
    print("=" * 60)
    
    return state

def main():
    parser = argparse.ArgumentParser(description="Complex Document Analysis Workflow Test Runner")
    parser.add_argument("--mock", action="store_true", help="Run in MOCK_MODE (no real LLM calls)")
    parser.add_argument("--live", action="store_true", help="Run with real LLM calls")
    parser.add_argument("--node", type=str, help="Test specific node only")
    parser.add_argument("--operators-only", action="store_true", help="Only test operator strategies")
    parser.add_argument("--llm-only", action="store_true", help="Only test LLM runner")
    
    args = parser.parse_args()
    
    # Default to mock mode
    mock_mode = not args.live
    
    print("=" * 60)
    print("Complex Document Analysis Workflow - Integration Test")
    print("=" * 60)
    print(f"Mode: {'MOCK' if mock_mode else 'LIVE'}")
    
    if args.operators_only:
        test_operator_strategies()
    elif args.llm_only:
        test_llm_runner(mock_mode)
    else:
        # Run all tests
        test_operator_strategies()
        test_llm_runner(mock_mode)
        test_full_pipeline(mock_mode)
    
    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)

if __name__ == "__main__":
    main()
