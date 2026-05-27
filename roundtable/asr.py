"""Phase 7B: 语音转写 — Whisper API / MiMo API + 按时长分片。

WhisperAdapter:
  - backend='whisper_api': 调用 OpenAI Whisper API（复用 openai 依赖）
  - backend='mimo': 调用小米 MiMo 音频理解 API（Base64 传入）
  - 长音频按时长分片（20 min/段），合并结果
  - pydub + ffmpeg 处理格式转换和分片
"""

from __future__ import annotations

import asyncio
import io
import json as _json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from roundtable.models import ASRResult, ASRSegment

logger = logging.getLogger("roundtable.asr")

# Whisper API 单次最长 25 分钟，留 5 分钟余量
MAX_CHUNK_SECONDS = 20 * 60  # 1200s = 20 min


class WhisperAdapter:
    """语音转写适配器。Whisper API 优先，faster-whisper 可选后续加。"""

    def __init__(
        self,
        backend: str = "whisper_api",
        api_key: str | None = None,
        model: str | None = None,
        language: str = "zh",
    ):
        self.backend = backend
        self.language = language

        if backend == "whisper_api":
            self.model = model or "whisper-1"
            self._api_key = api_key or os.getenv("OPENAI_API_KEY")
            if not self._api_key:
                raise ValueError(
                    "OPENAI_API_KEY not set. Needed for Whisper API. "
                    "Export it: $env:OPENAI_API_KEY='sk-...' (PowerShell) "
                    "or export OPENAI_API_KEY='sk-...' (bash)."
                )
            self._client = AsyncOpenAI(api_key=self._api_key)

        elif backend == "mimo":
            self.model = model or "mimo-v2.5"
            self._api_key = api_key or os.getenv("MIMO_API_KEY")
            if not self._api_key:
                raise ValueError(
                    "MIMO_API_KEY not set. Needed for MiMo audio understanding. "
                    "Export it: $env:MIMO_API_KEY='sk-...' (PowerShell) "
                    "or export MIMO_API_KEY='sk-...' (bash)."
                )
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url="https://api.xiaomimimo.com/v1",
            )

    # ── Public API ──

    def transcribe(self, audio_path: str | Path) -> ASRResult:
        """同步转写入口。"""
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            return _asyncio.run(self.transcribe_async(audio_path))
        raise RuntimeError(
            "Cannot use sync transcribe() inside a running event loop. "
            "Use await transcribe_async() instead."
        )

    async def transcribe_async(self, audio_path: str | Path) -> ASRResult:
        """异步转写。自动检测时长 → 超 20 分钟则分片。"""
        audio_path = Path(audio_path)

        if self.backend == "whisper_api":
            return await self._transcribe_api(audio_path)

        if self.backend == "mimo":
            return await self._transcribe_mimo(audio_path)

        raise ValueError(f"Unknown backend: {self.backend}")

    # ── Whisper API ──

    async def _transcribe_api(self, audio_path: Path) -> ASRResult:
        """Whisper API 转写。长音频自动分片。"""
        duration = self._get_duration(audio_path)

        if duration <= MAX_CHUNK_SECONDS:
            # 短音频：直接转写
            segments, detected_lang = await self._transcribe_chunk(audio_path)
            return ASRResult(
                segments=segments,
                language=detected_lang or self.language,
                duration=duration,
                model_used=self.model,
            )

        # 长音频：按时长分片
        logger.info("Audio duration %.0fs exceeds %ds, splitting into chunks",
                     duration, MAX_CHUNK_SECONDS)
        chunks = self._split_audio(audio_path, duration)
        logger.info("Split into %d chunks", len(chunks))

        try:
            all_segments: list[ASRSegment] = []
            detected_lang = self.language
            time_offset = 0.0

            for i, chunk_path in enumerate(chunks):
                logger.info("Transcribing chunk %d/%d", i + 1, len(chunks))
                segs, lang = await self._transcribe_chunk(chunk_path)

                # Offset segment timestamps
                for seg in segs:
                    seg.start += time_offset
                    seg.end += time_offset
                    all_segments.append(seg)

                if lang:
                    detected_lang = lang
                time_offset += MAX_CHUNK_SECONDS
        finally:
            # Clean up temporary chunk files
            for p in chunks:
                try:
                    p.unlink()
                except OSError:
                    pass

        return ASRResult(
            segments=all_segments,
            language=detected_lang,
            duration=duration,
            model_used=self.model,
        )

    async def _transcribe_chunk(
        self, audio_path: Path,
    ) -> tuple[list[ASRSegment], str | None]:
        """转写单个音频片段 → (segments, detected_language)。"""
        with open(audio_path, "rb") as f:
            response = await self._client.audio.transcriptions.create(
                model=self.model,
                file=f,
                response_format="verbose_json",
                language=self.language,
                timestamp_granularities=["segment"],
            )

        segments = []
        for seg in response.segments:
            segments.append(ASRSegment(
                speaker="Speaker",
                text=seg.get("text", "").strip(),
                start=seg.get("start", 0.0),
                end=seg.get("end", 0.0),
                confidence=seg.get("confidence", 1.0) or 1.0,
            ))

        detected_lang = getattr(response, "language", None)
        return segments, detected_lang

    # ── MiMo API ──

    async def _transcribe_mimo(self, audio_path: Path) -> ASRResult:
        """MiMo 音频理解转写。Base64 传入，返回整段文本。"""
        import base64

        mime_type = self._guess_mime_type(audio_path)
        with open(audio_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        data_uri = f"data:{mime_type};base64,{b64}"

        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个专业的语音转写助手。请准确转写用户提供的音频内容，"
                        "只输出转写后的原始文本，不要添加任何解释、总结、评论或额外说明。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_uri},
                        },
                        {
                            "type": "text",
                            "text": "请转写这段音频的内容。",
                        },
                    ],
                },
            ],
            max_completion_tokens=4096,
        )

        msg = response.choices[0].message
        text = (msg.content or "").strip()
        if not text:
            text = (getattr(msg, "reasoning_content", None) or "").strip()

        duration = self._get_duration(audio_path)
        return ASRResult(
            segments=[
                ASRSegment(
                    speaker="Speaker",
                    text=text,
                    start=0.0,
                    end=duration,
                    confidence=1.0,
                )
            ],
            language=self.language,
            duration=duration,
            model_used=self.model,
        )

    def _guess_mime_type(self, audio_path: Path) -> str:
        """根据文件后缀猜测 MIME 类型。"""
        ext = audio_path.suffix.lower()
        mapping = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".flac": "audio/flac",
            ".m4a": "audio/mp4",
            ".ogg": "audio/ogg",
            ".webm": "audio/webm",
        }
        return mapping.get(ext, "audio/mpeg")

    # ── Audio utilities ──

    def _get_duration(self, audio_path: Path) -> float:
        """获取音频时长（秒），使用 ffprobe。"""
        return _get_duration_ffprobe(audio_path)

    def _split_audio(self, audio_path: Path, total_duration: float) -> list[Path]:
        """按时长将音频切分为多个临时文件。每个 ≤ MAX_CHUNK_SECONDS，使用 ffmpeg。"""
        chunk_sec = MAX_CHUNK_SECONDS
        chunks = []
        for i, start_sec in enumerate(range(0, int(total_duration) + 1, chunk_sec)):
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()

            import subprocess
            cmd = [
                "ffmpeg", "-y", "-i", str(audio_path),
                "-ss", str(start_sec),
                "-t", str(chunk_sec),
                "-c", "copy", str(tmp.name),
            ]
            subprocess.run(cmd, capture_output=True, check=True)
            chunks.append(Path(tmp.name))
            logger.debug("Chunk %d: %d–%d sec → %s",
                         i, start_sec, start_sec + chunk_sec, tmp.name)

        return chunks


def _get_duration_ffprobe(audio_path: Path) -> float:
    """使用 ffprobe 获取音频时长。ffprobe 不可用时返回默认值。"""
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(audio_path)],
            capture_output=True, text=True, check=True,
        )
        info = _json.loads(result.stdout)
        return float(info.get("format", {}).get("duration", 0))
    except Exception:
        logger.warning("ffprobe unavailable, using default duration estimate")
        # Try to estimate from file size (rough: ~1 MB/min for mp3)
        size_mb = audio_path.stat().st_size / (1024 * 1024)
        return max(size_mb * 60, 1.0)  # Rough estimate
