"""Tests for /ws/voice WebSocket endpoint."""

import pytest
from fastapi.testclient import TestClient

from roundtable.app import app


@pytest.fixture
def ws_client():
    """Return a TestClient for WebSocket testing."""
    return TestClient(app)


class TestVoiceWebSocket:
    """Integration tests for the voice WebSocket endpoint."""

    def _receive_ready(self, ws):
        """Receive connected status + ready message."""
        msg1 = ws.receive_json()
        assert msg1["type"] == "status"
        assert msg1["state"] == "connected"
        msg2 = ws.receive_json()
        assert msg2["type"] == "ready"
        assert msg2["session_id"].startswith("v_")

    def test_ws_connect_and_ready(self, ws_client):
        """Connecting should immediately receive 'ready'."""
        with ws_client.websocket_connect("/ws/voice") as ws:
            self._receive_ready(ws)

    def test_ws_init_message(self, ws_client):
        """Sending init should transition to listening state."""
        with ws_client.websocket_connect("/ws/voice") as ws:
            self._receive_ready(ws)

            ws.send_json({"type": "init", "mode": "qa", "template": "general"})

            msg = ws.receive_json()
            assert msg["type"] == "status"
            assert msg["state"] == "listening"

    def test_ws_ping_pong(self, ws_client):
        """Ping should be answered with pong."""
        with ws_client.websocket_connect("/ws/voice") as ws:
            self._receive_ready(ws)

            ws.send_json({"type": "ping"})
            msg = ws.receive_json()
            assert msg["type"] == "pong"

    def test_ws_close_message(self, ws_client):
        """Sending close should gracefully end session."""
        with ws_client.websocket_connect("/ws/voice") as ws:
            self._receive_ready(ws)
            ws.send_json({"type": "close", "reason": "user_done"})

    def test_ws_audio_in_mock_mode(self, ws_client):
        """Sending audio in mock mode should not crash."""
        import base64

        with ws_client.websocket_connect("/ws/voice") as ws:
            self._receive_ready(ws)

            ws.send_json({"type": "init", "mode": "qa"})
            ws.receive_json()  # status: listening

            fake_pcm = base64.b64encode(b"\x00" * 3200).decode()
            ws.send_json({"type": "audio", "data": fake_pcm, "seq": 0})

            # In mock mode, no ASR response is expected
            # Just verify the connection stays alive for a moment

    def test_ws_invalid_message_type(self, ws_client):
        """Unknown message type should return error."""
        with ws_client.websocket_connect("/ws/voice") as ws:
            self._receive_ready(ws)

            ws.send_json({"type": "unknown_xyz"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "Invalid message" in msg["message"]

    def test_ws_concurrent_limit(self, ws_client, monkeypatch):
        """Too many concurrent connections should be rejected."""
        import roundtable.app as app_module
        import asyncio

        # Temporarily set max concurrent to 1
        monkeypatch.setattr(app_module, "MAX_VOICE_CONCURRENT", 1)
        monkeypatch.setattr(app_module, "_voice_semaphore", asyncio.Semaphore(1))

        # First connection takes the slot
        with ws_client.websocket_connect("/ws/voice") as ws1:
            self._receive_ready(ws1)

            # Second connection should be rejected
            with pytest.raises(Exception):
                with ws_client.websocket_connect("/ws/voice") as ws2:
                    ws2.receive_json(timeout=0.5)
