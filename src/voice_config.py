"""
Voice ID configuration for VoiceBuddy.

Centralises the Cartesia voice registry and the server-side resolution logic
so it can be imported and tested independently of the full server stack.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

# Selectable voices exposed in the UI.
# Key:   short name used as the ?voice= query-param value (always lowercase).
# Value: Cartesia voice UUID.
VOICE_IDS: dict[str, str] = {
    "allison": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    "don": "a3e3ea35-4533-47d6-afdb-c286538657ca",
}

DEFAULT_VOICE = "allison"


def resolve_voice_id(path: str) -> str:
    """Return the Cartesia voice UUID for the ?voice= param in *path*.

    Falls back to DEFAULT_VOICE for missing, empty, or unrecognised values.

    Args:
        path: The WebSocket request path, e.g. ``/ws?voice=don``.

    Returns:
        A Cartesia voice UUID string.
    """
    qs = parse_qs(urlparse(path).query)
    voice_key = qs.get("voice", [DEFAULT_VOICE])[0].strip().lower()
    return VOICE_IDS.get(voice_key, VOICE_IDS[DEFAULT_VOICE])
