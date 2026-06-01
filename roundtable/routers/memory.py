"""Memory search and retrieval endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from roundtable.auth import User, require_user
from roundtable.dependencies import get_store, get_memory, require_session_owner

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/search")
async def search_memory(q: str = "", limit: int = 20, user: User = Depends(require_user)):
    """Keyword search across user's own memory entries."""
    if not q:
        return {"results": [], "query": ""}
    # Filter to user's sessions only
    user_sessions = get_store().list_by_user(user.username, limit=1000)
    session_ids = {s.session_id for s in user_sessions}
    all_results = get_memory().search(q, limit=limit * 10)
    filtered = [r for r in all_results if r.get("session_id") in session_ids][:limit]
    return {"query": q, "result_count": len(filtered), "results": filtered}


@router.get("/{session_id}")
async def get_memory(session_id: str, user: User = Depends(require_user)):
    """Get auto-written memory entries for a session."""
    require_session_owner(session_id, user)
    entries = get_memory().get(session_id)
    return {
        "session_id": session_id,
        "entry_count": len(entries),
        "entries": entries,
    }
