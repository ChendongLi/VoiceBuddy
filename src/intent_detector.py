"""Intent detection for human handoff requests."""

from __future__ import annotations

import re

_HANDOFF_KEYWORDS = [
    "speak to someone",
    "real person",
    "human",
    "agent",
    "transfer",
    "representative",
    "operator",
    "manager",
]

_PATTERN = re.compile("|".join(re.escape(kw) for kw in _HANDOFF_KEYWORDS), re.IGNORECASE)


def detect_handoff_intent(transcript: str) -> bool:
    """Return True if the transcript contains a human-handoff keyword."""
    return bool(_PATTERN.search(transcript))
