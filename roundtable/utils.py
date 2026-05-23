"""Utils — safe async bridging and shared helpers.

Provides run_async_safely() as the single entry point for calling
async code from synchronous functions. The original asyncio.run()
throws RuntimeError("event loop is already running") when called
inside a FastAPI handler or any existing event loop — this utility
detects that case and gives a clear message telling the caller to
use the async version instead.
"""

from __future__ import annotations

import asyncio


def run_async_safely(coro, *, name: str = ""):
    """Execute a coroutine safely — works both inside and outside an event loop.

    When NO event loop is running (CLI / scripts):
        Behaves exactly like asyncio.run(coro).

    When an event loop IS running (FastAPI handler / pytest-asyncio):
        Raises RuntimeError with a clear message pointing to the async
        alternative, instead of the cryptic "event loop is already running".

    Usage:
        # In a sync wrapper function:
        def my_sync_fn():
            return run_async_safely(
                my_async_fn(arg1, arg2),
                name="my_sync_fn — use my_async_fn instead",
            )
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop running — safe to create one
        return asyncio.run(coro)

    # Event loop is running — can't nest asyncio.run()
    label = f" ({name})" if name else ""
    raise RuntimeError(
        f"在异步上下文中调用了同步包装函数{label}。"
        f"请改用对应的异步版本（通常加 _async 后缀），"
        f"或检查调用栈确保走的是异步路径。"
    )
