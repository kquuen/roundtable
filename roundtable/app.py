"""Phase 5: FastAPI application — REST API for roundtable operations.

v0.3.0: JSON file persistence via store.py + report archiving.

Usage:
    $env:DEEPSEEK_API_KEY="sk-..."   # PowerShell
    uvicorn roundtable.app:app --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Optional

import json as _json

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
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

from roundtable.models import SessionStatus, SessionMode, ReviewResult, AgentReview, SupervisorReview
from roundtable.evidence import build_evidence_packet
from roundtable.team import classify_session, recommend_teams
from roundtable.providers import get_provider, ProviderAdapter
from roundtable.store import SessionStore, ReportStore
from roundtable.memory import MemoryStore
from roundtable.services import RoundtableService
from roundtable.skills import load_from_directory, reload_skills, list_skills
from roundtable.logging_config import setup_logging
from roundtable.feedback import (
    UserVerdict, UserCorrection,
    process_user_verdict, apply_bulk_verdicts,
    process_user_correction, process_user_answer,
    update_memory_confirmation, get_pending_items,
)

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
        logger.warning("Provider init failed — running in mock mode", exc_info=True)
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
    version=version("roundtable"),
    description="AI 专家圆桌工作台后端 API — LLM 驱动 + 持久化",
    lifespan=lifespan,
    default_response_class=Utf8JSONResponse,
)

# CORS — allow all origins in dev, restrict via env var in production
_allowed_origins = os.getenv(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5173",
).split(",")

if "*" in _allowed_origins and True:  # allow_credentials=True below
    logger.warning(
        "CORS: allow_origins='*' is incompatible with allow_credentials=True. "
        "Browsers will reject credentialed requests from cross-origin pages. "
        "Set CORS_ALLOWED_ORIGINS to specific origins in production."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    lang: str = "zh"  # "zh" or "en"


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


@app.post("/speak")
async def speak(audio: UploadFile = File(...)):
    """Upload an audio file → transcribe via Whisper → create session + evidence.

    Accepts: mp3, wav, m4a, ogg, webm
    Returns: session_id + transcript segments

    Requires: OPENAI_API_KEY (for Whisper API)
    """
    import tempfile
    from roundtable.asr import WhisperAdapter
    from roundtable.store import SessionStore

    # Validate file type
    ALLOWED_MIMES = {
        "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
        "audio/mp4", "audio/m4a", "audio/ogg", "audio/webm",
    }
    mime = getattr(audio, "content_type", "") or ""
    # Accept generic audio/* if specific mime not provided
    if mime and not mime.startswith("audio/") and mime not in ALLOWED_MIMES:
        raise HTTPException(400, f"Unsupported audio type: {mime}")

    # Validate size (25 MB max)
    MAX_AUDIO_BYTES = 25 * 1024 * 1024
    content = await audio.read()
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(
            400,
            f"Audio file too large: {len(content) / 1024 / 1024:.1f} MB "
            f"(max {MAX_AUDIO_BYTES / 1024 / 1024:.0f} MB)",
        )

    # Save uploaded file to temp
    suffix = Path(audio.filename or "audio.mp3").suffix or ".mp3"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        # Transcribe
        adapter = WhisperAdapter(backend="whisper_api")
        try:
            result = await adapter.transcribe_async(tmp_path)
        except Exception as e:
            logger.warning("Whisper transcription failed: %s", e)
            return {
                "error": "transcription_failed",
                "detail": str(e)[:300],
                "filename": audio.filename,
            }

        # Create session
        title = audio.filename or "语音输入"
        session = _store.create(
            title=title,
            mode="personal_roundtable",
        )
        sid = session.session_id

        # Upload segments as evidence
        segments = [
            {"speaker": seg.speaker, "text": seg.text}
            for seg in result.segments
        ]
        if segments:
            _store.store_evidence(sid, segments)
            _store.update_status(sid, SessionStatus.TRANSCRIBING)

        return {
            "session_id": sid,
            "filename": audio.filename,
            "language": result.language,
            "duration": round(result.duration, 1),
            "model": result.model_used,
            "segment_count": len(result.segments),
            "segments": segments,
            "status": "transcribed — ready for /roundtable/run",
        }

    finally:
        # Clean up temp file
        try:
            tmp_path.unlink()
        except OSError:
            pass


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
        lang=req.lang,
    )

    # After analysis, wait for user review — don't complete yet
    if result.pending_confirmation_count > 0:
        _store.update_status(req.session_id, SessionStatus.REVIEWING)
    else:
        _store.update_status(req.session_id, SessionStatus.COMPLETED)

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


@app.post("/roundtable/debate")
async def run_debate(req: RunRoundtableRequest):
    """Execute a two-round debate analysis (Phase 6).

    Uses the same evidence as /roundtable/run but routes through
    the DebateEngine for Round 1 (independent) + Round 2 (peer review).
    """
    session = _store.get(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found — create a session first")

    segments = _store.get_evidence(req.session_id)
    if not segments:
        import json
        data_path = Path(__file__).resolve().parent.parent / "data" / "sample_transcript.json"
        if data_path.exists():
            segments = json.loads(data_path.read_text(encoding="utf-8")).get("segments", [])
        else:
            segments = [{"speaker": "Demo", "text": "Test segment — no evidence uploaded."}]

    _store.update_status(req.session_id, SessionStatus.ANALYZING)

    provider = None if req.use_mock else _provider
    svc = _get_service(provider)

    result = await svc.run_debate_pipeline(
        session_id=req.session_id,
        segments=segments,
        mode=session.mode,
        title=session.title,
        agent_count=req.agent_count,
        lang=req.lang,
    )

    _store.update_status(req.session_id, SessionStatus.COMPLETED)
    return result


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

@app.get("/memory/search")
async def search_memory(q: str = "", limit: int = 20):
    """Keyword search across all memory entries."""
    if not q:
        return {"results": [], "query": ""}
    results = _memory.search(q, limit=limit)
    return {"query": q, "result_count": len(results), "results": results}


@app.get("/memory/{session_id}")
async def get_memory(session_id: str):
    """Get auto-written memory entries for a session."""
    entries = _memory.get(session_id)
    return {
        "session_id": session_id,
        "entry_count": len(entries),
        "entries": entries,
    }


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


# ── 人在回路：用户反馈、裁决、记忆确认 ──

class ConfirmReviewRequest(BaseModel):
    session_id: str
    verdicts: list[dict]  # [{claim_id, decision: confirm|reject|retype, new_type?, note?}]


class FeedbackRequest(BaseModel):
    session_id: str
    corrections: list[dict] = []  # [{target, correction, reason}]
    answers: list[dict] = []      # [{question, answer}]


class MemoryConfirmRequest(BaseModel):
    session_id: str
    memory_id: str
    confirmed: bool


@app.get("/session/{session_id}/pending")
async def get_pending(session_id: str):
    """获取当前 session 中需要用户裁决的所有待办项。

    读取 /roundtable/run 时持久化的 reviews，而非重新分析。
    """
    s = _store.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")

    ar_dicts, sr_dicts = _store.get_reviews(session_id)
    if not ar_dicts or not sr_dicts:
        return {"session_id": session_id, "pending": [], "status": s.status.value}

    # Reconstruct from stored dicts
    agent_reviews = [AgentReview(**d) for d in ar_dicts]
    supervisor_reviews = [SupervisorReview(**d) for d in sr_dicts]

    pending = get_pending_items(supervisor_reviews, agent_reviews)

    return {
        "session_id": session_id,
        "status": s.status.value,
        "pending_count": len(pending),
        "pending": pending,
    }


@app.post("/review/confirm")
async def confirm_review(req: ConfirmReviewRequest):
    """用户对 NEEDS_CONFIRMATION 的 claim 提交裁决。

    读取 /roundtable/run 时持久化的 reviews，应用用户裁决。
    所有裁决处理完毕后，将 session 状态变为 COMPLETED。
    """
    s = _store.get(req.session_id)
    if not s:
        raise HTTPException(404, "Session not found")

    ar_dicts, sr_dicts = _store.get_reviews(req.session_id)
    if not ar_dicts or not sr_dicts:
        raise HTTPException(400, "No reviews found — run /roundtable/run first")

    # Reconstruct from stored dicts (no re-analysis)
    agent_reviews = [AgentReview(**d) for d in ar_dicts]
    supervisor_reviews = [SupervisorReview(**d) for d in sr_dicts]

    # 解析并应用用户裁决
    verdicts = []
    errors = []
    for v_dict in req.verdicts:
        try:
            verdicts.append(UserVerdict.from_dict(v_dict))
        except ValueError as e:
            errors.append({"input": v_dict, "error": str(e)})

    if errors:
        raise HTTPException(400, f"Invalid verdicts: {errors}")

    result = apply_bulk_verdicts(verdicts, supervisor_reviews, agent_reviews)

    # Persist updated reviews so verdicts survive restarts
    _store.store_reviews(req.session_id, agent_reviews, supervisor_reviews)

    # 所有待确认项都处理完后，标记完成
    remaining_pending = sum(
        1 for sr in supervisor_reviews
        if sr.review_result == ReviewResult.NEEDS_USER_CONFIRMATION
    )
    if remaining_pending == 0:
        _store.update_status(req.session_id, SessionStatus.COMPLETED)

    # 重新生成报告
    from roundtable.report import compose_report
    report = compose_report(agent_reviews, supervisor_reviews, session_title=s.title)

    return {
        "session_id": req.session_id,
        "verdicts_applied": result["applied"],
        "verdicts_failed": result["failed"],
        "remaining_pending": remaining_pending,
        "status": "completed" if remaining_pending == 0 else "reviewing",
        "report": report,
        "details": result["details"],
    }


@app.post("/session/{session_id}/feedback")
async def submit_feedback(session_id: str, req: FeedbackRequest):
    """提交用户反馈：纠正系统推断 + 回答问题。"""
    s = _store.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")

    correction_results = []
    for c_dict in req.corrections:
        try:
            corr = UserCorrection.from_dict(c_dict)
            r = process_user_correction(corr)
            correction_results.append(r)
        except Exception as e:
            correction_results.append({"error": str(e), "input": c_dict})

    answer_results = []
    for a_dict in req.answers:
        r = process_user_answer(
            session_id,
            a_dict.get("question", ""),
            a_dict.get("answer", ""),
        )
        answer_results.append(r)

    return {
        "session_id": session_id,
        "corrections_recorded": len([r for r in correction_results if r.get("recorded")]),
        "answers_recorded": len([r for r in answer_results if r.get("recorded")]),
        "corrections": correction_results,
        "answers": answer_results,
    }


@app.post("/memory/confirm")
async def confirm_memory(req: MemoryConfirmRequest):
    """用户确认或驳回一条自动写入的记忆条目。"""
    result = update_memory_confirmation(
        _memory, req.session_id, req.memory_id, req.confirmed,
    )
    if not result.get("updated"):
        raise HTTPException(404, "Memory entry not found or could not be updated")
    return result


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
