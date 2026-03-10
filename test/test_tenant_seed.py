"""Tests for sync_tenants_to_db — auto-seeding tenant rows from YAML registry."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models import Base, Tenant
from tenant_config import TenantConfig, TenantRegistry


def _make_config(tenant_id: str, phone: str, name: str) -> TenantConfig:
    """Create a minimal TenantConfig for testing."""
    return TenantConfig(
        tenant_id=tenant_id,
        phone_number=phone,
        business_name=name,
        system_prompt="test",
        services=[],
        providers=[],
        buffer_min=15,
        cancellation_policy="24h",
        filler_phrases=["one moment"],
        voice_id="test-voice",
        fallback_number="+10000000000",
        business_hours={"mon_fri": "9am-5pm", "saturday": "closed", "sunday": "closed"},
        timezone="UTC",
    )


@pytest_asyncio.fixture
async def db_parts():
    """Create in-memory SQLite engine + session factory, return (engine, session_factory)."""
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(test_engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    yield test_engine, factory
    await test_engine.dispose()


async def _sync(test_engine, factory, registry: TenantRegistry) -> None:
    """Run the same upsert logic as server.sync_tenants_to_db using test DB."""
    from sqlalchemy.dialects.sqlite import insert

    async with factory() as db:
        for cfg in registry.all_tenants:
            stmt = (
                insert(Tenant)
                .values(
                    id=cfg.tenant_id,
                    phone_number=cfg.phone_number,
                    business_name=cfg.business_name,
                    config_path=f"tenants/{cfg.tenant_id}.yaml",
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "phone_number": cfg.phone_number,
                        "business_name": cfg.business_name,
                    },
                )
            )
            await db.execute(stmt)
        await db.commit()


@pytest.mark.asyncio
async def test_sync_inserts_tenants(db_parts):
    """sync_tenants_to_db inserts tenant rows from the registry."""
    test_engine, factory = db_parts

    registry = TenantRegistry.__new__(TenantRegistry)
    cfg = _make_config("acme", "+15550001111", "Acme Corp")
    registry._by_id = {"acme": cfg}
    registry._by_phone = {cfg.phone_number: cfg}

    await _sync(test_engine, factory, registry)

    async with factory() as db:
        rows = (await db.execute(select(Tenant))).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == "acme"
        assert rows[0].business_name == "Acme Corp"
        assert rows[0].phone_number == "+15550001111"
        assert rows[0].config_path == "tenants/acme.yaml"


@pytest.mark.asyncio
async def test_sync_is_idempotent(db_parts):
    """Calling sync twice with the same data does not error or duplicate rows."""
    test_engine, factory = db_parts

    registry = TenantRegistry.__new__(TenantRegistry)
    cfg = _make_config("acme", "+15550001111", "Acme Corp")
    registry._by_id = {"acme": cfg}
    registry._by_phone = {cfg.phone_number: cfg}

    await _sync(test_engine, factory, registry)
    await _sync(test_engine, factory, registry)

    async with factory() as db:
        rows = (await db.execute(select(Tenant))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_sync_updates_on_conflict(db_parts):
    """When a tenant already exists, sync updates business_name and phone_number."""
    test_engine, factory = db_parts

    registry = TenantRegistry.__new__(TenantRegistry)
    cfg1 = _make_config("acme", "+15550001111", "Acme Corp")
    registry._by_id = {"acme": cfg1}
    registry._by_phone = {cfg1.phone_number: cfg1}

    await _sync(test_engine, factory, registry)

    cfg2 = _make_config("acme", "+15559999999", "Acme Industries")
    registry._by_id = {"acme": cfg2}
    registry._by_phone = {cfg2.phone_number: cfg2}

    await _sync(test_engine, factory, registry)

    async with factory() as db:
        row = (await db.execute(select(Tenant).where(Tenant.id == "acme"))).scalar_one()
        assert row.business_name == "Acme Industries"
        assert row.phone_number == "+15559999999"
