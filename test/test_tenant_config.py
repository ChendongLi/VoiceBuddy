"""Tests for the YAML-based multi-tenant config system."""

from pathlib import Path

import pytest

from tenant_config import TenantConfig, TenantRegistry

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "tenants"


@pytest.fixture()
def sample_yaml(tmp_path: Path) -> Path:
    """Write a minimal tenant YAML to a temp dir and return the dir."""
    (tmp_path / "acme.yaml").write_text(
        """\
tenant_id: acme
phone_number: "+15551234567"
business_name: Acme Corp
voice_id: "abc-123"
fallback_number: "+15559999999"
system_prompt: "You are a helpful assistant for Acme Corp."
services:
  - name: consulting
    duration_min: 30
providers:
  - name: Acme Dispatch
buffer_min: 10
cancellation_policy: "Cancel 12h before."
filler_phrases:
  - "One sec."
business_hours:
  mon_fri: "9am-5pm"
  saturday: "closed"
  sunday: "closed"
"""
    )
    return tmp_path


class TestTenantRegistry:
    def test_loads_yaml_and_lookup_by_phone(self, sample_yaml: Path) -> None:
        registry = TenantRegistry(tenants_dir=sample_yaml)
        tenant = registry.get_by_phone("+15551234567")
        assert tenant is not None
        assert tenant.tenant_id == "acme"
        assert tenant.business_name == "Acme Corp"
        assert tenant.voice_id == "abc-123"

    def test_lookup_by_id(self, sample_yaml: Path) -> None:
        registry = TenantRegistry(tenants_dir=sample_yaml)
        tenant = registry.get_by_id("acme")
        assert tenant is not None
        assert tenant.phone_number == "+15551234567"

    def test_missing_phone_returns_none(self, sample_yaml: Path) -> None:
        registry = TenantRegistry(tenants_dir=sample_yaml)
        assert registry.get_by_phone("+10000000000") is None

    def test_missing_id_returns_none(self, sample_yaml: Path) -> None:
        registry = TenantRegistry(tenants_dir=sample_yaml)
        assert registry.get_by_id("nonexistent") is None

    def test_all_tenants(self, sample_yaml: Path) -> None:
        registry = TenantRegistry(tenants_dir=sample_yaml)
        assert len(registry.all_tenants) == 1

    def test_empty_dir(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tenants_dir=tmp_path)
        assert registry.all_tenants == []

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        registry = TenantRegistry(tenants_dir=tmp_path / "does_not_exist")
        assert registry.all_tenants == []

    def test_services_parsed(self, sample_yaml: Path) -> None:
        registry = TenantRegistry(tenants_dir=sample_yaml)
        tenant = registry.get_by_id("acme")
        assert tenant is not None
        assert len(tenant.services) == 1
        assert tenant.services[0]["name"] == "consulting"
        assert tenant.services[0]["duration_min"] == 30

    def test_filler_phrases(self, sample_yaml: Path) -> None:
        registry = TenantRegistry(tenants_dir=sample_yaml)
        tenant = registry.get_by_id("acme")
        assert tenant is not None
        assert "One sec." in tenant.filler_phrases


class TestCoolBreezeYaml:
    """Validate the real coolbreeze_hvac.yaml loads correctly."""

    def test_loads_real_yaml(self) -> None:
        tenants_dir = Path(__file__).resolve().parent.parent / "tenants"
        registry = TenantRegistry(tenants_dir=tenants_dir)
        tenant = registry.get_by_phone("+13185688982")
        assert tenant is not None
        assert tenant.tenant_id == "coolbreeze_hvac"
        assert tenant.business_name == "CoolBreeze HVAC Services"
        assert tenant.voice_id == "f786b574-daa5-4673-aa0c-cbe3e8534c02"
        assert len(tenant.services) == 4
        assert tenant.business_hours["sunday"] == "closed"
        assert "24 hours" in tenant.cancellation_policy
        assert len(tenant.filler_phrases) >= 3
