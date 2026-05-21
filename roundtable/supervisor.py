"""Phase 3: Supervisor — claim-level fact checking and evidence validation."""

from __future__ import annotations

from roundtable.models import (
    AgentReview, EvidenceClaim, EvidencePacket,
    SupervisorReview, ReviewResult,
)


def review_claims(
    agent_reviews: list[AgentReview],
    evidence: EvidencePacket,
    mode: str = "meeting",
) -> list[SupervisorReview]:
    """审查所有 claim，按证据绑定规则做裁决。

    Args:
        agent_reviews: All agent analysis results
        evidence: Original evidence packet (for validating chunk_ids)
        mode: "meeting" (strict) or "personal_roundtable" (relaxed)

    Returns:
        One SupervisorReview per claim
    """
    valid_chunk_ids = {c.chunk_id for c in evidence.transcript_chunks}
    reviews: list[SupervisorReview] = []

    for ar in agent_reviews:
        for claim in ar.claims:
            r = _review_single_claim(claim, valid_chunk_ids, mode)
            reviews.append(r)

    return reviews


def _review_single_claim(
    claim: EvidenceClaim,
    valid_chunk_ids: set[str],
    mode: str,
) -> SupervisorReview:
    """审查单个 claim。"""
    has_evidence = len(claim.evidence_ids) > 0
    evidence_valid = (
        all(eid in valid_chunk_ids for eid in claim.evidence_ids)
        if has_evidence
        else False
    )

    # fact: MUST have valid evidence
    if claim.claim_type == "fact":
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
    if claim.claim_type == "inference":
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


def summarize_review(reviews: list[SupervisorReview]) -> dict:
    """Summary statistics of a review run."""
    return {
        "total": len(reviews),
        "approved": sum(1 for r in reviews if r.review_result == ReviewResult.APPROVED),
        "downgraded": sum(1 for r in reviews if r.review_result == ReviewResult.DOWNGRADED),
        "rejected": sum(1 for r in reviews if r.review_result == ReviewResult.REJECTED),
        "needs_confirmation": sum(1 for r in reviews if r.review_result == ReviewResult.NEEDS_USER_CONFIRMATION),
    }
