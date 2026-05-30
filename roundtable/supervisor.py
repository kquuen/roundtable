"""Phase 3: Supervisor — claim-level fact checking, forbidden enforcement, and contradiction detection.

v0.3.0: Added cross-agent contradiction detection and forbidden rule enforcement.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from roundtable.models import (
    AgentReview, ClaimType, EvidenceClaim, EvidencePacket,
    SupervisorReview, ReviewResult, BoundaryClass,
    ClaimLifecycle, ConsensusLevel,
)
from roundtable.utils import run_async_safely

logger = logging.getLogger("roundtable.supervisor")


def _review_all_claims(
    agent_reviews: list[AgentReview],
    evidence: EvidencePacket,
    mode: str,
    agent_forbidden: dict[str, list[str]] | None,
) -> list[SupervisorReview]:
    """Shared claim review loop used by both sync and async entry points."""
    valid_chunk_ids = {c.chunk_id for c in evidence.transcript_chunks}
    reviews: list[SupervisorReview] = []

    for ar in agent_reviews:
        for claim in ar.claims:
            if claim.lifecycle == ClaimLifecycle.DRAFT:
                claim.lifecycle = ClaimLifecycle.UNDER_REVIEW
            r = _review_single_claim(claim, valid_chunk_ids, mode)
            if r.review_result == ReviewResult.APPROVED and agent_forbidden:
                forbidden = agent_forbidden.get(ar.agent_id, [])
                fb_result, boundary = _check_forbidden(claim, forbidden)
                if fb_result:
                    r = fb_result
                elif boundary is not None:
                    r.boundary_classification = boundary
            reviews.append(r)

    return reviews


def review_claims(
    agent_reviews: list[AgentReview],
    evidence: EvidencePacket,
    mode: str = "meeting",
    provider=None,  # Optional[BaseLLMProvider] for LLM-based checks
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
    reviews = _review_all_claims(agent_reviews, evidence, mode, agent_forbidden)

    # Cross-agent contradiction detection (LLM-based if provider available)
    if provider is not None and len(reviews) >= 2:
        try:
            reviews, _conflict_pairs = run_async_safely(
                _detect_contradictions_async(reviews, agent_reviews, provider),
                name="review_claims — use review_claims_async() in async context",
            )
        except RuntimeError:
            logger.warning(
                "矛盾检测跳过：在事件循环中调用了同步 review_claims，请使用 review_claims_async"
            )
        except Exception:
            logger.warning("矛盾检测异常，已跳过", exc_info=True)

    _compute_consensus_levels(reviews, agent_reviews)
    return reviews


async def review_claims_async(
    agent_reviews: list[AgentReview],
    evidence: EvidencePacket,
    mode: str = "meeting",
    provider=None,
    agent_forbidden: dict[str, list[str]] | None = None,
) -> list[SupervisorReview]:
    """异步版本：直接 await 矛盾检测，不再嵌套 asyncio.run()。"""
    reviews = _review_all_claims(agent_reviews, evidence, mode, agent_forbidden)

    if provider is not None and len(reviews) >= 2:
        try:
            reviews, _conflict_pairs = await _detect_contradictions_async(reviews, agent_reviews, provider)
        except Exception:
            logger.warning("Contradiction detection failed, skipping", exc_info=True)

    _compute_consensus_levels(reviews, agent_reviews)
    return reviews


# ── Consensus level computation ──

def _compute_consensus_levels(
    reviews: list[SupervisorReview],
    agent_reviews: list[AgentReview],
) -> None:
    """根据多个 Agent 是否独立提出相似观点，计算每个 claim 的共识等级。

    按 claim.content 前 50 字符聚类，统计独立 Agent 数量。
    直接在 agent_reviews 的 claim 上设置 consensus_level。
    """
    # 构建 claim_id → claim 的查找表
    claim_map: dict[str, EvidenceClaim] = {}
    for ar in agent_reviews:
        for claim in ar.claims:
            claim_map[claim.claim_id] = claim

    # 按 content 前 50 字符聚类
    from collections import defaultdict
    content_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for ar in agent_reviews:
        for claim in ar.claims:
            key = claim.content[:50].strip()
            content_groups[key].append((claim.claim_id, ar.agent_id))

    # 对每个 review 对应的 claim 设置共识等级
    for r in reviews:
        claim = claim_map.get(r.claim_id)
        if claim is None:
            continue

        key = claim.content[:50].strip()
        agents_in_group = set()
        for cid, aid in content_groups.get(key, []):
            agents_in_group.add(aid)

        unique_agent_count = len(agents_in_group)

        if r.review_result == ReviewResult.REJECTED:
            claim.consensus_level = ConsensusLevel.CONTRADICTED
        elif unique_agent_count >= 3:
            claim.consensus_level = ConsensusLevel.STRONG
        elif unique_agent_count >= 2:
            claim.consensus_level = ConsensusLevel.MAJORITY
        else:
            claim.consensus_level = ConsensusLevel.ISOLATED

    logger.debug(
        "Consensus computed: %d claims classified",
        sum(1 for r in reviews if claim_map.get(r.claim_id)),
    )


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


# ── Forbidden rule enforcement (v5: 3-tier dynamic classification) ──

def classify_boundary_crossing(
    claim: EvidenceClaim,
    forbidden: list[str],
) -> BoundaryClass:
    """判定一个 claim 是否越界，以及严重程度。

    三级分类：
    - SAFE: 明确在领域内，没有触发任何 forbidden 规则
    - BORDERLINE: 踩线但不严重——有关键词匹配但置信度低，
      或 claim 内容有领域交叉但非断言性语言
    - VIOLATION: 明确越界——断言性语言 + 明确触犯 forbidden 规则
    """
    if not forbidden:
        return BoundaryClass.SAFE

    content = claim.content

    hedging = any(kw in content for kw in ["可能", "也许", "考虑", "或可", "不妨"])
    asserting = any(kw in content for kw in ["必须", "一定", "毫无疑问", "很明显", "肯定"])

    violations = 0
    borderlines = 0

    if "把建议写成事实" in forbidden or "把推测写成事实" in forbidden:
        rec_kw = ["建议", "推荐", "应该", "最好", "可以试试", "不妨"]
        if claim.claim_type == ClaimType.FACT and any(kw in content for kw in rec_kw):
            if asserting:
                violations += 1
            else:
                borderlines += 1

    if "断言技术架构的可行性" in forbidden:
        tech_kw = ["架构", "并发", "吞吐", "延迟", "QPS", "数据库选型", "微服务", "扩展性"]
        if any(kw in content for kw in tech_kw):
            if hedging:
                borderlines += 1
            else:
                violations += 1

    if "对产品策略做判断" in forbidden:
        prod_kw = ["用户价值", "产品定位", "市场策略", "定价", "竞品对比"]
        if any(kw in content for kw in prod_kw):
            if hedging:
                borderlines += 1
            else:
                violations += 1

    if "断言未确认的交付日期" in forbidden:
        date_patterns = [r"\d+月\d+日", r"\d+周", r"Q[1-4]", r"下个月", r"本周", r"下周"]
        if any(re.search(p, content) for p in date_patterns):
            if hedging:
                borderlines += 1
            else:
                violations += 1

    if "评估技术难度" in forbidden:
        diff_kw = ["技术难度", "实现复杂", "开发量大", "工作量", "人天", "人月"]
        if any(kw in content for kw in diff_kw):
            if hedging:
                borderlines += 1
            else:
                violations += 1

    if "在没有数据时断言市场规模" in forbidden:
        if "市场规模" in content and "亿" in content:
            if hedging:
                borderlines += 1
            else:
                violations += 1

    if "提出新的专家建议" in forbidden:
        rec_kw = ["建议", "推荐", "应该做"]
        if any(kw in content for kw in rec_kw):
            borderlines += 1

    if violations > 0:
        return BoundaryClass.VIOLATION
    if borderlines > 0:
        return BoundaryClass.BORDERLINE
    return BoundaryClass.SAFE


def _check_forbidden(
    claim: EvidenceClaim,
    forbidden: list[str],
) -> tuple[SupervisorReview | None, BoundaryClass | None]:
    """Check if a claim violates the agent's forbidden rules.

    Returns:
        (rejected_review_or_None, boundary_classification_or_None)
    """
    boundary = classify_boundary_crossing(claim, forbidden)

    if boundary == BoundaryClass.SAFE:
        return None, None

    if boundary == BoundaryClass.BORDERLINE:
        return None, BoundaryClass.BORDERLINE

    reason = _build_violation_reason(claim, forbidden)
    return (
        SupervisorReview(
            claim_id=claim.claim_id,
            review_result=ReviewResult.REJECTED,
            reason=reason,
            boundary_classification=BoundaryClass.VIOLATION,
        ),
        BoundaryClass.VIOLATION,
    )


def _build_violation_reason(claim: EvidenceClaim, forbidden: list[str]) -> str:
    """Build a human-readable reason for a forbidden rule violation."""
    content = claim.content

    if "断言技术架构的可行性" in forbidden:
        tech_kw = ["架构", "并发", "吞吐", "延迟", "QPS", "数据库选型", "微服务", "扩展性"]
        if any(kw in content for kw in tech_kw):
            return "违反规则：产品经理不应断言技术架构的可行性。[边界: VIOLATION]"

    if "对产品策略做判断" in forbidden:
        prod_kw = ["用户价值", "产品定位", "市场策略", "定价", "竞品对比"]
        if any(kw in content for kw in prod_kw):
            return "违反规则：架构师不应对产品策略做判断。[边界: VIOLATION]"

    if "把建议写成事实" in forbidden or "把推测写成事实" in forbidden:
        return "违反规则：把建议/推测写成了事实。[边界: VIOLATION]"

    if "断言未确认的交付日期" in forbidden:
        return "违反规则：项目经理不应断言未确认的交付日期。[边界: VIOLATION]"

    if "评估技术难度" in forbidden:
        return "违反规则：项目经理不应评估技术难度。[边界: VIOLATION]"

    if "在没有数据时断言市场规模" in forbidden:
        return "违反规则：商业分析师不应在没有数据时断言市场规模。[边界: VIOLATION]"

    if "提出新的专家建议" in forbidden:
        return "违反规则：审查官不应提出新的专家建议。[边界: VIOLATION]"

    return f"违反规则。[边界: VIOLATION]"


# ── Cross-agent contradiction detection ──

async def _detect_contradictions_async(
    reviews: list[SupervisorReview],
    agent_reviews: list[AgentReview],
    provider,
) -> tuple[list[SupervisorReview], list[dict]]:
    """Use LLM to detect contradictions between agents' claims.

    Modifies reviews in-place: conflicting claims → NEEDS_USER_CONFIRMATION.

    Returns:
        (modified_reviews, conflict_pairs) — conflict_pairs for report display.
    """
    # Build claim_id → agent_id lookup from agent_reviews
    claim_agent_map: dict[str, str] = {}
    for ar in agent_reviews:
        for claim in ar.claims:
            claim_agent_map[claim.claim_id] = ar.agent_id

    # Collect approved claims with their agent info
    claims_pairs = _flatten_claims(agent_reviews)
    approved = []
    review_index: dict[str, int] = {}  # claim_id → index in reviews
    for i, r in enumerate(reviews):
        if r.review_result == ReviewResult.APPROVED:
            approved.append({
                "claim_id": r.claim_id,
                "agent_id": claim_agent_map.get(r.claim_id, "unknown"),
                "content": _get_claim_content(r.claim_id, agent_reviews),
                "claim_type": r.final_type or "unknown",
            })
            review_index[r.claim_id] = i

    if len(approved) < 2:
        return reviews, []

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

        # Build conflict pairs for report display (both sides' full arguments)
        conflict_pairs: list[dict] = []

        for con in contradictions:
            claim_a = con.get("claim_a", "")
            claim_b = con.get("claim_b", "")
            reason = con.get("reason", "声明互相矛盾")

            # Get both claims' full content
            content_a = _get_claim_content(claim_a, agent_reviews)
            content_b = _get_claim_content(claim_b, agent_reviews)
            agent_a = _get_claim_agent(claim_a, agent_reviews) or "unknown"
            agent_b = _get_claim_agent(claim_b, agent_reviews) or "unknown"

            conflict_pairs.append({
                "claim_a": claim_a, "claim_b": claim_b,
                "agent_a": agent_a, "agent_b": agent_b,
                "content_a": content_a[:200], "content_b": content_b[:200],
                "reason": reason,
            })

            for cid in (claim_a, claim_b):
                if cid in review_index:
                    idx = review_index[cid]
                    r = reviews[idx]
                    # Only mark if currently approved
                    if r.review_result == ReviewResult.APPROVED:
                        other_cid = claim_b if cid == claim_a else claim_a
                        other_content = content_b if cid == claim_a else content_a
                        other_agent = agent_b if cid == claim_a else agent_a

                        # Update claim lifecycle
                        for ar in agent_reviews:
                            for claim in ar.claims:
                                if claim.claim_id == r.claim_id:
                                    claim.lifecycle = ClaimLifecycle.CHALLENGED
                                    break

                        # Enriched reason: show both sides
                        enriched_reason = (
                            f"⚔️ 矛盾检测：{reason}\n"
                            f"▶ 本方观点（{r.claim_id}）：{_get_claim_content(r.claim_id, agent_reviews)[:120]}\n"
                            f"▶ 对立方观点（{other_cid}，{other_agent}）：{other_content[:120]}"
                        )
                        reviews[idx] = SupervisorReview(
                            claim_id=r.claim_id,
                            review_result=ReviewResult.NEEDS_USER_CONFIRMATION,
                            reason=enriched_reason,
                        )
                        # Set NEEDS_USER after NEEDS_USER_CONFIRMATION
                        for ar in agent_reviews:
                            for claim in ar.claims:
                                if claim.claim_id == r.claim_id:
                                    claim.lifecycle = ClaimLifecycle.NEEDS_USER
                                    break

        return reviews, conflict_pairs

    except Exception as e:
        logger.warning("Contradiction detection failed: %s", e)

    return reviews, []


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


def _get_claim_agent(claim_id: str, agent_reviews: list[AgentReview]) -> str:
    """Find the agent_id that produced a given claim."""
    for ar in agent_reviews:
        for c in ar.claims:
            if c.claim_id == claim_id:
                return ar.agent_id
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
