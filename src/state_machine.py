"""
State machine skeleton for VoiceBuddy Phase 2.

Pure event-driven; no real audio or network dependencies. Transitions are logged
through LatencyLogger for later latency analysis.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto

from latency_logger import LatencyLogger


class State(Enum):
    IDLE = auto()
    USER_SPEAKING = auto()
    PROCESSING = auto()
    FILLER_RESPONSE = auto()
    BOT_SPEAKING = auto()
    BARGE_IN_DETECTED = auto()


class Event(Enum):
    START_OF_TURN = auto()
    END_OF_TURN = auto()
    LLM_FILLER_READY = auto()
    LLM_FULL_READY = auto()
    TTS_AUDIO_READY = auto()
    TTS_PLAYBACK_DONE = auto()
    BARGE_IN_DETECTED = auto()
    SILENCE_TIMEOUT = auto()
    RESET = auto()


@dataclass
class StateContext:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turn_id: int = 0
    # Optional timestamps for later latency calculations
    markers: dict[str, float] = field(default_factory=dict)


class StateMachine:
    """
    Deterministic state machine for the voice call lifecycle.
    """

    def __init__(self, logger: LatencyLogger, session_id: str | None = None) -> None:
        self.logger = logger
        self.ctx = StateContext(session_id=session_id or str(uuid.uuid4()))
        self.current_state = State.IDLE
        self.previous_state: State | None = None

        # Callback hooks for transition side-effects (Phases 4-7)
        self.on_transition: dict[tuple[State, Event], Callable] = {}
        self.on_enter: dict[State, Callable] = {}

        # Transition table: (from_state, event) -> to_state
        self.transitions: dict[tuple[State, Event], State] = {
            (State.IDLE, Event.START_OF_TURN): State.USER_SPEAKING,
            (State.USER_SPEAKING, Event.END_OF_TURN): State.PROCESSING,
            (State.PROCESSING, Event.LLM_FILLER_READY): State.FILLER_RESPONSE,
            (State.PROCESSING, Event.LLM_FULL_READY): State.BOT_SPEAKING,
            (State.FILLER_RESPONSE, Event.LLM_FULL_READY): State.BOT_SPEAKING,
            (State.BOT_SPEAKING, Event.TTS_PLAYBACK_DONE): State.IDLE,
            (State.BOT_SPEAKING, Event.BARGE_IN_DETECTED): State.BARGE_IN_DETECTED,
            (State.BARGE_IN_DETECTED, Event.START_OF_TURN): State.USER_SPEAKING,
            (State.PROCESSING, Event.BARGE_IN_DETECTED): State.BARGE_IN_DETECTED,
            (State.FILLER_RESPONSE, Event.BARGE_IN_DETECTED): State.BARGE_IN_DETECTED,
            # G2: filler playback finishes but Sonnet not back yet — stay in FILLER_RESPONSE
            (State.FILLER_RESPONSE, Event.TTS_PLAYBACK_DONE): State.FILLER_RESPONSE,
            # G1: TTS_AUDIO_READY while already speaking/processing — self-loop for marker recording
            (State.PROCESSING, Event.TTS_AUDIO_READY): State.PROCESSING,
            (State.BOT_SPEAKING, Event.TTS_AUDIO_READY): State.BOT_SPEAKING,
            (State.FILLER_RESPONSE, Event.TTS_AUDIO_READY): State.FILLER_RESPONSE,
            # G3: false barge-in with no follow-up speech — escape to IDLE
            (State.BARGE_IN_DETECTED, Event.SILENCE_TIMEOUT): State.IDLE,
            # G4: user started speaking then went silent
            (State.USER_SPEAKING, Event.SILENCE_TIMEOUT): State.IDLE,
            # G5: Deepgram false speech events during non-listening states — absorb silently
            (State.PROCESSING, Event.START_OF_TURN): State.PROCESSING,
            (State.PROCESSING, Event.END_OF_TURN): State.PROCESSING,
            (State.BOT_SPEAKING, Event.START_OF_TURN): State.BOT_SPEAKING,
            (State.BOT_SPEAKING, Event.END_OF_TURN): State.BOT_SPEAKING,
            (State.FILLER_RESPONSE, Event.START_OF_TURN): State.FILLER_RESPONSE,
            (State.FILLER_RESPONSE, Event.END_OF_TURN): State.FILLER_RESPONSE,
        }

    def handle(self, event: Event, data: dict | None = None) -> State:
        """
        Process an incoming event and advance state if valid.

        Raises:
            ValueError: if the transition is invalid.
        """
        data = data or {}

        # Global reset to IDLE
        if event == Event.RESET:
            self.previous_state = self.current_state
            self._log_transition(self.current_state, State.IDLE, event, data)
            self.current_state = State.IDLE
            self.ctx.turn_id = 0
            self.ctx.markers.clear()
            self._fire_callbacks(event, data)
            return self.current_state

        # No-op for silence while idle
        if self.current_state == State.IDLE and event == Event.SILENCE_TIMEOUT:
            return self.current_state

        key = (self.current_state, event)
        if key not in self.transitions:
            raise ValueError(f"Invalid transition: {self.current_state.name} + {event.name}")

        next_state = self.transitions[key]

        # Update turn_id when entering PROCESSING (new user turn)
        if next_state == State.PROCESSING:
            self.ctx.turn_id += 1

        self._record_markers(event)
        self._log_transition(self.current_state, next_state, event, data)

        self.previous_state = self.current_state
        self.current_state = next_state
        self._fire_callbacks(event, data)
        return self.current_state

    # Helpers -----------------------------------------------------------------

    def _fire_callbacks(self, event: Event, data: dict) -> None:
        key = (self.previous_state, event) if self.previous_state is not None else None
        if key and key in self.on_transition:
            self.on_transition[key](self.ctx, event, data)
        if self.current_state in self.on_enter:
            self.on_enter[self.current_state](self.ctx, event, data)

    def _record_markers(self, event: Event) -> None:
        """
        Record latency markers based on event type.
        """
        now_ms = self.logger.get_timestamp_ms()

        if event == Event.START_OF_TURN:
            self.ctx.markers["user_started_speaking"] = now_ms
        elif event == Event.END_OF_TURN:
            self.ctx.markers["user_stopped_speaking"] = now_ms
        elif event == Event.LLM_FILLER_READY:
            self.ctx.markers["llm_filler_ready"] = now_ms
        elif event == Event.LLM_FULL_READY:
            self.ctx.markers["llm_full_ready"] = now_ms
        elif event == Event.TTS_AUDIO_READY:
            self.ctx.markers["tts_first_byte"] = now_ms
        elif event == Event.TTS_PLAYBACK_DONE:
            self.ctx.markers["playback_done"] = now_ms
        elif event == Event.BARGE_IN_DETECTED:
            self.ctx.markers["barge_in_detected"] = now_ms

    def _log_transition(self, from_state: State, to_state: State, event: Event, data: dict) -> None:
        meta = {"event": event.name}
        if data:
            meta["data"] = data
        if from_state == to_state:
            meta["self_loop"] = True

        self.logger.log_state_transition(
            session_id=self.ctx.session_id,
            turn_id=self.ctx.turn_id,
            from_state=from_state.name,
            to_state=to_state.name,
            metadata=meta,
        )
