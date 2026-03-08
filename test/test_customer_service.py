"""Tests for CustomerService and CallService using SQLite in-memory DB."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Appointment, Base, Customer, Tenant
from customer_service import CustomerService
from call_service import CallService


@pytest_asyncio.fixture
async def db_session():
    """Create an in-memory SQLite database and return an async session."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # SQLite needs PRAGMA foreign_keys to enforce FK constraints
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        # Seed a tenant
        tenant = Tenant(
            id="test-tenant",
            phone_number="+15551234567",
            business_name="Test Business",
        )
        session.add(tenant)
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture
def customer_svc():
    return CustomerService()


@pytest.fixture
def call_svc():
    return CallService()


class TestCustomerService:
    @pytest.mark.asyncio
    async def test_get_or_create_new(self, db_session, customer_svc):
        customer, is_new = await customer_svc.get_or_create(
            db_session, "test-tenant", "+15559999999"
        )
        assert is_new is True
        assert customer.phone_number == "+15559999999"
        assert customer.tenant_id == "test-tenant"
        assert customer.name is None

    @pytest.mark.asyncio
    async def test_get_or_create_existing(self, db_session, customer_svc):
        c1, new1 = await customer_svc.get_or_create(db_session, "test-tenant", "+15559999999")
        c2, new2 = await customer_svc.get_or_create(db_session, "test-tenant", "+15559999999")
        assert new1 is True
        assert new2 is False
        assert c1.id == c2.id

    @pytest.mark.asyncio
    async def test_update_profile(self, db_session, customer_svc):
        customer, _ = await customer_svc.get_or_create(db_session, "test-tenant", "+15559999999")
        updated = await customer_svc.update_profile(
            db_session, customer.id, name="Sarah", email="sarah@example.com"
        )
        assert updated.name == "Sarah"
        assert updated.email == "sarah@example.com"

    @pytest.mark.asyncio
    async def test_update_profile_partial(self, db_session, customer_svc):
        customer, _ = await customer_svc.get_or_create(db_session, "test-tenant", "+15559999999")
        await customer_svc.update_profile(db_session, customer.id, name="Sarah")
        updated = await customer_svc.update_profile(
            db_session, customer.id, contact_pref="sms"
        )
        assert updated.name == "Sarah"
        assert updated.contact_pref == "sms"

    @pytest.mark.asyncio
    async def test_get_upcoming_appointment_none(self, db_session, customer_svc):
        customer, _ = await customer_svc.get_or_create(db_session, "test-tenant", "+15559999999")
        apt = await customer_svc.get_upcoming_appointment(db_session, customer.id)
        assert apt is None

    @pytest.mark.asyncio
    async def test_get_upcoming_appointment(self, db_session, customer_svc):
        customer, _ = await customer_svc.get_or_create(db_session, "test-tenant", "+15559999999")
        future = datetime.now(timezone.utc) + timedelta(days=3)
        apt = Appointment(
            id=uuid.uuid4(),
            tenant_id="test-tenant",
            customer_id=customer.id,
            service_name="Cleaning",
            provider_name="Dr. Smith",
            starts_at=future,
            duration_min=60,
            status="confirmed",
        )
        db_session.add(apt)
        await db_session.commit()

        result = await customer_svc.get_upcoming_appointment(db_session, customer.id)
        assert result is not None
        assert result.service_name == "Cleaning"

    @pytest.mark.asyncio
    async def test_get_upcoming_skips_past(self, db_session, customer_svc):
        customer, _ = await customer_svc.get_or_create(db_session, "test-tenant", "+15559999999")
        past = datetime.now(timezone.utc) - timedelta(days=1)
        apt = Appointment(
            id=uuid.uuid4(),
            tenant_id="test-tenant",
            customer_id=customer.id,
            service_name="Old",
            starts_at=past,
            duration_min=30,
            status="confirmed",
        )
        db_session.add(apt)
        await db_session.commit()

        result = await customer_svc.get_upcoming_appointment(db_session, customer.id)
        assert result is None

    def test_build_context_new_customer(self, customer_svc):
        customer = Customer(tenant_id="t", phone_number="+15559999999")
        context = customer_svc.build_customer_context(customer, None)
        assert "New customer" in context
        assert "collect name" in context

    def test_build_context_returning_no_appointment(self, customer_svc):
        customer = Customer(tenant_id="t", phone_number="+15559999999", name="Sarah")
        context = customer_svc.build_customer_context(customer, None)
        assert "Returning customer: Sarah" in context

    def test_build_context_returning_with_appointment(self, customer_svc):
        customer = Customer(tenant_id="t", phone_number="+15559999999", name="Sarah")
        apt = Appointment(
            tenant_id="t",
            customer_id=uuid.uuid4(),
            starts_at=datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
            service_name="Cleaning",
            provider_name="Dr. Smith",
            duration_min=60,
        )
        context = customer_svc.build_customer_context(customer, apt)
        assert "Returning customer: Sarah" in context
        assert "Jan 15" in context
        assert "Cleaning" in context
        assert "Dr. Smith" in context


class TestCallService:
    @pytest.mark.asyncio
    async def test_start_call(self, db_session, call_svc, customer_svc):
        customer, _ = await customer_svc.get_or_create(db_session, "test-tenant", "+15559999999")
        call = await call_svc.start_call(
            db_session, "test-tenant", customer.id, "CA123", "+15559999999"
        )
        assert call.tenant_id == "test-tenant"
        assert call.customer_id == customer.id
        assert call.twilio_call_sid == "CA123"
        assert call.outcome is None

    @pytest.mark.asyncio
    async def test_end_call(self, db_session, call_svc, customer_svc):
        customer, _ = await customer_svc.get_or_create(db_session, "test-tenant", "+15559999999")
        call = await call_svc.start_call(
            db_session, "test-tenant", customer.id, "CA456", "+15559999999"
        )
        ended = await call_svc.end_call(db_session, call.id, "completed", 120)
        assert ended.outcome == "completed"
        assert ended.duration_sec == 120
        assert ended.ended_at is not None
