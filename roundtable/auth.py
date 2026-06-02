"""Authentication — JWT + bcrypt for Roundtable API.

Provides:
    - User registration & login
    - Password hashing with bcrypt
    - JWT token generation & validation
    - FastAPI dependency: get_current_user
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field, field_validator
import json as _json

from roundtable.db import (
    init_db, create_user, get_user_by_username, get_user_by_id,
    update_user_custom_keys, list_all_users, _from_json as _db_from_json,
)

# JWT config
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError(
        "JWT_SECRET environment variable must be set. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "168"))  # 7 days default

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


# ── Models ──

class User(BaseModel):
    user_id: str
    username: str
    email: str
    created_at: datetime
    custom_keys: dict = Field(default_factory=dict)
    monthly_quota: int = 50000
    monthly_used: int = 0
    plan: str = "free"
    trial_expires_at: Optional[str] = None
    subscription_status: str = "active"
    quota_reset_at: Optional[str] = None


class UserInDB(User):
    hashed_password: str
    custom_keys: dict = Field(default_factory=dict)
    monthly_quota: int = 50000
    monthly_used: int = 0
    plan: str = "free"
    trial_expires_at: Optional[str] = None
    subscription_status: str = "active"
    quota_reset_at: Optional[str] = None


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class UserRegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    email: str = Field(min_length=5, max_length=128)
    password: str = Field(min_length=6, max_length=128)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email format")
        return v


class UserLoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


# ── Password hashing ──

def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ── JWT ──

def _create_access_token(user_id: str, username: str) -> str:
    import jwt as _jwt

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRE_HOURS),
        "jti": uuid.uuid4().hex,
    }
    return _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> Optional[dict]:
    import jwt as _jwt

    try:
        return _jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except _jwt.ExpiredSignatureError:
        return None
    except _jwt.InvalidTokenError:
        return None
    except Exception:
        import logging
        logging.getLogger("roundtable.auth").exception("Unexpected JWT decode error")
        return None


# ── SQLite-backed user store ──

class UserStore:
    """SQLite-backed user store. Replaces JSON file storage."""

    def __init__(self, base_dir: str = "data/users"):
        from pathlib import Path
        init_db()
        self._json_path = Path(base_dir) / "users.json"
        self._migrate_from_json_if_needed()

    def _migrate_from_json_if_needed(self) -> None:
        """One-time migration from legacy JSON file to SQLite."""
        if not self._json_path.exists():
            return
        try:
            data = _json.loads(self._json_path.read_text(encoding="utf-8"))
            existing = {u["username"] for u in list_all_users()}
            for u in data.get("users", []):
                if u["username"] in existing:
                    continue
                create_user(
                    user_id=u["user_id"],
                    username=u["username"],
                    email=u["email"],
                    hashed_password=u["hashed_password"],
                    custom_keys=_db_from_json(u.get("custom_keys"), {}),
                    monthly_quota=u.get("monthly_quota", 50000),
                    monthly_used=u.get("monthly_used", 0),
                )
            # Rename JSON file to prevent re-migration
            self._json_path.rename(self._json_path.with_suffix(".json.migrated"))
        except Exception:
            logger.exception("User JSON migration failed")

    @staticmethod
    def _row_to_user(row: dict) -> UserInDB:
        return UserInDB(
            user_id=row["user_id"],
            username=row["username"],
            email=row["email"],
            created_at=datetime.fromisoformat(row["created_at"]),
            hashed_password=row["hashed_password"],
            custom_keys=_db_from_json(row.get("custom_keys"), {}),
            monthly_quota=row.get("monthly_quota", 50000),
            monthly_used=row.get("monthly_used", 0),
            plan=row.get("plan", "free"),
            trial_expires_at=row.get("trial_expires_at"),
            subscription_status=row.get("subscription_status", "active"),
            quota_reset_at=row.get("quota_reset_at"),
        )

    def get_by_username(self, username: str) -> Optional[UserInDB]:
        row = get_user_by_username(username.lower().strip())
        if not row:
            return None
        return self._row_to_user(row)

    def get_by_id(self, user_id: str) -> Optional[UserInDB]:
        row = get_user_by_id(user_id)
        if not row:
            return None
        return self._row_to_user(row)

    def create(self, username: str, email: str, password: str) -> User:
        username = username.lower().strip()
        if get_user_by_username(username):
            raise ValueError("Username already exists")

        user_id = f"u_{uuid.uuid4().hex}"  # 16 bytes entropy
        now = datetime.now(timezone.utc)
        create_user(
            user_id=user_id,
            username=username,
            email=email,
            hashed_password=_hash_password(password),
            custom_keys={},
            monthly_quota=50000,
            monthly_used=0,
        )
        return User(
            user_id=user_id,
            username=username,
            email=email,
            created_at=now,
            custom_keys={},
            monthly_quota=3,
            monthly_used=0,
            plan="free",
            trial_expires_at=None,
            subscription_status="active",
            quota_reset_at=None,
        )

    def authenticate(self, username: str, password: str) -> Optional[User]:
        user = self.get_by_username(username.lower().strip())
        if not user:
            return None
        if not _verify_password(password, user.hashed_password):
            return None
        return User(
            user_id=user.user_id,
            username=user.username,
            email=user.email,
            created_at=user.created_at,
            custom_keys=user.custom_keys,
            monthly_quota=user.monthly_quota,
            monthly_used=user.monthly_used,
            plan=user.plan,
            trial_expires_at=user.trial_expires_at,
            subscription_status=user.subscription_status,
            quota_reset_at=user.quota_reset_at,
        )

    def _save(self) -> None:
        """Backward compatibility: no-op for SQLite (writes are immediate)."""
        pass


# Singleton user store
_user_store: Optional[UserStore] = None


def get_user_store() -> UserStore:
    global _user_store
    if _user_store is None:
        _user_store = UserStore()
    return _user_store


# ── FastAPI dependency ──

async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> Optional[User]:
    """Extract current user from JWT token.

    Returns None if no token provided (for endpoints that allow guests).
    Raises 401 if token is invalid or expired.
    """
    if not token:
        return None

    payload = _decode_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_user_store().get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return User(
        user_id=user.user_id,
        username=user.username,
        email=user.email,
        created_at=user.created_at,
        custom_keys=user.custom_keys,
        monthly_quota=user.monthly_quota,
        monthly_used=user.monthly_used,
        plan=user.plan,
        trial_expires_at=user.trial_expires_at,
        subscription_status=user.subscription_status,
        quota_reset_at=user.quota_reset_at,
    )


async def require_user(token: Optional[str] = Depends(oauth2_scheme)) -> User:
    """Strict dependency: user must be authenticated."""
    user = await get_current_user(token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def require_admin(user: User = Depends(require_user)) -> User:
    """Admin-only dependency."""
    from roundtable.settings import get_settings
    settings = get_settings()
    if user.username not in settings.admin_user_list:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user
