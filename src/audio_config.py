"""
Audio Configuration for VoiceBuddy

Centralized audio settings for Cartesia TTS output.
All settings can be overridden via environment variables.
"""

import os
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

AudioEncoding = Literal["pcm_f32le", "pcm_s16le", "pcm_mulaw"]
AudioContainer = Literal["raw", "wav"]


@dataclass
class AudioConfig:
    """
    Audio output configuration for TTS generation.

    Attributes:
        sample_rate: Audio sample rate in Hz (16000, 22050, 44100, 48000)
        encoding: PCM encoding format
        container: Output container format
        channels: Number of audio channels (1=mono, 2=stereo)
        sample_width: Bytes per sample (2 for int16, 4 for float32)
    """

    sample_rate: int
    encoding: AudioEncoding
    container: AudioContainer
    channels: int
    sample_width: int

    @classmethod
    def from_env(cls) -> "AudioConfig":
        """Load configuration from environment variables with defaults."""
        sample_rate = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
        encoding = os.getenv("AUDIO_ENCODING", "pcm_s16le")
        container = os.getenv("AUDIO_CONTAINER", "wav")
        channels = int(os.getenv("AUDIO_CHANNELS", "1"))

        # Determine sample width from encoding
        sample_width = 4 if "f32" in encoding else 2

        return cls(
            sample_rate=sample_rate,
            encoding=encoding,
            container=container,
            channels=channels,
            sample_width=sample_width,
        )

    def get_output_format(self) -> dict:
        """Get Cartesia API output_format dict."""
        return {
            "container": self.container,
            "sample_rate": self.sample_rate,
            "encoding": self.encoding,
        }


# Global config instance
config = AudioConfig.from_env()
