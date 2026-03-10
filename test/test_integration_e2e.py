"""
End-to-end integration tests for VoiceBuddy production stack.

Marked `integration` so they are excluded from the default unit-test run
(see pyproject.toml: addopts = "-m 'not integration'").

Run manually:
    # Against local server (needs .env with local DATABASE_URL):
    PYTHONPATH=src pytest test/test_integration_e2e.py -m integration -v

    # Against production Neon DB:
    DATABASE_URL="postgresql+asyncpg://..." \
    PYTHONPATH=src pytest test/test_integration_e2e.py -m integration -v

Checks:
  1. HTTP /health endpoint → 200 + status=ok
  2. Neon/PostgreSQL DB connection + basic query
  3. Google Calendar list_available_slots (real API)
  4. Full LLM booking flow: Claude → check_availability → book_appointment
  5. Calendar event visible after booking
  6. Cleanup: test event deleted
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PROD_DB_URL = (
    "postgresql+asyncpg://neondb_owner:npg_eK4d5BqVyXYT"
    "@ep-divine-sky-ak4xlwko-pooler.c-3.us-west-2.aws.neon.tech"
    "/neondb?ssl=require"
)
PROD_HOST = "https://voicebuddy.agentlens.net"
TENANT_PHONE = "+13185688982"
CALENDAR_ID = "lichendong@gmail.com"
TENANT_ID = "coolbreeze_hvac"


@pytest.fixture(scope="module", autouse=True)
def set_prod_db_url():
    """Use production Neon DB unless DATABASE_URL is already set."""
    if not os.environ.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = PROD_DB_URL
    yield


@pytest.fixture(scope="module")
def tenant():
    from tenant_config import TenantRegistry

    registry = TenantRegistry()
    return registry.get_by_phone(TENANT_PHONE)


@pytest.fixture(scope="module")
def calendar_service():
    from calendar_service import CalendarService

    return CalendarService()


@pytest.fixture(scope="module")
def tomorrow() -> date:
    return (datetime.now() + timedelta(days=1)).date()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_endpoint():
    """GET /health returns 200 and status=ok."""
    import httpx

    async with httpx.AsyncClient() as client:
        r = await client.get(f"{PROD_HOST}/health", timeout=10)

    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    body = r.json()
    assert body.get("status") == "ok", f"Unexpected body: {body}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_database_connection():
    """Can connect to Neon PostgreSQL and query the customers table."""
    from sqlalchemy import text

    from database import async_session

    async with async_session() as db:
        result = await db.execute(text("SELECT count(*) FROM customers"))
        count = result.scalar()

    assert isinstance(count, int)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_calendar_list_available_slots(calendar_service, tomorrow):
    """list_available_slots returns at least one slot in tenant timezone."""
    slots = await calendar_service.list_available_slots(
        tenant_id=TENANT_ID,
        calendar_id=CALENDAR_ID,
        date_=tomorrow,
        duration_min=60,
        buffer_min=15,
        timezone="America/Vancouver",
    )

    assert isinstance(slots, list)
    assert len(slots) > 0, "Expected at least one available slot"
    # Slot start must be a tz-aware ISO string in local time (not midnight UTC)
    first = slots[0]
    start_dt = datetime.fromisoformat(first["start"])
    assert start_dt.tzinfo is not None, "Slot start must be tz-aware"
    # 8 AM local = start of day window; should not be midnight
    assert start_dt.hour >= 8, f"Slot starts too early: {start_dt}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_llm_booking_flow_and_calendar_event(calendar_service, tenant, tomorrow):
    """
    Full booking flow:
      - LLMOrchestrator processes a booking request
      - Claude calls check_availability then book_appointment
      - Event appears on Google Calendar with correct local time
      - Event is deleted after the test (cleanup)
    """
    from googleapiclient.discovery import build

    from booking_service import BookingService
    from booking_tools import BOOKING_TOOLS
    from llm_orchestrator import LLMOrchestrator

    booking_svc = BookingService(
        calendar_service=calendar_service,
        tenant_config=tenant,
        db=AsyncMock(),
    )
    llm = LLMOrchestrator()
    llm.configure_booking(booking_svc, BOOKING_TOOLS, uuid.uuid4())
    llm.system_prompt_extra = (
        "\n\nYou have calendar booking tools. When customer provides name, phone, address, "
        "and desired time, call check_availability then book_appointment immediately. "
        "Do NOT ask for more info if you already have enough."
    )

    replies: list[str] = []
    llm.on_full_ready = lambda text, _: replies.append(text)

    date_str = tomorrow.strftime("%Y-%m-%d")
    await llm.process_turn(
        f"Book an AC tune-up for tomorrow ({date_str}) at 10 AM. "
        "Name: E2E Test, phone: 604-555-0000, address: 123 Test St Richmond."
    )

    reply = replies[-1] if replies else ""
    assert any(
        w in reply.lower() for w in ["confirmed", "booked", "all set", "appointment id"]
    ), f"Expected booking confirmation, got: {reply!r}"

    # Verify event on Google Calendar
    creds = await calendar_service.get_credentials(TENANT_ID)
    svc = build("calendar", "v3", credentials=creds)
    now_utc = datetime.now(UTC)
    result = (
        svc.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            timeMax=(now_utc + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            maxResults=10,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    test_events = [e for e in result.get("items", []) if "E2E Test" in e.get("summary", "")]

    assert len(test_events) > 0, "Expected test event on calendar, found none"

    # Verify local timezone (should be 10 AM Pacific, not midnight/UTC offset)
    event_start = test_events[0]["start"].get("dateTime", "")
    assert "T10:00:00" in event_start, f"Expected 10 AM local time in event start, got: {event_start}"

    # Cleanup
    for e in test_events:
        svc.events().delete(calendarId=CALENDAR_ID, eventId=e["id"]).execute()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_booking_pipeline_creates_calendar_event(tomorrow):
    """
    Mirrors the production Twilio call flow in server.py:
      - Load tenant config from YAML via TenantRegistry
      - Wire CalendarService + BookingService + LLMOrchestrator exactly as server.py does
      - Process a booking utterance and verify calendar event creation
    """
    from googleapiclient.discovery import build

    from booking_service import BookingService
    from booking_tools import BOOKING_TOOLS
    from calendar_service import CalendarService
    from llm_orchestrator import LLMOrchestrator
    from tenant_config import TenantRegistry

    # 1. Load tenant from YAML (mirrors production registry lookup)
    registry = TenantRegistry()
    tenant_cfg = registry.get_by_phone(TENANT_PHONE)
    assert tenant_cfg is not None, f"Tenant not found for {TENANT_PHONE}"
    assert tenant_cfg.tenant_id == TENANT_ID

    # 2. Set up services exactly as server.py start event handler does
    calendar_svc = CalendarService()
    booking_svc = BookingService(
        calendar_service=calendar_svc,
        tenant_config=tenant_cfg,
        db=AsyncMock(),
    )

    # 3. Configure LLM with booking tools
    customer_id = uuid.uuid4()
    llm = LLMOrchestrator()
    llm.configure_booking(booking_svc, BOOKING_TOOLS, customer_id)

    # 4. Set booking instruction (same as server.py)
    llm.system_prompt_extra = (
        "\n\nYou have access to booking tools: check_availability, book_appointment, "
        "cancel_appointment, reschedule_appointment. "
        "Use them proactively when the customer wants to schedule, cancel, or reschedule a service."
    )

    # 5. Process booking — two turns (mirrors real call: ask → confirm slot)
    replies: list[str] = []
    llm.on_full_ready = lambda text, _: replies.append(text)

    date_str = tomorrow.strftime("%Y-%m-%d")
    await llm.process_turn(
        f"I need an AC tune-up tomorrow morning ({date_str}). "
        "My name is Test User, phone 555-000-1234, address 123 Test St."
    )

    # LLM may ask which slot — confirm the earliest one
    await llm.process_turn("The earliest available slot works great, please book it.")

    # 6. Assert booking confirmation in either turn's reply
    all_replies = " ".join(replies).lower()
    assert any(
        w in all_replies for w in ["confirmed", "booked", "scheduled", "appointment", "all set", "you're set"]
    ), f"Expected booking confirmation in replies, got: {replies!r}"

    # 7. Verify calendar event was created
    creds = await calendar_svc.get_credentials(TENANT_ID)
    svc = build("calendar", "v3", credentials=creds)
    now_utc = datetime.now(UTC)
    result = (
        svc.events()
        .list(
            calendarId=CALENDAR_ID,
            timeMin=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            timeMax=(now_utc + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            maxResults=10,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    test_events = [e for e in result.get("items", []) if "Test User" in e.get("summary", "")]
    assert len(test_events) > 0, "Expected 'Test User' event on calendar, found none"

    # 8. Cleanup: delete test events
    for e in test_events:
        svc.events().delete(calendarId=CALENDAR_ID, eventId=e["id"]).execute()
