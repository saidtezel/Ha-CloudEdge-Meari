"""Colour night light for cameras with an RGB light (e.g. AINanny)."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.components.light import ATTR_RGB_COLOR, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CloudEdgeMeariCoordinator
from .entity import CloudEdgeMeariEntity
from .meari_commands import RGB_LIGHT_COLOR, RGB_LIGHT_STATUS, RGB_LIGHT_SWITCH
from .runtime import add_camera_entities


def has_rgb_light(coord: CloudEdgeMeariCoordinator) -> bool:
    return coord.supports_iot("rgb_light") or coord.has_iot_code(RGB_LIGHT_SWITCH)


def parse_rgb(value: Any) -> tuple[int, int, int] | None:
    """IoT 198 holds ``"[r,g,b]"`` (a JSON string) or, decoded, a list."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        r, g, b = (max(0, min(255, int(c))) for c in value)
    except (TypeError, ValueError):
        return None
    return r, g, b


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the night light for cameras with an RGB light."""
    add_camera_entities(hass, entry, async_add_entities, _camera_entities)


def _camera_entities(
    coord: CloudEdgeMeariCoordinator, entry: ConfigEntry
) -> list[LightEntity]:
    return [CloudEdgeMeariNightLight(coord, entry)] if has_rgb_light(coord) else []


class CloudEdgeMeariNightLight(CloudEdgeMeariEntity, LightEntity):
    """On/off (IoT 197) and colour (IoT 198); 1014 reports the actual state."""

    _attr_name = "Night Light"
    _attr_icon = "mdi:lightbulb-night"
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(self, coordinator: CloudEdgeMeariCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.device_uuid}_night_light"

    @property
    def is_on(self) -> bool | None:
        for code in (RGB_LIGHT_STATUS, RGB_LIGHT_SWITCH):
            value = self._coordinator.get_iot_value(code)
            if value is not None:
                try:
                    return int(value) == 1
                except (TypeError, ValueError):
                    continue
        return None

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return parse_rgb(self._coordinator.get_iot_value(RGB_LIGHT_COLOR))

    async def _set(self, code: int, value: Any) -> bool:
        return await self.hass.async_add_executor_job(
            self._coordinator.set_iot_value, code, value
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        if (rgb := kwargs.get(ATTR_RGB_COLOR)) is not None:
            await self._set(RGB_LIGHT_COLOR, json.dumps([int(c) for c in rgb], separators=(",", ":")))
        if await self._set(RGB_LIGHT_SWITCH, 1):
            self._coordinator.remember_iot_value(RGB_LIGHT_STATUS, 1)
        self.async_write_ha_state()

    async def async_turn_off(self, **_kwargs: Any) -> None:
        if await self._set(RGB_LIGHT_SWITCH, 0):
            self._coordinator.remember_iot_value(RGB_LIGHT_STATUS, 0)
        self.async_write_ha_state()
