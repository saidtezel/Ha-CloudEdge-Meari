"""Baby-monitor features (AINanny-style camera) on a real HA core."""

from __future__ import annotations

import asyncio
from unittest.mock import call, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from cloudplus_helpers import account_entry
from custom_components.cloudplus.const import DOMAIN
from custom_components.cloudplus.coordinator.iot import normalize_iot_values

SN = "SN-NANNY"
NANNY = {
    "deviceID": 301,
    "snNum": SN,
    "deviceName": "Nursery",
    "_category": "ipc",
    "capability": {"caps": {"ptz2": 1, "rgb": 1, "cct": 1}},
}
IOT = {1012: 0, 179: 40, 277: 1, 197: 0, 198: "[255,128,0]", 1014: 0, 863: 2}
COORDINATOR = "custom_components.cloudplus.coordinator.CloudEdgeMeariCoordinator"


@pytest.fixture
def cloud_devices() -> list[dict]:
    return [dict(NANNY)]


@pytest.fixture(autouse=True)
def camera_settings(fake_cloud):
    """The setup-time IoT read returns the nursery camera's values."""
    fake_cloud.set_device_iot_value.return_value = True
    fake_cloud.send_device_command.return_value = True

    def prefetch(self, api):
        self._api = api
        self._iot_data = normalize_iot_values(IOT)

    with patch(f"{COORDINATOR}.prefetch_lamp", prefetch):
        yield fake_cloud


def entity(hass: HomeAssistant, domain: str, unique_suffix: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{SN}_{unique_suffix}")
    assert entity_id, f"{domain} {unique_suffix} missing"
    return entity_id


@pytest.fixture
async def nursery(hass: HomeAssistant):
    account_entry().add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    return hass


async def test_lullaby_player(nursery: HomeAssistant, camera_settings):
    hass = nursery
    player = entity(hass, "media_player", "lullaby")
    state = hass.states.get(player)
    assert state.state == "idle"
    assert state.attributes["volume_level"] == 0.4
    assert state.attributes["repeat"] == "all"

    await hass.services.async_call("media_player", "media_play", {"entity_id": player}, blocking=True)
    assert camera_settings.send_device_command.call_args == call(SN, "821", 1)
    assert hass.states.get(player).state == "playing"

    await hass.services.async_call("media_player", "media_pause", {"entity_id": player}, blocking=True)
    assert camera_settings.send_device_command.call_args == call(SN, "821", 2)
    assert hass.states.get(player).state == "paused"

    await hass.services.async_call("media_player", "media_next_track", {"entity_id": player}, blocking=True)
    assert camera_settings.send_device_command.call_args == call(SN, "821", 3)
    assert hass.states.get(player).state == "playing"

    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": player, "volume_level": 0.6}, blocking=True
    )
    assert camera_settings.set_device_iot_value.call_args == call(SN, "179", 60)

    await hass.services.async_call(
        "media_player", "repeat_set", {"entity_id": player, "repeat": "off"}, blocking=True
    )
    assert camera_settings.set_device_iot_value.call_args == call(SN, "277", 0)
    assert hass.states.get(player).attributes["repeat"] == "off"


async def test_night_light_colour(nursery: HomeAssistant, camera_settings):
    hass = nursery
    light = entity(hass, "light", "night_light")
    state = hass.states.get(light)
    assert state.state == "off"

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": light, "rgb_color": [10, 20, 30]}, blocking=True
    )
    assert camera_settings.set_device_iot_value.call_args_list[-2:] == [
        call(SN, "198", "[10,20,30]"),
        call(SN, "197", 1),
    ]
    state = hass.states.get(light)
    assert state.state == "on"
    assert state.attributes["rgb_color"] == (10, 20, 30)

    await hass.services.async_call("light", "turn_off", {"entity_id": light}, blocking=True)
    assert camera_settings.set_device_iot_value.call_args == call(SN, "197", 0)
    assert hass.states.get(light).state == "off"

    old_switch = er.async_get(hass).async_get(entity(hass, "switch", "iot_switch_197"))
    assert old_switch.hidden_by is er.RegistryEntryHider.INTEGRATION


async def test_crying_turns_off_after_hold(nursery: HomeAssistant):
    hass = nursery
    crying = entity(hass, "binary_sensor", "crying")
    assert hass.states.get(crying).state == "off"
    coord = next(iter(hass.data[DOMAIN].values())).coordinators
    coord = next(iter(coord.values()))

    with patch("custom_components.cloudplus.coordinator.state.CRY_HOLD_S", 0.3):
        coord._note_cry()
        await hass.async_block_till_done()
        assert hass.states.get(crying).state == "on"
        await asyncio.sleep(0.6)
        await hass.async_block_till_done()
    assert hass.states.get(crying).state == "off"


async def test_baby_status(nursery: HomeAssistant):
    assert nursery.states.get(entity(nursery, "sensor", "baby_status")).state == "sleeping"


async def test_ptz_preset_goto(nursery: HomeAssistant, camera_settings):
    hass = nursery
    await hass.services.async_call(
        DOMAIN,
        "ptz_preset",
        {"entity_id": entity(hass, "camera", "camera"), "action": "goto", "preset": 2, "name": "Cot"},
        blocking=True,
    )
    assert camera_settings.send_device_command.call_args == call(
        SN, "848", '{"id":2,"name":"Cot","act":"goto"}'
    )


async def test_features_absent_without_settings(hass: HomeAssistant, camera_settings):
    """A camera reporting none of these settings gets none of the entities."""
    with patch(f"{COORDINATOR}.prefetch_lamp"):
        plain = dict(NANNY, capability={"caps": {}})
        camera_settings.get_camera_devices.side_effect = lambda: [dict(plain)]
        account_entry().add_to_hass(hass)
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    for domain, suffix in (
        ("media_player", "lullaby"),
        ("light", "night_light"),
        ("binary_sensor", "crying"),
        ("sensor", "baby_status"),
    ):
        assert ent_reg.async_get_entity_id(domain, DOMAIN, f"{SN}_{suffix}") is None
