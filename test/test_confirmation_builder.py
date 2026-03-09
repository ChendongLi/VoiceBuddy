"""Tests for ConfirmationBuilder — pure string logic, no TTS needed."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Allow imports from src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from confirmation_builder import ConfirmationBuilder
from tenant_config import TenantConfig


def _make_tenant(**overrides) -> TenantConfig:
    defaults = {
        "tenant_id": "test",
        "phone_number": "+10000000000",
        "business_name": "Test Biz",
        "system_prompt": "You are a bot.",
        "services": [
            {"name": "repair", "duration_min": 60, "upsell": "Ask about our plan."},
            {"name": "maintenance", "duration_min": 60, "upsell": None},
        ],
        "providers": [{"name": "Mike"}],
        "buffer_min": 15,
        "cancellation_policy": "24h notice.",
        "filler_phrases": ["One sec."],
        "voice_id": "abc",
        "fallback_number": "+19999999999",
        "business_hours": {"mon_fri": "9am-5pm"},
        "timezone": "UTC",
    }
    defaults.update(overrides)
    return TenantConfig(**defaults)


class TestBookingConfirmation:
    def test_basic(self):
        cb = ConfirmationBuilder()
        tenant = _make_tenant()
        dt = datetime(2026, 3, 10, 10, 0)
        result = cb.build_booking_confirmation("AC tune-up", "Mike", dt, tenant)
        assert "AC tune-up" in result
        assert "Mike" in result
        assert "March 10" in result
        assert "10:00 am" in result
        assert "text confirmation" in result

    def test_pm_time(self):
        cb = ConfirmationBuilder()
        tenant = _make_tenant()
        dt = datetime(2026, 3, 10, 14, 30)
        result = cb.build_booking_confirmation("repair", "Sarah", dt, tenant)
        assert "2:30 pm" in result


class TestCancellationConfirmation:
    def test_basic(self):
        cb = ConfirmationBuilder()
        dt = datetime(2026, 3, 10, 10, 0)
        result = cb.build_cancellation_confirmation("repair", dt)
        assert "repair" in result
        assert "cancelled" in result
        assert "March 10" in result


class TestRescheduleConfirmation:
    def test_basic(self):
        cb = ConfirmationBuilder()
        old = datetime(2026, 3, 10, 10, 0)
        new = datetime(2026, 3, 12, 14, 0)
        result = cb.build_reschedule_confirmation("maintenance", old, new)
        assert "rescheduled" in result
        assert "March 10" in result
        assert "March 12" in result
        assert "2:00 pm" in result


class TestUpsell:
    def test_returns_upsell_when_configured(self):
        cb = ConfirmationBuilder()
        tenant = _make_tenant()
        result = cb.build_upsell_prompt("repair", tenant)
        assert result == "Ask about our plan."

    def test_returns_none_when_null(self):
        cb = ConfirmationBuilder()
        tenant = _make_tenant()
        result = cb.build_upsell_prompt("maintenance", tenant)
        assert result is None

    def test_returns_none_for_unknown_service(self):
        cb = ConfirmationBuilder()
        tenant = _make_tenant()
        result = cb.build_upsell_prompt("plumbing", tenant)
        assert result is None

    def test_case_insensitive(self):
        cb = ConfirmationBuilder()
        tenant = _make_tenant()
        result = cb.build_upsell_prompt("Repair", tenant)
        assert result == "Ask about our plan."


class TestVerification:
    def test_returning_caller(self):
        cb = ConfirmationBuilder()
        result = cb.build_verification_prompt("Sarah")
        assert "Sarah" in result
        assert "confirm" in result

    def test_new_caller(self):
        cb = ConfirmationBuilder()
        result = cb.build_verification_prompt(None)
        assert "name" in result
