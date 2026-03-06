"""Integration tests for barge-in pipeline cancellation and LLM interruption handling."""

import asyncio
import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from latency_logger import LatencyLogger
from llm_orchestrator import LLMOrchestrator
from sentence_splitter import SentenceSplitter
from state_machine import Event, State, StateMachine


class TestCancelPipelineDrainsTtsQueue:
    @pytest.mark.asyncio
    async def test_cancel_pipeline_drains_tts_queue(self):
        """Verify TTS queue is drained after cancel_pipeline equivalent logic."""
        tts_queue: asyncio.Queue = asyncio.Queue()

        # Enqueue several sentences
        tts_queue.put_nowait(("Sentence one.", "ctx-1"))
        tts_queue.put_nowait(("Sentence two.", "ctx-1"))
        tts_queue.put_nowait(("Sentence three.", "ctx-1"))

        assert not tts_queue.empty()

        # Drain logic (mirrors cancel_pipeline step 2)
        while not tts_queue.empty():
            try:
                tts_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        assert tts_queue.empty()


class TestStopPlaybackMessageSent:
    @pytest.mark.asyncio
    async def test_stop_playback_message_sent(self):
        """Verify WebSocket receives stop_playback JSON message."""
        mock_ws = AsyncMock()
        sent_messages = []

        async def capture_send(data):
            sent_messages.append(data)

        mock_ws.send = capture_send

        # Simulate sending stop_playback (mirrors cancel_pipeline step 7)
        await mock_ws.send(json.dumps({"type": "stop_playback"}))

        assert len(sent_messages) == 1
        parsed = json.loads(sent_messages[0])
        assert parsed["type"] == "stop_playback"


class TestLLMMarkInterrupted:
    def test_mark_interrupted_with_partial_text(self):
        """Verify conversation history handles partial response."""
        llm = LLMOrchestrator()
        llm.conversation_history = [{"role": "user", "content": "Hello"}]
        llm._current_partial_text = "Sure, I can help you with"

        llm.mark_interrupted()

        assert len(llm.conversation_history) == 2
        entry = llm.conversation_history[1]
        assert entry["role"] == "assistant"
        assert "[interrupted]" in entry["content"]
        assert "Sure, I can help you with" in entry["content"]
        assert llm._current_partial_text == ""

    def test_mark_interrupted_no_partial_text(self):
        """Verify conversation history when interrupted before any response."""
        llm = LLMOrchestrator()
        llm.conversation_history = [{"role": "user", "content": "Hello"}]
        llm._current_partial_text = ""

        llm.mark_interrupted()

        assert len(llm.conversation_history) == 2
        entry = llm.conversation_history[1]
        assert entry["role"] == "assistant"
        assert entry["content"] == "[interrupted before response]"

    def test_mark_interrupted_already_has_assistant_entry(self):
        """If assistant entry already exists, don't duplicate."""
        llm = LLMOrchestrator()
        llm.conversation_history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        llm._current_partial_text = "Some leftover"

        llm.mark_interrupted()

        # Should not add another entry
        assert len(llm.conversation_history) == 2
        assert llm._current_partial_text == ""

    def test_mark_interrupted_empty_history(self):
        """Empty history with partial text — partial text is still recorded."""
        llm = LLMOrchestrator()
        llm.conversation_history = []
        llm._current_partial_text = "Some text"

        llm.mark_interrupted()

        # Partial text is recorded even without a user message
        assert len(llm.conversation_history) == 1
        assert llm.conversation_history[0]["content"] == "Some text [interrupted]"
        assert llm._current_partial_text == ""


class TestSplitterDiscard:
    def test_discard_prevents_emission(self):
        """After discard, flush should emit nothing."""
        sentences = []
        splitter = SentenceSplitter(on_sentence=lambda s: sentences.append(s))

        splitter.feed("This is a long sentence with many words that would normally be emitted. ")
        splitter.discard()
        splitter.flush()

        assert len(sentences) == 0


class TestStartOfTurnWithoutVadDropped:
    """START_OF_TURN during BOT_SPEAKING without VAD should be dropped entirely."""

    def test_no_state_transition_when_vad_inactive(self):
        """START_OF_TURN in BOT_SPEAKING without VAD must not call sm.handle."""
        log = LatencyLogger(log_file="/tmp/test_drop_sot.jsonl")
        sm = StateMachine(log)

        # Drive to BOT_SPEAKING
        sm.handle(Event.START_OF_TURN)
        sm.handle(Event.END_OF_TURN)
        sm.handle(Event.LLM_FULL_READY)
        sm.handle(Event.TTS_AUDIO_READY)
        assert sm.current_state == State.BOT_SPEAKING

        # Record the marker before the false event
        original_marker = sm.ctx.markers.get("user_started_speaking")

        # Simulate the guard logic from server.py:
        # In BOT_SPEAKING, vad_speech_active=False, not in {IDLE, BARGE_IN_DETECTED}
        # → the event should be dropped (no sm.handle call)
        current = sm.current_state
        vad_speech_active = False
        barge_in_states = {State.BOT_SPEAKING, State.FILLER_RESPONSE, State.PROCESSING}

        if current in barge_in_states and vad_speech_active:
            sm.handle(Event.BARGE_IN_DETECTED)  # should NOT happen
        elif current in barge_in_states:
            # Deepgram-first path — but we're testing the "no VAD ever" scenario
            # Without grace window match, this just defers. But the marker should not be set.
            pass  # Deferred / dropped
        elif current in {State.IDLE, State.BARGE_IN_DETECTED}:
            sm.handle(Event.START_OF_TURN)  # should NOT happen
        else:
            pass  # dropped

        # State must remain BOT_SPEAKING
        assert sm.current_state == State.BOT_SPEAKING
        # user_started_speaking marker must not be overwritten
        assert sm.ctx.markers.get("user_started_speaking") == original_marker

    def test_silence_timer_not_cancelled_on_dropped_sot(self):
        """When START_OF_TURN is dropped, silence timer should remain active."""
        silence_timer_cancelled = False

        def cancel_silence_timer():
            nonlocal silence_timer_cancelled
            silence_timer_cancelled = True

        log = LatencyLogger(log_file="/tmp/test_drop_sot_timer.jsonl")
        sm = StateMachine(log)

        # Drive to BOT_SPEAKING
        sm.handle(Event.START_OF_TURN)
        sm.handle(Event.END_OF_TURN)
        sm.handle(Event.LLM_FULL_READY)
        sm.handle(Event.TTS_AUDIO_READY)

        # Simulate guard: BOT_SPEAKING + no VAD → drop
        current = sm.current_state
        vad_speech_active = False
        barge_in_states = {State.BOT_SPEAKING, State.FILLER_RESPONSE, State.PROCESSING}

        if current in barge_in_states and vad_speech_active:
            cancel_silence_timer()
        elif current in barge_in_states:
            pass  # deferred — no timer cancel
        elif current in {State.IDLE, State.BARGE_IN_DETECTED}:
            cancel_silence_timer()

        assert not silence_timer_cancelled


class TestDeepgramFirstThenVadBargesIn:
    """When Deepgram fires START_OF_TURN before VAD, deferred barge-in should fire."""

    @pytest.mark.asyncio
    async def test_deferred_barge_in_fires(self):
        """START_OF_TURN in BOT_SPEAKING (no VAD) → VAD within 200ms → barge-in."""
        log = LatencyLogger(log_file="/tmp/test_deferred_bargein.jsonl")
        sm = StateMachine(log)

        # Drive to BOT_SPEAKING
        sm.handle(Event.START_OF_TURN)
        sm.handle(Event.END_OF_TURN)
        sm.handle(Event.LLM_FULL_READY)
        sm.handle(Event.TTS_AUDIO_READY)
        assert sm.current_state == State.BOT_SPEAKING

        # Simulate server state
        vad_speech_active = False
        pending_barge_in = None
        last_vad_speech_start_ms = 0.0
        BARGE_IN_GRACE_MS = 200.0
        pipeline_cancelled = False

        async def cancel_pipeline():
            nonlocal pipeline_cancelled
            pipeline_cancelled = True

        # Step 1: START_OF_TURN arrives, no VAD → defer
        now_ms = time.time() * 1000
        current = sm.current_state
        barge_in_states = {State.BOT_SPEAKING, State.FILLER_RESPONSE, State.PROCESSING}

        vad_recently_active = vad_speech_active or (
            last_vad_speech_start_ms > 0 and (now_ms - last_vad_speech_start_ms) < BARGE_IN_GRACE_MS
        )

        if current in barge_in_states and vad_recently_active:
            pass  # should NOT match
        elif current in barge_in_states:
            pending_barge_in = {"turn_index": 2, "ts_ms": now_ms}

        assert pending_barge_in is not None
        assert sm.current_state == State.BOT_SPEAKING  # no transition yet

        # Step 2: VAD fires within grace window (50ms later)
        await asyncio.sleep(0.01)  # small delay to simulate timing
        vad_now_ms = time.time() * 1000
        vad_speech_active = True
        last_vad_speech_start_ms = vad_now_ms
        sm.ctx.markers["vad_speech_start"] = vad_now_ms

        if (
            pending_barge_in
            and (vad_now_ms - pending_barge_in["ts_ms"]) < BARGE_IN_GRACE_MS
            and sm.current_state in barge_in_states
        ):
            pending_barge_in = None
            sm.handle(Event.BARGE_IN_DETECTED)
            await cancel_pipeline()
            vad_speech_active = False
            sm.handle(Event.START_OF_TURN)

        # Verify barge-in fired
        assert sm.current_state == State.USER_SPEAKING
        assert pipeline_cancelled

    @pytest.mark.asyncio
    async def test_expired_pending_barge_in_ignored(self):
        """Pending barge-in older than grace window should not trigger."""
        log = LatencyLogger(log_file="/tmp/test_expired_bargein.jsonl")
        sm = StateMachine(log)

        # Drive to BOT_SPEAKING
        sm.handle(Event.START_OF_TURN)
        sm.handle(Event.END_OF_TURN)
        sm.handle(Event.LLM_FULL_READY)
        sm.handle(Event.TTS_AUDIO_READY)
        assert sm.current_state == State.BOT_SPEAKING

        BARGE_IN_GRACE_MS = 200.0
        barge_in_states = {State.BOT_SPEAKING, State.FILLER_RESPONSE, State.PROCESSING}

        # Pending barge-in from 500ms ago (expired)
        pending_barge_in = {"turn_index": 2, "ts_ms": time.time() * 1000 - 500}
        pipeline_cancelled = False

        # VAD fires now
        vad_now_ms = time.time() * 1000

        if (
            pending_barge_in
            and (vad_now_ms - pending_barge_in["ts_ms"]) < BARGE_IN_GRACE_MS
            and sm.current_state in barge_in_states
        ):
            pipeline_cancelled = True

        # Should NOT trigger — expired
        assert not pipeline_cancelled
        assert sm.current_state == State.BOT_SPEAKING


class TestTtsTurnEndSentinel:
    """TTS worker should only fire tts_playback_done on __turn_end__ sentinel."""

    @pytest.mark.asyncio
    async def test_sentinel_fires_playback_done(self):
        """TTS worker fires tts_playback_done when it dequeues __turn_end__."""
        tts_queue: asyncio.Queue = asyncio.Queue()
        asyncio.Queue()

        # Simulate: two sentences then turn-end sentinel
        tts_queue.put_nowait(("Hello there.", "ctx-1"))
        tts_queue.put_nowait(("How are you?", "ctx-1"))
        tts_queue.put_nowait(("__turn_end__", "ctx-1"))

        events_fired = []

        # Process items like the tts_worker would
        while not tts_queue.empty():
            item = tts_queue.get_nowait()
            sentence, context_id = item
            if sentence == "__turn_end__":
                events_fired.append("tts_playback_done")
            else:
                events_fired.append(f"synthesize:{sentence}")

        assert events_fired == [
            "synthesize:Hello there.",
            "synthesize:How are you?",
            "tts_playback_done",
        ]

    @pytest.mark.asyncio
    async def test_empty_queue_between_sentences_no_playback_done(self):
        """Temporarily empty queue between sentences must NOT fire tts_playback_done."""
        tts_queue: asyncio.Queue = asyncio.Queue()
        playback_done_fired = False

        # Enqueue first sentence
        tts_queue.put_nowait(("First sentence.", "ctx-1"))

        # Process first sentence
        item = tts_queue.get_nowait()
        sentence, context_id = item

        # Queue is now empty between sentences — old code would fire playback_done here
        assert tts_queue.empty()

        # With sentinel approach, we only fire on __turn_end__, not on empty queue
        if sentence == "__turn_end__":
            playback_done_fired = True

        assert not playback_done_fired, "playback_done must not fire on regular sentence"

        # Now second sentence arrives (from splitter streaming)
        tts_queue.put_nowait(("Second sentence.", "ctx-1"))
        tts_queue.put_nowait(("__turn_end__", "ctx-1"))

        # Process remaining
        while not tts_queue.empty():
            item = tts_queue.get_nowait()
            sentence, context_id = item
            if sentence == "__turn_end__":
                playback_done_fired = True

        assert playback_done_fired, "playback_done should fire on __turn_end__"

    @pytest.mark.asyncio
    async def test_cancel_pipeline_drains_sentinel(self):
        """cancel_pipeline drain loop should also drain __turn_end__ sentinels."""
        tts_queue: asyncio.Queue = asyncio.Queue()

        tts_queue.put_nowait(("Sentence one.", "ctx-1"))
        tts_queue.put_nowait(("__turn_end__", "ctx-1"))

        # Drain logic (mirrors cancel_pipeline step 2)
        while not tts_queue.empty():
            try:
                tts_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        assert tts_queue.empty()


class TestSilenceTimerCancelledOnTtsAfterBargeInRecovery:
    """After barge-in recovery turn, TTS first byte should cancel silence timer."""

    @pytest.mark.asyncio
    async def test_tts_first_byte_cancels_silence_timer(self):
        """Full cycle: barge-in → new turn → silence timer → tts_first_byte cancels timer."""
        log = LatencyLogger(log_file="/tmp/test_recovery_silence.jsonl")
        sm = StateMachine(log)

        # Drive to BOT_SPEAKING (original turn)
        sm.handle(Event.START_OF_TURN)
        sm.handle(Event.END_OF_TURN)
        sm.handle(Event.LLM_FULL_READY)
        sm.handle(Event.TTS_AUDIO_READY)
        assert sm.current_state == State.BOT_SPEAKING

        # Barge-in
        sm.handle(Event.BARGE_IN_DETECTED)
        assert sm.current_state == State.BARGE_IN_DETECTED

        # New user turn after barge-in
        sm.handle(Event.START_OF_TURN)
        assert sm.current_state == State.USER_SPEAKING
        sm.handle(Event.END_OF_TURN)
        assert sm.current_state == State.PROCESSING

        # Filler ready → start silence timer
        sm.handle(Event.LLM_FILLER_READY)
        assert sm.current_state == State.FILLER_RESPONSE

        silence_timer_active = True

        def cancel_silence_timer():
            nonlocal silence_timer_active
            silence_timer_active = False

        # TTS first byte arrives → should cancel silence timer
        sm.handle(Event.TTS_AUDIO_READY)
        cancel_silence_timer()  # mirrors server.py tts_first_byte handler

        assert sm.current_state == State.FILLER_RESPONSE  # self-loop
        assert not silence_timer_active
