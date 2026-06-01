"""Custom FastAPI response classes."""

from __future__ import annotations

import json as _json

from fastapi.responses import JSONResponse


class Utf8JSONResponse(JSONResponse):
    """JSON response that renders Chinese characters as-is (not \\uXXXX escapes)."""

    def render(self, content) -> bytes:
        return _json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")
