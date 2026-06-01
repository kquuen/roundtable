"""Service Layer — unified roundtable pipeline orchestration.

Extracted from app.py and main.py so CLI and API share the same entry point.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from roundtable.models import PipelineResult, ReviewResult
from roundtable.evidence import build_evidence_packet
from roundtable.orchestrator import run_orchestrator, run_orchestrator_async
from roundtable.supervisor import review_claims, review_claims_async
from roundtable.report import compose_report
from roundtable.store import SessionStore, ReportStore
from roundtable.memory import MemoryStore
from roundtable.skills import load_skill
from roundtable.utils import run_async_safely

logger = logging.getLogger("roundtable.services")
_PROVIDER_UNSET = object()


async def _emit_stage(queue: Optional[asyncio.Queue], stage: str, idx: int, extra: Optional[dict] = None) -> None:
    """Push a pipeline stage event to the SSE queue."""
    if queue is None:
        return
    payload = {"type": "stage", "stage": stage, "idx": idx}
    if extra:
        payload.update(extra)
    await queue.put(payload)


def _has_llm_provider(agent_reviews) -> bool:
    """Infer whether any agent review came from an LLM-backed agent."""
    return any(getattr(ar, "provider", None) for ar in agent_reviews)


def _resolve_mode(agent_reviews, provider_explicit: bool, provider) -> str:
    """Resolve response mode based on explicit provider intent and runtime agent state."""
    if provider_explicit:
        return "llm" if provider is not None else "mock"
    return "llm" if _has_llm_provider(agent_reviews) else "mock"


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
        provider=_PROVIDER_UNSET,
        session_store: SessionStore | None = None,
        report_store: ReportStore | None = None,
        memory_store: MemoryStore | None = None,
    ):
        self.provider = provider
        self._provider_explicit = provider is not _PROVIDER_UNSET
        if provider is _PROVIDER_UNSET:
            self.provider = None
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
        event_queue: Optional[asyncio.Queue] = None,
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
        await _emit_stage(event_queue, "evidence", 0)

        # 0. Domain classification
        from roundtable.domain import classify_domain
        domain = await classify_domain(evidence, provider=self.provider)
        domain_name = domain.name
        logger.info("[%s] Debate domain classified: %s", session_id, domain_name)
        await _emit_stage(event_queue, "domain", 0)

        # 2. Create agents
        if self._provider_explicit:
            agents = _create_agents(
                agent_count=agent_count, provider=self.provider, domain_name=domain_name,
            )
        else:
            agents = _create_agents(agent_count=agent_count, domain_name=domain_name)
        await _emit_stage(event_queue, "agents", 1, {"agent_count": len(agents)})

        # 3. Run debate
        engine = DebateEngine(provider=self.provider, budget=budget, domain_name=domain_name)
        debate_session = await engine.run_debate(evidence, agents)
        await _emit_stage(event_queue, "review", 2, {"rounds": len(debate_session.rounds)})

        # 4. Build report
        from roundtable.report import compose_debate_report
        report = compose_debate_report(debate_session, session_title=title, lang=lang)
        await _emit_stage(event_queue, "report", 4)

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
        event_queue: Optional[asyncio.Queue] = None,
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
        await _emit_stage(event_queue, "evidence", 0, {"chunks": len(evidence.transcript_chunks)})

        # 0. Domain classification (after evidence is built)
        from roundtable.domain import classify_domain
        if domain_name is None:
            domain = await classify_domain(evidence, provider=self.provider)
            domain_name = domain.name
        logger.info("[%s] Domain classified: %s", session_id, domain_name)
        await _emit_stage(event_queue, "domain", 0, {"domain": domain_name})

        # 2. Agent analysis
        await _emit_stage(event_queue, "agents", 1, {"agent_count": agent_count})
        auto_llm_attempted = False
        if self._provider_explicit:
            if self.provider is not None:
                agent_reviews = await run_orchestrator_async(
                    evidence,
                    agent_count=agent_count,
                    provider=self.provider,
                    domain_name=domain_name,
                    budget=budget,
                )
            else:
                agent_reviews = run_orchestrator(
                    evidence,
                    agent_count=agent_count,
                    provider=None,
                    domain_name=domain_name,
                )
        else:
            agent_reviews, meta = await run_orchestrator_async(
                evidence,
                agent_count=agent_count,
                domain_name=domain_name,
                budget=budget,
                return_meta=True,
            )
            auto_llm_attempted = bool(meta.get("llm_attempted"))
        if self._provider_explicit:
            auto_llm_attempted = False
        logger.info("[%s] Agent analysis complete: %d reviews", session_id, len(agent_reviews))
        await _emit_stage(event_queue, "review", 2, {"reviews": len(agent_reviews)})

        # Budget: estimate agent analysis cost
        analysis_chars = sum(
            len(c.content) for ar in agent_reviews for c in ar.claims
        ) + sum(len(ar.summary) for ar in agent_reviews)
        budget.consume(budget.estimate(0, analysis_chars), "agent_analysis")

        # 3. Supervisor review
        agent_ids = list({ar.agent_id for ar in agent_reviews})
        agent_forbidden = _build_forbidden_map(agent_ids)
        supervisor_reviews = await review_claims_async(
            agent_reviews, evidence,
            mode=mode, provider=self.provider,
            agent_forbidden=agent_forbidden,
        )
        logger.info("[%s] Supervisor review complete: %d claims reviewed", session_id, len(supervisor_reviews))

        # Persist reviews
        if self.session_store is not None:
            self.session_store.store_reviews(session_id, agent_reviews, supervisor_reviews)

        # Budget: estimate contradiction detection
        budget.consume(budget.estimate(3000, 2000), "supervisor_review")

        # 3.5. Search Verification
        await self._search_verify_pending(
            session_id, supervisor_reviews, agent_reviews, budget,
        )

        # 4. Memory
        memories_written = 0
        if self.memory_store is not None:
            written = self.memory_store.write_from_reviews(
                session_id, agent_reviews, supervisor_reviews,
            )
            memories_written = len(written)
        await _emit_stage(event_queue, "memory", 3, {"memories": memories_written})

        # 5. Report
        report = compose_report(agent_reviews, supervisor_reviews, session_title=title, lang=lang)
        await _emit_stage(event_queue, "report", 4)

        # 6. Count pending confirmations
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
            mode=(
                _resolve_mode(agent_reviews, self._provider_explicit, self.provider)
                if self._provider_explicit
                else ("llm" if auto_llm_attempted else "mock")
            ),
            domain_name=domain_name,
            agent_reviews=[ar.model_dump() for ar in agent_reviews],
            supervisor_reviews=[sr.model_dump() for sr in supervisor_reviews],
            report=report,
            report_path=report_path,
            memories_written=memories_written,
            pending_confirmation_count=pending_count,
        )

    async def _search_verify_pending(
        self, session_id: str, supervisor_reviews, agent_reviews, budget,
        max_search_claims: int = 3,
    ) -> None:
        """Phase 7A: 对 NEEDS_USER_CONFIRMATION 的 claim 做搜索校验。"""
        from roundtable.search import SearchAdapter
        from roundtable.verify import verify_pending_claims

        pending = [
            sr for sr in supervisor_reviews
            if sr.review_result == ReviewResult.NEEDS_USER_CONFIRMATION
        ]
        if not pending:
            return

        logger.info("[%s] Search-verify: %d pending claims", session_id, len(pending))

        import os
        search_backend = "serpapi" if os.getenv("SERPAPI_API_KEY") else "mock"
        adapter = SearchAdapter(backend=search_backend)
        search_results = {}

        to_search = pending if max_search_claims <= 0 else pending[:max_search_claims]
        skipped = len(pending) - len(to_search)
        if skipped > 0:
            logger.info(
                "[%s] Search-verify: skipping %d/%d claims (max_search_claims=%d)",
                session_id, skipped, len(pending), max_search_claims,
            )

        for sr in to_search:
            query = _claim_to_query(sr.claim_id, agent_reviews)
            if not query:
                continue
            result = await adapter.search(query)
            search_results[query] = result

        if search_results:
            await verify_pending_claims(
                supervisor_reviews, agent_reviews, search_results, provider=self.provider,
            )
            logger.info("[%s] Search-verify complete: %d queries", session_id, len(search_results))

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
    """Build a mapping of agent_id → forbidden rules from the skill registry."""
    result: dict[str, list[str]] = {}
    for aid in agent_ids:
        try:
            skill = load_skill(aid)
            result[aid] = skill.forbidden
        except KeyError:
            result[aid] = []
    return result


def _claim_to_query(claim_id: str, agent_reviews) -> str:
    """Generate a search query from a claim's content."""
    for ar in agent_reviews:
        for claim in ar.claims:
            if claim.claim_id == claim_id:
                return claim.content[:100]
    return ""
