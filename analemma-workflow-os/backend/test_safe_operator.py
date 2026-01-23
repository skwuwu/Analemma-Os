"""
Test script for Safe Operator Strategies.
Run with: python test_safe_operator.py
"""
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from services.operators.operator_strategies import execute_strategy, get_available_strategies, OperatorStrategy

def test_json_operations():
    """Test JSON parsing and stringifying."""
    print("\n=== JSON Operations ===")
    
    # json_parse
    result = execute_strategy('json_parse', '{"name": "test", "value": 123}', {})
    assert result == {"name": "test", "value": 123}, f"Expected dict, got {result}"
    print("[PASS] json_parse")
    
    # json_stringify
    result = execute_strategy('json_stringify', {"a": 1, "b": [1, 2]}, {})
    assert isinstance(result, str), f"Expected string, got {type(result)}"
    print("[PASS] json_stringify")
    
    # deep_get
    data = {"user": {"profile": {"name": "John"}}}
    result = execute_strategy('deep_get', data, {"path": "user.profile.name"})
    assert result == "John", f"Expected 'John', got {result}"
    print("[PASS] deep_get")
    
    # pick_fields
    data = {"name": "John", "email": "john@example.com", "password": "secret"}
    result = execute_strategy('pick_fields', data, {"fields": ["name", "email"]})
    assert result == {"name": "John", "email": "john@example.com"}, f"Unexpected result: {result}"
    assert "password" not in result, "Password should be omitted"
    print("[PASS] pick_fields")
    
    # omit_fields
    result = execute_strategy('omit_fields', data, {"fields": ["password"]})
    assert "password" not in result, "Password should be omitted"
    assert "name" in result and "email" in result, "Should keep name and email"
    print("[PASS] omit_fields")

def test_list_operations():
    """Test list manipulation strategies."""
    print("\n=== List Operations ===")
    
    items = [
        {"name": "Alice", "active": True, "price": 10},
        {"name": "Bob", "active": False, "price": 20},
        {"name": "Charlie", "active": True, "price": 30},
    ]
    
    # list_filter
    result = execute_strategy('list_filter', items, {"condition": "$.active == true"})
    assert len(result) == 2, f"Expected 2 active items, got {len(result)}"
    print("[PASS] list_filter")
    
    # list_map
    result = execute_strategy('list_map', items, {"field": "name"})
    assert result == ["Alice", "Bob", "Charlie"], f"Unexpected result: {result}"
    print("[PASS] list_map")
    
    # list_reduce (sum)
    result = execute_strategy('list_reduce', items, {"operation": "sum", "field": "price"})
    assert result == 60, f"Expected 60, got {result}"
    print("[PASS] list_reduce (sum)")
    
    # list_reduce (count)
    result = execute_strategy('list_reduce', items, {"operation": "count"})
    assert result == 3, f"Expected 3, got {result}"
    print("[PASS] list_reduce (count)")
    
    # list_reduce (avg)
    result = execute_strategy('list_reduce', items, {"operation": "avg", "field": "price"})
    assert result == 20.0, f"Expected 20.0, got {result}"
    print("[PASS] list_reduce (avg)")
    
    # list_sort
    result = execute_strategy('list_sort', items, {"field": "price", "order": "desc"})
    assert result[0]["name"] == "Charlie", f"Expected Charlie first, got {result[0]}"
    print("[PASS] list_sort")
    
    # list_first
    result = execute_strategy('list_first', items, {})
    assert result["name"] == "Alice", f"Expected Alice, got {result}"
    print("[PASS] list_first")
    
    # list_last
    result = execute_strategy('list_last', items, {})
    assert result["name"] == "Charlie", f"Expected Charlie, got {result}"
    print("[PASS] list_last")
    
    # list_slice
    result = execute_strategy('list_slice', items, {"start": 1, "end": 2})
    assert len(result) == 1 and result[0]["name"] == "Bob", f"Unexpected result: {result}"
    print("[PASS] list_slice")
    
    # list_unique
    duped = [{"id": 1}, {"id": 2}, {"id": 1}, {"id": 3}]
    result = execute_strategy('list_unique', duped, {"field": "id"})
    assert len(result) == 3, f"Expected 3 unique items, got {len(result)}"
    print("[PASS] list_unique")
    
    # list_group_by
    result = execute_strategy('list_group_by', items, {"field": "active"})
    assert "True" in result and "False" in result, f"Unexpected grouping: {result.keys()}"
    print("[PASS] list_group_by")

def test_string_operations():
    """Test string manipulation strategies."""
    print("\n=== String Operations ===")
    
    # string_template
    ctx = {"name": "World", "count": 42}
    result = execute_strategy('string_template', ctx, {"template": "Hello {{name}}! Count: {{count}}"})
    assert result == "Hello World! Count: 42", f"Unexpected result: {result}"
    print("[PASS] string_template")
    
    # string_split
    result = execute_strategy('string_split', "a,b,c", {"delimiter": ","})
    assert result == ["a", "b", "c"], f"Unexpected result: {result}"
    print("[PASS] string_split")
    
    # string_join
    result = execute_strategy('string_join', ["a", "b", "c"], {"delimiter": ", "})
    assert result == "a, b, c", f"Unexpected result: {result}"
    print("[PASS] string_join")
    
    # regex_extract
    result = execute_strategy('regex_extract', "Hello 123 World", {"pattern": r"\d+"})
    assert result == "123", f"Unexpected result: {result}"
    print("[PASS] regex_extract")
    
    # regex_replace
    result = execute_strategy('regex_replace', "Hello 123", {"pattern": r"\d+", "replacement": "XXX"})
    assert result == "Hello XXX", f"Unexpected result: {result}"
    print("[PASS] regex_replace")
    
    # string_case
    result = execute_strategy('string_case', "hello world", {"case": "upper"})
    assert result == "HELLO WORLD", f"Unexpected result: {result}"
    print("[PASS] string_case (upper)")
    
    result = execute_strategy('string_case', "HelloWorld", {"case": "snake_case"})
    assert result == "hello_world", f"Unexpected result: {result}"
    print("[PASS] string_case (snake_case)")
    
    # string_truncate
    result = execute_strategy('string_truncate', "This is a long string", {"max_length": 10, "suffix": "..."})
    assert len(result) == 10 and result.endswith("..."), f"Unexpected result: {result}"
    print("[PASS] string_truncate")

def test_type_conversions():
    """Test type conversion strategies."""
    print("\n=== Type Conversions ===")
    
    # to_int
    result = execute_strategy('to_int', "42", {})
    assert result == 42 and isinstance(result, int), f"Unexpected result: {result}"
    print("[PASS] to_int")
    
    # to_float
    result = execute_strategy('to_float', "3.14", {})
    assert result == 3.14 and isinstance(result, float), f"Unexpected result: {result}"
    print("[PASS] to_float")
    
    # to_bool
    result = execute_strategy('to_bool', "true", {})
    assert result is True, f"Unexpected result: {result}"
    result = execute_strategy('to_bool', "false", {})
    assert result is False, f"Unexpected result: {result}"
    print("[PASS] to_bool")

def test_control_flow():
    """Test control flow strategies."""
    print("\n=== Control Flow ===")
    
    # default_value
    result = execute_strategy('default_value', None, {"default": "fallback"})
    assert result == "fallback", f"Unexpected result: {result}"
    result = execute_strategy('default_value', "actual", {"default": "fallback"})
    assert result == "actual", f"Unexpected result: {result}"
    print("[PASS] default_value")
    
    # switch_case
    result = execute_strategy('switch_case', "a", {"cases": {"a": 1, "b": 2}, "default": 0})
    assert result == 1, f"Unexpected result: {result}"
    result = execute_strategy('switch_case', "x", {"cases": {"a": 1, "b": 2}, "default": 0})
    assert result == 0, f"Unexpected result: {result}"
    print("[PASS] switch_case")

def test_encoding():
    """Test encoding/crypto strategies."""
    print("\n=== Encoding/Crypto ===")
    
    # base64_encode
    result = execute_strategy('base64_encode', "hello", {})
    assert result == "aGVsbG8=", f"Unexpected result: {result}"
    print("[PASS] base64_encode")
    
    # base64_decode
    result = execute_strategy('base64_decode', "aGVsbG8=", {})
    assert result == "hello", f"Unexpected result: {result}"
    print("[PASS] base64_decode")
    
    # hash_sha256
    result = execute_strategy('hash_sha256', "test", {})
    assert len(result) == 64, f"Expected 64 char hash, got {len(result)}"
    print("[PASS] hash_sha256")
    
    # uuid_generate
    result = execute_strategy('uuid_generate', None, {})
    assert len(result) == 36 and "-" in result, f"Unexpected UUID: {result}"
    print("[PASS] uuid_generate")

def test_math_operations():
    """Test math strategies."""
    print("\n=== Math Operations ===")
    
    # math_round
    result = execute_strategy('math_round', 3.14159, {"decimals": 2})
    assert result == 3.14, f"Unexpected result: {result}"
    print("[PASS] math_round")
    
    # math_floor
    result = execute_strategy('math_floor', 3.9, {})
    assert result == 3, f"Unexpected result: {result}"
    print("[PASS] math_floor")
    
    # math_ceil
    result = execute_strategy('math_ceil', 3.1, {})
    assert result == 4, f"Unexpected result: {result}"
    print("[PASS] math_ceil")
    
    # math_clamp
    result = execute_strategy('math_clamp', 150, {"min": 0, "max": 100})
    assert result == 100, f"Unexpected result: {result}"
    result = execute_strategy('math_clamp', -50, {"min": 0, "max": 100})
    assert result == 0, f"Unexpected result: {result}"
    print("[PASS] math_clamp")
    
    # math_percent
    result = execute_strategy('math_percent', 25, {"total": 100})
    assert result == 25.0, f"Unexpected result: {result}"
    print("[PASS] math_percent")

def test_utility():
    """Test utility strategies."""
    print("\n=== Utility ===")
    
    # echo (passthrough)
    result = execute_strategy('echo', {"test": "value"}, {})
    assert result == {"test": "value"}, f"Unexpected result: {result}"
    print("[PASS] echo")
    
    # timestamp
    result = execute_strategy('timestamp', None, {"format": "iso"})
    assert "T" in result, f"Expected ISO format, got {result}"
    print("[PASS] timestamp")

def main():
    """Run all tests."""
    print("=" * 50)
    print("Safe Operator Strategy Tests")
    print("=" * 50)
    
    strategies = get_available_strategies()
    print(f"\nTotal strategies available: {len(strategies)}")
    
    try:
        test_json_operations()
        test_list_operations()
        test_string_operations()
        test_type_conversions()
        test_control_flow()
        test_encoding()
        test_math_operations()
        test_utility()
        
        print("\n" + "=" * 50)
        print("ALL TESTS PASSED!")
        print("=" * 50)
        
    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
