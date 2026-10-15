"""Account entry setup on a real HA core: ordering, outages, entity defaults."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from cloudplus_helpers import account_entry
from custom_components.cloudplus.const import DOMAIN


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def test_cameras_start_before_their_entities(hass: HomeAssistant):
    """go2rtc is picked when the camera entity is added: it needs a source then."""
    entry = account_entry()
    forward = hass.config_entries.async_forward_entry_setups
    started_at_forward: list[bool] = []

    async def spy(config_entry, platforms):
        runtime = hass.data[DOMAIN][config_entry.entry_id]
        started_at_forward.extend(c.available for c in runtime.coordinators.values())
        return await forward(config_entry, platforms)

    with patch.object(hass.config_entries, "async_forward_entry_setups", spy):
        await setup(hass, entry)
    assert started_at_forward == [True, True]


async def test_cloud_outage_retries_setup(hass: HomeAssistant, fake_cloud):
    fake_cloud.login.side_effect = ConnectionError("offline")
    entry = account_entry()
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_camera_list_failure_retries_setup(hass: HomeAssistant, fake_cloud):
    fake_cloud.get_camera_devices.side_effect = RuntimeError("Home list failed")
    entry = account_entry()
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_credentials_do_not_retry(hass: HomeAssistant, fake_cloud):
    fake_cloud.login.side_effect = PermissionError("Login failed: 1003")
    entry = account_entry()
    await setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_camera_disabled_by_old_default_is_enabled(hass: HomeAssistant):
    entry = account_entry()
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    old_default = ent_reg.async_get_or_create(
        "camera", DOMAIN, "SN-BACK_camera", config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    by_user = ent_reg.async_get_or_create(
        "camera", DOMAIN, "SN-FRONT_camera", config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    assert ent_reg.async_get(old_default.entity_id).disabled_by is None
    assert ent_reg.async_get(by_user.entity_id).disabled_by is er.RegistryEntryDisabler.USER
