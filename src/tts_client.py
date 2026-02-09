"""
Cartesia Sonic 3 streaming TTS WebSocket wrapper for VoiceBuddy.

Produces raw PCM (16kHz, 16-bit signed, mono) audio chunks suitable for
direct playback via the browser's playPcmChunk function.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from cartesia import AsyncCartesia
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger("voicebuddy.tts")


class TTSClient:
    """Async Cartesia Sonic 3 TTS wrapper using WebSocket streaming."""

    # Must match browser playPcmChunk expectations: raw Int16 PCM, 16kHz mono
    OUTPUT_FORMAT = {
        "container": "raw",
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
    }

    def __init__(self):
        self._api_key = os.environ.get("CARTESIA_API_KEY")
        self._voice_id = os.environ.get("CARTESIA_VOICE_ID")
        self._client = AsyncCartesia(api_key=self._api_key)
        self._ws = None

    async def connect(self):
        """Open a persistent WebSocket connection to Cartesia."""
        self._ws = await self._client.tts.websocket()
        logger.info("Cartesia TTS WebSocket connected")

    async def synthesize(self, text: str, context_id: str | None = None) -> AsyncIterator[bytes]:
        """Synthesize text to raw PCM audio chunks via WebSocket.

        Args:
            text: The sentence to synthesize.
            context_id: Optional context ID for prosody continuity across sentences.

        Yields:
            Raw PCM audio chunks (16kHz, 16-bit signed LE, mono).
        """
        if not self._ws:
            raise RuntimeError("TTS WebSocket not connected — call connect() first")

        send_kwargs = {
            "model_id": "sonic-3",
            "transcript": text,
            "voice": {"mode": "id", "id": self._voice_id},
            "output_format": self.OUTPUT_FORMAT,
        }
        if context_id:
            send_kwargs["context_id"] = context_id

        generator = await self._ws.send(**send_kwargs)

        async for chunk in generator:
            if chunk.audio:
                audio = chunk.audio
                # Guard: raw PCM must NOT start with WAV header
                assert not audio[:4] == b"RIFF", "Received WAV header — expected raw PCM. Check output_format."
                yield audio

    async def close(self):
        """Close the Cartesia WebSocket and client."""
        if self._ws:
            await self._ws.close()
            self._ws = None
        await self._client.close()
        logger.info("Cartesia TTS connection closed")
