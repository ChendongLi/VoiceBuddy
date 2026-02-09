# VoiceBuddy Phases 4-6: STT + LLM + TTS Pipeline

## Context

Phases 1-3 are complete: API keys validated, state machine built, WebSocket echo server + browser AudioWorklet client working. The server currently echoes raw PCM back to the browser. Phases 4-6 replace the echo with the real AI pipeline: **Browser mic → Deepgram Flux (STT) → Claude Haiku+Sonnet (LLM) → Cartesia Sonic 3 (TTS) → Browser speaker**. These three phases are planned together because they share server architecture (event queue pattern) and the client evolves incrementally across all three.

---

## File Plan

### New files
| File | Phase | Purpose |
|------|-------|---------|
| `src/deepgram_client.py` | 4 | Deepgram Flux v2 WebSocket wrapper |
| `src/llm_orchestrator.py` | 5 | Dual-layer Claude (Haiku filler + Sonnet full) |
| `src/prompts.py` | 5 | System prompt constants (cached across turns) |
| `src/tts_client.py` | 6 | Cartesia Sonic 3 streaming WebSocket wrapper |
| `src/sentence_splitter.py` | 6 | Streaming sentence boundary detection |
| `test/test_sentence_splitter.py` | 6 | Unit tests for sentence splitter |

### Modified files
| File | Changes |
|------|---------|
| `src/server.py` | Replace echo with pipeline orchestration; event queue pattern |
| `src/static/index.html` | Add transcript display, conversation UI, TTS playback (reuse existing `playPcmChunk`) |

### Unchanged files
| File | Why |
|------|-----|
| `src/state_machine.py` | Transition table already covers all Phase 4-6 transitions |
| `src/latency_logger.py` | Already has all needed methods |
| `src/audio_config.py` | Used as-is by TTS client |
| `src/audio_sources.py` | Not used in server pipeline (browser provides audio) |

---

## Phase 4: Add STT (Deepgram Flux)

**Exit gate:** Speak a sentence → server prints transcript via Deepgram Flux within 350ms of end-of-speech (p50). EndOfTurn fires. Latency logger captures Stages 1-2.

### Step 4.1: Create `src/deepgram_client.py`

Wraps the Deepgram Flux v2 WebSocket. Reuses the exact SDK pattern from `test/test_deepgram_mic.py:94-143`.

```python
class DeepgramFluxClient:
    """Async Deepgram Flux v2 wrapper. One instance per browser session."""

    CHUNK_80MS = 2560  # 80ms at 16kHz, mono, 16-bit = 2560 bytes

    def __init__(self, on_start_of_turn, on_end_of_turn, on_transcript_update):
        # Callbacks are synchronous — they push events to an asyncio.Queue
        self._audio_buffer = bytearray()  # Accumulate 20ms chunks into 80ms frames

    async def connect(self):
        # client.listen.v2.connect(model="flux-general-en", encoding="linear16",
        #   sample_rate="16000", eot_threshold=0.7)
        # connection.on(EventType.MESSAGE, self._on_message)
        # asyncio.create_task(connection.start_listening())

    async def send_audio(self, chunk: bytes):
        # Buffer 20ms chunks from browser, forward to Deepgram when 80ms accumulated
        self._audio_buffer.extend(chunk)
        while len(self._audio_buffer) >= self.CHUNK_80MS:
            frame = bytes(self._audio_buffer[:self.CHUNK_80MS])
            del self._audio_buffer[:self.CHUNK_80MS]
            await connection._send(frame)

    async def flush_audio(self):
        # Send any remaining buffered audio (< 80ms) on disconnect/EOT

    async def close(self):
        # flush_audio(), cancel listen task, close connection
```

Key details:
- **80ms chunk buffering**: Browser sends 20ms (640-byte) frames. `send_audio()` accumulates 4 frames into 2560-byte (80ms) chunks before forwarding to Deepgram, matching the Flux spec from the build plan. `flush_audio()` sends any remaining partial buffer on disconnect.
- `eot_threshold=0.7`, `eager_eot_threshold` left unset by default (config toggle ready for Phase 7)
- **StartOfTurn from Flux event**: `_on_message` fires `on_start_of_turn` when `msg.event == "StartOfTurn"` — use the native Flux event, NOT transcript-based inference
- **EndOfTurn from Flux event**: `_on_message` fires `on_end_of_turn` when `msg.event == "EndOfTurn"`, carrying the final transcript
- **Stage-2 timestamp**: Record `transcript_received_ms = time.time() * 1000` inside the `on_end_of_turn` callback, distinct from the state machine's `user_stopped_speaking` marker. This gives us the explicit Stage 2 measurement.

### Step 4.2: Introduce event queue in `src/server.py`

The core architectural change: Deepgram SDK fires callbacks from its own thread/task. These callbacks must not directly `await` WebSocket sends. Solution: **event queue pattern**.

```python
async def handle_connection(websocket):
    log = LatencyLogger()
    sm = StateMachine(log)
    session_id = sm.ctx.session_id
    event_queue: asyncio.Queue = asyncio.Queue()

    # Deepgram callbacks push to queue (synchronous, safe from any thread)
    def on_start_of_turn(turn_index, ts_ms):
        event_queue.put_nowait(("start_of_turn", {...}))

    def on_end_of_turn(turn_index, transcript, confidence, ts_ms):
        event_queue.put_nowait(("end_of_turn", {"transcript": transcript, ...}))

    dg = DeepgramFluxClient(on_start_of_turn, on_end_of_turn, ...)
    await dg.connect()

    # Event processor (runs as background task)
    async def process_events():
        while True:
            event_type, data = await event_queue.get()
            try:
                if event_type == "start_of_turn":
                    sm.handle(Event.START_OF_TURN)
                elif event_type == "end_of_turn":
                    sm.handle(Event.END_OF_TURN, data=data)
                    # Log Stage 1: EOT detection latency
                    log.log_latency(session_id, sm.ctx.turn_id, "eot_detected",
                        sm.ctx.markers["user_stopped_speaking"] - sm.ctx.markers["user_started_speaking"])
                    # Log Stage 2: transcript received (explicit timestamp from DG callback)
                    log.log_latency(session_id, sm.ctx.turn_id, "transcript_received",
                        data["transcript_received_ms"] - sm.ctx.markers["user_stopped_speaking"])
                    await websocket.send(json.dumps({"type": "transcript", "text": data["transcript"]}))
            except Exception as e:
                # Don't crash the event loop on invalid transitions or unexpected events
                log.log_error(session_id, sm.ctx.turn_id, "event_processing_error",
                    f"{event_type}: {e}")
                logger.warning("[%s] Event error: %s — %s", session_id[:8], event_type, e)

    event_task = asyncio.create_task(process_events())

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                await dg.send_audio(message)  # Forward to Deepgram (no echo)
            else:
                # JSON ping/pong unchanged
    finally:
        # Graceful shutdown — signal workers, await cleanup
        event_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await event_task
        await dg.close()
```

### Step 4.3: Update `src/static/index.html`

Minimal changes:
- Add `<div id="transcript">` to display recognized speech
- In `ws.onmessage`: stop playing back binary frames (no more echo); handle `{"type": "transcript"}` JSON messages
- Keep the `playPcmChunk` function intact — Phase 6 will reuse it for TTS audio

### Step 4.4: Latency measurement

Stage 1 (EOT detection): `markers["user_stopped_speaking"] - markers["user_started_speaking"]` — how long between user starting and Flux detecting end-of-turn.
Stage 2 (transcript return): `data["transcript_received_ms"] - markers["user_stopped_speaking"]` — explicit timestamp captured in the Deepgram `on_end_of_turn` callback when the transcript string is available.

Both logged via `log.log_latency(...)` inside the event processor's try/except block.

---

## Phase 5: Add LLM (Dual-Layer Haiku + Sonnet)

**Exit gate:** Speak a question → Haiku filler within 600ms of end-of-speech, Sonnet full response within 1000ms total. Prompt caching confirmed on turn 2+.

### Step 5.1: Create `src/prompts.py`

Two constants — must never change between turns (for prompt caching):

- `SYSTEM_PROMPT`: CoolBreeze HVAC receptionist persona ("Allison"). Short sentences, conversational tone, company info, scheduling rules.
- `FILLER_SYSTEM_PROMPT`: Instructs Haiku to generate a brief 5-15 word acknowledgment.

### Step 5.2: Create `src/llm_orchestrator.py`

```python
class LLMOrchestrator:
    def __init__(self):
        self.client = AsyncAnthropic(api_key=os.environ.get("CLAUDE_API_KEY"))
        self.conversation_history: list[dict] = []
        # Callbacks (synchronous — push to event queue)
        self.on_filler_ready: Callable | None = None
        self.on_full_ready: Callable | None = None
        self.on_full_token: Callable | None = None  # For Phase 6 sentence streaming

    async def process_turn(self, transcript: str):
        self.conversation_history.append({"role": "user", "content": transcript})
        # Fire BOTH in parallel
        await asyncio.gather(
            self._run_haiku(transcript),
            self._run_sonnet(),
            return_exceptions=True,
        )

    async def _run_haiku(self, transcript):
        # client.messages.stream(model="claude-haiku-4-5-20251001",
        #   system=FILLER_SYSTEM_PROMPT, messages=[{"role": "user", "content": transcript}])
        # Measure TTFT, collect tokens, call on_filler_ready

    async def _run_sonnet(self):
        # Prompt caching: system=[{"type": "text", "text": SYSTEM_PROMPT,
        #   "cache_control": {"type": "ephemeral"}}]
        # client.messages.stream(model="claude-sonnet-4-5-20250929",
        #   system=system_blocks, messages=self.conversation_history)
        # Stream tokens → on_full_token callback (for Phase 6)
        # On complete → append to conversation_history, call on_full_ready
```

Key details:
- `.env` uses `CLAUDE_API_KEY` (not `ANTHROPIC_API_KEY`) — match existing pattern from `phase1_api_validation.py:361`
- Prompt caching: `cache_control: {"type": "ephemeral"}` on the system text block
- Verify caching works: check `response.usage.cache_read_input_tokens > 0` on turn 2
- Haiku gets only the current transcript (no history needed for filler)
- Sonnet gets the full `conversation_history` for multi-turn context

### Step 5.3: Wire LLM into `src/server.py`

In the event processor's `"end_of_turn"` handler:
```python
asyncio.create_task(llm.process_turn(data["transcript"]))
```

Add new event types to the processor:
- `"llm_filler_ready"` → `sm.handle(Event.LLM_FILLER_READY)` + send filler text to browser
- `"llm_full_ready"` → `sm.handle(Event.LLM_FULL_READY)` + send response text to browser
- Log `llm_filler_ttft` and `llm_full_ttft` latencies

### Step 5.4: Update `src/static/index.html`

- Add filler/response display areas
- Handle `{"type": "filler"}` and `{"type": "response"}` JSON messages
- Show conversation thread (user said X → bot said Y)

---

## Phase 6: Add TTS + Sentence Streaming

**Exit gate:** Speak a question → hear filler within 800ms, hear full response sentence-by-sentence. Total e2e (first audio byte) < 1000ms at p75. Cloned receptionist voice.

### Step 6.1: Create `src/sentence_splitter.py`

This is the most fragile component (per the build plan). Buffers streamed LLM tokens and emits complete sentences.

```python
class SentenceSplitter:
    def __init__(self, on_sentence: Callable[[str], None]):
        self._buffer = ""
        self._pending_short = ""  # Fragments < 15 words

    def feed(self, token: str):   # Called per LLM token
    def flush(self):              # Called when LLM stream ends
```

Rules:
1. Split only at `. ? !` followed by whitespace
2. Do NOT split on abbreviations: `Dr.`, `Mr.`, `Mrs.`, `U.S.`, `a.m.`, `p.m.`, `etc.`, `Inc.`, `St.`, `Ave.`
3. Buffer fragments under 15 words → prepend to next sentence
4. `flush()` emits whatever remains

### Step 6.2: Create `src/tts_client.py`

Wraps Cartesia's async WebSocket streaming API. Key difference from existing `test_cartesia.py`: uses `AsyncCartesia` with WebSocket (not sync HTTP).

```python
class TTSClient:
    # Pinned audio format — must match browser playPcmChunk expectations
    OUTPUT_FORMAT = {
        "container": "raw",       # No WAV header per chunk
        "sample_rate": 16000,     # 16kHz
        "encoding": "pcm_s16le", # 16-bit signed little-endian
    }

    def __init__(self):
        self.client = AsyncCartesia(api_key=os.environ.get("CARTESIA_API_KEY"))
        self.voice_id = os.environ.get("CARTESIA_VOICE_ID")
        self._ws = None

    async def connect(self):
        self._ws = self.client.tts.websocket()
        await self._ws.connect()

    async def synthesize(self, text, context_id=None) -> AsyncIterator[bytes]:
        # Uses OUTPUT_FORMAT (raw pcm_s16le 16kHz mono)
        # context_id maintains prosody continuity across sentences in a turn
        # Runtime assertion: first chunk must NOT start with b"RIFF" (WAV header guard)
        yield audio_chunk_bytes

    async def close(self):
        if self._ws: await self._ws.close()
        await self.client.close()
```

Critical format notes:
- `container="raw"` — the browser's `playPcmChunk` (index.html:175-197) expects raw Int16 PCM, not WAV
- `sample_rate=16000`, `encoding="pcm_s16le"` — must match browser's `TARGET_SAMPLE_RATE=16000` and Int16Array decoding
- Mono (1 channel) — Cartesia defaults to mono, but verify in first integration test
- ElevenLabs fallback deferred to Phase 7

### Step 6.3: Wire TTS pipeline into `src/server.py`

The full flow for a turn:
1. `"end_of_turn"` → `llm.process_turn(transcript)`
2. Haiku → `on_filler_ready` → push filler text directly to TTS queue
3. Sonnet → `on_full_token(token)` → `splitter.feed(token)` → `on_sentence(sentence)` → TTS queue
4. Sonnet done → `splitter.flush()` → last sentence to TTS queue
5. TTS worker: dequeue sentence → `tts.synthesize(sentence)` → `await websocket.send(audio_chunk)` as binary frames
6. After last audio chunk → `sm.handle(Event.TTS_PLAYBACK_DONE)`

```python
# TTS worker (background task)
tts_queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

async def tts_worker():
    while True:
        item = await tts_queue.get()
        if item is None:  # Shutdown sentinel
            break
        sentence, context_id = item
        first_byte = True
        try:
            async for audio_chunk in tts.synthesize(sentence, context_id):
                if first_byte:
                    sm.handle(Event.TTS_AUDIO_READY)
                    first_byte = False
                await websocket.send(audio_chunk)  # Binary frame → browser plays it
        except Exception as e:
            log.log_error(session_id, sm.ctx.turn_id, "tts_error", str(e))
            logger.warning("[%s] TTS error: %s", session_id[:8], e)
```

Server `finally` block (full cleanup after Phase 6):
```python
finally:
    # 1. Stop TTS worker gracefully
    tts_queue.put_nowait(None)  # Sentinel
    await asyncio.wait_for(tts_task, timeout=5.0)
    # 2. Stop event processor
    event_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await event_task
    # 3. Close service connections
    await dg.close()   # Flushes remaining audio buffer, cancels listen task
    await tts.close()  # Closes Cartesia WebSocket
```

### Step 6.4: Update `src/static/index.html`

- Restore binary frame handling in `ws.onmessage`: `playPcmChunk(new Int16Array(e.data))` — the existing function (lines 175-197) already handles this perfectly
- Binary frames are now TTS audio (not echo)
- Add `{"type": "playback_done"}` handler to update UI state
- Show conversation thread: "You: [transcript]" → "Allison: [response]"

### Step 6.5: Latency capture (all 5 stages)

| Stage | Start marker | End marker | Logger entry |
|-------|-------------|-----------|-------------|
| 1: EOT detection | `user_started_speaking` | `user_stopped_speaking` | `eot_detected` |
| 2: Transcript return | `user_stopped_speaking` | end_of_turn callback | `transcript_received` |
| 3: LLM TTFT | `user_stopped_speaking` | first LLM token | `llm_filler_ttft` / `llm_full_ttft` |
| 4: TTS first byte | `user_stopped_speaking` | first audio chunk | `tts_first_byte` |
| 5: Playback | first audio chunk | last chunk sent | `playback_done` |

Total e2e = `user_stopped_speaking` → `tts_first_byte` (target < 1000ms p75).

---

## Server Architecture After Phase 6

```
handle_connection(websocket)
├── Setup: log, sm, event_queue, tts_queue, dg, llm, tts, splitter
├── Connect: dg.connect(), tts.connect()
├── Background tasks:
│   ├── process_events() — reads event_queue, drives state machine + LLM
│   │   └── All sm.handle() wrapped in try/except (log + continue on error)
│   └── tts_worker() — reads tts_queue, synthesizes + sends audio
│       └── try/except per sentence (log + continue on TTS error)
├── Main loop: async for message in websocket
│   ├── Binary → dg.send_audio(chunk)    # Buffers 20ms→80ms, forwards to Deepgram
│   └── Text → handle ping/pong JSON
└── Finally (cleanup):
    ├── tts_queue.put_nowait(None) + await tts_task
    ├── event_task.cancel() + await event_task
    └── await dg.close() + await tts.close()
```

All callbacks (Deepgram, LLM) are synchronous and push to `event_queue`. All async work happens in the event processor or TTS worker. This avoids threading issues with the Deepgram SDK. Error handling ensures one bad event doesn't crash the session.

---

## Testing Strategy

### Unit tests (no API keys needed)
- `test/test_sentence_splitter.py` — required test cases:
  - Basic split: `"Hello world. How are you?"` → two sentences
  - Question/exclamation: `"How are you? I'm great!"` → two sentences
  - Abbreviations (no split): `"Dr. Smith is here."`, `"Mr. Jones called today."`
  - Time abbreviations: `"Come at 3 p.m. tomorrow."` → one sentence
  - Mixed abbreviations: `"U.S. economy is strong."` → one sentence
  - Short fragment buffering: fragment under 15 words prepended to next
  - Flush emits remainder
  - Streaming tokens: feed character-by-character, verify correct sentence output
  - Ellipsis: `"Wait... really?"` → handles gracefully
  - Quote boundaries: `"She said 'hello.' Then left."` → one or two sentences (not split at inner period)

### Integration tests (with API keys, manual)
- Run server → open browser → speak → verify transcript appears (Phase 4)
- Run server → speak → verify filler + response text appear (Phase 5)
- Run server → speak → hear filler + response audio (Phase 6)
- Check `logs/voicebuddy.jsonl` for all 5 latency stages

### Latency verification
- After Phase 6: run 10 end-to-end conversations, verify p75 < 1000ms from JSONL logs

---

## Verification

After each phase, verify by:

1. **Phase 4**: `poetry run python src/server.py` → open browser → speak → see transcript in browser + server console. Check JSONL for `eot_detected` entries < 350ms.

2. **Phase 5**: Same flow → see filler text appear quickly, then full response. Check JSONL for `llm_filler_ttft` < 600ms. On second turn, verify `cached: true` in logs.

3. **Phase 6**: Same flow → hear filler spoken aloud, then full response streamed sentence-by-sentence. Check JSONL for `tts_first_byte`. Total e2e < 1000ms p75. Run `pytest test/test_sentence_splitter.py`.
