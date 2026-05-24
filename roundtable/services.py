"""Service Layer — unified roundtable pipeline orchestration.

Extracted from app.py and main.py so CLI and API share the same entry point.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from roundtable.models import PipelineResult
from roundtable.evidence import build_evidence_packet
from roundtable.orchestrator import run_orchestrator, run_orchestrator_async
from roundtable.supervisor import review_claims, review_claims_async
from roundtable.report import compose_report
from roundtable.providers import ProviderAdapter
from roundtable.store import SessionStore, ReportStore
from roundtable.memory import MemoryStore
from roundtable.skills import load_skill
from roundtable.utils import run_async_safely

logger = logging.getLogger("roundtable.services")


# ── Token Budget ──

class BudgetExceeded(Exception):
    """Token 预算超限异常。"""

    def __init__(self, used: int, limit: int, stage: str):
        self.used = used
        self.limit = limit
        self.stage = stage
        super().__init__(f"Token budget exceeded at {stage}: {used}/{limit}")


class TokenBudget:
    """Token 用量追踪器。每次 LLM 调用后累加，超限抛 BudgetExceeded。"""

    def __init__(self, max_tokens: int = 80000):
        self.max_tokens = max_tokens
        self.used = 0
        self.calls = 0

    def estimate(self, input_chars: int, output_chars: int) -> int:
        """估算一次调用的 token 用量（简化为 chars/2）。"""
        return (input_chars + output_chars) // 2 + 10

    def consume(self, tokens: int, stage: str = "unknown") -> None:
        """消耗 tokens，超限抛异常。"""
        self.used += tokens
        self.calls += 1
        if self.used > self.max_tokens:
            raise BudgetExceeded(self.used, self.max_tokens, stage)

    def remaining(self) -> int:
        return max(0, self.max_tokens - self.used)

    def summary(self) -> str:
        return f"Tokens: {self.used}/{self.max_tokens} ({self.calls} calls, {self.remaining()} remaining)"


class RoundtableService:
    """Unified pipeline orchestrator shared by CLI and API.

    Handles the full roundtable lifecycle:
        evidence → agents → supervisor → memory → report → archive

    Dependencies (all optional — service degrades gracefully):
        provider: LLM adapter (None = mock mode)
        session_store: persistence for sessions/evidence
        report_store: persistence for generated reports
        memory_store: auto-write high-confidence claims to memory
    """

    def __init__(
        self,
        provider: ProviderAdapter | None = None,
        session_store: SessionStore | None = None,
        report_store: ReportStore | None = None,
        memory_store: MemoryStore | None = None,
    ):
        self.provider = provider
        self.session_store = session_store
        self.report_store = report_store
        self.memory_store = memory_store

    async def run_debate_pipeline(
        self,
        session_id: str,
        segments: list[dict],
        mode: str = "meeting",
        title: str = "",
        agent_count: int = 5,
        lang: str = "zh",
        max_tokens: int = 80000,
    ) -> dict:
        """Run a two-round debate pipeline (Phase 6).

        与 run_pipeline() 并行存在，走独立路径。
        """
        from roundtable.debate import DebateEngine
        from roundtable.orchestrator import create_agents as _create_agents

        budget = TokenBudget(max_tokens=max_tokens)
        logger.info("[%s] Debate pipeline start: mode=%s, agents=%d", session_id, mode, agent_count)

        # 1. Evidence
        evidence = build_evidence_packet(session_id, mode, segments)

        # 2. Create agents
        agents = _create_agents(agent_count=agent_count, provider=self.provider)

        # 3. Run debate
        engine = DebateEngine(provider=self.provider, budget=budget)
        debate_session = await engine.run_debate(evidence, agents)

        # 4. Build report
        from roundtable.report import compose_debate_report
        report = compose_debate_report(debate_session, session_title=title, lang=lang)

        logger.info("[%s] Debate pipeline complete: %d rounds, %s",
                     session_id, len(debate_session.rounds), budget.summary())

        return {
            "session_id": session_id,
            "mode": "debate",
            "rounds": len(debate_session.rounds),
            "arguments": sum(len(r.arguments) for r in debate_session.rounds),
            "consensus_items": len(debate_session.consensus_summary),
            "conflicts": len(debate_session.conflicts),
            "report": report,
        }

    async def run_pipeline(
        self,
        session_id: str,
        segments: list[dict],
        mode: str = "meeting",
        title: str = "",
        agent_count: int = 5,
        lang: str = "zh",
        max_tokens: int = 80000,
        domain_name: str | None = None,
    ) -> PipelineResult:
        """Run the complete roundtable pipeline.

        Args:
            session_id: Session identifier
            segments: List of {"speaker": "...", "text": "..."} dicts
            mode: "meeting" or "personal_roundtable"
            title: Session title (used in report header)
            agent_count: 1-5 agents to dispatch
            lang: "zh" for Chinese, "en" for English report
            max_tokens: Token budget limit (default 80K)
            domain_name: Override domain classification (auto-detected if None)

        Returns:
            PipelineResult with report, reviews, and metadata
        """
        budget = TokenBudget(max_tokens=max_tokens)

        # 1. Evidence
        logger.info("[%s] Pipeline start: mode=%s, agents=%d, budget=%d",
                     session_id, mode, agent_count, max_tokens)
        evidence = build_evidence_packet(session_id, mode, segments)
        logger.info("[%s] Evidence built: %d chunks", session_id, len(evidence.transcript_chunks))

        # 0. Domain classification (after evidence is built)
        from roundtable.domain import classify_domain
        if domain_name is None:
            domain = await classify_domain(evidence, provider=self.provider)
            domain_name = domain.name
        logger.info("[%s] Domain classified: %s", session_id, domain_name)

        # 2. Agent analysis (async with provider, sync without)
        if self.provider is not None:
            agent_reviews = await run_orchestrator_async(
                evidence,
                agent_count=agent_count,
                provider=self.provider,
                domain_name=domain_name,
            )
        else:
            agent_reviews = run_orchestrator(evidence, agent_count=agent_count, domain_name=domain_name)
        logger.info("[%s] Agent analysis complete: %d reviews", session_id, len(agent_reviews))

        # Budget: estimate agent analysis cost
        analysis_chars = sum(
            len(c.content) for ar in agent_reviews for c in ar.claims
        ) + sum(len(ar.summary) for ar in agent_reviews)
        budget.consume(budget.estimate(0, analysis_chars), "agent_analysis")

        # 3. Supervisor review (with forbidden rules from skill registry)
        agent_ids = list({ar.agent_id for ar in agent_reviews})
        agent_forbidden = _build_forbidden_map(agent_ids)
        supervisor_reviews = await review_claims_async(
            agent_reviews, evidence,
            mode=mode, provider=self.provider,
            agent_forbidden=agent_forbidden,
        )
        logger.info("[%s] Supervisor review complete: %d claims reviewed", session_id, len(supervisor_reviews))

        # Budget: estimate contradiction detection
        budget.consume(budget.estimate(3000, 2000), "supervisor_review")

        # 4. Memory — auto-write high-confidence approved claims
        memories_written = 0
        if self.memory_store is not None:
            written = self.memory_store.write_from_reviews(
                session_id, agent_reviews, supervisor_reviews,
            )
            memories_written = len(written)

        # 5. Report
        report = compose_report(agent_reviews, supervisor_reviews, session_title=title, lang=lang)

        # 6. Count pending confirmations
        from roundtable.models import ReviewResult
        pending_count = sum(
            1 for sr in supervisor_reviews
            if sr.review_result == ReviewResult.NEEDS_USER_CONFIRMATION
        )

        # 7. Archive report
        report_path = ""
        if self.report_store is not None:
            path = self.report_store.save(session_id, title or "Untitled", report)
            report_path = str(path)

        logger.info("[%s] Pipeline complete: memories=%d, pending=%d, %s", session_id, memories_written, pending_count, budget.summary())

        return PipelineResult(
            session_id=session_id,
            mode="llm" if self.provider else "mock",
            domain_name=domain_name,
            agent_reviews=[ar.model_dump() for ar in agent_reviews],
            supervisor_reviews=[sr.model_dump() for sr in supervisor_reviews],
            report=report,
            report_path=report_path,
            memories_written=memories_written,
            pending_confirmation_count=pending_count,
        )

    def run_pipeline_sync(
        self,
        session_id: str,
        segments: list[dict],
        mode: str = "meeting",
        title: str = "",
        agent_count: int = 5,
        lang: str = "zh",
    ) -> PipelineResult:
        """Synchronous wrapper for CLI usage."""
        return run_async_safely(
            self.run_pipeline(session_id, segments, mode, title, agent_count, lang),
            name="run_pipeline_sync — use run_pipeline() directly in async context",
        )


def _build_forbidden_map(agent_ids: list[str]) -> dict[str, list[str]]:
    """Build a mapping of agent_id → forbidden rules from the skill registry.

    This is the integration point between the supervisor (which needs
    forbidden rules) and the skill registry. It lives in the service layer
    so the supervisor stays decoupled from skills.py.
    """
    result: dict[str, list[str]] = {}
    for aid in agent_ids:
        try:
            skill = load_skill(aid)
            result[aid] = skill.forbidden
        except KeyError:
            result[aid] = []
    return result
