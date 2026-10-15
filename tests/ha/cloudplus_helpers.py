"""Shared builders for the real-HA tests."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.cloudplus.const import DOMAIN

ACCOUNT_DATA = {
    "email": "user@example.invalid",
    "password": "secret",
    "country_code": "FR",
    "phone_code": "33",
    "app_profile": "cloudedge",
}


def account_entry(**kwargs) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="CloudEdge / Meari (user@example.invalid)",
        data=dict(ACCOUNT_DATA),
        unique_id="user@example.invalid",
        version=kwargs.pop("version", 3),
        **kwargs,
    )
