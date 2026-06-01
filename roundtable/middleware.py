"""Global middleware and exception handlers."""

from __future__ import annotations

import logging
import traceback
import uuid
from contextvars import ContextVar

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("roundtable.middleware")

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="unknown")


async def request_id_middleware(request: Request, call_next):
    """Attach a unique request ID to every request/response."""
    req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
    request.state.request_id = req_id
    request_id_ctx.set(req_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Sanitize validation errors — don't leak internal field types."""
    req_id = getattr(request.state, "request_id", "unknown")
    logger.warning("[%s] Validation error: %s", req_id, exc.errors())
    return JSONResponse(
        status_code=422,
        content={
            "error": "Request validation failed",
            "code": "VALIDATION_ERROR",
            "request_id": req_id,
        },
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Add request_id to every HTTPException response."""
    req_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "code": f"HTTP_{exc.status_code}",
            "request_id": req_id,
        },
    )


async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all: log full traceback, return sanitized 500."""
    req_id = getattr(request.state, "request_id", "unknown")
    logger.error("[%s] Unhandled exception: %s", req_id, traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "code": "INTERNAL_ERROR",
            "request_id": req_id,
        },
    )
