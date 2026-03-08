"""Round-trip tests for TwilioAudioBridge (mulaw 8kHz ↔ PCM 16kHz)."""

import base64
import struct
import sys
from pathlib import Path

import pytest

# Make src importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from twilio_audio_bridge import TwilioAudioBridge


@pytest.fixture
def bridge():
    return TwilioAudioBridge()


def _make_pcm_16k_sine(freq_hz: int = 440, duration_ms: int = 20) -> bytes:
    """Generate a short PCM 16-bit 16kHz mono sine wave."""
    import math

    sample_rate = 16000
    n_samples = sample_rate * duration_ms // 1000
    samples = []
    for i in range(n_samples):
        value = int(16000 * math.sin(2 * math.pi * freq_hz * i / sample_rate))
        samples.append(value)
    return struct.pack(f"<{len(samples)}h", *samples)


class TestTwilioToPcm:
    def test_returns_bytes(self, bridge):
        # 160 bytes of mulaw = 20ms at 8kHz
        mulaw_data = b"\xff" * 160
        payload = base64.b64encode(mulaw_data).decode()
        result = bridge.twilio_to_pcm(payload)
        assert isinstance(result, bytes)

    def test_upsamples_to_16k(self, bridge):
        # 160 mulaw samples at 8kHz → 160 PCM samples at 8kHz → ~320 PCM samples at 16kHz
        n_mulaw_samples = 160
        mulaw_data = b"\xff" * n_mulaw_samples
        payload = base64.b64encode(mulaw_data).decode()
        result = bridge.twilio_to_pcm(payload)
        # Each 16-bit sample = 2 bytes; audioop.ratecv may produce ±1 sample
        n_pcm_samples = len(result) // 2
        expected = n_mulaw_samples * 2
        assert abs(n_pcm_samples - expected) <= 1

    def test_output_is_valid_pcm16(self, bridge):
        mulaw_data = b"\x80" * 80
        payload = base64.b64encode(mulaw_data).decode()
        result = bridge.twilio_to_pcm(payload)
        # Must be even number of bytes (16-bit samples)
        assert len(result) % 2 == 0
        # Should be parseable as int16 array
        samples = struct.unpack(f"<{len(result) // 2}h", result)
        for s in samples:
            assert -32768 <= s <= 32767


class TestPcmToTwilio:
    def test_returns_media_event(self, bridge):
        pcm = _make_pcm_16k_sine()
        result = bridge.pcm_to_twilio(pcm, seq=1, stream_sid="MZ123")
        assert result["event"] == "media"
        assert result["streamSid"] == "MZ123"
        assert result["sequenceNumber"] == "1"
        assert "payload" in result["media"]

    def test_payload_is_base64(self, bridge):
        pcm = _make_pcm_16k_sine()
        result = bridge.pcm_to_twilio(pcm, seq=42, stream_sid="MZ456")
        payload = result["media"]["payload"]
        decoded = base64.b64decode(payload)
        assert isinstance(decoded, bytes)
        assert len(decoded) > 0

    def test_downsamples_to_8k(self, bridge):
        pcm_16k = _make_pcm_16k_sine(duration_ms=20)
        n_input_samples = len(pcm_16k) // 2  # 320 samples at 16kHz
        result = bridge.pcm_to_twilio(pcm_16k, seq=1, stream_sid="MZ789")
        mulaw_bytes = base64.b64decode(result["media"]["payload"])
        # mulaw is 1 byte per sample; should be ~half the input sample count
        assert len(mulaw_bytes) == n_input_samples // 2


class TestRoundTrip:
    def test_pcm_to_twilio_to_pcm_preserves_length(self, bridge):
        """PCM 16kHz → Twilio → PCM 16kHz should preserve sample count (±1 from resampling)."""
        original_pcm = _make_pcm_16k_sine(freq_hz=300, duration_ms=40)
        n_original = len(original_pcm) // 2

        # Forward: PCM 16k → Twilio media event
        media_event = bridge.pcm_to_twilio(original_pcm, seq=1, stream_sid="test")
        payload = media_event["media"]["payload"]

        # Reverse: Twilio payload → PCM 16k
        recovered_pcm = bridge.twilio_to_pcm(payload)
        n_recovered = len(recovered_pcm) // 2

        # audioop.ratecv may lose/gain 1 sample per conversion (two conversions total)
        assert abs(n_recovered - n_original) <= 2

    def test_round_trip_signal_similarity(self, bridge):
        """Round-tripped signal should be correlated with the original (lossy but similar)."""
        original_pcm = _make_pcm_16k_sine(freq_hz=440, duration_ms=40)
        original_samples = struct.unpack(f"<{len(original_pcm) // 2}h", original_pcm)

        media_event = bridge.pcm_to_twilio(original_pcm, seq=1, stream_sid="test")
        recovered_pcm = bridge.twilio_to_pcm(media_event["media"]["payload"])
        recovered_samples = struct.unpack(f"<{len(recovered_pcm) // 2}h", recovered_pcm)

        # Compute normalized cross-correlation at zero lag
        n = min(len(original_samples), len(recovered_samples))
        orig = original_samples[:n]
        recv = recovered_samples[:n]

        mean_orig = sum(orig) / n
        mean_recv = sum(recv) / n

        num = sum((o - mean_orig) * (r - mean_recv) for o, r in zip(orig, recv))
        den_orig = sum((o - mean_orig) ** 2 for o in orig) ** 0.5
        den_recv = sum((r - mean_recv) ** 2 for r in recv) ** 0.5

        correlation = num / (den_orig * den_recv) if den_orig * den_recv > 0 else 0
        # mulaw is lossy, but a pure tone should still correlate well
        assert correlation > 0.9, f"Correlation too low: {correlation:.3f}"


class TestMakeMarkEvent:
    def test_structure(self):
        result = TwilioAudioBridge.make_mark_event("MZ123", "turn-end")
        assert result == {
            "event": "mark",
            "streamSid": "MZ123",
            "mark": {"name": "turn-end"},
        }
