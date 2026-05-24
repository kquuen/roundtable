"""Phase 6: 两轮辩论调度引擎 + 引用完整性校验。

核心流程：
  Round 1: N 个 Agent 并发独立分析（reuse orchestrator）
  Round 2: 每个 Agent 看到其他 (N-1) 人的 Round 1 结果 → 质疑/同意/修正
  Converge: Supervisor 纯函数处理 → 共识分层
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from roundtable.models import (
    DebateArgument, DebateRound, DebateSession,
    AgentReview, EvidenceClaim, EvidencePacket,
    ClaimLifecycle, ConsensusLevel,
)

logger = logging.getLogger("roundtable.debate")


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
        from roundtable.orchestrator import run_orchestrator_async, run_orchestrator

        if self.provider is not None:
            return await run_orchestrator_async(
                evidence, agent_count=len(agents), provider=self.provider,
                domain_name=self.domain_name,
            )
        return run_orchestrator(evidence, agent_count=len(agents), domain_name=self.domain_name)

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

        if self.provider is not None:
            # LLM 路径: 调用 agent.analyze_async with peer_reviews
            review = await agent.analyze_async(evidence, peer_reviews=peers)
        else:
            # Mock 降级: 模板化辩论
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
