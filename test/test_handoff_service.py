"""Tests for handoff_service and intent_detector."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from handoff_service import HandoffService
from intent_detector import detect_handoff_intent

# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


class TestDetectHandoffIntent:
    def test_exact_keyword(self):
        assert detect_handoff_intent("I want to speak to a human") is True

    def test_transfer_keyword(self):
        assert detect_handoff_intent("Can you transfer me?") is True

    def test_operator_keyword(self):
        assert detect_handoff_intent("Get me an operator") is True

    def test_representative(self):
        assert detect_handoff_intent("I need a representative") is True

    def test_manager(self):
        assert detect_handoff_intent("Let me talk to the manager") is True

    def test_speak_to_someone(self):
        assert detect_handoff_intent("I'd like to speak to someone") is True

    def test_real_person(self):
        assert detect_handoff_intent("Can I talk to a real person?") is True

    def test_case_insensitive(self):
        assert detect_handoff_intent("TRANSFER me now") is True

    def test_no_match(self):
        assert detect_handoff_intent("What are your business hours?") is False

    def test_empty_string(self):
        assert detect_handoff_intent("") is False


# ---------------------------------------------------------------------------
# Business hours
# ---------------------------------------------------------------------------

HOURS = {
    "mon_fri": "9am-5pm",
    "saturday": "10am-3pm",
    "sunday": "closed",
}


class TestBusinessHours:
    @patch("handoff_service.datetime")
    def test_within_weekday_hours(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 3, 9, 10, 30)  # Monday 10:30am
        mock_dt.now.return_value = mock_dt.now.return_value.replace(tzinfo=None)
        # Monday = weekday 0
        with patch("handoff_service.datetime") as mock_dt2:
            mock_now = datetime(2026, 3, 9, 10, 30)  # Monday
            mock_dt2.now.return_value = type("FakeDT", (), {"weekday": lambda self: 0, "hour": 10})()
            mock_dt2.now.return_value = mock_now
            # Use a real pytz timezone
            import pytz

            tz = pytz.timezone("America/Chicago")
            # Create a fixed datetime in business hours (Monday 10am)
            fixed = tz.localize(datetime(2026, 3, 9, 10, 30))
            with patch("handoff_service.datetime") as mdt:
                mdt.now.return_value = fixed
                assert HandoffService.is_within_business_hours(HOURS, "America/Chicago") is True

    @patch("handoff_service.datetime")
    def test_outside_weekday_hours(self, mock_dt):
        import pytz

        tz = pytz.timezone("America/Chicago")
        fixed = tz.localize(datetime(2026, 3, 9, 20, 0))  # Monday 8pm
        mock_dt.now.return_value = fixed
        assert HandoffService.is_within_business_hours(HOURS, "America/Chicago") is False

    @patch("handoff_service.datetime")
    def test_sunday_closed(self, mock_dt):
        import pytz

        tz = pytz.timezone("America/Chicago")
        fixed = tz.localize(datetime(2026, 3, 8, 12, 0))  # Sunday noon
        mock_dt.now.return_value = fixed
        assert HandoffService.is_within_business_hours(HOURS, "America/Chicago") is False

    @patch("handoff_service.datetime")
    def test_saturday_within_hours(self, mock_dt):
        import pytz

        tz = pytz.timezone("America/Chicago")
        fixed = tz.localize(datetime(2026, 3, 7, 11, 0))  # Saturday 11am
        mock_dt.now.return_value = fixed
        assert HandoffService.is_within_business_hours(HOURS, "America/Chicago") is True

    @patch("handoff_service.datetime")
    def test_saturday_outside_hours(self, mock_dt):
        import pytz

        tz = pytz.timezone("America/Chicago")
        fixed = tz.localize(datetime(2026, 3, 7, 15, 30))  # Saturday 3:30pm
        mock_dt.now.return_value = fixed
        assert HandoffService.is_within_business_hours(HOURS, "America/Chicago") is False


# ---------------------------------------------------------------------------
# TwiML generation
# ---------------------------------------------------------------------------


class TestGenerateTransferTwiml:
    def test_contains_dial(self):
        twiml = HandoffService.generate_transfer_twiml("+15551234567")
        assert "<Dial>+15551234567</Dial>" in twiml

    def test_contains_say(self):
        twiml = HandoffService.generate_transfer_twiml("+15551234567")
        assert "<Say>Transferring you now, please hold.</Say>" in twiml

    def test_wrapped_in_response(self):
        twiml = HandoffService.generate_transfer_twiml("+15551234567")
        assert twiml.startswith("<Response>")
        assert twiml.endswith("</Response>")


# ---------------------------------------------------------------------------
# Twilio transfer (mocked HTTP)
# ---------------------------------------------------------------------------


class TestInitiateTransfer:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_successful_transfer(self):
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("handoff_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await HandoffService.initiate_transfer(
                call_sid="CA123",
                fallback_number="+15551234567",
                account_sid="AC123",
                auth_token="token123",
            )
            assert result is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_failed_transfer(self):
        mock_response = AsyncMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        with patch("handoff_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await HandoffService.initiate_transfer(
                call_sid="CA123",
                fallback_number="+15551234567",
                account_sid="AC123",
                auth_token="token123",
            )
            assert result is False
