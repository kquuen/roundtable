"""Phase 3: Supervisor — claim-level fact checking, forbidden enforcement, and contradiction detection.

v0.3.0: Added cross-agent contradiction detection and forbidden rule enforcement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from roundtable.models import (
    AgentReview, ClaimType, EvidenceClaim, EvidencePacket,
    SupervisorReview, ReviewResult,
)

logger = logging.getLogger("roundtable.supervisor")


def review_claims(
    agent_reviews: list[AgentReview],
    evidence: EvidencePacket,
    mode: str = "meeting",
    provider=None,  # Optional[ProviderAdapter] for LLM-based checks
    agent_forbidden: dict[str, list[str]] | None = None,
) -> list[SupervisorReview]:
    """审查所有 claim，按证据绑定规则 + forbidden 规则做裁决。

    Args:
        agent_reviews: All agent analysis results
        evidence: Original evidence packet (for validating chunk_ids)
        mode: "meeting" (strict) or "personal_roundtable" (relaxed)
        provider: Optional LLM provider for contradiction detection

    Returns:
        One SupervisorReview per claim
    """
    valid_chunk_ids = {c.chunk_id for c in evidence.transcript_chunks}
    reviews: list[SupervisorReview] = []

    for ar in agent_reviews:
        for claim in ar.claims:
            # Step 1: Evidence-based review
            r = _review_single_claim(claim, valid_chunk_ids, mode)

            # Step 2: Forbidden rule check (overrides approval if violated)
            if r.review_result == ReviewResult.APPROVED and agent_forbidden:
                forbidden = agent_forbidden.get(ar.agent_id, [])
                fb_result = _check_forbidden(claim, forbidden)
                if fb_result:
                    r = fb_result

            reviews.append(r)

    # Step 3: Cross-agent contradiction detection (LLM-based if provider available)
    if provider is not None and len(reviews) >= 2:
        try:
            reviews = asyncio.run(
                _detect_contradictions_async(reviews, agent_reviews, provider)
            )
        except RuntimeError:
            logger.warning(
                "矛盾检测跳过：在事件循环中调用了同步 review_claims，请使用 review_claims_async"
            )
        except Exception:
            pass  # Contradiction detection is best-effort

    return reviews


async def review_claims_async(
    agent_reviews: list[AgentReview],
    evidence: EvidencePacket,
    mode: str = "meeting",
    provider=None,
    agent_forbidden: dict[str, list[str]] | None = None,
) -> list[SupervisorReview]:
    """异步版本：直接 await 矛盾检测，不再嵌套 asyncio.run()。"""
    valid_chunk_ids = {c.chunk_id for c in evidence.transcript_chunks}
    reviews: list[SupervisorReview] = []

    for ar in agent_reviews:
        for claim in ar.claims:
            r = _review_single_claim(claim, valid_chunk_ids, mode)
            if r.review_result == ReviewResult.APPROVED and agent_forbidden:
                forbidden = agent_forbidden.get(ar.agent_id, [])
                fb_result = _check_forbidden(claim, forbidden)
                if fb_result:
                    r = fb_result
            reviews.append(r)

    # 直接 await，不再 asyncio.run()
    if provider is not None and len(reviews) >= 2:
        try:
            reviews = await _detect_contradictions_async(reviews, agent_reviews, provider)
        except Exception:
            pass

    return reviews


# ── Single claim review ──

def _review_single_claim(
    claim: EvidenceClaim,
    valid_chunk_ids: set[str],
    mode: str,
) -> SupervisorReview:
    """审查单个 claim 的证据绑定。"""
    has_evidence = len(claim.evidence_ids) > 0
    evidence_valid = (
        all(eid in valid_chunk_ids for eid in claim.evidence_ids)
        if has_evidence
        else False
    )

    # fact: MUST have valid evidence
    if claim.claim_type == ClaimType.FACT:
        if not has_evidence or not evidence_valid:
            return SupervisorReview(
                claim_id=claim.claim_id,
                review_result=ReviewResult.REJECTED,
                reason="fact claim must have valid evidence_ids pointing to existing chunks.",
            )
        if claim.confidence < 0.5:
            return SupervisorReview(
                claim_id=claim.claim_id,
                review_result=ReviewResult.DOWNGRADED,
                final_type="inference",
                reason="Evidence confidence too low. Downgraded to inference.",
            )
        return SupervisorReview(
            claim_id=claim.claim_id,
            review_result=ReviewResult.APPROVED,
            final_type="fact",
            reason="Claim has valid transcript chunk support.",
        )

    # inference: in meeting mode, high-confidence inferences without evidence need confirmation
    if claim.claim_type == ClaimType.INFERENCE:
        if mode == "meeting" and not has_evidence and claim.confidence > 0.7:
            return SupervisorReview(
                claim_id=claim.claim_id,
                review_result=ReviewResult.NEEDS_USER_CONFIRMATION,
                reason="High-confidence inference without direct evidence in meeting mode. User confirmation required.",
            )
        return SupervisorReview(
            claim_id=claim.claim_id,
            review_result=ReviewResult.APPROVED,
            final_type="inference",
            reason="Inference is reasonable and properly tagged.",
        )

    # recommendation / extension: no evidence required, auto-approve
    return SupervisorReview(
        claim_id=claim.claim_id,
        review_result=ReviewResult.APPROVED,
        final_type=claim.claim_type,
        reason=f"{claim.claim_type} claims do not require evidence binding.",
    )


# ── Forbidden rule enforcement ──

def _check_forbidden(
    claim: EvidenceClaim,
    forbidden: list[str],
) -> SupervisorReview | None:
    """Check if a claim violates the agent's forbidden rules.

    Uses heuristic keyword matching against the provided forbidden rule list.
    Returns a REJECTED review if violated, None if clean.
    """
    if not forbidden:
        return None

    content = claim.content

    # Rule: "把建议写成事实" — if claim is FACT but uses recommendation language
    if "把建议写成事实" in forbidden or "把推测写成事实" in forbidden:
        rec_kw = ["建议", "推荐", "应该", "最好", "可以试试", "不妨"]
        if claim.claim_type == ClaimType.FACT and any(kw in content for kw in rec_kw):
            return SupervisorReview(
                claim_id=claim.claim_id,
                review_result=ReviewResult.REJECTED,
                reason="违反规则：把建议/推测写成了事实。建议类内容应标记为 recommendation。",
            )

    # Rule: "断言技术架构的可行性" — product_manager forbids architecture claims
    if "断言技术架构的可行性" in forbidden:
        tech_kw = ["架构", "并发", "吞吐", "延迟", "QPS", "数据库选型", "微服务", "扩展性"]
        if any(kw in content for kw in tech_kw):
            return SupervisorReview(
                claim_id=claim.claim_id,
                review_result=ReviewResult.REJECTED,
                reason="违反规则：产品经理不应断言技术架构的可行性。",
            )

    # Rule: "对产品策略做判断" — architect forbids product strategy
    if "对产品策略做判断" in forbidden:
        prod_kw = ["用户价值", "产品定位", "市场策略", "定价", "竞品对比"]
        if any(kw in content for kw in prod_kw):
            return SupervisorReview(
                claim_id=claim.claim_id,
                review_result=ReviewResult.REJECTED,
                reason="违反规则：架构师不应对产品策略做判断。",
            )

    # Rule: "断言未确认的交付日期" — project_manager
    if "断言未确认的交付日期" in forbidden:
        date_patterns = [r"\d+月\d+日", r"\d+周", r"Q[1-4]", r"下个月", r"本周", r"下周"]
        if any(re.search(p, content) for p in date_patterns):
            return SupervisorReview(
                claim_id=claim.claim_id,
                review_result=ReviewResult.REJECTED,
                reason="违反规则：项目经理不应断言未确认的交付日期。",
            )

    # Rule: "评估技术难度" — project_manager
    if "评估技术难度" in forbidden:
        diff_kw = ["技术难度", "实现复杂", "开发量大", "工作量", "人天", "人月"]
        if any(kw in content for kw in diff_kw):
            return SupervisorReview(
                claim_id=claim.claim_id,
                review_result=ReviewResult.REJECTED,
                reason="违反规则：项目经理不应评估技术难度。",
            )

    # Rule: "在没有数据时断言市场规模" — business_analyst
    if "在没有数据时断言市场规模" in forbidden:
        if "市场规模" in content and "亿" in content:
            return SupervisorReview(
                claim_id=claim.claim_id,
                review_result=ReviewResult.REJECTED,
                reason="违反规则：商业分析师不应在没有数据时断言市场规模。",
            )

    # Rule: "提出新的专家建议" — supervisor agent
    if "提出新的专家建议" in forbidden:
        rec_kw = ["建议", "推荐", "应该做"]
        if any(kw in content for kw in rec_kw):
            return SupervisorReview(
                claim_id=claim.claim_id,
                review_result=ReviewResult.REJECTED,
                reason="违反规则：审查官不应提出新的专家建议。",
            )

    return None


# ── Cross-agent contradiction detection ──

async def _detect_contradictions_async(
    reviews: list[SupervisorReview],
    agent_reviews: list[AgentReview],
    provider,
) -> list[SupervisorReview]:
    """Use LLM to detect contradictions between agents' claims.

    Modifies reviews in-place: conflicting claims → NEEDS_USER_CONFIRMATION.
    """
    # Collect approved claims with their agent info
    approved = []
    review_index: dict[str, int] = {}  # claim_id → index in reviews
    for i, (r, ar) in enumerate(zip(reviews, _flatten_claims(agent_reviews))):
        if r.review_result == ReviewResult.APPROVED:
            approved.append({
                "claim_id": r.claim_id,
                "agent_id": ar.agent_id if isinstance(ar, AgentReview) else "unknown",
                "content": _get_claim_content(r.claim_id, agent_reviews),
                "claim_type": r.final_type or "unknown",
            })
            review_index[r.claim_id] = i

    if len(approved) < 2:
        return reviews

    logger.info("Running contradiction detection on %d approved claims", len(approved))

    # Build prompt for contradiction detection
    claims_text = "\n".join(
        f"[{c['claim_id']}] ({c['agent_id']}, {c['claim_type']}) {c['content']}"
        for c in approved
    )

    prompt = f"""以下是不同专家对同一场会议的独立分析结论。请检查是否存在互相矛盾的声明。

=== 专家声明 ===
{claims_text}

请判断哪些声明之间存在矛盾。返回 JSON 格式：
{{
  "contradictions": [
    {{"claim_a": "claim_id_1", "claim_b": "claim_id_2", "reason": "矛盾原因简述"}}
  ]
}}

如果所有声明都一致，返回 {{"contradictions": []}}。
只输出 JSON，不要包含其他文字。"""

    try:
        raw = await provider.chat(
            system_prompt="你是一个事实一致性检查器。你的任务是找出专家分析结论之间的逻辑矛盾。",
            user_message=prompt,
            max_tokens=1000,
            temperature=0.3,
        )

        # Parse response
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        result = json.loads(cleaned)

        contradictions = result.get("contradictions", [])
        if contradictions:
            logger.info("Found %d contradictions", len(contradictions))
        for con in contradictions:
            claim_a = con.get("claim_a", "")
            claim_b = con.get("claim_b", "")
            reason = con.get("reason", "声明互相矛盾")

            for cid in (claim_a, claim_b):
                if cid in review_index:
                    idx = review_index[cid]
                    r = reviews[idx]
                    # Only mark if currently approved
                    if r.review_result == ReviewResult.APPROVED:
                        reviews[idx] = SupervisorReview(
                            claim_id=r.claim_id,
                            review_result=ReviewResult.NEEDS_USER_CONFIRMATION,
                            reason=f"跨 Agent 矛盾检测：{reason}（与 {claim_b if cid == claim_a else claim_a} 冲突）",
                        )

    except Exception as e:
        logger.warning("Contradiction detection failed: %s", e)

    return reviews


def _flatten_claims(agent_reviews: list[AgentReview]) -> list:
    """Flatten agent_reviews into a list of (AgentReview, EvidenceClaim) pairs."""
    result = []
    for ar in agent_reviews:
        for c in ar.claims:
            result.append((ar, c))
    return result


def _get_claim_content(claim_id: str, agent_reviews: list[AgentReview]) -> str:
    """Find claim content by ID."""
    for ar in agent_reviews:
        for c in ar.claims:
            if c.claim_id == claim_id:
                return c.content
    return ""


def summarize_review(reviews: list[SupervisorReview]) -> dict:
    """Summary statistics of a review run."""
    result = {
        "total": len(reviews),
        "approved": 0,
        "downgraded": 0,
        "rejected": 0,
        "needs_confirmation": 0,
    }
    for r in reviews:
        key = r.review_result.value if hasattr(r.review_result, 'value') else r.review_result
        if key == "approved":
            result["approved"] += 1
        elif key == "downgraded":
            result["downgraded"] += 1
        elif key == "rejected":
            result["rejected"] += 1
        elif key == "needs_user_confirmation":
            result["needs_confirmation"] += 1
    return result
