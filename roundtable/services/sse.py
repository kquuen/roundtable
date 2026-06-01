"""SSE (Server-Sent Events) pipeline management.

Centralises queue/key state and the unified start_sse_pipeline entry point.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from typing import Callable, Awaitable, Optional

logger = logging.getLogger("roundtable.sse")

# SSE session state
_sse_queues: dict[str, asyncio.Queue] = {}
_sse_keys: dict[str, str] = {}
_sse_lock = asyncio.Lock()
_sse_tasks: dict[str, asyncio.Task] = {}


async def start_sse_pipeline(
    session_id: str,
    run_fn: Callable[[asyncio.Queue], Awaitable[dict]],
    finalize_fn: Optional[Callable[[], Awaitable[None]]] = None,
    queue_maxsize: int = 1000,
) -> dict:
    """Wrap a pipeline execution in an SSE queue and background task.

    Returns {"session_id": ..., "stream_url": ...} for the client to connect.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
    stream_key = _uuid.uuid4().hex

    async with _sse_lock:
        _sse_queues[session_id] = queue
        _sse_keys[session_id] = stream_key

    async def _runner() -> None:
        try:
            result = await run_fn(queue)
            await queue.put({"type": "final_report", "data": result})
        except Exception:
            logger.exception("[%s] Pipeline error", session_id)
            await queue.put({"type": "error", "content": "Processing failed"})
        finally:
            await queue.put({"type": "done"})
            async with _sse_lock:
                _sse_keys.pop(session_id, None)
            if finalize_fn:
                try:
                    await finalize_fn()
                except Exception:
                    logger.exception("[%s] Finalize error", session_id)

    task = asyncio.create_task(_runner())
    _sse_tasks[session_id] = task
    return {
        "session_id": session_id,
        "stream_url": f"/roundtable/stream/{session_id}?key={stream_key}",
    }


async def cancel_sse_pipeline(session_id: str) -> None:
    """Cancel a running SSE pipeline and clean up state."""
    task = _sse_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    async with _sse_lock:
        _sse_queues.pop(session_id, None)
        _sse_keys.pop(session_id, None)


def get_sse_queue(session_id: str) -> asyncio.Queue | None:
    return _sse_queues.get(session_id)


def get_sse_key(session_id: str) -> str | None:
    return _sse_keys.get(session_id)


def validate_stream_key(session_id: str, key: str) -> bool:
    return bool(key and _sse_keys.get(session_id) == key)


def acquire_sse_lock():
    return _sse_lock


def pop_sse_queue(session_id: str) -> asyncio.Queue | None:
    return _sse_queues.pop(session_id, None)
