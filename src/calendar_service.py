"""Google Calendar integration — per-tenant OAuth, availability lookup, event CRUD."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger("voicebuddy.calendar_service")

TOKENS_DIR = Path(__file__).resolve().parent.parent / "tokens"
SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarService:
    """Per-tenant Google Calendar operations using stored OAuth tokens."""

    def __init__(self, tokens_dir: Path = TOKENS_DIR) -> None:
        self._tokens_dir = tokens_dir

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    async def get_credentials(self, tenant_id: str) -> Credentials | None:
        """Load stored OAuth token for tenant. Refreshes if expired. Returns None if missing."""
        token_path = self._tokens_dir / f"{tenant_id}.json"
        if not token_path.exists():
            logger.warning("No token file for tenant %s", tenant_id)
            return None

        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            logger.info("Refreshed token for tenant %s", tenant_id)

        return creds

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    async def list_available_slots(
        self,
        tenant_id: str,
        calendar_id: str,
        date_: date,
        duration_min: int,
        buffer_min: int = 15,
    ) -> list[dict]:
        """Return open slots on *date_* for *duration_min*-minute appointments.

        Uses the freebusy API to find gaps in the calendar, then slices them
        into bookable windows separated by *buffer_min*.
        """
        creds = await self.get_credentials(tenant_id)
        if creds is None:
            return []

        service = build("calendar", "v3", credentials=creds)

        day_start = datetime(date_.year, date_.month, date_.day, 8, 0, tzinfo=UTC)
        day_end = datetime(date_.year, date_.month, date_.day, 18, 0, tzinfo=UTC)

        body = {
            "timeMin": day_start.isoformat(),
            "timeMax": day_end.isoformat(),
            "items": [{"id": calendar_id}],
        }

        result = service.freebusy().query(body=body).execute()
        busy_periods = result["calendars"][calendar_id]["busy"]

        busy = []
        for period in busy_periods:
            start = datetime.fromisoformat(period["start"])
            end = datetime.fromisoformat(period["end"])
            busy.append((start, end))
        busy.sort()

        slots: list[dict] = []
        cursor = day_start

        for b_start, b_end in busy:
            while cursor + timedelta(minutes=duration_min) <= b_start:
                slot_end = cursor + timedelta(minutes=duration_min)
                slots.append(_format_slot(cursor, slot_end))
                cursor = slot_end + timedelta(minutes=buffer_min)
            cursor = max(cursor, b_end + timedelta(minutes=buffer_min))

        while cursor + timedelta(minutes=duration_min) <= day_end:
            slot_end = cursor + timedelta(minutes=duration_min)
            slots.append(_format_slot(cursor, slot_end))
            cursor = slot_end + timedelta(minutes=buffer_min)

        return slots

    # ------------------------------------------------------------------
    # Event CRUD
    # ------------------------------------------------------------------

    async def create_event(
        self,
        tenant_id: str,
        calendar_id: str,
        summary: str,
        start_dt: datetime,
        end_dt: datetime,
        attendee_email: str | None = None,
        description: str = "",
    ) -> str:
        """Create a calendar event and return its event_id."""
        creds = await self.get_credentials(tenant_id)
        if creds is None:
            raise RuntimeError(f"No credentials for tenant {tenant_id}")

        service = build("calendar", "v3", credentials=creds)

        event_body: dict = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "UTC"},
        }
        if attendee_email:
            event_body["attendees"] = [{"email": attendee_email}]

        event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        event_id: str = event["id"]
        logger.info("Created event %s on calendar %s for tenant %s", event_id, calendar_id, tenant_id)
        return event_id

    async def cancel_event(self, tenant_id: str, calendar_id: str, event_id: str) -> bool:
        """Delete (cancel) an event. Returns True on success."""
        creds = await self.get_credentials(tenant_id)
        if creds is None:
            raise RuntimeError(f"No credentials for tenant {tenant_id}")

        service = build("calendar", "v3", credentials=creds)

        try:
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            logger.info("Cancelled event %s for tenant %s", event_id, tenant_id)
            return True
        except Exception:
            logger.exception("Failed to cancel event %s for tenant %s", event_id, tenant_id)
            return False


def _format_slot(start: datetime, end: datetime) -> dict:
    """Format a time slot for display."""
    display = start.strftime("%A %B %-d at %-I%p").replace("AM", "am").replace("PM", "pm")
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "display": display,
    }
