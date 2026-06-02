"""Phase 2: Structured 4-Step Debate Engine V2.

Replaces the ad-hoc 2-round debate with a rigorous 4-step protocol:
  Step 1: Opening Statement (methodology + question list + data sources)
  Step 2: Mandatory Challenge (must point out omissions/errors/priorities, no "I agree")
  Step 3: New Perspective (introduce angles the previous two missed)
  Step 4: Response → Correction → Consensus (fatal flaw / high-risk flaw / improvement suggestion)

All steps are persisted to debate_steps + debate_events for full playback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from roundtable.models import (
    AgentManifest, AgentGroup,
    DebateStep, DebateStepType, DebateEvent,
    UserInterrupt, ConsensusSnapshot, AgreementLevel,
    StructuredDebateResult, EvidencePacket,
)
from roundtable import db

logger = logging.getLogger("roundtable.debate_v2")


# ── Prompt Templates ──

_STEP1_PROMPT = """你是 {agent_name}（{agent_role}）。
请对以下话题做【开场陈述】，输出严格 JSON：
{{
  "methodology": "你使用的方法论（一句话）",
  "key_questions": ["你提出的关键问题1", "问题2", "问题3"],
  "data_sources": ["你建议查找的数据来源1", "来源2"],
  "initial_assessment": "你对这个话题的初步评估（200字以内）",
  "confidence": 0.85
}}

话题：{topic}
证据片段：{evidence_summary}

约束：
- 禁止把建议写成事实
- 每个主张必须标注置信度
- 不要编造会议中未出现的承诺
"""

_STEP2_PROMPT = """你是 {agent_name}（{agent_role}）。
Step 2 是【强制质疑】。你必须对以下其他 Agent 的开场陈述提出质疑。
规则：
- 必须指出至少 1 个遗漏、错误或优先级问题
- 禁止说"我同意"、"基本正确"等无建设性的话
- 禁止人身攻击，只针对观点

你的质疑对象：
{peer_statements}

请输出严格 JSON：
{{
  "challenges": [
    {{
      "target_agent": "被质疑的 Agent ID",
      "target_claim": "你质疑的具体主张",
      "issue_type": "omission | error | priority_misorder | unsupported_claim",
      "reasoning": "你的推理过程",
      "suggested_correction": "你建议的修正方向"
    }}
  ],
  "confidence": 0.80
}}
"""

_STEP3_PROMPT = """你是 {agent_name}（{agent_role}）。
Step 3 是【补充新视角】。在前两轮的讨论基础上，引入一个或两个之前没有人提到过的角度。

已有的讨论：
{previous_discussion}

请输出严格 JSON：
{{
  "new_angles": [
    {{
      "angle_title": "新视角的标题",
      "angle_description": "详细描述（150字以内）",
      "why_missed": "为什么前两轮没有人想到这个角度",
      "implication": "如果忽略这个角度会有什么后果"
    }}
  ],
  "confidence": 0.75
}}
"""

_STEP4_PROMPT = """你是 {agent_name}（{agent_role}）。
Step 4 是【回应 → 修正 → 共识】。综合前三轮的讨论，输出你的最终立场。

全部讨论记录：
{full_discussion}

请输出严格 JSON：
{{
  "response_to_challenges": "你对 Step 2 质疑的回应（接受/反驳/修正）",
  "corrections_made": ["你对自己之前陈述的修正1", "修正2"],
  "final_position": "你的最终立场（200字以内）",
  "consensus_assessment": {{
    "fatal_flaws": ["致命缺陷1"],
    "high_risk_flaws": ["高危缺陷1"],
    "improvement_suggestions": ["改进建议1"]
  }},
  "confidence": 0.82
}}
"""


# ── Engine ──

class DebateEngineV2:
    """4-step structured debate engine with full persistence."""

    def __init__(self, provider=None):
        self.provider = provider
        self._event_seq = 0

    # ── Public API ──

    async def run(
        self,
        session_id: str,
        evidence: EvidencePacket,
        groups: list[AgentGroup],
        event_queue: Optional[asyncio.Queue] = None,
    ) -> StructuredDebateResult:
        """Run full 4-step debate for all groups, persist everything."""
        result = StructuredDebateResult(session_id=session_id)
        db.reset_sequence(session_id)

        await self._emit(event_queue, session_id, "debate_start", content=f"启动 {len(groups)} 个分组的结构化辩论")

        for group in groups:
            group_result = await self._run_group(session_id, evidence, group, event_queue)
            result.steps.extend(group_result.steps)
            result.events.extend(group_result.events)
            result.snapshots.extend(group_result.snapshots)
            result.interrupts.extend(group_result.interrupts)

        # Final cross-group consensus
        final_snapshot = self._compute_final_consensus(session_id, result.steps)
        if final_snapshot:
            result.snapshots.append(final_snapshot)
            db.insert_consensus_snapshot(
                final_snapshot.snapshot_id, session_id, None, None,
                final_snapshot.dimension_scores,
                final_snapshot.agreement_level.value,
                final_snapshot.consensus_text,
            )

        await self._emit(event_queue, session_id, "debate_end", content="辩论结束")
        result.final_consensus = {
            "agreement_level": final_snapshot.agreement_level.value if final_snapshot else "unknown",
            "consensus_text": final_snapshot.consensus_text if final_snapshot else "",
        }
        return result

    # ── Per-Group Execution ──

    async def _run_group(
        self,
        session_id: str,
        evidence: EvidencePacket,
        group: AgentGroup,
        event_queue: Optional[asyncio.Queue],
    ) -> StructuredDebateResult:
        result = StructuredDebateResult(session_id=session_id)
        group_id = group.group_id
        agents = group.agents

        # Persist group
        db.insert_debate_group(
            group_id, session_id, group.group_name, group.topic,
            [a.id for a in agents], "running",
        )

        await self._emit(event_queue, session_id, "group_start", agent_id=group_id,
                         content=f"分组 {group.group_name} 开始辩论")

        # Build evidence summary
        evidence_summary = self._summarize_evidence(evidence)

        # ── Step 1: Opening Statements ──
        step1_results = await self._run_step1(session_id, group_id, agents, group.topic, evidence_summary, event_queue)
        result.steps.extend(step1_results)

        # ── Step 2: Mandatory Challenges ──
        step2_results = await self._run_step2(session_id, group_id, agents, group.topic, step1_results, event_queue)
        result.steps.extend(step2_results)

        # ── Step 3: New Perspectives ──
        step3_results = await self._run_step3(session_id, group_id, agents, group.topic, step1_results + step2_results, event_queue)
        result.steps.extend(step3_results)

        # ── Step 4: Response → Correction → Consensus ──
        step4_results = await self._run_step4(session_id, group_id, agents, group.topic, step1_results + step2_results + step3_results, event_queue)
        result.steps.extend(step4_results)

        # Consensus snapshot for this group
        snapshot = self._compute_group_consensus(session_id, group_id, step4_results)
        if snapshot:
            result.snapshots.append(snapshot)
            db.insert_consensus_snapshot(
                snapshot.snapshot_id, session_id, group_id, None,
                snapshot.dimension_scores, snapshot.agreement_level.value, snapshot.consensus_text,
            )

        db.update_debate_group_status(group_id, "completed")
        await self._emit(event_queue, session_id, "group_end", agent_id=group_id,
                         content=f"分组 {group.group_name} 辩论完成")

        return result

    # ── Step Runners ──

    async def _run_step1(
        self, session_id: str, group_id: str, agents: list[AgentManifest],
        topic: str, evidence_summary: str, event_queue: Optional[asyncio.Queue],
    ) -> list[DebateStep]:
        await self._emit(event_queue, session_id, "step_start", content="Step 1: 开场陈述")
        tasks = []
        for agent in agents:
            tasks.append(self._single_agent_step1(session_id, group_id, agent, topic, evidence_summary, event_queue))
        return await asyncio.gather(*tasks)

    async def _single_agent_step1(
        self, session_id: str, group_id: str, agent: AgentManifest,
        topic: str, evidence_summary: str, event_queue: Optional[asyncio.Queue],
    ) -> DebateStep:
        await self._emit(event_queue, session_id, "agent_thinking", agent_id=agent.id,
                         content=f"{agent.name} 正在做开场陈述...")

        prompt = _STEP1_PROMPT.format(
            agent_name=agent.name, agent_role=agent.role,
            topic=topic, evidence_summary=evidence_summary,
        )
        raw = await self._call_llm(prompt, max_tokens=800)
        parsed = self._safe_json_parse(raw, {
            "methodology": agent.methodology or "",
            "key_questions": [],
            "data_sources": [],
            "initial_assessment": raw[:300],
            "confidence": 0.5,
        })

        step = DebateStep(
            step_id=f"st1_{uuid.uuid4().hex[:8]}",
            group_id=group_id,
            step_number=1,
            agent_id=agent.id,
            step_type=DebateStepType.STATEMENT,
            content=parsed.get("initial_assessment", ""),
            content_struct=parsed,
            confidence=float(parsed.get("confidence", 0.5)),
            sources=parsed.get("data_sources", []),
        )
        db.insert_debate_step(
            step.step_id, group_id, 1, agent.id, "statement",
            step.content, step.content_struct, step.confidence, [], step.sources,
        )
        await self._emit(event_queue, session_id, "agent_done", agent_id=agent.id,
                         content=step.content, metadata={"step": 1, "confidence": step.confidence})
        return step

    async def _run_step2(
        self, session_id: str, group_id: str, agents: list[AgentManifest],
        topic: str, step1_results: list[DebateStep], event_queue: Optional[asyncio.Queue],
    ) -> list[DebateStep]:
        await self._emit(event_queue, session_id, "step_start", content="Step 2: 强制质疑")
        tasks = []
        for agent in agents:
            peers = [s for s in step1_results if s.agent_id != agent.id]
            tasks.append(self._single_agent_step2(session_id, group_id, agent, peers, event_queue))
        return await asyncio.gather(*tasks)

    async def _single_agent_step2(
        self, session_id: str, group_id: str, agent: AgentManifest,
        peer_steps: list[DebateStep], event_queue: Optional[asyncio.Queue],
    ) -> DebateStep:
        await self._emit(event_queue, session_id, "agent_thinking", agent_id=agent.id,
                         content=f"{agent.name} 正在提出质疑...")

        peer_text = "\n\n".join(
            f"Agent {s.agent_id}:\n{s.content}" for s in peer_steps
        )
        prompt = _STEP2_PROMPT.format(
            agent_name=agent.name, agent_role=agent.role,
            peer_statements=peer_text,
        )
        raw = await self._call_llm(prompt, max_tokens=800)
        parsed = self._safe_json_parse(raw, {"challenges": [], "confidence": 0.5})
        challenges = parsed.get("challenges", [])
        content = "\n".join(
            f"质疑 {c.get('target_agent','?')}: [{c.get('issue_type','?')}] {c.get('reasoning','')}" for c in challenges
        ) or "无明确质疑"

        step = DebateStep(
            step_id=f"st2_{uuid.uuid4().hex[:8]}",
            group_id=group_id,
            step_number=2,
            agent_id=agent.id,
            step_type=DebateStepType.CHALLENGE,
            content=content,
            content_struct=parsed,
            confidence=float(parsed.get("confidence", 0.5)),
        )
        db.insert_debate_step(
            step.step_id, group_id, 2, agent.id, "challenge",
            step.content, step.content_struct, step.confidence,
        )
        await self._emit(event_queue, session_id, "agent_done", agent_id=agent.id,
                         content=step.content, metadata={"step": 2})
        return step

    async def _run_step3(
        self, session_id: str, group_id: str, agents: list[AgentManifest],
        topic: str, previous_steps: list[DebateStep], event_queue: Optional[asyncio.Queue],
    ) -> list[DebateStep]:
        await self._emit(event_queue, session_id, "step_start", content="Step 3: 补充新视角")
        tasks = []
        for agent in agents:
            tasks.append(self._single_agent_step3(session_id, group_id, agent, previous_steps, event_queue))
        return await asyncio.gather(*tasks)

    async def _single_agent_step3(
        self, session_id: str, group_id: str, agent: AgentManifest,
        previous_steps: list[DebateStep], event_queue: Optional[asyncio.Queue],
    ) -> DebateStep:
        await self._emit(event_queue, session_id, "agent_thinking", agent_id=agent.id,
                         content=f"{agent.name} 正在补充新视角...")

        prev_text = "\n\n".join(f"Step {s.step_number} ({s.agent_id}): {s.content[:200]}" for s in previous_steps)
        prompt = _STEP3_PROMPT.format(
            agent_name=agent.name, agent_role=agent.role,
            previous_discussion=prev_text,
        )
        raw = await self._call_llm(prompt, max_tokens=700)
        parsed = self._safe_json_parse(raw, {"new_angles": [], "confidence": 0.5})
        angles = parsed.get("new_angles", [])
        content = "\n".join(
            f"新视角: {a.get('angle_title','')} — {a.get('angle_description','')}" for a in angles
        ) or "无新视角补充"

        step = DebateStep(
            step_id=f"st3_{uuid.uuid4().hex[:8]}",
            group_id=group_id,
            step_number=3,
            agent_id=agent.id,
            step_type=DebateStepType.NEW_PERSPECTIVE,
            content=content,
            content_struct=parsed,
            confidence=float(parsed.get("confidence", 0.5)),
        )
        db.insert_debate_step(
            step.step_id, group_id, 3, agent.id, "new_perspective",
            step.content, step.content_struct, step.confidence,
        )
        await self._emit(event_queue, session_id, "agent_done", agent_id=agent.id,
                         content=step.content, metadata={"step": 3})
        return step

    async def _run_step4(
        self, session_id: str, group_id: str, agents: list[AgentManifest],
        topic: str, all_steps: list[DebateStep], event_queue: Optional[asyncio.Queue],
    ) -> list[DebateStep]:
        await self._emit(event_queue, session_id, "step_start", content="Step 4: 回应与共识")
        tasks = []
        for agent in agents:
            tasks.append(self._single_agent_step4(session_id, group_id, agent, all_steps, event_queue))
        return await asyncio.gather(*tasks)

    async def _single_agent_step4(
        self, session_id: str, group_id: str, agent: AgentManifest,
        all_steps: list[DebateStep], event_queue: Optional[asyncio.Queue],
    ) -> DebateStep:
        await self._emit(event_queue, session_id, "agent_thinking", agent_id=agent.id,
                         content=f"{agent.name} 正在形成最终立场...")

        full_text = "\n\n".join(f"Step {s.step_number} ({s.agent_id}): {s.content[:200]}" for s in all_steps)
        prompt = _STEP4_PROMPT.format(
            agent_name=agent.name, agent_role=agent.role,
            full_discussion=full_text,
        )
        raw = await self._call_llm(prompt, max_tokens=800)
        parsed = self._safe_json_parse(raw, {
            "response_to_challenges": "", "corrections_made": [],
            "final_position": raw[:300], "consensus_assessment": {}, "confidence": 0.5,
        })
        content = parsed.get("final_position", "")
        assessment = parsed.get("consensus_assessment", {})

        step = DebateStep(
            step_id=f"st4_{uuid.uuid4().hex[:8]}",
            group_id=group_id,
            step_number=4,
            agent_id=agent.id,
            step_type=DebateStepType.CONSENSUS,
            content=content,
            content_struct=parsed,
            confidence=float(parsed.get("confidence", 0.5)),
        )
        db.insert_debate_step(
            step.step_id, group_id, 4, agent.id, "consensus",
            step.content, step.content_struct, step.confidence,
        )
        await self._emit(event_queue, session_id, "agent_done", agent_id=agent.id,
                         content=step.content, metadata={"step": 4, "assessment": assessment})
        return step

    # ── User Interrupt ──

    async def handle_interrupt(
        self,
        session_id: str,
        user_id: str,
        interrupt_type: str,
        content: str,
        target_agent_id: Optional[str] = None,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> UserInterrupt:
        """Process a user interrupt and request target agent to respond."""
        interrupt = UserInterrupt(
            interrupt_id=f"int_{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            user_id=user_id,
            interrupt_type=interrupt_type,
            target_agent_id=target_agent_id,
            content=content,
            timestamp=datetime.now(timezone.utc),
        )
        db.insert_user_interrupt(
            interrupt.interrupt_id, session_id, user_id,
            interrupt_type, target_agent_id, content,
            interrupt.timestamp.isoformat(),
        )
        await self._emit(event_queue, session_id, "user_interrupt", agent_id=target_agent_id,
                         content=content, metadata={"type": interrupt_type})

        return interrupt

    # ── Consensus Computation ──

    def _compute_group_consensus(
        self, session_id: str, group_id: str, step4_results: list[DebateStep],
    ) -> Optional[ConsensusSnapshot]:
        """Compute consensus snapshot from Step 4 results."""
        if not step4_results:
            return None

        # Simple heuristic: count how many agents agree on key phrases
        contents = [s.content for s in step4_results]
        # Check for fatal_flaws and high_risk_flaws
        fatal_counts = sum(
            1 for s in step4_results
            if s.content_struct.get("consensus_assessment", {}).get("fatal_flaws")
        )

        # Agreement level heuristic
        if len(step4_results) >= 2:
            agreement = AgreementLevel.PARTIAL_CONSENSUS
        elif len(step4_results) == 1:
            agreement = AgreementLevel.UNKNOWN
        else:
            agreement = AgreementLevel.UNKNOWN

        # Extract dimension scores from agent manifest score_dimension
        dimension_scores: dict = {}
        for s in step4_results:
            dimension_scores[s.agent_id] = s.confidence

        return ConsensusSnapshot(
            snapshot_id=f"snap_{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            group_id=group_id,
            dimension_scores=dimension_scores,
            agreement_level=agreement,
            consensus_text="\n".join(contents)[:500],
        )

    def _compute_final_consensus(
        self, session_id: str, all_steps: list[DebateStep],
    ) -> Optional[ConsensusSnapshot]:
        """Compute cross-group final consensus."""
        step4 = [s for s in all_steps if s.step_number == 4]
        return self._compute_group_consensus(session_id, None, step4)

    # ── Helpers ──

    async def _call_llm(self, prompt: str, max_tokens: int) -> str:
        """Call LLM provider or return mock response."""
        if self.provider is not None:
            try:
                return await self.provider.chat(
                    system_prompt="你是圆桌专家助手。请严格输出 JSON 格式。",
                    user_message=prompt,
                    max_tokens=max_tokens,
                    temperature=0.6,
                )
            except Exception as e:
                logger.warning("LLM call failed, using mock: %s", e)
        return self._mock_response(prompt)

    def _mock_response(self, prompt: str) -> str:
        """Generate a plausible mock JSON response when no provider is available."""
        if "Step 1" in prompt or "开场陈述" in prompt:
            return json.dumps({
                "methodology": "用户旅程分析",
                "key_questions": ["目标用户是谁？", "核心痛点是什么？", "竞品如何解决？"],
                "data_sources": ["用户访谈", "竞品分析报告"],
                "initial_assessment": "这个话题有潜力，但需要更多用户验证。建议先做一个最小可行产品来测试假设。",
                "confidence": 0.75,
            }, ensure_ascii=False)
        if "Step 2" in prompt or "强制质疑" in prompt:
            return json.dumps({
                "challenges": [
                    {
                        "target_agent": "peer_1",
                        "target_claim": "市场很大",
                        "issue_type": "unsupported_claim",
                        "reasoning": "没有提供具体的市场规模数据来源",
                        "suggested_correction": "请引用第三方市场研究报告",
                    }
                ],
                "confidence": 0.8,
            }, ensure_ascii=False)
        if "Step 3" in prompt or "新视角" in prompt:
            return json.dumps({
                "new_angles": [
                    {
                        "angle_title": "监管合规风险",
                        "angle_description": "如果产品涉及用户数据收集，需要考虑 GDPR 和国内数据安全法的合规要求。",
                        "why_missed": "前两轮讨论集中在商业和技术层面，忽略了法律合规维度。",
                        "implication": "如果忽略合规，可能导致产品上线后被罚款或下架。",
                    }
                ],
                "confidence": 0.7,
            }, ensure_ascii=False)
        if "Step 4" in prompt or "回应" in prompt:
            return json.dumps({
                "response_to_challenges": "接受关于数据来源的质疑，会补充用户调研数据。",
                "corrections_made": ["修正了市场规模的断言，改为引用第三方数据"],
                "final_position": "项目在方向上可行，但需要补充用户验证和合规审查。建议先做 2 周的用户访谈。",
                "consensus_assessment": {
                    "fatal_flaws": [],
                    "high_risk_flaws": ["缺乏用户数据支撑"],
                    "improvement_suggestions": ["补充 10 个目标用户访谈", "做竞品功能对比表"],
                },
                "confidence": 0.78,
            }, ensure_ascii=False)
        return json.dumps({"content": "Mock response", "confidence": 0.5}, ensure_ascii=False)

    def _safe_json_parse(self, raw: str, default: dict) -> dict:
        """Safely parse LLM JSON response."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # Remove markdown code fences
            lines = cleaned.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("JSON parse failed, using default. Raw: %r", raw[:200])
            return default

    def _summarize_evidence(self, evidence: EvidencePacket) -> str:
        """Create a text summary of evidence for prompts."""
        chunks = evidence.transcript_chunks[:10]  # Limit to first 10 chunks
        return "\n".join(f"- {c.speaker}: {c.text}" for c in chunks)

    async def _emit(
        self,
        event_queue: Optional[asyncio.Queue],
        session_id: str,
        event_type: str,
        agent_id: Optional[str] = None,
        content: str = "",
        metadata: Optional[dict] = None,
    ) -> None:
        """Emit an event to the queue and persist to DB."""
        self._event_seq += 1
        db.insert_debate_event(session_id, event_type, agent_id, content, metadata)
        if event_queue is not None:
            try:
                await event_queue.put({
                    "type": event_type,
                    "agent": agent_id or "",
                    "content": content,
                    "meta": metadata or {},
                    "seq": self._event_seq,
                })
            except Exception:
                pass
