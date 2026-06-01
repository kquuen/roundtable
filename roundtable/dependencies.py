"""Shared FastAPI dependencies and service accessors.

All singleton stores and reusable Depends() callables live here
to prevent circular imports between routers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import Depends, HTTPException

from roundtable.auth import User, require_user
from roundtable.config import ConfigManager
from roundtable.models import Session
from roundtable.services import RoundtableService
from roundtable.store import SessionStore, ReportStore
from roundtable.memory import MemoryStore

logger = logging.getLogger("roundtable.dependencies")

# ── Singleton stores (survive restarts) ──
_store = SessionStore()
_reports = ReportStore()
_memory = MemoryStore()


def get_store() -> SessionStore:
    return _store


def get_reports() -> ReportStore:
    return _reports


def get_memory() -> MemoryStore:
    return _memory


def get_service() -> RoundtableService:
    """Build a service instance. Agents auto-resolve their own providers."""
    return RoundtableService(
        session_store=_store,
        report_store=_reports,
        memory_store=_memory,
    )


def require_session_owner(
    session_id: str,
    user: User = Depends(require_user),
    store: SessionStore = Depends(get_store),
) -> Session:
    s = store.get(session_id)
    if not s or s.created_by != user.username:
        raise HTTPException(404, "Session not found")
    return s


def llm_enabled() -> bool:
    """Check if any LLM provider is both configured and usable."""
    cfg = ConfigManager.get()
    if not cfg.loaded:
        return False

    for model_ref in cfg.list_agent_models().values():
        resolved = cfg.get_model_config(model_ref)
        if not resolved:
            continue
        pconf, _ = resolved
        if pconf.protocol in {"openai", "anthropic"} and (pconf.api_key or "").strip():
            return True

    return False
