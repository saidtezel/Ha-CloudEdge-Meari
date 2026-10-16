"""Coordinator mixin exposing camera availability and IoT-derived state."""

from __future__ import annotations

import json
import logging
import socket
import time
from typing import Any, Callable

from ..api import MeariApiClient
from ..const import (
    DEFAULT_MOTION_TIMEOUT,
    DEFAULT_STREAM_HOST_MODE,
    IOT_CODE_BATTERY_PERCENT,
    IOT_CODE_CHARGE_STATUS,
    IOT_CODE_LAMP,
    IOT_CODE_VIDEO_ENCRYPTION,
    MOTION_TIMEOUT_MAX,
    MOTION_TIMEOUT_MIN,
    PTZ_DIRECTIONS,
    STREAM_HOST_MODES,
)
from ..p2p_streamer import (
    quality_profile_labels,
    supports_adaptive_stream,
)
from ..meari_commands import PTZ_PRESET
from .iot import capability_value, iot_value, normalize_iot_values, supports_feature
from .stream_server import ANY_BIND_HOST, LOCAL_BIND_HOST

_LOGGER = logging.getLogger(__name__)

SNAPSHOT_CARD_REQUEST_TTL = 30.0
# Mains-powered cameras only stream on demand, so a dashboard still is
# refreshed by briefly streaming when it is missing or stale.
IPC_STILL_GRAB_S = 30.0
IPC_STILL_MAX_AGE_S = 300.0
IPC_STILL_RETRY_S = 60.0
# A cry alert keeps the Crying sensor on this long (alerts don't send an "end").
CRY_HOLD_S = 60.0


class CoordinatorStateMixin:
    """Mixin exposing availability and IoT-derived state to HA entities."""

    _latest_image_source: str
    _latest_image_generation: int = 0
    _last_ipc_still_request: float = float("-inf")
    _last_cry_time: float = 0.0
    _latest_image_updated_at: float

    @property
    def available(self) -> bool:
        return self._available

    @property
    def latest_image(self) -> bytes | None:
        return self._latest_image

    @property
    def latest_image_source(self) -> str:
        return self._latest_image_source

    @property
    def latest_image_generation(self) -> int:
        return self._latest_image_generation

    @property
    def latest_image_updated_at(self) -> float:
        return self._latest_image_updated_at

    @property
    def motion_type(self) -> str:
        return self._motion_type

    @property
    def motion_detected(self) -> bool:
        return self._motion_detected

    @property
    def device_uuid(self) -> str:
        return self._sn_num

    @property
    def device_name(self) -> str:
        return self._device_name

    @property
    def device_model(self) -> str:
        return f"Camera ({self._device_category or 'unknown'})"

    @property
    def device_id(self) -> int:
        return self._device_id

    @property
    def is_battery_camera(self) -> bool:
        return self._is_snap

    @property
    def camera_awake(self) -> bool:
        return self._camera_awake

    @property
    def battery_percent(self) -> int | None:
        return self._battery_percent

    @property
    def battery_charging(self) -> bool:
        return self._battery_charging

    @property
    def motion_wake_enabled(self) -> bool:
        return self._motion_wake_enabled

    @property
    def motion_timeout(self) -> int:
        return self._motion_timeout

    @property
    def has_lamp(self) -> bool:
        return self._has_lamp

    @property
    def lamp_on(self) -> bool:
        return self._lamp_on

    @property
    def has_ptz(self) -> bool:
        return self._has_ptz

    def supports_iot(self, feature: str | None) -> bool:
        return supports_feature(self._capabilities, self._device, feature)

    def iot_capability(self, name: str | None) -> int | None:
        """Return an advertised capability, or None when it is unknown."""
        return capability_value(self._capabilities, name)

    def has_iot_code(self, code: int | str) -> bool:
        return self.get_iot_value(code) is not None

    def get_iot_value(self, code: int | str) -> Any:
        return iot_value(self._iot_data, code)

    @property
    def stream_port(self) -> int:
        if self._running:
            return self._stream_server.ensure_running()
        return self._stream_server.port

    @property
    def stream_host_mode(self) -> str:
        return self._stream_host_mode

    @staticmethod
    def stream_bind_host_for(mode: str) -> str:
        return LOCAL_BIND_HOST if mode == DEFAULT_STREAM_HOST_MODE else ANY_BIND_HOST

    @property
    def stream_host(self) -> str:
        if self._stream_host_mode == DEFAULT_STREAM_HOST_MODE:
            return LOCAL_BIND_HOST
        if self._stream_host_mode == "docker":
            return socket.gethostname()
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"

    @property
    def quality_profiles(self) -> dict[int, str]:
        return quality_profile_labels(self._device)

    @property
    def supports_auto_quality(self) -> bool:
        return supports_adaptive_stream(self._device)

    @property
    def vvp_quality(self) -> int | None:
        return self._vvp_quality

    def get_latest_image(self) -> bytes | None:
        now = time.monotonic()
        self._snapshot_requested_until = now + SNAPSHOT_CARD_REQUEST_TTL
        if not self._is_snap and self._running:
            retry_s = IPC_STILL_RETRY_S if self._latest_image is None else IPC_STILL_MAX_AGE_S
            if now - self._last_ipc_still_request >= retry_s:
                self._last_ipc_still_request = now
                self._extend_live_deadline(IPC_STILL_GRAB_S)
        return self._latest_image

    def set_vvp_quality(self, quality: int | None) -> None:
        if quality == self._vvp_quality:
            return
        self._vvp_quality = quality
        self._codec_params.clear(self._video_codec)
        self._stream_started_keyframe = False
        self._reset_live_mux_timing()
        self._stream_restart.set()
        if self._is_snap:
            self._idle_frame_ready.clear()
            self._idle_frame_refresh.set()
            self._idle_stream_reset.set()
            with self._idle_frame_lock:
                self._idle_video_frame = None
                self._latest_video_kf = None
            self._latest_image = None
            self._latest_image_source = ""
            self._latest_image_generation += 1
            self._latest_image_updated_at = 0.0
        self._fire_update()

    def set_stream_host_mode(self, mode: str) -> None:
        """Switch how the stream is exposed; rebinds the listener (blocking)."""
        if mode not in STREAM_HOST_MODES:
            return
        self._stream_host_mode = mode
        self._stream_server.set_bind_host(self.stream_bind_host_for(mode))
        self._fire_update()

    def set_motion_wake_enabled(self, enabled: bool) -> None:
        self._motion_wake_enabled = bool(enabled)
        if self._motion_wake_enabled and self._motion_detected:
            self._wake_event.set()
            self._extend_live_deadline()
            self._set_camera_awake(True)
        self._fire_update()

    @staticmethod
    def clamp_motion_timeout(seconds: Any) -> int:
        try:
            value = int(seconds)
        except (TypeError, ValueError):
            value = DEFAULT_MOTION_TIMEOUT
        return max(MOTION_TIMEOUT_MIN, min(MOTION_TIMEOUT_MAX, value))

    def set_motion_timeout(self, seconds: int) -> None:
        self._motion_timeout = self.clamp_motion_timeout(seconds)
        if self._camera_awake:
            self._extend_live_deadline()
        self._fire_update()

    def prefetch_battery(self, api: MeariApiClient) -> None:
        self._api = api
        if not self._is_snap:
            return
        try:
            info = api.get_battery_info(self._sn_num)
            if not info and api.openapi_server:
                info = api.get_device_iot_config(self._sn_num)
            changed = self._apply_iot_values(info)
            changed = self._apply_battery_info(info) or changed
            changed = self._apply_video_encryption_info(info) or changed
            if changed:
                self._fire_update()
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            _LOGGER.warning("Battery prefetch failed for %s: %s", self._sn_num, exc)

    def prefetch_status(self, api: MeariApiClient) -> None:
        self.prefetch_lamp(api)

    def prefetch_lamp(self, api: MeariApiClient) -> None:
        self._api = api
        if not api.openapi_server:
            return
        try:
            iot = api.get_device_iot_config(self._sn_num)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            _LOGGER.debug("Lamp/status prefetch failed for %s: %s", self._sn_num, exc)
            return
        changed = self._apply_iot_values(iot)
        changed = self._apply_battery_info(iot) or changed
        changed = self._apply_video_encryption_info(iot) or changed
        lamp = self._as_int(self.get_iot_value(IOT_CODE_LAMP))
        if lamp is not None:
            changed = changed or not self._has_lamp or self._lamp_on != (lamp == 1)
            self._has_lamp = True
            self._lamp_on = lamp == 1
        if changed:
            self._fire_update()

    def set_lamp(self, enabled: bool) -> bool:
        return self.set_iot_value(IOT_CODE_LAMP, 1 if enabled else 0)

    @property
    def crying(self) -> bool:
        return self.crying_for_s > 0.0

    @property
    def crying_for_s(self) -> float:
        """Seconds the Crying sensor stays on after the latest cry alert."""
        if self._last_cry_time <= 0.0:
            return 0.0
        return max(0.0, self._last_cry_time + CRY_HOLD_S - time.monotonic())

    def _note_cry(self) -> None:
        self._last_cry_time = time.monotonic()
        self._fire_update()

    def remember_iot_value(self, code: int | str, value: Any) -> None:
        """Record a value locally (optimistic state) until the next poll."""
        self._iot_data.update(normalize_iot_values({code: value}))
        self._fire_update()

    def send_device_command(self, code: int | str, value: Any) -> bool:
        """Send an 8xx action straight to the camera (blocking)."""
        api = self._api
        return bool(api and api.send_device_command(self._sn_num, str(code), value))

    def set_iot_value(self, code: int | str, value: Any) -> bool:
        api = self._api
        if api is None:
            return False
        ok = api.set_device_iot_value(self._sn_num, str(code), value)
        if ok:
            self._iot_data.update(normalize_iot_values({code: value}))
            if str(code) == IOT_CODE_LAMP:
                self._has_lamp = True
                self._lamp_on = self._as_int(value) == 1
            self._fire_update()
        return ok

    @property
    def _uses_ptz2(self) -> bool:
        # IoT 841/842 for "ptz2" cameras, 807/808 for cameras advertising only
        # "ptz"; anything else (e.g. only the msc flag) keeps 841/842.
        caps = self._capabilities
        return bool(capability_value(caps, "ptz2")) or not capability_value(caps, "ptz")

    def ptz_move(self, direction: str) -> bool:
        api = self._api
        if api is None or direction not in PTZ_DIRECTIONS:
            return False
        return api.ptz_start(self._sn_num, direction, use_ptz2=self._uses_ptz2)

    def ptz_stop(self) -> bool:
        api = self._api
        return bool(api and api.ptz_stop(self._sn_num, use_ptz2=self._uses_ptz2))

    def ptz_preset(self, act: str, preset: int, name: str) -> bool:
        """Go to / save / delete a preset (IoT 848; act is goto, set or del)."""
        value = json.dumps({"id": preset, "name": name, "act": act}, separators=(",", ":"))
        return self.send_device_command(PTZ_PRESET, value)

    def wake_camera(self) -> None:
        self._wake_event.set()
        self._extend_live_deadline()
        self._set_camera_awake(True)

    def register_motion_callback(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._motion_callbacks.append(cb)
        return lambda: self._remove_callback(self._motion_callbacks, cb)

    def register_update_callback(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._update_callbacks.append(cb)
        return lambda: self._remove_callback(self._update_callbacks, cb)

    @staticmethod
    def _remove_callback(
        callbacks: list[Callable[[], None]],
        cb: Callable[[], None],
    ) -> None:
        try:
            callbacks.remove(cb)
        except ValueError:
            pass

    def _fire_update(self) -> None:
        if self.hass.loop.is_closed():
            return
        for cb in list(self._update_callbacks):
            self.hass.loop.call_soon_threadsafe(cb)

    def _fire_motion(self) -> None:
        if self.hass.loop.is_closed():
            return
        for cb in list(self._motion_callbacks):
            self.hass.loop.call_soon_threadsafe(cb)

    def _set_camera_awake(self, awake: bool) -> None:
        awake = bool(awake)
        if self._camera_awake == awake:
            return
        self._camera_awake = awake
        self._fire_update()

    def _set_motion(self, detected: bool, motion_type: str = "") -> None:
        detected = bool(detected)
        motion_type = motion_type if detected else ""
        changed = self._motion_detected != detected or self._motion_type != motion_type
        self._motion_detected = detected
        self._motion_type = motion_type
        if changed:
            self._fire_motion()
            self._fire_update()

    def _expire_motion(self) -> None:
        """Clear motion once no event has arrived for the motion timeout."""
        if (
            self._motion_detected
            and time.monotonic() - self._last_motion_time >= self._motion_timeout
        ):
            self._set_motion(False)

    def _extend_live_deadline(self, seconds: float | None = None) -> None:
        duration = float(seconds if seconds is not None else self._motion_timeout)
        self._live_deadline = max(self._live_deadline, time.monotonic() + duration)

    def _note_motion(self, motion_type: str, event_image: bytes | None = None) -> None:
        if event_image:
            self._latest_image = event_image
            self._latest_image_source = "event"
            self._latest_image_generation += 1
            self._latest_image_updated_at = time.time()
            # Keep the exact alarm image authoritative while motion is active.
            # A wake-up may first replay an old keyframe from the previous session.
            self._last_snapshot_convert_time = time.monotonic()
            self._fire_update()
        self._last_motion_time = time.monotonic()
        self._set_motion(True, motion_type)
        if self._is_snap and self._motion_wake_enabled:
            self._wake_event.set()
            self._extend_live_deadline()
            self._set_camera_awake(True)

    @staticmethod
    def _as_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _iot_value(self, info: dict[str, Any], code: str) -> Any:
        return iot_value(normalize_iot_values(info), code)

    def _apply_iot_values(self, info: dict[str, Any]) -> bool:
        values = normalize_iot_values(info)
        if not values:
            return False
        updated = dict(self._iot_data)
        updated.update(values)
        if updated == self._iot_data:
            return False
        self._iot_data = updated
        return True

    def _apply_battery_info(self, info: dict[str, Any]) -> bool:
        if not info:
            return False

        changed = False
        percent = self._as_int(self._iot_value(info, IOT_CODE_BATTERY_PERCENT))
        charging = self._as_int(self._iot_value(info, IOT_CODE_CHARGE_STATUS))

        if percent is not None and 0 <= percent <= 100:
            changed = changed or self._battery_percent != percent
            self._battery_percent = percent
        if charging is not None:
            is_charging = charging == 1
            changed = changed or self._battery_charging != is_charging
            self._battery_charging = is_charging
        return changed

    def _apply_video_encryption_info(self, info: dict[str, Any]) -> bool:
        state = self._as_int(self._iot_value(info, IOT_CODE_VIDEO_ENCRYPTION))
        if state is None:
            return False

        enabled = state == 1
        changed = self._video_e2ee_enabled != enabled
        self._video_e2ee_enabled = enabled

        effective_password = self._configured_video_password if enabled else ""
        if self._video_password != effective_password:
            changed = True
            self._video_password = effective_password
            if self._configured_video_password and not enabled:
                _LOGGER.debug(
                    "Ignoring stored video password for %s because E2EE is disabled",
                    self._sn_num,
                )
        return changed

    def _reauthenticate_api(self, api: MeariApiClient, context: str) -> bool:
        try:
            api.login()
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            _LOGGER.debug(
                "%s reauthentication failed for %s: %s",
                context,
                self._sn_num,
                exc,
            )
            return False
        _LOGGER.info("%s reauthenticated for %s", context, self._sn_num)
        return True

    def _poll_battery(self) -> None:
        api = self._api
        if not self._is_snap or api is None:
            return
        for attempt in range(2):
            try:
                info = api.get_battery_info(self._sn_num)
                if not info and api.openapi_server:
                    info = api.get_device_iot_config(self._sn_num)
                changed = self._apply_iot_values(info)
                changed = self._apply_battery_info(info) or changed
                changed = self._apply_video_encryption_info(info) or changed
                if changed:
                    self._fire_update()
                return
            except (OSError, RuntimeError, ValueError, KeyError) as exc:
                if attempt == 0:
                    _LOGGER.debug("Battery poll retry for %s: %s", self._sn_num, exc)
                    self._reauthenticate_api(api, "Battery poll")
                    continue
                _LOGGER.debug("Battery poll failed for %s: %s", self._sn_num, exc)

    def _poll_status(self) -> None:
        api = self._api
        if api is None or not api.openapi_server:
            return
        iot: dict[str, Any] = {}
        for attempt in range(2):
            try:
                iot = api.get_device_iot_config(self._sn_num)
                break
            except (OSError, RuntimeError, ValueError, KeyError) as exc:
                if attempt == 0:
                    _LOGGER.debug("Status poll retry for %s: %s", self._sn_num, exc)
                    self._reauthenticate_api(api, "Status poll")
                    continue
                _LOGGER.debug("Status poll failed for %s: %s", self._sn_num, exc)
                return

        changed = self._apply_iot_values(iot)
        changed = self._apply_battery_info(iot) or changed
        changed = self._apply_video_encryption_info(iot) or changed
        lamp = self._as_int(self.get_iot_value(IOT_CODE_LAMP))
        if lamp is not None:
            changed = changed or not self._has_lamp or self._lamp_on != (lamp == 1)
            self._has_lamp = True
            self._lamp_on = lamp == 1
        if changed:
            self._fire_update()
