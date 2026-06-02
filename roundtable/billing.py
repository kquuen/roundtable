"""Billing & quota enforcement for Roundtable.

Provides:
    - Plan-tier definitions (free / pro / team)
    - Quota checking dependency for FastAPI endpoints
    - Usage tracking helpers
    - Monthly reset logic
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import Depends, HTTPException, status

from roundtable.auth import User, require_user, get_user_store
from roundtable import db

logger = logging.getLogger("roundtable.billing")


# ── Plan Tiers ──

class PlanTier(str, Enum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"


_PLAN_LIMITS: dict[str, dict] = {
    PlanTier.FREE.value: {
        "monthly_quota": 3,          # 3 sessions per month
        "monthly_tokens": 50000,     # soft token cap
        "allow_export": False,
        "allow_custom_agents": False,
        "allow_batch": False,
        "max_agents_per_session": 5,
        "price_cents_monthly": 0,
    },
    PlanTier.PRO.value: {
        "monthly_quota": 9999,       # effectively unlimited
        "monthly_tokens": 500000,
        "allow_export": True,
        "allow_custom_agents": True,
        "allow_batch": False,
        "max_agents_per_session": 10,
        "price_cents_monthly": 2900,  # ¥29
    },
    PlanTier.TEAM.value: {
        "monthly_quota": 9999,
        "monthly_tokens": 2000000,
        "allow_export": True,
        "allow_custom_agents": True,
        "allow_batch": True,
        "max_agents_per_session": 20,
        "price_cents_monthly": 9900,  # ¥99
    },
}


# ── Helpers ──

def get_plan_limits(plan: str) -> dict:
    """Return limits for a given plan tier."""
    return _PLAN_LIMITS.get(plan, _PLAN_LIMITS[PlanTier.FREE.value]).copy()


def _should_reset_quota(user: User) -> bool:
    """Check if monthly quota should be reset (new month)."""
    # If quota_reset_at is missing or from a previous month, reset
    store = get_user_store()
    db_user = store.get_by_id(user.user_id)
    if not db_user:
        return False
    reset_at_str = getattr(db_user, "quota_reset_at", None)
    if not reset_at_str:
        return True
    try:
        reset_at = datetime.fromisoformat(reset_at_str)
        now = datetime.now(timezone.utc)
        return reset_at.year != now.year or reset_at.month != now.month
    except Exception:
        return True


def _maybe_reset_quota(user: User) -> User:
    """Reset monthly usage if we've crossed into a new month."""
    if _should_reset_quota(user):
        limits = get_plan_limits(getattr(user, "plan", PlanTier.FREE.value))
        db.reset_monthly_usage(user.user_id, limits["monthly_quota"])
        # Refresh user object
        store = get_user_store()
        refreshed = store.get_by_id(user.user_id)
        if refreshed:
            return User(
                user_id=refreshed.user_id,
                username=refreshed.username,
                email=refreshed.email,
                created_at=refreshed.created_at,
                custom_keys=refreshed.custom_keys,
                monthly_quota=refreshed.monthly_quota,
                monthly_used=0,
                plan=getattr(refreshed, "plan", PlanTier.FREE.value),
            )
    return user


# ── Quota Check Dependency ──

async def require_quota(
    cost: int = 1,
    user: User = Depends(require_user),
) -> User:
    """FastAPI dependency: enforce quota before expensive operations.

    Args:
        cost: How many quota units this operation consumes (default 1 = 1 session).
    """
    # Admin users bypass quota checks
    if getattr(user, "is_admin", False):
        return user

    user = _maybe_reset_quota(user)

    remaining = user.monthly_quota - user.monthly_used
    if remaining < cost:
        logger.warning(
            "Quota exceeded for user=%s plan=%s used=%s quota=%s",
            user.username, getattr(user, "plan", "free"),
            user.monthly_used, user.monthly_quota,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "quota_exceeded",
                "message": "月度配额已用完，请升级套餐或等待下月重置。",
                "monthly_used": user.monthly_used,
                "monthly_quota": user.monthly_quota,
                "remaining": remaining,
                "upgrade_url": "/user/plan",
            },
        )
    return user


# ── Usage Consumption ──

def consume_quota(user_id: str, cost: int = 1, action: str = "session",
                  session_id: str | None = None, tokens_used: int = 0) -> dict:
    """Deduct quota and log usage. Returns updated usage info."""
    store = get_user_store()
    user = store.get_by_id(user_id)
    if not user:
        raise ValueError(f"User {user_id} not found")

    user = _maybe_reset_quota(user)
    remaining = user.monthly_quota - user.monthly_used
    if remaining < cost:
        raise ValueError(
            f"Quota exceeded: used={user.monthly_used}, quota={user.monthly_quota}, "
            f"requested={cost}, remaining={remaining}"
        )
    new_used = user.monthly_used + cost
    db.update_user_usage(user_id, new_used)
    db.insert_usage_log(
        user_id=user_id,
        action=action,
        session_id=session_id,
        tokens_used=tokens_used,
        cost_cents=0,
    )
    return {
        "monthly_used": new_used,
        "monthly_quota": user.monthly_quota,
        "remaining": max(0, user.monthly_quota - new_used),
    }


# ── Plan Export Guard ──

def require_export_permission(user: User) -> None:
    """Raise 403 if user's plan does not allow export."""
    limits = get_plan_limits(getattr(user, "plan", PlanTier.FREE.value))
    if not limits.get("allow_export"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "feature_not_available",
                "message": "报告导出需要 Pro 或 Team 套餐。",
                "upgrade_url": "/user/plan",
            },
        )


# ── User-facing usage info ──

def get_user_usage_info(user_id: str) -> dict:
    """Return full usage + plan info for a user."""
    store = get_user_store()
    user = store.get_by_id(user_id)
    if not user:
        raise ValueError(f"User {user_id} not found")

    user = _maybe_reset_quota(user)
    plan = getattr(user, "plan", PlanTier.FREE.value)
    limits = get_plan_limits(plan)
    summary = db.get_monthly_usage_summary(user_id)

    return {
        "plan": plan,
        "limits": limits,
        "monthly_quota": user.monthly_quota,
        "monthly_used": user.monthly_used,
        "remaining": max(0, user.monthly_quota - user.monthly_used),
        "quota_reset_at": getattr(user, "quota_reset_at", None),
        "subscription_status": getattr(user, "subscription_status", "active"),
        "trial_expires_at": getattr(user, "trial_expires_at", None),
        "monthly_summary": summary,
    }
