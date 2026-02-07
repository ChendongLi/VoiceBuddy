"""
Synthetic driver to exercise the state machine and write transitions to a log.

Prints every transition to stdout and exercises ~10 scenarios covering all 14
transitions, RESET, and the SILENCE_TIMEOUT no-op.

Usage:
    poetry run python test/drive_state_machine.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from latency_logger import LatencyLogger  # noqa: E402
from state_machine import Event, State, StateMachine  # noqa: E402


def fire(sm: StateMachine, event: Event, scenario: str, step_log: list) -> None:
    prev = sm.current_state
    sm.handle(event)
    cur = sm.current_state
    label = f"  {prev.name} --{event.name}--> {cur.name}"
    print(label)
    step_log.append((prev, event, cur))


def run(log_path: Path = Path("logs/state_machine_demo.jsonl")) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = LatencyLogger(str(log_path))

    scenarios_passed = 0
    total_transitions = 0
    all_fired: set = set()

    def run_scenario(name: str, events: list[Event], expected_final: State) -> None:
        nonlocal scenarios_passed, total_transitions
        sm = StateMachine(logger)
        step_log: list = []
        print(f"\n--- Scenario: {name} ---")
        for ev in events:
            fire(sm, ev, name, step_log)
        for prev, ev, cur in step_log:
            all_fired.add((prev, ev, cur))
        total_transitions += len(step_log)
        assert sm.current_state == expected_final, f"FAIL: expected {expected_final.name}, got {sm.current_state.name}"
        print(f"  => Final: {sm.current_state.name}  [OK]")
        scenarios_passed += 1

    # 1. Happy path with filler
    run_scenario(
        "Happy path with filler",
        [
            Event.START_OF_TURN,
            Event.END_OF_TURN,
            Event.LLM_FILLER_READY,
            Event.LLM_FULL_READY,
            Event.TTS_PLAYBACK_DONE,
        ],
        State.IDLE,
    )

    # 2. Fast LLM (no filler)
    run_scenario(
        "Fast LLM (no filler)",
        [
            Event.START_OF_TURN,
            Event.END_OF_TURN,
            Event.LLM_FULL_READY,
            Event.TTS_PLAYBACK_DONE,
        ],
        State.IDLE,
    )

    # 3. Barge-in during BOT_SPEAKING
    run_scenario(
        "Barge-in during BOT_SPEAKING",
        [
            Event.START_OF_TURN,
            Event.END_OF_TURN,
            Event.LLM_FULL_READY,
            Event.BARGE_IN_DETECTED,
            Event.START_OF_TURN,
            Event.END_OF_TURN,
            Event.LLM_FULL_READY,
            Event.TTS_PLAYBACK_DONE,
        ],
        State.IDLE,
    )

    # 4. Barge-in during PROCESSING
    run_scenario(
        "Barge-in during PROCESSING",
        [
            Event.START_OF_TURN,
            Event.END_OF_TURN,
            Event.BARGE_IN_DETECTED,
            Event.START_OF_TURN,
            Event.END_OF_TURN,
            Event.LLM_FULL_READY,
            Event.TTS_PLAYBACK_DONE,
        ],
        State.IDLE,
    )

    # 5. Barge-in during FILLER_RESPONSE
    run_scenario(
        "Barge-in during FILLER_RESPONSE",
        [
            Event.START_OF_TURN,
            Event.END_OF_TURN,
            Event.LLM_FILLER_READY,
            Event.BARGE_IN_DETECTED,
            Event.START_OF_TURN,
            Event.END_OF_TURN,
            Event.LLM_FULL_READY,
            Event.TTS_PLAYBACK_DONE,
        ],
        State.IDLE,
    )

    # 6. RESET from mid-flow
    print("\n--- Scenario: RESET from mid-flow ---")
    sm = StateMachine(logger)
    step_log: list = []
    fire(sm, Event.START_OF_TURN, "RESET from mid-flow", step_log)
    fire(sm, Event.END_OF_TURN, "RESET from mid-flow", step_log)
    fire(sm, Event.LLM_FILLER_READY, "RESET from mid-flow", step_log)
    fire(sm, Event.RESET, "RESET from mid-flow", step_log)
    for prev, ev, cur in step_log:
        all_fired.add((prev, ev, cur))
    total_transitions += len(step_log)
    assert sm.current_state == State.IDLE
    assert sm.ctx.turn_id == 0
    print(f"  => Final: {sm.current_state.name}, turn_id={sm.ctx.turn_id}  [OK]")
    scenarios_passed += 1

    # 7. SILENCE_TIMEOUT scenarios (IDLE no-op, USER_SPEAKING->IDLE, BARGE_IN->IDLE)
    print("\n--- Scenario: SILENCE_TIMEOUT (3 sub-cases) ---")
    sm = StateMachine(logger)
    step_log = []

    # 7a. IDLE no-op
    prev = sm.current_state
    sm.handle(Event.SILENCE_TIMEOUT)
    print(f"  {prev.name} --SILENCE_TIMEOUT--> {sm.current_state.name} (no-op)")
    assert sm.current_state == State.IDLE

    # 7b. USER_SPEAKING -> IDLE
    fire(sm, Event.START_OF_TURN, "SILENCE_TIMEOUT", step_log)
    fire(sm, Event.SILENCE_TIMEOUT, "SILENCE_TIMEOUT", step_log)
    assert sm.current_state == State.IDLE

    # 7c. BARGE_IN_DETECTED -> IDLE
    fire(sm, Event.START_OF_TURN, "SILENCE_TIMEOUT", step_log)
    fire(sm, Event.END_OF_TURN, "SILENCE_TIMEOUT", step_log)
    fire(sm, Event.LLM_FULL_READY, "SILENCE_TIMEOUT", step_log)
    fire(sm, Event.BARGE_IN_DETECTED, "SILENCE_TIMEOUT", step_log)
    fire(sm, Event.SILENCE_TIMEOUT, "SILENCE_TIMEOUT", step_log)
    assert sm.current_state == State.IDLE

    for prev_s, ev, cur_s in step_log:
        all_fired.add((prev_s, ev, cur_s))
    total_transitions += len(step_log)
    print(f"  => Final: {sm.current_state.name}  [OK]")
    scenarios_passed += 1

    # 8. Filler TTS_PLAYBACK_DONE self-loop then Sonnet arrives
    run_scenario(
        "Filler self-loop then Sonnet",
        [
            Event.START_OF_TURN,
            Event.END_OF_TURN,
            Event.LLM_FILLER_READY,
            Event.TTS_PLAYBACK_DONE,  # self-loop in FILLER_RESPONSE
            Event.LLM_FULL_READY,
            Event.TTS_PLAYBACK_DONE,
        ],
        State.IDLE,
    )

    # 9. TTS_AUDIO_READY self-loop in BOT_SPEAKING
    run_scenario(
        "TTS_AUDIO_READY self-loop in BOT_SPEAKING",
        [
            Event.START_OF_TURN,
            Event.END_OF_TURN,
            Event.LLM_FULL_READY,
            Event.TTS_AUDIO_READY,  # self-loop
            Event.TTS_PLAYBACK_DONE,
        ],
        State.IDLE,
    )

    # 10. Multi-turn conversation (3 turns)
    print("\n--- Scenario: Multi-turn (3 turns) ---")
    sm = StateMachine(logger)
    step_log = []
    for turn in range(3):
        print(f"  [Turn {turn + 1}]")
        fire(sm, Event.START_OF_TURN, "Multi-turn", step_log)
        fire(sm, Event.END_OF_TURN, "Multi-turn", step_log)
        fire(sm, Event.LLM_FULL_READY, "Multi-turn", step_log)
        fire(sm, Event.TTS_PLAYBACK_DONE, "Multi-turn", step_log)
    for prev_s, ev, cur_s in step_log:
        all_fired.add((prev_s, ev, cur_s))
    total_transitions += len(step_log)
    assert sm.current_state == State.IDLE
    assert sm.ctx.turn_id == 3
    print(f"  => Final: {sm.current_state.name}, turn_id={sm.ctx.turn_id}  [OK]")
    scenarios_passed += 1

    # Summary
    print("\n" + "=" * 50)
    print(f"Scenarios run:      {scenarios_passed}")
    print(f"Transitions fired:  {total_transitions}")
    print(f"Unique transitions: {len(all_fired)}")
    print(f"All passed.")
    print(f"Log written to {log_path}")


if __name__ == "__main__":
    run()
