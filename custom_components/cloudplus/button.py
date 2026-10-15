"""Button platform for CloudEdge / Meari — wake camera."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CloudEdgeMeariCoordinator
from .runtime import add_camera_entities
from .entity import CloudEdgeMeariEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up CloudEdge / Meari buttons from a config entry."""
    add_camera_entities(hass, entry, async_add_entities, _camera_entities)


def _camera_entities(
    coord: CloudEdgeMeariCoordinator, entry: ConfigEntry
) -> list[ButtonEntity]:
    if coord.is_battery_camera:
        return [CloudEdgeMeariWakeButton(coord, entry)]
    return []


class CloudEdgeMeariWakeButton(CloudEdgeMeariEntity, ButtonEntity):
    """Button to wake the camera."""

    _attr_name = "Wake Camera"
    _attr_icon = "mdi:alarm-light-outline"

    def __init__(
        self, coordinator: CloudEdgeMeariCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.device_uuid}_wake"

    async def async_press(self) -> None:
        """Wake the camera."""
        _LOGGER.info("Wake button pressed for %s", self._coordinator.device_name)
        self._coordinator.wake_camera()
