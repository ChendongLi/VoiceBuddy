"""
Unit tests for voice ID routing (voice_config module).

Covers the VOICE_IDS registry and resolve_voice_id() URL-parsing logic.
Pure stdlib — no external SDK dependencies.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from voice_config import DEFAULT_VOICE, VOICE_IDS, resolve_voice_id

# Compiled once for UUID format checks
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

ALLISON_ID = "f786b574-daa5-4673-aa0c-cbe3e8534c02"
DON_ID = "a3e3ea35-4533-47d6-afdb-c286538657ca"


# ---------------------------------------------------------------------------
# VOICE_IDS registry integrity
# ---------------------------------------------------------------------------


class TestVoiceRegistry:
    def test_allison_id_correct(self):
        assert VOICE_IDS["allison"] == ALLISON_ID

    def test_don_id_correct(self):
        assert VOICE_IDS["don"] == DON_ID

    def test_all_ids_are_valid_uuids(self):
        for name, vid in VOICE_IDS.items():
            assert _UUID_RE.match(vid), f"Voice '{name}' has an invalid UUID: {vid!r}"

    def test_all_ids_are_lowercase_uuids(self):
        """UUIDs must be lowercase hex to match Cartesia's expected format."""
        for name, vid in VOICE_IDS.items():
            assert vid == vid.lower(), f"Voice '{name}' UUID is not lowercase: {vid!r}"

    def test_no_duplicate_voice_ids(self):
        ids = list(VOICE_IDS.values())
        assert len(ids) == len(set(ids)), "Duplicate Cartesia voice IDs detected"

    def test_registered_voices_are_known(self):
        assert set(VOICE_IDS.keys()) == {"allison", "don"}

    def test_default_voice_exists_in_registry(self):
        assert DEFAULT_VOICE in VOICE_IDS


# ---------------------------------------------------------------------------
# resolve_voice_id — happy paths
# ---------------------------------------------------------------------------


class TestResolveVoiceIdHappyPath:
    def test_allison_query_param(self):
        assert resolve_voice_id("/ws?voice=allison") == ALLISON_ID

    def test_don_query_param(self):
        assert resolve_voice_id("/ws?voice=don") == DON_ID

    def test_returns_string(self):
        result = resolve_voice_id("/ws?voice=allison")
        assert isinstance(result, str)

    def test_result_is_valid_uuid(self):
        for voice in VOICE_IDS:
            result = resolve_voice_id(f"/ws?voice={voice}")
            assert _UUID_RE.match(result), f"resolve_voice_id returned invalid UUID for '{voice}': {result}"


# ---------------------------------------------------------------------------
# resolve_voice_id — case normalisation
# ---------------------------------------------------------------------------


class TestCaseNormalisation:
    @pytest.mark.parametrize("variant", ["ALLISON", "Allison", "aLLiSoN"])
    def test_allison_case_variants(self, variant):
        assert resolve_voice_id(f"/ws?voice={variant}") == ALLISON_ID

    @pytest.mark.parametrize("variant", ["DON", "Don", "dOn"])
    def test_don_case_variants(self, variant):
        assert resolve_voice_id(f"/ws?voice={variant}") == DON_ID


# ---------------------------------------------------------------------------
# resolve_voice_id — fallback to default
# ---------------------------------------------------------------------------


class TestDefaultFallback:
    def test_missing_voice_param_defaults(self):
        assert resolve_voice_id("/ws") == VOICE_IDS[DEFAULT_VOICE]

    def test_empty_voice_param_defaults(self):
        # ?voice= with no value → empty string → not in registry → default
        assert resolve_voice_id("/ws?voice=") == VOICE_IDS[DEFAULT_VOICE]

    def test_unknown_voice_name_defaults(self):
        assert resolve_voice_id("/ws?voice=nobody") == VOICE_IDS[DEFAULT_VOICE]

    def test_numeric_voice_param_defaults(self):
        assert resolve_voice_id("/ws?voice=12345") == VOICE_IDS[DEFAULT_VOICE]

    def test_path_without_query_string_defaults(self):
        assert resolve_voice_id("/ws") == VOICE_IDS[DEFAULT_VOICE]

    def test_completely_different_path_defaults(self):
        assert resolve_voice_id("/health") == VOICE_IDS[DEFAULT_VOICE]


# ---------------------------------------------------------------------------
# resolve_voice_id — query string robustness
# ---------------------------------------------------------------------------


class TestQueryStringRobustness:
    def test_extra_params_before_voice(self):
        assert resolve_voice_id("/ws?session=abc&voice=don") == DON_ID

    def test_extra_params_after_voice(self):
        assert resolve_voice_id("/ws?voice=allison&lang=en") == ALLISON_ID

    def test_multiple_query_params(self):
        assert resolve_voice_id("/ws?a=1&b=2&voice=don&c=3") == DON_ID

    def test_voice_param_with_whitespace_stripped(self):
        # URL-encoded space (%20) around the value — stripped by .strip()
        assert resolve_voice_id("/ws?voice=%20allison%20") == ALLISON_ID

    def test_repeated_voice_param_first_wins(self):
        """When ?voice= appears twice, parse_qs returns both; we take index 0."""
        result = resolve_voice_id("/ws?voice=allison&voice=don")
        # Result must be one of the two valid voice IDs
        assert result in VOICE_IDS.values()

    def test_fragment_ignored(self):
        # URL fragments are not part of the query string
        assert resolve_voice_id("/ws?voice=don#section") == DON_ID


# ---------------------------------------------------------------------------
# Parametrised round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("voice_key,expected_id", list(VOICE_IDS.items()))
def test_every_registered_voice_resolves_correctly(voice_key, expected_id):
    """Every entry in VOICE_IDS must round-trip through resolve_voice_id."""
    assert resolve_voice_id(f"/ws?voice={voice_key}") == expected_id
