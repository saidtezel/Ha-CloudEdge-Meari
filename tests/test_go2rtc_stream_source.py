"""Offline regressions for the camera stream, its exposure and cloud retries.

Setup, subentries and migration run against a real HA core in tests/ha.
"""

from __future__ import annotations

import importlib
import json
import socket
import time
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from debug_tools.bootstrap import _bootstrap_integration_modules

MODULES = _bootstrap_integration_modules()
COORDINATOR = MODULES["coordinator"].CloudEdgeMeariCoordinator
CAMERA = MODULES["camera"]
CONST = MODULES["const"]
BACKOFF = importlib.import_module("custom_components.cloudplus.coordinator.backoff")
MOTION = importlib.import_module("custom_components.cloudplus.coordinator.motion")
STREAM_SERVER = importlib.import_module("custom_components.cloudplus.coordinator.stream_server")
NETWORK = importlib.import_module("custom_components.cloudplus.p2p_streamer.network")


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
        self.assertEqual(await camera.stream_source(), "http://192.0.2.10:40000/")

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
        camera.stream.update_source.assert_called_once_with("http://cloudplus-ha:40001/")
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
        return COORDINATOR(Mock(), "user@example.invalid", "x", {}, settings=options)

    def test_defaults(self):
        coord = self.build({})
        self.assertEqual(coord.stream_host_mode, "local")
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
        self.assertEqual(coord.stream_host_mode, "local")
        self.assertEqual(coord.motion_timeout, CONST.MOTION_TIMEOUT_MAX)
        self.assertEqual(self.build({CONST.CONF_MOTION_TIMEOUT: "bad"}).motion_timeout,
                         CONST.DEFAULT_MOTION_TIMEOUT)


class LocalOnlyStreamTests(unittest.TestCase):
    """The stream listens on loopback unless a LAN mode is chosen."""

    def build(self, options: dict) -> Any:
        return COORDINATOR(Mock(), "user@example.invalid", "x", {}, settings=options)

    def bound_host(self, coord: Any) -> str:
        return coord._stream_server._sock.getsockname()[0]

    def test_default_is_loopback(self):
        coord = self.build({})
        coord._stream_server.start()
        try:
            self.assertEqual(self.bound_host(coord), "127.0.0.1")
            self.assertEqual(coord.stream_host, "127.0.0.1")
        finally:
            coord._stream_server.stop()

    def test_lan_mode_rebinds_and_back(self):
        coord = self.build({})
        coord._stream_server.start()
        try:
            coord.set_stream_host_mode("ip")
            self.assertEqual(self.bound_host(coord), "0.0.0.0")
            self.assertNotEqual(coord.stream_host, "")
            coord.set_stream_host_mode("local")
            self.assertEqual(self.bound_host(coord), "127.0.0.1")
            self.assertGreater(coord._stream_server.port, 0)
        finally:
            coord._stream_server.stop()

    def test_saved_lan_mode_binds_all_interfaces(self):
        coord = self.build({CONST.CONF_STREAM_HOST_MODE: "docker"})
        coord._stream_server.start()
        try:
            self.assertEqual(self.bound_host(coord), "0.0.0.0")
        finally:
            coord._stream_server.stop()


class RetryBackoffTests(unittest.TestCase):
    """Cloud retries slow down instead of hammering the login endpoint."""

    def test_doubles_to_cap_and_resets_after_healthy_run(self):
        backoff = BACKOFF.RetryBackoff(2, 300)
        self.assertEqual([backoff.next_delay() for _ in range(10)],
                         [2, 4, 8, 16, 32, 64, 128, 256, 300, 300])
        self.assertEqual(backoff.next_delay(ran_for_s=3600), 2)

    def test_watch_loop_backs_off_on_rejected_login(self):
        coord = COORDINATOR(Mock(), "user@example.invalid", "x", {})
        coord._running = True
        delays: list[float] = []

        def wait(delay: float) -> bool:
            delays.append(delay)
            if len(delays) == 6:
                coord._running = False
            return False

        coord._stop_event = SimpleNamespace(wait=wait)
        client = Mock()
        client.return_value.login.side_effect = PermissionError("Login failed: 1003")
        with patch.object(MODULES["coordinator"], "MeariApiClient", client), \
                self.assertLogs(MODULES["coordinator"].__name__, "WARNING"):
            coord._watch_loop()
        self.assertEqual(delays, [2, 4, 8, 16, 32, 64])

    def test_motion_poll_backs_off_when_polls_keep_failing(self):
        listener = MOTION.MotionEventListener(Mock())
        listener._poll_device_events = Mock(return_value=False)
        waits: list[float] = []

        def wait(delay: float) -> bool:
            waits.append(delay)
            if len(waits) == 7:
                listener._stop_poll.set()
            return False

        listener._stop_poll = SimpleNamespace(
            is_set=lambda: len(waits) >= 7, set=lambda: None, wait=wait
        )
        listener._alarm_poll_loop()
        self.assertEqual(waits, [15, 60, 120, 240, 480, 600, 600])
        self.assertEqual(listener._api.login.call_count, 7)


class MainsCameraStillTests(unittest.TestCase):
    """Mains-powered cameras stream briefly to give the dashboard a still."""

    def test_missing_still_triggers_one_short_grab(self):
        coord = COORDINATOR(Mock(), "user@example.invalid", "x", {"_category": "ipc"})
        coord._running = True
        self.assertIsNone(coord.get_latest_image())
        deadline = coord._live_deadline
        self.assertGreater(deadline, 0)
        coord.get_latest_image()
        self.assertEqual(coord._live_deadline, deadline)

    def test_battery_camera_never_streams_for_stills(self):
        coord = COORDINATOR(Mock(), "user@example.invalid", "x", {"_category": "snap"})
        coord._running = True
        coord.get_latest_image()
        self.assertEqual(coord._live_deadline, 0.0)


class PtzCodeTests(unittest.TestCase):
    """807/808 for "ptz" cameras, 841/842 for "ptz2" (and flag-less msc) ones."""

    def sent_with(self, caps: dict) -> tuple[bool, bool]:
        device = {"capability": {"caps": caps}, "msc": 1}
        coord = COORDINATOR(Mock(), "user@example.invalid", "x", device)
        coord._api = Mock()
        coord.ptz_move("left")
        coord.ptz_stop()
        return (
            coord._api.ptz_start.call_args.kwargs["use_ptz2"],
            coord._api.ptz_stop.call_args.kwargs["use_ptz2"],
        )

    def test_capability_picks_the_codes(self):
        self.assertEqual(self.sent_with({"ptz2": 1}), (True, True))
        self.assertEqual(self.sent_with({"ptz": 1}), (False, False))
        self.assertEqual(self.sent_with({"ptz": 1, "ptz2": 1}), (True, True))
        self.assertEqual(self.sent_with({}), (True, True))


class CryAlertTests(unittest.TestCase):
    """"Baby cry" alerts reach the Crying sensor, never the Motion sensor."""

    def dispatch(self, event_type: int) -> tuple[Mock, Mock]:
        listener = MOTION.MotionEventListener(Mock())
        on_motion, on_cry = Mock(), Mock()
        listener.register(42, "synthetic-camera", on_motion, on_cry=on_cry)
        listener._handle_payload(
            json.dumps({"params": {"deviceID": "42", "eventType": event_type}}).encode()
        )
        return on_motion, on_cry

    def test_cry_alert(self):
        on_motion, on_cry = self.dispatch(7)
        on_cry.assert_called_once_with()
        on_motion.assert_not_called()

    def test_motion_alert(self):
        on_motion, on_cry = self.dispatch(2)
        on_motion.assert_called_once()
        on_cry.assert_not_called()


class StreamProtocolTests(unittest.TestCase):
    """One port serves raw TCP (legacy tcp:// configs) and HTTP (stream_source)."""

    PACKET = b"\x47" + bytes(187)

    def setUp(self):
        self.server = STREAM_SERVER.StreamServer()
        self.server.start()
        self.addCleanup(self.server.stop)

    def connect(self) -> socket.socket:
        sock = socket.create_connection(("127.0.0.1", self.server.port), timeout=5)
        self.addCleanup(sock.close)
        return sock

    def wait_for_client(self, count: int = 1) -> None:
        for _ in range(100):
            if self.server.client_count >= count:
                return
            time.sleep(0.01)
        self.fail("client never registered")

    def read(self, sock: socket.socket, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                break
            data += chunk
        return data

    def test_raw_tcp_client_gets_bare_mpegts(self):
        sock = self.connect()
        self.wait_for_client()
        time.sleep(STREAM_SERVER.HTTP_REQUEST_WAIT_S + 0.2)
        self.server.broadcast(self.PACKET)
        self.assertEqual(self.read(sock, 188), self.PACKET)

    def test_http_client_gets_response_then_mpegts(self):
        sock = self.connect()
        sock.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nUser-Agent: go2rtc\r\n\r\n")
        self.wait_for_client()
        header = self.read(sock, len(STREAM_SERVER.HTTP_RESPONSE))
        self.assertEqual(header, STREAM_SERVER.HTTP_RESPONSE)
        self.server.broadcast(self.PACKET)
        self.assertEqual(self.read(sock, 188), self.PACKET)

    def test_head_request_gets_headers_only(self):
        sock = self.connect()
        sock.sendall(b"HEAD / HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertEqual(self.read(sock, 4096), STREAM_SERVER.HTTP_RESPONSE)


class HostCandidateTests(unittest.TestCase):
    """The camera must be offered an address it can reach for direct media."""

    def local_ips(self, route_ip, hostname_ips):
        def getaddrinfo(*_args, **_kwargs):
            return [(2, 2, 17, "", (ip, 0)) for ip in hostname_ips]

        with patch.object(NETWORK, "_route_ip", return_value=route_ip), \
                patch.object(NETWORK.socket, "getaddrinfo", getaddrinfo):
            return NETWORK._get_local_ips()

    def test_haos_hostname_resolves_to_supervisor_network(self):
        # Inside HA OS "homeassistant" resolves to the Supervisor bridge only.
        self.assertEqual(
            self.local_ips("10.10.10.5", ["172.30.32.1"]), ["10.10.10.5", "172.30.32.1"]
        )

    def test_route_ip_first_and_not_duplicated(self):
        self.assertEqual(self.local_ips("192.168.1.20", ["192.168.1.20"]), ["192.168.1.20"])

    def test_no_default_route_falls_back_to_hostname(self):
        self.assertEqual(self.local_ips(None, ["192.168.1.20", "127.0.1.1"]), ["192.168.1.20"])
        self.assertEqual(self.local_ips(None, []), ["0.0.0.0"])

    def test_route_ip_ignores_loopback(self):
        sock = Mock()
        sock.__enter__ = Mock(return_value=sock)
        sock.__exit__ = Mock(return_value=False)
        sock.getsockname.return_value = ("127.0.0.1", 5555)
        with patch.object(NETWORK.socket, "socket", return_value=sock):
            self.assertIsNone(NETWORK._route_ip())


if __name__ == "__main__":
    unittest.main()
