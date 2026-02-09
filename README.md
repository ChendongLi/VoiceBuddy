# VoiceBuddy

AI + phone call + assistant.

Small service businesses — realtors, plumbers, electricians, HVAC companies, law firm secretaries, dental offices, reception desks — miss inbound calls every day. A missed call is a missed customer. Hiring a receptionist to cover every line is expensive and does not scale. Existing robotic phone trees frustrate callers and erode trust in the business.

VoiceBuddy is an AI-powered phone assistant that answers calls on behalf of these businesses. It is not a chatbot transplanted onto a phone line. It is purpose-built for phone: it listens, responds in under a second, handles interruptions naturally, and captures the information the business needs — all without the caller realising they are talking to a machine.

The core bet: if latency is low enough and turn-taking is natural enough, callers will not notice the difference in the first 30 seconds of a call. That window is all we need to qualify the need, collect information, and route or schedule.

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
    file_path=Path("assets/audio/fixtures/test_audio.wav"),
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

## Phase 3 Audio Echo Test

Test the browser-to-server audio transport layer before integrating AI services.

### Start the echo server
```bash
poetry run python src/server.py
```

### Browser test
1. Open http://localhost:8765/ in Chrome
2. Click **Start** and allow microphone access
3. Speak — you should hear your voice echoed back
4. RTT display should show < 200ms average
5. Click **Stop** to end the session

### Run automated tests
```bash
poetry run pytest test/test_echo_server.py -v
```

Tests verify: echo round-trip (bytes identical, RTT < 200ms), ping/pong latency (< 100ms), 100-chunk ordering, interleaved binary/text frames, HTTP serving, and JSONL log output with IDLE → USER_SPEAKING state transition.

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

This creates a 16-bit PCM WAV file at `assets/audio/fixtures/test_audio.wav`.

## Dependencies

VoiceBuddy uses Poetry for dependency management (no `requirements.txt`; use `poetry install`).

Environment setup:
```bash
cp .env.example .env
# then edit .env with your real API keys; audio defaults are pre-filled
```

Pre-commit (format/lint):
```bash
poetry run pre-commit install
poetry run pre-commit run --all-files
```
Formatting standards: Black + isort, line length 120.

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
