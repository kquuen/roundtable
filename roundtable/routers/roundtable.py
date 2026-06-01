"""Core roundtable analysis endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from roundtable.auth import User, require_user
from roundtable.dependencies import get_store, get_service, require_session_owner
from roundtable.models import SessionStatus
from roundtable.services.sse import start_sse_pipeline

logger = logging.getLogger("roundtable.routers.roundtable")
router = APIRouter(prefix="/roundtable", tags=["roundtable"])


class RunRoundtableRequest(BaseModel):
    session_id: str
    agent_count: int = Field(5, ge=1, le=20)
    use_mock: bool = False
    lang: str = "zh"
    stream: bool = False


@router.post("/run")
async def run_roundtable(req: RunRoundtableRequest, user: User = Depends(require_user)):
    """Execute a full roundtable analysis using stored evidence."""
    session = require_session_owner(req.session_id, user)

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
async def run_debate(req: RunRoundtableRequest, user: User = Depends(require_user)):
    """Execute a two-round debate analysis."""
    session = require_session_owner(req.session_id, user)

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
