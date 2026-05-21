"""Phase 5: FastAPI application — REST API for roundtable operations.

Usage: uvicorn roundtable.app:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from roundtable.models import (
    Session, SessionMode, SessionStatus,
)
from roundtable.evidence import build_evidence_packet
from roundtable.orchestrator import run_orchestrator
from roundtable.supervisor import review_claims
from roundtable.report import compose_report
from roundtable.team import classify_session, recommend_teams

app = FastAPI(
    title="圆桌会议 Roundtable API",
    version="0.1.0",
    description="AI 专家圆桌工作台后端 API",
)

# In-memory session store (POC)
_sessions: dict[str, Session] = {}


# ── Request models ──

class CreateSessionRequest(BaseModel):
    title: str = ""
    mode: str = "meeting"


class UploadEvidenceRequest(BaseModel):
    session_id: str
    segments: list[dict]  # [{"speaker": "...", "text": "..."}]


class RunRoundtableRequest(BaseModel):
    session_id: str
    agent_count: int = 5


# ── Routes ──

@app.get("/")
def root():
    return {"service": "roundtable", "version": "0.1.0"}


@app.post("/session/create", status_code=201)
def create_session(req: CreateSessionRequest):
    """Create a new analysis session."""
    sid = f"s_{len(_sessions) + 1:03d}"
    session = Session(
        session_id=sid,
        mode=SessionMode(req.mode),
        title=req.title,
        status=SessionStatus.RECORDING,
    )
    _sessions[sid] = session
    return session.model_dump()


@app.get("/session/{session_id}")
def get_session(session_id: str):
    """Get session details."""
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s.model_dump()


@app.post("/evidence/upload")
def upload_evidence(req: UploadEvidenceRequest):
    """Upload meeting text segments and build evidence packet."""
    if req.session_id not in _sessions:
        raise HTTPException(404, "Session not found")
    session = _sessions[req.session_id]
    evidence = build_evidence_packet(req.session_id, session.mode, req.segments)
    session.status = SessionStatus.TRANSCRIBING
    return {
        "session_id": req.session_id,
        "chunk_count": len(evidence.transcript_chunks),
        "evidence": evidence.model_dump(),
    }


@app.post("/roundtable/run")
def run_roundtable(req: RunRoundtableRequest):
    """Execute a full roundtable analysis."""
    if req.session_id not in _sessions:
        raise HTTPException(404, "Session not found")
    session = _sessions[req.session_id]

    # For demo: use hardcoded sample segments
    from pathlib import Path
    import json
    data_path = Path(__file__).resolve().parent.parent / "data" / "sample_transcript.json"
    if data_path.exists():
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        segments = data.get("segments", [])
    else:
        segments = [{"speaker": "Demo", "text": "Test segment"}]

    session.status = SessionStatus.ANALYZING
    evidence = build_evidence_packet(req.session_id, session.mode, segments)
    agent_reviews = run_orchestrator(evidence, agent_count=req.agent_count)
    reviews = review_claims(agent_reviews, evidence, mode=session.mode)
    report = compose_report(agent_reviews, reviews, session_title=session.title)
    session.status = SessionStatus.COMPLETED

    return {"session_id": req.session_id, "report": report}


@app.post("/team/recommend")
def recommend_team(req: UploadEvidenceRequest):
    """Recommend expert teams based on session content."""
    evidence = build_evidence_packet(req.session_id, "meeting", req.segments)
    session_type = classify_session(evidence)
    teams = recommend_teams(session_type)
    return {
        "session_type": session_type,
        "recommended_teams": [
            {"team_id": t.team_id, "name": t.name, "capability_scores": t.capability_scores}
            for t in teams
        ],
    }


@app.get("/health")
def health_check():
    return {"status": "ok", "sessions": len(_sessions)}
