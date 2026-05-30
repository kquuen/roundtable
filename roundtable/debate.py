"""Phase 6: 两轮辩论调度引擎 + 引用完整性校验。

核心流程：
  Round 1: N 个 Agent 并发独立分析（reuse orchestrator）
  Round 2: 每个 Agent 看到其他 (N-1) 人的 Round 1 结果 → 质疑/同意/修正
  Converge: Supervisor 纯函数处理 → 共识分层
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Optional, AsyncGenerator

from roundtable.models import (
    DebateArgument, DebateRound, DebateSession,
    AgentReview, EvidenceClaim, EvidencePacket,
    ClaimLifecycle, ConsensusLevel,
    AnchorStatement, SpecialistResponse, SpecialistStance,
    InformationGap, AnchoredReport, QuickRequest,
    InterviewContext, DecisionTemplate, DebateMode,
)

logger = logging.getLogger("roundtable.debate")

# ── 追问问题模板（按决策类型） ──
_INTERVIEW_QUESTIONS: dict[str, list[dict]] = {
    "direction": [
        {"id": "q1", "question": "你现在的资源状态是什么？（大概的时间/资金/人手）", "purpose": "评估可行性边界"},
        {"id": "q2", "question": "这个方向你做过什么验证，或者有什么让你觉得它可行的信号？", "purpose": "了解已有证据"},
        {"id": "q3", "question": "如果这个方向失败，你的备选是什么？", "purpose": "了解风险容忍度"},
    ],
    "feature": [
        {"id": "q1", "question": "你的目标用户现在是怎么解决这个问题的？", "purpose": "了解替代方案"},
        {"id": "q2", "question": "这个功能主要影响留存还是获客？", "purpose": "判断优先级"},
    ],
    "pricing": [
        {"id": "q1", "question": "竞品或同类产品定价是多少？", "purpose": "建立定价锚点"},
        {"id": "q2", "question": "目标用户现在为同类需求花多少钱（含时间成本）？", "purpose": "评估支付意愿"},
    ],
    "pivot": [
        {"id": "q1", "question": "当前方向不做了的最大原因是什么？", "purpose": "判断放弃驱动因素"},
        {"id": "q2", "question": "已经有多少用户或数据积累在当前方向上？", "purpose": "评估沉没成本"},
        {"id": "q3", "question": "新方向验证过市场需求吗？", "purpose": "判断新方向可靠性"},
    ],
    "partner": [
        {"id": "q1", "question": "合作的核心交换条件是什么（你给什么，对方给什么）？", "purpose": "澄清合作结构"},
        {"id": "q2", "question": "如果合作失败，你损失什么？", "purpose": "评估风险"},
    ],
    "general": [
        {"id": "q1", "question": "这个决定的时间压力是什么？你有多长时间？", "purpose": "了解紧迫性"},
        {"id": "q2", "question": "你最担心的是哪一方面？", "purpose": "找到核心顾虑"},
    ],
}

_DEFAULT_AGENTS = [
    "user_advocate",
    "market_positioning",
    "product_feasibility",
    "market_adoption",
    "monetization",
]


def get_interview_questions(template: DecisionTemplate) -> list[dict]:
    """按决策类型返回追问问题列表。"""
    return _INTERVIEW_QUESTIONS.get(template.value, _INTERVIEW_QUESTIONS["general"])


def sanitize_user_bias(question: str) -> tuple[str, Optional[str]]:
    """
    检测用户问题中的倾向性语言，返回(中立版本, 倾向信号)。
    倾向信号不发给Agent，只供Supervisor的反谄媚检测使用。
    """
    bias_patterns = [
        ("我觉得", ""), ("我认为", ""), ("我想", ""),
        ("我倾向于", ""), ("感觉", ""), ("应该是", ""),
        ("肯定", ""), ("显然", ""), ("明显", ""),
    ]
    bias_signal = None
    sanitized = question
    for pattern, _ in bias_patterns:
        if pattern in question:
            bias_signal = f"用户问题中包含倾向性语言：'{pattern}'"
            sanitized = sanitized.replace(pattern, "")
    return sanitized.strip(), bias_signal


class DebateEngine:
    """两轮辩论调度引擎。

    保留现有 run_orchestrator_async() 不动，辩论作为新入口并行存在。
    """

    def __init__(
        self,
        provider=None,
        budget=None,
        domain_name: str | None = None,
    ):
        self.provider = provider
        self.budget = budget
        self.domain_name = domain_name

    async def run_debate(
        self,
        evidence: EvidencePacket,
        agents: list,
    ) -> DebateSession:
        """执行完整的两轮辩论。

        Returns:
            DebateSession: 包含两个 DebateRound + consensus_summary + conflicts
        """
        session_id = evidence.session_id

        # ── Round 1: 并发独立分析 ──
        logger.info("[%s] Debate Round 1: dispatching %d agents", session_id, len(agents))
        round1_reviews = await self._run_round1(evidence, agents)
        logger.info("[%s] Debate Round 1 complete: %d reviews", session_id, len(round1_reviews))

        round1_args = self._reviews_to_arguments(round1_reviews, round_num=1)
        debate_session = DebateSession(
            session_id=session_id,
            rounds=[DebateRound(round_number=1, arguments=round1_args)],
        )

        # ── Round 2: 交叉辩论 ──
        peer_map = self._build_peer_map(round1_reviews)
        logger.info("[%s] Debate Round 2: agents receiving peer context", session_id)

        round2_args = await self._run_round2(evidence, agents, peer_map)
        logger.info("[%s] Debate Round 2 complete: %d arguments", session_id, len(round2_args))

        # 引用完整性校验
        valid_round1_ids = {a.argument_id for a in round1_args}
        round1_claim_ids = {c.claim_id for ar in round1_reviews for c in ar.claims}
        validated_args, ref_errors = self._validate_references(
            round2_args, valid_round1_ids, round1_claim_ids,
        )
        debate_session.conflicts = ref_errors

        debate_session.rounds.append(
            DebateRound(round_number=2, arguments=validated_args),
        )

        # ── Converge: 共识分层 ──
        self._converge(debate_session, round1_reviews)

        return debate_session

    # ── Round 1 ──

    async def _run_round1(self, evidence, agents) -> list[AgentReview]:
        """并发调度所有 Agent 做独立分析。"""
        from roundtable.orchestrator import run_orchestrator_async

        return await run_orchestrator_async(
            evidence, agent_count=len(agents), provider=self.provider,
            domain_name=self.domain_name, budget=self.budget,
        )

    # ── Peer map ──

    def _build_peer_map(self, reviews: list[AgentReview]) -> dict[str, list[AgentReview]]:
        """每个 Agent 看到除自己外的所有 review。"""
        return {
            ar.agent_id: [r for r in reviews if r.agent_id != ar.agent_id]
            for ar in reviews
        }

    # ── Round 2 ──

    async def _run_round2(
        self,
        evidence: EvidencePacket,
        agents: list,
        peer_map: dict[str, list[AgentReview]],
    ) -> list[DebateArgument]:
        """每个 Agent 基于 peer reviews 做质疑/同意/修正。"""
        tasks = []
        for agent in agents:
            peers = peer_map.get(agent.agent_id, [])
            tasks.append(self._single_agent_round2(agent, evidence, peers))

        results = await asyncio.gather(*tasks)
        # Flatten
        all_args: list[DebateArgument] = []
        for args in results:
            all_args.extend(args)
        return all_args

    async def _single_agent_round2(
        self,
        agent,
        evidence: EvidencePacket,
        peers: list[AgentReview],
    ) -> list[DebateArgument]:
        """单个 Agent 的 Round 2 执行。"""
        if not peers:
            return []

        agent_id = agent.agent_id
        args = []

        # LLM 路径（provider 已内嵌在 agent 中）或 mock 降级
        if agent.provider is not None:
            review = await agent.analyze_async(evidence, peer_reviews=peers, budget=self.budget)
        else:
            review = agent._analyze_debate_mock(evidence, peers)

        # Read raw claims data for position (Bug 5: preserved from LLM parse)
        raw_claims = getattr(agent, '_last_raw_claims', []) or []

        for i, claim in enumerate(review.claims):
            # Read position from raw LLM response (fallback to "extend")
            position = "extend"
            if i < len(raw_claims):
                position = raw_claims[i].get("position", "extend")

            # Read target_claim_id from raw LLM response or infer from content
            target_claim_id = None
            if i < len(raw_claims):
                target_claim_id = raw_claims[i].get("target_claim_id")
            if not target_claim_id:
                # Fallback: infer from content
                for pr in peers:
                    for pc in pr.claims:
                        if pc.claim_id in claim.content:
                            target_claim_id = pc.claim_id
                            break

            arg = DebateArgument(
                argument_id=f"arg_r2_{agent_id}_{i:03d}",
                agent_id=agent_id,
                round=2,
                position=position,
                target_claim_id=target_claim_id,
                content=claim.content,
                evidence_ids=claim.evidence_ids,
            )
            args.append(arg)

        return args

    # ── Reference validation ──

    def _validate_references(
        self,
        round2_args: list[DebateArgument],
        valid_arg_ids: set[str],
        valid_claim_ids: set[str],
    ) -> tuple[list[DebateArgument], list[dict]]:
        """校验 Round 2 的 target_claim_id 引用完整性。

        不合法引用 → 拒绝该 argument，记录到 conflicts。
        """
        validated: list[DebateArgument] = []
        errors: list[dict] = []

        for arg in round2_args:
            if arg.target_claim_id is None:
                # 无目标引用，直接通过（extend 类型不需要目标）
                validated.append(arg)
                continue

            if arg.target_claim_id not in valid_claim_ids:
                errors.append({
                    "argument_id": arg.argument_id,
                    "agent_id": arg.agent_id,
                    "target_claim_id": arg.target_claim_id,
                    "error": "target_claim_id not found in Round 1",
                })
                logger.warning(
                    "Reference integrity violation: %s references non-existent %s",
                    arg.argument_id, arg.target_claim_id,
                )
                continue

            validated.append(arg)

        return validated, errors

    # ── Converge ──

    def _converge(
        self,
        debate_session: DebateSession,
        round1_reviews: list[AgentReview],
    ) -> None:
        """Supervisor 纯函数处理：共识分层 + 冲突标记。"""
        from roundtable.supervisor import _compute_consensus_levels

        # 对 Round 1 claims 计算共识
        # 先用一个占位 review 列表
        dummy_reviews = []
        for ar in round1_reviews:
            from roundtable.models import SupervisorReview, ReviewResult
            for claim in ar.claims:
                dummy_reviews.append(SupervisorReview(
                    claim_id=claim.claim_id,
                    review_result=ReviewResult.APPROVED,
                ))

        _compute_consensus_levels(dummy_reviews, round1_reviews)

        # 汇总 consensus_summary
        for ar in round1_reviews:
            for claim in ar.claims:
                debate_session.consensus_summary[claim.claim_id] = claim.consensus_level.value

    # ── Helpers ──

    def _reviews_to_arguments(
        self,
        reviews: list[AgentReview],
        round_num: int,
    ) -> list[DebateArgument]:
        """将 AgentReview 列表转换为 DebateArgument 列表。"""
        args = []
        for ar in reviews:
            for i, claim in enumerate(ar.claims):
                args.append(DebateArgument(
                    argument_id=f"arg_r{round_num}_{ar.agent_id}_{i:03d}",
                    agent_id=ar.agent_id,
                    claim_id=claim.claim_id,
                    round=round_num,
                    position="extend",
                    target_claim_id=None,
                    content=claim.content,
                    evidence_ids=claim.evidence_ids,
                ))
        return args


# ══════════════════════════════════════════════
# 锚点式辩论引擎（个人圆桌模式）
# ══════════════════════════════════════════════

class AnchoredDebateEngine:
    """
    锚点式辩论引擎。

    辩论结构：
      Round 0: user_advocate 呈现用户计划（锚点）
      Round 1: 四个专家并发响应锚点（支持/挑战）
      Round 2: user_advocate 回应挑战，专家追问
      Converge: Supervisor 提炼 3+1 报告
    """

    def __init__(self, provider=None):
        self.provider = provider

    async def _provider_call(self, prompt: str, max_tokens: int) -> str:
        """Call provider using the unified chat interface with backward compatibility."""
        if self.provider is None:
            return ""

        if hasattr(self.provider, "chat"):
            return await self.provider.chat(
                system_prompt="你是圆桌专家助手。请严格根据用户提供信息输出中文结论。",
                user_message=prompt,
                max_tokens=max_tokens,
                temperature=0.7,
            )

        if hasattr(self.provider, "complete"):
            return await self.provider.complete(prompt, max_tokens=max_tokens)

        raise RuntimeError("Provider must implement chat() or complete()")

    async def run(
        self,
        interview: InterviewContext,
        mode: DebateMode = DebateMode.FULL,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> AnchoredReport:
        """
        执行完整的锚点式辩论。

        Args:
            interview: 追问阶段产生的完整上下文
            mode: QUICK / FULL / DEEP
            event_queue: 若传入，将推送SSE事件（实时流式展示用）
        """
        sid = interview.session_id

        async def emit(event_type: str, agent: str = "", content: str = "", meta: dict = None):
            if event_queue is not None:
                await event_queue.put({
                    "type": event_type,
                    "agent": agent,
                    "content": content,
                    "meta": meta or {},
                })

        # ── Round 0：代言人建立锚点 ──
        await emit("round_start", content="Round 0：用户愿景代言人开场")
        await emit("agent_thinking", agent="user_advocate")
        anchor = await self._run_round0(interview)
        await emit("agent_done", agent="user_advocate", content=anchor.raw_content)
        logger.info("[%s] Round 0 complete: anchor established", sid)

        # ── Round 1：专家并发响应 ──
        await emit("round_start", content="Round 1：专家独立分析")
        specialist_ids = [a for a in _DEFAULT_AGENTS if a != "user_advocate"]
        for aid in specialist_ids:
            await emit("agent_thinking", agent=aid)

        responses = await self._run_round1(interview, anchor)
        for r in responses:
            await emit("agent_done", agent=r.agent_id, content=r.raw_content,
                       meta={"stance": r.stance.value})
        logger.info("[%s] Round 1 complete: %d specialist responses", sid, len(responses))

        if mode == DebateMode.QUICK:
            report = self._synthesize(interview, anchor, responses, [], sid)
            await emit("report_ready", content="辩论完成")
            return report

        # ── Round 2：代言人回应 + 专家追问 ──
        await emit("round_start", content="Round 2：代言人回应 & 专家追问")
        await emit("agent_thinking", agent="user_advocate")
        gaps, round2_advocate = await self._run_round2(interview, anchor, responses)
        await emit("agent_done", agent="user_advocate", content=round2_advocate)
        logger.info("[%s] Round 2 complete: %d information gaps", sid, len(gaps))

        # ── 综合报告 ──
        await emit("supervisor_start")
        report = self._synthesize(interview, anchor, responses, gaps, sid)
        await emit("report_ready", content="辩论完成")
        return report

    async def _run_round0(self, interview: InterviewContext) -> AnchorStatement:
        """用户愿景代言人建立锚点。"""
        prompt = self._build_advocate_prompt(interview)

        if self.provider:
            raw = await self._provider_call(prompt, max_tokens=600)
        else:
            raw = self._mock_advocate(interview)

        return AnchorStatement(
            session_id=interview.session_id,
            agent_id="user_advocate",
            core_plan=interview.original_question,
            stated_reasons=self._extract_answers(interview),
            known_resources=[],
            main_concern=interview.answers.get("q2", ""),
            raw_content=raw,
        )

    async def _run_round1(
        self,
        interview: InterviewContext,
        anchor: AnchorStatement,
    ) -> list[SpecialistResponse]:
        """四个专家并发响应锚点。"""
        specialist_ids = [a for a in _DEFAULT_AGENTS if a != "user_advocate"]
        tasks = [
            self._single_specialist(sid, interview, anchor)
            for sid in specialist_ids
        ]
        return await asyncio.gather(*tasks)

    async def _single_specialist(
        self,
        skill_id: str,
        interview: InterviewContext,
        anchor: AnchorStatement,
    ) -> SpecialistResponse:
        prompt = self._build_specialist_prompt(skill_id, interview, anchor)

        if self.provider:
            raw = await self._provider_call(prompt, max_tokens=500)
            stance = self._parse_stance(raw)
            stance_summary = self._extract_summary(raw, skill_id)
        else:
            raw, stance = self._mock_specialist(skill_id, anchor)
            stance_summary = f"{skill_id} 对用户计划的分析"

        return SpecialistResponse(
            argument_id=f"arg_{skill_id}_{uuid.uuid4().hex[:6]}",
            agent_id=skill_id,
            stance=stance,
            stance_summary=stance_summary,
            supporting_points=[],
            challenge_points=[],
            raw_content=raw,
        )

    async def _run_round2(
        self,
        interview: InterviewContext,
        anchor: AnchorStatement,
        responses: list[SpecialistResponse],
    ) -> tuple[list[InformationGap], str]:
        """代言人回应挑战，识别信息缺口。"""
        challenges = [r for r in responses if r.stance in (
            SpecialistStance.CHALLENGE, SpecialistStance.MIXED
        )]
        gaps: list[InformationGap] = []
        advocate_lines: list[str] = []

        for r in challenges:
            # 判断代言人能否回应
            has_info = any(
                kw in interview.enriched_context
                for kw in r.raw_content.split()[:10]
            )
            if not has_info:
                gap = InformationGap(
                    gap_id=f"gap_{r.agent_id}_{uuid.uuid4().hex[:4]}",
                    challenger_agent_id=r.agent_id,
                    challenge_content=r.raw_content[:200],
                    gap_description=f"用户目前没有关于{r.agent_id}提出的问题的明确信息",
                )
                gaps.append(gap)
                advocate_lines.append(
                    f"[对{r.agent_id}的挑战] 用户目前没有这方面的明确信息。"
                )
            else:
                advocate_lines.append(
                    f"[对{r.agent_id}的挑战] 根据用户提供的信息：{interview.enriched_context[:100]}..."
                )

        return gaps, "\n".join(advocate_lines)

    def _synthesize(
        self,
        interview: InterviewContext,
        anchor: AnchorStatement,
        responses: list[SpecialistResponse],
        gaps: list[InformationGap],
        session_id: str,
    ) -> AnchoredReport:
        """提炼3+1报告。"""
        supports = [r for r in responses if r.stance == SpecialistStance.SUPPORT]
        challenges = [r for r in responses if r.stance == SpecialistStance.CHALLENGE]
        mixed = [r for r in responses if r.stance == SpecialistStance.MIXED]

        _ROLE_NAMES = {
            "market_positioning": "市场定位",
            "product_feasibility": "产品可行性",
            "market_adoption": "市场采纳",
            "monetization": "变现路径",
        }

        validated = [f"{_ROLE_NAMES.get(r.agent_id, r.agent_id)}：{r.stance_summary}" for r in supports]
        challenged = [f"{_ROLE_NAMES.get(r.agent_id, r.agent_id)}：{r.stance_summary}" for r in challenges + mixed]

        conclusions = []
        if validated:
            conclusions.append(f"✅ 支持：{validated[0]}")
        if challenged:
            conclusions.append(f"⚠️ 质疑：{challenged[0]}")
        if gaps:
            conclusions.append(f"❓ 缺口：{gaps[0].gap_description}")

        key_dispute = challenged[0] if challenged else "各专家基本一致"
        blind_spot = gaps[0].gap_description if gaps else "暂无明显盲区"
        next_action = (
            f"先验证：{gaps[0].gap_description}" if gaps
            else f"推进：{validated[0]}" if validated
            else "进行用户访谈验证核心假设"
        )

        return AnchoredReport(
            session_id=session_id,
            question=interview.original_question,
            conclusions=conclusions[:3],
            key_dispute=key_dispute,
            blind_spot=blind_spot,
            next_action=next_action,
            validated_aspects=validated,
            challenged_aspects=challenged,
            information_gaps=gaps,
            specialist_stances={r.agent_id: r.stance.value for r in responses},
        )

    # ── Prompt builders ──

    def _build_advocate_prompt(self, interview: InterviewContext) -> str:
        return f"""你是用户愿景代言人。只使用用户提供的信息，完整呈现他们的计划。

用户的问题：{interview.original_question}
补充信息：{interview.enriched_context}

按以下格式输出：
【用户的计划】
我想做的是：...
我认为能成的理由是：...
我目前有的资源是：...
我面临的主要困惑是：...

严格限制：不添加用户没说过的信息，不猜测，不美化。"""

    def _build_specialist_prompt(
        self, skill_id: str, interview: InterviewContext, anchor: AnchorStatement
    ) -> str:
        role_desc = {
            "market_positioning": "市场定位顾问，评估市场竞争和差异化",
            "product_feasibility": "产品可行性工程师，评估技术可行性和MVP范围",
            "market_adoption": "市场接受度分析师，评估用户会不会真正采用",
            "monetization": "变现路径顾问，评估商业模式和收入路径",
        }.get(skill_id, "专家顾问")

        return f"""你是{role_desc}。

用户愿景代言人的陈述：
{anchor.raw_content}

用户背景：{interview.enriched_context}

你的任务：
1. 明确表态：[支持] 或 [挑战] 用户愿景代言人的计划
2. 给出具体的支持或挑战理由（必须基于你的专业视角的事实判断）
3. 不要附和用户的倾向，只依据你的专业判断

按以下格式输出：
对用户愿景代言人的立场：[支持/挑战/部分支持挑战并存]
核心论据：...（具体说明）"""

    def _parse_stance(self, raw: str) -> SpecialistStance:
        raw_lower = raw.lower()
        if "支持" in raw and "挑战" not in raw:
            return SpecialistStance.SUPPORT
        elif "挑战" in raw and "支持" not in raw:
            return SpecialistStance.CHALLENGE
        return SpecialistStance.MIXED

    def _extract_summary(self, raw: str, skill_id: str) -> str:
        """从LLM原始输出中提取第一句有意义的结论。"""
        import re
        # 去掉常见的前缀标记
        cleaned = re.sub(r"^对用户愿景代言人的立场：.*?\n", "", raw).strip()
        # 找"核心论据："之后的内容
        match = re.search(r"核心论据[：:]\s*(.+?)(?:\n|$)", cleaned)
        if match:
            return match.group(1).strip()[:150]
        # 找第一句非空有意义文字
        lines = [l.strip() for l in cleaned.split("\n") if len(l.strip()) > 10]
        if lines:
            return lines[0][:150]
        return f"{skill_id}分析完成"

    def _extract_answers(self, interview: InterviewContext) -> list[str]:
        return [v for v in interview.answers.values() if v]

    # ── Mock mode ──

    def _mock_advocate(self, interview: InterviewContext) -> str:
        return f"""【用户的计划】
我想做的是：{interview.original_question}
我认为能成的理由是：（用户提供的背景信息）
我目前有的资源是：{interview.enriched_context or '用户未明确说明'}
我面临的主要困惑是：{interview.answers.get('q2', '用户未说明主要困惑')}"""

    def _mock_specialist(
        self, skill_id: str, anchor: AnchorStatement
    ) -> tuple[str, SpecialistStance]:
        mock_responses = {
            "market_positioning": (
                "对用户愿景代言人的立场：[挑战]\n核心论据：市场上已有类似产品，差异化需要更清晰的定义。",
                SpecialistStance.CHALLENGE,
            ),
            "product_feasibility": (
                "对用户愿景代言人的立场：[支持]\n核心论据：核心功能技术上可行，建议缩小MVP范围以加快验证。",
                SpecialistStance.SUPPORT,
            ),
            "market_adoption": (
                "对用户愿景代言人的立场：[部分支持挑战并存]\n核心论据：目标用户有需求，但切换成本较高。",
                SpecialistStance.MIXED,
            ),
            "monetization": (
                "对用户愿景代言人的立场：[挑战]\n核心论据：当前方案的付费用户路径不够清晰，需要明确第一笔收入来源。",
                SpecialistStance.CHALLENGE,
            ),
        }
        return mock_responses.get(
            skill_id,
            ("对用户愿景代言人的立场：[部分支持挑战并存]\n核心论据：需要更多信息才能给出明确判断。",
             SpecialistStance.MIXED)
        )
