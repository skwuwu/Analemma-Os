from typing import Any, Dict, List, Optional, Union


class BaseCallbackHandler:
    """Minimal BaseCallbackHandler stub compatible with langchain_core."""
    
    # Required attributes for langchain_core.callbacks.manager compatibility
    ignore_chain: bool = False
    ignore_llm: bool = False
    ignore_retry: bool = False
    ignore_agent: bool = False
    ignore_retriever: bool = False
    ignore_chat_model: bool = False
    ignore_custom_event: bool = False
    raise_error: bool = False
    run_inline: bool = False
    
    def __init__(self, *args, **kwargs):
        pass
    
    # Chain callbacks
    def on_chain_start(self, serialized: Dict[str, Any], inputs: Dict[str, Any], **kwargs: Any) -> None:
        pass
    
    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> None:
        pass
    
    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        pass
    
    # LLM callbacks
    def on_llm_start(self, serialized: Dict[str, Any], prompts: List[str], **kwargs: Any) -> None:
        pass
    
    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        pass
    
    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        pass
    
    # Tool callbacks
    def on_tool_start(self, serialized: Dict[str, Any], input_str: str, **kwargs: Any) -> None:
        pass
    
    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        pass
    
    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        pass
    
    # Agent callbacks
    def on_agent_action(self, action: Any, **kwargs: Any) -> None:
        pass
    
    def on_agent_finish(self, finish: Any, **kwargs: Any) -> None:
        pass
    
    # Retry callback
    def on_retry(self, retry_state: Any, **kwargs: Any) -> None:
        pass
