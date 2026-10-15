"""Media player for cameras with built-in lullabies / white noise (e.g. AINanny)."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import CloudEdgeMeariCoordinator
from .entity import CloudEdgeMeariEntity
from .meari_commands import MUSIC_CYCLICAL, MUSIC_PLAY_CONTROL, MUSIC_STATUS, MUSIC_VOLUME
from .runtime import add_camera_entities

# MUSIC_PLAY_CONTROL values.
_STOP, _PLAY, _PAUSE, _NEXT, _PREVIOUS = 0, 1, 2, 3, 4


def _has_music(coord: CloudEdgeMeariCoordinator) -> bool:
    return any(coord.has_iot_code(code) for code in (MUSIC_STATUS, MUSIC_VOLUME, MUSIC_CYCLICAL))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the lullaby player for cameras that report music settings."""
    add_camera_entities(hass, entry, async_add_entities, _camera_entities)


def _camera_entities(
    coord: CloudEdgeMeariCoordinator, entry: ConfigEntry
) -> list[MediaPlayerEntity]:
    return [CloudEdgeMeariLullabyPlayer(coord, entry)] if _has_music(coord) else []


class CloudEdgeMeariLullabyPlayer(CloudEdgeMeariEntity, MediaPlayerEntity):
    """The camera's built-in lullabies: play/pause/stop/skip, volume, repeat.

    Play state comes from IoT 1012 (polled), updated optimistically after a
    command. A track that ends by itself shows as playing until the next
    status poll.
    """

    _attr_name = "Lullaby"
    _attr_icon = "mdi:music-box"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER

    def __init__(self, coordinator: CloudEdgeMeariCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{coordinator.device_uuid}_lullaby"
        self._paused = False
        features = (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
        )
        if coordinator.has_iot_code(MUSIC_VOLUME):
            features |= MediaPlayerEntityFeature.VOLUME_SET
        if coordinator.has_iot_code(MUSIC_CYCLICAL):
            features |= MediaPlayerEntityFeature.REPEAT_SET
        self._attr_supported_features = features

    def _int(self, code: int) -> int | None:
        try:
            return int(self._coordinator.get_iot_value(code))
        except (TypeError, ValueError):
            return None

    @property
    def state(self) -> MediaPlayerState:
        if self._int(MUSIC_STATUS) == 1:
            return MediaPlayerState.PLAYING
        return MediaPlayerState.PAUSED if self._paused else MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float | None:
        volume = self._int(MUSIC_VOLUME)
        return None if volume is None else max(0, min(100, volume)) / 100

    @property
    def repeat(self) -> RepeatMode | None:
        cyclical = self._int(MUSIC_CYCLICAL)
        if cyclical is None:
            return None
        return RepeatMode.ALL if cyclical == 1 else RepeatMode.OFF

    async def _control(self, value: int) -> None:
        ok = await self.hass.async_add_executor_job(
            self._coordinator.send_device_command, MUSIC_PLAY_CONTROL, value
        )
        if ok:
            self._paused = value == _PAUSE
            playing = value in (_PLAY, _NEXT, _PREVIOUS)
            self._coordinator.remember_iot_value(MUSIC_STATUS, 1 if playing else 0)
        self.async_write_ha_state()

    async def async_media_play(self) -> None:
        await self._control(_PLAY)

    async def async_media_pause(self) -> None:
        await self._control(_PAUSE)

    async def async_media_stop(self) -> None:
        await self._control(_STOP)

    async def async_media_next_track(self) -> None:
        await self._control(_NEXT)

    async def async_media_previous_track(self) -> None:
        await self._control(_PREVIOUS)

    async def async_set_volume_level(self, volume: float) -> None:
        await self.hass.async_add_executor_job(
            self._coordinator.set_iot_value, MUSIC_VOLUME, round(volume * 100)
        )
        self.async_write_ha_state()

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        # The camera only loops or doesn't; "one" from HA's repeat cycle turns
        # looping off so the button still toggles.
        await self.hass.async_add_executor_job(
            self._coordinator.set_iot_value, MUSIC_CYCLICAL, 1 if repeat == RepeatMode.ALL else 0
        )
        self.async_write_ha_state()
