# VoiceBuddy

AI + phone call + assistant.

## Overview

VoiceBuddy is a real-time voice assistant system that combines:
- **Deepgram Flux** - Speech-to-text transcription
- **Claude (Anthropic)** - AI conversation handling
- **Cartesia Sonic 3** - Text-to-speech with voice cloning

## Audio Configuration

VoiceBuddy uses **16-bit PCM audio** at **16kHz** for optimal compatibility with Deepgram and minimal latency.

Audio settings in `.env`:
```bash
AUDIO_SAMPLE_RATE=16000
AUDIO_ENCODING=pcm_s16le
AUDIO_CONTAINER=wav
AUDIO_CHANNELS=1
```

## Audio Sources

VoiceBuddy supports two audio input modes:

### File Streaming
Read from WAV files with simulated real-time delays:
```python
from audio_sources import FileAudioSource

audio_source = FileAudioSource(
    file_path=Path("test/fixtures/test_audio.wav"),
    realtime_delay_ms=80
)
```

### Microphone Streaming
Capture from system microphone in real-time:
```python
from audio_sources import MicrophoneAudioSource

audio_source = MicrophoneAudioSource(
    sample_rate=16000,
    channels=1
)
```

See [doc/audio_sources.md](doc/audio_sources.md) for detailed documentation.

## Phase 1 API Validation

Test all API integrations:

```bash
# Test with file-based streaming (default)
python test/phase1_api_validation.py --mode=file

# Test with real-time microphone
python test/phase1_api_validation.py --mode=mic

# Test both modes
python test/phase1_api_validation.py --mode=both
```

## Test Audio Generation

Generate test audio files using Cartesia TTS:

```bash
python test/create_test_audio.py
```

This creates a 16-bit PCM WAV file at `test/fixtures/test_audio.wav`.

## Dependencies

VoiceBuddy uses Poetry for dependency management.

### Installation

```bash
# Install Poetry (if not already installed)
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install

# Or install without dev dependencies (production)
poetry install --only main
```

### Running Tests

```bash
# Test all API integrations (file mode)
poetry run python test/phase1_api_validation.py --mode=file

# Test with real-time microphone
poetry run python test/phase1_api_validation.py --mode=mic

# Test both modes
poetry run python test/phase1_api_validation.py --mode=both
```

### Platform-Specific Notes
- **macOS**: All dependencies work out of the box
- **Linux**: Install libportaudio2 first: `sudo apt-get install libportaudio2`
- **Windows**: Should work without additional setup
