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


_SESSION_TRANSITIONS: dict[str, set[str]] = {
    "recording": {"transcribing", "analyzing"},
    "transcribing": {"analyzing"},
    "analyzing": {"reviewing", "completed"},
    "reviewing": {"completed"},
    "completed": set(),
}


def is_valid_status_transition(from_status: str, to_status: str) -> bool:
    return to_status in _SESSION_TRANSITIONS.get(from_status, set())


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


# ── Structured Debate V2 Models ──

class DebateStepType(str, Enum):
    """四步结构化辩论的每一步类型。"""
    STATEMENT = "statement"       # Step 1: 开场陈述
    CHALLENGE = "challenge"       # Step 2: 强制质疑
    NEW_PERSPECTIVE = "new_perspective"  # Step 3: 补充新视角
    RESPONSE = "response"         # Step 4a: 回应
    CORRECTION = "correction"     # Step 4b: 修正
    CONSENSUS = "consensus"       # Step 4c: 共识收敛
    USER_INTERRUPT = "user_interrupt"    # 用户插话


class AgreementLevel(str, Enum):
    """共识强度分级。"""
    STRONG_CONSENSUS = "strong_consensus"      # 3+/3 同意
    PARTIAL_CONSENSUS = "partial_consensus"    # 2/3 或多数同意
    DIVIDED = "divided"                        # 明显分歧
    IRRECONCILABLE = "irreconcilable"          # 不可调和
    UNKNOWN = "unknown"


class DebateStep(BaseModel):
    """单个辩论步骤（持久化到 debate_steps 表）。"""
    step_id: str
    group_id: str
    step_number: int = Field(ge=1, le=4)
    agent_id: str
    step_type: DebateStepType
    content: str
    content_struct: dict = Field(default_factory=dict, description="结构化输出字段")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    hallucination_flags: List[dict] = Field(default_factory=list)
    sources: List[str] = Field(default_factory=list, description="数据来源引用")
    created_at: Optional[datetime] = None


class DebateEvent(BaseModel):
    """辩论事件流（用于回放，持久化到 debate_events 表）。"""
    event_id: Optional[int] = None
    session_id: str
    event_type: str  # round_start | agent_thinking | agent_done | user_interrupt | supervisor_start | report_ready
    agent_id: Optional[str] = None
    content: str = ""
    sequence_num: int = 0
    metadata: dict = Field(default_factory=dict)
    created_at: Optional[datetime] = None


class UserInterrupt(BaseModel):
    """用户插话记录（持久化到 user_interrupts 表）。"""
    interrupt_id: str
    session_id: str
    user_id: str
    interrupt_type: str = Field(default="question", description="question | rebuttal | clarify | deep_dive")
    target_agent_id: Optional[str] = None
    content: str
    timestamp: datetime


class ConsensusSnapshot(BaseModel):
    """共识快照（持久化到 consensus_snapshots 表）。"""
    snapshot_id: str
    session_id: str
    group_id: Optional[str] = None
    step_id: Optional[str] = None
    dimension_scores: dict = Field(default_factory=dict, description="维度 → 评分")
    agreement_level: AgreementLevel = Field(default=AgreementLevel.UNKNOWN)
    consensus_text: str = ""
    created_at: Optional[datetime] = None


class StructuredDebateResult(BaseModel):
    """V2 四步辩论的完整结果。"""
    session_id: str
    groups: List[AgentGroup] = Field(default_factory=list)
    steps: List[DebateStep] = Field(default_factory=list)
    events: List[DebateEvent] = Field(default_factory=list)
    interrupts: List[UserInterrupt] = Field(default_factory=list)
    snapshots: List[ConsensusSnapshot] = Field(default_factory=list)
    final_consensus: dict = Field(default_factory=dict)
    conflicts: List[dict] = Field(default_factory=list)


# ── Sentinel / Health ──

class CircuitState(str, Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing fast
    HALF_OPEN = "half_open"  # Testing recovery


class AgentHealth(BaseModel):
    """Agent health record (persisted to agent_health table)."""
    agent_id: str
    status: str = Field(default="healthy", description="healthy | degraded | unhealthy")
    failure_count: int = 0
    success_count: int = 0
    circuit_state: CircuitState = Field(default=CircuitState.CLOSED)
    total_hallucinations: int = 0
    avg_confidence: float = 0.0
    last_failure_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None


class SentinelAlertType(str, Enum):
    BOUNDARY_VIOLATION = "boundary_violation"
    HALLUCINATION = "hallucination"
    TIMEOUT = "timeout"
    REPETITION = "repetition"
    CIRCUIT_OPEN = "circuit_open"


class SentinelAlert(BaseModel):
    """Sentinel alert record (persisted to sentinel_alerts table)."""
    alert_id: str
    session_id: str
    alert_type: SentinelAlertType
    severity: str = Field(default="low", description="low | medium | high | critical")
    agent_id: Optional[str] = None
    claim_id: Optional[str] = None
    message: str
    metadata: dict = Field(default_factory=dict)
    acknowledged: bool = False
    created_at: Optional[datetime] = None


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


# ── Agent Registry (V2) ──

class AgentManifest(BaseModel):
    """Agent registration entry from config/agents/registry.json."""
    id: str = Field(description="Unique agent identifier, e.g. product_manager")
    name: str = Field(description="Display name, e.g. 产品经理")
    emoji: str = Field(default="🤖", description="Visual identifier")
    role: str = Field(default="", description="Functional role, e.g. product_analysis")
    domains: List[str] = Field(default_factory=list, description="Expertise domains")
    keywords: List[str] = Field(default_factory=list, description="Matching keywords")
    methodology: str = Field(default="", description="Analytical methodology description")
    score_dimension: str = Field(default="", description="Primary scoring dimension")
    can_challenge: List[str] = Field(default_factory=list, description="Agent IDs this agent can challenge")
    must_yield_to: List[str] = Field(default_factory=list, description="Agent IDs this agent must yield to")
    max_words: int = Field(default=800, ge=50, le=2000)
    min_words: int = Field(default=150, ge=20, le=500)
    forbidden_topics: List[str] = Field(default_factory=list)
    required_output_fields: List[str] = Field(default_factory=list)
    is_active: bool = Field(default=True)


class AgentMatchResult(BaseModel):
    """Result of matching a single agent against user input."""
    agent: AgentManifest
    match_score: float = Field(ge=0.0, le=1.0, description="Jaccard similarity score")
    matched_keywords: List[str] = Field(default_factory=list)
    methodology_bonus: float = Field(default=0.0, description="Extra score from methodology match")
    final_score: float = Field(ge=0.0, le=1.0, description="match_score + methodology_bonus, capped at 1.0")
    reason: str = Field(default="", description="Human-readable match explanation")


class AgentGroup(BaseModel):
    """A group of agents assigned to debate a specific topic."""
    group_id: str = Field(description="e.g. g_001")
    group_name: str = Field(description="e.g. 产品策略组")
    topic: str = Field(description="What this group will debate")
    agents: List[AgentManifest] = Field(default_factory=list)
    rationale: str = Field(default="", description="Why these agents were grouped")


class GroupRecommendation(BaseModel):
    """Full recommendation response for user confirmation."""
    session_id: str
    input_text: str = Field(description="Original user input or transcript summary")
    extracted_keywords: List[str] = Field(default_factory=list)
    matched_agents: List[AgentMatchResult] = Field(default_factory=list)
    groups: List[AgentGroup] = Field(default_factory=list)
    ungrouped_reason: str = Field(default="", description="Why some agents were excluded")


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
    agent_reviews: list[AgentReview] = Field(default_factory=list)
    supervisor_reviews: list[SupervisorReview] = Field(default_factory=list)
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


# ── Anchored Debate (个人圆桌模式) ──

class ClaimNature(str, Enum):
    """Claim的主客观属性：意见 vs 事实"""
    OPINION = "opinion"   # 视角性陈述，无需来源
    FACT    = "fact"      # 事实性陈述，必须有来源或降级为OPINION


class DebateMode(str, Enum):
    QUICK = "quick"  # 只跑Round 0+1，约5秒
    FULL  = "full"   # Round 0+1+2，约15秒（默认）
    DEEP  = "deep"   # Round 0+1+2+3守场，约25秒


class DecisionTemplate(str, Enum):
    DIRECTION  = "direction"   # 该不该做这个方向
    FEATURE    = "feature"     # 哪个功能先做
    PRICING    = "pricing"     # 定价策略
    PIVOT      = "pivot"       # 要不要转型
    PARTNER    = "partner"     # 合作是否值得
    GENERAL    = "general"     # 通用决策（默认）


class AnchorStatement(BaseModel):
    """Round 0：用户愿景代言人的锚点发言"""
    session_id: str
    agent_id: str = "user_advocate"
    core_plan: str = Field(description="用户想做的核心事项")
    stated_reasons: List[str] = Field(default_factory=list, description="用户明确说的理由")
    known_resources: List[str] = Field(default_factory=list, description="用户明确提到的资源")
    main_concern: str = Field(default="", description="用户的主要困惑或问题")
    raw_content: str = Field(default="", description="代言人的完整发言文本")


class SpecialistStance(str, Enum):
    SUPPORT   = "support"    # 支持用户愿景
    CHALLENGE = "challenge"  # 挑战用户愿景
    MIXED     = "mixed"      # 部分支持部分挑战


class SpecialistResponse(BaseModel):
    """Round 1：专家对锚点的响应"""
    argument_id: str
    agent_id: str
    stance: SpecialistStance
    stance_summary: str = Field(description="一句话说明支持/挑战哪一点")
    supporting_points: List[str] = Field(default_factory=list)
    challenge_points: List[str] = Field(default_factory=list)
    raw_content: str = Field(default="")


class InformationGap(BaseModel):
    """代言人在Round 2无法回应的挑战 → 关键缺口"""
    gap_id: str
    challenger_agent_id: str
    challenge_content: str
    gap_description: str = Field(description="用户缺失的具体信息类型")


class QuickRequest(BaseModel):
    """零门槛入口：一句话启动辩论"""
    question: str = Field(description="用户的决策问题或想法描述")
    context: Optional[str] = Field(default=None, description="可选补充背景")
    template: DecisionTemplate = Field(default=DecisionTemplate.GENERAL)
    mode: DebateMode = Field(default=DebateMode.FULL)
    agents: Optional[List[str]] = Field(
        default=None,
        description="指定专家skill_id列表，不传则自动推荐"
    )


class InterviewQuestion(BaseModel):
    question_id: str
    question: str
    purpose: str = Field(description="这个问题要补充什么信息")
    is_required: bool = False


class InterviewContext(BaseModel):
    """追问阶段的完整上下文"""
    session_id: str
    original_question: str
    template: DecisionTemplate
    questions: List[InterviewQuestion] = Field(default_factory=list)
    answers: dict = Field(default_factory=dict, description="question_id → 用户回答")
    enriched_context: str = Field(default="", description="整合后发给Agent的完整上下文")
    user_bias_signal: Optional[str] = Field(
        default=None,
        description="检测到的用户隐含倾向（不发给Agent，仅Supervisor使用）"
    )


class AnchoredReport(BaseModel):
    """3+1格式报告：面向个人决策者"""
    session_id: str
    question: str
    conclusions: List[str] = Field(
        default_factory=list,
        description="3条直接可用的结论，每条一句话"
    )
    key_dispute: str = Field(default="", description="专家们分歧最大的一个点")
    blind_spot: str = Field(default="", description="用户没想到但影响决策的角度")
    next_action: str = Field(default="", description="最先要去验证的一件事")
    validated_aspects: List[str] = Field(default_factory=list, description="≥2个专家支持的方面")
    challenged_aspects: List[str] = Field(default_factory=list, description="被专家明确反对的方面")
    information_gaps: List[InformationGap] = Field(default_factory=list, description="用户的关键信息缺口")
    specialist_stances: dict = Field(default_factory=dict, description="agent_id → stance")


# ── Memory (三层记忆) ──

class DecisionLog(BaseModel):
    """Layer A：决策日志"""
    log_id: str
    created_at: datetime
    question: str
    sanitized_question: str = Field(default="", description="去偏后的中立版本")
    agents_used: List[str] = Field(default_factory=list)
    conclusions: List[str] = Field(default_factory=list)
    key_dispute: str = ""
    blind_spots: List[str] = Field(default_factory=list)
    session_id: str
    follow_up_id: Optional[str] = None


class UserProfile(BaseModel):
    """Layer B：用户画像（自动从DecisionLog提炼）"""
    profile_id: str = "default"
    updated_at: Optional[datetime] = None
    decision_count: int = 0
    decision_themes: dict = Field(default_factory=dict, description="主题 → 出现次数")
    observed_patterns: List[str] = Field(default_factory=list, description="按时间排列的决策模式观察")
    recurring_blind_spots: List[str] = Field(default_factory=list)
    context_for_next_debate: str = Field(
        default="",
        description="注入下次辩论的用户画像摘要"
    )


class FollowUp(BaseModel):
    """Layer C：复盘提醒（用户主动设置）"""
    follow_up_id: str
    log_id: str
    created_at: datetime
    reminder_date: Optional[datetime] = None  # 用户自行设置，无默认值
    actual_outcome: Optional[str] = None
    outcome_rating: Optional[int] = Field(default=None, ge=1, le=5)
    was_decision_right: Optional[bool] = None
