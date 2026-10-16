"""Motion turns off after the timeout, and old alarms never re-fire it."""

from __future__ import annotations

import importlib
import json
import unittest
from unittest.mock import Mock, patch

from debug_tools.bootstrap import _bootstrap_integration_modules

MODULES = _bootstrap_integration_modules()
COORDINATOR = MODULES["coordinator"].CloudEdgeMeariCoordinator
MOTION = importlib.import_module("custom_components.cloudplus.coordinator.motion")
STATE = "custom_components.cloudplus.coordinator.state.time.monotonic"


def coordinator(category: str = "ipc", timeout: int = 60):
    coord = COORDINATOR(
        Mock(), "user@example.invalid", "x", {"_category": category},
        settings={"motion_timeout": timeout},
    )
    coord._api = Mock()
    return coord


class MotionExpiryTests(unittest.TestCase):
    def test_mains_motion_clears_after_timeout(self):
        coord = coordinator("ipc", timeout=60)
        with patch(STATE, return_value=1000.0):
            coord._note_motion("Motion")
        self.assertTrue(coord.motion_detected)
        with patch(STATE, return_value=1059.0):
            coord._expire_motion()
        self.assertTrue(coord.motion_detected)
        with patch(STATE, return_value=1060.0):
            coord._expire_motion()
        self.assertFalse(coord.motion_detected)

    def test_new_alert_restarts_the_timeout(self):
        coord = coordinator("ipc", timeout=60)
        with patch(STATE, return_value=1000.0):
            coord._note_motion("Motion")
        with patch(STATE, return_value=1050.0):
            coord._note_motion("Person")
        with patch(STATE, return_value=1080.0):
            coord._expire_motion()
        self.assertTrue(coord.motion_detected)
        with patch(STATE, return_value=1110.0):
            coord._expire_motion()
        self.assertFalse(coord.motion_detected)

    def test_battery_camera_without_motion_wake_also_clears(self):
        coord = coordinator("snap", timeout=30)
        coord.set_motion_wake_enabled(False)
        with patch(STATE, return_value=1000.0):
            coord._note_motion("PIR")
        with patch(STATE, return_value=1030.0):
            coord._expire_motion()
        self.assertFalse(coord.motion_detected)

    def test_mains_session_loop_expires_motion(self):
        coord = coordinator("ipc", timeout=10)
        coord._running = True
        coord._set_motion(True, "Motion")
        coord._last_motion_time = 0.0  # long ago

        def stop_after_check():
            COORDINATOR._expire_motion(coord)
            coord._running = False

        with patch.object(coord, "_expire_motion", side_effect=stop_after_check), \
             patch.object(coord, "_poll_status"):
            coord._ipc_session_loop()
        self.assertFalse(coord.motion_detected)


class SeenAlarmTests(unittest.TestCase):
    def setUp(self):
        self.api = Mock()
        self.api.get_device_events.return_value = []
        self.listener = MOTION.MotionEventListener(self.api)
        self.received = Mock()
        self.listener._callbacks = [("42", "SN42", self.received)]

    def test_summary_events_do_not_refire_after_midnight(self):
        old = {"deviceID": "42", "imageAlertType": 2, "devLocalTime": "20261015235900"}
        self.api.get_new_device_events.return_value = [old]
        with patch.object(MOTION.time, "strftime", return_value="20261015"):
            self.listener._poll_device_events(dispatch=False)
        with patch.object(MOTION.time, "strftime", return_value="20261016"), \
             patch.object(self.listener, "_handle_payload") as handle:
            self.listener._poll_device_events(dispatch=True)
        handle.assert_not_called()

    def test_trim_keeps_the_newest_events(self):
        with patch.object(MOTION, "_SEEN_ALARM_CAP", 3):
            for n in range(5):
                self.listener._remember_event("42", f"e{n}")
            self.listener._trim_seen_events()
        self.assertEqual(
            list(self.listener._seen_alarm_keys),
            [("42", "e2"), ("42", "e3"), ("42", "e4")],
        )

    def test_event_log_still_dispatches_new_events(self):
        self.api.get_new_device_events.return_value = []
        old = {"deviceID": "42", "eventType": 2, "msgID": "old"}
        self.api.get_device_events.return_value = [old]
        self.listener._poll_device_events(dispatch=False)
        self.api.get_device_events.return_value = [old, dict(old, msgID="new")]
        with patch.object(self.listener, "_handle_payload") as handle:
            self.listener._poll_device_events(dispatch=True)
        handle.assert_called_once()
        self.assertEqual(json.loads(handle.call_args[0][0])["msgID"], "new")


if __name__ == "__main__":
    unittest.main()
