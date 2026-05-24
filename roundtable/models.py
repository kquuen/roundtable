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


# ── Claim Lifecycle ──

class ClaimLifecycle(str, Enum):
    """每个 claim 从产出到最终确认的完整生命周期。"""
    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    CHALLENGED = "challenged"
    NEEDS_USER = "needs_user"
    USER_CONFIRMED = "user_confirmed"
    USER_REJECTED = "user_rejected"


class ConsensusLevel(str, Enum):
    """多 Agent 对同一 claim 的共识强度。"""
    STRONG = "strong"            # 3+/3 同意
    MAJORITY = "majority"        # 2/3 同意
    ISOLATED = "isolated"        # 1/3 孤立观点
    CONTRADICTED = "contradicted" # 存在明确矛盾
    UNKNOWN = "unknown"          # 尚未评估


class VerificationStatus(str, Enum):
    """搜索校验结果——中性命名，明确是'搜索佐证'而非'事实确认'。"""
    UNCHECKED = "unchecked"
    SUPPORTED_BY_SEARCH = "supported_by_search"
    CONTRADICTED_BY_SEARCH = "contradicted_by_search"
    NO_EVIDENCE_FOUND = "no_evidence_found"


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
    lifecycle: ClaimLifecycle = Field(default=ClaimLifecycle.DRAFT)
    consensus_level: ConsensusLevel = Field(default=ConsensusLevel.UNKNOWN)
    verification: VerificationStatus = Field(
        default=VerificationStatus.UNCHECKED,
        description="搜索校验状态",
    )
    debate_history: List[str] = Field(
        default_factory=list,
        description="辩论中引用此 claim 的 argument_id 列表",
    )


# ── Debate ──

class DebateArgument(BaseModel):
    """一轮辩论中的单个论点。"""
    argument_id: str = Field(description="e.g. arg_r2_001")
    agent_id: str
    claim_id: str = Field(default="", description="源 claim_id（Round 1 时存在）")
    round: int = Field(ge=1, le=2, description="1=首轮观点, 2=质疑/同意/修正")
    position: str = Field(description="agree | disagree | extend")
    target_claim_id: Optional[str] = Field(
        default=None,
        description="Round 2 时指向 Round 1 的 claim_id",
    )
    content: str
    evidence_ids: List[str] = Field(default_factory=list)


class DebateRound(BaseModel):
    """一轮完整的辩论。"""
    round_number: int
    arguments: List[DebateArgument] = Field(default_factory=list)


class DebateSession(BaseModel):
    """一次辩论的完整记录。"""
    session_id: str
    rounds: List[DebateRound] = Field(default_factory=list)
    consensus_summary: dict = Field(
        default_factory=dict,
        description="claim_id → consensus_level",
    )
    conflicts: List[dict] = Field(
        default_factory=list,
        description="未解决的矛盾列表",
    )


# ── Domain Configuration ──

class DomainConfig(BaseModel):
    """领域配置：场景 → agent 组合 + prompt 差异。"""
    name: str = Field(description="e.g. personal_roundtable")
    display: str = Field(description="e.g. 个人圆桌")
    description: str = ""
    keywords: List[str] = Field(default_factory=list, description="匹配关键词")
    agents: List[str] = Field(default_factory=list, description="agent skill_id 列表")
    agent_count: int = Field(default=5)
    prompt_modifier: str = Field(default="", description="注入 system prompt 的领域提示")
    forbidden_overrides: dict = Field(
        default_factory=dict,
        description="agent_id → [额外限制规则]",
    )


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


# ── Boundary Classification ──


class BoundaryClass(str, Enum):
    """Agent 输出是否越界的分级判定。"""
    SAFE = "safe"              # 明确在自己领域内
    BORDERLINE = "borderline"  # 踩线但可能合理
    VIOLATION = "violation"    # 明确越界


class SupervisorReview(BaseModel):
    claim_id: str
    review_result: ReviewResult
    final_type: Optional[str] = None
    reason: str = ""
    required_changes: List[str] = Field(default_factory=list)
    boundary_classification: Optional[BoundaryClass] = Field(
        default=None,
        description="Agent 输出是否越界的分级判定",
    )


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


# ── ASR ──

class ASRSegment(BaseModel):
    """单个转写片段。"""
    speaker: str = Field(default="Speaker")
    text: str
    start: float = Field(default=0.0, description="起始时间（秒）")
    end: float = Field(default=0.0, description="结束时间（秒）")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ASRResult(BaseModel):
    """语音转写结果。"""
    segments: List[ASRSegment] = Field(default_factory=list)
    language: str = Field(default="zh")
    duration: float = Field(default=0.0, description="音频总时长（秒）")
    model_used: str = Field(default="whisper-1")


# ── Pipeline Result ──

class PipelineResult(BaseModel):
    """Unified result from a roundtable pipeline run."""
    session_id: str
    mode: str = "mock"
    domain_name: str = Field(default="", description="Classified domain")
    agent_reviews: list = Field(default_factory=list)
    supervisor_reviews: list = Field(default_factory=list)
    report: str = ""
    report_path: str = ""
    memories_written: int = 0
    pending_confirmation_count: int = Field(
        default=0,
        description="需要用户裁决的 claim 数量",
    )
    user_decisions_applied: int = Field(
        default=0,
        description="本次已应用的用户裁决数量",
    )


# ── Team ──

class TeamTemplate(BaseModel):
    team_id: str
    name: str
    description: str
    suitable_scenarios: List[str] = Field(default_factory=list)
    recommended_agents: List[str] = Field(default_factory=list)
    capability_scores: dict = Field(default_factory=dict)
