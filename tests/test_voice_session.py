"""Targeted tests for VoiceSession lifecycle edge cases."""

from __future__ import annotations

import asyncio
import pytest

from roundtable.voice.session import VoiceSession


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
