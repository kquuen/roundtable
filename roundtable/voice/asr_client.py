"""DashScope Realtime ASR WebSocket client (asyncio).

Connects to Alibaba Cloud DashScope Realtime API for streaming
speech-to-text with server-side VAD (Voice Activity Detection).

Protocol: OpenAI Realtime-compatible WebSocket
Endpoint: wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-asr-flash-realtime
Audio format: PCM 16bit signed, 16000Hz, mono
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from typing import Callable, Awaitable

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidStatusCode

logger = logging.getLogger("roundtable.voice.asr")

DASHSCOPE_REALTIME_URL = (
    "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-asr-flash-realtime"
)


class DashScopeASRClient:
    """Async WebSocket client for DashScope Realtime ASR.

    Usage:
        client = DashScopeASRClient(on_final=handle_final)
        await client.connect()
        await client.send_audio(pcm_chunk)
        ...
        await client.close()
    """

    def __init__(
        self,
        api_key: str | None = None,
        on_final: Callable[[str], Awaitable[None]] | None = None,
        on_partial: Callable[[str], Awaitable[None]] | None = None,
        on_error: Callable[[str], Awaitable[None]] | None = None,
        enable_server_vad: bool = True,
    ):
        self._api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self._on_final = on_final
        self._on_partial = on_partial
        self._on_error = on_error
        self._enable_server_vad = enable_server_vad

        self._ws: websockets.WebSocketClientProtocol | None = None
        self._receive_task: asyncio.Task | None = None
        self._connected = False
        self._session_id = f"asr_{uuid.uuid4().hex[:8]}"
        self._seq = 0

        # Mock mode when no API key
        self._mock_mode = not self._api_key
        if self._mock_mode:
            logger.warning("DASHSCOPE_API_KEY not set — ASR client running in MOCK mode")

    # ── Public API ──

    async def connect(self) -> None:
        """Establish WebSocket connection to DashScope and send session config."""
        if self._mock_mode:
            self._connected = True
            logger.info("[MOCK ASR] Connected (simulated)")
            return

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }

        try:
            self._ws = await websockets.connect(
                DASHSCOPE_REALTIME_URL,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=10,
            )
        except InvalidStatus as e:
            raise RuntimeError(f"DashScope connection rejected: HTTP {e.response.status_code}") from e
        except InvalidStatusCode as e:
            raise RuntimeError(f"DashScope connection rejected: HTTP {e.status_code}") from e
        except Exception as e:
            raise RuntimeError(f"DashScope connection failed: {e}") from e

        self._connected = True
        logger.info("DashScope ASR WebSocket connected")

        # Send session configuration
        await self._send_session_update()

        # Start background receive loop
        self._receive_task = asyncio.create_task(
            self._receive_loop(),
            name=f"asr-receive-{self._session_id}",
        )

    async def send_audio(self, pcm_bytes: bytes) -> None:
        """Send a chunk of PCM audio data.

        Args:
            pcm_bytes: Raw PCM 16bit signed, 16000Hz, mono audio bytes.
        """
        if not self._connected:
            raise RuntimeError("ASR client not connected. Call connect() first.")

        if self._mock_mode:
            # In mock mode, just accumulate; no real ASR happens
            return

        if self._ws is None or self._ws.state != 1:  # 1 = OPEN
            raise RuntimeError("ASR WebSocket is not open")

        encoded = base64.b64encode(pcm_bytes).decode("utf-8")
        event = {
            "event_id": f"evt_{self._seq}_{uuid.uuid4().hex[:4]}",
            "type": "input_audio_buffer.append",
            "audio": encoded,
        }
        await self._ws.send(json.dumps(event))
        self._seq += 1

    async def commit(self) -> None:
        """Manually commit audio buffer (for non-VAD mode)."""
        if self._mock_mode or not self._connected or self._ws is None:
            return

        event = {
            "event_id": f"evt_commit_{uuid.uuid4().hex[:8]}",
            "type": "input_audio_buffer.commit",
        }
        await self._ws.send(json.dumps(event))

    async def finish(self) -> None:
        """Signal end of audio stream."""
        if self._mock_mode or not self._connected or self._ws is None:
            return

        event = {
            "event_id": f"evt_finish_{uuid.uuid4().hex[:8]}",
            "type": "session.finish",
        }
        await self._ws.send(json.dumps(event))

    async def close(self) -> None:
        """Close the WebSocket connection and cleanup."""
        self._connected = False

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        logger.info("DashScope ASR client closed")

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def mock_mode(self) -> bool:
        return self._mock_mode

    # ── Internal ──

    async def _send_session_update(self) -> None:
        """Send session.update to configure ASR behavior."""
        if self._ws is None:
            return

        session_config = {
            "event_id": f"evt_sess_{uuid.uuid4().hex[:8]}",
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "input_audio_format": "pcm",
                "sample_rate": 16000,
                "input_audio_transcription": {
                    "language": "zh",
                },
                "turn_detection": (
                    {
                        "type": "server_vad",
                        "threshold": 0.0,
                        "silence_duration_ms": 400,
                    }
                    if self._enable_server_vad
                    else None
                ),
            },
        }
        await self._ws.send(json.dumps(session_config))
        logger.debug("Session update sent (server_vad=%s)", self._enable_server_vad)

    async def _receive_loop(self) -> None:
        """Background task: continuously receive messages from DashScope."""
        if self._ws is None:
            return

        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("ASR received non-JSON message: %s", message[:200])
                    continue

                await self._handle_message(data)
        except ConnectionClosed:
            logger.info("DashScope ASR WebSocket closed by server")
        except asyncio.CancelledError:
            logger.debug("ASR receive loop cancelled")
            raise
        except Exception as e:
            logger.error("ASR receive loop error: %s", e, exc_info=True)
            if self._on_error:
                try:
                    await self._on_error(f"ASR receive error: {e}")
                except Exception:
                    pass
        finally:
            self._connected = False

    async def _handle_message(self, data: dict) -> None:
        """Dispatch DashScope events to callbacks."""
        msg_type = data.get("type", "")

        # Final transcription of one utterance (server_vad triggered)
        if msg_type == "conversation.item.input_audio_transcription.completed":
            transcript = data.get("transcript", "")
            if transcript and self._on_final:
                logger.info("ASR final transcript: %s", transcript)
                try:
                    await self._on_final(transcript)
                except Exception as e:
                    logger.error("on_final callback error: %s", e, exc_info=True)
            return

        # Session started confirmation
        if msg_type == "session.created":
            logger.info("DashScope ASR session created: %s", data.get("session", {}).get("id"))
            return

        # Session updated confirmation
        if msg_type == "session.updated":
            logger.debug("DashScope ASR session updated")
            return

        # Error from DashScope
        if msg_type == "error":
            error_msg = data.get("error", {}).get("message", "Unknown ASR error")
            logger.error("DashScope ASR error: %s", error_msg)
            if self._on_error:
                try:
                    await self._on_error(error_msg)
                except Exception:
                    pass
            return

        # Session finished
        if msg_type == "session.finished":
            logger.info("DashScope ASR session finished")
            return

        # Debug: log unhandled events at debug level
        logger.debug("Unhandled ASR event: %s", msg_type)
