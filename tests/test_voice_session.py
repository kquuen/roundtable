"""Targeted tests for VoiceSession lifecycle edge cases."""

from __future__ import annotations

import asyncio
import pytest

from roundtable.voice.session import VoiceSession
from roundtable.store import SessionStore


class _FakeWebSocket:
    def __init__(self):
        self.closed = False
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)

    async def receive_json(self):
        raise RuntimeError("boom-before-tasks")

    async def close(self):
        self.closed = True


def test_voice_session_run_cleanup_without_task_init(monkeypatch):
    """run() should not raise even if setup fails before background tasks are created."""
    async def _case():
        ws = _FakeWebSocket()
        session = VoiceSession(frontend_ws=ws, mode="qa", template="general")

        async def _raise_connect(self):
            raise RuntimeError("connect-failed")

        monkeypatch.setattr(
            "roundtable.voice.session.DashScopeASRClient.connect",
            _raise_connect,
        )

        await session.run()
        assert session.state.value == "closed"

    asyncio.run(_case())


def test_voice_session_evidence_mode_appends_transcript(tmp_path):
    """Evidence voice mode should persist final ASR text to the target session."""
    async def _case():
        store = SessionStore(base_dir=tmp_path / "sessions")
        session_meta = store.create(title="Live Voice", mode="meeting")
        ws = _FakeWebSocket()
        session = VoiceSession(
            frontend_ws=ws,
            session_store=store,
            mode="evidence",
            target_session_id=session_meta.session_id,
        )

        await session._on_asr_final("我们先把语音输入做成主流程")

        assert store.get_evidence(session_meta.session_id) == [
            {"speaker": "Speaker", "text": "我们先把语音输入做成主流程"}
        ]
        assert any(msg["type"] == "transcript_final" for msg in ws.sent)

    asyncio.run(_case())
