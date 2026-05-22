"""Service Layer — unified roundtable pipeline orchestration.

Extracted from app.py and main.py so CLI and API share the same entry point.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from roundtable.models import PipelineResult
from roundtable.evidence import build_evidence_packet
from roundtable.orchestrator import run_orchestrator, run_orchestrator_async
from roundtable.supervisor import review_claims
from roundtable.report import compose_report
from roundtable.providers import ProviderAdapter
from roundtable.store import SessionStore, ReportStore
from roundtable.memory import MemoryStore
from roundtable.skills import load_skill


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

    async def run_pipeline(
        self,
        session_id: str,
        segments: list[dict],
        mode: str = "meeting",
        title: str = "",
        agent_count: int = 5,
    ) -> PipelineResult:
        """Run the complete roundtable pipeline.

        Args:
            session_id: Session identifier
            segments: List of {"speaker": "...", "text": "..."} dicts
            mode: "meeting" or "personal_roundtable"
            title: Session title (used in report header)
            agent_count: 1-5 agents to dispatch

        Returns:
            PipelineResult with report, reviews, and metadata
        """
        # 1. Evidence
        evidence = build_evidence_packet(session_id, mode, segments)

        # 2. Agent analysis (async with provider, sync without)
        if self.provider is not None:
            agent_reviews = await run_orchestrator_async(
                evidence,
                agent_count=agent_count,
                provider=self.provider,
            )
        else:
            agent_reviews = run_orchestrator(evidence, agent_count=agent_count)

        # 3. Supervisor review (with forbidden rules from skill registry)
        agent_ids = list({ar.agent_id for ar in agent_reviews})
        agent_forbidden = _build_forbidden_map(agent_ids)
        supervisor_reviews = review_claims(
            agent_reviews, evidence,
            mode=mode, provider=self.provider,
            agent_forbidden=agent_forbidden,
        )

        # 4. Memory — auto-write high-confidence approved claims
        memories_written = 0
        if self.memory_store is not None:
            written = self.memory_store.write_from_reviews(
                session_id, agent_reviews, supervisor_reviews,
            )
            memories_written = len(written)

        # 5. Report
        report = compose_report(agent_reviews, supervisor_reviews, session_title=title)

        # 6. Archive report
        report_path = ""
        if self.report_store is not None:
            path = self.report_store.save(session_id, title or "Untitled", report)
            report_path = str(path)

        return PipelineResult(
            session_id=session_id,
            mode="llm" if self.provider else "mock",
            agent_reviews=[ar.model_dump() for ar in agent_reviews],
            supervisor_reviews=[sr.model_dump() for sr in supervisor_reviews],
            report=report,
            report_path=report_path,
            memories_written=memories_written,
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


    def run_pipeline_sync(
        self,
        session_id: str,
        segments: list[dict],
        mode: str = "meeting",
        title: str = "",
        agent_count: int = 5,
    ) -> PipelineResult:
        """Synchronous wrapper for CLI usage."""
        return asyncio.run(
            self.run_pipeline(session_id, segments, mode, title, agent_count)
        )
