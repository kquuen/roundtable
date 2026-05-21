"""Phase 1: Evidence Base — Transcript chunking and EvidencePacket construction."""

from __future__ import annotations

from roundtable.models import EvidencePacket, TranscriptChunk


def chunk_transcript(
    session_id: str,
    segments: list[dict],
    source: str = "text_import",
) -> list[TranscriptChunk]:
    """将原始会议文本分块为 TranscriptChunk 列表。

    Each segment must have: {"speaker": "...", "text": "..."}
    Returns ordered list of chunks with stable chunk_ids.
    """
    chunks = []
    for i, seg in enumerate(segments):
        chunk = TranscriptChunk(
            chunk_id=f"t_{i:06d}",
            session_id=session_id,
            speaker=seg.get("speaker", "unknown"),
            start_ms=i * 30000,  # Simulate: 30s per segment
            end_ms=(i + 1) * 30000,
            text=seg.get("text", ""),
            asr_confidence=1.0 if source == "text_import" else 0.9,
            source=source,
        )
        chunks.append(chunk)
    return chunks


def build_evidence_packet(
    session_id: str,
    mode: str,
    segments: list[dict],
    source: str = "text_import",
) -> EvidencePacket:
    """从原始会议文本构建 EvidencePacket。

    Args:
        session_id: e.g. "s_123"
        mode: "meeting" or "personal_roundtable"
        segments: [{"speaker": "...", "text": "..."}]
        source: "text_import" or "asr_api"

    Returns:
        EvidencePacket with chunked transcript
    """
    chunks = chunk_transcript(session_id, segments, source=source)
    return EvidencePacket(
        session_id=session_id,
        mode=mode,
        transcript_chunks=chunks,
    )
