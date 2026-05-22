"""Phase 3: Main pipeline — end-to-end roundtable analysis.

Usage:
    # Mock mode (no API key needed):
    python -m roundtable.main

    # LLM mode:
    $env:DEEPSEEK_API_KEY="sk-..."
    python -m roundtable.main

    # Custom input:
    python -m roundtable.main --input ./my_transcript.json --mode personal_roundtable
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from roundtable.providers import get_provider
from roundtable.services import RoundtableService


def main():
    """CLI entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description="圆桌会议 Roundtable — AI 专家圆桌分析"
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        help="Path to transcript JSON file (default: data/sample_transcript.json)",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["meeting", "personal_roundtable"],
        default="meeting",
        help="Analysis mode (default: meeting)",
    )
    parser.add_argument(
        "--agents", "-n",
        type=int,
        default=5,
        help="Number of agents (1-5, default: 5)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Force mock mode (no LLM call, even if API key is set)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output report path (default: reports/latest_report.md)",
    )

    args = parser.parse_args()

    # ── Load transcript ──
    if args.input:
        data_path = Path(args.input)
    else:
        data_path = Path(__file__).resolve().parent.parent / "data" / "sample_transcript.json"

    if data_path.exists():
        with open(data_path, encoding="utf-8") as f:
            data = json.load(f)
        segments = data.get("segments", [])
        title = data.get("title", "")
    else:
        print(f"Transcript file not found: {data_path}")
        print("Using default test segments...")
        segments = [
            {"speaker": "Zhang", "text": "We should do text import first, not real-time ASR."},
            {"speaker": "Li", "text": "Backend protocols first: TranscriptChunk, EvidenceClaim, SupervisorReview."},
            {"speaker": "Wang", "text": "Frontend should use game-like team assembly interaction."},
        ]
        title = "Default Test Meeting"

    # ── Initialize provider ──
    provider = None
    if not args.mock:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if api_key:
            try:
                provider = get_provider(provider="deepseek", api_key=api_key)
                print(f"[roundtable] LLM mode: deepseek (agents={args.agents})")
            except Exception as e:
                print(f"[roundtable] Provider init failed: {e} — falling back to mock")
        else:
            print("[roundtable] DEEPSEEK_API_KEY not set — running in mock mode")

    if provider is None:
        print("[roundtable] Mock mode: keyword-based analysis")

    # ── Run pipeline ──
    report = run_pipeline(
        segments=segments,
        session_id="s_001",
        mode=args.mode,
        title=title,
        agent_count=min(args.agents, 5),
        provider=provider,
    )

    # ── Output ──
    output_path = Path(args.output) if args.output else (
        Path(__file__).resolve().parent.parent / "reports" / "latest_report.md"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\n[roundtable] Report saved to: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
