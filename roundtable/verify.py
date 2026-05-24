"""Phase 7A: 搜索校验引擎 — 对比 claim vs 搜索结果。

verify_claim():
  - LLM 模式: provider 对比 claim + search results → VerificationStatus + reason
  - Mock 模式: 关键词匹配 fallback
  - 只对 NEEDS_USER_CONFIRMATION 的 claim 执行（不全量）
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from roundtable.models import (
    EvidenceClaim, VerificationStatus,
    SupervisorReview, ReviewResult,
)
from roundtable.search import SearchResult

logger = logging.getLogger("roundtable.verify")


async def verify_claim(
    claim: EvidenceClaim,
    search_result: SearchResult,
    provider=None,
) -> tuple[VerificationStatus, str]:
    """校验单个 claim 是否被搜索结果支持。

    Returns:
        (status, reason)
    """
    if provider is not None:
        return await _verify_with_llm(claim, search_result, provider)

    return _verify_with_keywords(claim, search_result)


async def verify_pending_claims(
    supervisor_reviews: list[SupervisorReview],
    agent_reviews: list,
    search_results: dict[str, SearchResult],  # query → result
    provider=None,
) -> None:
    """对 NEEDS_USER_CONFIRMATION 的 claim 执行搜索校验。

    直接修改 claim.verification 字段。
    """
    # Build claim lookup
    claim_map: dict[str, EvidenceClaim] = {}
    for ar in agent_reviews:
        for claim in ar.claims:
            claim_map[claim.claim_id] = claim

    pending = [
        sr for sr in supervisor_reviews
        if sr.review_result == ReviewResult.NEEDS_USER_CONFIRMATION
    ]

    if not pending:
        return

    logger.info("Verifying %d pending claims against search results", len(pending))

    for sr in pending:
        claim = claim_map.get(sr.claim_id)
        if claim is None:
            continue

        # Find relevant search result (first matching query)
        relevant = None
        for query, result in search_results.items():
            # Simple: check if any claim keyword appears in query
            claim_words = set(claim.content[:30].split())
            query_words = set(query.split())
            if claim_words & query_words:
                relevant = result
                break
        if relevant is None and search_results:
            relevant = list(search_results.values())[0]  # Use first

        if relevant is None:
            claim.verification = VerificationStatus.NO_EVIDENCE_FOUND
            continue

        status, reason = await verify_claim(claim, relevant, provider)
        claim.verification = status
        sr.reason = (sr.reason or "") + f"\n🔍 搜索校验 [{status.value}]: {reason[:200]}"


# ── LLM-based verification ──

async def _verify_with_llm(
    claim: EvidenceClaim,
    search_result: SearchResult,
    provider,
) -> tuple[VerificationStatus, str]:
    """LLM 对比 claim 与搜索结果。"""
    snippets = "\n".join(
        f"[{i+1}] {item.title}\n{item.snippet}"
        for i, item in enumerate(search_result.items)
    )

    system = (
        "你是一个事实校验器。对比专家声明与网络搜索结果，判断声明是否被搜索结果支持。\n\n"
        "返回 JSON: {\"status\": \"supported_by_search|contradicted_by_search|no_evidence_found\", "
        "\"reason\": \"简短理由\"}\n"
        "只输出 JSON。"
    )

    user = (
        f"=== 专家声明 ===\n{claim.content}\n\n"
        f"=== 搜索结果 ===\n{snippets}"
    )

    try:
        raw = await provider.chat(
            system_prompt=system,
            user_message=user,
            max_tokens=300,
            temperature=0.2,
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            import re
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        result = json.loads(cleaned)
        status_str = result.get("status", "no_evidence_found")
        reason = result.get("reason", "")
        return VerificationStatus(status_str), reason
    except Exception as e:
        logger.warning("LLM verification failed: %s", e)
        return VerificationStatus.UNCHECKED, str(e)[:200]


# ── Mock / keyword-based verification ──

def _verify_with_keywords(
    claim: EvidenceClaim,
    search_result: SearchResult,
) -> tuple[VerificationStatus, str]:
    """关键词匹配校验——无需 LLM。"""
    claim_lower = claim.content.lower()
    all_snippets = " ".join(item.snippet.lower() for item in search_result.items)

    if not all_snippets:
        return VerificationStatus.NO_EVIDENCE_FOUND, "No search results available"

    # Character-level matching for Chinese (no spaces between words)
    is_chinese = any('\u4e00' <= c <= '\u9fff' for c in claim_lower)
    if is_chinese:
        # Chinese stopwords — filter out common noise characters
        cn_stopwords = set("的了是在不和有着这也人们可以及自己上中下到说为能去而把被让对从所")
        claim_chars = set(claim_lower) - cn_stopwords
        snippet_chars = set(all_snippets) - cn_stopwords
        overlap = claim_chars & snippet_chars
        # Need at least 30% meaningful char overlap (excl. stopwords)
        meaningful_count = max(len(claim_chars), 1)
        ratio = len(overlap) / meaningful_count
        if ratio >= 0.3:
            return VerificationStatus.SUPPORTED_BY_SEARCH, f"字符匹配率: {ratio:.0%}"
        elif ratio >= 0.1:
            return VerificationStatus.CONTRADICTED_BY_SEARCH, f"部分字符匹配: {ratio:.0%}"
        else:
            return VerificationStatus.NO_EVIDENCE_FOUND, "搜索结果中未找到相关内容"
    else:
        # English: word-level matching
        claim_words = set(claim_lower.split())
        snippet_words = set(all_snippets.split())
        overlap = claim_words & snippet_words

        stopwords = {"a", "the", "is", "of", "to", "for", "in", "and"}
        meaningful = overlap - stopwords

        if len(meaningful) >= 3:
            return VerificationStatus.SUPPORTED_BY_SEARCH, f"关键词匹配: {', '.join(list(meaningful)[:5])}"
        elif len(meaningful) > 0:
            return VerificationStatus.CONTRADICTED_BY_SEARCH, f"部分匹配: {', '.join(list(meaningful)[:5])}"
        else:
            return VerificationStatus.NO_EVIDENCE_FOUND, "搜索结果中未找到相关关键词"
