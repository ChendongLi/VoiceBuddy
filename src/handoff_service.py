"""Human handoff service — business hours check and Twilio call transfer."""

from __future__ import annotations

import logging
import re
from datetime import datetime

import httpx
import pytz

logger = logging.getLogger("voicebuddy.handoff")

_TIME_RE = re.compile(r"(\d{1,2})(am|pm)", re.IGNORECASE)

_DAY_ALIASES: dict[str, list[int]] = {
    "mon_fri": [0, 1, 2, 3, 4],
    "monday": [0],
    "tuesday": [1],
    "wednesday": [2],
    "thursday": [3],
    "friday": [4],
    "saturday": [5],
    "sunday": [6],
}


def _parse_time(s: str) -> int:
    """Parse '9am' or '5pm' into 24-hour int."""
    m = _TIME_RE.match(s.strip())
    if not m:
        raise ValueError(f"Cannot parse time: {s!r}")
    hour = int(m.group(1))
    period = m.group(2).lower()
    if period == "am":
        return hour % 12
    return (hour % 12) + 12


class HandoffService:
    """Handles business hours checks and Twilio call transfers."""

    @staticmethod
    def is_within_business_hours(business_hours: dict[str, str], timezone: str) -> bool:
        """Check if the current time falls within business hours.

        Args:
            business_hours: e.g. {"mon_fri": "9am-5pm", "saturday": "10am-3pm", "sunday": "closed"}
            timezone: IANA timezone string, e.g. "America/Chicago"
        """
        tz = pytz.timezone(timezone)
        now = datetime.now(tz)
        weekday = now.weekday()  # 0=Mon, 6=Sun

        for key, value in business_hours.items():
            days = _DAY_ALIASES.get(key.lower())
            if days is None:
                continue
            if weekday not in days:
                continue
            if value.strip().lower() == "closed":
                return False
            parts = value.split("-")
            if len(parts) != 2:
                continue
            open_hour = _parse_time(parts[0])
            close_hour = _parse_time(parts[1])
            return open_hour <= now.hour < close_hour

        return False

    @staticmethod
    def generate_transfer_twiml(fallback_number: str) -> str:
        """Generate TwiML XML to transfer a call."""
        return (
            "<Response>" "<Say>Transferring you now, please hold.</Say>" f"<Dial>{fallback_number}</Dial>" "</Response>"
        )

    @staticmethod
    async def initiate_transfer(
        call_sid: str,
        fallback_number: str,
        account_sid: str,
        auth_token: str,
    ) -> bool:
        """Update an in-progress Twilio call to transfer it via REST API."""
        twiml = HandoffService.generate_transfer_twiml(fallback_number)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                auth=(account_sid, auth_token),
                data={"Twiml": twiml},
            )

        if resp.status_code == 200:
            logger.info("Transfer initiated for call %s", call_sid)
            return True

        logger.error("Transfer failed for call %s: %d %s", call_sid, resp.status_code, resp.text)
        return False
