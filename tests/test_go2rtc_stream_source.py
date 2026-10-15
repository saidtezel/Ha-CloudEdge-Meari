"""Offline regressions for HA's built-in go2rtc (WebRTC) picking up the camera."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import unittest
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from debug_tools.bootstrap import _bootstrap_integration_modules

MODULES = _bootstrap_integration_modules()
COORDINATOR = MODULES["coordinator"].CloudEdgeMeariCoordinator
CAMERA = MODULES["camera"]
CONST = MODULES["const"]


class ConfigEntryNotReady(Exception):
    """Stub of HA's ConfigEntryNotReady."""


def load_integration() -> ModuleType:
    """Import the integration's __init__ with the HA stubs it needs."""
    stubs: dict[str, ModuleType] = {}
    for name, attributes in {
        "homeassistant.config_entries": {
            "ConfigEntry": object,
            "ConfigEntryDisabler": SimpleNamespace(USER="user"),
            "SOURCE_IMPORT": "import",
        },
        "homeassistant.core": {
            "HomeAssistant": object,
            "ServiceCall": object,
            "callback": lambda func: func,
        },
        "homeassistant.exceptions": {"ConfigEntryNotReady": ConfigEntryNotReady},
        "homeassistant.helpers.device_registry": {},
        "homeassistant.helpers.entity_registry": {},
    }.items():
        module = ModuleType(name)
        vars(module).update(attributes)
        stubs[name] = module
    helpers = sys.modules["homeassistant.helpers"]
    with patch.dict(sys.modules, stubs), patch.multiple(
        helpers,
        create=True,
        device_registry=stubs["homeassistant.helpers.device_registry"],
        entity_registry=stubs["homeassistant.helpers.entity_registry"],
    ):
        path = CAMERA.__file__.replace("camera.py", "__init__.py")
        spec = importlib.util.spec_from_file_location(
            "custom_components.cloudplus._integration_under_test", path
        )
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "custom_components.cloudplus"
        spec.loader.exec_module(module)
        return module


INTEGRATION = load_integration()


def make_camera(port: int = 40000, host: str = "192.0.2.10") -> Any:
    """Camera entity bound to a stand-in coordinator, no HA runtime."""
    coord = SimpleNamespace(
        stream_port=port,
        stream_host=host,
        is_battery_camera=True,
        device_uuid="synthetic-camera",
        device_name="Synthetic camera",
        device_model="",
        register_update_callback=Mock(return_value=Mock()),
    )
    camera = CAMERA.CloudEdgeMeariCamera(coord, SimpleNamespace(options={}))
    camera.async_write_ha_state = Mock()
    camera.async_refresh_providers = AsyncMock()
    camera.stream = None
    tasks: list = []
    camera.hass = SimpleNamespace(async_create_task=tasks.append)
    return camera, coord, tasks


class CameraStreamSourceTests(unittest.IsolatedAsyncioTestCase):
    """The camera must re-offer itself to go2rtc whenever its URL changes."""

    async def test_stream_source_is_tcp_mpegts(self):
        camera, _coord, _tasks = make_camera()
        self.assertEqual(await camera.stream_source(), "tcp://192.0.2.10:40000")

    async def test_no_port_means_no_source(self):
        camera, _coord, _tasks = make_camera(port=0)
        self.assertIsNone(await camera.stream_source())

    async def test_unchanged_source_does_not_refresh(self):
        camera, _coord, tasks = make_camera()
        await camera.async_added_to_hass()
        camera._handle_update()
        self.assertEqual(tasks, [])
        camera.async_write_ha_state.assert_called_once()

    async def test_changed_source_refreshes_provider_and_hls(self):
        camera, coord, tasks = make_camera(port=0)
        await camera.async_added_to_hass()
        camera.stream = Mock()
        coord.stream_port = 40001
        coord.stream_host = "cloudplus-ha"
        camera._handle_update()
        self.assertEqual(len(tasks), 1)
        await tasks[0]
        camera.stream.update_source.assert_called_once_with("tcp://cloudplus-ha:40001")
        camera.async_refresh_providers.assert_awaited_once()
        camera._handle_update()
        self.assertEqual(len(tasks), 1)

    async def test_stopping_coordinator_does_not_refresh(self):
        camera, coord, tasks = make_camera()
        await camera.async_added_to_hass()
        coord.stream_port = 0
        camera._handle_update()
        self.assertEqual(tasks, [])


class PersistedSettingsTests(unittest.TestCase):
    """Runtime settings come back from the camera entry's options."""

    def build(self, options: dict) -> Any:
        entry = SimpleNamespace(options=options)
        return COORDINATOR(Mock(), "user@example.invalid", "x", {}, entry=entry)

    def test_defaults(self):
        coord = self.build({})
        self.assertEqual(coord.stream_host_mode, "ip")
        self.assertTrue(coord.motion_wake_enabled)
        self.assertEqual(coord.motion_timeout, CONST.DEFAULT_MOTION_TIMEOUT)

    def test_restored(self):
        coord = self.build({
            CONST.CONF_STREAM_HOST_MODE: "docker",
            CONST.CONF_MOTION_WAKE: False,
            CONST.CONF_MOTION_TIMEOUT: 300,
        })
        self.assertEqual(coord.stream_host_mode, "docker")
        self.assertFalse(coord.motion_wake_enabled)
        self.assertEqual(coord.motion_timeout, 300)

    def test_invalid_values_fall_back(self):
        coord = self.build({
            CONST.CONF_STREAM_HOST_MODE: "bogus",
            CONST.CONF_MOTION_TIMEOUT: 99999,
        })
        self.assertEqual(coord.stream_host_mode, "ip")
        self.assertEqual(coord.motion_timeout, CONST.MOTION_TIMEOUT_MAX)
        self.assertEqual(self.build({CONST.CONF_MOTION_TIMEOUT: "bad"}).motion_timeout,
                         CONST.DEFAULT_MOTION_TIMEOUT)


class CameraEntrySetupTests(unittest.IsolatedAsyncioTestCase):
    """Camera entry setup ordering and cloud-outage handling."""

    def setUp(self):
        self.calls: list[str] = []
        self.api = Mock()
        self.coord = Mock()
        self.coord.async_start = AsyncMock(side_effect=lambda: self.calls.append("start"))
        self.coord.async_stop = AsyncMock()
        account = SimpleNamespace(disabled_by=None, title="account")
        config_entries = SimpleNamespace(
            async_get_entry=Mock(return_value=account),
            async_forward_entry_setups=AsyncMock(
                side_effect=lambda *_: self.calls.append("platforms")
            ),
        )

        async def executor(func, *args):
            return func(*args)

        self.hass = SimpleNamespace(
            data={},
            config_entries=config_entries,
            async_add_executor_job=executor,
            services=SimpleNamespace(has_service=Mock(return_value=True)),
        )
        self.entry = SimpleNamespace(
            entry_id="camera",
            title="camera",
            disabled_by=None,
            options={},
            data={
                CONST.CONF_SN_NUM: "synthetic-camera",
                CONST.CONF_EMAIL: "user@example.invalid",
                CONST.CONF_PASSWORD: "x",
                "account_entry_id": "account",
                "device": {},
            },
        )

    async def setup_entry(self):
        with patch.object(INTEGRATION, "build_api_client", return_value=self.api), \
                patch.object(INTEGRATION, "CloudEdgeMeariCoordinator", return_value=self.coord):
            return await INTEGRATION.async_setup_entry(self.hass, self.entry)

    async def test_stream_server_starts_before_camera_entity(self):
        self.assertTrue(await self.setup_entry())
        self.assertEqual(self.calls, ["start", "platforms"])

    async def test_platform_failure_stops_coordinator(self):
        self.hass.config_entries.async_forward_entry_setups.side_effect = RuntimeError
        with self.assertRaises(RuntimeError):
            await self.setup_entry()
        self.coord.async_stop.assert_awaited_once()
        self.assertNotIn("camera", self.hass.data[CONST.DOMAIN])

    async def test_cloud_outage_retries_setup(self):
        self.api.login.side_effect = ConnectionError("offline")
        with self.assertRaises(ConfigEntryNotReady):
            await self.setup_entry()

    async def test_rejected_credentials_do_not_retry(self):
        self.api.login.side_effect = PermissionError("Login failed: 1003")
        self.assertFalse(await self.setup_entry())
        self.coord.async_start.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
