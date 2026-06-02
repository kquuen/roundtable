"""Authentication and user management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from roundtable.auth import (
    get_user_store,
    get_current_user,
    require_user,
    UserRegisterRequest,
    UserLoginRequest,
    User,
    _create_access_token,
    JWT_EXPIRE_HOURS,
)
from roundtable.dependencies import get_store

router = APIRouter(prefix="/auth", tags=["auth"])
user_router = APIRouter(prefix="/user", tags=["user"])


class ApiKeyUpdateRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    key: str = Field(min_length=10, max_length=256)


@router.post("/register", status_code=201)
async def auth_register(req: UserRegisterRequest):
    """Register a new user account."""
    try:
        user = get_user_store().create(req.username, req.email, req.password)
    except ValueError as e:
        raise HTTPException(409, str(e))
    token = _create_access_token(user.user_id, user.username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": JWT_EXPIRE_HOURS * 3600,
        "user": user.model_dump(),
    }


@router.post("/login")
async def auth_login(req: UserLoginRequest):
    """Login and obtain JWT token."""
    user = get_user_store().authenticate(req.username, req.password)
    if not user:
        raise HTTPException(401, "Invalid username or password")
    token = _create_access_token(user.user_id, user.username)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": JWT_EXPIRE_HOURS * 3600,
        "user": user.model_dump(),
    }


@router.get("/me")
async def auth_me(user: User = Depends(require_user)):
    """Get current authenticated user."""
    return user.model_dump()


# ── User sub-routes (mounted under /user) ──

@user_router.get("/api-keys")
async def list_api_keys(user: User = Depends(require_user)):
    from roundtable.api_keys import get_user_api_keys
    return {"keys": get_user_api_keys(user)}


@user_router.post("/api-keys")
async def update_api_key(req: ApiKeyUpdateRequest, user: User = Depends(require_user)):
    from roundtable.api_keys import set_user_api_key
    set_user_api_key(user, req.provider, req.key)
    return {"status": "updated", "provider": req.provider}


@user_router.delete("/api-keys/{provider}")
async def delete_api_key(provider: str, user: User = Depends(require_user)):
    from roundtable.api_keys import delete_user_api_key
    delete_user_api_key(user, provider)
    return {"status": "deleted", "provider": provider}


@user_router.get("/usage")
async def get_usage(user: User = Depends(require_user)):
    from roundtable.billing import get_user_usage_info
    return get_user_usage_info(user.user_id)


@user_router.get("/plan")
async def get_plan(user: User = Depends(require_user)):
    from roundtable.billing import get_user_usage_info, get_plan_limits
    info = get_user_usage_info(user.user_id)
    return {
        "plan": info["plan"],
        "limits": info["limits"],
        "subscription_status": info["subscription_status"],
        "trial_expires_at": info["trial_expires_at"],
        "quota_reset_at": info["quota_reset_at"],
        "monthly_used": info["monthly_used"],
        "monthly_quota": info["monthly_quota"],
        "remaining": info["remaining"],
    }


@user_router.get("/sessions")
async def list_user_sessions(
    user: User = Depends(require_user),
    limit: int = 20,
    offset: int = 0,
):
    sessions = get_store().list_by_user(user.username, limit=limit, offset=offset)
    return {
        "total": len(sessions),
        "limit": limit,
        "offset": offset,
        "sessions": [s.model_dump() for s in sessions],
    }
