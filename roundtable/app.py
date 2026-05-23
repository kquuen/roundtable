"""Phase 5: FastAPI application — REST API for roundtable operations.

v0.3.0: JSON file persistence via store.py + report archiving.

Usage:
    $env:DEEPSEEK_API_KEY="sk-..."   # PowerShell
    uvicorn roundtable.app:app --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import json as _json

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class Utf8JSONResponse(JSONResponse):
    """JSON response that renders Chinese characters as-is (not \\uXXXX escapes)."""

    def render(self, content) -> bytes:
        return _json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")

from roundtable.models import SessionStatus, SessionMode
from roundtable.evidence import build_evidence_packet
from roundtable.team import classify_session, recommend_teams
from roundtable.providers import get_provider, ProviderAdapter
from roundtable.store import SessionStore, ReportStore
from roundtable.memory import MemoryStore
from roundtable.services import RoundtableService
from roundtable.skills import load_from_directory, reload_skills, list_skills
from roundtable.logging_config import setup_logging

setup_logging()

import logging
logger = logging.getLogger("roundtable.app")

# ── Persistence stores (survive restarts) ──

_store = SessionStore()
_reports = ReportStore()
_memory = MemoryStore()

# Provider — created at startup
_provider: Optional[ProviderAdapter] = None

# Service — unified pipeline orchestrator
_service: Optional[RoundtableService] = None


def _init_provider() -> ProviderAdapter | None:
    """Initialize the LLM provider from environment."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    try:
        return get_provider(provider="deepseek", api_key=api_key)
    except Exception:
        return None


def _get_service(provider: ProviderAdapter | None = None) -> RoundtableService:
    """Get or create the service singleton (lazy-init for tests)."""
    global _service
    if _service is None:
        _service = RoundtableService(
            provider=provider or _provider,
            session_store=_store,
            report_store=_reports,
            memory_store=_memory,
        )
    elif provider is not None and _service.provider is None:
        # Update provider if it was None before (e.g., lifespan ran without API key)
        _service.provider = provider
    return _service


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _provider, _service
    _provider = _init_provider()
    if _provider:
        logger.info("LLM provider initialized: deepseek")
    else:
        logger.warning("DEEPSEEK_API_KEY not set — running in mock mode")
    logger.info("Loaded %d persisted sessions", _store.session_count())

    # Load YAML skills from skills/ directory
    yaml_loaded = load_from_directory()
    if yaml_loaded:
        logger.info("Loaded %d YAML skills from skills/", yaml_loaded)

    _get_service(_provider)

    yield

app = FastAPI(
    title="圆桌会议 Roundtable API",
    version="0.3.0",
    description="AI 专家圆桌工作台后端 API — LLM 驱动 + 持久化",
    lifespan=lifespan,
    default_response_class=Utf8JSONResponse,
)


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
    use_mock: bool = False


# ── Routes ──

@app.get("/")
async def root():
    return {
        "service": "roundtable",
        "version": "0.3.0",
        "llm_enabled": _provider is not None,
        "sessions": _store.session_count(),
    }


@app.post("/session/create", status_code=201)
async def create_session(req: CreateSessionRequest):
    """Create a new analysis session (persisted to disk)."""
    session = _store.create(title=req.title, mode=req.mode)
    return session.model_dump()


@app.get("/session/{session_id}")
async def get_session(session_id: str):
    """Get session details."""
    s = _store.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s.model_dump()


@app.get("/session/{session_id}/reports")
async def list_reports(session_id: str):
    """List archived reports for a session."""
    s = _store.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return {
        "session_id": session_id,
        "reports": _reports.list_for_session(session_id),
    }


@app.post("/evidence/upload")
async def upload_evidence(req: UploadEvidenceRequest):
    """Upload meeting text segments (persisted to session file)."""
    if not _store.get(req.session_id):
        raise HTTPException(404, "Session not found — create a session first")

    evidence = build_evidence_packet(req.session_id, "meeting", req.segments)
    _store.store_evidence(req.session_id, req.segments)
    _store.update_status(req.session_id, SessionStatus.TRANSCRIBING)

    return {
        "session_id": req.session_id,
        "chunk_count": len(evidence.transcript_chunks),
        "status": "evidence stored — ready for /roundtable/run",
    }


@app.post("/roundtable/run")
async def run_roundtable(req: RunRoundtableRequest):
    """Execute a full roundtable analysis using stored evidence.

    Uses evidence uploaded via /evidence/upload.
    Report is archived to reports/ automatically.
    """
    session = _store.get(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found — create a session first")

    # Use stored evidence, or fall back to sample data
    segments = _store.get_evidence(req.session_id)
    if not segments:
        import json
        data_path = Path(__file__).resolve().parent.parent / "data" / "sample_transcript.json"
        if data_path.exists():
            segments = json.loads(data_path.read_text(encoding="utf-8")).get("segments", [])
        else:
            segments = [{"speaker": "Demo", "text": "Test segment — no evidence uploaded."}]

    _store.update_status(req.session_id, SessionStatus.ANALYZING)

    # Delegate to Service Layer
    provider = None if req.use_mock else _provider
    svc = _get_service(provider)

    result = await svc.run_pipeline(
        session_id=req.session_id,
        segments=segments,
        mode=session.mode,
        title=session.title,
        agent_count=req.agent_count,
    )

    _store.update_status(req.session_id, SessionStatus.COMPLETED)

    return {
        "session_id": result.session_id,
        "mode": result.mode,
        "report_path": result.report_path,
        "memories_written": result.memories_written,
        "report": result.report,
    }


@app.post("/team/recommend")
async def recommend_team(req: UploadEvidenceRequest):
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


# ── Memory ──

@app.get("/memory/{session_id}")
async def get_memory(session_id: str):
    """Get auto-written memory entries for a session."""
    entries = _memory.get(session_id)
    return {
        "session_id": session_id,
        "entry_count": len(entries),
        "entries": entries,
    }


@app.get("/memory/search")
async def search_memory(q: str = "", limit: int = 20):
    """Keyword search across all memory entries."""
    if not q:
        return {"results": [], "query": ""}
    results = _memory.search(q, limit=limit)
    return {"query": q, "result_count": len(results), "results": results}


@app.post("/skills/reload")
async def reload_skills_endpoint():
    """Hot-reload skill definitions from skills/ directory."""
    result = reload_skills()
    return {
        "status": "reloaded",
        "skills_loaded": result["loaded"],
        "total_skills": result["total"],
        "skill_ids": result["skill_ids"],
    }


@app.get("/skills")
async def list_skills_endpoint():
    """List all registered skill IDs."""
    return {"skill_ids": list_skills(), "total": len(list_skills())}


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "sessions": _store.session_count(),
        "llm_enabled": _provider is not None,
    }
