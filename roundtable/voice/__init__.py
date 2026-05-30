"""Roundtable Voice — Real-time speech-to-text conversation module.

Provides WebSocket-based real-time voice communication with AI.
Uses Alibaba Cloud DashScope Realtime ASR for streaming recognition.
"""

from roundtable.voice.protocol import (
    FrontendMessage,
    BackendMessage,
    VoiceMessageType,
)
from roundtable.voice.session import VoiceSession, VoiceSessionState
from roundtable.voice.asr_client import DashScopeASRClient

__all__ = [
    "FrontendMessage",
    "BackendMessage",
    "VoiceMessageType",
    "VoiceSession",
    "VoiceSessionState",
    "DashScopeASRClient",
]
