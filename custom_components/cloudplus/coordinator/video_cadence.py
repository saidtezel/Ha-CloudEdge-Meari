"""Live video PTS steps derived from the camera's own frame timestamps."""

from __future__ import annotations

from collections import deque
from statistics import median

# A step outside this range is a stall, a reconnect or a timestamp jump, not
# the camera's frame cadence. 1.0 s matches LIVE_P2P_VIDEO_GAP_S.
CADENCE_MIN_INTERVAL_S = 1.0 / 60.0
CADENCE_MAX_INTERVAL_S = 1.0
CADENCE_WINDOW = 31
CADENCE_MIN_SAMPLES = 3
TIMESTAMP_MASK = 0xFFFFFFFF
TIMESTAMP_HALF_RANGE = 0x7FFFFFFF


class VideoCadence:
    """Turn camera frame timestamps into per-frame PTS intervals.

    A plausible step between consecutive camera timestamps is used as-is, so
    PTS follows the camera's real frame rate and a dropped frame keeps its
    slot. Anything else (first frame, a repeat, a backward or huge jump after
    a stall or reconnect) gets the learned cadence instead of a PTS jump, or
    ``None`` until a cadence has been learned.
    """

    def __init__(self) -> None:
        self._last_timestamp_ms: int | None = None
        self._intervals: deque[float] = deque(maxlen=CADENCE_WINDOW)

    @property
    def nominal_interval_s(self) -> float | None:
        """Median accepted frame step, once enough samples exist."""
        if len(self._intervals) < CADENCE_MIN_SAMPLES:
            return None
        return median(self._intervals)

    def reset(self) -> None:
        """Forget the timestamp base (new P2P session); keep the cadence."""
        self._last_timestamp_ms = None

    def clear(self) -> None:
        """Forget everything (codec or stream change)."""
        self._last_timestamp_ms = None
        self._intervals.clear()

    def interval_for(self, timestamp_ms: int | None) -> float | None:
        """Return the PTS interval from the previous frame to this one."""
        if timestamp_ms is None:
            self._last_timestamp_ms = None
            return self.nominal_interval_s
        current = int(timestamp_ms) & TIMESTAMP_MASK
        previous = self._last_timestamp_ms
        self._last_timestamp_ms = current
        if previous is None:
            return self.nominal_interval_s
        delta_ms = (current - previous) & TIMESTAMP_MASK
        if delta_ms > TIMESTAMP_HALF_RANGE:
            return self.nominal_interval_s
        delta_s = delta_ms / 1000.0
        if not CADENCE_MIN_INTERVAL_S <= delta_s <= CADENCE_MAX_INTERVAL_S:
            return self.nominal_interval_s
        self._intervals.append(delta_s)
        return delta_s
