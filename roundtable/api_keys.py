"""User API Key management — custom provider keys + usage tracking."""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, Field

from roundtable.auth import User, get_user_store
from roundtable.db import get_user_by_id, update_user_custom_keys, _from_json


class ApiKeyUpdateRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    key: str = Field(min_length=10, max_length=256)


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "••••"
    return key[:4] + "••••" + key[-4:]


def get_user_api_keys(user: User) -> dict:
    row = get_user_by_id(user.user_id)
    if not row:
        return {}
    custom_keys = _from_json(row.get("custom_keys"), {})
    return {k: _mask_key(v) for k, v in custom_keys.items() if v}


def set_user_api_key(user: User, provider: str, key: str) -> None:
    row = get_user_by_id(user.user_id)
    if not row:
        raise HTTPException(404, "User not found")
    custom_keys = _from_json(row.get("custom_keys"), {})
    custom_keys[provider] = key
    update_user_custom_keys(user.user_id, custom_keys)


def delete_user_api_key(user: User, provider: str) -> None:
    row = get_user_by_id(user.user_id)
    if not row:
        raise HTTPException(404, "User not found")
    custom_keys = _from_json(row.get("custom_keys"), {})
    custom_keys.pop(provider, None)
    update_user_custom_keys(user.user_id, custom_keys)
