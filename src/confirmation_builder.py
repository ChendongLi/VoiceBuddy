"""
Confirmation, upsell, and caller-verification message builder for VoiceBuddy.

Pure string construction — no TTS or network dependencies.
"""

from __future__ import annotations

from datetime import datetime

from tenant_config import TenantConfig


class ConfirmationBuilder:
    """Builds human-friendly confirmation, upsell, and verification prompts."""

    def build_booking_confirmation(
        self,
        service: str,
        provider: str,
        dt: datetime,
        tenant: TenantConfig,
    ) -> str:
        day = dt.strftime("%A %B %-d")
        time_str = dt.strftime("%-I:%M %p").lower()
        return (
            f"Great! I have booked your {service} with {provider} "
            f"on {day} at {time_str}. "
            f"You will receive a text confirmation shortly."
        )

    def build_cancellation_confirmation(self, service: str, dt: datetime) -> str:
        day = dt.strftime("%A %B %-d")
        time_str = dt.strftime("%-I:%M %p").lower()
        return f"Your {service} appointment on {day} at {time_str} " f"has been cancelled."

    def build_reschedule_confirmation(self, service: str, old_dt: datetime, new_dt: datetime) -> str:
        old_day = old_dt.strftime("%A %B %-d")
        old_time = old_dt.strftime("%-I:%M %p").lower()
        new_day = new_dt.strftime("%A %B %-d")
        new_time = new_dt.strftime("%-I:%M %p").lower()
        return (
            f"Your {service} appointment has been rescheduled from "
            f"{old_day} at {old_time} to {new_day} at {new_time}."
        )

    def build_upsell_prompt(self, service_name: str, tenant: TenantConfig) -> str | None:
        for svc in tenant.services:
            if svc.get("name", "").lower() == service_name.lower():
                return svc.get("upsell") or None
        return None

    def build_verification_prompt(self, customer_name: str | None) -> str:
        if customer_name:
            return f"Can I confirm I am speaking with {customer_name}?"
        return "Could I get your name please?"
