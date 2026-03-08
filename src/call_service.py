"""Call lifecycle service — create and finalize call records."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Call


class CallService:
    async def start_call(
        self,
        db: AsyncSession,
        tenant_id: str,
        customer_id: uuid.UUID,
        twilio_call_sid: str,
        caller_number: str,
    ) -> Call:
        """Create a Call record when call starts."""
        call = Call(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            customer_id=customer_id,
            twilio_call_sid=twilio_call_sid,
            caller_number=caller_number,
        )
        db.add(call)
        await db.commit()
        await db.refresh(call)
        return call

    async def end_call(
        self,
        db: AsyncSession,
        call_id: uuid.UUID,
        outcome: str,
        duration_sec: int,
    ) -> Call:
        """Update Call record with outcome and duration."""
        stmt = select(Call).where(Call.id == call_id)
        result = await db.execute(stmt)
        call = result.scalar_one()

        call.outcome = outcome
        call.duration_sec = duration_sec
        call.ended_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(call)
        return call
