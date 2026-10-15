"""Per-account runtime state: one coordinator per camera subentry."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import CONF_VIDEO_PASSWORD, DOMAIN, SUBENTRY_TYPE_CAMERA
from .coordinator import CloudEdgeMeariCoordinator


def camera_subentries(entry: ConfigEntry) -> dict[str, Any]:
    """Return the entry's camera subentries keyed by subentry id."""
    return {
        subentry_id: subentry
        for subentry_id, subentry in entry.subentries.items()
        if subentry.subentry_type == SUBENTRY_TYPE_CAMERA
    }


def cameras_in_other_entries(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    """Serials already added under another account entry of this integration.

    A camera shared between two configured accounts must exist once: its
    entities' unique ids are derived from the serial.
    """
    return {
        str(subentry.unique_id)
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
        for subentry in other.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_CAMERA
    }


def _reload_signature(entry: ConfigEntry) -> dict[str, str]:
    """What a camera's running coordinator was built from (beyond live settings)."""
    return {
        subentry_id: str(subentry.data.get(CONF_VIDEO_PASSWORD) or "")
        for subentry_id, subentry in camera_subentries(entry).items()
    }


@dataclass
class AccountRuntime:
    """Coordinators of one account entry, keyed by camera subentry id."""

    coordinators: dict[str, CloudEdgeMeariCoordinator] = field(default_factory=dict)
    # Snapshot of the camera subentries at setup, including cameras that were
    # skipped (disabled device). A different set means a camera was added or
    # removed and the entry must reload; live settings (stream mode, quality,
    # wake on motion...) are saved to subentry data without a reload.
    signature: dict[str, str] = field(default_factory=dict)

    def snapshot(self, entry: ConfigEntry) -> None:
        self.signature = _reload_signature(entry)

    def needs_reload(self, entry: ConfigEntry) -> bool:
        return _reload_signature(entry) != self.signature


def account_runtime(hass: HomeAssistant, entry: ConfigEntry) -> AccountRuntime | None:
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    return runtime if isinstance(runtime, AccountRuntime) else None


def all_coordinators(hass: HomeAssistant) -> Iterable[CloudEdgeMeariCoordinator]:
    for runtime in hass.data.get(DOMAIN, {}).values():
        if isinstance(runtime, AccountRuntime):
            yield from runtime.coordinators.values()


@callback
def add_camera_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
    build: Callable[[CloudEdgeMeariCoordinator, ConfigEntry], Iterable[Any]],
) -> None:
    """Add each camera's entities under its own subentry (and so its own device)."""
    runtime = account_runtime(hass, entry)
    if runtime is None:
        return
    for subentry_id, coord in runtime.coordinators.items():
        async_add_entities(list(build(coord, entry)), config_subentry_id=subentry_id)
