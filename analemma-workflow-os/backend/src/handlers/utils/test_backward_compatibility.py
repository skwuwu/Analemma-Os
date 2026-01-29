"""
v3.2 하위 호환성 검증 스크립트

검증 항목:
1. load_from_s3 시그니처 호환성 (기존 호출 깨지지 않음)
2. 모든 액션 반환값 구조 유지
3. state_data 필드명 일관성
4. ASL에서 사용하는 필드 존재 여부
5. universal_sync_core 통합 확인
"""

import sys
import os
import json

# 결과 추적
results = {
    "passed": [],
    "failed": [],
    "warnings": []
}


def check_load_from_s3_signature():
    """
    ✅ 1. load_from_s3 시그니처 호환성
    
    기존: load_from_s3(s3_path: str) -> Any
    v3.1: load_from_s3(s3_path: str, expected_checksum=None, max_retries=3) -> Any
    
    기존 호출자는 s3_path만 전달하므로 호환되어야 함
    """
    print("\n[1] load_from_s3 시그니처 호환성 검사...")
    
    # 모듈 임포트 없이 파일 분석
    file_path = os.path.join(
        os.path.dirname(__file__), 
        "state_data_manager.py"
    )
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 함수 시그니처 확인
    if "def load_from_s3(s3_path: str, expected_checksum: Optional[str] = None, max_retries: int = 3)" in content:
        print("  ✅ 새 시그니처에 기본값 있음 → 기존 호출 호환")
        results["passed"].append("load_from_s3 signature backward compatible")
    else:
        print("  ❌ 시그니처 확인 필요")
        results["failed"].append("load_from_s3 signature may break existing calls")
    
    # 기존 호출 패턴 존재 확인
    legacy_calls = content.count("load_from_s3(s3_path)")
    if legacy_calls > 0:
        print(f"  ⚠️ 단일 인자 호출 {legacy_calls}개 발견 (호환됨)")
        results["warnings"].append(f"{legacy_calls} legacy load_from_s3 calls")


def check_action_return_structures():
    """
    ✅ 2. 액션 반환값 구조 확인
    
    모든 액션은 다음 형태를 반환해야 함:
    - {"state_data": {...}, "next_action": "..."} 또는
    - {"state_data": {...}, "final_status": "...", ...}
    """
    print("\n[2] 액션 반환값 구조 검사...")
    
    file_path = os.path.join(
        os.path.dirname(__file__), 
        "state_data_manager.py"
    )
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 각 액션 함수가 올바른 반환 구조를 가지는지 확인
    actions = [
        "sync_state_data",
        "aggregate_branches", 
        "aggregate_distributed_results",
        "merge_callback_result",
        "merge_async_result",
        "create_snapshot",
        "sync_branch_state"
    ]
    
    for action in actions:
        # 함수 존재 확인
        if f"def {action}(event:" in content:
            # 반환 구조에 state_data 포함 확인
            func_start = content.find(f"def {action}(event:")
            func_end = content.find("\ndef ", func_start + 1)
            if func_end == -1:
                func_end = len(content)
            
            func_body = content[func_start:func_end]
            
            if "'state_data':" in func_body or '"state_data":' in func_body:
                print(f"  ✅ {action}: 올바른 반환 구조")
                results["passed"].append(f"{action} return structure OK")
            else:
                print(f"  ⚠️ {action}: state_data 반환 확인 필요")
                results["warnings"].append(f"{action} return structure check")
        else:
            print(f"  ❌ {action}: 함수 없음")
            results["failed"].append(f"{action} function missing")


def check_critical_fields():
    """
    ✅ 3. ASL에서 사용하는 필수 필드 존재 여부
    """
    print("\n[3] ASL 필수 필드 존재 확인...")
    
    file_path = os.path.join(
        os.path.dirname(__file__), 
        "state_data_manager.py"
    )
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # ASL에서 참조하는 필수 필드
    critical_fields = [
        "state_data",
        "next_action",
        "segment_to_run",
        "loop_counter",
        "state_s3_path",
        "payload_size_kb",
        "last_update_time",
        "idempotency_key",
        "execution_id"
    ]
    
    for field in critical_fields:
        # 필드가 반환값이나 업데이트에서 사용되는지 확인
        if f"'{field}'" in content or f'"{field}"' in content:
            print(f"  ✅ {field}")
            results["passed"].append(f"Field {field} present")
        else:
            print(f"  ❌ {field} 없음!")
            results["failed"].append(f"Field {field} missing")


def check_universal_sync_core():
    """
    ✅ 4. Universal Sync Core 모듈 존재 및 구조 확인
    """
    print("\n[4] Universal Sync Core 모듈 검사...")
    
    file_path = os.path.join(
        os.path.dirname(__file__), 
        "universal_sync_core.py"
    )
    
    if os.path.exists(file_path):
        print("  ✅ universal_sync_core.py 존재")
        results["passed"].append("universal_sync_core.py exists")
        
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # 핵심 함수 존재 확인
        core_functions = [
            "universal_sync_core",
            "flatten_result",
            "merge_logic",
            "optimize_and_offload",
            "StateHydrator",
            "ExponentialBackoffRetry"
        ]
        
        for func in core_functions:
            if f"def {func}" in content or f"class {func}" in content:
                print(f"    ✅ {func}")
                results["passed"].append(f"USC: {func} present")
            else:
                print(f"    ❌ {func} 없음")
                results["failed"].append(f"USC: {func} missing")
    else:
        print("  ❌ universal_sync_core.py 없음")
        results["failed"].append("universal_sync_core.py missing")


def check_p0_p2_fixes():
    """
    ✅ 5. P0~P2 수정사항 반영 확인
    """
    print("\n[5] P0~P2 수정사항 반영 확인...")
    
    file_path = os.path.join(
        os.path.dirname(__file__), 
        "state_data_manager.py"
    )
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # P0: aggregate_distributed에 오프로딩 추가 (v3.2에서는 USC에서 처리)
    usc_path = os.path.join(os.path.dirname(__file__), "universal_sync_core.py")
    with open(usc_path, "r", encoding="utf-8") as f:
        usc_content = f.read()
    
    # USC 또는 state_data_manager에서 failed_segments 오프로딩 처리 확인
    if ("failed_segments_s3_path" in content or "failed_segments_s3_path" in usc_content or 
        "_failed_segments" in usc_content):
        print("  ✅ P0: aggregate_distributed 오프로딩 (USC에서 처리)")
        results["passed"].append("P0: aggregate_distributed offloading")
    else:
        print("  ❌ P0: aggregate_distributed 오프로딩 미반영")
        results["failed"].append("P0: aggregate_distributed offloading missing")
    
    # P1: load_from_s3에 재시도 로직
    if "max_retries" in content and "Retry" in content:
        print("  ✅ P1: load_from_s3에 재시도 로직 추가")
        results["passed"].append("P1: load_from_s3 retry logic")
    else:
        print("  ❌ P1: load_from_s3 재시도 미반영")
        results["failed"].append("P1: load_from_s3 retry missing")
    
    # P1: Checksum 검증
    if "expected_checksum" in content and "hashlib.md5" in content:
        print("  ✅ P1: Checksum 검증 로직 추가")
        results["passed"].append("P1: Checksum validation")
    else:
        print("  ❌ P1: Checksum 검증 미반영")
        results["failed"].append("P1: Checksum validation missing")
    
    # P2: merge_callback/merge_async에 최적화
    merge_callback_has_optimization = (
        "merge_callback" in content and 
        "payload_size_kb" in content.split("merge_callback")[1].split("def ")[0]
    )
    if merge_callback_has_optimization:
        print("  ✅ P2: merge_callback에 자동 최적화 추가")
        results["passed"].append("P2: merge_callback optimization")
    else:
        print("  ⚠️ P2: merge_callback 최적화 확인 필요")
        results["warnings"].append("P2: merge_callback optimization check")
    
    # P2: create_snapshot 포인터 참조
    if "is_pointer_only" in content:
        print("  ✅ P2: create_snapshot 포인터 참조 모드 추가")
        results["passed"].append("P2: create_snapshot pointer mode")
    else:
        print("  ❌ P2: create_snapshot 포인터 참조 미반영")
        results["failed"].append("P2: create_snapshot pointer mode missing")


def check_wrapper_pattern():
    """
    ✅ 6. v3.2 래퍼 패턴 확인 - 액션이 universal_sync_core를 호출하는지
    """
    print("\n[6] v3.2 래퍼 패턴 확인 (universal_sync_core 호출)...")
    
    file_path = os.path.join(
        os.path.dirname(__file__), 
        "state_data_manager.py"
    )
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 래퍼로 변환된 액션들
    wrapper_actions = [
        "sync_state_data",
        "aggregate_branches",
        "merge_callback_result",
        "merge_async_result",
        "aggregate_distributed_results",
        "sync_branch_state"
    ]
    
    # universal_sync_core import 확인
    if "from .universal_sync_core import universal_sync_core" in content or \
       "from universal_sync_core import universal_sync_core" in content:
        print("  ✅ universal_sync_core import 확인")
        results["passed"].append("universal_sync_core import present")
    else:
        print("  ❌ universal_sync_core import 없음")
        results["failed"].append("universal_sync_core import missing")
    
    # 각 액션이 universal_sync_core를 호출하는지 확인
    for action in wrapper_actions:
        if f"def {action}(event:" in content:
            func_start = content.find(f"def {action}(event:")
            func_end = content.find("\ndef ", func_start + 1)
            if func_end == -1:
                func_end = len(content)
            
            func_body = content[func_start:func_end]
            
            if "universal_sync_core(" in func_body:
                print(f"  ✅ {action}: universal_sync_core 호출")
                results["passed"].append(f"{action} uses universal_sync_core")
            else:
                print(f"  ⚠️ {action}: universal_sync_core 미호출 (레거시)")
                results["warnings"].append(f"{action} does not use universal_sync_core")


def print_summary():
    """최종 결과 출력"""
    print("\n" + "=" * 60)
    print("📊 하위 호환성 검증 결과")
    print("=" * 60)
    
    print(f"\n✅ 통과: {len(results['passed'])}개")
    print(f"⚠️ 경고: {len(results['warnings'])}개")
    print(f"❌ 실패: {len(results['failed'])}개")
    
    if results["failed"]:
        print("\n❌ 실패 항목:")
        for item in results["failed"]:
            print(f"   - {item}")
    
    if results["warnings"]:
        print("\n⚠️ 경고 항목:")
        for item in results["warnings"]:
            print(f"   - {item}")
    
    print("\n" + "=" * 60)
    
    if len(results["failed"]) == 0:
        print("🎉 모든 하위 호환성 검사 통과!")
        return True
    else:
        print("⚠️ 일부 항목 수정 필요")
        return False


if __name__ == "__main__":
    print("=" * 60)
    print("🔍 v3.2 Universal Sync Core 하위 호환성 검증")
    print("=" * 60)
    
    check_load_from_s3_signature()
    check_action_return_structures()
    check_critical_fields()
    check_universal_sync_core()
    check_p0_p2_fixes()
    check_wrapper_pattern()
    
    success = print_summary()
    sys.exit(0 if success else 1)
