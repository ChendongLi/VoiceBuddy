"""
Deepgram Flux v2 WebSocket wrapper for VoiceBuddy.

One instance per browser session. Buffers 20ms browser chunks into 80ms frames
before forwarding to Deepgram. Fires synchronous callbacks for StartOfTurn and
EndOfTurn events — callers should push these to an asyncio.Queue.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from dotenv import load_dotenv

# Load .env BEFORE importing Deepgram SDK (it reads DEEPGRAM_API_KEY at import time)
load_dotenv(Path(__file__).parent.parent / ".env")

from deepgram import AsyncDeepgramClient  # noqa: E402
from deepgram.core.events import EventType  # noqa: E402

logger = logging.getLogger("voicebuddy.deepgram")


class DeepgramFluxClient:
    """Async Deepgram Flux v2 wrapper. One instance per browser session."""

    # 80ms at 16kHz, mono, 16-bit = 2560 bytes
    CHUNK_80MS = 2560

    def __init__(
        self,
        on_start_of_turn: Callable[[int, float], None],
        on_end_of_turn: Callable[[int, str, float, float], None],
        on_transcript_update: Callable[[str], None] | None = None,
    ):
        """
        Args:
            on_start_of_turn: callback(turn_index, timestamp_ms)
            on_end_of_turn: callback(turn_index, transcript, confidence, transcript_received_ms)
            on_transcript_update: optional callback(partial_transcript) for interim results
        """
        self._on_start_of_turn = on_start_of_turn
        self._on_end_of_turn = on_end_of_turn
        self._on_transcript_update = on_transcript_update

        self._audio_buffer = bytearray()
        self._turn_index = 0
        self._current_transcript = ""
        self._ctx = None
        self._connection = None
        self._listen_task: asyncio.Task | None = None
        self._client = AsyncDeepgramClient(api_key=os.environ.get("DEEPGRAM_API_KEY"))

    async def connect(self):
        """Open a Deepgram Flux v2 streaming connection."""
        self._ctx = self._client.listen.v2.connect(
            model="flux-general-en",
            encoding="linear16",
            sample_rate="16000",
            eot_threshold=0.7,
        )

        # Enter the async context manager to get the actual connection
        self._connection = await self._ctx.__aenter__()

        # Register event handlers on the connection object
        self._connection.on(EventType.OPEN, self._on_open)
        self._connection.on(EventType.MESSAGE, self._on_message)
        self._connection.on(EventType.CLOSE, self._on_close)
        self._connection.on(EventType.ERROR, self._on_error)

        # Start listening in background
        self._listen_task = asyncio.create_task(self._connection.start_listening())
        # Brief settle time for WebSocket handshake
        await asyncio.sleep(0.2)
        logger.info("Deepgram Flux connection established")

    async def send_audio(self, chunk: bytes):
        """Buffer 20ms chunks, forward to Deepgram when 80ms accumulated."""
        if not self._connection:
            return

        self._audio_buffer.extend(chunk)
        while len(self._audio_buffer) >= self.CHUNK_80MS:
            frame = bytes(self._audio_buffer[: self.CHUNK_80MS])
            del self._audio_buffer[: self.CHUNK_80MS]
            await self._connection._send(frame)

    async def flush_audio(self):
        """Send any remaining buffered audio (< 80ms) on disconnect/EOT."""
        if self._connection and len(self._audio_buffer) > 0:
            await self._connection._send(bytes(self._audio_buffer))
            self._audio_buffer.clear()

    async def close(self):
        """Flush remaining audio, cancel listen task, close connection."""
        await self.flush_audio()

        if self._listen_task:
            self._listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listen_task
            self._listen_task = None

        if self._ctx:
            await self._ctx.__aexit__(None, None, None)
            self._ctx = None
            self._connection = None

        logger.info("Deepgram Flux connection closed")

    # --- Event handlers (called from Deepgram SDK's internal task) ---

    def _on_open(self, _open_response):
        logger.info("Deepgram WebSocket opened")

    def _on_message(self, msg) -> None:
        """Handle Deepgram Flux v2 messages."""
        # Check for event-based signals (StartOfTurn, EndOfTurn)
        event_type = getattr(msg, "event", None)

        if event_type == "StartOfTurn":
            self._turn_index += 1
            ts_ms = time.time() * 1000
            logger.info("Deepgram StartOfTurn (turn %d)", self._turn_index)
            self._on_start_of_turn(self._turn_index, ts_ms)
            return

        if event_type == "EndOfTurn":
            ts_ms = time.time() * 1000
            transcript = self._current_transcript.strip()
            eot_conf = getattr(msg, "end_of_turn_confidence", 0.0)
            logger.info("Deepgram EndOfTurn (turn %d): %r [conf=%.2f]", self._turn_index, transcript, eot_conf)
            self._on_end_of_turn(self._turn_index, transcript, eot_conf, ts_ms)
            self._current_transcript = ""
            return

        # Accumulate transcript from Results messages
        transcript = getattr(msg, "transcript", None)
        if transcript:
            self._current_transcript = transcript
            if self._on_transcript_update:
                self._on_transcript_update(transcript)

    def _on_close(self, _close_response):
        logger.info("Deepgram WebSocket closed")

    def _on_error(self, error):
        logger.error("Deepgram error: %s", error)
