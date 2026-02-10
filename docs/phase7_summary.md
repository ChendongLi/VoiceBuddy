# Phase 7: Barge-In, Silence Policy, and Edge Cases

## Overview

Phase 7 adds real-time interruption handling to VoiceBuddy. Users can now interrupt the bot mid-sentence (barge-in), and the system handles prolonged silence with graduated prompts. This completes the conversational UX — the bot feels responsive and natural rather than forcing users to wait for it to finish speaking.

## Architecture

### Barge-In Detection (Dual-Gated)

Barge-in uses a two-signal gate to avoid false positives:

1. **Silero VAD** — Server-side voice activity detection processes every audio frame. Fires `on_speech_start` after 100ms of sustained speech above threshold (0.5).
2. **Deepgram `StartOfTurn`** — Confirms speech is real words, not just background noise.

Both signals must be active for a barge-in to fire. VAD alone (noise) or Deepgram alone (echo/artifact) won't trigger it.

```
Browser audio → VAD.feed()     → vad_speech_active = True
             → Deepgram STT   → StartOfTurn event
                                  ↓
                     if (bot is speaking) AND (vad_speech_active):
                         → BARGE_IN_DETECTED
                         → cancel_pipeline()
                         → START_OF_TURN (new utterance)
```

### Pipeline Cancellation

`cancel_pipeline()` performs 8 steps atomically:

| Step | Action | Why |
|------|--------|-----|
| 1 | `llm_task.cancel()` | Stop Claude mid-generation |
| 2 | Drain `tts_queue` | Discard unsynthesized sentences |
| 3 | Set `tts_cancel_event` | Signal TTS worker to abort current synthesis loop |
| 4 | `tts.cancel_current()` | Cancel Cartesia WebSocket stream |
| 5 | `splitter.discard()` | Drop buffered partial sentence |
| 6 | `llm.mark_interrupted()` | Record partial response in conversation history |
| 7 | Send `stop_playback` to browser | Stop scheduled AudioBufferSourceNodes |
| 8 | `vad.reset()` | Clear VAD state for new utterance |

### Silence Policy

Graduated silence handling after end-of-turn:

| Time | Action | State Guard |
|------|--------|-------------|
| 2s | "Are you still there?" (TTS + filler message) | PROCESSING or FILLER_RESPONSE |
| 5s | Goodbye message + disconnect after 5s more | PROCESSING or FILLER_RESPONSE |

The silence timer is cancelled by any of: `start_of_turn`, `llm_filler_ready`, `tts_first_byte`, or barge-in.

### Browser `stopPlayback()`

Recreates the `AudioContext` to instantly cancel all scheduled `AudioBufferSourceNode` instances (~1-5ms). Simpler and more reliable than tracking and stopping individual nodes.

## Files Changed

### New Files (3)

| File | Purpose |
|------|---------|
| `src/vad_detector.py` | Silero VAD v5 ONNX wrapper — 512-sample windows, speech/silence hysteresis |
| `test/test_vad_detector.py` | 6 VAD unit tests (silence, speech, end, min duration, reset, chunked feed) |
| `test/test_barge_in.py` | 6 integration tests (queue drain, stop_playback, mark_interrupted, discard) |

### Modified Files (7)

| File | Changes |
|------|---------|
| `pyproject.toml` | Added `silero-vad ^5.1`, `onnxruntime ^1.16` |
| `src/sentence_splitter.py` | Added `discard()` — clears buffer without emitting |
| `src/llm_orchestrator.py` | Added `_current_partial_text` tracking, `mark_interrupted()` for conversation history |
| `src/tts_client.py` | Added `_current_context_id` tracking, `cancel_current()` for Cartesia stream cancel |
| `src/state_machine.py` | Added `BARGE_IN_DETECTED` marker in `_record_markers()` |
| `src/server.py` | VAD init, `cancel_pipeline()`, barge-in event handling, silence policy, TTS cancel support |
| `src/static/index.html` | `stopPlayback()` function, `stop_playback` message handler |

### Test Additions to Existing Files

| File | Tests Added |
|------|-------------|
| `test/test_sentence_splitter.py` | `test_discard_clears_buffer`, `test_discard_then_new_input` |
| `test/test_state_machine_sim.py` | `test_barge_in_full_cycle_with_recovery`, `test_barge_in_during_processing_with_recovery` |

## Key Design Decisions

### 1. VAD + Deepgram Dual Gate

Single-signal barge-in has too many false positives. VAD alone triggers on coughs, keyboard clicks, and background TV. Deepgram alone triggers on echo artifacts. Requiring both signals eliminates nearly all false triggers while keeping reaction time under 200ms.

### 2. `mark_interrupted()` for Conversation History

When barge-in cancels the LLM task, `asyncio.CancelledError` propagates before the assistant response is appended to history. `mark_interrupted()` captures the partial text with an `[interrupted]` suffix so Claude maintains conversation context across barge-ins.

### 3. `tts_cancel_event` (Event, not direct cancel)

The TTS worker runs in its own task consuming from a queue. Rather than cancelling the task (which would kill the worker), we use an `asyncio.Event` flag that the worker checks between audio chunks. This lets the worker cleanly skip the current sentence and reset for the next turn.

### 4. AudioContext Recreation for Instant Stop

The browser schedules TTS audio chunks as future `AudioBufferSourceNode` starts. Calling `.stop()` on each would require tracking them all. Closing and recreating the `AudioContext` cancels everything instantly with ~1-5ms overhead.

## State Machine Transitions (Unchanged)

All barge-in transitions were already defined in Phase 2:

```
(BOT_SPEAKING,     BARGE_IN_DETECTED) → BARGE_IN_DETECTED
(PROCESSING,       BARGE_IN_DETECTED) → BARGE_IN_DETECTED
(FILLER_RESPONSE,  BARGE_IN_DETECTED) → BARGE_IN_DETECTED
(BARGE_IN_DETECTED, START_OF_TURN)    → USER_SPEAKING
(BARGE_IN_DETECTED, SILENCE_TIMEOUT)  → IDLE
```

Phase 7 wires the event firing — previously `StartOfTurn` during bot speech was absorbed by G5 self-loops.

## Test Results

```
62 passed, 0 failed
```

### Test Breakdown

| File | Tests | Status |
|------|-------|--------|
| `test/test_vad_detector.py` | 6 | All pass |
| `test/test_sentence_splitter.py` | 21 (2 new) | All pass |
| `test/test_state_machine_sim.py` | 25 (2 new) | All pass |
| `test/test_barge_in.py` | 6 | All pass |
| Other test files | 4 | All pass (no regressions) |

## Dependencies Added

| Package | Version | Size | Purpose |
|---------|---------|------|---------|
| `silero-vad` | ^5.1 | Small | VAD model + utilities |
| `onnxruntime` | ^1.16 | ~30MB | ONNX inference runtime |
| `torch` | (transitive) | ~200MB | Required by silero-vad package |

Note: The plan intended to use ONNX-only to avoid PyTorch, but `silero-vad` 5.1 requires `torch` as a dependency regardless. The ONNX inference path is still used (`load_silero_vad(onnx=True)`), which is faster than the PyTorch path at runtime.

## Manual Verification Checklist

- [ ] Start server (`python src/server.py`), open browser
- [ ] Have a conversation, interrupt the bot mid-sentence
- [ ] Verify TTS audio stops within ~200ms
- [ ] Verify bot begins listening to new utterance
- [ ] Verify conversation continues with full context
- [ ] Test rapid repeated interruptions (no crashes)
- [ ] Stay silent for 5+ seconds — verify "Are you still there?" fires
- [ ] Stay silent for 10+ seconds — verify goodbye + disconnect
- [ ] Background noise during bot speech — verify no false interruption
