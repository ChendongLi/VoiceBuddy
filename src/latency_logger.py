"""
Latency Logger for VoiceBuddy

Structured event logger that writes timestamped events to JSONL format.
Supports latency measurements, state transitions, and error tracking.
Designed for async contexts (asyncio compatible).
"""

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class LogEvent:
    """
    Structured log event for VoiceBuddy telemetry.

    Attributes:
        session_id: Unique identifier for the conversation session
        turn_id: Sequential turn number within the session
        timestamp_ms: Unix timestamp in milliseconds
        event_type: Type of event ("latency", "state", "error")
        data: Additional event-specific data
    """

    session_id: str
    turn_id: int
    timestamp_ms: float
    event_type: str
    data: Dict[str, Any]


class LatencyLogger:
    """
    Append-only JSONL logger for tracking latency and state transitions.

    Stage names for latency events:
    - user_stopped_speaking: Start of Stage 1
    - eot_detected: End of Stage 1 (End-of-Turn from Deepgram)
    - transcript_received: End of Stage 2
    - llm_first_token: End of Stage 3 (Haiku or Sonnet)
    - tts_first_byte: End of Stage 4 (Cartesia)
    - playback_start: End of Stage 5
    """

    def __init__(self, log_file: str = "logs/voicebuddy.jsonl"):
        """
        Initialize the latency logger.

        Args:
            log_file: Path to the JSONL log file (default: logs/voicebuddy.jsonl)
        """
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def log_event(
        self, session_id: str, turn_id: int, event_type: str, data: Dict[str, Any], timestamp_ms: Optional[float] = None
    ) -> None:
        """
        Log a single event to the JSONL file.

        Args:
            session_id: Unique session identifier
            turn_id: Turn number within session
            event_type: Type of event ("latency", "state", "error")
            data: Event-specific data dictionary
            timestamp_ms: Unix timestamp in ms (defaults to current time)
        """
        if timestamp_ms is None:
            timestamp_ms = time.time() * 1000

        event = LogEvent(
            session_id=session_id, turn_id=turn_id, timestamp_ms=timestamp_ms, event_type=event_type, data=data
        )

        # Write to JSONL (newline-delimited JSON)
        with open(self.log_file, "a") as f:
            f.write(json.dumps(asdict(event)) + "\n")

    def log_latency(
        self, session_id: str, turn_id: int, stage: str, latency_ms: float, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log a latency measurement event.

        Args:
            session_id: Unique session identifier
            turn_id: Turn number within session
            stage: Name of the stage (e.g., "eot_detected", "llm_first_token")
            latency_ms: Measured latency in milliseconds
            metadata: Optional additional metadata
        """
        data = {"stage": stage, "latency_ms": latency_ms}
        if metadata:
            data["metadata"] = metadata

        self.log_event(session_id, turn_id, "latency", data)

    def log_state_transition(
        self, session_id: str, turn_id: int, from_state: str, to_state: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log a state machine transition event.

        Args:
            session_id: Unique session identifier
            turn_id: Turn number within session
            from_state: Previous state name
            to_state: New state name
            metadata: Optional additional metadata
        """
        data = {"from_state": from_state, "to_state": to_state}
        if metadata:
            data["metadata"] = metadata

        self.log_event(session_id, turn_id, "state", data)

    def log_error(
        self,
        session_id: str,
        turn_id: int,
        error_type: str,
        error_message: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Log an error event.

        Args:
            session_id: Unique session identifier
            turn_id: Turn number within session
            error_type: Type/category of error
            error_message: Error message or description
            metadata: Optional additional metadata
        """
        data = {"error_type": error_type, "error_message": error_message}
        if metadata:
            data["metadata"] = metadata

        self.log_event(session_id, turn_id, "error", data)

    def get_timestamp_ms(self) -> float:
        """
        Get current timestamp in milliseconds.

        Returns:
            Current Unix timestamp in milliseconds
        """
        return time.time() * 1000

    def read_events(self) -> list[LogEvent]:
        """
        Read all events from the log file.

        Returns:
            List of LogEvent objects
        """
        events = []
        if not self.log_file.exists():
            return events

        with open(self.log_file, "r") as f:
            for line in f:
                if line.strip():
                    event_dict = json.loads(line)
                    events.append(LogEvent(**event_dict))

        return events
