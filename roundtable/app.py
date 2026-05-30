"""Phase 5: FastAPI application — REST API for roundtable operations.

v0.3.0: JSON file persistence via store.py + report archiving.

Usage:
    $env:DEEPSEEK_API_KEY="sk-..."   # PowerShell
    uvicorn roundtable.app:app --reload
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from typing import Optional

try:
    __version__ = version("roundtable")
except PackageNotFoundError:
    __version__ = "0.2.0"

import json as _json

from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
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
from roundtable.config import ConfigManager
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

def _get_service() -> RoundtableService:
    """Build a service instance. Agents auto-resolve their own providers."""
    return RoundtableService(
        session_store=_store,
        report_store=_reports,
        memory_store=_memory,
    )

def _llm_enabled() -> bool:
    """Check if any LLM provider is both configured and usable."""
    cfg = ConfigManager.get()
    if not cfg.loaded:
        return False

    # At least one agent-mapped model must resolve to a provider with a non-empty API key.
    for model_ref in cfg.list_agent_models().values():
        resolved = cfg.get_model_config(model_ref)
        if not resolved:
            continue
        pconf, _ = resolved
        if pconf.protocol in {"openai", "anthropic"} and (pconf.api_key or "").strip():
            return True

    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = ConfigManager.get()
    if cfg.loaded:
        logger.info("Config loaded: %d providers, %d agent models", len(cfg.list_providers()), len(cfg.list_agent_models()))
    else:
        logger.warning("Config not loaded — running in mock mode")
    logger.info("Loaded %d persisted sessions", _store.session_count())

    # Load YAML skills from skills/ directory
    yaml_loaded = load_from_directory()
    if yaml_loaded:
        logger.info("Loaded %d YAML skills from skills/", yaml_loaded)

    yield

app = FastAPI(
    title="圆桌会议 Roundtable API",
    version=__version__,
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


# ── 前端静态文件服务 ──

_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    # API 路由已定义在前，静态文件作为 fallback
    pass  # 在所有路由定义之后挂载


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
    stream: bool = False  # 启用 SSE 流式推送


# ── Routes ──



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
    """Upload an audio file → transcribe via Whisper or MiMo → create session + evidence.

    Accepts: mp3, wav, m4a, ogg, webm
    Returns: session_id + transcript segments

    Priority:
      1. OpenAI Whisper API (requires OPENAI_API_KEY)
      2. MiMo audio understanding (requires MIMO_API_KEY)
    """
    import tempfile
    from roundtable.asr import WhisperAdapter

    # Validate file type
    ALLOWED_MIMES = {
        "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
        "audio/mp4", "audio/m4a", "audio/ogg", "audio/webm",
    }
    mime = getattr(audio, "content_type", "") or ""
    # Accept generic audio/* if specific mime not provided
    if mime and not mime.startswith("audio/") and mime not in ALLOWED_MIMES:
        raise HTTPException(400, f"Unsupported audio type: {mime}")

    # Validate size (25 MB max for Whisper; MiMo Base64 limit ~37 MB raw)
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
        # Transcribe — try Whisper first, fallback to MiMo
        result = None
        backend_used = ""

        if os.getenv("OPENAI_API_KEY"):
            try:
                adapter = WhisperAdapter(backend="whisper_api")
                result = await adapter.transcribe_async(tmp_path)
                backend_used = "whisper_api"
            except Exception as e:
                logger.warning("Whisper transcription failed: %s", e)

        if result is None and os.getenv("MIMO_API_KEY"):
            try:
                adapter = WhisperAdapter(backend="mimo")
                result = await adapter.transcribe_async(tmp_path)
                backend_used = "mimo"
            except Exception as e:
                logger.warning("MiMo transcription failed: %s", e)

        if result is None:
            return {
                "error": "transcription_failed",
                "detail": (
                    "No transcription backend available. "
                    "Set OPENAI_API_KEY (Whisper) or MIMO_API_KEY (MiMo)."
                ),
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


def _get_evidence_segments(session_id: str) -> list[dict]:
    """Fetch stored evidence or fall back to sample data."""
    segments = _store.get_evidence(session_id)
    if not segments:
        import json
        data_path = Path(__file__).resolve().parent.parent / "data" / "sample_transcript.json"
        if data_path.exists():
            segments = json.loads(data_path.read_text(encoding="utf-8")).get("segments", [])
        else:
            segments = [{"speaker": "Demo", "text": "Test segment — no evidence uploaded."}]
    return segments


async def _start_sse_pipeline(
    session_id: str,
    run_fn,
    finalize_fn,
):
    """Wrap a pipeline execution in an SSE queue and background task."""
    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues[session_id] = queue

    async def _runner():
        try:
            result = await run_fn(queue)
            await queue.put({"type": "final_report", "data": result})
        except Exception as e:
            logger.exception("[%s] Pipeline error", session_id)
            await queue.put({"type": "error", "content": str(e)})
        finally:
            await queue.put({"type": "done"})

    asyncio.create_task(_runner())
    return {"session_id": session_id, "stream_url": f"/roundtable/stream/{session_id}"}


@app.post("/roundtable/run")
async def run_roundtable(req: RunRoundtableRequest):
    """Execute a full roundtable analysis using stored evidence.

    Uses evidence uploaded via /evidence/upload.
    Report is archived to reports/ automatically.
    """
    session = _store.get(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found — create a session first")

    segments = _get_evidence_segments(req.session_id)
    _store.update_status(req.session_id, SessionStatus.ANALYZING)

    svc = _get_service()

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
        return await _start_sse_pipeline(req.session_id, _run_fn, None)

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

    segments = _get_evidence_segments(req.session_id)
    _store.update_status(req.session_id, SessionStatus.ANALYZING)

    svc = _get_service()

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
            _store.update_status(req.session_id, SessionStatus.COMPLETED)
            return result
        return await _start_sse_pipeline(req.session_id, _run_fn, None)

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
        "llm_enabled": _llm_enabled(),
    }


@app.get("/providers")
async def list_providers():
    """Return configured LLM providers and agent model mappings.

    Useful for frontends to show which models are available.
    """
    cfg = ConfigManager.get()
    providers = []
    for pid in cfg.list_providers():
        p = cfg.get_provider(pid)
        if p:
            providers.append({
                "id": p.id,
                "name": p.name,
                "protocol": p.protocol,
                "base_url": p.base_url,
                "models": [m.get("id") for m in p.models],
            })
    return {
        "providers": providers,
        "agent_models": cfg.list_agent_models(),
        "voice": cfg.get_voice_config(),
        "loaded": cfg.loaded,
    }


# ══════════════════════════════════════════════
# 实时语音通话模式：WebSocket
# ══════════════════════════════════════════════

import os

MAX_VOICE_CONCURRENT = int(os.getenv("VOICE_MAX_CONCURRENT", "50"))
_voice_semaphore = asyncio.Semaphore(MAX_VOICE_CONCURRENT)
_voice_active_count = 0


@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    """实时语音通话 WebSocket 入口。

    用户通过麦克风实时发送 PCM 音频流，后端实时识别并回复文字。

    前端协议详见 roundtable/voice/protocol.py

    Usage (概念):
        1. 连接 wss://host/ws/voice
        2. 收到 {"type": "ready", "session_id": "v_xxx"}
        3. 发送 {"type": "init", "mode": "qa", "template": "general"}
        4. 循环发送 {"type": "audio", "data": "base64pcm...", "seq": N}
        5. 收到 {"type": "transcript_final", "text": "..."}
        6. 收到 {"type": "ai_response", "text": "..."}
        7. 发送 {"type": "close"} 或断开连接
    """
    global _voice_active_count

    if _voice_semaphore.locked():
        await websocket.close(code=1013, reason="Server busy: too many voice sessions")
        return

    async with _voice_semaphore:
        await websocket.accept()
        _voice_active_count += 1
        logger.info("Voice WebSocket accepted. Active: %d/%d", _voice_active_count, MAX_VOICE_CONCURRENT)

        try:
            from roundtable.voice.session import VoiceSession

            session = VoiceSession(
                frontend_ws=websocket,
                mode="qa",
                template="general",
            )
            await session.run()

        except WebSocketDisconnect:
            logger.info("Voice WebSocket disconnected")
        except Exception as e:
            logger.error("Voice WebSocket error: %s", e, exc_info=True)
            try:
                await websocket.close(code=1011, reason=f"Internal error: {e}")
            except Exception:
                pass
        finally:
            _voice_active_count -= 1
            logger.info("Voice WebSocket closed. Active: %d/%d", _voice_active_count, MAX_VOICE_CONCURRENT)


# ══════════════════════════════════════════════
# 个人圆桌模式：新增端点
# ══════════════════════════════════════════════

import asyncio
import uuid as _uuid
from fastapi.responses import StreamingResponse
from roundtable.models import (
    QuickRequest, InterviewContext, DecisionTemplate,
    AnchoredReport, DebateMode,
)
from roundtable.debate import (
    AnchoredDebateEngine, get_interview_questions, sanitize_user_bias,
    _DEFAULT_AGENTS,
)
from roundtable.providers import ProviderRouter
from roundtable.report import compose_anchored_report


def _get_debate_provider():
    """Resolve a working LLM provider for debate engines."""
    router = ProviderRouter.get_instance()
    # Try providers in priority order
    for ref in ("deepseek/deepseek-chat", "anthropic/claude-sonnet-4-20250514", "openai/gpt-4o"):
        try:
            return router.get(ref)
        except Exception:
            continue
    return None


# SSE会话队列（session_id → asyncio.Queue）
_sse_queues: dict[str, asyncio.Queue] = {}


class InterviewStartRequest(BaseModel):
    question: str
    template: str = "general"


class InterviewAnswerRequest(BaseModel):
    session_id: str
    answers: dict  # question_id → 答案文字


@app.post("/roundtable/interview", response_class=Utf8JSONResponse)
async def start_interview(req: InterviewStartRequest):
    """
    追问阶段：用户提交问题后，系统返回2-3个追问。
    用户回答后再调 /roundtable/quick 发起辩论。
    """
    session_id = f"rt_{_uuid.uuid4().hex[:8]}"
    try:
        template = DecisionTemplate(req.template)
    except ValueError:
        template = DecisionTemplate.GENERAL

    questions = get_interview_questions(template)
    sanitized, bias = sanitize_user_bias(req.question)

    return {
        "session_id": session_id,
        "original_question": req.question,
        "sanitized_question": sanitized,
        "template": template.value,
        "questions": questions,
        "_bias_detected": bias is not None,  # 仅供调试，不展示给用户
    }


@app.post("/roundtable/quick", response_class=Utf8JSONResponse)
async def quick_roundtable(req: QuickRequest):
    """
    零门槛启动辩论（同步，等待完成后返回报告）。
    前端若需要实时流，请先调此端点获得 session_id，
    再 GET /roundtable/stream/{session_id}。
    """
    session_id = f"rt_{_uuid.uuid4().hex[:8]}"
    sanitized, bias_signal = sanitize_user_bias(req.question)

    # 整合追问上下文
    interview = InterviewContext(
        session_id=session_id,
        original_question=req.question,
        template=req.template,
        questions=[],
        answers={},
        enriched_context=(req.context or "") + f"\n原始问题：{sanitized}",
        user_bias_signal=bias_signal,
    )

    engine = AnchoredDebateEngine(provider=_get_debate_provider())
    report = await engine.run(interview, mode=req.mode)

    # 渲染Markdown报告
    md = compose_anchored_report(report)

    return {
        "session_id": session_id,
        "report": md,
        "conclusions": report.conclusions,
        "key_dispute": report.key_dispute,
        "blind_spot": report.blind_spot,
        "next_action": report.next_action,
        "specialist_stances": report.specialist_stances,
        "information_gaps": [g.model_dump() for g in report.information_gaps],
    }


@app.post("/roundtable/quick/stream-start", response_class=Utf8JSONResponse)
async def quick_roundtable_stream_start(req: QuickRequest):
    """
    启动流式辩论：返回 session_id，前端随即连接 SSE 端点。
    辩论在后台异步执行，事件通过 SSE 推送。
    """
    session_id = f"rt_{_uuid.uuid4().hex[:8]}"
    sanitized, bias_signal = sanitize_user_bias(req.question)

    interview = InterviewContext(
        session_id=session_id,
        original_question=req.question,
        template=req.template,
        questions=[],
        answers={},
        enriched_context=(req.context or "") + f"\n原始问题：{sanitized}",
        user_bias_signal=bias_signal,
    )

    # 创建事件队列
    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues[session_id] = queue

    # 后台启动辩论
    async def run_debate():
        try:
            engine = AnchoredDebateEngine(provider=_get_debate_provider())
            report = await engine.run(interview, mode=req.mode, event_queue=queue)
            md = compose_anchored_report(report)
            await queue.put({
                "type": "final_report",
                "content": md,
                "data": {
                    "conclusions": report.conclusions,
                    "key_dispute": report.key_dispute,
                    "blind_spot": report.blind_spot,
                    "next_action": report.next_action,
                    "specialist_stances": report.specialist_stances,
                },
            })
        except Exception as e:
            await queue.put({"type": "error", "content": str(e)})
        finally:
            await queue.put({"type": "done"})

    asyncio.create_task(run_debate())

    return {"session_id": session_id, "stream_url": f"/roundtable/stream/{session_id}"}


@app.get("/roundtable/stream/{session_id}")
async def stream_debate_events(session_id: str):
    """
    SSE端点：推送辩论过程的实时事件流。
    前端使用 EventSource 连接此端点。
    """
    queue = _sse_queues.get(session_id)
    if queue is None:
        raise HTTPException(404, f"No active debate stream for session {session_id}")

    import json as _j

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    yield "data: {\"type\": \"heartbeat\"}\n\n"
                    continue

                data_str = _j.dumps(event, ensure_ascii=False)
                yield f"data: {data_str}\n\n"

                if event.get("type") in ("done", "error"):
                    break
        finally:
            _sse_queues.pop(session_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/roundtable/templates", response_class=Utf8JSONResponse)
async def list_templates():
    """列出所有决策模板及其推荐Agent组合。"""
    return {
        "templates": [
            {"id": "direction", "name": "方向选择", "desc": "该不该做这个方向"},
            {"id": "feature",   "name": "功能取舍", "desc": "哪个功能先做"},
            {"id": "pricing",   "name": "定价策略", "desc": "如何定价"},
            {"id": "pivot",     "name": "转型决策", "desc": "要不要转型"},
            {"id": "partner",   "name": "合作判断", "desc": "这个合作值不值得谈"},
            {"id": "general",   "name": "通用决策", "desc": "其他类型的决策"},
        ],
        "default_agents": _DEFAULT_AGENTS,
    }


# ── 前端 SPA fallback（必须在所有 API 路由之后） ──

if _FRONTEND_DIR.is_dir():
    @app.get("/", response_class=HTMLResponse)
    async def serve_frontend():
        return (_FRONTEND_DIR / "index.html").read_text(encoding="utf-8")

    # CSS/JS 静态资源
    app.mount("/css", StaticFiles(directory=str(_FRONTEND_DIR / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(_FRONTEND_DIR / "js")), name="js")
