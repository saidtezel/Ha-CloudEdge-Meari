"""Fold legacy per-camera config entries into camera subentries.

Up to config-entry version 2 the integration used one account entry plus
one separate config entry per camera, which Home Assistant lists as
separate rows on the integration page. Version 3 keeps a single account
entry and stores every camera as a ``camera`` subentry of it.

The migration moves each legacy camera entry's devices and entities onto the
new subentry, so entity ids, history, areas and customisations survive, then
removes the legacy entry. It runs from ``async_setup`` before any entry of
this domain is set up, so no legacy camera is ever started alongside its
replacement.
"""

from __future__ import annotations

import logging
from types import MappingProxyType
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryDisabler,
    ConfigSubentry,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import UNDEFINED

from .const import (
    CAMERA_SETTING_KEYS,
    CONF_APP_PROFILE,
    CONF_COUNTRY_CODE,
    CONF_DEVICE,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_EMAIL,
    CONF_KNOWN_CAMERAS,
    CONF_PASSWORD,
    CONF_PHONE_CODE,
    CONF_SN_NUM,
    DEFAULT_APP_PROFILE,
    DEFAULT_COUNTRY_CODE,
    DEFAULT_PHONE_CODE,
    DOMAIN,
    SUBENTRY_TYPE_CAMERA,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_VERSION = 3
LEGACY_ACCOUNT_ENTRY_ID = "account_entry_id"
LEGACY_DISABLED_BY_ACCOUNT = "disabled_by_account"
_ACCOUNT_KEYS = (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_COUNTRY_CODE,
    CONF_PHONE_CODE,
    CONF_APP_PROFILE,
)


def is_legacy_camera_entry(entry: ConfigEntry) -> bool:
    return CONF_SN_NUM in entry.data


def camera_subentry_data(
    device: dict[str, Any], settings: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Subentry data for one camera: identity, last device payload, settings."""
    sn = str(device.get("snNum", "")).strip()
    data: dict[str, Any] = {
        CONF_SN_NUM: sn,
        CONF_DEVICE_ID: device.get("deviceID"),
        CONF_DEVICE_NAME: str(device.get("deviceName", sn)).strip() or sn,
        CONF_DEVICE: dict(device),
    }
    for key in CAMERA_SETTING_KEYS:
        if settings and settings.get(key) not in (None, ""):
            data[key] = settings[key]
    return data


def _legacy_device(entry: ConfigEntry) -> dict[str, Any]:
    device = dict(entry.data.get(CONF_DEVICE) or {})
    device.setdefault("snNum", entry.data[CONF_SN_NUM])
    device.setdefault("deviceID", entry.data.get(CONF_DEVICE_ID))
    device.setdefault("deviceName", entry.data.get(CONF_DEVICE_NAME))
    return device


def _account_for(hass: HomeAssistant, camera: ConfigEntry) -> ConfigEntry | None:
    """The account entry a legacy camera belongs to: its link, else same email."""
    linked = hass.config_entries.async_get_entry(
        camera.data.get(LEGACY_ACCOUNT_ENTRY_ID) or ""
    )
    if linked is not None and not is_legacy_camera_entry(linked):
        return linked
    email = str(camera.data.get(CONF_EMAIL, "")).strip().lower()
    for entry in hass.config_entries.async_entries(DOMAIN):
        if (
            not is_legacy_camera_entry(entry)
            and str(entry.data.get(CONF_EMAIL, "")).strip().lower() == email
        ):
            return entry
    return None


def _add_camera_subentry(
    hass: HomeAssistant, account: ConfigEntry, camera: ConfigEntry
) -> ConfigSubentry:
    sn = camera.data[CONF_SN_NUM]
    for subentry in account.subentries.values():
        if subentry.unique_id == sn:
            return subentry
    data = camera_subentry_data(_legacy_device(camera), dict(camera.options))
    subentry = ConfigSubentry(
        data=MappingProxyType(data),
        subentry_type=SUBENTRY_TYPE_CAMERA,
        title=data[CONF_DEVICE_NAME],
        unique_id=sn,
    )
    hass.config_entries.async_add_subentry(account, subentry)
    return subentry


def _move_registry_entries(
    hass: HomeAssistant,
    source_entry_id: str,
    account: ConfigEntry,
    subentry_id: str,
    *,
    disable_device: bool,
) -> None:
    """Re-home a legacy camera's devices and entities onto its subentry."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    same_entry = source_entry_id == account.entry_id

    # Entities first: disabling a device afterwards lets HA mark its
    # entities disabled-by-device instead of this loop re-enabling them.
    for entity in er.async_entries_for_config_entry(ent_reg, source_entry_id):
        updates: dict[str, Any] = {"config_subentry_id": subentry_id}
        if not same_entry:
            updates["config_entry_id"] = account.entry_id
        if (
            entity.disabled_by is er.RegistryEntryDisabler.CONFIG_ENTRY
            and account.disabled_by is None
        ):
            updates["disabled_by"] = None
        ent_reg.async_update_entity(entity.entity_id, **updates)

    for device in dr.async_entries_for_config_entry(dev_reg, source_entry_id):
        # Two calls on purpose: in one call HA derives the removal from the
        # device as it was before the add and drops the new subentry link.
        dev_reg.async_update_device(
            device.id,
            add_config_entry_id=account.entry_id,
            add_config_subentry_id=subentry_id,
        )
        dev_reg.async_update_device(
            device.id,
            remove_config_entry_id=source_entry_id,
            remove_config_subentry_id=None if same_entry else UNDEFINED,
        )
        if disable_device:
            # The user had disabled this camera's entry; keep it switched off.
            dev_reg.async_update_device(
                device.id, disabled_by=dr.DeviceEntryDisabler.USER
            )


def _known_cameras(entry: ConfigEntry) -> list[str]:
    known = [str(sn) for sn in entry.data.get(CONF_KNOWN_CAMERAS, [])]
    for subentry in entry.subentries.values():
        if subentry.unique_id and subentry.unique_id not in known:
            known.append(subentry.unique_id)
    return known


def _remove_v1_registry_entries(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """V1 was account-centric: drop its devices/entities so cameras start clean."""
    ent_reg = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        ent_reg.async_remove(entity.entity_id)
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        dev_reg.async_remove_device(device.id)


def _finish_account(hass: HomeAssistant, account: ConfigEntry) -> None:
    if account.version == 1 and not account.subentries:
        _remove_v1_registry_entries(hass, account)
    hass.config_entries.async_update_entry(
        account,
        data={**account.data, CONF_KNOWN_CAMERAS: _known_cameras(account)},
        version=CONFIG_VERSION,
    )


def _convert_orphan_to_account(hass: HomeAssistant, camera: ConfigEntry) -> None:
    """A legacy camera whose account entry is gone becomes the account itself."""
    sn = camera.data[CONF_SN_NUM]
    email = str(camera.data.get(CONF_EMAIL, "")).strip()
    account_data = {
        CONF_EMAIL: email,
        CONF_PASSWORD: camera.data.get(CONF_PASSWORD, ""),
        CONF_COUNTRY_CODE: camera.data.get(CONF_COUNTRY_CODE, DEFAULT_COUNTRY_CODE),
        CONF_PHONE_CODE: camera.data.get(CONF_PHONE_CODE, DEFAULT_PHONE_CODE),
        CONF_APP_PROFILE: camera.data.get(CONF_APP_PROFILE, DEFAULT_APP_PROFILE),
    }
    subentry_data = camera_subentry_data(_legacy_device(camera), dict(camera.options))
    hass.config_entries.async_update_entry(
        camera,
        data=account_data,
        options={},
        title=f"CloudEdge / Meari ({email})",
        unique_id=email.lower(),
    )
    subentry = ConfigSubentry(
        data=MappingProxyType(subentry_data),
        subentry_type=SUBENTRY_TYPE_CAMERA,
        title=subentry_data[CONF_DEVICE_NAME],
        unique_id=sn,
    )
    hass.config_entries.async_add_subentry(camera, subentry)
    _move_registry_entries(
        hass, camera.entry_id, camera, subentry.subentry_id, disable_device=False
    )
    _finish_account(hass, camera)


async def async_migrate_legacy_entries(hass: HomeAssistant) -> None:
    """Fold every legacy camera entry into its account. Idempotent."""
    entries = hass.config_entries.async_entries(DOMAIN)
    legacy = [entry for entry in entries if is_legacy_camera_entry(entry)]
    if not legacy and all(entry.version >= CONFIG_VERSION for entry in entries):
        return

    for camera in legacy:
        account = _account_for(hass, camera)
        if account is None:
            _LOGGER.info(
                "Converting camera entry %s into its account entry", camera.title
            )
            _convert_orphan_to_account(hass, camera)
            continue

        subentry = _add_camera_subentry(hass, account, camera)
        user_disabled = camera.disabled_by is ConfigEntryDisabler.USER and not (
            camera.data.get(LEGACY_DISABLED_BY_ACCOUNT)
        )
        _move_registry_entries(
            hass,
            camera.entry_id,
            account,
            subentry.subentry_id,
            disable_device=user_disabled,
        )
        _LOGGER.info(
            "Moved camera %s into account entry %s", camera.title, account.title
        )
        await hass.config_entries.async_remove(camera.entry_id)

    for entry in hass.config_entries.async_entries(DOMAIN):
        if not is_legacy_camera_entry(entry) and entry.version < CONFIG_VERSION:
            _finish_account(hass, entry)
