"""Phase 3: Main pipeline — end-to-end roundtable analysis.

Usage: python -m roundtable.main
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from roundtable.evidence import build_evidence_packet
from roundtable.orchestrator import run_orchestrator
from roundtable.supervisor import review_claims, summarize_review
from roundtable.report import compose_report


def run_pipeline(
    segments: list[dict],
    session_id: str = "s_001",
    mode: str = "meeting",
    title: str = "",
    agent_count: int = 5,
) -> str:
    """Run the full roundtable pipeline end-to-end.

    Returns:
        Markdown report string
    """
    # 1. Evidence
    evidence = build_evidence_packet(session_id, mode, segments)

    # 2. Agent analysis
    agent_reviews = run_orchestrator(evidence, agent_count=agent_count)

    # 3. Supervisor review
    supervisor_reviews = review_claims(agent_reviews, evidence, mode=mode)

    # 4. Report
    report = compose_report(agent_reviews, supervisor_reviews, session_title=title)

    return report


def main():
    """CLI entry point."""
    data_path = Path(__file__).resolve().parent.parent / "data" / "sample_transcript.json"
    if not data_path.exists():
        print("No sample transcript found. Creating default...")
        segments = [
            {"speaker": "Zhang", "text": "We should do text import first, not real-time ASR."},
            {"speaker": "Li", "text": "Backend protocols first: TranscriptChunk, EvidenceClaim, SupervisorReview."},
            {"speaker": "Wang", "text": "Frontend should use game-like team assembly interaction."},
        ]
        title = "Default Test Meeting"
    else:
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        segments = data.get("segments", [])
        title = data.get("title", "")

    report = run_pipeline(
        segments=segments,
        session_id="s_001",
        mode="meeting",
        title=title,
        agent_count=5,
    )

    reports_dir = Path(__file__).resolve().parent.parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / "latest_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
