"""
Server-side VAD (Voice Activity Detection) using Silero VAD v5 ONNX.

Processes 16kHz PCM audio in 512-sample (32ms) windows. Fires callbacks
after sustained speech or silence to gate barge-in detection.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import torch
from silero_vad import load_silero_vad

logger = logging.getLogger("voicebuddy.vad")

# Silero v5 accepts 512-sample windows at 16kHz
_WINDOW_SAMPLES = 512
_SAMPLE_RATE = 16000
_WINDOW_MS = _WINDOW_SAMPLES / _SAMPLE_RATE * 1000  # 32ms


class VADDetector:
    """Wraps Silero VAD for streaming speech detection with hysteresis."""

    def __init__(
        self,
        on_speech_start: Callable[[], None],
        on_speech_end: Callable[[], None],
        threshold: float = 0.5,
        min_speech_ms: int = 100,
        min_silence_ms: int = 300,
    ):
        self._on_speech_start = on_speech_start
        self._on_speech_end = on_speech_end
        self._threshold = threshold
        self._min_speech_ms = min_speech_ms
        self._min_silence_ms = min_silence_ms

        self._model = load_silero_vad(onnx=True)

        # Audio buffer for accumulating chunks until we have a full window
        self._pcm_buffer = bytearray()

        # State tracking
        self._is_speaking = False
        self._speech_ms = 0.0  # Consecutive ms above threshold
        self._silence_ms = 0.0  # Consecutive ms below threshold

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    def feed(self, pcm_bytes: bytes) -> None:
        """Feed raw s16le 16kHz mono PCM bytes. Processes in 512-sample windows."""
        self._pcm_buffer.extend(pcm_bytes)

        window_bytes = _WINDOW_SAMPLES * 2  # 2 bytes per Int16 sample

        while len(self._pcm_buffer) >= window_bytes:
            chunk = bytes(self._pcm_buffer[:window_bytes])
            del self._pcm_buffer[:window_bytes]
            self._process_window(chunk)

    def reset(self) -> None:
        """Clear all state (call on new turn or barge-in)."""
        self._pcm_buffer.clear()
        self._is_speaking = False
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._model.reset_states()

    def _process_window(self, pcm_bytes: bytes) -> None:
        """Run VAD on a single 512-sample window."""
        # Convert s16le bytes to float32 tensor in [-1, 1]
        int16_tensor = torch.frombuffer(bytearray(pcm_bytes), dtype=torch.int16).float()
        audio = int16_tensor / 32768.0

        prob = self._model(audio, _SAMPLE_RATE).item()

        if prob >= self._threshold:
            self._speech_ms += _WINDOW_MS
            self._silence_ms = 0.0

            if not self._is_speaking and self._speech_ms >= self._min_speech_ms:
                self._is_speaking = True
                logger.info("VAD speech start (prob=%.2f)", prob)
                self._on_speech_start()
        else:
            self._silence_ms += _WINDOW_MS
            self._speech_ms = 0.0

            if self._is_speaking and self._silence_ms >= self._min_silence_ms:
                self._is_speaking = False
                logger.info("VAD speech end (prob=%.2f)", prob)
                self._on_speech_end()
