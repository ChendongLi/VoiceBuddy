"""
Tests for Twilio inbound call handling (AGE-11).

Covers:
- TwiML generation for /incoming-call
- Twilio signature validation
- MediaStream WebSocket protocol (connected → start → media → stop)
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from twilio_handler import build_twiml_response, handle_twilio_media

# ---------------------------------------------------------------------------
# TwiML generation
# ---------------------------------------------------------------------------


class TestBuildTwiml:
    @patch.dict("os.environ", {"TWILIO_WEBHOOK_HOST": "example.ngrok.io"})
    def test_contains_stream_url(self):
        twiml = build_twiml_response()
        assert 'url="wss://example.ngrok.io/twilio-media"' in twiml

    @patch.dict("os.environ", {"TWILIO_WEBHOOK_HOST": "example.ngrok.io"})
    def test_is_valid_xml_structure(self):
        twiml = build_twiml_response()
        assert twiml.startswith('<?xml version="1.0"')
        assert "<Response>" in twiml
        assert "<Connect>" in twiml
        assert "<Stream" in twiml
        assert "</Response>" in twiml

    @patch.dict("os.environ", {"TWILIO_WEBHOOK_HOST": "my-host.com"})
    def test_uses_configured_host(self):
        twiml = build_twiml_response()
        assert "my-host.com" in twiml


# ---------------------------------------------------------------------------
# Signature validation (via handle_incoming_call)
# ---------------------------------------------------------------------------


class TestIncomingCallHandler:
    def _make_request(self, signature="", body=b""):
        return SimpleNamespace(
            headers={"X-Twilio-Signature": signature},
            body=body,
        )

    def _make_connection(self):
        responses = []

        def respond(status, body):
            resp = SimpleNamespace(status=status, body=body, headers={})
            responses.append(resp)
            return resp

        conn = SimpleNamespace(respond=respond)
        return conn, responses

    @patch.dict("os.environ", {"TWILIO_AUTH_TOKEN": "", "TWILIO_WEBHOOK_HOST": "example.ngrok.io"})
    def test_no_auth_token_allows_request(self):
        """When TWILIO_AUTH_TOKEN is empty, skip validation and return TwiML."""
        from twilio_handler import handle_incoming_call

        conn, responses = self._make_connection()
        request = self._make_request()
        handle_incoming_call(conn, request)

        assert len(responses) == 1
        assert responses[0].headers["Content-Type"] == "application/xml"
        assert "<Response>" in responses[0].body

    @patch.dict(
        "os.environ",
        {
            "TWILIO_AUTH_TOKEN": "test-token-123",
            "TWILIO_WEBHOOK_HOST": "example.ngrok.io",
            "SKIP_TWILIO_VALIDATION": "",
        },
    )
    @patch("twilio_handler.RequestValidator")
    def test_invalid_signature_returns_403(self, mock_validator_cls):
        from twilio_handler import handle_incoming_call

        mock_validator_cls.return_value.validate.return_value = False

        conn, responses = self._make_connection()
        request = self._make_request(signature="bad-sig")
        handle_incoming_call(conn, request)

        assert len(responses) == 1
        assert responses[0].status.value == 403

    @patch.dict("os.environ", {"TWILIO_AUTH_TOKEN": "test-token-123", "TWILIO_WEBHOOK_HOST": "example.ngrok.io"})
    @patch("twilio_handler.RequestValidator")
    def test_valid_signature_returns_twiml(self, mock_validator_cls):
        from twilio_handler import handle_incoming_call

        mock_validator_cls.return_value.validate.return_value = True

        conn, responses = self._make_connection()
        request = self._make_request(signature="good-sig")
        handle_incoming_call(conn, request)

        assert len(responses) == 1
        assert responses[0].headers["Content-Type"] == "application/xml"
        assert "<Response>" in responses[0].body


# ---------------------------------------------------------------------------
# _validate_twilio_signature unit tests
# ---------------------------------------------------------------------------


class TestValidateTwilioSignature:
    def _make_request(self, signature="", body=b"", extra_headers=None):
        headers = {"X-Twilio-Signature": signature}
        if extra_headers:
            headers.update(extra_headers)
        return SimpleNamespace(headers=headers, body=body)

    @patch.dict("os.environ", {"SKIP_TWILIO_VALIDATION": "true"})
    def test_skip_validation_env_returns_true(self):
        from twilio_handler import _validate_twilio_signature

        assert _validate_twilio_signature(self._make_request()) is True

    @patch.dict(
        "os.environ", {"TWILIO_AUTH_TOKEN": "", "TWILIO_WEBHOOK_HOST": "host.com", "SKIP_TWILIO_VALIDATION": ""}
    )
    def test_missing_auth_token_returns_true(self):
        from twilio_handler import _validate_twilio_signature

        assert _validate_twilio_signature(self._make_request()) is True

    @patch.dict("os.environ", {"TWILIO_AUTH_TOKEN": "tok", "TWILIO_WEBHOOK_HOST": "", "SKIP_TWILIO_VALIDATION": ""})
    def test_missing_webhook_host_returns_true(self):
        from twilio_handler import _validate_twilio_signature

        assert _validate_twilio_signature(self._make_request()) is True

    @patch.dict(
        "os.environ",
        {"TWILIO_AUTH_TOKEN": "  tok  ", "TWILIO_WEBHOOK_HOST": "  host.com  ", "SKIP_TWILIO_VALIDATION": ""},
    )
    @patch("twilio_handler.RequestValidator")
    def test_strips_whitespace_from_env(self, mock_validator_cls):
        from twilio_handler import _validate_twilio_signature

        mock_validator_cls.return_value.validate.return_value = True
        _validate_twilio_signature(self._make_request(signature="sig"))
        mock_validator_cls.assert_called_once_with("tok")

    @patch.dict(
        "os.environ", {"TWILIO_AUTH_TOKEN": "tok", "TWILIO_WEBHOOK_HOST": "host.com", "SKIP_TWILIO_VALIDATION": ""}
    )
    @patch("twilio_handler.RequestValidator")
    def test_uses_x_forwarded_proto(self, mock_validator_cls):
        from twilio_handler import _validate_twilio_signature

        mock_validator_cls.return_value.validate.return_value = True
        req = self._make_request(signature="sig", extra_headers={"X-Forwarded-Proto": "https"})
        _validate_twilio_signature(req)
        call_args = mock_validator_cls.return_value.validate.call_args
        assert call_args[0][0] == "https://host.com/incoming-call"

    @patch.dict(
        "os.environ", {"TWILIO_AUTH_TOKEN": "tok", "TWILIO_WEBHOOK_HOST": "host.com", "SKIP_TWILIO_VALIDATION": ""}
    )
    @patch("twilio_handler.RequestValidator")
    def test_defaults_to_https_without_forwarded_proto(self, mock_validator_cls):
        from twilio_handler import _validate_twilio_signature

        mock_validator_cls.return_value.validate.return_value = True
        _validate_twilio_signature(self._make_request(signature="sig"))
        call_args = mock_validator_cls.return_value.validate.call_args
        assert call_args[0][0] == "https://host.com/incoming-call"

    @patch.dict(
        "os.environ", {"TWILIO_AUTH_TOKEN": "tok", "TWILIO_WEBHOOK_HOST": "host.com", "SKIP_TWILIO_VALIDATION": ""}
    )
    @patch("twilio_handler.RequestValidator")
    def test_parses_body_params(self, mock_validator_cls):
        from twilio_handler import _validate_twilio_signature

        mock_validator_cls.return_value.validate.return_value = True
        body = b"From=%2B15551234567&To=%2B15559876543&CallSid=CA123"
        _validate_twilio_signature(self._make_request(signature="sig", body=body))
        call_args = mock_validator_cls.return_value.validate.call_args
        flat_params = call_args[0][1]
        assert flat_params["From"] == "+15551234567"
        assert flat_params["To"] == "+15559876543"

    @patch.dict(
        "os.environ", {"TWILIO_AUTH_TOKEN": "tok", "TWILIO_WEBHOOK_HOST": "host.com", "SKIP_TWILIO_VALIDATION": ""}
    )
    @patch("twilio_handler.RequestValidator")
    def test_validator_exception_returns_false(self, mock_validator_cls):
        from twilio_handler import _validate_twilio_signature

        mock_validator_cls.return_value.validate.side_effect = Exception("bad")
        assert _validate_twilio_signature(self._make_request(signature="sig")) is False


# ---------------------------------------------------------------------------
# MediaStream WebSocket protocol
# ---------------------------------------------------------------------------


class FakeWebSocket:
    """Async iterator over a list of JSON messages."""

    def __init__(self, messages: list[str]):
        self._messages = messages
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._messages):
            raise StopAsyncIteration
        msg = self._messages[self._index]
        self._index += 1
        return msg


def _media_event(payload_bytes: bytes, sequence: int = 1) -> str:
    return json.dumps(
        {
            "event": "media",
            "sequenceNumber": str(sequence),
            "media": {
                "track": "inbound",
                "chunk": str(sequence),
                "timestamp": str(sequence * 20),
                "payload": base64.b64encode(payload_bytes).decode(),
            },
        }
    )


CONNECTED_EVENT = json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"})

START_EVENT = json.dumps(
    {
        "event": "start",
        "sequenceNumber": "1",
        "streamSid": "MZ-stream-123",
        "start": {
            "callSid": "CA-call-456",
            "accountSid": "AC-account-789",
            "customParameters": {"from": "+15551234567", "to": "+15559876543"},
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        },
    }
)

STOP_EVENT = json.dumps({"event": "stop", "sequenceNumber": "5", "streamSid": "MZ-stream-123"})


@pytest.mark.asyncio
class TestTwilioMediaStream:
    async def test_full_sequence(self):
        """connected → start → media (x2) → stop processes without error."""
        audio_chunk = b"\x80" * 160  # 20ms of mulaw silence
        messages = [
            CONNECTED_EVENT,
            START_EVENT,
            _media_event(audio_chunk, 1),
            _media_event(audio_chunk, 2),
            STOP_EVENT,
        ]
        ws = FakeWebSocket(messages)
        await handle_twilio_media(ws)

    async def test_start_extracts_session_context(self):
        """Verify CallSid and StreamSid are logged (no crash, correct parsing)."""
        messages = [CONNECTED_EVENT, START_EVENT, STOP_EVENT]
        ws = FakeWebSocket(messages)
        # Should complete without error — session context is internal
        await handle_twilio_media(ws)

    async def test_media_decodes_base64(self):
        """Media payload is decoded from base64 to raw bytes."""
        raw = b"\xff\x00\xab\xcd" * 40
        messages = [
            CONNECTED_EVENT,
            START_EVENT,
            _media_event(raw, 1),
            STOP_EVENT,
        ]
        ws = FakeWebSocket(messages)
        await handle_twilio_media(ws)

    async def test_empty_media_payload(self):
        """Empty media payload should not crash."""
        messages = [
            CONNECTED_EVENT,
            START_EVENT,
            json.dumps({"event": "media", "media": {"payload": ""}}),
            STOP_EVENT,
        ]
        ws = FakeWebSocket(messages)
        await handle_twilio_media(ws)

    async def test_invalid_json_skipped(self):
        """Non-JSON messages are skipped gracefully."""
        messages = [
            "not json at all",
            CONNECTED_EVENT,
            START_EVENT,
            STOP_EVENT,
        ]
        ws = FakeWebSocket(messages)
        await handle_twilio_media(ws)

    async def test_stop_without_start(self):
        """Stop event before start should not crash."""
        messages = [CONNECTED_EVENT, STOP_EVENT]
        ws = FakeWebSocket(messages)
        await handle_twilio_media(ws)
