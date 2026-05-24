"""Utils 模块测试 — run_async_safely 事件循环安全桥接。"""

import asyncio

import pytest

from roundtable.utils import run_async_safely


class TestRunAsyncSafely:
    """验证 run_async_safely 在有无事件循环时的行为。"""

    def test_runs_coroutine_when_no_loop_running(self):
        """无事件循环时：正常执行协程并返回结果。"""
        async def add(a, b):
            return a + b

        result = run_async_safely(add(1, 2))
        assert result == 3

    def test_returns_coroutine_result(self):
        """验证协程返回值正确传递。"""
        async def greet(name):
            return f"Hello, {name}"

        result = run_async_safely(greet("World"))
        assert result == "Hello, World"

    def test_name_appears_in_error_message(self):
        """在有事件循环时调用，错误消息应包含 name 参数。"""

        async def inner():
            async def dummy():
                return 42
            with pytest.raises(RuntimeError) as exc_info:
                run_async_safely(dummy(), name="my_sync_fn — use my_async_fn instead")
            msg = str(exc_info.value)
            assert "my_sync_fn" in msg
            assert "my_async_fn" in msg

        asyncio.run(inner())

    def test_raises_clear_error_when_loop_running(self):
        """有事件循环时：应抛出清晰的 RuntimeError，而非 'event loop is already running'。"""

        async def inner():
            async def dummy():
                return 42
            with pytest.raises(RuntimeError) as exc_info:
                run_async_safely(dummy())
            msg = str(exc_info.value)
            # 不应该包含原始 asyncio.run 的晦涩报错
            assert "already running" not in msg
            # 应该指出问题本质
            assert "异步上下文" in msg

        asyncio.run(inner())

    def test_no_name_argument_is_fine(self):
        """不传 name 参数时也能正常工作（无事件循环）。"""
        async def noop():
            return "ok"

        result = run_async_safely(noop())
        assert result == "ok"

    def test_no_name_error_still_clear(self):
        """不传 name 参数时，有事件循环的错误消息也应包含基本提示。"""

        async def inner():
            async def dummy():
                return 42
            with pytest.raises(RuntimeError) as exc_info:
                run_async_safely(dummy())
            msg = str(exc_info.value)
            assert "同步包装函数" in msg

        asyncio.run(inner())
