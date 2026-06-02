"""Tests for Agent Registry V2 — matching, grouping, and registry loading."""

import pytest
from roundtable.agent_matcher import (
    AgentRegistryV2,
    AgentMatcher,
    extract_keywords,
    jaccard_similarity,
    match_agent,
    build_groups,
    get_matcher,
)
from roundtable.models import AgentManifest


class TestKeywordExtraction:
    def test_extract_chinese_keywords(self):
        text = "帮双非学生写简历的AI工具，聚焦求职辅导"
        kws = extract_keywords(text)
        assert "双非" in kws
        assert "学生" in kws
        assert "简历" in kws
        assert "ai" in kws
        assert "工具" in kws
        assert "求职" in kws
        assert "辅导" in kws

    def test_extract_english_keywords(self):
        text = "Build a SaaS product for remote teams using React"
        kws = extract_keywords(text)
        assert "saas" in kws
        assert "product" in kws
        assert "remote_teams" in kws or "teams" in kws
        assert "react" in kws

    def test_deduplication(self):
        text = "产品产品产品需求需求"
        kws = extract_keywords(text)
        assert kws.count("产品") <= 1


class TestJaccardSimilarity:
    def test_identical_sets(self):
        assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0

    def test_no_overlap(self):
        assert jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        assert jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"}) == 0.5

    def test_empty_sets(self):
        assert jaccard_similarity(set(), set()) == 0.0


class TestMatchAgent:
    def test_exact_keyword_match(self):
        agent = AgentManifest(
            id="product_manager",
            name="产品经理",
            keywords=["产品", "用户", "需求"],
            methodology="JTBD",
        )
        user_tokens = {"产品", "用户", "增长"}
        result = match_agent(user_tokens, agent)
        assert result.match_score > 0.0
        assert "产品" in result.matched_keywords or "用户" in result.matched_keywords
        assert 0.0 <= result.final_score <= 1.0

    def test_no_match(self):
        agent = AgentManifest(
            id="architect",
            name="架构师",
            keywords=["架构", "后端"],
        )
        user_tokens = {"变现", "定价"}
        result = match_agent(user_tokens, agent)
        assert result.match_score == 0.0
        assert result.final_score == 0.0

    def test_methodology_bonus(self):
        agent = AgentManifest(
            id="pm",
            name="PM",
            keywords=["产品"],
            methodology="JTBD 模型",
        )
        user_tokens = {"产品", "jtbd"}
        result = match_agent(user_tokens, agent)
        assert result.methodology_bonus > 0.0
        assert result.final_score > result.match_score


class TestBuildGroups:
    def test_small_group_single(self):
        agents = [
            AgentManifest(id="pm", name="PM", domains=["product"]),
            AgentManifest(id="ba", name="BA", domains=["business"]),
        ]
        matched = [match_agent({"产品"}, a) for a in agents]
        groups = build_groups(matched)
        assert len(groups) == 1
        assert len(groups[0].agents) == 2

    def test_supervisor_added_to_all_groups(self):
        agents = [
            AgentManifest(id="pm", name="PM", domains=["product"]),
            AgentManifest(id="ba", name="BA", domains=["business"]),
            AgentManifest(id="supervisor", name="SV", domains=["review"]),
        ]
        matched = [match_agent({"产品"}, a) for a in agents]
        groups = build_groups(matched)
        assert any(a.id == "supervisor" for a in groups[0].agents)

    def test_clustering_large_set(self):
        agents = [
            AgentManifest(id="pm", name="PM", domains=["product", "strategy"]),
            AgentManifest(id="ba", name="BA", domains=["business", "strategy"]),
            AgentManifest(id="arch", name="Arch", domains=["tech", "architecture"]),
            AgentManifest(id="devops", name="DevOps", domains=["tech", "infra"]),
            AgentManifest(id="sup", name="Supervisor", domains=["review"]),
        ]
        matched = [match_agent({"产品", "技术", "架构"}, a) for a in agents]
        groups = build_groups(matched)
        assert len(groups) >= 1
        for g in groups:
            assert len(g.agents) <= 6  # max group size + supervisor


class TestAgentRegistryV2:
    def test_loads_from_default_path(self):
        registry = AgentRegistryV2()
        assert len(registry.list_all()) >= 5

    def test_get_existing_agent(self):
        registry = AgentRegistryV2()
        pm = registry.get("product_manager")
        assert pm is not None
        assert pm.name == "产品经理"

    def test_get_missing_agent(self):
        registry = AgentRegistryV2()
        assert registry.get("nonexistent") is None

    def test_list_active(self):
        registry = AgentRegistryV2()
        active = registry.list_active()
        assert all(a.is_active for a in active)


class TestAgentMatcher:
    def test_match_pipeline(self):
        matcher = AgentMatcher()
        result = matcher.match("MVP产品需求分析，用户优先级排序")
        assert len(result.extracted_keywords) > 0
        assert len(result.matched_agents) > 0
        assert len(result.groups) >= 1
        # Should match product_manager due to keywords
        ids = [m.agent.id for m in result.matched_agents]
        assert "product_manager" in ids

    def test_match_with_min_score(self):
        matcher = AgentMatcher()
        result = matcher.match(" quantum physics research ", min_score=0.5)
        # Unlikely to match any agent at 0.5
        assert len(result.matched_agents) == 0

    def test_match_capped_at_max_agents(self):
        matcher = AgentMatcher()
        result = matcher.match("产品技术商业用户", max_agents=3)
        assert len(result.matched_agents) <= 3

    def test_singleton_get_matcher(self):
        m1 = get_matcher()
        m2 = get_matcher()
        assert m1 is m2
