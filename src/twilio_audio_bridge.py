"""
Twilio ↔ VoiceBuddy audio format bridge.

Converts between Twilio MediaStream format (mulaw 8kHz) and the internal
pipeline format (PCM 16-bit signed LE, 16kHz mono).

Uses stdlib audioop when available (Python ≤3.12). Falls back to numpy
for Python 3.13+ where audioop was removed.
"""

from __future__ import annotations

import base64
import struct

try:
    import audioop

    def ulaw2lin(data: bytes, width: int) -> bytes:
        return audioop.ulaw2lin(data, width)

    def lin2ulaw(data: bytes, width: int) -> bytes:
        return audioop.lin2ulaw(data, width)

    def ratecv(
        data: bytes, width: int, nchannels: int, inrate: int, outrate: int
    ) -> bytes:
        result, _ = audioop.ratecv(data, width, nchannels, inrate, outrate, None)
        return result

except ImportError:
    import numpy as np

    # ITU-T G.711 mu-law expansion table (256 entries)
    _ULAW_TO_LINEAR = np.array(
        [
            -32124, -31100, -30076, -29052, -28028, -27004, -25980, -24956,
            -23932, -22908, -21884, -20860, -19836, -18812, -17788, -16764,
            -15996, -15484, -14972, -14460, -13948, -13436, -12924, -12412,
            -11900, -11388, -10876, -10364, -9852, -9340, -8828, -8316,
            -7932, -7676, -7420, -7164, -6908, -6652, -6396, -6140,
            -5884, -5628, -5372, -5116, -4860, -4604, -4348, -4092,
            -3900, -3772, -3644, -3516, -3388, -3260, -3132, -3004,
            -2876, -2748, -2620, -2492, -2364, -2236, -2108, -1980,
            -1884, -1820, -1756, -1692, -1628, -1564, -1500, -1436,
            -1372, -1308, -1244, -1180, -1116, -1052, -988, -924,
            -876, -844, -812, -780, -748, -716, -684, -652,
            -620, -588, -556, -524, -492, -460, -428, -396,
            -372, -356, -340, -324, -308, -292, -276, -260,
            -244, -228, -212, -196, -180, -164, -148, -132,
            -120, -112, -104, -96, -88, -80, -72, -64,
            -56, -48, -40, -32, -24, -16, -8, 0,
            32124, 31100, 30076, 29052, 28028, 27004, 25980, 24956,
            23932, 22908, 21884, 20860, 19836, 18812, 17788, 16764,
            15996, 15484, 14972, 14460, 13948, 13436, 12924, 12412,
            11900, 11388, 10876, 10364, 9852, 9340, 8828, 8316,
            7932, 7676, 7420, 7164, 6908, 6652, 6396, 6140,
            5884, 5628, 5372, 5116, 4860, 4604, 4348, 4092,
            3900, 3772, 3644, 3516, 3388, 3260, 3132, 3004,
            2876, 2748, 2620, 2492, 2364, 2236, 2108, 1980,
            1884, 1820, 1756, 1692, 1628, 1564, 1500, 1436,
            1372, 1308, 1244, 1180, 1116, 1052, 988, 924,
            876, 844, 812, 780, 748, 716, 684, 652,
            620, 588, 556, 524, 492, 460, 428, 396,
            372, 356, 340, 324, 308, 292, 276, 260,
            244, 228, 212, 196, 180, 164, 148, 132,
            120, 112, 104, 96, 88, 80, 72, 64,
            56, 48, 40, 32, 24, 16, 8, 0,
        ],
        dtype=np.int16,
    )

    def _build_lin2ulaw_table() -> np.ndarray:
        """Build a 65536-entry lookup from 16-bit signed sample → ulaw byte."""
        table = np.zeros(65536, dtype=np.uint8)
        for i in range(65536):
            sample = np.int16(i)
            sign = 0x80 if sample < 0 else 0
            magnitude = min(abs(int(sample)), 32635)
            magnitude += 0x84
            exp = 7
            for e in range(7, -1, -1):
                if magnitude & (1 << (e + 7)):
                    exp = e
                    break
            mantissa = (magnitude >> (exp + 3)) & 0x0F
            ulaw_byte = ~(sign | (exp << 4) | mantissa) & 0xFF
            table[np.uint16(i)] = ulaw_byte
        return table

    _LIN2ULAW = _build_lin2ulaw_table()

    def ulaw2lin(data: bytes, width: int) -> bytes:
        samples = np.frombuffer(data, dtype=np.uint8)
        pcm = _ULAW_TO_LINEAR[samples]
        return pcm.tobytes()

    def lin2ulaw(data: bytes, width: int) -> bytes:
        pcm = np.frombuffer(data, dtype=np.int16)
        indices = pcm.view(np.uint16)
        ulaw = _LIN2ULAW[indices]
        return ulaw.tobytes()

    def ratecv(
        data: bytes, width: int, nchannels: int, inrate: int, outrate: int
    ) -> bytes:
        pcm = np.frombuffer(data, dtype=np.int16).astype(np.float64)
        out_len = int(len(pcm) * outrate / inrate)
        indices = np.linspace(0, len(pcm) - 1, out_len)
        resampled = np.interp(indices, np.arange(len(pcm)), pcm)
        return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


class TwilioAudioBridge:
    """Converts audio between Twilio mulaw 8kHz and pipeline PCM 16kHz."""

    def twilio_to_pcm(self, payload: str) -> bytes:
        """Decode a Twilio media payload to PCM 16-bit 16kHz mono.

        Args:
            payload: Base64-encoded mulaw 8kHz audio from Twilio.

        Returns:
            Raw PCM bytes (16-bit signed LE, 16kHz, mono).
        """
        mulaw_8k = base64.b64decode(payload)
        pcm_8k = ulaw2lin(mulaw_8k, 2)
        pcm_16k = ratecv(pcm_8k, 2, 1, 8000, 16000)
        return pcm_16k

    def pcm_to_twilio(self, pcm_16k: bytes, seq: int, stream_sid: str) -> dict:
        """Convert pipeline PCM to a Twilio media event.

        Args:
            pcm_16k: Raw PCM bytes (16-bit signed LE, 16kHz, mono).
            seq: Outbound media sequence number.
            stream_sid: Twilio stream SID for this session.

        Returns:
            Twilio media event dict ready to JSON-serialize and send.
        """
        pcm_8k = ratecv(pcm_16k, 2, 1, 16000, 8000)
        mulaw_8k = lin2ulaw(pcm_8k, 2)
        payload = base64.b64encode(mulaw_8k).decode("ascii")
        return {
            "event": "media",
            "streamSid": stream_sid,
            "media": {
                "payload": payload,
            },
            "sequenceNumber": str(seq),
        }

    @staticmethod
    def make_mark_event(stream_sid: str, mark_name: str) -> dict:
        """Create a Twilio mark event for playback tracking.

        Args:
            stream_sid: Twilio stream SID.
            mark_name: Identifier for this mark point.

        Returns:
            Twilio mark event dict.
        """
        return {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {
                "name": mark_name,
            },
        }
