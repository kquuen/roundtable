"""圆桌会议 Roundtable Meeting — 后端协议模型。

Phase 0: 所有核心协议的 Pydantic v2 定义。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Session ──

class SessionMode(str, Enum):
    MEETING = "meeting"
    PERSONAL_ROUNDTABLE = "personal_roundtable"


class SessionStatus(str, Enum):
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    REVIEWING = "reviewing"
    COMPLETED = "completed"


class Session(BaseModel):
    session_id: str = Field(description="Unique session identifier, e.g. s_123")
    mode: SessionMode = Field(default=SessionMode.MEETING)
    title: str = ""
    status: SessionStatus = Field(default=SessionStatus.RECORDING)
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_by: str = "local_user"


# ── Transcript ──

class TranscriptChunk(BaseModel):
    chunk_id: str = Field(description="Stable chunk identifier, e.g. t_000482")
    session_id: str = Field(description="Parent session id")
    speaker: str = Field(default="unknown")
    start_ms: int = Field(default=0)
    end_ms: int = Field(default=0)
    text: str = Field(description="Transcribed text content")
    asr_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = Field(default="text_import")
    confirmed_by_user: bool = Field(default=False)


# ── Evidence ──

class ClaimType(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    RECOMMENDATION = "recommendation"
    EXTENSION = "extension"


class EvidenceClaim(BaseModel):
    claim_id: str = Field(description="Unique claim id, e.g. c_001")
    agent_id: str = Field(description="Which agent made this claim")
    claim_type: ClaimType = Field(default=ClaimType.INFERENCE)
    content: str = Field(description="The claim content itself")
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="Referenced TranscriptChunk ids",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: str = Field(default="pending_review")


class EvidencePacket(BaseModel):
    session_id: str
    mode: str = "meeting"
    topic_segments: List[str] = Field(default_factory=list)
    transcript_chunks: List[TranscriptChunk] = Field(default_factory=list)
    known_decisions: List[str] = Field(default_factory=list)
    known_action_items: List[str] = Field(default_factory=list)
    agent_scope: str = Field(default="analysis")
    forbidden_claims: List[str] = Field(default_factory=lambda: [
        "不要断言未在原文出现的负责人",
        "不要把推测写成会议事实",
    ])


# ── Agent ──

class AgentReview(BaseModel):
    agent_id: str
    summary: str = Field(description="Agent 对本次分析的一句话总结")
    claims: List[EvidenceClaim] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    recommended_next_actions: List[str] = Field(default_factory=list)


class SkillManifest(BaseModel):
    skill_id: str = Field(description="e.g. architect")
    name: str = Field(description="e.g. 架构师")
    version: str = "1.0.0"
    role: str = ""
    allowed_claim_types: List[str] = Field(default_factory=lambda: ["inference", "recommendation"])
    allowed_domains: List[str] = Field(default_factory=list)
    forbidden: List[str] = Field(default_factory=list)
    input_schema: str = "EvidencePacket"
    output_schema: str = "AgentReview"


# ── Review ──

class ReviewResult(str, Enum):
    APPROVED = "approved"
    DOWNGRADED = "downgraded"
    REJECTED = "rejected"
    NEEDS_USER_CONFIRMATION = "needs_user_confirmation"


class SupervisorReview(BaseModel):
    claim_id: str
    review_result: ReviewResult
    final_type: Optional[str] = None
    reason: str = ""
    required_changes: List[str] = Field(default_factory=list)


# ── Memory ──

class MemoryWrite(BaseModel):
    memory_id: str = Field(description="e.g. mem_001")
    session_id: str
    memory_type: str = Field(description="decision / insight / action_item")
    content: str
    evidence_ids: List[str] = Field(default_factory=list)
    source: str = Field(default="supervisor_approved")
    requires_user_confirmation: bool = False
    confirmed: bool = False


# ── Roundtable Run ──

class RoundtableRun(BaseModel):
    run_id: str = Field(description="e.g. r_001")
    session_id: str
    team_id: str = ""
    agents: List[str] = Field(default_factory=list)
    status: str = Field(default="queued")
    budget: dict = Field(default_factory=lambda: {"max_tokens": 80000, "max_cost_cny": 5})
    created_at: Optional[datetime] = None


# ── Pipeline Result ──

class PipelineResult(BaseModel):
    """Unified result from a roundtable pipeline run."""
    session_id: str
    mode: str = "mock"
    agent_reviews: list = Field(default_factory=list)
    supervisor_reviews: list = Field(default_factory=list)
    report: str = ""
    report_path: str = ""
    memories_written: int = 0


# ── Team ──

class TeamTemplate(BaseModel):
    team_id: str
    name: str
    description: str
    suitable_scenarios: List[str] = Field(default_factory=list)
    recommended_agents: List[str] = Field(default_factory=list)
    capability_scores: dict = Field(default_factory=dict)
