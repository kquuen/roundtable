"""Lightweight in-memory rate limiter for FastAPI.

No external dependencies (Redis not required).
Limits are per-client-IP and auto-expire.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("roundtable.rate_limit")

# window_seconds -> max_requests
_DEFAULT_LIMITS: dict[str, tuple[int, int]] = {
    "/auth/login": (60, 5),       # 5 per minute
    "/auth/register": (60, 5),    # 5 per minute
    "/roundtable/": (60, 10),     # 10 per minute for any /roundtable/*
}

# Env override: RATE_LIMIT_DISABLED=true disables all limiting


class _RateLimitStore:
    """Thread-safe-ish in-memory store using time buckets."""

    def __init__(self) -> None:
        # client_ip -> {endpoint_key: [(timestamp, count), ...]}
        self._data: dict[str, dict[str, list[tuple[float, int]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._last_cleanup = time.time()

    def is_allowed(self, client_ip: str, endpoint_key: str, window: int, max_req: int) -> bool:
        now = time.time()
        # Periodic cleanup every 5 minutes
        if now - self._last_cleanup > 300:
            self._cleanup(now)
            self._last_cleanup = now

        bucket = self._data[client_ip][endpoint_key]
        cutoff = now - window
        # Filter old entries
        valid = [(ts, cnt) for ts, cnt in bucket if ts > cutoff]
        total = sum(cnt for _, cnt in valid)
        if total >= max_req:
            self._data[client_ip][endpoint_key] = valid
            return False
        valid.append((now, 1))
        self._data[client_ip][endpoint_key] = valid
        return True

    def _cleanup(self, now: float) -> None:
        for ip in list(self._data.keys()):
            for key in list(self._data[ip].keys()):
                self._data[ip][key] = [
                    (ts, cnt) for ts, cnt in self._data[ip][key] if ts > now - 3600
                ]
                if not self._data[ip][key]:
                    del self._data[ip][key]
            if not self._data[ip]:
                del self._data[ip]


_store = _RateLimitStore()


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _match_limit(path: str) -> Optional[tuple[int, int]]:
    for prefix, (window, max_req) in _DEFAULT_LIMITS.items():
        if path.startswith(prefix):
            return (window, max_req)
    return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        import os
        if os.getenv("RATE_LIMIT_DISABLED", "").lower() in ("1", "true", "yes"):
            return await call_next(request)

        # Skip for non-restricted paths
        path = request.url.path
        limit = _match_limit(path)
        if not limit:
            return await call_next(request)

        window, max_req = limit
        client_ip = _get_client_ip(request)
        if not _store.is_allowed(client_ip, path, window, max_req):
            logger.warning("Rate limit exceeded for %s on %s", client_ip, path)
            return Response(
                content='{"error":"rate_limited","message":"请求过于频繁，请稍后再试。"}',
                status_code=429,
                media_type="application/json",
            )
        return await call_next(request)
