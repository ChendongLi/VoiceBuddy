"""Customer profile service — caller ID lookup + new vs returning flow."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Appointment, Customer


class CustomerService:
    async def get_or_create(self, db: AsyncSession, tenant_id: str, phone: str) -> tuple[Customer, bool]:
        """Lookup customer by tenant+phone. Returns (customer, is_new). Creates if not found."""
        stmt = select(Customer).where(
            Customer.tenant_id == tenant_id,
            Customer.phone_number == phone,
        )
        result = await db.execute(stmt)
        customer = result.scalar_one_or_none()

        if customer:
            return customer, False

        customer = Customer(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            phone_number=phone,
        )
        db.add(customer)
        await db.commit()
        await db.refresh(customer)
        return customer, True

    async def update_profile(
        self,
        db: AsyncSession,
        customer_id: uuid.UUID,
        name: str | None = None,
        email: str | None = None,
        contact_pref: str | None = None,
    ) -> Customer:
        """Update customer fields after info collected during call."""
        stmt = select(Customer).where(Customer.id == customer_id)
        result = await db.execute(stmt)
        customer = result.scalar_one()

        if name is not None:
            customer.name = name
        if email is not None:
            customer.email = email
        if contact_pref is not None:
            customer.contact_pref = contact_pref

        await db.commit()
        await db.refresh(customer)
        return customer

    async def get_upcoming_appointment(self, db: AsyncSession, customer_id: uuid.UUID) -> Appointment | None:
        """Return next upcoming confirmed appointment for customer."""
        now = datetime.now(UTC)
        stmt = (
            select(Appointment)
            .where(
                Appointment.customer_id == customer_id,
                Appointment.status == "confirmed",
                Appointment.starts_at > now,
            )
            .order_by(Appointment.starts_at.asc())
            .limit(1)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    def build_customer_context(self, customer: Customer, appointment: Appointment | None) -> str:
        """Build a context string to inject into the LLM system prompt."""
        if customer.name:
            # Returning customer
            parts = [f"Returning customer: {customer.name}"]
            if appointment:
                apt_date = appointment.starts_at.strftime("%b %d")
                service = appointment.service_name or "appointment"
                if appointment.provider_name:
                    parts.append(f"(next apt: {apt_date} {service} with {appointment.provider_name})")
                else:
                    parts.append(f"(next apt: {apt_date} {service})")
            return " ".join(parts)

        return "New customer — collect name and email during conversation."
