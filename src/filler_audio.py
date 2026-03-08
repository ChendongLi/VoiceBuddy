"""
Random filler audio playback for VoiceBuddy.

Picks a filler phrase, synthesizes it via TTS, and sends it to the Twilio stream
to fill silence during tool-call processing.
"""

from __future__ import annotations

import json
import logging
import random

from tts_client import TTSClient
from twilio_audio_bridge import TwilioAudioBridge

logger = logging.getLogger("voicebuddy.filler_audio")

FILLER_PHRASES = [
    "One moment please...",
    "Let me check that for you...",
    "Just a second...",
    "Sure, let me look that up...",
]


class FillerAudio:
    """Synthesizes and sends a random filler phrase over a Twilio media stream."""

    def __init__(self, tts_client: TTSClient) -> None:
        self._tts = tts_client
        self._bridge = TwilioAudioBridge()

    async def play_filler(self, websocket, stream_sid: str) -> None:
        """Pick a random filler phrase, synthesize via TTS, send to Twilio stream."""
        phrase = random.choice(FILLER_PHRASES)
        logger.info("Playing filler: %r", phrase)

        seq = 0
        async for audio_chunk in self._tts.synthesize(phrase):
            seq += 1
            media_evt = self._bridge.pcm_to_twilio(audio_chunk, seq, stream_sid)
            await websocket.send(json.dumps(media_evt))
