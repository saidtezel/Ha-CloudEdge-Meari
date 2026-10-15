"""The CloudEdge / Meari camera integration.

One config entry per Meari account. Each camera on the account is a
``camera`` subentry of that entry, with its own device and coordinator.
"""

from __future__ import annotations

import asyncio
import logging
from types import MappingProxyType
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_APP_PROFILE,
    CONF_COUNTRY_CODE,
    CONF_DEVICE,
    CONF_DEVICE_NAME,
    CONF_EMAIL,
    CONF_KNOWN_CAMERAS,
    CONF_PASSWORD,
    CONF_PHONE_CODE,
    CONF_SN_NUM,
    CONF_VIDEO_PASSWORD,
    DEFAULT_APP_PROFILE,
    DEFAULT_COUNTRY_CODE,
    DEFAULT_PHONE_CODE,
    DOMAIN,
    PTZ_DIRECTIONS,
    SUBENTRY_TYPE_CAMERA,
)
from .coordinator import CloudEdgeMeariCoordinator
from .api import MeariApiClient, build_api_client
from .migration import (
    CONFIG_VERSION,
    async_migrate_legacy_entries,
    camera_subentry_data,
    is_legacy_camera_entry,
)
from .runtime import (
    AccountRuntime,
    account_runtime,
    all_coordinators,
    camera_subentries,
    cameras_in_other_entries,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [
    "camera",
    "binary_sensor",
    "button",
    "sensor",
    "number",
    "select",
    "switch",
]

SERVICE_PTZ = "ptz"
SERVICE_PTZ_SCHEMA = vol.Schema(
    {
        vol.Required("action"): vol.In(["move", "stop"]),
        vol.Optional("argument"): vol.In(list(PTZ_DIRECTIONS.keys())),
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: Any) -> bool:
    """Migrate legacy per-camera entries before any entry is set up."""
    del config
    hass.data.setdefault(DOMAIN, {})
    await async_migrate_legacy_entries(hass)
    _register_ptz_service(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Safety net: ``async_setup`` normally migrated every entry already."""
    _LOGGER.debug(
        "Migrating config entry %s from version %s", entry.title, entry.version
    )
    await async_migrate_legacy_entries(hass)
    current = hass.config_entries.async_get_entry(entry.entry_id)
    return (
        current is not None
        and not is_legacy_camera_entry(current)
        and current.version == CONFIG_VERSION
    )


# ---------------------------------------------------------------------------
# Setup / unload
# ---------------------------------------------------------------------------


@callback
def _enable_mains_camera_entity(hass: HomeAssistant, coord: CloudEdgeMeariCoordinator) -> None:
    """Re-enable a camera entity left disabled by an old integration default.

    Earlier versions created the camera entity of mains-powered cameras
    disabled. Only that integration default is undone; a camera the user
    disabled themselves stays disabled.
    """
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id("camera", DOMAIN, f"{coord.device_uuid}_camera")
    reg_entry = ent_reg.async_get(entity_id) if entity_id else None
    if reg_entry and reg_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
        _LOGGER.info("Enabling camera entity %s", entity_id)
        ent_reg.async_update_entity(entity_id, disabled_by=None)


async def _async_login(
    hass: HomeAssistant, api: MeariApiClient, entry: ConfigEntry
) -> bool:
    """Log in, letting HA retry setup while the Meari cloud is unreachable."""
    try:
        await hass.async_add_executor_job(api.login)
    except PermissionError as err:
        # Wrong credentials won't fix themselves; retrying would only hammer
        # the login endpoint. Fix them via the account entry's Configure.
        _LOGGER.error("Login rejected for %s: %s", entry.title, err)
        return False
    except (OSError, ValueError, KeyError, RuntimeError) as err:
        raise ConfigEntryNotReady(f"Login failed for {entry.title}: {err}") from err
    return True


@callback
def _add_new_cameras(
    hass: HomeAssistant, entry: ConfigEntry, devices: dict[str, dict[str, Any]]
) -> None:
    """Add a subentry for each camera seen on the account for the first time.

    Cameras are remembered in ``known_cameras`` so one the user deleted is
    not re-added on the next start; "Add camera" brings it back.
    """
    known = [str(sn) for sn in entry.data.get(CONF_KNOWN_CAMERAS, [])]
    configured = {subentry.unique_id for subentry in entry.subentries.values()}
    configured |= cameras_in_other_entries(hass, entry)
    new_known = list(known)
    for sn, device in devices.items():
        if sn in known:
            continue
        new_known.append(sn)
        if sn in configured:
            continue
        data = camera_subentry_data(device)
        _LOGGER.info("Adding camera %s (%s)", data[CONF_DEVICE_NAME], sn)
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data=MappingProxyType(data),
                subentry_type=SUBENTRY_TYPE_CAMERA,
                title=data[CONF_DEVICE_NAME],
                unique_id=sn,
            ),
        )
    if new_known != known:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_KNOWN_CAMERAS: new_known}
        )


async def _async_stop_all(runtime: AccountRuntime) -> None:
    await asyncio.gather(
        *(coord.async_stop() for coord in runtime.coordinators.values())
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a CloudEdge / Meari account and its camera subentries."""
    hass.data.setdefault(DOMAIN, {})
    if is_legacy_camera_entry(entry):
        _LOGGER.error(
            "Camera entry %s was not migrated into its account; remove it and "
            "add the camera from the account entry instead",
            entry.title,
        )
        return False

    email = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]
    country_code = entry.data.get(CONF_COUNTRY_CODE, DEFAULT_COUNTRY_CODE)
    phone_code = entry.data.get(CONF_PHONE_CODE, DEFAULT_PHONE_CODE)
    app_profile = entry.data.get(CONF_APP_PROFILE, DEFAULT_APP_PROFILE)

    api = build_api_client(email, password, country_code, phone_code, app_profile)
    if not await _async_login(hass, api, entry):
        return False
    try:
        devices = await hass.async_add_executor_job(api.get_camera_devices)
    except (OSError, ValueError, KeyError, RuntimeError) as err:
        raise ConfigEntryNotReady(f"Could not list cameras: {err}") from err
    devices_by_sn = {
        str(device.get("snNum", "")).strip(): device
        for device in devices
        if str(device.get("snNum", "")).strip()
    }
    _add_new_cameras(hass, entry, devices_by_sn)

    runtime = AccountRuntime()
    dev_reg = dr.async_get(hass)
    for subentry_id, subentry in camera_subentries(entry).items():
        sn = str(subentry.data.get(CONF_SN_NUM, ""))
        device_entry = dev_reg.async_get_device(identifiers={(DOMAIN, sn)})
        if device_entry is not None and device_entry.disabled:
            # Disabling the camera's device is how a single camera is turned
            # off; enabling it again reloads the entry and starts it.
            _LOGGER.debug("Skipping camera %s: its device is disabled", subentry.title)
            continue
        coord = CloudEdgeMeariCoordinator(
            hass,
            email,
            password,
            devices_by_sn.get(sn) or dict(subentry.data.get(CONF_DEVICE) or {}),
            video_password=str(subentry.data.get(CONF_VIDEO_PASSWORD) or "").strip(),
            country_code=country_code,
            phone_code=phone_code,
            app_profile=app_profile,
            settings=subentry.data,
        )
        coord.subentry_id = subentry_id
        await hass.async_add_executor_job(coord.prefetch_battery, api)
        await hass.async_add_executor_job(coord.prefetch_lamp, api)
        runtime.coordinators[subentry_id] = coord
        _enable_mains_camera_entity(hass, coord)
    runtime.snapshot(entry)
    hass.data[DOMAIN][entry.entry_id] = runtime

    # Start before the platforms so each camera entity has a stream_source
    # when it is added: that is when HA picks its WebRTC provider (go2rtc).
    try:
        for coord in runtime.coordinators.values():
            await coord.async_start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await _async_stop_all(runtime)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when a camera was added/removed or its video password changed."""
    runtime = account_runtime(hass, entry)
    if runtime is not None and runtime.needs_reload(entry):
        hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime = account_runtime(hass, entry)
    if runtime is None:
        return True
    await _async_stop_all(runtime)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


# ---------------------------------------------------------------------------
# PTZ service
# ---------------------------------------------------------------------------


@callback
def _register_ptz_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_PTZ):
        return

    def _find_coordinator(entity_id: str) -> CloudEdgeMeariCoordinator | None:
        ent_entry = er.async_get(hass).async_get(entity_id)
        if ent_entry is None:
            return None
        for coord in all_coordinators(hass):
            if ent_entry.unique_id == f"{coord.device_uuid}_camera":
                return coord
        return None

    async def _handle_ptz(call: ServiceCall) -> None:
        action = call.data["action"]
        argument = call.data.get("argument")
        entity_ids = call.data.get("entity_id", [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        for eid in entity_ids:
            c = _find_coordinator(eid)
            if c is None:
                _LOGGER.warning("PTZ: no coordinator for %s", eid)
                continue
            if not c.has_ptz:
                _LOGGER.warning("PTZ: %s does not support PTZ", eid)
                continue
            if action == "move":
                if not argument:
                    _LOGGER.warning("PTZ move requires an argument")
                    continue
                await hass.async_add_executor_job(c.ptz_move, argument)
            elif action == "stop":
                await hass.async_add_executor_job(c.ptz_stop)

    hass.services.async_register(
        DOMAIN, SERVICE_PTZ, _handle_ptz, schema=SERVICE_PTZ_SCHEMA
    )
