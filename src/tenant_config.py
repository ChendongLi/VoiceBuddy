"""
YAML-based multi-tenant configuration for VoiceBuddy.

Each tenant is defined in a YAML file under tenants/ and looked up by phone number
at call time. This replaces the hardcoded prompts in prompts.py and voice IDs in
voice_config.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger("voicebuddy.tenant_config")

TENANTS_DIR = Path(__file__).resolve().parent.parent / "tenants"


@dataclass
class TenantConfig:
    tenant_id: str
    phone_number: str
    business_name: str
    system_prompt: str
    services: list[dict]  # [{name, duration_min, upsell?}]
    providers: list[dict]  # [{name, calendar_id?}]
    buffer_min: int
    cancellation_policy: str
    filler_phrases: list[str]
    voice_id: str
    fallback_number: str
    business_hours: dict  # {mon_fri, saturday, sunday}
    timezone: str  # IANA timezone, e.g. "America/Vancouver"


class TenantRegistry:
    """Loads all *.yaml tenant configs and provides lookup by phone number."""

    def __init__(self, tenants_dir: Path = TENANTS_DIR) -> None:
        self._by_phone: dict[str, TenantConfig] = {}
        self._by_id: dict[str, TenantConfig] = {}
        self._load(tenants_dir)

    def _load(self, tenants_dir: Path) -> None:
        if not tenants_dir.is_dir():
            logger.warning("Tenants directory not found: %s", tenants_dir)
            return

        for yaml_path in sorted(tenants_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                if not data:
                    continue
                tenant = TenantConfig(
                    tenant_id=data["tenant_id"],
                    phone_number=data["phone_number"],
                    business_name=data["business_name"],
                    system_prompt=data["system_prompt"],
                    services=data.get("services", []),
                    providers=data.get("providers", []),
                    buffer_min=data.get("buffer_min", 15),
                    cancellation_policy=data.get("cancellation_policy", ""),
                    filler_phrases=data.get("filler_phrases", []),
                    voice_id=data["voice_id"],
                    fallback_number=data.get("fallback_number", ""),
                    business_hours=data.get("business_hours", {}),
                    timezone=data.get("timezone", "UTC"),
                )
                self._by_phone[tenant.phone_number] = tenant
                self._by_id[tenant.tenant_id] = tenant
                logger.info("Loaded tenant: %s (%s)", tenant.tenant_id, tenant.phone_number)
            except Exception:
                logger.exception("Failed to load tenant config: %s", yaml_path)

    def get_by_phone(self, phone_number: str) -> TenantConfig | None:
        return self._by_phone.get(phone_number)

    def get_by_id(self, tenant_id: str) -> TenantConfig | None:
        return self._by_id.get(tenant_id)

    @property
    def all_tenants(self) -> list[TenantConfig]:
        return list(self._by_id.values())
