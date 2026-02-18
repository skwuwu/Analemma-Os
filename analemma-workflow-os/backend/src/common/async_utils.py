"""
Async utilities for Lambda handlers

Provides unified async event loop management for Lambda container reuse optimization.
"""
import asyncio
import logging
from typing import Optional, Callable, Any

logger = logging.getLogger(__name__)

# Global event loop for Lambda container reuse
_global_loop: Optional[asyncio.AbstractEventLoop] = None


def get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    """
    Get or create an event loop for async operations in Lambda.
    
    Lambda containers can be reused, so we cache the event loop globally
    to avoid creating a new one on every invocation.
    
    Returns:
        asyncio.AbstractEventLoop: Running event loop
    
    Note:
        Falls back to creating a new loop if the global loop is closed or unavailable.
    """
    global _global_loop
    
    try:
        # Try to get the current running loop first
        loop = asyncio.get_running_loop()
        _global_loop = loop
        return loop
    except RuntimeError:
        # No running loop - use or create global loop
        if _global_loop is None or _global_loop.is_closed():
            try:
                _global_loop = asyncio.get_event_loop()
            except RuntimeError:
                _global_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_global_loop)
        
        return _global_loop


def safe_run_async(coro: Callable[..., Any], *args, **kwargs) -> Any:
    """
    Safely run an async coroutine in a Lambda environment.
    
    Handles both cases:
    - When called from within an async context (uses current loop)
    - When called from sync context (creates/reuses event loop)
    
    Args:
        coro: Async coroutine function to execute
        *args: Positional arguments for the coroutine
        **kwargs: Keyword arguments for the coroutine
    
    Returns:
        Result of the coroutine execution
    
    Example:
        async def my_async_func(x, y):
            return x + y
        
        result = safe_run_async(my_async_func, 1, 2)
    """
    try:
        # Try to get running loop (if we're already in async context)
        loop = asyncio.get_running_loop()
        # If we're already in a loop, we can't use run_until_complete
        # Instead, create a task
        return loop.create_task(coro(*args, **kwargs))
    except RuntimeError:
        # No running loop - safe to use run_until_complete
        loop = get_or_create_event_loop()
        return loop.run_until_complete(coro(*args, **kwargs))
