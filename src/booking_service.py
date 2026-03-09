"""Booking engine — handles tool calls from Claude to manage appointments."""

from __future__ import annotations

import logging
import uuid
import zoneinfo
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from calendar_service import CalendarService
from models import Appointment
from tenant_config import TenantConfig

logger = logging.getLogger("voicebuddy.booking_service")


class BookingService:
    """Dispatches Claude tool calls to calendar + DB operations."""

    def __init__(
        self,
        calendar_service: CalendarService,
        tenant_config: TenantConfig,
        db: AsyncSession,
    ) -> None:
        self.calendar = calendar_service
        self.tenant = tenant_config
        self.db = db

    async def handle_tool_call(self, tool_name: str, tool_input: dict, customer_id: uuid.UUID) -> str:
        """Dispatch a tool call from Claude. Returns a plain-English result string."""
        handlers = {
            "check_availability": lambda: self.check_availability(
                tool_input["date"],
                tool_input["service_name"],
                tool_input.get("provider_name"),
            ),
            "book_appointment": lambda: self.book_appointment(
                customer_id=customer_id,
                date_str=tool_input["date"],
                time_str=tool_input["time"],
                service_name=tool_input["service_name"],
                provider_name=tool_input.get("provider_name"),
                customer_name=tool_input.get("customer_name"),
                customer_email=tool_input.get("customer_email"),
                notes=tool_input.get("notes", ""),
            ),
            "cancel_appointment": lambda: self.cancel_appointment(tool_input["appointment_id"]),
            "reschedule_appointment": lambda: self.reschedule_appointment(
                tool_input["appointment_id"],
                tool_input["new_date"],
                tool_input["new_time"],
            ),
        }
        handler = handlers.get(tool_name)
        if not handler:
            return f"Unknown tool: {tool_name}"
        try:
            return await handler()
        except Exception:
            logger.exception("Tool call %s failed", tool_name)
            return f"Sorry, something went wrong while processing {tool_name}. Please try again."

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def check_availability(self, date_str: str, service_name: str, provider_name: str | None = None) -> str:
        """Return a human-readable list of available slots or a 'no availability' message."""
        service = self._find_service(service_name)
        if not service:
            available = [s["name"] for s in self.tenant.services]
            return f"Service '{service_name}' not found. Available services: {', '.join(available)}."

        duration_min = service["duration_min"]
        provider = self._find_provider(provider_name)
        calendar_id = (provider or {}).get("calendar_id") or "primary"

        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            return f"Invalid date format: '{date_str}'. Please use YYYY-MM-DD."

        slots = await self.calendar.list_available_slots(
            tenant_id=self.tenant.tenant_id,
            calendar_id=calendar_id,
            date_=target_date,
            duration_min=duration_min,
            buffer_min=self.tenant.buffer_min,
            timezone=self.tenant.timezone,
        )

        if not slots:
            return f"No available slots for {service_name} on {target_date.strftime('%A, %B %-d')}."

        slot_lines = [s["display"] for s in slots]
        return f"Available slots for {service_name} on {target_date.strftime('%A, %B %-d')}:\n" + "\n".join(
            f"- {line}" for line in slot_lines
        )

    async def book_appointment(
        self,
        customer_id: uuid.UUID,
        date_str: str,
        time_str: str,
        service_name: str,
        provider_name: str | None = None,
        customer_name: str | None = None,
        customer_email: str | None = None,
        notes: str = "",
    ) -> str:
        """Create a Google Calendar event + DB Appointment record. Returns confirmation."""
        service = self._find_service(service_name)
        if not service:
            return f"Service '{service_name}' not found."

        duration_min = service["duration_min"]
        provider = self._find_provider(provider_name)
        calendar_id = (provider or {}).get("calendar_id") or "primary"
        provider_display = (provider or {}).get("name", "")

        tz = zoneinfo.ZoneInfo(self.tenant.timezone)
        try:
            # Parse as naive local time, then attach tenant timezone
            naive_start = datetime.fromisoformat(f"{date_str}T{time_str}:00")
            start_dt = naive_start.replace(tzinfo=tz)
        except ValueError:
            return f"Invalid date/time: '{date_str} {time_str}'. Use YYYY-MM-DD and HH:MM."

        end_dt = start_dt + timedelta(minutes=duration_min)

        summary = f"{service_name.title()} — {customer_name or 'Customer'}"
        if provider_display:
            summary += f" (with {provider_display})"

        event_id = await self.calendar.create_event(
            tenant_id=self.tenant.tenant_id,
            calendar_id=calendar_id,
            summary=summary,
            start_dt=start_dt,
            end_dt=end_dt,
            attendee_email=customer_email,
            description=notes,
            timezone=self.tenant.timezone,
        )

        appointment = Appointment(
            id=uuid.uuid4(),
            tenant_id=self.tenant.tenant_id,
            customer_id=customer_id,
            google_event_id=event_id,
            provider_name=provider_display or None,
            service_name=service_name,
            starts_at=start_dt,
            duration_min=duration_min,
            status="confirmed",
        )
        self.db.add(appointment)
        await self.db.commit()

        display_time = start_dt.strftime("%-I:%M %p")
        display_date = start_dt.strftime("%A, %B %-d")
        return (
            f"Appointment confirmed! {service_name.title()} on {display_date} at {display_time}. "
            f"Appointment ID: {appointment.id}"
        )

    async def cancel_appointment(self, appointment_id: str) -> str:
        """Cancel event from Calendar + mark DB record cancelled."""
        appointment = await self._get_appointment(appointment_id)
        if not appointment:
            return f"Appointment {appointment_id} not found."
        if appointment.status == "cancelled":
            return "This appointment is already cancelled."

        if appointment.google_event_id:
            provider = self._find_provider(appointment.provider_name)
            calendar_id = (provider or {}).get("calendar_id") or "primary"
            await self.calendar.cancel_event(
                tenant_id=self.tenant.tenant_id,
                calendar_id=calendar_id,
                event_id=appointment.google_event_id,
            )

        appointment.status = "cancelled"
        await self.db.commit()

        return f"Appointment {appointment_id} has been cancelled."

    async def reschedule_appointment(self, appointment_id: str, new_date: str, new_time: str) -> str:
        """Cancel old event, create new one, update DB record."""
        appointment = await self._get_appointment(appointment_id)
        if not appointment:
            return f"Appointment {appointment_id} not found."
        if appointment.status == "cancelled":
            return "Cannot reschedule a cancelled appointment."

        tz = zoneinfo.ZoneInfo(self.tenant.timezone)
        try:
            new_start = datetime.fromisoformat(f"{new_date}T{new_time}:00").replace(tzinfo=tz)
        except ValueError:
            return f"Invalid date/time: '{new_date} {new_time}'. Use YYYY-MM-DD and HH:MM."

        new_end = new_start + timedelta(minutes=appointment.duration_min)
        provider = self._find_provider(appointment.provider_name)
        calendar_id = (provider or {}).get("calendar_id") or "primary"

        # Cancel old event
        if appointment.google_event_id:
            await self.calendar.cancel_event(
                tenant_id=self.tenant.tenant_id,
                calendar_id=calendar_id,
                event_id=appointment.google_event_id,
            )

        # Create new event
        summary = f"{(appointment.service_name or 'Appointment').title()} — Customer"
        new_event_id = await self.calendar.create_event(
            tenant_id=self.tenant.tenant_id,
            calendar_id=calendar_id,
            summary=summary,
            start_dt=new_start,
            end_dt=new_end,
            timezone=self.tenant.timezone,
        )

        # Update DB
        appointment.google_event_id = new_event_id
        appointment.starts_at = new_start
        appointment.status = "confirmed"
        await self.db.commit()

        display_time = new_start.strftime("%-I:%M %p")
        display_date = new_start.strftime("%A, %B %-d")
        return f"Appointment rescheduled to {display_date} at {display_time}. " f"Appointment ID: {appointment.id}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_service(self, service_name: str) -> dict | None:
        for s in self.tenant.services:
            if s["name"].lower() == service_name.lower():
                return s
        return None

    def _find_provider(self, provider_name: str | None) -> dict | None:
        if not provider_name:
            return self.tenant.providers[0] if self.tenant.providers else None
        for p in self.tenant.providers:
            if p["name"].lower() == provider_name.lower():
                return p
        return None

    async def _get_appointment(self, appointment_id: str) -> Appointment | None:
        try:
            apt_uuid = uuid.UUID(appointment_id)
        except ValueError:
            return None
        stmt = select(Appointment).where(
            Appointment.id == apt_uuid,
            Appointment.tenant_id == self.tenant.tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
