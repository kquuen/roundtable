"""Phase 1: Evidence base tests."""

import pytest
from roundtable.evidence import chunk_transcript, build_evidence_packet
from roundtable.models import TranscriptChunk, EvidencePacket


class TestChunkTranscript:
    def test_single_segment(self):
        segments = [{"speaker": "Alice", "text": "Let's discuss the MVP scope."}]
        chunks = chunk_transcript("s_001", segments)
        assert len(chunks) == 1
        assert chunks[0].chunk_id == "t_000000"
        assert chunks[0].speaker == "Alice"
        assert chunks[0].text == "Let's discuss the MVP scope."

    def test_chunk_ids_are_sequential(self):
        segments = [
            {"speaker": "A", "text": "First"},
            {"speaker": "B", "text": "Second"},
            {"speaker": "C", "text": "Third"},
        ]
        chunks = chunk_transcript("s_002", segments)
        assert [c.chunk_id for c in chunks] == ["t_000000", "t_000001", "t_000002"]

    def test_default_speaker(self):
        segments = [{"text": "No speaker field"}]
        chunks = chunk_transcript("s_003", segments)
        assert chunks[0].speaker == "unknown"

    def test_text_import_confidence_is_1(self):
        segments = [{"speaker": "A", "text": "Hello"}]
        chunks = chunk_transcript("s_004", segments, source="text_import")
        assert chunks[0].asr_confidence == 1.0
        assert chunks[0].source == "text_import"

    def test_asr_source_lower_confidence(self):
        segments = [{"speaker": "A", "text": "Hello"}]
        chunks = chunk_transcript("s_005", segments, source="asr_api")
        assert chunks[0].asr_confidence == 0.9
        assert chunks[0].source == "asr_api"


class TestBuildEvidencePacket:
    def test_full_packet(self):
        segments = [
            {"speaker": "Zhang", "text": "We should do text import first."},
            {"speaker": "Li", "text": "Backend protocol first, then ASR."},
        ]
        packet = build_evidence_packet("s_006", "meeting", segments)
        assert isinstance(packet, EvidencePacket)
        assert packet.session_id == "s_006"
        assert packet.mode == "meeting"
        assert len(packet.transcript_chunks) == 2

    def test_chunks_are_transcript_chunk_instances(self):
        segments = [{"speaker": "X", "text": "Test"}]
        packet = build_evidence_packet("s_007", "meeting", segments)
        for chunk in packet.transcript_chunks:
            assert isinstance(chunk, TranscriptChunk)

    def test_personal_roundtable_mode(self):
        segments = [{"speaker": "User", "text": "I'm thinking about..."}]
        packet = build_evidence_packet("s_008", "personal_roundtable", segments)
        assert packet.mode == "personal_roundtable"
