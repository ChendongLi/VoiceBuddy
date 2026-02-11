# VoiceBuddy

AI + phone call + assistant.

Small service businesses — realtors, plumbers, electricians, HVAC companies, law firm secretaries, dental offices, reception desks — miss inbound calls every day. A missed call is a missed customer. Hiring a receptionist to cover every line is expensive and does not scale. Existing robotic phone trees frustrate callers and erode trust in the business.

VoiceBuddy is an AI-powered phone assistant that answers calls on behalf of these businesses. It is not a chatbot transplanted onto a phone line. It is purpose-built for phone: it listens, responds in under a second, handles interruptions naturally, and captures the information the business needs — all without the caller realising they are talking to a machine.

The core bet: if latency is low enough and turn-taking is natural enough, callers will not notice the difference in the first 30 seconds of a call. That window is all we need to qualify the need, collect information, and route or schedule.

## Architecture

```
Browser mic → WebSocket → Server
                            ├── Deepgram Flux v2 (STT)
                            │     ├── StartOfTurn → state machine
                            │     └── EndOfTurn + transcript
                            │           ↓
                            ├── Claude Haiku (filler) ──→ TTS queue
                            ├── Claude Sonnet (full)
                            │     └── tokens → SentenceSplitter → TTS queue
                            │           ↓
                            ├── Cartesia Sonic 3 (TTS)
                            │     └── PCM audio chunks → WebSocket → Browser speaker
                            │
                            ├── Silero VAD (barge-in detection)
                            │     └── VAD + StartOfTurn dual gate
                            │           → cancel_pipeline() → stop_playback
                            │
                            └── Silence policy (2s prompt, 5s goodbye)
```

All callbacks (Deepgram SDK, LLM streaming) push events to an `asyncio.Queue`. An event processor task drives the state machine and dispatches work. A separate TTS worker task consumes a sentence queue and streams audio to the browser. This avoids threading issues and keeps the WebSocket loop responsive.

### State Machine

```
IDLE → USER_SPEAKING → PROCESSING → BOT_SPEAKING → IDLE
                    ↘  FILLER_RESPONSE ↗
                         ↕
                    BARGE_IN_DETECTED → USER_SPEAKING (new turn)
```

All transitions are pre-defined and validated. Invalid transitions are logged and ignored, never crash the session.

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| STT | Deepgram Flux v2 | Real-time speech-to-text via WebSocket |
| LLM (filler) | Claude Haiku 4.5 | Fast acknowledgment (5-15 words, <600ms) |
| LLM (full) | Claude Sonnet 4.5 | Full response with prompt caching |
| TTS | Cartesia Sonic 3 | Voice synthesis via WebSocket, raw PCM streaming |
| VAD | Silero VAD v5 (ONNX) | Server-side voice activity detection |
| Transport | WebSocket (websockets 16.0) | Browser audio + JSON control messages |
| Frontend | AudioWorklet + Web Audio API | Mic capture + TTS playback |

### Audio Format

16kHz, 16-bit signed PCM, mono throughout the pipeline. Browser sends 20ms chunks (640 bytes), server buffers to 80ms (2560 bytes) for Deepgram. TTS returns raw PCM (no WAV headers).

## Latency Pipeline

| Stage | Metric | Target |
|-------|--------|--------|
| 1. EOT detection | User stop → Deepgram EndOfTurn | <350ms p50 |
| 2. Transcript | EndOfTurn → transcript available | ~50ms |
| 3. LLM TTFT | Transcript → first LLM token | <600ms (filler) |
| 4. TTS first byte | Sentence ready → first audio chunk | <200ms |
| 5. End-to-end | User stop → first audio in browser | <1000ms p75 |

All stages are logged to `logs/voicebuddy.jsonl` per session and turn.

## Barge-In

Users can interrupt the bot mid-sentence. Barge-in uses a dual-gate to avoid false positives:

1. **Silero VAD** confirms sustained speech (>100ms above 0.5 threshold)
2. **Deepgram StartOfTurn** confirms real words (not background noise)

Both signals must fire while bot is speaking. On barge-in, `cancel_pipeline()` atomically: cancels the LLM task, drains the TTS queue, cancels Cartesia synthesis, discards the sentence buffer, records partial response in conversation history, sends `stop_playback` to the browser, and resets VAD state.

## Silence Policy

After end-of-turn with no response activity:

| Delay | Action |
|-------|--------|
| 2s | "Are you still there?" (TTS prompt) |
| 5s | Goodbye message + disconnect |

Cancelled by any speech, filler response, or TTS activity.

## Setup

### Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- API keys: Deepgram, Anthropic (Claude), Cartesia

### Install

```bash
poetry install
```

### Configure

```bash
cp .env.example .env
# Edit .env with your API keys
# Audio defaults (16kHz PCM) are pre-filled
```

### Pre-commit

```bash
poetry run pre-commit install
```

Formatting: Black + isort, line length 120.

## Usage

### Start the server

```bash
poetry run python src/server.py
```

### Browser client

1. Open http://localhost:8765/ in Chrome
2. Click **Start** and allow microphone access
3. Speak — the bot responds with voice
4. Interrupt mid-sentence to test barge-in
5. Click **Stop** to end the session

## Project Structure

```
src/
├── server.py              # WebSocket server, event queue, pipeline orchestration
├── state_machine.py       # State transitions + latency markers
├── deepgram_client.py     # Deepgram Flux v2 STT wrapper
├── llm_orchestrator.py    # Dual-layer Claude (Haiku filler + Sonnet full)
├── prompts.py             # System prompts (cached across turns)
├── sentence_splitter.py   # Streaming sentence boundary detection
├── tts_client.py          # Cartesia Sonic 3 TTS wrapper
├── vad_detector.py        # Silero VAD v5 ONNX wrapper
├── latency_logger.py      # JSONL latency logging
├── audio_config.py        # Audio format constants
├── audio_sources.py       # File + microphone audio input
└── static/
    └── index.html         # Browser client (AudioWorklet + Web Audio)

test/
├── test_echo_server.py        # WebSocket echo + HTTP serving
├── test_sentence_splitter.py  # 21 sentence splitting tests
├── test_state_machine_sim.py  # 25 state transition tests (incl. barge-in)
├── test_vad_detector.py       # 6 VAD unit tests
├── test_barge_in.py           # 6 barge-in integration tests
└── ...                        # API validation + component tests
```

## Running Tests

```bash
# All tests
poetry run pytest -v

# Specific test files
poetry run pytest test/test_sentence_splitter.py -v
poetry run pytest test/test_state_machine_sim.py -v
poetry run pytest test/test_vad_detector.py -v
poetry run pytest test/test_barge_in.py -v
```

62 tests, all passing.

## Platform Notes

- **macOS**: All dependencies work out of the box
- **Linux**: Install libportaudio2 first: `sudo apt-get install libportaudio2`
- **Windows**: Should work without additional setup
