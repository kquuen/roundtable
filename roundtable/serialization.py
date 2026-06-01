"""Safe JSON serialization utilities for Roundtable.

Handles datetime, date, Decimal, and Pydantic models transparently.
"""

from __future__ import annotations

import json
from datetime import datetime, date
from decimal import Decimal
from typing import Any


class SafeJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles common non-serializable types."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return str(obj)
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "__pydantic_model__"):
            return obj.model_dump()
        return super().default(obj)


def safe_json_dumps(obj: Any, **kwargs) -> str:
    """Serialize *obj* to JSON, handling datetime/date/Decimal/Pydantic models.

    Defaults: ensure_ascii=False, allow_nan=False.
    """
    defaults = {"ensure_ascii": False, "allow_nan": False}
    defaults.update(kwargs)
    return json.dumps(obj, cls=SafeJSONEncoder, **defaults)
