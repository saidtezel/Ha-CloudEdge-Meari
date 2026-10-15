"""Config flow for CloudEdge / Meari integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    APP_PROFILE_NAMES,
    APP_PROFILES,
    CONF_APP_PROFILE,
    CONF_COUNTRY_CODE,
    CONF_DEVICE_NAME,
    CONF_EMAIL,
    CONF_PHONE_CODE,
    CONF_PASSWORD,
    CONF_SN_NUM,
    CONF_VIDEO_PASSWORD,
    DEFAULT_APP_PROFILE,
    DEFAULT_COUNTRY_CODE,
    DEFAULT_PHONE_CODE,
    DOMAIN,
    SUBENTRY_TYPE_CAMERA,
)
from .api import build_api_client
from .migration import CONFIG_VERSION, camera_subentry_data
from .runtime import cameras_in_other_entries

_LOGGER = logging.getLogger(__name__)


def _normalize_phone_code(raw: Any) -> str:
    return str(raw).strip().lstrip("+")


def _clean_optional_text(raw: Any) -> str:
    return "" if raw is None else str(raw).strip()


def _app_profile_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=p, label=APP_PROFILE_NAMES[p])
                for p in APP_PROFILES
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_COUNTRY_CODE, default=DEFAULT_COUNTRY_CODE): str,
        vol.Optional(CONF_PHONE_CODE, default=DEFAULT_PHONE_CODE): str,
        vol.Optional(
            CONF_APP_PROFILE, default=DEFAULT_APP_PROFILE
        ): _app_profile_selector(),
    }
)


class CloudEdgeMeariConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per account; each camera is a ``camera`` subentry of it.

    Cameras found on the account are added automatically when the entry is
    set up; "Add camera" re-adds one that was deleted.
    """

    VERSION = CONFIG_VERSION

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "CloudEdgeMeariOptionsFlow":
        return CloudEdgeMeariOptionsFlow(config_entry)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        del config_entry  # every account supports cameras
        return {SUBENTRY_TYPE_CAMERA: CameraSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]
            country_code = (
                str(user_input.get(CONF_COUNTRY_CODE, DEFAULT_COUNTRY_CODE))
                .strip()
                .upper()
            )
            phone_code = _normalize_phone_code(
                user_input.get(CONF_PHONE_CODE, DEFAULT_PHONE_CODE)
            )
            app_profile = user_input.get(CONF_APP_PROFILE, DEFAULT_APP_PROFILE)

            client = build_api_client(
                email, password, country_code, phone_code, app_profile
            )
            try:
                await self.hass.async_add_executor_job(client.login)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except (ConnectionError, OSError):
                errors["base"] = "cannot_connect"
            except (RuntimeError, ValueError, KeyError):
                _LOGGER.exception("Unexpected exception during login")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(email.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"CloudEdge / Meari ({email})",
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                        CONF_COUNTRY_CODE: country_code,
                        CONF_PHONE_CODE: phone_code,
                        CONF_APP_PROFILE: app_profile,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )


# ---------------------------------------------------------------------------
# Options flows
# ---------------------------------------------------------------------------


class CloudEdgeMeariOptionsFlow(OptionsFlow):
    """Account credentials; reloading the entry restarts every camera."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._account_options(user_input)

    # ------------------------------------------------------------------
    # Account entry — credentials, shared by every camera subentry
    # ------------------------------------------------------------------

    async def _account_options(
        self, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        current_data = dict(self._config_entry.data)
        country_code = (
            str(current_data.get(CONF_COUNTRY_CODE, DEFAULT_COUNTRY_CODE))
            .strip()
            .upper()
        )
        phone_code = _normalize_phone_code(
            current_data.get(CONF_PHONE_CODE, DEFAULT_PHONE_CODE)
        )
        app_profile = current_data.get(CONF_APP_PROFILE, DEFAULT_APP_PROFILE)

        if user_input is not None:
            new_data = dict(current_data)

            new_password = str(user_input.get(CONF_PASSWORD, "")).strip()
            if new_password:
                new_data[CONF_PASSWORD] = new_password

            new_data[CONF_COUNTRY_CODE] = (
                str(user_input.get(CONF_COUNTRY_CODE, country_code)).strip().upper()
            )
            new_data[CONF_PHONE_CODE] = _normalize_phone_code(
                user_input.get(CONF_PHONE_CODE, phone_code)
            )
            new_data[CONF_APP_PROFILE] = user_input.get(CONF_APP_PROFILE, app_profile)

            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )

            await self.hass.config_entries.async_reload(self._config_entry.entry_id)
            # Preserve any existing options on the account entry.
            return self.async_create_entry(
                title="", data=dict(self._config_entry.options)
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PASSWORD): str,
                    vol.Optional(CONF_COUNTRY_CODE, default=country_code): str,
                    vol.Optional(CONF_PHONE_CODE, default=phone_code): str,
                    vol.Optional(
                        CONF_APP_PROFILE, default=app_profile
                    ): _app_profile_selector(),
                }
            ),
        )


# ---------------------------------------------------------------------------
# Camera subentries
# ---------------------------------------------------------------------------


def _video_password_schema(default: str = "") -> vol.Schema:
    return vol.Schema({vol.Optional(CONF_VIDEO_PASSWORD, default=default): str})


def _with_video_password(data: dict[str, Any], raw: Any) -> dict[str, Any]:
    password = _clean_optional_text(raw)
    if password:
        data[CONF_VIDEO_PASSWORD] = password
    else:
        data.pop(CONF_VIDEO_PASSWORD, None)
    return data


class CameraSubentryFlow(ConfigSubentryFlow):
    """Add a camera from the account, or change its E2EE video password."""

    def __init__(self) -> None:
        self._devices: dict[str, dict[str, Any]] = {}

    def _account_entry(self) -> ConfigEntry:
        # handler is (entry_id, subentry_type); _get_entry() needs HA 2025.4+.
        entry = self.hass.config_entries.async_get_entry(self.handler[0])
        assert entry is not None
        return entry

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entry = self._account_entry()
        if user_input is not None and user_input.get(CONF_SN_NUM) in self._devices:
            device = self._devices[user_input[CONF_SN_NUM]]
            data = _with_video_password(
                camera_subentry_data(device), user_input.get(CONF_VIDEO_PASSWORD)
            )
            return self.async_create_entry(
                title=data[CONF_DEVICE_NAME], data=data, unique_id=data[CONF_SN_NUM]
            )

        client = build_api_client(
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
            entry.data.get(CONF_COUNTRY_CODE, DEFAULT_COUNTRY_CODE),
            entry.data.get(CONF_PHONE_CODE, DEFAULT_PHONE_CODE),
            entry.data.get(CONF_APP_PROFILE, DEFAULT_APP_PROFILE),
        )

        def _list_cameras() -> list[dict[str, Any]]:
            client.login()
            return client.get_camera_devices()

        try:
            devices = await self.hass.async_add_executor_job(_list_cameras)
        except PermissionError:
            return self.async_abort(reason="invalid_auth")
        except (OSError, RuntimeError, ValueError, KeyError):
            return self.async_abort(reason="cannot_connect")

        configured = {subentry.unique_id for subentry in entry.subentries.values()}
        configured |= cameras_in_other_entries(self.hass, entry)
        self._devices = {
            sn: device
            for device in devices
            if (sn := str(device.get("snNum", "")).strip()) and sn not in configured
        }
        if not self._devices:
            return self.async_abort(reason="no_new_cameras")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SN_NUM): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=sn,
                                    label=str(device.get("deviceName") or sn),
                                )
                                for sn, device in self._devices.items()
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Optional(CONF_VIDEO_PASSWORD, default=""): str,
                }
            ),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            # The entry's update listener reloads it when the password changes.
            return self.async_update_and_abort(
                self._account_entry(),
                subentry,
                data=_with_video_password(
                    dict(subentry.data), user_input.get(CONF_VIDEO_PASSWORD)
                ),
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_video_password_schema(
                _clean_optional_text(subentry.data.get(CONF_VIDEO_PASSWORD))
            ),
        )
