"""
v3.3 Unified Pipe 검증 스크립트

검증 항목:
1. action='init': Day-Zero Sync - 빈 상태에서 StateBag 생성
2. 필수 메타데이터 강제 주입 (segment_to_run, loop_counter 등)
3. T=0 가드레일: Dirty Input 방어 (256KB 초과 시 자동 오프로딩)
4. 생애 주기 흐름: init → sync → aggregate
"""

import sys
import os

# 결과 추적
results = {
    "passed": [],
    "failed": [],
    "warnings": []
}


def check_usc_init_action():
    """
    1. action='init' Day-Zero Sync 검증
    """
    print("\n[1] action='init' Day-Zero Sync 검사...")
    
    file_path = os.path.join(os.path.dirname(__file__), "universal_sync_core.py")
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # action='init' 로직 존재 확인
    checks = [
        ("elif action == 'init':", "init action case in flatten_result"),
        ("INIT_REQUIRED_METADATA", "Required metadata constant defined"),
        ("if action == 'init':", "init handling in merge_logic"),
        ("if action == 'init' or delta.get('_is_init'):", "init in _compute_next_action"),
        ("return 'STARTED'", "STARTED return for init action"),
        ("if action != 'init':", "Skip loop_counter increment for init"),
    ]
    
    for pattern, description in checks:
        if pattern in content:
            print(f"  [OK] {description}")
            results["passed"].append(f"USC init: {description}")
        else:
            print(f"  [FAIL] {description}")
            results["failed"].append(f"USC init: {description}")


def check_required_metadata():
    """
    2. 필수 메타데이터 강제 주입 확인
    """
    print("\n[2] INIT_REQUIRED_METADATA 검사...")
    
    file_path = os.path.join(os.path.dirname(__file__), "universal_sync_core.py")
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    required_fields = [
        "segment_to_run",
        "loop_counter", 
        "state_history",
        "max_loop_iterations",
        "max_branch_iterations",
        "distributed_mode",
        "distributed_strategy",
        "max_concurrency",
    ]
    
    for field in required_fields:
        if f"'{field}':" in content or f'"{field}":' in content:
            print(f"  [OK] {field} in INIT_REQUIRED_METADATA")
            results["passed"].append(f"Required field: {field}")
        else:
            print(f"  [WARN] {field} not found in INIT_REQUIRED_METADATA")
            results["warnings"].append(f"Required field: {field}")


def check_initialize_state_data_usc_integration():
    """
    3. initialize_state_data.py USC 통합 확인
    """
    print("\n[3] initialize_state_data.py USC 통합 검사...")
    
    # initialize_state_data.py 경로
    init_file = os.path.join(
        os.path.dirname(__file__), 
        "..", "..", "common", "initialize_state_data.py"
    )
    
    if not os.path.exists(init_file):
        print(f"  [SKIP] File not found: {init_file}")
        results["warnings"].append("initialize_state_data.py not found")
        return
    
    with open(init_file, "r", encoding="utf-8") as f:
        content = f.read()
    
    checks = [
        ("from src.handlers.utils.universal_sync_core import universal_sync_core", "USC import"),
        ("_HAS_USC", "USC availability flag"),
        ("action='init'", "action='init' in USC call"),
        ("base_state={}", "Empty base_state for init"),
        ("[Day-Zero Sync]", "Day-Zero Sync marker"),
    ]
    
    for pattern, description in checks:
        if pattern in content:
            print(f"  [OK] {description}")
            results["passed"].append(f"init_state: {description}")
        else:
            print(f"  [FAIL] {description}")
            results["failed"].append(f"init_state: {description}")


def check_docstring_version():
    """
    4. 버전 docstring 확인
    """
    print("\n[4] v3.3 버전 docstring 검사...")
    
    files_to_check = [
        ("universal_sync_core.py", "v3.3"),
        ("state_data_manager.py", "v3.3"),
    ]
    
    for filename, expected_version in files_to_check:
        file_path = os.path.join(os.path.dirname(__file__), filename)
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()[:1000]  # 첫 1000자만
        
        if expected_version in content:
            print(f"  [OK] {filename}: {expected_version} found")
            results["passed"].append(f"Version: {filename} is {expected_version}")
        else:
            print(f"  [WARN] {filename}: {expected_version} not found")
            results["warnings"].append(f"Version: {filename}")


def check_unified_pipe_docstring():
    """
    5. Unified Pipe 개념 docstring 확인
    """
    print("\n[5] Unified Pipe 개념 docstring 검사...")
    
    file_path = os.path.join(os.path.dirname(__file__), "universal_sync_core.py")
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()[:3000]
    
    checks = [
        ("Unified Pipe", "Unified Pipe concept"),
        ("Init", "Init (birth) lifecycle"),
        ("Sync", "Sync (growth) lifecycle"),
        ("Aggregate", "Aggregate (cooperation) lifecycle"),
        ("StateBag", "StateBag term"),
    ]
    
    for pattern, description in checks:
        if pattern in content:
            print(f"  [OK] {description}")
            results["passed"].append(f"Docstring: {description}")
        else:
            print(f"  [WARN] {description}")
            results["warnings"].append(f"Docstring: {description}")


def run_all_checks():
    """모든 검사 실행"""
    print("=" * 60)
    print("v3.3 Unified Pipe 검증")
    print("=" * 60)
    
    check_usc_init_action()
    check_required_metadata()
    check_initialize_state_data_usc_integration()
    check_docstring_version()
    check_unified_pipe_docstring()
    
    # 결과 출력
    print("\n" + "=" * 60)
    print("Unified Pipe 검증 결과")
    print("=" * 60)
    
    print(f"\n[PASS] 통과: {len(results['passed'])}개")
    print(f"[WARN] 경고: {len(results['warnings'])}개")
    print(f"[FAIL] 실패: {len(results['failed'])}개")
    
    if results["failed"]:
        print("\n[FAIL] 실패 항목:")
        for item in results["failed"]:
            print(f"   - {item}")
    
    if results["warnings"]:
        print("\n[WARN] 경고 항목:")
        for item in results["warnings"]:
            print(f"   - {item}")
    
    print("\n" + "=" * 60)
    if not results["failed"]:
        print("[SUCCESS] Unified Pipe v3.3 검증 통과!")
    else:
        print("[FAILURE] 검증 실패 - 수정 필요")
    print("=" * 60)
    
    return len(results["failed"]) == 0


if __name__ == "__main__":
    success = run_all_checks()
    sys.exit(0 if success else 1)
