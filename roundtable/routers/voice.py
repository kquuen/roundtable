"""Real-time voice WebSocket endpoint."""

from __future__ import annotations

import asyncio
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from roundtable.dependencies import get_store

logger = logging.getLogger("roundtable.routers.voice")
router = APIRouter()

MAX_VOICE_CONCURRENT = int(os.getenv("VOICE_MAX_CONCURRENT", "50"))
_voice_semaphore = asyncio.Semaphore(MAX_VOICE_CONCURRENT)
_voice_active_count = 0


@router.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    """实时语音通话 WebSocket 入口。"""
    global _voice_active_count

    if _voice_semaphore.locked():
        await websocket.close(code=1013, reason="Server busy: too many voice sessions")
        return

    async with _voice_semaphore:
        await websocket.accept()
        _voice_active_count += 1
        logger.info("Voice WebSocket accepted. Active: %d/%d", _voice_active_count, MAX_VOICE_CONCURRENT)

        try:
            from roundtable.voice.session import VoiceSession

            session = VoiceSession(
                frontend_ws=websocket,
                session_store=get_store(),
                mode="qa",
                template="general",
            )
            await session.run()

        except WebSocketDisconnect:
            logger.info("Voice WebSocket disconnected")
        except Exception as e:
            logger.error("Voice WebSocket error: %s", e, exc_info=True)
            try:
                await websocket.close(code=1011, reason="Internal server error")
            except Exception:
                pass
        finally:
            _voice_active_count -= 1
            logger.info("Voice WebSocket closed. Active: %d/%d", _voice_active_count, MAX_VOICE_CONCURRENT)
