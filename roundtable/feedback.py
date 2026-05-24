"""Phase 5: 用户反馈处理引擎 — 人在回路的接收端。

处理用户对 NEEDS_CONFIRMATION claim 的裁决、
对系统推断的纠正、对 open_questions 的回答、
以及对 Memory 条目的确认/驳回。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from roundtable.models import (
    AgentReview, EvidenceClaim, SupervisorReview,
    ReviewResult, ClaimLifecycle, ConsensusLevel, BoundaryClass,
    ClaimType, MemoryWrite, PipelineResult,
)

logger = logging.getLogger("roundtable.feedback")


# ── 用户裁决 ──

class UserVerdict:
    """用户对一条 claim 的裁决结果。"""

    def __init__(
        self,
        claim_id: str,
        decision: str,  # "confirm" | "reject" | "retype"
        new_type: str | None = None,
        note: str = "",
    ):
        self.claim_id = claim_id
        self.decision = decision
        self.new_type = new_type
        self.note = note

    @classmethod
    def from_dict(cls, data: dict) -> UserVerdict:
        decision = data.get("decision", "")
        if decision not in ("confirm", "reject", "retype"):
            raise ValueError(f"Invalid decision '{decision}': must be confirm/reject/retype")
        if decision == "retype" and not data.get("new_type"):
            raise ValueError("retype requires new_type")
        return cls(
            claim_id=data.get("claim_id", ""),
            decision=decision,
            new_type=data.get("new_type"),
            note=data.get("note", ""),
        )


def process_user_verdict(
    verdict: UserVerdict,
    supervisor_reviews: list[SupervisorReview],
    agent_reviews: list[AgentReview],
) -> dict:
    """应用用户对单条 claim 的裁决。

    返回:
        {"updated": bool, "claim_id": str, "old_status": str, "new_status": str}
    """
    # 找到对应的 SupervisorReview
    target_sr = None
    for sr in supervisor_reviews:
        if sr.claim_id == verdict.claim_id:
            target_sr = sr
            break

    if target_sr is None:
        logger.warning("Verdict for unknown claim_id: %s", verdict.claim_id)
        return {"updated": False, "claim_id": verdict.claim_id, "error": "claim not found"}

    old_status = target_sr.review_result.value

    if verdict.decision == "confirm":
        target_sr.review_result = ReviewResult.APPROVED
        target_sr.reason = f"{target_sr.reason} [用户已确认]"
    elif verdict.decision == "reject":
        target_sr.review_result = ReviewResult.REJECTED
        target_sr.reason = f"{target_sr.reason} [用户驳回: {verdict.note}]" if verdict.note else f"{target_sr.reason} [用户驳回]"
    elif verdict.decision == "retype":
        try:
            target_sr.final_type = ClaimType(verdict.new_type) if verdict.new_type else None
        except ValueError:
            pass
        target_sr.review_result = ReviewResult.APPROVED
        target_sr.reason = f"{target_sr.reason} [用户重分类为 {verdict.new_type}]"

    # 同步更新对应 claim 的 lifecycle
    for ar in agent_reviews:
        for claim in ar.claims:
            if claim.claim_id == verdict.claim_id:
                if verdict.decision == "confirm":
                    claim.lifecycle = ClaimLifecycle.USER_CONFIRMED
                elif verdict.decision == "reject":
                    claim.lifecycle = ClaimLifecycle.USER_REJECTED
                elif verdict.decision == "retype":
                    claim.lifecycle = ClaimLifecycle.USER_CONFIRMED
                break

    logger.info(
        "User verdict applied: %s %s → %s",
        verdict.claim_id, old_status, target_sr.review_result.value,
    )

    return {
        "updated": True,
        "claim_id": verdict.claim_id,
        "old_status": old_status,
        "new_status": target_sr.review_result.value,
    }


def apply_bulk_verdicts(
    verdicts: list[UserVerdict],
    supervisor_reviews: list[SupervisorReview],
    agent_reviews: list[AgentReview],
) -> dict:
    """批量应用用户裁决。

    返回:
        {"applied": int, "failed": int, "details": list}
    """
    results = {"applied": 0, "failed": 0, "details": []}
    for v in verdicts:
        r = process_user_verdict(v, supervisor_reviews, agent_reviews)
        results["details"].append(r)
        if r.get("updated"):
            results["applied"] += 1
        else:
            results["failed"] += 1
    return results


# ── 用户纠正 ──

class UserCorrection:
    """用户对系统推断的纠正。"""

    def __init__(self, target: str, correction: str, reason: str = ""):
        self.target = target       # 被纠正的推断描述
        self.correction = correction  # 用户正确的版本
        self.reason = reason

    @classmethod
    def from_dict(cls, data: dict) -> UserCorrection:
        return cls(
            target=data.get("target", ""),
            correction=data.get("correction", ""),
            reason=data.get("reason", ""),
        )


def process_user_correction(correction: UserCorrection) -> dict:
    """记录用户对系统推断的纠正。

    当前实现：记录日志 + 返回结构化纠正记录。
    Phase 6+ 中会接入个人记忆系统更新用户画像。
    """
    logger.info(
        "User correction: '%s' → '%s' (reason: %s)",
        correction.target, correction.correction, correction.reason,
    )

    return {
        "recorded": True,
        "target": correction.target,
        "correction": correction.correction,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def process_user_answer(session_id: str, question: str, answer: str) -> dict:
    """记录用户对 open_question 的回答。"""
    logger.info("[%s] User answered: Q='%s' A='%s'", session_id, question[:80], answer[:80])

    return {
        "recorded": True,
        "session_id": session_id,
        "question": question,
        "answer": answer,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── 记忆确认 ──

def update_memory_confirmation(
    memory_store,
    session_id: str,
    memory_id: str,
    confirmed: bool,
) -> dict:
    """确认或驳回一条记忆条目。

    调用 MemoryStore.update_entry() 进行原子更新。
    """
    if memory_store is None:
        return {"updated": False, "error": "no memory store available"}

    updated = memory_store.update_entry(
        session_id, memory_id,
        {"confirmed": confirmed, "confirmed_at": datetime.now(timezone.utc).isoformat()},
    )

    if updated:
        logger.info("Memory %s confirmed=%s", memory_id, confirmed)

    return {
        "updated": updated,
        "memory_id": memory_id,
        "confirmed": confirmed,
    }


# ── 待办查询 ──

def get_pending_items(
    supervisor_reviews: list[SupervisorReview],
    agent_reviews: list[AgentReview],
) -> list[dict]:
    """提取所有需要用户关注的待办项。

    返回: 需要用户裁决的 claim + 其上下文信息
    """
    pending = []
    review_map = {r.claim_id: r for r in supervisor_reviews}

    for ar in agent_reviews:
        for claim in ar.claims:
            sr = review_map.get(claim.claim_id)
            if sr and sr.review_result == ReviewResult.NEEDS_USER_CONFIRMATION:
                pending.append({
                    "claim_id": claim.claim_id,
                    "agent_id": claim.agent_id,
                    "content": claim.content,
                    "claim_type": claim.claim_type.value if hasattr(claim.claim_type, 'value') else str(claim.claim_type),
                    "confidence": claim.confidence,
                    "reason": sr.reason,
                    "lifecycle": claim.lifecycle.value if hasattr(claim.lifecycle, 'value') else str(claim.lifecycle),
                    "boundary_classification": sr.boundary_classification.value if sr.boundary_classification else None,
                })

    return pending
