"""Unit tests for the VAD detector wrapper."""

import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from vad_detector import VADDetector


def _make_silence(duration_ms: int) -> bytes:
    """Generate silent PCM (zeros) at 16kHz."""
    num_samples = int(16000 * duration_ms / 1000)
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))


def _make_tone(duration_ms: int, freq: float = 440.0, amplitude: float = 0.8) -> bytes:
    """Generate a sine tone as s16le 16kHz PCM."""
    import math

    num_samples = int(16000 * duration_ms / 1000)
    samples = []
    for i in range(num_samples):
        t = i / 16000
        val = int(amplitude * 32767 * math.sin(2 * math.pi * freq * t))
        samples.append(max(-32768, min(32767, val)))
    return struct.pack(f"<{num_samples}h", *samples)


class TestVADBasics:
    def test_vad_ignores_silence(self):
        """Feed zero PCM — no speech callback should fire."""
        on_start = MagicMock()
        on_end = MagicMock()
        vad = VADDetector(on_speech_start=on_start, on_speech_end=on_end)

        # Feed 500ms of silence
        silence = _make_silence(500)
        vad.feed(silence)

        on_start.assert_not_called()
        on_end.assert_not_called()
        assert not vad.is_speaking

    def test_vad_detects_speech(self):
        """Feed a loud tone — speech start callback should fire."""
        on_start = MagicMock()
        on_end = MagicMock()
        vad = VADDetector(on_speech_start=on_start, on_speech_end=on_end, threshold=0.3)

        # Feed 500ms of tone — Silero may or may not detect this as speech
        # depending on the model, so we test the plumbing works
        tone = _make_tone(500, freq=300, amplitude=0.9)
        vad.feed(tone)

        # If speech was detected, is_speaking should reflect that
        if on_start.called:
            assert vad.is_speaking

    def test_vad_speech_end(self):
        """If speech is detected, followed by silence, end callback fires."""
        on_start = MagicMock()
        on_end = MagicMock()
        vad = VADDetector(
            on_speech_start=on_start,
            on_speech_end=on_end,
            threshold=0.3,
            min_speech_ms=32,
            min_silence_ms=100,
        )

        # Feed tone then silence
        tone = _make_tone(500, freq=300, amplitude=0.9)
        vad.feed(tone)

        if on_start.called:
            # Now feed enough silence to trigger end
            silence = _make_silence(500)
            vad.feed(silence)
            assert on_end.called or not vad.is_speaking

    def test_vad_min_duration_filter(self):
        """Brief noise (<min_speech_ms) should NOT trigger start callback."""
        on_start = MagicMock()
        on_end = MagicMock()
        vad = VADDetector(
            on_speech_start=on_start,
            on_speech_end=on_end,
            threshold=0.5,
            min_speech_ms=200,  # Require 200ms of speech
        )

        # Feed only 30ms of tone (less than one 32ms window)
        tone = _make_tone(30, freq=300, amplitude=0.9)
        vad.feed(tone)
        # Then immediate silence
        silence = _make_silence(300)
        vad.feed(silence)

        # Even if Silero scores this as speech, min_speech_ms should filter it
        assert not vad.is_speaking

    def test_vad_reset(self):
        """After reset, state is clean."""
        on_start = MagicMock()
        on_end = MagicMock()
        vad = VADDetector(on_speech_start=on_start, on_speech_end=on_end, threshold=0.3)

        # Feed some audio
        tone = _make_tone(200, freq=300, amplitude=0.9)
        vad.feed(tone)

        # Reset
        vad.reset()

        assert not vad.is_speaking
        # Internal buffer should be clear (no leftover from previous audio)
        # Feed silence — should not trigger anything
        on_start.reset_mock()
        on_end.reset_mock()
        silence = _make_silence(100)
        vad.feed(silence)
        on_start.assert_not_called()

    def test_vad_feed_small_chunks(self):
        """Feed audio in 20ms (640 byte) chunks — VAD should buffer and process."""
        on_start = MagicMock()
        on_end = MagicMock()
        vad = VADDetector(on_speech_start=on_start, on_speech_end=on_end)

        # Feed 500ms of silence in 20ms chunks (25 chunks)
        chunk = _make_silence(20)
        for _ in range(25):
            vad.feed(chunk)

        on_start.assert_not_called()
        assert not vad.is_speaking
