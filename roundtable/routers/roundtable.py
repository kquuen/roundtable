"""Core roundtable analysis endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from typing import Optional
from roundtable.auth import User, require_user, get_current_user
from roundtable.dependencies import get_store, get_service, require_session_owner, _check_session_owner
from roundtable.models import SessionStatus
from roundtable.services.sse import start_sse_pipeline
from roundtable.billing import require_quota, consume_quota, _check_quota_sync

logger = logging.getLogger("roundtable.routers.roundtable")
router = APIRouter(prefix="/roundtable", tags=["roundtable"])


class RunRoundtableRequest(BaseModel):
    session_id: str
    agent_count: int = Field(5, ge=1, le=20)
    use_mock: bool = False
    lang: str = "zh"
    stream: bool = False


@router.post("/run")
async def run_roundtable(req: RunRoundtableRequest, user: Optional[User] = Depends(get_current_user)):
    """Execute a full roundtable analysis using stored evidence."""
    session = _check_session_owner(req.session_id, user, get_store())
    if user:
        user = _check_quota_sync(user, cost=1)

    segments = get_store().get_evidence(req.session_id) or []
    get_store().update_status(req.session_id, SessionStatus.ANALYZING)

    svc = get_service()

    if req.stream:
        async def _run_fn(queue):
            result = await svc.run_pipeline(
                session_id=req.session_id,
                segments=segments,
                mode=session.mode,
                title=session.title,
                agent_count=req.agent_count,
                lang=req.lang,
                event_queue=queue,
            )
            if result.pending_confirmation_count > 0:
                get_store().update_status(req.session_id, SessionStatus.REVIEWING)
            else:
                get_store().update_status(req.session_id, SessionStatus.COMPLETED)
            return {
                "session_id": result.session_id,
                "mode": result.mode,
                "domain": result.domain_name,
                "report_path": result.report_path,
                "memories_written": result.memories_written,
                "pending_confirmation_count": result.pending_confirmation_count,
                "status": "reviewing" if result.pending_confirmation_count > 0 else "completed",
                "report": result.report,
            }
        return await start_sse_pipeline(req.session_id, _run_fn, None)

    result = await svc.run_pipeline(
        session_id=req.session_id,
        segments=segments,
        mode=session.mode,
        title=session.title,
        agent_count=req.agent_count,
        lang=req.lang,
    )
    if user:
        consume_quota(user.user_id, cost=1, action="roundtable_run", session_id=req.session_id, tokens_used=0)

    if result.pending_confirmation_count > 0:
        get_store().update_status(req.session_id, SessionStatus.REVIEWING)
    else:
        get_store().update_status(req.session_id, SessionStatus.COMPLETED)

    return {
        "session_id": result.session_id,
        "mode": result.mode,
        "domain": result.domain_name,
        "report_path": result.report_path,
        "memories_written": result.memories_written,
        "pending_confirmation_count": result.pending_confirmation_count,
        "status": "reviewing" if result.pending_confirmation_count > 0 else "completed",
        "report": result.report,
    }


@router.post("/debate")
async def run_debate(req: RunRoundtableRequest, user: Optional[User] = Depends(get_current_user)):
    """Execute a two-round debate analysis."""
    session = _check_session_owner(req.session_id, user, get_store())
    if user:
        user = _check_quota_sync(user, cost=1)

    segments = get_store().get_evidence(req.session_id) or []
    get_store().update_status(req.session_id, SessionStatus.ANALYZING)

    svc = get_service()

    if req.stream:
        async def _run_fn(queue):
            result = await svc.run_debate_pipeline(
                session_id=req.session_id,
                segments=segments,
                mode=session.mode,
                title=session.title,
                agent_count=req.agent_count,
                lang=req.lang,
                event_queue=queue,
            )
            get_store().update_status(req.session_id, SessionStatus.COMPLETED)
            return result
        return await start_sse_pipeline(req.session_id, _run_fn, None)

    result = await svc.run_debate_pipeline(
        session_id=req.session_id,
        segments=segments,
        mode=session.mode,
        title=session.title,
        agent_count=req.agent_count,
        lang=req.lang,
    )

    get_store().update_status(req.session_id, SessionStatus.COMPLETED)
    return result


# ── V2 Structured Debate ──

class DebateV2Request(BaseModel):
    session_id: str
    stream: bool = False


@router.post("/debate-v2")
async def run_debate_v2(req: DebateV2Request, user: User = Depends(require_user)):
    """Execute a 4-step structured debate (Phase 2).

    Requires groups to be confirmed via /agents/confirm-group first.
    """
    from roundtable.debate_v2 import DebateEngineV2
    from roundtable.evidence import build_evidence_packet
    from roundtable.agent_matcher import get_matcher, AgentGroup, AgentManifest
    from roundtable.routers.agents import _SESSION_GROUPS

    session = require_session_owner(req.session_id, user)
    segments = get_store().get_evidence(req.session_id) or []
    evidence = build_evidence_packet(req.session_id, session.mode, segments)

    # Retrieve confirmed groups
    group_data = _SESSION_GROUPS.get(req.session_id)
    if not group_data:
        raise HTTPException(400, "No groups confirmed for this session. Call /agents/confirm-group first.")

    # Build AgentGroup objects from registry
    matcher = get_matcher()
    groups: list[AgentGroup] = []
    for g in group_data.get("groups", []):
        agents = [matcher.registry.get(aid) for aid in g.get("agents", [])]
        agents = [a for a in agents if a is not None]
        if agents:
            groups.append(AgentGroup(
                group_id=g.get("group_id", "g_001"),
                group_name=g.get("group_name", ""),
                topic=g.get("topic", ""),
                agents=agents,
            ))

    if not groups:
        # Fallback: use matched agents from /agents/match result stored in group selections
        raise HTTPException(400, "No valid agent groups found. Please confirm groups first.")

    engine = DebateEngineV2()

    if req.stream:
        async def _run_fn(queue):
            result = await engine.run(
                session_id=req.session_id,
                evidence=evidence,
                groups=groups,
                event_queue=queue,
            )
            get_store().update_status(req.session_id, SessionStatus.COMPLETED)
            return {
                "session_id": result.session_id,
                "steps_count": len(result.steps),
                "groups_count": len(result.groups),
                "snapshots_count": len(result.snapshots),
                "final_consensus": result.final_consensus,
            }
        return await start_sse_pipeline(req.session_id, _run_fn, None)

    result = await engine.run(
        session_id=req.session_id,
        evidence=evidence,
        groups=groups,
    )
    get_store().update_status(req.session_id, SessionStatus.COMPLETED)
    return {
        "session_id": result.session_id,
        "steps": [s.model_dump() for s in result.steps],
        "snapshots": [s.model_dump() for s in result.snapshots],
        "events": [e.model_dump() for e in result.events],
        "final_consensus": result.final_consensus,
    }
