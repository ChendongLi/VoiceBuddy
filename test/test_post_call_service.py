"""Tests for PostCallService — transcript storage, AI summary, SMS confirmation."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from post_call_service import PostCallService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tenant_config():
    from tenant_config import TenantConfig

    return TenantConfig(
        tenant_id="test-tenant",
        phone_number="+15550001111",
        business_name="Test HVAC",
        system_prompt="You are a test assistant.",
        services=[],
        providers=[],
        buffer_min=15,
        cancellation_policy="",
        filler_phrases=[],
        voice_id="test-voice",
        fallback_number="",
        business_hours={},
    )


def _make_customer(phone: str = "+15559998888", name: str = "Alice"):
    customer = MagicMock()
    customer.phone_number = phone
    customer.name = name
    return customer


def _make_call(call_id: uuid.UUID | None = None):
    call = MagicMock()
    call.id = call_id or uuid.uuid4()
    call.outcome = None
    return call


def _make_transcript(call_id: uuid.UUID):
    transcript = MagicMock()
    transcript.call_id = call_id
    transcript.full_text = ""
    transcript.summary = None
    return transcript


# ---------------------------------------------------------------------------
# _generate_summary
# ---------------------------------------------------------------------------


class TestGenerateSummary:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_structured_json(self):
        summary_json = {
            "outcome": "booked",
            "appointment_details": "AC repair Jan 15 at 2pm",
            "customer_name": "Alice",
            "notes": "Prefers morning slots",
        }

        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=json.dumps(summary_json))]

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = mock_message
        mock_client.close = AsyncMock()

        with patch("post_call_service.AsyncAnthropic", return_value=mock_client):
            svc = PostCallService()
            result = await svc._generate_summary("Customer: I'd like to book an AC repair.")

        assert result["outcome"] == "booked"
        assert result["appointment_details"] == "AC repair Jan 15 at 2pm"
        assert result["customer_name"] == "Alice"
        mock_client.messages.create.assert_called_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_fallback_on_bad_json(self):
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="not valid json")]

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = mock_message
        mock_client.close = AsyncMock()

        with patch("post_call_service.AsyncAnthropic", return_value=mock_client):
            svc = PostCallService()
            result = await svc._generate_summary("some transcript")

        assert result["outcome"] == "other"
        assert result["appointment_details"] is None


# ---------------------------------------------------------------------------
# _send_sms_confirmation
# ---------------------------------------------------------------------------


class TestSendSmsConfirmation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_successful_sms(self):
        mock_response = MagicMock()
        mock_response.status_code = 201

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("post_call_service.TWILIO_ACCOUNT_SID", "AC123"),
            patch("post_call_service.TWILIO_AUTH_TOKEN", "token123"),
            patch("post_call_service.httpx.AsyncClient", return_value=mock_client),
        ):
            svc = PostCallService()
            result = await svc._send_sms_confirmation(
                to_phone="+15559998888",
                from_phone="+15550001111",
                appointment_details="AC repair Jan 15 at 2pm",
                business_name="Test HVAC",
            )

        assert result is True
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "+15559998888" in str(call_kwargs)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_failed_sms(self):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("post_call_service.TWILIO_ACCOUNT_SID", "AC123"),
            patch("post_call_service.TWILIO_AUTH_TOKEN", "token123"),
            patch("post_call_service.httpx.AsyncClient", return_value=mock_client),
        ):
            svc = PostCallService()
            result = await svc._send_sms_confirmation(
                to_phone="+15559998888",
                from_phone="+15550001111",
                appointment_details="AC repair Jan 15 at 2pm",
                business_name="Test HVAC",
            )

        assert result is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_skipped_when_no_credentials(self):
        with (
            patch("post_call_service.TWILIO_ACCOUNT_SID", ""),
            patch("post_call_service.TWILIO_AUTH_TOKEN", ""),
        ):
            svc = PostCallService()
            result = await svc._send_sms_confirmation(
                to_phone="+15559998888",
                from_phone="+15550001111",
                appointment_details="AC repair",
                business_name="Test HVAC",
            )

        assert result is False


# ---------------------------------------------------------------------------
# process (full flow)
# ---------------------------------------------------------------------------


class TestProcess:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_full_flow_with_booking(self):
        call_id = uuid.uuid4()
        tenant_config = _make_tenant_config()
        customer = _make_customer()
        call = _make_call(call_id)

        summary = {
            "outcome": "booked",
            "appointment_details": "AC repair Jan 15 at 2pm",
            "customer_name": "Alice",
            "notes": None,
        }

        # Mock DB session
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = call
        db.execute.return_value = mock_result

        svc = PostCallService()
        svc._generate_summary = AsyncMock(return_value=summary)
        svc._send_sms_confirmation = AsyncMock(return_value=True)

        await svc.process(db, call_id, "Customer: I need AC repair", tenant_config, customer)

        db.add.assert_called_once()
        db.commit.assert_called_once()
        assert call.outcome == "booked"
        svc._send_sms_confirmation.assert_called_once_with(
            to_phone=customer.phone_number,
            from_phone=tenant_config.phone_number,
            appointment_details="AC repair Jan 15 at 2pm",
            business_name="Test HVAC",
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_sms_when_outcome_is_other(self):
        call_id = uuid.uuid4()
        tenant_config = _make_tenant_config()
        customer = _make_customer()
        call = _make_call(call_id)

        summary = {
            "outcome": "other",
            "appointment_details": None,
            "customer_name": None,
            "notes": None,
        }

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = call
        db.execute.return_value = mock_result

        svc = PostCallService()
        svc._generate_summary = AsyncMock(return_value=summary)
        svc._send_sms_confirmation = AsyncMock()

        await svc.process(db, call_id, "Just a general inquiry", tenant_config, customer)

        svc._send_sms_confirmation.assert_not_called()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_sms_when_no_customer(self):
        call_id = uuid.uuid4()
        tenant_config = _make_tenant_config()
        call = _make_call(call_id)

        summary = {
            "outcome": "booked",
            "appointment_details": "AC repair Jan 15",
            "customer_name": None,
            "notes": None,
        }

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = call
        db.execute.return_value = mock_result

        svc = PostCallService()
        svc._generate_summary = AsyncMock(return_value=summary)
        svc._send_sms_confirmation = AsyncMock()

        await svc.process(db, call_id, "Booking call", tenant_config, None)

        svc._send_sms_confirmation.assert_not_called()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rollback_on_error(self):
        call_id = uuid.uuid4()
        tenant_config = _make_tenant_config()

        db = AsyncMock()
        db.flush.side_effect = Exception("DB error")

        svc = PostCallService()

        await svc.process(db, call_id, "transcript", tenant_config, None)

        db.rollback.assert_called_once()
