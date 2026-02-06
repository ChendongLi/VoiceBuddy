# Audio Sources

The `audio_sources` module provides a clean abstraction for streaming audio from different sources.

## Overview

VoiceBuddy supports two types of audio sources:
1. **File-based streaming** - Read from WAV files with simulated real-time delays
2. **Microphone streaming** - Capture from system microphone in real-time

Both sources implement the same `AudioSource` interface, making it easy to swap between them.

## AudioSource Interface

```python
class AudioSource(ABC):
    """Abstract base class for audio sources."""

    async def get_chunks(self, chunk_size: int) -> AsyncIterator[bytes]:
        """Stream audio data in chunks."""
        pass

    def get_sample_rate(self) -> int:
        """Get the audio sample rate in Hz."""
        pass

    def get_channels(self) -> int:
        """Get the number of audio channels (1=mono, 2=stereo)."""
        pass

    def get_sample_width(self) -> int:
        """Get the sample width in bytes (2 for 16-bit, 4 for 32-bit)."""
        pass
```

## FileAudioSource

Reads audio from a WAV file and simulates real-time streaming with configurable delays.

### Usage

```python
from audio_sources import FileAudioSource

# Create file source with 80ms delay between chunks
audio_source = FileAudioSource(
    file_path=Path("assets/audio/fixtures/test_audio.wav"),
    realtime_delay_ms=80
)

# Stream chunks
chunk_size = 2560  # bytes
async for chunk in audio_source.get_chunks(chunk_size):
    # Process chunk
    await send_to_api(chunk)
```

### Parameters

- `file_path`: Path to WAV file (must be 16-bit PCM format)
- `realtime_delay_ms`: Delay between chunks in milliseconds (default: 80ms)

### Use Cases

- Testing streaming endpoints without requiring live microphone
- Reproducing specific audio inputs for debugging
- Automated testing with consistent audio data

## MicrophoneAudioSource

Captures audio from the system microphone in real-time using the `sounddevice` library.

### Usage

```python
from audio_sources import MicrophoneAudioSource

# Create microphone source
audio_source = MicrophoneAudioSource(
    sample_rate=16000,
    channels=1,
    dtype="int16"
)

# Stream chunks
chunk_size = 2560  # bytes
async for chunk in audio_source.get_chunks(chunk_size):
    # Process chunk
    await send_to_api(chunk)
```

### Parameters

- `sample_rate`: Sample rate in Hz (default: 16000)
- `channels`: Number of channels (default: 1 for mono)
- `dtype`: Data type for audio samples (default: "int16" for 16-bit PCM)
- `device`: Device ID to use, None for default device

### Requirements

Install the sounddevice library:
```bash
pip install sounddevice
```

Platform-specific notes:
- **macOS**: Works out of the box
- **Linux**: May need `sudo apt-get install libportaudio2`
- **Windows**: Should work without additional setup

### Permissions

On macOS, the first time you run microphone capture, you may need to grant microphone permissions in System Preferences → Security & Privacy → Microphone.

### Use Cases

- Real-time speech-to-text transcription
- Voice command detection
- Live testing of the full voice pipeline

## Audio Format

Both sources output **16-bit PCM audio** (signed little-endian) which is:
- Compatible with Python's `wave` module
- Native format for Deepgram's `linear16` encoding
- 50% smaller than 32-bit float format

Standard configuration:
- **Sample rate**: 16000 Hz (standard for speech)
- **Channels**: 1 (mono)
- **Sample width**: 2 bytes (16-bit)
- **Encoding**: pcm_s16le

## Chunk Size Calculation

For real-time streaming, calculate chunk size based on desired latency:

```python
# For 80ms chunks at 16kHz mono 16-bit:
chunk_size = int(
    (80 / 1000.0) *  # 80ms in seconds
    16000 *          # sample rate
    1 *              # channels
    2                # sample width in bytes
)
# Result: 2560 bytes
```

General formula:
```
chunk_size = (delay_ms / 1000.0) × sample_rate × channels × sample_width
```

## Example: Testing Both Sources

```python
from pathlib import Path
from audio_sources import FileAudioSource, MicrophoneAudioSource

async def test_streaming(audio_source: AudioSource):
    """Test streaming with any audio source."""
    chunk_size = 2560
    chunks_sent = 0

    async for chunk in audio_source.get_chunks(chunk_size):
        # Send to Deepgram, Claude, etc.
        await process_audio(chunk)
        chunks_sent += 1

    print(f"Sent {chunks_sent} chunks")

# Test with file
file_source = FileAudioSource(Path("test.wav"), realtime_delay_ms=80)
await test_streaming(file_source)

# Test with microphone
mic_source = MicrophoneAudioSource(sample_rate=16000, channels=1)
await test_streaming(mic_source)
```

## Error Handling

### FileAudioSource

```python
try:
    audio_source = FileAudioSource(file_path)
except FileNotFoundError:
    print(f"Audio file not found: {file_path}")
except wave.Error as e:
    print(f"Invalid WAV file format: {e}")
```

### MicrophoneAudioSource

```python
try:
    audio_source = MicrophoneAudioSource()
    async for chunk in audio_source.get_chunks(chunk_size):
        # Process chunk
        pass
except ImportError:
    print("sounddevice not installed")
    print("Install with: pip install sounddevice")
except Exception as e:
    print(f"Microphone error: {e}")
    # May indicate permission denied or no microphone available
```

## Integration with Phase 1 Tests

The phase1_api_validation script uses audio sources for Deepgram testing:

```bash
# Test with file
python test/phase1_api_validation.py --mode=file

# Test with microphone
python test/phase1_api_validation.py --mode=mic

# Test both
python test/phase1_api_validation.py --mode=both
```

See `test/phase1_api_validation.py` for full implementation examples.
