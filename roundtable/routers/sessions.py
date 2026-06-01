"""Session, evidence, and team recommendation endpoints."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from roundtable.auth import User, require_user
from roundtable.dependencies import get_store, get_reports, require_session_owner
from roundtable.evidence import build_evidence_packet
from roundtable.models import SessionStatus
from roundtable.team import classify_session, recommend_teams

logger = logging.getLogger("roundtable.routers.sessions")
router = APIRouter(tags=["sessions"])


class CreateSessionRequest(BaseModel):
    title: str = ""
    mode: str = "meeting"


class UploadEvidenceRequest(BaseModel):
    session_id: str
    segments: list[dict]


class UploadTextEvidenceRequest(BaseModel):
    session_id: str
    text: str


@router.post("/session/create", status_code=201)
async def create_session(req: CreateSessionRequest, user: User = Depends(require_user)):
    """Create a new analysis session (persisted to disk)."""
    session = get_store().create(title=req.title, mode=req.mode, created_by=user.username if user else "anonymous")
    return session.model_dump()


@router.get("/session/{session_id}")
async def get_session(session_id: str, user: User = Depends(require_user)):
    """Get session details."""
    s = require_session_owner(session_id, user)
    return s.model_dump()


@router.get("/session/{session_id}/reports")
async def list_reports(session_id: str, user: User = Depends(require_user)):
    """List archived reports for a session."""
    require_session_owner(session_id, user)
    return {
        "session_id": session_id,
        "reports": get_reports().list_for_session(session_id),
    }


@router.post("/evidence/upload")
async def upload_evidence(req: UploadEvidenceRequest, user: User = Depends(require_user)):
    """Upload meeting text segments (persisted to session file)."""
    require_session_owner(req.session_id, user)

    evidence = build_evidence_packet(req.session_id, "meeting", req.segments)
    get_store().store_evidence(req.session_id, req.segments)
    get_store().update_status(req.session_id, SessionStatus.TRANSCRIBING)

    return {
        "session_id": req.session_id,
        "chunk_count": len(evidence.transcript_chunks),
        "status": "evidence stored — ready for /roundtable/run",
    }


def _parse_text_segments(text: str) -> list[dict]:
    """Parse plain text lines into evidence segments."""
    segments: list[dict] = []
    for line in text.splitlines():
        value = line.strip()
        if not value:
            continue
        if "：" in value:
            speaker, content = value.split("：", 1)
        elif ":" in value:
            speaker, content = value.split(":", 1)
        else:
            speaker, content = "Speaker", value
        speaker = speaker.strip() or "Speaker"
        content = content.strip()
        if content:
            segments.append({"speaker": speaker[:64], "text": content})
    return segments


@router.post("/evidence/text")
async def upload_text_evidence(req: UploadTextEvidenceRequest, user: User = Depends(require_user)):
    """Upload plain TXT evidence and persist normalized transcript segments."""
    require_session_owner(req.session_id, user)

    segments = _parse_text_segments(req.text)
    if not segments:
        raise HTTPException(400, "No text evidence found")
    if len(segments) > 500:
        raise HTTPException(400, "Too many segments (max 500)")

    evidence = build_evidence_packet(req.session_id, "meeting", segments)
    get_store().store_evidence(req.session_id, segments)
    get_store().update_status(req.session_id, SessionStatus.TRANSCRIBING)

    return {
        "session_id": req.session_id,
        "chunk_count": len(evidence.transcript_chunks),
        "segments": segments,
        "status": "text evidence stored — ready for /roundtable/run",
    }


@router.post("/speak")
async def speak(
    audio: UploadFile = File(...),
    session_id: str = Form(default=""),
    user: User = Depends(require_user),
):
    """Upload an audio file → transcribe via Whisper or MiMo → create session + evidence."""
    from roundtable.asr import WhisperAdapter

    # Validate file type
    ALLOWED_MIMES = {
        "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav",
        "audio/mp4", "audio/m4a", "audio/ogg", "audio/webm",
    }
    mime = getattr(audio, "content_type", "") or ""
    if mime and not mime.startswith("audio/") and mime not in ALLOWED_MIMES:
        raise HTTPException(400, f"Unsupported audio type: {mime}")

    MAX_AUDIO_BYTES = 25 * 1024 * 1024
    content = await audio.read()
    if len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(
            400,
            f"Audio file too large: {len(content) / 1024 / 1024:.1f} MB "
            f"(max {MAX_AUDIO_BYTES / 1024 / 1024:.0f} MB)",
        )

    suffix = Path(audio.filename or "audio.mp3").suffix or ".mp3"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)

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

        store = get_store()
        existing = store.get(session_id) if session_id else None
        if existing:
            require_session_owner(session_id, user)
            sid = existing.session_id
        else:
            title = audio.filename or "语音输入"
            session = store.create(
                title=title,
                mode="personal_roundtable",
                created_by=user.username,
            )
            sid = session.session_id

        segments = [
            {"speaker": seg.speaker, "text": seg.text}
            for seg in result.segments
        ]
        if segments:
            store.store_evidence(sid, segments)
            store.update_status(sid, SessionStatus.TRANSCRIBING)

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
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


@router.post("/team/recommend")
async def recommend_team(req: UploadEvidenceRequest, user: User = Depends(require_user)):
    """Recommend expert teams based on session content."""
    require_session_owner(req.session_id, user)
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
