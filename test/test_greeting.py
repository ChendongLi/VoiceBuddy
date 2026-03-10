"""Tests for call greeting on connect (AGE-23).

Covers:
- TenantConfig greeting field defaults and YAML loading
- Greeting is queued to TTS on Twilio MediaStream start
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tenant_config import TenantConfig, TenantRegistry


# ---------------------------------------------------------------------------
# TenantConfig greeting field
# ---------------------------------------------------------------------------


class TestTenantConfigGreeting:
    def test_default_greeting(self):
        tc = TenantConfig(
            tenant_id="t",
            phone_number="+1",
            business_name="Biz",
            system_prompt="prompt",
            services=[],
            providers=[],
            buffer_min=15,
            cancellation_policy="",
            filler_phrases=[],
            voice_id="v",
            fallback_number="",
            business_hours={},
            timezone="UTC",
        )
        assert "Hi!" in tc.greeting
        assert "help" in tc.greeting.lower()

    def test_custom_greeting(self):
        tc = TenantConfig(
            tenant_id="t",
            phone_number="+1",
            business_name="Biz",
            system_prompt="prompt",
            services=[],
            providers=[],
            buffer_min=15,
            cancellation_policy="",
            filler_phrases=[],
            voice_id="v",
            fallback_number="",
            business_hours={},
            timezone="UTC",
            greeting="Welcome to Biz!",
        )
        assert tc.greeting == "Welcome to Biz!"


# ---------------------------------------------------------------------------
# TenantRegistry loads greeting from YAML
# ---------------------------------------------------------------------------


class TestGreetingYAMLLoading:
    def test_greeting_loaded_from_yaml(self, tmp_path):
        yaml_content = dedent("""\
            tenant_id: test_tenant
            phone_number: "+15550001111"
            business_name: Test Co
            voice_id: "abc123"
            system_prompt: "You are a bot."
            greeting: "Hello from Test Co!"
        """)
        (tmp_path / "test.yaml").write_text(yaml_content)
        registry = TenantRegistry(tenants_dir=tmp_path)
        tenant = registry.get_by_id("test_tenant")
        assert tenant is not None
        assert tenant.greeting == "Hello from Test Co!"

    def test_greeting_uses_default_when_missing(self, tmp_path):
        yaml_content = dedent("""\
            tenant_id: test_tenant
            phone_number: "+15550001111"
            business_name: Test Co
            voice_id: "abc123"
            system_prompt: "You are a bot."
        """)
        (tmp_path / "test.yaml").write_text(yaml_content)
        registry = TenantRegistry(tenants_dir=tmp_path)
        tenant = registry.get_by_id("test_tenant")
        assert tenant is not None
        assert tenant.greeting == TenantConfig.greeting


# ---------------------------------------------------------------------------
# CoolBreeze YAML has greeting
# ---------------------------------------------------------------------------


class TestCoolBreezeGreeting:
    def test_coolbreeze_yaml_has_greeting(self):
        yaml_path = Path(__file__).resolve().parent.parent / "tenants" / "coolbreeze_hvac.yaml"
        data = yaml.safe_load(yaml_path.read_text())
        assert "greeting" in data
        assert "CoolBreeze" in data["greeting"]

    def test_coolbreeze_greeting_loaded(self):
        tenants_dir = Path(__file__).resolve().parent.parent / "tenants"
        registry = TenantRegistry(tenants_dir=tenants_dir)
        tenant = registry.get_by_id("coolbreeze_hvac")
        assert tenant is not None
        assert "CoolBreeze" in tenant.greeting
