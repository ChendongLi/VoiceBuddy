"""Tests for BookingService with mocked CalendarService and in-memory DB."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from booking_service import BookingService
from models import Appointment, Base, Customer, Tenant
from tenant_config import TenantConfig

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

TENANT_CFG = TenantConfig(
    tenant_id="test_tenant",
    phone_number="+10000000000",
    business_name="Test HVAC",
    system_prompt="You are a test assistant.",
    services=[
        {"name": "repair", "duration_min": 60},
        {"name": "installation", "duration_min": 120},
    ],
    providers=[{"name": "Test Tech", "calendar_id": "tech@example.com"}],
    buffer_min=15,
    cancellation_policy="24 hours notice.",
    filler_phrases=["One sec."],
    voice_id="test-voice",
    fallback_number="+10000000000",
    business_hours={"mon_fri": "8am-6pm", "saturday": "9am-2pm", "sunday": "closed"},
    timezone="America/Vancouver",
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        # Seed tenant + customer
        tenant = Tenant(id="test_tenant", phone_number="+10000000000", business_name="Test HVAC")
        session.add(tenant)
        customer = Customer(
            id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            tenant_id="test_tenant",
            phone_number="+15551234567",
            name="Jane Doe",
        )
        session.add(customer)
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture
def mock_calendar():
    cal = AsyncMock()
    cal.list_available_slots = AsyncMock(
        return_value=[
            {
                "start": "2026-03-10T09:00:00+00:00",
                "end": "2026-03-10T10:00:00+00:00",
                "display": "Tuesday March 10 at 9am",
            },
            {
                "start": "2026-03-10T11:00:00+00:00",
                "end": "2026-03-10T12:00:00+00:00",
                "display": "Tuesday March 10 at 11am",
            },
        ]
    )
    cal.create_event = AsyncMock(return_value="gcal_event_123")
    cal.cancel_event = AsyncMock(return_value=True)
    return cal


@pytest.fixture
def customer_id():
    return uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest_asyncio.fixture
async def svc(db_session, mock_calendar):
    return BookingService(
        calendar_service=mock_calendar,
        tenant_config=TENANT_CFG,
        db=db_session,
    )


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestCheckAvailability:
    @pytest.mark.asyncio
    async def test_returns_slots(self, svc, customer_id):
        result = await svc.check_availability("2026-03-10", "repair")
        assert "Tuesday March 10 at 9am" in result
        assert "Tuesday March 10 at 11am" in result

    @pytest.mark.asyncio
    async def test_unknown_service(self, svc):
        result = await svc.check_availability("2026-03-10", "plumbing")
        assert "not found" in result.lower()
        assert "repair" in result

    @pytest.mark.asyncio
    async def test_invalid_date(self, svc):
        result = await svc.check_availability("not-a-date", "repair")
        assert "Invalid date" in result

    @pytest.mark.asyncio
    async def test_no_slots(self, svc, mock_calendar):
        mock_calendar.list_available_slots.return_value = []
        result = await svc.check_availability("2026-03-10", "repair")
        assert "No available slots" in result


class TestBookAppointment:
    @pytest.mark.asyncio
    async def test_success(self, svc, mock_calendar, customer_id, db_session):
        result = await svc.book_appointment(
            customer_id=customer_id,
            date_str="2026-03-10",
            time_str="09:00",
            service_name="repair",
            customer_name="Jane Doe",
        )
        assert "confirmed" in result.lower()
        assert "Appointment ID:" in result
        mock_calendar.create_event.assert_called_once()

        # Verify DB record
        from sqlalchemy import select

        stmt = select(Appointment).where(Appointment.customer_id == customer_id)
        row = await db_session.execute(stmt)
        apt = row.scalar_one()
        assert apt.service_name == "repair"
        assert apt.status == "confirmed"
        assert apt.google_event_id == "gcal_event_123"

    @pytest.mark.asyncio
    async def test_unknown_service(self, svc, customer_id):
        result = await svc.book_appointment(
            customer_id=customer_id,
            date_str="2026-03-10",
            time_str="09:00",
            service_name="plumbing",
        )
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_datetime(self, svc, customer_id):
        result = await svc.book_appointment(
            customer_id=customer_id,
            date_str="bad",
            time_str="bad",
            service_name="repair",
        )
        assert "Invalid date/time" in result


class TestCancelAppointment:
    @pytest.mark.asyncio
    async def test_cancel_success(self, svc, mock_calendar, customer_id, db_session):
        # First book one
        await svc.book_appointment(
            customer_id=customer_id,
            date_str="2026-03-10",
            time_str="09:00",
            service_name="repair",
        )
        from sqlalchemy import select

        stmt = select(Appointment).where(Appointment.customer_id == customer_id)
        row = await db_session.execute(stmt)
        apt = row.scalar_one()

        result = await svc.cancel_appointment(str(apt.id))
        assert "cancelled" in result.lower()
        mock_calendar.cancel_event.assert_called_once()

        await db_session.refresh(apt)
        assert apt.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_not_found(self, svc):
        result = await svc.cancel_appointment(str(uuid.uuid4()))
        assert "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_cancel_already_cancelled(self, svc, customer_id, db_session):
        await svc.book_appointment(
            customer_id=customer_id,
            date_str="2026-03-10",
            time_str="09:00",
            service_name="repair",
        )
        from sqlalchemy import select

        stmt = select(Appointment).where(Appointment.customer_id == customer_id)
        row = await db_session.execute(stmt)
        apt = row.scalar_one()
        await svc.cancel_appointment(str(apt.id))

        result = await svc.cancel_appointment(str(apt.id))
        assert "already cancelled" in result.lower()


class TestRescheduleAppointment:
    @pytest.mark.asyncio
    async def test_reschedule_success(self, svc, mock_calendar, customer_id, db_session):
        await svc.book_appointment(
            customer_id=customer_id,
            date_str="2026-03-10",
            time_str="09:00",
            service_name="repair",
        )
        from sqlalchemy import select

        stmt = select(Appointment).where(Appointment.customer_id == customer_id)
        row = await db_session.execute(stmt)
        apt = row.scalar_one()

        result = await svc.reschedule_appointment(str(apt.id), "2026-03-12", "14:00")
        assert "rescheduled" in result.lower()
        # cancel_event called once for old, create_event called twice total
        assert mock_calendar.cancel_event.call_count == 1
        assert mock_calendar.create_event.call_count == 2

        await db_session.refresh(apt)
        assert apt.starts_at.hour == 14
        assert apt.starts_at.day == 12

    @pytest.mark.asyncio
    async def test_reschedule_not_found(self, svc):
        result = await svc.reschedule_appointment(str(uuid.uuid4()), "2026-03-12", "14:00")
        assert "not found" in result.lower()


class TestHandleToolCall:
    @pytest.mark.asyncio
    async def test_dispatch_check_availability(self, svc, customer_id):
        result = await svc.handle_tool_call(
            "check_availability",
            {"date": "2026-03-10", "service_name": "repair"},
            customer_id,
        )
        assert "9am" in result

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool(self, svc, customer_id):
        result = await svc.handle_tool_call("unknown_tool", {}, customer_id)
        assert "Unknown tool" in result
