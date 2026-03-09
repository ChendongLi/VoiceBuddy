"""Tests for CalendarService with mocked Google API calls."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from calendar_service import CalendarService


@pytest.fixture
def token_dir(tmp_path: Path) -> Path:
    """Create a temp tokens dir with a fake credential file."""
    tokens = tmp_path / "tokens"
    tokens.mkdir()
    # Minimal credential JSON that google.oauth2.credentials can parse
    cred_data = {
        "token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "client_id": "fake-client-id",
        "client_secret": "fake-client-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    import json

    (tokens / "test_tenant.json").write_text(json.dumps(cred_data))
    return tokens


@pytest.fixture
def service(token_dir: Path) -> CalendarService:
    return CalendarService(tokens_dir=token_dir)


# ------------------------------------------------------------------
# get_credentials
# ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_get_credentials_returns_creds(service: CalendarService) -> None:
    with patch("calendar_service.Credentials.from_authorized_user_file") as mock_from_file:
        mock_creds = MagicMock()
        mock_creds.expired = False
        mock_from_file.return_value = mock_creds

        creds = await service.get_credentials("test_tenant")
        assert creds is mock_creds


@pytest.mark.asyncio(loop_scope="function")
async def test_get_credentials_missing_tenant(service: CalendarService) -> None:
    # When no tenant token AND no shared SA key, returns None
    with patch("calendar_service.SA_KEY_PATH") as mock_sa_path:
        mock_sa_path.exists.return_value = False
        creds = await service.get_credentials("nonexistent")
    assert creds is None


@pytest.mark.asyncio(loop_scope="function")
async def test_get_credentials_refreshes_expired(service: CalendarService) -> None:
    with patch("calendar_service.Credentials.from_authorized_user_file") as mock_from_file:
        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.refresh_token = "fake-refresh"
        mock_creds.to_json.return_value = '{"token": "refreshed"}'
        mock_from_file.return_value = mock_creds

        creds = await service.get_credentials("test_tenant")
        mock_creds.refresh.assert_called_once()
        assert creds is mock_creds


# ------------------------------------------------------------------
# list_available_slots
# ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_list_available_slots_empty_calendar(service: CalendarService) -> None:
    mock_creds = MagicMock()
    mock_creds.expired = False

    freebusy_response = {"calendars": {"cal@example.com": {"busy": []}}}

    with (
        patch.object(service, "get_credentials", return_value=mock_creds),
        patch("calendar_service.build") as mock_build,
    ):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.freebusy().query().execute.return_value = freebusy_response

        slots = await service.list_available_slots(
            tenant_id="test_tenant",
            calendar_id="cal@example.com",
            date_=date(2026, 3, 10),
            duration_min=60,
            buffer_min=15,
        )

    assert len(slots) > 0
    assert "start" in slots[0]
    assert "end" in slots[0]
    assert "display" in slots[0]


@pytest.mark.asyncio(loop_scope="function")
async def test_list_available_slots_with_busy(service: CalendarService) -> None:
    mock_creds = MagicMock()
    mock_creds.expired = False

    freebusy_response = {
        "calendars": {
            "cal@example.com": {
                "busy": [
                    {
                        "start": "2026-03-10T08:00:00+00:00",
                        "end": "2026-03-10T12:00:00+00:00",
                    }
                ]
            }
        }
    }

    with (
        patch.object(service, "get_credentials", return_value=mock_creds),
        patch("calendar_service.build") as mock_build,
    ):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.freebusy().query().execute.return_value = freebusy_response

        slots = await service.list_available_slots(
            tenant_id="test_tenant",
            calendar_id="cal@example.com",
            date_=date(2026, 3, 10),
            duration_min=60,
            buffer_min=15,
        )

    # Morning is fully booked (8-12), so first slot should be after 12:15
    for slot in slots:
        assert slot["start"] >= "2026-03-10T12:15:00"


@pytest.mark.asyncio(loop_scope="function")
async def test_list_available_slots_no_creds(service: CalendarService) -> None:
    with patch.object(service, "get_credentials", return_value=None):
        slots = await service.list_available_slots(
            tenant_id="missing",
            calendar_id="cal@example.com",
            date_=date(2026, 3, 10),
            duration_min=60,
        )
    assert slots == []


# ------------------------------------------------------------------
# create_event
# ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_create_event(service: CalendarService) -> None:
    mock_creds = MagicMock()
    mock_creds.expired = False

    with (
        patch.object(service, "get_credentials", return_value=mock_creds),
        patch("calendar_service.build") as mock_build,
    ):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.events().insert().execute.return_value = {"id": "evt_123"}

        event_id = await service.create_event(
            tenant_id="test_tenant",
            calendar_id="cal@example.com",
            summary="HVAC Repair",
            start_dt=datetime(2026, 3, 10, 10, 0, tzinfo=UTC),
            end_dt=datetime(2026, 3, 10, 11, 0, tzinfo=UTC),
            attendee_email="customer@example.com",
            description="Annual maintenance",
        )

    assert event_id == "evt_123"


@pytest.mark.asyncio(loop_scope="function")
async def test_create_event_no_creds(service: CalendarService) -> None:
    with (
        patch.object(service, "get_credentials", return_value=None),
        pytest.raises(RuntimeError, match="No credentials"),
    ):
        await service.create_event(
            tenant_id="missing",
            calendar_id="cal@example.com",
            summary="Test",
            start_dt=datetime(2026, 3, 10, 10, 0, tzinfo=UTC),
            end_dt=datetime(2026, 3, 10, 11, 0, tzinfo=UTC),
        )


# ------------------------------------------------------------------
# cancel_event
# ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="function")
async def test_cancel_event_success(service: CalendarService) -> None:
    mock_creds = MagicMock()
    mock_creds.expired = False

    with (
        patch.object(service, "get_credentials", return_value=mock_creds),
        patch("calendar_service.build") as mock_build,
    ):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.events().delete().execute.return_value = None

        result = await service.cancel_event(
            tenant_id="test_tenant",
            calendar_id="cal@example.com",
            event_id="evt_123",
        )

    assert result is True


@pytest.mark.asyncio(loop_scope="function")
async def test_cancel_event_failure(service: CalendarService) -> None:
    mock_creds = MagicMock()
    mock_creds.expired = False

    with (
        patch.object(service, "get_credentials", return_value=mock_creds),
        patch("calendar_service.build") as mock_build,
    ):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        mock_service.events().delete().execute.side_effect = Exception("API error")

        result = await service.cancel_event(
            tenant_id="test_tenant",
            calendar_id="cal@example.com",
            event_id="evt_123",
        )

    assert result is False
