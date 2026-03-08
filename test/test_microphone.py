"""
Simple test to verify microphone audio source works.
Requires real microphone hardware — run with: pytest -m integration
"""

import asyncio
import sys
from pathlib import Path

import pytest

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from audio_sources import MicrophoneAudioSource


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mic():
    """Test microphone capture."""
    print("Testing microphone capture...")
    print("Speak into your microphone for 3 seconds...")

    try:
        # Create audio source
        audio_source = MicrophoneAudioSource(sample_rate=16000, channels=1)

        # Calculate chunk size for 80ms chunks
        chunk_size = int((80 / 1000.0) * 16000 * 1 * 2)  # 80ms at 16kHz mono 16-bit

        chunks_received = 0
        total_bytes = 0

        # Capture for 3 seconds
        start_time = asyncio.get_event_loop().time()
        timeout = 3.0

        async for chunk in audio_source.get_chunks(chunk_size):
            chunks_received += 1
            total_bytes += len(chunk)

            # Check timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout:
                break

        print("\n✅ Microphone test successful!")
        print(f"   Chunks received: {chunks_received}")
        print(f"   Total bytes: {total_bytes:,}")
        print(f"   Duration: {timeout}s")
        print(f"   Average chunk size: {total_bytes / chunks_received:.0f} bytes")

    except ImportError as e:
        print(f"❌ Error: {e}")
        print("Install sounddevice: pip install sounddevice")
        return False
    except Exception as e:
        print(f"❌ Microphone test failed: {e}")
        import traceback

        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    success = asyncio.run(test_mic())
    sys.exit(0 if success else 1)
