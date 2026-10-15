"""Press-and-hold PTZ through the cloudplus.ptz action, on a real HA core."""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from cloudplus_helpers import account_entry
from custom_components.cloudplus.const import DOMAIN

PTZ_CAMERA = {
    "deviceID": 201,
    "snNum": "SN-PTZ",
    "deviceName": "Yard",
    "_category": "ipc",
    "capability": {"caps": {"ptz2": 1}},
}
COORDINATOR = "custom_components.cloudplus.coordinator.CloudEdgeMeariCoordinator"


@pytest.fixture
def cloud_devices() -> list[dict]:
    return [dict(PTZ_CAMERA)]


@pytest.fixture
def commands():
    """Record the PTZ commands that reach the (fake) Meari cloud, in order."""
    sent: list[str] = []
    lock = threading.Lock()

    def move(self, direction):
        time.sleep(0.05)  # a cloud round trip
        with lock:
            sent.append(f"move:{direction}")
        return True

    def stop(self):
        with lock:
            sent.append("stop")
        return True

    with patch(f"{COORDINATOR}.ptz_move", move), patch(f"{COORDINATOR}.ptz_stop", stop):
        yield sent


async def camera_entity(hass: HomeAssistant) -> str:
    account_entry().add_to_hass(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    return er.async_get(hass).async_get_entity_id("camera", DOMAIN, "SN-PTZ_camera")


async def ptz(hass: HomeAssistant, entity_id: str, **data) -> None:
    await hass.services.async_call(
        DOMAIN, "ptz", {"entity_id": entity_id, **data}, blocking=True
    )


async def test_hold_then_release(hass: HomeAssistant, commands):
    entity_id = await camera_entity(hass)
    await ptz(hass, entity_id, action="move", argument="left")
    await ptz(hass, entity_id, action="stop")
    await asyncio.sleep(0.1)
    assert commands == ["move:left", "stop"]


async def test_quick_tap_stop_never_overtakes_move(hass: HomeAssistant, commands):
    """Press and release land together; the slower move must still go first."""
    entity_id = await camera_entity(hass)
    await asyncio.gather(
        ptz(hass, entity_id, action="move", argument="up"),
        ptz(hass, entity_id, action="stop"),
    )
    await asyncio.sleep(0.1)
    assert commands == ["move:up", "stop"]


async def test_duration_is_a_nudge(hass: HomeAssistant, commands):
    entity_id = await camera_entity(hass)
    await ptz(hass, entity_id, action="move", argument="right", duration=0.2)
    assert commands == ["move:right"]
    await asyncio.sleep(0.4)
    await hass.async_block_till_done()
    assert commands == ["move:right", "stop"]


async def test_lost_release_stops_after_safety_limit(hass: HomeAssistant, commands):
    entity_id = await camera_entity(hass)
    with patch("custom_components.cloudplus.ptz.PTZ_MAX_MOVE_S", 0.2):
        await ptz(hass, entity_id, action="move", argument="down")
        await asyncio.sleep(0.4)
        await hass.async_block_till_done()
    assert commands == ["move:down", "stop"]


async def test_new_move_replaces_pending_auto_stop(hass: HomeAssistant, commands):
    entity_id = await camera_entity(hass)
    await ptz(hass, entity_id, action="move", argument="left", duration=0.2)
    await ptz(hass, entity_id, action="move", argument="right")  # held, no duration
    await asyncio.sleep(0.4)
    await hass.async_block_till_done()
    assert commands == ["move:left", "move:right"]  # the nudge's stop was dropped
    await ptz(hass, entity_id, action="stop")
    assert commands[-1] == "stop"
