"""
Unit test for LatencyLogger

Validates that the logger:
1. Creates log files correctly
2. Writes valid JSONL events
3. Supports all event types (latency, state, error)
4. Can read back events correctly
"""

import json
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from latency_logger import LatencyLogger, LogEvent


def test_logger():
    """Test the latency logger functionality."""
    # Use a test-specific log file
    test_log_file = "logs/test_logger.jsonl"

    # Clean up any existing test log
    test_log_path = Path(test_log_file)
    if test_log_path.exists():
        test_log_path.unlink()

    print("🧪 Testing LatencyLogger...")

    # Initialize logger
    logger = LatencyLogger(log_file=test_log_file)
    print(f"✓ Logger initialized with file: {test_log_file}")

    # Test session parameters
    session_id = "test-session-001"
    turn_id = 1

    # Test 1: Log a latency event
    print("\n1. Testing latency event logging...")
    logger.log_latency(
        session_id=session_id,
        turn_id=turn_id,
        stage="eot_detected",
        latency_ms=125.5,
        metadata={"model": "deepgram-flux"},
    )
    print("✓ Latency event logged")

    # Test 2: Log a state transition
    print("\n2. Testing state transition logging...")
    logger.log_state_transition(
        session_id=session_id,
        turn_id=turn_id,
        from_state="LISTENING",
        to_state="PROCESSING",
        metadata={"trigger": "eot_detected"},
    )
    print("✓ State transition logged")

    # Test 3: Log an error
    print("\n3. Testing error logging...")
    logger.log_error(
        session_id=session_id,
        turn_id=turn_id,
        error_type="API_ERROR",
        error_message="Deepgram connection timeout",
        metadata={"retry_count": 1},
    )
    print("✓ Error event logged")

    # Test 4: Log multiple latency events (simulating a full turn)
    print("\n4. Testing multiple latency events...")
    stages = [
        ("user_stopped_speaking", 0.0),
        ("eot_detected", 150.2),
        ("transcript_received", 175.8),
        ("llm_first_token", 425.3),
        ("tts_first_byte", 625.7),
        ("playback_start", 650.1),
    ]

    for stage, latency in stages:
        logger.log_latency(session_id=session_id, turn_id=turn_id, stage=stage, latency_ms=latency)
    print(f"✓ Logged {len(stages)} latency measurements")

    # Test 5: Read back and validate
    print("\n5. Reading back events from JSONL...")
    events = logger.read_events()
    print(f"✓ Read {len(events)} events from log file")

    # Validate structure
    expected_event_count = 1 + 1 + 1 + len(stages)  # latency + state + error + stages
    assert len(events) == expected_event_count, f"Expected {expected_event_count} events, got {len(events)}"
    print(f"✓ Event count matches expected ({expected_event_count})")

    # Validate event types
    event_types = [e.event_type for e in events]
    assert "latency" in event_types, "Missing latency events"
    assert "state" in event_types, "Missing state events"
    assert "error" in event_types, "Missing error events"
    print("✓ All event types present")

    # Validate JSONL format by reading raw file
    print("\n6. Validating JSONL format...")
    with open(test_log_file, "r") as f:
        lines = f.readlines()
        for i, line in enumerate(lines):
            try:
                event_dict = json.loads(line)
                # Validate required fields
                assert "session_id" in event_dict, f"Line {i}: missing session_id"
                assert "turn_id" in event_dict, f"Line {i}: missing turn_id"
                assert "timestamp_ms" in event_dict, f"Line {i}: missing timestamp_ms"
                assert "event_type" in event_dict, f"Line {i}: missing event_type"
                assert "data" in event_dict, f"Line {i}: missing data"
            except json.JSONDecodeError as e:
                raise AssertionError(f"Line {i} is not valid JSON: {e}")
    print(f"✓ All {len(lines)} lines are valid JSON")

    # Validate timestamps are monotonically increasing (or very close)
    print("\n7. Validating timestamps...")
    timestamps = [e.timestamp_ms for e in events]
    for i in range(1, len(timestamps)):
        # Allow some small backward drift for events logged in quick succession
        assert timestamps[i] >= timestamps[i - 1] - 1, f"Timestamps not monotonic: {timestamps[i-1]} -> {timestamps[i]}"
    print("✓ Timestamps are valid")

    # Display sample events
    print("\n8. Sample events:")
    print("-" * 80)
    for i, event in enumerate(events[:3]):
        print(f"\nEvent {i+1}:")
        print(f"  Type: {event.event_type}")
        print(f"  Session: {event.session_id}")
        print(f"  Turn: {event.turn_id}")
        print(f"  Timestamp: {event.timestamp_ms}")
        print(f"  Data: {event.data}")
    print("-" * 80)

    # Clean up test log
    print("\n9. Cleaning up test log file...")
    test_log_path.unlink()
    print("✓ Test log file removed")

    print("\n" + "=" * 80)
    print("✅ ALL TESTS PASSED")
    print("=" * 80)
    print(f"\nThe logger successfully:")
    print(f"  • Created and wrote to {test_log_file}")
    print(f"  • Logged {expected_event_count} events across 3 event types")
    print(f"  • Generated valid JSONL format")
    print(f"  • Maintained correct timestamps")
    print(f"  • Read back events correctly")


if __name__ == "__main__":
    try:
        test_logger()
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
