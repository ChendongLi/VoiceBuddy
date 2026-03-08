"""Post-call processing: transcript storage, AI summary, SMS confirmation."""

from __future__ import annotations

import json
import logging
import os
from uuid import UUID

import httpx
from anthropic import AsyncAnthropic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Call, Customer, Transcript
from tenant_config import TenantConfig

logger = logging.getLogger("voicebuddy.post_call")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")

SUMMARY_SYSTEM_PROMPT = (
    "You are a post-call analyst. Given a call transcript, produce a JSON object with:\n"
    '- "outcome": one of "booked", "cancelled", "rescheduled", "transferred", "dropped", "other"\n'
    '- "appointment_details": string describing the appointment (e.g. "Teeth cleaning Jan 20 at 10am '
    'with Dr. Smith") or null\n'
    '- "customer_name": the customer\'s name if mentioned, or null\n'
    '- "notes": any notable preferences or follow-ups, or null\n\n'
    "Respond with ONLY valid JSON, no markdown or extra text."
)


class PostCallService:
    async def process(
        self,
        db: AsyncSession,
        call_id: UUID,
        transcript_text: str,
        tenant_config: TenantConfig,
        customer: Customer | None,
    ) -> None:
        """Run after call ends. Saves transcript, generates summary, sends SMS."""
        try:
            # 1. Save transcript
            transcript = Transcript(call_id=call_id, full_text=transcript_text)
            db.add(transcript)
            await db.flush()

            # 2. Generate AI summary
            summary = await self._generate_summary(transcript_text)

            # 3. Update transcript summary and call outcome
            transcript.summary = json.dumps(summary)
            result = await db.execute(select(Call).where(Call.id == call_id))
            call = result.scalar_one_or_none()
            if call:
                call.outcome = summary.get("outcome", "other")

            await db.commit()
            logger.info("Post-call processed for call_id=%s outcome=%s", call_id, summary.get("outcome"))

            # 4. Send SMS if appointment was booked
            appointment_details = summary.get("appointment_details")
            if appointment_details and customer and customer.phone_number:
                await self._send_sms_confirmation(
                    to_phone=customer.phone_number,
                    from_phone=tenant_config.phone_number,
                    appointment_details=appointment_details,
                    business_name=tenant_config.business_name,
                )

        except Exception:
            logger.exception("Post-call processing failed for call_id=%s", call_id)
            await db.rollback()

    async def _generate_summary(self, transcript: str) -> dict:
        """Call Claude claude-haiku-3-5 to generate a structured call summary."""
        client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
        try:
            response = await client.messages.create(
                model="claude-haiku-4-5-20241022",
                max_tokens=512,
                system=SUMMARY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": transcript}],
            )
            text = response.content[0].text
            return json.loads(text)
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.warning("Failed to parse summary response: %s", e)
            return {"outcome": "other", "appointment_details": None, "customer_name": None, "notes": None}
        finally:
            await client.close()

    async def _send_sms_confirmation(
        self, to_phone: str, from_phone: str, appointment_details: str, business_name: str
    ) -> bool:
        """Send SMS via Twilio REST API. Returns True on success."""
        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            logger.warning("SMS skipped: TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN not set")
            return False

        body = f"Hi! Your appointment with {business_name} is confirmed: {appointment_details}. Reply STOP to opt out."
        url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                data={"To": to_phone, "From": from_phone, "Body": body},
            )

        if resp.status_code in (200, 201):
            logger.info("SMS sent to %s", to_phone)
            return True
        else:
            logger.warning("SMS failed (%d): %s", resp.status_code, resp.text)
            return False
