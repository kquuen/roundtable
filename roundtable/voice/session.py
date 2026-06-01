"""VoiceSession — Real-time voice conversation state machine.

Manages the full lifecycle of a voice call:
  Frontend WebSocket  ←→  VoiceSession  ←→  DashScope ASR  ←→  DeepSeek LLM

State machine:
  IDLE → CONNECTED → LISTENING → THINKING → RESPONDING → LISTENING (loop)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from enum import Enum

from fastapi import WebSocket, WebSocketDisconnect

from roundtable.models import SessionStatus
from roundtable.providers import ProviderRouter
from roundtable.voice.asr_client import DashScopeASRClient
from roundtable.voice.protocol import (
    parse_frontend_message,
    InitMessage,
    AudioMessage,
    CommitMessage,
    PingMessage,
    CloseMessage,
    ReadyMessage,
    StatusMessage,
    TranscriptFinalMessage,
    AIResponseMessage,
    ErrorMessage,
    PongMessage,
)

logger = logging.getLogger("roundtable.voice.session")

# Session limits
MAX_SESSION_MINUTES = int(os.getenv("VOICE_MAX_SESSION_MINUTES", "5"))
MAX_AUDIO_QUEUE_SIZE = 200  # ~20s of audio at 100ms chunks


class VoiceSessionState(str, Enum):
    IDLE = "idle"
    CONNECTED = "connected"
    LISTENING = "listening"
    THINKING = "thinking"
    RESPONDING = "responding"
    CLOSED = "closed"


class VoiceSession:
    """Manages one real-time voice conversation.

    Args:
        frontend_ws: The FastAPI WebSocket connected to the client.
        provider: LLM provider (None = mock mode).
        session_store: Optional store for evidence capture mode.
        mode: "qa", "personal_roundtable", or "evidence".
        template: DecisionTemplate for personal_roundtable mode.
        context: Optional background context.
        target_session_id: Existing Roundtable session for evidence capture.
    """

    def __init__(
        self,
        frontend_ws: WebSocket,
        provider=None,
        session_store=None,
        mode: str = "qa",
        template: str = "general",
        context: Optional[str] = None,
        target_session_id: Optional[str] = None,
    ):
        self.session_id = f"v_{uuid.uuid4().hex[:8]}"
        self.frontend_ws = frontend_ws
        self.mode = mode
        self.template = template
        self.context = context or ""
        self.session_store = session_store
        self.target_session_id = target_session_id
        self._user_provider = provider  # optional override

        self.state = VoiceSessionState.IDLE
        self._asr: DashScopeASRClient | None = None
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MAX_AUDIO_QUEUE_SIZE)
        self._transcript_buffer: list[str] = []
        self._conversation_history: list[dict] = []
        self._closed = False
        self._llm_lock = asyncio.Lock()  # Prevent concurrent LLM calls

        # Timeout tracking
        self._last_activity = asyncio.get_running_loop().time()

    def _get_provider(self):
        """Return the effective provider for this session."""
        if self._user_provider is not None:
            return self._user_provider
        return ProviderRouter.get_instance().get_default()

    # ── Main entry ──

    async def run(self) -> None:
        """Main session loop. Called by the FastAPI WebSocket endpoint."""
        audio_task: asyncio.Task | None = None
        watchdog_task: asyncio.Task | None = None
        try:
            await self._transition(VoiceSessionState.CONNECTED)
            await self._send(ReadyMessage(session_id=self.session_id))

            # Start ASR client
            self._asr = DashScopeASRClient(
                on_final=self._on_asr_final,
                on_error=self._on_asr_error,
                enable_server_vad=True,
            )
            await self._asr.connect()

            # Start background audio forwarding task
            audio_task = asyncio.create_task(
                self._audio_forward_loop(),
                name=f"audio-fwd-{self.session_id}",
            )

            # Start timeout watchdog
            watchdog_task = asyncio.create_task(
                self._watchdog_loop(),
                name=f"watchdog-{self.session_id}",
            )

            # Main frontend message loop
            await self._frontend_loop()

        except WebSocketDisconnect:
            logger.info("[%s] Client disconnected", self.session_id)
        except Exception as e:
            logger.error("[%s] Session error: %s", self.session_id, e, exc_info=True)
            await self._send(ErrorMessage(message=f"Session error: {e}"))
        finally:
            await self._cleanup()
            if audio_task is not None:
                audio_task.cancel()
                try:
                    await audio_task
                except asyncio.CancelledError:
                    pass
            if watchdog_task is not None:
                watchdog_task.cancel()
                try:
                    await watchdog_task
                except asyncio.CancelledError:
                    pass

    # ── Frontend message loop ──

    async def _frontend_loop(self) -> None:
        """Continuously receive and process messages from the frontend."""
        while not self._closed and self.state != VoiceSessionState.CLOSED:
            try:
                raw = await self.frontend_ws.receive_json()
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.warning("[%s] Failed to receive JSON: %s", self.session_id, e)
                continue

            self._last_activity = asyncio.get_running_loop().time()

            try:
                msg = parse_frontend_message(raw)
            except ValueError as e:
                await self._send(ErrorMessage(message=f"Invalid message: {e}"))
                continue

            if isinstance(msg, InitMessage):
                # Re-configure session parameters
                self.mode = msg.mode
                self.template = msg.template
                self.context = msg.context or ""
                self.target_session_id = msg.session_id or self.target_session_id
                logger.info(
                    "[%s] Re-initialized: mode=%s, template=%s, target_session_id=%s",
                    self.session_id,
                    self.mode,
                    self.template,
                    self.target_session_id,
                )
                await self._transition(VoiceSessionState.LISTENING)

            elif isinstance(msg, AudioMessage):
                if self.state == VoiceSessionState.IDLE:
                    await self._transition(VoiceSessionState.LISTENING)

                # Decode and queue audio for ASR forwarding
                try:
                    pcm = self._decode_audio(msg.data)
                    if self._audio_queue.full():
                        # Drop oldest chunk to prevent memory growth
                        try:
                            self._audio_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    await self._audio_queue.put(pcm)
                except Exception as e:
                    logger.warning("[%s] Audio decode error: %s", self.session_id, e)

            elif isinstance(msg, CommitMessage):
                # Manual commit (for non-VAD mode)
                if self._asr:
                    await self._asr.commit()

            elif isinstance(msg, PingMessage):
                await self._send(PongMessage())

            elif isinstance(msg, CloseMessage):
                logger.info("[%s] Client requested close: %s", self.session_id, msg.reason)
                break

    # ── Audio forwarding ──

    async def _audio_forward_loop(self) -> None:
        """Background task: dequeue audio chunks and send to ASR."""
        while not self._closed:
            try:
                pcm = await asyncio.wait_for(self._audio_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if self._asr and self._asr.connected:
                try:
                    await self._asr.send_audio(pcm)
                except Exception as e:
                    logger.warning("[%s] ASR send error: %s", self.session_id, e)

    # ── ASR callbacks ──

    async def _on_asr_final(self, text: str) -> None:
        """Called when DashScope returns a final transcript (server_vad)."""
        logger.info("[%s] ASR final: %s", self.session_id, text)
        self._transcript_buffer.append(text)

        # Send final transcript to frontend
        await self._send(TranscriptFinalMessage(text=text, is_final=True))

        if self.mode == "evidence":
            await self._append_evidence_transcript(text)
            await self._transition(VoiceSessionState.LISTENING)
            return

        # Process through LLM
        await self._process_user_utterance(text)

    async def _append_evidence_transcript(self, text: str) -> None:
        """Append a final transcript sentence to an existing evidence session."""
        if not self.session_store or not self.target_session_id:
            return

        session = self.session_store.get(self.target_session_id)
        if not session:
            await self._send(ErrorMessage(message="Target session not found for voice evidence"))
            return

        existing = list(self.session_store.get_evidence(self.target_session_id))
        existing.append({"speaker": "Speaker", "text": text})
        self.session_store.store_evidence(self.target_session_id, existing)
        self.session_store.update_status(self.target_session_id, SessionStatus.TRANSCRIBING)

    async def _on_asr_error(self, error_msg: str) -> None:
        """Called when ASR encounters an error."""
        logger.error("[%s] ASR error: %s", self.session_id, error_msg)
        await self._send(ErrorMessage(message=f"ASR error: {error_msg}"))

    # ── LLM processing ──

    async def _process_user_utterance(self, text: str) -> None:
        """Send the recognized text to LLM and push the response to frontend."""
        async with self._llm_lock:
            await self._transition(VoiceSessionState.THINKING)

            try:
                if self.mode == "personal_roundtable":
                    response = await self._run_roundtable_mode(text)
                else:
                    response = await self._run_qa_mode(text)
            except Exception as e:
                logger.error("[%s] LLM error: %s", self.session_id, e, exc_info=True)
                await self._send(ErrorMessage(message=f"AI response failed: {e}"))
                await self._transition(VoiceSessionState.LISTENING)
                return

            await self._transition(VoiceSessionState.RESPONDING)
            await self._send(AIResponseMessage(text=response))
            await self._transition(VoiceSessionState.LISTENING)

    async def _run_qa_mode(self, text: str) -> str:
        """Simple Q&A mode: direct LLM chat with roundtable persona."""
        system_prompt = (
            "你是圆桌会议 AI 助手。用户正在通过语音与你实时对话。"
            "请用简洁、口语化的中文回复，不要太长（控制在 200 字以内）。"
            "如果用户的问题适合多人专家分析，可以建议他使用圆桌模式。"
        )
        if self.context:
            system_prompt += f"\n\n背景上下文：{self.context}"

        # Build conversation history
        messages = [{"role": "system", "content": system_prompt}]
        for turn in self._conversation_history[-6:]:  # Keep last 6 turns
            messages.append({"role": "user", "content": turn["user"]})
            messages.append({"role": "assistant", "content": turn["assistant"]})
        messages.append({"role": "user", "content": text})

        provider = self._get_provider()
        if provider is None or getattr(provider, "provider_id", None) == "mock":
            # Mock mode
            return f"[Mock] 收到你的问题：{text[:50]}... 这是一个很好的问题，建议从多个角度深入分析。"

        # Call LLM
        raw = await provider.chat(
            system_prompt=system_prompt,
            user_message=text,
            max_tokens=800,
            temperature=0.7,
        )

        # Store in history
        self._conversation_history.append({"user": text, "assistant": raw})
        return raw

    async def _run_roundtable_mode(self, text: str) -> str:
        """Roundtable mode: treat user speech as a question and run anchored debate."""
        from roundtable.debate import (
            AnchoredDebateEngine, sanitize_user_bias, get_interview_questions,
        )
        from roundtable.models import InterviewContext, DecisionTemplate, DebateMode
        from roundtable.report import compose_anchored_report

        sanitized, bias_signal = sanitize_user_bias(text)

        try:
            template = DecisionTemplate(self.template)
        except ValueError:
            template = DecisionTemplate.GENERAL

        interview = InterviewContext(
            session_id=self.session_id,
            original_question=text,
            template=template,
            questions=[],
            answers={},
            enriched_context=(self.context or "") + f"\n原始问题：{sanitized}",
            user_bias_signal=bias_signal,
        )

        engine = AnchoredDebateEngine(provider=self._get_provider())
        report = await engine.run(interview, mode=DebateMode.QUICK)
        md = compose_anchored_report(report)

        # Store in history
        self._conversation_history.append({"user": text, "assistant": md})
        return md

    # ── Utilities ──

    async def _transition(self, new_state: VoiceSessionState) -> None:
        """Update state and notify frontend."""
        if self.state == new_state:
            return
        old_state = self.state
        self.state = new_state
        logger.debug("[%s] State: %s → %s", self.session_id, old_state, new_state)
        await self._send(StatusMessage(state=new_state.value))

    async def _send(self, msg) -> None:
        """Safely send a message to the frontend WebSocket."""
        if self._closed:
            return
        try:
            data = msg.model_dump()
            await self.frontend_ws.send_json(data)
        except Exception as e:
            logger.warning("[%s] Failed to send to frontend: %s", self.session_id, e)

    @staticmethod
    def _decode_audio(base64_data: str) -> bytes:
        """Decode base64 audio string to PCM bytes."""
        import base64 as _base64
        return _base64.b64decode(base64_data)

    async def _watchdog_loop(self) -> None:
        """Close inactive sessions after timeout."""
        timeout_seconds = MAX_SESSION_MINUTES * 60
        while not self._closed:
            await asyncio.sleep(30)
            elapsed = asyncio.get_running_loop().time() - self._last_activity
            if elapsed > timeout_seconds:
                logger.info("[%s] Session timeout after %ds", self.session_id, int(elapsed))
                await self._send(ErrorMessage(message="Session timeout due to inactivity"))
                break

    async def _cleanup(self) -> None:
        """Graceful shutdown."""
        self._closed = True
        self.state = VoiceSessionState.CLOSED

        if self._asr:
            try:
                await self._asr.close()
            except Exception:
                pass
            self._asr = None

        try:
            await self.frontend_ws.close()
        except Exception:
            pass

        logger.info("[%s] Session cleaned up", self.session_id)
