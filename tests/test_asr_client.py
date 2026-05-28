"""Tests for DashScopeASRClient."""

import asyncio
import base64
import json
import pytest

from roundtable.voice.asr_client import DashScopeASRClient


@pytest.fixture
def mock_final_callback():
    """Collect final transcripts."""
    transcripts = []

    async def cb(text: str):
        transcripts.append(text)

    return cb, transcripts


class TestDashScopeASRClientMockMode:
    """Test ASR client in mock mode (no API key)."""

    @pytest.mark.asyncio
    async def test_mock_connect(self):
        client = DashScopeASRClient(api_key="", enable_server_vad=True)
        await client.connect()
        assert client.connected is True
        assert client.mock_mode is True
        await client.close()

    @pytest.mark.asyncio
    async def test_mock_send_audio_noop(self):
        client = DashScopeASRClient(api_key="")
        await client.connect()
        # In mock mode, send_audio should not raise
        await client.send_audio(b"\x00\x01\x02\x03")
        await client.close()

    @pytest.mark.asyncio
    async def test_mock_close_idempotent(self):
        client = DashScopeASRClient(api_key="")
        await client.connect()
        await client.close()
        await client.close()  # Should not raise


class TestDashScopeASRClientRealMode:
    """Test ASR client behavior with mocked WebSocket."""

    @pytest.mark.asyncio
    async def test_session_update_sent_on_connect(self, monkeypatch):
        """Verify that session.update is sent immediately after connect."""
        sent_messages = []

        class FakeWs:
            state = 1

            async def send(self, data):
                sent_messages.append(json.loads(data))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def __aiter__(self):
                class _AsyncIter:
                    def __init__(self, items):
                        self._items = items
                    def __aiter__(self):
                        return self
                    async def __anext__(self):
                        if not self._items:
                            raise StopAsyncIteration
                        return self._items.pop(0)
                return _AsyncIter([])

            async def close(self):
                pass

        async def fake_connect(*args, **kwargs):
            return FakeWs()

        monkeypatch.setattr(
            "roundtable.voice.asr_client.websockets.connect", fake_connect
        )

        client = DashScopeASRClient(api_key="sk-test", enable_server_vad=True)
        await client.connect()

        # Give a tiny moment for the background receive loop to start
        await asyncio.sleep(0.01)

        # First message should be session.update
        assert len(sent_messages) >= 1
        assert sent_messages[0]["type"] == "session.update"
        assert sent_messages[0]["session"]["turn_detection"]["type"] == "server_vad"

        await client.close()

    @pytest.mark.asyncio
    async def test_send_audio_encodes_correctly(self, monkeypatch):
        """Verify audio bytes are base64-encoded before sending."""
        sent_messages = []

        class FakeWs:
            state = 1

            async def send(self, data):
                sent_messages.append(json.loads(data))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def __aiter__(self):
                class _AsyncIter:
                    def __aiter__(self):
                        return self
                    async def __anext__(self):
                        # Hang forever so _receive_loop keeps running
                        await asyncio.sleep(3600)
                        raise StopAsyncIteration
                return _AsyncIter()

            async def close(self):
                pass

        async def fake_connect(*args, **kwargs):
            return FakeWs()

        monkeypatch.setattr(
            "roundtable.voice.asr_client.websockets.connect", fake_connect
        )

        client = DashScopeASRClient(api_key="sk-test")
        await client.connect()
        await asyncio.sleep(0.01)

        raw = b"\x00\x01\x02\x03"
        await client.send_audio(raw)

        audio_msgs = [m for m in sent_messages if m.get("type") == "input_audio_buffer.append"]
        assert len(audio_msgs) == 1
        decoded = base64.b64decode(audio_msgs[0]["audio"])
        assert decoded == raw

        await client.close()

    @pytest.mark.asyncio
    async def test_final_transcript_callback(self, monkeypatch, mock_final_callback):
        cb, transcripts = mock_final_callback

        class FakeWs:
            state = 1
            _messages = []

            async def send(self, data):
                self._messages.append(json.loads(data))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def close(self):
                pass

            def __aiter__(self):
                events = [
                    json.dumps({
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "你好世界",
                    }),
                ]
                class _AsyncIter:
                    def __init__(self, items):
                        self._items = items
                    def __aiter__(self):
                        return self
                    async def __anext__(self):
                        if not self._items:
                            raise StopAsyncIteration
                        return self._items.pop(0)
                return _AsyncIter(events)

        async def fake_connect(*args, **kwargs):
            return FakeWs()

        monkeypatch.setattr(
            "roundtable.voice.asr_client.websockets.connect", fake_connect
        )

        client = DashScopeASRClient(api_key="sk-test", on_final=cb)
        await client.connect()
        await asyncio.sleep(0.1)

        assert "你好世界" in transcripts
        await client.close()

    @pytest.mark.asyncio
    async def test_connect_uses_additional_headers(self, monkeypatch):
        """websockets>=15 should receive additional_headers, not extra_headers."""
        captured_kwargs = {}

        class FakeWs:
            state = 1

            async def send(self, data):
                pass

            def __aiter__(self):
                class _AsyncIter:
                    def __aiter__(self):
                        return self

                    async def __anext__(self):
                        raise StopAsyncIteration

                return _AsyncIter()

            async def close(self):
                pass

        async def fake_connect(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeWs()

        monkeypatch.setattr("roundtable.voice.asr_client.websockets.connect", fake_connect)

        client = DashScopeASRClient(api_key="sk-test")
        await client.connect()
        await client.close()

        assert "additional_headers" in captured_kwargs
        assert "extra_headers" not in captured_kwargs
