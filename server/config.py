"""Configuration loaded from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()

# API Keys
DEEPGRAM_API_KEY: str = os.environ.get("DEEPGRAM_API_KEY", "")
CLAUDE_API_KEY: str = os.environ.get("CLAUDE_API_KEY", "")
CARTESIA_API_KEY: str = os.environ.get("CARTESIA_API_KEY", "")

# Cartesia voice — falls back to a known public demo voice
CARTESIA_VOICE_ID: str = os.environ.get(
    "CARTESIA_VOICE_ID", "a0e99841-438c-4a64-b679-ae501e7d6091"
)

# Audio settings
AUDIO_SAMPLE_RATE: int = int(os.environ.get("AUDIO_SAMPLE_RATE", "16000"))
AUDIO_ENCODING: str = os.environ.get("AUDIO_ENCODING", "pcm_s16le")

# Server settings
HOST: str = os.environ.get("HOST", "0.0.0.0")
PORT: int = int(os.environ.get("PORT", "8080"))

# Model IDs
CLAUDE_HAIKU_MODEL: str = "claude-haiku-4-5-20251001"
CLAUDE_SONNET_MODEL: str = "claude-sonnet-4-6"
CARTESIA_MODEL: str = "sonic-2"
