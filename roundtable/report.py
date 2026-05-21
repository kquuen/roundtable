"""Phase 3: Report Composer — structured markdown report generation."""

from __future__ import annotations

from roundtable.models import AgentReview, SupervisorReview, ReviewResult


def compose_report(
    agent_reviews: list[AgentReview],
    supervisor_reviews: list[SupervisorReview],
    session_title: str = "",
) -> str:
    """合成最终圆桌审查报告。

    Returns:
        Structured markdown report string
    """
    review_map = {r.claim_id: r for r in supervisor_reviews}

    # Categorize claims by review result
    facts: list[tuple[str, str]] = []
    inferences: list[tuple[str, str]] = []
    recommendations: list[tuple[str, str]] = []
    extensions: list[tuple[str, str]] = []
    rejected: list[tuple[str, str, str]] = []  # (agent_id, content, reason)
    downgraded: list[tuple[str, str, str]] = []
    needs_confirm: list[tuple[str, str]] = []

    for ar in agent_reviews:
        for c in ar.claims:
            r = review_map.get(c.claim_id)
            if not r:
                continue
            if r.review_result == ReviewResult.REJECTED:
                rejected.append((ar.agent_id, c.content, r.reason))
            elif r.review_result == ReviewResult.DOWNGRADED:
                downgraded.append((ar.agent_id, r.final_type or c.claim_type, c.content))
            elif r.review_result == ReviewResult.NEEDS_USER_CONFIRMATION:
                needs_confirm.append((ar.agent_id, c.content))
            else:  # APPROVED
                final_type = r.final_type or c.claim_type
                item = (ar.agent_id, c.content)
                if final_type == "fact":
                    facts.append(item)
                elif final_type == "inference":
                    inferences.append(item)
                elif final_type == "recommendation":
                    recommendations.append(item)
                elif final_type == "extension":
                    extensions.append(item)

    lines = ["# 圆桌会议审查报告", ""]
    if session_title:
        lines.append(f"> **会议主题：{session_title}**")
        lines.append("")

    # 1. Summary
    lines.append("## 摘要")
    for ar in agent_reviews:
        lines.append(f"- **{ar.agent_id}**：{ar.summary}")
    lines.append("")

    # 2. Facts
    lines.append("## 会议事实")
    if facts:
        for agent_id, content in facts:
            lines.append(f"- [{agent_id}] {content}")
    else:
        lines.append("（无通过审查的事实认定）")
    lines.append("")

    # 3. Inferences
    if inferences:
        lines.append("## 推断与判断")
        for agent_id, content in inferences:
            lines.append(f"- [{agent_id}] {content}")
        lines.append("")

    # 4. Recommendations
    if recommendations:
        lines.append("## 专家建议")
        for agent_id, content in recommendations:
            lines.append(f"- [{agent_id}] {content}")
        lines.append("")

    # 5. Extensions
    if extensions:
        lines.append("## 创意延展")
        for agent_id, content in extensions:
            lines.append(f"- [{agent_id}] {content}")
        lines.append("")

    # 6. Downgraded
    if downgraded:
        lines.append("## 已降级项")
        for agent_id, final_type, content in downgraded:
            lines.append(f"- [{agent_id}] ~{final_type}~：{content}")
        lines.append("")

    # 7. Rejected (hallucinations)
    if rejected:
        lines.append("## 已驳回（幻觉/证据不足）")
        for agent_id, content, reason in rejected:
            lines.append(f"- ❌ [{agent_id}] {content} — _{reason}_")
        lines.append("")

    # 8. Needs user confirmation
    if needs_confirm:
        lines.append("## 待用户确认")
        for agent_id, content in needs_confirm:
            lines.append(f"- [{agent_id}] {content}")
        lines.append("")

    # 9. Open questions
    lines.append("## 待解决问题")
    for ar in agent_reviews:
        for q in ar.open_questions:
            lines.append(f"- [{ar.agent_id}] {q}")
    lines.append("")

    # 10. Next actions
    lines.append("## 下一步行动建议")
    for ar in agent_reviews:
        for act in ar.recommended_next_actions:
            lines.append(f"- [{ar.agent_id}] {act}")
    lines.append("")

    # Stats
    total = len(supervisor_reviews)
    rej = sum(1 for r in supervisor_reviews if r.review_result == ReviewResult.REJECTED)
    lines.append("---")
    lines.append(f"**审查统计**：共 {total} 条 claim，通过 {total - rej} 条，驳回 {rej} 条。")

    return "\n".join(lines)
