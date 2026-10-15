"""Binary sensor platform for CloudEdge / Meari — motion & awake state."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CloudEdgeMeariCoordinator
from .runtime import add_camera_entities
from .entity import CloudEdgeMeariEntity
from .meari_commands import CRY_DET_ENABLE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up CloudEdge / Meari binary sensors from a config entry."""
    add_camera_entities(hass, entry, async_add_entities, _camera_entities)


def _camera_entities(
    coord: CloudEdgeMeariCoordinator, entry: ConfigEntry
) -> list[BinarySensorEntity]:
    entities: list[BinarySensorEntity] = [CloudEdgeMeariMotionSensor(coord, entry)]
    if coord.is_battery_camera:
        entities.append(CloudEdgeMeariAwakeSensor(coord, entry))
        entities.append(CloudEdgeMeariChargingSensor(coord, entry))
    if coord.supports_iot("cry_det") or coord.has_iot_code(CRY_DET_ENABLE):
        entities.append(CloudEdgeMeariCryingSensor(coord, entry))
    return entities


class CloudEdgeMeariMotionSensor(CloudEdgeMeariEntity, BinarySensorEntity):
    """Binary sensor for motion detection."""

    _attr_name = "Motion"
    _attr_device_class = BinarySensorDeviceClass.MOTION

    def __init__(
        self, coordinator: CloudEdgeMeariCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.device_uuid}_motion"
        self._unsub_motion: Any = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_motion = self._coordinator.register_motion_callback(
            self._handle_update
        )

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._unsub_motion:
            self._unsub_motion()

    @property
    def is_on(self) -> bool:
        return self._coordinator.motion_detected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._coordinator.motion_type:
            attrs["motion_type"] = self._coordinator.motion_type
        return attrs


class CloudEdgeMeariAwakeSensor(CloudEdgeMeariEntity, BinarySensorEntity):
    """Binary sensor for camera awake state."""

    _attr_name = "Camera Awake"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:eye"

    def __init__(
        self, coordinator: CloudEdgeMeariCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.device_uuid}_awake"

    @property
    def is_on(self) -> bool:
        return self._coordinator.camera_awake


class CloudEdgeMeariChargingSensor(CloudEdgeMeariEntity, BinarySensorEntity):
    """Binary sensor for camera charging state."""

    _attr_name = "Charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(
        self, coordinator: CloudEdgeMeariCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.device_uuid}_charging"

    @property
    def is_on(self) -> bool | None:
        if self._coordinator.battery_percent is None:
            return None
        return self._coordinator.battery_charging


class CloudEdgeMeariCryingSensor(CloudEdgeMeariEntity, BinarySensorEntity):
    """On for a minute after each "Baby cry" alert from the camera.

    Alerts arrive through the same cloud path as motion (MQTT push, or the
    15 s event poll) and carry no "stopped crying" counterpart, so the
    sensor turns itself off after the hold time unless another alert lands.
    """

    _attr_name = "Crying"
    _attr_device_class = BinarySensorDeviceClass.SOUND
    _attr_icon = "mdi:emoticon-cry-outline"

    def __init__(
        self, coordinator: CloudEdgeMeariCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.device_uuid}_crying"
        self._unsub_expire: Any = None

    @property
    def is_on(self) -> bool:
        return self._coordinator.crying

    @callback
    def _handle_update(self) -> None:
        if self._unsub_expire:
            self._unsub_expire()
            self._unsub_expire = None
        if (remaining := self._coordinator.crying_for_s) > 0:
            self._unsub_expire = async_call_later(
                self.hass, remaining + 0.1, self._expire
            )
        super()._handle_update()

    @callback
    def _expire(self, _now: Any) -> None:
        self._unsub_expire = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._unsub_expire:
            self._unsub_expire()
            self._unsub_expire = None
