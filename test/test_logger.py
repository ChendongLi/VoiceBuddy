"""
Unit tests for LatencyLogger.

Validates JSONL output structure, event types, field completeness,
timestamp monotonicity, and file-creation behaviour.

All tests use pytest's tmp_path fixture — no files are written to the
repo working tree and no cleanup is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from latency_logger import LatencyLogger, LogEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_logger(tmp_path: Path, filename: str = "test.jsonl") -> LatencyLogger:
    return LatencyLogger(log_file=str(tmp_path / filename))


def read_raw_lines(log_file: Path) -> list[dict]:
    return [json.loads(line) for line in log_file.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# File creation
# ---------------------------------------------------------------------------


class TestFileCreation:
    def test_creates_file_on_first_write(self, tmp_path):
        logger = make_logger(tmp_path)
        assert not (tmp_path / "test.jsonl").exists()
        logger.log_event("s1", 1, "latency", {"stage": "eot_detected", "latency_ms": 100.0})
        assert (tmp_path / "test.jsonl").exists()

    def test_creates_nested_directory(self, tmp_path):
        nested = str(tmp_path / "deep" / "nested" / "log.jsonl")
        logger = LatencyLogger(log_file=nested)
        logger.log_event("s1", 1, "latency", {"stage": "eot_detected", "latency_ms": 50.0})
        assert Path(nested).exists()

    def test_appends_across_instances(self, tmp_path):
        log_file = str(tmp_path / "append.jsonl")
        LatencyLogger(log_file=log_file).log_event("s1", 1, "latency", {"stage": "a", "latency_ms": 1.0})
        LatencyLogger(log_file=log_file).log_event("s1", 1, "latency", {"stage": "b", "latency_ms": 2.0})
        events = LatencyLogger(log_file=log_file).read_events()
        assert len(events) == 2


# ---------------------------------------------------------------------------
# JSONL format
# ---------------------------------------------------------------------------


class TestJsonlFormat:
    REQUIRED_FIELDS = {"session_id", "turn_id", "timestamp_ms", "event_type", "data"}

    def test_each_line_is_valid_json(self, tmp_path):
        logger = make_logger(tmp_path)
        for i in range(5):
            logger.log_latency("s1", i, f"stage_{i}", float(i * 10))
        lines = (tmp_path / "test.jsonl").read_text().splitlines()
        for line in lines:
            json.loads(line)  # raises if invalid

    def test_required_fields_present_in_every_event(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_latency("s1", 1, "eot_detected", 120.5)
        logger.log_state_transition("s1", 1, "IDLE", "USER_SPEAKING")
        logger.log_error("s1", 1, "API_ERROR", "Timeout")
        for row in read_raw_lines(tmp_path / "test.jsonl"):
            missing = self.REQUIRED_FIELDS - row.keys()
            assert not missing, f"Missing fields: {missing}"

    def test_no_trailing_whitespace_on_lines(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_latency("s1", 1, "eot_detected", 99.0)
        for line in (tmp_path / "test.jsonl").read_text().splitlines():
            assert line == line.rstrip()

    def test_one_event_per_line(self, tmp_path):
        logger = make_logger(tmp_path)
        for i in range(7):
            logger.log_latency("s1", i, f"stage_{i}", float(i))
        lines = [ln for ln in (tmp_path / "test.jsonl").read_text().splitlines() if ln.strip()]
        assert len(lines) == 7


# ---------------------------------------------------------------------------
# Latency events
# ---------------------------------------------------------------------------


class TestLatencyEvents:
    def test_latency_event_type(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_latency("s1", 1, "eot_detected", 150.0)
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["event_type"] == "latency"

    def test_latency_stage_and_value_preserved(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_latency("s1", 2, "tts_first_byte", 625.75)
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["data"]["stage"] == "tts_first_byte"
        assert rows[0]["data"]["latency_ms"] == pytest.approx(625.75)

    def test_latency_metadata_attached(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_latency("s1", 1, "eot_detected", 100.0, metadata={"model": "deepgram-flux"})
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["data"]["metadata"]["model"] == "deepgram-flux"

    def test_full_pipeline_stages_logged_in_order(self, tmp_path):
        logger = make_logger(tmp_path)
        stages = [
            ("user_stopped_speaking", 0.0),
            ("eot_detected", 150.2),
            ("transcript_received", 175.8),
            ("llm_first_token", 425.3),
            ("tts_first_byte", 625.7),
            ("playback_start", 650.1),
        ]
        for stage, ms in stages:
            logger.log_latency("s1", 1, stage, ms)

        events = logger.read_events()
        assert len(events) == len(stages)
        for event, (stage, ms) in zip(events, stages, strict=False):
            assert event.data["stage"] == stage
            assert event.data["latency_ms"] == pytest.approx(ms)


# ---------------------------------------------------------------------------
# State transition events
# ---------------------------------------------------------------------------


class TestStateTransitionEvents:
    def test_state_event_type(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_state_transition("s1", 1, "IDLE", "USER_SPEAKING")
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["event_type"] == "state"

    def test_from_and_to_state_preserved(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_state_transition("s1", 1, "PROCESSING", "BOT_SPEAKING")
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["data"]["from_state"] == "PROCESSING"
        assert rows[0]["data"]["to_state"] == "BOT_SPEAKING"

    def test_metadata_attached(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_state_transition("s1", 1, "IDLE", "USER_SPEAKING", metadata={"trigger": "vad"})
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["data"]["metadata"]["trigger"] == "vad"

    def test_full_happy_path_sequence(self, tmp_path):
        """Log a realistic multi-state sequence and verify all transitions round-trip."""
        logger = make_logger(tmp_path)
        transitions = [
            ("IDLE", "USER_SPEAKING"),
            ("USER_SPEAKING", "PROCESSING"),
            ("PROCESSING", "BOT_SPEAKING"),
            ("BOT_SPEAKING", "IDLE"),
        ]
        for from_s, to_s in transitions:
            logger.log_state_transition("s1", 1, from_s, to_s)

        events = logger.read_events()
        assert len(events) == 4
        for event, (from_s, to_s) in zip(events, transitions, strict=False):
            assert event.data["from_state"] == from_s
            assert event.data["to_state"] == to_s


# ---------------------------------------------------------------------------
# Error events
# ---------------------------------------------------------------------------


class TestErrorEvents:
    def test_error_event_type(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_error("s1", 1, "API_ERROR", "Deepgram timeout")
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["event_type"] == "error"

    def test_error_type_and_message_preserved(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_error("s1", 1, "TTS_ERROR", "Cartesia connection refused")
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["data"]["error_type"] == "TTS_ERROR"
        assert rows[0]["data"]["error_message"] == "Cartesia connection refused"

    def test_error_with_retry_metadata(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_error("s1", 1, "API_ERROR", "Timeout", metadata={"retry_count": 3})
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["data"]["metadata"]["retry_count"] == 3


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------


class TestTimestamps:
    def test_timestamp_is_milliseconds(self, tmp_path):
        """timestamp_ms should be in the Unix-ms ballpark (> year 2020 in ms)."""
        logger = make_logger(tmp_path)
        logger.log_latency("s1", 1, "eot_detected", 100.0)
        rows = read_raw_lines(tmp_path / "test.jsonl")
        year_2020_ms = 1_577_836_800_000.0
        assert rows[0]["timestamp_ms"] > year_2020_ms

    def test_timestamps_monotonically_non_decreasing(self, tmp_path):
        logger = make_logger(tmp_path)
        for i in range(10):
            logger.log_latency("s1", 1, f"stage_{i}", float(i))
        events = logger.read_events()
        for i in range(1, len(events)):
            # Allow 1 ms drift for events written in very quick succession
            assert events[i].timestamp_ms >= events[i - 1].timestamp_ms - 1

    def test_custom_timestamp_honoured(self, tmp_path):
        logger = make_logger(tmp_path)
        fixed_ts = 1_700_000_000_000.0
        logger.log_event("s1", 1, "latency", {"stage": "x", "latency_ms": 0.0}, timestamp_ms=fixed_ts)
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["timestamp_ms"] == pytest.approx(fixed_ts)


# ---------------------------------------------------------------------------
# Session / turn metadata
# ---------------------------------------------------------------------------


class TestSessionMetadata:
    def test_session_id_preserved(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_latency("session-xyz-999", 3, "eot_detected", 50.0)
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["session_id"] == "session-xyz-999"

    def test_turn_id_preserved(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_latency("s1", 7, "eot_detected", 50.0)
        rows = read_raw_lines(tmp_path / "test.jsonl")
        assert rows[0]["turn_id"] == 7

    def test_multi_turn_session(self, tmp_path):
        """Multiple turns from the same session are distinguishable."""
        logger = make_logger(tmp_path)
        for turn in range(1, 4):
            logger.log_latency("session-abc", turn, "eot_detected", float(turn * 100))
        events = logger.read_events()
        assert [e.turn_id for e in events] == [1, 2, 3]


# ---------------------------------------------------------------------------
# read_events round-trip
# ---------------------------------------------------------------------------


class TestReadEvents:
    def test_returns_empty_list_when_file_missing(self, tmp_path):
        logger = LatencyLogger(log_file=str(tmp_path / "nonexistent.jsonl"))
        assert logger.read_events() == []

    def test_round_trip_all_event_types(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_latency("s1", 1, "eot_detected", 123.0)
        logger.log_state_transition("s1", 1, "IDLE", "USER_SPEAKING")
        logger.log_error("s1", 1, "API_ERROR", "Timeout")

        events = logger.read_events()
        assert len(events) == 3
        types = {e.event_type for e in events}
        assert types == {"latency", "state", "error"}

    def test_returns_log_event_objects(self, tmp_path):
        logger = make_logger(tmp_path)
        logger.log_latency("s1", 1, "eot_detected", 50.0)
        events = logger.read_events()
        assert all(isinstance(e, LogEvent) for e in events)
