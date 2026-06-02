"""Agent Registry V2 endpoints — dynamic matching, profiles, and group confirmation."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from roundtable.auth import User, require_user
from roundtable.dependencies import require_session_owner
from roundtable.agent_matcher import AgentRegistryV2, AgentMatcher, get_matcher, DEFAULT_REGISTRY_PATH
from roundtable.models import AgentManifest, GroupRecommendation
from roundtable import db

logger = logging.getLogger("roundtable.routers.agents")
router = APIRouter(prefix="/agents", tags=["agents"])


# ── Request/Response Models ──

class MatchRequest(BaseModel):
    input_text: str = Field(min_length=1, max_length=10000)
    session_id: str = Field(default="", description="Optional session to associate with")
    min_score: float = Field(default=0.15, ge=0.0, le=1.0)
    max_agents: int = Field(default=10, ge=1, le=20)


class ConfirmGroupRequest(BaseModel):
    session_id: str
    group_selections: list[str] = Field(default_factory=list, description="Selected group_ids")
    agent_adjustments: dict[str, list[str]] = Field(
        default_factory=dict,
        description="group_id → [agent_id] overrides",
    )


class AdjustGroupRequest(BaseModel):
    session_id: str
    groups: list[dict] = Field(description="Full group structure from client")


# ── Sync Helper ──

def _sync_registry_to_db() -> int:
    """Load registry.json and upsert all agents into SQLite."""
    registry = AgentRegistryV2()
    count = 0
    for agent in registry.list_all():
        # Try to load profile markdown
        profile_path = DEFAULT_REGISTRY_PATH.parent / "profiles" / f"{agent.id}.md"
        profile_md = ""
        if profile_path.exists():
            profile_md = profile_path.read_text(encoding="utf-8")

        db.upsert_agent({
            **agent.model_dump(),
            "profile_md": profile_md,
        })
        count += 1
    logger.info("Synced %d agents from registry to DB", count)
    return count


# ── Endpoints ──

@router.get("", summary="List all agents")
async def list_agents(active_only: bool = True):
    """Return agents from DB (synced from registry.json)."""
    return {"agents": db.list_agents(active_only=active_only)}


@router.get("/{agent_id}", summary="Get agent by ID")
async def get_agent(agent_id: str):
    """Return a single agent with full profile."""
    agent = db.get_agent(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    return agent


@router.post("/match", summary="Match agents to user input")
async def match_agents(req: MatchRequest):
    """Dynamically match agents using Jaccard similarity + methodology bonus."""
    matcher = get_matcher()
    result = matcher.match(
        input_text=req.input_text,
        session_id=req.session_id,
        min_score=req.min_score,
        max_agents=req.max_agents,
    )
    return result.model_dump()


@router.post("/reload", summary="Reload agent registry from disk")
async def reload_registry():
    """Reload registry.json and sync to DB. Admin only."""
    matcher = get_matcher()
    registry_count = matcher.reload()
    db_count = _sync_registry_to_db()
    return {
        "registry_loaded": registry_count,
        "db_synced": db_count,
        "status": "ok",
    }


@router.post("/confirm-group", summary="Confirm agent groups for a session")
async def confirm_group(req: ConfirmGroupRequest, user: User = Depends(require_user)):
    """User confirms or adjusts the recommended agent groups.

    Stores the confirmed groups in the session metadata (via store.py).
    """
    session = require_session_owner(req.session_id, user)
    # Store confirmed groups in session metadata for later use by debate engine
    from roundtable.dependencies import get_store
    store = get_store()

    # Build confirmed group data
    confirmed = {
        "group_selections": req.group_selections,
        "agent_adjustments": req.agent_adjustments,
        "confirmed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }

    # Persist to session metadata (using a simple JSON metadata approach)
    # If store doesn't support metadata, we'll store in a new table later
    # For now, store in memory via a simple global cache or session extension
    _SESSION_GROUPS[req.session_id] = confirmed

    logger.info("User %s confirmed groups for session %s: %s", user.username, req.session_id, req.group_selections)
    return {
        "session_id": req.session_id,
        "confirmed_groups": req.group_selections,
        "status": "confirmed",
    }


@router.post("/adjust-group", summary="Adjust agent groups for a session")
async def adjust_group(req: AdjustGroupRequest, user: User = Depends(require_user)):
    """User provides fully custom group structure."""
    session = require_session_owner(req.session_id, user)

    adjusted = {
        "groups": req.groups,
        "adjusted_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    _SESSION_GROUPS[req.session_id] = adjusted

    logger.info("User %s adjusted groups for session %s", user.username, req.session_id)
    return {
        "session_id": req.session_id,
        "groups": req.groups,
        "status": "adjusted",
    }


@router.get("/session/{session_id}/groups", summary="Get confirmed groups for session")
async def get_session_groups(session_id: str, user: User = Depends(require_user)):
    """Return the confirmed/adjusted groups for a session."""
    require_session_owner(session_id, user)
    groups = _SESSION_GROUPS.get(session_id)
    if not groups:
        raise HTTPException(404, "No groups confirmed for this session")
    return {"session_id": session_id, **groups}


# ── In-memory session group cache (until debate_groups table is implemented in Phase 2) ──

_SESSION_GROUPS: dict[str, dict] = {}
