"""
Synthetic driver for the Phase 2 state machine skeleton.
Uses fake events to validate transitions and logging.
"""

import json
import sys
from pathlib import Path

import pytest

# Make src importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from latency_logger import LatencyLogger
from state_machine import Event, State, StateContext, StateMachine


def make_sm(tmp_path: Path) -> StateMachine:
    logger = LatencyLogger(log_file=str(tmp_path / "state_machine_log.jsonl"))
    return StateMachine(logger)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_happy_path_with_filler(tmp_path: Path):
    sm = make_sm(tmp_path)
    for ev in [
        Event.START_OF_TURN,
        Event.END_OF_TURN,
        Event.LLM_FILLER_READY,
        Event.LLM_FULL_READY,
        Event.TTS_PLAYBACK_DONE,
    ]:
        sm.handle(ev)
    assert sm.current_state == State.IDLE
    assert sm.ctx.turn_id == 1


def test_happy_path_no_filler(tmp_path: Path):
    sm = make_sm(tmp_path)
    for ev in [
        Event.START_OF_TURN,
        Event.END_OF_TURN,
        Event.LLM_FULL_READY,
        Event.TTS_PLAYBACK_DONE,
    ]:
        sm.handle(ev)
    assert sm.current_state == State.IDLE
    assert sm.ctx.turn_id == 1


# ---------------------------------------------------------------------------
# Self-loops
# ---------------------------------------------------------------------------


def test_filler_playback_done_then_sonnet(tmp_path: Path):
    """G2: filler finishes playing, Sonnet not back yet — self-loop, then Sonnet arrives."""
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FILLER_READY)
    assert sm.current_state == State.FILLER_RESPONSE

    sm.handle(Event.TTS_PLAYBACK_DONE)  # self-loop
    assert sm.current_state == State.FILLER_RESPONSE

    sm.handle(Event.LLM_FULL_READY)
    assert sm.current_state == State.BOT_SPEAKING

    sm.handle(Event.TTS_PLAYBACK_DONE)
    assert sm.current_state == State.IDLE


def test_tts_audio_ready_self_loop(tmp_path: Path):
    """G1: TTS_AUDIO_READY while already in BOT_SPEAKING — self-loop."""
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FULL_READY)
    assert sm.current_state == State.BOT_SPEAKING

    sm.handle(Event.TTS_AUDIO_READY)
    assert sm.current_state == State.BOT_SPEAKING

    sm.handle(Event.TTS_PLAYBACK_DONE)
    assert sm.current_state == State.IDLE


def test_tts_audio_ready_during_processing_self_loop(tmp_path: Path):
    """TTS_AUDIO_READY in PROCESSING — self-loop (Sonnet streams fast, TTS fires before filler)."""
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    assert sm.current_state == State.PROCESSING

    # TTS fires while still processing — should not crash
    sm.handle(Event.TTS_AUDIO_READY)
    assert sm.current_state == State.PROCESSING

    # Normal flow continues
    sm.handle(Event.LLM_FULL_READY)
    assert sm.current_state == State.BOT_SPEAKING

    sm.handle(Event.TTS_PLAYBACK_DONE)
    assert sm.current_state == State.IDLE


# ---------------------------------------------------------------------------
# Barge-in scenarios
# ---------------------------------------------------------------------------


def test_barge_in_during_bot_speaking(tmp_path: Path):
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FULL_READY)
    sm.handle(Event.BARGE_IN_DETECTED)
    assert sm.current_state == State.BARGE_IN_DETECTED

    sm.handle(Event.START_OF_TURN)
    assert sm.current_state == State.USER_SPEAKING
    assert sm.ctx.turn_id == 1


def test_barge_in_during_processing(tmp_path: Path):
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    assert sm.current_state == State.PROCESSING

    sm.handle(Event.BARGE_IN_DETECTED)
    assert sm.current_state == State.BARGE_IN_DETECTED

    sm.handle(Event.START_OF_TURN)
    assert sm.current_state == State.USER_SPEAKING


def test_barge_in_during_filler(tmp_path: Path):
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FILLER_READY)
    assert sm.current_state == State.FILLER_RESPONSE

    sm.handle(Event.BARGE_IN_DETECTED)
    assert sm.current_state == State.BARGE_IN_DETECTED

    sm.handle(Event.START_OF_TURN)
    assert sm.current_state == State.USER_SPEAKING


# ---------------------------------------------------------------------------
# Barge-in with full recovery
# ---------------------------------------------------------------------------


def test_barge_in_full_cycle_with_recovery(tmp_path: Path):
    """BOT_SPEAKING -> BARGE_IN -> USER_SPEAKING -> full new turn completes."""
    sm = make_sm(tmp_path)
    # First turn
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FULL_READY)
    assert sm.current_state == State.BOT_SPEAKING

    # Barge-in
    sm.handle(Event.BARGE_IN_DETECTED)
    assert sm.current_state == State.BARGE_IN_DETECTED
    assert "barge_in_detected" in sm.ctx.markers

    # Start new utterance
    sm.handle(Event.START_OF_TURN)
    assert sm.current_state == State.USER_SPEAKING

    # Complete the new turn
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FULL_READY)
    sm.handle(Event.TTS_PLAYBACK_DONE)
    assert sm.current_state == State.IDLE
    assert sm.ctx.turn_id == 2


def test_barge_in_during_processing_with_recovery(tmp_path: Path):
    """PROCESSING -> BARGE_IN -> USER_SPEAKING -> full new turn completes."""
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    assert sm.current_state == State.PROCESSING

    # Barge-in during processing
    sm.handle(Event.BARGE_IN_DETECTED)
    assert sm.current_state == State.BARGE_IN_DETECTED

    sm.handle(Event.START_OF_TURN)
    assert sm.current_state == State.USER_SPEAKING

    # Complete the new turn
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FILLER_READY)
    sm.handle(Event.LLM_FULL_READY)
    sm.handle(Event.TTS_PLAYBACK_DONE)
    assert sm.current_state == State.IDLE
    assert sm.ctx.turn_id == 2


# ---------------------------------------------------------------------------
# Silence timeout
# ---------------------------------------------------------------------------


def test_silence_timeout_idle_noop(tmp_path: Path):
    sm = make_sm(tmp_path)
    assert sm.current_state == State.IDLE
    sm.handle(Event.SILENCE_TIMEOUT)
    assert sm.current_state == State.IDLE


def test_silence_timeout_from_user_speaking(tmp_path: Path):
    """G4: user started speaking then went silent."""
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    assert sm.current_state == State.USER_SPEAKING

    sm.handle(Event.SILENCE_TIMEOUT)
    assert sm.current_state == State.IDLE


def test_silence_timeout_from_barge_in(tmp_path: Path):
    """G3: false barge-in, no speech follows — escape to IDLE."""
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FULL_READY)
    sm.handle(Event.BARGE_IN_DETECTED)
    assert sm.current_state == State.BARGE_IN_DETECTED

    sm.handle(Event.SILENCE_TIMEOUT)
    assert sm.current_state == State.IDLE


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_reset_from_processing(tmp_path: Path):
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    assert sm.current_state == State.PROCESSING
    assert sm.ctx.turn_id == 1

    sm.handle(Event.RESET)
    assert sm.current_state == State.IDLE
    assert sm.ctx.turn_id == 0
    assert sm.ctx.markers == {}


def test_reset_from_bot_speaking(tmp_path: Path):
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FULL_READY)
    assert sm.current_state == State.BOT_SPEAKING

    sm.handle(Event.RESET)
    assert sm.current_state == State.IDLE
    assert sm.ctx.turn_id == 0
    assert sm.ctx.markers == {}


# ---------------------------------------------------------------------------
# Multi-turn
# ---------------------------------------------------------------------------


def test_multi_turn_sequence(tmp_path: Path):
    sm = make_sm(tmp_path)
    for _ in range(3):
        sm.handle(Event.START_OF_TURN)
        sm.handle(Event.END_OF_TURN)
        sm.handle(Event.LLM_FULL_READY)
        sm.handle(Event.TTS_PLAYBACK_DONE)
    assert sm.current_state == State.IDLE
    assert sm.ctx.turn_id == 3


# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------


def test_markers_recorded_correctly(tmp_path: Path):
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FILLER_READY)
    sm.handle(Event.LLM_FULL_READY)
    sm.handle(Event.TTS_AUDIO_READY)  # self-loop in BOT_SPEAKING
    sm.handle(Event.TTS_PLAYBACK_DONE)

    expected_keys = {
        "user_started_speaking",
        "user_stopped_speaking",
        "llm_filler_ready",
        "llm_full_ready",
        "tts_first_byte",
        "playback_done",
    }
    assert set(sm.ctx.markers.keys()) == expected_keys
    for key in expected_keys:
        assert isinstance(sm.ctx.markers[key], float)
        assert sm.ctx.markers[key] > 0


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


def test_callback_on_transition(tmp_path: Path):
    sm = make_sm(tmp_path)
    calls = []

    def on_start(ctx: StateContext, event: Event, data: dict) -> None:
        calls.append(("on_transition", ctx.turn_id, event.name))

    sm.on_transition[(State.IDLE, Event.START_OF_TURN)] = on_start
    sm.handle(Event.START_OF_TURN)

    assert len(calls) == 1
    assert calls[0] == ("on_transition", 0, "START_OF_TURN")


def test_callback_on_enter(tmp_path: Path):
    sm = make_sm(tmp_path)
    calls = []

    def on_enter_processing(ctx: StateContext, event: Event, data: dict) -> None:
        calls.append(("on_enter", ctx.turn_id))

    sm.on_enter[State.PROCESSING] = on_enter_processing
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)

    assert len(calls) == 1
    assert calls[0] == ("on_enter", 1)


def test_callback_on_enter_fires_on_reset(tmp_path: Path):
    sm = make_sm(tmp_path)
    calls = []

    def on_enter_idle(ctx: StateContext, event: Event, data: dict) -> None:
        calls.append("idle_entered")

    sm.on_enter[State.IDLE] = on_enter_idle
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.RESET)

    assert "idle_entered" in calls


# ---------------------------------------------------------------------------
# Invalid transition
# ---------------------------------------------------------------------------


def test_invalid_transition_raises(tmp_path: Path):
    sm = make_sm(tmp_path)
    with pytest.raises(ValueError):
        sm.handle(Event.LLM_FULL_READY)


# ---------------------------------------------------------------------------
# Previous state tracking
# ---------------------------------------------------------------------------


def test_previous_state_tracking(tmp_path: Path):
    sm = make_sm(tmp_path)
    assert sm.previous_state is None

    sm.handle(Event.START_OF_TURN)
    assert sm.previous_state == State.IDLE
    assert sm.current_state == State.USER_SPEAKING

    sm.handle(Event.END_OF_TURN)
    assert sm.previous_state == State.USER_SPEAKING
    assert sm.current_state == State.PROCESSING


# ---------------------------------------------------------------------------
# All transitions covered (meta-test)
# ---------------------------------------------------------------------------


def test_all_transitions_covered(tmp_path: Path):
    """Fire every transition in the table and verify none crashes."""
    sm = make_sm(tmp_path)
    for (from_state, event), to_state in sm.transitions.items():
        # Create a fresh SM for each transition
        test_sm = make_sm(tmp_path)
        test_sm.current_state = from_state
        result = test_sm.handle(event)
        assert (
            result == to_state
        ), f"Transition ({from_state.name}, {event.name}) expected {to_state.name}, got {result.name}"


# ---------------------------------------------------------------------------
# Self-loop annotation in logs
# ---------------------------------------------------------------------------


def test_self_loop_annotation_in_log(tmp_path: Path):
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)
    sm.handle(Event.LLM_FILLER_READY)
    sm.handle(Event.TTS_PLAYBACK_DONE)  # self-loop

    log_file = tmp_path / "state_machine_log.jsonl"
    lines = log_file.read_text().strip().split("\n")
    last_entry = json.loads(lines[-1])
    meta = last_entry["data"]["metadata"]
    assert meta.get("self_loop") is True


# ---------------------------------------------------------------------------
# Log output format
# ---------------------------------------------------------------------------


def test_log_output_format(tmp_path: Path):
    sm = make_sm(tmp_path)
    sm.handle(Event.START_OF_TURN)
    sm.handle(Event.END_OF_TURN)

    log_file = tmp_path / "state_machine_log.jsonl"
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) == 2

    for line in lines:
        entry = json.loads(line)
        assert "session_id" in entry
        assert "turn_id" in entry
        assert "timestamp_ms" in entry
        assert "event_type" in entry
        assert entry["event_type"] == "state"
        assert "data" in entry
        assert "from_state" in entry["data"]
        assert "to_state" in entry["data"]
        assert "metadata" in entry["data"]
        assert "event" in entry["data"]["metadata"]
