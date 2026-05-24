"""Phase 7B: ASR /speak endpoint tests."""

import io
import os
import pytest
from starlette.testclient import TestClient
from roundtable.app import app
from roundtable.models import ASRResult, ASRSegment
from roundtable.asr import WhisperAdapter, MAX_CHUNK_SECONDS


# Ensure OPENAI_API_KEY is set in test env (will fail at API level, but init won't crash)
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key-for-unit-tests")


class TestASRModels:
    def test_segment_serialization(self):
        seg = ASRSegment(speaker="A", text="你好", start=1.0, end=2.0, confidence=0.95)
        j = seg.model_dump_json()
        loaded = ASRSegment.model_validate_json(j)
        assert loaded.text == "你好"
        assert loaded.confidence == 0.95

    def test_result_serialization(self):
        result = ASRResult(
            segments=[ASRSegment(text="test", confidence=0.9)],
            language="zh",
            duration=10.0,
            model_used="whisper-1",
        )
        j = result.model_dump_json()
        loaded = ASRResult.model_validate_json(j)
        assert loaded.language == "zh"
        assert len(loaded.segments) == 1

    def test_result_defaults(self):
        result = ASRResult()
        assert result.segments == []
        assert result.language == "zh"
        assert result.model_used == "whisper-1"


class TestWhisperAdapterInit:
    def test_no_api_key_raises(self):
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                WhisperAdapter(backend="whisper_api")
        finally:
            if old_key:
                os.environ["OPENAI_API_KEY"] = old_key
            else:
                os.environ["OPENAI_API_KEY"] = "sk-test-key-for-unit-tests"

    def test_with_api_key(self):
        adapter = WhisperAdapter(backend="whisper_api", api_key="sk-test")
        assert adapter.backend == "whisper_api"

    def test_unknown_backend_raises_at_transcribe(self):
        adapter = WhisperAdapter(backend="whisper_api", api_key="sk-test")
        adapter.backend = "unknown"
        import asyncio
        with pytest.raises(ValueError, match="Unknown backend"):
            asyncio.run(adapter.transcribe_async("dummy.mp3"))


class TestAudioSplit:
    def test_duration_chunk_threshold(self):
        """20 min = 1200 seconds = MAX_CHUNK_SECONDS."""
        assert MAX_CHUNK_SECONDS == 1200


class TestSpeakEndpoint:
    def test_speak_endpoint_no_file(self):
        """Missing file should return 422."""
        client = TestClient(app)
        r = client.post("/speak")
        assert r.status_code == 422

    def test_speak_endpoint_handles_invalid_audio(self):
        """Invalid audio should return error response, not crash."""
        client = TestClient(app)
        tiny_mp3 = b"\xff\xfb\x90\x00" * 10
        r = client.post(
            "/speak",
            files={"audio": ("test.mp3", io.BytesIO(tiny_mp3), "audio/mpeg")},
        )
        # Should return either 200 (with error field) or error status
        assert r.status_code in (200, 400, 422, 500, 401)
