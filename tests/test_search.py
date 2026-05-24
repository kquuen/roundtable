"""Phase 7A: Search + Verification tests."""

import pytest
from roundtable.models import (
    VerificationStatus, EvidenceClaim, ClaimType,
    SupervisorReview, ReviewResult,
)
from roundtable.search import SearchAdapter, SearchResult, SearchResultItem
from roundtable.verify import verify_claim, verify_pending_claims


class TestVerificationStatus:
    def test_all_values(self):
        statuses = list(VerificationStatus)
        assert VerificationStatus.UNCHECKED in statuses
        assert VerificationStatus.SUPPORTED_BY_SEARCH in statuses
        assert VerificationStatus.CONTRADICTED_BY_SEARCH in statuses
        assert VerificationStatus.NO_EVIDENCE_FOUND in statuses

    def test_default_is_unchecked(self):
        claim = EvidenceClaim(
            claim_id="c_001", agent_id="pm",
            claim_type=ClaimType.INFERENCE, content="test",
        )
        assert claim.verification == VerificationStatus.UNCHECKED


class TestSearchAdapter:
    def test_mock_search_returns_result(self):
        import asyncio
        adapter = SearchAdapter(backend="mock")
        result = asyncio.run(adapter.search("简历优化"))
        assert isinstance(result, SearchResult)
        assert len(result.items) >= 1

    def test_mock_search_resume_keywords(self):
        import asyncio
        adapter = SearchAdapter(backend="mock")
        result = asyncio.run(adapter.search("简历工具的API实现"))
        # Should match resume-related mock
        assert result.items[0].title != ""

    def test_mock_search_unknown_query(self):
        import asyncio
        adapter = SearchAdapter(backend="mock")
        result = asyncio.run(adapter.search("xyzzy"))
        assert len(result.items) >= 1  # Fallback item

    def test_search_respects_max_results(self):
        import asyncio
        adapter = SearchAdapter(backend="mock", max_results=1)
        result = asyncio.run(adapter.search("简历"))
        assert len(result.items) == 1


class TestVerifyClaim:
    def test_verify_supported_keywords(self):
        import asyncio
        claim = EvidenceClaim(
            claim_id="c_001", agent_id="pm",
            claim_type=ClaimType.FACT, content="简历优化使用AI技术",
        )
        search_result = SearchResult(
            query="简历优化",
            items=[SearchResultItem(
                title="AI简历优化",
                snippet="AI简历优化使用人工智能技术自动改进简历内容。",
            )],
        )
        status, reason = asyncio.run(verify_claim(claim, search_result))
        assert status == VerificationStatus.SUPPORTED_BY_SEARCH

    def test_verify_no_evidence(self):
        import asyncio
        claim = EvidenceClaim(
            claim_id="c_002", agent_id="pm",
            claim_type=ClaimType.FACT, content="火星上有液态海洋",
        )
        search_result = SearchResult(
            query="火星海洋",
            items=[SearchResultItem(
                title="完全不同的话题",
                snippet="今天天气很好适合出门散步呼吸新鲜空气。",
            )],
        )
        status, reason = asyncio.run(verify_claim(claim, search_result))
        assert status == VerificationStatus.NO_EVIDENCE_FOUND

    def test_verify_empty_results(self):
        import asyncio
        claim = EvidenceClaim(
            claim_id="c_003", agent_id="pm",
            claim_type=ClaimType.FACT, content="anything",
        )
        search_result = SearchResult(query="q")
        status, reason = asyncio.run(verify_claim(claim, search_result))
        assert status == VerificationStatus.NO_EVIDENCE_FOUND


class TestVerifyPendingClaims:
    def test_verify_pending_modifies_claims(self):
        import asyncio
        claim = EvidenceClaim(
            claim_id="c_test", agent_id="pm",
            claim_type=ClaimType.FACT, content="AI简历优化技术",
        )
        sr = SupervisorReview(
            claim_id="c_test",
            review_result=ReviewResult.NEEDS_USER_CONFIRMATION,
            reason="test",
        )
        search_results = {
            "简历": SearchResult(
                query="简历",
                items=[SearchResultItem(
                    title="AI简历",
                    snippet="AI简历优化使用人工智能技术。",
                )],
            ),
        }
        agent_reviews = [type("AR", (), {"claims": [claim]})()]
        asyncio.run(verify_pending_claims([sr], agent_reviews, search_results))
        assert claim.verification != VerificationStatus.UNCHECKED
