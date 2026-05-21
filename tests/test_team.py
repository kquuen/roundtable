"""Phase 4: Team Builder tests."""

import pytest
from roundtable.models import TranscriptChunk, EvidencePacket
from roundtable.team import (
    classify_session, recommend_teams, get_team, list_teams,
    BUILTIN_TEAMS,
)


class TestClassifySession:
    def test_tech_session(self):
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s", speaker="A", text="后端架构和协议先定死。")]
        evidence = EvidencePacket(session_id="s", transcript_chunks=chunks)
        assert classify_session(evidence) == "技术"

    def test_product_session(self):
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s", speaker="A", text="用户体验和产品功能讨论。")]
        evidence = EvidencePacket(session_id="s", transcript_chunks=chunks)
        assert classify_session(evidence) == "产品"

    def test_personal_roundtable(self):
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s", speaker="User", text="我在思考")]
        evidence = EvidencePacket(session_id="s", mode="personal_roundtable", transcript_chunks=chunks)
        assert classify_session(evidence) == "个人"

    def test_default_fallback(self):
        chunks = [TranscriptChunk(chunk_id="t_0", session_id="s", speaker="A", text="hello world")]
        evidence = EvidencePacket(session_id="s", transcript_chunks=chunks)
        result = classify_session(evidence)
        assert result in ("技术", "产品", "探索")


class TestRecommendTeams:
    def test_recommend_for_tech(self):
        teams = recommend_teams("技术")
        assert len(teams) >= 1
        assert teams[0].team_id == "tech_review"

    def test_recommend_for_product(self):
        teams = recommend_teams("产品")
        assert teams[0].team_id == "product_deep_dive"

    def test_recommend_caps_at_n(self):
        teams = recommend_teams("技术", top_n=1)
        assert len(teams) == 1


class TestTeamRegistry:
    def test_list_teams(self):
        assert len(list_teams()) >= 4

    def test_get_valid_team(self):
        t = get_team("tech_review")
        assert t is not None
        assert t.name == "技术审查队"

    def test_get_invalid_team(self):
        assert get_team("nonexistent") is None
