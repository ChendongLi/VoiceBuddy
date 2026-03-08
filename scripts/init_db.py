#!/usr/bin/env python3
"""Run Alembic migrations and seed the CoolBreeze HVAC tenant."""

import asyncio
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import async_session  # noqa: E402
from src.models import Tenant  # noqa: E402

TENANT = Tenant(
    id="coolbreeze",
    phone_number="+15551234567",
    business_name="Cool Breeze HVAC",
    config_path="tenants/coolbreeze.yaml",
)


async def seed() -> None:
    async with async_session() as session:
        existing = await session.execute(select(Tenant).where(Tenant.id == TENANT.id))
        if existing.scalar_one_or_none() is None:
            session.add(TENANT)
            await session.commit()
            print(f"Seeded tenant: {TENANT.business_name}")
        else:
            print(f"Tenant already exists: {TENANT.business_name}")


def main() -> None:
    project_root = Path(__file__).resolve().parent.parent
    print("Running alembic upgrade head ...")
    subprocess.run(["alembic", "upgrade", "head"], cwd=project_root, check=True)
    print("Seeding database ...")
    asyncio.run(seed())
    print("Done.")


if __name__ == "__main__":
    main()
