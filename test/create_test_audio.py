"""
Generate test audio fixture using Cartesia TTS

Creates a standard test audio file:
- Content: "Hello, I need to schedule an HVAC repair appointment for tomorrow afternoon."
- Format: 16kHz, mono, 16-bit PCM WAV
- Output: assets/audio/fixtures/test_audio.wav
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from cartesia import Cartesia
except ImportError:
    print("❌ Missing cartesia package")
    print("Please install: pip install cartesia")
    sys.exit(1)

from audio_config import config as audio_config


def generate_test_audio():
    """Generate test audio using Cartesia TTS."""
    # Load environment variables
    env_path = Path(__file__).parent.parent / ".env"
    env_vars = {}

    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                env_vars[key] = value.strip('"')

    api_key = env_vars.get("CARTESIA_API_KEY")
    voice_id = env_vars.get("CARTESIA_VOICE_ID")

    if not api_key or not voice_id:
        print("❌ Missing CARTESIA_API_KEY or CARTESIA_VOICE_ID in .env")
        sys.exit(1)

    print("Generating test audio fixture with Cartesia TTS...")
    print(f"Voice ID: {voice_id}")

    test_text = "Hello, I need to schedule an HVAC repair appointment for tomorrow afternoon."
    print(f'Text: "{test_text}"')

    try:
        client = Cartesia(api_key=api_key)

        # Generate audio (already in WAV format)
        chunk_iter = client.tts.bytes(
            model_id="sonic-3",
            transcript=test_text,
            voice={
                "mode": "id",
                "id": voice_id,
            },
            output_format=audio_config.get_output_format(),
        )

        # Collect all chunks
        audio_data = b""
        for chunk in chunk_iter:
            audio_data += chunk

        # Save as WAV file
        output_path = Path(__file__).parent.parent / "assets" / "audio" / "fixtures" / "test_audio.wav"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(str(output_path), "wb") as f:
            f.write(audio_data)

        # Get file size and duration estimate
        file_size = output_path.stat().st_size
        # Rough estimate: WAV header is 44 bytes, rest is audio data
        audio_bytes = file_size - 44
        num_samples = audio_bytes // (audio_config.channels * audio_config.sample_width)
        duration = num_samples / audio_config.sample_rate

        print(f"\n✅ Test audio fixture created successfully!")
        print(f"   Output: {output_path}")
        print(f"   Size: {file_size:,} bytes")
        print(f"   Duration: ~{duration:.1f} seconds")
        print(f"   Format: 16kHz, mono, 16-bit PCM WAV")

    except Exception as e:
        print(f"❌ Error generating test audio: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    generate_test_audio()
