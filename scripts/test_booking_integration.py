#!/usr/bin/env python3
"""
Integration test: end-to-end calendar booking via BookingService + LLMOrchestrator.

Simulates a real call:
  1. Load tenant config for coolbreeze_hvac
  2. Configure LLMOrchestrator with booking tools
  3. Run a multi-turn conversation until a booking is confirmed
  4. Verify calendar event was created on lichendong@gmail.com
  5. Clean up (delete the test event)

Run from project root:
  PYTHONPATH=src python scripts/test_booking_integration.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import logging
import uuid

from booking_service import BookingService
from booking_tools import BOOKING_TOOLS
from calendar_service import CalendarService
from googleapiclient.discovery import build
from llm_orchestrator import LLMOrchestrator
from tenant_config import TenantRegistry

logging.basicConfig(level=logging.WARNING)  # quiet — we print our own output

TENANT_PHONE = "+13185688982"

# Tomorrow's date so check_availability has a real target
TOMORROW = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

# Scripted conversation — enough info for the LLM to call the tool directly
CONVERSATION = [
    (
        f"Hi, I need to schedule an AC tune-up. "
        f"I'm available tomorrow ({TOMORROW}) at 10 AM. "
        f"My name is Test User, phone 604-555-0100, "
        f"address 4260 Coventry Drive, Richmond BC."
    ),
    "Yes, please confirm the booking.",
    "Yes, that's correct.",
]


async def find_test_event(service, calendar_id: str, after: datetime) -> dict | None:
    # Google Calendar API requires RFC3339 with Z suffix, no +00:00
    time_min = after.strftime("%Y-%m-%dT%H:%M:%SZ")
    result = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        maxResults=20,
        singleEvents=True,
        orderBy="startTime",
    ).execute()
    events = result.get("items", [])
    for e in reversed(events):
        summary = e.get("summary", "").lower()
        if any(k in summary for k in ["ac", "hvac", "tune", "cool", "voicebuddy", "technician", "test"]):
            return e
    # Return most recent event if we created any
    return events[-1] if events else None


async def delete_event(service, calendar_id: str, event_id: str) -> None:
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()


async def run_test():
    print("\n" + "=" * 60)
    print("  VoiceBuddy Booking Integration Test")
    print("=" * 60 + "\n")

    # ── 1. Tenant + calendar ──────────────────────────────────────────────────
    registry = TenantRegistry()
    tenant = registry.get_by_phone(TENANT_PHONE)
    assert tenant, f"Tenant not found for {TENANT_PHONE}"
    calendar_id = tenant.providers[0].get("calendar_id") if tenant.providers else None
    assert calendar_id, "calendar_id not configured in tenant YAML"
    print(f"✅ Tenant:       {tenant.tenant_id}")
    print(f"✅ Calendar ID:  {calendar_id}")
    print(f"✅ Target date:  {TOMORROW}\n")

    cal_service = CalendarService()
    creds = await cal_service.get_credentials(tenant.tenant_id)
    assert creds, "No calendar credentials"
    g_service = build("calendar", "v3", credentials=creds)
    print(f"✅ Credentials:  {type(creds).__name__}\n")

    # ── 2. LLM setup ─────────────────────────────────────────────────────────
    mock_db = AsyncMock()
    booking_svc = BookingService(
        calendar_service=cal_service,
        tenant_config=tenant,
        db=mock_db,
    )

    llm = LLMOrchestrator()
    llm.configure_booking(
        booking_service=booking_svc,
        tools=BOOKING_TOOLS,
        customer_id=uuid.uuid4(),
    )

    # Tell Sonnet explicitly to use the tools — append to system prompt
    llm.system_prompt_extra = (
        "\n\nYou have calendar booking tools available. "
        "When a customer provides their name, phone, address, and desired date/time, "
        "use check_availability to find slots, then book_appointment to confirm. "
        "Do NOT ask for more info if you already have enough — just book it."
    )

    responses: list[str] = []

    def on_full_ready(text: str, ttft_ms: float) -> None:
        responses.append(text)

    llm.on_full_ready = on_full_ready

    print("✅ LLMOrchestrator + BookingService ready\n")

    # ── 3. Multi-turn conversation ────────────────────────────────────────────
    started_at = datetime.now(UTC)
    event_found = None

    for i, user_msg in enumerate(CONVERSATION, 1):
        print(f"👤 Turn {i}: {user_msg[:120]}")
        await llm.process_turn(user_msg)
        bot_reply = responses[-1] if responses else "(no response)"
        print(f"🤖 Bot:   {bot_reply[:200]}\n")

        # Check calendar after each turn
        event_found = await find_test_event(g_service, calendar_id, started_at)
        if event_found:
            break

        # Stop if bot is clearly done
        done_phrases = ["booked", "confirmed", "scheduled", "all set", "see you"]
        if any(p in bot_reply.lower() for p in done_phrases):
            await asyncio.sleep(1)
            event_found = await find_test_event(g_service, calendar_id, started_at)
            break

    # ── 4. Result ─────────────────────────────────────────────────────────────
    print("-" * 60)
    if event_found:
        start = event_found["start"].get("dateTime", event_found["start"].get("date"))
        print(f"\n✅ CALENDAR EVENT CREATED!")
        print(f"   Summary: {event_found['summary']}")
        print(f"   Start:   {start}")
        print(f"   ID:      {event_found['id']}\n")

        print("🧹 Cleaning up test event...")
        await delete_event(g_service, calendar_id, event_found["id"])
        print("✅ Deleted.\n")
        print("🎉 Integration test PASSED\n")
    else:
        print("\n❌ FAILED — no calendar event was created.")
        print("\nAll LLM responses:")
        for i, r in enumerate(responses, 1):
            print(f"  [{i}] {r[:400]}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_test())
