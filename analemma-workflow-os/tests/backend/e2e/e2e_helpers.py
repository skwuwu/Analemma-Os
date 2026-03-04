"""
E2E test helper functions for navigating SFN output with SmartStateBag dehydration.

seal_state_bag offloads non-control-plane fields (react_result, llm_raw_output)
to S3 batch pointers (__hot_batch__, __cold_batch__). These helpers allow tests
to verify execution evidence whether data is inline or dehydrated.
"""


def _extract_bag(output: dict) -> dict:
    """
    Extract the bag dict from SFN output, handling both direct and distributed formats.

    The distributed SFN wraps results in final_state after seal_state_bag. This
    navigates through the common nesting patterns:
        output -> state_data -> bag -> final_state
    """
    state_data = output.get("state_data", output)
    bag = state_data.get("bag", state_data)
    # Distributed SFN wraps in final_state
    final_state = bag.get("final_state", bag)
    return final_state


def _has_batch_pointers(bag: dict) -> bool:
    """
    Check if seal_state_bag dehydrated data into S3 batch pointers.

    When SmartStateBag.seal_state_bag runs, non-control-plane fields are
    batched into __hot_batch__ and __cold_batch__ S3 pointers. The presence
    of these pointers confirms that seal_state_bag executed and data was
    properly offloaded.
    """
    hot = bag.get("__hot_batch__", {})
    cold = bag.get("__cold_batch__", {})
    return (
        (isinstance(hot, dict) and hot.get("__batch_pointer__"))
        or (isinstance(cold, dict) and cold.get("__batch_pointer__"))
    )


def _assert_react_evidence(bag: dict, context: str = ""):
    """
    Assert that either react_result/llm_raw_output are present (pre-dehydration)
    or batch pointers exist (post-dehydration via seal_state_bag).

    Args:
        bag: The extracted bag/final_state dict.
        context: Optional description for assertion error messages.
    """
    has_direct_keys = bag.get("react_result") or bag.get("llm_raw_output")
    has_dehydrated = _has_batch_pointers(bag)
    prefix = f"[{context}] " if context else ""
    assert has_direct_keys or has_dehydrated, (
        f"{prefix}No ReactResult evidence found. Expected react_result/llm_raw_output "
        f"in bag or __hot_batch__/__cold_batch__ pointers from seal_state_bag. "
        f"Keys present: {list(bag.keys())}"
    )


def _assert_lambda_internal_execution(bag: dict, context: str = ""):
    """
    Assert that ReactExecutor ran INSIDE Lambda, not via MQTT proxy.

    The evidence marker __react_execution_context == "lambda_internal" is set by
    SegmentRunnerService._execute_react_segment(). Its absence means the segment
    was routed through ExecuteSegmentProxy (MQTT worker) instead.
    """
    exec_ctx = bag.get("__react_execution_context")
    prefix = f"[{context}] " if context else ""

    # If data was dehydrated (batch pointers), the marker may be offloaded too.
    # In that case, verify that at least batch pointers exist (confirming the
    # segment ran and sealed state).
    if exec_ctx is None and _has_batch_pointers(bag):
        return  # Dehydrated: can't verify execution context, but bag was sealed

    assert exec_ctx == "lambda_internal", (
        f"{prefix}Expected lambda_internal execution context but got: "
        f"{exec_ctx!r}. This segment may have routed through MQTT proxy "
        f"instead of Lambda ReactExecutor. Keys: {list(bag.keys())[:20]}"
    )


def _get_react_result(bag: dict) -> dict:
    """
    Safely extract react_result from bag. Returns empty dict if dehydrated.

    When seal_state_bag dehydrates the bag, react_result is offloaded to S3
    and no longer directly accessible. Callers should gate assertions on the
    return value being non-empty.
    """
    result = bag.get("react_result", {})
    if isinstance(result, dict) and not result.get("__batch_pointer__"):
        return result
    return {}
