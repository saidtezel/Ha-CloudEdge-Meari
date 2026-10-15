"""Press-and-hold PTZ: ordered start/stop commands with a safety stop.

Meari PTZ is "start moving" / "stop moving": the official app sends start on
touch-down and stop on touch-up. A dashboard does the same through the
``cloudplus.ptz`` action (``move`` on press, ``stop`` on release). Each
command is a cloud round trip, so this controller

* keeps one camera's commands in the order they were issued — otherwise a
  quick tap's ``stop`` can overtake its ``move`` and leave the camera panning;
* stops a move automatically after ``duration`` seconds (a nudge), or after
  ``PTZ_MAX_MOVE_S`` when the release never arrives (closed tab, lost network).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback

if TYPE_CHECKING:
    from .coordinator import CloudEdgeMeariCoordinator

_LOGGER = logging.getLogger(__name__)

# Long enough for a full sweep while held, short enough that a lost
# release doesn't leave the camera grinding against its end stop.
PTZ_MAX_MOVE_S = 20.0


class PtzController:
    """Serialise one camera's PTZ commands and stop moves nobody stopped."""

    def __init__(self, hass: HomeAssistant, coordinator: CloudEdgeMeariCoordinator) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._lock = asyncio.Lock()
        self._generation = 0
        self._auto_stop: asyncio.TimerHandle | None = None

    async def async_move(self, direction: str, duration: float | None = None) -> bool:
        """Start moving; stop after ``duration`` s, or the safety limit."""
        self._generation += 1
        generation = self._generation
        self._cancel_auto_stop()
        async with self._lock:
            ok = await self._hass.async_add_executor_job(
                self._coordinator.ptz_move, direction
            )
        if not ok:
            _LOGGER.warning(
                "PTZ move %s failed for %s", direction, self._coordinator.device_name
            )
        # A stop or newer move issued meanwhile owns the camera now.
        if generation == self._generation:
            self._auto_stop = self._hass.loop.call_later(
                duration if duration else PTZ_MAX_MOVE_S, self._fire_auto_stop, generation
            )
        return ok

    async def async_stop(self) -> bool:
        """Stop moving (after any command already in flight)."""
        self._generation += 1
        self._cancel_auto_stop()
        async with self._lock:
            ok = await self._hass.async_add_executor_job(self._coordinator.ptz_stop)
        if not ok:
            _LOGGER.warning("PTZ stop failed for %s", self._coordinator.device_name)
        return ok

    @callback
    def async_shutdown(self) -> None:
        self._generation += 1
        self._cancel_auto_stop()

    @callback
    def _fire_auto_stop(self, generation: int) -> None:
        self._auto_stop = None
        if generation == self._generation:
            self._hass.async_create_task(self.async_stop())

    @callback
    def _cancel_auto_stop(self) -> None:
        if self._auto_stop is not None:
            self._auto_stop.cancel()
            self._auto_stop = None
