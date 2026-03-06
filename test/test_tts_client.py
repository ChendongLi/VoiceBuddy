"""
Unit tests for TTSClient voice ID resolution.

AsyncCartesia is mocked at import time so no real Cartesia API key or
network connection is required.  Tests focus on how TTSClient resolves
_voice_id from constructor arguments and environment variables.

Key design note: tts_client.py calls load_dotenv() at module level, so env
vars from .env are present by the time the module is imported.  All env
manipulation uses monkeypatch, which reverts after each test.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Patch AsyncCartesia at import time so the module loads cleanly without a
# real API key.  We use patch.object inside each test to swap the mock per-call.
with patch("tts_client.AsyncCartesia", MagicMock()):
    import tts_client as _mod
    from tts_client import TTSClient


def _make(voice_id=None):
    """Instantiate TTSClient with AsyncCartesia mocked out."""
    with patch.object(_mod, "AsyncCartesia", MagicMock()):
        return TTSClient(voice_id=voice_id)


# ---------------------------------------------------------------------------
# Explicit voice_id argument
# ---------------------------------------------------------------------------


class TestExplicitVoiceId:
    def test_explicit_id_is_used(self):
        client = _make(voice_id="explicit-voice-id")
        assert client._voice_id == "explicit-voice-id"

    def test_explicit_id_overrides_env_var(self, monkeypatch):
        monkeypatch.setenv("CARTESIA_VOICE_ID", "env-voice-id")
        client = _make(voice_id="explicit-voice-id")
        assert client._voice_id == "explicit-voice-id"

    def test_explicit_id_with_real_allison_uuid(self):
        uuid = "f786b574-daa5-4673-aa0c-cbe3e8534c02"
        client = _make(voice_id=uuid)
        assert client._voice_id == uuid

    def test_explicit_id_with_real_don_uuid(self):
        uuid = "a3e3ea35-4533-47d6-afdb-c286538657ca"
        client = _make(voice_id=uuid)
        assert client._voice_id == uuid

    def test_two_clients_hold_independent_ids(self):
        c1 = _make(voice_id="voice-A")
        c2 = _make(voice_id="voice-B")
        assert c1._voice_id == "voice-A"
        assert c2._voice_id == "voice-B"


# ---------------------------------------------------------------------------
# Environment variable fallback
# ---------------------------------------------------------------------------


class TestEnvVarFallback:
    def test_uses_env_var_when_no_explicit_id(self, monkeypatch):
        monkeypatch.setenv("CARTESIA_VOICE_ID", "env-voice-id")
        client = _make()
        assert client._voice_id == "env-voice-id"

    def test_none_explicit_falls_back_to_env(self, monkeypatch):
        """Passing voice_id=None explicitly should still fall back to env."""
        monkeypatch.setenv("CARTESIA_VOICE_ID", "env-fallback-id")
        client = _make(voice_id=None)
        assert client._voice_id == "env-fallback-id"

    def test_voice_id_is_none_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("CARTESIA_VOICE_ID", raising=False)
        client = _make()
        assert client._voice_id is None

    def test_explicit_id_wins_even_when_env_is_also_set(self, monkeypatch):
        monkeypatch.setenv("CARTESIA_VOICE_ID", "env-voice-id")
        client = _make(voice_id="winner")
        assert client._voice_id == "winner"


# ---------------------------------------------------------------------------
# API key wiring
# ---------------------------------------------------------------------------


class TestApiKeyWiring:
    def test_api_key_read_from_env(self, monkeypatch):
        monkeypatch.setenv("CARTESIA_API_KEY", "sk-test-key")
        mock_cls = MagicMock()
        with patch.object(_mod, "AsyncCartesia", mock_cls):
            TTSClient()
        mock_cls.assert_called_once_with(api_key="sk-test-key")

    def test_api_key_none_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
        mock_cls = MagicMock()
        with patch.object(_mod, "AsyncCartesia", mock_cls):
            TTSClient()
        mock_cls.assert_called_once_with(api_key=None)

    def test_api_key_not_exposed_as_voice_id(self, monkeypatch):
        monkeypatch.setenv("CARTESIA_API_KEY", "sk-secret")
        monkeypatch.setenv("CARTESIA_VOICE_ID", "v-id")
        client = _make()
        assert client._voice_id == "v-id"
        assert client._api_key == "sk-secret"
        assert client._voice_id != client._api_key


# ---------------------------------------------------------------------------
# Initial connection state
# ---------------------------------------------------------------------------


class TestInitialState:
    def test_ws_initially_none(self):
        client = _make(voice_id="any-id")
        assert client._ws is None

    def test_current_context_id_initially_none(self):
        client = _make(voice_id="any-id")
        assert client._current_context_id is None

    def test_client_object_created_on_init(self):
        mock_cls = MagicMock()
        with patch.object(_mod, "AsyncCartesia", mock_cls):
            client = TTSClient(voice_id="any-id")
        assert client._client is not None
