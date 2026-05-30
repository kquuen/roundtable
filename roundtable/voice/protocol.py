"""WebSocket message protocol for real-time voice communication.

Defines all message types exchanged between frontend (App/Mini Program)
and backend via the /ws/voice WebSocket endpoint.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Literal

from pydantic import BaseModel, Field


class VoiceMessageType(str, Enum):
    """All possible message types in the voice protocol."""

    # Frontend → Backend
    INIT = "init"
    AUDIO = "audio"
    COMMIT = "commit"
    PING = "ping"
    CLOSE = "close"

    # Backend → Frontend
    READY = "ready"
    TRANSCRIPT_PARTIAL = "transcript_partial"
    TRANSCRIPT_FINAL = "transcript_final"
    STATUS = "status"
    AI_RESPONSE = "ai_response"
    REPORT = "report"
    ERROR = "error"
    PONG = "pong"


# ── Frontend → Backend Messages ──


class InitMessage(BaseModel):
    """Initialize a voice session with mode and template."""
    type: Literal["init"] = "init"
    mode: str = "personal_roundtable"  # "personal_roundtable" | "meeting" | "qa"
    template: str = "general"          # DecisionTemplate for personal_roundtable
    context: Optional[str] = None      # Optional background context


class AudioMessage(BaseModel):
    """Send a chunk of PCM audio data (Base64 encoded)."""
    type: Literal["audio"] = "audio"
    data: str = Field(description="Base64-encoded PCM 16bit 16kHz mono audio chunk")
    seq: int = Field(default=0, description="Sequence number for ordering")


class CommitMessage(BaseModel):
    """Manually signal end of current utterance (non-VAD mode)."""
    type: Literal["commit"] = "commit"


class PingMessage(BaseModel):
    """Keep-alive heartbeat from frontend."""
    type: Literal["ping"] = "ping"


class CloseMessage(BaseModel):
    """Gracefully close the voice session."""
    type: Literal["close"] = "close"
    reason: Optional[str] = None


FrontendMessage = InitMessage | AudioMessage | CommitMessage | PingMessage | CloseMessage


# ── Backend → Frontend Messages ──


class ReadyMessage(BaseModel):
    """Session is ready, audio streaming can begin."""
    type: Literal["ready"] = "ready"
    session_id: str


class TranscriptPartialMessage(BaseModel):
    """Intermediate ASR result (while user is still speaking)."""
    type: Literal["transcript_partial"] = "transcript_partial"
    text: str


class TranscriptFinalMessage(BaseModel):
    """Final ASR result for one utterance (server_vad triggered)."""
    type: Literal["transcript_final"] = "transcript_final"
    text: str
    is_final: bool = True


class StatusMessage(BaseModel):
    """State update for the frontend UI."""
    type: Literal["status"] = "status"
    state: Literal["idle", "connected", "listening", "thinking", "responding", "closed"]
    detail: Optional[str] = None


class AIResponseMessage(BaseModel):
    """AI text response to user's speech."""
    type: Literal["ai_response"] = "ai_response"
    text: str
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None


class ReportMessage(BaseModel):
    """Complete roundtable report (sent at session end)."""
    type: Literal["report"] = "report"
    report: str


class ErrorMessage(BaseModel):
    """Error notification."""
    type: Literal["error"] = "error"
    message: str
    code: Optional[str] = None


class PongMessage(BaseModel):
    """Heartbeat response."""
    type: Literal["pong"] = "pong"


BackendMessage = (
    ReadyMessage
    | TranscriptPartialMessage
    | TranscriptFinalMessage
    | StatusMessage
    | AIResponseMessage
    | ReportMessage
    | ErrorMessage
    | PongMessage
)


def parse_frontend_message(raw: dict) -> FrontendMessage:
    """Parse a raw dict into the correct frontend message type."""
    msg_type = raw.get("type")
    if msg_type == "init":
        return InitMessage(**raw)
    if msg_type == "audio":
        return AudioMessage(**raw)
    if msg_type == "commit":
        return CommitMessage(**raw)
    if msg_type == "ping":
        return PingMessage(**raw)
    if msg_type == "close":
        return CloseMessage(**raw)
    raise ValueError(f"Unknown frontend message type: {msg_type}")
