"""Phase 7C: Domain adaptation tests."""

import pytest
from roundtable.domain import DomainRegistry, classify_domain
from roundtable.models import DomainConfig, EvidencePacket, TranscriptChunk


def _make_chunk(text: str, session_id: str = "s_test") -> TranscriptChunk:
    return TranscriptChunk(chunk_id="c1", session_id=session_id, speaker="A", text=text)


def _make_evidence(text: str, mode: str = "meeting", session_id: str = "s_test") -> EvidencePacket:
    return EvidencePacket(
        session_id=session_id, mode=mode,
        transcript_chunks=[_make_chunk(text, session_id)],
    )


class TestDomainRegistry:
    def test_loads_4_domains(self):
        configs = DomainRegistry.load()
        assert len(configs) >= 4

    def test_each_domain_has_agents(self):
        for d in DomainRegistry.load():
            assert len(d.agents) >= 2, f"{d.name} has <2 agents"

    def test_get_by_name(self):
        d = DomainRegistry.get("personal_roundtable")
        assert d is not None
        assert d.display == "个人圆桌"

    def test_get_nonexistent(self):
        assert DomainRegistry.get("nonexistent") is None

    def test_list_all_returns_same_length(self):
        a = DomainRegistry.list_all()
        b = DomainRegistry.list_all()
        assert len(a) == len(b)

    def test_reload(self):
        before = DomainRegistry.load()
        after = DomainRegistry.reload()
        assert len(before) == len(after)


class TestClassifyDomain:
    @pytest.mark.asyncio
    async def test_personal_roundtable_by_mode(self):
        evidence = _make_evidence("思考", mode="personal_roundtable")
        domain = await classify_domain(evidence)
        assert domain.name == "personal_roundtable"

    @pytest.mark.asyncio
    async def test_tech_keywords(self):
        evidence = _make_evidence("架构设计 数据库选型 性能优化")
        domain = await classify_domain(evidence)
        assert domain.name == "tech_review"

    @pytest.mark.asyncio
    async def test_product_keywords(self):
        evidence = _make_evidence("用户体验 功能需求 竞品分析")
        domain = await classify_domain(evidence)
        assert domain.name == "product_brainstorm"

    @pytest.mark.asyncio
    async def test_team_meeting_keywords(self):
        evidence = _make_evidence("团队会议 项目进度 迭代 sprint 需求评审")
        domain = await classify_domain(evidence)
        assert domain.name == "team_meeting"

    @pytest.mark.asyncio
    async def test_fallback_personal(self):
        evidence = _make_evidence("xyzzy unknown words")
        domain = await classify_domain(evidence)
        assert domain.name == "personal_roundtable"


class TestOrchestratorDomain:
    def test_create_agents_with_domain(self):
        from roundtable.orchestrator import create_agents
        agents = create_agents(domain_name="personal_roundtable")
        assert len(agents) >= 2

    def test_create_agents_with_domain_tech(self):
        from roundtable.orchestrator import create_agents
        agents = create_agents(domain_name="tech_review")
        assert len(agents) >= 1

    def test_run_orchestrator_with_domain(self):
        from roundtable.orchestrator import run_orchestrator
        from roundtable.evidence import build_evidence_packet
        evidence = build_evidence_packet("test", "meeting", [
            {"speaker": "A", "text": "架构评审"},
        ])
        reviews = run_orchestrator(evidence, domain_name="tech_review")
        assert len(reviews) >= 1


class TestTeamRecommendDomain:
    def test_recommend_with_domain_name(self):
        from roundtable.team import recommend_teams
        teams = recommend_teams(domain_name="tech_review")
        assert len(teams) >= 1
        assert teams[0].team_id == "tech_review"
